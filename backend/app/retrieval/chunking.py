"""Split a parsed file into retrievable chunks with exact line ranges.

Chunks: one per function/method (outermost), one header chunk per class, residual module-level code in windows,
and fixed windows for files without symbols (config, docs, unparseable files).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.parsing.ir import ParsedFile

MAX_CHUNK_LINES = 120
WINDOW = 60
OVERLAP = 10


@dataclass
class ChunkSpec:
    start_line: int
    end_line: int
    qname: str | None  # owning symbol, if any
    content: str


def _windows(lo: int, hi: int) -> list[tuple[int, int]]:
    out = []
    s = lo
    while s <= hi:
        e = min(s + WINDOW - 1, hi)
        out.append((s, e))
        if e == hi:
            break
        s = e - OVERLAP + 1
    return out


def make_chunks(parsed: ParsedFile, lines: list[str]) -> list[ChunkSpec]:
    n = len(lines)
    if n == 0:
        return []

    def text(a: int, b: int) -> str:
        return "\n".join(lines[a - 1 : b])

    syms = [s for s in parsed.symbols if s.kind != "module"]
    if not syms:
        return [ChunkSpec(a, b, None, text(a, b)) for a, b in _windows(1, n) if text(a, b).strip()]

    covered = [False] * (n + 2)
    chunks: list[ChunkSpec] = []
    callable_kinds = {"function", "method"}
    # outermost callables (skip those nested in another callable)
    callables = [s for s in syms if s.kind in callable_kinds]
    outer: list = []
    for s in sorted(callables, key=lambda x: (x.start_line, -x.end_line)):
        if outer and s.start_line >= outer[-1].start_line and s.end_line <= outer[-1].end_line:
            continue
        outer.append(s)
    for s in outer:
        a, b = s.start_line, min(s.end_line, n)
        if a > n:
            continue
        for lo, hi in ([(a, b)] if b - a + 1 <= MAX_CHUNK_LINES else _windows(a, b)):
            chunks.append(ChunkSpec(lo, hi, s.qualified_name, text(lo, hi)))
        for i in range(a, b + 1):
            covered[i] = True
    for s in syms:
        if s.kind in ("class", "interface", "type"):
            # header = from class start to the line before the first covered member (bounded)
            a = s.start_line
            first_member = next((c.start_line for c in sorted(outer, key=lambda x: x.start_line) if c.start_line > a and c.end_line <= s.end_line), s.end_line + 1)
            b = min(first_member - 1, s.end_line, a + 40, n)
            if b >= a:
                chunks.append(ChunkSpec(a, b, s.qualified_name, text(a, b)))
                for i in range(a, b + 1):
                    covered[i] = True
    # residual module-level lines
    i = 1
    while i <= n:
        if covered[i]:
            i += 1
            continue
        j = i
        while j + 1 <= n and not covered[j + 1]:
            j += 1
        block = text(i, j)
        if block.strip():
            for lo, hi in _windows(i, j):
                t = text(lo, hi)
                if t.strip():
                    chunks.append(ChunkSpec(lo, hi, None, t))
        i = j + 1
    return chunks
