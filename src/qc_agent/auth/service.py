"""Xác thực: người dùng (email + mật khẩu), phiên đăng nhập, token API theo project.

Nguyên tắc:
  - Token phiên / token API chỉ được lưu dạng sha256; token thô chỉ tồn tại ở cookie/lúc tạo (hiện một lần).
  - authenticate() trả None cho MỌI lý do thất bại (sai mật khẩu, không có tài khoản, bị khoá, vô hiệu hoá): không lộ email tồn tại.
  - Domain email chỉ lấy từ QC_ALLOWED_EMAIL_DOMAINS; để trống => không ai được thêm (fail-closed).
  - Mọi người dùng có quyền như nhau ở giai đoạn này; AuthProvider chừa chỗ cho Microsoft Entra ID (OIDC) sau này.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from qc_agent import settings
from qc_agent.auth import passwords
from qc_agent.jobs.models import ApiToken, Project
from qc_agent.jobs.models import Session as LoginSession
from qc_agent.jobs.models import User

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
API_TOKEN_PREFIX = "qca_"


class AuthError(ValueError):
    pass


class InvalidEmail(AuthError):
    pass


class DomainNotAllowed(AuthError):
    pass


class UserExists(AuthError):
    pass


class UserNotFound(AuthError):
    pass


class AuthProvider(Protocol):
    """Điểm mở rộng: PasswordProvider (hiện tại), EntraProvider (OIDC, sau này) cùng trả User hoặc None."""
    name: str

    def authenticate(self, session: Session, **credentials) -> User | None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---- email / domain ----

def allowed_domains() -> set[str]:
    return {d.strip().lower() for d in settings.get().allowed_email_domains.split(",") if d.strip()}


def normalize_email(email: str) -> str:
    """Chữ thường, cắt khoảng trắng, chỉ nhận ASCII (tránh domain đồng dạng Unicode), đúng dạng a@b.c."""
    if not isinstance(email, str):
        raise InvalidEmail("email phải là chuỗi")
    value = email.strip().lower()
    if len(value) > 320 or not value.isascii() or not _EMAIL.match(value) or value.count("@") != 1:
        raise InvalidEmail("email không hợp lệ")
    return value


def check_domain(email: str) -> None:
    domains = allowed_domains()
    if not domains:
        raise DomainNotAllowed("chưa cấu hình QC_ALLOWED_EMAIL_DOMAINS: không tạo được tài khoản nào")
    if email.rsplit("@", 1)[1] not in domains:
        raise DomainNotAllowed("email không thuộc domain được phép")


# ---- người dùng ----

def create_user(session: Session, email: str, password: str | None = None, display_name: str | None = None) -> tuple[User, str]:
    """Trả (user, mật khẩu). Không truyền password => sinh mật khẩu ngẫu nhiên (hiện một lần cho admin)."""
    email = normalize_email(email)
    check_domain(email)
    password = password or secrets.token_urlsafe(15)
    user = User(email=email, password_hash=passwords.hash_password(password), display_name=display_name)
    try:
        with session.begin_nested():  # savepoint: trùng email chỉ huỷ phần insert, không rollback transaction của caller
            session.add(user)
            session.flush()
    except IntegrityError:
        raise UserExists(f"đã có tài khoản {email}") from None
    return user, password


def get_user(session: Session, email: str) -> User:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise UserNotFound(email)
    return user


def reset_password(session: Session, email: str, password: str | None = None) -> tuple[User, str]:
    """Đặt lại mật khẩu (admin, hoặc người dùng tự đổi). Mở khoá tài khoản và thu hồi MỌI phiên đang có."""
    user = get_user(session, email)
    password = password or secrets.token_urlsafe(15)
    user.password_hash = passwords.hash_password(password)
    user.failed_attempts, user.locked_until = 0, None
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session.flush()
    return user, password


def set_active(session: Session, email: str, active: bool) -> User:
    user = get_user(session, email)
    user.is_active = active
    if not active:
        session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session.flush()
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    """Email + mật khẩu. None cho mọi thất bại. Sai liên tiếp `login_max_failures` lần => khoá `login_lockout_minutes`
    (trong lúc khoá, ngay cả mật khẩu đúng cũng bị từ chối và không đếm thêm)."""
    cfg = settings.get()
    try:
        email = normalize_email(email)
    except InvalidEmail:
        passwords.burn_time(password)
        return None
    user = session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not user.password_hash:
        passwords.burn_time(password)
        return None
    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        passwords.burn_time(password)
        return None
    if passwords.verify(user.password_hash, password):
        user.failed_attempts, user.locked_until, user.last_login_at = 0, None, now
        session.flush()
        return user
    failed = session.execute(update(User).where(User.id == user.id).values(failed_attempts=User.failed_attempts + 1)
                             .returning(User.failed_attempts)).scalar_one()
    if failed >= cfg.login_max_failures:
        session.execute(update(User).where(User.id == user.id)
                        .values(failed_attempts=0, locked_until=now + timedelta(minutes=cfg.login_lockout_minutes)))
    session.expire(user)
    return None


class PasswordProvider:
    name = "password"

    def authenticate(self, session: Session, *, email: str, password: str) -> User | None:
        return authenticate(session, email, password)


# ---- phiên đăng nhập ----

def create_session(session: Session, user: User, ttl: timedelta | None = None) -> str:
    """Trả token thô cho cookie; DB chỉ giữ sha256."""
    token = secrets.token_urlsafe(32)
    ttl = ttl or timedelta(hours=settings.get().session_ttl_hours)
    session.add(LoginSession(id=_digest(token), user_id=user.id, expires_at=_now() + ttl))
    session.flush()
    return token


def resolve_session(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = session.get(LoginSession, _digest(token))
    if row is None or row.expires_at <= _now():
        return None
    user = session.get(User, row.user_id)
    return user if user is not None and user.is_active else None


def revoke_session(session: Session, token: str | None) -> None:
    if token:
        session.execute(delete(LoginSession).where(LoginSession.id == _digest(token)))


def purge_expired_sessions(session: Session) -> int:
    return session.execute(delete(LoginSession).where(LoginSession.expires_at <= func.now())).rowcount


# ---- token API theo project (CI đẩy report) ----

def create_api_token(session: Session, project_slug: str, name: str, created_by: int | None = None) -> tuple[ApiToken, str]:
    project = session.scalar(select(Project).where(Project.slug == project_slug))
    if project is None:
        raise UserNotFound(f"không có project {project_slug!r}")
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(project_id=project.id, name=name, token_hash=_digest(raw), created_by=created_by)
    session.add(row)
    session.flush()
    return row, raw


def resolve_api_token(session: Session, raw: str | None) -> ApiToken | None:
    if not raw or not raw.startswith(API_TOKEN_PREFIX):
        return None
    row = session.scalar(select(ApiToken).where(ApiToken.token_hash == _digest(raw), ApiToken.revoked_at.is_(None)))
    if row is not None:
        row.last_used_at = _now()
        session.flush()
    return row


def token_can_write(session: Session, token: ApiToken, project_slug: str) -> bool:
    """Token chỉ ghi được vào ĐÚNG project của nó."""
    project = session.get(Project, token.project_id)
    return project is not None and project.slug == project_slug


def revoke_api_token(session: Session, token_id: int) -> bool:
    return session.execute(update(ApiToken).where(ApiToken.id == token_id, ApiToken.revoked_at.is_(None))
                           .values(revoked_at=_now())).rowcount == 1
