"""Sandbox restrictions, command timeouts, result classification and the patch-verification lifecycle."""
import os
import sys
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import session_scope
from app.models import Investigation, BugReport, PatchProposal, Repository, TestExecution
from app.patches import service as svc
from app.sandbox import runner
from tests.conftest import tree_hashes, write_tree

pytestmark = pytest.mark.slow


def test_command_timeout_kills_the_process(env):
    ws = write_tree(env / "ws", {"a.py": ""})
    res = runner.run_command(["python", "-c", "import time; time.sleep(60)"], ws, "local", timeout=2)
    assert res.timed_out is True and res.exit_code is None and res.runtime_seconds < 10
    assert runner.classify(res, runner.TestSummary()) == "TESTS_TIMED_OUT"


def test_timeout_kills_child_process_groups_too(env):
    ws = write_tree(env / "ws", {"a.py": ""})
    marker = ws / "alive.txt"
    code = f"import subprocess,time; subprocess.Popen(['sh','-c','sleep 4; echo x > {marker}']); time.sleep(60)"
    runner.run_command(["python", "-c", code], ws, "local", timeout=1)
    import time

    time.sleep(5)
    assert not marker.exists(), "grandchildren must be killed with the process group"


def test_environment_is_scrubbed_of_host_secrets(env, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("CODEMIND_LLM_API_KEY", "sk-should-not-leak")
    ws = write_tree(env / "ws", {"a.py": ""})
    res = runner.run_command(["python", "-c", "import os; print(sorted(os.environ))"], ws, "local")
    assert "SUPER_SECRET_TOKEN" not in res.stdout and "CODEMIND_LLM_API_KEY" not in res.stdout and "hunter2" not in res.stdout
    assert res.isolation == "restricted-local" and res.environment["isolation"] == "restricted-local"


def test_memory_limit_is_enforced(env, monkeypatch):
    monkeypatch.setenv("CODEMIND_SANDBOX_MEMORY_MB", "256")
    get_settings.cache_clear()
    ws = write_tree(env / "ws", {"a.py": ""})
    res = runner.run_command(["python", "-c", "x = bytearray(2 * 1024**3); print('ALLOCATED')"], ws, "local", get_settings())
    assert "ALLOCATED" not in res.stdout and res.exit_code not in (0, None)


def test_cwd_is_the_disposable_workspace(env):
    ws = write_tree(env / "ws", {"a.py": ""})
    res = runner.run_command(["python", "-c", "import os; print(os.getcwd())"], ws, "local")
    assert res.stdout.strip() == str(ws)


@pytest.mark.parametrize("bad", ["--help", "-k x", "tests/a.py; rm -rf /", "$(id)", "../../etc/passwd", "a b", "", "tests/a.py::t\nother", "`id`"])
def test_unsafe_test_ids_rejected(bad):
    with pytest.raises(AppError):
        runner.validate_test_ids([bad])


def test_safe_test_ids_accepted():
    ids = ["tests/test_a.py::test_x", "tests/test_a.py::TestK::test_y[param-1]", "test_b.py"]
    assert runner.validate_test_ids(ids) == ids
    assert runner.pytest_argv(ids)[-3:] == ids and runner.pytest_argv(None)[:3] == ["python", "-m", "pytest"]


def test_no_unsafe_execution_by_default(env, monkeypatch):
    monkeypatch.setenv("CODEMIND_ALLOW_LOCAL_SANDBOX", "false")
    monkeypatch.setenv("CODEMIND_SANDBOX_MODE", "auto")
    get_settings.cache_clear()
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    mode, why = runner.resolve_mode(get_settings())
    assert mode is None and "not enabled" in why
    monkeypatch.setenv("CODEMIND_SANDBOX_MODE", "local")
    get_settings.cache_clear()
    assert runner.resolve_mode(get_settings())[0] is None
    monkeypatch.setenv("CODEMIND_SANDBOX_MODE", "docker")
    get_settings.cache_clear()
    assert runner.resolve_mode(get_settings())[0] is None
    monkeypatch.setenv("CODEMIND_ALLOW_LOCAL_SANDBOX", "true")
    monkeypatch.setenv("CODEMIND_SANDBOX_MODE", "auto")
    get_settings.cache_clear()
    assert runner.resolve_mode(get_settings())[0] == "local"
    monkeypatch.setattr(runner, "docker_available", lambda: True)
    assert runner.resolve_mode(get_settings())[0] == "docker"


def test_workspace_copy_excludes_git_and_drops_escaping_symlinks(env):
    outside = env / "outside.py"
    outside.write_text("secret")
    repo = write_tree(env / "repo", {"a.py": "x=1\n", ".git/config": "[core]\n", "node_modules/m.js": "1\n"})
    os.symlink(outside, repo / "escape.py")
    os.symlink(repo / "a.py", repo / "inner.py")
    ws = runner.make_workspace(repo, get_settings().workspaces_dir, "t1")
    assert (ws / "a.py").exists() and not (ws / ".git").exists() and not (ws / "node_modules").exists()
    assert not (ws / "escape.py").exists() and not (ws / "escape.py").is_symlink()
    runner.remove_workspace(ws, get_settings().workspaces_dir)
    assert not ws.exists() and outside.read_text() == "secret"
    runner.remove_workspace(repo, get_settings().workspaces_dir)  # refuses to delete outside the workspaces dir
    assert repo.exists()


PYTEST_OUT_FAIL = "FAILED tests/test_a.py::test_x - assert 1 == 2\n=========== 1 failed, 3 passed, 1 skipped in 0.12s ===========\n"


def test_pytest_output_parsing_and_classification():
    s = runner.parse_pytest(PYTEST_OUT_FAIL)
    assert (s.passed, s.failed, s.total, s.failed_ids) == (3, 1, 5, ["tests/test_a.py::test_x"])
    ok = runner.parse_pytest("..\n2 passed in 0.01s\n")
    assert (ok.passed, ok.failed, ok.total) == (2, 0, 2)
    none = runner.parse_pytest("no tests ran in 0.01s")
    assert none.total == 0
    unknown = runner.parse_pytest("garbage output")
    assert unknown.total is None and unknown.passed is None  # never guessed

    def res(code, to=False, err=None):
        return runner.RunResult(["x"], code, 1.0, to, "", "", "local", "restricted-local", error=err)

    assert runner.classify(res(0), ok) == "TESTS_PASSED"
    assert runner.classify(res(0), none) == "VERIFICATION_INCOMPLETE"  # exit 0 but nothing ran
    assert runner.classify(res(1), s) == "TESTS_FAILED"
    assert runner.classify(res(None, True), s) == "TESTS_TIMED_OUT"
    assert runner.classify(res(5), none) == "VERIFICATION_INCOMPLETE"
    assert runner.classify(res(2), unknown) == "VERIFICATION_INCOMPLETE"
    assert runner.classify(res(None, err="failed to start"), unknown) == "VERIFICATION_INCOMPLETE"
    assert runner.classify(res(137), unknown) == "VERIFICATION_INCOMPLETE"


def make_inv(rec):
    with session_scope() as s:
        r = Repository(name="x", root_path=rec["repo_path"])
        s.add(r)
        s.flush()
        inv = Investigation(repository_id=r.id, title="t")
        inv.bug_report = BugReport(description="d", failing_tests=rec["relevant_tests"])
        s.add(inv)
        s.flush()
        return inv.id


def test_verify_good_patch_records_honest_evidence(db, task):
    before = tree_hashes(Path(task["repo_path"]))
    iid = make_inv(task)
    with session_scope() as s:
        pid = svc.create_patch(s, iid, task["oracle_fix_diff"], "oracle fix (test fixture)").id
    with session_scope() as s:
        rep = svc.verify_patch(s, pid)
    assert rep["status"] == "TESTS_PASSED" and rep["isolation"] == ["restricted-local"]
    phases = {e["phase"]: e for e in rep["executions"]}
    assert phases["baseline"]["outcome"] == "TESTS_FAILED" and phases["targeted"]["outcome"] == "TESTS_PASSED" and phases["broad"]["outcome"] == "TESTS_PASSED"
    assert phases["broad"]["tests_passed"] == 20 and phases["targeted"]["exit_code"] == 0 and "pytest" in phases["targeted"]["command"]
    assert rep["possible_regression"] is False and len(rep["verification_limits"]) >= 3
    assert tree_hashes(Path(task["repo_path"])) == before, "verification must run in a disposable copy"
    assert not list(get_settings().workspaces_dir.iterdir()), "workspace is cleaned up"


def test_verify_wrong_patch_is_reported_as_failed(db, task):
    iid = make_inv(task)
    wrong = task["oracle_fix_diff"].replace("+    if quantity >= BULK_THRESHOLD:", "+    if quantity > BULK_THRESHOLD:  # still wrong")
    with session_scope() as s:
        pid = svc.create_patch(s, iid, wrong).id
    with session_scope() as s:
        rep = svc.verify_patch(s, pid)
    assert rep["status"] == "TESTS_FAILED" and any("skipped" in n for n in rep["notes"])
    assert "broad" not in {e["phase"] for e in rep["executions"]}


def test_regression_is_detected_when_targeted_passes_but_suite_breaks(db, task):
    iid = make_inv(task)
    import difflib

    path = Path(task["repo_path"]) / "inventory/pricing.py"
    buggy = path.read_text()
    fixed = buggy.replace("if quantity > BULK_THRESHOLD:", "if quantity >= BULK_THRESHOLD:")  # fixes the target bug...
    fixed = fixed.replace("return round(amount * TAX_RATE, 2)", "return round(amount * TAX_RATE * 2, 2)")  # ...but breaks tax (regression)
    diff = "".join(difflib.unified_diff(buggy.splitlines(keepends=True), fixed.splitlines(keepends=True), "a/inventory/pricing.py", "b/inventory/pricing.py"))
    assert "TAX_RATE * 2" in diff and ">= BULK_THRESHOLD" in diff
    with session_scope() as s:
        pid = svc.create_patch(s, iid, diff).id
    with session_scope() as s:
        rep = svc.verify_patch(s, pid)
    assert rep["status"] == "TESTS_FAILED" and rep["possible_regression"] is True
    assert any("regression" in n for n in rep["notes"])


def test_unappliable_patch_is_rejected_at_creation_and_cannot_be_verified(db, task):
    iid = make_inv(task)
    stale = "--- a/inventory/pricing.py\n+++ b/inventory/pricing.py\n@@ -1,1 +1,1 @@\n-nothing like this\n+x\n"
    with session_scope() as s:
        p = svc.create_patch(s, iid, stale)
        assert p.status == "PATCH_REJECTED" and "does not apply" in p.rationale
        pid = p.id
    from app.core.errors import ConflictError

    with pytest.raises(ConflictError):
        with session_scope() as s:
            svc.verify_patch(s, pid)


def test_verification_blocked_when_no_safe_sandbox(db, task, monkeypatch):
    iid = make_inv(task)
    with session_scope() as s:
        pid = svc.create_patch(s, iid, task["oracle_fix_diff"]).id
    monkeypatch.setenv("CODEMIND_ALLOW_LOCAL_SANDBOX", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    with session_scope() as s:
        rep = svc.verify_patch(s, pid)
    assert rep["status"] == "VERIFICATION_INCOMPLETE" and not rep["executions"] and any("did not run" in n for n in rep["notes"])
    assert "TESTS_PASSED" not in str(rep["status"])


def test_verification_without_test_ids_only_runs_broad_suite_and_says_so(db, task):
    with session_scope() as s:
        r = Repository(name="x", root_path=task["repo_path"])
        s.add(r)
        s.flush()
        inv = Investigation(repository_id=r.id, title="t")
        inv.bug_report = BugReport(description="d", failing_tests=[])
        s.add(inv)
        s.flush()
        iid = inv.id
    with session_scope() as s:
        pid = svc.create_patch(s, iid, task["oracle_fix_diff"]).id
    with session_scope() as s:
        rep = svc.verify_patch(s, pid)
    assert [e["phase"] for e in rep["executions"]] == ["broad"] and any("No failing test identifiers" in n for n in rep["notes"])


def test_export_requires_explicit_approval_and_apply_is_separate(db, task):
    repo = Path(task["repo_path"])
    before = tree_hashes(repo)
    iid = make_inv(task)
    with session_scope() as s:
        pid = svc.create_patch(s, iid, task["oracle_fix_diff"]).id
    with pytest.raises(AppError) as e:
        with session_scope() as s:
            svc.export_patch(s, pid, approve=False)
    assert e.value.code == "approval_required" and e.value.status_code == 403
    with session_scope() as s:
        out = svc.export_patch(s, pid, approve=True)
    assert Path(out["exported_to"]).read_text().startswith("--- a/inventory/pricing.py") and out["applied_to_working_tree"] is False
    assert tree_hashes(repo) == before, "exporting a .patch file must not touch the working tree"
    with session_scope() as s:
        out = svc.export_patch(s, pid, approve=True, apply_to_working_tree=True)
    assert out["applied_files"] == ["inventory/pricing.py"] and "quantity >= BULK_THRESHOLD" in (repo / "inventory/pricing.py").read_text()
    with session_scope() as s:
        assert [a.action for a in s.query(__import__("app.models", fromlist=["AuditEvent"]).AuditEvent).order_by("id")][-1] == "patch.exported"


def test_test_execution_rows_are_complete(db, task):
    iid = make_inv(task)
    with session_scope() as s:
        pid = svc.create_patch(s, iid, task["oracle_fix_diff"]).id
        svc.verify_patch(s, pid)
    with session_scope() as s:
        rows = s.query(TestExecution).filter_by(patch_id=pid).all()
        assert {r.phase for r in rows} == {"baseline", "targeted", "broad"}
        for r in rows:
            assert r.command and r.runtime_seconds >= 0 and r.sandbox_mode == "local" and r.environment["python"] == sys.version.split()[0] and r.stdout
        assert s.get(PatchProposal, pid).status == "TESTS_PASSED"


def test_windows_never_offers_local_mode(monkeypatch):
    from app.sandbox import runner

    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    s = runner.get_settings().model_copy(update={"allow_local_sandbox": True, "sandbox_mode": "auto"})
    mode, why = runner.resolve_mode(s)
    assert mode is None and "Windows" in why
    monkeypatch.setattr(runner, "docker_available", lambda: True)
    assert runner.resolve_mode(s)[0] == "docker"
