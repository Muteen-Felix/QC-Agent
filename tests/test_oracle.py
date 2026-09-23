"""oracle/threshold.py + oracle/signals.py: 8 test. Đi qua oracle.evaluate để kiểm luôn việc tự đăng ký (không sửa __init__)."""
import math

import pytest

from qc_agent import oracle
from qc_agent.core import schema
from qc_agent.oracle import OracleError

P95 = {"metric": "http_req_duration.p95", "op": "<", "value": 300, "unit": "ms"}
ERR = {"metric": "http_req_failed.rate", "op": "<", "value": 0.01}


def thr(*assertions):
    return {"kind": "threshold", "assertions": list(assertions)}


def sig(*names):
    return {"kind": "implicit_signals", "signals": list(names)}


def assert_valid_findings(findings):
    """Finding phải lọt schema result.json (additionalProperties=false: nhét `gating` vào finding là contract error)."""
    res = schema.make_result({"task_id": "t-1", "run_id": "r-1"}, "error", "x")
    res["findings"] = findings
    assert schema.validate_result(res) == []


def test_threshold_all_pass_is_pass_and_kind_is_autoloaded():
    assert {"threshold", "implicit_signals"} <= set(oracle._KINDS)
    out = oracle.evaluate(thr(P95, ERR), {"http_req_duration.p95": 214.7, "http_req_failed.rate": 0.002, "extra": 1}, {})
    assert out.value == "pass" and out.findings == []


def test_threshold_violation_is_fail_with_exactly_one_matching_finding():
    out = oracle.evaluate(thr(P95, ERR), {"http_req_duration.p95": 412.5, "http_req_failed.rate": 0.002}, {})
    assert out.value == "fail" and len(out.findings) == 1  # chỉ assertion sai mới thành finding
    f = out.findings[0]
    assert f["finding_id"] == "f-thr-http_req_duration.p95"
    assert f["title"] == "http_req_duration.p95 = 412.5 ms vi phạm < 300 ms"
    assert f["detected_by"] == "threshold:http_req_duration.p95"
    assert f["verdict_source"] == "deterministic_assert" and f["confidence"] is None and f["severity_hint"] == "high"
    assert "gating" not in f  # gating thuộc verdict do adapter gán, không thuộc finding
    assert_valid_findings(out.findings)


def test_threshold_missing_metric_is_error_never_a_verdict():
    with pytest.raises(OracleError, match="http_req_failed.rate"):
        oracle.evaluate(thr(P95, ERR), {"http_req_duration.p95": 100}, {})
    # assertion kia đã sai rõ ràng cũng không đổi được kết luận: thiếu số đo thì không phán, kể cả "fail"
    with pytest.raises(OracleError):
        oracle.evaluate(thr(P95, ERR), {"http_req_duration.p95": 9999}, {})


def test_threshold_supports_all_six_operators_with_boundary():
    # (op, giá trị đo, kỳ vọng pass). Ngưỡng luôn là 10 nên mỗi toán tử có đủ ba điểm: dưới, đúng biên, trên.
    table = {
        "<": (True, False, False), "<=": (True, True, False),
        ">": (False, False, True), ">=": (False, True, True),
        "==": (False, True, False), "!=": (True, False, True),
    }
    for op, expected in table.items():
        for actual, ok in zip((9, 10, 11), expected):
            out = oracle.evaluate(thr({"metric": "m", "op": op, "value": 10}), {"m": actual}, {})
            assert out.value == ("pass" if ok else "fail"), f"m={actual} {op} 10"
    # cùng một metric hai assertion cùng sai: hai finding, finding_id không trùng
    out = oracle.evaluate(thr({"metric": "m", "op": ">", "value": 10}, {"metric": "m", "op": "<", "value": 5}), {"m": 7}, {})
    assert [f["finding_id"] for f in out.findings] == ["f-thr-m", "f-thr-m-2"]


def test_threshold_rejects_malformed_spec_or_unmeasurable_values():
    good = {"m": 1}
    for spec, metrics in [
        ({"kind": "threshold"}, good),  # thiếu assertions
        (thr(), good),  # rỗng = pass chay
        (thr({"metric": "m", "op": "=~", "value": 1}), good),  # toán tử lạ
        (thr({"metric": "m", "op": "<", "value": "1"}), good),  # ngưỡng không phải số
        (thr({"op": "<", "value": 1}), good),  # thiếu tên metric
        (thr({"metric": "m", "op": "<", "value": 1}), {"m": None}),  # chưa đo
        (thr({"metric": "m", "op": "<", "value": 1}), {"m": True}),  # bool không phải số đo
        (thr({"metric": "m", "op": "!=", "value": 1}), {"m": math.nan}),  # NaN != 1 là True: sẽ pass oan
    ]:
        with pytest.raises(OracleError):
            oracle.evaluate(spec, metrics, {})


def test_signals_keep_only_named_signals_and_note_the_dropped_ones():
    detected = [
        {"name": "http_5xx", "title": "POST /api/notes -> 502"},
        {"name": "mystery", "title": "lạ"},
        {"name": "console_error"},
        {"name": "mystery"},
        {"name": "dom_unchanged", "title": "có trong plan nhưng không khai ở oracle.signals"},
    ]
    out = oracle.evaluate(sig("http_5xx", "console_error", "element_not_found"), {}, {"detected": detected})
    assert [f["detected_by"] for f in out.findings] == ["implicit_signal:http_5xx", "implicit_signal:console_error"]
    assert out.findings[0]["title"] == "POST /api/notes -> 502"
    assert out.findings[1]["title"]  # thiếu title thì có tiêu đề dự phòng, không rỗng
    # mức nghiêm trọng theo bảng cố định trong code; mọi finding tất định, không confidence
    assert [f["severity_hint"] for f in out.findings] == ["high", "medium"]
    assert all(f["verdict_source"] == "deterministic_assert" and f["confidence"] is None for f in out.findings)
    assert len(out.notes) == 1 and "mystery" in out.notes[0] and "dom_unchanged" in out.notes[0]
    assert_valid_findings(out.findings)


def test_signals_never_judge_and_severity_table_is_fixed():
    names = ["http_5xx", "dom_unchanged", "element_not_found", "console_error"]
    out = oracle.evaluate(sig(*names), {}, {"detected": [{"name": n} for n in names]})
    assert out.value is None  # có 4 phát hiện mà vẫn không phán: discovery không chặn gate
    assert {f["detected_by"].split(":")[1]: f["severity_hint"] for f in out.findings} == {
        "http_5xx": "high", "dom_unchanged": "high", "element_not_found": "medium", "console_error": "medium"}
    assert oracle.evaluate(sig("http_5xx"), {}, {"detected": []}) == oracle.OracleOutcome(value=None)
    for spec, signals in [(sig("http_5xx"), {}),  # adapter không báo 'detected' khác với "báo rỗng"
                          (sig("http_5xx"), {"detected": [{"title": "thiếu name"}]}),
                          ({"kind": "implicit_signals", "signals": []}, {"detected": []})]:
        with pytest.raises(OracleError):
            oracle.evaluate(spec, {}, signals)


def test_signals_merge_evidence_and_promote_candidate_and_keep_ids_unique():
    ev = [{"kind": "screenshot", "uri": "runs/r-1/t-1/s.png", "sha256": "a" * 64}]
    promote = {"suggested_capability": "e2e.delete_note", "repro_steps": ["mở /", "bấm Xoá"],
               "suggested_assertion": "note biến mất sau khi bấm Xoá"}
    detected = [{"name": "dom_unchanged", "title": "danh sách không đổi sau Xoá", "evidence": ev, "promote_candidate": promote},
                {"name": "dom_unchanged", "title": "lần hai"}]
    out = oracle.evaluate(sig("dom_unchanged"), {}, {"detected": detected})
    first, second = out.findings
    assert first["evidence"] == ev and first["promote_candidate"] == promote
    assert "evidence" not in second and "promote_candidate" not in second  # không bịa trường adapter không cấp
    assert [first["finding_id"], second["finding_id"]] == ["f-sig-dom_unchanged", "f-sig-dom_unchanged-2"]
    assert_valid_findings(out.findings)
