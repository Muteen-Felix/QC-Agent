"""Dựng hội thoại nhiều lượt giả cho `llm/agent_loop.py` và Ground-Truth agent: response của Claude Messages API được soạn sẵn, phát lần lượt qua
`httpx.MockTransport`, mọi request được ghi lại (body đã parse + header) để test kiểm hình dạng, tính chỉ-nối-thêm của lịch sử và egress.

    transport, requests = scripted([tool_use_msg(("t1", "read", {"path": "a"})), end_turn_msg()])
Mỗi phần tử của script là: dict (HTTP 200 với JSON đó), int (HTTP lỗi với mã đó và body lỗi chuẩn), hoặc Exception (ném khi gửi).
"""
from __future__ import annotations

import copy
import json

import httpx

SIGNATURE = "sig-0123456789abcdef"


def usage(input_tokens=100, output_tokens=50, cache_creation=0, cache_read=0) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation, "cache_read_input_tokens": cache_read}


def thinking_block(signature: str = SIGNATURE) -> dict:
    """Opus 5.5 mặc định không trả chữ thinking (`display: omitted`) nhưng VẪN trả block kèm chữ ký; lịch sử phải giữ nguyên nó."""
    return {"type": "thinking", "thinking": "", "signature": signature}


def tool_use(call_id: str, name: str, data: dict) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": data}


def message(content: list[dict], stop_reason: str = "tool_use", model: str = "claude-opus-5-5", usage_: dict | None = None) -> dict:
    return {"id": "msg_fake", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": usage_ or usage()}


def tool_use_msg(*calls: tuple, thinking: bool = True, text: str | None = None, **kwargs) -> dict:
    """`calls`: (id, tên tool, input)."""
    content = ([thinking_block()] if thinking else []) + ([{"type": "text", "text": text}] if text else []) + [tool_use(*call) for call in calls]
    return message(content, "tool_use", **kwargs)


def end_turn_msg(text: str = "xong", **kwargs) -> dict:
    return message([{"type": "text", "text": text}], "end_turn", **kwargs)


def refusal_msg(**kwargs) -> dict:
    return message([], "refusal", **kwargs)


def max_tokens_msg(**kwargs) -> dict:
    return message([{"type": "text", "text": "dở"}], "max_tokens", **kwargs)


class Recorded:
    """Một request đã gửi: body đã parse (độc lập với các lần append sau của caller) và header."""

    def __init__(self, request: httpx.Request):
        self.body = json.loads(request.content)
        self.headers = {k.lower(): v for k, v in request.headers.items()}
        self.url = str(request.url)

    @property
    def messages(self) -> list[dict]:
        return self.body["messages"]


def scripted(script: list, *, requests: list | None = None) -> tuple[httpx.MockTransport, list[Recorded]]:
    """Transport phát `script` tuần tự. Hết script mà vẫn có request thì trả 599 (test sẽ thấy ngay)."""
    seen: list[Recorded] = [] if requests is None else requests
    queue = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(Recorded(request))
        if not queue:
            return httpx.Response(599, json={"error": {"type": "api_error", "message": "script đã hết"}})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"type": "invalid_request_error", "message": "SECRET-PROMPT-CONTENT"}})
        if isinstance(item, (bytes, str)):
            return httpx.Response(200, content=item)
        return httpx.Response(200, json=copy.deepcopy(item))

    return httpx.MockTransport(handler), seen
