"""`qc-agent gt generate | validate | regen` (S1-06): CLI, merge khi PRD đổi, cổng HITL.

LLM là `FakeAnthropic` (HTTP server giả, `ANTHROPIC_BASE_URL`) phát `tests/fixtures/llm/gt_noteboard_response.json`; không có mạng thật.
CLI chạy qua `qc_agent.core.cli.main(["gt", ...])`, đúng đường mà người dùng đi (kể cả cấu hình log ra stderr).
"""
import copy
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.fakes import FakeAnthropic
from qc_agent.core import egress
from qc_agent.core.cli import main as cli_main
from qc_agent.groundtruth import check as gt_check
from qc_agent.groundtruth import render as gt_render
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.merge import merge
from qc_agent.llm.client import LLMError, call_tool
from qc_agent.scaffold import validate as v

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
PRD_FILE = FIXTURES / "prd" / "noteboard-prd.md"
OPENAPI = FIXTURES / "openapi" / "noteboard.json"
RESPONSE = FIXTURES / "llm" / "gt_noteboard_response.json"
EXPECTED = FIXTURES / "gt" / "noteboard" / "expected"
GT = ".qc-agent/ground-truth"
KEY = "sk-ant-FAKE-KEY-0123456789"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    for name in ("ANTHROPIC_BASE_URL", "QC_GT_MODEL", "QC_RUNS_DIR", "QC_LOG_FORMAT", "QC_LOG_LEVEL", "QC_JOB_ID", "QC_PROJECT", "QC_LLM_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)   # egress mặc định là $QC_RUNS_DIR/gt = ./runs/gt: nằm trong tmp_path, không vào repo này


@pytest.fixture
def fake(monkeypatch):
    with FakeAnthropic(RESPONSE, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        yield server


def make_sut(tmp_path, name="sut", prd_text=None) -> Path:
    sut = tmp_path / name
    (sut / "docs" / "prd").mkdir(parents=True)
    (sut / "docs" / "prd" / "noteboard-prd.md").write_text(prd_text if prd_text is not None else PRD_FILE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return sut


def gt(capsys, *args):
    capsys.readouterr()
    code = cli_main(["gt", *map(str, args)])
    out = capsys.readouterr()
    return code, out.out, out.err


def generate_args(sut, *extra, prd="docs/prd/noteboard-prd.md", openapi=True, egress=None):
    args = ["generate", "--prd", sut / prd, "--sut-root", sut]
    if openapi:
        args += ["--openapi", OPENAPI]
    if egress is not None:
        args += ["--egress-dir", egress]
    return [*args, *extra]


def generated(tmp_path, capsys, name="sut", **kwargs) -> Path:
    sut = make_sut(tmp_path, name, **kwargs)
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / f"egress-{name}"))
    assert code == 0, out + err
    return sut


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def load_catalog(sut: Path) -> dict:
    return yaml.safe_load((sut / GT / "test-cases.yaml").read_text(encoding="utf-8"))


def save_catalog(sut: Path, data: dict) -> None:
    (sut / GT / "test-cases.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")


def approve_everything(sut: Path, *, keep_draft=()) -> None:
    """Thao tác của QA: duyệt hết TC và catalog, điền module-map rồi đổi sang approved."""
    data = load_catalog(sut)
    for tc in data["test_cases"]:
        if tc["tc_id"] not in keep_draft:
            tc["status"] = "approved"
    data["status"] = "approved" if not keep_draft else "draft"
    save_catalog(sut, data)
    path = sut / GT / "module-map.yaml"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if "qc-agent:todo" not in line]
    path.write_text("\n".join(lines).replace("status: draft", "status: approved").replace("TODO-route-files-of-notes", "toyapp/**") + "\n", encoding="utf-8")


def dump(entry) -> str:
    return yaml.safe_dump(entry, sort_keys=True, allow_unicode=True)


# ---------------- generate ----------------

def test_generate_reproduces_the_golden_tree_and_two_runs_are_byte_identical(tmp_path, capsys, fake):
    first, second = generated(tmp_path, capsys, "one"), generated(tmp_path, capsys, "two")
    golden = tree(EXPECTED / ".qc-agent")
    assert tree(first / ".qc-agent") == golden == tree(second / ".qc-agent")
    assert fake.count == 2 and all(not r["rejected"] and r["headers"]["x-api-key"] == KEY and r["headers"]["anthropic-version"] for r in fake.requests)


def test_generate_writes_a_machine_readable_summary_and_prints_a_human_one(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    summary_path = tmp_path / "out" / "summary.json"
    code, out, err = gt(capsys, *generate_args(sut, "--summary-json", summary_path, egress=tmp_path / "egress"))
    assert code == 0, err
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    prd_text = PRD_FILE.read_text(encoding="utf-8")
    assert (summary["command"], summary["stories"], summary["acs"], summary["test_cases"]) == ("generate", 4, 25, 33)
    assert summary["by_status"] == {"draft": 33, "approved": 0, "rejected": 0} and summary["orphans"] == ["AC-3.5"] and summary["uncovered_acs"] == ["AC-1.8"]
    assert summary["model"] == "claude-sonnet-5" and summary["prompt_version"] == "gt-generate/1" and summary["dropped_test_cases"] == 5
    assert summary["prd_sha256"] == load_catalog(sut)["prd"]["sha256"] and summary["prd_source"] == "docs/prd/noteboard-prd.md" and summary["prd_id"] == "noteboard"
    assert summary["usage"] == {"input_tokens": 4210, "output_tokens": 9350, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert any("gộp" in w for w in summary["warnings"]) and {f["status"] for f in summary["files"]} == {"created"}
    assert "AC mồ côi" in out and "AC-3.5" in out and "33 test case" in out
    assert "Noteboard là dịch vụ" not in out and "Noteboard là dịch vụ" not in json.dumps(summary, ensure_ascii=False)   # không có văn bản PRD


def test_egress_goes_to_egress_dir_or_runs_and_never_into_the_sut_qc_agent_dir(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    assert gt(capsys, *generate_args(sut))[0] == 0
    assert (tmp_path / "runs" / "gt" / egress.LOG_NAME).is_file()
    assert not list((sut / ".qc-agent").rglob("egress.jsonl"))
    (line,) = [json.loads(x) for x in (tmp_path / "runs" / "gt" / egress.LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert line["worker"] == "qc-agent-gt-generate" and line["categories"] == ["api_spec", "prd_text"]


def test_an_egress_dir_inside_the_locked_directory_is_refused_before_anything_is_sent(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    code, out, err = gt(capsys, *generate_args(sut, egress=sut / ".qc-agent" / "runs"))
    assert code == 3 and ".qc-agent" in err and fake.count == 0 and not (sut / ".qc-agent").exists()


def test_generate_refuses_an_existing_catalog_and_points_at_regen(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    before = tree(sut / ".qc-agent")
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e2"))
    assert code == 3 and "regen" in err and fake.count == 1 and tree(sut / ".qc-agent") == before
    data = load_catalog(sut)
    data["test_cases"][0]["status"] = "approved"
    save_catalog(sut, data)
    code, *_ = gt(capsys, *generate_args(sut, "--force", egress=tmp_path / "e3"))
    assert code == 0 and load_catalog(sut)["test_cases"][0]["status"] == "draft" and fake.count == 2   # --force ghi đè, MẤT thứ QA đã duyệt (đã cảnh báo ở --help)


def test_without_openapi_generate_still_works_and_marks_the_api_contract_suite_for_refine(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    code, out, err = gt(capsys, *generate_args(sut, openapi=False, egress=tmp_path / "e"))
    assert code == 0, err
    assert "qc-agent:todo REFINE" in (sut / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8")
    assert "không kiểm được endpoint" in out


def test_an_existing_api_contract_suite_is_kept(tmp_path, capsys, fake):
    sut = make_sut(tmp_path)
    (sut / ".qc-agent" / "suites").mkdir(parents=True)
    (sut / ".qc-agent" / "suites" / "api-contract.yaml").write_text("suite: api-contract\n# của người\n", encoding="utf-8")
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e"))
    assert code == 0 and (sut / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8") == "suite: api-contract\n# của người\n"
    assert "api-contract.yaml" not in out          # render không đưa file đã có vào danh sách ghi


# ---------------- lỗi: exit 3 và không ghi dở ----------------

class DenyAll(egress.EgressPolicy):
    def decide(self, event):
        return egress.Decision("deny", "test")


def assert_nothing_written(sut: Path):
    assert not (sut / ".qc-agent").exists()


@pytest.mark.parametrize("status", [429, 529, 500, 400])
def test_llm_http_errors_are_exit_3_with_nothing_written(tmp_path, capsys, monkeypatch, status):
    sut = make_sut(tmp_path)
    with FakeAnthropic(status, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e"))
    assert code == 3 and "LỖI" in err and server.count == 1 and out == ""   # không retry lời gọi hạ tầng
    assert_nothing_written(sut)


def test_a_missing_key_is_exit_3_without_any_request(tmp_path, capsys, fake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    sut = make_sut(tmp_path)
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e"))
    assert code == 3 and "missing_key" in err and fake.count == 0
    assert_nothing_written(sut)


def test_denied_egress_is_exit_3_without_any_request(tmp_path, capsys, fake, monkeypatch):
    monkeypatch.setattr(egress, "LogOnlyPolicy", DenyAll)
    sut = make_sut(tmp_path)
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e"))
    assert code == 3 and "egress_denied" in err and fake.count == 0
    assert_nothing_written(sut)


def test_a_bad_answer_is_repaired_once_then_fails_as_exit_3(tmp_path, capsys, monkeypatch):
    sut = make_sut(tmp_path)
    bad = {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5", "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1},
           "content": [{"type": "tool_use", "id": "t", "name": "emit_test_cases", "input": {"test_cases": "không phải mảng", "uncovered_acs": []}}]}
    with FakeAnthropic(bad, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e"))
    assert code == 3 and "bad_output" in err and server.count == 2
    assert_nothing_written(sut)
    with FakeAnthropic(bad, RESPONSE, key=KEY) as server:   # lần sửa thành công
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        assert gt(capsys, *generate_args(sut, egress=tmp_path / "e"))[0] == 0 and server.count == 2


def test_the_fake_can_time_out_and_the_client_reports_it_without_a_body(monkeypatch):
    with FakeAnthropic("timeout", hang_s=2.0, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        with pytest.raises(LLMError) as caught:
            call_tool(purpose="t", model="m", system="", user="u", tool_name="x", tool_description="d", egress_dir=Path("."), data_categories=[],
                      input_schema={"type": "object", "properties": {}, "additionalProperties": False}, timeout_s=0.3)
    assert caught.value.kind == "timeout"


def test_the_fake_rejects_requests_without_the_documented_headers(monkeypatch):
    import httpx
    with FakeAnthropic(RESPONSE, key=KEY) as server:
        assert httpx.post(f"{server.url}/v1/messages", json={}).status_code == 401
        assert httpx.post(f"{server.url}/v1/messages", json={}, headers={"x-api-key": KEY}).status_code == 401
        assert httpx.post(f"{server.url}/v1/messages", json={}, headers={"x-api-key": "sai", "anthropic-version": "2023-06-01"}).status_code == 401
        assert httpx.post(f"{server.url}/v1/other", json={}, headers={"x-api-key": KEY, "anthropic-version": "2023-06-01"}).status_code == 404
        ok = httpx.post(f"{server.url}/v1/messages", json={"a": 1}, headers={"x-api-key": KEY, "anthropic-version": "2023-06-01"})
        assert ok.status_code == 200 and ok.json()["content"][0]["name"] == "emit_test_cases"
        assert server.count == 5 and [r["rejected"] for r in server.requests] == [True, True, True, False, False] and server.requests[-1]["body"] == {"a": 1}


@pytest.mark.parametrize("case", ["no_prd", "too_big", "not_utf8", "bad_sut_root", "bad_openapi", "no_acs", "unknown_flag"])
def test_input_errors_are_exit_3_with_no_request_and_no_files(tmp_path, capsys, fake, case):
    sut = make_sut(tmp_path)
    args = generate_args(sut, egress=tmp_path / "e")
    if case == "no_prd":
        args = generate_args(sut, prd="docs/prd/khong-co.md", egress=tmp_path / "e")
    elif case == "too_big":
        (sut / "docs" / "prd" / "noteboard-prd.md").write_text("x" * (300 * 1024), encoding="utf-8")
    elif case == "not_utf8":
        (sut / "docs" / "prd" / "noteboard-prd.md").write_bytes(b"\xff\xfe\x00bad")
    elif case == "bad_sut_root":
        args[args.index("--sut-root") + 1] = tmp_path / "khong-co"
    elif case == "bad_openapi":
        args[args.index("--openapi") + 1] = tmp_path / "khong-co.json"
    elif case == "no_acs":
        args = ["generate", "--prd", OPENAPI, "--sut-root", sut, "--egress-dir", tmp_path / "e"]   # OpenAPI không có story/AC nào
    elif case == "unknown_flag":
        args = [*args, "--bogus"]
    code, out, err = gt(capsys, *args)
    assert code == 3 and err.strip() and fake.count == 0
    assert_nothing_written(sut)


# ---------------- validate: cổng HITL ----------------

def test_validate_fails_while_drafts_remain_and_passes_when_everything_is_reviewed(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    code, out, err = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "33 test case còn draft" in out and "catalog còn status: draft" in out and "module-map còn status: draft" in out and "qc-agent:todo" in out
    approve_everything(sut)
    code, out, err = gt(capsys, "validate", "--sut-root", sut)
    assert code == 0, out
    assert out.strip().splitlines()[-1] == "OK: 0 lỗi, 1 cảnh báo" and "AC-3.5" in out     # orphan chỉ là cảnh báo


def test_a_catalog_marked_approved_while_drafts_remain_is_a_gate_failure_not_a_schema_error(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    data = load_catalog(sut)
    data["test_cases"][3]["status"] = "draft"
    save_catalog(sut, data)
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "1 test case còn draft" in out


def test_drift_a_hand_edited_generated_file_fails_validate(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    target = sut / GT / "tests_gt" / "test_us_1.py"
    target.write_text(target.read_text(encoding="utf-8").replace("gt_run(tc)", "gt_run(tc)  # sửa tay"), encoding="utf-8")
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "drift" in out and "test_us_1.py" in out
    shutil.copy(EXPECTED / GT / "tests_gt" / "test_us_1.py", target)
    assert gt(capsys, "validate", "--sut-root", sut)[0] == 0


@pytest.mark.parametrize("tamper", ["delete_conftest", "extra_file", "edit_ini", "crlf_is_not_drift", "new_story_without_regen"])
def test_drift_variants(tmp_path, capsys, fake, tamper):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    directory = sut / GT / "tests_gt"
    if tamper == "delete_conftest":
        (directory / "conftest.py").unlink()
    elif tamper == "extra_file":
        (directory / "helper.py").write_text("x = 1\n", encoding="utf-8")
    elif tamper == "edit_ini":
        (directory / "pytest.ini").write_text("[pytest]\naddopts = --deselect x\n", encoding="utf-8")
    elif tamper == "crlf_is_not_drift":
        for path in directory.glob("*.py"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    else:
        data = load_catalog(sut)
        data["stories"].append({"story_id": "US-9", "title": "Mới", "acs": [{"ac_id": "AC-9.1", "text": "mới"}]})
        save_catalog(sut, data)
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == (0 if tamper == "crlf_is_not_drift" else 1), out


def test_rejected_without_a_reason_fails_and_with_one_passes(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    data = load_catalog(sut)
    data["test_cases"][0].update(status="rejected")
    save_catalog(sut, data)
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "rejected_reason" in out and data["test_cases"][0]["tc_id"] in out
    data["test_cases"][0]["rejected_reason"] = "   "
    save_catalog(sut, data)
    assert gt(capsys, "validate", "--sut-root", sut)[0] == 1
    data["test_cases"][0]["rejected_reason"] = "ngoài phạm vi"
    save_catalog(sut, data)
    assert gt(capsys, "validate", "--sut-root", sut)[0] == 0


def test_duplicate_tc_id_and_dangling_draft_refs_fail(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    data = load_catalog(sut)
    data["test_cases"].append(copy.deepcopy(data["test_cases"][0]))
    save_catalog(sut, data)
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "tc_id bị trùng" in out
    data["test_cases"].pop()
    data["test_cases"][1].update(status="draft", ac_refs=["AC-9.9"])
    save_catalog(sut, data)
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "draft trỏ tới AC không có" in out


def test_an_approved_test_case_pointing_at_a_deleted_ac_is_a_warning_not_a_failure(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    data = load_catalog(sut)
    data["test_cases"][0]["ac_refs"] = ["AC-9.9"]
    save_catalog(sut, data)
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 0 and "trỏ tới AC không còn trong catalog" in out and data["test_cases"][0]["tc_id"] in out and "exit 4" in out


def test_module_map_gate(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    path = sut / GT / "module-map.yaml"
    good = path.read_text(encoding="utf-8")
    path.write_text(good.replace("status: approved", "status: draft"), encoding="utf-8")
    assert "module-map còn status: draft" in gt(capsys, "validate", "--sut-root", sut)[1]
    path.write_text(good.replace("version: 1", "# qc-agent:todo VERIFY: điền\nversion: 1"), encoding="utf-8")
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 1 and "dấu qc-agent:todo" in out
    path.unlink()
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 0 and "chưa có module-map.yaml" in out


def test_no_approved_test_case_is_only_a_warning(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    data = load_catalog(sut)
    for tc in data["test_cases"]:
        tc.update(status="rejected", rejected_reason="loại")
    data["status"] = "approved"
    save_catalog(sut, data)
    approve_everything_module_map = sut / GT / "module-map.yaml"
    approve_everything_module_map.write_text("version: 1\nstatus: approved\nmodules: []\n", encoding="utf-8")
    code, out, _ = gt(capsys, "validate", "--sut-root", sut)
    assert code == 0 and "chưa có test case approved nào" in out


@pytest.mark.parametrize("damage", ["broken_yaml", "not_object", "bad_kind", "missing_file", "not_utf8", "bad_module_map", "missing_stories"])
def test_unreadable_or_schema_invalid_files_are_exit_3_without_echoing_content(tmp_path, capsys, fake, damage):
    sut = generated(tmp_path, capsys)
    approve_everything(sut)
    catalog = sut / GT / "test-cases.yaml"
    if damage == "broken_yaml":
        catalog.write_text("test_cases: [\n  SECRETMARK", encoding="utf-8")
    elif damage == "not_object":
        catalog.write_text("- SECRETMARK\n", encoding="utf-8")
    elif damage == "bad_kind":
        data = load_catalog(sut)
        data["test_cases"][0]["kind"] = "SECRETMARK"
        save_catalog(sut, data)
    elif damage == "missing_file":
        catalog.unlink()
    elif damage == "not_utf8":
        catalog.write_bytes(b"\xff\xfe SECRETMARK")
    elif damage == "bad_module_map":
        (sut / GT / "module-map.yaml").write_text("modules: SECRETMARK\n", encoding="utf-8")
    else:
        data = load_catalog(sut)
        del data["stories"]
        save_catalog(sut, data)
    code, out, err = gt(capsys, "validate", "--sut-root", sut)
    assert code == 3 and "SECRETMARK" not in out + err and err.strip()


def test_validate_touches_nothing(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    before = tree(sut)
    gt(capsys, "validate", "--sut-root", sut)
    assert tree(sut) == before and fake.count == 1   # validate không gọi LLM


# ---------------- qc-agent validate chặn theo cùng bộ kiểm ----------------

def test_the_existing_qc_agent_validate_also_blocks_on_draft_ground_truth(tmp_path, capsys, fake):
    sut = tmp_path / "noteboard"
    shutil.copytree(FIXTURES / "sut" / "noteboard", sut, ignore=shutil.ignore_patterns("__pycache__"))
    (sut / "docs" / "prd").mkdir(parents=True)
    shutil.copy(PRD_FILE, sut / "docs" / "prd" / "noteboard-prd.md")
    assert gt(capsys, "generate", "--prd", sut / "docs/prd/noteboard-prd.md", "--sut-root", sut, "--openapi", OPENAPI, "--egress-dir", tmp_path / "e")[0] == 0
    kwargs = dict(projects_dir=ROOT / "configs" / "projects", workers_dirs=[ROOT / "workers"])
    report = v.validate("noteboard", sut, **kwargs)
    errors = [f.message for f in report.findings if f.level == v.ERROR and f.where.startswith(".qc-agent/ground-truth")]
    assert any("còn draft" in m for m in errors) and any("module-map còn status: draft" in m for m in errors)
    approve_everything(sut)
    report = v.validate("noteboard", sut, **kwargs)
    assert not [f for f in report.findings if f.level == v.ERROR and f.where.startswith(".qc-agent/ground-truth")]
    assert any(f.level == v.WARN and "AC-3.5" in f.message for f in report.findings)
    (sut / GT / "test-cases.yaml").write_text("[", encoding="utf-8")
    report = v.validate("noteboard", sut, **kwargs)
    assert any(f.level == v.ERROR and f.where == ".qc-agent/ground-truth" for f in report.findings)


def test_qc_agent_validate_ignores_repos_without_ground_truth():
    report = v.validate("noteboard", FIXTURES / "sut" / "noteboard", projects_dir=ROOT / "configs" / "projects", workers_dirs=[ROOT / "workers"])
    assert not [f for f in report.findings if "ground-truth" in f.where]


# ---------------- regen ----------------

def approved_qa_and_rejected(sut: Path) -> dict[str, dict]:
    """Trộn đủ bốn loại: approved, rejected, origin qa, draft. Trả {tc_id: entry} của những TC PHẢI được giữ nguyên."""
    data = load_catalog(sut)
    tcs = data["test_cases"]
    tcs[0]["status"] = "approved"
    tcs[1]["status"] = "approved"
    tcs[1]["notes"] = "QA đã xem: giữ"
    tcs[2].update(status="rejected", rejected_reason="ngoài phạm vi")
    tcs.append({"tc_id": "TC-AC-1.1-qa-emoji", "title": "QA: tiêu đề có emoji 🚀", "ac_refs": ["AC-1.1"], "kind": "api_functional", "status": "approved",
                "origin": "qa", "notes": "do QA viết", "steps": [{"request": {"method": "POST", "path": "/notes", "json": {"title": "🚀", "body": "x"}},
                                                                   "expect": {"status": [201], "json": [{"path": "$.title", "op": "eq", "value": "🚀"}]}}]})
    tcs.append({"tc_id": "TC-AC-2.1-qa-draft", "title": "QA: nháp chưa duyệt", "ac_refs": ["AC-2.1"], "kind": "api_functional", "status": "draft", "origin": "qa",
                "steps": [{"request": {"method": "GET", "path": "/notes"}, "expect": {"status": [200]}}]})
    data["status"] = "draft"
    save_catalog(sut, data)
    return {tc["tc_id"]: copy.deepcopy(tc) for tc in (tcs[0], tcs[1], tcs[2], tcs[-2], tcs[-1])}


def regen(capsys, sut, tmp_path, *extra, prd=None):
    return gt(capsys, "regen", "--prd", prd or sut / "docs/prd/noteboard-prd.md", "--sut-root", sut, "--openapi", OPENAPI, "--egress-dir", tmp_path / "eg", *extra)


def test_regen_keeps_every_reviewed_case_byte_for_byte_and_replaces_only_llm_drafts(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    protected = approved_qa_and_rejected(sut)
    draft_ids = {tc["tc_id"] for tc in load_catalog(sut)["test_cases"] if tc["status"] == "draft" and tc["origin"] == "llm"}
    code, out, err = regen(capsys, sut, tmp_path, "--summary-json", tmp_path / "s.json")
    assert code == 0, out + err
    after = {tc["tc_id"]: tc for tc in load_catalog(sut)["test_cases"]}
    for tc_id, entry in protected.items():
        assert dump(after[tc_id]) == dump(entry), tc_id
    assert after["TC-AC-2.1-qa-draft"]["status"] == "draft" and after["TC-AC-2.1-qa-draft"]["origin"] == "qa"
    summary = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert summary["command"] == "regen" and summary["merge"]["kept"] == 5 and len(summary["merge"]["added"]) == len(draft_ids)   # 3 TC llm đầu đã duyệt/loại nên ứng viên trùng của chúng bị bỏ
    assert set(after) == set(protected) | draft_ids                                # mọi ứng viên trùng được giữ đúng một bản
    assert len(after) == len(load_catalog(sut)["test_cases"]) and load_catalog(sut)["status"] == "draft"
    assert fake.count == 2


def test_regen_never_re_proposes_a_rejected_case_and_is_idempotent(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    protected = approved_qa_and_rejected(sut)
    rejected_id = next(i for i, tc in protected.items() if tc["status"] == "rejected")
    assert regen(capsys, sut, tmp_path)[0] == 0
    once = tree(sut / ".qc-agent")
    ids = [tc["tc_id"] for tc in load_catalog(sut)["test_cases"]]
    assert ids.count(rejected_id) == 1 and next(tc for tc in load_catalog(sut)["test_cases"] if tc["tc_id"] == rejected_id)["status"] == "rejected"
    assert regen(capsys, sut, tmp_path)[0] == 0 and tree(sut / ".qc-agent") == once


def test_regen_after_the_prd_changes_updates_stories_and_reports_lost_acs_without_touching_cases(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    data = load_catalog(sut)
    lost = next(tc for tc in data["test_cases"] if tc["ac_refs"][0] == "AC-2.6")
    lost["status"] = "approved"
    tc_ok = next(tc for tc in data["test_cases"] if tc["ac_refs"][0] == "AC-1.1")
    tc_ok["status"] = "approved"
    save_catalog(sut, data)
    approved = {tc["tc_id"]: copy.deepcopy(tc) for tc in (lost, tc_ok)}
    prd_path = sut / "docs" / "prd" / "noteboard-prd.md"
    text = prd_path.read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.startswith("- AC-2.6:"))                      # xoá AC
    text = text.replace("- AC-1.3: `title` là chuỗi rỗng", "- AC-1.3: `title` là chuỗi rỗng hoặc chỉ toàn dấu cách")   # sửa AC
    text = text.replace("- AC-1.8:", "- AC-1.9: `title` chứa ký tự xuống dòng thì trả 422.\n- AC-1.8:")                # thêm AC
    prd_path.write_text(text + "\n", encoding="utf-8", newline="\n")
    summary_path = tmp_path / "s.json"
    code, out, err = regen(capsys, sut, tmp_path, "--summary-json", summary_path)
    assert code == 0, err
    catalog = load_catalog(sut)
    ac_ids = [ac["ac_id"] for s in catalog["stories"] for ac in s["acs"]]
    assert "AC-2.6" not in ac_ids and "AC-1.9" in ac_ids and "chỉ toàn dấu cách" in json.dumps(catalog, ensure_ascii=False)
    after = {tc["tc_id"]: tc for tc in catalog["test_cases"]}
    assert all(dump(after[i]) == dump(entry) for i, entry in approved.items())                                 # TC approved trỏ AC đã mất: không sửa
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["merge"]["lost_acs"] == {lost["tc_id"]: ["AC-2.6"]} and "AC-1.9" in summary["orphans"]
    assert f"{lost['tc_id']} trỏ tới AC không còn trong PRD" in out
    assert catalog["prd"]["sha256"] != EXPECTED_SHA and catalog["status"] == "draft"
    checked = gt_check.check(sut)
    assert any("không còn trong catalog" in message and lost["tc_id"] in message for _, message in checked.warnings)


EXPECTED_SHA = yaml.safe_load((EXPECTED / GT / "test-cases.yaml").read_text(encoding="utf-8"))["prd"]["sha256"]


def test_regen_regenerates_the_machine_files_but_leaves_human_owned_ones_alone(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    approve_everything(sut, keep_draft=())
    (sut / GT / "tests_gt" / "test_us_1.py").write_text("# sửa tay\n", encoding="utf-8")
    module_map = (sut / GT / "module-map.yaml").read_text(encoding="utf-8")
    suite = sut / ".qc-agent" / "suites" / "gt-functional.yaml"
    suite.write_text(suite.read_text(encoding="utf-8") + "# QA thêm\n", encoding="utf-8")
    contract = sut / ".qc-agent" / "suites" / "api-contract.yaml"
    contract.write_text("suite: api-contract\n# của người\n", encoding="utf-8")
    assert regen(capsys, sut, tmp_path)[0] == 0
    assert (sut / GT / "tests_gt" / "test_us_1.py").read_bytes() == (EXPECTED / GT / "tests_gt" / "test_us_1.py").read_bytes()    # drift được sửa
    assert (sut / GT / "module-map.yaml").read_text(encoding="utf-8") == module_map and suite.read_text(encoding="utf-8").endswith("# QA thêm\n")
    assert contract.read_text(encoding="utf-8") == "suite: api-contract\n# của người\n"


def test_regen_removes_only_stale_generated_story_files(tmp_path, capsys, fake):
    sut = generated(tmp_path, capsys)
    (sut / GT / "tests_gt" / "test_custom.py").write_text("def test_x():\n    pass\n", encoding="utf-8")    # không có dấu generated: không được xoá
    prd_path = sut / "docs" / "prd" / "noteboard-prd.md"
    text = prd_path.read_text(encoding="utf-8")
    prd_path.write_text(text[: text.index("## US-4")], encoding="utf-8", newline="\n")
    code, out, err = regen(capsys, sut, tmp_path)
    assert code == 0, err
    assert not (sut / GT / "tests_gt" / "test_us_4.py").exists() and (sut / GT / "tests_gt" / "test_custom.py").exists()
    assert "removed" in out and "test_us_4.py" in out
    assert gt(capsys, "validate", "--sut-root", sut)[0] == 1   # test_custom.py là file thừa: drift


@pytest.mark.parametrize("case", ["no_catalog", "broken_yaml", "schema_invalid", "llm_error", "missing_key"])
def test_regen_failures_are_exit_3_and_leave_every_file_untouched(tmp_path, capsys, monkeypatch, case):
    sut = make_sut(tmp_path)
    with FakeAnthropic(RESPONSE, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        if case != "no_catalog":
            assert gt(capsys, *generate_args(sut, egress=tmp_path / "e"))[0] == 0
    catalog = sut / GT / "test-cases.yaml"
    if case == "broken_yaml":
        catalog.write_text("a: [\n  SECRETMARK", encoding="utf-8")
    elif case == "schema_invalid":
        data = load_catalog(sut)
        data["test_cases"][0]["kind"] = "SECRETMARK"
        save_catalog(sut, data)
    if case == "missing_key":
        monkeypatch.delenv("ANTHROPIC_API_KEY")
    before = tree(sut)
    with FakeAnthropic(429 if case == "llm_error" else RESPONSE, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        code, out, err = regen(capsys, sut, tmp_path)
    assert code == 3 and "SECRETMARK" not in out + err and tree(sut) == before


# ---------------- merge: thuộc tính bất biến trên 50 catalog ngẫu nhiên (seed cố định) ----------------

def random_tc(rng, tc_id, ac_pool, status, origin):
    refs = rng.sample(ac_pool, rng.randint(1, min(2, len(ac_pool))))
    tc = {"tc_id": tc_id, "title": f"tc {rng.random()}", "ac_refs": refs, "kind": "api_functional", "status": status, "origin": origin,
          "steps": [{"request": {"method": "GET", "path": "/notes", "query": {"q": rng.choice(["a", "b", "c"])}}, "expect": {"status": [200]}}]}
    if status == "rejected":
        tc["rejected_reason"] = "loại"
    if rng.random() < 0.3:
        tc["notes"] = "ghi chú của QA"
    return tc


def random_case(seed):
    rng = random.Random(seed)
    old_acs = [f"AC-{s}.{a}" for s in range(1, rng.randint(2, 4) + 1) for a in range(1, rng.randint(2, 4) + 1)]
    new_acs = [ac for ac in old_acs if rng.random() > 0.25] or old_acs[:1]
    new_acs += [f"AC-9.{n}" for n in range(rng.randint(0, 2))]

    def stories(acs):
        return [{"story_id": "US-1", "title": "s", "acs": [{"ac_id": ac, "text": f"t {ac}"} for ac in acs]}]
    base = {"version": 1, "prd": {"id": "p", "sha256": "0" * 64, "source": "p.md"}, "generated_by": {"model": "m", "prompt_version": "gt-generate/1"}}
    old_tcs = []
    for n in range(rng.randint(0, 14)):
        status = rng.choice(["draft", "draft", "approved", "approved", "rejected"])
        origin = rng.choice(["llm", "llm", "llm", "qa"])
        old_tcs.append(random_tc(rng, f"TC-old-{n}", old_acs, status, origin))
    old = {**base, "status": rng.choice(["draft", "approved"]) if not any(t["status"] == "draft" for t in old_tcs) else "draft",
           "stories": stories(old_acs), "test_cases": old_tcs, "uncovered_acs": [{"ac_id": old_acs[0], "reason": "QA: ui"}]}
    candidates = [copy.deepcopy(t) | {"status": "draft", "origin": "llm"} for t in rng.sample(old_tcs, rng.randint(0, len(old_tcs)))]
    for t in candidates:
        t.pop("rejected_reason", None)
        t.pop("notes", None)
    candidates += [random_tc(rng, f"TC-new-{n}", new_acs, "draft", "llm") for n in range(rng.randint(0, 6))]
    new = {**base, "status": "draft", "stories": stories(new_acs), "test_cases": candidates,
           "uncovered_acs": [{"ac_id": new_acs[-1], "reason": "LLM: ui"}]}
    return old, new


@pytest.mark.parametrize("seed", range(50))
def test_merge_invariants_hold_for_random_catalogs(seed):
    old, new = random_case(seed)
    result = merge(old, new)
    merged = {tc["tc_id"]: tc for tc in result.catalog["test_cases"]}
    protected = [tc for tc in old["test_cases"] if tc["status"] in ("approved", "rejected") or tc["origin"] == "qa"]
    for tc in protected:                                                                          # 1. giữ nguyên văn
        assert dump(merged[tc["tc_id"]]) == dump(tc)
    stale = {tc["tc_id"] for tc in old["test_cases"] if tc not in protected}
    fresh = {tc["tc_id"] for tc in new["test_cases"]} - {tc["tc_id"] for tc in protected}
    assert set(merged) == {tc["tc_id"] for tc in protected} | fresh                               # 2-3. draft cũ bị thay, ứng viên trùng bị bỏ
    assert len(merged) == len(result.catalog["test_cases"])                                       # không nhân đôi
    for tc_id in fresh:
        assert merged[tc_id]["status"] == "draft" and merged[tc_id]["origin"] == "llm"
    assert set(result.removed_drafts) == stale and set(result.added) == fresh and result.kept == len(protected)
    assert [ac["ac_id"] for s in result.catalog["stories"] for ac in s["acs"]] == [ac["ac_id"] for s in new["stories"] for ac in s["acs"]]   # 4
    new_acs = {ac["ac_id"] for s in new["stories"] for ac in s["acs"]}
    assert result.lost_acs == {tc["tc_id"]: tuple(r for r in tc["ac_refs"] if r not in new_acs) for tc in protected if set(tc["ac_refs"]) - new_acs}
    assert result.catalog["status"] == ("draft" if any(tc["status"] == "draft" for tc in merged.values()) else old["status"])            # 5
    assert result.catalog["prd"] == new["prd"] and result.catalog["generated_by"] == new["generated_by"]
    covered = {r for tc in merged.values() if tc["status"] != "rejected" for r in tc["ac_refs"]}
    assert not {u["ac_id"] for u in result.catalog["uncovered_acs"]} & covered
    assert set(result.orphans) == new_acs - covered - {u["ac_id"] for u in result.catalog["uncovered_acs"]}
    assert gt_schema.validate_catalog(result.catalog) == [] or all("ac_refs" not in e for e in gt_schema.validate_catalog(result.catalog))
    again = merge(result.catalog, new)                                                            # idempotent
    assert json.dumps(again.catalog, sort_keys=True) == json.dumps(result.catalog, sort_keys=True)
    shuffled = copy.deepcopy(new)
    random.Random(seed).shuffle(shuffled["test_cases"])
    assert json.dumps(merge(old, shuffled).catalog, sort_keys=True) == json.dumps(result.catalog, sort_keys=True)   # không phụ thuộc thứ tự ứng viên


def test_merge_does_not_mutate_its_inputs():
    old, new = random_case(7)
    before = (copy.deepcopy(old), copy.deepcopy(new))
    merge(old, new)
    assert (old, new) == before


def test_an_old_uncovered_reason_edited_by_qa_beats_the_new_one():
    old, new = random_case(3)
    ac = old["uncovered_acs"][0]["ac_id"]
    new["stories"][0]["acs"] = [{"ac_id": ac, "text": "t"}]
    new["test_cases"] = []
    old["test_cases"] = []
    new["uncovered_acs"] = [{"ac_id": ac, "reason": "LLM"}]
    assert merge(old, new).catalog["uncovered_acs"] == [{"ac_id": ac, "reason": "QA: ui"}]


# ---------------- ranh giới ----------------

def test_importing_the_core_cli_never_pulls_the_llm_or_groundtruth_packages_in():
    code = ("import sys; import qc_agent.core.cli, qc_agent.core.engine; "
            "bad = sorted(m for m in sys.modules if m.split('.')[:2] in (['qc_agent', 'llm'], ['qc_agent', 'groundtruth'], ['qc_agent', 'selector'])); "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_running_a_gate_plan_never_imports_the_gt_or_llm_packages(tmp_path):
    code = ("import sys; from qc_agent.core.cli import main; rc = main(['--plan', 'tests/fixtures/plans/demo.yaml']); "
            "bad = sorted(m for m in sys.modules if m.split('.')[:2] in (['qc_agent', 'llm'], ['qc_agent', 'groundtruth'])); print(rc, bad); sys.exit(0 if rc == 0 and not bad else 1)")
    env = {**os.environ, "QC_WORKERS_PATH": os.pathsep.join(["workers", "tests/fixtures/workers"]), "QC_RUNS_DIR": str(tmp_path / "runs")}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout[-500:] + done.stderr[-500:]


def test_help_works_and_bad_subcommands_are_exit_3(capsys):
    assert gt(capsys, "--help")[0] == 0
    code, out, err = gt(capsys)
    assert code == 3 and err.strip()
    assert gt(capsys, "nope")[0] == 3


# ---------------- log/stderr không lộ nội dung ----------------

MARK = "PRDMARK-4c9e"


def test_stderr_and_stdout_never_carry_prd_content_or_the_key(tmp_path, capsys, fake, monkeypatch):
    text = PRD_FILE.read_text(encoding="utf-8").replace("- AC-1.3: `title` là chuỗi rỗng", f"- AC-1.3: {MARK} `title` là chuỗi rỗng")
    sut = make_sut(tmp_path, prd_text=text)
    code, out, err = gt(capsys, *generate_args(sut, egress=tmp_path / "e"))
    assert code == 0 and MARK not in out + err and KEY not in out + err
    assert MARK in (sut / GT / "test-cases.yaml").read_text(encoding="utf-8")            # nội dung có ở catalog (đúng chỗ), nhưng không ở log/stdout
    code, out, err = gt(capsys, "validate", "--sut-root", sut)
    assert MARK not in out + err
    code, out, err = regen(capsys, sut, tmp_path)
    assert code == 0 and MARK not in out + err
    with FakeAnthropic(429, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        code, out, err = regen(capsys, sut, tmp_path)
    assert code == 3 and MARK not in out + err and KEY not in out + err
    assert all(json.loads(line) for line in err.splitlines() if line.startswith("{"))   # log là JSON một dòng, không hỏng khi có tiếng Việt
