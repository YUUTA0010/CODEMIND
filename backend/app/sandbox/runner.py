"""Controlled command execution for verification.

Modes
-----
docker  Disposable container: --network none (unless enabled), read-write bind of the *disposable copy only*, memory /
        CPU / pids limits, read-only root FS, all capabilities dropped, no-new-privileges, non-root user.
local   Restricted host subprocess (new session, rlimits for CPU/memory/processes/file size, scrubbed environment,
        hard timeout with process-group kill). It provides NO filesystem or network isolation: it only runs when
        CODEMIND_ALLOW_LOCAL_SANDBOX=true, and every result records isolation="restricted-local".

Commands are argv lists chosen by CodeMind (never free-form model output). Test identifiers are validated first.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.errors import AppError

_SAFE_TEST_ID = re.compile(r"^[\w./\-]+(::[\w\[\].\-:]+)?$")
MAX_LOG_CHARS = 60_000
IGNORE_ON_COPY = shutil.ignore_patterns(".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "*.pyc", "dist", "build", ".mypy_cache")


@dataclass
class RunResult:
    command: list[str]
    exit_code: int | None
    runtime_seconds: float
    timed_out: bool
    stdout: str
    stderr: str
    sandbox_mode: str
    isolation: str
    environment: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class TestSummary:
    __test__ = False
    total: int | None = None
    passed: int | None = None
    failed: int | None = None
    failed_ids: list[str] = field(default_factory=list)


def validate_test_ids(ids: list[str]) -> list[str]:
    out = []
    for t in ids:
        t = t.strip()
        if not t or t.startswith("-") or not _SAFE_TEST_ID.match(t) or ".." in t:
            raise AppError(f"unsafe or malformed test identifier: {t!r}", code="invalid_test_id", status_code=422)
        out.append(t)
    return out


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_mode(settings: Settings | None = None) -> tuple[str | None, str]:
    """-> (mode or None, explanation). None means verification cannot run safely."""
    s = settings or get_settings()
    if os.name == "nt":  # local mode needs POSIX preexec_fn/rlimits, which Windows lacks
        if s.sandbox_mode != "local" and docker_available():
            return "docker", "docker available"
        return None, "On Windows only Docker mode is supported; Docker is not reachable or not requested"
    if s.sandbox_mode == "docker":
        return ("docker", "docker requested") if docker_available() else (None, "Docker was requested but the daemon is not reachable")
    if s.sandbox_mode == "local":
        return ("local", "local requested") if s.allow_local_sandbox else (None, "local sandbox requested but CODEMIND_ALLOW_LOCAL_SANDBOX is not true")
    if docker_available():
        return "docker", "docker available"
    if s.allow_local_sandbox:
        return "local", "docker unavailable; local restricted subprocess explicitly allowed"
    return None, "Docker is unavailable and local execution is not enabled (set CODEMIND_ALLOW_LOCAL_SANDBOX=true to opt in)"


def make_workspace(repo_root: Path, workspaces_dir: Path, token: str) -> Path:
    """Disposable copy of the repository (never the original). Symlinks are not followed."""
    dest = workspaces_dir / token
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(repo_root, dest, symlinks=True, ignore=IGNORE_ON_COPY)
    # drop any symlinks that point outside the copy
    base = dest.resolve()
    for p in list(dest.rglob("*")):
        if p.is_symlink():
            try:
                tgt = p.resolve()
                if base not in tgt.parents and tgt != base:
                    p.unlink()
            except OSError:
                p.unlink()
    return dest


def remove_workspace(path: Path, workspaces_dir: Path) -> None:
    try:
        if workspaces_dir.resolve() in path.resolve().parents:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _limit_fn(memory_mb: int, cpu_seconds: int, pids: int):
    def apply():  # runs in the child between fork and exec
        import resource

        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (200 * 1024 * 1024, 200 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (pids, pids))
        except (ValueError, OSError):
            pass

    return apply


def _clip(b: bytes | str | None) -> str:
    if b is None:
        return ""
    s = b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
    return s if len(s) <= MAX_LOG_CHARS else s[:MAX_LOG_CHARS // 2] + f"\n...[truncated {len(s) - MAX_LOG_CHARS} chars]...\n" + s[-MAX_LOG_CHARS // 2:]


def _environment(mode: str, isolation: str, s: Settings) -> dict:
    return {"python": sys.version.split()[0], "platform": platform.platform(), "sandbox_mode": mode, "isolation": isolation,
            "timeout_seconds": s.sandbox_timeout_seconds, "memory_mb": s.sandbox_memory_mb, "network_enabled": s.sandbox_network if mode == "docker" else "not isolated"}


def run_command(argv: list[str], workspace: Path, mode: str, settings: Settings | None = None, timeout: int | None = None) -> RunResult:
    s = settings or get_settings()
    timeout = timeout or s.sandbox_timeout_seconds
    t0 = time.perf_counter()
    if mode == "docker":
        name = f"codemind-{os.getpid()}-{int(t0 * 1000)}"
        cmd = ["docker", "run", "--rm", "--name", name, "--memory", f"{s.sandbox_memory_mb}m", "--cpus", str(s.sandbox_cpus),
               "--pids-limit", str(s.sandbox_pids), "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
               "--tmpfs", "/tmp:rw,size=256m", "--user", "65534:65534", "-v", f"{workspace}:/work:rw", "-w", "/work",
               "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp"]
        if not s.sandbox_network:
            cmd += ["--network", "none"]
        cmd += [s.sandbox_docker_image, *argv]
        isolation = "docker"
        popen_kwargs: dict = {}
    else:
        cmd = list(argv)
        if cmd and cmd[0] == "python":
            cmd[0] = sys.executable
        isolation = "restricted-local"
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workspace), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "", "NO_COLOR": "1"}
        popen_kwargs = {"cwd": str(workspace), "env": env, "preexec_fn": _limit_fn(s.sandbox_memory_mb, timeout + 5, s.sandbox_pids)}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **popen_kwargs)
    except OSError as e:
        return RunResult(argv, None, time.perf_counter() - t0, False, "", "", mode, isolation, _environment(mode, isolation, s), f"failed to start: {e}")
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if mode == "docker":
            subprocess.run(["docker", "kill", name], capture_output=True)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        out, err = proc.communicate()
    return RunResult(argv, None if timed_out else proc.returncode, time.perf_counter() - t0, timed_out, _clip(out), _clip(err), mode, isolation,
                     _environment(mode, isolation, s))


_PYTEST_SUMMARY = re.compile(r"=+ (.*?) in [\d.]+s.*?=+$|^(\d+ (?:passed|failed|error|skipped).*?) in [\d.]+s", re.M)
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s]+)", re.M)


def parse_pytest(output: str) -> TestSummary:
    """Extract counts and failing ids from pytest output. Fields stay None when they cannot be identified (never guessed)."""
    summ = TestSummary()
    m = None
    for m in _PYTEST_SUMMARY.finditer(output):
        pass
    if m:
        text = m.group(1) or m.group(2) or ""
        counts = {k: int(v) for v, k in _COUNT.findall(text)}
        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
        summ.passed, summ.failed = passed, failed
        summ.total = passed + failed + counts.get("skipped", 0) + counts.get("xfailed", 0)
    elif re.search(r"no tests ran", output):
        summ.total = summ.passed = summ.failed = 0
    summ.failed_ids = sorted(set(_FAILED_LINE.findall(output)))
    return summ


def classify(result: RunResult, summary: TestSummary) -> str:
    """Honest outcome classification from observed evidence only."""
    if result.error:
        return "VERIFICATION_INCOMPLETE"
    if result.timed_out:
        return "TESTS_TIMED_OUT"
    if result.exit_code == 0:
        if summary.total == 0:
            return "VERIFICATION_INCOMPLETE"  # nothing actually ran
        return "TESTS_PASSED"
    if result.exit_code == 5:  # pytest: no tests collected
        return "VERIFICATION_INCOMPLETE"
    if result.exit_code in (1,):
        return "TESTS_FAILED"
    # 2/3/4 (interrupted, internal error, usage error) and signals: we cannot call this a test failure with confidence
    return "TESTS_FAILED" if summary.failed else "VERIFICATION_INCOMPLETE"


def pytest_argv(test_ids: list[str] | None, *, extra: list[str] | None = None) -> list[str]:
    argv = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE"]
    argv += extra or []
    argv += validate_test_ids(test_ids) if test_ids else []
    return argv
