"""Client Claude Messages API dùng chung, chỉ qua `httpx` (quyết định #5: không thêm SDK). Mọi output của LLM bị ÉP vào JSON bằng tool-use.

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
import logging
import os
import re
import time
from dataclasses import dataclass
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
API_VERSION = "2023-06-01"
_SAMPLING_OK = ("claude-haiku-4-5",)  # tiền tố model còn nhận `temperature`; Sonnet 5 trả 400 nếu có
_PURPOSE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
# `error.type` của API là tập đóng; chỉ những giá trị này được đưa vào message (không bao giờ đưa `error.message`, có thể chứa nội dung).
_ERROR_TYPES = frozenset({"invalid_request_error", "authentication_error", "permission_error", "not_found_error", "request_too_large",
                          "rate_limit_error", "api_error", "overloaded_error"})
# Từ khoá strict mode không hỗ trợ: bỏ khỏi schema gửi đi, client tự validate lại.
_UNSUPPORTED = frozenset({"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                          "maxItems", "uniqueItems", "minContains", "maxContains"})
_NAME_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions"})  # khoá con là TÊN, không phải từ khoá schema
_DATA = frozenset({"enum", "const", "default", "examples", "required"})               # giá trị là dữ liệu, không duyệt


class LLMError(RuntimeError):
    """`.kind` ∈ KINDS. Message chỉ chứa mã/định danh ngắn: KHÔNG có body response, URL, prompt hay khoá."""

    def __init__(self, kind: str, detail: str = ""):
        if kind not in KINDS:
            raise ValueError(f"LLMError.kind phải thuộc {KINDS}, nhận {kind!r}")
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


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


def _http_error(response: httpx.Response) -> LLMError:
    kind = "unavailable" if response.status_code == 429 or response.status_code >= 500 else "bad_request"
    detail = f"HTTP {response.status_code}"
    try:
        error_type = (response.json().get("error") or {}).get("type")
    except (ValueError, AttributeError):
        error_type = None
    if error_type in _ERROR_TYPES:
        detail += f" {error_type}"
    return LLMError(kind, detail)


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


def call_tool(*, purpose: str, model: str, system: str, user: str,
              tool_name: str, tool_description: str, input_schema: dict,
              egress_dir: Path, data_categories: list[str],
              max_tokens: int = 4096, timeout_s: float | None = None,
              policy: egress.EgressPolicy | None = None,
              transport: httpx.BaseTransport | None = None) -> ToolCall:
    """Gọi Messages API một lần, ép model trả `tool_name` với input theo `input_schema`, rồi validate đầy đủ phía client.

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
    wire = wire_schema(input_schema)

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

    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
        "tools": [{"name": tool_name, "description": tool_description, "input_schema": wire, "strict": True}],
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    if system and system.strip():
        body["system"] = [{"type": "text", "text": system}]
    if model.startswith(_SAMPLING_OK):
        body["temperature"] = 0
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    timeout = timeout_s if timeout_s is not None else settings.get().llm_timeout_s

    started = time.monotonic()
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.post(f"{base_url}/v1/messages", headers=headers, json=body)
    except httpx.TimeoutException:
        error = LLMError("timeout", f"quá {timeout:g}s")
    except httpx.HTTPError as caught:
        error = LLMError("unavailable", f"lỗi mạng ({type(caught).__name__})")  # không kèm str(caught): có thể chứa URL
    else:
        error = None
    duration = time.monotonic() - started
    if error is None and not response.is_success:
        error = _http_error(response)
    if error is not None:
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
        raise
    _log_call(logging.INFO, purpose=purpose, model=call.model, stop_reason=call.stop_reason, usage=call.usage, duration_s=duration, kind=None)
    return call
