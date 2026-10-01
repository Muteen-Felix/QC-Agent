"""Negative network tests with real Playwright/Chromium (Plan B-8)."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from qc_agent.scaffold import suites_integration

ROOT = Path(__file__).parents[1]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _run(port: int, *, har: bool, fallback: bool = False, recorded: bool = False) -> dict:
    if shutil.which("node") is None or not (ROOT / "node_modules" / "@playwright" / "test").is_dir():
        pytest.skip("needs npm ci with @playwright/test")
    directory = Path(tempfile.mkdtemp(prefix="pw-guard-", dir=ROOT / "tests"))
    try:
        support = suites_integration.support()
        if fallback:
            support = support.replace("notFound: 'abort'", "notFound: 'fallback'")
        (directory / "support.mjs").write_text(support, encoding="utf-8")
        target = f"http://localhost:{port}/missing"
        (directory / "guard.spec.mjs").write_text(
            "import { test, expect } from './support.mjs';\n"
            f"test('network_attempt', async ({{ page }}) => {{ await page.goto('http://127.0.0.1:{port}/'); "
            f"const ok = await page.evaluate(async () => {{ try {{ await fetch('{target}', {{ mode: 'no-cors' }}); return true; }} catch {{ return false; }} }}); "
            f"expect(ok).toBe({'true' if fallback or recorded else 'false'}); }});\n", encoding="utf-8")
        har_path = directory / "empty.har"
        entries = [{"startedDateTime": "2026-01-01T00:00:00.000Z", "time": 1,
                    "request": {"method": "GET", "url": target, "httpVersion": "HTTP/1.1", "cookies": [], "headers": [],
                                "queryString": [], "headersSize": -1, "bodySize": 0},
                    "response": {"status": 200, "statusText": "OK", "httpVersion": "HTTP/1.1", "cookies": [],
                                 "headers": [{"name": "Content-Type", "value": "text/plain"}],
                                 "content": {"size": 2, "mimeType": "text/plain", "text": "ok"}, "redirectURL": "",
                                 "headersSize": -1, "bodySize": 2}, "cache": {}, "timings": {"send": 0, "wait": 1, "receive": 0}}] if recorded else []
        har_path.write_text(json.dumps({"log": {"version": "1.2", "creator": {"name": "qc", "version": "1"}, "entries": entries}}), encoding="utf-8")
        events, report = directory / "events.json", directory / "report.json"
        env = {**os.environ, "QC_ALLOW_HOSTS": "127.0.0.1", "QC_B_HOST": "localhost" if har else "",
               "QC_HAR_FILE": str(har_path) if har else "", "QC_EVENTS_FILE": str(events),
               "PLAYWRIGHT_JSON_OUTPUT_NAME": str(report)}
        relative_spec = (directory / "guard.spec.mjs").relative_to(ROOT).as_posix()
        done = subprocess.run(["npx.cmd" if os.name == "nt" else "npx", "playwright", "test", relative_spec,
                               "--reporter=json", "--workers=1"], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        assert done.returncode == 0, done.stdout + done.stderr
        return json.loads(events.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_unknown_host_is_aborted_and_recorded(server):
    events = _run(server, har=False)
    assert events["blocked_hosts"] == ["localhost"] and events["har_missing"] == []


def test_missing_har_entry_is_aborted_and_records_only_path(server):
    events = _run(server, har=True)
    assert events["blocked_hosts"] == [] and events["har_missing"] == ["/missing"]


def test_recorded_har_entry_is_replayed_without_network_fallback(server):
    events = _run(server, har=True, recorded=True)
    assert events == {"blocked_hosts": [], "har_missing": []}


def test_fallback_would_reach_the_network_and_proves_abort_is_required(server):
    events = _run(server, har=True, fallback=True)
    assert events["blocked_hosts"] == []
