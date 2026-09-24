"""Giao diện web: tệp tĩnh + CSP (test API), và E2E bằng Chromium thật (Playwright bằng Node). Cần QC_TEST_DATABASE_URL."""
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from qc_agent.api.app import create_app
from qc_agent.jobs.executor import Executor, ExecutorConfig
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.projkit import make_sut, task, write_suite
from tests.test_api import make_user, write_projects  # noqa: F401

pytestmark = requires_pg
ROOT = Path(__file__).resolve().parent.parent
PW = "correct horse battery"
XSS_INTENT = '<img src=x onerror="window.__xss=1">'


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "corp.test")
    monkeypatch.setenv("QC_COOKIE_SECURE", "false")


def _app(tmp_path, engine):
    sut = make_sut(tmp_path)
    return create_app(engine, runs_root=tmp_path / "runs", projects_dir=write_projects(tmp_path, sut)), sut


def test_static_files_are_served_with_a_strict_csp_and_api_routes_still_win(tmp_path, engine):
    app, _ = _app(tmp_path, engine)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200 and "text/html" in page.headers["content-type"] and 'id="root"' in page.text
        csp = page.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
        assert page.headers["x-frame-options"] == "DENY" and page.headers["referrer-policy"] == "same-origin"
        assert "<script>" not in page.text and " style=" not in page.text and "onclick" not in page.text  # không inline
        js, css = client.get("/app.js"), client.get("/style.css")
        assert "javascript" in js.headers["content-type"] and "innerHTML" not in js.text.replace("KHÔNG BAO GIỜ đưa vào innerHTML", "")
        assert "css" in css.headers["content-type"]
        assert client.get("/healthz").json() == {"status": "ok"} and client.get("/api/v1/auth/me").status_code == 401
        assert "content-security-policy" not in client.get("/api/v1/auth/me").headers  # API trả JSON: không cần CSP của trang
        assert client.get("/khong-co-trang-nay").status_code == 404


def test_web_source_never_writes_data_into_html(tmp_path):
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source.replace("KHÔNG BAO GIỜ đưa vào innerHTML", "") and "insertAdjacentHTML" not in source and "document.write" not in source
    assert "eval(" not in source and "new Function" not in source


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("node") is None or not (ROOT / "node_modules" / "playwright").is_dir(), reason="needs node + playwright")
def test_browser_end_to_end_flow(tmp_path, engine):
    sut = make_sut(tmp_path)
    write_suite(sut, "core", [task("t-1", intent=XSS_INTENT), task("t-2")])
    slow = task("t-slow", inputs={"fixture": "data/mock_ok.json", "sleep_s": 63.7}, budget={"wallclock_s": 300, "tokens": 0, "usd": 0})
    write_suite(sut, "slow", [slow])
    projects = write_projects(tmp_path, sut)
    make_user(engine, "alice@corp.test", PW)
    app = create_app(engine, runs_root=tmp_path / "runs", projects_dir=projects)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    web = threading.Thread(target=server.run, daemon=True)
    web.start()
    stop = threading.Event()
    executor = Executor(engine, ExecutorConfig(runs_root=tmp_path / "runs", projects_dir=projects, tick_s=0.1, heartbeat_interval_s=0.3,
                                               poll_interval_s=0.2, default_timeout_s=300, cancel_grace_s=5, max_concurrent=2))
    worker = threading.Thread(target=executor.serve, args=(stop,), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn không khởi động"
        env = {**os.environ, "BASE_URL": f"http://127.0.0.1:{port}", "EMAIL": "alice@corp.test", "PASSWORD": PW,
               "SHOT": str(tmp_path / "e2e-failure.png"), **({"SHOT_OK": os.environ["QC_WEB_SHOT"]} if os.environ.get("QC_WEB_SHOT") else {})}
        result = subprocess.run(["node", str(ROOT / "tests" / "web" / "e2e.mjs")], cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=240)
        if result.returncode == 77:
            pytest.skip("Chromium không khởi chạy được: " + result.stderr.strip().splitlines()[-1].encode("ascii", "replace").decode())
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "E2E OK" in result.stdout
    finally:
        stop.set()
        server.should_exit = True
        worker.join(30)
        web.join(15)
