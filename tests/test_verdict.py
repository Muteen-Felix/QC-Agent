from qc_agent.core.findings import NormFinding
from qc_agent.core.verdict import BLOCKED, PASSED, PASSED_WITH_WARNINGS, gate_verdict


def finding(severity):
    return NormFinding("fp", severity, "task", "suite", "worker", "rule", "title", None, None, None,
                       "gate", "deterministic_assert", "finding")


def test_verdict_by_severity_and_infrastructure():
    assert (gate_verdict([], []).value, gate_verdict([], []).exit_code) == (PASSED, 0)
    assert gate_verdict([finding("low")], []).value == PASSED_WITH_WARNINGS
    for severity in ("medium", "critical"):
        gate = gate_verdict([finding(severity)], [])
        assert gate.value == BLOCKED and gate.exit_code == 1 and gate.counts[severity] == 1
    assert gate_verdict([], [("task", "error")]).value == BLOCKED


def test_policy_can_change_block_set():
    assert gate_verdict([finding("medium")], [], block_on=("critical",)).value == PASSED_WITH_WARNINGS
