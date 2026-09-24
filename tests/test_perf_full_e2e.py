"""perf-full THẬT (k6 + toyapp thật + PostgreSQL + executor + cấu hình project THẬT configs/projects/noteboard.yaml).

Kiểm hai điều chưa được kiểm bằng worker giả:
  1. hai job perf-full cùng môi trường staging chạy TUẦN TỰ (khoảng thời gian chạy không chồng nhau) nhờ concurrency_key của cấu hình;
  2. khi SUT chậm (QC_LATENCY_MS=400) thì ngưỡng p95 < 300ms bị vi phạm: job `failed`, gate FAIL (không phải error).
Chậm (~70s, 2 lần k6 30s) và cần k6 + PostgreSQL."""
import importlib
import shutil
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from qc_agent.api.app import create_app
from qc_agent.auth import service
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope
from qc_agent.jobs.executor import Executor, ExecutorConfig
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
pytestmark = [requires_pg, pytest.mark.skipif(shutil.which("k6") is None, reason="needs k6")]


def serve_toyapp(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(str(SUT))
    for name in [m for m in sys.modules if m.startswith("toyapp")]:
        del sys.modules[name]
    app = importlib.import_module("toyapp.app").app
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
    return server, thread, f"http://127.0.0.1:{port}"


def test_two_perf_full_jobs_on_staging_run_one_at_a_time_and_a_slow_sut_fails_the_threshold(tmp_path, engine, monkeypatch):
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "corp.test")
    monkeypatch.setenv("QC_COOKIE_SECURE", "false")
    server, thread, url = serve_toyapp(monkeypatch, QC_BUGS="none", QC_LATENCY_MS="400")
    monkeypatch.setenv("NOTEBOARD_STAGING_URL", url)  # bí mật của server: địa chỉ staging
    runs, projects = tmp_path / "runs", ROOT / "configs" / "projects"
    try:
        with session_scope(engine) as s:
            service.create_user(s, "alice@corp.test", "correct horse battery")
        app = create_app(engine, runs_root=runs, projects_dir=projects)
        with TestClient(app, base_url="http://testserver") as client:
            assert client.post("/api/v1/auth/login", json={"email": "alice@corp.test", "password": "correct horse battery"}).status_code == 200
            ids = [client.post("/api/v1/projects/noteboard/jobs",
                               json={"mode": "manual", "suites": ["perf-full"], "environment": "staging"}).json()["id"] for _ in range(2)]
            with session_scope(engine) as s:
                assert {repo.get_job(s, uuid.UUID(i)).params["concurrency_key"] for i in ids} == {"env:noteboard-staging"}  # cùng khoá, từ cấu hình

            def work():
                executor = Executor(engine, ExecutorConfig(runs_root=runs, projects_dir=projects, tick_s=0.1, heartbeat_interval_s=0.5,
                                                           default_timeout_s=300, cancel_grace_s=5, poll_interval_s=0.3))
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    with session_scope(engine) as s:
                        if all(repo.get_job(s, uuid.UUID(i)).finished_at for i in ids):
                            return
                    executor.run_once() or time.sleep(0.3)

            workers = [threading.Thread(target=work) for _ in range(2)]  # hai executor tranh nhau, như hai tiến trình
            for w in workers:
                w.start()
            for w in workers:
                w.join(260)
            with session_scope(engine) as s:
                jobs = sorted((repo.get_job(s, uuid.UUID(i)) for i in ids), key=lambda j: j.started_at)
                spans = [(j.started_at, j.finished_at, j.status, j.gate_verdict, j.error) for j in jobs]
            (first, second) = spans
            assert first[1] and second[1], spans
            assert first[1] <= second[0], f"hai job perf-full chồng thời gian: {spans}"  # tuần tự
            for _, _, status, verdict, error in spans:
                assert (status, verdict) == ("failed", "FAIL"), (status, verdict, error)  # ngưỡng p95 < 300ms bị vi phạm khi SUT chậm 400ms
            with session_scope(engine) as s:
                for i in ids:
                    (row,) = repo.get_job(s, uuid.UUID(i)).tasks
                    assert (row.task_id, row.worker, row.status, row.gating) == ("t-002", "k6", "fail", True)
                    assert row.summary["metrics"]["http_req_duration.p95"] > 300, row.summary  # số đo thật từ k6 vượt ngưỡng
    finally:
        server.should_exit = True
        thread.join(15)
