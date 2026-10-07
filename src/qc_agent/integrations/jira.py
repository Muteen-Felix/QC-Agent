"""Đồng bộ finding Low sang Jira Cloud; mọi lỗi tích hợp đều giữ nguyên verdict."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx

from qc_agent.core import egress, project
from qc_agent import settings
from qc_agent.logging_setup import configure, event

log = logging.getLogger("qc_agent.jira")
_PROJECT = re.compile(r"[A-Z][A-Z0-9_]{1,29}")
_FINGERPRINT = re.compile(r"[a-f0-9]{16,64}")


def _text(value: object, limit: int = 180) -> str:
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())[:limit]


def _description(finding: dict, ctx: dict, assignee_missing: bool) -> dict:
    where = _text(finding.get("path"), 200)
    if where and finding.get("line"):
        where += f":{finding['line']}"
    lines = [
        _text(finding.get("title"), 200),
        "Rule: " + _text(finding.get("rule_id"), 100),
        "Vị trí: " + where,
        "PR: " + _text(ctx.get("pr_url"), 300),
        "Run: " + _text(ctx.get("run_url"), 300),
        "Fingerprint: " + str(finding["fingerprint"]),
    ]
    if assignee_missing:
        lines.append("Không map được tác giả PR: " + _text(ctx.get("author"), 100))
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines if line.strip()
    ]}


def sync_low_findings(findings: list[dict], *, cfg: dict, ctx: dict, env: dict,
                      egress_dir: Path, transport: httpx.BaseTransport | None = None,
                      egress_policy: egress.EgressPolicy | None = None) -> dict:
    """Tra label trước khi tạo; không raise vì Jira không được ảnh hưởng gate."""
    jira = cfg.get("jira", cfg)
    low = {f.get("fingerprint"): f for f in findings if f.get("severity") == "low"
           and isinstance(f.get("fingerprint"), str) and _FINGERPRINT.fullmatch(f["fingerprint"])}
    result = {"jira": "skipped", "created": 0, "duplicates": 0, "remaining": 0}
    if not low:
        result["jira"] = "skipped: không có finding Low"
        return result
    key, base = jira.get("project_key"), (env.get("JIRA_BASE_URL") or "").rstrip("/")
    email, token = env.get("JIRA_EMAIL"), env.get("JIRA_API_TOKEN")
    try:
        parsed = urlsplit(base)
        secure_base = (parsed.scheme == "https" and bool(parsed.netloc)) or (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"})
    except ValueError:
        secure_base = False
    if not (isinstance(key, str) and _PROJECT.fullmatch(key) and jira.get("issue_type") and
            base and email and token and secure_base):
        result["jira"] = "skipped: thiếu cấu hình Jira hoặc secret"
        return result
    limit = jira.get("max_new_per_run", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        result["jira"] = "error: cấu hình max_new_per_run"
        return result
    worker = SimpleNamespace(name="qc-agent-jira", data_egress=["finding_title", "code_location"])
    spec = {"task_id": "jira-low", "capability": "jira.issue", "target": {"base_url": base}}

    def request(client: httpx.Client, method: str, path: str, payload: dict) -> dict:
        decision = egress.record(egress_policy or egress.LogOnlyPolicy(), Path(egress_dir), spec, worker, 1)
        if decision.action != "allow":
            raise PermissionError("egress_denied")
        response = client.request(method, path, json=payload)
        if not response.is_success:
            raise RuntimeError(f"HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("bad_output")
        return data

    try:
        with httpx.Client(base_url=base, auth=(email, token), transport=transport, timeout=15) as client:
            labels = [f"qcagent-{fingerprint}" for fingerprint in sorted(low)]
            jql = f'project = {key} AND labels in (' + ", ".join(f'"{label}"' for label in labels) + ")"
            existing: set[str] = set()
            page_token = None
            seen_tokens = set()
            for _ in range(20):
                search = {"jql": jql, "fields": ["labels"], "maxResults": min(100, max(50, len(labels)))}
                if page_token:
                    search["nextPageToken"] = page_token
                found = request(client, "POST", "/rest/api/3/search/jql", search)
                existing.update(label for issue in found.get("issues", [])
                                for label in (issue.get("fields") or {}).get("labels", []))
                next_token = found.get("nextPageToken")
                if not next_token:
                    break
                if next_token in seen_tokens:
                    raise RuntimeError("search_pagination_cycle")
                seen_tokens.add(next_token)
                page_token = next_token
            else:
                raise RuntimeError("search_page_limit")
            user_map = jira.get("user_map") or {}
            author = ctx.get("author")
            assignee = user_map.get(author) if isinstance(user_map, dict) else None
            for fingerprint, finding in sorted(low.items()):
                label = f"qcagent-{fingerprint}"
                if label in existing:
                    result["duplicates"] += 1
                    continue
                if result["created"] >= limit:
                    result["remaining"] += 1
                    continue
                fields = {"project": {"key": key}, "issuetype": {"name": str(jira["issue_type"])},
                          "summary": _text(finding.get("title"), 200) or f"QC finding {fingerprint}",
                          "description": _description(finding, ctx, bool(author and not assignee)),
                          "labels": [label]}
                if assignee:
                    fields["assignee"] = {"accountId": assignee}
                request(client, "POST", "/rest/api/3/issue", {"fields": fields})
                result["created"] += 1
            result["jira"] = "ok"
    except (httpx.HTTPError, ValueError, RuntimeError, PermissionError) as error:
        result["jira"] = "error: " + (str(error) if isinstance(error, (RuntimeError, PermissionError)) else type(error).__name__)
    event(log, "jira.sync", created=result["created"], duplicates=result["duplicates"], remaining=result["remaining"],
          errors=int(str(result["jira"]).startswith("error:")), status=result["jira"])
    return result


def main(argv: list[str] | None = None) -> int:
    configure()   # điểm vào riêng (`python -m`): không cấu hình thì event review.*/jira.sync bị logger nuốt, CI không thấy
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        from qc_agent.integrations.ci import context_from_env
        report = json.loads((Path(args.run_dir) / "report.json").read_text(encoding="utf-8"))
        cfg = project.load_project(args.project, settings.get().resolved_projects_dir)
        result = sync_low_findings(report.get("findings") or [], cfg=cfg, ctx=context_from_env(),
                                   env=os.environ, egress_dir=Path(args.run_dir))
    except Exception as error:  # noqa: BLE001 — Jira lỗi không thay verdict
        result = {"jira": "error: " + type(error).__name__, "created": 0}
    try:
        (Path(args.run_dir) / "jira-status.json").write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
