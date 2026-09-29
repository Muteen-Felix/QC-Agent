# S1-05 · Render tất định: catalog → file trong repo SUT (plan S1.5)

- Branch: `feat/s1-05-gt-render`
- Tiền điều kiện: S1-02, S1-03 (worker `pytest`, capability `api.functional`) và S1-04 (fixture catalog) đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S1.5
- `src/qc_agent/scaffold/templates.py` (`render`, `TODO`, `todo_mark`, `api_contract_suite`) và thư mục `src/qc_agent/scaffold/tmpl/`
- `src/qc_agent/scaffold/init.py` (`apply`, `_write`: cách ghi file không đè và giữ owner)
- `src/qc_agent/scaffold/scan.py`, `src/qc_agent/scaffold/openapi.py` (dùng để dựng module-map nháp)
- `tests/fixtures/sut/noteboard/.qc-agent/suites/api-contract.yaml` (mẫu suite)

## Mục tiêu

Hàm thuần `render(catalog, …) -> list[RenderedFile]` sinh toàn bộ tài sản GT. Cùng input phải ra **byte giống hệt**. Không có chuỗi nào do LLM viết lọt vào code Python.

## Thiết kế đề xuất (có thể đổi nếu có lý do, nhưng phải nêu trong báo cáo)

Test được **điều khiển bằng dữ liệu**: file `.py` là template cố định, còn TC nằm trong `test-cases.yaml`. Nhờ vậy:
- QA chỉ sửa YAML (đổi `draft → approved`, thêm TC `origin: qa`) mà không cần render lại;
- chuỗi do LLM viết không bao giờ thành code;
- `gt validate` (S1-06) phát hiện được file `.py` bị sửa tay, bằng cách render lại trong bộ nhớ rồi so.

## File sinh ra (đường dẫn chốt trong `_common.md` §3)

| File | Nội dung |
|---|---|
| `.qc-agent/ground-truth/test-cases.yaml` | Catalog. Đầu file có comment hướng dẫn QA (ý nghĩa status, cách thêm `origin: qa`, `rejected_reason` bắt buộc). Đầu file cũng ghi `qc-agent:generated gt` |
| `.qc-agent/ground-truth/tests_gt/conftest.py` | **Runtime**, từ template `gt-conftest.py.tmpl`, chi tiết ở dưới |
| `.qc-agent/ground-truth/tests_gt/test_<story>.py` | Từ template `gt-api-functional.py.tmpl` (plan). Chỉ có `STORY_ID = "US-1"` và một test được parametrize qua fixture/hook của conftest. `<story>` là slug `[a-z0-9_]` sinh từ `story_id`, **không** lấy từ title |
| `.qc-agent/suites/gt-functional.yaml` | task `t-030` · capability `api.functional` · lane `gate` · `prefer: [pytest]` · `inputs.paths: [.qc-agent/ground-truth/tests_gt]` · oracle `threshold`: `pytest.failures == 0`, `pytest.errors == 0`, `pytest.tests >= 1` · `target.base_url: ${env.APP_BASE_URL}` · `retry: {max: 1, "on": [error]}` |
| `.qc-agent/suites/api-contract.yaml` | **Chỉ tạo khi chưa có**, bằng `templates.api_contract_suite(...)`. Không bao giờ ghi đè suite có sẵn nếu không có `force` |
| `.qc-agent/ground-truth/module-map.yaml` | Nháp, `status: draft`. Mỗi module là một segment tĩnh đầu tiên của path OpenAPI (`/notes/…` → `notes`). `paths` lấy từ `scan.py` nếu nó biết file khai báo route; không biết thì để placeholder kèm `qc-agent:todo VERIFY`. `suites: [api-contract, gt-functional]` |

Runtime trong `conftest.py`:
- Đọc `../test-cases.yaml` theo đường dẫn tương đối với file, lọc `status == "approved"` và đúng `story_id` → `parametrize`, với ids là `tc_id`.
- Chạy từng bước bằng `httpx`:
  - dùng `APP_BASE_URL`; thiếu biến này thì gọi `pytest.exit(..., returncode=4)` để adapter tính là `error`, không phải `fail`;
  - timeout 10s mỗi request, không follow redirect.
- `request.path` là template OpenAPI (`/notes/{note_id}`): `{name}` được điền từ `path_params`, giá trị đã URL-quote. Thay `{{var}}` chỉ trong `path_params`, `query` và `json`. **Không có `eval`.** Vắng `json` = không gửi body.
- Đánh giá assertion theo bộ đóng ở S1-02. Thông báo khi fail ngắn gọn: `tc_id`, bước, và kỳ vọng so với thực tế đã cắt ngắn.
- Chỉ dùng `httpx`, `PyYAML`, `pytest`; cả ba đều có trong image.

## Việc cần làm

1. **`src/qc_agent/groundtruth/render.py`**:
   - `render(catalog: dict, *, sut_root: Path, openapi: Analysis | None = None, force: bool = False) -> list[RenderedFile]` là hàm thuần.
   - `write(files, sut_root, *, force=False)` dùng lại cách ghi của `scaffold/init.py`, thay vì tự viết cái mới.
2. **Template mới** trong `src/qc_agent/scaffold/tmpl/`: `gt-api-functional.py.tmpl` và `gt-conftest.py.tmpl`, render qua `templates.render`. Cập nhật `template_names()` nếu có test liệt kê template.
3. **Tất định**:
   - sắp xếp ổn định; `yaml.safe_dump(..., sort_keys=False, allow_unicode=True)` với thứ tự khoá cố định;
   - LF, UTF-8 không BOM, không timestamp;
   - header chỉ ghi `prd.sha256`, `prompt_version`, `model`.
4. **Mọi thứ sinh ra mang `status: draft`**: cả catalog, từng TC, và module-map.

## Test bắt buộc: `tests/test_gt_render.py`

- **Golden**: render catalog lấy từ fixture S1-04 (qua `generate` với `MockTransport`), rồi so **từng byte** với `tests/fixtures/gt/noteboard/expected/**`. Render hai lần phải giống hệt nhau.
- **Chống chèn code**: story title và TC title chứa `"); import os; os.system("x") #`, `{{`, `</script>`, xuống dòng. Không file `.py` nào chứa các chuỗi đó, và tên file vẫn là slug hợp lệ.
- Suite sinh ra qua được `core/plan.resolve` (schema task hợp lệ, capability có trong `capabilities.json`).
- Suite `api-contract` có sẵn → không bị ghi đè.
- **Tích hợp runtime**, mark `slow` nếu repo có marker đó:
  - render vào `tmp_path` và đặt một vài TC là `approved`;
  - chạy toyapp **sạch** bằng `python -m uvicorn --app-dir tests/fixtures/sut/noteboard toyapp.app:app --port <cổng trống>` với `QC_BUGS=none`;
  - chạy `python -m pytest <tmp>/.qc-agent/ground-truth/tests_gt` với `APP_BASE_URL`.

  Kỳ vọng: TC `approved` chạy và pass; TC `draft` không chạy; thiếu `APP_BASE_URL` → exit 4.

## Ngoài phạm vi

CLI và merge khi regen (S1-06); đưa GT vào noteboard và bật suite trong policy (S1-08).
