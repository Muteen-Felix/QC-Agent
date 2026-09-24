"""Gói oracle/ (đăng ký, trivial, checks) và nhánh discovery của Adapter.run. Ngoài 10 test bắt buộc của test_base.py."""
import copy
import json
from pathlib import Path

import pytest

from qc_agent import oracle
from qc_agent.core import schema
from fake_adapter import FakeAdapter
from qc_agent.oracle import OracleError

_K6 = json.loads((Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "contract" / "task.k6.json").read_text(encoding="utf-8-sig"))


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))


def spec_for(mode, lane="gate", **over):
    s = copy.deepcopy(_K6)
    s.update(capability="demo.echo", oracle={"kind": "trivial"}, evidence_required=["raw_output"], lane=lane,
             expected_result_kind="verdict" if lane == "gate" else "candidate_finding")
    s["inputs"] = {"mode": mode}
    s["budget"] = {"wallclock_s": 20, "tokens": 0, "usd": 0}
    s.update(over)
    assert schema.validate_task(s) == []
    return s


def test_trivial_always_passes_and_kinds_are_autoloaded():
    assert {"trivial", "checks"} <= set(oracle._KINDS)  # nạp động, __init__ không import từng module
    assert oracle.evaluate({"kind": "trivial"}, {}, {}) == oracle.OracleOutcome(value="pass")


def test_checks_pass_fail_and_only_required_checks_judge():
    spec = {"kind": "checks", "required": ["a", "b"]}
    ok = oracle.evaluate(spec, {}, {"checks": {"a": True, "b": True, "extra": False}})
    assert ok.value == "pass" and ok.findings == [] and "extra" in ok.notes[0]
    bad = oracle.evaluate(spec, {}, {"checks": {"a": False, "b": False}})
    assert bad.value == "fail"
    assert [f["finding_id"] for f in bad.findings] == ["f-check-a", "f-check-b"]
    assert bad.findings[0]["detected_by"] == "check:a" and bad.findings[0]["verdict_source"] == "deterministic_assert"


@pytest.mark.parametrize("spec,signals", [
    ({"kind": "checks", "required": ["a", "b"]}, {"checks": {"a": True}}),  # thiếu check bắt buộc
    ({"kind": "checks", "required": ["a"]}, {}),  # không có signals["checks"]
    ({"kind": "checks", "required": ["a"]}, {"checks": {"a": 1}}),  # truthy không phải bool
    ({"kind": "checks", "required": ["a"]}, {"checks": {"a": None}}),
    ({"kind": "checks", "required": []}, {"checks": {}}),  # required rỗng = pass chay
    ({"kind": "checks"}, {"checks": {"a": True}}),
])
def test_checks_never_pass_on_insufficient_data(spec, signals):
    with pytest.raises(OracleError):
        oracle.evaluate(spec, {}, signals)


def test_register_rejects_conflicting_kind_but_allows_same_function_reload():
    with pytest.raises(OracleError, match="đã được đăng ký"):
        oracle.register("trivial")(lambda s, m, g: oracle.OracleOutcome("pass"))
    same = oracle._KINDS["trivial"]
    assert oracle.register("trivial")(same) is same


def test_evaluate_rejects_bad_oracle_return(monkeypatch):
    monkeypatch.setitem(oracle._KINDS, "bad", lambda s, m, g: oracle.OracleOutcome(value="maybe"))
    with pytest.raises(OracleError):
        oracle.evaluate({"kind": "bad"}, {}, {})


def test_discovery_never_gates():
    spec = spec_for("ok", lane="discovery")
    res = FakeAdapter().run(spec)
    assert schema.validate_result(res) == [] and schema.check_result_against_spec(spec, res) == []
    assert res["status"] == "pass" and res["verdict"]["value"] == "non_gating"
    assert res["verdict"]["gating"] is False and res["verdict"]["verdict_source"] == "heuristic"


def test_discovery_flow_failed_or_failed_check_is_fail_but_still_not_gating():
    for spec in (spec_for("flow_failed", lane="discovery"),
                 spec_for("checks_fail", lane="discovery", oracle={"kind": "checks", "required": ["a", "b"]})):
        res = FakeAdapter().run(spec)
        assert schema.validate_result(res) == [] and schema.check_result_against_spec(spec, res) == []
        assert res["status"] == "fail" and res["verdict"]["gating"] is False


def test_cost_reports_worker_tokens_and_never_invents_them():
    res = FakeAdapter().run(spec_for("tokens"))
    assert res["cost"]["tokens"] == 1234 and res["cost"]["usd"] == 0.02
    res = FakeAdapter().run(spec_for("ok"))
    assert res["cost"]["tokens"] is None and res["cost"]["usd"] is None


def test_gate_task_with_non_judging_oracle_is_error(monkeypatch):
    monkeypatch.setitem(oracle._KINDS, "abstain", lambda s, m, g: oracle.OracleOutcome(value=None))
    res = FakeAdapter().run(spec_for("ok", oracle={"kind": "abstain"}))
    assert res["status"] == "error" and res["verdict"]["rationale"].startswith("parse:")


def test_run_id_cannot_escape_runs_dir():
    res = FakeAdapter().run(spec_for("ok", run_id="..", task_id="..x"))
    assert res["status"] == "error" and res["verdict"]["rationale"].startswith("crash:")
