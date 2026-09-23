"""oracle.kind = "checks": oracle_spec = {"kind": "checks", "required": [tên, ...]}, signals["checks"] = {tên: bool}.

Chỉ các check trong `required` tham gia phán. Check bị thiếu KHÔNG được coi là pass: worker chưa chạy check đó thì
không có quyền khẳng định gì -> OracleError (adapter đổi thành status=error).
"""
from qc_agent.oracle import OracleError, OracleOutcome, register


@register("checks")
def checks(oracle_spec: dict, metrics: dict, signals: dict) -> OracleOutcome:
    required = oracle_spec.get("required")
    if not isinstance(required, list) or not required or not all(isinstance(n, str) for n in required):
        # required rỗng sẽ pass "chay" — muốn vậy thì dùng oracle.kind=trivial một cách tường minh
        raise OracleError("oracle checks cần 'required' là danh sách tên check không rỗng")

    got = signals.get("checks")
    if not isinstance(got, dict):
        raise OracleError("oracle checks cần signals['checks'] = {tên: bool}, adapter không cung cấp")

    missing = [n for n in dict.fromkeys(required) if n not in got]
    if missing:
        raise OracleError(f"thiếu kết quả check bắt buộc trong signals['checks']: {missing}")
    bad_type = [n for n in dict.fromkeys(required) if not isinstance(got[n], bool)]
    if bad_type:  # truthy/None không phải pass
        raise OracleError(f"kết quả check phải là bool, nhận giá trị khác cho: {bad_type}")

    failed = [n for n in dict.fromkeys(required) if got[n] is False]
    findings = [{
        "finding_id": f"f-check-{n}",
        "title": f"check {n} không đạt",
        "detected_by": f"check:{n}",
        "verdict_source": "deterministic_assert",
        "confidence": None,
        "severity_hint": "high",
    } for n in failed]
    extra = sorted(set(got) - set(required))
    notes = [f"check ngoài required, không tham gia phán: {extra}"] if extra else []
    return OracleOutcome(value="fail" if failed else "pass", findings=findings, notes=notes)
