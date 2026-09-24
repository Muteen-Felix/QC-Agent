"""Chạy các bước shell của workflow tái sử dụng trên Docker cục bộ (tools/run_reusable_locally.py): build SUT thật, chạy gate trong image,
báo cáo vào GitHub GIẢ và ghi lịch sử vào API THẬT (PostgreSQL). CHỈ chạy khi có docker + QC_TEST_DOCKER_IMAGE (tên image qc-agent đã build,
vd. qc-agent:dev) + QC_TEST_DATABASE_URL. Chậm (build SUT + hai lần chạy gate thật): dùng để kiểm chứng workflow, không chạy mặc định."""
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import text

from qc_agent.api.app import create_app
from qc_agent.auth import service
from qc_agent.jobs.db import session_scope
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.fakes import FakeGitHub

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_reusable_locally as harness  # noqa: E402

IMAGE = os.environ.get("QC_TEST_DOCKER_IMAGE", "")
HOST = os.environ.get("QC_TEST_DOCKER_HOST", "host.docker.internal")  # địa chỉ của máy chủ nhìn từ container
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
pytestmark = [requires_pg, pytest.mark.skipif(not IMAGE or shutil.which("docker") is None, reason="needs docker + QC_TEST_DOCKER_IMAGE")]
GITHUB_TOKEN = "ghs_local_test_token"


def free_port():
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


@pytest.fixture
def stack(tmp_path, engine, monkeypatch):
    monkeypatch.setenv("QC_COOKIE_SECURE", "false")
    from tests.projkit import make_sut
    from tests.test_api import write_projects
    projects = write_projects(tmp_path, make_sut(tmp_path))
    text_ = (projects / "demo.yaml").read_text(encoding="utf-8").replace("slug: demo", "slug: noteboard")
    (projects / "noteboard.yaml").write_text(text_, encoding="utf-8")
    (projects / "demo.yaml").unlink()
    app = create_app(engine, runs_root=tmp_path / "runs", projects_dir=projects)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    with session_scope(engine) as s:
        _, token = service.create_api_token(s, "noteboard", "ci")
    with FakeGitHub(host="0.0.0.0") as gh:
        yield type("Stack", (), {"gh": gh, "gh_url": f"http://{HOST}:{gh.server.server_port}", "api_url": f"http://{HOST}:{port}",
                                 "token": token, "engine": engine})
    server.should_exit = True
    thread.join(15)


def run(stack, *, bugs: str, run_id: str):
    github = {"token": GITHUB_TOKEN, "sha": "mergecommit0000", "actor": "tester", "run_attempt": "1",
              "event": {"pull_request": {"number": 7, "head": {"sha": "abc1234def5678"}}}, "env": {}}
    event = Path(os.environ.get("TEMP", "/tmp")) / f"qc-event-{uuid.uuid4().hex}.json"
    event.write_text('{"pull_request": {"number": 7, "head": {"sha": "abc1234def5678", "ref": "feat/x"}}}', encoding="utf-8")
    github["env"] = {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "mergecommit0000", "GITHUB_RUN_ID": run_id,
                     "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_API_URL": stack.gh_url, "GITHUB_REF_NAME": "feat/x"}
    logs = []
    inputs = {"project": "noteboard", "image": IMAGE, "allow_unpinned_image": "true", "sut_env": f"QC_BUGS={bugs}", "qc_api_url": stack.api_url}
    results = harness.run_workflow(harness.DEFAULT_WORKFLOW, SUT, inputs, {"QC_API_TOKEN": stack.token}, github, echo=logs.append)
    shutil.rmtree(SUT / "runs", ignore_errors=True)
    return results, "\n".join(logs)


def test_pr_flow_pass_then_fail_with_sticky_comment_check_runs_and_history(stack):
    ok, log = run(stack, bugs="none", run_id="2001")
    assert ok["Run qc-agent gate"]["outputs"] == {"exit_code": "0"}, log
    assert ok["Enforce gate result"]["returncode"] == 0, log  # job xanh theo exit code của gate
    assert len(stack.gh.check_runs) == 1 and stack.gh.check_runs[0]["conclusion"] == "success"
    assert stack.gh.check_runs[0]["name"] == "qc-agent / noteboard" and stack.gh.check_runs[0]["head_sha"] == "abc1234def5678"
    assert len(stack.gh.comments) == 1 and "PASS" in stack.gh.comments[0]["body"] and "gate 2/2" in stack.gh.comments[0]["body"]
    assert all(r["auth"] == f"Bearer {GITHUB_TOKEN}" for r in stack.gh.requests)

    bad, log = run(stack, bugs="1,3", run_id="2002")  # push tiếp vào PR có lỗi cài sẵn
    assert bad["Run qc-agent gate"]["outputs"] == {"exit_code": "1"}, log
    assert bad["Enforce gate result"]["returncode"] == 1, log  # job đỏ
    assert len(stack.gh.comments) == 1, "comment phải được cập nhật tại chỗ, không tạo thêm"
    body = stack.gh.comments[0]["body"]
    assert "FAIL" in body and "gate 0/2" in body and "schemathesis" in body and "deepeval" in body
    assert [c["conclusion"] for c in stack.gh.check_runs] == ["success", "failure"]

    with session_scope(stack.engine) as s:
        rows = s.execute(text("SELECT id, external_id, gate_verdict, status, pr_number, sha, source FROM jobs ORDER BY created_at")).all()
    assert [tuple(r)[1:] for r in rows] == [("gh-2001-1", "PASS", "succeeded", 7, "abc1234def5678", "ci"),
                                            ("gh-2002-1", "FAIL", "failed", 7, "abc1234def5678", "ci")]
    assert f"[chi tiết]({stack.api_url}/#project=noteboard&job={rows[1][0]})" in body  # link tới job của lần chạy MỚI NHẤT trong lịch sử
    assert stack.gh.check_runs[1]["details_url"].endswith(f"job={rows[1][0]}")
    assert GITHUB_TOKEN not in log and stack.token not in log  # bí mật không lọt vào log của các bước


def test_unpinned_image_is_refused_by_default(stack):
    github = {"token": GITHUB_TOKEN, "sha": "m", "actor": "t", "run_attempt": "1", "event": {}, "env": {}}
    logs = []
    results = harness.run_workflow(harness.DEFAULT_WORKFLOW, SUT, {"project": "noteboard", "image": "qc-agent:dev"}, {}, github,
                                   skip=(), echo=logs.append)
    assert results["Pull qc-agent image"]["returncode"] != 0 and "ghim theo digest" in "\n".join(logs)
    assert "Enforce gate result" in results and results["Enforce gate result"]["returncode"] == 1  # lỗi cấu hình => job đỏ, không xanh nhầm
