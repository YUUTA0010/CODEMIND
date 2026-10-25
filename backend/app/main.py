"""CodeMind FastAPI application factory."""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import experiments, investigations, repos, system
from app.core import runtime_settings
from app.core.config import APP_VERSION, ROOT_DIR, get_settings
from app.core.errors import install_error_handlers
from app.db.session import init_db, init_engine

log = logging.getLogger("codemind")


def create_app(database_url: str | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    init_engine(database_url or settings.database_url)
    init_db()
    runtime_settings.load()
    app = FastAPI(title="CodeMind API", version=APP_VERSION, description="Dependency-aware bug localization and verified repair")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
    install_error_handlers(app)

    @app.middleware("http")
    async def _log(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        if not request.url.path.startswith("/api/v1/ws"):
            log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, resp.status_code, (time.perf_counter() - t0) * 1000)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    for r in (system.router, repos.router, investigations.router, investigations.patch_router, experiments.router):
        app.include_router(r)

    dist = ROOT_DIR / "frontend" / "dist"
    if dist.is_dir():  # optional single-process deployment: serve the built frontend
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                from app.core.errors import NotFoundError

                raise NotFoundError("unknown API route")
            f = (dist / full_path).resolve()
            if full_path and dist.resolve() in f.parents and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


def get_app() -> FastAPI:
    """uvicorn factory entry point: `uvicorn app.main:get_app --factory`."""
    return create_app()
