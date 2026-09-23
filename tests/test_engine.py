"""core.engine.run_plan: dùng được như thư viện, song song trong một tiến trình, không sửa os.environ."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from qc_agent.core import engine
from qc_agent.core.plan import PlanError

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "tests" / "fixtures" / "plans" / "demo.yaml"
DEMO_FAIL = ROOT / "tests" / "fixtures" / "plans" / "demo_fail.yaml"


def test_run_plan_returns_result_without_printing(tmp_path, capsys):
    result = engine.run_plan(DEMO, tmp_path / "runs")
    assert result.exit_code == 0 and result.gate.value == "PASS"
    assert result.run_id == "r-0001" and (result.run_dir / "report.json").is_file()
    assert "QC Gate Report" in result.report_md
    assert capsys.readouterr().out == ""  # engine không print: việc đó của CLI


def test_run_plan_does_not_mutate_os_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("QC_RUNS_DIR", raising=False)
    engine.run_plan(DEMO, tmp_path / "runs")
    assert "QC_RUNS_DIR" not in os.environ


def test_two_parallel_runs_with_different_runs_dirs_do_not_mix(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    with ThreadPoolExecutor(2) as pool:
        ra, rb = pool.map(lambda d: engine.run_plan(DEMO, d), [a, b])
    assert ra.exit_code == rb.exit_code == 0
    for runs, res in ((a, ra), (b, rb)):
        assert res.run_dir.parent == runs
        # adapter mock ghi mock-output.json vào workdir = <QC_RUNS_DIR>/<run>/<task>: phải nằm đúng runs_dir của lời gọi đó
        assert (runs / res.run_id / "t-e01" / "mock-output.json").is_file()


def test_parallel_runs_in_same_runs_dir_get_distinct_ids(tmp_path):
    runs = tmp_path / "runs"
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _: engine.run_plan(DEMO, runs), range(3)))
    ids = sorted(r.run_id for r in results)
    assert ids == ["r-0001", "r-0002", "r-0003"] and all(r.exit_code == 0 for r in results)


def test_explicit_run_id_and_duplicate_rejected(tmp_path):
    engine.run_plan(DEMO, tmp_path, run_id="job-42")
    assert (tmp_path / "job-42" / "report.json").is_file()
    with pytest.raises(PlanError, match="đã tồn tại"):
        engine.run_plan(DEMO, tmp_path, run_id="job-42")


def test_failed_plan_setup_leaves_no_run_dir(tmp_path):
    with pytest.raises(PlanError):
        engine.run_plan(DEMO, tmp_path / "runs", only="khong-co-task")
    assert list((tmp_path / "runs").glob("r-*")) == []


def test_gate_fail_exit_code_and_yellow_exit(tmp_path):
    assert engine.run_plan(DEMO_FAIL, tmp_path / "runs").exit_code == 1


def test_worker_version_from_probe_is_recorded_in_results(tmp_path):
    result = engine.run_plan(DEMO, tmp_path / "runs")
    data = json.loads((result.run_dir / "results" / "t-e01.json").read_text(encoding="utf-8"))
    assert data["worker"]["version"].startswith("Python")
