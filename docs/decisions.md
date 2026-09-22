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

## Schemathesis exit-code matrix

Đã hiệu chuẩn trên Schemathesis **4.27.5** bằng `scripts/test_step24_schemathesis.py`; mỗi lượt dùng tiến trình toyapp mới và port trống riêng. CLI đã xác nhận các cờ: `--max-examples 25` (mỗi operation), `--seed 1337`, `--checks`, `--exclude-path`, `--report junit`, `--report-junit-path`. Thêm `--generation-database none` để mỗi lượt không dùng lại generation database. Schemathesis vẫn lưu manifest crash riêng dưới `.schemathesis/<project>/cache/crashes`, nên runner chạy CLI từ thư mục `runs/step24/` bị Git ignore; JUnit được ghi bằng đường dẫn tuyệt đối.

`--checks all` phát hiện BUG-1 nhưng cũng tạo false positive ở `OPTIONS` (Allow header) và request body 400. Vì vậy hiệu chuẩn gate dùng đúng `not_a_server_error,response_schema_conformance`; bỏ riêng `POST /notes/{note_id}/summarize` khỏi lượt chạy, còn `GET /notes/{note_id}` và các thao tác tạo/list/xoá vẫn được kiểm tra.

| case | exit code | có báo cáo? | chỗ nào nói check nào hỏng |
|---|---:|---|---|
| bug_on | **1 trong 5/5** | Có, JUnit 5/5 | `GET /notes/{note_id}` có failure `Server error` / HTTP 500; `DELETE /notes/{note_id}` cũng thấy cùng BUG-1 |
| bug_off | **0 trong 5/5** | Có, JUnit 5/5, 0 failure | Không có check hỏng; còn một warning schema mismatch nhưng không đổi exit code |
| server_down | **1** | Có JUnit rỗng, `tests=0` | Không chạy check; stdout báo không tải được OpenAPI vì connection refused |

Hiệu chuẩn ban đầu với `QC_LONG_ID_LEN=64` và `32` không tái hiện BUG-1 cho seed `1337`. Theo quy tắc hiệu chuẩn STEP 24, mặc định trong `toyapp/app.py` được hạ xuống **16**; tại giá trị này BUG-1 vẫn là lỗi cài sẵn (id dài hơn ngưỡng trả 500) và bắt được mục tiêu 5/5. Cần nêu rõ thay đổi ngưỡng khi review kết quả.

Mẫu đã lưu: `tests/samples/st_bug_on.txt`, `st_bug_on.junit.xml`, `st_bug_off.txt`, `st_bug_off.junit.xml`, `st_server_down.txt`. Báo cáo đủ 11 lượt được giữ cục bộ trong `runs/step24/` (thư mục bị loại khỏi Git).

## Schemathesis adapter parsing

Ở Schemathesis 4.27.5, JUnit failure text không ghi tên check: `not_a_server_error` được nhận diện bằng tiêu đề `Server error` cùng status 5xx; `response_schema_conformance` bằng tiêu đề `Response violates schema`. Failure không khớp các dạng đã biết, report thiếu/hỏng, report rỗng/thiếu operation testcase hoặc exit code mâu thuẫn với JUnit đều thành `AdapterParseError` (`status=error`), không bị đổi thành fail/pass. Để tránh report thiếu bị hiểu là toàn bộ check đã pass, adapter đối chiếu số operation trên stdout, testcase trong JUnit và các bộ đếm XML. Schemathesis tạo JUnit hợp lệ nhưng rỗng khi server không kết nối được; adapter cũng xử lý trường hợp đó như lỗi worker. Mỗi lượt tạo config riêng trong workdir với `[cache] enabled = false`; `--generation-database none` vẫn tắt riêng kho ví dụ sinh.

## k6 exit-code matrix

Đã chạy trên macOS `darwin/arm64` với k6 **v2.2.0**, khớp `.k6-version`. `k6 run --help` xác nhận các cờ `--vus`, `--duration`, `--summary-export`; `tests/perf/notes_list.js` chỉ kiểm tra HTTP 200 và không có threshold. Script `notes_list_thresholds.js` dùng riêng cho hiệu chuẩn.

| case | exit code | có file summary? |
|---|---:|---|
| pass | **0** | Có |
| threshold_fail | **99** | Có |
| script_error | **107** | Không |
| server_down | **99** | Có |

Với toyapp sạch (`QC_BUGS=none`, `QC_LATENCY_MS=0`), lượt pass đo được p95 **5.56 ms** và failed rate **0.0**. Với `QC_LATENCY_MS=400`, lượt threshold_fail đo p95 **417.25 ms**, failed rate **0.0**; chỉ threshold p95 bị vượt. Khi server down, summary vẫn được ghi với p95 **0 ms** và failed rate **1.0**. Script cú pháp sai dừng trước khi tạo summary.

Trong summary JSON thật, key nguồn là `metrics.http_req_duration["p(95)"]` (đơn vị ms) và `metrics.http_req_failed.value` (tỉ lệ 0–1; ánh xạ sang metric chuẩn `http_req_failed.rate`). Giá trị này được xác nhận từ summary và stdout của k6 v2.2.0; không dùng tên key minh họa trong ví dụ kiến trúc thay cho dữ liệu thật.

Mẫu đã lưu và được Git track: `tests/samples/k6_summary.pass.json`, `k6_summary.threshold_fail.json`, `k6_summary.server_down.json`, `k6_stdout.script_error.txt`. Lượt server-down 10 VU/30 giây tạo nhiều log `connection refused`; khi chạy lại thủ công có thể dùng `--quiet --log-output=none` để tránh in các cảnh báo lặp lại.

## DeepEval matrix

Đã kiểm tra với DeepEval **4.2.3**. `assert_test` chạy metric tự viết mà không cần key; pytest exit code và JUnit phản ánh đúng case tất định đỏ. G-Eval trực tiếp dùng `GeminiModel` với `gemini-3.5-flash-lite`; lần smoke test trả điểm **1.0**. DeepEval không hỏi đăng nhập, không mở trình duyệt và không chờ prompt tương tác trong các lệnh đã chạy. `deepeval test run` ghi JSON vào `DEEPEVAL_RESULTS_FOLDER`; adapter STEP 37 dùng pytest/JUnit làm nguồn verdict và không phụ thuộc file JSON này. DeepEval tự nạp `.env`, vì vậy lượt no-key phải đặt `DEEPEVAL_DISABLE_DOTENV=1`, không chỉ xóa key khỏi process environment.

| case | exit code | có JUnit? | ghi chú |
|---|---:|---|---|
| metric_pass | **0** | Có | `test_not_empty[ok]` pass khi chạy riêng; không cần key |
| metric_fail | **1** | Có | `test_not_empty[empty]` có `<failure>`; ca đỏ được tạo có chủ đích |
| geval_with_key | **0** | Có | G-Eval gọi Gemini thật, `GEVAL_SCORE 1.0`; không cần login Confident AI |
| geval_no_key | **1** | Có | G-Eval skip khi dotenv bị tắt; metric pass vẫn chạy và ca `empty` cố ý fail |

Mẫu đã lưu: `tests/samples/de_junit_mixed.xml`, `de_junit_pass.xml`, `de_geval_stdout.txt`. `deepeval test run` được kiểm tra riêng khi tắt dotenv/telemetry; nó lưu report JSON ở `runs/step35/deepeval-results/`. `workers/deepeval.yaml` buộc tắt telemetry của DeepEval để dữ liệu gửi ra ngoài chỉ đi qua judge đã khai báo. `GeminiModel` cần gói `google-genai`; gói này đã được thêm vào dependency và lock sau khi smoke test phát hiện môi trường ban đầu thiếu SDK.

## Midscene STEP 29 live run

Ngày 2026-09-22, lỗi `document-start-failed` đã được truy tới `puppeteer.launch()`: Chrome GPU process crash lặp lại với exit `-1073741790` (`0xC0000022`, Access Denied), sau đó Chrome báo `GPU process isn't usable`. Phép thử Puppeteer tối thiểu cũng timeout 30 giây; thêm duy nhất `--no-sandbox` thì browser launch thành công trong khoảng 1.4 giây. Hai flow STEP 29 khai báo cờ này bằng `web.chromeArgs`.

Sau bản sửa browser, canary chạy thật qua model và cho đúng `status=fail`, `gating=false`, finding `implicit_signal:element_not_found`; result qua schema. Ca no-key cần tạm chuyển `.env` ra ngoài thư mục làm việc vì Midscene tự nạp file này; adapter đã được bổ sung nhận diện `Model configuration is incomplete` là lỗi hạ tầng, nên kết quả đúng `status=error`. Adapter cũng xoá summary cũ trước mỗi run để không đọc nhầm output của lượt trước.

| run | status | findings | kết luận |
|---|---|---:|---|
| explore | error | 0 | browser đã chạy; key mới thêm được note (`render_done`, count 1) rồi Gemini 429 trước bước xoá |
| canary | fail | 1 | đúng `element_not_found`, non-gating |
| no_key | error | 0 | đúng lỗi cấu hình model thiếu; phải cô lập `.env` |

Chưa chạy bảng biến thiên explore 3 lần vì Gemini trả `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, giới hạn 20 request/ngày. Đổi API key nhưng quota vẫn cạn sau canary và các request của explore (key có thể cùng project/quota); flow đã được tách thành bốn `aiAct` ngắn và `max_steps` giảm còn 8. Cần chờ quota ngày reset hoặc dùng key thuộc project có billing/quota riêng rồi chạy lại; không dùng fixture STEP 28 để giả làm số đo live.
