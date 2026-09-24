"""Bước "báo cáo" sau khi gate chạy trong GitHub Actions: đẩy lịch sử lên service, Check Run, comment PR dính, webhook.

    python -m qc_agent.integrations.ci --run-dir runs/r-0001 --project noteboard --mode pr --exit-code 1

Đọc ngữ cảnh từ môi trường của Actions: GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_EVENT_PATH (số PR, head sha, nhánh), GITHUB_RUN_ID/ATTEMPT,
GITHUB_SERVER_URL, GITHUB_TOKEN. Tuỳ chọn: QC_API_URL + QC_API_TOKEN (lịch sử tập trung), ALERT_WEBHOOK_URL (+ DASHBOARD_URL).

Mỗi việc chạy ĐỘC LẬP và chỉ khi đủ dữ liệu (không có PR thì không comment...). CHỈ GHI NHẬN: mọi lỗi được ghi vào kết quả JSON và
LUÔN exit 0 để không đổi verdict của job; việc chặn merge do exit code của bước gate quyết định. Token/URL bí mật không vào output."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from qc_agent.integrations import github, ingest_client, notify


def context_from_env(env=None) -> dict:
    env = os.environ if env is None else env
    event = {}
    path = env.get("GITHUB_EVENT_PATH")
    if path:
        try:
            event = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            event = {}
    pr = event.get("pull_request") or {}
    number = pr.get("number") if isinstance(pr.get("number"), int) else None
    run_id, attempt = env.get("GITHUB_RUN_ID"), env.get("GITHUB_RUN_ATTEMPT") or "1"
    server, repo = (env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/"), env.get("GITHUB_REPOSITORY")
    return {
        "repo": repo,
        # pull_request: GITHUB_SHA là merge commit; Check Run phải gắn vào HEAD của PR mới hiện trên PR
        "sha": (pr.get("head") or {}).get("sha") or env.get("GITHUB_SHA"),
        "pr_number": number,
        "branch": (pr.get("head") or {}).get("ref") or env.get("GITHUB_REF_NAME"),
        "external_id": f"gh-{run_id}-{attempt}" if run_id else None,
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None,
    }


def report_run(run_dir, *, project: str, mode: str, exit_code: int | None, env=None) -> dict:
    env = os.environ if env is None else env
    ctx = context_from_env(env)
    out: dict = {}
    run = notify.load_run(run_dir)
    if run is None:
        return {"error": "không đọc được report.json", "ingest": "skipped", "check_run": "skipped", "comment": "skipped", "notify": "skipped"}
    link = ctx["run_url"]

    # 1) lịch sử tập trung (trước để có link tới job cho các thông báo sau)
    api_url, api_token = env.get("QC_API_URL"), env.get("QC_API_TOKEN")
    if api_url and api_token and ctx["external_id"]:
        result = ingest_client.push_run(api_url, api_token, project, run_dir, external_id=ctx["external_id"], mode=mode,
                                        pr_number=ctx["pr_number"], sha=ctx["sha"], branch=ctx["branch"])
        out["ingest"] = result
        base = (env.get("DASHBOARD_URL") or api_url).rstrip("/")
        if result.get("ok") and result.get("job_id"):
            link = f"{base}/#project={project}&job={result['job_id']}"
    else:
        out["ingest"] = "skipped: cần QC_API_URL, QC_API_TOKEN và GITHUB_RUN_ID"

    # 2) Check Run + comment PR
    token = env.get("GITHUB_TOKEN")
    verdict = str(run["report"].get("gate_verdict", "UNKNOWN"))
    summary = github.render_summary(run, project=project, mode=mode, exit_code=exit_code, sut_sha=ctx["sha"], link=link)
    if token and ctx["repo"]:
        try:
            client = github.GitHubClient(token, env.get("GITHUB_API_URL"))
        except ValueError as error:
            client, out["check_run"], out["comment"] = None, f"error: {error}", "skipped"
        if client is not None:
            if ctx["sha"]:
                try:
                    out["check_run"] = {"id": client.create_check_run(
                        ctx["repo"], ctx["sha"], name=f"qc-agent / {project}", conclusion=github.conclusion_for(verdict, exit_code),
                        title=f"{verdict} — gate {sum(1 for g in run['report'].get('deterministic_view') or [] if isinstance(g, dict) and g.get('status') == 'pass')}/"
                              f"{len(run['report'].get('deterministic_view') or [])}", summary=summary, details_url=link)}
                except (github.GitHubError, ValueError) as error:
                    out["check_run"] = f"error: {error}"
            else:
                out["check_run"] = "skipped: không có sha"
            if ctx["pr_number"]:
                try:
                    body = github.render_comment(run, project=project, mode=mode, exit_code=exit_code, sut_sha=ctx["sha"], link=link)
                    out["comment"] = client.upsert_comment(ctx["repo"], ctx["pr_number"], body, github.marker_for(project, mode))
                except (github.GitHubError, ValueError) as error:
                    out["comment"] = f"error: {error}"
            else:
                out["comment"] = "skipped: không phải pull_request"
    else:
        out["check_run"] = out["comment"] = "skipped: cần GITHUB_TOKEN và GITHUB_REPOSITORY"

    # 3) webhook
    if env.get("ALERT_WEBHOOK_URL"):
        label = f"{ctx['repo'] or project}{'#' + str(ctx['pr_number']) if ctx['pr_number'] else ''} · {mode}"
        result = notify.notify_run(run_dir, exit_code=exit_code, label=label, link=link, url=env["ALERT_WEBHOOK_URL"])
        out["notify"] = {k: v for k, v in result.items() if k in ("ok", "channel", "status", "error")}
    else:
        out["notify"] = "skipped: không có ALERT_WEBHOOK_URL"
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--exit-code", type=int)
    args = ap.parse_args(argv)
    try:
        result = report_run(args.run_dir, project=args.project, mode=args.mode, exit_code=args.exit_code)
    except Exception as error:  # noqa: BLE001 — báo cáo hỏng không được làm đỏ/xanh job
        result = {"error": f"{type(error).__name__}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
