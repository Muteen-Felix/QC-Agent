"""tools/eval_gt_sut.py `--generator single|agent|both`: so bộ sinh một lời gọi với agent trên cùng PRD/SUT.

Phần thuần (aggregate_runs, compare, verdict, render) test bằng số liệu dựng tay; phần cuối chạy THẬT qua SUT sống với LLM giả. Số liệu của chế độ fake KHÔNG nói gì về chất lượng
thật của agent (hội thoại cố định); các test này chỉ chứng minh ĐƯỜNG ỐNG đo đúng và các tiêu chí so sánh hoạt động.
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from qc_agent.groundtruth.prd import parse_prd
from tests import gt_agent_fixture as fx
from tests.test_eval_gt_sut import OPENAPI, PRD, ROOT, SUT, THR, config

SPEC = importlib.util.spec_from_file_location("eval_gt_sut_agent", ROOT / "tools" / "eval_gt_sut.py")
ev = importlib.util.module_from_spec(SPEC)
sys.modules["eval_gt_sut_agent"] = ev
SPEC.loader.exec_module(ev)


def run(coverage=1.0, green=1.0, *, scorer_complete=True, kill=None, cost=1.0, completed=True, wall=60.0, agent=False, test_cases=10, turns=12):
    out = {"ac_coverage": {"value": coverage, "testable": 10, "covered": round(coverage * 10), "missing": []}, "green_rate": {"value": green}, "test_cases": test_cases}
    if scorer_complete is not None:
        out["scorer"] = {"dims": {"ac": {"ratio": 1.0 if scorer_complete else 0.8}, "technique": {"ratio": 1.0 if scorer_complete else 0.5}, "api": {"ratio": 1.0 if scorer_complete else 0.75}},
                         "complete": scorer_complete}
    if kill is not None:
        out["mutants_generated"] = {"measured": True, "value": kill, "survived": ["M-x"]}
    if agent:
        out["agent"] = {"turns": turns, "completed": completed, "cost_usd_est": cost, "duration_s": wall}
    return out


# ---------------- phần thuần ----------------

def test_scripted_transport_plays_in_order_and_then_fails_loudly():
    transport = ev.scripted_transport([{"n": 1}, {"n": 2}])
    with httpx.Client(transport=transport) as client:
        assert [client.post("http://x/v1/messages").json() for _ in range(2)] == [{"n": 1}, {"n": 2}]
        late = client.post("http://x/v1/messages")
    assert late.status_code == 599 and late.json()["error"]["message"] == "script đã hết"


def test_aggregate_keeps_the_old_keys_and_adds_the_new_ones_only_when_measured():
    plain = ev.aggregate_runs([run(0.9, 0.8, scorer_complete=None), run(1.0, 1.0, scorer_complete=None), run(0.8, 0.9, scorer_complete=None)])
    assert set(plain) == {"runs", "ac_coverage", "green_rate"} and plain["ac_coverage"]["median"] == 0.9 and plain["green_rate"]["median"] == 0.9
    assert plain["ac_coverage"]["covered"] == 8 and plain["ac_coverage"]["testable"] == 10                    # chi tiết lấy từ lượt TỆ nhất
    full = ev.aggregate_runs([run(kill=0.5, agent=True, cost=2.0, wall=100, turns=10), run(kill=0.7, agent=True, cost=4.0, wall=300, turns=20, completed=False),
                              run(kill=0.9, agent=True, cost=None, wall=200, turns=30, scorer_complete=False)])
    assert full["scorer"] == {"median": {"ac": 1.0, "technique": 1.0, "api": 1.0}, "complete_runs": 2, "runs": 3}
    assert full["mutants_generated"]["median_kill_rate"] == 0.7 and full["mutants_generated"]["runs"] == 3 and full["mutants_generated"]["survived_in_worst"] == ["M-x"]
    assert full["agent"] == {"runs": 3, "completed_runs": 2, "turns_median": 20, "cost_usd_median": 3.0, "cost_usd_max": 4.0, "wall_s_max": 300}     # lượt không biết giá: bỏ qua, không coi là 0
    unknown = ev.aggregate_runs([run(agent=True, cost=None)])
    assert unknown["agent"]["cost_usd_max"] is None and unknown["agent"]["cost_usd_median"] is None


def test_aggregate_skips_unmeasured_mutant_runs():
    runs = [run(), run(kill=0.5)]
    runs[0]["mutants_generated"] = {"measured": False, "reason": "không có TC nào xanh"}
    assert ev.aggregate_runs(runs)["mutants_generated"] == {"median_kill_rate": 0.5, "runs": 1, "survived_in_worst": ["M-x"]}


def single(kill=0.4, green=0.95):
    return ev.aggregate_runs([run(green=green, scorer_complete=False, kill=kill)])


def agent_agg(**kw):
    kw = {"scorer_complete": True, "kill": 0.7, "agent": True, **kw}
    return ev.aggregate_runs([run(**kw)])


def test_compare_passes_when_the_agent_is_clearly_better():
    result = ev.compare(single(), agent_agg(), THR)
    assert result == {"checks": {"scorer_100": True, "kill_rate": True, "green_rate": True, "completed": True, "cost": True, "wall_time": True}, "passed": True}   # +0,30 >= +0,20


@pytest.mark.parametrize("kw, failing", [
    (dict(scorer_complete=False), "scorer_100"),                 # chưa đủ 100% ở chiều nào đó
    (dict(kill=0.5), "kill_rate"),                               # +0,10 < +0,20 và < 0,9
    (dict(green=0.84), "green_rate"),                            # dưới sàn 0,85
    (dict(completed=False), "completed"),
    (dict(cost=10.01), "cost"),
    (dict(wall=1800.5), "wall_time"),
])
def test_compare_each_criterion_can_fail_on_its_own(kw, failing):
    result = ev.compare(single(), agent_agg(**kw), THR)
    assert result["checks"][failing] is False and result["passed"] is False
    assert [name for name, ok in result["checks"].items() if not ok] == [failing]


def test_compare_kill_rate_is_met_by_the_absolute_threshold_or_by_the_gain():
    assert ev.compare(single(kill=0.95), agent_agg(kill=0.9), THR)["checks"]["kill_rate"] is True        # ≥ 0,9 dù không hơn single
    assert ev.compare(single(kill=0.2), agent_agg(kill=0.4), THR)["checks"]["kill_rate"] is True          # +0,20 đúng ngưỡng
    assert ev.compare(single(kill=0.2), agent_agg(kill=0.39), THR)["checks"]["kill_rate"] is False


def test_compare_green_floor_is_the_larger_of_single_and_the_minimum():
    assert ev.compare(single(green=0.99), agent_agg(green=0.95), THR)["checks"]["green_rate"] is False   # agent không được kém single
    assert ev.compare(single(green=0.5), agent_agg(green=0.85), THR)["checks"]["green_rate"] is True
    assert ev.compare(single(green=0.5), agent_agg(green=0.84), THR)["checks"]["green_rate"] is False    # nhưng cũng phải ≥ 0,85


def test_compare_thresholds_can_be_overridden_and_unmeasured_parts_are_absent():
    loose = {**THR, "kill_gain": 0.05, "max_cost_usd": 20, "green_min": 0.5}
    assert ev.compare(single(green=0.5), agent_agg(kill=0.5, cost=15, green=0.6), loose)["passed"] is True
    assert ev.compare(single(), agent_agg(kill=0.5, cost=15, green=0.6), loose)["checks"]["green_rate"] is False        # vẫn không được kém single (0,95)
    bare = ev.compare(ev.aggregate_runs([run(scorer_complete=None)]), ev.aggregate_runs([run(scorer_complete=None, agent=True, cost=None)]), THR)
    assert set(bare["checks"]) == {"green_rate", "completed", "wall_time"}                                # không scorer, không mutant, không giá: không bịa check
    assert ev.compare({"green_rate": {"median": 1}}, {"green_rate": {"median": 1}}, THR)["checks"] == {"green_rate": True}


def test_verdict_accepts_the_new_inputs_without_changing_the_old_behaviour():
    old = dict(coverage=1, green=1, mutants=None, baseline_green=None)
    assert set(ev.verdict(THR, **old)["checks"]) == {"ac_coverage", "green_rate"}
    got = ev.verdict(THR, **old, scorer_complete=True, generated_mutants={"median_kill_rate": 0.92})
    assert got["checks"] == {"coverage_scorer_100": True, "generated_mutant_kill_rate": True, "ac_coverage": True, "green_rate": True} and got["passed"] is True
    assert ev.verdict(THR, **old, scorer_complete=False)["passed"] is False
    assert ev.verdict(THR, **old, generated_mutants={"median_kill_rate": 0.89})["passed"] is False
    compared = ev.verdict(THR, **old, comparison={"checks": {"kill_rate": True, "cost": False}})
    assert compared["checks"]["vs_single:cost"] is False and compared["passed"] is False


def test_agent_thresholds_in_the_config_are_validated():
    base = yaml.safe_load(config(Path(os.environ.get("TEMP", "."))).read_text(encoding="utf-8")) if False else None
    cfg = {"sut_root": "x", "prd": "y", "sut": {"base_url": "http://x"}}
    assert ev.validate_config({**cfg, "agent_thresholds": {"kill_gain": 0.1, "max_cost_usd": 5, "mutant_kill_rate": 0.8}}) == []
    for bad in ({"nope": 1}, {"kill_gain": -1}, {"kill_gain": "x"}, {"kill_gain": True}, [1], "x"):
        assert any("agent_thresholds" in p for p in ev.validate_config({**cfg, "agent_thresholds": bad})), bad


def test_the_comparison_table_appears_only_when_the_agent_ran():
    only_single = {"generators": {"single": single()}, "comparison": None}
    assert ev.render_generators(only_single) == []
    both = {"generators": {"single": single(), "agent": agent_agg()}, "comparison": ev.compare(single(), agent_agg(), THR)}
    text = "\n".join(ev.render_generators(both))
    for needle in ("| Metric | single | agent |", "Coverage scorer technique (median) | 50% | 100%", "Coverage scorer 100% (số lượt) | 0/1 | 1/1", "Mutant bị bắt trên bộ vừa sinh (median) | 40% | 70%",
                   "Agent: 1/1 lượt hoàn tất · 12 lượt gọi (median) · chi phí ước tính ≤ $1.00 · 60s", "| kill_rate | ✅ |", "| cost | ✅ |"):
        assert needle in text, needle


# ---------------- fixture và cấu hình ----------------

def test_the_fake_agent_script_matches_its_builder():
    prd = parse_prd(PRD, openapi_source=str(OPENAPI))
    wanted = fx.dump(fx.build(prd))
    if os.environ.get("QC_UPDATE_GOLDEN") == "1":
        fx.AGENT_SCRIPT.write_text(wanted, encoding="utf-8", newline="\n")
    assert fx.AGENT_SCRIPT.read_text(encoding="utf-8") == wanted
    script = json.loads(wanted)
    assert [b["name"] for r in script for b in r["content"] if b["type"] == "tool_use"][-1] == "finish_generation" and all(r["stop_reason"] == "tool_use" for r in script)
    ids = [b["id"] for r in script for b in r["content"] if b["type"] == "tool_use"]
    assert len(ids) == len(set(ids))                                                                      # id cố định và duy nhất


def fresh_sut(tmp_path) -> Path:
    fresh = tmp_path / "fresh-sut"
    shutil.copytree(SUT, fresh, ignore=shutil.ignore_patterns(".qc-agent", "__pycache__", ".hypothesis"))
    return fresh


def evaluate(tmp_path, capsys, *extra, **over):
    out = tmp_path / "out.json"
    code = ev.main(["--config", str(config(tmp_path, sut_root=str(fresh_sut(tmp_path)), **over)), "--llm", "fake", "--out-json", str(out), *extra])
    text, err = capsys.readouterr()
    return code, text, err, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


# ---------------- chạy thật (SUT sống, LLM giả) ----------------

def test_agent_only_run_measures_the_scorer_and_the_agent_stats_without_touching_the_sut(tmp_path, capsys):
    code, text, err, data = evaluate(tmp_path, capsys, "--generator", "agent", "--skip-mutants")
    assert code == 0, text + err
    agg = data["generators"]["agent"]
    assert data["generator"] == "agent" and data["generation"] == agg and "single" not in data["generators"] and "comparison" not in data
    assert agg["ac_coverage"]["median"] >= 0.9 and agg["green_rate"]["median"] >= 0.9
    assert agg["scorer"] == {"median": {"ac": 1.0, "technique": 1.0, "api": 1.0}, "complete_runs": 1, "runs": 1}       # AC + technique + API đều 100% (ô 422 được waive)
    run_ = agg["runs"][0]
    assert run_["agent"]["completed"] is True and run_["agent"]["finish_rejections"] == 1 and run_["agent"]["waivers"] == 3 and run_["agent"]["files_read"] == 1
    assert run_["agent"]["cost_usd_est"] > 0 and run_["agent"]["duration_s"] >= 0 and run_["agent"]["repo_map"]["files"] >= 1
    assert data["verdict"]["checks"] == {"coverage_scorer_100": True, "ac_coverage": True, "green_rate": True}
    assert "single" not in text.split("**Kết luận")[0].split("### So các bộ sinh")[-1].splitlines()[0] if "### So các bộ sinh" in text else True


def test_both_generators_are_compared_on_the_same_prd_and_sut_including_mutants_on_the_generated_sets(tmp_path, capsys):
    """Một lượt chạy THẬT cho `both`: single vs agent trên cùng PRD/SUT, kèm mutant chạy trên bộ vừa sinh (chỉ hai mutant bắt được để giữ thời gian chạy hợp lý;
    mutant sống sót và các ngưỡng đã có test thuần ở trên)."""
    mutants = [{"id": "M-env", "acs": ["AC-1.3"], "required": True, "env": {"QC_BUGS": "4"}},
               {"id": "M-edit", "acs": ["AC-1.1"], "edits": [{"file": "toyapp/app.py", "find": 'if "6" in BUGS:', "replace": "if True:"}]}]
    code, text, err, data = evaluate(tmp_path, capsys, "--generator", "both", mutants=mutants)
    assert code == 0, text + err
    assert set(data["generators"]) == {"single", "agent"} and data["generation"] == data["generators"]["agent"] and data["generator"] == "both"
    assert data["generators"]["single"]["scorer"]["complete_runs"] == 0                                  # single-shot của fixture còn gap API (422) và AC mồ côi
    assert data["generators"]["agent"]["scorer"]["complete_runs"] == 1
    for kind in ("single", "agent"):
        gm = data["generators"][kind]["mutants_generated"]
        measured = data["generators"][kind]["runs"][0]["mutants_generated"]
        assert gm["median_kill_rate"] == 1.0 and gm["survived_in_worst"] == [] and gm["runs"] == 1
        assert measured["results"]["M-env"]["killed"] is True and measured["results"]["M-edit"]["killed"] is True and measured["excluded_red"] >= 0
    assert data["comparison"]["checks"] == {"scorer_100": True, "kill_rate": True, "green_rate": True, "completed": True, "cost": True, "wall_time": True}
    assert data["verdict"]["checks"]["vs_single:kill_rate"] is True and " vs " in data["model"] and data["model"].endswith("(fake)")
    for needle in ("### So các bộ sinh", "| Metric | single | agent |", "Coverage scorer 100% (số lượt) | 0/1 | 1/1", "Mutant bị bắt trên bộ vừa sinh (median) | 100% | 100%",
                   "| scorer_100 | ✅ |", "| kill_rate | ✅ |", "**Kết luận: ĐẠT**"):
        assert needle in text, needle


def test_the_single_generator_is_unchanged_and_does_not_measure_the_new_things_by_default(tmp_path, capsys):
    code, text, err, data = evaluate(tmp_path, capsys, "--skip-mutants")
    assert code == 0, text + err
    assert data["generator"] == "single" and set(data["generators"]) == {"single"} and "mutants_generated" not in data["generators"]["single"]
    assert set(data["verdict"]["checks"]) == {"ac_coverage", "green_rate"} and "### So các bộ sinh" not in text                # scorer chỉ để báo cáo, không đổi verdict của single
    assert "scorer" in data["generators"]["single"]


# ---------------- cách dùng sai và chế độ real ----------------

def test_the_agent_needs_a_claude_model_and_a_working_fake_script(tmp_path, capsys, monkeypatch):
    cfg = str(config(tmp_path))
    assert ev.main(["--config", cfg, "--llm", "fake", "--generator", "agent", "--agent-model", "gemini-3.6-flash"]) == 3
    assert "model Claude" in capsys.readouterr().err
    assert ev.main(["--config", cfg, "--llm", "fake", "--generator", "agent", "--skip-mutants", "--fake-script", str(tmp_path / "missing.json")]) == 3
    assert "--fake-script" in capsys.readouterr().err


def test_a_truncated_fake_script_fails_loudly_or_yields_a_partial_set_marked_incomplete(tmp_path, capsys):
    script = json.loads(fx.AGENT_SCRIPT.read_text(encoding="utf-8"))
    nothing = tmp_path / "nothing.json"
    nothing.write_text(json.dumps(script[:1]), encoding="utf-8")                  # mới đọc mã, chưa nộp TC nào, rồi hết script
    code, text, err, data = evaluate(tmp_path, capsys, "--generator", "agent", "--skip-mutants", "--fake-script", str(nothing))
    assert code == 3 and data is None and "LỖI HỆ THỐNG: GTError" in err           # không có TC nào thì là lỗi, không phải số liệu đẹp
    (tmp_path / "partial").mkdir()
    partial = tmp_path / "partial" / "partial.json"
    partial.write_text(json.dumps(script[:6]), encoding="utf-8")                  # đã nộp đủ các story nhưng chưa kết thúc: hết script = lỗi API giữa chừng
    code, text, err, data = evaluate(tmp_path / "partial", capsys, "--generator", "agent", "--skip-mutants", "--fake-script", str(partial))
    agent = data["generators"]["agent"]
    assert code == 1 and agent["agent"]["completed_runs"] == 0 and agent["runs"][0]["agent"]["error_kind"] == "unavailable"
    assert data["verdict"]["checks"]["coverage_scorer_100"] is False and data["verdict"]["passed"] is False


def test_real_mode_states_the_hard_cost_cap_and_that_source_code_is_sent_and_needs_yes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("QC_GT_AGENT_MAX_COST_USD", "1.0")
    out = tmp_path / "out.json"
    common = ["--config", str(config(tmp_path)), "--generator", "agent", "--out-json", str(out), "--max-total-usd", "3"]
    assert ev.main([*common, "--runs", "4"]) == 3
    assert "vượt --max-total-usd $3.00" in capsys.readouterr().err                               # 1,0 × 4 = 4,0 > 3,0
    assert ev.main([*common, "--runs", "2"]) == 3
    err = capsys.readouterr().err
    assert "ƯỚC TÍNH AGENT (claude-sonnet-5-5)" in err and "$1.00/lượt" in err and "2 lượt ≤ $2.00" in err and "MÃ NGUỒN" in err and "--yes" in err and not out.exists()
    monkeypatch.setenv("QC_GT_MODEL", "claude-sonnet-5")
    assert ev.main(["--config", str(config(tmp_path)), "--generator", "both", "--runs", "2", "--max-total-usd", "2"]) == 3
    err = capsys.readouterr().err
    assert "ƯỚC TÍNH (claude-sonnet-5)" in err and "ƯỚC TÍNH AGENT" in err and "2 lượt ≤ $2.00" in err


# ---------------- chốt chặn chi phí và kiểm khô ----------------

def test_real_agent_runs_refuse_to_start_without_an_explicit_cap_and_total_budget(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("QC_GT_AGENT_MAX_COST_USD", raising=False)
    cfg = str(config(tmp_path))
    assert ev.main(["--config", cfg, "--generator", "agent", "--yes", "--max-total-usd", "3"]) == 3
    assert "đặt tường minh QC_GT_AGENT_MAX_COST_USD" in capsys.readouterr().err                   # không có trần tường minh thì không gọi API
    monkeypatch.setenv("QC_GT_AGENT_MAX_COST_USD", "3.0")
    assert ev.main(["--config", cfg, "--generator", "agent", "--yes"]) == 3
    assert "cần --max-total-usd" in capsys.readouterr().err
    assert ev.main(["--config", cfg, "--generator", "agent", "--yes", "--max-total-usd", "2.9"]) == 3
    assert "vượt --max-total-usd $2.90" in capsys.readouterr().err
    assert ev.main(["--config", cfg, "--generator", "agent", "--yes", "--max-total-usd", "3", "--runs", "2"]) == 3
    assert "$6.00 vượt" in capsys.readouterr().err


def test_check_only_validates_everything_without_calling_any_llm(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")                                 # nếu lỡ gọi LLM sẽ thấy ngay
    code = ev.main(["--config", str(config(tmp_path, sut_root=str(fresh_sut(tmp_path)))), "--llm", "real", "--generator", "agent", "--check-only"])
    out, err = capsys.readouterr()
    assert code == 0, out + err
    for needle in ("OK  PRD `noteboard`: 4 story, 25 AC (23 testable", "OK  OpenAPI: 5 operation, 10 yêu cầu technique", "OK  repo map:", "OK  SUT sạch khởi động được: http://127.0.0.1:", "OK  mutant: 3",
                   "KIỂM KHÔ ĐẠT: chưa gọi LLM nào"):
        assert needle in out, needle


# ---------------- không mất kết quả đã trả tiền ----------------

def test_the_generated_set_is_saved_before_measuring_and_remeasured_for_free(tmp_path, capsys, monkeypatch):
    keep = tmp_path / "keep"
    code, text, err, first = evaluate(tmp_path, capsys, "--generator", "agent", "--skip-mutants", "--keep-dir", str(keep))
    assert code == 0, text + err
    assert (keep / "agent-run1" / "catalog.json").is_file() and (keep / "agent-run1" / "meta.json").is_file() and (keep / "agent-egress").is_dir()
    assert "Đã lưu bộ vừa sinh (agent, lượt 1)" in err and "--from-saved" in err
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")                                 # nếu lỡ gọi LLM sẽ thấy ngay
    out = tmp_path / "again.json"
    code = ev.main(["--config", str(config(tmp_path, sut_root=str(tmp_path / "fresh-sut"))), "--generator", "agent", "--skip-mutants", "--from-saved", str(keep), "--out-json", str(out)])
    again = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0, capsys.readouterr()
    assert again["llm"] == "saved" and again["generation"]["ac_coverage"] == first["generation"]["ac_coverage"]
    assert again["generation"]["runs"][0]["agent"]["cost_usd_est"] == first["generation"]["runs"][0]["agent"]["cost_usd_est"]
    assert ev.main(["--config", str(config(tmp_path)), "--from-saved", str(tmp_path / "nowhere")]) == 3 and "không đọc được lượt đã lưu" in capsys.readouterr().err


def test_a_measurement_failure_after_generation_keeps_the_paid_result_and_says_how_to_remeasure(tmp_path, capsys):
    broken = {"sut": {"start": {"cmd": f'"{sys.executable}" -c "raise SystemExit(1)" --port {{port}}', "health_path": "/", "timeout_s": 5}}}
    keep = tmp_path / "keep"
    code, text, err, data = evaluate(tmp_path, capsys, "--generator", "agent", "--keep-dir", str(keep), **broken)
    assert code == 3 and data is None
    assert (keep / "agent-run1" / "catalog.json").is_file()
    assert "đo agent lượt 1 thất bại" in err and f"--from-saved {keep}" in err


def test_a_mutant_that_cannot_be_measured_is_recorded_and_does_not_lose_the_run(tmp_path, capsys):
    mutants = [{"id": "M-env", "acs": ["AC-1.3"], "env": {"QC_BUGS": "4"}},
               {"id": "M-stale", "acs": ["AC-1.1"], "edits": [{"file": "toyapp/app.py", "find": "ĐOẠN KHÔNG TỒN TẠI", "replace": "x"}]}]
    code, text, err, data = evaluate(tmp_path, capsys, "--generator", "agent", "--generated-mutants", mutants=mutants)
    assert data is not None, text + err
    gm = data["generators"]["agent"]["mutants_generated"]
    assert gm["measurement_errors"] == ["M-stale"] and gm["survived_in_worst"] == ["M-stale"]          # tính là không bị bắt, nhưng được gọi đúng tên
    results = data["generators"]["agent"]["runs"][0]["mutants_generated"]["results"]
    assert results["M-env"]["killed"] is True and results["M-stale"]["killed"] is False
    assert results["M-stale"]["measurement_error"] is True and "khớp 0 lần" in results["M-stale"]["error"]
    assert "Mutant LỖI KHI ĐO ở bộ sinh agent" in text and "M-stale" in text


def test_check_only_fails_on_a_bad_sut_command_before_any_spending(tmp_path, capsys):
    broken = {"sut": {"start": {"cmd": f'"{sys.executable}" -c "raise SystemExit(1)" --port {{port}}', "health_path": "/", "timeout_s": 5}}}
    code = ev.main(["--config", str(config(tmp_path, sut_root=str(fresh_sut(tmp_path)), **broken)), "--check-only"])
    assert code == 3 and "KIỂM KHÔ ĐẠT" not in capsys.readouterr().out
