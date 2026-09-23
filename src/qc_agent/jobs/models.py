"""Mô hình dữ liệu vận hành (PostgreSQL): projects, jobs, job_tasks, artifacts, users, sessions, api_tokens.

Trạng thái job: queued -> running -> {succeeded | failed | timed_out | cancelled}; queued -> cancelled.
  succeeded = chạy xong, gate PASS hoặc YELLOW
  failed    = chạy xong nhưng gate FAIL, hoặc lỗi hệ thống của executor/core (exit 3)
  timed_out = vượt timeout cấp job (executor giết cây tiến trình)
  cancelled = người dùng huỷ (khi còn queued: ngay; khi running: executor dừng và chuyển)
Đây là trạng thái của JOB, không phải của result.json (contract của worker không đổi).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, MetaData,
                        String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "timed_out")
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
JOB_SOURCES = ("web", "ci")
# trạng thái đích hợp lệ theo trạng thái hiện tại (nguồn sự thật của máy trạng thái)
TRANSITIONS = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "timed_out"}),
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


def _ts(**kw):
    return mapped_column(DateTime(timezone=True), **kw)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    repo: Mapped[str | None] = mapped_column(String(200))
    config_sha256: Mapped[str | None] = mapped_column(String(64))  # hash configs/projects/<slug>.yaml lúc đồng bộ
    synced_at: Mapped[datetime] = _ts(server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # luôn lưu chữ thường
    password_hash: Mapped[str | None] = mapped_column(String(255))  # null khi đăng nhập qua IdP (Entra sau này)
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    failed_attempts: Mapped[int] = mapped_column(Integer, server_default="0")  # đăng nhập sai liên tiếp
    locked_until: Mapped[datetime | None] = _ts(nullable=True)
    last_login_at: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 của token; token thô chỉ nằm ở cookie
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    expires_at: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)


class ApiToken(Base):
    """Token theo project cho CI đẩy report lên (chỉ ghi được vào project của nó)."""
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256; token thô chỉ hiện một lần lúc tạo
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    last_used_at: Mapped[datetime | None] = _ts(nullable=True)
    revoked_at: Mapped[datetime | None] = _ts(nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    mode: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, server_default="0")  # cao hơn chạy trước (PR > manual)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")  # số lần executor nhận job; quá max_attempts thì failed
    suites: Mapped[list | None] = mapped_column(JSONB)
    task_ids: Mapped[list | None] = mapped_column(JSONB)
    params: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    pr_number: Mapped[int | None] = mapped_column(Integer)
    sha: Mapped[str | None] = mapped_column(String(64))
    branch: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    started_at: Mapped[datetime | None] = _ts(nullable=True)
    finished_at: Mapped[datetime | None] = _ts(nullable=True)
    heartbeat_at: Mapped[datetime | None] = _ts(nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default="false")
    gate_verdict: Mapped[str | None] = mapped_column(String(8))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list["JobTask"]] = relationship(back_populates="job", cascade="all, delete-orphan", order_by="JobTask.id")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="job", cascade="all, delete-orphan", order_by="Artifact.id")

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled','timed_out')", name="status_valid"),
        CheckConstraint("source IN ('web','ci')", name="source_valid"),
        CheckConstraint("gate_verdict IS NULL OR gate_verdict IN ('PASS','YELLOW','FAIL')", name="gate_verdict_valid"),
        Index("ix_jobs_project_created", "project_id", "created_at"),
        Index("ix_jobs_status_priority_created", "status", "priority", "created_at"),
    )


class JobTask(Base):
    __tablename__ = "job_tasks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(String(128))
    worker: Mapped[str | None] = mapped_column(String(128))
    capability: Mapped[str | None] = mapped_column(String(128))
    lane: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # pass | fail | error | skipped (result.json)
    gating: Mapped[bool] = mapped_column(Boolean, server_default="false")
    duration_s: Mapped[float | None] = mapped_column(Float)
    tokens: Mapped[int | None] = mapped_column(Integer)
    usd: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[dict] = mapped_column(JSONB, server_default="{}")  # metrics + tiêu đề finding; chi tiết nằm ở artifact
    job: Mapped[Job] = relationship(back_populates="tasks")
    __table_args__ = (UniqueConstraint("job_id", "task_id", name="uq_job_tasks_job_task"),)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(512))  # tương đối trong thư mục run của job
    kind: Mapped[str | None] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_uri: Mapped[str] = mapped_column(String(1024))  # file:// hoặc s3:// (ArtifactStore, bước sau)
    job: Mapped[Job] = relationship(back_populates="artifacts")
    __table_args__ = (UniqueConstraint("job_id", "path", name="uq_artifacts_job_path"),)
