"""oracle.kind = "implicit_signals" (discovery lane): oracle_spec = {"kind": "implicit_signals", "signals": [tên, ...]}.

signals["detected"] = [{"name", "title"?, "evidence"?, "promote_candidate"?}] do ADAPTER cấp (tín hiệu tất định:
telemetry, DOM, HTTP). Oracle chỉ giữ tín hiệu có tên trong oracle_spec["signals"], gán severity theo bảng cố định.

Luôn trả value=None: discovery không phán pass/fail cổng gate. LLM chỉ được mô tả, không được phán, nên oracle này
không nhận nguồn nào ngoài `detected` của adapter.
"""
from oracle import OracleError, OracleOutcome, register

# Mức nghiêm trọng cố định trong code, không đọc từ spec: worker/plan không tự hạ được mức của một tín hiệu.
_SEVERITY = {"http_5xx": "high", "dom_unchanged": "high", "element_not_found": "medium", "console_error": "medium"}


@register("implicit_signals")
def implicit_signals(oracle_spec: dict, metrics: dict, signals: dict) -> OracleOutcome:
    wanted = oracle_spec.get("signals")
    if not isinstance(wanted, list) or not wanted or not all(isinstance(n, str) for n in wanted):
        raise OracleError("oracle implicit_signals cần 'signals' là danh sách tên tín hiệu không rỗng")
    detected = signals.get("detected")
    if not isinstance(detected, list):  # "không phát hiện gì" phải là [] tường minh, khác với "adapter không báo"
        raise OracleError("oracle implicit_signals cần signals['detected'] = [...], adapter không cung cấp")

    findings, dropped, seen = [], [], {}
    for d in detected:
        name = d.get("name") if isinstance(d, dict) else None
        if not isinstance(name, str):
            raise OracleError(f"phần tử signals['detected'] phải là dict có 'name' (str): {d!r}")
        if name not in wanted:
            dropped.append(name)
            continue
        seen[name] = seen.get(name, 0) + 1  # cùng một tín hiệu có thể xảy ra nhiều lần: mỗi lần một finding, id không trùng
        finding = {
            "finding_id": f"f-sig-{name}" if seen[name] == 1 else f"f-sig-{name}-{seen[name]}",
            "title": d.get("title") or f"tín hiệu {name}",
            "detected_by": f"implicit_signal:{name}",
            "verdict_source": "deterministic_assert",
            "confidence": None,
            "severity_hint": _SEVERITY.get(name),
        }
        if d.get("evidence"):
            finding["evidence"] = d["evidence"]
        if d.get("promote_candidate"):
            finding["promote_candidate"] = d["promote_candidate"]
        findings.append(finding)

    notes = [f"tín hiệu ngoài oracle.signals, bị bỏ: {list(dict.fromkeys(dropped))}"] if dropped else []
    return OracleOutcome(value=None, findings=findings, notes=notes)
