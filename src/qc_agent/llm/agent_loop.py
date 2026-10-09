"""Vòng lặp agent NHIỀU LƯỢT cho Claude (Messages API, chỉ `httpx`, quyết định #5: không SDK). Dùng cho Ground-Truth agent; không biết gì về GT.

Khác `client.call_tool` (một lời gọi, ép `tool_choice`): ở đây model tự gọi tool qua nhiều lượt, caller cấp tool + handler và nhận lại `AgentRun`.

Sự thật về API đã kiểm với tài liệu Claude API (2026-09), quyết định hình dạng của file này:
  - `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-fable-5-1` TRẢ 400 với `tool_choice` kiểu `any`/`tool`: dùng `auto` + `strict: true` và để lời nhắc cuối lượt
    ("hãy gọi `<finish_tool>`") dẫn model kết thúc bằng tool, không ép được. Hết lượt mà chưa gọi thì nhắc MỘT lần, vẫn không thì `bad_output`.
  - `strict: true` có trần độ phức tạp: API biên dịch schema thành grammar và trả 400 "compiled grammar is too large" khi quá lớn (đo thật: schema ~6 KB của
    `submit_test_cases`). `AgentTool.strict=False` bỏ cờ này cho riêng tool đó; client vẫn validate đầy đủ và trả lỗi cho model sửa.
  - Thinking không tắt được trên Opus 5.5 và có "preserved thinking": lịch sử PHẢI chỉ-được-nối-thêm. Nội dung assistant được trả về NGUYÊN VĂN (kể cả block
    `thinking` kèm chữ ký và block `fallback`); lời nhắc/cảnh báo chỉ được THÊM vào cuối, không bao giờ sửa message cũ. Có test: request N là tiền tố của request N+1.
  - Tool song song: mọi `tool_result` của một lượt nằm trong MỘT message user, theo đúng thứ tự `tool_use`, đứng TRƯỚC mọi block text.
  - Prompt cache: `cache_control` trên system và tool cuối (tiền tố tĩnh, byte-giống-nhau giữa các lượt) + `cache_control` cấp cao nhất để cache phần lịch sử đang lớn dần.
  - Không gửi `thinking`, `temperature`, `top_p` (400 trên các model này). `output_config.effort` điều khiển độ sâu suy nghĩ.
  - `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) để lời từ chối của bộ phân loại an toàn được chuyển sang model dự phòng ngay trong một lời gọi.
    CHƯA kiểm với API thật (cần một lần gọi thử có chi phí, xin phép người dùng trước): tắt bằng `fallbacks=False` / `QC_GT_AGENT_FALLBACKS=false` nếu API trả 400.

Ranh giới cứng (docs/core-rules.md), giống `client.py`:
  - `core/` không import module này ở top-level. Ghi egress TRƯỚC MỖI request; policy khác `allow` thì không có request nào.
  - Prompt, response, nội dung file, thinking, khoá API không bao giờ vào log hay message của `LLMError`: chỉ định danh, số đếm, mã lỗi.
  - Không tự retry (kể cả 429/5xx): caller quyết định. Lỗi luôn là `LLMError` với `.kind` thuộc `KINDS`; trạng thái đã tích luỹ nằm trong handler của caller
    nên caller có thể giữ kết quả dở khi vòng lặp bị lỗi hoặc hết ngân sách.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from urllib.parse import urlsplit

import httpx
import jsonschema

from qc_agent.core import egress
from qc_agent.llm import client
from qc_agent.llm.prices import PRICES, estimate_cost  # noqa: F401  (bảng giá DUY NHẤT nằm ở llm/prices.py; re-export để giữ tên cũ)
from qc_agent.logging_setup import event

log = logging.getLogger("qc_agent.llm")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
STOPS = ("finished", "budget_turns", "budget_cost", "budget_time")
MAX_RESULT_CHARS = 200_000          # chốt chặn cuối cho một tool_result; handler nên tự giới hạn nhỏ hơn
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PURPOSE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_monotonic = time.monotonic         # test thay thế để kiểm ngân sách thời gian mà không ngủ thật

@dataclass(frozen=True)
class ToolOutcome:
    """Kết quả một lần chạy tool. `content` là chuỗi gửi lại cho model (caller đã bọc/làm sạch dữ liệu không tin cậy).
    `categories`: loại dữ liệu mà `content` đưa vào lịch sử (vd {"source_code"}); từ request kế tiếp chúng được ghi vào egress.
    `stop=True`: tool kết thúc được chấp nhận, vòng lặp dừng mà không gửi thêm request."""
    content: str
    is_error: bool = False
    categories: frozenset[str] = frozenset()
    stop: bool = False


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict                       # schema ĐẦY ĐỦ (có thể có maxLength...); `wire_schema` rút gọn khi gửi, client validate lại bằng schema này
    handler: Callable[[dict], ToolOutcome]
    strict: bool = True                      # False khi schema quá lớn để API biên dịch thành grammar (400 "compiled grammar is too large"): vẫn được validate đầy đủ phía client


@dataclass(frozen=True)
class AgentBudget:
    max_turns: int = 40
    max_cost_usd: float | None = 10.0        # chỉ áp dụng khi biết giá của model (PRICES)
    max_wall_s: float = 1800.0
    warn_at_turns_left: int = 3              # còn chừng này lượt thì THÊM lời nhắc "nộp rồi kết thúc" vào cuối message user


@dataclass(frozen=True)
class AgentRun:
    stop: str                                # thuộc STOPS
    turns: int                               # số request đã gửi
    usage: client.Usage
    model: str                               # model của response cuối (có thể là model dự phòng)
    duration_s: float
    tool_calls: dict[str, int] = field(default_factory=dict)
    nudges: int = 0
    cost_usd_est: float | None = None
    unknown_calls: int = 0                   # request cuối đã gửi mà không có response (timeout, đứt kết nối): chi phí CHƯA XÁC ĐỊNH, `usage` chưa gồm nó (S4-03)


def _add(a: client.Usage, b: client.Usage) -> client.Usage:
    return client.Usage(a.input_tokens + b.input_tokens, a.output_tokens + b.output_tokens,
                        a.cache_creation_input_tokens + b.cache_creation_input_tokens, a.cache_read_input_tokens + b.cache_read_input_tokens)


def _check_args(*, purpose, model, system, first_user, tools, finish_tool, budget, effort, max_tokens) -> dict[str, jsonschema.Draft202012Validator]:
    """Sai cách dùng (đối số/schema hỏng) là lỗi lập trình: ValueError, không phải LLMError. Trả validator theo tên tool."""
    if not _PURPOSE.fullmatch(purpose or ""):
        raise ValueError("purpose phải khớp [a-z0-9][a-z0-9_-]{0,39}")
    if not model or not system or not system.strip() or not first_user or not first_user.strip():
        raise ValueError("model, system và first_user không được rỗng")
    if effort not in EFFORTS:
        raise ValueError(f"effort phải thuộc {EFFORTS}")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError("max_tokens phải là số nguyên ≥ 1")
    if budget.max_turns < 1 or budget.max_wall_s <= 0 or budget.warn_at_turns_left < 0 or (budget.max_cost_usd is not None and budget.max_cost_usd <= 0):
        raise ValueError("ngân sách không hợp lệ")
    names = [tool.name for tool in tools]
    if not tools or len(set(names)) != len(names) or not all(_TOOL_NAME.fullmatch(n or "") for n in names):
        raise ValueError("tools phải không rỗng, tên duy nhất và khớp [A-Za-z0-9_-]{1,64}")
    if finish_tool not in names:
        raise ValueError("finish_tool phải là tên của một tool")
    validators = {}
    for tool in tools:
        try:
            jsonschema.Draft202012Validator.check_schema(tool.input_schema)
        except jsonschema.SchemaError as error:
            raise ValueError(f"input_schema của {tool.name} không hợp lệ theo JSON Schema 2020-12 ({error.validator})") from None
        validators[tool.name] = jsonschema.Draft202012Validator(tool.input_schema)
    return validators


def _run_tool(tool: AgentTool | None, validator, raw_input) -> ToolOutcome:
    if tool is None:
        return ToolOutcome("tool không tồn tại", is_error=True)
    if not isinstance(raw_input, dict):
        return ToolOutcome("input của tool phải là object", is_error=True)
    failure = next(iter(validator.iter_errors(raw_input)), None)
    if failure is not None:   # strict mode không diễn đạt được maxLength/pattern, nên đây mới là nơi chặn thật
        return ToolOutcome(f"input vi phạm schema tại {client._validation_path(failure)}", is_error=True)
    try:
        outcome = tool.handler(raw_input)
    except Exception as error:  # noqa: BLE001 — chỉ báo loại lỗi: str(error) có thể chứa nội dung file/PRD
        return ToolOutcome(f"lỗi nội bộ của tool ({type(error).__name__})", is_error=True)
    if not isinstance(outcome, ToolOutcome):
        return ToolOutcome("lỗi nội bộ của tool (kết quả sai kiểu)", is_error=True)
    if len(outcome.content) > MAX_RESULT_CHARS:
        outcome = ToolOutcome(outcome.content[:MAX_RESULT_CHARS] + "\n… (cắt bớt)", outcome.is_error, outcome.categories, outcome.stop)
    return outcome


def run_agent(*, purpose: str, model: str, system: str, first_user: str, tools: list[AgentTool], finish_tool: str,
              egress_dir: Path, base_categories: list[str], budget: AgentBudget | None = None, effort: str = "high",
              max_tokens: int = 32000, timeout_s: float = 600.0, fallbacks: bool = True,
              policy: egress.EgressPolicy | None = None, transport: httpx.BaseTransport | None = None) -> AgentRun:
    """Chạy agent tới khi một tool trả `stop=True` (kết thúc thật) hoặc hết ngân sách. Raise `LLMError` khi lỗi API/egress/đầu ra hỏng.

    `finish_tool` là tool kết thúc: model được nhắc gọi nó khi tự dừng (`end_turn`) hoặc sắp hết lượt. `base_categories` là loại dữ liệu có trong
    `first_user` (vd ["prd_text", "api_spec"]); mỗi `ToolOutcome.categories` thêm vào từ request kế tiếp. Hết ngân sách KHÔNG raise: trả `AgentRun(stop="budget_*")`.
    """
    budget = budget or AgentBudget()
    validators = _check_args(purpose=purpose, model=model, system=system, first_user=first_user, tools=tools, finish_tool=finish_tool,
                             budget=budget, effort=effort, max_tokens=max_tokens)
    by_name = {tool.name: tool for tool in tools}
    wire_tools = [{"name": t.name, "description": t.description, "input_schema": client.wire_schema(t.input_schema), **({"strict": True} if t.strict else {})} for t in tools]
    wire_tools[-1]["cache_control"] = {"type": "ephemeral"}

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise client.LLMError("missing_key", "chưa đặt ANTHROPIC_API_KEY")   # chưa có gì rời máy nên không ghi egress
    base_url = (os.environ.get("ANTHROPIC_BASE_URL", "").strip() or client.DEFAULT_BASE_URL).rstrip("/")
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise client.LLMError("bad_request", "ANTHROPIC_BASE_URL phải là http(s)")
    headers = {"x-api-key": key, "anthropic-version": client.API_VERSION, "content-type": "application/json"}
    if fallbacks:
        headers["anthropic-beta"] = FALLBACK_BETA

    egress_dir = Path(egress_dir)
    egress_dir.mkdir(parents=True, exist_ok=True)
    spec = {"task_id": purpose, "capability": f"llm.{purpose}", "target": {"base_url": base_url}}
    categories: set[str] = set(base_categories)
    policy = policy or egress.LogOnlyPolicy()

    messages: list[dict] = [{"role": "user", "content": [{"type": "text", "text": first_user}]}]
    usage, last_model, calls, nudges, turns = client.Usage(), model, {}, 0, 0
    started = _monotonic()
    stop = "finished"

    def cost() -> float | None:
        return estimate_cost(last_model, usage)

    def fail(error: client.LLMError) -> client.LLMError:
        """Đính số liệu dở dang (lượt, token, chi phí đã tốn) vào lỗi: không có nó thì người gọi chỉ thấy 0 dù các lượt trước đã bị tính tiền."""
        error.partial = AgentRun(stop="error", turns=turns, usage=usage, model=last_model, duration_s=_monotonic() - started,
                                 tool_calls=dict(sorted(calls.items())), nudges=nudges, cost_usd_est=cost(), unknown_calls=error.unknown_calls)
        return error

    with httpx.Client(transport=transport, timeout=timeout_s) as http:
        while True:
            spent = cost()
            if turns >= budget.max_turns:
                stop = "budget_turns"
            elif _monotonic() - started >= budget.max_wall_s:
                stop = "budget_time"
            elif budget.max_cost_usd is not None and spent is not None and spent >= budget.max_cost_usd:
                stop = "budget_cost"
            else:
                stop = ""
            if stop:
                break

            turns += 1
            worker = SimpleNamespace(name=f"qc-agent-{purpose}", data_egress=sorted(categories))
            decision = egress.record(policy, egress_dir, spec, worker, turns)   # mỗi request rời máy là một dòng, TRƯỚC khi gửi
            if decision.action != "allow":
                raise fail(client.LLMError("egress_denied", f"chính sách egress: {decision.action}"))

            body: dict = {
                "model": model, "max_tokens": max_tokens,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "tools": wire_tools, "tool_choice": {"type": "auto"},
                "messages": messages, "cache_control": {"type": "ephemeral"},
                "output_config": {"effort": effort},
            }
            if fallbacks:
                body["fallbacks"] = "default"

            began = _monotonic()
            try:
                response = http.post(f"{base_url}/v1/messages", headers=headers, json=body)
            except httpx.HTTPError as caught:   # gồm TimeoutException; chi phí chưa xác định nếu request đã có thể tới server (client._transport_error)
                error = client._transport_error(caught, timeout_s)
            else:
                error = None
            elapsed = _monotonic() - began
            if error is None and not response.is_success:
                error = client._http_error(response)
            if error is not None:
                client._log_call(logging.WARNING, purpose=purpose, model=model, stop_reason=None, usage=client.Usage(), duration_s=elapsed, kind=error.kind)
                raise fail(error)

            try:
                payload = response.json()
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                client._log_call(logging.WARNING, purpose=purpose, model=model, stop_reason=None, usage=client.Usage(), duration_s=elapsed, kind="bad_output")
                raise fail(client.LLMError("bad_output", "response không phải object JSON", sent=True, unknown_calls=1))
            turn_usage = client._usage(payload.get("usage"))
            usage = _add(usage, turn_usage)
            last_model = payload["model"] if isinstance(payload.get("model"), str) else last_model
            reason = payload.get("stop_reason") if isinstance(payload.get("stop_reason"), str) else ""
            content = payload.get("content") if isinstance(payload.get("content"), list) else None
            blocks = [b for b in content if isinstance(b, dict)] if content is not None else []
            uses = [b for b in blocks if b.get("type") == "tool_use"]

            problem = None
            if reason == "refusal":
                problem = ("refused", "model từ chối yêu cầu")
            elif reason == "max_tokens":
                problem = ("bad_output", "bị cắt do max_tokens")   # tool_use dở dang không tiếp tục được
            elif content is None or len(blocks) != len(content):
                problem = ("bad_output", "content không phải danh sách block")
            elif reason == "tool_use" and not uses:
                problem = ("bad_output", "stop_reason=tool_use nhưng không có tool_use")
            if problem is not None:
                client._log_call(logging.WARNING, purpose=purpose, model=last_model, stop_reason=reason or None, usage=turn_usage, duration_s=elapsed, kind=problem[0])
                raise fail(client.LLMError(*problem))
            client._log_call(logging.INFO, purpose=purpose, model=last_model, stop_reason=reason or None, usage=turn_usage, duration_s=elapsed, kind=None)

            messages.append({"role": "assistant", "content": content})   # NGUYÊN VĂN, kể cả thinking + chữ ký
            if reason == "pause_turn":
                continue   # phòng thủ: ta không dùng server tool; gửi lại nguyên lịch sử để model tiếp tục

            if not uses:   # end_turn mà chưa gọi tool nào
                if nudges >= 1:
                    raise fail(client.LLMError("bad_output", f"kết thúc mà không gọi {finish_tool}"))
                nudges += 1
                messages.append({"role": "user", "content": [{"type": "text", "text": f"You stopped without calling `{finish_tool}`. Continue the task and finish by calling `{finish_tool}`."}]})
                continue

            results, finished = [], False
            for block in uses:
                name = block.get("name") if isinstance(block.get("name"), str) else ""
                calls[name if name in by_name else "<unknown>"] = calls.get(name if name in by_name else "<unknown>", 0) + 1
                outcome = _run_tool(by_name.get(name), validators.get(name), block.get("input"))
                categories |= outcome.categories
                result = {"type": "tool_result", "tool_use_id": block.get("id"), "content": outcome.content}
                if outcome.is_error:
                    result["is_error"] = True
                results.append(result)
                if outcome.stop and not outcome.is_error:
                    finished = True
                    break   # đã kết thúc: các tool_use còn lại của lượt này không chạy, và cũng không còn request nào để trả kết quả
            if finished:
                break
            left = budget.max_turns - turns
            if left in (budget.warn_at_turns_left, 1) and left >= 1:
                results.append({"type": "text", "text": f"Budget notice: {left} turn(s) left. Submit what you still have and call `{finish_tool}` now."})
            messages.append({"role": "user", "content": results})

    run = AgentRun(stop=stop or "finished", turns=turns, usage=usage, model=last_model, duration_s=_monotonic() - started,
                   tool_calls=dict(sorted(calls.items())), nudges=nudges, cost_usd_est=cost())
    event(log, "llm.agent", logging.INFO, purpose=purpose, model=last_model, stop=run.stop, turns=turns, nudges=nudges,
          tool_calls=run.tool_calls, cost_usd_est=None if run.cost_usd_est is None else round(run.cost_usd_est, 4), duration_s=round(run.duration_s, 3),
          input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
          cache_creation_input_tokens=usage.cache_creation_input_tokens, cache_read_input_tokens=usage.cache_read_input_tokens)
    return run
