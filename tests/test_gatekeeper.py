"""Severity, lane, verdict source and status combinations use deterministic policy."""
from itertools import product

import pytest

from qc_agent.core.findings import normalize
from qc_agent.core.verdict import BLOCKED, PASSED, PASSED_WITH_WARNINGS, gate_verdict


@pytest.mark.parametrize("severity,lane,source,status", list(product(
    ("low", "medium", "critical"), ("gate", "discovery"),
    ("deterministic_assert", "llm_judgment", "heuristic"), ("pass", "fail", "error", "skipped"))))
def test_gatekeeper_combinations(severity, lane, source, status):
    result = {"status": status, "worker": {"name": "worker"},
              "verdict": {"gating": status in ("pass", "fail"), "verdict_source": source},
              "findings": [{"title": "finding", "detected_by": "tool:rule", "severity_hint": severity,
                            "verdict_source": source}]}
    specs = {"task": {"lane": lane}}
    findings, blockers = normalize({"task": result}, specs, policy={"on_skipped_gate_task": "fail"})
    actual = gate_verdict(findings, blockers).value
    blocks_by_infra = lane == "gate" and status in ("error", "skipped")
    blocks_by_severity = (lane == "gate" and status in ("pass", "fail") and
                          source != "llm_judgment" and severity in ("medium", "critical"))
    expected = (BLOCKED if blocks_by_infra or blocks_by_severity else
                PASSED if lane == "discovery" and status in ("error", "skipped") else PASSED_WITH_WARNINGS)
    assert actual == expected


def test_llm_and_skipped_yellow_never_block_when_low_is_in_block_on():
    specs = {"pass": {"lane": "gate"}, "llm": {"lane": "gate"}, "skipped": {"lane": "gate"}}
    results = {
        "pass": {"status": "pass", "worker": {"name": "mock"}, "verdict": {"gating": True}, "findings": []},
        "llm": {"status": "pass", "worker": {"name": "llm"}, "verdict": {"gating": False, "verdict_source": "llm_judgment"},
                "findings": [{"title": "low", "detected_by": "llm:score", "severity_hint": "low", "verdict_source": "llm_judgment"}]},
        "skipped": {"status": "skipped", "worker": {"name": "mock"}, "verdict": {"gating": False}, "findings": []},
    }
    findings, blockers = normalize(results, specs, policy={"on_skipped_gate_task": "yellow"})
    assert not blockers and gate_verdict(findings, blockers, block_on=("low",)).value == PASSED_WITH_WARNINGS
