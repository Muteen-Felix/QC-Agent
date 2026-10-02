"""Pure severity based gate verdict; worker results are normalized first."""
from dataclasses import dataclass, field

BLOCKED, PASSED_WITH_WARNINGS, PASSED = "BLOCKED", "PASSED_WITH_WARNINGS", "PASSED"


@dataclass
class GateVerdict:
    value: str
    exit_code: int
    reasons: list = field(default_factory=list)
    banner: list = field(default_factory=list)
    counts: dict = field(default_factory=lambda: {"critical": 0, "medium": 0, "low": 0})


def gate_verdict(findings, infra_blockers, block_on=("critical", "medium")) -> GateVerdict:
    counts = {"critical": 0, "medium": 0, "low": 0}
    reasons = list(infra_blockers)
    for finding in findings:
        counts[finding.severity] += 1
        if (finding.severity in block_on and finding.lane == "gate" and
                finding.verdict_source != "llm_judgment" and
                not (finding.source == "task_default" and finding.title == "skipped gate task")):
            reasons.append((finding.task_id, f"{finding.severity}: {finding.title}"))
    if reasons:
        return GateVerdict(BLOCKED, 1, reasons, counts=counts)
    if findings:
        return GateVerdict(PASSED_WITH_WARNINGS, 0, reasons, counts=counts)
    return GateVerdict(PASSED, 0, reasons, counts=counts)


def canary_alerts(results: dict, plan_only: dict) -> list[dict]:
    """Compare canary outcomes with plan expectations without affecting the gate."""
    alerts = []
    for task_id, fields in plan_only.items():
        if "expect_status" not in fields:
            continue
        expected = fields["expect_status"]
        result = results.get(task_id)
        actual = result.get("status") if result is not None else None
        ok = actual == expected
        if ok:
            message = f"CANARY {task_id}: OK ({expected} như kỳ vọng)"
        else:
            shown_actual = actual if actual is not None else "missing"
            message = (
                f"CANARY HỎNG: {task_id} báo {shown_actual} cho task chắc chắn phải {expected} "
                "— worker tự hành không đáng tin"
            )
        alerts.append({"task_id": task_id, "expected": expected, "actual": actual, "ok": ok, "message": message})
    return alerts


def lane_has_gate(specs: dict) -> bool:
    return any(s["lane"] == "gate" for s in specs.values())
