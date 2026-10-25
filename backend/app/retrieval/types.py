"""Shared retrieval data types."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

SIGNALS = ["lexical", "symbol_exact", "semantic", "graph", "test", "trace", "history"]
METHODS = ["lexical", "semantic", "hybrid_conventional", "dependency_aware", "hybrid_dependency", "adaptive"]

# Defaults are an arbitrary, documented starting point - NOT claimed optimal. Experiments may tune on a train split only.
DEFAULT_WEIGHTS: dict[str, float] = {
    "lexical": 0.30, "symbol_exact": 0.15, "semantic": 0.20, "graph": 0.15, "test": 0.10, "trace": 0.10, "history": 0.0,
}

# Which signals each method is *allowed* to use (the method definitions of the experiment).
METHOD_SIGNALS: dict[str, list[str]] = {
    "lexical": ["lexical", "symbol_exact", "trace"],
    "semantic": ["semantic"],
    "hybrid_conventional": ["lexical", "symbol_exact", "semantic", "trace"],
    "dependency_aware": ["lexical", "symbol_exact", "graph", "test", "trace"],
    "hybrid_dependency": ["lexical", "symbol_exact", "semantic", "graph", "test", "trace", "history"],
    "adaptive": ["lexical", "symbol_exact", "semantic", "graph", "test", "trace", "history"],
}


@dataclass
class RetrievalConfig:
    method: str = "hybrid_dependency"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    disabled_signals: list[str] = field(default_factory=list)  # ablation switch
    top_k: int = 20
    budget_chars: int = 24_000
    graph_depth: int = 2
    graph_decay: float = 0.5
    min_edge_confidence: float = 0.25
    seed_count: int = 8
    candidate_pool: int = 60
    include_tests_in_context: bool = True
    adaptive_margin_threshold: float = 0.25
    seed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "RetrievalConfig":
        d = dict(d or {})
        base = cls()
        known = set(asdict(base))
        for k in list(d):
            if k not in known:
                d.pop(k)
        w = dict(DEFAULT_WEIGHTS)
        w.update(d.pop("weights", {}) or {})
        out = cls(**{**asdict(base), **d})
        out.weights = w
        return out

    def active_signals(self) -> list[str]:
        allowed = METHOD_SIGNALS.get(self.method, SIGNALS)
        return [s for s in allowed if s not in self.disabled_signals and self.weights.get(s, 0.0) > 0.0]


@dataclass
class TraceFrame:
    path: str
    line: int | None
    function: str | None


@dataclass
class QueryObject:
    """Structured query. Built from a bug report (never from benchmark ground truth)."""

    text: str  # full free text (used for embeddings)
    identifiers: list[str] = field(default_factory=list)  # code-like tokens (exact-match candidates)
    keywords: list[str] = field(default_factory=list)  # natural-language terms
    error_terms: list[str] = field(default_factory=list)
    frames: list[TraceFrame] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hit:
    chunk_id: int
    file_id: int
    path: str
    symbol_id: int | None
    symbol_name: str | None
    start_line: int
    end_line: int
    is_test: bool
    score: float
    signals: dict[str, float] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = round(self.score, 6)
        d["signals"] = {k: round(v, 6) for k, v in self.signals.items()}
        return d
