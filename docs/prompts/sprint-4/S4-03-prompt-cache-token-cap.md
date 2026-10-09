# S4-03 · Prompt caching, trần token, `llm_usage.json`, chi phí trong summary (plan S4.3)

- Branch: `feat/s4-03-prompt-cache-budget`
- Tiền điều kiện: S4-02 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.3; DoD S4 (các dòng `cache_read_input_tokens > 0`, "vượt trần → FULL SET, gate không đỏ", "Check Run có dòng chi phí/PR"); README "Cần bạn quyết" #2
- `src/qc_agent/llm/client.py` (tham số `system` là phần tĩnh; nhánh Claude và nhánh Gemini `_call_gemini`), `src/qc_agent/llm/agent_loop.py` (`PRICES`, `estimate_cost`, `cache_control` đã có), `src/qc_agent/selector/agent.py` (phần tĩnh đã ổn định từng byte, có test ở S2-04), `src/qc_agent/groundtruth/{generate.py, agent.py}`
- `tools/eval_groundtruth.py: estimate_cost` (bản tính chi phí thứ hai), `src/qc_agent/settings.py` (`gt_agent_max_cost_usd`, `gt_agent_max_turns`, `gt_agent_max_wall_s`)
- `src/qc_agent/core/report.py: _cost_line`, `src/qc_agent/integrations/github.py: render_summary, check_title`

## Sự thật về API (tài liệu Claude API 2026-09; đừng dựa vào trí nhớ)

- **Cache là so khớp tiền tố** theo thứ tự `tools → system → messages`. Đổi một byte trong phần tiền tố là mất cache. Đánh dấu bằng `"cache_control": {"type": "ephemeral"}` (TTL 5 phút) trên block cuối của phần tĩnh. Tối đa 4 breakpoint.
- **Tiền tố tối thiểu để được cache**, ngắn hơn thì **im lặng không cache** (không báo lỗi, `cache_creation_input_tokens: 0`):
  - `claude-sonnet-5`: **1024** token;
  - `claude-haiku-4-5`: **4096** token.
- **Usage**: `input_tokens` chỉ là phần *không* cache. Tổng prompt = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
- **Giá tham chiếu** (2026-09-25; **kiểm lại trang pricing trước khi merge**), tính theo USD mỗi MTok:

  | Model | Input | Output |
  |---|---|---|
  | Sonnet 5 | 2 | 10 |
  | Haiku 4.5 | 1 | 5 |

  Ghi cache (TTL 5 phút) ≈ 1,25 × giá input. Đọc cache ≈ 0,1 × giá input.
- **`count_tokens`**: `POST /v1/messages/count_tokens` với cùng body nhưng không có `max_tokens`. Endpoint này **gửi nội dung ra ngoài**, nên phải ghi egress giống một lời gọi LLM.

## Những gì ĐÃ CÓ trong code (kiểm 2026-10-06; đừng làm lại)

- **Bảng giá** đã có ở `llm/agent_loop.py: PRICES` + `estimate_cost` (gồm Sonnet 5.5, Opus 5/5.5, Sonnet 5, Haiku 4.5; ghi cache = 1,25 × input). `tools/eval_groundtruth.py: estimate_cost` là bản tính thứ hai, nhận giá qua tham số.
- **Prompt caching của GT agent** đã có: `agent_loop.run_agent` gắn `cache_control` lên `system`, tool cuối và cấp cao nhất (lịch sử). S4-03 **không** sửa phần này, chỉ gom usage của agent vào `llm_usage.json`.
- **Gemini**: `client.call_tool` chọn provider theo tiền tố model (`gemini-*`); `.env.example` đang đặt mẫu Gemini cho bản miễn phí.
- **Dòng chi phí trong report**: `core/report.py: _cost_line` đã in `wallclock … · LLM tokens: N (tasks: …) · cost $X` từ trường `cost` của từng task worker.
- **`schemas/selection.json`** đã có `fallback_reason: token_cap` và khối `llm` có đủ 4 trường usage.

## HỎI TRƯỚC

Đo tiền tố tĩnh của Diff Agent trên noteboard (system + module-map + catalog + tool schema) bằng `count_tokens`. Có key thì dùng key; không có key thì ước lượng và ghi rõ là ước lượng. Nếu **< 4096**, DoD "`cache_read_input_tokens > 0` từ lần gọi 2" **không thể đạt** trên Haiku 4.5 với repo này. Báo số đo và để tôi chọn:
- (a) chấp nhận N/A với repo nhỏ, và kiểm DoD trên GT generator (Sonnet 5, ngưỡng 1024);
- (b) đổi `QC_SELECTOR_MODEL`;
- (c) chấp nhận không cache.

**Không** độn prompt để vượt ngưỡng.

## Việc cần làm

1. **`llm/client.py`** (chỉ nhánh Claude của `call_tool`; `agent_loop.py` đã xong)
   - `system` gửi dạng `[{"type":"text","text": system, "cache_control": {"type":"ephemeral"}}]`.
   - **Nhánh Gemini giữ nguyên request**: không gửi `cache_control`, không có `count_tokens`. Thêm test chứng minh body gửi Gemini không đổi.
   - Đảm bảo `tools` sắp xếp tất định: enum đã sort, khoá JSON ổn định.
   - Thêm `count_tokens(...)` dùng chung đường egress và phân loại lỗi. Model `gemini-*` → `ValueError` (lỗi lập trình), không gọi gì.
   - **Bảng giá một chỗ**: chuyển `PRICES` + `estimate_cost` từ `agent_loop.py` sang `src/qc_agent/llm/prices.py` (giữ ngày tham chiếu, giữ thứ tự khoá dài trước). `agent_loop.py` và `tools/eval_groundtruth.py` import từ đó. **Không** tạo bảng thứ hai.
2. **Trần `QC_LLM_MAX_INPUT_TOKENS`** (thêm vào `settings.py` và `.env.example`)
   - **Trước khi gọi**, ước lượng tất định và bảo thủ: số ký tự / 3. Cách này không tốn thêm một lời gọi và không gửi thêm dữ liệu.
   - Áp cho **cả hai provider**: ước lượng ký tự/3 không phụ thuộc provider.
   - Selector vượt trần → FULL SET với `fallback_reason: token_cap`, **không** gọi LLM. Gate không đỏ vì chi phí.
   - GT một lời gọi vượt trần → exit 3, kèm gợi ý tách PRD.
   - **GT agent không áp trần này**: agent đã có ngân sách riêng (`QC_GT_AGENT_MAX_COST_USD`, `QC_GT_AGENT_MAX_TURNS`, `QC_GT_AGENT_MAX_WALL_S`). Chồng hai trần lên agent sẽ cho hai lý do dừng mâu thuẫn.
3. **`runs/<run_id>/llm_usage.json`**, mỗi lời gọi một dòng:
   ```
   {purpose, model, prompt_version, input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens, est_usd, cache_hit, duration_s}
   ```
   - Không có nội dung.
   - Selector chạy ở bước riêng, nên ghi usage vào `selection.json`; gate chép sang `run_dir`.
   - Model lạ, không có trong bảng giá → `est_usd: null`. Không đoán. Mọi model `gemini-*` hiện rơi vào trường hợp này.
   - GT agent: một dòng cho mỗi lượt, hoặc một dòng tổng cho cả lần chạy kèm `turns`; chọn một và ghi vào docstring.
   - Bảng giá nằm ở **một** chỗ, `src/qc_agent/llm/prices.py` (xem mục 1).
4. **Report và GitHub**
   - **Mở rộng `_cost_line`**, không thêm dòng thứ hai: cộng usage của selector (từ `selection.json` trong `run_dir`) vào phần LLM, và hiện `<tổng prompt> in (<cache_read> từ cache) / <out> out · ~$<est>`. Report không được có hai con số chi phí khác nhau.
   - `render_summary` và Check Run có cùng dòng đó (DoD: "Check Run có dòng chi phí/PR").
   - `report.json` thêm khối `llm_usage` tổng hợp.
5. **Tài liệu**: `docs/usage-ci.md`, mục chi phí: cách đọc dòng chi phí, trần token, và ngưỡng cache theo model.

## Test bắt buộc

- `tests/test_llm_client.py` (mở rộng)
  - Có `cache_control` trên `system` (Claude); body gửi Gemini không có `cache_control`.
  - Hai request với hai `user` khác nhau có phần `tools + system` giống từng byte.
  - `count_tokens` ghi egress; deny thì không có HTTP.
- `tests/test_selector_agent.py` (mở rộng): vượt trần → FULL SET với `token_cap` và 0 lời gọi, với cả model Claude lẫn `gemini-*`.
- `tests/test_llm_usage.py`
  - `est_usd` đúng công thức với cả 4 trường usage.
  - Model lạ và model `gemini-*` → `null`.
  - `agent_loop` dùng bảng giá từ `llm/prices.py` (test cũ của agent vẫn xanh, không đổi số).
  - `llm_usage.json` không chứa chuỗi đánh dấu.
- `FakeAnthropic` giả lập `cache_read_input_tokens > 0` từ lần gọi 2 → report hiện "từ cache".

## Ngoài phạm vi

Tinh chỉnh pruner (S4-04). Không gọi API thật, trừ khi tôi đồng ý lượt đo ở mục HỎI TRƯỚC.
