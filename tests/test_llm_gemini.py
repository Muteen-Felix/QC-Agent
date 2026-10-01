"""llm/client.py, provider Gemini (S1-09): chọn provider theo tiền tố model, generateContent + function calling ép buộc, backoff/giãn cách/model dự phòng.

Không có mạng thật và không ngủ thật: request đi qua httpx.MockTransport, `_sleep`/`_monotonic`/`_jitter` được thay bằng đồng hồ giả.
"""
import io
import json
from pathlib import Path

import httpx
import pytest

from qc_agent import logging_setup, settings
from qc_agent.core import egress
from qc_agent.groundtruth.generate import generate
from qc_agent.groundtruth.prd import parse_prd
from qc_agent.llm import client as llm
from qc_agent.llm.client import LLMError, ToolCall, Usage, call_tool, gemini_schema, provider_of

ROOT = Path(__file__).resolve().parent.parent
KEY = "AIzaFAKE-KEY-0123456789"
MARK_USER = "USERMARK-91c2"
MARK_BODY = "BODYMARK-55de"
PRIMARY = "gemini-3.6-flash"
FALLBACK = "gemini-3.8-flash"
LAST_RESORT = "gemini-2.5-flash"
SCHEMA = {
    "type": "object",
    "properties": {
        "workers": {"type": "array", "items": {"type": "string", "enum": ["semgrep", "gitleaks"]}, "minItems": 1, "maxItems": 2},
        "kind": {"const": "pick"},
        "note": {"type": "string", "maxLength": 20, "pattern": "^[a-z]+$"},
    },
    "required": ["workers", "kind", "note"],
    "additionalProperties": False,
}
GOOD = {"workers": ["semgrep"], "kind": "pick", "note": "ok"}


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds

    def monotonic(self):
        return self.now


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    for name in ("GEMINI_BASE_URL", "ANTHROPIC_BASE_URL", "QC_LLM_TIMEOUT_S", "QC_LLM_MAX_RETRIES", "QC_LLM_MIN_INTERVAL_S", "QC_LLM_FALLBACK_MODELS",
                 "QC_GEMINI_THINKING_LEVEL", "QC_GT_MODEL", "QC_SELECTOR_MODEL", "QC_LOG_FORMAT", "QC_LOG_LEVEL", "QC_JOB_ID", "QC_PROJECT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(llm, "_sleep", fake.sleep)
    monkeypatch.setattr(llm, "_monotonic", fake.monotonic)
    monkeypatch.setattr(llm, "_jitter", lambda: 0.0)
    monkeypatch.setattr(llm, "_last_request_at", {})
    return fake


@pytest.fixture
def logs():
    buffer = io.StringIO()
    logging_setup.configure(buffer)
    yield buffer
    logging_setup.configure(io.StringIO())


def log_records(buffer):
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def reply(args=None, *, name="pick_workers", finish="STOP", usage=None, parts_extra=(), model_version=PRIMARY):
    return {
        "candidates": [{"content": {"role": "model", "parts": [*parts_extra, {"functionCall": {"name": name, "args": GOOD if args is None else args}}]},
                        "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 3500, "cachedContentTokenCount": 3000, "candidatesTokenCount": 30, "thoughtsTokenCount": 90}
        if usage is None else usage,
        "modelVersion": model_version,
    }


def error(status, name, *, details=None, message=MARK_BODY):
    body = {"error": {"code": status, "message": message, "status": name}}
    if details is not None:
        body["error"]["details"] = details
    return status, body


def script(*steps, capture=None):
    """Mỗi phần tử: dict (200), (status, body) (lỗi) hoặc Exception (ném). Hết thì lặp lại phần tử cuối."""
    queue = list(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple):
            return httpx.Response(step[0], json=step[1])
        return httpx.Response(200, json=step)
    return httpx.MockTransport(handler)


def boom():
    def handler(request):
        raise AssertionError("không được có HTTP request nào")
    return httpx.MockTransport(handler)


def call(tmp_path, *, transport, model=PRIMARY, **kw):
    args = dict(purpose="gt-generate", model=model, system="system tĩnh", user=f"dữ liệu động {MARK_USER}", tool_name="pick_workers",
                tool_description="Chọn worker", input_schema=SCHEMA, egress_dir=tmp_path / "run", data_categories=["prd_text"], transport=transport)
    args.update(kw)
    return call_tool(**args)


def egress_lines(tmp_path):
    path = tmp_path / "run" / egress.LOG_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


RATE_LIMIT = error(429, "RESOURCE_EXHAUSTED")


# ---------------- chọn provider + request ----------------

def test_provider_is_chosen_by_model_prefix():
    assert provider_of("gemini-3.6-flash") == "gemini" and provider_of("Gemini-3.5-flash-lite") == "gemini"
    assert provider_of("claude-sonnet-5") == "anthropic" and provider_of("") == "anthropic"


def test_success_sends_the_documented_generate_content_request_and_parses_it(tmp_path, clock):
    seen: list[httpx.Request] = []
    result = call(tmp_path, transport=script(reply(), capture=seen))

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"https://generativelanguage.googleapis.com/v1beta/models/{PRIMARY}:generateContent"
    assert request.headers["x-goog-api-key"] == KEY and "anthropic-version" not in request.headers
    assert KEY not in str(request.url)   # khoá chỉ đi trong header
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "system tĩnh"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": f"dữ liệu động {MARK_USER}"}]}]
    (declaration,) = body["tools"][0]["functionDeclarations"]
    assert declaration["name"] == "pick_workers" and declaration["description"] == "Chọn worker"
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["pick_workers"]}}
    assert body["generationConfig"] == {"maxOutputTokens": 4096}   # không gửi temperature (Gemini 3 khuyến nghị mặc định) và không có thinkingConfig

    assert isinstance(result, ToolCall) and result.data == GOOD and result.model == PRIMARY and result.stop_reason == "tool_use"
    assert result.fallback_from is None
    # promptTokenCount đã gồm 3000 token cache; thinking (90) tính vào output
    assert result.usage == Usage(500, 120, 0, 3000) and result.usage.prompt_tokens == 3500


def test_gemini_never_touches_the_anthropic_key_or_endpoint(tmp_path, clock, monkeypatch):
    seen = []
    call(tmp_path, transport=script(reply(), capture=seen))
    assert seen[0].url.host == "generativelanguage.googleapis.com" and "sk-ant" not in json.dumps(dict(seen[0].headers))
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(LLMError) as caught:   # có ANTHROPIC_API_KEY nhưng model là Gemini: vẫn thiếu khoá
        call(tmp_path, transport=boom(), egress_dir=tmp_path / "second")
    assert caught.value.kind == "missing_key" and "GEMINI_API_KEY" in str(caught.value) and not (tmp_path / "second").exists()


def test_claude_models_still_use_the_messages_api(tmp_path, clock):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"model": "claude-sonnet-5", "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1},
                                         "content": [{"type": "tool_use", "id": "t", "name": "pick_workers", "input": GOOD}]})
    result = call(tmp_path, model="claude-sonnet-5", transport=httpx.MockTransport(handler))
    assert str(seen[0].url) == "https://api.anthropic.com/v1/messages" and result.data == GOOD


def test_base_url_and_thinking_level_come_from_the_environment(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("GEMINI_BASE_URL", "http://127.0.0.1:9911/")
    monkeypatch.setenv("QC_GEMINI_THINKING_LEVEL", "low")
    seen = []
    call(tmp_path, transport=script(reply(), capture=seen))
    assert str(seen[0].url) == f"http://127.0.0.1:9911/v1beta/models/{PRIMARY}:generateContent"
    assert json.loads(seen[0].content)["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert egress_lines(tmp_path)[0]["target_host"] == "127.0.0.1:9911"
    monkeypatch.setenv("GEMINI_BASE_URL", "ftp://example.test")
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=boom())
    assert caught.value.kind == "bad_request"


def test_thought_parts_are_ignored_and_the_function_call_is_found(tmp_path, clock):
    result = call(tmp_path, transport=script(reply(parts_extra=[{"text": "đang nghĩ…", "thought": True}])))
    assert result.data == GOOD


@pytest.mark.parametrize("bad", ["a/b", "x?y=1", "m:generate", "a b", "../x"])
def test_model_id_cannot_smuggle_url_syntax(tmp_path, clock, bad):
    with pytest.raises(ValueError):
        call(tmp_path, model=f"gemini-{bad}", transport=boom())
    assert egress_lines(tmp_path) == []


# ---------------- schema gửi đi ----------------

def test_gemini_schema_drops_risky_keywords_turns_const_into_enum_and_never_mutates():
    before = json.dumps(SCHEMA, sort_keys=True)
    wire = gemini_schema(SCHEMA)
    assert json.dumps(SCHEMA, sort_keys=True) == before
    text = json.dumps(wire)
    for keyword in ("additionalProperties", "maxLength", "pattern"):
        assert f'"{keyword}"' not in text
    assert wire["properties"]["kind"] == {"enum": ["pick"]}
    assert wire["properties"]["workers"]["maxItems"] == 2 and wire["properties"]["workers"]["minItems"] == 1   # Gemini hỗ trợ min/maxItems
    assert wire["properties"]["workers"]["items"]["enum"] == ["semgrep", "gitleaks"] and wire["required"] == ["workers", "kind", "note"]


def test_gemini_schema_keeps_property_names_that_look_like_keywords():
    wire = gemini_schema({"type": "object", "properties": {"pattern": {"type": "string"}, "additionalProperties": {"type": "string", "enum": ["pattern"]}}})
    assert set(wire["properties"]) == {"pattern", "additionalProperties"} and wire["properties"]["additionalProperties"]["enum"] == ["pattern"]


def test_the_client_still_enforces_the_full_schema_that_gemini_never_saw(tmp_path, clock):
    for bad in ({**GOOD, "note": "x" * 50}, {**GOOD, "note": "UPPER"}, {**GOOD, "extra": 1}, {**GOOD, "kind": "other"}, {"workers": [], "kind": "pick", "note": "ok"},
                {**GOOD, "workers": ["semgrep", "gitleaks", "semgrep"]}, {**GOOD, "workers": ["nmap"]}):
        with pytest.raises(LLMError) as caught:
            call(tmp_path, transport=script(reply(bad)))
        assert caught.value.kind == "bad_output" and "schema" in str(caught.value) and "xxxx" not in str(caught.value)


# ---------------- backoff / giãn cách / dự phòng ----------------

def test_429_backs_off_exponentially_then_succeeds_and_every_attempt_is_an_egress_line(tmp_path, clock):
    seen = []
    result = call(tmp_path, transport=script(RATE_LIMIT, RATE_LIMIT, reply(), capture=seen))
    assert result.data == GOOD and len(seen) == 3
    assert clock.sleeps == [2.0, 4.0]   # 2·2^0, 2·2^1 (jitter giả = 0)
    lines = egress_lines(tmp_path)
    assert [line["attempt"] for line in lines] == [1, 2, 3] and all(line["decision"]["action"] == "allow" for line in lines)
    assert KEY not in json.dumps(lines) and MARK_USER not in json.dumps(lines)


def test_backoff_is_capped_and_jittered(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(llm, "_jitter", lambda: 0.5)
    with pytest.raises(LLMError):
        call(tmp_path, max_retries=6, transport=script(error(503, "UNAVAILABLE")))
    assert clock.sleeps == [2.5, 4.5, 8.5, 16.5, 32.5, 60.5]   # trần 60s + jitter


def test_retry_delay_from_the_server_is_honoured(tmp_path, clock):
    info = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}]
    call(tmp_path, transport=script(error(429, "RESOURCE_EXHAUSTED", details=info), reply()))
    assert clock.sleeps == [12.0]


def test_retry_after_header_is_honoured(tmp_path, clock):
    def handler(request, _state=[0]):
        _state[0] += 1
        if _state[0] == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
        return httpx.Response(200, json=reply())
    call(tmp_path, transport=httpx.MockTransport(handler))
    assert clock.sleeps == [7.0]


def test_exhausted_retries_raise_unavailable_with_no_content_and_leave_one_egress_line_per_attempt(tmp_path, clock, logs):
    seen = []
    with pytest.raises(LLMError) as caught:
        call(tmp_path, max_retries=2, transport=script(error(429, "RESOURCE_EXHAUSTED", message=f"{MARK_BODY} {KEY}"), capture=seen))
    assert caught.value.kind == "unavailable" and "429" in str(caught.value) and "RESOURCE_EXHAUSTED" in str(caught.value)
    assert len(seen) == 3 and clock.sleeps == [2.0, 4.0] and len(egress_lines(tmp_path)) == 3
    dump = str(caught.value) + logs.getvalue() + json.dumps(egress_lines(tmp_path))
    assert all(secret not in dump for secret in (KEY, MARK_BODY, MARK_USER))
    events = [r["event"] for r in log_records(logs)]
    assert events.count("llm.retry") == 2 and events.count("llm.call") == 1   # `llm.call` chỉ ghi kết quả cuối
    (final,) = [r for r in log_records(logs) if r["event"] == "llm.call"]
    assert final["kind"] == "unavailable" and final["model"] == PRIMARY


def test_max_retries_zero_means_a_single_request_and_the_setting_is_the_default(tmp_path, clock, monkeypatch):
    seen = []
    with pytest.raises(LLMError):
        call(tmp_path, max_retries=0, transport=script(RATE_LIMIT, capture=seen))
    assert len(seen) == 1 and clock.sleeps == []
    monkeypatch.setenv("QC_LLM_MAX_RETRIES", "1")
    seen.clear()
    with pytest.raises(LLMError):
        call(tmp_path, transport=script(RATE_LIMIT, capture=seen))
    assert len(seen) == 2 and settings.get().llm_max_retries == 1
    monkeypatch.delenv("QC_LLM_MAX_RETRIES")
    assert settings.get().llm_max_retries == 5
    with pytest.raises(ValueError):
        call(tmp_path, max_retries=-1, transport=boom())


@pytest.mark.parametrize("status,name", [(400, "INVALID_ARGUMENT"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
def test_client_errors_are_not_retried(tmp_path, clock, status, name):
    seen = []
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=script(error(status, name), capture=seen))
    assert caught.value.kind == "bad_request" and name in str(caught.value) and len(seen) == 1 and clock.sleeps == []


def test_5xx_and_network_errors_are_retried_but_timeouts_are_not(tmp_path, clock):
    assert call(tmp_path, transport=script(error(503, "UNAVAILABLE"), httpx.ConnectError(f"boom {KEY}"), reply())).data == GOOD
    assert clock.sleeps == [2.0, 4.0]
    seen = []
    with pytest.raises(LLMError) as caught:
        call(tmp_path, timeout_s=3, transport=script(httpx.ReadTimeout(f"{MARK_BODY}"), capture=seen))
    assert caught.value.kind == "timeout" and len(seen) == 1 and MARK_BODY not in str(caught.value)


def test_daily_quota_is_not_waited_out_and_falls_back_to_the_next_model(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", f"{FALLBACK}, {LAST_RESORT}")
    quota = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
              "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaMetric": "x"}]}]
    seen = []
    result = call(tmp_path, transport=script(error(429, "RESOURCE_EXHAUSTED", details=quota), reply(model_version=FALLBACK), capture=seen))
    assert clock.sleeps == []   # không ngủ vô ích khi quota ngày đã hết
    assert [request.url.path.split("/")[-1] for request in seen] == [f"{PRIMARY}:generateContent", f"{FALLBACK}:generateContent"]
    assert result.model == FALLBACK and result.fallback_from == PRIMARY and result.data == GOOD


def test_very_long_retry_hint_gives_up_on_that_model_instead_of_sleeping(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", FALLBACK)
    info = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3600s"}]
    result = call(tmp_path, transport=script(error(429, "RESOURCE_EXHAUSTED", details=info), reply()))
    assert clock.sleeps == [] and result.model == FALLBACK


def test_unknown_model_404_falls_back_and_exhausted_retries_fall_back(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", f"{FALLBACK},{LAST_RESORT}")
    seen = []
    result = call(tmp_path, max_retries=1, transport=script(error(404, "NOT_FOUND"), RATE_LIMIT, RATE_LIMIT, reply(), capture=seen))
    assert [r.url.path.split("/")[-1].split(":")[0] for r in seen] == [PRIMARY, FALLBACK, FALLBACK, LAST_RESORT]
    assert result.model == LAST_RESORT and result.fallback_from == PRIMARY
    assert [line["attempt"] for line in egress_lines(tmp_path)] == [1, 2, 3, 4]   # đánh số liên tục qua các model


def test_all_models_failing_raises_one_llm_error(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", FALLBACK)
    with pytest.raises(LLMError) as caught:
        call(tmp_path, max_retries=0, transport=script(error(404, "NOT_FOUND")))
    assert caught.value.kind == "bad_request" and "2 model" in str(caught.value)


def test_bad_output_and_refusal_do_not_trigger_fallback(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", FALLBACK)
    seen = []
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=script(reply(finish="SAFETY"), capture=seen))
    assert caught.value.kind == "refused" and len(seen) == 1


@pytest.mark.parametrize("value", ["claude-sonnet-5", "gemini-a/b", "gemini-x?y"])
def test_invalid_fallback_configuration_sends_nothing(tmp_path, clock, monkeypatch, value):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", value)
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=boom())
    assert caught.value.kind == "bad_request" and egress_lines(tmp_path) == []


def test_min_interval_spaces_requests_across_calls(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("QC_LLM_MIN_INTERVAL_S", "12")
    call(tmp_path, transport=script(reply()))
    assert clock.sleeps == []                      # lời gọi đầu tiên không phải chờ
    call(tmp_path, transport=script(reply()))
    assert clock.sleeps == [12.0]                  # lời gọi kế tiếp chờ đủ 12s kể từ request trước
    clock.now += 100
    call(tmp_path, transport=script(reply()))
    assert clock.sleeps == [12.0]                  # đã quá lâu: không chờ thêm


def test_pacing_is_off_by_default(tmp_path, clock):
    call(tmp_path, transport=script(reply()))
    call(tmp_path, transport=script(reply()))
    assert clock.sleeps == [] and settings.get().llm_min_interval_s == 0.0


def test_egress_deny_on_a_retry_stops_the_loop(tmp_path, clock):
    class DenySecond(egress.EgressPolicy):
        def __init__(self):
            self.calls = 0

        def decide(self, event):
            self.calls += 1
            return egress.Decision("allow" if self.calls == 1 else "deny", "test")
    seen = []
    with pytest.raises(LLMError) as caught:
        call(tmp_path, policy=DenySecond(), transport=script(RATE_LIMIT, reply(), capture=seen))
    assert caught.value.kind == "egress_denied" and len(seen) == 1


def test_egress_deny_sends_nothing(tmp_path, clock):
    class Deny(egress.EgressPolicy):
        def decide(self, event):
            return egress.Decision("deny", "test")
    with pytest.raises(LLMError) as caught:
        call(tmp_path, policy=Deny(), transport=boom())
    assert caught.value.kind == "egress_denied" and egress_lines(tmp_path)[0]["decision"]["action"] == "deny"


# ---------------- phân loại response ----------------

@pytest.mark.parametrize("finish", ["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"])
def test_blocked_finish_reasons_are_refused(tmp_path, clock, finish):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=script(reply(finish=finish)))
    assert caught.value.kind == "refused"


def test_prompt_feedback_block_is_refused_and_empty_candidates_are_bad_output(tmp_path, clock):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=script({"promptFeedback": {"blockReason": "SAFETY"}}))
    assert caught.value.kind == "refused"
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=script({"candidates": []}))
    assert caught.value.kind == "bad_output"


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "MALFORMED_FUNCTION_CALL"])
def test_truncated_or_malformed_calls_are_bad_output(tmp_path, clock, finish):
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=script(reply(finish=finish)))
    assert caught.value.kind == "bad_output"


def test_missing_wrong_or_multiple_function_calls_are_bad_output(tmp_path, clock):
    text_only = {"candidates": [{"content": {"parts": [{"text": f"xin lỗi {MARK_BODY}"}]}, "finishReason": "STOP"}]}
    two = reply()
    two["candidates"][0]["content"]["parts"].append({"functionCall": {"name": "pick_workers", "args": GOOD}})
    for payload in (text_only, reply(name="other"), two, reply("just a string"), [1, 2]):
        with pytest.raises(LLMError) as caught:
            call(tmp_path, transport=script(payload))
        assert caught.value.kind == "bad_output" and MARK_BODY not in str(caught.value)
    not_json = httpx.MockTransport(lambda request: httpx.Response(200, text="not json"))
    with pytest.raises(LLMError) as caught:
        call(tmp_path, transport=not_json)
    assert caught.value.kind == "bad_output"


def test_usage_tolerates_missing_and_garbage_fields(tmp_path, clock):
    assert call(tmp_path, transport=script(reply(usage={"promptTokenCount": 5}))).usage == Usage(5, 0, 0, 0)
    assert call(tmp_path, transport=script(reply(usage={"promptTokenCount": "x", "candidatesTokenCount": True}))).usage == Usage()
    assert call(tmp_path, transport=script(reply(usage={"promptTokenCount": 2, "cachedContentTokenCount": 9}))).usage.input_tokens == 0


def test_success_logs_only_identifiers_and_numbers(tmp_path, clock, logs):
    call(tmp_path, transport=script(reply({**GOOD, "note": "ok"})))
    (record,) = [r for r in log_records(logs) if r["event"] == "llm.call"]
    assert record["model"] == PRIMARY and record["stop_reason"] == "tool_use" and "kind" not in record
    assert (record["input_tokens"], record["output_tokens"], record["cache_read_input_tokens"]) == (500, 120, 3000)
    assert all(secret not in logs.getvalue() for secret in (KEY, MARK_USER, "system tĩnh"))


# ---------------- tích hợp với GT generator ----------------

def _gemini_reply_from_fixture():
    payload = json.loads((ROOT / "tests" / "fixtures" / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))
    (block,) = [b for b in payload["content"] if b["type"] == "tool_use"]
    return reply(block["input"], name=block["name"], usage={"promptTokenCount": 2000, "candidatesTokenCount": 800})


@pytest.fixture(scope="module")
def prd():
    return parse_prd(ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md", openapi_source=str(ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"))


def test_gt_generate_runs_end_to_end_through_gemini(tmp_path, clock, prd):
    result = generate(prd, model=PRIMARY, egress_dir=tmp_path / "e", transport=script(_gemini_reply_from_fixture()))
    assert result.catalog["generated_by"]["model"] == PRIMARY and result.usage == Usage(2000, 800, 0, 0)
    assert result.catalog["test_cases"] and all(tc["status"] == "draft" and tc["origin"] == "llm" for tc in result.catalog["test_cases"])
    (line,) = [json.loads(x) for x in (tmp_path / "e" / egress.LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert line["capability"] == "llm.gt-generate" and line["target_host"] == "generativelanguage.googleapis.com"


def test_gt_catalog_records_the_fallback_model_that_actually_answered(tmp_path, clock, prd, monkeypatch):
    monkeypatch.setenv("QC_LLM_FALLBACK_MODELS", FALLBACK)
    result = generate(prd, model=PRIMARY, egress_dir=tmp_path / "e", transport=script(error(404, "NOT_FOUND"), _gemini_reply_from_fixture()))
    assert result.catalog["generated_by"]["model"] == FALLBACK


def test_gt_repair_round_after_a_bad_gemini_answer_is_a_second_request(tmp_path, clock, prd):
    bad = reply({"summary": "x"}, name="emit_test_cases")
    seen = []
    result = generate(prd, model=PRIMARY, egress_dir=tmp_path / "e", transport=script(bad, _gemini_reply_from_fixture(), capture=seen))
    assert len(seen) == 2 and "<validation_error>" in json.loads(seen[1].content)["contents"][0]["parts"][0]["text"] and result.catalog["test_cases"]
