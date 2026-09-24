import copy
import json

from qc_agent.core.report import RunContext, render, write
from qc_agent.core.verdict import gate_verdict


PLAN = """name: demo
tasks:
  - task_id: t-gate
    capability: demo.echo
  - task_id: t-ai
    capability: llmapp.eval
  - task_id: t-ui
    capability: ui.explore
"""


def result(task_id, *, gating=True, status="pass", value="pass", source="deterministic_assert"):
    return {
        "task_id": task_id,
        "worker": {"name": f"worker-{task_id}"},
        "status": status,
        "verdict": {"value": value, "verdict_source": source, "gating": gating, "rationale": None},
        "findings": [],
        "metrics": {"ping_ms": 1},
        "evidence": [{"kind": "raw_output", "uri": f"runs/r-0001/{task_id}/raw.json", "sha256": "a" * 64}],
        "cost": {"wallclock_s": 1.0, "tokens": 0, "usd": 0.0},
        "determinism": {"replay_cmd": f"replay {task_id}"},
    }


def context(*, skipped=False):
    specs = {
        "t-gate": {"task_id": "t-gate", "lane": "gate", "capability": "demo.echo"},
        "t-ai": {"task_id": "t-ai", "lane": "gate", "capability": "llmapp.eval"},
        "t-ui": {"task_id": "t-ui", "lane": "discovery", "capability": "ui.explore"},
    }
    results = {
        "t-gate": result("t-gate"),
        "t-ai": result("t-ai"),
        "t-ui": result("t-ui", gating=False, value="non_gating", source="heuristic"),
    }
    results["t-ai"]["metrics"] = {"GEval.score": 0.71}
    results["t-ai"]["findings"] = [{
        "finding_id": "f-ai", "title": "Giữ ý chính", "detected_by": "metric:GEval",
        "verdict_source": "llm_judgment", "confidence": 0.64,
    }]
    results["t-ui"]["metrics"] = {"steps_executed": 3}
    results["t-ui"]["cost"] = {"wallclock_s": 2.0, "tokens": 120, "usd": 0.01}
    if skipped:
        results["t-ai"] = result("t-ai", gating=False, status="skipped", value="non_gating", source="heuristic")
        results["t-ai"]["verdict"]["rationale"] = "thiếu key"
    gate = gate_verdict(results, specs)
    return RunContext(
        run_id="r-0001", plan_id="plan-1a2b3c4d", plan_name="demo", plan_path="plans/demo.yaml",
        plan_text=PLAN, sut_id="sut-9f2c0a11", run_signature="sig-1", generated_at="2026-09-19 14:32",
        wallclock_s=192, specs=specs, results=results, gate=gate,
    )


def test_has_all_five_sections_in_order():
    md, _ = render(context())
    assert md.startswith("# QC Gate Report — run r-0001\n")
    assert "plan: plans/demo.yaml (plan-1a2b3c4d) · SUT: sut-9f2c0a11 · 2026-09-19 14:32" in md
    assert "wallclock 3m12s · LLM tokens: 120 (tasks: t-ui) · cost $0.01" in md
    headings = [
        "## 1. DETERMINISTIC ASSERT", "## 2. LLM JUDGMENT", "## 3. HEURISTIC / DISCOVERY",
        "## 4. SKIPPED / ERROR", "## 5. AUDIT",
    ]
    positions = [md.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_low_llm_score_cannot_change_verdict_line():
    ctx = context()
    before, _ = render(ctx)
    changed = copy.deepcopy(ctx)
    changed.results["t-ai"]["metrics"]["GEval.score"] = 0.01
    changed.results["t-ai"]["findings"][0]["confidence"] = 0.01
    after, _ = render(changed)
    verdict = lambda text: next(line for line in text.splitlines() if line.startswith("## VERDICT:"))
    assert verdict(before) == verdict(after) == "## VERDICT: ✅ PASS"


def test_skipped_banner_precedes_verdict():
    md, _ = render(context(skipped=True))
    assert md.index("## ⚠ SKIPPED / ERROR — ĐỌC TRƯỚC") < md.index("## VERDICT:")


def test_gate_formula_matches_gate_value():
    ctx = context()
    md, _ = render(ctx)
    line = next(line for line in md.splitlines() if line.startswith("→ gate_verdict ="))
    assert line.endswith(f"**{ctx.gate.value}**")


def test_deterministic_view_excludes_non_gating_and_unstable_fields():
    _, data = render(context())
    view = data["deterministic_view"]
    assert [item["task_id"] for item in view] == ["t-ai", "t-gate"]
    assert all("metrics" not in item and "cost" not in item for item in view)
    assert set(data) == {
        "run_id", "plan_id", "run_signature", "sut_id", "gate_verdict", "exit_code",
        "deterministic_view", "banner", "canary", "tickets_draft", "details",
    }


def test_write_round_trips_utf8_and_emoji(tmp_path):
    ctx = context()
    ctx.plan_name = "Kiểm thử tiếng Việt ✅"
    md, data = write(ctx, tmp_path)
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == md
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == data
    assert "ĐỌC TRƯỚC" not in md and "✅" in md


def test_gate_detail_prefers_metrics_the_oracle_asserts_on():
    from qc_agent.core.report import _gate_detail
    result = {"status": "pass", "findings": [], "metrics": {"checks.fails": 0, "checks.passes": 9, "http_req_duration.p95": 5.5, "http_req_failed.rate": 0}}
    spec = {"oracle": {"kind": "threshold", "assertions": [{"metric": "http_req_duration.p95"}, {"metric": "http_req_failed.rate"}]}}
    assert _gate_detail(result, spec) == "http_req_duration.p95=5.5, http_req_failed.rate=0"
    assert _gate_detail(result, {"oracle": {"kind": "checks"}}) == "checks.fails=0, checks.passes=9"  # không có assertion: như trước
