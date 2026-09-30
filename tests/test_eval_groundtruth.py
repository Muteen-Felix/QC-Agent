"""tools/eval_groundtruth.py (S1-08): phần tính metric với input tổng hợp, thoả ngưỡng và CLI; một lượt chạy thật với LLM giả trên toyapp thật."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("eval_groundtruth", ROOT / "tools" / "eval_groundtruth.py")
ev = importlib.util.module_from_spec(SPEC)
sys.modules["eval_groundtruth"] = ev
SPEC.loader.exec_module(ev)

GOLDEN_FILE = ROOT / "tests" / "fixtures" / "prd" / "noteboard-golden.yaml"
PRD = ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md"
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"

GOLDEN = {
    "acs": {"AC-1": {"testable": True, "kills": ["BUG-4"]}, "AC-2": {"testable": True, "kills": []}, "AC-3": {"testable": False, "kills": []},
            "AC-4": {"testable": True, "kills": []}},
    "mutants": {"BUG-4": {"acs": ["AC-1"]}, "BUG-5": {"acs": ["AC-2"]}, "BUG-1": {"acs": ["AC-4"]}},
}


def tc(tc_id, refs, status="draft"):
    return {"tc_id": tc_id, "ac_refs": refs, "status": status}


# ---------------- (a) AC coverage ----------------

def test_coverage_counts_only_testable_acs_and_ignores_ui_only_ones():
    result = ev.ac_coverage(GOLDEN, [tc("a", ["AC-1"]), tc("b", ["AC-3"]), tc("c", ["AC-1", "AC-2"])])
    assert result == {"testable": 3, "covered": 2, "missing": ["AC-4"], "value": pytest.approx(2 / 3)}


def test_coverage_of_a_ui_only_ac_does_not_help_and_unknown_refs_are_harmless():
    assert ev.ac_coverage(GOLDEN, [tc("a", ["AC-3"]), tc("b", ["AC-99"])])["covered"] == 0


def test_a_rejected_test_case_is_not_coverage():
    assert ev.ac_coverage(GOLDEN, [tc("a", ["AC-1"], "rejected")])["covered"] == 0
    assert ev.ac_coverage(GOLDEN, [tc("a", ["AC-1"], "approved")])["covered"] == 1


def test_coverage_edge_cases():
    assert ev.ac_coverage(GOLDEN, [])["value"] == 0.0
    assert ev.ac_coverage({"acs": {"AC-1": {"testable": False}}, "mutants": {}}, [tc("a", ["AC-1"])])["value"] == 0.0   # không có AC testable: không chia cho 0
    assert ev.ac_coverage(GOLDEN, [{"tc_id": "x"}])["covered"] == 0                                                   # TC thiếu ac_refs


def test_testable_acs_are_sorted_and_strictly_true():
    golden = {"acs": {"AC-2": {"testable": True}, "AC-1": {"testable": True}, "AC-3": {"testable": "yes"}, "AC-4": "x"}, "mutants": {}}
    assert ev.testable_acs(golden) == ["AC-1", "AC-2"]


# ---------------- (b) TC xanh ----------------

JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="5">
<testcase classname="test_us_1" name="test_story[TC-AC-1.1-aaa111]"/>
<testcase classname="test_us_1" name="test_story[TC-AC-1.2-bbb222]"><failure message="x">boom</failure></testcase>
<testcase classname="test_us_2" name="test_story[TC-AC-2.1-ccc333]"><error message="e">oops</error></testcase>
<testcase classname="test_us_2" name="test_story[TC-AC-2.2-ddd444]"><skipped message="s"/></testcase>
<testcase classname="test_us_2" name="test_story[TC-AC-2.3-eee555]"/>
</testsuite></testsuites>"""


def test_junit_is_parsed_per_test_case_id():
    assert ev.parse_junit(JUNIT) == {"TC-AC-1.1-aaa111": "passed", "TC-AC-1.2-bbb222": "failed", "TC-AC-2.1-ccc333": "error",
                                     "TC-AC-2.2-ddd444": "skipped", "TC-AC-2.3-eee555": "passed"}


def test_green_rate_counts_failed_error_and_skipped_as_not_green():
    result = ev.green_rate(ev.parse_junit(JUNIT))
    assert (result["total"], result["passed"], result["value"]) == (5, 2, pytest.approx(0.4))
    assert result["failing"] == ["TC-AC-1.2-bbb222", "TC-AC-2.1-ccc333", "TC-AC-2.2-ddd444"]


def test_green_rate_of_nothing_is_zero_not_a_division_error():
    assert ev.green_rate({}) == {"total": 0, "passed": 0, "failing": [], "value": 0.0}


def test_a_test_without_a_tc_id_keeps_its_own_name():
    assert ev.parse_junit('<testsuite><testcase name="test_other"/></testsuite>') == {"test_other": "passed"}


# ---------------- (c) mutant ----------------

def test_a_mutant_is_killed_only_when_pytest_fails_it():
    approved = [tc("TC-1", ["AC-1"]), tc("TC-2", ["AC-2"])]
    caught = ev.mutant_result("BUG-4", 1, {"TC-1": "failed", "TC-2": "passed"}, GOLDEN, approved)
    assert caught == {"killed": True, "exit_code": 1, "failing_tcs": ["TC-1"], "caught_by_expected_ac": True, "measurement_error": False}
    assert ev.mutant_result("BUG-4", 0, {"TC-1": "passed"}, GOLDEN, approved)["killed"] is False


def test_a_mutant_caught_by_an_unrelated_test_case_is_flagged_but_still_killed():
    approved = [tc("TC-1", ["AC-1"]), tc("TC-2", ["AC-2"])]
    result = ev.mutant_result("BUG-4", 1, {"TC-1": "passed", "TC-2": "failed"}, GOLDEN, approved)
    assert result["killed"] is True and result["caught_by_expected_ac"] is False


@pytest.mark.parametrize("code", [2, 3, 4, 5, 137])
def test_a_broken_measurement_is_never_counted_as_a_kill(code):
    result = ev.mutant_result("BUG-4", code, {}, GOLDEN, [])
    assert result["killed"] is False and result["measurement_error"] is True


def test_exit_1_without_any_failing_test_is_not_a_kill():
    assert ev.mutant_result("BUG-4", 1, {"TC-1": "passed"}, GOLDEN, [])["killed"] is False


def test_mutant_summary_counts_bug_4_to_13_and_reports_bug1_apart():
    results = {f"BUG-{n}": {"killed": n != 9} for n in range(4, 14)} | {"BUG-1": {"killed": True}}
    summary = ev.summarize_mutants(results)
    assert (summary["count"], summary["total"], summary["survived"], summary["bug1_killed"]) == (9, 10, ["BUG-9"], True)
    assert summary["value"] == pytest.approx(0.9)
    assert ev.summarize_mutants({})["count"] == 0 and ev.summarize_mutants({"BUG-1": {"killed": True}})["count"] == 0   # BUG-1 không cộng vào 10


# ---------------- ngưỡng ----------------

def mutants(count, bug1=True):
    return {"count": count, "total": 10, "bug1_killed": bug1, "killed": [], "survived": [], "value": count / 10}


@pytest.mark.parametrize("coverage, green, killed, bug1, baseline, passed", [
    (0.9, 0.9, 9, True, True, True),          # đúng ngưỡng: đạt
    (1.0, 1.0, 10, True, True, True),
    (0.899, 1.0, 10, True, True, False),      # coverage thiếu
    (1.0, 0.899, 10, True, True, False),      # TC xanh thiếu
    (1.0, 1.0, 8, True, True, False),         # chỉ bắt 8/10
    (1.0, 1.0, 10, False, True, False),       # không bắt được BUG-1
    (1.0, 1.0, 10, True, False, False),       # bộ đã duyệt không xanh trên SUT sạch: phép đo vô nghĩa
])
def test_thresholds(coverage, green, killed, bug1, baseline, passed):
    assert ev.verdict(coverage, green, mutants(killed, bug1), baseline)["passed"] is passed


def test_verdict_lists_which_check_failed():
    checks = ev.verdict(0.5, 1.0, mutants(10), True)["checks"]
    assert checks == {"ac_coverage": False, "green_rate": True, "mutants": True, "bug1": True, "baseline_green": True}


def test_median_of_runs():
    assert ev.median([0.5, 1.0, 0.9]) == 0.9 and ev.median([0.4, 0.6]) == 0.5 and ev.median([]) == 0.0


# ---------------- chi phí ----------------

def test_cost_estimate_uses_per_million_token_prices():
    est = ev.estimate_cost(10_000, 16_000, 3, 2.0, 10.0)
    assert est["usd_per_run"] == pytest.approx(0.18) and est["usd_total"] == pytest.approx(0.54) and est["runs"] == 3


def test_sonnet_5_price_is_the_documented_one():
    assert ev.PRICES_PER_MTOK["claude-sonnet-5"] == (2.0, 10.0)


def test_an_unknown_model_needs_explicit_prices():
    with pytest.raises(ev.EvalError, match="--price-in"):
        ev.estimate_for(PRD, None, 1, "gemini-x", None, None)
    assert ev.estimate_for(PRD, None, 2, "gemini-x", 0.3, 2.5)["usd_total"] > 0


# ---------------- bảng Markdown ----------------

def report(**over):
    base = {"prd_id": "p", "llm": "fake", "model": "m (fake)", "runs": [{}], "ac_coverage": {"median": 0.92, "testable": 23, "covered": 21, "missing": ["AC-1.4", "AC-2.1"]},
            "green_rate": {"median": 0.97}, "baseline_green": True, "mutants": {"count": 9, "total": 10, "bug1_killed": True, "survived": ["BUG-9"], "killed": []},
            "mutant_results": {"BUG-4": {"killed": True, "failing_tcs": ["TC-a", "TC-b", "TC-c", "TC-d"]}, "BUG-9": {"killed": False, "failing_tcs": [], "measurement_error": True}},
            "golden_mutants": {"BUG-4": {"acs": ["AC-1.3"]}}, "verdict": {"passed": True, "checks": {k: True for k in ("ac_coverage", "green_rate", "mutants", "bug1", "baseline_green")}}}
    return {**base, **over}


def test_the_markdown_table_shows_all_three_metrics_and_each_mutant():
    text = ev.render_markdown(report())
    for needle in ("(a) AC coverage", "92.0%", "(b) TC chạy xanh", "97.0%", "(c) Mutant bị bắt", "9/10", "BUG-1 bị bắt", "| BUG-4 | ✅ | AC-1.3 | TC-a, TC-b, TC-c … |",
                   "| BUG-9 | ❌ (đo lỗi) |", "Mutant sống sót: BUG-9", "AC testable chưa có TC", "AC-1.4", "**Kết luận: ĐẠT**"):
        assert needle in text, needle
    assert "KHÔNG ĐẠT" in ev.render_markdown(report(verdict={"passed": False, "checks": {k: False for k in report()["verdict"]["checks"]}}))


# ---------------- CLI ----------------

def base_args(tmp_path, *extra):
    return ["--prd", str(PRD), "--golden", str(GOLDEN_FILE), "--sut-root", str(SUT), "--out-json", str(tmp_path / "out.json"), *extra]


def test_real_mode_needs_yes_and_prints_the_estimate_first(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("QC_GT_MODEL", "claude-sonnet-5")
    assert ev.main(base_args(tmp_path, "--llm", "real", "--runs", "3")) == 3
    err = capsys.readouterr().err
    assert "ƯỚC TÍNH CHI PHÍ" in err and "3 lượt" in err and "--yes" in err and not (tmp_path / "out.json").exists()


@pytest.mark.parametrize("extra", [["--runs", "0"], ["--runs", "11"], ["--llm", "nope"]])
def test_bad_usage_is_exit_3(tmp_path, capsys, extra):
    assert ev.main(base_args(tmp_path, *extra)) == 3
    capsys.readouterr()


def test_a_broken_golden_or_missing_approved_suite_is_exit_3(tmp_path, capsys):
    bad = tmp_path / "golden.yaml"
    bad.write_text("acs: []\n", encoding="utf-8")
    assert ev.main(["--prd", str(PRD), "--golden", str(bad), "--sut-root", str(SUT)]) == 3
    empty = tmp_path / "sut"
    empty.mkdir()
    assert ev.main(["--prd", str(PRD), "--golden", str(GOLDEN_FILE), "--sut-root", str(empty)]) == 3
    capsys.readouterr()


def test_the_golden_file_is_consistent_with_the_prd_and_the_toyapp():
    from qc_agent.groundtruth.prd import parse_prd
    golden = ev.load_golden(GOLDEN_FILE)
    prd_acs = {ac.ac_id for story in parse_prd(PRD).stories for ac in story.acs}
    assert set(golden["acs"]) == prd_acs and golden["labeled_by"].startswith("dev")
    assert set(ev.MUTANTS) <= set(golden["mutants"]) and ev.BUG1 in golden["mutants"]
    for bug, spec in golden["mutants"].items():
        assert spec["acs"] and set(spec["acs"]) <= prd_acs and all(golden["acs"][ac]["testable"] for ac in spec["acs"]), bug
        assert all(bug in golden["acs"][ac]["kills"] for ac in spec["acs"]), bug                       # hai chiều khớp nhau
    for ac, spec in golden["acs"].items():
        assert set(spec["kills"]) <= set(golden["mutants"]), ac
    for bug in ev.MUTANTS:
        assert golden["mutants"][bug]["api_contract_catches"] is False


def test_end_to_end_with_the_fake_llm_measures_all_three_metrics_on_the_real_toyapp(tmp_path, capsys):
    """Chạy thật: sinh bằng LLM giả, xanh trên toyapp sạch, và bộ đã duyệt của noteboard bắt các mutant (≈ 1–2 phút)."""
    code = ev.main(base_args(tmp_path, "--llm", "fake"))
    out, err = capsys.readouterr()
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert code == 0, out + err
    assert data["baseline_green"] is True and data["mutants"]["count"] >= 9 and data["mutants"]["bug1_killed"] is True
    assert data["ac_coverage"]["median"] >= 0.9 and data["green_rate"]["median"] >= 0.9 and data["verdict"]["passed"] is True
    assert data["golden_labeled_by"] == "dev — CẦN QA DUYỆT" and "CẦN QA DUYỆT" in err
    assert "(a) AC coverage" in out and "(b) TC chạy xanh" in out and "(c) Mutant bị bắt" in out
    assert all(data["mutant_results"][bug]["killed"] for bug in (*ev.MUTANTS, ev.BUG1))
    assert all(data["mutant_results"][bug]["caught_by_expected_ac"] for bug in ev.MUTANTS)
