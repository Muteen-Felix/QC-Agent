"""Phần dùng chung của ba adapter Security (semgrep, gitleaks, trivy). KHÔNG có tên công cụ cụ thể, KHÔNG có ngưỡng: adapter ĐẾM, oracle `threshold` PHÁN.

Đầu vào của suite nằm trong repo SUT nên là input không tin cậy: mỗi phần tử đi vào lệnh là MỘT argv (không qua shell) và đã qua `safe_relpath`
(không tuyệt đối, không `..`, không bắt đầu bằng `-`) nên không thoát khỏi thư mục SUT và không chèn được cờ.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

from qc_agent.adapters._base import AdapterParseError

LEVELS = ("critical", "high", "medium", "low")
MAX_FINDINGS = 200   # chặn result/comment phình to; METRIC vẫn đếm đủ, phần cắt được ghi vào adapter_notes (mất thông tin, không bịa)
_HINT = {"critical": "critical", "high": "critical", "medium": "medium", "low": "low"}


def severity_hint(level: str | None) -> str | None:
    return _HINT.get(level) if level else None


def safe_relpath(value, what: str) -> str:
    """Đường dẫn tương đối nằm trong SUT root; ném AdapterParseError nếu tuyệt đối, có `..`, bắt đầu bằng `-` hoặc chứa ký tự điều khiển."""
    if not isinstance(value, str) or not value.strip():
        raise AdapterParseError(f"{what} phải là chuỗi không rỗng")
    if any(ord(ch) < 32 for ch in value):
        raise AdapterParseError(f"{what} chứa ký tự điều khiển")
    if value.startswith("-"):
        raise AdapterParseError(f"{what} không được bắt đầu bằng '-' (chống chèn cờ): {value!r}")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or value.startswith(("/", "\\")):
        raise AdapterParseError(f"{what} phải là đường dẫn tương đối trong repo SUT: {value!r}")
    if ".." in PureWindowsPath(value).parts or ".." in PurePosixPath(value).parts:
        raise AdapterParseError(f"{what} không được chứa '..': {value!r}")
    return value


def safe_relpaths(value, what: str, *, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if not isinstance(value, list):
        raise AdapterParseError(f"{what} phải là danh sách chuỗi")
    return [safe_relpath(item, f"{what}[{i}]") for i, item in enumerate(value)]


def safe_patterns(value, what: str) -> list[str]:
    """Mẫu loại trừ (glob): được có `*`, nhưng vẫn là chuỗi không rỗng, không ký tự điều khiển. Luôn đi vào lệnh dạng `--flag=<mẫu>` (một argv)."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(p, str) or not p.strip() or any(ord(c) < 32 for c in p) for p in value):
        raise AdapterParseError(f"{what} phải là danh sách chuỗi không rỗng, không ký tự điều khiển")
    return list(value)


def read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise AdapterParseError(f"{what} không đọc được: {type(error).__name__}") from error   # không nhúng nội dung lỗi: có thể trích dữ liệu của file


def finding_id(prefix: str, key: str, seen: dict[str, int]) -> str:
    """`f-<tool>-<sha1(key)[:12]>` — ổn định giữa các lần chạy (không dùng số thứ tự). Trùng trong CÙNG result thì thêm hậu tố -2, -3…
    (thứ tự do nơi gọi sắp xếp tất định)."""
    base = f"f-{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}-{seen[base]}"


def finding(fid: str, title: str, tool: str, level: str | None) -> dict:
    return {"finding_id": fid, "title": title, "detected_by": tool, "verdict_source": "deterministic_assert",
            "confidence": None, "severity_hint": severity_hint(level)}


def cap_findings(findings: list[dict], notes: list[str]) -> list[dict]:
    if len(findings) > MAX_FINDINGS:
        notes.append(f"findings bị cắt còn {MAX_FINDINGS}/{len(findings)} (metric vẫn đếm đủ; danh sách đầy đủ ở evidence raw_output)")
        return findings[:MAX_FINDINGS]
    return findings


def require_int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterParseError(f"{what} phải là số nguyên")
    return value
