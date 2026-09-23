from __future__ import annotations

import copy
import json
import shlex
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.collect_adapter import CollectAdapter, OUTPUT_NAME, REPORT_NAME
from qc_agent.adapters.collect_runtime import CollectionError, collect, write_collection_artifacts


ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "eval" / "golden.json"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "task_collect.json").read_text(encoding="utf-8"))


def _spec() -> dict:
    spec = copy.deepcopy(SPEC)
    spec["inputs"]["golden"] = str(GOLDEN)
    spec["target"]["base_url"] = "http://toyapp.test"
    return spec


def _factory(handler):
    return lambda **kwargs: httpx.Client(
        transport=httpx.MockTransport(handler), **kwargs
    )


def _proc(command: list[str], returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=command, returncode=returncode, stdout="", stderr="")


def _collect_with_handler(tmp_path: Path, handler):
    records, report = collect(GOLDEN, "http://toyapp.test", _factory(handler))
    write_collection_artifacts(records, report, tmp_path / OUTPUT_NAME, tmp_path / REPORT_NAME)
    return records, report


def test_collects_all_golden_outputs_and_cleans_up(tmp_path):
    next_id = 0
    created: list[int] = []
    deleted: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal next_id
        if request.method == "POST" and request.url.path == "/notes":
            next_id += 1
            created.append(next_id)
            body = json.loads(request.content)
            return httpx.Response(201, json={"id": next_id, **body})
        if request.method == "POST" and request.url.path.endswith("/summarize"):
            return httpx.Response(
                200,
                json={"summary": "Tóm tắt ngắn", "model": "stub-rule-v1", "prompt_hash": "abc123"},
            )
        if request.method == "DELETE":
            deleted.append(int(request.url.path.rsplit("/", 1)[-1]))
            return httpx.Response(204)
        return httpx.Response(404)

    records, report = _collect_with_handler(tmp_path, handler)
    spec = _spec()
    command = CollectAdapter().build_cmd(spec, tmp_path)
    parsed = CollectAdapter().parse_output(_proc(command), tmp_path, spec)

    assert parsed.signals == {"checks": {"all_http_2xx": True, "count_matches_golden": True}}
    assert report["count"] == report["expected_count"] == 5
    assert len(records) == 5
    assert all(record["actual_output"] == "Tóm tắt ngắn" for record in records)
    assert all(record["model"] == "stub-rule-v1" and record["prompt_hash"] == "abc123" for record in records)
    assert created == deleted == [1, 2, 3, 4, 5]
    assert [kind for kind, _ in parsed.evidence_paths] == ["raw_output", "raw_output", "stdout"]


def test_base_url_is_passed_via_environment_not_replay_command(tmp_path):
    spec = _spec()
    spec["target"]["base_url"] = "https://api.example.test/v1"
    adapter = CollectAdapter()

    command = adapter.build_cmd(spec, tmp_path)
    replay_cmd = shlex.join(command)

    assert "api.example.test" not in replay_cmd
    assert adapter.env["QC_COLLECT_BASE_URL"] == spec["target"]["base_url"]
    assert "--base-url" not in command


@pytest.mark.parametrize(
    "base_url",
    [
        "https://alice:password@api.example.test/v1",
        "https://api.example.test/v1?token=query-secret",
        "https://api.example.test/v1#fragment",
    ],
)
def test_base_url_rejects_credentials_query_and_fragment(tmp_path, base_url):
    spec = _spec()
    spec["target"]["base_url"] = base_url

    with pytest.raises(AdapterParseError, match="không được chứa thông tin đăng nhập"):
        CollectAdapter().build_cmd(spec, tmp_path)


def test_non_2xx_is_a_failed_check_and_keeps_all_records(tmp_path):
    next_id = 0
    deleted: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal next_id
        if request.method == "POST" and request.url.path == "/notes":
            next_id += 1
            return httpx.Response(201, json={"id": next_id})
        if request.method == "POST" and request.url.path.endswith("/summarize"):
            if request.url.path.startswith("/notes/2/"):
                return httpx.Response(500, json={"detail": "internal error"})
            return httpx.Response(
                200, json={"summary": "ok", "model": "stub", "prompt_hash": "hash"}
            )
        if request.method == "DELETE":
            deleted.append(int(request.url.path.rsplit("/", 1)[-1]))
            return httpx.Response(204)
        return httpx.Response(404)

    records, report = _collect_with_handler(tmp_path, handler)
    assert report["checks"] == {"all_http_2xx": False, "count_matches_golden": True}
    assert len(records) == 5
    assert records[1]["http_status"] == 500
    assert records[1]["actual_output"] == ""
    assert deleted == [1, 2, 3, 4, 5]

    parsed = CollectAdapter().parse_output(_proc(["worker"]), tmp_path, _spec())
    assert parsed.signals["checks"] == report["checks"]


def test_transport_error_is_an_adapter_error_and_attempts_cleanup(tmp_path):
    deleted: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/notes":
            return httpx.Response(201, json={"id": 42})
        if request.method == "POST" and request.url.path.endswith("/summarize"):
            raise httpx.ConnectError("private transport detail", request=request)
        if request.method == "DELETE":
            deleted.append(42)
            return httpx.Response(204)
        return httpx.Response(404)

    with pytest.raises(CollectionError, match="HTTP transport error: ConnectError") as error:
        collect(GOLDEN, "http://toyapp.test", _factory(handler))

    assert "private transport detail" not in str(error.value)
    assert deleted == [42]
    assert not (tmp_path / OUTPUT_NAME).exists()


def test_malformed_success_response_is_an_adapter_error_and_cleans_note():
    deleted: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/notes":
            return httpx.Response(201, json={"id": 77})
        if request.method == "POST" and request.url.path.endswith("/summarize"):
            return httpx.Response(200, json={"summary": "ok", "model": "stub"})
        if request.method == "DELETE":
            deleted.append(77)
            return httpx.Response(204)
        return httpx.Response(404)

    with pytest.raises(CollectionError, match="misses summary/model/prompt_hash"):
        collect(GOLDEN, "http://toyapp.test", _factory(handler))
    assert deleted == [77]


def test_collection_runs_in_a_worker_with_the_task_wallclock_limit(tmp_path, monkeypatch):
    entered_request = threading.Event()

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            entered_request.set()
            time.sleep(5)
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        spec = _spec()
        spec["target"]["base_url"] = f"http://127.0.0.1:{server.server_port}"
        spec["budget"]["wallclock_s"] = 1
        spec["run_id"] = "r-collect-timeout"
        monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))

        started = time.perf_counter()
        result = CollectAdapter().run(spec)
        elapsed = time.perf_counter() - started

        assert entered_request.wait(timeout=1)
        assert result["status"] == "error"
        assert result["verdict"]["rationale"].startswith("timeout:")
        assert elapsed < 3
    finally:
        server.shutdown()
        server.server_close()
