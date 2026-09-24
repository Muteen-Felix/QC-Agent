from qc_agent.core.report import RunContext, render
from qc_agent.core.verdict import canary_alerts, gate_verdict


def result(status, *, gating=False):
    return {
        "task_id": "t-canary",
        "worker": {"name": "mock"},
        "status": status,
        "verdict": {
            "value": "pass" if status == "pass" else "fail",
            "gating": gating,
            "verdict_source": "deterministic_assert",
            "rationale": None,
        },
        "findings": [],
        "metrics": {},
        "evidence": [],
        "cost": {},
        "determinism": {"replay_cmd": None},
    }


def test_fail_expected_fail_is_ok():
    alerts = canary_alerts({"t-canary": result("fail")}, {"t-canary": {"expect_status": "fail"}})
    assert alerts == [{
        "task_id": "t-canary", "expected": "fail", "actual": "fail", "ok": True,
        "message": "CANARY t-canary: OK (fail như kỳ vọng)",
    }]


def test_pass_expected_fail_is_broken_and_banner_precedes_verdict():
    results = {"t-canary": result("pass")}
    specs = {"t-canary": {"lane": "discovery", "capability": "demo.echo"}}
    alerts = canary_alerts(results, {"t-canary": {"expect_status": "fail"}})
    ctx = RunContext(
        run_id="r-0001", plan_id="plan-1", plan_name="demo", plan_path="plan.yaml",
        plan_text="tasks:\n  - task_id: t-canary\n", sut_id="sut-1", run_signature="sig-1",
        generated_at="2026-09-22T00:00:00+00:00", wallclock_s=0, specs=specs,
        results=results, gate=gate_verdict(results, specs), canary=alerts,
    )
    md, _ = render(ctx)
    assert alerts[0]["ok"] is False
    assert md.index("CANARY HỎNG") < md.index("## VERDICT")


def test_missing_result_is_broken():
    alert = canary_alerts({}, {"t-canary": {"expect_status": "fail"}})[0]
    assert alert["actual"] is None
    assert alert["ok"] is False


def test_broken_canary_does_not_change_gate_verdict():
    specs = {
        "t-gate": {"lane": "gate"},
        "t-canary": {"lane": "discovery"},
    }
    results = {
        "t-gate": result("pass", gating=True),
        "t-canary": result("pass"),
    }
    before = gate_verdict(results, specs)
    alerts = canary_alerts(results, {"t-canary": {"expect_status": "fail"}})
    after = gate_verdict(results, specs)
    assert alerts[0]["ok"] is False
    assert (before.value, before.exit_code) == (after.value, after.exit_code) == ("PASS", 0)
