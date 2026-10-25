from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import jobs, runtime_settings
from app.core.auth import Principal, admin, current_principal, developer, hash_token, new_token, viewer
from app.core.config import APP_VERSION, get_settings
from app.core.errors import ConflictError
from app.core.events import bus
from app.db.session import get_db, session_scope
from app.indexing.indexer import create_job, run_indexing_job
from app.models import (AuditEvent, ExperimentRun, IndexingJob, Investigation, PatchProposal, Repository, TestExecution, User)
from app.parsing.js_parser import TREE_SITTER_AVAILABLE
from app.patches.service import audit
from app.repositories.service import repo_dict
from app.retrieval.embeddings import get_embedder
from app.sandbox.runner import docker_available, resolve_mode
from app.schemas.api import SettingsUpdate, UserCreate

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "database": db.get_bind().dialect.name}


@router.get("/version")
def version():
    return {"name": "CodeMind", "version": APP_VERSION, "api": "v1"}


@router.get("/settings")
def get_settings_view(_: Principal = Depends(viewer)):
    return runtime_settings.effective()


@router.get("/settings/status")
def settings_status(_: Principal = Depends(viewer)):
    s = get_settings()
    mode, why = resolve_mode(s)
    emb = get_embedder(s)
    return {"sandbox": {"resolved_mode": mode, "explanation": why, "docker_available": docker_available(),
                        "local_execution_allowed": s.allow_local_sandbox,
                        "isolation_note": "docker: container isolation; local: restricted subprocess with NO filesystem/network isolation" },
            "embedding": {"name": emb.name, "is_neural": emb.is_neural, "note": None if emb.is_neural else "offline hashing embedder: sub-word overlap, not neural semantics"},
            "llm": {"provider": s.llm_provider, "configured": s.llm_provider != "none" and bool(s.llm_api_key)},
            "parsers": {"python": "ast", "javascript_typescript": "tree-sitter" if TREE_SITTER_AVAILABLE else "unavailable (text only)"},
            "git_available": bool(shutil.which("git"))}


@router.put("/settings")
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db), p: Principal = Depends(admin)):
    patch = body.model_dump(exclude_none=True)
    if "retrieval_weights" in patch:
        patch["retrieval_weights"] = {**(runtime_settings.effective()["retrieval_weights"]), **patch["retrieval_weights"]}
    runtime_settings.update(patch)
    audit(db, "settings.updated", "settings", "", {k: v for k, v in patch.items()}, p.username)
    return runtime_settings.effective()


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    repos = db.execute(select(Repository).where(Repository.kind != "benchmark").order_by(Repository.id.desc()).limit(10)).scalars().all()
    invs = db.execute(select(Investigation).where(Investigation.created_by != "benchmark").order_by(Investigation.id.desc()).limit(8)).scalars().all()
    execs = db.execute(select(PatchProposal).join(Investigation, Investigation.id == PatchProposal.investigation_id).where(Investigation.created_by != "benchmark")
                       .order_by(PatchProposal.id.desc()).limit(8)).scalars().all()
    exps = db.execute(select(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(5)).scalars().all()
    jobs_ = db.execute(select(IndexingJob).where(IndexingJob.status.in_(["QUEUED", "RUNNING"]))).scalars().all()
    return {
        "repositories": [repo_dict(db, r) for r in repos],
        "recent_investigations": [{"id": i.id, "title": i.title, "status": i.status, "repository_id": i.repository_id, "created_at": i.created_at} for i in invs],
        "active_indexing_jobs": [{"id": j.id, "repository_id": j.repository_id, "progress": j.progress, "total": j.total, "message": j.message} for j in jobs_],
        "recent_verifications": [{"patch_id": p.id, "investigation_id": p.investigation_id, "status": p.status, "files": p.changed_files, "created_at": p.created_at} for p in execs],
        "experiments": [{"id": e.id, "name": e.name, "status": e.status, "progress": e.progress, "total": e.total, "created_at": e.created_at} for e in exps],
        "counts": {"repositories": len(repos), "investigations": db.scalar(select(func.count()).select_from(Investigation).where(Investigation.created_by != "benchmark")) or 0,
                   "test_executions": db.scalar(select(func.count()).select_from(TestExecution)) or 0},
    }


@router.get("/audit")
def audit_log(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), action: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(admin)):
    q = select(AuditEvent)
    if action:
        q = q.where(AuditEvent.action == action)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [{"id": a.id, "actor": a.actor, "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "details": a.details, "created_at": a.created_at} for a in rows],
            "total": total, "limit": limit, "offset": offset}


@router.post("/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), p: Principal = Depends(admin)):
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise ConflictError("username already exists")
    token = new_token()
    db.add(User(username=body.username, role=body.role, token_hash=hash_token(token)))
    audit(db, "user.created", "user", body.username, {"role": body.role}, p.username)
    return {"username": body.username, "role": body.role, "token": token, "note": "store this token now; only its hash is kept"}


@router.post("/demo/load", status_code=201)
def load_demo(db: Session = Depends(get_db), p: Principal = Depends(developer)):
    """DEMO MODE: registers a clearly labelled synthetic repository (kind='demo') with a known injected bug."""
    from app.experiments.adapters.synthetic import materialize_suite

    root = get_settings().data_dir / "demo"
    recs = materialize_suite(root)
    rec = recs[0]
    existing = db.execute(select(Repository).where(Repository.root_path == str(Path(rec["repo_path"]).resolve()))).scalar_one_or_none()
    if existing:
        return {"repository": repo_dict(db, existing), "demo": True, "task": {k: rec[k] for k in ("task_key", "description", "stack_trace", "relevant_tests")}}
    repo = Repository(name="[DEMO] synthetic inventory (injected bug)", root_path=str(Path(rec["repo_path"]).resolve()), kind="demo")
    db.add(repo)
    db.flush()
    job = create_job(db, repo, "full")
    db.commit()
    jobs.submit(run_indexing_job, job.id)
    return {"repository": repo_dict(db, db.get(Repository, repo.id)), "demo": True, "notice": "SYNTHETIC DEMO DATA - isolated from your repositories' results",
            "task": {k: rec[k] for k in ("task_key", "description", "stack_trace", "relevant_tests")}}


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    s = get_settings()
    if s.auth_required:
        token = ws.query_params.get("token", "")
        ok = bool(s.admin_token) and token == s.admin_token
        if not ok:
            with session_scope() as db:
                ok = db.execute(select(User).where(User.token_hash == hash_token(token))).scalar_one_or_none() is not None if token else False
        if not ok:
            await ws.close(code=4401)
            return
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_json({"type": "hello", "data": {"recent": bus.history[-20:]}})
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=25)
                await ws.send_json(ev)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping", "data": {}})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


__all__ = ["router", "current_principal"]
