"""Aggregation, statistics, CSV/JSON export and plots for an experiment run."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from statistics import mean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.experiments import stats  # noqa: E402
from app.models import BenchmarkTask, ExperimentRun, ExperimentTaskResult  # noqa: E402

LOC_METRICS = ["top1", "top5", "mrr", "recall@1", "recall@3", "recall@5", "recall@10"]
RET_METRICS = ["context_recall", "context_chars", "context_files", "irrelevant_files", "latency_ms"]
HEADLINE = ["top1", "top5", "mrr"]


def _load(experiment_id: int):
    with session_scope() as s:
        exp = s.get(ExperimentRun, experiment_id)
        cfg = dict(exp.config)
        name, status, seed = exp.name, exp.status, exp.seed
        rows = s.execute(select(ExperimentTaskResult, BenchmarkTask.task_key).join(BenchmarkTask, BenchmarkTask.id == ExperimentTaskResult.task_id)
                         .where(ExperimentTaskResult.experiment_id == experiment_id).order_by(ExperimentTaskResult.id)).all()
        data = [{"task": tk, "method": r.method, "run": r.run_index, "metrics": dict(r.metrics), "ranked_files": list(r.ranked_files), "repair": dict(r.repair or {}),
                 "runtime_ms": r.runtime_ms, "context_chars": r.context_chars} for r, tk in rows]
    return name, status, seed, cfg, data


def _per_task(data: list[dict], method: str, metric: str) -> dict[str, float]:
    """Average over repeats per task so each task contributes exactly one value (the unit of analysis)."""
    acc: dict[str, list[float]] = defaultdict(list)
    for d in data:
        if d["method"] == method and metric in d["metrics"]:
            acc[d["task"]].append(d["metrics"][metric])
    return {t: mean(v) for t, v in acc.items()}


def summarize_experiment(experiment_id: int) -> dict:
    name, status, seed, cfg, data = _load(experiment_id)
    methods = [m["label"] for m in cfg.get("resolved_methods", [])] or sorted({d["method"] for d in data})
    tasks = sorted({d["task"] for d in data})
    ref = cfg.get("reference_method") or ("hybrid_conventional" if "hybrid_conventional" in methods else methods[0])
    summary: dict = {"experiment": name, "n_tasks": len(tasks), "seed": seed, "reference_method": ref, "methods": {}, "paired_vs_reference": {},
                     "sample_size_warning": (f"n={len(tasks)} tasks is below {stats.MIN_N_FOR_INFERENCE}; all comparisons are descriptive, not evidence of improvement."
                                             if len(tasks) < stats.MIN_N_FOR_INFERENCE else None)}
    for m in methods:
        entry: dict = {"n_tasks": 0}
        for metric in LOC_METRICS + RET_METRICS:
            per = _per_task(data, m, metric)
            if per:
                vals = [per[t] for t in tasks if t in per]
                mu, lo, hi = stats.bootstrap_ci(vals, seed=seed)
                entry[metric] = {"mean": mu, "ci_low": lo, "ci_high": hi}
                entry["n_tasks"] = len(vals)
        lat = [d["metrics"]["latency_ms"] for d in data if d["method"] == m and "latency_ms" in d["metrics"]]
        if lat:
            entry["latency_ms_median"] = median(lat)
        repairs = [d["repair"] for d in data if d["method"] == m and d["repair"] and "skipped" not in d["repair"]]
        if repairs:
            n = len(repairs)
            entry["repair"] = {"n": n, **{k: sum(1 for r in repairs if r.get(k)) / n for k in ("patch_generated", "patch_applied", "targeted_passed", "broad_passed", "resolved", "regression")},
                               "input_tokens_mean": mean(r.get("input_tokens", 0) for r in repairs), "output_tokens_mean": mean(r.get("output_tokens", 0) for r in repairs),
                               "runtime_s_mean": mean(r.get("runtime_s", 0) for r in repairs)}
        skipped = {d["repair"]["skipped"] for d in data if d["method"] == m and d["repair"] and "skipped" in d["repair"]}
        if skipped:
            entry["repair_skipped"] = sorted(skipped)[0]
        summary["methods"][m] = entry
    for m in methods:
        if m == ref:
            continue
        summary["paired_vs_reference"][m] = {}
        for metric in HEADLINE + ["recall@5", "context_recall"]:
            a, b = _per_task(data, m, metric), _per_task(data, ref, metric)
            common = [t for t in tasks if t in a and t in b]
            if not common:
                continue
            d = stats.paired_difference([a[t] for t in common], [b[t] for t in common], seed=seed)
            d["interpretation"] = stats.interpret(d)
            summary["paired_vs_reference"][m][metric] = d
    return summary


def export_experiment(experiment_id: int) -> dict:
    name, status, seed, cfg, data = _load(experiment_id)
    summary = summarize_experiment(experiment_id)
    out_dir = get_settings().exports_dir / f"experiment-{experiment_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # per-task rows
    metric_keys = LOC_METRICS + RET_METRICS
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["task", "method", "run", *metric_keys, "top_ranked_file"])
        for d in data:
            w.writerow([d["task"], d["method"], d["run"], *[round(d["metrics"].get(k, float("nan")), 6) for k in metric_keys],
                        d["ranked_files"][0]["path"] if d["ranked_files"] else ""])
    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "n_tasks", *[f"{k}_{s}" for k in metric_keys for s in ("mean", "ci_low", "ci_high")]])
        for m, e in summary["methods"].items():
            w.writerow([m, e.get("n_tasks", 0), *[round(e.get(k, {}).get(s, float("nan")), 6) for k in metric_keys for s in ("mean", "ci_low", "ci_high")]])
    with open(out_dir / "paired_comparisons.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "reference", "metric", "n", "mean_diff", "ci_low", "ci_high", "p_value", "wins", "losses", "ties", "interpretation"])
        for m, per in summary["paired_vs_reference"].items():
            for metric, d in per.items():
                w.writerow([m, summary["reference_method"], metric, d["n"], round(d["mean_diff"], 6), round(d["ci_low"], 6), round(d["ci_high"], 6),
                            round(d["p_value"], 6), d["wins"], d["losses"], d["ties"], d["interpretation"]])
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out_dir / "config.json").write_text(json.dumps({"experiment": name, "seed": seed, "config": cfg}, indent=2, default=str), encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    _plot(summary, out_dir / "localization.png")
    _markdown_table(summary, out_dir / "comparison.md")
    return {k: str(out_dir / v) for k, v in {"results_csv": "results.csv", "summary_csv": "summary.csv", "paired_csv": "paired_comparisons.csv",
                                             "summary_json": "summary.json", "config_json": "config.json", "results_json": "results.json",
                                             "plot": "localization.png", "table_md": "comparison.md"}.items()}


def _plot(summary: dict, path) -> None:
    methods = list(summary["methods"].keys())
    fig, ax = plt.subplots(figsize=(max(7, 1.5 * len(methods)), 4.2))
    width = 0.8 / len(HEADLINE)
    for i, metric in enumerate(HEADLINE):
        vals = [summary["methods"][m].get(metric, {}).get("mean", 0) for m in methods]
        lo = [vals[j] - summary["methods"][m].get(metric, {}).get("ci_low", vals[j]) for j, m in enumerate(methods)]
        hi = [summary["methods"][m].get(metric, {}).get("ci_high", vals[j]) - vals[j] for j, m in enumerate(methods)]
        ax.bar([x + i * width for x in range(len(methods))], vals, width, yerr=[lo, hi], capsize=3, label=metric)
    ax.set_xticks([x + width for x in range(len(methods))])
    ax.set_xticklabels(methods, rotation=25, ha="right", fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("file-level localization (higher is better)")
    n = summary["n_tasks"]
    ax.set_title(f"Bug localization, n={n} tasks, 95% bootstrap CI" + ("  [n too small for inference]" if summary.get("sample_size_warning") else ""), fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _markdown_table(summary: dict, path) -> None:
    lines = [f"# {summary['experiment']}", "", f"Tasks: {summary['n_tasks']}  |  reference method: `{summary['reference_method']}`  |  seed: {summary['seed']}", ""]
    if summary.get("sample_size_warning"):
        lines += [f"> **Warning:** {summary['sample_size_warning']}", ""]
    lines += ["| method | top-1 | top-5 | MRR | recall@5 | context chars | irrelevant files | median latency ms |", "|---|---|---|---|---|---|---|---|"]

    def cell(e, k):
        v = e.get(k)
        return f"{v['mean']:.3f} [{v['ci_low']:.3f}, {v['ci_high']:.3f}]" if v else "n/a"

    for m, e in summary["methods"].items():
        lines.append(f"| {m} | {cell(e, 'top1')} | {cell(e, 'top5')} | {cell(e, 'mrr')} | {cell(e, 'recall@5')} | "
                     f"{e.get('context_chars', {}).get('mean', float('nan')):.0f} | {e.get('irrelevant_files', {}).get('mean', float('nan')):.2f} | {e.get('latency_ms_median', float('nan')):.1f} |")
    lines += ["", "## Paired comparison against reference (MRR)", "", "| method | mean diff | 95% CI | p | W/L/T | reading |", "|---|---|---|---|---|---|"]
    for m, per in summary["paired_vs_reference"].items():
        d = per.get("mrr")
        if d:
            lines.append(f"| {m} | {d['mean_diff']:+.3f} | [{d['ci_low']:+.3f}, {d['ci_high']:+.3f}] | {d['p_value']:.3f} | {d['wins']}/{d['losses']}/{d['ties']} | {d['interpretation']} |")
    p = [m for m, e in summary["methods"].items() if "repair" in e]
    if p:
        lines += ["", "## Repair", "", "| method | n | patch generated | applied | targeted pass | broad pass | resolved* | regression |", "|---|---|---|---|---|---|---|---|"]
        for m in p:
            r = summary["methods"][m]["repair"]
            lines.append(f"| {m} | {r['n']} | {r['patch_generated']:.2f} | {r['patch_applied']:.2f} | {r['targeted_passed']:.2f} | {r['broad_passed']:.2f} | {r['resolved']:.2f} | {r['regression']:.2f} |")
        lines += ["", "*resolved = baseline failure reproduced AND targeted tests pass after patch AND broad suite passes (SWE-bench-style approximation, not the official harness). Test pass rate is not the same as resolved-task rate."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
