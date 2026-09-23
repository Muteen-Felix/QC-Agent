import copy
import json
from pathlib import Path

from adapters.mock_adapter import MockAdapter
from core import schema


ROOT = Path(__file__).resolve().parent.parent
PASS_SPEC = json.loads((ROOT / "tests/fixtures/task_mock_pass.json").read_text(encoding="utf-8"))
FAIL_SPEC = json.loads((ROOT / "tests/fixtures/task_mock_fail.json").read_text(encoding="utf-8"))


def run(spec, tmp_path, monkeypatch):
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    return MockAdapter().run(spec)


def test_mock_fixture_passes(tmp_path, monkeypatch):
    result = run(copy.deepcopy(PASS_SPEC), tmp_path, monkeypatch)
    assert result["status"] == "pass"
    assert result["cost"]["tokens"] == 0 and result["cost"]["usd"] == 0.0
    assert {item["kind"] for item in result["evidence"]} == {"raw_output", "stdout"}
    assert schema.validate_result(result) == []


def test_mock_fixture_fails_via_checks_oracle(tmp_path, monkeypatch):
    result = run(copy.deepcopy(FAIL_SPEC), tmp_path, monkeypatch)
    assert result["status"] == "fail"
    assert result["verdict"]["gating"] is True
    assert [finding["finding_id"] for finding in result["findings"]] == ["f-check-always_true"]


def test_missing_fixture_is_error_not_fail(tmp_path, monkeypatch):
    spec = copy.deepcopy(PASS_SPEC)
    spec["inputs"]["fixture"] = "tests/fixtures/does-not-exist.json"
    result = run(spec, tmp_path, monkeypatch)
    assert result["status"] == "error"
    assert result["verdict"]["rationale"].startswith("parse: fixture không tồn tại:")
