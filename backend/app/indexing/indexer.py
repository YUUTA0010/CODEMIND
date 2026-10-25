"""Indexing pipeline (full and incremental).

Phase A (read-only w.r.t. the database): walk the repository, detect changes, parse changed files, build chunks and
embeddings. Progress is reported through short, independent sessions, and cancellation is checked throughout.
Phase B (single transaction): apply deletions/updates/inserts, rebuild dependency edges, bump the index version.

Because all database mutation is confined to Phase B, cancelling a job never leaves a half-written index.
Incremental mode skips re-reading, re-parsing, re-chunking and re-embedding of unchanged files; edges are always
re-resolved globally (a change in one file can alter how other files' references resolve), which requires a cheap
re-parse of unchanged code files - recorded as `files_reparsed_for_edges` in the job stats.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select, text

from app.core.config import get_settings
from app.core.events import bus
from app.db.session import session_scope
from app.graph.resolver import FileInfo, Resolver, SymMeta
from app.indexing.ingest import WalkStats, compute_fingerprint, read_git_head, read_text, walk_repository
from app.models import (
    Chunk,
    DependencyEdge,
    IndexingJob,
    Repository,
    RepositoryRevision,
    SourceFile,
    Symbol,
)
from app.parsing.ir import ParsedFile
from app.parsing.registry import is_test_file, parse_source
from app.retrieval.chunking import ChunkSpec, make_chunks
from app.retrieval.embeddings import get_embedder, to_bytes
from app.retrieval.text import tokenize

log = logging.getLogger("codemind.indexer")
CODE_LANGUAGES = {"python", "javascript", "typescript"}


class IndexCancelled(Exception):
    pass


def split_lines(content: str) -> list[str]:
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


@dataclass
class _Work:
    path: str
    language: str
    size: int
    mtime_ns: int
    sha: str
    existing_id: int | None
    changed: bool
    content_changed: bool = False
    text: str = ""
    parsed: ParsedFile | None = None
    chunks: list[ChunkSpec] = field(default_factory=list)
    is_test: bool = False


def create_job(session, repo: Repository, mode: str = "incremental") -> IndexingJob:
    job = IndexingJob(repository_id=repo.id, mode=mode, status="QUEUED", message="queued")
    session.add(job)
    session.flush()
    return job


def _progress(job_id: int, progress: int, total: int, message: str, repo_id: int) -> None:
    with session_scope() as s:
        job = s.get(IndexingJob, job_id)
        if job is None:
            return
        job.progress, job.total, job.message = progress, total, message
    bus.publish("index.progress", {"job_id": job_id, "repository_id": repo_id, "progress": progress, "total": total, "message": message})


def _cancel_requested(job_id: int) -> bool:
    with session_scope() as s:
        job = s.get(IndexingJob, job_id)
        return bool(job and job.cancel_requested)


def run_indexing_job(job_id: int, extra_ignore: list[str] | None = None) -> dict:
    with session_scope() as s:
        job = s.get(IndexingJob, job_id)
        if job is None:
            raise ValueError(f"indexing job {job_id} not found")
        repo = s.get(Repository, job.repository_id)
        repo_id, root, mode = repo.id, Path(repo.root_path), job.mode
        prev_status = "INDEXED" if repo.index_version > 0 else "REGISTERED"
        job.status = "RUNNING"
        job.started_at = datetime.now(timezone.utc)
        repo.status = "INDEXING"
    bus.publish("index.started", {"job_id": job_id, "repository_id": repo_id})
    try:
        stats = _index(job_id, repo_id, root, mode, extra_ignore)
    except IndexCancelled:
        _finish(job_id, repo_id, "CANCELLED", prev_status, message="cancelled; index unchanged")
        return {"status": "CANCELLED"}
    except Exception as e:  # noqa: BLE001
        log.exception("indexing failed")
        _finish(job_id, repo_id, "FAILED", "FAILED" if prev_status == "REGISTERED" else prev_status, message="failed", error=f"{type(e).__name__}: {e}")
        return {"status": "FAILED", "error": str(e)}
    _finish(job_id, repo_id, "COMPLETED", "INDEXED", message="completed", stats=stats)
    return {"status": "COMPLETED", **stats}


def _finish(job_id: int, repo_id: int, job_status: str, repo_status: str, *, message: str, error: str | None = None, stats: dict | None = None) -> None:
    with session_scope() as s:
        job = s.get(IndexingJob, job_id)
        repo = s.get(Repository, repo_id)
        job.status, job.message, job.error = job_status, message, error
        job.finished_at = datetime.now(timezone.utc)
        if stats is not None:
            job.stats = stats
            job.progress = job.total
        repo.status = repo_status
        if job_status == "COMPLETED":
            repo.last_indexed_at = datetime.now(timezone.utc)
    bus.publish("index.done", {"job_id": job_id, "repository_id": repo_id, "status": job_status, "error": error, "stats": stats or {}})


def _index(job_id: int, repo_id: int, root: Path, mode: str, extra_ignore: list[str] | None) -> dict:
    settings = get_settings()
    embedder = get_embedder(settings)
    walk_stats = WalkStats()

    _progress(job_id, 0, 0, "scanning repository", repo_id)
    candidates = []
    for i, c in enumerate(walk_repository(root, max_file_bytes=settings.max_file_bytes, max_files=settings.max_files_per_repo,
                                          extra_ignore=extra_ignore, stats=walk_stats)):
        candidates.append(c)
        if i % 200 == 0 and _cancel_requested(job_id):
            raise IndexCancelled()

    with session_scope() as s:
        rows = s.execute(
            select(SourceFile.id, SourceFile.path, SourceFile.size_bytes, SourceFile.mtime_ns, SourceFile.sha256, SourceFile.language)
            .where(SourceFile.repository_id == repo_id)
        ).all()
        existing = {r.path: r for r in rows}
        models_in_use = {m for (m,) in s.execute(select(Chunk.embedding_model).where(Chunk.repository_id == repo_id).distinct()).all()} - {None}
    forced_full = False
    if mode == "incremental" and models_in_use and models_in_use != {embedder.name}:
        mode, forced_full = "full", True  # mixing embedding models in one index would corrupt similarity search

    total = len(candidates)
    work: list[_Work] = []
    reparsed_for_edges = 0
    for i, c in enumerate(candidates):
        if i % 25 == 0:
            if _cancel_requested(job_id):
                raise IndexCancelled()
            _progress(job_id, i, total, f"analyzing {c.path}", repo_id)
        old = existing.get(c.path)
        unchanged_by_stat = old is not None and mode != "full" and old.size_bytes == c.size and old.mtime_ns == c.mtime_ns
        try:
            if unchanged_by_stat:
                w = _Work(c.path, c.language, c.size, c.mtime_ns, old.sha256, old.id, changed=False, content_changed=False)
                if c.language in CODE_LANGUAGES:
                    content, _ = read_text(c.abs_path)
                    w.text = content
            else:
                content, sha = read_text(c.abs_path)
                changed = old is None or mode == "full" or old.sha256 != sha
                w = _Work(c.path, c.language, c.size, c.mtime_ns, sha, old.id if old else None, changed=changed,
                          content_changed=(old is None or old.sha256 != sha))
                w.text = content
        except OSError as e:
            walk_stats.skip("unreadable")
            log.warning("cannot read %s: %s", c.path, e)
            continue
        if w.changed or c.language in CODE_LANGUAGES:
            w.parsed = parse_source(c.path, c.language, w.text)
            w.is_test = is_test_file(c.path, c.language) or bool(w.parsed and w.parsed.is_test)
            if not w.changed:
                reparsed_for_edges += 1
        if w.changed:
            lines = split_lines(w.text)
            w.chunks = make_chunks(w.parsed, lines) if w.parsed else []
        if not w.changed:
            w.text = ""  # release memory; unchanged content is not persisted again
        work.append(w)

    # embeddings for new chunks (batched; failures degrade gracefully to "no semantic signal" for those chunks)
    _progress(job_id, total, total, "computing embeddings", repo_id)
    embed_errors = 0
    pending = [(w, ch) for w in work if w.changed for ch in w.chunks]
    vectors: dict[int, bytes] = {}
    for start in range(0, len(pending), 128):
        if _cancel_requested(job_id):
            raise IndexCancelled()
        batch = pending[start : start + 128]
        texts = [f"{w.path} {ch.qname or ''}\n{ch.content}" for w, ch in batch]
        try:
            mat = embedder.embed(texts)
            for j in range(len(batch)):
                vectors[start + j] = to_bytes(mat[j])
        except Exception as e:  # noqa: BLE001
            embed_errors += len(batch)
            log.warning("embedding batch failed (%s); chunks stay without vectors", e)
    if _cancel_requested(job_id):
        raise IndexCancelled()

    # ---------------------------------------------------------------- Phase B: single transaction
    _progress(job_id, total, total, "writing index", repo_id)
    stats = {"mode": mode, "forced_full_reindex": forced_full, "embedding_model": embedder.name, "embedding_is_neural": embedder.is_neural,
             "embedding_errors": embed_errors, "skipped": walk_stats.skipped}
    with session_scope() as s:
        repo = s.get(Repository, repo_id)
        current_paths = {w.path for w in work}
        gone = [existing[p].id for p in existing if p not in current_paths]
        changed_ids = [w.existing_id for w in work if w.changed and w.existing_id is not None]
        to_delete = gone + changed_ids
        n_new = sum(1 for w in work if w.existing_id is None)
        n_mod = sum(1 for w in work if w.existing_id is not None and w.content_changed)
        n_reindexed = sum(1 for w in work if w.changed)
        if mode == "full":
            to_delete = [r.id for r in existing.values()]
        _delete_files(s, repo_id, to_delete)

        file_rows: dict[str, SourceFile] = {}
        sym_maps: dict[str, dict[str, int]] = {}
        meta_by_path: dict[str, list[SymMeta]] = {}
        chunk_index = {id(ch): idx for idx, (_, ch) in enumerate(pending)}
        for w in work:
            if w.changed:
                parsed = w.parsed
                sf = SourceFile(
                    repository_id=repo_id, path=w.path, language=w.language, size_bytes=w.size, sha256=w.sha, mtime_ns=w.mtime_ns,
                    line_count=(parsed.line_count if parsed else len(split_lines(w.text))), is_test=w.is_test,
                    parse_error=parsed.parse_error if parsed else None, indexed_version=repo.index_version + 1,
                )
                s.add(sf)
                file_rows[w.path] = sf
        s.flush()
        for w in work:
            if w.changed and w.parsed is not None:
                sf = file_rows[w.path]
                q2id: dict[str, int] = {}
                sym_objs: list[tuple[Symbol, object]] = []
                for ps in w.parsed.symbols:
                    sym = Symbol(repository_id=repo_id, file_id=sf.id, kind=ps.kind, name=ps.name[:300], qualified_name=ps.qualified_name[:600],
                                 signature=ps.signature, docstring=ps.docstring, start_line=ps.start_line, end_line=ps.end_line,
                                 is_test=ps.is_test)
                    s.add(sym)
                    sym_objs.append((sym, ps))
                s.flush()
                for sym, ps in sym_objs:
                    q2id.setdefault(ps.qualified_name, sym.id)
                for sym, ps in sym_objs:
                    if ps.parent_qname and ps.parent_qname in q2id:
                        sym.parent_id = q2id[ps.parent_qname]
                sym_maps[w.path] = q2id
                for ch in w.chunks:
                    sid = q2id.get(ch.qname) if ch.qname else None
                    idx = chunk_index[id(ch)]
                    vec = vectors.get(idx)
                    row = Chunk(repository_id=repo_id, file_id=sf.id, symbol_id=sid, start_line=ch.start_line, end_line=ch.end_line,
                                content=ch.content, embedding=vec, embedding_model=embedder.name if vec else None)
                    s.add(row)
        s.flush()
        _write_fts(s, repo_id, [w.path for w in work if w.changed])

        # unchanged files: refresh mtime if the hash matched but mtime differed; load their symbol ids
        unchanged = [w for w in work if not w.changed]
        if unchanged:
            ids = [w.existing_id for w in unchanged]
            mt = {w.existing_id: w.mtime_ns for w in unchanged}
            for sf in s.execute(select(SourceFile).where(SourceFile.id.in_(ids))).scalars():
                if sf.mtime_ns != mt[sf.id]:
                    sf.mtime_ns = mt[sf.id]
                file_rows[sf.path] = sf
            path_by_id = {r.id: p for p, r in file_rows.items()}
            for fid, qname, sid in s.execute(select(Symbol.file_id, Symbol.qualified_name, Symbol.id).where(Symbol.file_id.in_(ids)).order_by(Symbol.id)).all():
                sym_maps.setdefault(path_by_id[fid], {}).setdefault(qname, sid)

        # ---- dependency edges (global re-resolution)
        infos: list[FileInfo] = []
        metas: list[SymMeta] = []
        for w in work:
            sf = file_rows.get(w.path)
            if sf is None or w.parsed is None:
                continue
            q2id = sym_maps.get(w.path, {})
            infos.append(FileInfo(sf.id, w.path, w.language, w.parsed, q2id, is_test=w.is_test))
            seen_q: set[str] = set()
            for ps in w.parsed.symbols:
                sid = q2id.get(ps.qualified_name)
                if sid is None or ps.qualified_name in seen_q:
                    continue
                seen_q.add(ps.qualified_name)
                metas.append(SymMeta(sid, sf.id, ps.kind, ps.name, ps.qualified_name, ps.parent_qname, ps.is_test, ps.bases))
        s.execute(delete(DependencyEdge).where(DependencyEdge.repository_id == repo_id))
        edges = Resolver(infos, metas).resolve_all()
        if edges:
            s.bulk_insert_mappings(DependencyEdge, [{**e, "repository_id": repo_id} for e in edges])

        # ---- repository bookkeeping
        lang: dict[str, dict[str, int]] = {}
        for w in work:
            d = lang.setdefault(w.language, {"files": 0, "lines": 0})
            d["files"] += 1
            d["lines"] += (w.parsed.line_count if w.parsed else 0) or (file_rows[w.path].line_count if w.path in file_rows else 0)
        any_change = bool(n_new or n_mod or gone or mode == "full" or repo.index_version == 0)
        fingerprint = compute_fingerprint([(w.path, w.sha) for w in work])
        if any_change:
            repo.index_version += 1
            s.add(RepositoryRevision(repository_id=repo_id, index_version=repo.index_version, fingerprint=fingerprint,
                                     git_commit=read_git_head(root), file_count=len(work)))
        repo.fingerprint = fingerprint
        repo.languages = lang
        stats.update({"files_total": len(work), "files_new": n_new, "files_modified": n_mod, "files_reindexed": n_reindexed, "files_deleted": len(gone),
                      "files_unchanged": len(unchanged), "files_reparsed_for_edges": reparsed_for_edges, "edges": len(edges),
                      "chunks_added": len(pending), "index_version": repo.index_version, "changed": any_change,
                      "parse_errors": sum(1 for w in work if w.parsed and w.parsed.parse_error)})
    return stats


def _delete_files(s, repo_id: int, file_ids: list[int]) -> None:
    if not file_ids:
        return
    dialect = s.get_bind().dialect.name
    for i in range(0, len(file_ids), 500):
        batch = file_ids[i : i + 500]
        if dialect == "sqlite":
            chunk_ids = [c for (c,) in s.execute(select(Chunk.id).where(Chunk.file_id.in_(batch))).all()]
            for j in range(0, len(chunk_ids), 500):
                part = chunk_ids[j : j + 500]
                s.execute(text(f"DELETE FROM chunk_fts WHERE chunk_id IN ({','.join(str(int(x)) for x in part)})"))
        s.execute(delete(Chunk).where(Chunk.file_id.in_(batch)))
        s.execute(delete(DependencyEdge).where((DependencyEdge.src_file_id.in_(batch)) | (DependencyEdge.dst_file_id.in_(batch))))
        s.execute(delete(Symbol).where(Symbol.file_id.in_(batch)))
        s.execute(delete(SourceFile).where(SourceFile.id.in_(batch)))


def _write_fts(s, repo_id: int, paths: list[str]) -> None:
    """Index chunks of the given files in FTS5 (SQLite). Content is stored as code-aware tokens (camelCase/snake_case split)."""
    if s.get_bind().dialect.name != "sqlite" or not paths:
        return
    rows = s.execute(
        select(Chunk.id, Chunk.content, Chunk.symbol_id, SourceFile.path)
        .join(SourceFile, SourceFile.id == Chunk.file_id)
        .where(Chunk.repository_id == repo_id, SourceFile.path.in_(paths))
    ).all()
    sym_names = {sid: (qn,) for sid, qn in s.execute(select(Symbol.id, Symbol.qualified_name).where(Symbol.repository_id == repo_id)).all()}
    payload = []
    for cid, content, sid, path in rows:
        payload.append((" ".join(tokenize(content, keep_stopwords=True)), " ".join(tokenize(sym_names.get(sid, ("",))[0], keep_stopwords=True)),
                        " ".join(tokenize(path, keep_stopwords=True)), repo_id, cid))
    s.connection().exec_driver_sql(
        "INSERT INTO chunk_fts(content, symbol_names, path, repository_id, chunk_id) VALUES (?,?,?,?,?)", payload
    )
