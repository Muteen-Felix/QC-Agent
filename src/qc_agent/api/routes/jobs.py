"""Job chạy thủ công: tạo (với danh sách suite/task tuỳ chọn), theo dõi, huỷ, lịch sử, report, artifact, log."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import select

from qc_agent.api.deps import DbDep, StateDep, UserDep, project_or_404
from qc_agent.api.routes.catalog import load_project_suites
from qc_agent.api.schemas import JobIn
from qc_agent.api.serialize import job_json
from qc_agent.core import engine as core_engine
from qc_agent.core import project as project_lib
from qc_agent.core.plan import PlanError
from qc_agent.jobs import repository as repo
from qc_agent.jobs.models import Artifact, Job, Project, User

router = APIRouter(prefix="/api/v1", tags=["jobs"])
_INLINE_TYPES = {".json": "application/json", ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                 ".log": "text/plain; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".webp": "image/webp"}


def _serialize(session, state, jobs: list[Job], *, detail: bool = False) -> list[dict]:
    slugs = {p.id: p.slug for p in session.scalars(select(Project).where(Project.id.in_({j.project_id for j in jobs})))} if jobs else {}
    ids = {j.created_by for j in jobs if j.created_by}
    creators = {u.id: u.email for u in session.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return [job_json(j, slugs[j.project_id], creators, runs_root=state.runs_root, detail=detail) for j in jobs]


def _job_or_404(session, job_id: str) -> Job:
    try:
        return repo.get_job(session, uuid.UUID(job_id))
    except (ValueError, repo.JobNotFound):
        raise HTTPException(status_code=404, detail="không có job này") from None


def _slug_of(session, job: Job) -> str:
    return session.get(Project, job.project_id).slug


@router.post("/projects/{slug}/jobs", status_code=202)
def create_job(slug: str, body: JobIn, state: StateDep, session: DbDep, user: UserDep):
    project = project_or_404(state, slug)
    suites = load_project_suites(state, slug)
    try:  # validate sớm bằng đúng logic của core: mode/suite/lane/trùng id/phụ thuộc
        plan, _meta = project_lib.build_plan(project, body.mode, suites, body.suites)
        if body.task_ids:
            core_engine.select_tasks(plan, ",".join(body.task_ids))
        environment = state.resolver.environment(slug, body.environment)
    except PlanError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    cfg = state.cfg
    if repo.count_open_jobs(session, slug) >= cfg.max_open_jobs_per_project:
        raise HTTPException(status_code=429, detail=f"project đã có {cfg.max_open_jobs_per_project} job đang chờ/chạy")
    params: dict = {}
    if body.environment:
        params["environment"] = body.environment
    if environment and environment.get("concurrency_key"):
        params["concurrency_key"] = environment["concurrency_key"]  # từ cấu hình server, không từ người dùng
    timeout = body.timeout_s or (environment or {}).get("timeout_s")
    if timeout:
        params["timeout_s"] = min(float(timeout), cfg.max_job_timeout_s)
    job = repo.create_job(session, slug, mode=body.mode, source="web", suites=body.suites, task_ids=body.task_ids,
                          params=params, created_by=user.id)
    return _serialize(session, state, [job])[0]


@router.get("/jobs")
def list_jobs(state: StateDep, session: DbDep, user: UserDep, project: str | None = None, status: str | None = None,
              mode: str | None = None, source: str | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0):
    if project:
        project_or_404(state, project)
    jobs = repo.list_jobs(session, project_slug=project, status=status, mode=mode, source=source, limit=limit, offset=offset)
    return {"items": _serialize(session, state, jobs), "limit": limit, "offset": offset}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, state: StateDep, session: DbDep, user: UserDep):
    return _serialize(session, state, [_job_or_404(session, job_id)], detail=True)[0]


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str, state: StateDep, session: DbDep, user: UserDep):
    job = _job_or_404(session, job_id)
    try:
        job = repo.request_cancel(session, job.id)
    except repo.InvalidTransition:
        raise HTTPException(status_code=409, detail="job đã kết thúc, không huỷ được") from None
    return _serialize(session, state, [job])[0]


# ---- report / artifact / log ----

def _artifact_path(state, session, job: Job, rel: str) -> Path:
    """Chỉ phục vụ file CÓ trong bảng artifacts của job và nằm trong thư mục của project (chống path traversal)."""
    known = session.scalar(select(Artifact.id).where(Artifact.job_id == job.id, Artifact.path == rel))
    if known is None:
        raise HTTPException(status_code=404, detail="không có artifact này")
    slug = _slug_of(session, job)
    base = (state.runs_root / slug).resolve()
    path = (base / f"{job.id}.log") if rel == "executor.log" else (base / str(job.id) / rel)
    path = path.resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="file không còn trên máy chủ")
    return path


def _file_response(path: Path, download_name: str):
    inline = _INLINE_TYPES.get(path.suffix.lower())
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    if inline:  # loại an toàn (không chạy được script): xem trực tiếp
        return FileResponse(path, media_type=inline, headers=headers)
    # HTML/SVG/... từ SUT là nội dung KHÔNG TIN CẬY và cùng origin với cookie phiên: luôn tải về, không render
    return FileResponse(path, media_type="application/octet-stream", filename=download_name, headers=headers)


@router.get("/jobs/{job_id}/report.json")
def report_json(job_id: str, state: StateDep, session: DbDep, user: UserDep):
    job = _job_or_404(session, job_id)
    path = _artifact_path(state, session, job, "report.json")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/report.md")
def report_md(job_id: str, state: StateDep, session: DbDep, user: UserDep):
    job = _job_or_404(session, job_id)
    return PlainTextResponse(_artifact_path(state, session, job, "report.md").read_text(encoding="utf-8"),
                             media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str, state: StateDep, session: DbDep, user: UserDep):
    job = _job_or_404(session, job_id)
    return [{"path": a.path, "size_bytes": a.size_bytes, "sha256": a.sha256} for a in job.artifacts]


@router.get("/jobs/{job_id}/artifacts/{rel:path}")
def get_artifact(job_id: str, rel: str, state: StateDep, session: DbDep, user: UserDep):
    job = _job_or_404(session, job_id)
    path = _artifact_path(state, session, job, rel)
    return _file_response(path, Path(rel).name)


@router.get("/jobs/{job_id}/log")
def job_log(job_id: str, state: StateDep, session: DbDep, user: UserDep,
            tail: Annotated[int, Query(ge=1, le=262144)] = 65536):
    """Log của executor (job đang chạy cũng xem được): `tail` byte cuối."""
    job = _job_or_404(session, job_id)
    path = state.runs_root / _slug_of(session, job) / f"{job.id}.log"
    if not path.is_file():
        return PlainTextResponse("", media_type="text/plain; charset=utf-8")
    with path.open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(fh.tell() - tail, 0))
        text = fh.read().decode("utf-8", errors="replace")
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})
