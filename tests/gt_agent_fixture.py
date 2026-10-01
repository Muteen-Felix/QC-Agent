"""Hội thoại agent CỐ ĐỊNH cho noteboard: danh sách response Messages API phát tuần tự, dùng cho `tools/eval_gt_sut.py --llm fake --generator agent` và cho test.

File JSON `tests/fixtures/llm/gt_agent_noteboard.json` được sinh từ đây (đặt QC_UPDATE_GOLDEN=1 khi chạy tests/test_eval_gt_agent.py để ghi lại sau khi CỐ Ý đổi), và một test so
file với kết quả của `build()` để hai nơi không lệch nhau. Nội dung: đọc mã (toyapp/app.py) và OpenAPI, lập kế hoạch, nộp TC theo story từ bộ fixture single-shot
(có 5 TC cố ý sai để thử vòng tự sửa), bị từ chối `finish_generation` một lần vì còn gap, rồi kết thúc bằng `waivers` cho ba ô API 422 (toyapp nhận id là chuỗi tự do nên
KHÔNG có đầu vào nào gây 422: viết TC cho chúng sẽ là TC sai) và `uncovered_acs` cho hai AC chỉ kiểm được ở giao diện.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SINGLE_SHOT_RESPONSE = FIXTURES / "llm" / "gt_noteboard_response.json"
AGENT_SCRIPT = FIXTURES / "llm" / "gt_agent_noteboard.json"
WAIVED = ("GET /notes/{note_id} 422", "DELETE /notes/{note_id} 422", "POST /notes/{note_id}/summarize 422")
USAGE = {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def _message(content: list[dict]) -> dict:
    return {"id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content, "stop_reason": "tool_use", "stop_sequence": None, "usage": dict(USAGE)}


def _turn(counter: list[int], *calls: tuple[str, dict]) -> dict:
    blocks = [{"type": "thinking", "thinking": "", "signature": "sig-fixture"}]
    for name, data in calls:
        counter[0] += 1
        blocks.append({"type": "tool_use", "id": f"toolu_{counter[0]:03}", "name": name, "input": data})
    return _message(blocks)


def _agentify(tc: dict, **extra) -> dict:
    return {**copy.deepcopy(tc), "technique": "happy_path", "priority": "medium", "preconditions": None, "rationale": None, "evidence": [], **extra}


def build(prd) -> list[dict]:
    """`prd` là ParsedPRD của noteboard (để gom TC theo story)."""
    emitted = next(b["input"] for b in json.loads(SINGLE_SHOT_RESPONSE.read_text(encoding="utf-8"))["content"] if b["type"] == "tool_use")
    story_of = {ac.ac_id: story.story_id for story in prd.stories for ac in story.acs}
    by_story: dict[str, list[dict]] = {}
    for tc in emitted["test_cases"]:
        by_story.setdefault(story_of.get(tc["ac_refs"][0], prd.stories[0].story_id), []).append(_agentify(tc))
    counter = [0]
    plan = {"items": [{"ac_id": "AC-1.1", "technique": "happy_path", "scenario": "tạo ghi chú", "decision": "planned", "reason": None},
                      {"ac_id": "AC-1.5", "technique": "boundary", "scenario": "title dài 200 và 201", "decision": "planned", "reason": None}]}
    script = [
        _turn(counter, ("list_dir", {"path": ".", "depth": 2}), ("read_file", {"path": "toyapp/app.py", "start_line": 1, "max_lines": 120}),
              ("openapi_operation", {"method": "POST", "path": "/notes"})),
        _turn(counter, ("record_coverage_plan", plan)),
        *[_turn(counter, ("submit_test_cases", {"story_id": story, "test_cases": tcs})) for story, tcs in by_story.items()],
        _turn(counter, ("finish_generation", {"uncovered_acs": [], "waivers": [], "self_review": {"gaps_fixed": 0, "notes": "lần đầu"}})),
        _turn(counter, ("finish_generation", {"uncovered_acs": [{"ac_id": "AC-1.8", "reason": "chỉ kiểm được ở giao diện"}, {"ac_id": "AC-3.5", "reason": "chỉ kiểm được ở giao diện"}],
                                              "waivers": [{"kind": "api", "target": target, "reason_code": "not_applicable", "reason": "note_id là chuỗi tự do nên không có đầu vào nào gây 422"}
                                                          for target in WAIVED],
                                              "self_review": {"gaps_fixed": 0, "notes": "ba ô 422 không thể đạt được qua HTTP"}})),
    ]
    return script


def dump(script: list[dict]) -> str:
    return json.dumps(script, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
