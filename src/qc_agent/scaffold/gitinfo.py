"""Đọc offline tên repo GitHub của một thư mục (từ `.git/config`, không gọi `git`, không mạng) và suy slug mặc định từ đó.
Dùng chung cho `init` (--slug mặc định) và `validate` (--project mặc định) để hai lệnh luôn suy ra CÙNG một slug."""
from __future__ import annotations

import re
from pathlib import Path

_URL = re.compile(r"^(?:https?://[^/]+/|ssh://[^/]+/|[\w.-]+@[^:]+:)(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def origin_repo(root) -> str | None:
    """`owner/repo` của remote `origin`; None nếu không đọc được (không phải git, worktree dùng file .git, không có origin)."""
    config = Path(root) / ".git" / "config"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_origin = bool(re.fullmatch(r'\[remote\s+"origin"\]', line))
        elif in_origin and re.match(r"url\s*=", line):
            match = _URL.match(line.split("=", 1)[1].strip())
            return match["repo"] if match else None
    return None


def normalize_slug(text: str) -> str:
    """Chuẩn hoá về `^[a-z0-9][a-z0-9_-]*$` (luật slug của project)."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", (text or "").lower()).strip("-_")
    return slug or "sut"


def default_slug(root) -> str:
    """Tên repo trong origin; không có thì tên thư mục (sut_root đã resolve)."""
    repo = origin_repo(root)
    return normalize_slug(repo.split("/", 1)[1] if repo else Path(root).resolve().name)
