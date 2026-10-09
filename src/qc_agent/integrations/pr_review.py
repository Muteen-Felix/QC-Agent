"""Đăng finding chuẩn hoá lên PR; lỗi GitHub không đổi kết quả gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path

from qc_agent.integrations import ci, github
from qc_agent.integrations.refine_review import MAX_COMMENTS, MAX_PAGES, pr_diff_lines
from qc_agent.logging_setup import configure, event

log = logging.getLogger("qc_agent.pr_review")
MARKER = "<!-- qc-agent:review sha256={digest} -->"
ICON = {"critical": "🔴", "medium": "🟠", "low": "🟡"}
MAX_LISTED = 30


def digest(findings: list[dict]) -> str:
    pairs = sorted((str(f.get("fingerprint", "")), str(f.get("severity", ""))) for f in findings)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _hint(finding: dict) -> str:
    worker, rule = finding.get("worker"), github.clean_md(finding.get("rule_id") or "", 80)
    if worker == "semgrep":
        return f"Bỏ qua có lý do: `# nosemgrep: {rule}`."
    if worker == "gitleaks":
        return "Nếu là dương tính giả, thêm fingerprint vào `.gitleaksignore`."
    if worker == "trivy":
        return "Nếu là dương tính giả, thêm mục có lý do vào `.trivyignore`."
    return ""


def inline_body(finding: dict) -> str:
    severity = str(finding.get("severity", "low"))
    title = github.clean_md(finding.get("title") or "Finding", 200)
    worker = github.clean_md(finding.get("worker") or "worker", 80)
    rule = github.clean_md(finding.get("rule_id") or "", 80)
    return f"{ICON.get(severity, '⚪')} **{severity}** · {worker} · {rule}\n\n{title}\n\n{_hint(finding)}"[:2000]


def split_by_diff(findings: list[dict], diff_lines: dict[str, set[int]]) -> tuple[list[dict], list[dict]]:
    comments, outside = [], []
    for finding in findings:
        raw = finding.get("path")
        path = raw[2:] if isinstance(raw, str) and raw.startswith("./") else raw
        line = finding.get("line")
        if (isinstance(path, str) and isinstance(line, int) and not isinstance(line, bool) and line > 0
                and line in diff_lines.get(path, ()) and len(comments) < MAX_COMMENTS):
            comments.append({"path": path, "line": line, "side": "RIGHT", "body": inline_body(finding)})
        else:
            outside.append(finding)
    return comments, outside


def render_body(findings: list[dict], outside: list[dict], inline: int, digest_hex: str) -> str:
    counts = {level: sum(f.get("severity") == level for f in findings) for level in ICON}
    lines = ["### qc-agent · Review", "",
             f"Critical: {counts['critical']} · Medium: {counts['medium']} · Low: {counts['low']}",
             f"{inline} finding gắn đúng dòng; {len(outside)} finding ngoài diff hoặc không có vị trí."]
    for level in ICON:
        mine = [f for f in outside if f.get("severity") == level]
        if not mine:
            continue
        lines += ["", f"**{level.title()}**"]
        for finding in mine[:MAX_LISTED]:
            title = github.clean_md(finding.get("title") or "Finding", 160)
            worker = github.clean_md(finding.get("worker") or "worker", 60)
            where = github.clean_md(finding.get("path") or "không có vị trí", 120)
            if finding.get("line"):
                where += ":" + str(finding["line"])
            lines.append(f"- {ICON[level]} {worker}: {title} @ {where}")
        if len(mine) > MAX_LISTED:
            lines.append(f"- … và {len(mine) - MAX_LISTED} finding nữa trong artifact")
    lines += ["", "Review chỉ để đọc; verdict của gate quyết định PR có bị chặn hay không.", "",
              MARKER.format(digest=digest_hex)]
    return "\n".join(lines)[:github.MAX_COMMENT]


def already_posted(client: github.GitHubClient, repo: str, pr_number: int, digest_hex: str) -> bool:
    marker = MARKER.format(digest=digest_hex)
    for page in range(1, MAX_PAGES + 1):
        _, reviews = client.request("GET", f"/repos/{repo}/pulls/{pr_number}/reviews?per_page=100&page={page}")
        if any(marker in (review.get("body") or "") and (review.get("user") or {}).get("type") == "Bot"
               for review in reviews or []):
            return True
        if not reviews or len(reviews) < 100:
            break
    return False


def post_pr_review(run_dir, *, env: dict, ctx: dict) -> dict:
    try:
        report = json.loads((Path(run_dir) / "report.json").read_text(encoding="utf-8"))
        findings = report.get("findings") or []
        if not findings:
            return {"review": "skipped: không có finding"}
        if not (ctx.get("repo") and ctx.get("pr_number") and ctx.get("sha") and env.get("GITHUB_TOKEN")):
            return {"review": "skipped: thiếu ngữ cảnh pull_request hoặc token"}
        github.validate_target(ctx["repo"], ctx["sha"])
        digest_hex = digest(findings)
        client = github.GitHubClient(env["GITHUB_TOKEN"], env.get("GITHUB_API_URL"))
        if already_posted(client, ctx["repo"], ctx["pr_number"], digest_hex):
            event(log, "review.skipped", reason="duplicate", findings=len(findings))
            return {"review": "skipped: đã đăng", "sha256": digest_hex}
        comments, outside = split_by_diff(findings, pr_diff_lines(client, ctx["repo"], ctx["pr_number"]))
        body = render_body(findings, outside, len(comments), digest_hex)
        client.request("POST", f"/repos/{ctx['repo']}/pulls/{ctx['pr_number']}/reviews",
                       {"commit_id": ctx["sha"], "event": "COMMENT", "body": body, "comments": comments})
        event(log, "review.posted", comments=len(comments), outside_diff=len(outside))
        return {"review": "created", "comments": len(comments), "outside_diff": len(outside), "sha256": digest_hex}
    except (OSError, ValueError, TypeError, github.GitHubError) as error:
        event(log, "review.skipped", reason=type(error).__name__)
        return {"review": "error: " + type(error).__name__}


def main(argv: list[str] | None = None) -> int:
    configure()   # điểm vào riêng (`python -m`): không cấu hình thì event review.*/jira.sync bị logger nuốt, CI không thấy
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    result = post_pr_review(args.run_dir, env=os.environ, ctx=ci.context_from_env())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
