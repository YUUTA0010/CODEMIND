"""Patch lifecycle: create -> verify in a disposable copy -> report -> approval-gated export.

Status values: PATCH_GENERATED, PATCH_APPLIED_IN_SANDBOX, TESTS_PASSED, TESTS_FAILED, TESTS_TIMED_OUT,
VERIFICATION_INCOMPLETE, PATCH_REJECTED. Statuses are derived only from observed command results.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.events import bus
from app.models import AuditEvent, Investigation, PatchProposal, Repository, TestExecution
from app.patches.diff import PatchError, apply_to_directory, changed_files, parse_unified_diff
from app.sandbox import runner

VERIFICATION_LIMITS = [
    "Passing tests show only that the selected tests passed in this environment; they do not prove the change is correct in general.",
    "The test suite may not cover the defect's full behavior, and the patch may introduce regressions in untested code.",
    "Environment differences (dependencies, OS, versions) between this sandbox and production can change outcomes.",
    "A model-written patch was checked mechanically only; it has not been reviewed by a human.",
]


def audit(s: Session, action: str, target_type: str = "", target_id: str | int = "", details: dict | None = None, actor: str = "local") -> None:
    s.add(AuditEvent(actor=actor, action=action, target_type=target_type, target_id=str(target_id), details=details or {}))


def create_patch(s: Session, investigation_id: int, diff: str, rationale: str = "", *, model_name: str | None = None,
                 generation_config: dict | None = None, actor: str = "local") -> PatchProposal:
    inv = s.get(Investigation, investigation_id)
    if inv is None:
        raise NotFoundError(f"investigation {investigation_id} not found")
    files = changed_files(diff)  # validates syntax and path policy; raises PatchError / SecurityViolation
    repo = s.get(Repository, inv.repository_id)
    # dry-run against a throw-away copy so an unappliable patch is rejected immediately with a clear reason
    token = f"dry-{uuid.uuid4().hex[:12]}"
    ws = runner.make_workspace(Path(repo.root_path), get_settings().workspaces_dir, token)
    status, note = "PATCH_GENERATED", ""
    try:
        apply_to_directory(diff, ws)
    except PatchError as e:
        status, note = "PATCH_REJECTED", f"does not apply cleanly: {e.message}"
    finally:
        runner.remove_workspace(ws, get_settings().workspaces_dir)
    p = PatchProposal(investigation_id=investigation_id, diff=diff, changed_files=files, rationale=(rationale + (f"\n\n[CodeMind] Patch {note}" if note else "")).strip(),
                      status=status, model_name=model_name, generation_config=generation_config or {})
    s.add(p)
    s.flush()
    audit(s, "patch.created", "patch", p.id, {"status": status, "files": files, "source": "model" if model_name else "user"}, actor)
    return p


def _targeted_ids(s: Session, patch: PatchProposal, explicit: list[str] | None) -> list[str]:
    if explicit:
        return runner.validate_test_ids(explicit)
    inv = s.get(Investigation, patch.investigation_id)
    ids = list(inv.bug_report.failing_tests or []) if inv.bug_report else []
    return runner.validate_test_ids(ids) if ids else []


def _record(s: Session, patch_id: int, phase: str, res: runner.RunResult) -> tuple[TestExecution, runner.TestSummary]:
    summ = runner.parse_pytest(res.stdout + "\n" + res.stderr)
    outcome = runner.classify(res, summ)
    te = TestExecution(patch_id=patch_id, phase=phase, command=" ".join(res.command), exit_code=res.exit_code, runtime_seconds=round(res.runtime_seconds, 3),
                       timed_out=res.timed_out, stdout=res.stdout, stderr=res.stderr, tests_total=summ.total, tests_passed=summ.passed,
                       tests_failed=summ.failed, failed_test_ids=summ.failed_ids, outcome=outcome, sandbox_mode=res.sandbox_mode,
                       environment={**res.environment, **({"error": res.error} if res.error else {})})
    s.add(te)
    s.flush()
    return te, summ


def verify_patch(s: Session, patch_id: int, *, targeted_tests: list[str] | None = None, run_broad: bool = True, actor: str = "local") -> dict:
    patch = s.get(PatchProposal, patch_id)
    if patch is None:
        raise NotFoundError(f"patch {patch_id} not found")
    if patch.status == "PATCH_REJECTED":
        raise ConflictError("patch was rejected (does not apply or violates policy) and cannot be verified")
    inv = s.get(Investigation, patch.investigation_id)
    repo = s.get(Repository, inv.repository_id)
    settings = get_settings()
    mode, why = runner.resolve_mode(settings)
    bus.publish("verify.progress", {"patch_id": patch_id, "stage": "starting", "sandbox": mode or "unavailable"})
    if mode is None:
        patch.status = "VERIFICATION_INCOMPLETE"
        audit(s, "patch.verify_blocked", "patch", patch_id, {"reason": why}, actor)
        return build_report(s, patch_id, extra_notes=[f"Verification did not run: {why}."])
    targeted = _targeted_ids(s, patch, targeted_tests)
    token = f"verify-{patch_id}-{uuid.uuid4().hex[:8]}"
    ws = runner.make_workspace(Path(repo.root_path), settings.workspaces_dir, token)
    notes: list[str] = []
    try:
        if not targeted:
            notes.append("No failing test identifiers were supplied, so no targeted run was possible; only the broad suite was run.")
        # 1) baseline (before the patch) - shows whether the failure actually reproduces here
        if targeted:
            bus.publish("verify.progress", {"patch_id": patch_id, "stage": "baseline"})
            base_res = runner.run_command(runner.pytest_argv(targeted), ws, mode, settings)
            _record(s, patch_id, "baseline", base_res)
        # 2) apply the patch inside the disposable copy only
        try:
            apply_to_directory(patch.diff, ws)
        except PatchError as e:
            patch.status = "PATCH_REJECTED"
            audit(s, "patch.apply_failed", "patch", patch_id, {"error": e.message}, actor)
            return build_report(s, patch_id, extra_notes=[f"Patch could not be applied in the sandbox: {e.message}"])
        patch.status = "PATCH_APPLIED_IN_SANDBOX"
        s.flush()
        bus.publish("verify.progress", {"patch_id": patch_id, "stage": "applied"})
        final = "VERIFICATION_INCOMPLETE"
        targeted_outcome = None
        if targeted:
            bus.publish("verify.progress", {"patch_id": patch_id, "stage": "targeted"})
            res = runner.run_command(runner.pytest_argv(targeted), ws, mode, settings)
            te, _ = _record(s, patch_id, "targeted", res)
            targeted_outcome = te.outcome
            final = te.outcome
        if run_broad and (targeted_outcome in (None, "TESTS_PASSED")):
            bus.publish("verify.progress", {"patch_id": patch_id, "stage": "broad"})
            res = runner.run_command(runner.pytest_argv(None), ws, mode, settings)
            te, _ = _record(s, patch_id, "broad", res)
            if te.outcome == "TESTS_PASSED":
                final = "TESTS_PASSED" if targeted_outcome in (None, "TESTS_PASSED") else final
            else:
                final = te.outcome
                if te.outcome == "TESTS_FAILED" and targeted_outcome == "TESTS_PASSED":
                    notes.append("Targeted tests passed but the broader suite failed: possible regression.")
        elif run_broad:
            notes.append("Broad suite skipped because the targeted tests did not pass.")
        patch.status = final
        audit(s, "patch.verified", "patch", patch_id, {"status": final, "sandbox": mode}, actor)
    finally:
        runner.remove_workspace(ws, settings.workspaces_dir)
    bus.publish("verify.done", {"patch_id": patch_id, "status": patch.status})
    return build_report(s, patch_id, extra_notes=notes)


def build_report(s: Session, patch_id: int, extra_notes: list[str] | None = None) -> dict:
    patch = s.get(PatchProposal, patch_id)
    execs = s.execute(select(TestExecution).where(TestExecution.patch_id == patch_id).order_by(TestExecution.id)).scalars().all()
    by_phase = {e.phase: e for e in execs}
    base, targ, broad = by_phase.get("baseline"), by_phase.get("targeted"), by_phase.get("broad")
    facts, notes = [], list(extra_notes or [])
    if base:
        facts.append(f"Before the patch, the targeted tests {'failed (reproduced)' if base.outcome == 'TESTS_FAILED' else 'did not fail' if base.outcome == 'TESTS_PASSED' else 'could not be evaluated'} in the sandbox.")
        if base.outcome == "TESTS_PASSED":
            notes.append("The targeted tests already passed before the patch, so this run does not demonstrate that the patch fixes anything.")
    if targ:
        facts.append(f"After the patch, targeted tests: {targ.outcome} (passed={targ.tests_passed}, failed={targ.tests_failed}).")
    if broad:
        facts.append(f"After the patch, broader suite: {broad.outcome} (passed={broad.tests_passed}, failed={broad.tests_failed}).")
    regression = bool(targ and targ.outcome == "TESTS_PASSED" and broad and broad.outcome == "TESTS_FAILED")
    return {
        "patch_id": patch_id, "status": patch.status, "changed_files": patch.changed_files, "sandbox_modes": sorted({e.sandbox_mode for e in execs if e.sandbox_mode}),
        "isolation": sorted({str(e.environment.get("isolation")) for e in execs if e.environment}),
        "executions": [{"id": e.id, "phase": e.phase, "command": e.command, "exit_code": e.exit_code, "runtime_seconds": e.runtime_seconds, "timed_out": e.timed_out,
                        "tests_total": e.tests_total, "tests_passed": e.tests_passed, "tests_failed": e.tests_failed, "failed_test_ids": e.failed_test_ids,
                        "outcome": e.outcome, "sandbox_mode": e.sandbox_mode, "environment": e.environment} for e in execs],
        "observed_facts": facts, "notes": notes, "possible_regression": regression, "verification_limits": VERIFICATION_LIMITS,
        "export_approved": patch.export_approved, "exported_to": patch.exported_to,
    }


def export_patch(s: Session, patch_id: int, *, approve: bool, apply_to_working_tree: bool = False, actor: str = "local") -> dict:
    """Both writing a .patch file and modifying the original working tree require explicit approval."""
    if not approve:
        raise AppError("explicit approval is required to export or apply a patch (approve=true)", code="approval_required", status_code=403)
    patch = s.get(PatchProposal, patch_id)
    if patch is None:
        raise NotFoundError(f"patch {patch_id} not found")
    if patch.status == "PATCH_REJECTED":
        raise ConflictError("rejected patches cannot be exported")
    inv = s.get(Investigation, patch.investigation_id)
    repo = s.get(Repository, inv.repository_id)
    settings = get_settings()
    out = settings.exports_dir / f"patch-{patch.id}-{hashlib.sha256(patch.diff.encode()).hexdigest()[:10]}.patch"
    out.write_text(patch.diff if patch.diff.endswith("\n") else patch.diff + "\n", encoding="utf-8")
    applied: list[str] = []
    if apply_to_working_tree:
        # re-validate and apply atomically to the real working tree; fails without touching anything if context no longer matches
        applied = apply_to_directory(patch.diff, Path(repo.root_path))
    patch.export_approved = True
    patch.exported_to = str(out)
    audit(s, "patch.exported", "patch", patch_id, {"file": str(out), "applied_to_working_tree": apply_to_working_tree, "files": applied}, actor)
    return {"patch_id": patch_id, "exported_to": str(out), "applied_to_working_tree": apply_to_working_tree, "applied_files": applied,
            "verification_status": patch.status, "verification_limits": VERIFICATION_LIMITS}


__all__ = ["create_patch", "verify_patch", "build_report", "export_patch", "parse_unified_diff", "audit", "VERIFICATION_LIMITS"]
