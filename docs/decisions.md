# Decisions — STEP 07 (chốt contract qrs-v0.1)

> **Trạng thái xác nhận:** họp STEP 07 đã diễn ra; A (Nghĩa), B (Đức), C (Huy) đều xác nhận
> (xem bảng "Xác nhận" cuối file). Các dòng dưới là giá trị mặc định ở `plan-execution.md`
> mục 0.4, chốt nguyên trạng. Đổi sau freeze chỉ qua luật mục 0.2 (cả ba đồng ý + sửa cùng lúc mọi adapter).

## Kickoff D-01…D-10

D-01: ACCEPT — (a) tất định: `toyapp/summarizer.py` là stub thuần, g3 luôn fail `summary_shorter_than_body` — đề xuất bởi C
D-02: ACCEPT — gate 🟡 exit `0` + khối SKIPPED đầu report; cờ `--yellow-exit N`
D-03: ACCEPT — Python 3.11
D-04: ACCEPT — Node cùng major trên 3 máy, ≥ 22.12
D-05: ACCEPT — toy app `http://127.0.0.1:8000`
D-06: ACCEPT — file này không chọn model; team điền ở `.env`; model chấm ≠ model SUT
D-07: ACCEPT — UTF-8 ép ở mọi tiến trình; đọc JSON bằng `utf-8-sig`
D-08: ACCEPT — `inputs_schema` tuỳ chọn trong PoC
D-09: ACCEPT — rule chéo lane ↔ `expected_result_kind` bật
D-10: ACCEPT — `confidence` của `llm_judgment` chỉ lấy từ số tool thật sự báo; Midscene `aiAssert` không phát `llm_judgment`

## Review 3 cặp contract (2 câu hỏi bắt buộc `[R5 P3 slot 1]`)

Câu trả lời chốt tại họp, đối chiếu với `schemas/result.json` (`additionalProperties: false` ở mọi object) và các file `examples/`.

### Cặp tất định — `task.k6.json` ↔ `result.k6_pass.json`
1. Trường không có dữ liệu: `tool.version`, `verdict.confidence`, `verdict.rationale`, `determinism.seed` — đều `null` được.
2. Trường riêng cho worker này: **không**. Số liệu k6 (p95, error rate…) đi vào `evidence`/`raw_output`, không thêm trường.

### Cặp discovery — `task.midscene.json` ↔ `result.e2e_pass.json` + `result.e2e_fail.json` (canary)
1. Trường không có dữ liệu: `confidence`, `rationale`, `severity_hint`, `promote_candidate`, `determinism.seed`, `replay_cmd` — đều `null` được (Midscene chỉ trả đúng/sai, không có điểm ⟹ D-10).
2. Trường riêng cho worker này: **không**. Cấm thêm trường riêng `[R5 P4.1 #1]`.

### `result.ai_eval.json` — G3, G4
- Một result, hai `verdict_source` (`deterministic_assert` và `llm_judgment`).
- `gating` **chỉ** nằm ở `verdict`, không nằm ở finding `[arch §0.3 G3]`.
- Quy tắc vàng: adapter **được mất** thông tin, **không được bịa** (không điền hằng số cho `confidence`).

## Phân vai lệch (mục 0.3 #6)

A đồng ý chuyển: `core/registry.py` + `core/signature.py` → **C**; `core/verdict.py` + `core/report.py` → **B**.
A giữ `schema.py`, `evidence.py`, `plan.py`, `_base.py`, `runner.py`, `cli.py` và review tất cả.

## Xác nhận

| Người | Vai | Trạng thái |
|---|---|---|
| Nghĩa | A | ghi 2026-09-20 |
| Đức | B | xác nhận tại họp STEP 07, 2026-09-20 |
| Huy | C | xác nhận tại họp STEP 07, 2026-09-20 |

## Midscene exit-code matrix

Chạy thật bằng `@midscene/cli 1.13.0`, model `gemini-3.5-flash`, ngày 2026-09-21. CLI đặt file
`--summary runs/<name>.json` dưới `midscene_run/output/runs/`, không phải dưới `runs/` ở repo root.

| case | exit code | có file summary? | khoá/giá trị phân biệt pass–fail | ghi chú |
|---|---:|---|---|---|
| pass | 1 | có | `results[0].success=false`; `resultType=failed`; `error` chứa HTTP 429 | Không xác nhận được live pass: flow hai `aiAct` vượt Gemini free-tier 5 request/phút; retry cuối vẫn `RESOURCE_EXHAUSTED`. Sample thật mang tên `live_quota_error`, không gắn nhãn pass. |
| missing_element | 1 | có | `results[0].success=false`; `resultType=failed`; `error="Task failed: Không tìm thấy nút ..."` | Phân biệt được lỗi thiếu element từ `error`; có đường dẫn report HTML. |
| aiassert_false | 1 | có | `results[0].success=false`; `resultType=failed`; `error="Assertion failed: ..."` | `aiAssert` sai làm exit khác 0; summary không có confidence. |
| no_key | 1 | có | `results[0].success=false`; `resultType=failed`; `error="Timed out after waiting 30000ms"` | Đã nạp base/model/family, tạm ẩn `.env` và xoá riêng key; CLI không fail-fast theo lỗi missing-key mà timeout browser/run. |

Kết luận: `--summary` đủ để biết thành công/thất bại cấp file và phân biệt `missing_element`/`aiAssert`
qua chuỗi `error`, nhưng **không đủ trường có cấu trúc cho từng step để sinh đầy đủ `findings[]`**; không có
token/cost và chỉ có đường dẫn report HTML. `aiAssert` sai làm exit `1`. STEP 28 phải parse bảo thủ, được mất
thông tin nhưng không đoán; live pass chưa xác nhận nên dùng `midscene_summary.fixture.json` có nhãn **MOCK**.

`MIDSCENE_MODEL_FAMILY=gemini`. Chrome/Puppeteer headless là bắt buộc; không cần `--headed` cho các run trên.
