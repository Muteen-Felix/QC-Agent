"""P2-7: workflow tái sử dụng cấp đủ dữ liệu cho khâu dò nợ test — checkout fetch-depth 0 (đủ lịch sử cho merge-base của Select) và QC_DIFF_BASE chỉ trên pull_request, chỉ ở bước gate.
Kiểm tĩnh trên file thật (không Docker/GitHub). Dò nợ là MỘT suite trong lượt gate: không có bước riêng, không nằm trong refine, không continue-on-error."""
import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_workflow_static import DATA, STEPS, TEXT, step

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_reusable_locally as harness  # noqa: E402

EXPRESSION = "${{ github.event_name == 'pull_request' && 'HEAD^1' || '' }}"
GATE = "Run qc-agent gate"


def checkouts():
    return [s for s in STEPS if str(s.get("uses", "")).startswith("actions/checkout@")]


def test_checkout_fetches_full_history_and_keeps_its_hardening():
    (checkout,) = checkouts()
    assert checkout["with"]["fetch-depth"] == 0  # 0 = full history: Select cần merge-base; HEAD^1 của dò nợ vẫn có sẵn
    assert checkout["with"]["persist-credentials"] is False  # không nới lỏng bảo mật khi sửa checkout
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])  # vẫn ghim theo SHA


def test_gate_step_sets_qc_diff_base_only_on_pull_request_with_a_fixed_value():
    env = step(GATE)["env"]
    assert env["QC_DIFF_BASE"] == EXPRESSION
    # giá trị cố định: không lấy từ input/tên nhánh/PR nên mã của PR không chèn được vào biến này
    assert not re.search(r"inputs\.|head_ref|pull_request\.|secrets\.", env["QC_DIFF_BASE"])


def test_qc_diff_base_is_forwarded_into_the_gate_container():
    run = step(GATE)["run"]
    assert re.search(r"(?<![\w-])-e QC_DIFF_BASE(?![\w=])", run)  # dạng `-e NAME` (giá trị ở env của bước, không lên dòng lệnh)
    assert run.index("-e QC_DIFF_BASE") < run.index('"$IMAGE"')  # là cờ của docker run, đứng trước tên image
    assert "QC_DIFF_BASE=" not in run


def test_qc_diff_base_appears_in_the_gate_step_and_nowhere_else():
    holders = [s.get("name") or s.get("uses") for s in STEPS if "QC_DIFF_BASE" in json.dumps(s)]
    assert holders == [GATE]  # đặc biệt: không ở Refine / Post refine / Security review / Report
    assert "QC_DIFF_BASE" not in json.dumps(DATA.get("env") or {}) and "QC_DIFF_BASE" not in json.dumps(DATA["jobs"]["gate"].get("env") or {})
    for name in ("Refine (onboarding suggestions)", "Post refine review"):
        assert "QC_DIFF_BASE" not in json.dumps(step(name))


def test_debt_scan_is_part_of_the_gate_run_not_a_new_step_and_never_continue_on_error():
    gate = step(GATE)
    assert "continue-on-error" not in gate and gate.get("id") == "gate"  # lỗi hạ tầng của gate không bị nuốt
    assert "continue-on-error" not in json.dumps(gate)
    assert not [s.get("name") or s.get("uses") for s in STEPS if re.search(r"coverage.?debt|debt|nợ", json.dumps(s, ensure_ascii=False), re.I)]
    # không bước nào (kể cả gate) gọi worker dò nợ trực tiếp: nó chỉ là một suite chạy bên trong `qc-agent run`
    assert "coverage_debt_worker" not in TEXT and "coverage-debt" not in TEXT.replace("# ", "#")
    # thứ tự chốt: gate chạy trước bước báo cáo (báo cáo cần debt trong report.json)
    names = [s.get("name") or s.get("uses") for s in STEPS]
    assert names.index(GATE) < names.index("Report (Check Run, PR comment, history, webhook)")


@pytest.mark.parametrize("event, expected", [("pull_request", "HEAD^1"), ("push", ""), ("workflow_dispatch", ""), ("schedule", ""), ("pull_request_target", "")])
def test_the_expression_evaluates_to_head_parent_only_for_pull_request(event, expected):
    ctx = harness.Context({}, {}, {"event_name": event})
    assert ctx.render(step(GATE)["env"]["QC_DIFF_BASE"]) == expected


def test_harness_expression_evaluator_follows_github_semantics():
    ctx = harness.Context({"x": "1", "empty": ""}, {}, {"event_name": "push", "flag": True})
    assert ctx.evaluate("github.event_name == 'push' && 'yes' || 'no'") == "yes"
    assert ctx.evaluate("github.event_name != 'push' && 'yes' || 'no'") == "no"
    assert ctx.evaluate("github.event_name == 'push' && inputs.x || 'no'") == "1"
    assert ctx.evaluate("github.event_name == 'push' && inputs.empty || 'fallback'") == "fallback"  # a && b: b rỗng => rơi sang ||
    assert ctx.evaluate("inputs.empty || inputs.x") == "1" and ctx.evaluate("inputs.empty") == "" and ctx.evaluate("github.flag") == "true"
    assert ctx.evaluate("github.event_name == 'a' && 'x'") == ""  # không có nhánh || và vế đầu sai


def test_workflow_text_documents_fetch_depth_and_head_parent():
    assert "fetch-depth: 0" in TEXT and "HEAD^1" in TEXT
