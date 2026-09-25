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


# ---------- init: một repo, một lệnh, chỉ ghi trong repo SUT ----------

SCAN_FIXTURE = ROOT / "tests" / "fixtures" / "scan" / "vahan-rpa"


def options(tmp_path, *, fixture=SCAN_FIXTURE, files=None, **over):
    """SUT tạm = bản cắt cấu trúc vahan-rpa (mặc định) hoặc cây tự dựng `files`."""
    sut = tmp_path / "sut"
    if files is not None:
        for rel, text in files.items():
            (sut / rel).parent.mkdir(parents=True, exist_ok=True)
            (sut / rel).write_text(text, encoding="utf-8")
    elif not sut.exists():
        shutil.copytree(fixture, sut)
    sut.mkdir(exist_ok=True)
    return init_mod.Options(**{"sut_root": sut, "slug": "vahan-rpa", **over})


def run_init(tmp_path, **over):
    force, dry = over.pop("force", False), over.pop("dry_run", False)
    plan = init_mod.build(options(tmp_path, **over))
    return plan, init_mod.apply(plan, force=force, dry_run=dry)


def read(tmp_path, rel):
    return (tmp_path / "sut" / rel).read_text(encoding="utf-8")


def workflow_with(tmp_path):
    return yaml.safe_load(read(tmp_path, ".github/workflows/qc.yml"))["jobs"]["qc"]["with"]


def test_vahan_fixture_generates_the_phase1_file_set_with_a_real_ui_dockerfile(tmp_path):
    plan, outcomes = run_init(tmp_path)
    assert [o.label for o in outcomes] == [
        ".qc-agent/suites/api-contract.yaml", ".qc-agent/suites/perf-smoke.yaml", ".qc-agent/perf/smoke.js", ".qc-agent/Dockerfile.ui",
        ".qc-agent/suites/ui-explore.yaml", ".qc-agent/midscene/explore.yaml", ".qc-agent/midscene/canary.yaml", ".github/workflows/qc.yml"]
    assert all(o.status == "created" for o in outcomes) and plan.slug == "vahan-rpa"
    dockerfile = read(tmp_path, ".qc-agent/Dockerfile.ui")
    assert "FROM node:22-alpine AS build" in dockerfile and "RUN npm ci" in dockerfile and "ARG VITE_API_URL" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile and "listen 8080;" in dockerfile and "try_files $uri $uri/ /index.html;" in dockerfile
    with_ = workflow_with(tmp_path)
    assert with_["sut_ui_dockerfile"] == ".qc-agent/Dockerfile.ui" and with_["sut_ui_context"] == "apps/web-ui" and with_["sut_ui_port"] == "8080"
    assert with_["sut_ui_build_args"].strip() == "VITE_API_URL=http://sut:8000"
    assert with_["sut_health_path"] == "/health"
    assert with_["sut_env"].strip() == "VAHAN_API_CORS_ORIGINS=http://ui:8080"
    assert "sut_port" not in with_ and "sut_dockerfile" not in with_       # bằng mặc định của workflow thì không khai


def test_ambiguous_choices_carry_verify_and_nothing_else_does(tmp_path):
    run_init(tmp_path)
    workflow = read(tmp_path, ".github/workflows/qc.yml")
    verify = [line for line in workflow.splitlines() if "qc-agent:todo VERIFY" in line]
    assert len(verify) == 1 and "sut_env" in verify[0]
    assert "chọn VAHAN_API_CORS_ORIGINS trong [VAHAN_API_CORS_ORIGINS, VAHAN_API_SOCKETIO_CORS_ORIGINS]" in verify[0]   # có 2 tên CORS


def test_without_openapi_the_suites_have_refine_regions_and_todos(tmp_path):
    _, outcomes = run_init(tmp_path)
    contract = read(tmp_path, ".qc-agent/suites/api-contract.yaml")
    assert "# qc-agent:begin refine exclude_path" in contract and "# qc-agent:todo REFINE:" in contract and "# qc-agent:end" in contract
    assert "exclude_path:" not in contract and "schema_url: ${env.APP_BASE_URL}/openapi.json" in contract
    k6 = read(tmp_path, ".qc-agent/perf/smoke.js")
    assert 'const PATHS = ["/health"];' in k6 and "// qc-agent:begin refine k6_paths" in k6 and "// qc-agent:end" in k6
    assert "// qc-agent:todo REFINE:" in k6
    assert any(o.label == ".qc-agent/suites/api-contract.yaml" and o.todos for o in outcomes)
    yaml.safe_load(contract)     # vẫn là YAML hợp lệ


def test_with_openapi_the_old_behaviour_holds_and_there_is_no_refine_marker(tmp_path):
    run_init(tmp_path, openapi_source=str(VAHAN))
    contract = read(tmp_path, ".qc-agent/suites/api-contract.yaml")
    assert "refine" not in contract and "/api/jobs/{job_id}/upload-excel" in contract
    assert '["/", "/api/health", "/api/runners"]' in read(tmp_path, ".qc-agent/perf/smoke.js") and "refine" not in read(tmp_path, ".qc-agent/perf/smoke.js")


def test_no_fastapi_asks_to_verify_the_openapi_path(tmp_path):
    run_init(tmp_path, files={"Dockerfile": "FROM x\nEXPOSE 3000\n", "app.js": "x"})
    assert "qc-agent:todo VERIFY: không thấy FastAPI" in read(tmp_path, ".qc-agent/suites/api-contract.yaml")
    assert workflow_with(tmp_path)["sut_port"] == "3000"


def test_next_ssr_gets_no_ui_dockerfile_and_a_note(tmp_path):
    plan, outcomes = run_init(tmp_path, files={"Dockerfile": "x", "web/package.json": '{"dependencies": {"next": "15"}}', "web/package-lock.json": "{}"})
    assert ".qc-agent/Dockerfile.ui" not in [o.label for o in outcomes] and not (tmp_path / "sut" / ".qc-agent" / "suites" / "ui-explore.yaml").exists()
    assert any("Next.js SSR" in n and "--ui-dockerfile" in n for n in plan.notes) and "sut_ui_dockerfile" not in workflow_with(tmp_path)


def test_own_ui_dockerfile_flag_skips_generation(tmp_path):
    _, outcomes = run_init(tmp_path, ui_dockerfile="apps/web-ui/Dockerfile", ui_context="apps/web-ui", ui_build_args=["VITE_API_URL=http://sut:9"])
    assert ".qc-agent/Dockerfile.ui" not in [o.label for o in outcomes]
    with_ = workflow_with(tmp_path)
    assert with_["sut_ui_dockerfile"] == "apps/web-ui/Dockerfile" and with_["sut_ui_build_args"].strip() == "VITE_API_URL=http://sut:9"


def test_missing_api_dockerfile_is_an_error_with_guidance_and_writes_nothing(tmp_path):
    with pytest.raises(init_mod.InitError, match="--sut-dockerfile"):
        init_mod.build(options(tmp_path, files={"README.md": "x"}))
    assert not (tmp_path / "sut" / ".qc-agent").exists()


def test_flags_override_the_scanner_and_leave_no_verify(tmp_path):
    run_init(tmp_path, sut_port="9000", sut_health_path="/api/health", sut_env=["X=y"])
    with_ = workflow_with(tmp_path)
    assert with_["sut_port"] == "9000" and with_["sut_health_path"] == "/api/health" and with_["sut_env"].strip() == "X=y"
    assert "qc-agent:todo VERIFY" not in read(tmp_path, ".github/workflows/qc.yml")


def test_non_default_dockerfile_and_context_are_declared(tmp_path):
    run_init(tmp_path, files={"api/Dockerfile": "EXPOSE 5000\n"})
    with_ = workflow_with(tmp_path)
    assert with_["sut_dockerfile"] == "api/Dockerfile" and with_["sut_context"] == "api" and with_["sut_port"] == "5000"


def test_workflow_pin_comes_from_the_image_build_sha_but_the_digest_stays_a_todo(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_AGENT_GIT_SHA", "c" * 40)
    run_init(tmp_path)
    text = read(tmp_path, ".github/workflows/qc.yml")
    assert "qc-gate.reusable.yml@" + "c" * 40 in text and "qc-agent-todo-pin" not in text
    assert "<DIGEST>" in text and "qc-agent:todo" in text
    monkeypatch.setenv("QC_AGENT_GIT_SHA", "unknown")
    (tmp_path / "sut" / ".github" / "workflows" / "qc.yml").unlink()
    run_init(tmp_path)
    assert "qc-agent-todo-pin-commit-sha" in read(tmp_path, ".github/workflows/qc.yml")


def test_default_slug_comes_from_origin_then_directory_name(tmp_path):
    (tmp_path / "sut").mkdir()
    (tmp_path / "sut" / ".git").mkdir()
    (tmp_path / "sut" / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/Muteen-Felix/Vahan-RPA.git\n', encoding="utf-8")
    plan = init_mod.build(options(tmp_path, files={"Dockerfile": "x"}, slug=None))
    assert plan.slug == "vahan-rpa" and "project: vahan-rpa" in next(f.content for f in plan.files if f.label.endswith("qc.yml"))


def test_init_writes_only_inside_sut_root_and_leaves_no_temp_files(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    run_init(tmp_path, dry_run=False)
    assert not list((tmp_path / "sut").rglob(".qc-init-*"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["elsewhere", "sut"] and not list((tmp_path / "elsewhere").iterdir())


def test_todos_are_reported_with_their_location(tmp_path):
    _, outcomes = run_init(tmp_path)
    todos = {o.label: o.todos for o in outcomes if o.todos}
    assert set(todos) == {".qc-agent/suites/api-contract.yaml", ".qc-agent/perf/smoke.js", ".qc-agent/midscene/explore.yaml", ".github/workflows/qc.yml"}
    assert all(entry.startswith("dòng ") for entries in todos.values() for entry in entries)


def test_pins_given_leave_only_the_scanner_and_refine_todos(tmp_path):
    _, outcomes = run_init(tmp_path, qc_ref="a" * 40, image="ghcr.io/muteen-felix/qc-agent@sha256:" + "b" * 64, openapi_source=str(VAHAN),
                           sut_env=["X=y"])
    assert {o.label for o in outcomes if o.todos} == {".qc-agent/midscene/explore.yaml"}


def test_existing_files_are_never_overwritten_without_force(tmp_path):
    run_init(tmp_path)
    target = tmp_path / "sut" / ".qc-agent" / "suites" / "api-contract.yaml"
    target.write_text("# tay sửa\n", encoding="utf-8")
    _, outcomes = run_init(tmp_path)
    assert all(o.status == "kept" for o in outcomes) and target.read_text(encoding="utf-8") == "# tay sửa\n"
    _, forced = run_init(tmp_path, force=True)
    assert all(o.status == "overwritten" for o in forced) and target.read_text(encoding="utf-8") != "# tay sửa\n"


def test_a_partial_rerun_only_creates_missing_files(tmp_path):
    run_init(tmp_path)
    (tmp_path / "sut" / ".qc-agent" / "perf" / "smoke.js").unlink()
    _, outcomes = run_init(tmp_path)
    assert {o.label: o.status for o in outcomes}[".qc-agent/perf/smoke.js"] == "created" and [o.status for o in outcomes].count("kept") == 7


def test_dry_run_writes_nothing_and_shows_a_diff_when_forcing(tmp_path):
    _, outcomes = run_init(tmp_path, dry_run=True)
    assert all(o.status == "would-create" for o in outcomes) and not (tmp_path / "sut" / ".qc-agent").exists()
    run_init(tmp_path)
    workflow = tmp_path / "sut" / ".github" / "workflows" / "qc.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("name: qc", "name: mine"), encoding="utf-8")
    before = workflow.read_text(encoding="utf-8")
    _, preview = run_init(tmp_path, dry_run=True, force=True)
    diff = next(o.diff for o in preview if o.label == ".github/workflows/qc.yml")
    assert "-name: mine" in diff and "+name: qc" in diff and workflow.read_text(encoding="utf-8") == before


def test_generated_files_load_through_the_real_loaders_and_the_default_policy(tmp_path):
    run_init(tmp_path, qc_ref="a" * 40)
    cfg, info = pj.resolve_project("vahan-rpa-unregistered", ROOT / "configs" / "projects")   # repo chưa đăng ký => _default
    suites = pj.load_suites(tmp_path / "sut" / ".qc-agent" / "suites")
    plan, meta = pj.build_plan(cfg, "pr", suites)
    assert {t_["task_id"]: t_["lane"] for t_ in plan["tasks"]} == {"t-001": "gate", "t-102": "discovery", "t-101": "discovery", "t-canary-01": "discovery"}
    assert meta["on_skipped_gate_task"] == "fail" and info["source"] == "default"


def test_no_api_generates_only_ui_and_warns_it_is_not_eligible_for_pr_mode(tmp_path):
    plan, outcomes = run_init(tmp_path, no_api=True)
    assert {o.label for o in outcomes} == {".qc-agent/Dockerfile.ui", ".qc-agent/suites/ui-explore.yaml", ".qc-agent/midscene/explore.yaml",
                                           ".qc-agent/midscene/canary.yaml", ".github/workflows/qc.yml"}
    assert any("không đủ điều kiện mode pr" in w for w in plan.warnings)
    assert "sut_health_path" not in workflow_with(tmp_path) and "sut_env" not in workflow_with(tmp_path)


@pytest.mark.parametrize("over, message", [
    ({"no_api": True, "files": {"README.md": "x"}}, "không có gì để sinh"),
    ({"ui_port": "9", "files": {"Dockerfile": "x"}}, "không có UI"), ({"sut_root": Path("/nonexistent-dir-xyz")}, "không phải thư mục"),
    ({"slug": "Bad Slug"}, "slug"), ({"qc_ref": "main"}, "qc_ref"), ({"openapi_source": "/no/such/openapi.json"}, "không đọc được"),
    ({"sut_env": ["bad line"]}, "sut_env"), ({"sut_dockerfile": "nope/Dockerfile"}, "không tồn tại"),
    ({"ui_urls": ["http://x"]}, "--ui-url chỉ dùng cùng --suggest-ui")])
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
    assert "schema_url: ${env.APP_BASE_URL}/api/docs/openapi.json" in read(tmp_path, ".qc-agent/suites/api-contract.yaml")


def test_running_as_root_in_a_container_chowns_only_what_it_created(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(init_mod, "_owner", lambda root: (1000, 1001))
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: calls.append((Path(path), uid, gid)), raising=False)
    (tmp_path / "sut").mkdir()
    (tmp_path / "sut" / ".github").mkdir()          # đã có từ trước: không được đổi chủ
    plan = init_mod.build(options(tmp_path, files={"Dockerfile": "x"}))
    init_mod.apply(plan)
    chowned = {p.relative_to(tmp_path / "sut").as_posix() for p, uid, gid in calls}
    assert ".github/workflows/qc.yml" in chowned and ".github/workflows" in chowned and ".qc-agent/suites/api-contract.yaml" in chowned
    assert ".github" not in chowned and "" not in chowned and all((uid, gid) == (1000, 1001) for _, uid, gid in calls)
    init_mod.apply(plan, dry_run=True)
    assert len(calls) == len(chowned)      # dry-run không chown gì thêm


def test_owner_is_none_unless_root_with_a_non_root_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    assert init_mod._owner(tmp_path) is None
    assert init_mod._owner(None) is None


def test_sut_root_defaults_to_cwd_when_the_docker_mount_is_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert init_mod.default_sut_root() == tmp_path or not Path(init_mod.DEFAULT_SUT_MOUNT).is_dir()


# ---------- qua CLI ----------

def test_cli_init_entry_point_and_exit_codes(tmp_path, capsys):
    shutil.copytree(SCAN_FIXTURE, tmp_path / "sut")
    argv = ["init", "--sut-root", str(tmp_path / "sut"), "--slug", "demo", "--openapi", str(VAHAN), "--dry-run"]
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    assert "slug: demo" in out and "would-create" in out and "(dry-run: chưa ghi gì)" in out and "qc-agent:todo" in out
    assert not (tmp_path / "sut" / ".qc-agent").exists()
    assert cli_main(argv[:-1] + ["--openapi", "/nope.json", "--dry-run"]) == 3
    assert "LỖI:" in capsys.readouterr().err
    assert cli_main(["init", "--repo", "o/x"]) == 3 and cli_main(["init", "--projects-dir", "x"]) == 3   # cờ đã bị xoá: exit 3, không phải 2 của argparse


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
        sut = tmp_path / "sut"
        sut.mkdir()
        (sut / "Dockerfile").write_text("FROM python:3.11-slim\nEXPOSE 8000\n", encoding="utf-8")
        assert cli_main(["init", "--sut-root", str(sut), "--slug", "nb", "--openapi", f"{url}/openapi.json"]) == 0
        projects = ROOT / "configs" / "projects"      # `nb` chưa đăng ký => policy _default

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


@pytest.mark.skipif(os.name == "nt", reason="quyền POSIX")
def test_generated_files_are_world_readable_like_ordinary_files_not_0600(tmp_path):
    """mkstemp tạo 0600; gate chạy bằng uid khác trong container nên phải đọc được (bug thật gặp khi chạy `docker run … init` rồi gate)."""
    run_init(tmp_path)
    modes = {p.relative_to(tmp_path / "sut").as_posix(): p.stat().st_mode & 0o777 for p in (tmp_path / "sut" / ".qc-agent").rglob("*") if p.is_file()}
    workflow = (tmp_path / "sut" / ".github" / "workflows" / "qc.yml").stat().st_mode & 0o777
    assert modes and all(mode & 0o044 == 0o044 for mode in modes.values()) and workflow & 0o044 == 0o044


@pytest.mark.skipif(os.name == "nt", reason="umask POSIX")
def test_default_mode_honours_the_umask(monkeypatch):
    mask = os.umask(0o027)
    try:
        assert init_mod._default_mode() == 0o640
    finally:
        os.umask(mask)
