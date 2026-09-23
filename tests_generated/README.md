# tests_generated/

File trong thư mục này do `tools/auto_promote.py` sinh ra tự động từ một finding discovery của
Midscene (nút **🧬 Promote to Test Case** trên dashboard, hoặc chạy CLI trực tiếp). Mỗi finding
sinh ra **2 file**:

- `test_promoted_<slug>.py` — dùng `playwright.sync_api`. `.venv` của repo này **chưa có** gói
  Python `playwright` (không được cài thêm — xem `CLAUDE.md`), nên file này hiện tại sẽ
  **SKIP** khi chạy `pytest`. Cài `pip install playwright && python -m playwright install
  chromium` trong một venv khác (không phải `.venv` của repo) thì mới chạy thật được.
- `promoted_<slug>.spec.mjs` — dùng gói `playwright` đã có sẵn trong `node_modules/` (npm ci ở
  root) + Chromium đã cài sẵn (`ms-playwright`). Chạy được **ngay bây giờ**:
  ```powershell
  .\scripts\toyapp.ps1 start -Bugs "2"     # bật lại bug để xem test đỏ
  node tests_generated\promoted_<slug>.spec.mjs
  ```
  Exit 0 = pass, exit 1 = fail (assertion không đạt hoặc lỗi Playwright).

Thư mục này nằm **ngoài** `testpaths = tests` trong `pytest.ini`, nên `pytest tests/` (suite gốc
của hệ thống) không bao giờ chạy vào đây và không bị ảnh hưởng bởi file được sinh ra.

## Cách "dịch" repro_steps sang code

`tools/auto_promote.py` dùng một **bảng template tất định** (không LLM) để map từng câu tiếng
Việt trong `repro_steps` / `suggested_assertion` sang lệnh Playwright, bám đúng DOM của
`toyapp/static/index.html` (`#title`, `#body`, nút "Xoá"). Bước nào không khớp bảng sẽ sinh
`pytest.fail(...)` / `assert.fail(...)` tường minh — **không bao giờ âm thầm cho pass**.

Giới hạn: bảng template chỉ phủ các thao tác của toyapp trong PoC này. `[EXTERNAL GAP]` — tổng
quát hoá cho ứng dụng bất kỳ đòi hỏi LLM codegen có người review, ngoài phạm vi course.

Mỗi file sinh ra đều có header ghi rõ `run_id`, `finding_id`, `detected_by`, sha256 của evidence
và thời điểm sinh, cùng dòng "GENERATED — review trước khi merge". Review nội dung trước khi
đưa vào test suite thật.
