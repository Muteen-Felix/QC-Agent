"""llm/agent_loop.py: vòng lặp agent nhiều lượt cho Claude. Không có mạng thật: response soạn sẵn qua httpx.MockTransport (tests/agentkit.py).

Các tính chất quan trọng nhất (mỗi cái có test riêng): lịch sử chỉ được nối thêm (preserved thinking), tool song song trả về trong MỘT message,
egress ghi trước MỖI request và `source_code` chỉ xuất hiện sau lần đọc đầu tiên, không có nội dung nào lọt vào log/lỗi, không tự retry.
"""
import json
import logging

import httpx
import pytest

from qc_agent.core import egress
from qc_agent.llm import agent_loop as al
from qc_agent.llm import client
from tests.agentkit import SIGNATURE, end_turn_msg, max_tokens_msg, message, refusal_msg, scripted, tool_use_msg, usage

KEY = "sk-ant-FAKE-KEY-0123456789"
SENTINEL = "SENTINEL-FILE-CONTENT-do-not-log"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {"path": {"type": "string", "maxLength": 20}}}
FINISH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["note"], "properties": {"note": {"type": "string"}}}


class Kit:
    """Tool `read` (trả nội dung chứa SENTINEL, loại source_code) và tool kết thúc `finish`, ghi lại mọi lần gọi."""

    def __init__(self, *, accept_finish_after=0, boom=False):
        self.read_calls, self.finish_calls, self.accept_finish_after, self.boom = [], [], accept_finish_after, boom

    def read(self, data):
        if self.boom:
            raise RuntimeError(SENTINEL)
        self.read_calls.append(data["path"])
        return al.ToolOutcome(f"<file>{SENTINEL}</file>", categories=frozenset({"source_code"}))

    def finish(self, data):
        self.finish_calls.append(data["note"])
        if len(self.finish_calls) <= self.accept_finish_after:
            return al.ToolOutcome("còn thiếu: AC-1", is_error=True)
        return al.ToolOutcome("đã nhận", stop=True)

    @property
    def tools(self):
        return [al.AgentTool("read", "read a file", READ_SCHEMA, self.read), al.AgentTool("finish", "finish", FINISH_SCHEMA, self.finish)]


def run(tmp_path, script, kit=None, **kwargs):
    kit = kit or Kit()
    transport, requests = scripted(script)
    options = dict(purpose="gt-agent", model="claude-opus-5-5", system="SYSTEM", first_user="FIRST", tools=kit.tools, finish_tool="finish",
                   egress_dir=tmp_path / "egress", base_categories=["prd_text"], transport=transport)
    options.update(kwargs)
    return al.run_agent(**options), requests, kit


def egress_lines(tmp_path):
    return [json.loads(line) for line in (tmp_path / "egress" / "egress.jsonl").read_text(encoding="utf-8").splitlines()]


READ_THEN_FINISH = [tool_use_msg(("t1", "read", {"path": "a.py"})), tool_use_msg(("t2", "finish", {"note": "ok"}))]


# ---------------- hình dạng request ----------------

def test_request_shape_matches_what_opus_5_5_accepts(tmp_path):
    result, requests, _ = run(tmp_path, READ_THEN_FINISH)
    body, headers = requests[0].body, requests[0].headers
    assert result.stop == "finished" and result.turns == 2 and len(requests) == 2
    assert requests[0].url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == KEY and headers["anthropic-version"] == "2023-06-01" and headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 32000
    assert body["tool_choice"] == {"type": "auto"}                                   # forced tool_choice bị Opus 5.5 từ chối (400)
    assert not {"thinking", "temperature", "top_p", "top_k"} & set(body)
    assert body["output_config"] == {"effort": "high"} and body["fallbacks"] == "default"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert all(tool["strict"] is True for tool in body["tools"])
    assert [t["name"] for t in body["tools"]] == ["read", "finish"] and "cache_control" in body["tools"][-1] and "cache_control" not in body["tools"][0]
    assert "maxLength" not in json.dumps(body["tools"][0]["input_schema"]) and body["tools"][0]["input_schema"]["additionalProperties"] is False   # wire_schema
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "FIRST"}]}]


def test_fallbacks_can_be_turned_off_and_effort_is_passed_through(tmp_path):
    _, requests, _ = run(tmp_path, READ_THEN_FINISH, fallbacks=False, effort="xhigh", max_tokens=1234)
    assert "fallbacks" not in requests[0].body and "anthropic-beta" not in requests[0].headers
    assert requests[0].body["output_config"] == {"effort": "xhigh"} and requests[0].body["max_tokens"] == 1234


def test_a_tool_can_opt_out_of_strict_and_is_still_validated_client_side(tmp_path):
    kit = Kit()
    loose = al.AgentTool("read", "read a file", READ_SCHEMA, kit.read, strict=False)       # schema quá lớn cho grammar strict của API
    script = [tool_use_msg(("t1", "read", {"path": "x" * 21})), tool_use_msg(("t2", "finish", {"note": "ok"}))]
    result, requests, _ = run(tmp_path, script, kit, tools=[loose, kit.tools[1]])
    tools = requests[0].body["tools"]
    assert "strict" not in tools[0] and tools[1]["strict"] is True and result.stop == "finished"
    assert kit.read_calls == []                                                             # maxLength vẫn chặn được: client validate bằng schema đầy đủ
    assert "path (maxLength)" in requests[1].messages[-1]["content"][0]["content"]


def test_the_tool_list_and_system_are_byte_identical_every_turn(tmp_path):
    _, requests, _ = run(tmp_path, READ_THEN_FINISH)
    assert requests[0].body["tools"] == requests[1].body["tools"] and requests[0].body["system"] == requests[1].body["system"]


# ---------------- lịch sử chỉ nối thêm ----------------

def test_history_is_append_only_and_assistant_content_is_returned_verbatim(tmp_path):
    script = [tool_use_msg(("t1", "read", {"path": "a.py"}), text="đang đọc"), tool_use_msg(("t2", "read", {"path": "b.py"})), tool_use_msg(("t3", "finish", {"note": "ok"}))]
    result, requests, _ = run(tmp_path, script)
    assert result.turns == 3
    for before, after in zip(requests, requests[1:]):
        assert after.messages[:len(before.messages)] == before.messages          # tiền tố giữ nguyên từng byte: preserved thinking hợp lệ
        assert len(after.messages) == len(before.messages) + 2                   # đúng một assistant + một user được thêm
    assistant = requests[1].messages[1]
    assert assistant["role"] == "assistant" and assistant["content"][0] == {"type": "thinking", "thinking": "", "signature": SIGNATURE}
    assert assistant["content"][1] == {"type": "text", "text": "đang đọc"} and assistant["content"][2]["type"] == "tool_use"


def test_parallel_tool_results_share_one_user_message_in_tool_use_order_before_text(tmp_path):
    script = [tool_use_msg(("a", "read", {"path": "1"}), ("b", "read", {"path": "2"}), ("c", "read", {"path": "3"})), tool_use_msg(("d", "finish", {"note": "ok"}))]
    _, requests, kit = run(tmp_path, script, budget=al.AgentBudget(max_turns=3, warn_at_turns_left=2))
    user = requests[1].messages[2]
    assert user["role"] == "user" and len(requests[1].messages) == 3
    assert [b["tool_use_id"] for b in user["content"][:3]] == ["a", "b", "c"] and all(b["type"] == "tool_result" for b in user["content"][:3])
    assert len(user["content"]) == 4 and user["content"][3]["type"] == "text" and "2 turn(s) left" in user["content"][3]["text"]   # lời nhắc đứng SAU mọi tool_result
    assert kit.read_calls == ["1", "2", "3"]


# ---------------- lỗi của tool trả về cho model, không làm hỏng vòng lặp ----------------

def test_tool_errors_go_back_to_the_model_as_is_error_results(tmp_path):
    script = [tool_use_msg(("u", "nope", {}), ("v", "read", {"path": "x" * 21}), ("w", "read", {}), ("x", "read", ["bad"])), tool_use_msg(("f", "finish", {"note": "ok"}))]
    _, requests, kit = run(tmp_path, script)
    results = {b["tool_use_id"]: b for b in requests[1].messages[2]["content"]}
    assert all(r.get("is_error") for r in results.values()) and kit.read_calls == []
    assert results["u"]["content"] == "tool không tồn tại"
    assert "path (maxLength)" in results["v"]["content"] and "x" * 21 not in results["v"]["content"]    # strict mode không chặn maxLength: client chặn
    assert "<gốc> (required)" in results["w"]["content"] and "phải là object" in results["x"]["content"]


def test_a_raising_handler_reports_only_the_exception_type(tmp_path):
    kit = Kit(boom=True)
    _, requests, _ = run(tmp_path, [tool_use_msg(("t", "read", {"path": "a"})), tool_use_msg(("f", "finish", {"note": "ok"}))], kit)
    result = requests[1].messages[2]["content"][0]
    assert result["is_error"] is True and result["content"] == "lỗi nội bộ của tool (RuntimeError)" and SENTINEL not in json.dumps(requests[1].body)


def test_a_rejected_finish_lets_the_model_continue_and_the_accepted_one_stops_without_another_request(tmp_path):
    kit = Kit(accept_finish_after=1)
    script = [tool_use_msg(("f1", "finish", {"note": "sớm"})), tool_use_msg(("f2", "finish", {"note": "đủ"}))]
    result, requests, _ = run(tmp_path, script, kit)
    assert kit.finish_calls == ["sớm", "đủ"] and result.stop == "finished" and len(requests) == 2   # không có request thứ ba sau khi finish được nhận
    rejected = requests[1].messages[2]["content"][0]
    assert rejected["is_error"] is True and rejected["content"] == "còn thiếu: AC-1"


def test_tools_after_an_accepted_finish_in_the_same_turn_do_not_run(tmp_path):
    script = [tool_use_msg(("f", "finish", {"note": "ok"}), ("r", "read", {"path": "late"}))]
    result, requests, kit = run(tmp_path, script)
    assert result.stop == "finished" and kit.read_calls == [] and len(requests) == 1


# ---------------- tự dừng và lỗi API ----------------

def test_end_turn_without_finish_is_nudged_once_then_continues(tmp_path):
    script = [end_turn_msg(), tool_use_msg(("f", "finish", {"note": "ok"}))]
    result, requests, _ = run(tmp_path, script)
    assert result.nudges == 1 and result.stop == "finished" and len(requests) == 2
    nudge = requests[1].messages[-1]
    assert nudge["role"] == "user" and "`finish`" in nudge["content"][0]["text"] and requests[1].messages[:2] == requests[0].messages + [requests[1].messages[1]]


def test_a_second_end_turn_without_finish_is_bad_output(tmp_path):
    with pytest.raises(client.LLMError) as error:
        run(tmp_path, [end_turn_msg(), end_turn_msg()])
    assert error.value.kind == "bad_output" and "finish" in str(error.value)


@pytest.mark.parametrize("script,kind", [
    ([refusal_msg()], "refused"),
    ([max_tokens_msg()], "bad_output"),
    ([message([{"type": "text", "text": "x"}], "tool_use")], "bad_output"),
    ([message("không phải list", "end_turn")], "bad_output"),
    (["không phải json"], "bad_output"),
    (['[1, 2]'], "bad_output"),
    ([429], "unavailable"), ([500], "unavailable"), ([529], "unavailable"),
    ([400], "bad_request"), ([401], "bad_request"),
    ([httpx.ReadTimeout("quá lâu")], "timeout"),
    ([httpx.ConnectError("https://user:pass@host/secret")], "unavailable"),
])
def test_api_failures_become_llm_errors_without_retry_or_content(tmp_path, script, kind):
    with pytest.raises(client.LLMError) as error:
        _, requests, _ = run(tmp_path, script)
    assert error.value.kind == kind
    assert "SECRET-PROMPT-CONTENT" not in str(error.value) and "pass" not in str(error.value) and "secret" not in str(error.value)


def test_a_failed_request_is_never_retried(tmp_path):
    transport, requests = scripted([429, tool_use_msg(("f", "finish", {"note": "ok"}))])
    with pytest.raises(client.LLMError):
        al.run_agent(purpose="gt-agent", model="claude-opus-5-5", system="S", first_user="U", tools=Kit().tools, finish_tool="finish",
                     egress_dir=tmp_path / "e", base_categories=[], transport=transport)
    assert len(requests) == 1


def test_missing_key_sends_and_records_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    transport, requests = scripted([])
    with pytest.raises(client.LLMError) as error:
        al.run_agent(purpose="gt-agent", model="claude-opus-5-5", system="S", first_user="U", tools=Kit().tools, finish_tool="finish",
                     egress_dir=tmp_path / "e", base_categories=[], transport=transport)
    assert error.value.kind == "missing_key" and requests == [] and not (tmp_path / "e").exists()


def test_base_url_must_be_http(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "file:///etc/passwd")
    with pytest.raises(client.LLMError) as error:
        run(tmp_path, [])
    assert error.value.kind == "bad_request"


# ---------------- egress ----------------

def test_egress_is_recorded_before_every_request_and_source_code_appears_only_after_the_first_read(tmp_path):
    seen_at_send: list[int] = []

    def handler(request):
        seen_at_send.append(len((tmp_path / "egress" / "egress.jsonl").read_text(encoding="utf-8").splitlines()))   # dòng egress đã có TRƯỚC khi request tới server
        return httpx.Response(200, json=[*READ_THEN_FINISH][len(seen_at_send) - 1])

    result, _, _ = run(tmp_path, [], transport=httpx.MockTransport(handler))
    lines = egress_lines(tmp_path)
    assert seen_at_send == [1, 2] and result.turns == 2
    assert [line["categories"] for line in lines] == [["prd_text"], ["prd_text", "source_code"]]   # request 1 chưa có file nào; request 2 mang nội dung file
    assert [line["attempt"] for line in lines] == [1, 2]
    assert {line["worker"] for line in lines} == {"qc-agent-gt-agent"} and {line["capability"] for line in lines} == {"llm.gt-agent"}
    assert {line["target_host"] for line in lines} == {"api.anthropic.com"} and SENTINEL not in json.dumps(lines)


def test_a_denying_policy_sends_nothing(tmp_path):
    class Deny(egress.EgressPolicy):
        def decide(self, event):
            return egress.Decision("deny", "test")

    with pytest.raises(client.LLMError) as error:
        _, requests, _ = run(tmp_path, READ_THEN_FINISH, policy=Deny())
    assert error.value.kind == "egress_denied"
    assert [line["decision"]["action"] for line in egress_lines(tmp_path)] == ["deny"]


def test_a_policy_that_denies_later_stops_the_loop_before_the_second_request(tmp_path):
    class DenySourceCode(egress.EgressPolicy):
        def decide(self, event):
            return egress.Decision("deny" if "source_code" in event["categories"] else "allow")

    transport, requests = scripted(READ_THEN_FINISH)
    with pytest.raises(client.LLMError) as error:
        al.run_agent(purpose="gt-agent", model="claude-opus-5-5", system="S", first_user="U", tools=Kit().tools, finish_tool="finish",
                     egress_dir=tmp_path / "e", base_categories=["prd_text"], policy=DenySourceCode(), transport=transport)
    assert error.value.kind == "egress_denied" and len(requests) == 1   # file đã đọc nhưng không rời máy


# ---------------- ngân sách ----------------

def test_turn_budget_stops_without_raising_and_without_an_extra_request(tmp_path):
    script = [tool_use_msg((f"t{i}", "read", {"path": f"f{i}"})) for i in range(5)]
    result, requests, kit = run(tmp_path, script, budget=al.AgentBudget(max_turns=2))
    assert result.stop == "budget_turns" and result.turns == 2 and len(requests) == 2 and kit.read_calls == ["f0", "f1"]   # tool của lượt cuối vẫn được chạy


def test_cost_budget_uses_the_price_table_and_unknown_models_have_no_cost_cap(tmp_path):
    big = usage(input_tokens=1_000_000, output_tokens=100_000)           # opus-5-5: 4 + 2 = $6
    script = [tool_use_msg(("t", "read", {"path": "a"}), usage_=big), tool_use_msg(("f", "finish", {"note": "ok"}))]
    result, requests, _ = run(tmp_path, script, budget=al.AgentBudget(max_cost_usd=5.0))
    assert result.stop == "budget_cost" and len(requests) == 1 and result.cost_usd_est == pytest.approx(6.0)
    script = [tool_use_msg(("t", "read", {"path": "a"}), usage_=big, model="some-future-model"), tool_use_msg(("f", "finish", {"note": "ok"}), model="some-future-model")]
    result, requests, _ = run(tmp_path / "x", script, budget=al.AgentBudget(max_cost_usd=0.01))
    assert result.stop == "finished" and result.cost_usd_est is None


def test_time_budget(tmp_path, monkeypatch):
    clock = iter([0.0, 0.0, 0.1, 0.1, 999.0, 999.0, 999.0, 999.0])
    monkeypatch.setattr(al, "_monotonic", lambda: next(clock))
    script = [tool_use_msg(("t", "read", {"path": "a"})), tool_use_msg(("f", "finish", {"note": "ok"}))]
    result, requests, _ = run(tmp_path, script, budget=al.AgentBudget(max_wall_s=60))
    assert result.stop == "budget_time" and len(requests) == 1


def test_a_notice_is_appended_when_few_turns_are_left_and_never_edits_older_messages(tmp_path):
    script = [tool_use_msg((f"t{i}", "read", {"path": f"f{i}"})) for i in range(4)]
    _, requests, _ = run(tmp_path, script, budget=al.AgentBudget(max_turns=4, warn_at_turns_left=2))
    texts = [b["text"] for req in requests for m in req.messages if m["role"] == "user" for b in m["content"] if b["type"] == "text" and "Budget notice" in b["text"]]
    assert any("2 turn(s) left" in t for t in texts) and any("1 turn(s) left" in t for t in texts) and all("`finish`" in t for t in texts)
    for before, after in zip(requests, requests[1:]):
        assert after.messages[:len(before.messages)] == before.messages


# ---------------- kết quả, log ----------------

def test_run_aggregates_usage_counts_tool_calls_and_reports_the_last_model(tmp_path):
    script = [tool_use_msg(("a", "read", {"path": "1"}), ("b", "nope", {}), usage_=usage(100, 10, 5, 1)),
              tool_use_msg(("f", "finish", {"note": "ok"}), model="claude-opus-5", usage_=usage(200, 20, 0, 6))]
    result, _, _ = run(tmp_path, script)
    assert result.usage == client.Usage(300, 30, 5, 7) and result.model == "claude-opus-5"   # model dự phòng đã trả lời lượt cuối
    assert result.tool_calls == {"<unknown>": 1, "finish": 1, "read": 1} and result.duration_s >= 0


def test_estimate_cost_matches_the_longest_model_prefix():
    one_million = client.Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert al.estimate_cost("claude-opus-5-5", one_million) == pytest.approx(4 + 20 + 5 + 0.2)       # ghi cache = 1,25 × đầu vào
    assert al.estimate_cost("claude-opus-5", one_million) == pytest.approx(5 + 25 + 6.25 + 0.5)       # không bị bắt bởi tiền tố của claude-opus-5-5
    assert al.estimate_cost("gemini-3.6-flash", one_million) is None


def test_logs_never_contain_prompt_file_content_or_the_key(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    run(tmp_path, [tool_use_msg(("t", "read", {"path": "secret/path.py"})), tool_use_msg(("f", "finish", {"note": "NOTE-CONTENT"}))])
    dump = "\n".join(f"{r.getMessage()} {getattr(r, 'qc_fields', '')}" for r in caplog.records)
    assert "llm.agent" in dump and "llm.call" in dump
    for forbidden in (SENTINEL, "FIRST", "SYSTEM", "secret/path.py", "NOTE-CONTENT", KEY, SIGNATURE):
        assert forbidden not in dump, forbidden


# ---------------- dùng sai là lỗi lập trình ----------------

@pytest.mark.parametrize("change", [
    {"effort": "ultra"}, {"purpose": "Bad Purpose"}, {"model": ""}, {"first_user": "  "}, {"system": ""}, {"max_tokens": 0}, {"finish_tool": "missing"},
    {"budget": al.AgentBudget(max_turns=0)}, {"budget": al.AgentBudget(max_cost_usd=0)}, {"budget": al.AgentBudget(max_wall_s=0)}, {"tools": []},
])
def test_programming_errors_raise_value_error_before_anything_is_sent(tmp_path, change):
    with pytest.raises(ValueError):
        run(tmp_path, [], **change)
    assert not (tmp_path / "egress").exists()


def test_duplicate_tool_names_bad_schemas_and_open_objects_are_rejected(tmp_path):
    kit = Kit()
    duplicate = [kit.tools[0], al.AgentTool("read", "again", READ_SCHEMA, kit.read), kit.tools[1]]
    broken = [al.AgentTool("read", "x", {"type": "nonsense"}, kit.read), kit.tools[1]]
    open_object = [al.AgentTool("read", "x", {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": True}, kit.read), kit.tools[1]]
    free_map = [al.AgentTool("read", "x", {"type": "object"}, kit.read), kit.tools[1]]
    for tools in (duplicate, broken, open_object, free_map):
        with pytest.raises(ValueError):
            run(tmp_path, [], tools=tools)
