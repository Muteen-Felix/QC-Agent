"""Bước 26: `qc-agent init` — đọc OpenAPI, sinh file, không ghi đè, dry-run; và file sinh ra chạy được thật trên noteboard."""
import copy
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml

from qc_agent.core import project as pj
from qc_agent.core.cli import main as cli_main
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import openapi
from qc_agent.scaffold import templates as t

ROOT = Path(__file__).resolve().parent.parent
VAHAN = ROOT / "tests" / "fixtures" / "openapi" / "vahan-rpa.json"
NOTEBOARD = ROOT / "tests" / "fixtures" / "sut" / "noteboard"


def spec_of(paths, **extra):
    return {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": paths, **extra}


def get(**op):
    return {"get": {"responses": {"200": {"description": "ok"}}, **op}}


# ---------- phân tích OpenAPI ----------

def test_real_vahan_openapi_yields_the_upload_exclusion_and_three_simple_get_paths():
    result = openapi.analyze(openapi.load(str(VAHAN)))
    assert result.exclude == [("/api/jobs/{job_id}/upload-excel", "POST nhận multipart/form-data: fuzz tệp không có nghĩa và tốn thời gian")]
    assert result.get_paths == ["/", "/api/health", "/api/runners"]  # gốc trước, rồi đường dẫn ngắn, ổn định theo thứ tự chữ cái
    assert result.title == "VAHAN RPA API" and result.warnings == [] and result.secured is False


def test_get_paths_skip_parameters_security_deprecated_and_bodies():
    spec = spec_of({
        "/ok": get(),
        "/p/{id}": get(),
        "/q": get(parameters=[{"name": "x", "in": "query", "required": True}]),
        "/opt": get(parameters=[{"name": "x", "in": "query", "required": False}]),
        "/dep": get(deprecated=True),
        "/sec": get(security=[{"key": []}]),
        "/pub": {"get": {"security": [], "responses": {"200": {"description": "ok"}}}},
        "/ref": get(parameters=[{"$ref": "#/components/parameters/Req"}]),
        "/broken": get(parameters=[{"$ref": "http://elsewhere/x"}]),
        "/body": {"get": {"requestBody": {"content": {"application/json": {}}}, "responses": {}}},
    }, components={"parameters": {"Req": {"name": "r", "in": "header", "required": True}}})
    result = openapi.analyze(spec)
    assert result.get_paths == ["/ok", "/opt", "/pub"]  # /broken: không giải được => coi như cần tham số, không đoán


def test_global_security_marks_the_api_secured_and_removes_endpoints_from_k6():
    spec = spec_of({"/a": get(), "/b": {"get": {"security": [], "responses": {}}}}, security=[{"bearer": []}])
    result = openapi.analyze(spec)
    assert result.secured and result.get_paths == ["/b"]
    assert any("inputs.auth" in w for w in result.warnings)


def test_no_usable_get_path_warns_and_returns_none():
    result = openapi.analyze(spec_of({"/x/{id}": get()}))
    assert result.get_paths == [] and any("bỏ perf-smoke" in w for w in result.warnings)


def test_server_prefix_is_applied_to_k6_paths_but_not_to_exclusions():
    up = {"post": {"requestBody": {"content": {"multipart/form-data": {}}}, "responses": {}}}
    result = openapi.analyze(spec_of({"/": get(), "/h": get(), "/up": up}, servers=[{"url": "https://x.example/api/v1/"}]))
    assert result.get_paths == ["/api/v1/", "/api/v1/h"] and [p for p, _ in result.exclude] == ["/up"]
    assert openapi.analyze(spec_of({"/h": get()}, servers=[{"url": "/{version}"}])).warnings and \
        openapi.analyze(spec_of({"/h": get()}, servers=[{"url": "/{version}"}])).get_paths == ["/h"]


def test_swagger2_file_upload_and_base_path():
    spec = {"swagger": "2.0", "info": {"title": "S"}, "basePath": "/v2", "paths": {
        "/f": {"post": {"consumes": ["multipart/form-data"], "responses": {}}},
        "/g": {"post": {"parameters": [{"name": "file", "in": "formData", "type": "file"}], "responses": {}}},
        "/h": get()}}
    result = openapi.analyze(spec)
    assert [p for p, _ in result.exclude] == ["/f", "/g"] and result.get_paths == ["/v2/h"]


def test_octet_stream_body_is_excluded_too():
    spec = spec_of({"/blob": {"put": {"requestBody": {"content": {"application/octet-stream": {}}}, "responses": {}}}})
    assert openapi.analyze(spec).exclude[0][0] == "/blob"


def test_request_body_ref_is_resolved_for_uploads():
    spec = spec_of({"/u": {"post": {"requestBody": {"$ref": "#/components/requestBodies/Up"}, "responses": {}}}},
                   components={"requestBodies": {"Up": {"content": {"multipart/form-data": {}}}}})
    assert openapi.analyze(spec).exclude[0][0] == "/u"


def test_at_most_three_paths_and_ranking_is_stable():
    result = openapi.analyze(spec_of({f"/{n}": get() for n in "edcba"} | {"/z/y/x": get(), "/": get()}))
    assert result.get_paths == ["/", "/a", "/b"]


@pytest.mark.parametrize("content, message", [
    (b"not: [valid", "JSON/YAML"), (b'{"paths": {}}', "không phải OpenAPI"), (b'{"openapi": "3", "paths": []}', "không phải OpenAPI"),
    (b"[1, 2]", "không phải OpenAPI"), (b"\xff\xfe\x00", "JSON/YAML")])
def test_invalid_documents_are_rejected(tmp_path, content, message):
    path = tmp_path / "x.json"
    path.write_bytes(content)
    with pytest.raises(openapi.OpenApiError, match=message):
        openapi.load(str(path))


def test_yaml_documents_and_missing_files_and_bad_schemes(tmp_path):
    path = tmp_path / "api.yaml"
    path.write_text("openapi: 3.0.0\ninfo: {title: Y}\npaths: {/a: {get: {responses: {}}}}\n", encoding="utf-8")
    assert openapi.analyze(openapi.load(str(path))).get_paths == ["/a"]
    with pytest.raises(openapi.OpenApiError, match="không đọc được file"):
        openapi.load(str(tmp_path / "nope.json"))
    with pytest.raises(openapi.OpenApiError, match="scheme"):
        openapi.load("ftp://host/openapi.json")


def test_oversized_document_is_rejected(tmp_path):
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (openapi.MAX_BYTES + 1))
    with pytest.raises(openapi.OpenApiError, match="vượt"):
        openapi.load(str(path))


def test_url_errors_do_not_leak_the_query_string(monkeypatch):
    import httpx

    def boom(*_a, **_k):
        raise httpx.ConnectError("secret-detail")
    monkeypatch.setattr(openapi.httpx, "get", boom)
    with pytest.raises(openapi.OpenApiError) as info:
        openapi.load("http://user:pw@host:1/openapi.json?token=abc")
    assert "token=abc" not in str(info.value) and "pw" not in str(info.value) and "secret-detail" not in str(info.value)


# ---------- init: ghi file ----------

def options(tmp_path, **over):
    base = dict(sut_root=tmp_path / "sut", slug="vahan-rpa", repo="Muteen-Felix/vahan-rpa", openapi_source=str(VAHAN),
                projects_dir=tmp_path / "projects")
    base.update(over)
    (tmp_path / "sut").mkdir(exist_ok=True)
    return init_mod.Options(**base)


def run_init(tmp_path, **over):
    force, dry = over.pop("force", False), over.pop("dry_run", False)
    plan = init_mod.build(options(tmp_path, **over))
    return plan, init_mod.apply(plan, force=force, dry_run=dry)


def test_init_writes_the_expected_files_for_api_only(tmp_path):
    _, outcomes = run_init(tmp_path)
    assert [o.label for o in outcomes] == [".qc-agent/suites/api-contract.yaml", ".qc-agent/suites/perf-smoke.yaml", ".qc-agent/perf/smoke.js",
                                           ".github/workflows/qc.yml", "<projects>/vahan-rpa.yaml"]
    assert all(o.status == "created" for o in outcomes)
    assert (tmp_path / "sut" / ".qc-agent" / "perf" / "smoke.js").is_file() and (tmp_path / "projects" / "vahan-rpa.yaml").is_file()
    assert not list((tmp_path / "sut").rglob(".qc-init-*")), "không để lại file tạm"


def test_init_with_ui_adds_suite_flows_and_workflow_inputs(tmp_path):
    plan, outcomes = run_init(tmp_path, ui_dockerfile="apps/web-ui/Dockerfile", ui_context="apps/web-ui", ui_port="8080",
                              ui_build_args=["VITE_API_URL=http://sut:8000"], sut_health_path="/api/health", sut_env=["X=y"])
    labels = {o.label for o in outcomes}
    assert {".qc-agent/suites/ui-explore.yaml", ".qc-agent/midscene/explore.yaml", ".qc-agent/midscene/canary.yaml"} <= labels
    workflow = yaml.safe_load((tmp_path / "sut" / ".github" / "workflows" / "qc.yml").read_text(encoding="utf-8"))["jobs"]["qc"]["with"]
    assert workflow["sut_ui_dockerfile"] == "apps/web-ui/Dockerfile" and workflow["sut_health_path"] == "/api/health"
    cfg = yaml.safe_load((tmp_path / "projects" / "vahan-rpa.yaml").read_text(encoding="utf-8"))
    assert cfg["modes"]["pr"]["advisory_suites"] == ["perf-smoke", "ui-explore"] and cfg["name"] == "VAHAN RPA API"


def test_todos_are_reported_with_their_location(tmp_path):
    _, outcomes = run_init(tmp_path, ui_dockerfile="apps/web-ui/Dockerfile")
    todos = {o.label: o.todos for o in outcomes if o.todos}
    assert set(todos) == {".qc-agent/midscene/explore.yaml", ".github/workflows/qc.yml"} and len(todos[".github/workflows/qc.yml"]) == 2
    assert all(entry.startswith("dòng ") for entries in todos.values() for entry in entries)


def test_pins_given_leave_no_todo_in_the_workflow(tmp_path):
    _, outcomes = run_init(tmp_path, qc_ref="a" * 40, image="ghcr.io/muteen-felix/qc-agent@sha256:" + "b" * 64)
    assert not [o for o in outcomes if o.todos]


def test_existing_files_are_never_overwritten_without_force(tmp_path):
    run_init(tmp_path)
    target = tmp_path / "sut" / ".qc-agent" / "suites" / "api-contract.yaml"
    target.write_text("# tay sửa\n", encoding="utf-8")
    _, outcomes = run_init(tmp_path)
    assert all(o.status == "kept" for o in outcomes) and target.read_text(encoding="utf-8") == "# tay sửa\n"
    _, forced = run_init(tmp_path, force=True)
    assert all(o.status == "overwritten" for o in forced) and target.read_text(encoding="utf-8") != "# tay sửa\n"


def test_kept_files_report_no_todos_and_a_partial_rerun_only_creates_missing_files(tmp_path):
    run_init(tmp_path)
    (tmp_path / "sut" / ".qc-agent" / "perf" / "smoke.js").unlink()
    _, outcomes = run_init(tmp_path)
    assert {o.label: o.status for o in outcomes}[".qc-agent/perf/smoke.js"] == "created"
    assert [o.status for o in outcomes].count("kept") == 4


def test_dry_run_writes_nothing_and_shows_a_diff_when_forcing(tmp_path):
    _, outcomes = run_init(tmp_path, dry_run=True)
    assert all(o.status == "would-create" for o in outcomes) and not (tmp_path / "sut" / ".qc-agent").exists() and not (tmp_path / "projects").exists()
    run_init(tmp_path)
    workflow = tmp_path / "sut" / ".github" / "workflows" / "qc.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("name: qc", "name: mine"), encoding="utf-8")
    before = workflow.read_text(encoding="utf-8")
    _, preview = run_init(tmp_path, dry_run=True, force=True)
    diff = next(o.diff for o in preview if o.label == ".github/workflows/qc.yml")
    assert "-name: mine" in diff and "+name: qc" in diff and workflow.read_text(encoding="utf-8") == before


def test_generated_files_load_through_the_real_project_and_suite_loaders(tmp_path):
    run_init(tmp_path, ui_dockerfile="apps/web-ui/Dockerfile", qc_ref="a" * 40)
    cfg = pj.load_project("vahan-rpa", tmp_path / "projects")
    suites = pj.load_suites(tmp_path / "sut" / ".qc-agent" / "suites")
    plan, meta = pj.build_plan(cfg, "pr", suites)
    assert {t_["task_id"]: t_["lane"] for t_ in plan["tasks"]} == {"t-001": "gate", "t-102": "discovery", "t-101": "discovery", "t-canary-01": "discovery"}
    assert meta["on_skipped_gate_task"] == "fail"


def test_no_api_generates_only_ui_and_marks_the_project_as_having_no_blocking_suite(tmp_path):
    plan, outcomes = run_init(tmp_path, openapi_source=None, no_api=True, ui_dockerfile="ui/Dockerfile")
    assert {o.label for o in outcomes} == {".qc-agent/suites/ui-explore.yaml", ".qc-agent/midscene/explore.yaml", ".qc-agent/midscene/canary.yaml",
                                           ".github/workflows/qc.yml", "<projects>/vahan-rpa.yaml"}
    text = (tmp_path / "projects" / "vahan-rpa.yaml").read_text(encoding="utf-8")
    assert "blocking_suites: []" in text and t.TODO in text  # gate luôn PASS: phải để người quyết định, không im lặng
    assert any("projects" in o.label and o.todos for o in outcomes)
    assert pj.load_project("vahan-rpa", tmp_path / "projects")["modes"]["pr"]["blocking_suites"] == []


def test_no_project_skips_the_central_config(tmp_path):
    _, outcomes = run_init(tmp_path, no_project=True)
    assert not any("projects" in o.label for o in outcomes) and not (tmp_path / "projects").exists()


def test_default_project_name_is_the_slug_when_openapi_has_no_title(tmp_path):
    spec = json.loads(VAHAN.read_text(encoding="utf-8"))
    spec["info"] = {}
    path = tmp_path / "noinfo.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    run_init(tmp_path, openapi_source=str(path))
    assert yaml.safe_load((tmp_path / "projects" / "vahan-rpa.yaml").read_text(encoding="utf-8"))["name"] == "vahan-rpa"


@pytest.mark.parametrize("over, message", [
    ({"openapi_source": None}, "--openapi"), ({"no_api": True, "openapi_source": None}, "không có gì để sinh"),
    ({"ui_port": "9"}, "--ui-dockerfile"), ({"sut_root": Path("/nonexistent-dir-xyz")}, "không phải thư mục"),
    ({"slug": "Bad Slug"}, "slug|project"), ({"repo": "nope"}, "repo"), ({"qc_ref": "main"}, "qc_ref"),
    ({"openapi_source": "/no/such/openapi.json"}, "không đọc được"), ({"sut_env": ["bad line"]}, "sut_env")])
def test_bad_options_fail_before_anything_is_written(tmp_path, over, message):
    with pytest.raises(init_mod.InitError, match=message):
        init_mod.build(options(tmp_path, **over))
    assert not (tmp_path / "sut" / ".qc-agent").exists()


def test_a_directory_in_the_way_is_an_error_not_a_crash(tmp_path):
    plan = init_mod.build(options(tmp_path))
    (tmp_path / "sut" / ".github" / "workflows" / "qc.yml").mkdir(parents=True)
    with pytest.raises(init_mod.InitError, match="là thư mục"):
        init_mod.apply(plan)


def test_openapi_path_comes_from_the_url_when_given_a_url(tmp_path, monkeypatch):
    class Response:
        content = VAHAN.read_bytes()

        def raise_for_status(self):
            pass
    monkeypatch.setattr(openapi.httpx, "get", lambda *a, **k: Response())
    run_init(tmp_path, openapi_source="http://sut.local:8000/api/docs/openapi.json")
    text = (tmp_path / "sut" / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8")
    assert "schema_url: ${env.APP_BASE_URL}/api/docs/openapi.json" in text


# ---------- qua CLI ----------

def test_cli_init_entry_point_and_exit_codes(tmp_path, capsys):
    (tmp_path / "sut").mkdir()
    argv = ["init", "--sut-root", str(tmp_path / "sut"), "--slug", "demo", "--repo", "o/demo", "--openapi", str(VAHAN),
            "--projects-dir", str(tmp_path / "projects"), "--dry-run"]
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    assert "would-create" in out and "(dry-run: chưa ghi gì)" in out and "qc-agent:todo" in out
    assert not (tmp_path / "sut" / ".qc-agent").exists()
    assert cli_main(argv[:-1] + ["--openapi", "/nope.json", "--dry-run"]) == 3
    assert "LỖI:" in capsys.readouterr().err
    assert cli_main(["init", "--slug", "x"]) == 3  # thiếu tham số bắt buộc: exit 3, không phải 2 của argparse


# ---------- file sinh ra chạy được THẬT ----------

def _serve_noteboard(monkeypatch, bugs):
    monkeypatch.setenv("QC_BUGS", bugs)
    monkeypatch.syspath_prepend(str(NOTEBOARD))
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


@pytest.mark.skipif(shutil.which("st") is None or shutil.which("k6") is None, reason="needs st and k6")
def test_init_from_a_live_sut_produces_a_gate_that_passes_clean_and_fails_on_a_seeded_bug(tmp_path, monkeypatch):
    server, thread, url = _serve_noteboard(monkeypatch, "none")
    try:
        sut, projects = tmp_path / "sut", tmp_path / "projects"
        sut.mkdir()
        assert cli_main(["init", "--sut-root", str(sut), "--slug", "nb", "--repo", "o/nb", "--openapi", f"{url}/openapi.json",
                         "--projects-dir", str(projects)]) == 0

        def gate(run_name):
            env = {**os.environ, "APP_BASE_URL": url, "PYTHONUTF8": "1"}
            proc = subprocess.run([sys.executable, "-m", "qc_agent.core.cli", "run", "--project", "nb", "--mode", "pr", "--projects-dir", str(projects),
                                   "--sut-root", str(sut), "--runs-dir", str(tmp_path / run_name)], cwd=ROOT, env=env, capture_output=True,
                                  text=True, encoding="utf-8", timeout=300)
            return proc.returncode, proc.stdout

        code, report = gate("clean")
        assert code == 0 and "VERDICT: ✅ PASS" in report, report[-800:]
        assert "t-102" in report  # perf-smoke (discovery) cũng chạy được, k6 thật
    finally:
        server.should_exit = True
        thread.join(15)

    server, thread, url = _serve_noteboard(monkeypatch, "1")
    try:
        code, report = gate("bug")
        assert code == 1 and "not_a_server_error" in report, report[-800:]  # BUG-1 bị bắt bằng suite hoàn toàn do init sinh ra
    finally:
        server.should_exit = True
        thread.join(15)
