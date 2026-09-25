"""Bước 25: mẫu cấu hình của `qc-agent init`. Kiểm chứng rằng cái sinh ra HỢP LỆ với đúng các bộ nạp/adapter thật, không chỉ "trông đúng"."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from qc_agent.adapters.k6_adapter import K6Adapter
from qc_agent.adapters.schemathesis_adapter import SchemathesisAdapter
from qc_agent.core import plan as plan_lib
from qc_agent.core import project as pj
from qc_agent.scaffold import templates as t

ROOT = Path(__file__).resolve().parent.parent
REUSABLE = ROOT / ".github" / "workflows" / "qc-gate.reusable.yml"
SHA = "9947edff8146903a5848d75cacc12c3b94b18547"
DIGEST = "ghcr.io/muteen-felix/qc-agent@sha256:" + "d" * 64


def write_all(tmp_path, *, ui=True):
    """Đúng bố cục mà `init` sẽ ghi (bước 26): suites + script + flow + project."""
    sut = tmp_path / "sut"
    (sut / ".qc-agent" / "suites").mkdir(parents=True)
    (sut / ".qc-agent" / "perf").mkdir()
    (sut / ".qc-agent" / "midscene").mkdir()
    suites = sut / ".qc-agent" / "suites"
    (suites / "api-contract.yaml").write_text(t.api_contract_suite(exclude=[("/api/jobs/{job_id}/upload-excel", "multipart upload")]), encoding="utf-8")
    (suites / "perf-smoke.yaml").write_text(t.perf_smoke_suite(), encoding="utf-8")
    (sut / ".qc-agent" / "perf" / "smoke.js").write_text(t.k6_smoke_script(paths=["/api/health", "/api/runners"]), encoding="utf-8")
    if ui:
        (suites / "ui-explore.yaml").write_text(t.ui_explore_suite(entry_path="/"), encoding="utf-8")
        (sut / ".qc-agent" / "midscene" / "explore.yaml").write_text(t.midscene_explore_flow(steps=[("aiTap", "tab Settings")]), encoding="utf-8")
        (sut / ".qc-agent" / "midscene" / "canary.yaml").write_text(t.midscene_canary_flow(), encoding="utf-8")
    projects = tmp_path / "projects"    # `myapp` chưa đăng ký => policy `_default` (advisory: perf-smoke, ui-explore)
    projects.mkdir()
    shutil.copy(ROOT / "configs" / "projects" / "_default.yaml", projects / "_default.yaml")
    return sut, projects


# ---------- suite + project qua bộ nạp thật ----------

def test_generated_suites_and_project_load_and_build_both_modes(tmp_path):
    sut, projects = write_all(tmp_path)
    cfg = pj.load_project("myapp", projects)
    suites = pj.load_suites(sut / cfg["suites_dir"] if "suites_dir" in cfg else sut / ".qc-agent" / "suites")
    assert sorted(suites) == ["api-contract", "perf-smoke", "ui-explore"]

    pr, meta = pj.build_plan(cfg, "pr", suites)
    lanes = {task["task_id"]: task["lane"] for task in pr["tasks"]}
    assert lanes == {"t-001": "gate", "t-102": "discovery", "t-101": "discovery", "t-canary-01": "discovery"}  # chỉ api-contract chặn merge
    assert meta["on_skipped_gate_task"] == "fail"
    manual, _ = pj.build_plan(cfg, "manual", suites)
    assert len(manual["tasks"]) == 4


def test_every_generated_task_resolves_to_a_contract_valid_spec(tmp_path, monkeypatch):
    sut, projects = write_all(tmp_path)
    monkeypatch.setenv("APP_BASE_URL", "http://sut:8000")
    monkeypatch.setenv("APP_UI_URL", "http://ui:8080")
    cfg = pj.load_project("myapp", projects)
    plan, _ = pj.build_plan(cfg, "pr", pj.load_suites(sut / ".qc-agent" / "suites"))
    ctx = {"plan_id": "plan-x", "run_id": "r-0001", "runs_dir": str(tmp_path / "runs"), "sut_identity_ref": "sut-x"}
    specs = {task["task_id"]: plan_lib.resolve(task, dict(ctx))[0] for task in plan["tasks"]}  # PlanError nếu vi phạm contract
    assert specs["t-001"]["target"]["base_url"] == "http://sut:8000" and specs["t-101"]["target"]["base_url"] == "http://ui:8080"
    assert specs["t-canary-01"]["capability"] == "ui.explore"


def test_ui_is_optional_project_without_ui_has_no_ui_suite(tmp_path):
    sut, projects = write_all(tmp_path, ui=False)
    cfg = pj.load_project("myapp", projects)
    assert cfg["modes"]["pr"]["advisory_suites"] == ["perf-smoke", "ui-explore"]   # _default; suite ui-explore vắng mặt nên bị bỏ qua
    plan, meta = pj.build_plan(cfg, "pr", pj.load_suites(sut / ".qc-agent" / "suites"))
    assert [task["task_id"] for task in plan["tasks"]] == ["t-001", "t-102"] and meta["absent_advisory_suites"] == ["ui-explore"]


# ---------- adapter thật chấp nhận cái sinh ra ----------

def resolved(tmp_path, monkeypatch, task_id):
    sut, projects = write_all(tmp_path)
    monkeypatch.setenv("APP_BASE_URL", "http://sut:8000")
    monkeypatch.setenv("APP_UI_URL", "http://ui:8080")
    cfg = pj.load_project("myapp", projects)
    plan, _ = pj.build_plan(cfg, "pr", pj.load_suites(sut / ".qc-agent" / "suites"))
    task = next(x for x in plan["tasks"] if x["task_id"] == task_id)
    spec = plan_lib.resolve(task, {"plan_id": "p", "run_id": "r-0001", "runs_dir": str(tmp_path / "runs"), "sut_identity_ref": "s"})[0]
    return sut, spec


def test_schemathesis_adapter_builds_a_command_from_the_generated_suite(tmp_path, monkeypatch):
    _, spec = resolved(tmp_path, monkeypatch, "t-001")
    workdir = tmp_path / "w"
    workdir.mkdir()
    cmd = SchemathesisAdapter().build_cmd(spec, workdir)
    assert cmd[cmd.index("--exclude-path") + 1] == "/api/jobs/{job_id}/upload-excel"
    assert "http://sut:8000/openapi.json" in cmd


def test_k6_adapter_builds_a_command_and_k6_parses_the_generated_script(tmp_path, monkeypatch):
    sut, spec = resolved(tmp_path, monkeypatch, "t-102")
    workdir = tmp_path / "w"
    workdir.mkdir()
    cmd = K6Adapter().build_cmd(spec, workdir)
    assert cmd[cmd.index("--vus") + 1] == "2" and cmd[cmd.index("--duration") + 1] == "10s" and cmd[-1] == ".qc-agent/perf/smoke.js"
    if shutil.which("k6"):  # `k6 inspect` biên dịch script mà không chạy: bắt lỗi cú pháp JS thật
        done = subprocess.run(["k6", "inspect", str(sut / ".qc-agent" / "perf" / "smoke.js")], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr


def test_k6_script_contains_the_paths_as_data_not_code(tmp_path):
    script = t.k6_smoke_script(paths=["/api/health", "/a/b-c_d.e"])
    assert 'const PATHS = ["/api/health", "/a/b-c_d.e"];' in script and "${__ENV.APP_BASE_URL}${path}" in script


def test_midscene_flows_have_the_shape_the_adapter_requires():
    for text in (t.midscene_explore_flow(steps=[("aiTap", "nút Thêm"), ("aiAssert", "danh sách có mục mới")]), t.midscene_canary_flow(),
                 t.midscene_explore_flow()):
        flow = yaml.safe_load(text)
        assert isinstance(flow["tasks"], list) and flow["tasks"] and isinstance(flow["web"], dict) and "url" in flow["web"]  # như MidsceneAdapter.build_cmd đòi
        assert all(isinstance(step, dict) for task in flow["tasks"] for step in task["flow"])


def test_explore_flow_skeleton_is_marked_todo_and_a_filled_one_is_not():
    assert t.TODO in t.midscene_explore_flow() and t.TODO not in t.midscene_explore_flow(steps=[("aiTap", "x")])
    assert t.TODO not in t.midscene_canary_flow()


# ---------- qc.yml đối chiếu input THẬT của workflow tái sử dụng ----------

def reusable_inputs():
    return set(yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))[True]["workflow_call"]["inputs"])


def test_generated_qc_yml_only_uses_inputs_the_reusable_workflow_declares():
    text = t.qc_workflow(project="myapp", qc_ref=SHA, image=DIGEST, sut_port="3000", sut_health_path="/health", sut_env=["A=b", "C=d e"],
                         ui_dockerfile="apps/web-ui/Dockerfile", ui_context="apps/web-ui", ui_port="8080", ui_health_path="/",
                         ui_build_args=["VITE_API_URL=http://sut:3000"])
    job = yaml.safe_load(text)["jobs"]["qc"]
    assert set(job["with"]) <= reusable_inputs()  # chống lệch: workflow đổi tên input thì test này đỏ
    assert job["with"]["project"] == "myapp" and job["with"]["image"] == DIGEST
    assert job["with"]["sut_env"] == "A=b\nC=d e\n" and job["with"]["sut_ui_build_args"] == "VITE_API_URL=http://sut:3000\n"
    assert job["uses"] == f"Muteen-Felix/QC-Agent/.github/workflows/qc-gate.reusable.yml@{SHA}" and job["secrets"] == "inherit"
    assert job["permissions"] == {"contents": "read", "checks": "write", "pull-requests": "write", "packages": "read"}
    assert "suites" not in job["with"]  # policy quyết suite nào chạy
    assert t.TODO not in text


def test_qc_yml_without_pins_carries_todo_markers_and_still_parses():
    text = t.qc_workflow(project="myapp")
    job = yaml.safe_load(text)["jobs"]["qc"]
    assert text.count(t.TODO) == 2 and job["uses"].endswith("@qc-agent-todo-pin-commit-sha") and "<DIGEST>" in job["with"]["image"]
    assert set(job["with"]) == {"project", "image"}


def test_qc_yml_workflow_dispatch_and_pr_triggers_present():
    on = yaml.safe_load(t.qc_workflow(project="myapp"))[True]
    assert on["pull_request"] == {"branches": ["main"]} and "workflow_dispatch" in on


# ---------- an toàn: dữ liệu từ OpenAPI không chèn được cấu trúc ----------

@pytest.mark.parametrize("bad", ['/a"b', "/a b", "/a\nb", "/a#b", "//x?y", "a/b", "/a'b", "/x;rm"])
def test_unsafe_paths_are_refused_everywhere(bad):
    for build in (lambda: t.k6_smoke_script(paths=[bad]), lambda: t.ui_explore_suite(entry_path=bad),
                  lambda: t.api_contract_suite(openapi_path=bad), lambda: t.api_contract_suite(exclude=[(bad, "r")])):
        with pytest.raises(t.TemplateError):
            build()


def test_exclude_reason_cannot_break_out_of_its_comment():
    text = t.api_contract_suite(exclude=[("/a", "up\nload: {evil} # x" + "y" * 300)])
    suite = yaml.safe_load(text)
    assert suite["tasks"][0]["inputs"]["exclude_path"] == ["/a"] and len(suite) == 3  # không thêm khoá nào
    assert all(len(line) < 200 for line in text.splitlines() if "/a" in line)


def test_empty_exclude_drops_the_whole_line():
    assert "exclude_path" not in t.api_contract_suite() and "\n\n" not in t.api_contract_suite()


@pytest.mark.parametrize("kwargs, message", [
    ({"vus": 0}, "vus"), ({"vus": True}, "vus"), ({"vus": 999}, "vus"), ({"duration": "10"}, "duration"),
    ({"duration": "1s\nx: y"}, "duration"), ({"script": "../etc/passwd "}, "script"), ({"script": "a b.js"}, "script")])
def test_perf_smoke_parameters_are_validated(kwargs, message):
    with pytest.raises(t.TemplateError, match=message):
        t.perf_smoke_suite(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"project": "Bad Slug"}, {"project": "a", "qc_ref": "main"}, {"project": "a", "qc_ref": "g" * 40},
    {"project": "a", "image": "ghcr.io/x/y:latest"}, {"project": "a", "sut_env": ["A=b\nC=d"]}, {"project": "a", "sut_env": ["no equals"]},
    {"project": "a", "ui_build_args": ["X\n=y"]}, {"project": "a", "qc_repo": "not a repo"}])
def test_workflow_parameters_are_validated(kwargs):
    with pytest.raises(t.TemplateError):
        t.qc_workflow(**kwargs)


def test_midscene_steps_only_allow_known_commands():
    with pytest.raises(t.TemplateError, match="không được phép"):
        t.midscene_explore_flow(steps=[("evaluateJavaScript", "alert(1)")])
    with pytest.raises(t.TemplateError):
        t.midscene_explore_flow(steps=[("aiTap", "  ")])
    with pytest.raises(t.TemplateError):
        t.midscene_explore_flow(steps=[])
    text = t.midscene_explore_flow(steps=[("aiTap", 'nút "Lưu"\nx: y')])
    assert yaml.safe_load(text)["tasks"][0]["flow"] == [{"aiTap": 'nút "Lưu"\nx: y'}]  # ký tự đặc biệt chỉ là dữ liệu


# ---------- renderer ----------

def test_render_requires_exactly_the_templates_placeholders():
    with pytest.raises(t.TemplateError, match="không khớp"):
        t.render("k6-smoke.js.tmpl", {})
    with pytest.raises(t.TemplateError, match="không khớp"):
        t.render("k6-smoke.js.tmpl", {"paths": "[]", "extra": "x"})


def test_inline_placeholder_cannot_carry_newlines():
    with pytest.raises(t.TemplateError, match="xuống dòng"):
        t.render("perf-smoke.yaml.tmpl", {"script": "s", "vus": "1", "duration": "1s\nx: y"})


def test_every_template_is_used_by_a_builder_and_every_builder_fills_all_placeholders(tmp_path):
    used = set()
    original = t.render
    try:
        t.render = lambda name, values: (used.add(name), original(name, values))[1]
        t.api_contract_suite(); t.perf_smoke_suite(); t.k6_smoke_script(paths=["/x"]); t.ui_explore_suite()
        t.midscene_explore_flow(); t.midscene_canary_flow(); t.qc_workflow(project="a")
        t.ui_dockerfile(node_major=22, lockfile="package-lock.json", output_dir="dist")
    finally:
        t.render = original
    assert used == set(t.template_names())


def test_templates_ship_inside_the_package_directory():
    names = t.template_names()
    assert len(names) == 8 and all((ROOT / "src" / "qc_agent" / "scaffold" / "tmpl" / n).is_file() for n in names)


# ---------- bước 32-33: Dockerfile.ui, khối REFINE, dấu TODO 4 dạng ----------

@pytest.mark.parametrize("lock, install, pm", [("package-lock.json", "RUN npm ci", "npm"),
                                               ("pnpm-lock.yaml", "RUN corepack enable && pnpm install --frozen-lockfile", "pnpm"),
                                               ("yarn.lock", "RUN corepack enable && yarn install --frozen-lockfile", "yarn")])
def test_ui_dockerfile_uses_the_lockfile_it_was_given(lock, install, pm):
    text = t.ui_dockerfile(node_major=20, lockfile=lock, output_dir="build", arg_names=["REACT_APP_API_URL", "REACT_APP_X"])
    assert f"COPY package.json {lock} ./" in text and install in text and f"RUN {pm} run build" in text and text.startswith("# qc-agent:generated")
    assert "FROM node:20-alpine AS build" in text and "ARG REACT_APP_API_URL\nARG REACT_APP_X" in text and "COPY --from=build /app/build /usr/share/nginx/html" in text
    assert 'COPY <<"NGINX"' in text and "listen 8080;" in text and "try_files $uri $uri/ /index.html;" in text    # nginx.conf nhúng, một file duy nhất


def test_ui_dockerfile_without_build_args_has_no_arg_line_and_no_secrets():
    text = t.ui_dockerfile(node_major=22, lockfile="package-lock.json", output_dir="dist")
    assert "ARG" not in text.replace("ARG_", "") and t.TODO not in text


@pytest.mark.parametrize("kwargs", [dict(lockfile="none.lock"), dict(node_major=2), dict(node_major=True), dict(output_dir="../x"),
                                    dict(output_dir="a b"), dict(arg_names=["A B"]), dict(arg_names=["A\nRUN evil"])])
def test_ui_dockerfile_refuses_unsafe_or_unknown_values(kwargs):
    base = dict(node_major=22, lockfile="package-lock.json", output_dir="dist")
    with pytest.raises(t.TemplateError):
        t.ui_dockerfile(**{**base, **kwargs})


def test_refine_blocks_are_delimited_and_the_suites_stay_valid():
    contract = t.api_contract_suite(refine=True)
    assert contract.count("qc-agent:begin refine exclude_path") == 1 and contract.count("qc-agent:end") == 1 and "todo REFINE:" in contract
    task = yaml.safe_load(contract)["tasks"][0]
    assert "exclude_path" not in task["inputs"]
    k6 = t.k6_smoke_script(paths=["/api/health"], refine=True)
    assert k6.index("qc-agent:begin refine k6_paths") < k6.index("const PATHS") < k6.index("qc-agent:end")
    with pytest.raises(t.TemplateError):
        t.api_contract_suite(refine=True, exclude=[("/x", "r")])
    assert "refine" not in t.api_contract_suite(exclude=[("/x", "r")]) and "refine" not in t.k6_smoke_script(paths=["/a"])


def test_todo_mark_has_four_forms_and_rejects_unknown_kinds():
    assert t.todo_mark(None, "x") == "qc-agent:todo x"
    assert [t.todo_mark(k, "a\nb") for k in t.TODO_KINDS] == [f"qc-agent:todo {k}: a b" for k in ("VERIFY", "REFINE", "SUGGESTED")]
    with pytest.raises(t.TemplateError):
        t.todo_mark("MAYBE", "x")


def test_workflow_marks_annotate_the_right_line_and_keep_the_yaml_valid():
    text = t.qc_workflow(project="a", qc_ref=SHA, image=DIGEST, sut_dockerfile="api/Dockerfile", sut_context="api", sut_port="5000",
                         sut_env=["A=1"], ui_dockerfile=".qc-agent/Dockerfile.ui", ui_context="web", ui_build_args=["VITE_API_URL=http://sut:5000"],
                         marks={"sut_port": t.todo_mark("VERIFY", "cổng: mặc định"), "sut_env": t.todo_mark("VERIFY", "chọn X"),
                                "sut_ui_context": t.todo_mark("VERIFY", "chọn web")})
    with_ = yaml.safe_load(text)["jobs"]["qc"]["with"]
    assert with_["sut_dockerfile"] == "api/Dockerfile" and with_["sut_context"] == "api" and with_["sut_port"] == "5000"
    assert with_["sut_env"].strip() == "A=1" and with_["sut_ui_build_args"].strip() == "VITE_API_URL=http://sut:5000"   # comment không lọt vào giá trị
    lines = {line.split(":")[0].strip(): line for line in text.splitlines() if "qc-agent:todo VERIFY" in line}
    assert set(lines) == {"sut_port", "sut_env", "sut_ui_context"}
    with pytest.raises(t.TemplateError):
        t.qc_workflow(project="a", marks={"nope": "x"})
    with pytest.raises(t.TemplateError):
        t.qc_workflow(project="a", sut_dockerfile="../evil")
