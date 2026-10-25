"""Adapter for repository-specific / controlled benchmark tasks stored as a directory tree:

    <root>/<task_key>/repo/            the buggy repository snapshot
    <root>/<task_key>/task.json        {"description", "stack_trace", "relevant_tests", "ground_truth_files", "ground_truth_symbols", ...}

(The synthetic suite writes exactly this layout, so it is also loaded through this adapter.)
"""
from __future__ import annotations

import json
from pathlib import Path

from app.experiments.tasks import BenchmarkTaskRecord


def load_directory_tasks(root: Path) -> list[BenchmarkTaskRecord]:
    out = []
    for tj in sorted(Path(root).glob("*/task.json")):
        rec = json.loads(tj.read_text(encoding="utf-8"))
        repo = tj.parent / "repo"
        if not repo.is_dir():
            continue
        gt = rec.get("ground_truth_files") or []
        if not gt:
            raise ValueError(f"{tj}: ground_truth_files is required for evaluation")
        out.append(BenchmarkTaskRecord(
            task_key=rec.get("task_key", tj.parent.name), repo_path=str(repo.resolve()), repo_revision=rec.get("repo_revision", "snapshot"),
            description=rec["description"], stack_trace=rec.get("stack_trace", ""), relevant_tests=rec.get("relevant_tests", []),
            ground_truth_files=gt, ground_truth_symbols=rec.get("ground_truth_symbols", [])))
    return out
