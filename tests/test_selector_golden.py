from pathlib import Path
import subprocess

import yaml

from qc_agent.llm.client import ToolCall, Usage
from qc_agent.selector import agent, rules
from qc_agent.selector.pruner import PrunedDiff, PrunedFile
import pytest

from qc_agent.llm.client import LLMError
from tools import eval_selector
from tools.eval_selector import EvalAbort, _covered_workers, _fake_gate_verdict, _ratio, collect, evaluate, main, score

ROOT = Path(__file__).resolve().parent / "fixtures/diffs"
SUT = Path(__file__).resolve().parent / "fixtures/sut/noteboard"


def test_golden_patch_inventory_and_rule_categories():
    labels = yaml.safe_load((ROOT / "labels.yaml").read_text(encoding="utf-8"))
    normal = sorted(ROOT.glob("diff-*.patch"))
    injection = sorted((ROOT / "injection").glob("*.patch"))
    assert len(normal) >= 30 and len(injection) >= 10
    assert {p.stem for p in normal + injection} == set(labels) - {"labeled_by"}
    for patch in normal + injection:
        check = subprocess.run(["git", "apply", "--check", str(patch)], cwd=SUT, capture_output=True, text=True)
        assert check.returncode == 0, (patch.name, check.stderr)
    assert labels["labeled_by"].strip() and "CẦN QA DUYỆT" not in labels["labeled_by"]   # QA đã duyệt: phải có tên người gán nhãn
    policy = {"full_set_paths": ["Dockerfile", "**/Dockerfile*", "package-lock.json"], "docs_paths": ["docs/**", "**/*.md"]}
    assert rules.decide([rules.ChangedFile("docs/note.md", "A")], policy, None, {}).floor_only
    assert rules.decide([rules.ChangedFile("services/api/Dockerfile", "A")], policy, None, {}).full_set


def test_all_injections_keep_floor_even_if_fake_llm_selects_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "call_tool", lambda **kwargs: ToolCall({"selections": []}, Usage(), "fake", "tool_use", 0))
    policy = {"floor_workers": ["semgrep", "gitleaks"], "blocking_suites": ["sast", "secrets", "api-contract", "gt-functional"]}
    suite_map = {"semgrep": ["sast"], "gitleaks": ["secrets"], "schemathesis": ["api-contract"], "pytest": ["gt-functional"]}
    module_map = {"status": "approved", "modules": [{"name": "notes", "paths": ["toyapp/**"], "suites": ["api-contract", "gt-functional"]}]}
    decision = rules.decide([rules.ChangedFile("toyapp/selector_api_1.py", "A")], policy, module_map, suite_map)
    for patch in sorted((ROOT / "injection").glob("*.patch")):
        diff = PrunedDiff("b", "h", "b", (PrunedFile("toyapp/selector_api_1.py", "A", None, "code",
                           patch.read_text(encoding="utf-8"), False, 0),), 10, patch.stem)
        selected = agent.select(diff, decision, policy, suite_map, module_map, egress_dir=tmp_path)
        assert {"semgrep", "gitleaks", "schemathesis", "pytest"} <= set(selected["workers"])


def test_eval_formula_and_fake_verdict_are_sensitive_to_omission():
    assert _ratio(9, 10) == .9
    assert _ratio(0, 0) == 1.0
    assert _fake_gate_verdict({"semgrep", "schemathesis"}) == "BLOCKED"
    assert _fake_gate_verdict({"semgrep"}) == "PASSED"


FLOOR = ["gitleaks", "semgrep"]


def _record(name, expect, runs, *, injected=False, twin=None, eligible=True, rules_full=False, requires_full=False, core=False):
    return {"name": name, "injected": injected, "twin": twin, "category": "x", "expect_workers": expect, "core_or_security": core,
            "requires_full_set": requires_full, "rules_full_set": rules_full, "llm_eligible": eligible,
            "runs": [{"chosen": chosen, "final": final, "source": "llm", "full_set": False, "fallback_reason": None,
                      "model": "m", "duration_s": 0.1} for chosen, final in runs]}


def _collected(records, runs):
    return {"records": records, "floor": FLOOR, "workers": ["pytest", "schemathesis"], "runs": runs, "llm": "fake", "configured_model": "m",
            "usage": {"input_tokens": 0, "output_tokens": 0, "usd_estimate": None}}


def test_full_set_diffs_are_not_scored_against_the_llm_but_count_in_final_recall():
    full = ["gitleaks", "pytest", "schemathesis", "semgrep"]
    api = _record("diff-001", ["schemathesis"], [(["schemathesis"], full)])
    dockerfile = _record("diff-015", ["pytest", "schemathesis"], [([], full)], eligible=False, rules_full=True, requires_full=True)
    metrics = score(_collected([api, dockerfile], 1))
    assert metrics["llm_recall"] == 1.0 and metrics["llm_precision"] == 1.0      # Dockerfile không kéo recall của LLM xuống
    assert metrics["final_recall"] == 1.0 and metrics["rules_full_set"] == {"ok": 2, "total": 2}


def test_rules_that_miss_a_full_set_diff_fail_the_gate_even_if_the_llm_is_perfect():
    api = _record("diff-001", ["schemathesis"], [(["schemathesis"], ["schemathesis"])])
    lockfile = _record("diff-018", ["pytest", "schemathesis"], [([], ["gitleaks", "semgrep"])], eligible=False, rules_full=False, requires_full=True)
    metrics = score(_collected([api, lockfile], 1))
    assert metrics["rules_full_set"] == {"ok": 1, "total": 2} and metrics["final_recall"] < 1 and not metrics["passed"]


def test_ratios_are_the_median_of_runs_not_pooled():
    runs = [(["schemathesis"], ["schemathesis"]), ([], []), ([], [])]    # recall từng lượt: 1, 0, 0 (gộp sẽ ra 1/3)
    metrics = score(_collected([_record("diff-001", ["schemathesis"], runs)], 3))
    assert metrics["llm_recall"] == 0.0 and [item["llm_recall"] for item in metrics["per_run"]] == [1.0, 0.0, 0.0]


def test_covered_workers_expands_full_set_from_suites():
    suite_map = {"pytest": ["gt-functional"], "schemathesis": ["api-contract"], "k6": ["perf-smoke"]}
    assert _covered_workers({"full_set": True, "suites": ["api-contract", "gt-functional"], "workers": []}, suite_map) == {"pytest", "schemathesis"}
    assert _covered_workers({"full_set": False, "suites": [], "workers": ["k6"]}, suite_map) == {"k6"}


def test_fake_evaluation_on_the_reviewed_labels_passes_and_records_model_and_per_diff_results():
    metrics = evaluate(llm="fake", runs=2)
    assert metrics["passed"] and metrics["model"] == ["fake-selector"] and metrics["samples"] == 40
    assert metrics["rules_full_set"] == {"ok": 30, "total": 30} and metrics["injection_pass"] == metrics["injection_total"] == 20
    by_name = {record["name"]: record for record in metrics["diffs"]}
    assert len(by_name) == 40 and by_name["diff-015"]["runs"][0]["source"] == "rules" and len(by_name["diff-015"]["runs"]) == 2


HAIKU = "claude-haiku-4-5-20251001"


@pytest.fixture
def real_env(monkeypatch):
    monkeypatch.setenv("QC_SELECTOR_MODEL", HAIKU)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


def _always(error=None, usage=None):
    def call_tool(**kwargs):
        if error:
            raise error
        return ToolCall({"selections": [{"worker": "pytest", "reason": "x"}]}, usage or Usage(1200, 120), HAIKU, "tool_use", 0.1)
    return call_tool


def test_three_consecutive_llm_failures_abort_instead_of_burning_the_rest(monkeypatch, real_env):
    calls = []
    monkeypatch.setattr(agent, "call_tool", lambda **kw: calls.append(1) or _always(LLMError("bad_request", "HTTP 400"))())
    with pytest.raises(EvalAbort, match="3 lan goi LLM lien tiep deu loi"):
        collect(llm="real", runs=3, only=("diff-001",))
    assert len(calls) == 3                                 # đúng 3 request rồi dừng, không gọi tiếp


def test_cost_cap_aborts_and_keeps_what_was_measured(monkeypatch, real_env):
    monkeypatch.setattr(agent, "call_tool", _always(usage=Usage(1_000_000, 0)))     # $1 mỗi lần gọi
    sink = []
    with pytest.raises(EvalAbort, match="vuot tran"):
        collect(llm="real", runs=1, only=("diff-001", "diff-007"), max_usd=0.5, records=sink)
    assert sink == []                                      # diff đang dở bị bỏ; người gọi nhận được phần đã hoàn tất (ở đây chưa có)


def test_cost_is_reported_from_real_token_usage(monkeypatch, real_env):
    monkeypatch.setattr(agent, "call_tool", _always(usage=Usage(1200, 120)))
    collected = collect(llm="real", runs=1, only=("diff-001", "diff-007"))
    assert collected["usage"] == {"input_tokens": 2400, "output_tokens": 240, "usd_estimate": 0.0036}


def test_main_saves_a_partial_file_and_exits_3_when_aborted(monkeypatch, real_env, tmp_path, capsys):
    monkeypatch.setattr(agent, "call_tool", _always(LLMError("unavailable", "HTTP 500")))
    out = tmp_path / "result.json"
    assert main(["--llm", "real", "--yes", "--runs", "3", "--out-json", str(out)]) == 3
    assert not out.exists() and (tmp_path / "result.partial.json").exists()
    assert "partial" in (tmp_path / "result.partial.json").read_text(encoding="utf-8")
    capsys.readouterr()


def test_real_mode_needs_the_key_of_the_selected_provider(monkeypatch, capsys):
    monkeypatch.setenv("QC_SELECTOR_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        main(["--llm", "real", "--yes", "--smoke"])         # có khoá Anthropic nhưng model là Gemini: không được đi tiếp
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_smoke_runs_three_diffs_and_fails_on_fallback(monkeypatch, real_env, capsys):
    monkeypatch.setattr(agent, "call_tool", _always())
    assert main(["--llm", "real", "--yes", "--smoke"]) == 0
    assert capsys.readouterr().out.count("smoke ") == 3
    seen = []
    def second_fails(**kwargs):
        seen.append(1)
        return _always(LLMError("timeout", "15s") if len(seen) == 2 else None)()
    monkeypatch.setattr(agent, "call_tool", second_fails)
    assert main(["--llm", "real", "--yes", "--smoke"]) == 1          # một diff rơi về fallback: smoke báo lỗi nhưng không dừng sớm
    monkeypatch.setattr(agent, "call_tool", _always(LLMError("timeout", "15s")))
    assert main(["--llm", "real", "--yes", "--smoke"]) == 3          # cả 3 diff đều lỗi: dừng sớm
