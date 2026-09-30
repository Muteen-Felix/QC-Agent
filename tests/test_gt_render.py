"""groundtruth/render.py (S1-05): catalog -> file trong repo SUT, tất định, điều khiển bằng dữ liệu; runtime chạy TC `approved` qua HTTP.

Golden: `tests/fixtures/gt/noteboard/expected/**` (đặt QC_UPDATE_GOLDEN=1 để ghi lại sau khi CỐ Ý đổi mẫu, rồi đọc `git diff`).
Các test tích hợp bật toyapp thật (`QC_BUGS=none` = SUT sạch, `1,2,3` = SUT có lỗi cài sẵn) trên cổng trống và chạy pytest thật trong tiến trình con.
"""
import copy
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
import yaml

from qc_agent.adapters.pytest_adapter import PytestAdapter
from qc_agent.core import plan, project, schema
from qc_agent.groundtruth import render as r
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.generate import generate
from qc_agent.groundtruth.prd import parse_prd
from qc_agent.groundtruth.render import GTRenderError, render, story_slug, write
from qc_agent.scaffold import openapi

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
EXPECTED = FIXTURES / "gt" / "noteboard" / "expected"
GT = ".qc-agent/ground-truth"
TESTS_GT = f"{GT}/tests_gt"
INJECTION = '"); import os; os.system("x") #\n{{ </script> \n'
NOTEBOARD_APP = FIXTURES / "sut" / "noteboard"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE-KEY-0123456789")
    for name in ("ANTHROPIC_BASE_URL", "QC_LLM_TIMEOUT_S", "APP_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def analysis():
    return openapi.analyze(openapi.load(str(FIXTURES / "openapi" / "noteboard.json")))


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    """Catalog THẬT của noteboard: đi qua `generate` với response soạn tay (S1-04), không copy tay."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-FAKE-KEY-0123456789"
    prd = parse_prd(FIXTURES / "prd" / "noteboard-prd.md", openapi_source=str(FIXTURES / "openapi" / "noteboard.json"))
    response = json.loads((FIXTURES / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))
    result = generate(prd, model="claude-sonnet-5", egress_dir=tmp_path_factory.mktemp("egress"), source="docs/prd/noteboard-prd.md",
                      transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
    return result.catalog


def files_of(rendered):
    return {f.path: f.content for f in rendered}


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------- golden + tất định ----------------

def test_render_matches_the_golden_files_byte_for_byte(tmp_path, catalog, analysis):
    rendered = files_of(render(catalog, sut_root=tmp_path, openapi=analysis))
    if os.environ.get("QC_UPDATE_GOLDEN") == "1":
        shutil.rmtree(EXPECTED, ignore_errors=True)
        write(render(catalog, sut_root=EXPECTED, openapi=analysis), EXPECTED)
    expected = tree(EXPECTED)
    assert sorted(rendered) == sorted(expected)
    for path, content in rendered.items():
        assert content.encode("utf-8") == expected[path], path


def test_rendering_twice_is_identical_and_written_bytes_are_clean(tmp_path, catalog, analysis):
    first, second = tmp_path / "a", tmp_path / "b"
    write(render(catalog, sut_root=first, openapi=analysis), first)
    write(render(copy.deepcopy(catalog), sut_root=second, openapi=analysis), second)
    assert tree(first) == tree(second)
    for path, raw in tree(first).items():
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n") and not raw.endswith(b"\n\n"), path


def test_output_does_not_depend_on_the_key_order_of_the_input_dicts(tmp_path, catalog, analysis):
    def reverse(node):
        if isinstance(node, dict):
            return {key: reverse(node[key]) for key in reversed(list(node))}
        return [reverse(item) for item in node] if isinstance(node, list) else node
    assert files_of(render(reverse(catalog), sut_root=tmp_path, openapi=analysis)) == files_of(render(catalog, sut_root=tmp_path, openapi=analysis))


def test_the_catalog_file_round_trips_and_its_header_only_names_provenance(tmp_path, catalog, analysis):
    text = files_of(render(catalog, sut_root=tmp_path, openapi=analysis))[f"{GT}/test-cases.yaml"]
    assert text.startswith("# qc-agent:generated gt")
    first = text.splitlines()[0]
    assert catalog["prd"]["sha256"] in first and "gt-generate/1" in first and "claude-sonnet-5" in first
    assert yaml.safe_load(text) == catalog and gt_schema.validate_catalog(yaml.safe_load(text)) == []
    assert catalog["status"] == "draft" and {tc["status"] for tc in catalog["test_cases"]} == {"draft"}
    for word in ("usage", "input_tokens", "duration"):
        assert word not in text


def test_render_keeps_the_status_it_is_given_so_regen_can_preserve_reviewed_cases(tmp_path, catalog):
    reviewed = copy.deepcopy(catalog)
    reviewed["test_cases"][0]["status"] = "approved"
    reviewed["test_cases"][1].update(status="rejected", rejected_reason="ngoài phạm vi")
    text = files_of(render(reviewed, sut_root=tmp_path))[f"{GT}/test-cases.yaml"]
    assert yaml.safe_load(text) == reviewed
    py = {path: body for path, body in files_of(render(reviewed, sut_root=tmp_path)).items() if path.endswith(".py")}
    assert py == {path: body for path, body in files_of(render(catalog, sut_root=tmp_path)).items() if path.endswith(".py")}   # .py không phụ thuộc status


def test_the_file_set_and_paths(tmp_path, catalog, analysis):
    assert sorted(files_of(render(catalog, sut_root=tmp_path, openapi=analysis))) == sorted([
        f"{GT}/module-map.yaml", f"{GT}/test-cases.yaml", f"{TESTS_GT}/conftest.py", f"{TESTS_GT}/pytest.ini",
        *(f"{TESTS_GT}/test_us_{n}.py" for n in (1, 2, 3, 4)), ".qc-agent/suites/api-contract.yaml", ".qc-agent/suites/gt-functional.yaml"])


# ---------------- chống chèn mã ----------------

def evil_catalog(catalog):
    evil = copy.deepcopy(catalog)
    evil["stories"][0]["title"] = INJECTION
    evil["test_cases"][0]["title"] = INJECTION
    evil["stories"][1]["acs"][0]["text"] = INJECTION
    evil["uncovered_acs"][0]["reason"] = INJECTION
    return evil


def test_llm_and_prd_strings_never_reach_python_files_and_file_names_stay_slugs(tmp_path, catalog):
    evil = evil_catalog(catalog)
    rendered = render(evil, sut_root=tmp_path)
    for file in rendered:
        if file.path.endswith(".py"):
            for needle in ("os.system", "</script>", '"); import', "{{ </", "x\")"):
                assert needle not in file.content, (file.path, needle)
    story_tests = [f for f in rendered if Path(f.path).name.startswith("test_")]
    assert sorted(Path(f.path).name for f in story_tests) == [f"test_us_{n}.py" for n in (1, 2, 3, 4)]
    for file in story_tests:
        assert file.content == '# qc-agent:generated gt — test của MỘT story. KHÔNG sửa tay: các test case nằm trong ../test-cases.yaml, chạy bởi conftest.py.\n' \
                               f'STORY_ID = "US-{file.path[-4]}"\n\n\ndef test_story(tc, gt_run):\n    gt_run(tc)\n'
    catalog_text = files_of(rendered)[f"{GT}/test-cases.yaml"]
    assert yaml.safe_load(catalog_text) == evil                       # dữ liệu được giữ nguyên văn, chỉ là DỮ LIỆU YAML
    assert not [line for line in catalog_text.splitlines() if line.startswith("import ") or line.startswith("os.system")]


def test_the_conftest_is_the_same_fixed_code_whatever_the_catalog(tmp_path, catalog):
    other = evil_catalog(catalog)
    other["stories"].pop()
    other["test_cases"] = [tc for tc in other["test_cases"] if tc["ac_refs"][0] in {ac["ac_id"] for s in other["stories"] for ac in s["acs"]}]
    key = f"{TESTS_GT}/conftest.py"
    assert files_of(render(other, sut_root=tmp_path))[key] == files_of(render(catalog, sut_root=tmp_path))[key]
    assert "eval(" not in files_of(render(catalog, sut_root=tmp_path))[key] and "exec(" not in files_of(render(catalog, sut_root=tmp_path))[key]


@pytest.mark.parametrize("story_id", ['US-1"); import os #', "US-1\nx", "US-1\n", "US 1", "../x", "", "a" * 65])
def test_a_story_id_outside_the_schema_regex_is_refused_before_any_code_is_written(tmp_path, catalog, story_id):
    bad = copy.deepcopy(catalog)
    bad["stories"][0]["story_id"] = story_id
    with pytest.raises(GTRenderError):
        render(bad, sut_root=tmp_path)


def test_two_stories_with_the_same_slug_are_refused(tmp_path, catalog):
    clash = copy.deepcopy(catalog)
    clash["stories"][1]["story_id"] = "US_1"
    with pytest.raises(GTRenderError, match="cùng slug"):
        render(clash, sut_root=tmp_path)


@pytest.mark.parametrize("story_id, slug", [("US-1", "us_1"), ("US.10-b", "us_10_b"), ("-x-", "x"), ("Ünï", "n"), ("---", "story")])
def test_story_slug_is_derived_from_the_id_only(story_id, slug):
    assert story_slug(story_id) == slug


def test_provenance_values_that_could_break_out_of_the_header_comment_are_refused(tmp_path, catalog):
    for path, value in ((("generated_by", "model"), "x\nimport os"), (("generated_by", "prompt_version"), "gt/1 #"), (("prd", "sha256"), "z" * 64)):
        bad = copy.deepcopy(catalog)
        bad[path[0]][path[1]] = value
        with pytest.raises(GTRenderError):
            render(bad, sut_root=tmp_path)


def test_an_invalid_catalog_is_refused_without_echoing_its_content(tmp_path, catalog):
    bad = copy.deepcopy(catalog)
    bad["test_cases"][0]["kind"] = "SECRETMARK"
    with pytest.raises(GTRenderError) as caught:
        render(bad, sut_root=tmp_path)
    assert "SECRETMARK" not in str(caught.value)
    with pytest.raises(GTRenderError):
        render({"version": 1}, sut_root=tmp_path)


# ---------------- suite, module-map, pytest.ini ----------------

def test_pytest_ini_isolates_the_gt_directory(tmp_path, catalog):
    ini = files_of(render(catalog, sut_root=tmp_path))[f"{TESTS_GT}/pytest.ini"]
    assert "[pytest]\naddopts =\n" in ini and ini.startswith("# qc-agent:generated gt")


def test_generated_suites_load_and_the_gt_task_resolves(tmp_path, catalog, analysis, monkeypatch):
    write(render(catalog, sut_root=tmp_path, openapi=analysis), tmp_path)
    suites = project.load_suites(tmp_path / ".qc-agent" / "suites")
    assert sorted(suites) == ["api-contract", "gt-functional"]
    (task,) = suites["gt-functional"]["tasks"]
    assert (task["task_id"], task["capability"], task["lane"], task["prefer"]) == ("t-030", "api.functional", "gate", ["pytest"])
    assert task["inputs"] == {"paths": [TESTS_GT]} and task["retry"] == {"max": 1, "on": ["error"]}
    assert task["oracle"] == {"kind": "threshold", "assertions": [
        {"metric": "pytest.failures", "op": "==", "value": 0}, {"metric": "pytest.errors", "op": "==", "value": 0},
        {"metric": "pytest.tests", "op": ">=", "value": 1}]}
    assert "api.functional" in json.loads((ROOT / "schemas" / "capabilities.json").read_text(encoding="utf-8"))["capabilities"]
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    spec, plan_only = plan.resolve(copy.deepcopy(task), {"plan_id": "plan-gt", "run_id": "r-0001", "sut_identity_ref": "sut-gt"})
    assert spec["target"]["base_url"] == "http://127.0.0.1:8000" and schema.validate_task(spec) == [] and plan_only["prefer"] == ["pytest"]


def test_an_existing_api_contract_suite_is_never_overwritten(tmp_path, catalog, analysis):
    existing = tmp_path / ".qc-agent" / "suites" / "api-contract.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_text("suite: api-contract\n# của QA, đã chỉnh tay\n", encoding="utf-8")
    files = render(catalog, sut_root=tmp_path, openapi=analysis)
    assert ".qc-agent/suites/api-contract.yaml" not in files_of(files)
    write(files, tmp_path)
    assert existing.read_text(encoding="utf-8") == "suite: api-contract\n# của QA, đã chỉnh tay\n"
    forced = render(catalog, sut_root=tmp_path, openapi=analysis, force=True)
    assert ".qc-agent/suites/api-contract.yaml" in files_of(forced)
    assert existing.read_text(encoding="utf-8").startswith("suite: api-contract\n# của QA")   # render là hàm thuần: chưa ghi gì


def test_write_never_overwrites_without_force_and_reports_what_it_did(tmp_path, catalog, analysis):
    files = render(catalog, sut_root=tmp_path, openapi=analysis)
    assert {o.status for o in write(files, tmp_path)} == {"created"}
    catalog_file = tmp_path / GT / "test-cases.yaml"
    catalog_file.write_text(catalog_file.read_text(encoding="utf-8").replace("draft", "approved", 1), encoding="utf-8")   # QA đã sửa
    edited = catalog_file.read_bytes()
    assert {o.status for o in write(files, tmp_path)} == {"kept"} and catalog_file.read_bytes() == edited
    assert {o.status for o in write(files, tmp_path, force=True)} == {"overwritten"} and catalog_file.read_bytes() != edited


def test_write_refuses_paths_outside_the_sut_root(tmp_path):
    with pytest.raises(GTRenderError, match="ngoài repo"):
        write([r.RenderedFile("../escape.txt", "x")], tmp_path / "sut")
    assert not (tmp_path / "escape.txt").exists()


def test_without_openapi_the_api_contract_suite_gets_a_refine_area_and_modules_come_from_the_catalog(tmp_path, catalog):
    files = files_of(render(catalog, sut_root=tmp_path))
    assert "qc-agent:todo REFINE" in files[".qc-agent/suites/api-contract.yaml"]
    assert [m["name"] for m in yaml.safe_load(files[f"{GT}/module-map.yaml"])["modules"]] == ["notes"]


def test_module_map_is_a_schema_valid_draft_with_todo_paths(tmp_path, catalog, analysis):
    text = files_of(render(catalog, sut_root=tmp_path, openapi=analysis))[f"{GT}/module-map.yaml"]
    data = yaml.safe_load(text)
    assert gt_schema.validate_module_map(data) == [] and data["status"] == "draft"
    (module,) = data["modules"]
    assert module == {"name": "notes", "paths": ["TODO-route-files-of-notes"], "suites": ["api-contract", "gt-functional"], "source": "openapi"}
    assert "qc-agent:todo VERIFY" in text


def test_module_names_come_from_the_first_static_path_segment(tmp_path, catalog):
    custom = copy.deepcopy(catalog)
    template = copy.deepcopy(custom["test_cases"][0]["steps"][0])
    custom["test_cases"] = []
    for number, path in enumerate(["/", "/{id}", "/Notes/{id}", "/v1.2/x", "/a_b/c", "/notes"], 1):
        step = copy.deepcopy(template)
        step["request"]["path"] = path
        custom["test_cases"].append({"tc_id": f"TC-AC-1.1-x{number}", "title": "t", "ac_refs": ["AC-1.1"], "kind": "api_functional",
                                     "status": "draft", "origin": "llm", "steps": [step]})
    text = files_of(render(custom, sut_root=tmp_path))[f"{GT}/module-map.yaml"]
    data = yaml.safe_load(text)
    assert [m["name"] for m in data["modules"]] == ["a-b", "notes", "root", "v1-2"] and gt_schema.validate_module_map(data) == []


def test_an_empty_catalog_still_renders_and_the_module_map_is_empty(tmp_path, catalog):
    empty = copy.deepcopy(catalog)
    empty["test_cases"], empty["uncovered_acs"] = [], []
    data = yaml.safe_load(files_of(render(empty, sut_root=tmp_path))[f"{GT}/module-map.yaml"])
    assert data["modules"] == [] and gt_schema.validate_module_map(data) == []


# ---------------- runtime: hàm của conftest ----------------

@pytest.fixture(scope="module")
def runtime(tmp_path_factory, catalog):
    root = tmp_path_factory.mktemp("runtime")
    write(render(catalog, sut_root=root), root)
    spec = importlib.util.spec_from_file_location("gt_conftest_under_test", root / TESTS_GT / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conftest_slug_is_identical_to_the_renderer_slug(runtime):
    for story_id in ("US-1", "US.10-b", "-x-", "Ünï", "---", "A_B", "story 9"):
        assert runtime.story_slug(story_id) == story_slug(story_id)


def test_json_path_lookup_is_the_closed_subset(runtime):
    body = {"a": {"b": [10, {"c": None}]}, "k-1": True}
    look = runtime._lookup
    assert look(body, "$") == body and look(body, "$.a.b[0]") == 10 and look(body, "$.a.b[1].c") is None and look(body, "$.k-1") is True
    for missing in ("$.x", "$.a.b[2]", "$.a[0]", "$.a.b.c", "a.b", "$..a", "$.a.b[-1]", "$[0]"):
        assert look(body, missing) is runtime._MISSING


@pytest.mark.parametrize("op, found, value, ok", [
    ("eq", 1, 1, True), ("eq", 1, 1.0, True), ("eq", True, 1, False), ("eq", 1, True, False), ("eq", "1", 1, False), ("eq", None, None, True),
    ("eq", [1, {"a": 2}], [1, {"a": 2}], True), ("eq", "a", "b", False),
    ("ne", "a", "b", True), ("ne", "a", "a", False), ("ne", 1, True, True),
    ("exists", None, None, True), ("exists", 0, None, True), ("absent", "MISSING", None, True), ("absent", None, None, False),
    ("type", "x", "string", True), ("type", 1, "integer", True), ("type", True, "integer", False), ("type", True, "number", False),
    ("type", 1.5, "number", True), ("type", 1.5, "integer", False), ("type", None, "null", True), ("type", {}, "object", True),
    ("type", [], "array", True), ("type", True, "boolean", True), ("type", 1, "float", False),
    ("len_eq", "abc", 3, True), ("len_eq", [1, 2], 3, False), ("len_gte", [1, 2], 2, True), ("len_gte", [], 1, False),
    ("len_eq", 123, 3, False), ("len_eq", {"a": 1}, 1, False), ("len_gte", "ab", True, False),
    ("contains", "hello", "ell", True), ("contains", "hello", "z", False), ("contains", "hello", 1, False),
    ("contains", [1, "a"], "a", True), ("contains", [1, "a"], True, False), ("contains", [[1]], 1, False), ("contains", {"a": 1}, "a", False),
    ("nope", 1, 1, False),
])
def test_assertion_semantics(runtime, op, found, value, ok):
    found = runtime._MISSING if found == "MISSING" else found
    assert runtime._holds(op, found, value) is ok


@pytest.mark.parametrize("op", ["eq", "ne", "exists", "type", "len_eq", "len_gte", "contains"])
def test_every_op_but_absent_fails_when_the_path_is_missing(runtime, op):
    assert runtime._holds(op, runtime._MISSING, 1) is False


def test_variables_keep_the_type_when_a_json_string_is_exactly_one_variable(runtime):
    def fail(message):
        raise AssertionError(message)
    fill = runtime._fill
    assert fill({"id": "{{n}}", "s": "a-{{n}}-{{t}}", "list": ["{{n}}"], "k": 5}, {"n": 7, "t": "x"}, fail) == {"id": 7, "s": "a-7-x", "list": [7], "k": 5}
    assert fill("{{o}}", {"o": {"b": [1]}}, fail) == {"b": [1]} and fill("v={{o}}", {"o": {"b": [1]}}, fail) == 'v={"b":[1]}'
    with pytest.raises(AssertionError, match="ghost"):
        fill("{{ghost}}", {}, fail)
    with pytest.raises(AssertionError, match="ghost"):
        fill("x{{ghost}}", {"n": 1}, fail)
    assert fill("{{ n }} {{N}} {{}}", {"n": 1}, fail) == "{{ n }} {{N}} {{}}"   # không phải cú pháp biến: giữ nguyên, không đoán


# ---------------- runtime: chạy pytest thật với toyapp thật ----------------

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(bugs: str):
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir", str(NOTEBOARD_APP), "toyapp.app:app", "--host", "127.0.0.1", "--port", str(port)],
                              env={**os.environ, "QC_BUGS": bugs}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{url}/notes", timeout=1)
            return server, url
        except httpx.HTTPError:
            time.sleep(0.2)
    server.terminate()
    raise RuntimeError("toyapp không lên")


@pytest.fixture(scope="module")
def clean_sut():
    server, url = serve("none")
    yield url
    server.terminate()
    server.wait(timeout=20)


@pytest.fixture(scope="module")
def buggy_sut():
    server, url = serve("1,2,3")
    yield url
    server.terminate()
    server.wait(timeout=20)


def approve(sut: Path, primary_acs: set[str]) -> list[str]:
    path = sut / GT / "test-cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    chosen = []
    for tc in data["test_cases"]:
        if tc["ac_refs"][0] in primary_acs:
            tc["status"] = "approved"
            chosen.append(tc["tc_id"])
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")   # đúng thao tác của QA: sửa YAML, không render lại
    return chosen


def run_pytest(sut: Path, base_url: str | None, *extra):
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    env.pop("APP_BASE_URL", None)
    if base_url is not None:
        env["APP_BASE_URL"] = base_url
    junit = sut / "junit.xml"
    junit.unlink(missing_ok=True)
    done = subprocess.run([sys.executable, "-m", "pytest", TESTS_GT, "-q", "-p", "no:cacheprovider", "--junitxml", str(junit), *extra],
                          cwd=sut, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    cases = []
    if junit.exists():
        cases = [(c.get("name"), "failed" if c.find("failure") is not None else "error" if c.find("error") is not None else
                  "skipped" if c.find("skipped") is not None else "passed") for c in ET.parse(junit).getroot().iter("testcase")]
    return done, cases


@pytest.fixture
def sut(tmp_path, catalog, analysis):
    root = tmp_path / "sut"
    write(render(catalog, sut_root=root, openapi=analysis), root)
    return root


APPROVED_ACS = {"AC-1.2", "AC-2.4", "AC-3.3", "AC-4.2", "AC-1.5"}


def test_only_approved_cases_run_and_they_pass_against_the_clean_sut(sut, clean_sut):
    chosen = approve(sut, APPROVED_ACS)
    assert len(chosen) >= 6
    done, cases = run_pytest(sut, clean_sut)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(name for name, _ in cases) == sorted(f"test_story[{tc_id}]" for tc_id in chosen)   # draft không chạy, cũng không bị đếm là skipped
    assert {state for _, state in cases} == {"passed"}


def test_flow_cases_really_talk_to_the_service_and_a_failing_case_names_the_step(sut, buggy_sut):
    chosen = approve(sut, {"AC-2.5", "AC-4.4", "AC-4.2"})
    done, cases = run_pytest(sut, buggy_sut)
    assert done.returncode == 1
    states = dict(cases)
    assert sum(state == "failed" for state in states.values()) == 2 and sum(state == "passed" for state in states.values()) == len(chosen) - 2
    assert "bước 1: GET /notes/{note_id} trả 500, kỳ vọng [404]" in done.stdout and "bước 2: $.summary eq" in done.stdout   # tc_id + bước + kỳ vọng/thực tế


def test_missing_app_base_url_is_exit_4_not_a_test_failure(sut):
    approve(sut, {"AC-1.2"})
    done, cases = run_pytest(sut, None)
    assert done.returncode == 4 and "thiếu biến môi trường APP_BASE_URL" in done.stdout + done.stderr and cases == []
    done, _ = run_pytest(sut, "ftp://x")
    assert done.returncode == 4 and "phải là http(s)" in done.stdout + done.stderr


def test_nothing_approved_collects_nothing_so_the_gate_cannot_be_green(sut, clean_sut):
    done, cases = run_pytest(sut, clean_sut)
    assert done.returncode == 5 and cases == []           # adapter: pytest.tests = 0 => oracle `pytest.tests >= 1` fail


def test_an_approved_case_that_no_story_file_can_run_is_an_error_not_silence(sut, clean_sut):
    approve(sut, {"AC-4.2"})
    (sut / TESTS_GT / "test_us_4.py").unlink()
    assert run_pytest(sut, clean_sut)[0].returncode == 4
    (sut / TESTS_GT / "test_us_4.py").write_text('STORY_ID = "US-4"\n\n\ndef test_story(tc, gt_run):\n    gt_run(tc)\n', encoding="utf-8")
    assert run_pytest(sut, clean_sut)[0].returncode == 0


def test_an_approved_qa_case_pointing_at_an_unknown_ac_is_an_error(sut, clean_sut):
    path = sut / GT / "test-cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["test_cases"].append({"tc_id": "TC-AC-9.9-qa", "title": "QA gõ nhầm AC", "ac_refs": ["AC-9.9"], "kind": "api_functional", "status": "approved",
                               "origin": "qa", "steps": [{"request": {"method": "GET", "path": "/notes"}, "expect": {"status": [200]}}]})
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    done, _ = run_pytest(sut, clean_sut)
    assert done.returncode == 4 and "TC-AC-9.9-qa" in done.stdout + done.stderr


def test_a_missing_or_broken_catalog_is_an_error(sut, clean_sut):
    (sut / GT / "test-cases.yaml").write_text("test_cases: [\n", encoding="utf-8")
    assert run_pytest(sut, clean_sut)[0].returncode == 4
    (sut / GT / "test-cases.yaml").unlink()
    assert run_pytest(sut, clean_sut)[0].returncode == 4


def test_a_malformed_approved_case_fails_that_case_only(sut, clean_sut):
    path = sut / GT / "test-cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    good = next(tc for tc in data["test_cases"] if tc["ac_refs"][0] == "AC-2.3")
    broken = copy.deepcopy(good)
    broken.update(tc_id="TC-AC-2.3-broken", status="approved", steps=[{"request": {"path": "/notes"}, "expect": {"status": [200]}}])
    good["status"] = "approved"
    data["test_cases"].append(broken)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    done, cases = run_pytest(sut, clean_sut)
    assert done.returncode == 1 and dict(cases) == {f"test_story[{good['tc_id']}]": "passed", "test_story[TC-AC-2.3-broken]": "failed"}
    assert "sai cấu trúc" in done.stdout


def test_redirects_are_not_followed(sut, clean_sut):
    path = sut / GT / "test-cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tc = next(tc for tc in data["test_cases"] if tc["kind"] == "api_contract")
    tc.update(status="approved")
    tc["steps"][0] = {"request": {"method": "GET", "path": "/notes/"}, "expect": {"status": [307]}}   # toyapp chuyển /notes/ -> /notes; theo redirect sẽ thành 200
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    done, _ = run_pytest(sut, clean_sut)
    assert done.returncode == 0, done.stdout


def test_a_url_unsafe_path_param_is_quoted_not_interpreted(sut, clean_sut):
    path = sut / GT / "test-cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tc = next(tc for tc in data["test_cases"] if tc["ac_refs"][0] == "AC-2.4")
    tc["status"] = "approved"
    tc["steps"][0]["request"]["path_params"] = {"note_id": "../notes?x=1#frag"}    # nếu không quote sẽ trở thành GET /notes (200), chứ không phải 404
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    done, _ = run_pytest(sut, clean_sut)
    assert done.returncode == 0, done.stdout


# ---------------- chống lách bằng cấu hình của repo SUT (phát hiện ở S1-03) ----------------

def adapter_spec(base_url: str):
    spec = {"task_id": "t-030", "plan_id": "plan-gt", "run_id": "r-0001", "capability": "api.functional", "lane": "gate", "intent": "gt",
            "target": {"kind": "http_service", "base_url": base_url}, "inputs": {"paths": [TESTS_GT]},
            "oracle": {"kind": "threshold", "assertions": [{"metric": "pytest.failures", "op": "==", "value": 0}, {"metric": "pytest.errors", "op": "==", "value": 0},
                                                           {"metric": "pytest.tests", "op": ">=", "value": 1}]},
            "expected_result_kind": "verdict", "budget": {"wallclock_s": 120, "tokens": 0, "usd": 0}, "determinism": {"seed": 0, "replayable": True},
            "sut_identity_ref": "sut-gt", "evidence_required": ["raw_output", "stdout"], "retry": {"max": 1, "on": ["error"]}}
    assert schema.validate_task(spec) == []
    return spec


def test_sut_pytest_config_and_root_conftest_cannot_hide_a_failing_case(sut, buggy_sut, tmp_path, monkeypatch):
    (failing,) = approve(sut, {"AC-2.5"})
    (sut / "pyproject.toml").write_text(f'[tool.pytest.ini_options]\naddopts = "--deselect {TESTS_GT}/test_us_2.py::test_story[{failing}]"\n', encoding="utf-8")
    (sut / "conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items[:] = []\n", encoding="utf-8")
    monkeypatch.chdir(sut)
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("APP_BASE_URL", buggy_sut)
    result = PytestAdapter().run(adapter_spec(buggy_sut))
    assert result["status"] == "fail", result["adapter_notes"]           # TC đang fail VẪN fail, không bị deselect
    assert result["metrics"]["pytest.failures"] == 1 and result["metrics"]["pytest.tests"] == 1
    (sut / TESTS_GT / "pytest.ini").unlink()                            # thiếu file cô lập: worker từ chối chạy (fail-closed) thay vì để gate xanh giả
    assert PytestAdapter().run(adapter_spec(buggy_sut))["status"] == "error"


def test_without_the_isolating_ini_the_sut_config_would_really_hide_the_failure(sut, buggy_sut):
    """Kiểm chứng rằng bài trên không xanh oan: bỏ pytest.ini rồi chạy pytest thẳng thì cấu hình SUT làm TC đang fail biến mất."""
    (failing,) = approve(sut, {"AC-2.5"})
    (sut / TESTS_GT / "pytest.ini").unlink()
    (sut / "pyproject.toml").write_text(f'[tool.pytest.ini_options]\naddopts = "--deselect {TESTS_GT}/test_us_2.py::test_story[{failing}]"\n', encoding="utf-8")
    done, cases = run_pytest(sut, buggy_sut)
    assert done.returncode == 5 and cases == []


# ---------------- ranh giới ----------------

def test_render_module_has_no_llm_and_core_does_not_import_it():
    source = (ROOT / "src" / "qc_agent" / "groundtruth" / "render.py").read_text(encoding="utf-8")
    assert "qc_agent.llm" not in source and "httpx" not in source and "eval(" not in source and "subprocess" not in source
    code = ("import sys; import qc_agent.core.cli, qc_agent.core.engine; "
            "bad = sorted(m for m in sys.modules if m.split('.')[:2] in (['qc_agent', 'llm'], ['qc_agent', 'groundtruth'], ['qc_agent', 'selector'])); "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
