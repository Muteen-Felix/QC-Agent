# S1-06 · CLI `qc-agent gt generate | validate | regen` + chốt HITL (plan S1.7, S1.8 phần CLI)

- Branch: `feat/s1-06-gt-cli`
- Tiền điều kiện: S1-04 và S1-05 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S1.7, S1.8
- `src/qc_agent/core/cli.py: main` (mẫu import lười: `init`, `validate`, `doctor`)
- `src/qc_agent/scaffold/validate.py` (cách báo `qc-agent:todo`, exit code)
- `tests/fakes.py` (`FakeGitHub`, mẫu fake HTTP server bằng `http.server`)
- `src/qc_agent/groundtruth/{prd,generate,render}.py`

## Mục tiêu

Nối parse → generate → render thành CLI, cài thuật toán merge khi PRD đổi, và cài cổng HITL. Cổng HITL có ba lớp:
- `gt validate` trả exit 1 khi còn TC `draft`;
- test runtime chỉ chạy TC `approved` (đã có từ S1-05);
- `qc-agent validate` cũng chặn.

## Việc cần làm

1. **`src/qc_agent/groundtruth/cli.py: main(argv) -> int`**, và thêm nhánh `gt` vào `core/cli.py` theo kiểu import lười.
   - **`gt generate --prd FILE --sut-root DIR [--openapi FILE|URL] [--egress-dir DIR] [--summary-json FILE] [--force]`**
     - Nếu `.qc-agent/ground-truth/test-cases.yaml` đã tồn tại → exit 3, gợi ý dùng `regen` (trừ khi có `--force`).
     - Egress mặc định ghi vào `--egress-dir`, **không** ghi vào `.qc-agent/`. File đó không được lọt vào PR sinh GT.
     - `--summary-json` ghi số story/AC/TC, orphan, warning, `prd_sha256`, `model`, `prompt_version`, `usage`. Workflow S1-07 dùng file này để viết thân PR, và S4 dùng nó để làm cache. Stdout in bản tóm tắt cho người đọc.
   - **`gt validate --sut-root DIR`**
     - Exit **1** khi có một trong các lỗi sau:
       - còn TC `draft` hoặc catalog `status: draft`;
       - `rejected` thiếu `rejected_reason`;
       - trùng `tc_id`;
       - `ac_refs` trỏ tới AC không có trong catalog;
       - module-map còn `draft` hoặc còn `qc-agent:todo`;
       - **drift**: file sinh ra trong `tests_gt/` khác với bản render lại trong bộ nhớ.
     - Exit **0** kèm warning khi có orphan hoặc TC `approved` trỏ tới AC đã bị xoá.
     - Exit **3** khi file không đọc được hoặc sai schema.
   - **`gt regen --prd FILE --sut-root DIR [...]`**: sinh lại rồi merge theo `tc_id`:
     1. Giữ **nguyên văn** mọi TC `approved`, mọi TC `origin: qa`, và mọi TC `rejected`. Giữ `rejected` để LLM không đề xuất lại đúng TC đã bị loại, vì `tc_id` được băm theo nội dung.
     2. Bỏ các TC `draft` + `origin: llm` cũ, thay bằng tập ứng viên mới.
     3. Ứng viên có `tc_id` trùng với TC đang giữ thì bỏ ứng viên. Còn lại thêm vào với `status: draft`.
     4. Cập nhật stories/ACs theo PRD mới. TC `approved` trỏ tới AC đã mất: **không sửa TC**, chỉ báo trong phần tóm tắt.
     5. Catalog chuyển thành `status: draft` nếu còn bất kỳ TC draft nào.
   - **Exit code**: 0 thành công; 1 (chỉ `validate`); 3 cho lỗi input, LLM, egress, hoặc schema. Mọi `LLMError` và `GTError` đều ra exit 3, kèm thông điệp không chứa nội dung.
2. **`scaffold/validate.py`**: khi thư mục `.qc-agent/ground-truth/` tồn tại, chạy cùng bộ kiểm của `gt validate` (TC draft là lỗi). Nhờ vậy luồng `qc-agent validate` hiện có cũng chặn.
3. **`tests/fakes.py`**: thêm `FakeAnthropic`. Đây là HTTP server giả nghe ở `/v1/messages`:
   - phát response lấy từ file fixture;
   - kiểm có header `x-api-key` và `anthropic-version`;
   - đếm số lời gọi;
   - có chế độ trả 429/529/timeout.

   CLI test chạy qua `ANTHROPIC_BASE_URL`. Các bước S1-08, S2 và S4 sẽ dùng lại fake này.
4. **`CLAUDE.md`** (phần Commands) và `docs/core-rules.md`: thêm các lệnh `gt`.

## Test bắt buộc: `tests/test_gt_cli.py`

- **DoD S1 (fake LLM)**: `gt generate` trên PRD mẫu cho ra đúng bộ golden của S1-05. Chạy hai lần vào hai thư mục khác nhau → so từng byte giống hệt.
- **`regen`, bảo toàn 100%**: dựng catalog có trộn approved, qa, rejected và draft; đổi PRD (thêm, sửa, xoá AC) rồi regen. Mọi TC approved và `origin: qa` phải **giống hệt từng byte** (so bản `yaml.safe_dump` của từng entry). Thêm một property test với 50 catalog sinh ngẫu nhiên có seed cố định.
- **`validate`**: exit 1 khi còn draft, khi drift (sửa một dòng `.py`), khi rejected thiếu lý do. Exit 0 khi sạch. Exit 3 khi YAML hỏng.
- Lỗi LLM (429 qua `FakeAnthropic`), thiếu key, egress deny → exit 3, và không có file GT nào được ghi dở.
- Import `qc_agent.core.cli` (trong subprocess) không kéo `qc_agent.llm` hay `qc_agent.groundtruth` vào.
- Stderr của CLI không chứa chuỗi đánh dấu đặt trong PRD, cũng không chứa key giả.

## Ngoài phạm vi

Workflow CI và CODEOWNERS (S1-07); bộ GT đã duyệt cho noteboard (S1-08).
