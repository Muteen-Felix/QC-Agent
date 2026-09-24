"""Truy cập dữ liệu job. Mọi chuyển trạng thái là MỘT câu UPDATE có điều kiện (`WHERE status IN (...)`): hai executor/API
tranh nhau chỉ một bên thắng, không có cửa sổ đọc-rồi-ghi. Hàm nhận Session; caller quản lý transaction (db.session_scope)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
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


def claim_next(session: Session, *, can_run=None, scan: int = 20) -> Job | None:
    """Nhận job queued ưu tiên cao nhất (priority DESC, cũ trước) và chuyển sang running trong CÙNG transaction.
    Danh sách ứng viên đọc KHÔNG khoá; chỉ hàng thực sự được nhận mới bị khoá (`FOR UPDATE SKIP LOCKED`, từng hàng), nên nhiều
    executor không nhận trùng và không chặn nhau. `can_run(job) -> bool` cho phép bỏ qua job chưa chạy được (vd. khoá môi
    trường đang bận) mà vẫn để nó ở queued. Lưu ý: nếu can_run có tác dụng phụ (giữ khoá) cho một job rồi hàng đó bị bên khác
    nhận trước, caller phải nhả phần tác dụng phụ của mọi ứng viên không được trả về."""
    candidates = session.scalars(
        select(Job).where(Job.status == "queued").order_by(Job.priority.desc(), Job.created_at, Job.id).limit(scan)).all()
    for candidate in candidates:
        if can_run is not None and not can_run(candidate):
            continue
        job = session.scalar(select(Job).where(Job.id == candidate.id, Job.status == "queued")
                             .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if job is None:  # đã có bên khác nhận / đang khoá
            continue
        now = _now()
        session.execute(update(Job).where(Job.id == job.id).values(status="running", started_at=now, heartbeat_at=now,
                                                                     attempts=Job.attempts + 1))
        session.expire_all()
        return get_job(session, job.id)
    return None


def heartbeat(session: Session, job_id: uuid.UUID) -> bool | None:
    """Ghi nhịp sống của job running. Trả cancel_requested; None nếu job không còn running (đã bị requeue/kết thúc)."""
    row = session.execute(update(Job).where(Job.id == job_id, Job.status == "running")
                          .values(heartbeat_at=_now()).returning(Job.cancel_requested)).first()
    return None if row is None else bool(row[0])


def requeue_stale(session: Session, *, stale_after_s: float, max_attempts: int) -> dict[str, list[uuid.UUID]]:
    """Job `running` mà heartbeat quá cũ = executor đã chết. Quyết định theo thứ tự:
      cancel_requested -> cancelled; hết lượt (attempts >= max_attempts) -> failed('executor lost'); còn lại -> queued (chạy lại).
    Đây là đường DUY NHẤT đi từ running về queued (không nằm trong TRANSITIONS thường)."""
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_s)
    stale = (Job.status == "running", Job.heartbeat_at < cutoff)
    now = _now()
    out = {"cancelled": [], "failed": [], "requeued": []}
    for key, condition, values in (
        ("cancelled", Job.cancel_requested.is_(True), {"status": "cancelled", "finished_at": now}),
        ("failed", Job.attempts >= max_attempts,
         {"status": "failed", "finished_at": now, "error": "executor lost: job đang chạy mà không còn heartbeat"}),
        ("requeued", Job.attempts < max_attempts, {"status": "queued", "started_at": None, "heartbeat_at": None}),
    ):
        rows = session.execute(update(Job).where(*stale, condition).values(**values).returning(Job.id)).all()
        out[key] = [r[0] for r in rows]
    session.expire_all()
    return out


def replace_job_results(session: Session, job_id: uuid.UUID, tasks: Iterable[dict], artifacts: Iterable[dict]) -> None:
    """Ghi lại kết quả của job. Xoá bản cũ trước: job bị requeue chạy lại không được đụng unique (job_id, task_id)."""
    session.execute(delete(JobTask).where(JobTask.job_id == job_id))
    session.execute(delete(Artifact).where(Artifact.job_id == job_id))
    add_job_tasks(session, job_id, tasks)
    add_artifacts(session, job_id, artifacts)


def count_open_jobs(session: Session, project_slug: str) -> int:
    """Job đang queued hoặc running của project (giới hạn hàng đợi)."""
    return session.scalar(select(func.count()).select_from(Job).join(Project, Project.id == Job.project_id)
                          .where(Project.slug == project_slug, Job.status.in_(("queued", "running")))) or 0


def create_finished_job(session: Session, project_slug: str, *, external_id: str, mode: str, status: str,
                        gate_verdict: str | None, exit_code: int | None, pr_number: int | None = None,
                        sha: str | None = None, branch: str | None = None) -> tuple[Job, bool]:
    """Ghi một run đã kết thúc do CI đẩy lên (source=ci). Idempotent theo (project, external_id):
    gửi lại cùng external_id trả về job cũ (created=False), kể cả khi hai request đua nhau."""
    if status not in TERMINAL_STATUSES:
        raise ValueError("status phải là trạng thái kết thúc")
    project = get_project(session, project_slug)
    existing = session.scalar(select(Job).where(Job.project_id == project.id, Job.external_id == external_id))
    if existing is not None:
        return existing, False
    now = _now()
    job = Job(id=uuid.uuid4(), project_id=project.id, mode=mode, source="ci", status=status, external_id=external_id,
              gate_verdict=gate_verdict, exit_code=exit_code, pr_number=pr_number, sha=sha, branch=branch,
              started_at=now, finished_at=now, params={"ingested": True})
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Job).where(Job.project_id == project.id, Job.external_id == external_id))
        if existing is None:
            raise
        return existing, False
    job.run_id = str(job.id)
    session.flush()
    session.refresh(job)
    return job, True
