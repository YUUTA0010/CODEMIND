"""Benchmark metrics against hand-computed values; statistics determinism and honest interpretation."""
import math

import pytest

from app.experiments import metrics as M
from app.experiments import stats
from app.experiments.runner import expand_methods, split_tasks
from app.experiments.tasks import BenchmarkTaskRecord

RANKED = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]


def test_rank_based_metrics_hand_computed():
    rel = {"c.py"}
    assert M.first_relevant_rank(RANKED, rel) == 3
    assert M.top_k_hit(RANKED, rel, 1) == 0.0 and M.top_k_hit(RANKED, rel, 3) == 1.0
    assert M.reciprocal_rank(RANKED, rel) == pytest.approx(1 / 3)
    assert M.reciprocal_rank(RANKED, {"zzz"}) == 0.0
    assert M.reciprocal_rank(RANKED, {"a.py", "c.py"}) == 1.0  # best relevant item counts


def test_recall_and_precision_with_multiple_relevant():
    rel = {"a.py", "c.py", "z.py"}
    assert M.recall_at_k(RANKED, rel, 1) == pytest.approx(1 / 3)
    assert M.recall_at_k(RANKED, rel, 3) == pytest.approx(2 / 3)
    assert M.recall_at_k(RANKED, rel, 10) == pytest.approx(2 / 3)  # z.py never retrieved
    assert M.recall_at_k(RANKED, set(), 3) == 0.0
    assert M.precision_at_k(RANKED, rel, 3) == pytest.approx(2 / 3)
    assert M.precision_at_k([], rel, 3) == 0.0


def test_localization_and_retrieval_bundle():
    m = M.localization_metrics(RANKED, {"b.py"})
    assert (m["top1"], m["top5"], m["mrr"], m["recall@1"], m["recall@3"]) == (0.0, 1.0, 0.5, 0.0, 1.0)
    r = M.retrieval_metrics(["a.py", "a.py", "b.py", "x.py"], {"b.py", "q.py"}, 1234, 5.5)
    assert r["context_recall"] == 0.5 and r["irrelevant_files"] == 2.0 and r["context_files"] == 3.0
    assert r["context_chars"] == 1234.0 and r["latency_ms"] == 5.5
    assert M.rate([True, False, True, True]) == 0.75 and M.rate([]) is None


def test_bootstrap_is_seeded_and_brackets_the_mean():
    v = [0.0, 1.0, 1.0, 0.5, 0.25, 1.0, 0.0, 0.75]
    a, b = stats.bootstrap_ci(v, seed=3), stats.bootstrap_ci(v, seed=3)
    assert a == b and a[1] <= a[0] <= a[2] and a[0] == pytest.approx(sum(v) / len(v))
    assert stats.bootstrap_ci([0.7]) == (0.7, 0.7, 0.7)
    assert math.isnan(stats.bootstrap_ci([])[0])
    assert stats.bootstrap_ci([1.0] * 10) == (1.0, 1.0, 1.0)


def test_paired_difference_exact_signflip():
    a = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    b = [0.0] * 6
    d = stats.paired_difference(a, b, seed=1)
    assert d["mean_diff"] == 1.0 and (d["wins"], d["losses"], d["ties"]) == (6, 0, 0)
    assert d["p_value"] == pytest.approx(2 / 64)  # exact two-sided sign-flip test: 2 of 2^6 assignments are as extreme
    same = stats.paired_difference([0.5, 0.2], [0.5, 0.2])
    assert same["p_value"] == 1.0 and same["ties"] == 2 and same["mean_diff"] == 0
    mixed = stats.paired_difference([1, 0, 1, 0], [0, 1, 1, 0])
    assert (mixed["wins"], mixed["losses"], mixed["ties"]) == (1, 1, 2) and mixed["p_value"] == 1.0
    with pytest.raises(ValueError):
        stats.paired_difference([1], [1, 2])
    assert stats.paired_difference([], [])["n"] == 0


def test_interpretation_refuses_to_overclaim_on_small_samples():
    small = stats.paired_difference([1.0] * 6, [0.0] * 6)
    assert small["sufficient_n"] is False and "inconclusive" in stats.interpret(small) and "improvement" not in stats.interpret(small)
    big = stats.paired_difference([1.0, 0.8, 0.9, 1.0] * 6, [0.2, 0.1, 0.3, 0.0] * 6, seed=2)
    assert big["sufficient_n"] and stats.interpret(big).startswith("improvement")
    worse = stats.paired_difference([0.0, 0.1] * 12, [0.9, 1.0] * 12, seed=2)
    assert stats.interpret(worse).startswith("regression")
    flat = stats.paired_difference([0.5, 0.6, 0.4, 0.5] * 6, [0.5, 0.4, 0.6, 0.5] * 6, seed=2)
    assert stats.interpret(flat).startswith("no detectable difference")


def test_large_sample_permutation_is_seeded():
    a = [(i % 7) / 7 for i in range(40)]
    b = [(i % 5) / 9 for i in range(40)]
    assert stats.paired_difference(a, b, seed=5) == stats.paired_difference(a, b, seed=5)


def test_method_expansion_and_ablation_rules():
    specs = expand_methods({"ablation_of": "dependency_aware_hybrid"})
    labels = [s["label"] for s in specs]
    assert "dependency_aware_hybrid-minus-graph" in labels and "dependency_aware_hybrid-minus-test" in labels
    assert "dependency_aware_hybrid-minus-history" not in labels, "zero-weight signals are not ablated"
    minus = next(s for s in specs if s["label"].endswith("minus-graph"))
    assert minus["disabled_signals"] == ["graph"]
    with pytest.raises(ValueError):
        expand_methods({"ablation_of": "nope"})
    with pytest.raises(ValueError):
        expand_methods({"methods": [{"method": "lexical"}, {"method": "lexical"}]})


def _tasks(n):
    return [BenchmarkTaskRecord(f"t{i:02d}", "/x", "r", "d", "", [], ["a.py"]) for i in range(n)]


def test_train_test_split_is_deterministic_disjoint_and_complete():
    t = _tasks(10)
    tr1, te1 = split_tasks(t, 7, 0.3)
    tr2, te2 = split_tasks(list(reversed(t)), 7, 0.3)
    assert {x.task_key for x in te1} == {x.task_key for x in te2} and len(te1) == 3
    assert not {x.task_key for x in tr1} & {x.task_key for x in te1} and len(tr1) + len(te1) == 10
