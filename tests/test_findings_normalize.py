from qc_agent.core.findings import normalize
from qc_agent.core.verdict import BLOCKED, PASSED_WITH_WARNINGS, gate_verdict


def result(status="pass", *, hint="critical", source="deterministic_assert", title="rule @ src/app.py:12",
           path="src/app.py", detected_by="semgrep:R1", findings=True):
    return {"status": status, "worker": {"name": "semgrep"},
            "verdict": {"gating": status in ("pass", "fail"), "verdict_source": source},
            "findings": ([{"title": title, "detected_by": detected_by, "severity_hint": hint,
                           "verdict_source": source, "location": {"path": path, "line": 12}}] if findings else [])}


def test_override_and_stable_fingerprint():
    spec = {"t": {"lane": "gate"}}
    policy = {"default_severity": {"*": "medium"}, "overrides": [
        {"suite": "sast", "rule_id": "R*", "from": "critical", "to": "low"}]}
    a, blockers = normalize({"t": result()}, spec, task_suite={"t": "sast"}, policy=policy)
    b, _ = normalize({"t": result(title="rule @ src/app.py:17")}, spec,
                     task_suite={"t": "sast"}, policy=policy)
    assert not blockers and a[0].severity == "low" and a[0].rule_id == "R1"
    assert a[0].fingerprint == b[0].fingerprint
    assert gate_verdict(a, blockers).value == PASSED_WITH_WARNINGS
    c, _ = normalize({"t": result(path="src/other.py")}, spec, task_suite={"t": "sast"}, policy=policy)
    assert c[0].fingerprint != a[0].fingerprint


def test_llm_and_discovery_are_capped_after_override():
    policy = {"overrides": [{"suite": "*", "rule_id": "*", "from": "medium", "to": "critical"}]}
    for lane, source in (("gate", "llm_judgment"), ("discovery", "deterministic_assert")):
        found, _ = normalize({"t": result(hint="medium", source=source)}, {"t": {"lane": lane}}, policy=policy)
        assert found[0].severity == "low"


def test_fail_without_findings_and_empty_gate_block():
    found, blockers = normalize({"t": result("fail", findings=False)}, {"t": {"lane": "gate"}})
    assert found[0].source == "task_default" and found[0].severity == "medium"
    assert gate_verdict(found, blockers).value == BLOCKED
    _, blockers = normalize({"t": result("skipped", findings=False)}, {"t": {"lane": "gate"}},
                            policy={"on_skipped_gate_task": "yellow"})
    assert blockers  # no gating result still blocks


def test_threshold_rule_id_without_location():
    r = result(path="src/app.py", detected_by="threshold:pytest.failures")
    r["findings"][0].pop("location")
    found, _ = normalize({"t": r}, {"t": {"lane": "gate"}})
    assert found[0].rule_id == "pytest.failures" and found[0].path is None


def test_skipped_yellow_ignores_untrusted_stale_findings():
    results = {"ok": result(findings=False), "skip": result("skipped", hint="critical")}
    specs = {"ok": {"lane": "gate"}, "skip": {"lane": "gate"}}
    found, blockers = normalize(results, specs, policy={"on_skipped_gate_task": "yellow"})
    assert not blockers and len(found) == 1 and found[0].title == "skipped gate task"
    assert gate_verdict(found, blockers).value == PASSED_WITH_WARNINGS
