"""Truy cập dữ liệu job. Mọi chuyển trạng thái là MỘT câu UPDATE có điều kiện (`WHERE status IN (...)`): hai executor/API
tranh nhau chỉ một bên thắng, không có cửa sổ đọc-rồi-ghi. Hàm nhận Session; caller quản lý transaction (db.session_scope)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from qc_agent.jobs.models import JOB_SOURCES, TERMINAL_STATUSES, TRANSITIONS, Artifact, Job, JobTask, Project

_UPDATABLE = {"gate_verdict", "exit_code", "run_id", "error", "heartbeat_at"}


class JobNotFound(LookupError):
    pass


class ProjectNotFound(LookupError):
    pass


class InvalidTransition(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sync_project(session: Session, slug: str, *, name: str | None = None, repo: str | None = None,
                 config_sha256: str | None = None) -> Project:
    """Upsert theo slug (đồng bộ từ configs/projects/*.yaml)."""
    stmt = pg_insert(Project).values(slug=slug, name=name, repo=repo, config_sha256=config_sha256)
    stmt = stmt.on_conflict_do_update(index_elements=[Project.slug], set_={
        "name": stmt.excluded.name, "repo": stmt.excluded.repo, "config_sha256": stmt.excluded.config_sha256,
        "synced_at": func.now()}).returning(Project.id)
    project_id = session.execute(stmt).scalar_one()
    return session.get(Project, project_id, populate_existing=True)


def get_project(session: Session, slug: str) -> Project:
    project = session.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise ProjectNotFound(slug)
    return project


def create_job(session: Session, project_slug: str, *, mode: str, source: str, suites: list[str] | None = None,
               task_ids: list[str] | None = None, params: dict | None = None, pr_number: int | None = None,
               sha: str | None = None, branch: str | None = None, created_by: int | None = None,
               priority: int = 0, job_id: uuid.UUID | None = None) -> Job:
    if source not in JOB_SOURCES:
        raise ValueError(f"source phải là {'|'.join(JOB_SOURCES)}")
    project = get_project(session, project_slug)
    job = Job(id=job_id or uuid.uuid4(), project_id=project.id, mode=mode, source=source, status="queued",
              suites=suites, task_ids=task_ids, params=params or {}, pr_number=pr_number, sha=sha, branch=branch,
              created_by=created_by, priority=priority)
    session.add(job)
    session.flush()
    session.refresh(job)  # nạp created_at do server đặt
    return job


def get_job(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise JobNotFound(str(job_id))
    return job


def list_jobs(session: Session, *, project_slug: str | None = None, status: str | None = None, mode: str | None = None,
              source: str | None = None, limit: int = 50, offset: int = 0) -> list[Job]:
    """Mới nhất trước (lịch sử)."""
    stmt = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0))
    if project_slug:
        stmt = stmt.join(Project, Project.id == Job.project_id).where(Project.slug == project_slug)
    for column, value in ((Job.status, status), (Job.mode, mode), (Job.source, source)):
        if value:
            stmt = stmt.where(column == value)
    return list(session.scalars(stmt))


def transition(session: Session, job_id: uuid.UUID, to: str, **fields: Any) -> Job:
    """Chuyển trạng thái nguyên tử. Raise InvalidTransition nếu trạng thái hiện tại không cho phép đi tới `to`
    (gồm cả job đã ở trạng thái kết thúc), JobNotFound nếu không có job."""
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"trường không được phép cập nhật khi chuyển trạng thái: {sorted(unknown)}")
    sources = [s for s, targets in TRANSITIONS.items() if to in targets]
    if not sources:
        raise InvalidTransition(f"không có đường đi tới trạng thái {to!r}")
    values: dict[str, Any] = {"status": to, **fields}
    now = _now()
    if to == "running":
        values.update(started_at=now, heartbeat_at=now)
    if to in TERMINAL_STATUSES:
        values["finished_at"] = now
    result = session.execute(update(Job).where(Job.id == job_id, Job.status.in_(sources)).values(**values).returning(Job.id))
    if result.first() is None:
        current = session.scalar(select(Job.status).where(Job.id == job_id))
        if current is None:
            raise JobNotFound(str(job_id))
        raise InvalidTransition(f"job {job_id}: {current} -> {to} không hợp lệ")
    session.expire_all()
    return get_job(session, job_id)


def request_cancel(session: Session, job_id: uuid.UUID) -> Job:
    """queued -> cancelled ngay; running -> đặt cancel_requested (executor dừng job rồi chuyển cancelled);
    đã kết thúc -> InvalidTransition."""
    rows = session.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                           .values(status="cancelled", finished_at=_now()).returning(Job.id))
    if rows.first() is None:
        rows = session.execute(update(Job).where(Job.id == job_id, Job.status == "running")
                               .values(cancel_requested=True).returning(Job.id))
        if rows.first() is None:
            get_job(session, job_id)  # JobNotFound nếu không tồn tại
            raise InvalidTransition(f"job {job_id} đã kết thúc, không huỷ được")
    session.expire_all()
    return get_job(session, job_id)


def add_job_tasks(session: Session, job_id: uuid.UUID, rows: Iterable[dict]) -> None:
    session.add_all(JobTask(job_id=job_id, **row) for row in rows)
    session.flush()


def add_artifacts(session: Session, job_id: uuid.UUID, rows: Iterable[dict]) -> None:
    session.add_all(Artifact(job_id=job_id, **row) for row in rows)
    session.flush()
