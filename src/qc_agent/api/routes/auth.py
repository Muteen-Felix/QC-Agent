"""Đăng nhập / đăng xuất / tôi là ai / đổi mật khẩu. Phiên là cookie httpOnly; DB chỉ giữ sha256 của token."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response

from qc_agent.api.deps import SESSION_COOKIE, DbDep, StateDep, UserDep
from qc_agent.api.schemas import LoginIn, PasswordIn
from qc_agent.api.serialize import user_json
from qc_agent.auth import passwords, service
from qc_agent.jobs.db import session_scope

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_cookie(response: Response, state, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=state.cfg.cookie_secure, samesite="lax",
                        max_age=int(timedelta(hours=state.cfg.session_ttl_hours).total_seconds()), path="/")


@router.post("/login")
def login(body: LoginIn, response: Response, state: StateDep):
    # scope riêng: đếm đăng nhập sai / khoá tài khoản phải được COMMIT trước khi trả 401 (nếu không sẽ bị rollback theo lỗi)
    with session_scope(state.engine) as session:
        user = service.authenticate(session, body.email, body.password)
        token = service.create_session(session, user) if user else None
        data = user_json(user) if user else None
    if user is None:
        raise HTTPException(status_code=401, detail="sai email hoặc mật khẩu")  # một thông điệp cho mọi lý do
    _set_cookie(response, state, token)
    return {"user": data}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: DbDep):
    service.revoke_session(session, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(user: UserDep):
    return {"user": user_json(user)}


@router.post("/password")
def change_password(body: PasswordIn, response: Response, state: StateDep, user: UserDep):
    error = None
    with session_scope(state.engine) as session:
        verified = service.authenticate(session, user.email, body.current_password)  # sai => tính vào khoá tài khoản (được commit)
        token = None
        if verified is not None:
            try:
                passwords.check_policy(body.new_password)
                service.reset_password(session, user.email, body.new_password)  # thu hồi MỌI phiên cũ
                token = service.create_session(session, service.get_user(session, user.email))
            except passwords.WeakPassword as exc:
                error = str(exc)
    if verified is None:
        raise HTTPException(status_code=403, detail="mật khẩu hiện tại không đúng")
    if error:
        raise HTTPException(status_code=422, detail=error)
    _set_cookie(response, state, token)
    return {"ok": True}
