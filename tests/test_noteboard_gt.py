"""Bộ Ground-Truth ĐÃ DUYỆT của noteboard (S1-08) và việc bật gate `gt-functional` cho project này.

Bộ này do `qc-agent gt generate` (LLM giả + fixture S1-04) sinh ra, rồi dev ĐÓNG VAI QA duyệt: đây là dữ liệu giả lập để đo pipeline, chưa phải QA thật.
"""
import json
from pathlib import Path

import yaml

from qc_agent.core import project as pj
from qc_agent.core.cli import main as cli_main
from qc_agent.groundtruth import check

ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
GT = SUT / ".qc-agent" / "ground-truth"
RESPONSE = ROOT / "tests" / "fixtures" / "llm" / "gt_noteboard_response.json"


def catalog():
    return yaml.safe_load((GT / "test-cases.yaml").read_text(encoding="utf-8"))


def test_the_approved_set_passes_the_hitl_gate_with_no_warnings():
    result = check.check(SUT)
    assert result.errors == [] and result.warnings == []


def test_gt_validate_exits_0_through_the_cli(capsys):
    assert cli_main(["gt", "validate", "--sut-root", str(SUT)]) == 0
    assert "OK: 0 lỗi, 0 cảnh báo" in capsys.readouterr().out


def test_every_test_case_was_reviewed_and_the_qa_additions_are_marked():
    data = catalog()
    assert data["status"] == "approved" and {tc["status"] for tc in data["test_cases"]} <= {"approved", "rejected"}
    qa = [tc for tc in data["test_cases"] if tc["origin"] == "qa"]
    assert len(qa) >= 3 and all(tc["status"] == "approved" and tc["notes"].startswith("QA:") for tc in qa)
    rejected = [tc for tc in data["test_cases"] if tc["status"] == "rejected"]
    assert rejected and all(tc["rejected_reason"].strip() for tc in rejected)
    assert sum(tc["status"] == "approved" for tc in data["test_cases"]) >= 30


def test_llm_test_cases_are_exactly_what_the_fake_generation_produced(tmp_path):
    """Không sửa tay nội dung TC của LLM: QA chỉ đổi status (và thêm TC của QA)."""
    from qc_agent.groundtruth.generate import generate
    from qc_agent.groundtruth.prd import parse_prd
    import httpx
    import os
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-x")
    payload = json.loads(RESPONSE.read_text(encoding="utf-8"))
    prd = parse_prd(ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md", openapi_source=str(ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"))
    generated = {tc["tc_id"]: tc for tc in generate(prd, model="claude-sonnet-5", egress_dir=tmp_path,
                                                   transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))).catalog["test_cases"]}
    kept = {tc["tc_id"]: tc for tc in catalog()["test_cases"] if tc["origin"] == "llm"}
    assert set(kept) == set(generated)
    for tc_id, tc in kept.items():
        assert {k: v for k, v in tc.items() if k not in ("status", "rejected_reason")} == {k: v for k, v in generated[tc_id].items() if k != "status"}, tc_id


def test_the_ui_only_acs_are_declared_uncovered_not_orphaned():
    data = catalog()
    assert {u["ac_id"] for u in data["uncovered_acs"]} == {"AC-1.8", "AC-3.5"}


def test_the_generated_suite_and_the_module_map_are_in_place():
    suite = yaml.safe_load((SUT / ".qc-agent" / "suites" / "gt-functional.yaml").read_text(encoding="utf-8"))
    assert suite["suite"] == "gt-functional" and suite["tasks"][0]["task_id"] == "t-030" and suite["tasks"][0]["lane"] == "gate"
    module_map = yaml.safe_load((GT / "module-map.yaml").read_text(encoding="utf-8"))
    assert module_map["status"] == "approved" and module_map["modules"][0]["suites"] == ["api-contract", "gt-functional"]
    assert "qc-agent:todo" not in (GT / "module-map.yaml").read_text(encoding="utf-8")


def test_noteboard_policy_blocks_on_the_ground_truth_suite_and_keeps_the_old_ones():
    project, _ = pj.resolve_project("noteboard", ROOT / "configs" / "projects")
    blocking = project["modes"]["pr"]["blocking_suites"]
    assert blocking == ["api-contract", "ai-eval", "gt-functional"]                 # danh sách THAY THẾ (không cộng dồn): phải giữ đủ suite cũ
    suites = pj.load_suites(SUT / ".qc-agent" / "suites")
    plan, _ = pj.build_plan(project, "pr", suites)
    lanes = {task["task_id"]: task["lane"] for task in plan["tasks"]}
    assert lanes["t-030"] == "gate" and lanes["t-001"] == "gate"


def test_the_default_policy_is_untouched_so_repos_without_the_suite_do_not_break():
    default = yaml.safe_load((ROOT / "configs" / "projects" / "_default.yaml").read_text(encoding="utf-8"))
    assert "gt-functional" not in json.dumps(default)
