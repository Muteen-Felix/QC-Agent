import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import schema
from fake_adapter import FakeAdapter

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).resolve().parent / "fake_adapter.py"

_K6 = json.loads((ROOT / "examples" / "task.k6.json").read_text(encoding="utf-8-sig"))


def make_spec(mode="ok", **over):
    s = copy.deepcopy(_K6)
    s.update(capability="demo.echo", oracle={"kind": "trivial"}, evidence_required=["raw_output"])
    s["inputs"] = {"mode": mode}
    s["budget"] = {"wallclock_s": 20, "tokens": 0, "usd": 0}
    s.update(over)
    assert schema.validate_task(s) == []
    return s


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def assert_valid(spec, res):
    assert schema.validate_result(res) == []
    assert schema.check_result_against_spec(spec, res) == []


def test_01_happy_path_gate_pass():
    spec = make_spec()
    res = FakeAdapter().run(spec)
    assert_valid(spec, res)
    assert res["status"] == "pass"
    assert res["verdict"] == {"value": "pass", "verdict_source": "deterministic_assert",
                              "gating": True, "confidence": None, "rationale": None}
    assert res["worker"] == {"name": "fake", "version": None, "adapter_version": "0.0.1"}
    assert res["evidence"][0]["uri"] == "runs/r-0001/t-002/raw.json"
    assert res["cost"]["wallclock_s"] > 0 and res["cost"]["tokens"] is None and res["cost"]["usd"] is None


def test_02_timeout_is_error_and_kills_whole_process_tree(tmp_path):
    beat = tmp_path / "beat.txt"
    spec = make_spec("spawn_child", budget={"wallclock_s": 3, "tokens": 0, "usd": 0})
    spec["inputs"]["beat_file"] = str(beat)
    t0 = time.monotonic()
    res = FakeAdapter().run(spec)
    assert time.monotonic() - t0 < 15  # đúng giờ, không chờ cha ngủ hết 60s
    assert res["status"] == "error" and "timeout" in res["verdict"]["rationale"]
    assert res["verdict"]["value"] != "pass" and res["verdict"]["gating"] is False
    assert schema.validate_result(res) == []
    size = beat.stat().st_size
    assert size > 0, "tiến trình cháu chưa kịp chạy: test vô nghĩa"
    time.sleep(0.6)
    assert beat.stat().st_size == size, "tiến trình cháu vẫn sống sau timeout (taskkill /T không diệt cả cây)"


def test_03_adapter_parse_error_is_error():
    spec = make_spec("parse_error")
    res = FakeAdapter().run(spec)
    assert res["status"] == "error" and res["verdict"]["rationale"].startswith("parse:")
    assert schema.validate_result(res) == [] and schema.check_result_against_spec(spec, res) == []


def test_04_unexpected_exception_is_crash_error_not_fail():
    res = FakeAdapter().run(make_spec("keyerror"))
    assert res["status"] == "error" and res["verdict"]["rationale"].startswith("crash:")
    assert "KeyError" in res["verdict"]["rationale"]


def test_05_checks_oracle_failure_is_fail_and_gating():
    spec = make_spec("checks_fail", oracle={"kind": "checks", "required": ["a", "b"]})
    res = FakeAdapter().run(spec)
    assert_valid(spec, res)
    assert res["status"] == "fail"
    assert res["verdict"]["value"] == "fail" and res["verdict"]["gating"] is True
    assert [f["finding_id"] for f in res["findings"]] == ["f-check-b"]


def test_06_llm_judgment_finding_without_confidence_is_error():
    res = FakeAdapter().run(make_spec("llm_no_confidence"))
    assert res["status"] == "error"
    assert "llm_judgment" in res["verdict"]["rationale"] and "confidence" in res["verdict"]["rationale"]
    assert res["findings"] == []  # không giữ finding vi phạm để "sửa cho vừa"


def test_07_missing_required_evidence_is_error():
    res = FakeAdapter().run(make_spec("no_evidence"))
    assert res["status"] == "error" and "thiếu evidence bắt buộc" in res["verdict"]["rationale"]
    # file evidence khai báo mà không tồn tại cũng là error (parse), không phải fail
    res = FakeAdapter().run(make_spec("ghost_evidence"))
    assert res["status"] == "error" and res["verdict"]["rationale"].startswith("parse:")


def test_08_unknown_oracle_kind_is_error():
    res = FakeAdapter().run(make_spec(oracle={"kind": "khong_ton_tai"}))
    assert res["status"] == "error" and "khong_ton_tai" in res["verdict"]["rationale"]


def _cli(args, stdin=None):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}  # QC_RUNS_DIR đã do fixture đặt vào os.environ
    return subprocess.run([sys.executable, str(FAKE), *args], input=stdin, capture_output=True, env=env, cwd=ROOT)


def test_09_main_exit0_even_when_result_is_fail_exit2_on_bad_spec(tmp_path):
    spec_file, out_file = tmp_path / "spec.json", tmp_path / "out" / "result.json"
    spec_file.write_text(json.dumps(make_spec("checks_fail", oracle={"kind": "checks", "required": ["a", "b"]})),
                         encoding="utf-8")
    p = _cli(["--spec", str(spec_file), "--out", str(out_file)])
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    assert json.loads(out_file.read_text(encoding="utf-8"))["status"] == "fail"

    assert _cli([], stdin=b"{khong phai json").returncode == 2
    assert _cli([], stdin=json.dumps({"task_id": "chi-co-mot-truong"}).encode()).returncode == 2
    assert _cli(["--spec", str(tmp_path / "khong_co.json")]).returncode == 2


def test_10_vietnamese_survives_stdin_to_stdout_with_bom():
    spec = make_spec("vn_parse_error", intent="Kiểm tra trang Đăng nhập chịu tải")
    stdin = b"\xef\xbb\xbf" + json.dumps(spec, ensure_ascii=False).encode("utf-8")  # BOM như PowerShell 5.1
    p = _cli([], stdin=stdin)
    assert p.returncode == 0, p.stderr.decode(errors="replace")
    res = json.loads(p.stdout.decode("ascii"))  # ensure_ascii=True: stdout cp1252 không thể vỡ
    assert res["status"] == "error"
    assert "không parse được tệp tóm tắt: định dạng lỗi — Đặng Thị Ánh" in res["verdict"]["rationale"]
