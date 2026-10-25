"""Individual ranking signals. Each returns raw scores per chunk id plus human-readable evidence; the orchestrator
normalises them (divide by the per-query maximum) and combines them with configurable weights."""
from __future__ import annotations

from collections import defaultdict

from app.retrieval.snapshot import RepoSnapshot
from app.retrieval.text import split_identifier
from app.retrieval.types import QueryObject, RetrievalConfig

Scores = dict[int, float]
Evidence = dict[int, list[str]]

# (outgoing weight, incoming weight) when walking an edge of this type away from a relevant node
EDGE_WEIGHTS: dict[str, tuple[float, float]] = {
    "CALLS": (1.0, 0.8),       # relevant function -> what it calls / who calls it
    "INHERITS": (0.8, 0.8),
    "REFERENCES": (0.6, 0.6),
    "TESTS": (0.7, 0.5),       # test -> target / target -> its tests
    "IMPORTS": (0.5, 0.5),
    "DEFINES": (0.4, 0.4),     # class <-> member (symbol level only)
}
HUB_FREE_DEGREE = 8
HUB_PENALTY = 0.1


def _add(ev: Evidence, cid: int, msg: str) -> None:
    lst = ev.setdefault(cid, [])
    if msg not in lst and len(lst) < 6:
        lst.append(msg)


def _identifier_like(tok: str) -> bool:
    return "_" in tok or any(c.isupper() for c in tok[1:]) or "." in tok or tok.endswith("()")


# ---------------------------------------------------------------------------------------------- symbol exact
def symbol_exact(snap: RepoSnapshot, q: QueryObject) -> tuple[Scores, Evidence]:
    """Exact (1.0) and sub-token (<=0.6) matches between identifier-like query tokens and symbol names."""
    scores: Scores = defaultdict(float)
    ev: Evidence = {}
    for raw in q.identifiers:
        tok = raw.rstrip("()").strip(".")
        if not tok:
            continue
        last = tok.split(".")[-1]
        low = last.lower()
        exact = snap.symbols_by_name.get(low, [])
        for sid in exact:
            sym = snap.symbols[sid]
            if sym.kind == "module":
                continue
            # qualified-name match (Class.method) is stronger than a bare name match
            strong = "." in tok and sym.qname.lower().endswith(tok.lower())
            for cid in snap.chunks_by_symbol.get(sid, []):
                val = 1.0 if (strong or "." not in tok) else 0.8
                if val > scores[cid]:
                    scores[cid] = val
                    _add(ev, cid, f"exact symbol match: `{tok}`")
        if _identifier_like(tok):
            subs = set(split_identifier(last))
            if len(subs) >= 2:
                for sid, sym in snap.symbols.items():
                    if sym.kind == "module" or sym.name.lower() == low:
                        continue
                    ssubs = set(split_identifier(sym.name))
                    if not ssubs:
                        continue
                    overlap = len(subs & ssubs) / max(len(subs | ssubs), 1)
                    if overlap >= 0.5:
                        for cid in snap.chunks_by_symbol.get(sid, []):
                            val = 0.6 * overlap
                            if val > scores[cid]:
                                scores[cid] = val
                                _add(ev, cid, f"sub-token overlap with `{tok}` ({overlap:.2f})")
    return dict(scores), ev


# ---------------------------------------------------------------------------------------------- trace
def resolve_frame_files(snap: RepoSnapshot, path: str) -> list[int]:
    p = path.replace("\\", "/").lstrip("./")
    exact = snap.path_to_file.get(p)
    if exact is not None:
        return [exact]
    hits = [fid for fp, fid in snap.path_to_file.items() if p.endswith("/" + fp) or fp.endswith("/" + p) or p == fp]
    if hits:
        # prefer the longest path overlap (most specific)
        hits.sort(key=lambda fid: -len(snap.file_paths[fid]))
        best = len(snap.file_paths[hits[0]]) if p.endswith(snap.file_paths[hits[0]]) else 0
        return [h for h in hits if (len(snap.file_paths[h]) == best) or best == 0][:3]
    return []


def trace_signal(snap: RepoSnapshot, q: QueryObject) -> tuple[Scores, Evidence]:
    scores: Scores = defaultdict(float)
    ev: Evidence = {}
    for rank, fr in enumerate(q.frames):
        w = 0.85 ** rank  # innermost frame is the most informative
        for fid in resolve_frame_files(snap, fr.path):
            for cid in snap.chunks_by_file.get(fid, []):
                m = snap.chunks[cid]
                val = 0.0
                why = ""
                if fr.line is not None and m.start_line <= fr.line <= m.end_line:
                    val, why = 1.0, f"stack frame {fr.path}:{fr.line}"
                elif fr.function and m.symbol_name and m.symbol_name.split(".")[-1] == fr.function:
                    val, why = 0.9, f"stack frame function `{fr.function}`"
                else:
                    val, why = 0.15, f"same file as stack frame {fr.path}"
                val *= w
                if val > scores[cid]:
                    scores[cid] = val
                    _add(ev, cid, why)
    return dict(scores), ev


# ---------------------------------------------------------------------------------------------- tests
def resolve_failing_tests(snap: RepoSnapshot, ids: list[str]) -> list[tuple[int, float]]:
    """Map failing test identifiers ('path::name' or bare 'name') to test symbol ids."""
    out: dict[int, float] = {}
    for raw in ids:
        path, _, name = raw.partition("::")
        if not name:
            path, name = "", raw
        name = name.split("::")[-1].split("[")[0].strip()
        for sid in snap.symbols_by_name.get(name.lower(), []):
            sym = snap.symbols[sid]
            if sym.kind == "module" or not sym.is_test:
                continue
            if path and not (sym.path.endswith(path) or path.endswith(sym.path)):
                continue
            out[sid] = 1.0
    return list(out.items())


def test_signal(snap: RepoSnapshot, q: QueryObject, lexical_norm: Scores, cfg: RetrievalConfig) -> tuple[Scores, Evidence]:
    """Source chunks exercised by (a) the failing tests named in the report and (b) tests whose text matched the query."""
    scores: Scores = defaultdict(float)
    ev: Evidence = {}
    g = snap.graph
    if g is None:
        return {}, {}
    test_weights: dict[int, float] = dict(resolve_failing_tests(snap, q.failing_tests))
    failing_files = {snap.symbols[sid].file_id for sid in test_weights}
    for cid, val in lexical_norm.items():  # tests that look relevant to the report get partial weight
        m = snap.chunks[cid]
        if m.is_test and m.symbol_id and val >= 0.3:
            test_weights[m.symbol_id] = max(test_weights.get(m.symbol_id, 0.0), 0.6 * val)
    for sid, w in test_weights.items():
        node = f"s{sid}"
        if node not in g.info:
            continue
        for _, dst, d in g.g.out_edges(node, data=True):
            if d["type"] != "TESTS" or d["confidence"] < cfg.min_edge_confidence:
                continue
            info = g.info.get(dst, {})
            if "symbol_id" not in info:
                continue
            for cid in snap.chunks_by_symbol.get(info["symbol_id"], []):
                val = w * d["confidence"]
                if val > scores[cid]:
                    scores[cid] = val
                    _add(ev, cid, f"exercised by test `{snap.symbols[sid].qname}` (edge confidence {d['confidence']:.2f})")
    # file-level: modules imported by a failing test file receive a weak, flat boost
    for fid in failing_files:
        node = f"f{fid}"
        for _, dst, d in g.g.out_edges(node, data=True):
            if d["type"] == "TESTS" and d["confidence"] >= 0.5 and dst.startswith("f"):
                tf = int(dst[1:])
                for cid in snap.chunks_by_file.get(tf, []):
                    val = 0.3
                    if val > scores[cid]:
                        scores[cid] = val
                        _add(ev, cid, f"module covered by failing test file {snap.file_paths[fid]}")
    return dict(scores), ev


# ---------------------------------------------------------------------------------------------- graph
def graph_signal(snap: RepoSnapshot, seeds: dict[int, float], cfg: RetrievalConfig) -> tuple[Scores, Evidence, dict]:
    """Bounded best-first propagation from seed symbols through typed, confidence-weighted edges.

    A node never receives credit from itself, so the signal is independent evidence ("related to something relevant").
    Hub nodes are damped to avoid flooding the context with ubiquitous utilities.
    """
    g = snap.graph
    if g is None or not seeds:
        return {}, {}, {"expanded": 0}
    best: dict[str, tuple[float, str]] = {}
    for sid, s0 in seeds.items():
        start = f"s{sid}"
        if start not in g.info:
            continue
        frontier = [(start, s0, "")]
        visited_best = {start: s0}
        for hop in range(1, cfg.graph_depth + 1):
            nxt = []
            for node, val, why in frontier:
                for direction in ("out", "in"):
                    edges = g.g.out_edges(node, data=True) if direction == "out" else g.g.in_edges(node, data=True)
                    for u, v, d in edges:
                        other = v if direction == "out" else u
                        etype = d["type"]
                        if etype not in EDGE_WEIGHTS or d["confidence"] < cfg.min_edge_confidence:
                            continue
                        if etype == "DEFINES" and (not node.startswith("s") or not other.startswith("s")):
                            continue
                        w = EDGE_WEIGHTS[etype][0 if direction == "out" else 1]
                        deg = g.g.degree(other)
                        hub = 1.0 / (1.0 + HUB_PENALTY * max(0, deg - HUB_FREE_DEGREE))
                        nv = val * cfg.graph_decay * w * d["confidence"] * hub
                        if nv <= 0.01 or other == start:
                            continue
                        label = f"{etype.lower()} {'->' if direction == 'out' else '<-'} {g.info[node].get('label', node)} (hop {hop}, conf {d['confidence']:.2f})"
                        if nv > visited_best.get(other, 0.0):
                            visited_best[other] = nv
                            nxt.append((other, nv, label))
                            cur = best.get(other)
                            if cur is None or nv > cur[0]:
                                best[other] = (nv, f"{label} from seed `{g.info[start].get('label')}`")
            frontier = nxt
    scores: Scores = {}
    ev: Evidence = {}
    for node, (val, why) in best.items():
        info = g.info.get(node, {})
        if node.startswith("s"):
            for cid in snap.chunks_by_symbol.get(info.get("symbol_id", -1), []):
                scores[cid] = max(scores.get(cid, 0.0), val)
                _add(ev, cid, why)
        elif node.startswith("f"):
            # file-level (import) relations only credit module-level chunks, keeping the signal sparse
            for cid in snap.chunks_by_file.get(info.get("file_id", -1), []):
                if snap.chunks[cid].symbol_id is None:
                    scores[cid] = max(scores.get(cid, 0.0), 0.5 * val)
                    _add(ev, cid, why)
    return scores, ev, {"expanded": len(best), "seeds": len(seeds)}
