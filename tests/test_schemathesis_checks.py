"""Bước 21: auth header từ secret, exclude_path tuỳ chọn/danh sách, KNOWN_CHECKS mở rộng (ánh xạ tiêu đề lỗi -> check)."""
import copy
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.schemathesis_adapter import (
    CONFIG_NAME, KNOWN_CHECKS, REPORT_NAME, TITLE_TO_CHECK, SchemathesisAdapter,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tests" / "samples"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "task_st.json").read_text(encoding="utf-8"))
STDOUT_5_OPS = "Operations:       5 selected / 5 total\n"
SECRET = "s3cr3t-token-value"


def spec_with(**inputs):
    spec = copy.deepcopy(SPEC)
    spec["inputs"].update(inputs)
    return spec


def completed(returncode, stdout=STDOUT_5_OPS):
    return subprocess.CompletedProcess(args=["st"], returncode=returncode, stdout=stdout, stderr="")


def with_checks(*names):
    spec = copy.deepcopy(SPEC)
    spec["inputs"]["checks"] = list(names)
    spec["oracle"] = {"kind": "checks", "required": list(names)}
    return spec


# ---------- exclude_path ----------

def cmd(spec, tmp_path):
    return SchemathesisAdapter().build_cmd(spec, tmp_path)


def test_exclude_path_is_optional_a_string_or_a_list(tmp_path):
    spec = copy.deepcopy(SPEC)
    del spec["inputs"]["exclude_path"]
    assert "--exclude-path" not in cmd(spec, tmp_path)

    assert cmd(spec_with(exclude_path="/a"), tmp_path).count("--exclude-path") == 1

    command = cmd(spec_with(exclude_path=["/a", "/b/{id}"]), tmp_path)
    pairs = [command[i + 1] for i, arg in enumerate(command) if arg == "--exclude-path"]
    assert pairs == ["/a", "/b/{id}"]
    assert cmd(spec_with(exclude_path=[]), tmp_path).count("--exclude-path") == 0


@pytest.mark.parametrize("bad", ["notes", ["/ok", "bad"], [1], 5, {"a": "/x"}])
def test_bad_exclude_path_is_rejected(tmp_path, bad):
    with pytest.raises(AdapterParseError, match="exclude_path"):
        cmd(spec_with(exclude_path=bad), tmp_path)


# ---------- auth ----------

def test_auth_writes_only_an_env_placeholder_never_the_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("SUT_API_TOKEN", SECRET)
    spec = spec_with(auth={"header": "Authorization", "secret_env": "SUT_API_TOKEN", "prefix": "Bearer "})
    command = cmd(spec, tmp_path)

    config = (tmp_path / CONFIG_NAME).read_text(encoding="utf-8")
    assert 'Authorization = "Bearer ${SUT_API_TOKEN}"' in config and "[cache]" in config
    assert SECRET not in config and SECRET not in " ".join(command) and SECRET not in json.dumps(spec)


def test_no_auth_leaves_config_unchanged(tmp_path):
    cmd(copy.deepcopy(SPEC), tmp_path)
    assert (tmp_path / CONFIG_NAME).read_text(encoding="utf-8") == "[cache]\nenabled = false\n"


@pytest.mark.parametrize("auth, message", [
    ({"header": "Authorization"}, "secret_env"),
    ({"header": "Bad Header", "secret_env": "X"}, "header"),
    ({"header": "Authorization", "secret_env": "1BAD"}, "TÊN biến"),
    ({"header": "Authorization", "secret_env": "SUT_API_TOKEN", "prefix": "Bearer ${OTHER_SECRET}"}, "prefix"),
    ({"header": "Authorization", "secret_env": "SUT_API_TOKEN", "prefix": "B\nx"}, "prefix"),
    ({"header": "Authorization", "secret_env": "SUT_API_TOKEN", "extra": 1}, "inputs.auth"),
    ("Bearer x", "inputs.auth"),
])
def test_invalid_auth_is_rejected(tmp_path, monkeypatch, auth, message):
    monkeypatch.setenv("SUT_API_TOKEN", SECRET)
    with pytest.raises(AdapterParseError, match=message):
        cmd(spec_with(auth=auth), tmp_path)


def test_missing_secret_is_an_error_not_an_unauthenticated_run(tmp_path, monkeypatch):
    monkeypatch.delenv("SUT_API_TOKEN", raising=False)
    with pytest.raises(AdapterParseError, match="SUT_API_TOKEN chưa được cấp"):
        cmd(spec_with(auth={"header": "Authorization", "secret_env": "SUT_API_TOKEN"}), tmp_path)
    monkeypatch.setenv("SUT_API_TOKEN", "")
    with pytest.raises(AdapterParseError, match="chưa được cấp"):
        cmd(spec_with(auth={"header": "Authorization", "secret_env": "SUT_API_TOKEN"}), tmp_path)


def test_secret_leaking_into_output_is_redacted_before_becoming_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("SUT_API_TOKEN", SECRET)
    xml = (SAMPLES / "st_bug_on.junit.xml").read_text(encoding="utf-8").replace(
        "curl -X GET http://127.0.0.1:54807/notes", f"curl -X GET -H 'Authorization: Bearer {SECRET}' http://127.0.0.1:54807/notes", 1)
    (tmp_path / REPORT_NAME).write_text(xml, encoding="utf-8")
    stdout = (SAMPLES / "st_bug_on.txt").read_text(encoding="utf-8") + f"\nAuthorization: Bearer {SECRET}\n"
    spec = spec_with(auth={"header": "Authorization", "secret_env": "SUT_API_TOKEN", "prefix": "Bearer "})

    parsed = SchemathesisAdapter().parse_output(completed(1, stdout), tmp_path, spec)

    for name in (REPORT_NAME, "stdout.log"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert SECRET not in text
    assert "[REDACTED]" in (tmp_path / REPORT_NAME).read_text(encoding="utf-8")
    assert SECRET not in json.dumps(parsed.findings) and SECRET not in parsed.replay_cmd


# ---------- KNOWN_CHECKS mở rộng ----------

def test_known_checks_are_exactly_what_the_title_table_can_attribute():
    assert KNOWN_CHECKS == set(TITLE_TO_CHECK.values()) and {"not_a_server_error", "response_schema_conformance"} <= KNOWN_CHECKS
    assert not {"all", "max_response_time"} & KNOWN_CHECKS  # mơ hồ / cần tham số chưa có trong contract


def test_undocumented_status_code_maps_to_status_code_conformance(tmp_path):
    shutil.copyfile(SAMPLES / "st_status_code.junit.xml", tmp_path / REPORT_NAME)  # đầu ra THẬT của st 4.27.5
    spec = with_checks("status_code_conformance", "not_a_server_error")

    parsed = SchemathesisAdapter().parse_output(completed(1), tmp_path, spec)

    assert parsed.signals["checks"] == {"status_code_conformance": False, "not_a_server_error": True}
    assert [f["detected_by"] for f in parsed.findings] == ["schemathesis:status_code_conformance"]
    assert "POST /notes" in parsed.findings[0]["title"]


def test_invalid_allow_header_maps_to_allow_header_conformance_per_operation(tmp_path):
    shutil.copyfile(SAMPLES / "st_allow_header.junit.xml", tmp_path / REPORT_NAME)
    parsed = SchemathesisAdapter().parse_output(completed(1), tmp_path, with_checks("allow_header_conformance"))

    assert parsed.signals["checks"] == {"allow_header_conformance": False}
    assert len(parsed.findings) == 2 and {"POST /notes", "GET /notes/{note_id}"} == {f["title"].split(":")[0] for f in parsed.findings}


def test_failure_of_a_check_that_was_not_requested_is_an_error(tmp_path):
    shutil.copyfile(SAMPLES / "st_status_code.junit.xml", tmp_path / REPORT_NAME)
    with pytest.raises(AdapterParseError, match="không được yêu cầu: status_code_conformance"):
        SchemathesisAdapter().parse_output(completed(1), tmp_path, with_checks("not_a_server_error"))


def test_every_title_in_the_table_is_attributed_to_its_check(tmp_path):
    for index, (title, check) in enumerate(TITLE_TO_CHECK.items()):
        (tmp_path / REPORT_NAME).write_text(
            "<?xml version='1.0'?><testsuites tests='1' failures='1' errors='0'><testsuite tests='1' failures='1' errors='0'>"
            f"<testcase name='GET /x'><failure type='failure'>1. Test Case ID: a\n\n- {title}\n\ndetail</failure></testcase>"
            "</testsuite></testsuites>", encoding="utf-8")
        spec = with_checks(check)
        parsed = SchemathesisAdapter().parse_output(completed(1, "Operations: 1 selected / 1 total\n"), tmp_path, spec)
        assert parsed.signals["checks"] == {check: False}, title


def test_a_failure_with_two_titles_fails_both_checks(tmp_path):
    (tmp_path / REPORT_NAME).write_text(
        "<?xml version='1.0'?><testsuites tests='1' failures='1' errors='0'><testsuite tests='1' failures='1' errors='0'>"
        "<testcase name='GET /x'><failure type='failure'>1. Test Case ID: a\n\n- Server error\n\n2. Test Case ID: b\n\n- Response violates schema\n</failure></testcase>"
        "</testsuite></testsuites>", encoding="utf-8")
    parsed = SchemathesisAdapter().parse_output(completed(1, "Operations: 1 selected / 1 total\n"), tmp_path, copy.deepcopy(SPEC))
    assert parsed.signals["checks"] == {"not_a_server_error": False, "response_schema_conformance": False}


def test_unsupported_check_names_are_still_rejected(tmp_path):
    for name in ("all", "max_response_time", "made_up"):
        with pytest.raises(AdapterParseError, match="sin parser"):
            cmd(with_checks(name), tmp_path)


# ---------- chạy st THẬT với API cần xác thực ----------

def _app():
    app = FastAPI()

    class Item(BaseModel):
        id: int
        name: str

    @app.get("/items", response_model=Item)
    def items(authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {SECRET}":
            raise HTTPException(status_code=401, detail="unauthorized")  # KHÔNG được ghi trong OpenAPI => status_code_conformance bắt được
        return {"id": 1, "name": "x"}

    return app


@pytest.fixture
def secured_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(15)


@pytest.mark.skipif(shutil.which("st") is None, reason="needs schemathesis (st)")
def test_real_st_sends_the_secret_header_and_it_never_reaches_the_evidence(tmp_path, monkeypatch, secured_server):
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("PYTHONUTF8", "1")

    def run(token, run_id):
        monkeypatch.setenv("SUT_API_TOKEN", token)
        spec = with_checks("status_code_conformance", "not_a_server_error")
        spec["run_id"] = run_id
        spec["inputs"].update(schema_url=f"{secured_server}/openapi.json", max_examples=10, auth={
            "header": "Authorization", "secret_env": "SUT_API_TOKEN", "prefix": "Bearer "})
        del spec["inputs"]["exclude_path"]
        return SchemathesisAdapter().run(spec)

    good = run(SECRET, "r-good")
    assert good["status"] == "pass", good["verdict"]  # header đã đến SUT: không còn 401
    bad = run("wrong-token", "r-bad")
    assert bad["status"] == "fail" and any(f["detected_by"] == "schemathesis:status_code_conformance" for f in bad["findings"])

    for run_dir in (tmp_path / "runs").rglob("*"):  # bí mật không nằm ở bất kỳ file nào của run
        if run_dir.is_file():
            assert SECRET.encode() not in run_dir.read_bytes(), run_dir
    assert "SUT_API_TOKEN" in (tmp_path / "runs" / "r-good" / "t-001" / CONFIG_NAME).read_text(encoding="utf-8")
