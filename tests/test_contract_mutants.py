"""Mutation testing CỦA HỢP ĐỒNG: cố tình làm hỏng một result/task hợp lệ, schema PHẢI từ chối.
Mutant nào không bị từ chối = lỗ hổng của contract (đây là N2/N4 ở dạng test).
Tên test = ID mutant (C1.., T1..)."""
import copy
import json
import pathlib

import pytest

from core import schema


def L(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))


GATE = L("tests/fixtures/contract/result.ai_eval.json")
DISC = L("tests/fixtures/contract/result.e2e_pass.json")
TASK = L("tests/fixtures/contract/task.k6.json")
TASK_DISC = L("tests/fixtures/contract/task.midscene.json")


def mutate(base, fn):
    o = copy.deepcopy(base)
    fn(o)
    return o


RESULT_MUTANTS = {
    "C1_gating_true_with_llm_judgment": (GATE, lambda o: o["verdict"].update(verdict_source="llm_judgment", confidence=0.7)),
    "C2_deterministic_with_confidence": (GATE, lambda o: o["verdict"].update(confidence=0.9)),
    "C3_evidence_sha256_too_short": (GATE, lambda o: o["evidence"][0].update(sha256="abc123")),
    "C4_missing_status": (GATE, lambda o: o.pop("status")),
    "C5_status_not_in_enum": (GATE, lambda o: o.update(status="timeout")),
    "C6_extra_top_level_field": (GATE, lambda o: o.update(worker_id="k6")),
    "C7_heuristic_gating_true": (DISC, lambda o: o["verdict"].update(gating=True)),
    "C8_evidence_kind_outside_vocab": (GATE, lambda o: o["evidence"][0].update(kind="screenshots")),
    "C9_missing_wallclock": (GATE, lambda o: o["cost"].pop("wallclock_s")),
}

TASK_MUTANTS = {
    "T1_retry_on_fail": (TASK, lambda o: o["retry"].update(on=["fail"])),
    "T2_retry_max_3": (TASK, lambda o: o["retry"].update(max=3)),
    "T3_discovery_expects_verdict": (TASK_DISC, lambda o: o.update(expected_result_kind="verdict")),
    "T4_gate_expects_candidate_finding": (TASK, lambda o: o.update(expected_result_kind="candidate_finding")),
    "T5_worker_name_in_spec": (TASK, lambda o: o.update(worker="k6")),
    "T6_budget_missing_usd": (TASK, lambda o: o["budget"].pop("usd")),
    "T7_evidence_required_outside_vocab": (TASK, lambda o: o["evidence_required"].append("screenshots")),
}


@pytest.mark.parametrize("name", RESULT_MUTANTS)
def test_result_mutant_is_rejected(name):
    base, fn = RESULT_MUTANTS[name]
    assert schema.validate_result(mutate(base, fn)), f"{name}: schema KHÔNG chặn — contract có lỗ hổng"


@pytest.mark.parametrize("name", TASK_MUTANTS)
def test_task_mutant_is_rejected(name):
    base, fn = TASK_MUTANTS[name]
    assert schema.validate_task(mutate(base, fn)), f"{name}: schema KHÔNG chặn — contract có lỗ hổng"
