import copy
import json
from pathlib import Path

import pytest

from core import schema

ROOT = Path(__file__).resolve().parent.parent


def L(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8-sig"))


K6S, K6R = L("task.k6.json"), L("result.k6_pass.json")
MS, MP, MF = L("task.midscene.json"), L("result.e2e_pass.json"), L("result.e2e_fail.json")
EV = L("result.ai_eval.json")


def fit(spec, res):
    s = copy.deepcopy(spec)
    s["task_id"], s["run_id"] = res["task_id"], res["run_id"]
    return s


AI_SPEC = fit(K6S, EV)


def _mut(base, fn):
    o = copy.deepcopy(base)
    fn(o)
    return o


def _llm_finding(res):
    return next(f for f in res["findings"] if f["verdict_source"] == "llm_judgment")


@pytest.mark.parametrize("res", [K6R, MP, MF, EV], ids=["k6", "e2e_pass", "e2e_fail", "ai_eval"])
def test_examples_validate(res):
    assert schema.validate_result(res) == []


@pytest.mark.parametrize("spec", [K6S, MS], ids=["k6", "midscene"])
def test_task_examples_validate(spec):
    assert schema.validate_task(spec) == []


@pytest.mark.parametrize("spec,res", [(K6S, K6R), (fit(MS, MP), MP), (fit(MS, MF), MF), (AI_SPEC, EV)],
                         ids=["k6", "e2e_pass", "canary_fail", "ai_eval"])
def test_good_pairs_have_no_violations(spec, res):
    assert schema.check_result_against_spec(spec, res) == []


def test_gate_task_silently_non_gating():
    r = _mut(K6R, lambda o: o["verdict"].update(gating=False, value="non_gating"))
    assert any("gating" in v for v in schema.check_result_against_spec(K6S, r))


def test_discovery_task_cannot_gate():
    r = _mut(MP, lambda o: o["verdict"].update(gating=True, verdict_source="deterministic_assert", value="pass"))
    assert any("discovery" in v for v in schema.check_result_against_spec(fit(MS, r), r))


def test_llm_finding_without_confidence():
    r = _mut(EV, lambda o: _llm_finding(o).pop("confidence"))
    assert any("confidence" in v for v in schema.check_result_against_spec(AI_SPEC, r))


def test_missing_required_evidence():
    r = _mut(K6R, lambda o: o.update(evidence=o["evidence"][:1]))
    assert any("evidence" in v for v in schema.check_result_against_spec(K6S, r))


def test_status_disagrees_with_verdict():
    assert schema.check_result_against_spec(K6S, _mut(K6R, lambda o: o.update(status="fail")))


def test_task_id_and_run_id_mismatch():
    assert any("run_id" in v for v in schema.check_result_against_spec(dict(K6S, run_id="r-9999"), K6R))
    assert any("task_id" in v for v in schema.check_result_against_spec(dict(K6S, task_id="t-999"), K6R))


@pytest.mark.parametrize("status", ["error", "skipped"])
def test_make_result_is_valid_and_utf8(status):
    r = schema.make_result(K6S, status, "lý do có dấu: không thấy binary", worker_name="w")
    assert schema.validate_result(r) == [] and schema.check_result_against_spec(K6S, r) == []
    assert json.loads(json.dumps(r, ensure_ascii=True)) == r


def test_known_gap_g4_schema_does_not_reject_llm_finding_without_confidence():
    """Lỗ hổng ĐÃ BIẾT (arch §0.3 G4): schema không chặn. check_result_against_spec phải chặn."""
    r = _mut(EV, lambda o: _llm_finding(o).pop("confidence"))
    assert schema.validate_result(r) == []
