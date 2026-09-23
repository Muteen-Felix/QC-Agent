"""Phụ thuộc dùng chung của route: trạng thái ứng dụng, phiên DB, xác thực."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from qc_agent.auth import service
from qc_agent.core.project import ProjectResolver
from qc_agent.jobs.db import session_scope
from qc_agent.jobs.models import User
from qc_agent.settings import Settings

SESSION_COOKIE = "qc_session"


@dataclass
class AppState:
    engine: Engine
    runs_root: Path
    resolver: ProjectResolver
    projects: dict[str, dict]  # slug -> cấu hình đã validate (nạp lúc khởi động: file hỏng => service không lên)
    cfg: Settings


def get_state(request: Request) -> AppState:
    return request.app.state.qc


StateDep = Annotated[AppState, Depends(get_state)]


def db(state: StateDep) -> Iterator[Session]:
    """Một transaction cho mỗi request: commit khi êm, rollback khi có lỗi (HTTPException cũng rollback).
    Đường cần ghi nhận thất bại (đăng nhập sai) phải dùng session_scope riêng và commit TRƯỚC khi raise."""
    with session_scope(state.engine) as session:
        yield session


DbDep = Annotated[Session, Depends(db)]


def current_user(request: Request, session: DbDep) -> User | None:
    return service.resolve_session(session, request.cookies.get(SESSION_COOKIE))


def require_user(user: Annotated[User | None, Depends(current_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="cần đăng nhập")
    return user


UserDep = Annotated[User, Depends(require_user)]


def project_or_404(state: AppState, slug: str) -> dict:
    project = state.projects.get(slug)
    if project is None:
        raise HTTPException(status_code=404, detail=f"không có project {slug!r}")
    return project
