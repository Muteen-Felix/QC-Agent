import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from adapters._base import AdapterParseError
from adapters.schemathesis_adapter import REPORT_NAME, SchemathesisAdapter


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tests" / "samples"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "task_st.json").read_text(encoding="utf-8"))


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["st", "run", SPEC["inputs"]["schema_url"]],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def test_bug_on_maps_only_the_known_server_error_check(tmp_path):
    spec = copy.deepcopy(SPEC)
    adapter = SchemathesisAdapter()
    command = adapter.build_cmd(spec, tmp_path)
    config_path = Path(command[command.index("--config-file") + 1])
    assert config_path.read_text(encoding="utf-8") == "[cache]\nenabled = false\n"
    assert command[command.index("run") + 1] == spec["inputs"]["schema_url"]
    assert command[command.index("--checks") + 1] == "not_a_server_error,response_schema_conformance"
    assert command[command.index("--exclude-path") + 1] == "/notes/{note_id}/summarize"
    assert command[command.index("--report-junit-path") + 1] == str((tmp_path / REPORT_NAME).resolve())
    shutil.copyfile(SAMPLES / "st_bug_on.junit.xml", tmp_path / REPORT_NAME)
    sample_stdout = (SAMPLES / "st_bug_on.txt").read_text(encoding="utf-8")

    parsed = adapter.parse_output(_completed(1, sample_stdout), tmp_path, spec)

    assert parsed.signals == {"checks": {"not_a_server_error": False, "response_schema_conformance": True}}
    assert parsed.adapter_notes == ["PARSER_VERSION=1"]
    assert [kind for kind, _ in parsed.evidence_paths] == ["raw_output", "stdout"]
    assert "st run" in parsed.replay_cmd


def test_bug_off_marks_each_required_check_passed(tmp_path):
    shutil.copyfile(SAMPLES / "st_bug_off.junit.xml", tmp_path / REPORT_NAME)
    stdout = (SAMPLES / "st_bug_off.txt").read_text(encoding="utf-8")

    parsed = SchemathesisAdapter().parse_output(_completed(0, stdout), tmp_path, copy.deepcopy(SPEC))

    assert parsed.signals == {"checks": {"not_a_server_error": True, "response_schema_conformance": True}}


def test_missing_report_is_an_adapter_error(tmp_path):
    server_down = (SAMPLES / "st_server_down.txt").read_text(encoding="utf-8")
    with pytest.raises(AdapterParseError, match="không có JUnit report"):
        SchemathesisAdapter().parse_output(_completed(1, server_down), tmp_path, copy.deepcopy(SPEC))
    assert (tmp_path / "stdout.log").read_text(encoding="utf-8") == server_down


def test_nonzero_exit_with_clean_report_is_a_contradiction(tmp_path):
    shutil.copyfile(SAMPLES / "st_bug_off.junit.xml", tmp_path / REPORT_NAME)
    stdout = (SAMPLES / "st_bug_off.txt").read_text(encoding="utf-8")

    with pytest.raises(AdapterParseError, match="mâu thuẫn exit code/báo cáo"):
        SchemathesisAdapter().parse_output(_completed(1, stdout), tmp_path, copy.deepcopy(SPEC))

    (tmp_path / REPORT_NAME).write_text(
        "<?xml version='1.0'?>\n"
        "<testsuites tests='1' failures='0' errors='0'><testsuite tests='1' failures='0' errors='0'>"
        "<testcase name='GET /notes' /></testsuite></testsuites>",
        encoding="utf-8",
    )
    with pytest.raises(AdapterParseError, match="chưa đủ operation testcase"):
        SchemathesisAdapter().parse_output(_completed(0, stdout), tmp_path, copy.deepcopy(SPEC))


def test_unknown_failure_is_not_guessed_as_a_known_check(tmp_path):
    stdout = (SAMPLES / "st_bug_off.txt").read_text(encoding="utf-8")
    (tmp_path / REPORT_NAME).write_text(
        "<?xml version='1.0'?>\n"
        "<testsuites tests='4' failures='1' errors='0'><testsuite tests='4' failures='1' errors='0'>"
        "<testcase name='GET /notes'><failure type='failure'>Unrecognized worker failure</failure></testcase>"
        "<testcase name='POST /notes' /><testcase name='GET /notes/{note_id}' />"
        "<testcase name='DELETE /notes/{note_id}' />"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    with pytest.raises(AdapterParseError, match="không quy được về check đã biết"):
        SchemathesisAdapter().parse_output(_completed(1, stdout), tmp_path, copy.deepcopy(SPEC))
