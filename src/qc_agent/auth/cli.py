"""Quản trị tài khoản/token bằng CLI (không có form tự đăng ký; quên mật khẩu = `user reset`). Cần QC_DATABASE_URL.

  qc-agent user add EMAIL [--name TÊN]      tạo tài khoản; in MẬT KHẨU TẠM MỘT LẦN (email phải thuộc QC_ALLOWED_EMAIL_DOMAINS)
  qc-agent user reset EMAIL                 đặt lại mật khẩu (in mật khẩu mới), mở khoá, thu hồi mọi phiên
  qc-agent user deactivate|activate EMAIL   vô hiệu hoá / kích hoạt lại (vô hiệu hoá cũng thu hồi phiên)
  qc-agent user list
  qc-agent token create --project SLUG --name TÊN     tạo token API cho CI; in TOKEN MỘT LẦN (DB chỉ giữ sha256)
  qc-agent token revoke ID
  qc-agent token list --project SLUG

Exit: 0 ok · 3 lỗi dữ liệu/cấu hình (email/domain/không tìm thấy) · 2 dùng sai lệnh.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from qc_agent.auth import service
from qc_agent.jobs.db import make_engine, session_scope
from qc_agent.jobs.models import ApiToken, Project, User


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="qc-agent", description=__doc__.split("\n")[0])
    top = ap.add_subparsers(dest="group", required=True)
    user = top.add_parser("user").add_subparsers(dest="action", required=True)
    add = user.add_parser("add")
    add.add_argument("email")
    add.add_argument("--name")
    for name in ("reset", "deactivate", "activate"):
        user.add_parser(name).add_argument("email")
    user.add_parser("list")
    token = top.add_parser("token").add_subparsers(dest="action", required=True)
    create = token.add_parser("create")
    create.add_argument("--project", required=True)
    create.add_argument("--name", required=True)
    token.add_parser("revoke").add_argument("id", type=int)
    token.add_parser("list").add_argument("--project", required=True)
    return ap


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 2
    try:
        engine = make_engine()
    except RuntimeError as error:
        print(f"LỖI CẤU HÌNH: {error}", file=sys.stderr)
        return 3
    try:
        with session_scope(engine) as s:
            code, lines = _run(s, args)
        # in SAU khi commit: mật khẩu/token chỉ hiện khi bản ghi đã thật sự được lưu
        for line in lines:
            print(line, file=sys.stdout if code == 0 else sys.stderr)
        return code
    except (service.AuthError, ValueError) as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return 3
    finally:
        engine.dispose()


def _run(s, args) -> tuple[int, list[str]]:
    if args.group == "user":
        if args.action == "add":
            user, password = service.create_user(s, args.email, display_name=args.name)
            return 0, [f"đã tạo {user.email}", f"mật khẩu tạm (chỉ hiện một lần): {password}"]
        if args.action == "reset":
            user, password = service.reset_password(s, args.email)
            return 0, [f"đã đặt lại mật khẩu {user.email}; mọi phiên cũ bị thu hồi", f"mật khẩu mới (chỉ hiện một lần): {password}"]
        if args.action in ("deactivate", "activate"):
            user = service.set_active(s, args.email, args.action == "activate")
            return 0, [f"{user.email}: {'đang hoạt động' if user.is_active else 'đã vô hiệu hoá'}"]
        return 0, [f"{u.email}	{'active' if u.is_active else 'disabled'}	{u.last_login_at or '-'}"
                   for u in s.scalars(select(User).order_by(User.email))]
    if args.action == "create":
        row, raw = service.create_api_token(s, args.project, args.name)
        return 0, [f"token #{row.id} cho project {args.project}", f"token (chỉ hiện một lần): {raw}"]
    if args.action == "revoke":
        if service.revoke_api_token(s, args.id):
            return 0, ["đã thu hồi"]
        return 3, ["không có token đang hoạt động với id này"]
    rows = s.execute(select(ApiToken, Project.slug).join(Project, Project.id == ApiToken.project_id)
                     .where(Project.slug == args.project).order_by(ApiToken.id)).all()
    return 0, [f"{t.id}	{slug}	{t.name}	{'revoked' if t.revoked_at else 'active'}	{t.last_used_at or '-'}" for t, slug in rows]
