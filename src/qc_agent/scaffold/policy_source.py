"""Nguồn policy cho `validate`/`init` ở MÁY DEV (plan 16, Q8). Thứ tự: `--projects-dir` > fetch nhánh `main` của qc-agent > snapshot trong image.

Gate CI KHÔNG đi qua đây: nó đọc bản fetch của workflow và không bao giờ dùng snapshot. Snapshot chỉ là dự phòng để `validate` chạy được khi offline,
nên luôn kèm cảnh báo để dev biết policy có thể cũ hơn `main`."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from qc_agent import settings
from qc_agent.core import project as pj

QC_AGENT_REPO = "Muteen-Felix/QC-Agent"
API_URL = "https://api.github.com"
TIMEOUT_S = 10


@dataclass(frozen=True)
class PolicySource:
    dir: Path
    label: str                 # dòng đầu output của validate
    warning: str | None = None


class FetchError(Exception):
    pass


def _get(url: str, token: str | None) -> bytes | None:
    """None nếu 404. Mọi lỗi khác (mạng, 401/403, 5xx) là FetchError."""
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": "qc-agent-validate", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise FetchError(f"HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise FetchError(str(getattr(error, "reason", error))) from None


def fetch_main(slug: str, into: Path) -> str:
    """Tải `_default.yaml` (bắt buộc) và `<slug>.yaml` (tuỳ chọn) từ qc-agent@main vào `into`. Trả API URL đã dùng để ghi nhãn."""
    if not re.match(pj._NAME, slug or ""):
        raise FetchError(f"slug không hợp lệ: {slug!r}")
    api = (os.environ.get("QC_POLICY_API_URL") or API_URL).rstrip("/")
    token = os.environ.get("QC_READ_TOKEN") or None
    base = f"{api}/repos/{QC_AGENT_REPO}/contents/configs/projects"
    default = _get(f"{base}/{pj.DEFAULT_NAME}.yaml?ref=main", token)
    if default is None:
        raise FetchError(f"main không có {pj.DEFAULT_NAME}.yaml")
    (into / f"{pj.DEFAULT_NAME}.yaml").write_bytes(default)
    own = _get(f"{base}/{slug}.yaml?ref=main", token)
    if own is not None:
        (into / f"{slug}.yaml").write_bytes(own)
    return api


@contextlib.contextmanager
def resolve_source(slug: str, projects_dir=None):
    if projects_dir:
        yield PolicySource(Path(projects_dir), f"policy: {projects_dir} (--projects-dir)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="qc-policy-"))
    try:
        try:
            fetch_main(slug, tmp)
            source = PolicySource(tmp, f"policy: {QC_AGENT_REPO}@main (fetch)")
        except FetchError as error:
            snapshot = settings.get().resolved_projects_dir
            build = os.environ.get("QC_AGENT_GIT_SHA") or "unknown"
            source = PolicySource(snapshot, f"policy: snapshot đóng gói trong image (build {build})",
                                  f"using bundled policy snapshot from build {build}: không lấy được {QC_AGENT_REPO}@main ({error}); "
                                  f"policy có thể cũ hơn main, gate CI luôn dùng main")
        yield source
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
