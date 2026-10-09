# S4-05b · Test tích hợp đường Select THÀNH CÔNG trong container (plan S4.5b, nợ từ S2.10)

- Branch: `test/s4-05b-select-success`
- Tiền điều kiện: Sprint 3 đã nghiệm thu. Không phụ thuộc S4-01…S4-03, nên **làm được ngay**, là việc đầu tiên của Sprint 4. Máy cần Docker và image qc-agent (`QC_TEST_DOCKER_IMAGE`).

## Đọc trước

- `docs/prompts/_common.md`; plan S4.5b và DoD S2 (dòng "Diff chỉ docs thì chỉ chạy floor", "`selection.json` có trong artifact, lý do chọn hiện trong report")
- `.github/workflows/qc-gate.reusable.yml`: bước "Select (PR)" (đọc `github.event.pull_request.base.sha`/`head.sha`, `ok=true|false`) và bước gate (cảnh báo `::warning::Select failed; gate runs full set` khi `SELECT_OK` khác `true`)
- `tools/run_reusable_locally.py` (`run_workflow`, `--sha`, `--pr`, cách dựng `github.event`)
- `tests/test_reusable_workflow.py` (`run(...)`: event giả chỉ có `head.sha = "abc1234def5678"`, không có `base.sha`), `tests/test_selector_cli.py` (docs → `source=rules`)
- `tools/eval_selector.py: _git` (cách dựng repo git tạm có commit thật)
- `docs/prompts/README.md`, hiệu chỉnh #7 (diff lấy từ merge-base) và #16 (`docker build` lỗi TLS: dùng image dựng sẵn)

## Mục tiêu

Hiện tại, trong mọi test container, bước Select **luôn** lùi về FULL SET vì SHA là giả. Nghĩa là đường Select thành công chưa từng được kiểm trong container. Bước này kiểm đường đó, **không gọi LLM** (diff chỉ docs đi đường `rules`), nên không tốn tiền.

## Việc cần làm

1. **SUT mẫu là git repo thật**
   - Trong test, chép `tests/fixtures/sut/noteboard` sang thư mục tạm, `git init`, commit làm `base`; sửa một file docs (vd `README.md` hoặc `docs/*.md` khớp `docs_paths` của policy noteboard) rồi commit làm `head`.
   - Không sửa fixture gốc; không commit thư mục `.git` nào vào repo qc-agent.
2. **`tools/run_reusable_locally.py`**
   - Thêm `--base-sha` (và dùng `--sha` sẵn có làm head) để `github.event.pull_request` có đủ `base.sha` và `head.sha` thật. Mặc định giữ hành vi cũ (không có `base.sha`), để test hiện có không đổi.
   - Ghi trong docstring: thiếu `base.sha` thì Select lùi về FULL SET, đúng như trước.
3. **Test mới** `tests/test_select_success_container.py` (mark giống `test_reusable_workflow.py`: skip kèm lý do khi thiếu Docker hoặc `QC_TEST_DOCKER_IMAGE`; nếu dựng stack cần Postgres thì ghi rõ, còn không thì tránh phụ thuộc `QC_TEST_DATABASE_URL`). Assert:
   - bước "Select (PR)" có `ok=true`;
   - log **không** có `Select failed`;
   - `runs/<run>/selection.json` có `source: "rules"` và `suites` đúng bằng `["sast", "secrets"]` (chỉ floor);
   - report ghi nguồn `rules` ở mục "Phạm vi chạy";
   - không có lời gọi LLM: không cấp `ANTHROPIC_API_KEY`, và (nếu tiện) trỏ `ANTHROPIC_BASE_URL` vào `FakeAnthropic` rồi assert 0 lời gọi.

## Test bắt buộc

- Test mới ở trên (cần Docker; không có thì ghi rõ là skip trong báo cáo).
- `tests/test_reusable_workflow.py` vẫn xanh hoặc skip như trước (hành vi mặc định của harness không đổi).
- `tests/test_workflow_static.py` xanh.

## Ngoài phạm vi

Đường Select qua LLM (cần key thật, thuộc S4-06). Harness trọn chuỗi A–E (S4-05, xây tiếp trên `--base-sha` của bước này). Không sửa workflow.
