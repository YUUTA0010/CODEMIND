"""Bug localization: ranked candidate locations with algorithmic evidence kept separate from any model explanation.

A ranking is NOT a proven root cause. `confidence_label` is an interpretation of the score margin and signal agreement,
not a calibrated probability, and each candidate carries explicit uncertainty notes.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.retrieval.context import select_context
from app.retrieval.hybrid import RetrievalResult, retrieve
from app.retrieval.snapshot import RepoSnapshot
from app.retrieval.types import QueryObject, RetrievalConfig
from app.sources import read_lines


@dataclass
class Candidate:
    rank: int
    file_path: str
    symbol_name: str | None
    start_line: int
    end_line: int
    score: float
    signals: dict[str, float]
    evidence: list[str]
    confidence_label: str
    uncertainty: list[str]
    related_tests: list[str]

    def to_dict(self) -> dict:
        return {"rank": self.rank, "file_path": self.file_path, "symbol_name": self.symbol_name, "start_line": self.start_line,
                "end_line": self.end_line, "score": round(self.score, 6), "signals": {k: round(v, 4) for k, v in self.signals.items()},
                "evidence": self.evidence, "confidence_label": self.confidence_label, "uncertainty": self.uncertainty,
                "related_tests": self.related_tests}


def _confidence(score: float, second: float | None, n_signals: int, active: int) -> tuple[str, list[str]]:
    notes = ["A ranking is a retrieval heuristic, not a proven root cause."]
    margin = (score - second) / score if second is not None and score > 0 else 1.0
    if n_signals >= 3 and margin >= 0.15 and score >= 0.5:
        label = "high"
    elif n_signals >= 2 and score >= 0.3:
        label = "medium"
    else:
        label = "low"
    if margin < 0.1:
        notes.append("Near-tied with the next candidate; alternatives are about as plausible.")
    if n_signals < 2:
        notes.append("Supported by a single signal only.")
    if label == "low":
        notes.append("Weak evidence overall; treat as a lead to inspect, not a conclusion.")
    return label, notes


def localize(s: Session, snap: RepoSnapshot, q: QueryObject, cfg: RetrievalConfig, *, max_candidates: int = 10) -> tuple[list[Candidate], RetrievalResult]:
    result = retrieve(s, snap, q, cfg)
    ranked = result.rank_symbols(include_tests=False)
    # symbol-level candidates only (module-level chunks are kept if nothing else exists for that file)
    named = [r for r in ranked if r[1] != "<module>"]
    pool = named if named else ranked
    # file-level fallbacks for files with only module-level hits (e.g. constants, config tables)
    covered = {r[0] for r in pool}
    for r in ranked:
        if r[1] == "<module>" and r[0] not in covered:
            pool.append(r)
            covered.add(r[0])
    pool.sort(key=lambda t: (-t[2], t[0], t[1]))
    tests_by_file: dict[str, list[str]] = {}
    g = snap.graph
    cands: list[Candidate] = []
    for i, (path, sym, score, hit) in enumerate(pool[:max_candidates]):
        second = pool[i + 1][2] if i + 1 < len(pool) else None
        label, notes = _confidence(score, second, len(hit.signals), len(result.signals_used))
        tests: list[str] = []
        if g is not None and hit.symbol_id is not None:
            for u, _, d in g.g.in_edges(f"s{hit.symbol_id}", data=True):
                if d["type"] == "TESTS" and u.startswith("s") and d["confidence"] >= 0.25:
                    tinfo = g.info[u]
                    tests.append(f"{tinfo['path']}::{tinfo['label']}")
        if not tests and g is not None:
            fid = snap.path_to_file.get(path)
            if fid is not None:
                for u, _, d in g.g.in_edges(f"f{fid}", data=True):
                    if d["type"] == "TESTS" and u.startswith("f"):
                        tests.append(g.info[u]["path"])
        cands.append(Candidate(i + 1, path, None if sym == "<module>" else sym, hit.start_line, hit.end_line, score, hit.signals,
                               hit.evidence, label, notes, sorted(set(tests))[:8]))
    return cands, result


def context_for(s: Session, snap: RepoSnapshot, result: RetrievalResult, cfg: RetrievalConfig) -> dict:
    """Budgeted context from a retrieval result; reads current chunk text from the index store."""
    from sqlalchemy import select

    from app.models import Chunk

    ids = [h.chunk_id for h in result.hits]
    contents = dict(s.execute(select(Chunk.id, Chunk.content).where(Chunk.id.in_(ids))).all()) if ids else {}
    return select_context(result.hits, contents, cfg.budget_chars, include_tests=cfg.include_tests_in_context, max_snippets=cfg.top_k)


__all__ = ["Candidate", "localize", "context_for", "read_lines"]
