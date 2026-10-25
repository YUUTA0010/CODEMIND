"""Experiment runner.

Reproducibility: the full config (including seed, dataset revision info, method specs and weights) is stored on the run and
exported as JSON. Retrieval is deterministic (stable tie-breaking, seeded hashing embeddings), so repeated runs only differ
when a nondeterministic model is involved; `repeats` re-runs the repair stage for that purpose.

Fairness: every method receives the identical `SystemInput` and the identical index. Ground-truth labels are used only here,
after retrieval, to compute metrics.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.investigator import run_agent
from app.agents.providers import LLMProvider, NullProvider
from app.core.config import get_settings
from app.core.events import bus
from app.db.session import session_scope
from app.experiments import metrics as M
from app.experiments.tasks import BenchmarkTaskRecord, EvalLabels, SystemInput
from app.indexing.indexer import create_job, run_indexing_job
from app.localization.engine import context_for, localize
from app.localization.report import build_query
from app.models import (BenchmarkDataset, BenchmarkTask, BugReport, ExperimentRun, ExperimentTaskResult, Investigation, Repository)
from app.patches.service import create_patch, verify_patch
from app.retrieval.snapshot import get_snapshot, invalidate_snapshot
from app.retrieval.types import DEFAULT_WEIGHTS, METHOD_SIGNALS, RetrievalConfig

log = logging.getLogger("codemind.experiments")

DEFAULT_METHODS = [
    {"label": "lexical", "method": "lexical"},
    {"label": "semantic", "method": "semantic"},
    {"label": "hybrid_conventional", "method": "hybrid_conventional"},
    {"label": "dependency_aware_hybrid", "method": "hybrid_dependency"},
    {"label": "adaptive", "method": "adaptive"},
]


def expand_methods(cfg: dict) -> list[dict]:
    specs = [dict(m) for m in (cfg.get("methods") or DEFAULT_METHODS)]
    for m in specs:
        m.setdefault("label", m["method"])
        m.setdefault("disabled_signals", [])
        m.setdefault("weights", {})
    base_label = cfg.get("ablation_of")
    if base_label:
        base = next((m for m in specs if m["label"] == base_label), None)
        if base is None:
            raise ValueError(f"ablation_of '{base_label}' is not one of the configured methods")
        for sig in METHOD_SIGNALS.get(base["method"], []):
            if sig in base["disabled_signals"] or (RetrievalConfig.from_dict({"weights": base["weights"]}).weights.get(sig, 0.0) <= 0.0):
                continue  # ablating an already-disabled / zero-weight signal would just duplicate the base method
            specs.append({"label": f"{base_label}-minus-{sig}", "method": base["method"], "weights": base["weights"],
                          "disabled_signals": [*base["disabled_signals"], sig], "ablation": sig})
    labels = [m["label"] for m in specs]
    if len(set(labels)) != len(labels):
        raise ValueError("method labels must be unique")
    return specs


def retrieval_config_for(spec: dict, cfg: dict) -> RetrievalConfig:
    base = dict(cfg.get("retrieval") or {})
    base.update({"method": spec["method"], "disabled_signals": spec.get("disabled_signals", []), "seed": cfg.get("seed", 0)})
    rc = RetrievalConfig.from_dict(base)
    w = dict(rc.weights)
    w.update(spec.get("weights") or {})
    rc.weights = w
    return rc


def split_tasks(tasks: list[BenchmarkTaskRecord], seed: int, test_fraction: float) -> tuple[list[BenchmarkTaskRecord], list[BenchmarkTaskRecord]]:
    """Deterministic train/test split (seeded shuffle of sorted keys). Weight tuning may only look at `train`."""
    keys = sorted(t.task_key for t in tasks)
    rng = random.Random(seed)
    rng.shuffle(keys)
    n_test = max(1, int(round(len(keys) * test_fraction))) if len(keys) > 1 else len(keys)
    test_keys = set(keys[:n_test])
    return [t for t in tasks if t.task_key not in test_keys], [t for t in tasks if t.task_key in test_keys]


def ensure_benchmark_repo(task: BenchmarkTaskRecord) -> int:
    with session_scope() as s:
        repo = s.execute(select(Repository).where(Repository.root_path == task.repo_path)).scalar_one_or_none()
        if repo is None:
            repo = Repository(name=f"bench:{task.task_key}", root_path=task.repo_path, kind="benchmark")
            s.add(repo)
            s.flush()
        rid = repo.id
        needs_index = repo.index_version == 0
        if needs_index:
            job = create_job(s, repo, "full")
            jid = job.id
    if needs_index:
        res = run_indexing_job(jid)
        if res.get("status") != "COMPLETED":
            raise RuntimeError(f"indexing failed for {task.task_key}: {res}")
        invalidate_snapshot(rid)
    return rid


def _query_for(inp: SystemInput):
    return build_query(description=inp.description, stack_trace=inp.stack_trace, failing_tests=list(inp.failing_tests))


def evaluate_method(s: Session, snap, inp: SystemInput, labels: EvalLabels, rc: RetrievalConfig, query) -> tuple[dict, list[dict], object]:
    """Run one method on one task -> (metrics, ranked file list, localize artifacts). Labels are touched only after retrieval."""
    cands, result = localize(s, snap, query, rc, max_candidates=50)
    ctx = context_for(s, snap, result, rc)
    files_ranked = [p for p, _ in result.rank_files(include_tests=False)]
    relevant = set(labels.ground_truth_files)
    metrics = M.localization_metrics(files_ranked, relevant)
    metrics.update(M.retrieval_metrics([sn["path"] for sn in ctx["selected"]], relevant, ctx["used_chars"], result.runtime_ms))
    metrics["context_omitted_for_budget"] = float(ctx["omitted_due_to_budget"])
    ranked = [{"path": p, "score": round(sc, 6)} for p, sc in result.rank_files(include_tests=False)[:30]]
    return metrics, ranked, (cands, result, ctx)


def tune_weights(train: list[BenchmarkTaskRecord], base_spec: dict, cfg: dict, trials: int, seed: int) -> dict[str, float]:
    """Seeded random search maximising mean file-level MRR on the TRAIN split only. Returns tuned weights."""
    rng = random.Random(seed)
    prepared = []
    for t in train:
        rid = ensure_benchmark_repo(t)
        prepared.append((t, rid))
    signals = [sg for sg in METHOD_SIGNALS[base_spec["method"]] if sg != "history"]
    best_w, best_score = dict(DEFAULT_WEIGHTS), -1.0
    candidates = [dict(DEFAULT_WEIGHTS)] + [{sg: round(rng.uniform(0.0, 1.0), 3) for sg in signals} for _ in range(trials)]
    for w in candidates:
        spec = {**base_spec, "weights": w}
        rc = retrieval_config_for(spec, cfg)
        tot = 0.0
        with session_scope() as s:
            for t, rid in prepared:
                snap = get_snapshot(s, rid)
                inp = t.system_input(provide_failing_tests=cfg.get("provide_failing_tests", False))
                m, _, _ = evaluate_method(s, snap, inp, t.labels(), rc, _query_for(inp))
                tot += m["mrr"]
        score = tot / max(1, len(prepared))
        if score > best_score:
            best_score, best_w = score, {**DEFAULT_WEIGHTS, **w}
    return best_w


def run_experiment(experiment_id: int, tasks: list[BenchmarkTaskRecord], provider: LLMProvider | None = None,
                   progress: Callable[[int, int], None] | None = None) -> dict:
    with session_scope() as s:
        exp = s.get(ExperimentRun, experiment_id)
        cfg = dict(exp.config)
        exp.status = "RUNNING"
    try:
        specs = expand_methods(cfg)
        eval_tasks = tasks
        notes: list[str] = []
        split_cfg = cfg.get("split")
        tuned_label = None
        if split_cfg and split_cfg.get("test_fraction"):
            train, test = split_tasks(tasks, cfg.get("seed", 0), float(split_cfg["test_fraction"]))
            eval_tasks = test
            notes.append(f"train/test split: {len(train)} train / {len(test)} test tasks; metrics reported on the TEST split only")
            if split_cfg.get("tune_weights") and train:
                base = next(m for m in specs if m["label"] == split_cfg.get("tune_method", "dependency_aware_hybrid"))
                w = tune_weights(train, base, cfg, int(split_cfg.get("trials", 25)), cfg.get("seed", 0))
                tuned_label = f"{base['label']}-tuned"
                specs.append({**base, "label": tuned_label, "weights": w, "disabled_signals": base.get("disabled_signals", [])})
                notes.append(f"weights for '{tuned_label}' were tuned on the train split only: {w}")
        total = len(eval_tasks) * len(specs)
        with session_scope() as s:
            exp = s.get(ExperimentRun, experiment_id)
            exp.total, exp.progress = total, 0
            exp.config = {**cfg, "resolved_methods": specs, "evaluated_task_keys": sorted(t.task_key for t in eval_tasks), "notes": notes,
                          "retrieval_defaults": RetrievalConfig().to_dict()}
        done = 0
        repair_cfg = cfg.get("repair") or {}
        repair_enabled = bool(repair_cfg.get("enabled"))
        provider = provider or NullProvider()
        repair_skipped_reason = None
        if repair_enabled and isinstance(provider, NullProvider):
            repair_skipped_reason = "repair requested but no language-model provider is configured; repair metrics are NOT available"
            notes.append(repair_skipped_reason)
        for task in eval_tasks:
            rid = ensure_benchmark_repo(task)
            inp = task.system_input(provide_failing_tests=bool(cfg.get("provide_failing_tests", False)))
            labels = task.labels()
            query = _query_for(inp)
            for spec in specs:
                rc = retrieval_config_for(spec, cfg)
                for rep in range(max(1, int(cfg.get("repeats", 1)))):
                    t0 = time.perf_counter()
                    with session_scope() as s:
                        snap = get_snapshot(s, rid)
                        metrics, ranked, (cands, result, ctx) = evaluate_method(s, snap, inp, labels, rc, query)
                        repair: dict = {}
                        if repair_enabled and not isinstance(provider, NullProvider) and spec["label"] == repair_cfg.get("method_label", specs[-1]["label"]):
                            repair = _repair_stage(s, snap, task, inp, cands, ctx, rc, provider, repair_cfg, rid)
                        elif repair_enabled and repair_skipped_reason:
                            repair = {"skipped": repair_skipped_reason}
                        tid = _task_row_id(s, experiment_id, task)
                        s.add(ExperimentTaskResult(experiment_id=experiment_id, task_id=tid, method=spec["label"], run_index=rep, ranked_files=ranked,
                                                   metrics=metrics, runtime_ms=metrics["latency_ms"], context_chars=int(metrics["context_chars"]), repair=repair))
                    done += 1
                    with session_scope() as s:
                        s.get(ExperimentRun, experiment_id).progress = done
                    bus.publish("experiment.progress", {"experiment_id": experiment_id, "progress": done, "total": total, "task": task.task_key, "method": spec["label"]})
        from app.experiments.reporting import summarize_experiment, export_experiment

        summary = summarize_experiment(experiment_id)
        paths = export_experiment(experiment_id)
        summary["exports"] = paths
        summary["notes"] = notes
        with session_scope() as s:
            exp = s.get(ExperimentRun, experiment_id)
            exp.summary, exp.status, exp.finished_at = summary, "COMPLETED", datetime.now(timezone.utc)
        bus.publish("experiment.done", {"experiment_id": experiment_id, "status": "COMPLETED"})
        return summary
    except Exception as e:  # noqa: BLE001
        log.exception("experiment failed")
        with session_scope() as s:
            exp = s.get(ExperimentRun, experiment_id)
            exp.status, exp.error, exp.finished_at = "FAILED", f"{type(e).__name__}: {e}", datetime.now(timezone.utc)
        bus.publish("experiment.done", {"experiment_id": experiment_id, "status": "FAILED", "error": str(e)})
        raise


def _task_row_id(s: Session, experiment_id: int, task: BenchmarkTaskRecord) -> int:
    exp = s.get(ExperimentRun, experiment_id)
    ds_id = exp.dataset_id
    row = s.execute(select(BenchmarkTask).where(BenchmarkTask.dataset_id == ds_id, BenchmarkTask.task_key == task.task_key)).scalar_one_or_none()
    if row is None:
        row = BenchmarkTask(dataset_id=ds_id, task_key=task.task_key, repo_path=task.repo_path, repo_revision=task.repo_revision,
                            description=task.description, stack_trace=task.stack_trace, relevant_tests=task.relevant_tests,
                            ground_truth_files=task.ground_truth_files, ground_truth_symbols=task.ground_truth_symbols)
        s.add(row)
        s.flush()
    return row.id


def _repair_stage(s: Session, snap, task: BenchmarkTaskRecord, inp: SystemInput, cands, ctx, rc: RetrievalConfig, provider: LLMProvider,
                  repair_cfg: dict, repo_id: int) -> dict:
    """Model-assisted repair for one task. `resolved` = targeted tests pass after the patch AND the broad suite passes
    (SWE-bench-style FAIL_TO_PASS + PASS_TO_PASS approximation; this is NOT the official harness)."""
    t0 = time.perf_counter()
    inv = Investigation(repository_id=repo_id, title=f"[bench] {task.task_key}", created_by="benchmark")
    inv.bug_report = BugReport(description=inp.description, stack_trace=inp.stack_trace, failing_tests=list(task.relevant_tests))
    s.add(inv)
    s.flush()
    out = {"patch_generated": False, "patch_applied": False, "targeted_passed": False, "broad_passed": False, "resolved": False, "regression": False,
           "iterations": 0, "input_tokens": 0, "output_tokens": 0, "model": getattr(provider, "model", None), "status": None}
    bug_text = inp.description + (("\n\nStack trace:\n" + inp.stack_trace) if inp.stack_trace else "")
    res = run_agent(s, snap, provider, bug_text=bug_text, cands=cands, context=ctx, failing_tests=list(task.relevant_tests),
                    max_iterations=int(repair_cfg.get("max_iterations", 3)))
    out.update({"iterations": res.iterations, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "errors": res.errors})
    if res.patch:
        out["patch_generated"] = True
        try:
            p = create_patch(s, inv.id, res.patch["diff"], res.patch.get("rationale", ""), model_name=getattr(provider, "model", None),
                             generation_config={"max_iterations": repair_cfg.get("max_iterations", 3)}, actor="benchmark")
            out["patch_applied"] = p.status != "PATCH_REJECTED"
            if out["patch_applied"]:
                rep = verify_patch(s, p.id, targeted_tests=list(task.relevant_tests) or None, run_broad=True, actor="benchmark")
                out["status"] = rep["status"]
                phases = {e["phase"]: e["outcome"] for e in rep["executions"]}
                out["targeted_passed"] = phases.get("targeted") == "TESTS_PASSED"
                out["broad_passed"] = phases.get("broad") == "TESTS_PASSED"
                out["resolved"] = out["targeted_passed"] and out["broad_passed"] and phases.get("baseline") == "TESTS_FAILED"
                out["regression"] = bool(rep["possible_regression"])
                out["sandbox_isolation"] = rep["isolation"]
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {getattr(e, 'message', e)}"
    out["runtime_s"] = round(time.perf_counter() - t0, 3)
    return out


def create_experiment(name: str, cfg: dict, dataset_name: str, dataset_kind: str, tasks: list[BenchmarkTaskRecord], *, is_synthetic: bool,
                      source: str = "", revision: str = "") -> int:
    with session_scope() as s:
        ds = s.execute(select(BenchmarkDataset).where(BenchmarkDataset.name == dataset_name)).scalar_one_or_none()
        if ds is None:
            ds = BenchmarkDataset(name=dataset_name, kind=dataset_kind, source=source, revision=revision, is_synthetic=is_synthetic,
                                  description=("SYNTHETIC fixtures for pipeline testing only" if is_synthetic else ""))
            s.add(ds)
            s.flush()
        exp = ExperimentRun(name=name, status="QUEUED", config=cfg, seed=int(cfg.get("seed", 0)), dataset_id=ds.id, total=0)
        s.add(exp)
        s.flush()
        return exp.id
