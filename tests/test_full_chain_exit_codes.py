"""Kịch bản A–E (tests/full_chain.py) phải kiểm mã thoát của TỪNG bước bắt buộc, nhất là Report (S4-05).
Vì sao: bước "Enforce gate result" chỉ nhìn `exit_code` của gate, nên Report (Check Run, comment PR, lịch sử, webhook) hỏng mà gate xanh thì Enforce vẫn exit 0,
và `run_workflow` không đổi mã trả về. Trước khi có dòng "mã thoát Report", kịch bản vẫn xanh. Các test này KHÔNG cần Docker: `Result` dựng sẵn thay cho lần chạy workflow thật
(chỉ kiểm logic của kịch bản và của bảng kết quả, không nói gì về việc Report thật chạy đúng)."""
import inspect
import json
from pathlib import Path

import pytest
import yaml

from tests import full_chain as fc
from tests import harness_kit as kit

PR_STEPS = fc.STEPS_PR
DISPATCH_STEPS = ["Start SUT", "Run qc-agent gate", fc.REPORT_STEP, "Clean up SUT", "Enforce gate result"]


def fake_result(tmp_path: Path, steps: list, *, gate_exit="0", codes=None, verdict="PASSED", tasks=("code.sast",), selection=None) -> kit.Result:
    """Kết quả workflow giả, đúng hình dạng mà `_pipeline` và các kịch bản đọc. `codes` ghi đè mã thoát; giá trị None = bước không chạy."""
    codes = {"Enforce gate result": 0 if gate_exit == "0" else 1, **(codes or {})}
    outputs = {"Start SUT": {"base_url": "http://sut:8000"}, "Select (PR)": {"ok": "true"}, "Run qc-agent gate": {"exit_code": gate_exit}}
    results = {name: {"returncode": codes.get(name, 0), "outputs": outputs.get(name, {}), "masks": []} for name in steps if codes.get(name, 0) is not None}
    run_dir = tmp_path / "r-0001"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "plan.yaml").write_text(yaml.safe_dump({"tasks": [{"capability": cap} for cap in tasks]}), encoding="utf-8")
    report = {"gate_verdict": verdict, "exit_code": int(gate_exit), "severity_counts": {"critical": 0, "medium": 0, "low": 0}, "findings": []}
    return kit.Result(results, "log của harness", tmp_path, run_dir, selection or {"source": "manual", "suites": ["sast"]}, report)


def rows(rec: fc.Recorder, step_prefix: str) -> list:
    return [row for row in rec.rows if row[0].startswith(step_prefix)]


# ---- _pipeline: dùng chung cho C, D, E ----

@pytest.mark.parametrize(("steps", "gate_exit"), [(PR_STEPS, "0"), (PR_STEPS, "1"), (DISPATCH_STEPS, "0")])
def test_a_healthy_run_has_no_failing_row_and_a_report_exit_row(tmp_path, steps, gate_exit):
    rec = fc.Recorder("T", "t")
    fc._pipeline(rec, fake_result(tmp_path, steps, gate_exit=gate_exit, tasks=()), gate_exit=gate_exit, steps=steps)
    assert rec.failures() == [], rec.table()
    assert [row[2] for row in rows(rec, "mã thoát Report")] == [fc.PASS], "kịch bản phải ghi dòng mã thoát Report vào bảng"


@pytest.mark.parametrize(("steps", "gate_exit"), [(PR_STEPS, "0"), (PR_STEPS, "1"), (DISPATCH_STEPS, "0")])
def test_a_failing_report_step_makes_the_scenario_fail_even_when_the_gate_result_matches(tmp_path, steps, gate_exit):
    """Tái hiện lỗi: Report exit 1; gate đúng kỳ vọng và Enforce đúng (chỉ nhìn gate) => trước khi sửa, không dòng nào FAIL."""
    rec = fc.Recorder("T", "t")
    res = fake_result(tmp_path, steps, gate_exit=gate_exit, codes={fc.REPORT_STEP: 1}, tasks=())
    fc._pipeline(rec, res, gate_exit=gate_exit, steps=steps)
    failed = [row[0] for row in rec.failures()]
    assert failed == ["mã thoát Report"], rec.table()
    assert "exit 1" in rec.failures()[0][3]
    assert res.code("Enforce gate result") == (0 if gate_exit == "0" else 1)   # chứng minh Enforce không bắt được lỗi này


def test_a_report_step_that_did_not_run_also_fails(tmp_path):
    rec = fc.Recorder("T", "t")
    res = fake_result(tmp_path, [n for n in PR_STEPS if n not in (fc.REPORT_STEP, "Clean up SUT")])     # thiếu Report và Clean up SUT
    fc._pipeline(rec, res, gate_exit="0")
    assert "mã thoát Report" in [row[0] for row in rec.failures()] and "thứ tự các bước" in [row[0] for row in rec.failures()], rec.table()


@pytest.mark.parametrize("step", ["Start SUT", "Select (PR)", "PR review", "Jira (Low)", "Clean up SUT"])
def test_any_other_required_step_exiting_non_zero_fails_but_report_stays_green(tmp_path, step):
    rec = fc.Recorder("T", "t")
    fc._pipeline(rec, fake_result(tmp_path, PR_STEPS, codes={step: 2}, tasks=()), gate_exit="0")
    failed = [row[0] for row in rec.failures()]
    assert "mã thoát các bước (trừ Report, gate, Enforce)" in failed, rec.table()
    assert not rows(rec, "mã thoát Report") or rows(rec, "mã thoát Report")[0][2] == fc.PASS


def test_the_gate_step_itself_must_exit_zero_because_the_gate_code_travels_in_the_output(tmp_path):
    rec = fc.Recorder("T", "t")
    fc._pipeline(rec, fake_result(tmp_path, PR_STEPS, codes={"Run qc-agent gate": 1}, tasks=()), gate_exit="0")
    assert "Run qc-agent gate (mã thoát bước)" in [row[0] for row in rec.failures()], rec.table()


# ---- trọn kịch bản E (không Docker: run_pr được thay bằng kết quả dựng sẵn) ----

@pytest.fixture
def run_e(tmp_path, monkeypatch):
    def run(**kwargs):
        monkeypatch.setattr(kit, "run_pr", lambda *a, **k: fake_result(tmp_path, DISPATCH_STEPS, **kwargs))
        return fc.scenario_e("qc-agent:fake")
    return run


def test_scenario_e_is_green_when_every_step_exits_as_expected(run_e):
    rec = run_e()
    assert rec.failures() == [], rec.table()


def test_scenario_e_fails_when_report_fails(run_e):
    rec = run_e(codes={fc.REPORT_STEP: 1})
    assert [row[0] for row in rec.failures()] == ["mã thoát Report"], rec.table()


def test_the_pytest_wrapper_and_the_cli_both_go_red_on_a_report_failure(run_e, monkeypatch, capsys):
    from tests import test_full_chain_local as wrapper
    rec = run_e(codes={fc.REPORT_STEP: 1})
    with pytest.raises(AssertionError, match="mã thoát Report"):
        wrapper._assert_ok(rec)
    monkeypatch.setattr(kit.image_check, "ensure_image", lambda **_: kit.image_check.Verdict("qc-agent:x", True, "commit", "a" * 40, "a" * 40))
    monkeypatch.setattr(fc, "run_all", lambda image: [rec])
    assert fc.main() == 1
    assert "1 FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("scenario", [fc.scenario_c, fc.scenario_d, fc.scenario_e])
def test_scenarios_c_d_e_all_go_through_the_shared_pipeline_check(scenario):
    """C và D chạy nhiều bước nặng cần Docker; ở đây chỉ canh để không kịch bản nào bỏ gọi `_pipeline` (nơi có dòng mã thoát Report)."""
    assert "_pipeline(rec, res" in inspect.getsource(scenario)


def test_the_report_row_name_is_the_one_the_real_workflow_uses():
    steps = yaml.safe_load(kit.harness.DEFAULT_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["gate"]["steps"]
    assert fc.REPORT_STEP in [step.get("name") for step in steps]
    assert json.dumps(fc.STEPS_PR) and fc.STEPS_PR == [name for name in (s.get("name") for s in steps) if name in fc.STEPS_PR]   # cùng thứ tự với workflow
