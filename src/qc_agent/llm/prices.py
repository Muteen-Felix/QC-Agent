"""Bảng giá LLM DUY NHẤT của qc-agent (S4-03). `agent_loop`, `tools/eval_groundtruth.py` và các nơi tính `est_usd` đều import từ đây: không có bảng thứ hai.

Giá là ước tính theo bảng giá đã cache, KHÔNG phải hoá đơn. Model không có trong bảng (kể cả mọi `gemini-*`) thì không ước tính được: `estimate_cost` trả `None`,
không đoán. `core/` không được import module này (`qc_agent.llm`): selector/GT tính `est_usd` rồi ghi vào artifact, `core` chỉ cộng số đã có.
"""
from __future__ import annotations

from qc_agent.llm.client import Usage

# USD / 1 triệu token (đầu vào, đầu ra, đọc cache); ghi cache = 1,25 × đầu vào. Giá cached 2026-09-25 từ tài liệu Claude API; model lạ thì không ước tính được cost.
# Khoá dài hơn xếp trước để `claude-sonnet-5-5` không bị bắt bởi `claude-sonnet-5`.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-opus-5": (5.0, 25.0, 0.50), "claude-sonnet-5-5": (2.0, 10.0, 0.20),
          "claude-sonnet-5": (2.0, 10.0, 0.20), "claude-haiku-4-5": (1.0, 5.0, 0.10)}
PRICES_DATE = "2026-09-25"
CACHE_WRITE_FACTOR = 1.25
_PRICE_KEYS = sorted(PRICES, key=len, reverse=True)


def estimate_cost(model: str, usage: Usage) -> float | None:
    key = next((k for k in _PRICE_KEYS if model.startswith(k)), None)
    if key is None:
        return None
    price_in, price_out, price_read = PRICES[key]
    total = (usage.input_tokens * price_in + usage.output_tokens * price_out + usage.cache_creation_input_tokens * price_in * CACHE_WRITE_FACTOR
             + usage.cache_read_input_tokens * price_read)
    return total / 1_000_000
