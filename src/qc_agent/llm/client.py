"""Client LLM dùng chung (Claude Messages API + Gemini generateContent), chỉ qua `httpx` (quyết định #5: không thêm SDK). Mọi output của LLM bị ÉP vào JSON bằng tool-use.

Provider chọn theo TIỀN TỐ model: `gemini-*` -> Gemini (khoá `GEMINI_API_KEY`, tuỳ chọn `GEMINI_BASE_URL`), còn lại -> Claude (`ANTHROPIC_API_KEY`). Đổi provider = đổi
`QC_GT_MODEL` / `QC_SELECTOR_MODEL`, không có công tắc thứ hai. Gemini free tier chỉ có ~5-15 RPM nên riêng Gemini có:
  - giãn cách chủ động giữa hai request (`QC_LLM_MIN_INTERVAL_S`, mặc định 0 = tắt),
  - exponential backoff + jitter khi 429/5xx/lỗi mạng (`QC_LLM_MAX_RETRIES`, mặc định 5; tôn trọng `Retry-After` / `RetryInfo.retryDelay`),
  - chuyển sang model dự phòng (`QC_LLM_FALLBACK_MODELS`) khi model 404, hết hạn mức ngày, hoặc hết lượt retry (mỗi model có quota riêng),
  - mỗi request rời máy (kể cả lần retry) ghi MỘT dòng egress trước khi gửi.
  Claude giữ nguyên hành vi cũ: không tự retry. Gemini 3 khuyến nghị để `temperature` mặc định nên client không gửi `temperature`.

Ranh giới cứng (docs/core-rules.md):
  - `core/` không import module này ở top-level. Người dùng hợp lệ: `groundtruth/` (S1-04) và `selector/` (S2-04).
  - Ghi egress TRƯỚC khi gửi; policy khác `allow` thì KHÔNG có request nào (mẫu: scaffold/suggest.py: suggest_flows).
  - Prompt, response, rationale, khoá API không bao giờ vào log hay message của `LLMError` (chỉ định danh, số token, mã lỗi).
  - Không tự retry: Diff Agent có ngân sách thời gian chặt, GT generator có vòng "sửa một lần" riêng, nên caller tự quyết.
  - `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` đọc bằng os.environ LÚC GỌI, không đưa vào `settings.Settings` (repr của pydantic có thể lộ khoá).

Sự thật về API đã kiểm với tài liệu Claude API (2026-09), lệch so với plan ở chỗ đánh dấu (*):
  (*) `temperature`: `claude-sonnet-5` TRẢ 400 nếu có tham số này (đã bỏ sampling params). Chỉ gửi `temperature: 0` cho model thuộc `_SAMPLING_OK`
      (Haiku 4.5). Plan S1.1 ghi "temperature=0" cho mọi model là sai với Sonnet 5; tính tái lập dựa vào fixture và render tất định.
  (*) Không gửi `thinking` (Sonnet 5 mặc định chạy adaptive; `budget_tokens` trả 400).
  - Forced `tool_choice` ({"type":"tool"}) hợp lệ với Sonnet 5 và Haiku 4.5. `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1` TỪ CHỐI nó (400):
    đổi sang các model đó thì phải dùng `tool_choice: auto` + `strict` + kiểm có đúng một `tool_use`. Chưa cài đặt ở đây.
  - Strict tool schema: mọi object phải có `additionalProperties: false`; KHÔNG hỗ trợ minLength/maxLength/minimum/maximum/exclusive*/multipleOf,
    ràng buộc mảng phức tạp và schema đệ quy. `wire_schema` rút gọn schema để gửi; `call_tool` vẫn validate `input` nhận về theo schema ĐẦY ĐỦ
    bằng `jsonschema` (client là nguồn chân lý, API không chặn được maxLength).
  - `system` (tĩnh) tách khỏi `user` (động) để S4-03 gắn `cache_control` vào `system`: đừng đưa thời gian/id vào `system`.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import jsonschema

from qc_agent import settings
from qc_agent.core import egress
from qc_agent.logging_setup import event

log = logging.getLogger("qc_agent.llm")

KINDS = ("missing_key", "egress_denied", "timeout", "unavailable", "bad_request", "bad_output", "refused")
DEFAULT_BASE_URL = "https://api.anthropic.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
API_VERSION = "2023-06-01"
BACKOFF_BASE_S = 2.0        # lần retry thứ n chờ min(BACKOFF_CAP_S, BASE * 2**n) + jitter[0,1)s
BACKOFF_CAP_S = 60.0
MAX_HINT_WAIT_S = 90.0      # server bảo chờ lâu hơn mức này (thường là hết quota) thì bỏ model này thay vì ngủ
_sleep = time.sleep         # test thay thế để không ngủ thật
_monotonic = time.monotonic
_jitter = random.random
_last_request_at: dict[str, float] = {}   # provider -> thời điểm bắt đầu request gần nhất (cho giãn cách chủ động)
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")   # model nằm trong đường dẫn URL của Gemini: chặn `/`, `?`, `:`
_SAMPLING_OK = ("claude-haiku-4-5",)  # tiền tố model còn nhận `temperature`; Sonnet 5 trả 400 nếu có
_PURPOSE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
# `error.type` của API là tập đóng; chỉ những giá trị này được đưa vào message (không bao giờ đưa `error.message`, có thể chứa nội dung).
_ERROR_TYPES = frozenset({"invalid_request_error", "authentication_error", "permission_error", "not_found_error", "request_too_large",
                          "rate_limit_error", "api_error", "overloaded_error",
                          # `error.status` của Gemini (cũng là tập đóng)
                          "INVALID_ARGUMENT", "FAILED_PRECONDITION", "PERMISSION_DENIED", "NOT_FOUND", "RESOURCE_EXHAUSTED", "INTERNAL",
                          "UNAVAILABLE", "DEADLINE_EXCEEDED", "UNAUTHENTICATED"})
_GEMINI_REFUSED = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "LANGUAGE"})
# Từ khoá không đưa cho Gemini (client vẫn validate theo schema ĐẦY ĐỦ). `const` đổi thành enum 1 phần tử.
_GEMINI_DROP = frozenset({"additionalProperties", "patternProperties", "pattern", "$schema", "$id", "minLength", "maxLength", "multipleOf",
                          "exclusiveMinimum", "exclusiveMaximum", "uniqueItems", "minContains", "maxContains"})
# Từ khoá strict mode không hỗ trợ: bỏ khỏi schema gửi đi, client tự validate lại.
_UNSUPPORTED = frozenset({"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                          "maxItems", "uniqueItems", "minContains", "maxContains"})
_NAME_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions"})  # khoá con là TÊN, không phải từ khoá schema
_DATA = frozenset({"enum", "const", "default", "examples", "required"})               # giá trị là dữ liệu, không duyệt


class LLMError(RuntimeError):
    """`.kind` ∈ KINDS. Message chỉ chứa mã/định danh ngắn: KHÔNG có body response, URL, prompt hay khoá.

    Để báo cáo chi phí không bỏ sót (S4-03), lỗi mang theo những gì client biết về chi phí của lời gọi đã hỏng:
      - `usage`: token của response bị TỪ CHỐI (sai schema, `refused`, `max_tokens`); `None` khi không có response hoặc response không có usage.
      - `sent`: request đã rời máy chưa (`False` cho thiếu khoá, egress bị chặn, không kết nối được; `True` khi đã gửi, kể cả khi API trả lỗi HTTP).
      - `unknown_calls`: số request đã gửi mà KHÔNG có response (timeout đọc, đứt kết nối giữa chừng): server có thể đã tính phí, nên chi phí là CHƯA XÁC ĐỊNH,
        không phải 0. Lỗi HTTP có body lỗi của API (4xx/429/5xx) không tính vào đây: [Assumption, chưa kiểm chứng] API không tính phí request trả lỗi.
    """

    def __init__(self, kind: str, detail: str = "", *, usage: "Usage | None" = None, sent: bool = False, unknown_calls: int = 0):
        if kind not in KINDS:
            raise ValueError(f"LLMError.kind phải thuộc {KINDS}, nhận {kind!r}")
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.usage = usage
        self.sent = sent or usage is not None or unknown_calls > 0
        self.unknown_calls = unknown_calls
        self.duration_s: float | None = None   # thời gian client đã chờ lời gọi hỏng (đo phía client), nếu biết


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        """input_tokens chỉ là phần KHÔNG cache; tổng prompt = input + ghi cache + đọc cache."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


@dataclass(frozen=True)
class ToolCall:
    data: dict          # input của tool_use, ĐÃ validate theo schema đầy đủ
    usage: Usage
    model: str
    stop_reason: str
    duration_s: float
    fallback_from: str | None = None   # Gemini: model được yêu cầu ban đầu, khi `model` là model dự phòng đã trả lời
    unknown_calls: int = 0             # Gemini: request ĐÃ GỬI TRƯỚC lần thành công này mà không có response (retry sau lỗi mạng, model dự phòng): chi phí chưa xác định, KHÔNG nằm trong `usage`


def wire_schema(schema: dict) -> dict:
    """Schema gửi lên API (strict): bỏ từ khoá không hỗ trợ ở mọi độ sâu, ép `additionalProperties: false` cho mọi object. Không sửa `schema`.

    Object mở (`additionalProperties` khác false) là lỗi của người viết schema: strict mode không diễn đạt được, nên ném ValueError sớm
    thay vì âm thầm siết chặt. Muốn map thì dùng mảng `[{key, value}]` rồi đổi ở phía caller.
    """
    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key in _UNSUPPORTED or (key == "minItems" and value not in (0, 1)):
                continue
            if key in _NAME_MAPS and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}
            elif key in _DATA:
                out[key] = copy.deepcopy(value)
            else:
                out[key] = walk(value)
        kind = out.get("type")
        if kind == "object" or (isinstance(kind, list) and "object" in kind) or "properties" in out:
            explicit = out.get("additionalProperties")
            if explicit not in (None, False) or "patternProperties" in out:
                raise ValueError("strict tool schema không hỗ trợ object mở: additionalProperties phải là false")
            if "properties" not in out and explicit is None:
                # `{"type": "object"}` trần là map tự do: siết thành additionalProperties=false sẽ âm thầm biến nó thành "chỉ được {}".
                raise ValueError("strict tool schema không hỗ trợ object tự do (không khai properties); dùng mảng [{name, value}]")
            out["additionalProperties"] = False
        return out

    return walk(schema)


def _usage(raw) -> Usage:
    raw = raw if isinstance(raw, dict) else {}
    def number(name: str) -> int:
        value = raw.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    return Usage(number("input_tokens"), number("output_tokens"), number("cache_creation_input_tokens"), number("cache_read_input_tokens"))


def _never_sent(error: httpx.HTTPError) -> bool:
    """Lỗi xảy ra TRƯỚC khi request tới server (không kết nối được, hết chờ kết nối/hồ chứa): chắc chắn không bị tính phí."""
    return isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _transport_error(error: httpx.HTTPError, timeout: float) -> LLMError:
    """Timeout/lỗi mạng. Nếu request đã có thể tới server mà không có response thì chi phí CHƯA XÁC ĐỊNH (`unknown_calls=1`), không phải 0."""
    sent = not _never_sent(error)
    if isinstance(error, httpx.TimeoutException):
        return LLMError("timeout", f"quá {timeout:g}s", sent=sent, unknown_calls=int(sent))
    return LLMError("unavailable", f"lỗi mạng ({type(error).__name__})", sent=sent, unknown_calls=int(sent))   # không kèm str(error): có thể chứa URL


def _rejected_usage(payload) -> Usage | None:
    """Usage của một response bị từ chối; None nếu response không có khối usage (khi đó chi phí chưa xác định)."""
    raw = payload.get("usage") if isinstance(payload, dict) else None
    return _usage(raw) if isinstance(raw, dict) else None


def _http_error(response: httpx.Response) -> LLMError:
    kind = "unavailable" if response.status_code == 429 or response.status_code >= 500 else "bad_request"
    detail = f"HTTP {response.status_code}"
    try:
        body = response.json().get("error") or {}
        error_type = body.get("type") or body.get("status")   # Claude: `type`; Gemini: `status`
    except (ValueError, AttributeError):
        error_type = None
    if error_type in _ERROR_TYPES:
        detail += f" {error_type}"
    return LLMError(kind, detail, sent=True)


def _validation_path(error: jsonschema.ValidationError) -> str:
    path = "/".join(str(part) for part in error.absolute_path) or "<gốc>"
    return f"{path} ({error.validator})"  # chỉ vị trí + từ khoá vi phạm; không có `error.message` vì nó chứa giá trị LLM sinh ra


def _parse(payload, *, tool_name: str, validator: jsonschema.Draft202012Validator, requested_model: str, duration_s: float) -> ToolCall:
    if not isinstance(payload, dict):
        raise LLMError("bad_output", "response không phải object JSON")
    usage = _usage(payload.get("usage"))
    stop_reason = payload.get("stop_reason") if isinstance(payload.get("stop_reason"), str) else ""
    model = payload.get("model") if isinstance(payload.get("model"), str) else requested_model
    if stop_reason == "refusal":
        raise LLMError("refused", "model từ chối yêu cầu")
    if stop_reason == "max_tokens":
        raise LLMError("bad_output", "bị cắt do max_tokens")
    content = payload.get("content")
    blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] if isinstance(content, list) else []
    if len(blocks) != 1:
        raise LLMError("bad_output", f"cần đúng 1 tool_use, nhận {len(blocks)}")
    block = blocks[0]
    if block.get("name") != tool_name:
        raise LLMError("bad_output", "tool_use sai tên tool")
    data = block.get("input")
    if not isinstance(data, dict):
        raise LLMError("bad_output", "input của tool_use không phải object")
    failure = next(iter(validator.iter_errors(data)), None)
    if failure is not None:
        raise LLMError("bad_output", f"input vi phạm schema tại {_validation_path(failure)}")
    return ToolCall(data=data, usage=usage, model=model, stop_reason=stop_reason, duration_s=duration_s)


def _log_call(level: int, *, purpose: str, model: str, stop_reason: str | None, usage: Usage, duration_s: float, kind: str | None) -> None:
    event(log, "llm.call", level, purpose=purpose, model=model, stop_reason=stop_reason or None, kind=kind,
          input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
          cache_creation_input_tokens=usage.cache_creation_input_tokens, cache_read_input_tokens=usage.cache_read_input_tokens,
          duration_s=round(duration_s, 3))


# ---------------- Gemini (generateContent) ----------------

class _ModelUnavailable(LLMError):
    """Model Gemini này không dùng được (404, hết quota ngày, hết lượt retry): người gọi có thể thử model dự phòng. Với caller vẫn chỉ là LLMError."""


def provider_of(model: str) -> str:
    return "gemini" if (model or "").lower().startswith("gemini-") else "anthropic"


def gemini_schema(schema: dict) -> dict:
    """Schema gửi cho Gemini: bỏ từ khoá không chắc được hỗ trợ (client vẫn validate lại đầy đủ), `const` -> `enum`. Không sửa `schema`."""
    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key in _GEMINI_DROP:
                continue
            if key == "const":
                out["enum"] = [copy.deepcopy(value)]
            elif key in _NAME_MAPS and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}
            elif key in _DATA:
                out[key] = copy.deepcopy(value)
            else:
                out[key] = walk(value)
        return out

    return walk(schema)


def _gemini_usage(raw) -> Usage:
    raw = raw if isinstance(raw, dict) else {}
    def number(name: str) -> int:
        value = raw.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    cached = number("cachedContentTokenCount")
    # promptTokenCount đã gồm phần cache; Usage.input_tokens chỉ là phần KHÔNG cache. Token "thinking" tính vào output (bị tính tiền như output).
    return Usage(max(number("promptTokenCount") - cached, 0), number("candidatesTokenCount") + number("thoughtsTokenCount"), 0, cached)


def _retry_hint(response: httpx.Response) -> tuple[float | None, bool]:
    """(giây server bảo chờ, hết quota THEO NGÀY?). Chỉ đọc số/khoá đã biết; không bao giờ đọc `error.message`."""
    hint = None
    header = response.headers.get("retry-after", "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", header):
        hint = float(header)
    daily = False
    try:
        details = (response.json().get("error") or {}).get("details") or []
    except (ValueError, AttributeError):
        details = []
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        delay = item.get("retryDelay")
        if isinstance(delay, str) and re.fullmatch(r"\d+(\.\d+)?s", delay):
            hint = float(delay[:-1])
        violations = item.get("violations")
        for violation in violations if isinstance(violations, list) else []:
            if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                daily = True
    return hint, daily


def _parse_gemini(payload, *, tool_name: str, validator: jsonschema.Draft202012Validator, model: str, duration_s: float) -> ToolCall:
    if not isinstance(payload, dict):
        raise LLMError("bad_output", "response không phải object JSON")
    usage = _gemini_usage(payload.get("usageMetadata"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        feedback = payload.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise LLMError("refused", "prompt bị chặn (promptFeedback)")
        raise LLMError("bad_output", "response không có candidate")
    candidate = candidates[0]
    finish = candidate.get("finishReason") if isinstance(candidate.get("finishReason"), str) else ""
    if finish in _GEMINI_REFUSED:
        raise LLMError("refused", f"model từ chối yêu cầu ({finish})")
    if finish == "MAX_TOKENS":
        raise LLMError("bad_output", "bị cắt do max_tokens")   # token thinking cũng tính vào maxOutputTokens
    if finish == "MALFORMED_FUNCTION_CALL":
        raise LLMError("bad_output", "function call sai định dạng")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    calls = ([p["functionCall"] for p in parts if isinstance(p, dict) and isinstance(p.get("functionCall"), dict) and not p.get("thought")]
             if isinstance(parts, list) else [])
    if len(calls) != 1:
        raise LLMError("bad_output", f"cần đúng 1 functionCall, nhận {len(calls)}")
    if calls[0].get("name") != tool_name:
        raise LLMError("bad_output", "functionCall sai tên tool")
    data = calls[0].get("args")
    if not isinstance(data, dict):
        raise LLMError("bad_output", "args của functionCall không phải object")
    failure = next(iter(validator.iter_errors(data)), None)
    if failure is not None:
        raise LLMError("bad_output", f"input vi phạm schema tại {_validation_path(failure)}")
    return ToolCall(data=data, usage=usage, model=model, stop_reason="tool_use" if finish in ("", "STOP") else finish.lower(), duration_s=duration_s)


def _gemini_models(primary: str) -> list[str]:
    models = [primary]
    for name in settings.get().llm_fallback_models.split(","):
        name = name.strip()
        if not name or name in models:
            continue
        if provider_of(name) != "gemini" or not _MODEL_ID.fullmatch(name):
            raise LLMError("bad_request", "QC_LLM_FALLBACK_MODELS chỉ nhận model gemini-* hợp lệ")
        models.append(name)
    return models


def _pace(provider: str, min_interval_s: float) -> None:
    """Giãn cách chủ động: đủ `min_interval_s` giây kể từ lúc bắt đầu request trước (mọi lời gọi trong tiến trình, kể cả giữa các lượt eval)."""
    last = _last_request_at.get(provider)
    if last is not None and min_interval_s > 0:
        wait = min_interval_s - (_monotonic() - last)
        if wait > 0:
            _sleep(wait)
    _last_request_at[provider] = _monotonic()


def _detail(error: LLMError) -> str:
    return str(error).split(": ", 1)[-1]


def _call_gemini(*, purpose: str, model: str, system: str, user: str, tool_name: str, tool_description: str, input_schema: dict,
                 validator: jsonschema.Draft202012Validator, egress_dir: Path, data_categories: list[str], max_tokens: int,
                 timeout: float, policy: egress.EgressPolicy | None, transport: httpx.BaseTransport | None, max_retries: int) -> ToolCall:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("missing_key", "chưa đặt GEMINI_API_KEY")   # chưa có gì rời máy nên không ghi egress
    base_url = (os.environ.get("GEMINI_BASE_URL", "").strip() or GEMINI_BASE_URL).rstrip("/")
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise LLMError("bad_request", "GEMINI_BASE_URL phải là http(s)")
    if not _MODEL_ID.fullmatch(model):
        raise ValueError("model Gemini phải khớp [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    models = _gemini_models(model)

    worker = SimpleNamespace(name=f"qc-agent-{purpose}", data_egress=list(data_categories))
    spec = {"task_id": purpose, "capability": f"llm.{purpose}", "target": {"base_url": base_url}}
    egress_dir = Path(egress_dir)
    egress_dir.mkdir(parents=True, exist_ok=True)
    cfg = settings.get()
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "tools": [{"functionDeclarations": [{"name": tool_name, "description": tool_description, "parametersJsonSchema": gemini_schema(input_schema)}]}],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool_name]}},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system and system.strip():
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if cfg.gemini_thinking_level.strip():
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": cfg.gemini_thinking_level.strip()}
    headers = {"x-goog-api-key": key, "content-type": "application/json"}
    sent = 0     # đánh số dòng egress (attempt); tăng TRƯỚC khi hỏi policy nên không đếm được request đã rời máy
    posted = 0   # số request thật sự đã gửi đi (qua egress)
    unknown = 0  # trong đó số request không có response (timeout đọc, đứt kết nối) hoặc response không có usage: chi phí chưa xác định (LLMError.unknown_calls)

    def one_model(name: str) -> ToolCall:
        nonlocal sent, posted, unknown
        url = f"{base_url}/v1beta/models/{name}:generateContent"
        for retry in range(max_retries + 1):
            sent += 1
            decision = egress.record(policy or egress.LogOnlyPolicy(), egress_dir, spec, worker, sent)
            if decision.action != "allow":
                raise LLMError("egress_denied", f"chính sách egress: {decision.action}")
            _pace("gemini", cfg.llm_min_interval_s)
            started = _monotonic()
            network = None
            response = None
            posted += 1
            try:
                with httpx.Client(transport=transport, timeout=timeout) as client:
                    response = client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as caught:
                if _never_sent(caught):
                    posted -= 1
                else:
                    unknown += 1
                waited = _monotonic() - started
                _log_call(logging.WARNING, purpose=purpose, model=name, stop_reason=None, usage=Usage(), duration_s=waited, kind="timeout")
                timed_out = LLMError("timeout", f"quá {timeout:g}s")   # đã chờ cả timeout: không retry
                timed_out.duration_s = waited
                raise timed_out from None
            except httpx.HTTPError as caught:
                network = f"lỗi mạng ({type(caught).__name__})"   # không kèm str(caught): có thể chứa URL
                if _never_sent(caught):
                    posted -= 1
                else:
                    unknown += 1
            duration = _monotonic() - started

            if response is not None and response.is_success:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                try:
                    call = _parse_gemini(payload, tool_name=tool_name, validator=validator, model=name, duration_s=duration)
                except LLMError as bad:
                    seen = payload if isinstance(payload, dict) else {}
                    candidates = seen.get("candidates")
                    reason = candidates[0].get("finishReason") if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else None
                    _log_call(logging.WARNING, purpose=purpose, model=name, stop_reason=reason.lower() if isinstance(reason, str) else None,
                              usage=_gemini_usage(seen.get("usageMetadata")), duration_s=duration, kind=bad.kind)
                    bad.duration_s = duration
                    meta = seen.get("usageMetadata")
                    if isinstance(meta, dict):
                        bad.usage = _gemini_usage(meta)   # token của response bị từ chối (đã bị tính phí)
                    else:
                        unknown += 1                       # có response nhưng không có usage: chưa biết đã tốn bao nhiêu
                    raise
                _log_call(logging.INFO, purpose=purpose, model=name, stop_reason=call.stop_reason, usage=call.usage, duration_s=duration, kind=None)
                return call

            hint, daily = (None, False) if response is None else _retry_hint(response)
            failure = LLMError("unavailable", network) if response is None else _http_error(response)
            status = 0 if response is None else response.status_code
            transient = response is None or status == 429 or status >= 500
            give_up = status == 404 or daily or (hint is not None and hint > MAX_HINT_WAIT_S) or retry >= max_retries
            if not transient and status != 404:
                _log_call(logging.WARNING, purpose=purpose, model=name, stop_reason=None, usage=Usage(), duration_s=duration, kind=failure.kind)
                raise failure
            if give_up:
                _log_call(logging.WARNING, purpose=purpose, model=name, stop_reason=None, usage=Usage(), duration_s=duration, kind=failure.kind)
                why = "model không tồn tại hoặc không có quyền" if status == 404 else "hết quota ngày" if daily else f"đã thử {retry + 1} lần"
                raise _ModelUnavailable(failure.kind, f"{_detail(failure)} ({why})")
            wait = (hint + _jitter()) if hint is not None else min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** retry) + _jitter()
            event(log, "llm.retry", logging.WARNING, purpose=purpose, model=name, retry=retry + 1, status=status or None, wait_s=round(wait, 2))
            _sleep(wait)
        raise AssertionError("unreachable")   # vòng lặp luôn return/raise

    last: LLMError | None = None
    try:
        for name in models:
            try:
                call = one_model(name)
            except _ModelUnavailable as unavailable:
                last = unavailable
                event(log, "llm.fallback", logging.WARNING, purpose=purpose, model=name, kind=unavailable.kind)
                continue
            return replace(call, unknown_calls=unknown, fallback_from=None if name == model else model)   # `unknown` gom qua mọi lần thử và mọi model
        assert last is not None
        raise LLMError(last.kind, _detail(last) + (f" (đã thử {len(models)} model)" if len(models) > 1 else ""))
    except LLMError as error:
        error.sent = error.sent or posted > 0   # mọi lỗi thoát ra đều biết đã có request nào rời máy chưa và bao nhiêu cái chưa biết chi phí
        error.unknown_calls = unknown
        raise


def _canonical(node):
    """Sắp khoá của MỌI dict (giữ nguyên thứ tự mảng) để `tools` giống từng byte giữa các lời gọi: một byte lệch trong tiền tố (tools -> system -> messages) là mất cache."""
    if isinstance(node, dict):
        return {key: _canonical(node[key]) for key in sorted(node)}
    if isinstance(node, list):
        return [_canonical(item) for item in node]
    return node


def _claude_body(model: str, system: str, user: str, tool_name: str, tool_description: str, wire: dict) -> dict:
    """Phần body dùng chung cho `messages` và `count_tokens` (chưa có `max_tokens`/`temperature`). `system` là block tĩnh cuối cùng của tiền tố nên gắn
    `cache_control` (TTL 5 phút); tiền tố ngắn hơn ngưỡng của model thì API im lặng không cache (không lỗi, không tốn thêm)."""
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
        "tools": [{"name": tool_name, "description": tool_description, "input_schema": _canonical(wire), "strict": True}],
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    if system and system.strip():
        body["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return body


def estimate_input_tokens(*parts) -> int:
    """Ước lượng TẤT ĐỊNH số token đầu vào: `ceil(số byte UTF-8 / 3)`, không gọi API và không gửi thêm dữ liệu; dùng chung cho mọi provider.

    Chỉ là ƯỚC LƯỢNG GẦN ĐÚNG, KHÔNG phải trần tuyệt đối: với ASCII bằng `ký tự / 3`; với CJK (3 byte/ký tự) cho ≈ 1 token/ký tự, và tiếng Việt có dấu, emoji cho số
    lớn hơn `ký tự / 3`. Vẫn có thể thấp hơn thực tế ở ký tự hiếm. Số token thật chỉ biết sau lời gọi (`usage`). `parts` là chuỗi hoặc dict (schema, được canonical hoá)."""
    total = 0
    for part in parts:
        text = part if isinstance(part, str) else json.dumps(_canonical(part), ensure_ascii=False, separators=(",", ":"))
        total += len(text.encode("utf-8"))
    return -(-total // 3)


def count_tokens(*, purpose: str, model: str, system: str, user: str, tool_name: str, tool_description: str, input_schema: dict,
                 egress_dir: Path, data_categories: list[str], timeout_s: float | None = None,
                 policy: egress.EgressPolicy | None = None, transport: httpx.BaseTransport | None = None) -> int:
    """`POST /v1/messages/count_tokens` với cùng body của `call_tool` (không có `max_tokens`): số token đầu vào THẬT của một lời gọi (chỉ Claude).

    Endpoint này GỬI NỘI DUNG RA NGOÀI nên đi đúng đường egress của một lời gọi LLM: ghi egress TRƯỚC khi gửi, policy khác `allow` thì KHÔNG có request nào.
    Model `gemini-*` là lỗi lập trình (`ValueError`, không gọi gì). Lỗi dùng chung phân loại của `call_tool` (`LLMError`, kèm `sent`/`unknown_calls`)."""
    if not _PURPOSE.fullmatch(purpose or ""):
        raise ValueError("purpose phải khớp [a-z0-9][a-z0-9_-]{0,39}")
    if not _TOOL_NAME.fullmatch(tool_name or ""):
        raise ValueError("tool_name phải khớp [A-Za-z0-9_-]{1,64}")
    if not model or not user or not user.strip():
        raise ValueError("model và user không được rỗng")
    if provider_of(model) == "gemini":
        raise ValueError("count_tokens chỉ hỗ trợ model Claude")
    try:
        jsonschema.Draft202012Validator.check_schema(input_schema)
    except jsonschema.SchemaError as error:
        raise ValueError(f"input_schema không hợp lệ theo JSON Schema 2020-12 ({error.validator})") from None
    wire = wire_schema(input_schema)
    timeout = timeout_s if timeout_s is not None else settings.get().llm_timeout_s
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise LLMError("missing_key", "chưa đặt ANTHROPIC_API_KEY")
    base_url = (os.environ.get("ANTHROPIC_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise LLMError("bad_request", "ANTHROPIC_BASE_URL phải là http(s)")
    worker = SimpleNamespace(name=f"qc-agent-{purpose}", data_egress=list(data_categories))
    spec = {"task_id": purpose, "capability": f"llm.{purpose}", "target": {"base_url": base_url}}
    egress_dir = Path(egress_dir)
    egress_dir.mkdir(parents=True, exist_ok=True)
    decision = egress.record(policy or egress.LogOnlyPolicy(), egress_dir, spec, worker, 1)
    if decision.action != "allow":
        raise LLMError("egress_denied", f"chính sách egress: {decision.action}")
    body = _claude_body(model, system, user, tool_name, tool_description, wire)
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.post(f"{base_url}/v1/messages/count_tokens", headers=headers, json=body)
    except httpx.HTTPError as caught:
        raise _transport_error(caught, timeout) from None
    if not response.is_success:
        raise _http_error(response)
    try:
        count = response.json().get("input_tokens")
    except (ValueError, AttributeError):
        count = None
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise LLMError("bad_output", "response không có input_tokens hợp lệ", sent=True)
    return count


def call_tool(*, purpose: str, model: str, system: str, user: str,
              tool_name: str, tool_description: str, input_schema: dict,
              egress_dir: Path, data_categories: list[str],
              max_tokens: int = 4096, timeout_s: float | None = None,
              policy: egress.EgressPolicy | None = None,
              transport: httpx.BaseTransport | None = None, max_retries: int | None = None) -> ToolCall:
    """Gọi LLM một lần, ép model trả `tool_name` với input theo `input_schema`, rồi validate đầy đủ phía client.

    Provider theo tiền tố `model` (xem docstring module). `max_retries` (chỉ Gemini, mặc định `QC_LLM_MAX_RETRIES`) là số lần thử LẠI khi 429/5xx/lỗi mạng;
    Diff Agent có ngân sách thời gian chặt nên truyền 0. Claude không bao giờ tự retry.

    `purpose` (vd "gt-generate", "diff-select") đặt tên worker/capability trong egress.jsonl; `data_categories` là loại dữ liệu rời máy
    (vd ["prd_text"]). `transport` để test tiêm `httpx.MockTransport`. Lỗi luôn là `LLMError` với `.kind` ∈ KINDS.
    Sai cách dùng (đối số/schema hỏng) là lỗi lập trình nên ném ValueError, không phải LLMError.
    """
    if not _PURPOSE.fullmatch(purpose or ""):
        raise ValueError("purpose phải khớp [a-z0-9][a-z0-9_-]{0,39}")
    if not _TOOL_NAME.fullmatch(tool_name or ""):
        raise ValueError("tool_name phải khớp [A-Za-z0-9_-]{1,64}")
    if not model or not user or not user.strip():
        raise ValueError("model và user không được rỗng")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens phải là số nguyên ≥ 1")
    try:
        jsonschema.Draft202012Validator.check_schema(input_schema)
    except jsonschema.SchemaError as error:
        raise ValueError(f"input_schema không hợp lệ theo JSON Schema 2020-12 ({error.validator})") from None
    validator = jsonschema.Draft202012Validator(input_schema)
    wire = wire_schema(input_schema)   # cũng là nơi từ chối schema có object mở, cho cả hai provider
    timeout = timeout_s if timeout_s is not None else settings.get().llm_timeout_s
    if provider_of(model) == "gemini":
        retries = settings.get().llm_max_retries if max_retries is None else max_retries
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
            raise ValueError("max_retries phải là số nguyên ≥ 0")
        return _call_gemini(purpose=purpose, model=model, system=system, user=user, tool_name=tool_name, tool_description=tool_description,
                            input_schema=input_schema, validator=validator, egress_dir=egress_dir, data_categories=data_categories,
                            max_tokens=max_tokens, timeout=timeout, policy=policy, transport=transport, max_retries=retries)

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise LLMError("missing_key", "chưa đặt ANTHROPIC_API_KEY")  # chưa có gì rời máy nên không ghi egress
    base_url = (os.environ.get("ANTHROPIC_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise LLMError("bad_request", "ANTHROPIC_BASE_URL phải là http(s)")

    worker = SimpleNamespace(name=f"qc-agent-{purpose}", data_egress=list(data_categories))
    spec = {"task_id": purpose, "capability": f"llm.{purpose}", "target": {"base_url": base_url}}
    egress_dir = Path(egress_dir)
    egress_dir.mkdir(parents=True, exist_ok=True)
    decision = egress.record(policy or egress.LogOnlyPolicy(), egress_dir, spec, worker, 1)
    if decision.action != "allow":
        raise LLMError("egress_denied", f"chính sách egress: {decision.action}")

    body = _claude_body(model, system, user, tool_name, tool_description, wire)
    body["max_tokens"] = max_tokens
    if model.startswith(_SAMPLING_OK):
        body["temperature"] = 0
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}

    started = time.monotonic()
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.post(f"{base_url}/v1/messages", headers=headers, json=body)
    except httpx.HTTPError as caught:   # gồm cả TimeoutException; chi phí chưa xác định nếu request đã có thể tới server (xem LLMError)
        error = _transport_error(caught, timeout)
    else:
        error = None
    duration = time.monotonic() - started
    if error is None and not response.is_success:
        error = _http_error(response)
    if error is not None:
        error.duration_s = duration
        _log_call(logging.WARNING, purpose=purpose, model=model, stop_reason=None, usage=Usage(), duration_s=duration, kind=error.kind)
        raise error

    try:
        payload = response.json()
    except ValueError:
        payload = None
    try:
        call = _parse(payload, tool_name=tool_name, validator=validator, requested_model=model, duration_s=duration)
    except LLMError as bad:
        seen = payload if isinstance(payload, dict) else {}
        reason = seen.get("stop_reason")
        _log_call(logging.WARNING, purpose=purpose, model=model, stop_reason=reason if isinstance(reason, str) else None,
                  usage=_usage(seen.get("usage")), duration_s=duration, kind=bad.kind)
        bad.usage = _rejected_usage(payload)   # response bị từ chối vẫn đã bị tính phí: caller phải đưa vào báo cáo chi phí
        bad.sent, bad.duration_s = True, duration
        bad.unknown_calls = 0 if bad.usage is not None else 1   # response không có usage (hoặc không phải JSON): chưa biết đã tốn bao nhiêu
        raise
    _log_call(logging.INFO, purpose=purpose, model=call.model, stop_reason=call.stop_reason, usage=call.usage, duration_s=duration, kind=None)
    return call
