"""Chuỗi review, Check Run và Jira trên máy chủ giả; không cần database."""
import json

import pytest

from qc_agent.core.findings import normalize
from qc_agent.core.verdict import gate_verdict
from qc_agent.integrations import ci, jira, pr_review
from tests.fakes import FakeGitHub, FakeJira


@pytest.mark.parametrize("worker,rule,severity", [
    ("schemathesis", "http_5xx", "critical"),
    ("semgrep", "WARNING", "medium"),
])
def test_blocking_finding_reaches_failed_check_and_inline_review(tmp_path, worker, rule, severity):
    run = tmp_path / "r-0001"
    run.mkdir()
    specs = {"t": {"lane": "gate"}}
    results = {"t": {"status": "fail", "worker": {"name": worker},
                     "verdict": {"gating": True, "value": "fail", "verdict_source": "deterministic_assert"},
                     "findings": [{"title": "Regression", "detected_by": f"{worker}:{rule}",
                                   "verdict_source": "deterministic_assert", "severity_hint": severity,
                                   "location": {"path": "toyapp/app.py", "line": 5}}]}}
    findings, blockers = normalize(results, specs)
    gate = gate_verdict(findings, blockers)
    assert (gate.value, gate.exit_code) == ("BLOCKED", 1)
    report = {"gate_verdict": gate.value, "exit_code": gate.exit_code, "severity_counts": gate.counts,
              "findings": [f.to_dict() for f in findings], "deterministic_view": [],
              "banner": [], "details": {"results": {}}}
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    ctx = {"repo": "org/noteboard", "pr_number": 7, "sha": "a" * 40}
    with FakeGitHub() as gh:
        gh.pr_files = [{"filename": "toyapp/app.py", "patch": "@@ -5,0 +5,1 @@\n+regression"}]
        outcome = pr_review.post_pr_review(run, env={"GITHUB_TOKEN": "fake", "GITHUB_API_URL": gh.url}, ctx=ctx)
        assert outcome["comments"] == 1 and gh.reviews[0]["comments"][0]["line"] == 5
    assert ci.github.conclusion_for(gate.value, gate.exit_code) == "failure"


def test_low_finding_reviews_and_ticket_once(tmp_path):
    finding = {"fingerprint": "0123456789abcdef", "severity": "low", "title": "Thiếu test route",
               "worker": "coverage-debt", "rule_id": "api_endpoint", "path": "toyapp/app.py", "line": 5}
    run = tmp_path / "r-0001"
    run.mkdir()
    report = {"gate_verdict": "PASSED_WITH_WARNINGS", "exit_code": 0, "severity_counts": {"critical": 0, "medium": 0, "low": 1},
              "findings": [finding], "deterministic_view": [], "banner": [], "details": {"results": {}}}
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    ctx = {"repo": "org/noteboard", "pr_number": 7, "sha": "a" * 40, "author": "dev",
           "pr_url": "https://github.com/org/noteboard/pull/7", "run_url": "https://github.com/org/noteboard/actions/runs/42"}
    with FakeGitHub() as gh, FakeJira() as server:
        gh.pr_files = [{"filename": "toyapp/app.py", "patch": "@@ -5,0 +5,1 @@\n+@app.get('/notes')"}]
        review_env = {"GITHUB_TOKEN": "fake", "GITHUB_API_URL": gh.url}
        cfg = {"jira": {"project_key": "QCSB", "issue_type": "Task", "user_map": {"dev": "account-123"}}}
        jira_env = {"JIRA_BASE_URL": server.url, "JIRA_EMAIL": "fake@example.invalid", "JIRA_API_TOKEN": "fake"}
        first = pr_review.post_pr_review(run, env=review_env, ctx=ctx)
        again = pr_review.post_pr_review(run, env=review_env, ctx=ctx)
        ticket = jira.sync_low_findings([finding], cfg=cfg, ctx=ctx, env=jira_env, egress_dir=run)
        repeat = jira.sync_low_findings([finding], cfg=cfg, ctx=ctx, env=jira_env, egress_dir=run)
        assert first["review"] == "created" and again["review"].startswith("skipped")
        assert len(gh.reviews) == 1 and gh.reviews[0]["comments"][0]["line"] == 5
        assert ticket["created"] == 1 and repeat["duplicates"] == 1 and len(server.issues) == 1
        assert server.issues[0]["fields"]["assignee"] == {"accountId": "account-123"}
        assert ci.github.conclusion_for(report["gate_verdict"], report["exit_code"]) == "success"
        assert ci.github.check_title({"report": report}) == "✅ PASS · 1 cảnh báo Low"


def test_jira_failure_keeps_gate_verdict(tmp_path):
    finding = {"fingerprint": "0123456789abcdef", "severity": "low", "title": "Low"}
    cfg = {"jira": {"project_key": "QCSB", "issue_type": "Task"}}
    with FakeJira() as server:
        server.forced_status = 503
        result = jira.sync_low_findings([finding], cfg=cfg, ctx={},
                                        env={"JIRA_BASE_URL": server.url, "JIRA_EMAIL": "fake", "JIRA_API_TOKEN": "fake"},
                                        egress_dir=tmp_path)
    assert result["jira"] == "error: HTTP 503"
    assert ci.github.conclusion_for("PASSED_WITH_WARNINGS", 0) == "success"
    summary = ci.github.render_summary({"report": {"gate_verdict": "PASSED_WITH_WARNINGS", "jira_warning": result["jira"]}},
                                       project="demo", mode="pr", exit_code=0)
    assert "Jira: error: HTTP 503" in summary


def test_jira_401_and_503_are_visible_in_check_run(tmp_path):
    run = tmp_path / "r-0001"
    run.mkdir()
    finding = {"fingerprint": "0123456789abcdef", "severity": "low", "title": "Missing test"}
    report = {"gate_verdict": "PASSED_WITH_WARNINGS", "exit_code": 0,
              "severity_counts": {"critical": 0, "medium": 0, "low": 1}, "findings": [finding],
              "deterministic_view": [], "banner": [], "details": {"results": {}}}
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    env = {"GITHUB_TOKEN": "fake", "GITHUB_REPOSITORY": "org/noteboard", "GITHUB_SHA": "a" * 40,
           "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
    cfg = {"jira": {"project_key": "QCSB", "issue_type": "Task"}}
    with FakeGitHub() as gh, FakeJira() as server:
        env["GITHUB_API_URL"] = gh.url
        jira_env = {"JIRA_BASE_URL": server.url, "JIRA_EMAIL": "fake", "JIRA_API_TOKEN": "fake"}
        for status in (401, 503):
            server.forced_status = status
            sync = jira.sync_low_findings([finding], cfg=cfg, ctx={}, env=jira_env, egress_dir=run)
            (run / "jira-status.json").write_text(json.dumps(sync), encoding="utf-8")
            result = ci.report_run(run, project="noteboard", mode="pr", exit_code=0, env=env)
            assert result["jira_warning"] == f"error: HTTP {status}"
            assert gh.check_runs[-1]["conclusion"] == "success"
            assert f"Jira: error: HTTP {status}" in gh.check_runs[-1]["output"]["summary"]
