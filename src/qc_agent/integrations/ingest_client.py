"""Đẩy kết quả một run (report.json + report.md) lên API của service để có lịch sử tập trung: POST /api/v1/projects/{slug}/runs.

  QC_API_URL    gốc URL của service (https://qc.example.com)
  QC_API_TOKEN  token API của ĐÚNG project (qca_...) — BÍ MẬT: chỉ nằm trong header, không log, không trả về

Chỉ GHI NHẬN: lỗi ở đây không được làm đổi verdict/exit code của CI. Idempotent theo external_id (gửi lại cùng id là an toàn)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

TIMEOUT_S = 20.0
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _read_text(path: Path, limit: int) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace") if len(data) <= limit else None


def push_run(api_url: str, token: str, project: str, run_dir, *, external_id: str, mode: str, pr_number: int | None = None,
             sha: str | None = None, branch: str | None = None, timeout: float = TIMEOUT_S) -> dict:
    """Trả {'ok': bool, 'status'?, 'job_id'?, 'created'?, 'error'?}. Không bao giờ raise, không bao giờ chứa token/URL."""
    if not _SLUG.match(project or ""):
        return {"ok": False, "error": "project không hợp lệ"}
    if urlsplit(api_url or "").scheme not in ("http", "https"):
        return {"ok": False, "error": "QC_API_URL phải là http(s)"}
    if not token:
        return {"ok": False, "error": "thiếu QC_API_TOKEN"}
    run_dir = Path(run_dir)
    try:
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"ok": False, "error": "không đọc được report.json"}
    body = {"external_id": external_id, "mode": mode, "report": report, "report_md": _read_text(run_dir / "report.md", 1_500_000)}
    for key, value in (("pr_number", pr_number), ("sha", sha), ("branch", branch)):
        if value is not None:
            body[key] = value
    req = urllib.request.Request(f"{api_url.rstrip('/')}/api/v1/projects/{quote(project)}/runs", method="POST",
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read() or b"{}")
            return {"ok": True, "status": response.status, "job_id": data.get("id"), "created": data.get("created")}
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "error": "service từ chối (kiểm tra token/project)"}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        return {"ok": False, "error": type(error).__name__}
