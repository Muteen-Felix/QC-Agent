import copy
import json

from qc_agent.core.report import RunContext, render, write
from qc_agent.core.findings import normalize
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
    findings, blockers = normalize(results, specs, policy={"on_skipped_gate_task": "yellow"})
    gate = gate_verdict(findings, blockers)
    gate.banner = [(tid, f"{r['status']}: {r['verdict'].get('rationale') or ''}") for tid, r in results.items()
                   if r["status"] in ("skipped", "error")]
    return RunContext(
        run_id="r-0001", plan_id="plan-1a2b3c4d", plan_name="demo", plan_path="plans/demo.yaml",
        plan_text=PLAN, sut_id="sut-9f2c0a11", run_signature="sig-1", generated_at="2026-09-19 14:32",
        wallclock_s=192, specs=specs, results=results, gate=gate, findings=findings,
    )


def test_has_all_five_sections_in_order():
    md, _ = render(context())
    assert md.startswith("# QC Gate Report — run r-0001\n")
    assert "plan: plans/demo.yaml (plan-1a2b3c4d) · SUT: sut-9f2c0a11 · 2026-09-19 14:32" in md
    assert "wallclock 3m12s · LLM: 0 in (0 từ cache) / 0 out · worker: 120 token (tasks: t-ui) · ~$0.01" in md   # S4-03: một dòng, gộp phần Select và worker
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
    assert verdict(before) == verdict(after) == "## VERDICT: ✅⚠ PASSED_WITH_WARNINGS"


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
        "deterministic_view", "banner", "canary", "tickets_draft", "details", "findings", "severity_counts", "llm_usage",
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


def test_report_json_carries_real_lane_and_finding_ids_per_task_for_ingest():
    """CI ingest/executor cần lane THẬT (không suy ra từ deterministic_view) và finding_id (dựng sổ nợ); chỉ id + tiêu đề, không evidence."""
    _, data = render(context())
    results = data["details"]["results"]
    assert {tid: r["lane"] for tid, r in results.items()} == {"t-gate": "gate", "t-ai": "gate", "t-ui": "discovery"}
    assert results["t-ai"]["findings"] == [{"finding_id": "f-ai", "title": "Giữ ý chính"}]
    assert results["t-gate"]["findings"] == [] and set(results["t-ai"]) == {"status", "lane", "cost", "metrics", "findings"}


def test_severity_sections_are_critical_medium_low_in_order():
    md, _ = render(context())
    positions = [md.index(f"## {name} (") for name in ("Critical", "Medium", "Low")]
    assert positions == sorted(positions)
    assert md.index("## Low (") < md.index("## 1. DETERMINISTIC ASSERT")   # mục severity đứng trước các mục chi tiết


# ---------------- S4-03: dòng chi phí gộp Select + worker, llm_usage.json ----------------

MARK = "PRIVATE_RATIONALE_MARKER_3b9"


def selection(llm, *, source="llm"):
    return {"version": 1, "trigger_type": "pr", "source": source, "full_set": False, "diff_sha256": "d" * 8, "floor": ["semgrep"], "workers": ["pytest", "semgrep"],
            "suites": ["gt-functional", "sast"], "rationale": {"pytest": MARK}, "fallback_reason": None, "llm": llm}


def with_selection(llm, *, worker_usd=0.01):
    ctx = context()
    ctx.results["t-ui"]["cost"] = {"wallclock_s": 2.0, "tokens": 120, "usd": worker_usd}
    ctx.selection, ctx.selected_suite_count, ctx.policy_suite_count = selection(llm), 2, 5
    return ctx


def usage_llm(**over):
    base = {"model": "claude-haiku-4-5-20251001", "prompt_version": "diff-select/1", "input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 4096, "est_usd": 0.01}
    base.update(over)
    return base


def test_the_cost_line_adds_the_selector_usage_and_shows_what_came_from_cache():
    """Lần gọi 2 có cache_read_input_tokens > 0 => report hiện 'từ cache'; MỘT dòng, MỘT con số tiền (worker 0,01 + Select 0,01)."""
    md, data = render(with_selection(usage_llm()))
    line = "wallclock 3m12s · LLM: 4 196 in (4 096 từ cache) / 50 out · worker: 120 token (tasks: t-ui) · ~$0.02"
    assert line in md and data["llm_usage"]["line"] == line and md.count("wallclock ") == 1
    assert data["llm_usage"]["summary"]["prompt_tokens"] == 4196 and data["llm_usage"]["summary"]["cache_read_tokens"] == 4096
    (row,) = data["llm_usage"]["calls"]
    assert row["purpose"] == "diff-select" and row["cache_hit"] is False and row["est_usd"] == 0.01 and row["duration_s"] is None and "usage_known" not in row


def test_a_cache_hit_is_listed_but_never_added_to_the_total_or_the_money():
    md, data = render(with_selection(usage_llm(cache_hit=True)))
    assert "LLM: 0 in (0 từ cache) / 0 out" in md and "1 lần dùng cache (không tính phí)" in md and "~$0.01" in md   # chỉ còn tiền worker
    (row,) = data["llm_usage"]["calls"]
    assert row["cache_hit"] is True and row["input_tokens"] == 100 and row["duration_s"] is None     # giữ số của lần tạo để minh bạch


def test_an_unpriced_model_is_named_and_not_guessed():
    md, data = render(with_selection(usage_llm(model="gemini-3.6-flash", est_usd=None)))
    assert "~$0.01 (chưa gồm giá của gemini-3.6-flash)" in md and data["llm_usage"]["summary"]["unpriced_models"] == ["gemini-3.6-flash"]
    assert data["llm_usage"]["calls"][0]["est_usd"] is None


def test_a_timeout_is_an_unknown_cost_so_the_money_is_a_lower_bound_with_a_suffix():
    unknown = {"model": "claude-haiku-4-5-20251001", "prompt_version": "diff-select/1", "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
               "cache_read_input_tokens": 0, "usage_known": False, "status": "timeout"}
    md, data = render(with_selection(unknown))
    assert "~$0.01 (+1 lời gọi timeout/lỗi mạng chưa rõ chi phí)" in md
    (row,) = data["llm_usage"]["calls"]
    assert row["usage_known"] is False and row["unknown_calls"] == 1 and row["status"] == "timeout"
    assert all(row[k] is None for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "est_usd"))   # null, KHÔNG phải 0


def test_when_nothing_is_known_the_money_is_not_zero_dollars():
    unknown = {"model": "m", "prompt_version": "p", "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
               "usage_known": False, "status": "unavailable"}
    ctx = with_selection(unknown)
    ctx.results["t-ui"]["cost"] = {"wallclock_s": 2.0, "tokens": 0, "usd": None}
    md, data = render(ctx)
    assert "~$? (chưa rõ)" in md and "$0.00" not in md and "$0.0" not in data["llm_usage"]["line"]


def test_a_rejected_response_that_cost_tokens_counts_as_a_normal_row():
    """Selector fallback sau output sai schema: `source: fallback` nhưng `llm` mang token đã tốn; không bị coi là hit cache và không bị bỏ sót."""
    rejected = usage_llm(input_tokens=200, output_tokens=40, cache_read_input_tokens=0, est_usd=0.002, status="bad_output")
    ctx = with_selection(rejected)
    ctx.selection["source"], ctx.selection["fallback_reason"], ctx.selection["full_set"] = "fallback", "bad_output", True
    md, data = render(ctx)
    assert "LLM: 200 in (0 từ cache) / 40 out" in md and "~$0.01" in md
    (row,) = data["llm_usage"]["calls"]
    assert row["status"] == "bad_output" and row["cache_hit"] is False and row["input_tokens"] == 200


def test_llm_usage_json_is_a_json_array_without_content_and_absent_when_there_is_no_llm_call(tmp_path):
    ctx = with_selection(usage_llm())
    write(ctx, tmp_path / "run")
    rows = json.loads((tmp_path / "run" / "llm_usage.json").read_text(encoding="utf-8"))
    assert isinstance(rows, list) and len(rows) == 1 and set(rows[0]) == {"purpose", "model", "prompt_version", "input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                                          "cache_read_input_tokens", "est_usd", "cache_hit", "duration_s"}
    assert MARK not in (tmp_path / "run" / "llm_usage.json").read_text(encoding="utf-8")                 # không có nội dung/rationale
    write(context(), tmp_path / "plain")
    assert not (tmp_path / "plain" / "llm_usage.json").exists()
    assert json.loads((tmp_path / "plain" / "report.json").read_text(encoding="utf-8"))["llm_usage"]["calls"] == []


def test_the_llm_usage_block_is_in_report_json_with_the_same_line_as_the_markdown():
    md, data = render(with_selection(usage_llm()))
    assert data["llm_usage"]["line"] in md and set(data["llm_usage"]) == {"calls", "summary", "line"}


def test_a_known_call_with_earlier_unknown_attempts_keeps_its_tokens_and_flags_the_lower_bound():
    md, data = render(with_selection(usage_llm(unknown_calls=2)))
    (row,) = data["llm_usage"]["calls"]
    assert row["unknown_calls"] == 2 and row["input_tokens"] == 100 and row["est_usd"] == 0.01 and "usage_known" not in row
    assert data["llm_usage"]["summary"]["unknown_calls"] == 2
    assert "~$0.02 (+2 lời gọi timeout/lỗi mạng chưa rõ chi phí)" in md
