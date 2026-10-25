from __future__ import annotations

import collections
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.auth import Principal, developer, viewer
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.session import get_db
from app.graph.service import EDGE_TYPES, get_graph
from app.indexing.indexer import create_job, run_indexing_job
from app.indexing.ingest import detect_repository_root, read_git_head
from app.localization.engine import context_for, localize
from app.localization.report import build_query
from app.models import DependencyEdge, IndexingJob, Repository, SourceFile, Symbol
from app.patches.service import audit
from app.repositories.service import (active_job, get_repo, job_dict, record_run, repo_dict, require_indexed)
from app.core.runtime_settings import default_retrieval_config
from app.retrieval.snapshot import get_snapshot
from app.schemas.api import IndexRequest, RepositoryCreate, SearchRequest
from app.sources import read_lines

router = APIRouter(prefix="/api/v1", tags=["repositories"])
_BLOCKED_ROOTS = {"/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/var", "/proc", "/sys", "/dev", "/boot", "/root"}


@router.post("/repositories", status_code=201)
def create_repository(body: RepositoryCreate, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    root, is_git = detect_repository_root(body.path)
    if str(root) in _BLOCKED_ROOTS:
        raise AppError("refusing to index a system directory", code="invalid_path")
    existing = db.execute(select(Repository).where(Repository.root_path == str(root))).scalar_one_or_none()
    if existing:
        raise ConflictError("repository already registered", {"repository_id": existing.id})
    adjusted = str(Path(body.path).expanduser().resolve()) != str(root)
    repo = Repository(name=body.name or root.name, root_path=str(root), is_git=is_git, kind="user")
    db.add(repo)
    db.flush()
    audit(db, "repository.registered", "repository", repo.id, {"path": str(root), "git_head": read_git_head(root) if is_git else None}, p.username)
    out = repo_dict(db, repo)
    out["root_adjusted_to_git_root"] = adjusted
    if body.auto_index:
        job = create_job(db, repo, "full")
        db.commit()
        jobs.submit(run_indexing_job, job.id, body.ignore_patterns or None)
        out["latest_job"] = job_dict(db.get(IndexingJob, job.id))
    return out


@router.get("/repositories")
def list_repositories(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    q = select(Repository).where(Repository.kind != "benchmark")
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(Repository.id.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [repo_dict(db, r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    d = repo_dict(db, repo)
    d["language_stats"] = repo.languages
    return d


@router.post("/repositories/{repository_id}/index", status_code=202)
def start_index(repository_id: int, body: IndexRequest | None = None, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    repo = get_repo(db, repository_id)
    if not Path(repo.root_path).is_dir():
        raise AppError("repository path no longer exists", code="invalid_path", status_code=409)
    running = active_job(db, repository_id)
    if running:
        raise ConflictError("an indexing job is already queued or running", {"job_id": running.id})
    mode = (body.mode if body else "incremental")
    job = create_job(db, repo, mode)
    audit(db, "index.started", "repository", repository_id, {"mode": mode, "job_id": job.id}, p.username)
    db.commit()
    jobs.submit(run_indexing_job, job.id)
    return job_dict(db.get(IndexingJob, job.id))


@router.get("/repositories/{repository_id}/index-status")
def index_status(repository_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    job = db.execute(select(IndexingJob).where(IndexingJob.repository_id == repository_id).order_by(IndexingJob.id.desc()).limit(1)).scalar_one_or_none()
    return {"repository_id": repository_id, "repository_status": repo.status, "index_version": repo.index_version, "job": job_dict(job) if job else None}


@router.post("/repositories/{repository_id}/index/cancel")
def cancel_index(repository_id: int, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    job = active_job(db, repository_id)
    if job is None:
        raise NotFoundError("no active indexing job")
    job.cancel_requested = True
    audit(db, "index.cancel_requested", "repository", repository_id, {"job_id": job.id}, p.username)
    return job_dict(job)


@router.get("/repositories/{repository_id}/files")
def file_tree(repository_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    get_repo(db, repository_id)
    rows = db.execute(select(SourceFile.path, SourceFile.language, SourceFile.line_count, SourceFile.is_test, SourceFile.parse_error)
                      .where(SourceFile.repository_id == repository_id).order_by(SourceFile.path)).all()
    langs = collections.Counter(r.language for r in rows)
    return {"files": [{"path": r.path, "language": r.language, "lines": r.line_count, "is_test": r.is_test, "parse_error": r.parse_error} for r in rows],
            "language_counts": dict(langs), "total": len(rows)}


@router.get("/repositories/{repository_id}/file")
def read_file(repository_id: int, path: str = Query(..., max_length=1000), start: int | None = Query(None, ge=1), end: int | None = Query(None, ge=1),
              db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    sf = db.execute(select(SourceFile).where(SourceFile.repository_id == repository_id, SourceFile.path == path)).scalar_one_or_none()
    if sf is None:
        raise NotFoundError("file is not part of the index")
    lines = read_lines(repo.root_path, path)
    s0 = start or 1
    e0 = min(end or len(lines), len(lines))
    stale = False
    return {"path": path, "language": sf.language, "start_line": s0, "end_line": e0, "total_lines": len(lines), "content": "\n".join(lines[s0 - 1:e0]),
            "stale": stale, "indexed_sha256": sf.sha256, "note": "content is read from disk; reindex if the file changed since indexing"}


@router.get("/repositories/{repository_id}/symbols")
def list_symbols(repository_id: int, q: str | None = Query(None, max_length=200), kind: str | None = Query(None, max_length=20),
                 path: str | None = Query(None, max_length=1000), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    get_repo(db, repository_id)
    stmt = (select(Symbol, SourceFile.path, SourceFile.language).join(SourceFile, SourceFile.id == Symbol.file_id)
            .where(Symbol.repository_id == repository_id, Symbol.kind != "module"))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # escape LIKE wildcards instead of deleting characters
        stmt = stmt.where(Symbol.qualified_name.ilike(f"%{escaped}%", escape="\\"))
    if kind:
        stmt = stmt.where(Symbol.kind == kind)
    if path:
        stmt = stmt.where(SourceFile.path.like(path.rstrip("/") + "%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(SourceFile.path, Symbol.start_line).limit(limit).offset(offset)).all()
    items = [{"id": s.id, "node_id": f"s{s.id}", "kind": s.kind, "name": s.name, "qualified_name": s.qualified_name, "signature": s.signature, "docstring": s.docstring[:300],
              "path": p_, "language": lang, "start_line": s.start_line, "end_line": s.end_line, "is_test": s.is_test} for s, p_, lang in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/repositories/{repository_id}/graph")
def graph(repository_id: int, level: str = Query("file", pattern="^(file|symbol)$"), language: list[str] | None = Query(None), directory: str | None = Query(None, max_length=500),
          edge_type: list[str] | None = Query(None), min_confidence: float = Query(0.0, ge=0, le=1), include_tests: bool = True,
          center: str | None = Query(None, max_length=40), depth: int = Query(2, ge=0, le=5), max_nodes: int = Query(300, ge=10, le=2000),
          db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    require_indexed(repo)
    if edge_type:
        bad = [e for e in edge_type if e not in EDGE_TYPES]
        if bad:
            raise AppError(f"unknown edge types: {bad}", code="validation_error", status_code=422)
    g = get_graph(db, repository_id)
    if center and center not in g.info:
        raise NotFoundError(f"graph node {center} not found")
    out = g.export(level=level, languages=language, directory=directory, edge_types=edge_type, min_confidence=min_confidence, include_tests=include_tests,
                   center=center, depth=depth, max_nodes=max_nodes)
    out["index_version"] = repo.index_version
    out["resolution_legend"] = {"CONFIRMED": "syntactically/lexically determined", "INFERRED": "heuristic, lower confidence", "UNRESOLVED": "not drawn (external/dynamic/ambiguous)"}
    return out


@router.get("/repositories/{repository_id}/graph/node/{node_id}")
def graph_node(repository_id: int, node_id: str, depth: int = Query(1, ge=1, le=4), min_confidence: float = Query(0.0, ge=0, le=1),
               db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    require_indexed(repo)
    g = get_graph(db, repository_id)
    if node_id not in g.info:
        raise NotFoundError(f"graph node {node_id} not found")
    nb = g.neighborhood(node_id, depth, None, min_confidence)
    return {"node": g.info[node_id], "dependencies": g.direct_dependencies(node_id, None, min_confidence), "dependents": g.reverse_dependencies(node_id, None, min_confidence),
            "callers": g.callers(node_id, min_confidence), "callees": g.callees(node_id, min_confidence),
            "blast_radius": [{"node": g.info[n], "hops": d} for n, d in sorted(g.transitive_reverse(node_id).items(), key=lambda kv: kv[1]) if n != node_id][:100],
            "neighborhood": {"count": len(nb["distances"]), "truncated": nb["truncated"]}, "unresolved": g.unresolved_for(node_id)}


@router.get("/repositories/{repository_id}/graph/path")
def graph_path(repository_id: int, source: str, target: str, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    require_indexed(repo)
    g = get_graph(db, repository_id)
    path = g.shortest_path(source, target)
    return {"path": None if path is None else [g.info[n] for n in path], "note": "undirected shortest path over typed edges; unresolved edges are never traversed"}


@router.get("/repositories/{repository_id}/graph/analysis")
def graph_analysis(repository_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repo = get_repo(db, repository_id)
    require_indexed(repo)
    g = get_graph(db, repository_id)
    counts = collections.Counter(db.execute(select(DependencyEdge.edge_type, DependencyEdge.resolution).where(DependencyEdge.repository_id == repository_id)).all())
    return {"components": g.components()[:25], "degree_centrality": g.degree_centrality(20),
            "edge_counts": [{"edge_type": k[0], "resolution": k[1], "count": v} for k, v in sorted(counts.items())]}


@router.post("/search")
def search(body: SearchRequest, db: Session = Depends(get_db), p: Principal = Depends(viewer)):
    repo = get_repo(db, body.repository_id)
    require_indexed(repo)
    snap = get_snapshot(db, repo.id)
    method = {"symbol": "lexical", "lexical": "lexical", "semantic": "semantic", "hybrid": "hybrid_dependency", "dependency": "dependency_aware"}[body.mode]
    disabled = list(body.disabled_signals) + (["lexical", "trace"] if body.mode == "symbol" else [])
    cfg = default_retrieval_config(method=method, disabled_signals=disabled, top_k=body.limit)
    q = build_query(description=body.query, error_message=body.query if any(w in body.query for w in ("Error", "Exception")) else "")
    if not q.identifiers:  # a bare token like `apply_discount` or `Cart.add_item` is itself an identifier query
        q.identifiers = [t for t in body.query.replace("(", " ").replace(")", " ").split() if t.replace(".", "").replace("_", "").isalnum()][:10]
    cands, result = localize(db, snap, q, cfg, max_candidates=body.limit)
    ctx = context_for(db, snap, result, cfg)
    run = record_run(db, repo.id, None, body.query, cfg, result, ctx)
    sn_by_key = {(s["path"], s["start_line"]): s for s in ctx["selected"]}
    results = []
    for c in cands:
        sn = sn_by_key.get((c.file_path, c.start_line))
        results.append({"rank": c.rank, "path": c.file_path, "symbol": c.symbol_name, "start_line": c.start_line, "end_line": c.end_line, "score": round(c.score, 6),
                        "signals": {k: round(v, 4) for k, v in c.signals.items()}, "explanation": c.evidence,
                        "snippet": sn["content"] if sn else None})
    return {"run_id": run.id, "mode": body.mode, "method": cfg.method, "results": results, "runtime_ms": round(result.runtime_ms, 2), "index_version": result.index_version,
            "active_signals": result.signals_used, "warnings": result.warnings, "omitted_by_budget": ctx["omitted_due_to_budget"]}
