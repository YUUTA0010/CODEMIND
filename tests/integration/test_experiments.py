"""Experiment runner: leakage prevention, exports, ablations, split/tuning, repair metrics, adapters, API.

SYNTHETIC FIXTURES ONLY. `OracleProvider` is a TEST DOUBLE that returns each task's known fix; it demonstrates the pipeline
and metrics plumbing, it says nothing about the performance of any real model.
"""
import csv
import json
import subprocess
from dataclasses import fields
from pathlib import Path

import pytest

from app.agents.providers import LLMResponse
from app.db.session import session_scope
from app.experiments import runner as R
from app.experiments.adapters.directory import load_directory_tasks
from app.experiments.adapters.swebench import gold_patch_files, load_swebench_jsonl
from app.experiments.runner import create_experiment, run_experiment
from app.experiments.tasks import LABEL_FIELD_NAMES, SystemInput, assert_input_has_no_label_fields
from app.models import ExperimentRun, ExperimentTaskResult
from tests.conftest import index_repo


class OracleProvider:
    """TEST DOUBLE ONLY. Looks up the oracle diff by the task description found in the prompt."""

    name, model = "oracle-test-double", "oracle"

    def __init__(self, records):
        self.records, self.prompts = records, []

    def complete(self, system, user, *, max_tokens=2000, temperature=0.0):
        self.prompts.append(user)
        rec = next(r for r in self.records if r["description"][:60] in user)
        gt = rec["ground_truth_files"][0]
        return LLMResponse(json.dumps({"action": "final", "summary": "oracle", "hypotheses": [{"statement": "oracle hypothesis", "citations": [{"path": gt, "start_line": 1, "end_line": 2}]}],
                                       "patch": {"diff": rec["oracle_fix_diff"], "rationale": "test double"}}), 10, 5, "oracle")


@pytest.fixture()
def tasks(db, suite):
    return load_directory_tasks(Path(suite[0]["repo_path"]).parents[1])


def make(tasks, **cfg):
    cfg = {"seed": 3, **cfg}
    return create_experiment("t", cfg, "synthetic-inventory-v1", "synthetic", tasks, is_synthetic=True)


def test_system_input_structurally_cannot_carry_labels():
    assert_input_has_no_label_fields()
    names = {f.name for f in fields(SystemInput)}
    assert not (names & (LABEL_FIELD_NAMES - {"failing_tests"}))
    with pytest.raises(Exception):
        SystemInput("k", "/p", "d").ground_truth_files = ["x"]  # frozen + no such field
    with pytest.raises(TypeError):
        SystemInput("k", "/p", "d", ground_truth_files=["x"])  # type: ignore[call-arg]


def test_failing_tests_only_reach_the_system_when_explicitly_enabled(tasks):
    t = tasks[0]
    assert t.system_input(provide_failing_tests=False).failing_tests == ()
    assert t.system_input(provide_failing_tests=True).failing_tests == tuple(t.relevant_tests)


def test_retrieval_output_is_independent_of_ground_truth_labels(db, tasks):
    t = tasks[0]
    rid = index_repo(t.repo_path, "x", kind="benchmark")
    inp = t.system_input(provide_failing_tests=False)
    from app.experiments.tasks import EvalLabels
    from app.retrieval.snapshot import get_snapshot
    from app.retrieval.types import RetrievalConfig

    q = R._query_for(inp)
    outs = []
    for labels in (t.labels(), EvalLabels(("inventory/shipping.py",)), EvalLabels(("nonexistent.py",), ("zzz",), ("t",))):
        with session_scope() as s:
            snap = get_snapshot(s, rid)
            m, ranked, _ = R.evaluate_method(s, snap, inp, labels, RetrievalConfig(method="hybrid_dependency"), q)
        outs.append(ranked)
    assert outs[0] == outs[1] == outs[2], "rankings must not change with the labels"


def test_runner_never_passes_labels_or_oracle_to_retrieval_or_model(db, tasks, suite, monkeypatch):
    seen = []
    orig = R.localize

    def spy(s, snap, q, cfg, **kw):
        seen.append(json.dumps(q.to_dict()))
        return orig(s, snap, q, cfg, **kw)

    monkeypatch.setattr(R, "localize", spy)
    prov = OracleProvider(suite)
    eid = make(tasks[:2], methods=[{"method": "hybrid_dependency", "label": "dep"}], repair={"enabled": True})
    run_experiment(eid, tasks[:2], prov)
    assert seen
    for t, s in zip(tasks[:2], seen):
        assert t.ground_truth_files[0] not in s and "oracle" not in s and "ground_truth" not in s
        assert not any(tid in s for tid in t.relevant_tests), "failing tests withheld by default"
    by_key = {r["task_key"]: r for r in suite}
    for task_, prompt in zip(tasks[:2], prov.prompts):  # prompts are produced in task order
        rec = by_key[task_.task_key]
        fixed_line = [l for l in rec["oracle_fix_diff"].splitlines() if l.startswith("+") and not l.startswith("+++")][0][1:]
        assert fixed_line.strip() not in prompt, "the oracle fix must never be visible to the model"
        assert "ground_truth" not in prompt and "oracle_fix_diff" not in prompt and "expected_failing" not in prompt


def test_full_experiment_exports_and_honest_summary(db, tasks):
    eid = make(tasks, ablation_of="dependency_aware_hybrid")
    summ = run_experiment(eid, tasks)
    assert summ["n_tasks"] == 6 and "below 20" in summ["sample_size_warning"]
    out = Path(summ["exports"]["results_csv"]).parent
    assert {"results.csv", "summary.csv", "paired_comparisons.csv", "summary.json", "config.json", "results.json", "localization.png", "comparison.md"} <= {p.name for p in out.iterdir()}
    rows = list(csv.DictReader(open(out / "results.csv")))
    methods = {r["method"] for r in rows}
    assert {"lexical", "semantic", "hybrid_conventional", "dependency_aware_hybrid", "adaptive", "dependency_aware_hybrid-minus-graph"} <= methods
    assert len(rows) == 6 * len(methods) and {"top1", "mrr", "recall@5", "context_chars", "latency_ms", "irrelevant_files"} <= set(rows[0])
    md = (out / "comparison.md").read_text()
    assert "Warning" in md and "inconclusive" in md and "improvement" not in md.split("Paired comparison")[1].lower().replace("no improvement", "")
    assert (out / "localization.png").stat().st_size > 5000
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["seed"] == 3 and cfg["config"]["resolved_methods"] and len(cfg["config"]["evaluated_task_keys"]) == 6
    for m, per in summ["paired_vs_reference"].items():
        for metric, d in per.items():
            assert d["sufficient_n"] is False and d["interpretation"].startswith("inconclusive")
    with session_scope() as s:
        e = s.get(ExperimentRun, eid)
        assert e.status == "COMPLETED" and e.progress == e.total > 0


def test_experiment_is_reproducible_across_runs(db, tasks):
    e1 = make(tasks[:4], methods=[{"method": "hybrid_dependency", "label": "dep"}, {"method": "lexical"}])
    e2 = make(tasks[:4], methods=[{"method": "hybrid_dependency", "label": "dep"}, {"method": "lexical"}])
    s1, s2 = run_experiment(e1, tasks[:4]), run_experiment(e2, tasks[:4])
    for m in s1["methods"]:
        for k in ("top1", "top5", "mrr", "recall@5", "context_chars", "irrelevant_files"):
            assert s1["methods"][m][k] == s2["methods"][m][k], (m, k)
    assert s1["paired_vs_reference"] == s2["paired_vs_reference"]


def test_ablation_changes_results_and_is_labelled(db, tasks):
    eid = make(tasks, methods=[{"method": "hybrid_dependency", "label": "full"}], ablation_of="full", reference_method="full")
    summ = run_experiment(eid, tasks)
    assert "full-minus-graph" in summ["methods"] and "full-minus-lexical" in summ["methods"] and summ["reference_method"] == "full"
    with session_scope() as s:
        rows = s.query(ExperimentTaskResult).filter(ExperimentTaskResult.method.in_(["full", "full-minus-lexical"])).all()
    sig = {r.method: [x["path"] for x in r.ranked_files[:3]] for r in rows}
    assert any(r.ranked_files for r in rows) and len(sig) == 2


def test_split_is_test_only_and_tuning_uses_train_only(db, tasks):
    eid = make(tasks, methods=[{"method": "hybrid_dependency", "label": "dependency_aware_hybrid"}],
               split={"test_fraction": 0.5, "tune_weights": True, "tune_method": "dependency_aware_hybrid", "trials": 4})
    summ = run_experiment(eid, tasks)
    train, test = R.split_tasks(tasks, 3, 0.5)
    with session_scope() as s:
        cfg = s.get(ExperimentRun, eid).config
        evaluated = set(cfg["evaluated_task_keys"])
    assert evaluated == {t.task_key for t in test} and not evaluated & {t.task_key for t in train}
    assert "dependency_aware_hybrid-tuned" in summ["methods"] and summ["n_tasks"] == len(test)
    assert any("tuned on the train split only" in n for n in summ["notes"]) and any("TEST split only" in n for n in summ["notes"])


def test_repair_metrics_with_labelled_test_double(db, tasks, suite):
    eid = make(tasks, methods=[{"method": "hybrid_dependency", "label": "dep"}], repair={"enabled": True, "method_label": "dep"})
    summ = run_experiment(eid, tasks, OracleProvider(suite))
    r = summ["methods"]["dep"]["repair"]
    assert r["n"] == 6 and r["patch_generated"] == 1.0 and r["patch_applied"] == 1.0
    assert r["targeted_passed"] == 1.0 and r["broad_passed"] == 1.0 and r["resolved"] == 1.0 and r["regression"] == 0.0
    assert r["input_tokens_mean"] == 10
    assert "resolved*" in (Path(summ["exports"]["table_md"]).read_text()) and "not the official harness" in Path(summ["exports"]["table_md"]).read_text()


def test_wrong_model_patches_are_not_counted_as_resolved(db, tasks, suite):
    class Wrong(OracleProvider):
        def complete(self, system, user, **kw):
            r = super().complete(system, user, **kw)
            d = json.loads(r.text)
            d["patch"]["diff"] = "--- a/inventory/models.py\n+++ b/inventory/models.py\n@@ -1 +1 @@\n-\"\"\"Domain objects.\"\"\"\n+\"\"\"Domain objects (edited).\"\"\"\n"
            r.text = json.dumps(d)
            return r

    eid = make(tasks[:3], methods=[{"method": "hybrid_dependency", "label": "dep"}], repair={"enabled": True, "method_label": "dep"})
    summ = run_experiment(eid, tasks[:3], Wrong(suite))
    r = summ["methods"]["dep"]["repair"]
    assert r["patch_applied"] == 1.0 and r["targeted_passed"] == 0.0 and r["resolved"] == 0.0


def test_repair_without_a_model_is_reported_as_skipped_not_zero(db, tasks):
    eid = make(tasks[:2], methods=[{"method": "lexical"}], repair={"enabled": True})
    summ = run_experiment(eid, tasks[:2])
    assert "repair" not in summ["methods"]["lexical"] and "NOT available" in summ["methods"]["lexical"]["repair_skipped"]
    assert any("no language-model provider" in n for n in summ["notes"])


def test_directory_adapter_requires_ground_truth(db, tmp_path):
    d = tmp_path / "ds" / "t1"
    (d / "repo").mkdir(parents=True)
    (d / "task.json").write_text(json.dumps({"description": "x", "relevant_tests": []}))
    with pytest.raises(ValueError, match="ground_truth_files"):
        load_directory_tasks(tmp_path / "ds")


def test_swebench_adapter_on_synthetic_jsonl(db, tmp_path):
    """SYNTHETIC jsonl following SWE-bench field names; NOT real SWE-bench data."""
    clone = tmp_path / "repos" / "acme__widgets"
    clone.mkdir(parents=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    (clone / "w.py").write_text("def f():\n    return 1\n")
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(clone), *cmd], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, env=env).stdout.strip()
    patch = "diff --git a/w.py b/w.py\n--- a/w.py\n+++ b/w.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    rec = {"instance_id": "acme__widgets-1", "repo": "acme/widgets", "base_commit": sha, "problem_statement": "f returns the wrong number", "patch": patch,
           "FAIL_TO_PASS": json.dumps(["tests/test_w.py::test_f"]), "PASS_TO_PASS": "[]"}
    jl = tmp_path / "data.jsonl"
    jl.write_text(json.dumps(rec) + "\n")
    out = load_swebench_jsonl(jl, tmp_path / "repos", tmp_path / "work")
    assert len(out) == 1 and out[0].ground_truth_files == ["w.py"] and out[0].repo_revision == sha and out[0].relevant_tests == ["tests/test_w.py::test_f"]
    assert (Path(out[0].repo_path) / "w.py").read_text().endswith("return 1\n") and not (Path(out[0].repo_path) / ".git").exists()
    assert gold_patch_files(patch) == ["w.py"]
    with pytest.raises(FileNotFoundError):
        load_swebench_jsonl(jl, tmp_path / "norepos", tmp_path / "work2")
    from app.experiments.adapters.swebench import export_commit

    with pytest.raises(ValueError):
        export_commit(clone, "--upload-pack=evil", tmp_path / "x")


def test_experiment_api_end_to_end(client):
    bad = client.post("/api/v1/experiments", json={"name": "x", "ablation_of": "nope"})
    assert bad.status_code == 422
    assert client.post("/api/v1/experiments", json={"name": "x", "dataset": {"kind": "directory", "path": "/nonexistent"}}).status_code == 422
    assert client.post("/api/v1/experiments", json={"name": "x", "dataset": {"kind": "swebench"}}).status_code == 422
    r = client.post("/api/v1/experiments", json={"name": "api-run", "seed": 5, "dataset": {"kind": "synthetic", "limit": 3},
                                                 "methods": [{"method": "lexical"}, {"method": "hybrid_dependency", "label": "dep"}], "reference_method": "lexical"})
    assert r.status_code == 202
    eid = r.json()["id"]
    assert r.json()["synthetic_notice"] and r.json()["dataset"]["is_synthetic"] is True
    res = client.get(f"/api/v1/experiments/{eid}/results", params={"include_rows": True}).json()
    assert res["status"] == "COMPLETED" and res["summary"]["n_tasks"] == 3 and len(res["rows"]) == 6 and res["downloads"]
    csv_resp = client.get(f"/api/v1/experiments/{eid}/files/results.csv")
    assert csv_resp.status_code == 200 and csv_resp.text.startswith("task,method,run")
    assert client.get(f"/api/v1/experiments/{eid}/files/../../etc/passwd").status_code in (404, 422)
    assert client.get(f"/api/v1/experiments/{eid}/files/evil.sh").status_code == 404
    assert client.get(f"/api/v1/experiments/{eid}").json()["config"]["seed"] == 5
    assert client.get("/api/v1/experiments").json()["total"] == 1 and client.get("/api/v1/experiments/999").status_code == 404
    assert client.get("/api/v1/repositories").json()["total"] == 0, "benchmark repositories must not appear among the user's repositories"
    assert client.get("/api/v1/dashboard").json()["experiments"][0]["status"] == "COMPLETED"
