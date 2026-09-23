"""FastAPI app: API v1 là lớp chính; web chỉ là một client của nó.

    uvicorn qc_agent.api.app:create_app --factory --host 127.0.0.1 --port 8080     (QC_DATABASE_URL bắt buộc)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from qc_agent import settings
from qc_agent.api.deps import SESSION_COOKIE, AppState
from qc_agent.api.routes import auth, catalog, health, ingest, jobs
from qc_agent.core import project as project_lib
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import make_engine, session_scope

_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(engine: Engine | None = None, *, runs_root: Path | None = None, projects_dir: Path | None = None) -> FastAPI:
    cfg = settings.get()
    projects_dir = Path(projects_dir or cfg.resolved_projects_dir)
    state = AppState(engine=engine or make_engine(), runs_root=Path(runs_root or cfg.runs_dir).resolve(),
                     resolver=project_lib.ProjectResolver(projects_dir), projects=project_lib.list_projects(projects_dir), cfg=cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with session_scope(state.engine) as session:  # đồng bộ danh sách project vào DB (job tham chiếu project_id)
            for slug, project in state.projects.items():
                repo.sync_project(session, slug, name=project.get("name"), repo=project.get("repo"),
                                  config_sha256=project_lib.file_sha256(projects_dir / f"{slug}.yaml"))
        yield

    app = FastAPI(title="qc-agent", version="0.1.0", lifespan=lifespan)
    app.state.qc = state

    @app.middleware("http")
    async def security(request: Request, call_next):
        # CSRF: yêu cầu ghi mang cookie phiên từ origin khác host bị chặn (cookie SameSite=Lax là lớp thứ nhất)
        origin = request.headers.get("origin")
        if request.method in _UNSAFE and origin and request.cookies.get(SESSION_COOKIE):
            if urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "origin không hợp lệ"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    for module in (health, auth, catalog, jobs, ingest):
        app.include_router(module.router)
    return app
