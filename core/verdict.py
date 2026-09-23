"""Gộp verdict. Hàm THUẦN: không I/O, không LLM. Luật: architecture.md §6.3.
FAIL > YELLOW > PASS.  exit_code: FAIL=1, YELLOW=0 (D-02), PASS=0.
"""
from dataclasses import dataclass, field

PASS, YELLOW, FAIL = "PASS", "YELLOW", "FAIL"


@dataclass
class GateVerdict:
    value: str
    exit_code: int
    reasons: list = field(default_factory=list)  # mỗi phần tử: (task_id, lý do)
    banner: list = field(default_factory=list)  # in Ở ĐẦU report: skipped / error


def gate_verdict(results: dict, specs: dict) -> GateVerdict:
    """results: {task_id: result_dict}; specs: {task_id: spec_dict} — chỉ gồm task ĐƯỢC CHỌN."""
    reasons, banner = [], []
    fail = yellow = False
    gating_seen = 0
    for tid, spec in specs.items():
        r = results.get(tid)
        lane = spec["lane"]
        if r is None:  # task được chọn mà không có result
            (reasons if lane == "gate" else banner).append((tid, "không có result"))
            fail = fail or lane == "gate"
            continue
        st = r["status"]
        if st == "error":
            banner.append((tid, "error: " + str((r["verdict"].get("rationale") or ""))[:120]))
            if lane == "gate":
                fail = True
                reasons.append((tid, "error (hạ tầng)"))
        elif st == "skipped":
            banner.append((tid, "skipped: " + str((r["verdict"].get("rationale") or ""))[:120]))
            if lane == "gate":
                yellow = True
        elif r["verdict"]["gating"]:
            gating_seen += 1
            if r["verdict"]["value"] != "pass":
                fail = True
                reasons.append((tid, "assert tất định fail"))
    if lane_has_gate(specs) and gating_seen == 0 and not fail:
        fail = True
        reasons.append(("*", "không có result gating nào — không có gate"))  # chặn AND-rỗng
    if fail:
        return GateVerdict(FAIL, 1, reasons, banner)
    if yellow:
        return GateVerdict(YELLOW, 0, reasons, banner)
    return GateVerdict(PASS, 0, reasons, banner)


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
        alerts.append({
            "task_id": task_id,
            "expected": expected,
            "actual": actual,
            "ok": ok,
            "message": message,
        })
    return alerts


def lane_has_gate(specs: dict) -> bool:
    return any(s["lane"] == "gate" for s in specs.values())
