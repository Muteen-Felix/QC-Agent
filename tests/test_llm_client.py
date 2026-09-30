"""llm/client.py (S1-01): Messages API qua httpx, ép JSON bằng tool-use, egress trước khi gửi, lỗi/log không lộ nội dung.

Không có lời gọi mạng thật: mọi request đi qua httpx.MockTransport (hoặc transport nổ tung nếu bị gọi, để chứng minh KHÔNG có HTTP).
"""
import io
import json
import logging
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from qc_agent import logging_setup, settings
from qc_agent.core import egress
from qc_agent.llm import client as llm
from qc_agent.llm.client import LLMError, ToolCall, Usage, call_tool, wire_schema

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-ant-FAKE-KEY-0123456789"
MARK_SYSTEM = "SYSMARK-7f3a"
MARK_USER = "USERMARK-91c2"
MARK_BODY = "BODYMARK-55de"   # chuỗi nằm trong body response (lỗi hoặc thành công): không được lọt vào log/message
SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"

SCHEMA = {
    "type": "object",
    "properties": {
        "workers": {"type": "array", "items": {"type": "string", "enum": ["semgrep", "gitleaks", "schemathesis"]}, "minItems": 1, "maxItems": 3},
        "note": {"type": "string", "maxLength": 20},
    },
    "required": ["workers", "note"],
    "additionalProperties": False,
}
GOOD = {"workers": ["semgrep"], "note": "ok"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    for name in ("ANTHROPIC_BASE_URL", "QC_LLM_TIMEOUT_S", "QC_GT_MODEL", "QC_SELECTOR_MODEL", "QC_LOG_FORMAT", "QC_LOG_LEVEL", "QC_JOB_ID", "QC_PROJECT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def logs():
    buffer = io.StringIO()
    logging_setup.configure(buffer)
    yield buffer
    logging_setup.configure(io.StringIO())


def log_records(buffer):
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def message(tool_input=None, *, name="pick_workers", stop_reason="tool_use", usage=None, model=SONNET, extra_blocks=()):
    return {
        "id": "msg_01", "type": "message", "role": "assistant", "model": model, "stop_reason": stop_reason,
        "content": [{"type": "tool_use", "id": "toolu_01", "name": name, "input": GOOD if tool_input is None else tool_input}, *extra_blocks],
        "usage": {"input_tokens": 120, "output_tokens": 30, "cache_creation_input_tokens": 400, "cache_read_input_tokens": 3000} if usage is None else usage,
    }


def transport(response=None, *, status=200, capture=None, raises=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if raises is not None:
            raise raises
        return httpx.Response(status, json=message() if response is None else response)
    return httpx.MockTransport(handler)


def boom_transport():
    def handler(request):
        raise AssertionError("không được có HTTP request nào")
    return httpx.MockTransport(handler)


def call(tmp_path, *, model=SONNET, transport=None, schema=SCHEMA, **kw):
    args = dict(purpose="diff-select", model=model, system=f"system tĩnh {MARK_SYSTEM}", user=f"dữ liệu động {MARK_USER}",
                tool_name="pick_workers", tool_description="Chọn worker", input_schema=schema,
                egress_dir=tmp_path / "run", data_categories=["diff"], transport=transport)
    args.update(kw)
    return call_tool(**args)


def egress_lines(tmp_path):
    path = tmp_path / "run" / egress.LOG_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ---------------- đường thành công ----------------

def test_success_sends_the_documented_request_and_parses_everything(tmp_path):
    seen: list[httpx.Request] = []
    result = call(tmp_path, transport=transport(capture=seen))

    (request,) = seen
    assert request.method == "POST" and str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == KEY and request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["content-type"].startswith("application/json")
    body = json.loads(request.content)
    assert body["model"] == SONNET and body["max_tokens"] == 4096
    assert body["system"] == [{"type": "text", "text": f"system tĩnh {MARK_SYSTEM}"}]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": f"dữ liệu động {MARK_USER}"}]}]
    (tool,) = body["tools"]
    assert tool["name"] == "pick_workers" and tool["description"] == "Chọn worker" and tool["strict"] is True
    assert body["tool_choice"] == {"type": "tool", "name": "pick_workers"}
    assert "thinking" not in body

    assert isinstance(result, ToolCall) and result.data == GOOD and result.model == SONNET and result.stop_reason == "tool_use"
    assert result.usage == Usage(120, 30, 400, 3000) and result.usage.prompt_tokens == 120 + 400 + 3000
    assert result.duration_s >= 0


def test_temperature_is_omitted_for_sonnet_5_and_zero_for_haiku(tmp_path):
    sonnet, haiku = [], []
    call(tmp_path, model=SONNET, transport=transport(capture=sonnet))
    call(tmp_path, model=HAIKU, transport=transport(message(model=HAIKU), capture=haiku))
    assert "temperature" not in json.loads(sonnet[0].content)  # Sonnet 5 trả 400 nếu có tham số này
    assert json.loads(haiku[0].content)["temperature"] == 0
    call(tmp_path, model="claude-haiku-4-5", transport=transport(message(model="claude-haiku-4-5"), capture=haiku))  # alias không hậu tố ngày
    assert json.loads(haiku[1].content)["temperature"] == 0


def test_empty_system_is_omitted_and_max_tokens_is_forwarded(tmp_path):
    seen = []
    call(tmp_path, system="", max_tokens=777, transport=transport(capture=seen))
    body = json.loads(seen[0].content)
    assert "system" not in body and body["max_tokens"] == 777


def test_missing_usage_fields_count_as_zero(tmp_path):
    result = call(tmp_path, transport=transport(message(usage={"input_tokens": 5})))
    assert result.usage == Usage(5, 0, 0, 0) and result.usage.prompt_tokens == 5
    assert call(tmp_path, transport=transport(message(usage={"input_tokens": "x", "output_tokens": True}))).usage == Usage()
    no_usage = message()
    del no_usage["usage"]
    assert call(tmp_path, transport=transport(no_usage)).usage == Usage()


def test_response_model_is_reported_and_falls_back_to_the_requested_one(tmp_path):
    assert call(tmp_path, transport=transport(message(model="claude-sonnet-5-0001"))).model == "claude-sonnet-5-0001"
    payload = message()
    del payload["model"]
    assert call(tmp_path, transport=transport(payload)).model == SONNET


def test_base_url_comes_from_the_environment_and_is_used_for_request_and_egress(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9911/")
    seen = []
    call(tmp_path, transport=transport(capture=seen))
    assert str(seen[0].url) == "http://127.0.0.1:9911/v1/messages"
    assert egress_lines(tmp_path)[0]["target_host"] == "127.0.0.1:9911"


def test_timeout_comes_from_argument_then_settings(tmp_path, monkeypatch):
    seen = []
    call(tmp_path, timeout_s=5, transport=transport(capture=seen))
    assert seen[0].extensions["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}
    monkeypatch.setenv("QC_LLM_TIMEOUT_S", "7")
    call(tmp_path, transport=transport(capture=seen))
    assert seen[1].extensions["timeout"]["read"] == 7
    monkeypatch.delenv("QC_LLM_TIMEOUT_S")
    call(tmp_path, transport=transport(capture=seen))
    assert seen[2].extensions["timeout"]["read"] == 120.0


def test_settings_defaults_and_env_overrides(monkeypatch):
    cfg = settings.get()
    assert (cfg.gt_model, cfg.selector_model, cfg.llm_timeout_s) == ("claude-sonnet-5", "claude-haiku-4-5-20251001", 120.0)
    monkeypatch.setenv("QC_GT_MODEL", "m1")
    monkeypatch.setenv("QC_SELECTOR_MODEL", "m2")
    monkeypatch.setenv("QC_LLM_TIMEOUT_S", "9.5")
    cfg = settings.get()
    assert (cfg.gt_model, cfg.selector_model, cfg.llm_timeout_s) == ("m1", "m2", 9.5)
    assert KEY not in repr(cfg) and not hasattr(cfg, "anthropic_api_key")  # khoá không bao giờ vào Settings


# ---------------- egress ----------------

def test_egress_is_recorded_before_the_request_is_sent(tmp_path):
    def handler(request):
        lines = egress_lines(tmp_path)
        assert len(lines) == 1, "phải ghi egress TRƯỚC khi gửi"
        return httpx.Response(200, json=message())
    call(tmp_path, transport=httpx.MockTransport(handler))
    (line,) = egress_lines(tmp_path)
    assert line["worker"] == "qc-agent-diff-select" and line["task_id"] == "diff-select" and line["capability"] == "llm.diff-select"
    assert line["categories"] == ["diff"] and line["target_host"] == "api.anthropic.com" and line["decision"]["action"] == "allow"
    assert KEY not in json.dumps(line) and MARK_USER not in json.dumps(line)  # chỉ khai loại dữ liệu và host, không có nội dung


class Deny(egress.EgressPolicy):
    def __init__(self, action="deny"):
        self.action = action

    def decide(self, event):
        return egress.Decision(self.action, "test")


@pytest.mark.parametrize("action", ["deny", "mask"])
def test_non_allow_egress_decision_sends_nothing_but_leaves_a_record(tmp_path, action):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, policy=Deny(action), transport=boom_transport())
    assert caught.value.kind == "egress_denied"
    (line,) = egress_lines(tmp_path)
    assert line["decision"]["action"] == action


def test_missing_key_sends_nothing_and_records_no_egress(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=boom_transport())
    assert caught.value.kind == "missing_key"
    assert egress_lines(tmp_path) == []  # chưa có gì rời máy, nên không có bản ghi
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=boom_transport())
    assert caught.value.kind == "missing_key"


def test_invalid_base_url_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "ftp://example.test")
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=boom_transport())
    assert caught.value.kind == "bad_request" and egress_lines(tmp_path) == []


# ---------------- phân loại lỗi ----------------

@pytest.mark.parametrize("status,kind", [(429, "unavailable"), (500, "unavailable"), (529, "unavailable"), (503, "unavailable"),
                                         (400, "bad_request"), (401, "bad_request"), (403, "bad_request"), (404, "bad_request")])
def test_http_status_maps_to_kind(tmp_path, status, kind):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport({"type": "error", "error": {"type": "api_error", "message": MARK_BODY}}, status=status))
    assert caught.value.kind == kind and f"HTTP {status}" in str(caught.value)


def test_known_error_type_is_named_but_the_error_message_never_is(tmp_path):
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": f"{MARK_BODY} {KEY} {MARK_USER}"}}
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(body, status=429))
    text = str(caught.value)
    assert "rate_limit_error" in text
    assert all(secret not in text for secret in (MARK_BODY, KEY, MARK_USER, MARK_SYSTEM))
    body["error"]["type"] = f"weird-{MARK_BODY}"  # type lạ (không thuộc tập đóng) cũng không được lọt vào message
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(body, status=400))
    assert MARK_BODY not in str(caught.value)


def test_non_json_error_body_is_still_classified(tmp_path):
    handler = httpx.MockTransport(lambda request: httpx.Response(502, text=f"<html>{MARK_BODY}</html>"))
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=handler)
    assert caught.value.kind == "unavailable" and MARK_BODY not in str(caught.value)


def test_timeout_is_classified(tmp_path):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, timeout_s=3, transport=transport(raises=httpx.ReadTimeout(f"timed out {MARK_BODY}")))
    assert caught.value.kind == "timeout" and MARK_BODY not in str(caught.value)


@pytest.mark.parametrize("failure", [httpx.ConnectError(f"boom https://x.test/?key={KEY}"), httpx.RemoteProtocolError("bad framing")])
def test_network_errors_are_unavailable_and_never_echo_the_exception_text(tmp_path, failure):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(raises=failure))
    assert caught.value.kind == "unavailable" and KEY not in str(caught.value) and "x.test" not in str(caught.value)


def test_refusal_is_refused_even_if_a_tool_use_block_is_present(tmp_path):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(message(stop_reason="refusal")))
    assert caught.value.kind == "refused"


def test_max_tokens_is_bad_output(tmp_path):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(message(stop_reason="max_tokens")))
    assert caught.value.kind == "bad_output" and "max_tokens" in str(caught.value)


def test_no_tool_use_block_is_bad_output(tmp_path):
    text_only = message()
    text_only["content"] = [{"type": "text", "text": f"tôi xin lỗi {MARK_BODY}"}]
    text_only["stop_reason"] = "end_turn"
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(text_only))
    assert caught.value.kind == "bad_output" and MARK_BODY not in str(caught.value)
    empty = message()
    del empty["content"]
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(empty))
    assert caught.value.kind == "bad_output"


def test_wrong_tool_name_is_bad_output(tmp_path):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(message(name="other_tool")))
    assert caught.value.kind == "bad_output"


def test_more_than_one_tool_use_is_bad_output(tmp_path):
    second = {"type": "tool_use", "id": "toolu_02", "name": "pick_workers", "input": GOOD}
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(message(extra_blocks=[second])))
    assert caught.value.kind == "bad_output"


@pytest.mark.parametrize("bad_input", [
    {"workers": ["semgrep"]},                                                     # thiếu required
    {"workers": ["nmap"], "note": "ok"},                                          # ngoài enum allowlist
    {"workers": ["semgrep"], "note": "ok", "extra": 1},                           # thừa khoá
    {"workers": [], "note": "ok"},                                                # minItems
    {"workers": ["semgrep", "gitleaks", "schemathesis", "semgrep"], "note": "x"},  # maxItems (API không chặn được)
    {"workers": "semgrep", "note": "ok"},                                         # sai kiểu
])
def test_input_violating_the_full_schema_is_bad_output(tmp_path, bad_input):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(message(bad_input)))
    assert caught.value.kind == "bad_output" and "schema" in str(caught.value)


def test_non_object_input_or_payload_is_bad_output(tmp_path):
    for payload in (message("just a string"), [1, 2], "plain"):
        with pytest.raises(LLMError) as caught:
            call(tmp_path, transport=transport(payload))
        assert caught.value.kind == "bad_output"
    not_json = httpx.MockTransport(lambda request: httpx.Response(200, text="not json at all"))
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=not_json)
    assert caught.value.kind == "bad_output"


def test_max_length_is_dropped_from_the_wire_schema_but_still_enforced_by_the_client(tmp_path):
    seen = []
    too_long = {"workers": ["semgrep"], "note": "x" * 50}   # vi phạm maxLength 20: API không chặn, client phải chặn
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=transport(message(too_long), capture=seen))
    assert caught.value.kind == "bad_output" and "note" in str(caught.value) and "maxLength" in str(caught.value)
    assert "x" * 50 not in str(caught.value)  # message chỉ nêu vị trí + từ khoá, không lặp lại giá trị LLM sinh ra
    sent = json.loads(seen[0].content)["tools"][0]["input_schema"]
    assert "maxLength" not in json.dumps(sent) and "maxItems" not in json.dumps(sent) and sent["additionalProperties"] is False
    assert SCHEMA["properties"]["note"]["maxLength"] == 20  # schema gốc của caller không bị sửa


def test_llm_error_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        LLMError("nope")
    assert set(llm.KINDS) == {"missing_key", "egress_denied", "timeout", "unavailable", "bad_request", "bad_output", "refused"}


# ---------------- wire_schema ----------------

def test_wire_schema_drops_unsupported_keywords_at_every_depth_and_never_mutates_the_input():
    schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 0, "maximum": 9, "exclusiveMinimum": -1, "exclusiveMaximum": 10, "multipleOf": 1},
            "s": {"type": "string", "minLength": 1, "maxLength": 5, "pattern": "^a"},
            "list": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "string", "maxLength": 3}}}, "minItems": 2, "maxItems": 4, "uniqueItems": True},
            "one": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "either": {"anyOf": [{"type": "string", "maxLength": 2}, {"type": "null"}]},
        },
        "$defs": {"d": {"type": "object", "properties": {"x": {"type": "number", "minimum": 1}}}},
    }
    before = json.dumps(schema, sort_keys=True)
    wire = wire_schema(schema)
    assert json.dumps(schema, sort_keys=True) == before
    text = json.dumps(wire)
    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "maxItems", "uniqueItems"):
        assert f'"{keyword}"' not in text
    assert wire["properties"]["list"].get("minItems") is None            # minItems > 1 không hỗ trợ
    assert wire["properties"]["one"]["minItems"] == 1                    # minItems 0/1 được giữ
    assert wire["properties"]["s"]["pattern"] == "^a"                    # từ khoá còn hỗ trợ được giữ
    assert wire["additionalProperties"] is False
    assert wire["properties"]["list"]["items"]["additionalProperties"] is False   # object lồng trong mảng
    assert wire["$defs"]["d"]["additionalProperties"] is False
    assert wire["properties"]["either"]["anyOf"][0] == {"type": "string"}


def test_wire_schema_keeps_property_names_and_enum_values_that_look_like_keywords():
    schema = {"type": "object", "properties": {"minimum": {"type": "integer"}, "maxLength": {"type": "string", "enum": ["maxLength", "minimum"]}},
              "required": ["minimum"]}
    wire = wire_schema(schema)
    assert set(wire["properties"]) == {"minimum", "maxLength"} and wire["properties"]["maxLength"]["enum"] == ["maxLength", "minimum"]
    assert wire["required"] == ["minimum"]


def test_wire_schema_rejects_open_objects():
    with pytest.raises(ValueError, match="additionalProperties"):
        wire_schema({"type": "object", "properties": {}, "additionalProperties": {"type": "string"}})
    with pytest.raises(ValueError):
        wire_schema({"type": "object", "additionalProperties": True})


@pytest.mark.parametrize("free_form", [
    {"type": "object"},                                           # map tự do trần: trước đây bị siết âm thầm thành "chỉ được {}"
    {"type": ["object", "null"]},
    {"type": "object", "patternProperties": {"^x": {"type": "string"}}},
    {"type": "object", "properties": {"inner": {"type": "object"}}},   # tự do ở độ sâu bất kỳ
    {"type": "array", "items": {"type": "object", "description": "một map"}},
])
def test_wire_schema_rejects_free_form_objects_instead_of_silently_closing_them(free_form):
    with pytest.raises(ValueError):
        wire_schema(free_form)


def test_wire_schema_accepts_explicitly_closed_and_property_bearing_objects():
    assert wire_schema({"type": "object", "additionalProperties": False}) == {"type": "object", "additionalProperties": False}
    assert wire_schema({"type": "object", "properties": {}})["additionalProperties"] is False   # đóng rỗng có chủ đích
    assert wire_schema({"properties": {"a": {"type": "string"}}})["additionalProperties"] is False


# ---------------- sai cách dùng ----------------

@pytest.mark.parametrize("override", [
    {"purpose": "Bad Purpose"}, {"purpose": ""}, {"tool_name": "bad name"}, {"tool_name": ""}, {"user": "   "}, {"model": ""},
    {"max_tokens": 0}, {"max_tokens": True}, {"input_schema": {"type": "not-a-type"}},
])
def test_programmer_errors_raise_value_error_before_anything_is_sent(tmp_path, override):
    with pytest.raises(ValueError):
        call(tmp_path, transport=boom_transport(), **override)
    assert egress_lines(tmp_path) == []


# ---------------- log: chỉ định danh + số token ----------------

def test_success_logs_one_llm_call_event_with_only_identifiers_and_numbers(tmp_path, logs):
    call(tmp_path, transport=transport(message({"workers": ["semgrep"], "note": MARK_BODY})))
    (record,) = [r for r in log_records(logs) if r["event"] == "llm.call"]
    assert record["level"] == "INFO" and record["logger"] == "qc_agent.llm"
    assert record["purpose"] == "diff-select" and record["model"] == SONNET and record["stop_reason"] == "tool_use" and "kind" not in record
    assert (record["input_tokens"], record["output_tokens"], record["cache_creation_input_tokens"], record["cache_read_input_tokens"]) == (120, 30, 400, 3000)
    assert isinstance(record["duration_s"], float)
    assert set(record) == {"ts", "level", "logger", "event", "purpose", "model", "stop_reason", "input_tokens", "output_tokens",
                           "cache_creation_input_tokens", "cache_read_input_tokens", "duration_s"}
    dump = logs.getvalue()
    assert all(secret not in dump for secret in (KEY, MARK_SYSTEM, MARK_USER, MARK_BODY))


@pytest.mark.parametrize("scenario", ["timeout", "http429", "refusal", "max_tokens", "bad_schema", "no_tool", "network"])
def test_no_prompt_response_or_key_ever_reaches_the_log_or_the_error_message(tmp_path, logs, scenario):
    tainted = {"workers": ["semgrep"], "note": MARK_BODY * 3}
    cases = {
        "timeout": transport(raises=httpx.ReadTimeout(f"{MARK_BODY} {KEY}")),
        "http429": transport({"type": "error", "error": {"type": "rate_limit_error", "message": f"{MARK_BODY} {MARK_USER} {KEY}"}}, status=429),
        "refusal": transport(message(tainted, stop_reason="refusal")),
        "max_tokens": transport(message(tainted, stop_reason="max_tokens")),
        "bad_schema": transport(message(tainted)),   # note quá dài -> vi phạm maxLength
        "no_tool": transport({**message(), "content": [{"type": "text", "text": f"{MARK_BODY} {MARK_USER}"}]}),
        "network": transport(raises=httpx.ConnectError(f"{MARK_BODY} {KEY}")),
    }
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=cases[scenario])
    haystack = str(caught.value) + logs.getvalue() + json.dumps(egress_lines(tmp_path))
    for secret in (KEY, MARK_SYSTEM, MARK_USER, MARK_BODY):
        assert secret not in haystack
    (record,) = [r for r in log_records(logs) if r["event"] == "llm.call"]
    assert record["level"] == "WARNING" and record["kind"] == caught.value.kind and record["purpose"] == "diff-select"


def test_failed_call_without_response_logs_zero_tokens(tmp_path, logs):
    with pytest.raises(LLMError):
        call(tmp_path, transport=transport({"error": {"type": "api_error"}}, status=500))
    (record,) = [r for r in log_records(logs) if r["event"] == "llm.call"]
    assert record["kind"] == "unavailable" and record["input_tokens"] == record["output_tokens"] == 0 and "stop_reason" not in record


def test_refused_call_still_logs_the_token_usage_for_cost_tracking(tmp_path, logs):
    with pytest.raises(LLMError):
        call(tmp_path, transport=transport(message(stop_reason="refusal", usage={"input_tokens": 11, "output_tokens": 2})))
    (record,) = [r for r in log_records(logs) if r["event"] == "llm.call"]
    assert record["kind"] == "refused" and record["stop_reason"] == "refusal" and (record["input_tokens"], record["output_tokens"]) == (11, 2)


# ---------------- core/ không kéo llm vào ----------------

def test_importing_core_cli_and_engine_never_pulls_the_llm_package_in():
    code = ("import sys; import qc_agent.core.cli, qc_agent.core.engine; "
            "bad = sorted(m for m in sys.modules if m == 'qc_agent.llm' or m.startswith('qc_agent.llm.')); "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_llm_package_itself_can_be_imported():
    done = subprocess.run([sys.executable, "-c", "import qc_agent.llm.client as c; print(c.KINDS[0])"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "missing_key", done.stderr
