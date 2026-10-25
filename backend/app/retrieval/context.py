"""Budgeted context selection with de-duplication. Nothing is dropped silently: every omitted candidate is reported
with its reason, and a snippet is only truncated (explicitly flagged) when it alone exceeds the whole budget."""
from __future__ import annotations

from app.retrieval.types import Hit

HEADER_OVERHEAD = 96  # characters reserved per snippet for its "path:start-end" header when rendered


def _overlap_fraction(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    inter = max(0, min(a_end, b_end) - max(a_start, b_start) + 1)
    return inter / max(1, a_end - a_start + 1)


def select_context(hits: list[Hit], contents: dict[int, str], budget_chars: int, *, include_tests: bool = True,
                   max_snippets: int | None = None) -> dict:
    selected: list[dict] = []
    omitted: list[dict] = []
    used = 0
    dedup = 0
    for h in hits:
        body = contents.get(h.chunk_id, "")
        ref = {"path": h.path, "start_line": h.start_line, "end_line": h.end_line, "score": round(h.score, 6), "chunk_id": h.chunk_id}
        if h.is_test and not include_tests:
            omitted.append({**ref, "reason": "tests excluded by configuration"})
            continue
        if any(s["path"] == h.path and _overlap_fraction(h.start_line, h.end_line, s["start_line"], s["end_line"]) >= 0.5 for s in selected):
            dedup += 1
            omitted.append({**ref, "reason": "overlaps a higher-ranked selected snippet"})
            continue
        if max_snippets is not None and len(selected) >= max_snippets:
            omitted.append({**ref, "reason": "snippet count limit"})
            continue
        size = len(body) + HEADER_OVERHEAD
        truncated = False
        if used + size > budget_chars:
            if not selected and size > budget_chars:
                keep = max(0, budget_chars - HEADER_OVERHEAD)
                body, truncated, size = body[:keep], True, budget_chars
            else:
                omitted.append({**ref, "reason": "context budget exhausted"})
                continue
        used += size
        selected.append({
            "chunk_id": h.chunk_id, "path": h.path, "start_line": h.start_line, "end_line": h.end_line, "symbol": h.symbol_name,
            "score": round(h.score, 6), "signals": {k: round(v, 4) for k, v in h.signals.items()}, "evidence": h.evidence,
            "is_test": h.is_test, "content": body, "truncated": truncated,
            "original_range": [h.start_line, h.end_line] if truncated else None,
        })
    return {"selected": selected, "omitted": omitted, "used_chars": used, "budget_chars": budget_chars, "deduplicated": dedup,
            "omitted_due_to_budget": sum(1 for o in omitted if o["reason"] == "context budget exhausted")}
