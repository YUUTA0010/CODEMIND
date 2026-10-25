"""Lexical retrieval: BM25 via SQLite FTS5 (primary) with a pure-Python BM25 fallback (PostgreSQL / FTS unavailable).

Chunk text is indexed as code-aware tokens (identifiers lower-cased plus camelCase/snake_case sub-tokens) in three
columns - content, symbol_names, path - and `bm25()` is called with column weights so symbol/path matches count more.
"""
from __future__ import annotations

import math
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk
from app.retrieval.snapshot import RepoSnapshot
from app.retrieval.text import tokenize
from app.retrieval.types import QueryObject

FTS_WEIGHTS = (1.0, 4.0, 2.0)  # content, symbol_names, path
MAX_TERMS = 48


def query_terms(q: QueryObject) -> list[str]:
    """Ordered, de-duplicated search terms: identifiers first, then error terms, then natural-language keywords."""
    seen: set[str] = set()
    out: list[str] = []
    for group in (q.identifiers, q.error_terms, q.keywords):
        for item in group:
            for tok in tokenize(item):
                if tok not in seen and tok.isalnum():
                    seen.add(tok)
                    out.append(tok)
    return out[:MAX_TERMS]


def build_match(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in terms)


def fts_search(s: Session, repo_id: int, terms: list[str], limit: int) -> dict[int, float]:
    """-> {chunk_id: positive BM25 score}. Higher is better."""
    if not terms:
        return {}
    w = ", ".join(str(x) for x in FTS_WEIGHTS)
    sql = (
        f"SELECT chunk_id, -bm25(chunk_fts, {w}) AS score FROM chunk_fts "
        "WHERE chunk_fts MATCH ? AND repository_id = ? ORDER BY score DESC, chunk_id ASC LIMIT ?"
    )
    rows = s.connection().exec_driver_sql(sql, (build_match(terms), repo_id, limit)).all()
    return {int(cid): float(sc) for cid, sc in rows if sc > 0}


class PyBM25:
    """Reference BM25 implementation (k1=1.2, b=0.75) over the same token streams; used as fallback and as a test oracle."""

    def __init__(self, docs: dict[int, list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = {i: Counter(t) for i, t in docs.items()}
        self.len = {i: len(t) for i, t in docs.items()}
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 1.0
        self.df: Counter = Counter()
        for c in self.tf.values():
            self.df.update(c.keys())
        self.n = len(docs)

    def search(self, terms: list[str], limit: int) -> dict[int, float]:
        scores: dict[int, float] = {}
        for term in set(terms):
            df = self.df.get(term, 0)
            if not df:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for i, c in self.tf.items():
                f = c.get(term, 0)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
                    scores[i] = scores.get(i, 0.0) + idf * f * (self.k1 + 1) / denom
        top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        return dict(top)


def _fallback_docs(s: Session, snap: RepoSnapshot) -> dict[int, list[str]]:
    if snap.chunk_tokens is None:
        docs: dict[int, list[str]] = {}
        for cid, content in s.execute(select(Chunk.id, Chunk.content).where(Chunk.repository_id == snap.repo_id)).all():
            meta = snap.chunks[cid]
            toks = tokenize(content, keep_stopwords=True)
            toks += tokenize(meta.symbol_name or "", keep_stopwords=True) * 4
            toks += tokenize(meta.path, keep_stopwords=True) * 2
            docs[cid] = toks
        snap.chunk_tokens = docs
    return snap.chunk_tokens


def lexical_scores(s: Session, snap: RepoSnapshot, q: QueryObject, limit: int) -> dict[int, float]:
    terms = query_terms(q)
    if s.get_bind().dialect.name == "sqlite":
        try:
            return fts_search(s, snap.repo_id, terms, limit)
        except Exception:  # FTS5 unavailable or malformed table -> fall back transparently
            pass
    return PyBM25(_fallback_docs(s, snap)).search(terms, limit)
