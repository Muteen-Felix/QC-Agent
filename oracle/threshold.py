"""oracle.kind = "threshold": oracle_spec = {"kind": "threshold", "assertions": [{"metric", "op", "value", "unit"?}, ...]}.

`metrics` là dict PHẲNG do adapter trích ({"http_req_duration.p95": 214.7}); adapter không so ngưỡng, oracle này so.
Metric đã khai báo mà adapter chưa đo được thì không có quyền phán -> OracleError (adapter đổi thành status=error),
không bao giờ là pass hay fail. Bộ so sánh không kiểm cờ gating: verdict.gating do adapter gán theo lane.
"""
import math
import operator

from oracle import OracleError, OracleOutcome, register

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def _is_number(x) -> bool:
    # bool là con của int (True < 300 chạy được) và NaN so gì cũng False: cả hai không phải một phép đo
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not math.isnan(x)


def _well_formed(a) -> bool:
    return (isinstance(a, dict) and isinstance(a.get("metric"), str)
            and isinstance(a.get("op"), str) and a["op"] in _OPS and _is_number(a.get("value")))


@register("threshold")
def threshold(oracle_spec: dict, metrics: dict, signals: dict) -> OracleOutcome:
    assertions = oracle_spec.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        # assertions rỗng sẽ pass "chay" — muốn vậy thì dùng oracle.kind=trivial một cách tường minh
        raise OracleError("oracle threshold cần 'assertions' là danh sách không rỗng")
    bad = [a for a in assertions if not _well_formed(a)]
    if bad:
        raise OracleError(f"assertion không hợp lệ (cần metric: str, op ∈ {sorted(_OPS)}, value: số): {bad}")

    names = list(dict.fromkeys(a["metric"] for a in assertions))
    missing = [n for n in names if n not in metrics]
    if missing:  # kiểm hết trước khi so: thiếu một metric thì không được phán dựa trên phần còn lại
        raise OracleError(f"adapter không cung cấp metric bắt buộc: {missing}")
    bad_type = [n for n in names if not _is_number(metrics[n])]
    if bad_type:  # None/chuỗi/bool/NaN không phải số đo
        raise OracleError(f"metric phải là số, nhận giá trị khác cho: {bad_type}")

    findings, seen = [], {}
    for a in assertions:
        m, actual = a["metric"], metrics[a["metric"]]
        if _OPS[a["op"]](actual, a["value"]):
            continue
        seen[m] = seen.get(m, 0) + 1  # cùng metric có thể có nhiều assertion (>= và <=): finding_id không được trùng
        unit = f" {a['unit']}" if a.get("unit") else ""
        findings.append({
            "finding_id": f"f-thr-{m}" if seen[m] == 1 else f"f-thr-{m}-{seen[m]}",
            "title": f"{m} = {actual}{unit} vi phạm {a['op']} {a['value']}{unit}",
            "detected_by": f"threshold:{m}",
            "verdict_source": "deterministic_assert",
            "confidence": None,
            "severity_hint": "high",
        })
    return OracleOutcome(value="fail" if findings else "pass", findings=findings)
