# S1-01 · LLM client dùng chung (plan S1.1)

- Branch: `feat/s1-01-llm-client`
- Tiền điều kiện: S1-00 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan §0 (quyết định #5) và S1.1
- `src/qc_agent/scaffold/suggest.py`, hai hàm `call_llm` và `suggest_flows`. Đây là mẫu để làm theo: gọi httpx, ghi egress trước khi gửi, lỗi không kèm nội dung response.
- `src/qc_agent/core/egress.py`, `src/qc_agent/settings.py`, `src/qc_agent/logging_setup.py`, `tests/test_logging.py`

## Mục tiêu

Tạo một module duy nhất để gọi Claude Messages API qua `httpx`, dùng chung cho GT generator (S1-04) và Diff Agent (S2-04). Không thêm SDK (quyết định #5). Mọi output của LLM được ép vào JSON bằng tool-use và validate phía client.

## Interface (tên chốt trong `_common.md` §3)

```python
# src/qc_agent/llm/client.py
@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # prompt_tokens = input + cache_creation + cache_read (input_tokens chỉ là phần KHÔNG cache)

@dataclass(frozen=True)
class ToolCall:
    data: dict          # input của tool_use, ĐÃ validate theo schema đầy đủ
    usage: Usage
    model: str
    stop_reason: str
    duration_s: float

class LLMError(RuntimeError):   # .kind ∈ {missing_key, egress_denied, timeout, unavailable, bad_request, bad_output, refused}
    ...

def call_tool(*, purpose: str, model: str, system: str, user: str,
              tool_name: str, tool_description: str, input_schema: dict,
              egress_dir: Path, data_categories: list[str],
              max_tokens: int = 4096, timeout_s: float | None = None,
              policy: egress.EgressPolicy | None = None,
              transport: httpx.BaseTransport | None = None) -> ToolCall: ...
```

Tách `system` (phần tĩnh) và `user` (phần động) là có chủ đích: S4-03 sẽ gắn `cache_control` vào `system`. Đừng trộn dữ liệu động (thời gian, id) vào `system`.

## Sự thật về API (đã kiểm với tài liệu Claude API 2026-09; đừng dựa vào trí nhớ)

- **Request**: `POST {ANTHROPIC_BASE_URL or https://api.anthropic.com}/v1/messages`
  - Header: `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`.
  - Body: `model`, `max_tokens`, `system` (danh sách block `{"type":"text","text":…}`), `messages: [{"role":"user","content":[{"type":"text","text": user}]}]`.
  - Tool: `tools: [{"name", "description", "input_schema", "strict": true}]` và `tool_choice: {"type":"tool","name": tool_name}`.
- **`temperature`**: `claude-sonnet-5` **trả 400** nếu có tham số này, vì model đã bỏ sampling params. Chỉ gửi `temperature: 0` khi model khớp allowlist `_SAMPLING_OK` (bắt đầu với tiền tố `claude-haiku-4-5`).
  - Đây là hiệu chỉnh so với dòng "temperature=0" của plan: ghi vào docstring và báo lại trong báo cáo.
- **`thinking`**: không gửi. Sonnet 5 mặc định chạy adaptive; `budget_tokens` trả 400. Forced `tool_choice` vẫn hợp lệ với Sonnet 5 và Haiku 4.5 trên Claude API.
  - Ghi chú trong docstring: `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1` **từ chối** forced `tool_choice` (400). Nếu đổi sang model đó thì phải dùng `tool_choice: auto` + `strict` + kiểm có đúng một `tool_use`. Không cần cài đặt phần này bây giờ.
- **Response**:
  - `stop_reason` ∈ {`tool_use`, `end_turn`, `max_tokens`, `refusal`, …}.
  - `content[]` có block `{"type":"tool_use","name","input"}`.
  - `usage` gồm `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`. Khi thiếu trường nào thì coi là 0.
- **Mã lỗi**: 429 `rate_limit_error`, 500 `api_error`, 529 `overloaded_error` → `unavailable`. Các 4xx khác → `bad_request`.
- **Giới hạn của strict schema**:
  - mọi object phải có `additionalProperties: false`;
  - **không hỗ trợ** `minLength`, `maxLength`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, ràng buộc mảng phức tạp, và schema đệ quy.

  Vì vậy cần viết `wire_schema(schema)` để bỏ các keyword không hỗ trợ trước khi gửi, còn `call_tool` validate `input` nhận về theo **schema đầy đủ** bằng `jsonschema`. Client là nguồn chân lý.

## Việc cần làm

1. **`src/qc_agent/llm/__init__.py` và `client.py`**
   - `transport` được tiêm vào để test (`httpx.Client(transport=…)`).
   - Không retry tự động: caller tự quyết. Diff Agent có ngân sách thời gian chặt, còn GT generator có vòng "sửa một lần" riêng.
2. **Egress ghi TRƯỚC khi gửi**, giống `suggest_flows`:
   - `worker = SimpleNamespace(name=f"qc-agent-{purpose}", data_egress=data_categories)`;
   - `spec = {"task_id": purpose, "capability": f"llm.{purpose}", "target": {"base_url": base_url}}`;
   - gọi `egress.record(policy or LogOnlyPolicy(), egress_dir, spec, worker, attempt)`. Kết quả khác `allow` → `LLMError("egress_denied")` và **không có HTTP**.
3. **Phân loại lỗi** (mọi message đều không chứa body response, URL có khoá, hay prompt):
   - thiếu key → `missing_key`, không có HTTP;
   - `httpx.TimeoutException` → `timeout`;
   - 429/5xx/529 hoặc lỗi mạng → `unavailable`;
   - 4xx khác → `bad_request`;
   - `stop_reason == "refusal"` → `refused`;
   - `max_tokens`, không có `tool_use`, sai tên tool, hoặc vi phạm schema → `bad_output`.
4. **Log**: event `llm.call` gồm purpose, model, stop_reason, 4 số token, duration_s. Không có nội dung nào khác.
5. **`settings.py`** thêm `gt_model="claude-sonnet-5"`, `selector_model="claude-haiku-4-5-20251001"`, `llm_timeout_s=120.0` (env `QC_GT_MODEL`, `QC_SELECTOR_MODEL`, `QC_LLM_TIMEOUT_S`), và cập nhật docstring đầu file.
   - `ANTHROPIC_API_KEY` và `ANTHROPIC_BASE_URL` đọc bằng `os.environ` **lúc gọi**. Không đưa vào `Settings`: repr của pydantic có thể lộ khoá khi bị log.
6. **`.env.example`** thêm các biến trên, kèm comment ngắn.
7. **`CLAUDE.md`**: thêm một dòng env vào phần Commands.

## Test bắt buộc: `tests/test_llm_client.py`, dùng `httpx.MockTransport`

- Đường chạy thành công:
  - header và body đúng; `strict: true`; `tool_choice` là forced;
  - **không có `temperature` với `claude-sonnet-5`, có `temperature: 0` với `claude-haiku-4-5-20251001`**;
  - parse đủ `Usage`, kể cả hai trường cache.
- Mỗi `kind` lỗi có ít nhất một test: 429, 500, 529, timeout, 400, refusal, max_tokens, không có tool_use, sai tên tool, input sai schema.
- Thiếu key hoặc egress deny: transport **raise nếu bị gọi** (chứng minh không có HTTP), và `egress.jsonl` có một dòng ghi decision.
- `wire_schema` bỏ đúng các keyword không hỗ trợ. Một input vi phạm `maxLength` (thứ API không chặn được) vẫn bị client bắt, kết quả `bad_output`.
- Log và message của `LLMError` không chứa chuỗi đánh dấu trong prompt, không chứa API key giả, không chứa body response.
- Import `qc_agent.core.cli` và `qc_agent.core.engine` (chạy trong subprocess) **không** kéo `qc_agent.llm` vào `sys.modules`.

## Ngoài phạm vi

Prompt/tool cụ thể của GT và selector, `cache_control`, trần token, `count_tokens`, bảng giá (S4-03). Không gọi API thật.
