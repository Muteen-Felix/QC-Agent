"""Băm và kiểm mật khẩu bằng argon2id (mặc định của argon2-cffi)."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH, MAX_LENGTH = 10, 128  # MAX chặn DoS bằng mật khẩu khổng lồ; không giới hạn ký tự
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-login")


class WeakPassword(ValueError):
    pass


def check_policy(password: str) -> None:
    if not isinstance(password, str) or not (MIN_LENGTH <= len(password) <= MAX_LENGTH):
        raise WeakPassword(f"mật khẩu phải dài {MIN_LENGTH}-{MAX_LENGTH} ký tự")
    if not password.strip():
        raise WeakPassword("mật khẩu không được chỉ gồm khoảng trắng")


def hash_password(password: str) -> str:
    check_policy(password)
    return _hasher.hash(password)


def verify(password_hash: str | None, password: str) -> bool:
    if not password_hash or not isinstance(password, str) or len(password) > MAX_LENGTH:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError, UnicodeError):  # hash hỏng/không phải ASCII trong DB: coi như sai, không 500
        return False


def burn_time(password: str) -> None:
    """Tài khoản không tồn tại/không có mật khẩu: vẫn tốn một lần băm để thời gian phản hồi không lộ email có tồn tại."""
    verify(_DUMMY_HASH, password if isinstance(password, str) else "")
