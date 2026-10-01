from pathlib import Path

import pytest

from qc_agent.core import engine, registry
from qc_agent.core.cli import main
from qc_agent.core.plan import PlanError

ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests/fixtures/sut/noteboard"


def selection(suites):
    return {"version": 1, "trigger_type": "pr", "source": "rules", "full_set": False,
            "diff_sha256": "abc", "floor": [], "workers": [], "suites": suites,
            "rationale": {}, "fallback_reason": None, "llm": None}


def test_core_adds_floor_to_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "_execute", lambda plan, *a, **k: plan)
    result = engine.run_project("noteboard", "pr", tmp_path / "runs", sut_root=SUT, selection=selection(["api-contract"]), trigger="pr")
    assert {"t-001", "t-010", "t-011"} == {task["task_id"] for task in result["tasks"]}
    assert result["selection"]["floor_enforced_by_core"] == ["sast", "secrets"]
    with pytest.raises(PlanError):
        engine.run_project("noteboard", "pr", tmp_path / "runs", sut_root=SUT, selection=selection(["outside"]), trigger="pr")


def test_invalid_selection_rejected(monkeypatch, tmp_path):
    with pytest.raises(PlanError, match="selection.json"):
        engine.run_project("noteboard", "pr", tmp_path / "runs", sut_root=SUT, selection={"bad": 1}, trigger="pr")


def test_report_and_rerender_keep_enforced_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:1")
    def unavailable(worker):
        worker.probe_ok = False
        worker.probe_reason = "fixture"
        return worker
    monkeypatch.setattr(registry, "probe", unavailable)
    result = engine.run_project("noteboard", "pr", tmp_path / "runs", sut_root=SUT,
                                selection=selection(["api-contract"]), trigger="pr")
    assert (result.run_dir / "selection.json").is_file()
    assert "Floor được core bổ sung" in result.report_md
    assert main(["--rerender", str(result.run_dir)]) == result.exit_code
    assert "Phạm vi chạy" in (result.run_dir / "report.rerender.md").read_text(encoding="utf-8")
