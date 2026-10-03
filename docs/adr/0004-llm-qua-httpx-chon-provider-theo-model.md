# ADR 0004: Gọi LLM qua `httpx`, không SDK; chọn provider theo tiền tố model
- Trạng thái: Accepted
- Ngày: 2026-10-03 (gốc: quyết định #5 của `implementation-plan.md` §0, mở rộng sang Gemini)

## Bối cảnh
`httpx` đã có trong dependency (cùng kiểu `scaffold/suggest.py`). Sprint 1 đo trên SUT thật bằng
Gemini free tier, và vòng lặp agent đọc repo cần kiểm soát chi phí/ngân sách chặt.

## Quyết định
- `llm/client.py` gọi Messages API (Claude) và `generateContent` (Gemini) bằng `httpx`; `llm/agent_loop.py` là vòng lặp tool-use viết tay. Không thêm SDK.
- Provider chọn theo tiền tố model: `gemini-*` → Gemini (`GEMINI_API_KEY`), còn lại → Claude (`ANTHROPIC_API_KEY`). Đổi provider = đổi `QC_GT_MODEL` / `QC_SELECTOR_MODEL`.
- Mặc định: GT `claude-sonnet-5`, agent GT `claude-sonnet-5-5`, Diff Agent `claude-haiku-4-5-20251001` (xem `settings.py`).
- Mọi lời gọi ra ngoài ghi egress **trước khi gửi**; policy `deny` thì không có request nào; log không chứa PRD, diff, prompt hay response.

## Hệ quả
- Phải tự xử lý khác biệt tham số theo model (ví dụ `temperature`, `tool_choice` forced bị một số model từ chối 400); ghi trong docstring `client.py` / `agent_loop.py`.
- Agent GT chưa từng chạy trên API Anthropic thật; lần smoke đầu tốn tiền và gửi PRD/mã ra ngoài, cần xác nhận câu hỏi #3 trong `implementation-plan.md` (mục chờ chốt).
- Chưa có: `count_tokens`, prompt caching, trần `QC_LLM_MAX_INPUT_TOKENS` (S4).
