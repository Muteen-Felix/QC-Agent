"""Worker `pytest` (capability api.functional): JUnit XML -> metric/finding, oracle `threshold` phán. Fixture XML là output THẬT của pytest 9.1.1.

Các test tích hợp chạy pytest thật trong tmp_path (không mạng); các test còn lại tiêm CompletedProcess giả để phủ đủ exit code.
"""
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from qc_agent.adapters import pytest_adapter
from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.pytest_adapter import DEFAULT_PATHS, JUNIT_NAME, MAX_XML_BYTES, STDOUT_NAME, PytestAdapter
from qc_agent.core import registry, schema
from securitykit import completed

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "pytest"
GT_DIR = ".qc-agent/ground-truth/tests_gt"
ASSERTIONS = [{"metric": "pytest.failures", "op": "==", "value": 0}, {"metric": "pytest.errors", "op": "==", "value": 0},
              {"metric": "pytest.tests", "op": ">=", "value": 1}]   # như suite gt-functional (S1-05)
KEYS = {"pytest.tests", "pytest.passed", "pytest.failures", "pytest.errors", "pytest.skipped"}
MARK = "MARK-5e21ab"


def make_spec(inputs=None, assertions=None, *, task_id="t-030", run_id="r-0001") -> dict:
    spec = {
        "task_id": task_id, "plan_id": "plan-gt", "run_id": run_id, "capability": "api.functional", "lane": "gate", "intent": "functional",
        "target": {"kind": "api", "base_url": "http://sut:8000"}, "inputs": {} if inputs is None else inputs,
        "oracle": {"kind": "threshold", "assertions": ASSERTIONS if assertions is None else assertions}, "expected_result_kind": "verdict",
        "budget": {"wallclock_s": 120, "tokens": 0, "usd": 0}, "determinism": {"seed": 0, "replayable": True},
        "sut_identity_ref": "sut-gt", "evidence_required": ["raw_output", "stdout"], "retry": {"max": 1, "on": ["error"]},
    }
    assert schema.validate_task(spec) == []
    return spec


def put_fixture(workdir: Path, name: str) -> Path:
    target = workdir / JUNIT_NAME
    target.write_bytes((FIX / name).read_bytes())
    return target


def parse(tmp_path, fixture: str | None, returncode: int, *, stdout="pytest log", stderr=""):
    if fixture is not None:
        put_fixture(tmp_path, fixture)
    proc = completed(returncode, stdout, stderr, args=[sys.executable, "-m", "pytest"])
    return PytestAdapter().parse_output(proc, tmp_path, make_spec())


def junit(*cases, root="testsuites") -> str:
    """cases: (classname, name, kind, message); kind ∈ pass | failure | error | skipped."""
    body = ""
    for classname, name, kind, message in cases:
        attrs = f"classname={quoteattr(classname)} name={quoteattr(name)}"
        if kind == "pass":
            body += f"<testcase {attrs} />"
        else:
            body += f"<testcase {attrs}><{kind} message={quoteattr(message)}>TRACEBACK-{MARK}</{kind}></testcase>"
    suite = f'<testsuite name="pytest">{body}</testsuite>'
    return f"<{root}>{suite}</{root}>" if root == "testsuites" else suite


# ---------------- build_cmd ----------------

def test_build_cmd_is_exactly_the_documented_argv(tmp_path):
    cmd = PytestAdapter().build_cmd(make_spec(), tmp_path)
    assert cmd == [sys.executable, "-m", "pytest", GT_DIR, "-q", "-p", "no:cacheprovider", "--junitxml", str((tmp_path / JUNIT_NAME).resolve()),
                   "-o", "junit_family=xunit2"]
    assert DEFAULT_PATHS == [GT_DIR]


def test_build_cmd_paths_and_markers(tmp_path):
    spec = make_spec({"paths": ["tests_a", "sub/tests_b"], "markers": "smoke and not slow"})
    cmd = PytestAdapter().build_cmd(spec, tmp_path)
    assert cmd[3:5] == ["tests_a", "sub/tests_b"] and cmd[5] == "-q"
    assert cmd[-2:] == ["-m", "smoke and not slow"]           # markers là MỘT argv
    assert cmd.count("-m") == 2 and cmd[2] == "pytest"          # `-m pytest` của interpreter + `-m <expr>` của pytest
    assert PytestAdapter().build_cmd(make_spec({"paths": []}), tmp_path)[3] == GT_DIR   # danh sách rỗng: quay về mặc định, không chạy cả repo SUT
    assert PytestAdapter().build_cmd(make_spec({"markers": "(a or b)"}), tmp_path)[-1] == "(a or b)"


def test_build_cmd_removes_a_stale_report(tmp_path):
    (tmp_path / JUNIT_NAME).write_text("cũ", encoding="utf-8")
    PytestAdapter().build_cmd(make_spec(), tmp_path)
    assert not (tmp_path / JUNIT_NAME).exists()


@pytest.mark.parametrize("bad", [
    ["../outside"], ["a/../b"], ["/etc"], ["C:\\Windows"], ["\\\\server\\share"], ["-x"], ["--collect-only"], ["-p", "evil"], ["a\nb"], ["a\x00b"], [""], ["  "], [5], [None],
])
def test_dangerous_paths_are_rejected(tmp_path, bad):
    with pytest.raises(AdapterParseError):
        PytestAdapter().build_cmd(make_spec({"paths": bad}), tmp_path)


@pytest.mark.parametrize("bad", ["a; rm -rf /", "a$(id)", "a|b", "a-b", "a&&b", "a\nb", "a`b`", "a'b", "a\"b", "a.b", "a:b", "a,b", "", "   ", 5, ["smoke"], True])
def test_markers_with_unusual_characters_are_rejected(tmp_path, bad):
    with pytest.raises(AdapterParseError):
        PytestAdapter().build_cmd(make_spec({"markers": bad}), tmp_path)


def test_paths_must_be_a_list(tmp_path):
    for bad in ("tests", {"a": 1}, 5):
        with pytest.raises(AdapterParseError):
            PytestAdapter().build_cmd(make_spec({"paths": bad}), tmp_path)


@pytest.mark.parametrize("key", ["extra_args", "args", "addopts", "plugins", "k", "config", "rootdir", "timeout"])
def test_there_is_no_way_to_pass_extra_pytest_arguments(tmp_path, key):
    with pytest.raises(AdapterParseError, match="không hỗ trợ"):
        PytestAdapter().build_cmd(make_spec({key: ["-p", "evil"]}), tmp_path)


def test_pytest_runs_with_a_neutral_environment():
    assert PytestAdapter.env == {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    assert PytestAdapter.NAME == "pytest"


# ---------------- parse_output: fixture thật ----------------

def test_all_pass(tmp_path):
    out = parse(tmp_path, "all_pass.xml", 0)
    assert out.metrics == {"pytest.tests": 2, "pytest.passed": 2, "pytest.failures": 0, "pytest.errors": 0, "pytest.skipped": 0}
    assert out.findings == [] and out.exit_code == 0 and out.tokens == 0 and out.usd == 0.0
    assert [kind for kind, _ in out.evidence_paths] == ["raw_output", "stdout"]
    assert (tmp_path / STDOUT_NAME).read_text(encoding="utf-8") == "pytest log"
    assert any(n.startswith("PARSER_VERSION=") for n in out.adapter_notes) and "pytest exit_code=0" in out.adapter_notes


def test_mixed_run_counts_every_outcome_and_xfail_is_skipped(tmp_path):
    out = parse(tmp_path, "mixed.xml", 1)
    # 9 <testcase>: pass, param(pass), param(fail), fail, error-in-fixture, fail + teardown-error (HAI phần tử cùng tên), skip, xfail
    assert out.metrics == {"pytest.tests": 9, "pytest.passed": 2, "pytest.failures": 3, "pytest.errors": 2, "pytest.skipped": 2}
    assert len(out.findings) == 5 and out.exit_code == 1


def test_findings_are_sorted_stable_and_have_unique_ids(tmp_path):
    first = parse(tmp_path, "mixed.xml", 1).findings
    second = parse(tmp_path, "mixed.xml", 1).findings
    assert [f["finding_id"] for f in first] == [f["finding_id"] for f in second]
    ids = [f["finding_id"] for f in first]
    assert len(set(ids)) == 5 and all(i.startswith("f-pytest-") for i in ids)
    twins = [i for i in ids if i.endswith("-2")]
    assert len(twins) == 1 and twins[0][:-2] in ids       # fail + lỗi teardown của cùng một test: hậu tố -2, không đè nhau
    assert [f["title"].split(" — ")[0] for f in first] == [
        "mixed.test_sample::test_error_in_fixture", "mixed.test_sample::test_fail", "mixed.test_sample::test_fail_and_teardown_error",
        "mixed.test_sample::test_fail_and_teardown_error", "mixed.test_sample::test_param[TC-AC-1.2-9b8c7d]"]


def test_finding_shape_severity_and_detector(tmp_path):
    by_title = {f["title"].split(" — ")[0]: f for f in parse(tmp_path, "mixed.xml", 1).findings}
    param = by_title["mixed.test_sample::test_param[TC-AC-1.2-9b8c7d]"]
    assert param["detected_by"] == "pytest:TC-AC-1.2-9b8c7d"                 # rule_id = tc_id khi tên test có
    assert by_title["mixed.test_sample::test_fail"]["detected_by"] == "pytest:test_fail"   # không có tc_id: tên test
    for finding in by_title.values():
        assert finding["severity_hint"] == "medium" and finding["verdict_source"] == "deterministic_assert" and finding["confidence"] is None
        assert set(finding) == {"finding_id", "title", "detected_by", "verdict_source", "confidence", "severity_hint"}


def test_titles_are_one_clean_line_and_never_contain_the_traceback(tmp_path):
    for finding in parse(tmp_path, "mixed.xml", 1).findings:
        title = finding["title"]
        assert "\n" not in title and "\r" not in title and "\t" not in title and len(title) <= pytest_adapter.TITLE_MAX
        assert "mixed/test_sample.py:" not in title and "Traceback" not in title and "\nE " not in title and "def test_" not in title
    titles = {f["title"] for f in parse(tmp_path, "mixed.xml", 1).findings}
    assert "mixed.test_sample::test_fail — AssertionError: assert {'a': 1} == {'a': 2} Differing items: {'a': 1} != {'a': 2} Use -v to get more diff" in titles
    assert any(t.endswith('failed on setup with "RuntimeError: fixture hỏng với nhiều dòng"') for t in titles)


def test_no_tests_collected_gives_zero_metrics_so_an_empty_gate_is_not_green(tmp_path):
    out = parse(tmp_path, "no_tests.xml", 5)
    assert out.metrics == {"pytest.tests": 0, "pytest.passed": 0, "pytest.failures": 0, "pytest.errors": 0, "pytest.skipped": 0}
    assert out.findings == [] and any("exit 5" in n for n in out.adapter_notes)
    assert [kind for kind, _ in out.evidence_paths] == ["raw_output", "stdout"]
    bare = tmp_path / "bare"
    bare.mkdir()
    no_junit = parse(bare, None, 5)                # exit 5 mà không có junit.xml: chỉ khai evidence có thật
    assert [kind for kind, _ in no_junit.evidence_paths] == ["stdout"] and no_junit.metrics["pytest.tests"] == 0


@pytest.mark.parametrize("fixture", ["all_pass.xml", "mixed.xml", "no_tests.xml"])
def test_metrics_always_carry_all_five_keys(tmp_path, fixture):
    code = {"all_pass.xml": 0, "mixed.xml": 1, "no_tests.xml": 5}[fixture]
    metrics = parse(tmp_path, fixture, code).metrics
    assert set(metrics) == KEYS and all(isinstance(v, int) and not isinstance(v, bool) for v in metrics.values())
    assert metrics["pytest.passed"] + metrics["pytest.skipped"] <= metrics["pytest.tests"]


@pytest.mark.parametrize("code", [2, 3, 4, 6, 7, 127, 137, 255, -9, -11])
def test_every_exit_code_except_0_1_5_is_an_error_and_ignores_the_report(tmp_path, code):
    put_fixture(tmp_path, "collect_error.xml")   # kể cả khi có junit (lỗi collect ghi cả <error>): exit 2 là "bị ngắt", không phải kết quả test
    with pytest.raises(AdapterParseError, match=f"exit code {code}"):
        PytestAdapter().parse_output(completed(code, "log"), tmp_path, make_spec())
    assert (tmp_path / STDOUT_NAME).is_file()    # log vẫn được giữ để người điều tra


def test_exit_code_meaning_is_named_for_the_known_ones(tmp_path):
    for code, word in ((2, "collect"), (3, "nội bộ"), (4, "cách dùng")):
        with pytest.raises(AdapterParseError, match=word):
            PytestAdapter().parse_output(completed(code), tmp_path, make_spec())


# ---------------- mâu thuẫn giữa exit code và JUnit ----------------

@pytest.mark.parametrize("fixture,code", [("mixed.xml", 0), ("all_pass.xml", 1), ("no_tests.xml", 0), ("no_tests.xml", 1)])
def test_contradiction_between_exit_code_and_junit_is_a_parse_error(tmp_path, fixture, code):
    with pytest.raises(AdapterParseError, match="mâu thuẫn|không có testcase"):
        parse(tmp_path, fixture, code)


# ---------------- XML hỏng / quá cỡ / độc hại ----------------

def test_missing_report_is_a_parse_error(tmp_path):
    for code in (0, 1):
        with pytest.raises(AdapterParseError, match="không ghi ra junit.xml"):
            PytestAdapter().parse_output(completed(code), tmp_path, make_spec())


@pytest.mark.parametrize("content", [
    b"", b"not xml at all", (FIX / "mixed.xml").read_bytes()[:400], b"<testsuites><testsuite>", b"<html><body/></html>", b"<?xml version='1.0'?><root/>",
    b"\xff\xfe\x00garbage",
])
def test_broken_or_foreign_xml_is_a_parse_error_without_echoing_content(tmp_path, content):
    (tmp_path / JUNIT_NAME).write_bytes(content)
    with pytest.raises(AdapterParseError) as caught:
        PytestAdapter().parse_output(completed(1), tmp_path, make_spec())
    assert "testcase" not in str(caught.value) and "not xml" not in str(caught.value)


def test_entity_expansion_is_refused_before_parsing(tmp_path):
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            '<testsuites><testsuite><testcase classname="c" name="&b;"><failure message="x"/></testcase></testsuite></testsuites>')
    (tmp_path / JUNIT_NAME).write_text(bomb, encoding="utf-8")
    with pytest.raises(AdapterParseError, match="DOCTYPE"):
        PytestAdapter().parse_output(completed(1), tmp_path, make_spec())


def test_size_cap_is_10_mb(tmp_path):
    valid = (FIX / "all_pass.xml").read_bytes()
    (tmp_path / JUNIT_NAME).write_bytes(valid + b" " * (MAX_XML_BYTES - len(valid)))
    assert (tmp_path / JUNIT_NAME).stat().st_size == MAX_XML_BYTES == 10 * 1024 * 1024
    assert PytestAdapter().parse_output(completed(0), tmp_path, make_spec()).metrics["pytest.tests"] == 2    # đúng cận: còn parse được
    (tmp_path / JUNIT_NAME).write_bytes(valid + b" " * (MAX_XML_BYTES - len(valid) + 1))
    with pytest.raises(AdapterParseError, match="vượt trần"):
        PytestAdapter().parse_output(completed(0), tmp_path, make_spec())


def test_both_testsuites_and_bare_testsuite_roots_are_accepted(tmp_path):
    cases = [("a.b", "test_ok", "pass", ""), ("a.b", "test_bad", "failure", "boom")]
    for root in ("testsuites", "testsuite"):
        (tmp_path / JUNIT_NAME).write_text(junit(*cases, root=root), encoding="utf-8")
        out = PytestAdapter().parse_output(completed(1), tmp_path, make_spec())
        assert out.metrics == {"pytest.tests": 2, "pytest.passed": 1, "pytest.failures": 1, "pytest.errors": 0, "pytest.skipped": 0} and len(out.findings) == 1


def test_a_testcase_with_both_failure_and_error_is_one_finding_and_not_a_pass(tmp_path):
    xml = ('<testsuites><testsuite><testcase classname="a" name="t"><failure message="f"/><error message="e"/></testcase>'
           '<testcase classname="a" name="s"><skipped message="x"/></testcase></testsuite></testsuites>')
    (tmp_path / JUNIT_NAME).write_text(xml, encoding="utf-8")
    out = PytestAdapter().parse_output(completed(1), tmp_path, make_spec())
    assert out.metrics == {"pytest.tests": 2, "pytest.passed": 0, "pytest.failures": 1, "pytest.errors": 1, "pytest.skipped": 1} and len(out.findings) == 1
    assert out.findings[0]["title"] == "a::t — f"


# ---------------- title sạch, dữ liệu của SUT ----------------

def test_title_cleaning_drops_control_characters_and_truncates(tmp_path):
    message = "dòng một\n\tdòng hai\x85ba\u2028bốn\x9fnăm  " + "x" * 400
    (tmp_path / JUNIT_NAME).write_text(junit(("suite.mod", "test_a[TC-X-1]", "failure", message)), encoding="utf-8")
    (finding,) = PytestAdapter().parse_output(completed(1), tmp_path, make_spec()).findings
    title = finding["title"]
    assert len(title) == pytest_adapter.TITLE_MAX and title.endswith("…") and title.startswith("suite.mod::test_a[TC-X-1] — dòng một dòng hai ba bốn năm x")
    assert not any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F for c in title)
    assert f"TRACEBACK-{MARK}" not in title           # nội dung phần tử (traceback) không bao giờ vào title


def test_traceback_text_and_stdout_never_reach_findings_or_notes(tmp_path):
    (tmp_path / JUNIT_NAME).write_text(junit(("c", "t", "failure", "assert 1 == 2"), ("c", "u", "error", "")), encoding="utf-8")
    out = PytestAdapter().parse_output(completed(1, f"log {MARK}", f"err {MARK}"), tmp_path, make_spec())
    dumped = json.dumps([out.findings, out.metrics, out.adapter_notes], ensure_ascii=False)
    assert MARK not in dumped
    log = (tmp_path / STDOUT_NAME).read_text(encoding="utf-8")
    assert f"log {MARK}" in log and "--- stderr ---" in log and f"err {MARK}" in log   # nhưng vẫn nằm trong evidence để người đọc


def test_missing_classname_and_message_and_detector_fallbacks(tmp_path):
    (tmp_path / JUNIT_NAME).write_text('<testsuites><testsuite><testcase name="tests/test_x.py"><error/></testcase>'
                                       '<testcase classname="k" name="test_with_tc[TC-AC-9.9-abcdef]"><failure message=""/></testcase></testsuite></testsuites>',
                                       encoding="utf-8")
    findings = PytestAdapter().parse_output(completed(1), tmp_path, make_spec()).findings
    assert [f["title"] for f in findings] == ["tests/test_x.py", "k::test_with_tc[TC-AC-9.9-abcdef]"]    # không có classname/message: không thêm " — "
    assert [f["detected_by"] for f in findings] == ["pytest:tests/test_x.py", "pytest:TC-AC-9.9-abcdef"]


def test_findings_are_capped_but_metrics_count_everything(tmp_path):
    cases = [("c", f"test_{n:04d}", "failure", "boom") for n in range(250)]
    (tmp_path / JUNIT_NAME).write_text(junit(*cases), encoding="utf-8")
    out = PytestAdapter().parse_output(completed(1), tmp_path, make_spec())
    assert len(out.findings) == 200 and out.metrics["pytest.failures"] == 250 and out.metrics["pytest.tests"] == 250
    assert any("250" in n and "200" in n for n in out.adapter_notes)
    assert len({f["finding_id"] for f in out.findings}) == 200


def test_replay_cmd_is_the_shell_quoted_command(tmp_path):
    proc = completed(0, args=[sys.executable, "-m", "pytest", "a b", "-q"])
    put_fixture(tmp_path, "all_pass.xml")
    assert PytestAdapter().parse_output(proc, tmp_path, make_spec()).replay_cmd == shlex.join(proc.args)


# ---------------- manifest / registry / đóng gói ----------------

def test_manifest_and_capability_are_registered_and_routable(monkeypatch):
    worker = registry.load(ROOT / "workers")["pytest"]
    assert worker.module == "qc_agent.adapters.pytest_adapter" and worker.lanes == ["gate"] and worker.data_egress == []
    capability = worker.capabilities["api.functional"]
    assert capability["oracle_kinds"] == ["threshold"] and capability["verdict_sources"] == ["deterministic_assert"] and capability["parallel_safe"] is True
    assert worker.requires == {"env": [], "binaries": ["python"]} and worker.version_probe == "python -m pytest --version"
    assert "api.functional" in json.loads((ROOT / "schemas" / "capabilities.json").read_text(encoding="utf-8"))["capabilities"]

    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])   # như image (/opt/venv/bin) hoặc .venv đã kích hoạt
    registry.probe(worker)
    assert worker.probe_ok and worker.version.startswith("pytest "), worker.probe_reason
    spec = {"capability": "api.functional", "lane": "gate", "oracle": {"kind": "threshold"}}
    assert registry.pick({"pytest": worker}, spec)[0] is worker
    assert registry.pick({"pytest": worker}, {**spec, "lane": "discovery"})[0] is None              # chỉ lane gate
    assert registry.pick({"pytest": worker}, {**spec, "oracle": {"kind": "checks"}})[0] is None      # chỉ oracle threshold


def test_adapter_module_name_matches_the_manifest_path():
    manifest = (ROOT / "workers" / "pytest.yaml").read_text(encoding="utf-8")
    assert 'adapter: "qc_agent/adapters/pytest_adapter.py"' in manifest
    assert (ROOT / "src" / "qc_agent" / "adapters" / "pytest_adapter.py").is_file()


def test_pytest_is_a_runtime_dependency_because_the_image_syncs_without_dev():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "pytest==9.1.1" in pyproject["project"]["dependencies"]
    assert "pytest" not in json.dumps(pyproject.get("dependency-groups", {}))      # không còn ở nhóm dev (nhóm dev bị bỏ khi build image)
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    ours = next(p for p in lock["package"] if p["name"] == "qc-agent")
    assert "pytest" in [d["name"] for d in ours["dependencies"]] and "dev-dependencies" not in ours
    assert next(p for p in lock["package"] if p["name"] == "pytest")["version"] == "9.1.1"
    assert "uv sync --frozen --no-dev" in (ROOT / "Dockerfile").read_text(encoding="utf-8")   # lý do của việc chuyển nhóm


# ---------------- tích hợp: chạy pytest thật ----------------

@pytest.fixture
def sut(tmp_path, monkeypatch):
    """Thư mục SUT giả trong tmp_path; pytest con chạy với cwd ở đây, nên không đọc cấu hình của repo này."""
    root = tmp_path / "sut"
    (root / GT_DIR).mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    return root


def write_tests(sut: Path, body: str, name="test_gt.py", where=GT_DIR) -> None:
    (sut / where).mkdir(parents=True, exist_ok=True)
    (sut / where / name).write_text(body, encoding="utf-8")


ONE_PASS_ONE_FAIL = "def test_ok():\n    assert 1 + 1 == 2\n\ndef test_bad():\n    assert 1 + 1 == 3, 'kỳ vọng 3'\n"


def test_integration_one_pass_one_fail_is_fail_and_the_result_validates(sut):
    write_tests(sut, ONE_PASS_ONE_FAIL)
    spec = make_spec()
    result = PytestAdapter().run(spec)
    assert result["status"] == "fail", result["adapter_notes"]
    assert result["verdict"]["value"] == "fail" and result["verdict"]["gating"] is True and result["verdict"]["verdict_source"] == "deterministic_assert"
    assert result["metrics"] == {"pytest.tests": 2, "pytest.passed": 1, "pytest.failures": 1, "pytest.errors": 0, "pytest.skipped": 0}
    assert schema.validate_result(result) == [] and schema.check_result_against_spec(spec, result) == []
    detected = {f["detected_by"] for f in result["findings"]}
    assert detected == {"pytest:test_bad", "threshold:pytest.failures"}       # finding của test + finding do oracle threshold sinh ra
    assert {e["kind"] for e in result["evidence"]} == {"raw_output", "stdout"} and all(len(e["sha256"]) == 64 for e in result["evidence"])
    assert result["cost"]["tokens"] == 0 and result["worker"]["name"] == "pytest"


def test_integration_all_pass_is_pass(sut):
    write_tests(sut, "def test_a():\n    assert True\n")
    result = PytestAdapter().run(make_spec())
    assert result["status"] == "pass" and result["findings"] == [] and result["metrics"]["pytest.passed"] == 1


def test_integration_an_empty_test_directory_is_a_fail_not_a_green_gate(sut):
    result = PytestAdapter().run(make_spec())    # thư mục tồn tại nhưng không có test nào -> pytest exit 5
    assert result["status"] == "fail" and result["metrics"]["pytest.tests"] == 0
    assert [f["detected_by"] for f in result["findings"]] == ["threshold:pytest.tests"]


def test_integration_missing_directory_broken_test_file_and_bad_marker_are_errors(sut):
    (sut / GT_DIR).rmdir()
    assert PytestAdapter().run(make_spec())["status"] == "error"             # đường dẫn không tồn tại: exit 4
    write_tests(sut, "def test_a(:\n    pass\n")                              # lỗi cú pháp -> lỗi collect: exit 2
    broken = PytestAdapter().run(make_spec())
    assert broken["status"] == "error" and "exit code 2" in broken["verdict"]["rationale"]
    write_tests(sut, "def test_a():\n    pass\n")
    assert PytestAdapter().run(make_spec({"markers": "(("}))["status"] == "error"   # biểu thức marker sai: exit 4


def test_integration_markers_select_a_subset(sut):
    write_tests(sut, "import pytest\n\n@pytest.mark.smoke\ndef test_fast():\n    assert True\n\ndef test_other():\n    assert False\n")
    result = PytestAdapter().run(make_spec({"markers": "smoke"}))
    assert result["status"] == "pass" and result["metrics"]["pytest.tests"] == 1
    assert PytestAdapter().run(make_spec({"markers": "not smoke"}))["status"] == "fail"


def test_integration_custom_paths_and_pytest_plugin_autoload_and_addopts_are_neutralised(sut, monkeypatch):
    write_tests(sut, "import os\n\ndef test_env_is_neutral():\n    assert os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'\n"
                     "    assert os.environ['PYTEST_ADDOPTS'] == ''\n\ndef test_second():\n    assert True\n", where="custom/tests")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing_matches --maxfail=1")     # cờ kế thừa từ môi trường không được làm thay đổi phiên chạy
    result = PytestAdapter().run(make_spec({"paths": ["custom/tests"]}))
    assert result["status"] == "pass" and result["metrics"]["pytest.tests"] == 2, result["adapter_notes"]


def test_integration_a_pytest_ini_next_to_the_tests_shields_them_from_sut_config(sut):
    """Cấu hình/conftest của repo SUT (không tin cậy, nằm ngoài .qc-agent) không được làm biến mất test GT đang fail. Xem "GIỚI HẠN ĐÃ BIẾT" trong adapter."""
    write_tests(sut, ONE_PASS_ONE_FAIL)
    (sut / "pyproject.toml").write_text(f'[tool.pytest.ini_options]\naddopts = "--deselect {GT_DIR}/test_gt.py::test_bad"\n', encoding="utf-8")
    (sut / "conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items[:] = [i for i in items if i.name != 'test_bad']\n", encoding="utf-8")
    (sut / GT_DIR / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
    result = PytestAdapter().run(make_spec())
    assert result["status"] == "fail" and result["metrics"]["pytest.tests"] == 2 and result["metrics"]["pytest.failures"] == 1, result["adapter_notes"]


def test_integration_a_broken_task_input_is_an_error_result_not_a_crash(sut):
    result = PytestAdapter().run(make_spec({"paths": ["../etc"]}))
    assert result["status"] == "error" and result["verdict"]["rationale"].startswith("parse:")
    assert PytestAdapter().run(make_spec({"extra_args": ["-p", "evil"]}))["status"] == "error"


def test_integration_runs_as_python_dash_m_module_and_writes_a_valid_result(sut, tmp_path):
    write_tests(sut, ONE_PASS_ONE_FAIL)
    spec_file, out_file = tmp_path / "spec.json", tmp_path / "result.json"
    spec_file.write_text(json.dumps(make_spec()), encoding="utf-8")
    env = {**os.environ, "QC_RUNS_DIR": str(tmp_path / "runs2"), "PYTHONUTF8": "1"}
    done = subprocess.run([sys.executable, "-m", "qc_agent.adapters.pytest_adapter", "--spec", str(spec_file), "--out", str(out_file)],
                          cwd=sut, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr           # luôn exit 0: verdict nằm trong JSON
    result = json.loads(out_file.read_text(encoding="utf-8"))
    assert result["status"] == "fail" and schema.validate_result(result) == []
