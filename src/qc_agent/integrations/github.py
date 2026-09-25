"""GitHub: Check Run + comment PR "dính" (một comment được cập nhật tại chỗ, không spam) cho kết quả của một run.

  GITHUB_TOKEN     token của workflow (checks:write, pull-requests:write) — BÍ MẬT: chỉ nằm trong header, không log, không trả về
  GITHUB_API_URL   mặc định https://api.github.com (GitHub Enterprise: đặt lại)

Report/finding/thông điệp canary là NỘI DUNG DO SUT KIỂM SOÁT (mã của PR có thể chọn chúng): được làm sạch trước khi vào Markdown
(một dòng, thoát ký tự Markdown/HTML, vô hiệu @mention và #123 tham chiếu issue, cắt độ dài).
Ghi vào GitHub chỉ là PHẦN PHỤ: verdict chặn merge vẫn do exit code của job quyết định, không phụ thuộc module này."""
from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TIMEOUT_S = 15.0
DEFAULT_API = "https://api.github.com"
ZWSP = "​"
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SAFE_LINK = re.compile(r"^https?://[^\s()<>`\\]+$")  # chèn vào [text](link): không khoảng trắng/ngoặc/ký tự phá Markdown
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")
_MD = re.compile(r"([\\`*_{}\[\]()#+!|~>-])")
_EMOJI = {"PASS": "🟢", "FAIL": "🔴", "YELLOW": "🟡"}
_ICON = {"pass": "✅", "fail": "❌", "error": "⚠️", "skipped": "⏭️"}
MAX_COMMENT = 60000
MAX_ROWS, MAX_FINDINGS = 100, 15


class GitHubError(Exception):
    """Lỗi gọi API GitHub. Không bao giờ chứa token."""

    def __init__(self, status: int | None, message: str):
        super().__init__(f"{status or 'network'}: {message}")
        self.status = status


def clean_md(value, limit: int = 160) -> str:
    """Làm sạch một mẩu văn bản không tin cậy để chèn vào Markdown của GitHub."""
    text = " ".join(_CONTROL.sub("", str(value)).split())
    text = html.escape(text, quote=False)  # <script>, <img ...>, &entity;
    text = _MD.sub(r"\\\1", text)
    text = text.replace("@", "@" + ZWSP).replace("\\#", "\\#" + ZWSP)  # @user / @org/team, #123 (tham chiếu issue/PR)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def validate_target(repo: str, sha: str | None = None) -> None:
    if not _REPO.match(repo or ""):
        raise ValueError("repo phải có dạng owner/name")
    if sha is not None and not _SHA.match(sha):
        raise ValueError("sha không hợp lệ")


class GitHubClient:
    def __init__(self, token: str, api_url: str | None = None, timeout: float = TIMEOUT_S):
        if not token:
            raise ValueError("thiếu GITHUB_TOKEN")
        self._token = token
        self.api = (api_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API).rstrip("/")
        if urlsplit(self.api).scheme not in ("http", "https"):
            raise ValueError("GITHUB_API_URL phải là http(s)")
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "qc-agent", **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            message = ""
            try:
                message = str(json.loads(error.read()).get("message", ""))[:200]
            except (ValueError, OSError, AttributeError):
                pass
            raise GitHubError(error.code, message or "http error") from None
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
            raise GitHubError(None, type(error).__name__) from None  # không đưa str(error): có thể chứa URL/header

    # ---- Check Run ----

    def create_check_run(self, repo: str, sha: str, *, name: str, conclusion: str, title: str, summary: str,
                         details_url: str | None = None) -> int | None:
        validate_target(repo, sha)
        body = {"name": name[:100], "head_sha": sha, "status": "completed", "conclusion": conclusion,
                "output": {"title": title[:250], "summary": summary[:MAX_COMMENT]}}
        if details_url and urlsplit(details_url).scheme in ("http", "https"):
            body["details_url"] = details_url
        _, data = self.request("POST", f"/repos/{repo}/check-runs", body)
        return (data or {}).get("id")

    # ---- comment dính ----

    def upsert_comment(self, repo: str, pr_number: int, body: str, marker: str, max_pages: int = 5) -> str:
        """Cập nhật comment đã có marker (do CHÍNH token này tạo), nếu chưa có thì tạo mới. Trả 'created' | 'updated'."""
        validate_target(repo)
        if not isinstance(pr_number, int) or pr_number < 1:
            raise ValueError("pr_number không hợp lệ")
        for page in range(1, max_pages + 1):
            _, comments = self.request("GET", f"/repos/{repo}/issues/{pr_number}/comments?per_page=100&page={page}")
            for comment in comments or []:
                # chỉ sửa comment của bot và có marker: người khác dán marker vào comment của họ thì không bị ghi đè
                if marker in (comment.get("body") or "") and (comment.get("user") or {}).get("type") == "Bot":
                    self.request("PATCH", f"/repos/{repo}/issues/comments/{comment['id']}", {"body": body})
                    return "updated"
            if not comments or len(comments) < 100:
                break
        self.request("POST", f"/repos/{repo}/issues/{pr_number}/comments", {"body": body})
        return "created"


# ---- dựng nội dung ----

def conclusion_for(verdict: str, exit_code: int | None) -> str:
    if exit_code not in (None, 0, 1):
        return "failure"  # lỗi hệ thống/cấu hình: không được xanh
    return {"PASS": "success", "FAIL": "failure", "YELLOW": "neutral"}.get(verdict, "failure")


def marker_for(project: str, mode: str) -> str:
    return f"<!-- qc-agent:{re.sub(r'[^A-Za-z0-9_.-]', '_', project)}:{re.sub(r'[^A-Za-z0-9_.-]', '_', mode)} -->"


def render_summary(run: dict, *, project: str, mode: str, exit_code: int | None = None, sut_sha: str | None = None,
                   link: str | None = None) -> str:
    """Markdown tóm tắt (dùng cho cả comment và summary của Check Run)."""
    report = run["report"]
    verdict = str(report.get("gate_verdict", "UNKNOWN"))
    problem = exit_code not in (None, 0, 1)
    emoji = "⚠️" if problem else _EMOJI.get(verdict, "⚠️")
    gating = [g for g in report.get("deterministic_view") or [] if isinstance(g, dict)]
    passed = sum(1 for g in gating if g.get("status") == "pass")
    meta = [f"gate {passed}/{len(gating)}"]
    if exit_code is not None:
        meta.insert(0, f"exit {int(exit_code)}")
    if sut_sha and _SHA.match(sut_sha):
        meta.append(f"SUT `{sut_sha[:7]}`")
    lines = [f"## {emoji} QC-Agent · {clean_md(project, 60)} · {clean_md(mode, 30)} — {clean_md(verdict, 12)}", "", " · ".join(meta)]
    if link and _SAFE_LINK.match(link):
        lines[-1] += f" · [chi tiết]({link})"
    policy = report.get("policy") if isinstance(report.get("policy"), dict) else {}
    if policy.get("source") in ("default", "registered"):
        ref = str(policy.get("ref") or "")
        lines.append(f"policy: {'_default' if policy['source'] == 'default' else clean_md(project, 60)}"
                     f"{' @ main ' + ref[:7] if re.fullmatch(r'[0-9a-f]{7,40}', ref) else ''}")
    if problem:
        lines += ["", "> ⚠️ Lỗi hệ thống/cấu hình (không phải kết quả của gate). Xem log của job."]
    if gating:
        lines += ["", "**Task chặn merge (gate)**", "", "| Task | Worker | Kết quả |", "|---|---|---|"]
        for g in gating[:MAX_ROWS]:
            status = str(g.get("status", "?"))
            lines.append(f"| {clean_md(g.get('task_id', '?'), 60)} | {clean_md(g.get('worker', '—'), 40)} | {_ICON.get(status, '❔')} {clean_md(status, 12)} |")
        if len(gating) > MAX_ROWS:
            lines.append(f"| … | | +{len(gating) - MAX_ROWS} task nữa |")
    banner = report.get("banner") or []
    if banner:
        lines += ["", "**⚠ Skipped / error — đọc trước**", ""]
        lines += [f"- `{clean_md(b[0], 60)}`: {clean_md(b[1], 160)}" for b in banner[:20] if isinstance(b, (list, tuple)) and len(b) >= 2]
    for canary in report.get("canary") or []:
        if isinstance(canary, dict) and not canary.get("ok"):
            lines += ["", f"🚨 canary: {clean_md(canary.get('message', ''), 200)}"]
    findings = run.get("findings") or []
    if findings:
        lines += ["", f"<details><summary>🔎 {len(findings)} finding discovery (tham khảo, KHÔNG chặn merge)</summary>", ""]
        lines += [f"- {clean_md(f)}" for f in findings[:MAX_FINDINGS]]
        if len(findings) > MAX_FINDINGS:
            lines.append(f"- … +{len(findings) - MAX_FINDINGS} finding nữa")
        lines += ["", "</details>"]
    return "\n".join(lines)


def render_comment(run: dict, *, project: str, mode: str, **kw) -> str:
    body = marker_for(project, mode) + "\n" + render_summary(run, project=project, mode=mode, **kw)
    return body if len(body) <= MAX_COMMENT else body[: MAX_COMMENT - 40] + "\n\n… (đã cắt bớt)"
