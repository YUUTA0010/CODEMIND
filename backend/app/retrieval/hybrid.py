"""Retrieval orchestrator: computes enabled signals, normalises them, fuses with configurable weights.

Methods (see types.METHOD_SIGNALS): lexical | semantic | hybrid_conventional | dependency_aware | hybrid_dependency | adaptive.
`disabled_signals` implements the ablation mode. Scores are normalised per signal by the per-query maximum and the
final score is a weight-normalised linear combination over the *active* signals, so weights are relative.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from sqlalchemy.orm import Session

from app.retrieval import signals as sig
from app.retrieval.embeddings import Embedder, get_embedder
from app.retrieval.history import file_churn
from app.retrieval.lexical import lexical_scores
from app.retrieval.snapshot import RepoSnapshot
from app.retrieval.types import Hit, QueryObject, RetrievalConfig


@dataclass
class RetrievalResult:
    hits: list[Hit]
    config: RetrievalConfig
    signals_used: list[str]
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    runtime_ms: float = 0.0
    index_version: int = 0
    embedding_model: str | None = None

    def rank_files(self, include_tests: bool = False) -> list[tuple[str, float]]:
        best: dict[str, float] = {}
        for h in self.hits:
            if h.is_test and not include_tests:
                continue
            if h.score > best.get(h.path, 0.0):
                best[h.path] = h.score
        return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))

    def rank_symbols(self, include_tests: bool = False) -> list[tuple[str, str, float, Hit]]:
        best: dict[tuple[str, str], Hit] = {}
        for h in self.hits:
            if h.is_test and not include_tests:
                continue
            key = (h.path, h.symbol_name or "<module>")
            if key not in best or h.score > best[key].score:
                best[key] = h
        out = [(k[0], k[1], h.score, h) for k, h in best.items()]
        return sorted(out, key=lambda t: (-t[2], t[0], t[1]))


def _norm(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    mx = max(scores.values())
    return {k: v / mx for k, v in scores.items() if v > 0} if mx > 0 else {}


def _top(scores: dict[int, float], n: int) -> list[int]:
    return [k for k, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def retrieve(s: Session, snap: RepoSnapshot, q: QueryObject, cfg: RetrievalConfig, embedder: Embedder | None = None) -> RetrievalResult:
    if cfg.method == "adaptive":
        return _adaptive(s, snap, q, cfg, embedder)
    t0 = time.perf_counter()
    active = cfg.active_signals()
    warnings: list[str] = []
    diag: dict = {}
    raw: dict[str, dict[int, float]] = {}
    evid: dict[str, dict[int, list[str]]] = {}

    if "lexical" in active:
        raw["lexical"] = lexical_scores(s, snap, q, cfg.candidate_pool * 3)
    if "symbol_exact" in active:
        raw["symbol_exact"], evid["symbol_exact"] = sig.symbol_exact(snap, q)
    if "trace" in active and q.frames:
        raw["trace"], evid["trace"] = sig.trace_signal(snap, q)
    if "semantic" in active:
        emb = embedder or get_embedder()
        if snap.vectors is None:
            warnings.append("semantic signal unavailable: no chunk embeddings are stored for this repository")
        elif snap.embedding_model != emb.name:
            warnings.append(f"semantic signal skipped: index embeddings were produced by '{snap.embedding_model}' but the active embedder is '{emb.name}'; reindex")
        else:
            qv = emb.embed([q.text or " ".join(q.keywords + q.identifiers)])[0]
            hits = snap.vectors.search(qv, cfg.candidate_pool * 3)
            raw["semantic"] = {cid: max(0.0, c) for cid, c in hits if c > 0}
            diag["semantic_is_neural"] = bool(emb.is_neural)
            if not emb.is_neural:
                warnings.append("semantic signal uses the offline hashing embedder (sub-word overlap, not neural semantics)")

    norm = {k: _norm(v) for k, v in raw.items()}
    weights = {k: cfg.weights.get(k, 0.0) for k in active}

    base = [k for k in ("lexical", "symbol_exact", "semantic", "trace") if k in norm]
    if base:
        wsum = sum(weights[k] for k in base) or 1.0
        fused: dict[int, float] = {}
        for k in base:
            for cid, v in norm[k].items():
                fused[cid] = fused.get(cid, 0.0) + weights[k] * v / wsum
    else:
        fused = {}

    if "test" in active:
        tscores, tev = sig.test_signal(snap, q, norm.get("lexical", {}), cfg)
        raw["test"], evid["test"] = tscores, tev
        norm["test"] = _norm(tscores)
    if "graph" in active:
        seeds: dict[int, float] = {}
        for cid in _top(fused, cfg.seed_count * 3):
            sid = snap.chunks[cid].symbol_id
            if sid is not None and snap.symbols.get(sid) and snap.symbols[sid].kind != "module":
                seeds[sid] = max(seeds.get(sid, 0.0), fused[cid])
            if len(seeds) >= cfg.seed_count:
                break
        gscores, gev, gdiag = sig.graph_signal(snap, seeds, cfg)
        raw["graph"], evid["graph"] = gscores, gev
        norm["graph"] = _norm(gscores)
        diag["graph"] = {**gdiag, "seed_symbols": [snap.symbols[i].qname for i in seeds]}
    if "history" in active:
        churn = file_churn(snap.root_path)
        if not churn:
            warnings.append("history signal unavailable (not a git repository or git not accessible)")
        else:
            pool_ids = set(_top(fused, cfg.candidate_pool))
            raw["history"] = {cid: churn.get(snap.chunks[cid].path, 0.0) for cid in pool_ids}
            norm["history"] = _norm(raw["history"])

    pool: set[int] = set()
    for k, d in norm.items():
        pool.update(_top(d, cfg.candidate_pool))
    wsum_all = sum(weights.get(k, 0.0) for k in active) or 1.0
    hits: list[Hit] = []
    for cid in pool:
        m = snap.chunks[cid]
        signals = {k: norm[k][cid] for k in norm if cid in norm[k]}
        score = sum(weights.get(k, 0.0) * v for k, v in signals.items()) / wsum_all
        if score <= 0:
            continue
        evidence = []
        for k in signals:
            if k in evid and cid in evid[k]:
                evidence += [f"[{k}] {e}" for e in evid[k][cid][:3]]
            elif k == "lexical":
                evidence.append(f"[lexical] BM25 relevance {signals[k]:.2f} (normalised)")
            elif k == "semantic":
                evidence.append(f"[semantic] cosine similarity {signals[k]:.2f} (normalised)")
            elif k == "history":
                evidence.append(f"[history] file change frequency {signals[k]:.2f}")
        hits.append(Hit(cid, m.file_id, m.path, m.symbol_id, m.symbol_name, m.start_line, m.end_line, m.is_test, score, signals, evidence))
    hits.sort(key=lambda h: (-h.score, h.path, h.start_line))
    hits = hits[: max(cfg.top_k * 3, cfg.top_k)]
    return RetrievalResult(hits, cfg, active, warnings, diag, (time.perf_counter() - t0) * 1000.0, snap.index_version, snap.embedding_model)


def _margin(res: RetrievalResult, k: int = 5) -> float:
    syms = res.rank_symbols()
    if not syms or syms[0][2] <= 0:
        return 0.0
    kth = syms[min(k, len(syms)) - 1][2]
    return (syms[0][2] - kth) / syms[0][2]


def _adaptive(s: Session, snap: RepoSnapshot, q: QueryObject, cfg: RetrievalConfig, embedder: Embedder | None) -> RetrievalResult:
    """Adaptive expansion: run the full hybrid; if the ranking is flat (small top-1 vs top-5 margin = high diagnostic
    uncertainty) widen graph depth, seed count, graph weight and candidate pool, then re-run. Deterministic."""
    t0 = time.perf_counter()
    first = retrieve(s, snap, q, replace(cfg, method="hybrid_dependency"), embedder)
    margin = _margin(first)
    diag = {"adaptive": {"initial_margin": round(margin, 4), "threshold": cfg.adaptive_margin_threshold, "expanded": False}}
    result = first
    if margin < cfg.adaptive_margin_threshold:
        w = dict(cfg.weights)
        w["graph"] = w.get("graph", 0.15) * 1.5
        expanded = replace(cfg, method="hybrid_dependency", graph_depth=cfg.graph_depth + 1, seed_count=cfg.seed_count * 2,
                           candidate_pool=int(cfg.candidate_pool * 1.5), weights=w)
        result = retrieve(s, snap, q, expanded, embedder)
        diag["adaptive"].update({"expanded": True, "final_margin": round(_margin(result), 4),
                                 "expanded_config": {"graph_depth": expanded.graph_depth, "seed_count": expanded.seed_count}})
    result.diagnostics = {**result.diagnostics, **diag}
    result.config = cfg
    result.runtime_ms = (time.perf_counter() - t0) * 1000.0
    return result
