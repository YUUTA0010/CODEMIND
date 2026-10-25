"""Evaluation metrics. Pure functions: ranked list + ground truth in, numbers out. Only evaluation code may call these
with ground truth; the system under test never sees it."""
from __future__ import annotations


def first_relevant_rank(ranked: list[str], relevant: set[str]) -> int | None:
    for i, item in enumerate(ranked, 1):
        if item in relevant:
            return i
    return None


def top_k_hit(ranked: list[str], relevant: set[str], k: int) -> float:
    """1.0 if any relevant item appears in the first k results (Top-k accuracy for a single task)."""
    return 1.0 if any(r in relevant for r in ranked[:k]) else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    r = first_relevant_rank(ranked, relevant)
    return 1.0 / r if r else 0.0


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of ALL relevant items found in the first k results."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    top = ranked[:k]
    return (sum(1 for r in top if r in relevant) / len(top)) if top else 0.0


def irrelevant_count(retrieved: list[str], relevant: set[str]) -> int:
    return len({r for r in retrieved if r not in relevant})


def localization_metrics(ranked: list[str], relevant: set[str], ks: tuple[int, ...] = (1, 3, 5, 10)) -> dict[str, float]:
    m = {"top1": top_k_hit(ranked, relevant, 1), "top5": top_k_hit(ranked, relevant, 5), "mrr": reciprocal_rank(ranked, relevant)}
    for k in ks:
        m[f"recall@{k}"] = recall_at_k(ranked, relevant, k)
    return m


def retrieval_metrics(context_files: list[str], relevant: set[str], context_chars: int, latency_ms: float) -> dict[str, float]:
    uniq = list(dict.fromkeys(context_files))
    return {"context_recall": (len(set(uniq) & relevant) / len(relevant)) if relevant else 0.0, "context_chars": float(context_chars),
            "context_files": float(len(uniq)), "irrelevant_files": float(irrelevant_count(uniq, relevant)), "latency_ms": float(latency_ms)}


def rate(flags: list[bool]) -> float | None:
    return (sum(1 for f in flags if f) / len(flags)) if flags else None
