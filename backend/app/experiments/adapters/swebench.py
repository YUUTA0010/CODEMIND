"""SWE-bench adapter (local data only; nothing is downloaded automatically).

Licensing / provenance: SWE-bench instances are derived from public GitHub repositories under their own licenses and the
dataset has its own terms. Obtain the dataset yourself from its official distribution, review its license, and pass a
local JSONL file. CodeMind never redistributes it. This adapter has been unit-tested only on a SMALL SYNTHETIC JSONL
that follows the documented field names - it has NOT been run on real SWE-bench data in this repository's test suite.

Expected JSONL fields per line: instance_id, repo ("owner/name"), base_commit, problem_statement, patch (gold source patch,
used ONLY to derive ground-truth files), FAIL_TO_PASS and PASS_TO_PASS (JSON list or JSON-encoded string).

Repositories must already be cloned locally at `<repos_dir>/<owner>__<name>`. The base commit is exported with
`git archive` (no checkout hooks run) into a per-task directory.
"""
from __future__ import annotations

import json
import re
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

from app.experiments.tasks import BenchmarkTaskRecord

_DIFF_FILE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.M)


def gold_patch_files(patch: str) -> list[str]:
    seen: list[str] = []
    for a, b in _DIFF_FILE.findall(patch or ""):
        if b not in seen:
            seen.append(b)
    return seen


def _as_list(v) -> list[str]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return [v]
    return list(v or [])


def export_commit(repo_dir: Path, commit: str, dest: Path) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise ValueError(f"invalid commit id: {commit!r}")
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo_dir), "archive", "--format=tar", commit],
                          capture_output=True, timeout=300, check=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"})
    with tarfile.open(fileobj=BytesIO(proc.stdout)) as tf:
        base = dest.resolve()
        for m in tf.getmembers():
            target = (dest / m.name).resolve()
            if base not in target.parents and target != base or m.issym() or m.islnk():
                continue  # skip path escapes and links
            tf.extract(m, dest, filter="data")


def load_swebench_jsonl(jsonl: Path, repos_dir: Path, workdir: Path, *, limit: int | None = None) -> list[BenchmarkTaskRecord]:
    out: list[BenchmarkTaskRecord] = []
    for i, line in enumerate(Path(jsonl).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        if limit is not None and len(out) >= limit:
            break
        d = json.loads(line)
        iid = d["instance_id"]
        src = Path(repos_dir) / d["repo"].replace("/", "__")
        if not src.is_dir():
            raise FileNotFoundError(f"clone of {d['repo']} not found at {src}")
        dest = Path(workdir) / iid / "repo"
        if not dest.exists():
            export_commit(src, d["base_commit"], dest)
        files = gold_patch_files(d.get("patch", ""))
        if not files:
            continue
        out.append(BenchmarkTaskRecord(task_key=iid, repo_path=str(dest), repo_revision=d["base_commit"], description=d["problem_statement"],
                                       stack_trace="", relevant_tests=_as_list(d.get("FAIL_TO_PASS")), ground_truth_files=files))
    return out
