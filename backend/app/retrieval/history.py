"""Optional git-history signal (OFF by default: weight 0).

Computes a per-file change-frequency prior from `git log`. It is a weak, generic "files that change often are more
bug-prone" prior and is NOT bug-specific. To stay safe on untrusted repositories, git is run with config-driven code
execution disabled (core.fsmonitor, hooks) and a hard timeout.
"""
from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path


def file_churn(root: str, max_commits: int = 300, timeout: int = 15) -> dict[str, float]:
    """-> {relative path: churn normalised to [0,1]}; empty when not a git repo or git is unavailable."""
    p = Path(root)
    if not (p / ".git").exists():
        return {}
    cmd = [
        "git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
        "-C", str(p), "log", f"-n{max_commits}", "--name-only", "--pretty=format:", "--no-ext-diff", "--no-textconv",
    ]
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/nonexistent"}
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    counts = Counter(line.strip() for line in out.splitlines() if line.strip())
    if not counts:
        return {}
    top = max(counts.values())
    return {path: c / top for path, c in counts.items()}
