"""Client ingest và đường đầu-cuối CI -> API thật (uvicorn + PostgreSQL) -> comment PR có link tới job."""
import json
import socket
import threading
import time
import uuid

import pytest
import uvicorn
from sqlalchemy import text

from qc_agent.api.app import create_app
from qc_agent.auth import service
from qc_agent.integrations import ci, ingest_client
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.fakes import FakeGitHub
from tests.projkit import make_sut
from tests.test_api import report, write_projects

TOKEN_GH = "ghs_SECRETTOKENVALUE123"


def make_valid_run(tmp_path, verdict="PASS", exit_code=0, name="r-0001"):
    run = tmp_path / name
    run.mkdir(parents=True)
    (run / "report.json").write_text(json.dumps(report(verdict, exit_code)), encoding="utf-8")
    (run / "report.md").write_text("# QC Gate Report", encoding="utf-8")
    return run


# ---- không cần DB ----

@pytest.mark.parametrize("kwargs,fragment", [
    (dict(api_url="http://127.0.0.1:1", token="", project="demo"), "QC_API_TOKEN"),
    (dict(api_url="file:///x", token="t", project="demo"), "http(s)"),
    (dict(api_url="", token="t", project="demo"), "http(s)"),
    (dict(api_url="http://127.0.0.1:1", token="t", project="../etc"), "project"),
    (dict(api_url="http://127.0.0.1:1", token="t", project="Demo"), "project"),
])
def test_push_run_rejects_bad_inputs_without_touching_the_network(tmp_path, kwargs, fragment):
    run = make_valid_run(tmp_path)
    result = ingest_client.push_run(kwargs["api_url"], kwargs["token"], kwargs["project"], run, external_id="gh-1-1", mode="pr")
    assert result["ok"] is False and fragment in result["error"]


def test_push_run_missing_report_and_dead_server_do_not_raise_or_leak(tmp_path):
    assert "report.json" in ingest_client.push_run("http://127.0.0.1:1", "qca_SECRET", "demo", tmp_path / "khong-co", external_id="x", mode="pr")["error"]
    result = ingest_client.push_run("http://127.0.0.1:1", "qca_SECRET", "demo", make_valid_run(tmp_path), external_id="gh-1-1", mode="pr")
    assert result["ok"] is False and "SECRET" not in json.dumps(result) and "127.0.0.1" not in json.dumps(result)


# ---- API thật ----

@pytest.fixture
def live(tmp_path, engine, monkeypatch):
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "corp.test")
    monkeypatch.setenv("QC_COOKIE_SECURE", "false")
    sut = make_sut(tmp_path)
    app = create_app(engine, runs_root=tmp_path / "runs", projects_dir=write_projects(tmp_path, sut))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    with session_scope(engine) as s:
        _, raw = service.create_api_token(s, "demo", "ci")
    yield type("Live", (), {"url": f"http://127.0.0.1:{port}", "token": raw, "engine": engine, "tmp": tmp_path})
    server.should_exit = True
    thread.join(15)


@requires_pg
def test_push_run_creates_the_job_once_and_is_idempotent(live):
    run = make_valid_run(live.tmp)
    first = ingest_client.push_run(live.url, live.token, "demo", run, external_id="gh-42-1", mode="pr", pr_number=7, sha="abc1234", branch="feat/x")
    again = ingest_client.push_run(live.url, live.token, "demo", run, external_id="gh-42-1", mode="pr", pr_number=7, sha="abc1234", branch="feat/x")
    assert first["ok"] and first["status"] == 201 and first["created"] is True
    assert again["ok"] and again["status"] == 200 and again["created"] is False and again["job_id"] == first["job_id"]
    with session_scope(live.engine) as s:
        job = repo.get_job(s, uuid.UUID(first["job_id"]))
        assert (job.source, job.mode, job.pr_number, job.sha, job.branch, job.gate_verdict, job.status) == ("ci", "pr", 7, "abc1234", "feat/x", "PASS", "succeeded")
        assert s.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 1


@requires_pg
def test_service_rejection_is_reported_without_the_token(live):
    run = make_valid_run(live.tmp)
    result = ingest_client.push_run(live.url, "qca_token-sai", "demo", run, external_id="gh-1-1", mode="pr")
    assert result["ok"] is False and result["status"] == 401 and "qca_token-sai" not in json.dumps(result)
    result = ingest_client.push_run(live.url, live.token, "khong-co-project", run, external_id="gh-1-1", mode="pr")
    assert result["ok"] is False and result["status"] == 404


@requires_pg
def test_end_to_end_ci_step_ingests_then_comments_with_a_link_to_the_job(live):
    run = make_valid_run(live.tmp, "FAIL", 1)
    event = live.tmp / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": "abc1234def", "ref": "feat/x"}}}), encoding="utf-8")
    with FakeGitHub() as gh:
        env = {"GITHUB_TOKEN": TOKEN_GH, "GITHUB_API_URL": gh.url, "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "merge999", "GITHUB_EVENT_PATH": str(event),
               "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "1", "QC_API_URL": live.url, "QC_API_TOKEN": live.token}
        first = ci.report_run(run, project="demo", mode="pr", exit_code=1, env=env)
        again = ci.report_run(run, project="demo", mode="pr", exit_code=1, env=env)  # workflow chạy lại (re-run cùng attempt)
    assert first["ingest"]["ok"] and first["ingest"]["created"] is True and again["ingest"]["created"] is False
    job_id = first["ingest"]["job_id"]
    body = gh.comments[0]["body"]
    assert f"[chi tiết]({live.url}/#project=demo&job={job_id})" in body  # link tới job vừa ghi vào lịch sử
    assert gh.check_runs[0]["details_url"] == f"{live.url}/#project=demo&job={job_id}"
    assert gh.check_runs[0]["conclusion"] == "failure" and len(gh.comments) == 1
    with session_scope(live.engine) as s:
        job = repo.get_job(s, uuid.UUID(job_id))
        assert job.external_id == "gh-77-1" and job.gate_verdict == "FAIL" and job.status == "failed"
    assert TOKEN_GH not in json.dumps(first) and live.token not in json.dumps(first)


@requires_pg
def test_a_rejected_ingest_does_not_stop_the_github_report(live):
    run = make_valid_run(live.tmp)
    with FakeGitHub() as gh:
        env = {"GITHUB_TOKEN": TOKEN_GH, "GITHUB_API_URL": gh.url, "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "abc1234", "GITHUB_RUN_ID": "1",
               "QC_API_URL": live.url, "QC_API_TOKEN": "qca_sai"}
        result = ci.report_run(run, project="demo", mode="manual", exit_code=0, env=env)
    assert result["ingest"]["ok"] is False and result["ingest"]["status"] == 401
    assert result["check_run"]["id"] and gh.check_runs[0]["conclusion"] == "success"  # Check Run vẫn được tạo, link rơi về run của Actions
    assert "actions/runs/1" in gh.check_runs[0]["details_url"]
