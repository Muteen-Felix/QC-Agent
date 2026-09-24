"""Thu thập theo mẫu (deepeval_collect) + chạy adapter DeepEval thật (worker subprocess, pytest của qc-agent) trên toyapp thật."""
from __future__ import annotations

import copy
import importlib
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml

from qc_agent.adapters import deepeval_collect
from qc_agent.adapters.deepeval_adapter import DeepEvalAdapter
from qc_agent.adapters.deepeval_collect import CollectionError, collect, render, validate_config

ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
GOLDEN = SUT / "tests" / "eval" / "golden.json"
SUITE_TASK = yaml.safe_load((SUT / ".qc-agent" / "suites" / "ai-eval.yaml").read_text(encoding="utf-8"))["tasks"][0]
CONFIG = SUITE_TASK["inputs"]["collect"]


def factory(handler):
    return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs)


class Notes:
    """Bản giả tối thiểu của API noteboard: ghi lại mọi request."""

    def __init__(self, summarize_status=200):
        self.next_id, self.requests, self.alive, self.summarize_status = 0, [], set(), summarize_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.raw_path.decode()))
        if request.method == "POST" and request.url.path == "/notes":
            self.next_id += 1
            self.alive.add(self.next_id)
            return httpx.Response(201, json={"id": self.next_id, **json.loads(request.content)})
        if request.method == "POST" and request.url.path.endswith("/summarize"):
            if self.summarize_status != 200:
                return httpx.Response(self.summarize_status)
            return httpx.Response(200, json={"summary": "tom tat", "model": "stub", "prompt_hash": "h1"})
        if request.method == "DELETE":
            self.alive.discard(int(request.url.path.rsplit("/", 1)[1]))
            return httpx.Response(204)
        return httpx.Response(404)


def test_collects_every_golden_case_maps_record_and_cleans_up():
    notes = Notes()
    records, report = collect(CONFIG, "http://toyapp.test", GOLDEN, factory(notes), nonce="abcd1234")

    assert [r["id"] for r in records] == ["g1", "g2", "g3", "g4", "g5"]
    assert records[0] == {"id": "g1", "http_status": 200, "input": json.loads(GOLDEN.read_text(encoding="utf-8"))[0]["body"],
                          "actual_output": "tom tat", "model": "stub", "prompt_hash": "h1"}
    assert report == {"count": 5, "expected_count": 5, "checks": {"all_http_2xx": True, "count_matches_golden": True}}
    assert notes.alive == set(), "mọi dữ liệu test phải được dọn"


def test_test_data_is_namespaced_per_run_with_nonce():
    seen = []

    def handler(request):
        if request.method == "POST" and request.url.path == "/notes":
            seen.append(json.loads(request.content)["title"])
        return Notes()(request)

    collect(CONFIG, "http://t.test", GOLDEN, factory(handler), nonce="run1")
    collect(CONFIG, "http://t.test", GOLDEN, factory(handler))
    assert all(t.startswith("run1-") for t in seen[:5]) and not any(t.startswith("run1-") for t in seen[5:])
    assert len({t.split("-", 1)[0] for t in seen[5:]}) == 1, "cùng một lần chạy dùng cùng một nonce"


def test_non_2xx_is_a_failed_check_not_an_error_and_keeps_every_record():
    notes = Notes(summarize_status=500)
    records, report = collect(CONFIG, "http://t.test", GOLDEN, factory(notes))

    assert len(records) == 5 and all(r["http_status"] == 500 and r["actual_output"] == "" for r in records)
    assert report["checks"] == {"all_http_2xx": False, "count_matches_golden": True}
    assert notes.alive == set()


def test_transport_error_is_a_collection_error_and_cleanup_is_attempted():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.url.path.endswith("/summarize"):
            raise httpx.ConnectError("boom")
        return Notes()(request) if request.method != "DELETE" else httpx.Response(204)

    with pytest.raises(CollectionError, match="transport error: ConnectError"):
        collect(CONFIG, "http://t.test", GOLDEN, factory(handler))
    assert "DELETE" in calls


def test_malformed_response_is_a_collection_error_not_a_guess():
    def handler(request):
        if request.method == "POST" and request.url.path == "/notes":
            return httpx.Response(201, json={"no_id": True})
        return httpx.Response(204)

    with pytest.raises(CollectionError, match="lacks 'id'"):
        collect(CONFIG, "http://t.test", GOLDEN, factory(handler))


def test_record_variable_missing_although_all_steps_ok_is_an_error():
    config = copy.deepcopy(CONFIG)
    config["record"]["extra"] = "{{never_captured}}"
    with pytest.raises(CollectionError, match="never_captured"):
        collect(config, "http://t.test", GOLDEN, factory(Notes()))


def test_values_are_percent_encoded_in_paths_and_cannot_change_the_target():
    paths = []

    def handler(request):
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"id": "../../admin?x=1#y"})

    config = {"golden": "g", "steps": [{"method": "POST", "path": "/a", "capture": {"ident": "id"}}, {"method": "GET", "path": "/notes/{{ident}}"}],
              "record": {"input": "i", "actual_output": "{{ident}}"}}
    collect(config, "http://t.test", GOLDEN, factory(handler))
    assert paths[1] == "/notes/..%2F..%2Fadmin%3Fx%3D1%23y"


@pytest.mark.parametrize("path", ["http://evil.test/x", "//evil.test/x", "notes", "/a\\b", ""])
def test_absolute_or_relative_urls_are_refused_in_config(path):
    config = copy.deepcopy(CONFIG)
    config["steps"][0]["path"] = path
    assert any("path" in e for e in validate_config(config))


@pytest.mark.parametrize("mutate, fragment", [
    (lambda c: c.update(unknown=1), "khoá lạ"),
    (lambda c: c.update(steps=[]), "steps"),
    (lambda c: c["steps"][0].update(method="TRACE"), "method"),
    (lambda c: c["record"].pop("input"), "record"),
    (lambda c: c["record"].update(id="x"), "'id'/'http_status'"),
    (lambda c: c["steps"][0].update(capture={"bad name": "id"}), "capture"),
])
def test_config_shape_is_validated(mutate, fragment):
    config = copy.deepcopy(CONFIG)
    mutate(config)
    assert any(fragment in e for e in validate_config(config))


def test_bad_golden_is_a_collection_error(tmp_path):
    for content, message in (("[]", "non-empty"), ('[{"id": "A-1"}]', "needs an id"), ('[{"id":"a"},{"id":"a"}]', "unique"), ("{", "cannot read")):
        golden = tmp_path / "g.json"
        golden.write_text(content, encoding="utf-8")
        with pytest.raises(CollectionError, match=message):
            collect(CONFIG, "http://t.test", golden, factory(Notes()))


def test_render_keeps_type_for_whole_placeholders_and_stringifies_inside_text():
    variables = {"n": 7, "case": {"tags": ["a", 1]}}
    assert render("{{n}}", variables) == 7
    assert render("id={{n}}", variables) == "id=7"
    assert render({"k": ["{{case.tags.1}}"]}, variables) == {"k": [1]}
    assert render("{{case.tags}}", variables) == ["a", 1]


# ─────────── integration: adapter thật + worker thật + toyapp thật ───────────

def _serve(monkeypatch, bugs: str):
    monkeypatch.setenv("QC_BUGS", bugs)
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


def _run_noteboard_eval(tmp_path, monkeypatch, bugs: str) -> dict:
    server, thread, url = _serve(monkeypatch, bugs)
    try:
        spec = json.loads((ROOT / "tests" / "fixtures" / "task_de.json").read_text(encoding="utf-8"))
        for key in ("collect", "metrics", "geval", "judge_config", "sut_model"):
            spec["inputs"][key] = SUITE_TASK["inputs"][key]
        del spec["inputs"]["outputs_path"]
        spec["oracle"] = SUITE_TASK["oracle"]
        spec["target"]["base_url"] = url
        spec["run_id"] = f"r-{bugs.replace(',', '')}"
        spec["budget"]["wallclock_s"] = 120
        monkeypatch.chdir(SUT)
        monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
        for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(name, raising=False)  # không gọi judge thật: G-Eval advisory sẽ báo lỗi, gate không đổi
        result = DeepEvalAdapter().run(spec)
        health = httpx.get(f"{url}/notes", timeout=5).json()
        return {"result": result, "notes_left": health}
    finally:
        server.should_exit = True
        thread.join(15)


def test_noteboard_clean_passes_and_leaves_no_test_data(tmp_path, monkeypatch):
    out = _run_noteboard_eval(tmp_path, monkeypatch, "none")
    result = out["result"]
    assert result["status"] == "pass", result["verdict"]["rationale"]
    assert out["notes_left"] == []


def test_bug3_is_still_caught_as_summary_shorter_than_body(tmp_path, monkeypatch):
    out = _run_noteboard_eval(tmp_path, monkeypatch, "3")
    result = out["result"]
    assert result["status"] == "fail", result["verdict"]
    detected = {f["detected_by"] for f in result["findings"] if f["verdict_source"] == "deterministic_assert"}
    assert detected == {"metric:summary_shorter_than_body", "check:summary_shorter_than_body"}  # metric của pytest + finding của oracle
    assert any("case g3" in f["title"] for f in result["findings"])
    assert out["notes_left"] == []
    assert deepeval_collect.OUTPUT_NAME in {Path(e["uri"]).name for e in result["evidence"]}


def test_unreachable_sut_is_an_error_not_a_fail(tmp_path, monkeypatch):
    spec = json.loads((ROOT / "tests" / "fixtures" / "task_de.json").read_text(encoding="utf-8"))
    for key in ("collect", "metrics", "geval", "judge_config", "sut_model"):
        spec["inputs"][key] = SUITE_TASK["inputs"][key]
    del spec["inputs"]["outputs_path"]
    spec["oracle"] = SUITE_TASK["oracle"]
    spec["target"]["base_url"] = "http://127.0.0.1:1"
    spec["run_id"] = "r-unreachable"
    monkeypatch.chdir(SUT)
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    result = DeepEvalAdapter().run(spec)
    assert result["status"] == "error" and "thu thập" in result["verdict"]["rationale"]
