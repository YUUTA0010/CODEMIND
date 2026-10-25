"""Safe source access: all reads resolve strictly inside the repository root (no traversal, no symlink escape)."""
from __future__ import annotations

from pathlib import Path

from app.core.errors import AppError, SecurityViolation


def safe_join(root: str | Path, rel: str) -> Path:
    root_p = Path(root).resolve()
    if not rel or "\x00" in rel:
        raise SecurityViolation("empty or invalid path")
    candidate = (root_p / rel).resolve()
    if candidate != root_p and root_p not in candidate.parents:
        raise SecurityViolation(f"path escapes repository root: {rel}")
    return candidate


def read_lines(root: str | Path, rel: str, start: int | None = None, end: int | None = None, max_bytes: int = 2_000_000) -> list[str]:
    p = safe_join(root, rel)
    if not p.is_file():
        raise AppError(f"not a file: {rel}", code="not_found", status_code=404)
    if p.stat().st_size > max_bytes:
        raise AppError("file too large to display", code="too_large", status_code=413)
    lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if start is None:
        return lines
    return lines[max(0, start - 1): end]
