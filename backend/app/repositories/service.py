"""Application services shared by API routes: repositories, retrieval-run recording, investigation serialization."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.localization.engine import Candidate
from app.models import (BugReport, CandidateLocation, Hypothesis, IndexingJob, Investigation, PatchProposal, Repository, RetrievalRun, SourceFile)
from app.retrieval.hybrid import RetrievalResult
from app.retrieval.types import QueryObject, RetrievalConfig


def get_repo(s: Session, repo_id: int) -> Repository:
    repo = s.get(Repository, repo_id)
    if repo is None:
        raise NotFoundError(f"repository {repo_id} not found")
    return repo


def repo_dict(s: Session, repo: Repository) -> dict:
    nfiles = s.scalar(select(func.count()).select_from(SourceFile).where(SourceFile.repository_id == repo.id)) or 0
    job = s.execute(select(IndexingJob).where(IndexingJob.repository_id == repo.id).order_by(IndexingJob.id.desc()).limit(1)).scalar_one_or_none()
    return {"id": repo.id, "name": repo.name, "root_path": repo.root_path, "is_git": repo.is_git, "kind": repo.kind, "status": repo.status,
            "fingerprint": repo.fingerprint, "languages": repo.languages, "index_version": repo.index_version, "file_count": nfiles,
            "created_at": repo.created_at, "last_indexed_at": repo.last_indexed_at, "latest_job": job_dict(job) if job else None}


def job_dict(j: IndexingJob) -> dict:
    return {"id": j.id, "repository_id": j.repository_id, "mode": j.mode, "status": j.status, "progress": j.progress, "total": j.total, "message": j.message,
            "error": j.error, "cancel_requested": j.cancel_requested, "stats": j.stats, "started_at": j.started_at, "finished_at": j.finished_at, "created_at": j.created_at}


def active_job(s: Session, repo_id: int) -> IndexingJob | None:
    return s.execute(select(IndexingJob).where(IndexingJob.repository_id == repo_id, IndexingJob.status.in_(["QUEUED", "RUNNING"]))
                     .order_by(IndexingJob.id.desc()).limit(1)).scalar_one_or_none()


def require_indexed(repo: Repository) -> None:
    if repo.index_version == 0:
        raise ConflictError(f"repository {repo.id} has not been indexed yet", code="not_indexed")


def record_run(s: Session, repo_id: int, investigation_id: int | None, query_text: str, cfg: RetrievalConfig, result: RetrievalResult, ctx: dict,
               extra: dict | None = None) -> RetrievalRun:
    run = RetrievalRun(repository_id=repo_id, investigation_id=investigation_id, query=query_text[:20000], method=cfg.method,
                       config={**cfg.to_dict(), "active_signals": result.signals_used, "warnings": result.warnings, "diagnostics": result.diagnostics, **(extra or {})},
                       index_version=result.index_version, runtime_ms=round(result.runtime_ms, 3), context_chars=ctx["used_chars"],
                       selected=[{k: v for k, v in sn.items() if k != "content"} for sn in ctx["selected"]])
    s.add(run)
    s.flush()
    return run


def store_candidates(s: Session, run: RetrievalRun, investigation_id: int | None, cands: list[Candidate]) -> None:
    for c in cands:
        s.add(CandidateLocation(retrieval_run_id=run.id, investigation_id=investigation_id, rank=c.rank, file_path=c.file_path, symbol_name=c.symbol_name,
                                start_line=c.start_line, end_line=c.end_line, score=c.score, signals=c.signals,
                                evidence={"items": c.evidence, "uncertainty": c.uncertainty, "related_tests": c.related_tests},
                                confidence_label=c.confidence_label))


def candidate_dict(c: CandidateLocation) -> dict:
    return {"id": c.id, "rank": c.rank, "file_path": c.file_path, "symbol_name": c.symbol_name, "start_line": c.start_line, "end_line": c.end_line,
            "score": c.score, "signals": c.signals, "confidence_label": c.confidence_label,
            "evidence": (c.evidence or {}).get("items", []) if isinstance(c.evidence, dict) else (c.evidence or []),
            "uncertainty": (c.evidence or {}).get("uncertainty", []) if isinstance(c.evidence, dict) else [],
            "related_tests": (c.evidence or {}).get("related_tests", []) if isinstance(c.evidence, dict) else []}


def investigation_dict(s: Session, inv: Investigation, *, full: bool = True) -> dict:
    br: BugReport | None = inv.bug_report
    d = {"id": inv.id, "repository_id": inv.repository_id, "title": inv.title, "status": inv.status, "created_by": inv.created_by, "created_at": inv.created_at,
         "failing_tests": br.failing_tests if br else []}
    if not full:
        return d
    run = s.execute(select(RetrievalRun).where(RetrievalRun.investigation_id == inv.id).order_by(RetrievalRun.id.desc()).limit(1)).scalar_one_or_none()
    cands = []
    if run:
        cands = [candidate_dict(c) for c in s.execute(select(CandidateLocation).where(CandidateLocation.retrieval_run_id == run.id)
                                                      .order_by(CandidateLocation.rank)).scalars()]
    hyps = [{"id": h.id, "origin": h.origin, "statement": h.statement, "supporting": h.supporting, "contradicting": h.contradicting, "citations": h.citations,
             "rejected_citations": h.rejected_citations, "unverified": h.unverified, "model_name": h.model_name, "created_at": h.created_at}
            for h in s.execute(select(Hypothesis).where(Hypothesis.investigation_id == inv.id).order_by(Hypothesis.id)).scalars()]
    patches = [patch_dict(p) for p in s.execute(select(PatchProposal).where(PatchProposal.investigation_id == inv.id).order_by(PatchProposal.id.desc())).scalars()]
    d.update({
        "bug_report": None if br is None else {"description": br.description, "stack_trace": br.stack_trace, "error_message": br.error_message,
                                               "failing_tests": br.failing_tests, "reproduction_steps": br.reproduction_steps,
                                               "expected_behavior": br.expected_behavior, "observed_behavior": br.observed_behavior, "extracted": br.extracted},
        "retrieval_run": None if run is None else {"id": run.id, "method": run.method, "config": run.config, "index_version": run.index_version, "runtime_ms": run.runtime_ms,
                                                   "context_chars": run.context_chars, "selected": run.selected, "created_at": run.created_at},
        "candidates": cands, "hypotheses": hyps, "patches": patches,
        "evidence_notice": "Candidates are algorithmic retrieval results. Hypotheses marked origin=model are AI-generated and unverified. "
                           "Only test executions attached to a patch are verified outcomes."})
    return d


def patch_dict(p: PatchProposal) -> dict:
    return {"id": p.id, "investigation_id": p.investigation_id, "status": p.status, "changed_files": p.changed_files, "rationale": p.rationale,
            "model_name": p.model_name, "export_approved": p.export_approved, "exported_to": p.exported_to, "created_at": p.created_at, "diff": p.diff}
