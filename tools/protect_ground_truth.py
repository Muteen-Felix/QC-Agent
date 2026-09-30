"""Bật khoá QA cho Ground-Truth trên nhánh chính của một repo SUT (S1-07): yêu cầu review của code owner + bắt buộc đi qua PR.

    GITHUB_TOKEN=<token admin repo> python tools/protect_ground_truth.py --repo OWNER/REPO [--branch main] [--dry-run]

Cơ chế (nói đúng những gì nó làm, không hơn):
  - CODEOWNERS (`/.qc-agent/ <team QA>`, do `qc-agent init` sinh) nói AI phải duyệt; `require_code_owner_reviews` làm việc merge một PR đụng `.qc-agent/**`
    THIẾU duyệt của code owner bị chặn; `required_pull_request_reviews` (không null) chặn PUSH THẲNG vào nhánh. Hai lớp này ĐI CÙNG nhau.
  - Nó GỘP vào bảo vệ hiện có (GET rồi PUT), không ghi đè: `required_status_checks`, `enforce_admins`, `restrictions` và các cờ khác được giữ nguyên;
    chỉ nâng `require_code_owner_reviews` lên true và `required_approving_review_count` lên ít nhất 1. Admin không bị ép (`enforce_admins` giữ nguyên):
    bật nó nếu muốn cả admin cũng phải qua duyệt.
  - Token chỉ đọc từ biến môi trường GITHUB_TOKEN, cần quyền admin repo; không bao giờ được in ra. `--dry-run` chỉ in payload, không PUT.
Exit code: 0 xong · 1 lỗi (kèm thông điệp không lộ token).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from qc_agent.integrations.github import GitHubClient, GitHubError, validate_target

_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


class ProtectError(RuntimeError):
    """Lỗi cấu hình/gọi API. Thông điệp không chứa token."""


def _logins(items, key: str = "login") -> list[str]:
    return [item[key] for item in items or [] if isinstance(item, dict) and isinstance(item.get(key), str)]


def _allowances(block: dict | None) -> dict | None:
    if not isinstance(block, dict):
        return None
    return {"users": _logins(block.get("users")), "teams": _logins(block.get("teams"), "slug"), "apps": _logins(block.get("apps"), "slug")}


def _enabled(block) -> bool | None:
    return bool(block.get("enabled")) if isinstance(block, dict) and "enabled" in block else None


def build_payload(current: dict | None) -> dict:
    """Payload PUT = bảo vệ hiện có (chuyển từ dạng GET sang dạng PUT) + khoá QA. `current` None nghĩa là nhánh chưa được bảo vệ."""
    current = current or {}
    checks = current.get("required_status_checks")
    if isinstance(checks, dict):
        entries = [{"context": c["context"], **({"app_id": c["app_id"]} if isinstance(c.get("app_id"), int) and c["app_id"] >= 0 else {})}
                   for c in checks.get("checks") or [] if isinstance(c, dict) and isinstance(c.get("context"), str)]
        if not entries:
            entries = [{"context": name} for name in checks.get("contexts") or [] if isinstance(name, str)]
        status_checks = {"strict": bool(checks.get("strict")), "checks": entries}
    else:
        status_checks = None

    reviews = dict(current.get("required_pull_request_reviews") or {})
    review = {
        "dismiss_stale_reviews": bool(reviews.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": True,                                           # cơ chế chặn merge thiếu QA
        "required_approving_review_count": max(int(reviews.get("required_approving_review_count") or 0), 1),
        "require_last_push_approval": bool(reviews.get("require_last_push_approval", False)),
    }
    for key in ("dismissal_restrictions", "bypass_pull_request_allowances"):
        allowance = _allowances(reviews.get(key))
        if allowance is not None:
            review[key] = allowance

    payload = {
        "required_status_checks": status_checks,
        "enforce_admins": bool(_enabled(current.get("enforce_admins"))),
        "required_pull_request_reviews": review,                                       # không null => phải qua PR, chặn push thẳng
        "restrictions": _allowances(current.get("restrictions")),
    }
    for key in ("required_linear_history", "allow_force_pushes", "allow_deletions", "block_creations", "required_conversation_resolution",
                "lock_branch", "allow_fork_syncing"):
        value = _enabled(current.get(key))
        if value is not None:
            payload[key] = value
    return payload


def _client(token: str) -> GitHubClient:
    try:
        return GitHubClient(token)
    except ValueError as error:
        raise ProtectError(str(error)) from None


def protect(repo: str, branch: str, token: str, *, dry_run: bool) -> dict:
    try:
        validate_target(repo)
    except ValueError as error:
        raise ProtectError(str(error)) from None
    if not _BRANCH.fullmatch(branch) or ".." in branch:
        raise ProtectError("--branch không hợp lệ")
    client = _client(token)
    path = f"/repos/{repo}/branches/{branch}/protection"
    try:
        _, current = client.request("GET", path)
    except GitHubError as error:
        if error.status != 404:
            raise ProtectError(f"không đọc được bảo vệ hiện tại ({error})") from None
        current = None   # nhánh chưa được bảo vệ (hoặc không có quyền xem: PUT bên dưới sẽ báo rõ)
    payload = build_payload(current)
    if not dry_run:
        try:
            client.request("PUT", path, payload)
        except GitHubError as error:
            hint = " (cần token có quyền admin repo)" if error.status in (401, 403, 404) else ""
            raise ProtectError(f"không bật được bảo vệ ({error}){hint}") from None
    return payload


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="protect_ground_truth.py", description="Bật require_code_owner_reviews + bắt buộc qua PR cho nhánh chính (gộp vào bảo vệ hiện có).")
    ap.add_argument("--repo", required=True, metavar="OWNER/REPO")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in payload sẽ PUT, không gọi PUT")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) and exit_.code == 0 else 1
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        if not token:
            raise ProtectError("thiếu biến môi trường GITHUB_TOKEN (token admin repo)")
        payload = protect(args.repo, args.branch, token, dry_run=args.dry_run)
    except ProtectError as error:
        print(f"LỖI: {str(error).replace(token, '***') if token else error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        print(f"(dry-run: chưa gọi PUT lên {args.repo}@{args.branch})", file=sys.stderr)
    else:
        print(f"Đã bật require_code_owner_reviews + bắt buộc qua PR cho {args.repo}@{args.branch}. "
              f"Cần CODEOWNERS có `/.qc-agent/ <team QA>` trên nhánh này thì khoá mới có tác dụng.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
