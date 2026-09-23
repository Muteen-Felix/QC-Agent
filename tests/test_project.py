"""Project/suite đa dự án: nạp + validate, policy lane, chạy thật với cwd = SUT root, CLI."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from qc_agent.core import engine, project as pj
from qc_agent.core.cli import main
from qc_agent.core.plan import PlanError

ROOT = Path(__file__).resolve().parent.parent
NOTEBOARD_SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
PROJECTS = ROOT / "configs" / "projects"


from tests.projkit import make_sut, task, write_project, write_suite  # noqa: F401


@pytest.fixture
def sut(tmp_path):
    return make_sut(tmp_path)


def build(tmp_path, sut, mode="pr", **kw):
    projects = write_project(tmp_path)
    cfg = pj.load_project("demo", projects)
    return pj.build_plan(cfg, mode, pj.load_suites(sut / cfg["suites_dir"]), **kw)


# ---- nạp + validate ----

def test_reference_project_and_suites_load_and_build_both_modes():
    cfg = pj.load_project("noteboard", PROJECTS)
    suites = pj.load_suites(NOTEBOARD_SUT / cfg["suites_dir"])
    assert sorted(suites) == ["ai-eval", "api-contract", "perf", "ui-explore"]
    pr, meta = pj.build_plan(cfg, "pr", suites)
    assert [t["task_id"] for t in pr["tasks"]] == ["t-001", "t-000", "t-003", "t-101", "t-canary-01"]
    assert meta["on_skipped_gate_task"] == "fail" and set(meta["suite_sha256"]) == {"api-contract", "ai-eval", "ui-explore"}
    manual, _ = pj.build_plan(cfg, "manual", suites)
    assert "t-002" in {t["task_id"] for t in manual["tasks"]}  # perf chỉ chạy thủ công
    assert "t-002" not in {t["task_id"] for t in pr["tasks"]}


def test_project_schema_errors(tmp_path):
    d = tmp_path
    (d / "a.yaml").write_text("slug: a\nmodes: {}\n", encoding="utf-8")
    with pytest.raises(PlanError, match="không hợp lệ"):
        pj.load_project("a", d)
    (d / "b.yaml").write_text("slug: khac\nmodes: {pr: {suites: '*'}}\n", encoding="utf-8")
    with pytest.raises(PlanError, match="trùng tên file"):
        pj.load_project("b", d)
    (d / "c.yaml").write_text("slug: c\nmodes: {pr: {suites: '*', blocking_suites: [x]}}\n", encoding="utf-8")
    with pytest.raises(PlanError, match="không hợp lệ"):  # `suites` và blocking/advisory loại trừ nhau
        pj.load_project("c", d)
    with pytest.raises(PlanError, match="thiếu"):
        pj.load_project("khong-co", d)


def test_suite_name_must_match_file(tmp_path, sut):
    (sut / ".qc-agent" / "suites" / "core.yaml").write_text(yaml.safe_dump({"suite": "other", "tasks": [task("t-1")]}), encoding="utf-8")
    with pytest.raises(PlanError, match="trùng tên file"):
        pj.load_suites(sut / ".qc-agent" / "suites")


# ---- policy lane ----

def test_blocking_suite_with_discovery_task_is_an_error(tmp_path, sut):
    write_suite(sut, "core", [task("t-1"), task("t-2", lane="discovery")])
    with pytest.raises(PlanError, match=r"suite core task t-2: lane='discovery'.*blocking_suites"):
        build(tmp_path, sut)


def test_advisory_suite_with_gate_task_is_an_error(tmp_path, sut):
    write_suite(sut, "extra", [task("t-9", lane="gate")])
    with pytest.raises(PlanError, match=r"suite extra task t-9.*advisory_suites"):
        build(tmp_path, sut)


def test_manual_mode_does_not_enforce_lanes(tmp_path, sut):
    write_suite(sut, "core", [task("t-1"), task("t-2", lane="discovery")])
    plan, _ = build(tmp_path, sut, mode="manual")
    assert {t["lane"] for t in plan["tasks"]} == {"gate", "discovery"}


def test_missing_suite_unknown_mode_duplicate_ids(tmp_path, sut):
    (sut / ".qc-agent" / "suites" / "extra.yaml").unlink()
    with pytest.raises(PlanError, match="cần suite không có"):
        build(tmp_path, sut)
    with pytest.raises(PlanError, match="không có mode"):
        build(tmp_path, sut, mode="nightly")
    write_suite(sut, "extra", [task("t-1", lane="discovery")])  # trùng t-1 của core
    with pytest.raises(PlanError, match="trùng"):
        build(tmp_path, sut)


def test_suite_cannot_be_both_blocking_and_advisory(tmp_path, sut):
    projects = write_project(tmp_path, {"pr": {"blocking_suites": ["core"], "advisory_suites": ["core"]}})
    cfg = pj.load_project("demo", projects)
    with pytest.raises(PlanError, match="vừa blocking vừa advisory"):
        pj.build_plan(cfg, "pr", pj.load_suites(sut / cfg["suites_dir"]))


def test_only_suites_must_stay_inside_policy(tmp_path, sut):
    plan, meta = build(tmp_path, sut, only_suites=["core"])
    assert set(meta["suite_sha256"]) == {"core"} and len(plan["tasks"]) == 2
    with pytest.raises(PlanError, match="ngoài policy"):
        build(tmp_path, sut, only_suites=["perf"])


# ---- chạy thật ----

def test_run_project_runs_workers_with_cwd_sut_root_and_records_project_meta(tmp_path, sut):
    projects = write_project(tmp_path)
    result = engine.run_project("demo", "pr", tmp_path / "runs", projects_dir=projects, sut_root=sut)
    assert result.exit_code == 0 and result.gate.value == "PASS"
    # fixture chỉ tồn tại dưới SUT root: mock adapter đọc được => worker chạy với cwd = SUT root
    data = json.loads((result.run_dir / "report.json").read_text(encoding="utf-8"))
    assert data["project"] == "demo" and data["mode"] == "pr"
    assert set(data["suite_sha256"]) == {"core", "extra"} and all(len(v) == 64 for v in data["suite_sha256"].values())
    assert "demo:pr" in (result.run_dir / "plan.yaml").read_text(encoding="utf-8")
    assert result.run_ctx.plan_path == f"{result.run_id}/plan.yaml"


def test_suite_sha256_changes_when_suite_file_changes(tmp_path, sut):
    projects = write_project(tmp_path)
    a = engine.run_project("demo", "pr", tmp_path / "runs", projects_dir=projects, sut_root=sut)
    write_suite(sut, "extra", [task("t-9", lane="discovery", intent="đã bị sửa")])
    b = engine.run_project("demo", "pr", tmp_path / "runs", projects_dir=projects, sut_root=sut)
    assert a.run_ctx.suite_sha256["core"] == b.run_ctx.suite_sha256["core"]
    assert a.run_ctx.suite_sha256["extra"] != b.run_ctx.suite_sha256["extra"]


def test_mode_policy_on_skipped_gate_task_applies_and_can_be_overridden(tmp_path, sut, monkeypatch):
    monkeypatch.setenv("QC_WORKERS_PATH", str(ROOT / "tests" / "fixtures" / "workers"))  # không worker nào có http.load => skipped, không phụ thuộc máy có k6 hay không
    write_suite(sut, "core", [task("t-1"), task("t-3", capability="http.load", oracle={"kind": "threshold", "assertions": [{"metric": "m", "op": "<", "value": 1}]})])
    projects = write_project(tmp_path)
    blocked = engine.run_project("demo", "pr", tmp_path / "runs", projects_dir=projects, sut_root=sut)
    assert blocked.exit_code == 1 and blocked.gate.value == "FAIL"  # policy mode pr: on_skipped_gate_task=fail
    relaxed = engine.run_project("demo", "pr", tmp_path / "runs", projects_dir=projects, sut_root=sut, on_skipped_gate_task="yellow")
    assert relaxed.gate.value == "YELLOW"


def test_lane_conflict_leaves_no_run_dir(tmp_path, sut):
    write_suite(sut, "core", [task("t-1", lane="discovery")])
    with pytest.raises(PlanError):
        engine.run_project("demo", "pr", tmp_path / "runs", projects_dir=write_project(tmp_path), sut_root=sut)
    assert not (tmp_path / "runs").exists()


# ---- CLI ----

def test_cli_run_project_optional_run_word_and_exit_codes(tmp_path, sut, capsys):
    projects = write_project(tmp_path)
    argv = ["--project", "demo", "--mode", "pr", "--projects-dir", str(projects), "--sut-root", str(sut),
            "--runs-dir", str(tmp_path / "runs")]
    assert main(["run", *argv]) == 0
    assert "QC Gate Report" in capsys.readouterr().out
    assert main(argv) == 0  # không có từ `run` cũng chạy
    assert main([*argv, "--suites", "core"]) == 0


def test_cli_usage_errors_exit_3(tmp_path, sut):
    projects = write_project(tmp_path)
    base = ["--projects-dir", str(projects), "--sut-root", str(sut), "--runs-dir", str(tmp_path / "runs")]
    assert main(["--project", "demo", *base]) == 3  # thiếu --mode
    assert main(["--project", "demo", "--mode", "pr", "--plan", "x.yaml", *base]) == 3  # --plan và --project loại trừ nhau
    assert main([*base]) == 3  # không có gì để chạy
    assert main(["--project", "demo", "--mode", "nightly", *base]) == 3
    assert main(["--project", "khong-co", "--mode", "pr", *base]) == 3
