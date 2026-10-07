"""Dòng usage của từng lời gọi LLM và dòng chi phí hiển thị trong report/Check Run (S4-03). Module THUẦN DỮ LIỆU: không import `qc_agent.llm`, nên `core/` dùng được.
Giá (`est_usd`) KHÔNG được tính ở đây: selector/GT tính bằng `llm/prices.py` rồi ghi vào artifact; chỗ này chỉ cộng các số đã có.

`llm_usage.json` là MẢNG JSON, mỗi lời gọi ĐÃ GỬI một phần tử (kể cả lời gọi bị từ chối do output sai schema), KHÔNG có nội dung (prompt/response/rationale):
  purpose, model, prompt_version, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, est_usd, cache_hit, duration_s
cộng các trường tuỳ chọn, chỉ xuất hiện khi khác mặc định: `status` (`ok` mặc định; hoặc `LLMError.kind`), `usage_known` (`false` khi đã gửi mà không có response),
`unknown_calls` (số lời gọi đã gửi mà chưa biết usage), `turns` (dòng tổng của agent GT: MỘT dòng cho cả lần chạy, vì `AgentRun` chỉ có usage tổng).

`null` luôn nghĩa là KHÔNG CÓ DỮ LIỆU, không bao giờ là 0:
  - `duration_s`: thời gian client chờ MỘT request HTTP (không gồm ngủ backoff của Gemini). `null` ở dòng Selector (thời lượng không lưu vào `selection.json` để khỏi
    đổi `plan_id`; xem `llm.call` trong log bước Select) và ở mọi dòng `cache_hit: true` (lần này không có request). Không phải "0 giây".
  - token/`est_usd`: `null` khi `usage_known: false` (đã gửi nhưng không có response: timeout, đứt kết nối; server có thể đã tính phí) hoặc model không có trong bảng giá
    (mọi `gemini-*`). Dòng `cache_hit: true` giữ số của LẦN TẠO để minh bạch nhưng KHÔNG được cộng vào tổng và không tính tiền.
[Assumption, chưa kiểm chứng] response lỗi HTTP (4xx/429/5xx có body lỗi của API) không bị tính phí nên không sinh dòng nào.
"""
from __future__ import annotations

USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def row(*, purpose: str, model: str | None, prompt_version: str | None, usage: dict | None, est_usd: float | None, cache_hit: bool = False,
        duration_s: float | None = None, status: str = "ok", unknown_calls: int = 0, turns: int | None = None) -> dict:
    """Một dòng `llm_usage.json`. `usage=None` nghĩa là chưa biết (token thành `null`, `usage_known: false`)."""
    known = usage is not None
    out = {"purpose": purpose, "model": model, "prompt_version": prompt_version}
    for field in USAGE_FIELDS:
        out[field] = int(usage.get(field, 0) or 0) if known else None
    out.update(est_usd=est_usd if known else None, cache_hit=bool(cache_hit), duration_s=duration_s)
    if status != "ok":
        out["status"] = status
    if not known:
        out["usage_known"] = False
    if unknown_calls:
        out["unknown_calls"] = int(unknown_calls)
    if turns is not None:
        out["turns"] = int(turns)
    return out


def selection_rows(selection: dict | None) -> list[dict]:
    """Dòng của Diff Agent suy ra từ khối `llm` của `selection.json` (nguồn duy nhất cho gate: Select chạy ở bước riêng, `core` chỉ đọc số)."""
    llm = (selection or {}).get("llm")
    if not isinstance(llm, dict):
        return []
    known = llm.get("usage_known", True) is not False
    return [row(purpose="diff-select", model=llm.get("model"), prompt_version=llm.get("prompt_version"),
                usage={field: llm.get(field, 0) for field in USAGE_FIELDS} if known else None, est_usd=llm.get("est_usd") if known else None,
                cache_hit=bool(llm.get("cache_hit")), duration_s=None, status=llm.get("status", "ok"), unknown_calls=0 if known else 1)]


def summarize(rows: list[dict]) -> dict:
    """Tổng của các dòng đã TỐN thật: bỏ dòng cache hit (đã tính ở lần tạo). `est_usd` là CẬN DƯỚI khi còn `unknown_calls` hoặc `unpriced_models`."""
    total = {"calls": len(rows), "cache_hits": 0, "prompt_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "est_usd": 0.0, "priced_calls": 0,
             "unknown_calls": 0, "unpriced_models": []}
    unpriced = set()
    for item in rows:
        if item.get("cache_hit"):
            total["cache_hits"] += 1
            continue
        total["unknown_calls"] += int(item.get("unknown_calls", 0) or 0)
        if item.get("usage_known") is False or item.get("input_tokens") is None:
            continue
        total["prompt_tokens"] += item["input_tokens"] + item["cache_creation_input_tokens"] + item["cache_read_input_tokens"]
        total["cache_read_tokens"] += item["cache_read_input_tokens"]
        total["output_tokens"] += item["output_tokens"]
        if isinstance(item.get("est_usd"), (int, float)):
            total["est_usd"] += item["est_usd"]
            total["priced_calls"] += 1
        else:
            unpriced.add(str(item.get("model") or "?"))
    total["unpriced_models"] = sorted(unpriced)
    total["est_usd"] = round(total["est_usd"], 6)
    return total


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _money(value: float) -> str:
    return f"{value:.4f}" if 0 < value < 0.01 else f"{value:.2f}"


def cost_line(*, elapsed: str, summary: dict, worker_tokens: int = 0, worker_tasks: list[str] | None = None, worker_usd: float = 0.0) -> str:
    """MỘT dòng chi phí duy nhất cho report.md, Check Run và comment (cùng chuỗi, một con số tiền).

    `~$` = tổng `cost.usd` của worker + `est_usd` của các lời gọi LLM đã biết giá, là CẬN DƯỚI khi có hậu tố. Không bao giờ in `$0.00` khi còn lời gọi chưa xác định
    (đã gửi, không có response): khi phần đã biết bằng 0 thì in `~$? (chưa rõ)`."""
    parts = [f"wallclock {elapsed}",
             f"LLM: {_number(summary['prompt_tokens'])} in ({_number(summary['cache_read_tokens'])} từ cache) / {_number(summary['output_tokens'])} out"]
    if worker_tokens > 0:
        parts.append(f"worker: {_number(worker_tokens)} token (tasks: {', '.join(worker_tasks or []) or '—'})")
    cost = worker_usd + summary["est_usd"]
    notes = []
    if summary["unpriced_models"]:
        notes.append("chưa gồm giá của " + ", ".join(summary["unpriced_models"]))
    if summary["unknown_calls"] and cost <= 0:
        money = "~$? (chưa rõ)"   # đã nói đủ: không thêm hậu tố đếm
    else:
        money = f"~${_money(cost)}"
        if summary["unknown_calls"]:
            notes.append(f"+{summary['unknown_calls']} lời gọi timeout/lỗi mạng chưa rõ chi phí")
    parts.append(money + (f" ({'; '.join(notes)})" if notes else ""))
    if summary["cache_hits"]:
        parts.append(f"{summary['cache_hits']} lần dùng cache (không tính phí)")
    return " · ".join(parts)
