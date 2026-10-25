"""Bug report -> structured QueryObject. Only the report text is used; benchmark ground truth never reaches this code."""
from __future__ import annotations

import re

from app.retrieval.text import STOPWORDS, identifiers, split_identifier
from app.retrieval.types import QueryObject, TraceFrame

_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([\w<>.]+))?')
_JS_FRAME = re.compile(r"at (?:([\w.$<>]+) \()?([^\s():]+):(\d+)(?::\d+)?\)?")
_PYTEST_ID = re.compile(r"([\w./\\-]+\.py)::([\w:\[\]-]+)")
_ERR_LINE = re.compile(r"^\s*([A-Z][A-Za-z0-9_.]*(?:Error|Exception|Warning)):?\s*(.*)$", re.M)
_BACKTICK = re.compile(r"`([^`\n]{2,80})`")
_CALL_LIKE = re.compile(r"\b([A-Za-z_][\w.]*)\(\)")


def parse_trace(trace: str) -> list[TraceFrame]:
    frames: list[TraceFrame] = []
    for m in _PY_FRAME.finditer(trace or ""):
        frames.append(TraceFrame(m.group(1).replace("\\", "/"), int(m.group(2)), m.group(3)))
    if not frames:
        for m in _JS_FRAME.finditer(trace or ""):
            path = m.group(2)
            if "node_modules" in path or path.startswith("node:"):
                continue
            frames.append(TraceFrame(path.replace("\\", "/"), int(m.group(3)), (m.group(1) or "").split(".")[-1] or None))
    # innermost frame is last in Python tracebacks and first in JS; normalise so index 0 is innermost
    if frames and _PY_FRAME.search(trace or ""):
        frames.reverse()
    return frames[:12]


def _identifier_candidates(text: str) -> list[str]:
    out: list[str] = []
    for m in _BACKTICK.finditer(text):
        out.append(m.group(1).strip())
    for m in _CALL_LIKE.finditer(text):
        out.append(m.group(1))
    for tok in identifiers(text):
        if len(tok) < 3:
            continue
        if "_" in tok or (any(c.isupper() for c in tok[1:]) and any(c.islower() for c in tok)):
            out.append(tok)
    seen, uniq = set(), []
    for t in out:
        k = t.lower()
        if k not in seen and k not in STOPWORDS:
            seen.add(k)
            uniq.append(t)
    return uniq[:40]


def build_query(*, description: str = "", stack_trace: str = "", error_message: str = "", failing_tests: list[str] | None = None,
                reproduction_steps: str = "", expected_behavior: str = "", observed_behavior: str = "") -> QueryObject:
    failing_tests = list(failing_tests or [])
    full_text = "\n".join(x for x in (description, error_message, observed_behavior, expected_behavior, reproduction_steps) if x)
    corpus = "\n".join([full_text, stack_trace])
    for m in _PYTEST_ID.finditer(corpus):
        tid = f"{m.group(1)}::{m.group(2)}"
        if tid not in failing_tests:
            failing_tests.append(tid)
    frames = parse_trace(stack_trace)
    err_terms: list[str] = []
    for m in _ERR_LINE.finditer(stack_trace + "\n" + error_message):
        err_terms.append(m.group(1))
        err_terms += [w for w in identifiers(m.group(2)) if w.lower() not in STOPWORDS][:6]
    idents = _identifier_candidates(corpus)
    for fr in frames:
        if fr.function and fr.function not in ("<module>", "<lambda>") and fr.function not in idents:
            idents.append(fr.function)
    kw: list[str] = []
    seen: set[str] = set()
    for tok in identifiers(full_text):
        for sub in (split_identifier(tok) or [tok.lower()]):
            if len(sub) > 2 and sub not in STOPWORDS and sub not in seen:
                seen.add(sub)
                kw.append(sub)
    for t in failing_tests:
        for sub in split_identifier(t.split("::")[-1]):
            if len(sub) > 2 and sub not in STOPWORDS and sub not in seen:
                seen.add(sub)
                kw.append(sub)
    return QueryObject(text=full_text or error_message or stack_trace, identifiers=idents, keywords=kw[:60], error_terms=err_terms[:20],
                       frames=frames, failing_tests=failing_tests)
