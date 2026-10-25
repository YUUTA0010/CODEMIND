"""Per-index-version in-memory snapshot of everything retrieval needs (chunk metadata, vectors, graph)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.graph.service import RepoGraph, get_graph
from app.models import Chunk, Repository, SourceFile, Symbol
from app.retrieval.embeddings import from_bytes
from app.retrieval.vector import VectorIndex, make_index


@dataclass
class ChunkMeta:
    id: int
    file_id: int
    path: str
    symbol_id: int | None
    symbol_name: str | None
    start_line: int
    end_line: int
    is_test: bool
    language: str


@dataclass
class SymInfo:
    id: int
    file_id: int
    path: str
    name: str
    qname: str
    kind: str
    is_test: bool
    start_line: int
    end_line: int


@dataclass
class RepoSnapshot:
    repo_id: int
    index_version: int
    fingerprint: str
    root_path: str
    chunks: dict[int, ChunkMeta] = field(default_factory=dict)
    chunks_by_symbol: dict[int, list[int]] = field(default_factory=dict)
    chunks_by_file: dict[int, list[int]] = field(default_factory=dict)
    file_paths: dict[int, str] = field(default_factory=dict)
    path_to_file: dict[str, int] = field(default_factory=dict)
    file_is_test: dict[int, bool] = field(default_factory=dict)
    symbols: dict[int, SymInfo] = field(default_factory=dict)
    symbols_by_name: dict[str, list[int]] = field(default_factory=dict)
    embedding_model: str | None = None
    vectors: VectorIndex | None = None
    chunk_tokens: dict[int, list[str]] | None = None  # lazily built for the pure-Python BM25 fallback
    graph: RepoGraph | None = None


_cache: dict[int, RepoSnapshot] = {}
_lock = threading.Lock()


def get_snapshot(s: Session, repo_id: int) -> RepoSnapshot:
    repo = s.get(Repository, repo_id)
    if repo is None:
        raise NotFoundError(f"repository {repo_id} not found")
    with _lock:
        c = _cache.get(repo_id)
        if c is not None and c.index_version == repo.index_version and c.fingerprint == repo.fingerprint:
            return c
    snap = RepoSnapshot(repo_id, repo.index_version, repo.fingerprint, repo.root_path)
    for f in s.execute(select(SourceFile.id, SourceFile.path, SourceFile.is_test, SourceFile.language).where(SourceFile.repository_id == repo_id)).all():
        snap.file_paths[f.id] = f.path
        snap.path_to_file[f.path] = f.id
        snap.file_is_test[f.id] = f.is_test
    langs = {f.id: f.language for f in s.execute(select(SourceFile.id, SourceFile.language).where(SourceFile.repository_id == repo_id)).all()}
    for sy in s.execute(select(Symbol).where(Symbol.repository_id == repo_id)).scalars():
        snap.symbols[sy.id] = SymInfo(sy.id, sy.file_id, snap.file_paths.get(sy.file_id, ""), sy.name, sy.qualified_name, sy.kind,
                                      sy.is_test or snap.file_is_test.get(sy.file_id, False), sy.start_line, sy.end_line)
        snap.symbols_by_name.setdefault(sy.name.lower(), []).append(sy.id)
    ids, vecs, model = [], [], None
    for row in s.execute(select(Chunk.id, Chunk.file_id, Chunk.symbol_id, Chunk.start_line, Chunk.end_line, Chunk.embedding, Chunk.embedding_model)
                         .where(Chunk.repository_id == repo_id).order_by(Chunk.id)).all():
        sym = snap.symbols.get(row.symbol_id) if row.symbol_id else None
        snap.chunks[row.id] = ChunkMeta(row.id, row.file_id, snap.file_paths.get(row.file_id, ""), row.symbol_id, sym.qname if sym else None,
                                        row.start_line, row.end_line, snap.file_is_test.get(row.file_id, False) or bool(sym and sym.is_test),
                                        langs.get(row.file_id, ""))
        snap.chunks_by_file.setdefault(row.file_id, []).append(row.id)
        if row.symbol_id:
            snap.chunks_by_symbol.setdefault(row.symbol_id, []).append(row.id)
        if row.embedding is not None:
            ids.append(row.id)
            vecs.append(from_bytes(row.embedding))
            model = row.embedding_model
    if ids:
        idx = make_index("bruteforce")
        idx.build(ids, np.vstack(vecs))
        snap.vectors = idx
        snap.embedding_model = model
    snap.graph = get_graph(s, repo_id)
    with _lock:
        _cache[repo_id] = snap
    return snap


def invalidate_snapshot(repo_id: int | None = None) -> None:
    with _lock:
        if repo_id is None:
            _cache.clear()
        else:
            _cache.pop(repo_id, None)
