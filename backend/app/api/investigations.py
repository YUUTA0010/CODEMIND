from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.investigator import attach_line_counts, run_agent
from app.agents.providers import NullProvider, get_provider
from app.core.auth import Principal, developer, viewer
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.runtime_settings import default_retrieval_config
from app.db.session import get_db
from app.localization.engine import context_for, localize
from app.localization.report import build_query
from app.models import BugReport, CandidateLocation, Hypothesis, Investigation, PatchProposal, RetrievalRun
from app.patches import service as patch_svc
from app.repositories.service import (get_repo, investigation_dict, patch_dict, record_run, require_indexed, store_candidates)
from app.retrieval.snapshot import get_snapshot
from app.schemas.api import AnalyzeRequest, InvestigationCreate, PatchCreate, RetrieveRequest, VerifyRequest, ExportRequest

router = APIRouter(prefix="/api/v1", tags=["investigations"])


def _get_inv(db: Session, inv_id: int) -> Investigation:
    inv = db.get(Investigation, inv_id)
    if inv is None:
        raise NotFoundError(f"investigation {inv_id} not found")
    return inv


def _query_for(inv: Investigation):
    b = inv.bug_report
    return build_query(description=b.description, stack_trace=b.stack_trace, error_message=b.error_message, failing_tests=list(b.failing_tests or []),
                       reproduction_steps=b.reproduction_steps, expected_behavior=b.expected_behavior, observed_behavior=b.observed_behavior)


@router.post("/investigations", status_code=201)
def create_investigation(body: InvestigationCreate, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    repo = get_repo(db, body.repository_id)
    if not body.has_content():
        raise AppError("provide at least one of: description, stack_trace, error_message, failing_tests", code="validation_error", status_code=422)
    inv = Investigation(repository_id=repo.id, title=body.title, created_by=p.username)
    inv.bug_report = BugReport(description=body.description, stack_trace=body.stack_trace, error_message=body.error_message, failing_tests=body.failing_tests,
                               reproduction_steps=body.reproduction_steps, expected_behavior=body.expected_behavior, observed_behavior=body.observed_behavior)
    db.add(inv)
    db.flush()
    q = _query_for(inv)
    inv.bug_report.extracted = {"identifiers": q.identifiers, "keywords": q.keywords[:30], "error_terms": q.error_terms,
                                "trace_frames": [f.__dict__ for f in q.frames], "failing_tests": q.failing_tests}
    inv.bug_report.failing_tests = q.failing_tests
    patch_svc.audit(db, "investigation.created", "investigation", inv.id, {"repository_id": repo.id}, p.username)
    return investigation_dict(db, inv)


@router.get("/investigations")
def list_investigations(repository_id: int | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                        db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    q = select(Investigation)
    if repository_id:
        q = q.where(Investigation.repository_id == repository_id)
    q = q.where(Investigation.created_by != "benchmark")
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(Investigation.id.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [investigation_dict(db, i, full=False) for i in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    return investigation_dict(db, _get_inv(db, investigation_id))


@router.post("/investigations/{investigation_id}/retrieve")
def retrieve(investigation_id: int, body: RetrieveRequest | None = None, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    inv = _get_inv(db, investigation_id)
    repo = get_repo(db, inv.repository_id)
    require_indexed(repo)
    snap = get_snapshot(db, repo.id)
    o = (body.options if body else None)
    overrides = {} if o is None else {"method": o.method, "weights": o.weights, "disabled_signals": o.disabled_signals, "top_k": o.top_k,
                                      "budget_chars": o.budget_chars, "graph_depth": o.graph_depth, "include_tests_in_context": o.include_tests_in_context}
    cfg = default_retrieval_config(**overrides)
    q = _query_for(inv)
    cands, result = localize(db, snap, q, cfg, max_candidates=15)
    ctx = context_for(db, snap, result, cfg)
    run = record_run(db, repo.id, inv.id, q.text, cfg, result, ctx, extra={"query_object": q.to_dict(), "omitted": ctx["omitted"][:50]})
    store_candidates(db, run, inv.id, cands)
    inv.status = "RETRIEVED" if inv.status in ("CREATED", "RETRIEVED") else inv.status
    db.flush()
    d = investigation_dict(db, inv)
    d["context"] = {"used_chars": ctx["used_chars"], "budget_chars": ctx["budget_chars"], "deduplicated": ctx["deduplicated"], "omitted_for_budget": ctx["omitted_due_to_budget"],
                    "snippets": ctx["selected"][:30]}
    d["warnings"] = result.warnings
    d["candidate_uncertainty"] = {c.rank: c.uncertainty for c in cands}
    d["related_tests"] = {c.rank: c.related_tests for c in cands}
    return d


@router.post("/investigations/{investigation_id}/analyze")
def analyze(investigation_id: int, body: AnalyzeRequest | None = None, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    body = body or AnalyzeRequest()
    inv = _get_inv(db, investigation_id)
    repo = get_repo(db, inv.repository_id)
    require_indexed(repo)
    snap = get_snapshot(db, repo.id)
    attach_line_counts(db, snap)
    cfg = default_retrieval_config()
    q = _query_for(inv)
    cands, result = localize(db, snap, q, cfg, max_candidates=15)
    ctx = context_for(db, snap, result, cfg)
    run = record_run(db, repo.id, inv.id, q.text, cfg, result, ctx, extra={"query_object": q.to_dict()})
    store_candidates(db, run, inv.id, cands)
    provider = get_provider() if body.use_model else NullProvider()
    b = inv.bug_report
    bug_text = "\n".join(x for x in (b.description, b.error_message, ("Stack trace:\n" + b.stack_trace) if b.stack_trace else "", b.expected_behavior, b.observed_behavior) if x)

    def more(query_text: str) -> dict:
        q2 = build_query(description=query_text)
        _, r2 = localize(db, snap, q2, cfg, max_candidates=8)
        return context_for(db, snap, r2, cfg)

    res = run_agent(db, snap, provider, bug_text=bug_text, cands=cands, context=ctx, failing_tests=list(b.failing_tests or []), retrieve_more=more,
                    max_iterations=body.max_iterations)
    # replace previous analysis hypotheses so the page always shows the latest, internally consistent result
    for h in db.execute(select(Hypothesis).where(Hypothesis.investigation_id == inv.id)).scalars():
        db.delete(h)
    db.flush()
    for h in res.hypotheses:
        db.add(Hypothesis(investigation_id=inv.id, origin=h["origin"], statement=h["statement"], supporting=h["supporting"], contradicting=h["contradicting"],
                          citations=h["citations"], rejected_citations=h["rejected_citations"], unverified=h["unverified"],
                          model_name=res.model if h["origin"] == "model" else None))
    inv.status = "ANALYZED"
    # keep the model's proposed diff (unapplied, unverified) so it can be turned into a reviewable patch explicitly
    inv.bug_report.extracted = {**(inv.bug_report.extracted or {}), "model_proposed_patch": res.patch, "model_proposed_by": res.model if res.patch else None}
    patch_svc.audit(db, "investigation.analyzed", "investigation", inv.id, {"origin": res.origin, "iterations": res.iterations, "tokens": res.input_tokens + res.output_tokens}, p.username)
    db.flush()
    d = investigation_dict(db, inv)
    d["analysis"] = {"origin": res.origin, "provider_available": res.provider_available, "model": res.model, "summary": res.summary, "affected_tests": res.affected_tests,
                     "unverified": res.unverified, "iterations": res.iterations, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "errors": res.errors,
                     "proposed_patch": res.patch}
    return d


@router.post("/investigations/{investigation_id}/patches", status_code=201)
def create_patch(investigation_id: int, body: PatchCreate, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    _get_inv(db, investigation_id)
    try:
        body.validate_source()
    except ValueError as e:
        raise AppError(str(e), code="validation_error", status_code=422)
    if body.from_analysis:
        ex = (_get_inv(db, investigation_id).bug_report.extracted or {})
        proposed = ex.get("model_proposed_patch")
        if not proposed:
            raise ConflictError("the latest analysis contains no model-proposed patch (is a model provider configured? run /analyze first)", code="no_model_patch")
        patch = patch_svc.create_patch(db, investigation_id, proposed["diff"], body.rationale or proposed.get("rationale", ""), model_name=ex.get("model_proposed_by"),
                                       generation_config={"source": "analysis"}, actor=p.username)
        return patch_dict(patch)
    patch = patch_svc.create_patch(db, investigation_id, body.diff or "", body.rationale, actor=p.username)
    return patch_dict(patch)


patch_router = APIRouter(prefix="/api/v1", tags=["patches"])


@patch_router.get("/patches")
def list_patches(limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    q = select(PatchProposal).join(Investigation, Investigation.id == PatchProposal.investigation_id).where(Investigation.created_by != "benchmark")
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(PatchProposal.id.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [{**patch_dict(p_), "diff": None} for p_ in rows], "total": total, "limit": limit, "offset": offset}


@patch_router.get("/patches/{patch_id}")
def get_patch(patch_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    patch = db.get(PatchProposal, patch_id)
    if patch is None:
        raise NotFoundError(f"patch {patch_id} not found")
    d = patch_dict(patch)
    d["report"] = patch_svc.build_report(db, patch_id)
    return d


@patch_router.post("/patches/{patch_id}/verify")
def verify(patch_id: int, body: VerifyRequest | None = None, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    body = body or VerifyRequest()
    return patch_svc.verify_patch(db, patch_id, targeted_tests=body.targeted_tests, run_broad=body.run_broad, actor=p.username)


@patch_router.post("/patches/{patch_id}/export")
def export(patch_id: int, body: ExportRequest, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    return patch_svc.export_patch(db, patch_id, approve=body.approve, apply_to_working_tree=body.apply_to_working_tree, actor=p.username)


__all__ = ["router", "patch_router", "RetrievalRun", "CandidateLocation"]
