from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.providers import get_provider
from app.core import jobs
from app.core.auth import Principal, developer, viewer
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.db.session import get_db
from app.experiments.adapters.directory import load_directory_tasks
from app.experiments.adapters.swebench import load_swebench_jsonl
from app.experiments.adapters.synthetic import materialize_suite
from app.experiments.runner import create_experiment, expand_methods, run_experiment
from app.models import ExperimentRun, ExperimentTaskResult, BenchmarkDataset
from app.patches.service import audit
from app.schemas.api import ExperimentCreate

router = APIRouter(prefix="/api/v1", tags=["experiments"])
_DOWNLOADABLE = {"results.csv": "text/csv", "summary.csv": "text/csv", "paired_comparisons.csv": "text/csv", "summary.json": "application/json",
                 "config.json": "application/json", "results.json": "application/json", "localization.png": "image/png", "comparison.md": "text/markdown"}


def _load_tasks(spec):
    s = get_settings()
    if spec.kind == "synthetic":
        root = s.data_dir / "benchmarks" / "synthetic"
        materialize_suite(root)
        return load_directory_tasks(root), True, "synthetic-inventory-v1", str(root)
    if spec.kind == "directory":
        if not spec.path or not Path(spec.path).is_dir():
            raise AppError("dataset.path must be an existing directory of tasks (<task>/repo + task.json)", code="validation_error", status_code=422)
        tasks = load_directory_tasks(Path(spec.path))
        return tasks, False, f"directory:{Path(spec.path).name}", spec.path
    if not spec.path or not spec.repos_dir:
        raise AppError("swebench datasets need dataset.path (local JSONL) and dataset.repos_dir (local clones)", code="validation_error", status_code=422)
    try:
        tasks = load_swebench_jsonl(Path(spec.path), Path(spec.repos_dir), s.data_dir / "benchmarks" / "swebench", limit=spec.limit)
    except (FileNotFoundError, ValueError, KeyError) as e:
        raise AppError(f"could not load SWE-bench data: {e}", code="dataset_error", status_code=422)
    return tasks, False, f"swebench:{Path(spec.path).stem}", spec.path


def _run_job(exp_id: int, tasks, provider):
    try:
        run_experiment(exp_id, tasks, provider)
    except Exception:  # noqa: BLE001  (already recorded on the run)
        pass


@router.post("/experiments", status_code=202)
def start_experiment(body: ExperimentCreate, db: Session = Depends(get_db), p: Principal = Depends(developer)):
    tasks, synthetic, ds_name, source = _load_tasks(body.dataset)
    if body.dataset.limit:
        tasks = tasks[: body.dataset.limit]
    if not tasks:
        raise AppError("dataset contains no tasks", code="dataset_error", status_code=422)
    cfg = body.model_dump(exclude={"dataset"}, exclude_none=True)
    cfg["dataset"] = body.dataset.model_dump(exclude_none=True)
    cfg["dataset_synthetic"] = synthetic
    try:
        expand_methods(cfg)  # validate labels / ablation target early
    except ValueError as e:
        raise AppError(str(e), code="validation_error", status_code=422)
    db.commit()
    eid = create_experiment(body.name, cfg, ds_name, body.dataset.kind, tasks, is_synthetic=synthetic, source=source,
                            revision=",".join(sorted({t.repo_revision for t in tasks}))[:100])
    audit(db, "experiment.started", "experiment", eid, {"dataset": ds_name, "tasks": len(tasks), "seed": body.seed}, p.username)
    db.commit()
    jobs.submit(_run_job, eid, tasks, get_provider())
    return _exp_dict(db.get(ExperimentRun, eid), db)


def _exp_dict(e: ExperimentRun, db: Session) -> dict:
    ds = db.get(BenchmarkDataset, e.dataset_id) if e.dataset_id else None
    return {"id": e.id, "name": e.name, "status": e.status, "seed": e.seed, "progress": e.progress, "total": e.total, "error": e.error, "created_at": e.created_at,
            "finished_at": e.finished_at, "dataset": None if ds is None else {"name": ds.name, "kind": ds.kind, "is_synthetic": ds.is_synthetic, "revision": ds.revision},
            "synthetic_notice": "SYNTHETIC fixtures - pipeline test only, not evidence about real-world performance." if ds and ds.is_synthetic else None}


@router.get("/experiments")
def list_experiments(limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    total = db.scalar(select(func.count()).select_from(ExperimentRun)) or 0
    rows = db.execute(select(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [_exp_dict(e, db) for e in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: int, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    e = db.get(ExperimentRun, experiment_id)
    if e is None:
        raise NotFoundError(f"experiment {experiment_id} not found")
    d = _exp_dict(e, db)
    d["config"] = e.config
    return d


@router.get("/experiments/{experiment_id}/results")
def experiment_results(experiment_id: int, include_rows: bool = False, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    e = db.get(ExperimentRun, experiment_id)
    if e is None:
        raise NotFoundError(f"experiment {experiment_id} not found")
    out = {"experiment": _exp_dict(e, db), "status": e.status, "summary": e.summary, "config": e.config,
           "downloads": [f"/api/v1/experiments/{experiment_id}/files/{n}" for n in _DOWNLOADABLE] if e.status == "COMPLETED" else []}
    if include_rows:
        rows = db.execute(select(ExperimentTaskResult).where(ExperimentTaskResult.experiment_id == experiment_id).order_by(ExperimentTaskResult.id)).scalars().all()
        out["rows"] = [{"task_id": r.task_id, "method": r.method, "run": r.run_index, "metrics": r.metrics, "ranked_files": r.ranked_files[:10], "repair": r.repair} for r in rows]
    return out


@router.get("/experiments/{experiment_id}/files/{name}")
def download(experiment_id: int, name: str, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    if name not in _DOWNLOADABLE:
        raise NotFoundError("unknown export file")
    path = get_settings().exports_dir / f"experiment-{experiment_id}" / name
    if not path.is_file():
        raise NotFoundError("export not available (experiment not finished?)")
    return FileResponse(path, media_type=_DOWNLOADABLE[name], filename=f"experiment-{experiment_id}-{name}")
