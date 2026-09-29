"""Đăng kết quả khâu Security (Semgrep, gitleaks, Trivy) lên PR dưới dạng MỘT review (Làn A, A-9).

    python -m qc_agent.integrations.security_review --run-dir runs/r-0001

Vì sao là module riêng: finding trong result.json KHÔNG có trường vị trí (file:dòng chỉ nằm trong `title`), nên `github.render_summary` không tự gắn dòng được.
Vị trí đầy đủ nằm ở file evidence `semgrep.json`/`gitleaks.json`/`trivy.json` của từng task; ở đây đọc lại chúng (khớp sha256 với result để không đọc nhầm file cũ).

  - finding của Semgrep/gitleaks NẰM TRONG diff của PR => một comment inline; NGOÀI diff => chỉ liệt kê trong thân review (GitHub chỉ cho gắn vào dòng trong diff).
  - Trivy không có số dòng đáng tin (lỗ hổng gắn với lockfile) => chỉ nằm trong thân review.
  - Nội dung không tin cậy: rule id, message, path, tên gói đều xuất phát từ công cụ chạy trên mã của PR => qua `github.clean_md`. KHÔNG BAO GIỜ đọc/đưa
    đoạn mã (`extra.lines`) hay `Secret`/`Match` vào review; báo cáo gitleaks còn giá trị chưa REDACTED thì bị bỏ nguyên cả file (kiểm lại dù adapter đã kiểm).
  - Chống spam: review mang `<!-- qc-agent:security sha256=<hash danh sách finding> -->`; đã có review của bot cùng hash thì không đăng lại (push thêm commit mà finding không đổi).
  - CHỈ GHI NHẬN, giống mọi module integrations/: không đổi verdict/exit code. Lỗi GitHub (fork: token chỉ-đọc => 403) chỉ là cảnh báo trong JSON kết quả; CLI luôn exit 0.
Tái dùng refine_review.pr_diff_lines (dòng nằm trong diff) và các giới hạn của nó; `already_posted` của refine gắn cứng marker `refine` nên ở đây có bản riêng cho marker `security`."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.gitleaks_adapter import _assert_redacted
from qc_agent.adapters.semgrep_adapter import SEVERITY as SEMGREP_SEVERITY
from qc_agent.adapters.trivy_adapter import SEVERITY as TRIVY_SEVERITY
from qc_agent.integrations import ci, github
from qc_agent.integrations.refine_review import MAX_COMMENTS, MAX_PAGES, pr_diff_lines

MARKER = "<!-- qc-agent:security sha256={digest} -->"
_TASK_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
TOOLS = {"semgrep": "semgrep.json", "gitleaks": "gitleaks.json", "trivy": "trivy.json"}
_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
_ICON = {"critical": "🔴", "high": "🔴", "medium": "🟠", "low": "🟡", "unknown": "⚪"}
MAX_LISTED = 30           # số finding liệt kê trong thân review (phần còn lại chỉ có số đếm)
MAX_MESSAGE = 200


@dataclass(frozen=True)
class Finding:
    tool: str                     # semgrep | gitleaks | trivy
    rule: str                     # check_id | RuleID | VulnerabilityID
    level: str                    # critical | high | medium | low | unknown
    path: str                     # semgrep/gitleaks: file; trivy: lockfile (Target)
    line: int | None = None       # trivy: None
    message: str = ""             # chỉ semgrep (thông điệp của LUẬT, không phải đoạn mã); gitleaks/trivy để trống
    package: str = ""             # trivy: tên@phiên bản
    fixed: str = ""               # trivy: phiên bản đã vá

    def key(self) -> tuple:
        return (self.tool, self.rule, self.level, self.path, self.line if self.line is not None else -1, self.package)


# ---------- đọc evidence ----------

def _evidence_file(run_dir: Path, result: dict, name: str) -> Path | None:
    """File báo cáo thô của task, chỉ khi nằm trong run_dir VÀ sha256 khớp evidence raw_output của result."""
    task_id = result.get("task_id")
    if not isinstance(task_id, str) or not _TASK_ID.match(task_id):
        return None
    path = (run_dir / task_id / name).resolve()
    try:
        path.relative_to(run_dir.resolve())
    except ValueError:
        return None
    expected = next((e.get("sha256") for e in result.get("evidence") or [] if isinstance(e, dict) and e.get("kind") == "raw_output"), None)
    if not path.is_file() or not isinstance(expected, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        return None
    return path


def _semgrep(data) -> list[Finding]:
    out = []
    for item in data.get("results") or []:
        extra = item.get("extra") or {}          # KHÔNG đọc extra.lines / extra.fingerprint
        level = SEMGREP_SEVERITY.get(extra.get("severity"))
        line = (item.get("start") or {}).get("line")
        if not (isinstance(item.get("check_id"), str) and isinstance(item.get("path"), str) and isinstance(line, int) and level):
            raise ValueError("finding semgrep sai dạng")
        out.append(Finding("semgrep", item["check_id"], level, item["path"], line, str(extra.get("message") or "")))
    return out


def _gitleaks(data) -> list[Finding]:
    entries = data or []
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError("báo cáo gitleaks sai dạng")
    try:
        _assert_redacted(entries)      # còn secret thật => bỏ nguyên báo cáo, không trích trường nào
    except AdapterParseError:
        raise ValueError("báo cáo gitleaks chưa REDACTED") from None
    out = []
    for entry in entries:
        if not (isinstance(entry.get("RuleID"), str) and isinstance(entry.get("File"), str) and isinstance(entry.get("StartLine"), int)):
            raise ValueError("finding gitleaks sai dạng")
        out.append(Finding("gitleaks", entry["RuleID"], "high", entry["File"], entry["StartLine"]))     # không đọc Secret/Match/Description
    return out


def _trivy(data) -> list[Finding]:
    out = []
    for result in data.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            level = TRIVY_SEVERITY.get(vuln.get("Severity"))
            if not (isinstance(result.get("Target"), str) and isinstance(vuln.get("VulnerabilityID"), str) and level):
                raise ValueError("finding trivy sai dạng")
            out.append(Finding("trivy", vuln["VulnerabilityID"], level, result["Target"], None,
                               package=f"{vuln.get('PkgName') or '?'}@{vuln.get('InstalledVersion') or '?'}", fixed=str(vuln.get("FixedVersion") or "")))
    return out


_PARSERS = {"semgrep": _semgrep, "gitleaks": _gitleaks, "trivy": _trivy}


def collect(run_dir) -> tuple[list[Finding], list[str]]:
    """(finding, ghi chú về báo cáo đọc không được). Chỉ đọc kết quả của task thuộc ba worker Security đã chạy xong (pass/fail)."""
    run_dir = Path(run_dir)
    findings: list[Finding] = []
    notes: list[str] = []
    for result_file in sorted((run_dir / "results").glob("*.json")):
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            tool = (result.get("worker") or {}).get("name")
        except (OSError, ValueError, AttributeError):
            continue
        if tool not in TOOLS or result.get("status") not in ("pass", "fail"):
            continue
        raw = _evidence_file(run_dir, result, TOOLS[tool])
        if raw is None:
            notes.append(f"không đọc được báo cáo của {tool} (thiếu file hoặc sha256 không khớp result)")
            continue
        try:
            findings += _PARSERS[tool](json.loads(raw.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, AttributeError, TypeError) as error:
            notes.append(f"bỏ qua báo cáo của {tool}: {error if isinstance(error, ValueError) else 'không đọc được'}")
    findings.sort(key=lambda f: (_RANK[f.level], f.tool, f.path, f.line or 0, f.rule, f.package))
    return findings, notes


# ---------- dựng review ----------

def digest(findings: list[Finding]) -> str:
    """Hash của DANH SÁCH finding (công cụ, luật, mức, vị trí, gói) — không phụ thuộc thông điệp/định dạng và thứ tự."""
    canonical = "\n".join(sorted(json.dumps(f.key(), ensure_ascii=False) for f in findings))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _txt(value, limit: int = 100) -> str:
    """Văn bản không tin cậy đã qua clean_md, hiển thị THƯỜNG (không bọc backtick): clean_md escape ký tự Markdown bằng dấu gạch chéo ngược, Markdown tự bỏ nó
    khi hiển thị, còn trong code span thì dấu đó lộ ra. Vì thế không đặt nội dung động vào code span."""
    return github.clean_md(value, limit)


def _where(f: Finding) -> str:
    return _txt(f.path if f.line is None else f"{f.path}:{f.line}", 120)


def inline_body(f: Finding) -> str:
    if f.tool == "gitleaks":
        return (f"🔐 **Có thể lộ secret** · gitleaks · {_txt(f.rule)}\n\nGiá trị đã được ẩn khỏi báo cáo. Hãy thu hồi/xoay secret rồi xoá khỏi mã. "
                "Nếu là dương tính giả: thêm fingerprint vào `.gitleaksignore` (hiện trong diff PR).")
    text = f"{_ICON[f.level]} **{f.level}** · Semgrep · {_txt(f.rule)}"
    if f.message:
        text += f"\n\n{github.clean_md(f.message, MAX_MESSAGE)}"
    return text + "\n\nBỏ qua có lý do: thêm `# nosemgrep: <rule-id>` ở dòng này (hiện trong diff PR)."


def split_by_diff(findings: list[Finding], diff: dict[str, set[int]]) -> tuple[list[dict], list[Finding]]:
    """(comment inline, finding chỉ vào thân). Inline khi là Semgrep/gitleaks có số dòng NẰM TRONG diff (phía RIGHT); tối đa MAX_COMMENTS."""
    comments, outside = [], []
    for f in findings:
        path = f.path[2:] if f.path.startswith("./") else f.path
        if f.tool != "trivy" and f.line is not None and f.line in diff.get(path, ()) and len(comments) < MAX_COMMENTS:
            comments.append({"path": path, "line": f.line, "side": "RIGHT", "body": inline_body(f)})
        else:
            outside.append(f)
    return comments, outside


def _counts(findings: list[Finding]) -> str:
    parts = []
    for tool, label in (("semgrep", "Semgrep"), ("gitleaks", "gitleaks"), ("trivy", "Trivy")):
        mine = [f for f in findings if f.tool == tool]
        if mine and tool != "gitleaks":
            by_level = ", ".join(f"{sum(1 for f in mine if f.level == lv)} {lv}" for lv in _RANK if any(f.level == lv for f in mine))
            parts.append(f"{label}: {len(mine)} ({by_level})")
        elif mine:
            parts.append(f"{label}: {len(mine)}")
    return " · ".join(parts)


def render_body(digest_hex: str, findings: list[Finding], *, inline: int, outside: list[Finding], notes: list[str]) -> str:
    lines = ["### qc-agent · Security", "", _counts(findings),
             f"{inline} finding gắn vào dòng của PR; {len(outside)} nằm ngoài diff hoặc không có số dòng nên chỉ liệt kê ở đây (GitHub chỉ cho gắn vào dòng trong diff)."]
    code = [f for f in outside if f.tool != "trivy"]
    deps = [f for f in outside if f.tool == "trivy"]
    if code:
        lines += ["", "**Mã nguồn**", *[f"- {_ICON[f.level]} {f.level} · {f.tool} · {_txt(f.rule)} @ {_where(f)}" for f in code[:MAX_LISTED]]]
        if len(code) > MAX_LISTED:
            lines.append(f"- … và {len(code) - MAX_LISTED} finding nữa (xem artifact `qc-runs-*`)")
    if deps:
        lines += ["", "**Thư viện có lỗ hổng (Trivy)**"]
        for f in deps[:MAX_LISTED]:
            fix = f" → bản vá {_txt(f.fixed, 40)}" if f.fixed else " → chưa có bản vá"
            lines.append(f"- {_ICON[f.level]} {_txt(f.rule, 60)} · {_txt(f.package)}{fix} · {_where(f)}")
        if len(deps) > MAX_LISTED:
            lines.append(f"- … và {len(deps) - MAX_LISTED} lỗ hổng nữa (xem artifact `qc-runs-*`)")
    if notes:
        lines += ["", *[f"- ⚠️ {github.clean_md(n, 200)}" for n in notes[:10]]]
    lines += ["", "Review này chỉ để đọc: PR bị chặn hay không do kết quả gate quyết định. Bỏ qua một finding có lý do: `# nosemgrep: <rule>` · `.gitleaksignore` · `.trivyignore` "
                  "(mọi dòng bỏ qua đều hiện trong diff PR).", "", MARKER.format(digest=digest_hex)]
    return "\n".join(lines)[: github.MAX_COMMENT]


# ---------- đăng ----------

def already_posted(client: github.GitHubClient, repo: str, pr_number: int, digest_hex: str) -> bool:
    marker = MARKER.format(digest=digest_hex)
    for page in range(1, MAX_PAGES + 1):
        _, reviews = client.request("GET", f"/repos/{repo}/pulls/{pr_number}/reviews?per_page=100&page={page}")
        for review in reviews or []:
            # chỉ tin review của bot: người khác dán marker vào review của họ không chặn được review của ta
            if marker in (review.get("body") or "") and (review.get("user") or {}).get("type") == "Bot":
                return True
        if not reviews or len(reviews) < 100:
            break
    return False


def post_security_review(run_dir, *, env: dict, ctx: dict) -> dict:
    """Trả dict kết quả (không ném lỗi GitHub). `ctx` = ci.context_from_env."""
    findings, notes = collect(run_dir)
    if not findings:
        return {"review": "skipped: không có finding", "notes": notes}
    if not (ctx.get("repo") and ctx.get("pr_number") and ctx.get("sha")) or not env.get("GITHUB_TOKEN"):
        return {"review": "skipped: cần GITHUB_TOKEN và một pull_request", "findings": len(findings)}
    digest_hex = digest(findings)
    try:
        client = github.GitHubClient(env["GITHUB_TOKEN"], env.get("GITHUB_API_URL"))
        github.validate_target(ctx["repo"], ctx["sha"])
        if already_posted(client, ctx["repo"], ctx["pr_number"], digest_hex):
            return {"review": "skipped: đã đăng review cùng hash", "sha256": digest_hex}
        comments, outside = split_by_diff(findings, pr_diff_lines(client, ctx["repo"], ctx["pr_number"]))
        body = render_body(digest_hex, findings, inline=len(comments), outside=outside, notes=notes)
        client.request("POST", f"/repos/{ctx['repo']}/pulls/{ctx['pr_number']}/reviews",
                       {"commit_id": ctx["sha"], "event": "COMMENT", "body": body, "comments": comments})
        return {"review": "created", "comments": len(comments), "outside_diff": len(outside), "sha256": digest_hex}
    except (github.GitHubError, ValueError) as error:
        return {"review": f"error: {error}", "sha256": digest_hex}     # fork: token chỉ-đọc => 403; gate vẫn đúng theo exit code


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", required=True, help="thư mục run (runs/r-NNNN) có results/*.json và <task_id>/{semgrep,gitleaks,trivy}.json")
    args = ap.parse_args(argv)
    try:
        result = post_security_review(args.run_dir, env=os.environ, ctx=ci.context_from_env())
    except Exception as error:  # noqa: BLE001 — báo cáo hỏng không được làm đỏ/xanh job
        result = {"error": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
