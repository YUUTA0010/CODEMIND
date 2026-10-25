"""Lexical (FTS5 vs reference BM25), semantic fallback, hybrid ranking, ablation, context budget, localization."""
import numpy as np
import pytest

from app.db.session import session_scope
from app.localization.engine import context_for, localize
from app.localization.report import build_query, parse_trace
from app.retrieval.context import select_context
from app.retrieval.embeddings import HashingEmbedder
from app.retrieval.hybrid import retrieve
from app.retrieval.lexical import PyBM25, lexical_scores, query_terms
from app.retrieval.snapshot import get_snapshot
from app.retrieval.text import split_identifier, tokenize
from app.retrieval.types import METHOD_SIGNALS, Hit, QueryObject, RetrievalConfig
from app.retrieval.vector import BruteForceIndex


def run(rid, q, **cfg):
    with session_scope() as s:
        snap = get_snapshot(s, rid)
        return retrieve(s, snap, q, RetrievalConfig.from_dict(cfg)), snap  # from_dict merges partial weights over the defaults


def test_tokenization_splits_identifiers():
    assert split_identifier("parseHTTPResponse_v2") == ["parse", "http", "response", "v", "2"]
    toks = tokenize("applyDiscount")
    assert "applydiscount" in toks and "apply" in toks and "discount" in toks


def test_lexical_finds_symbol_and_is_source_grounded(indexed_task):
    rid, task = indexed_task
    q = QueryObject(text="apply_discount", identifiers=["apply_discount"], keywords=["discount"])
    res, snap = run(rid, q, method="lexical")
    top = res.hits[0]
    assert top.path == "inventory/pricing.py" and top.symbol_name == "apply_discount"
    assert 1 <= top.start_line <= top.end_line
    lines = (open(f"{task['repo_path']}/{top.path}").read().split("\n"))
    assert "def apply_discount" in "\n".join(lines[top.start_line - 1: top.end_line])  # exact line range


def test_fts5_agrees_with_reference_bm25(indexed_task):
    rid, _ = indexed_task
    q = QueryObject(text="shipping zone rate", keywords=["shipping", "zone", "rate"], identifiers=[])
    with session_scope() as s:
        snap = get_snapshot(s, rid)
        fts = lexical_scores(s, snap, q, 20)
        from app.retrieval.lexical import _fallback_docs

        ref = PyBM25(_fallback_docs(s, snap)).search(query_terms(q), 20)
    assert fts and ref
    assert list(fts)[0] == list(ref)[0], "both BM25 implementations should rank the same chunk first"
    assert len(set(list(fts)[:3]) & set(list(ref)[:3])) >= 2


def test_stacktrace_signal_pins_the_frame(indexed_task, suite):
    rid, _ = indexed_task
    shipping = next(t for t in suite if t["task_key"].endswith("shipping-keyerror"))
    trace = shipping["stack_trace"]
    frames = parse_trace(trace)
    assert frames[0].path == "inventory/shipping.py" and frames[0].function == "shipping_cost"  # innermost frame first
    q = build_query(description="crash", stack_trace=trace)
    assert any(t.endswith("test_eu_rate") for t in q.failing_tests) or q.frames
    assert "KeyError" in q.error_terms


def test_embedder_is_deterministic_and_similarity_is_meaningful():
    e = HashingEmbedder()
    a, b, c = e.embed(["def apply_discount(price, quantity): bulk discount", "bulk discount for quantity", "slugify text into hyphen separated"])
    assert np.allclose(e.embed(["bulk discount for quantity"])[0], b)
    assert float(a @ b) > float(a @ c)
    assert abs(np.linalg.norm(a) - 1) < 1e-5


def test_vector_index_ordering_and_ties():
    idx = BruteForceIndex()
    m = np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32)
    idx.build([10, 20, 30], m)
    assert [i for i, _ in idx.search(np.array([1, 0], dtype=np.float32), 3)][:2] == [10, 30]  # tie broken by id, deterministically
    assert idx.search(np.array([0, 0], dtype=np.float32), 3) == []


def test_semantic_retrieval_works_with_fallback_and_warns(indexed_task):
    rid, _ = indexed_task
    q = QueryObject(text="bulk discount applied when quantity reaches threshold")
    res, _ = run(rid, q, method="semantic")
    assert res.hits and res.hits[0].path == "inventory/pricing.py"
    assert any("hashing embedder" in w for w in res.warnings)
    assert res.signals_used == ["semantic"]


def test_each_method_only_uses_its_allowed_signals(indexed_task):
    rid, task = indexed_task
    q = build_query(description=task["description"], failing_tests=task["relevant_tests"])
    for method, allowed in METHOD_SIGNALS.items():
        if method == "adaptive":
            continue
        res, _ = run(rid, q, method=method)
        used = {k for h in res.hits for k in h.signals}
        assert used <= set(allowed), (method, used)
    assert "graph" not in METHOD_SIGNALS["hybrid_conventional"] and "graph" in METHOD_SIGNALS["hybrid_dependency"]


def test_ablation_disables_a_signal_and_weights_are_configurable(indexed_task):
    rid, task = indexed_task
    q = build_query(description=task["description"], failing_tests=task["relevant_tests"])
    full, _ = run(rid, q, method="hybrid_dependency")
    assert "graph" in full.signals_used and any("graph" in h.signals for h in full.hits)
    ablated, _ = run(rid, q, method="hybrid_dependency", disabled_signals=["graph"])
    assert "graph" not in ablated.signals_used and not any("graph" in h.signals for h in ablated.hits)
    only_lex, _ = run(rid, q, method="hybrid_dependency", weights={k: 0.0 for k in ("symbol_exact", "semantic", "graph", "test", "trace", "history")})
    assert only_lex.signals_used == ["lexical"]
    # changing a weight changes scores (it is not hard-coded)
    a, _ = run(rid, q, method="hybrid_dependency", weights={"graph": 0.9})
    b, _ = run(rid, q, method="hybrid_dependency", weights={"graph": 0.01})
    assert [round(h.score, 6) for h in a.hits[:5]] != [round(h.score, 6) for h in b.hits[:5]]


def test_graph_signal_is_independent_evidence_with_provenance(indexed_task):
    rid, task = indexed_task
    q = build_query(description=task["description"], failing_tests=task["relevant_tests"])
    res, _ = run(rid, q, method="dependency_aware")
    graph_hits = [h for h in res.hits if "graph" in h.signals]
    assert graph_hits and all(any(e.startswith("[graph]") for e in h.evidence) for h in graph_hits)
    assert res.diagnostics["graph"]["seeds"] >= 1 and res.diagnostics["graph"]["expanded"] >= 1


def test_failing_test_signal_credits_tested_code(indexed_task):
    rid, task = indexed_task
    q = build_query(description="something unrelated zzz", failing_tests=task["relevant_tests"])
    res, _ = run(rid, q, method="dependency_aware")
    hit = next(h for h in res.hits if h.symbol_name == "apply_discount")
    assert "test" in hit.signals and any("exercised by test" in e for e in hit.evidence)


def test_adaptive_records_decision_and_is_deterministic(indexed_task):
    rid, task = indexed_task
    q = build_query(description="price total wrong")
    r1, _ = run(rid, q, method="adaptive", adaptive_margin_threshold=1.01)  # force expansion
    r2, _ = run(rid, q, method="adaptive", adaptive_margin_threshold=1.01)
    assert r1.diagnostics["adaptive"]["expanded"] is True
    assert [(h.chunk_id, round(h.score, 8)) for h in r1.hits] == [(h.chunk_id, round(h.score, 8)) for h in r2.hits]
    r3, _ = run(rid, q, method="adaptive", adaptive_margin_threshold=0.0)
    assert r3.diagnostics["adaptive"]["expanded"] is False


def mk(cid, path, a, b, score, test=False):
    return Hit(cid, 1, path, None, None, a, b, test, score)


def test_context_budget_dedup_and_no_silent_loss():
    hits = [mk(1, "a.py", 1, 10, 0.9), mk(2, "a.py", 3, 9, 0.8), mk(3, "b.py", 1, 5, 0.7), mk(4, "c.py", 1, 5, 0.6, test=True), mk(5, "d.py", 1, 5, 0.5)]
    contents = {i: "x" * 300 for i in range(1, 6)}
    out = select_context(hits, contents, budget_chars=3 * (300 + 96), include_tests=False)
    sel = [s["chunk_id"] for s in out["selected"]]
    assert sel == [1, 3, 5] and out["used_chars"] <= out["budget_chars"]
    reasons = {o["chunk_id"]: o["reason"] for o in out["omitted"]}
    assert "overlaps" in reasons[2] and "tests excluded" in reasons[4]
    tight = select_context(hits, contents, budget_chars=2 * (300 + 96))
    assert tight["used_chars"] <= tight["budget_chars"]
    assert {o["chunk_id"]: o["reason"] for o in tight["omitted"]}[5] == "context budget exhausted" and tight["omitted_due_to_budget"] >= 1
    assert len(tight["selected"]) + len(tight["omitted"]) == len(hits)  # every candidate is accounted for


def test_oversized_single_snippet_is_truncated_explicitly():
    out = select_context([mk(1, "a.py", 1, 500, 1.0)], {1: "y" * 5000}, budget_chars=1000)
    s = out["selected"][0]
    assert s["truncated"] is True and s["original_range"] == [1, 500] and len(s["content"]) <= 1000 and out["used_chars"] <= 1000


def test_retrieval_context_respects_configured_budget(indexed_task):
    rid, task = indexed_task
    q = build_query(description=task["description"])
    with session_scope() as s:
        snap = get_snapshot(s, rid)
        cfg = RetrievalConfig(method="hybrid_dependency", budget_chars=900)
        res = retrieve(s, snap, q, cfg)
        ctx = context_for(s, snap, res, cfg)
    assert ctx["used_chars"] <= 900 and ctx["omitted"], "small budgets must omit (and report) candidates"


def test_localization_ranks_bug_with_evidence_and_honest_confidence(indexed_task):
    rid, task = indexed_task
    q = build_query(description=task["description"], failing_tests=task["relevant_tests"])
    with session_scope() as s:
        snap = get_snapshot(s, rid)
        cands, res = localize(s, snap, q, RetrievalConfig(method="hybrid_dependency"))
    top = cands[0]
    assert top.file_path == "inventory/pricing.py" and top.symbol_name == "apply_discount" and top.rank == 1
    assert top.evidence and top.related_tests and top.uncertainty
    assert any("not a proven root cause" in u for u in top.uncertainty)
    assert top.confidence_label in ("low", "medium", "high")
    assert [c.score for c in cands] == sorted((c.score for c in cands), reverse=True)
    d = top.to_dict()
    assert {"file_path", "symbol_name", "start_line", "end_line", "score", "evidence", "uncertainty", "confidence_label"} <= set(d)


def test_localization_on_gibberish_yields_low_confidence_or_nothing(indexed_task):
    rid, _ = indexed_task
    with session_scope() as s:
        snap = get_snapshot(s, rid)
        cands, _ = localize(s, snap, build_query(description="qwertyuiop zxcvbnm"), RetrievalConfig(method="lexical"))
    assert cands == [] or all(c.confidence_label == "low" for c in cands)
