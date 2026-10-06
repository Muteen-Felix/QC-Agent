# S4-10 · `qc-agent validate` bắt ba lỗi cấu hình có thật + ví dụ trung tính trong docs (plan S4.10, nợ từ onboarding VAHAN)

- Branch: `fix/s4-10-validate-config-errors`
- Tiền điều kiện: **S4-08 đã merge** (dùng lại hàm kiểm nguồn `COPY`/`ADD` của S4-08, và cùng sửa `docs/user-guide-sprint-1.md`).

## Đọc trước

- `docs/prompts/_common.md`; plan S4.10 (toàn văn)
- `src/qc_agent/scaffold/validate.py`: mức ERROR/WARN/NOTE, `CODEOWNERS_FILES`, hàm kiểm CODEOWNERS (dòng ~144), khối Ground-Truth (dòng ~241: có `.qc-agent/ground-truth/` thì gọi kiểm của `gt validate`)
- `src/qc_agent/groundtruth/check.py` (`GTCheckError` "không có …" → exit 3)
- `src/qc_agent/scaffold/scan.py` (hàm kiểm `COPY`/`ADD` của S4-08), `src/qc_agent/scaffold/tmpl/CODEOWNERS.tmpl`
- `docs/groundtruth.md` §5e và `docs/user-guide-sprint-1.md` (dòng ~113: thêm `auth.yaml` **trước** `gt generate`; dòng ~141, 145, 151, 165: ví dụ `@my-org/qa-team` và `apps/api-server/Dockerfile`)
- `tests/test_scaffold_validate.py`

## Mục tiêu

Ba giá trị sai khi onboarding VAHAN đều qua được `validate` mà không bị báo, và hai trong đó do chép nguyên ví dụ trong docs. Bước này cho `validate` bắt cả ba, và sửa docs để không còn ví dụ dễ chép nhầm.

## Việc cần làm

1. **`COPY`/`ADD` không tồn tại tính từ `sut_context`**
   - Đọc `sut_dockerfile`/`sut_context` từ caller (`qc-gate.yml` hoặc `qc.yml`), dùng hàm của S4-08 để kiểm từng nguồn.
   - Nguồn không tồn tại → **ERROR** (build chắc chắn hỏng), thông báo nêu đường dẫn nguồn và context gợi ý nếu suy được (vd "context nên là `.`").
   - Bỏ qua `--from=...`, URL, nguồn có biến; `.dockerignore` không cần xử lý (chỉ kiểm tồn tại).
2. **Owner CODEOWNERS còn là mẫu**
   - Quy tắc `/.qc-agent/` trỏ tới owner mẫu trong docs (`@my-org/qa-team`, `@org/team`) hoặc còn dấu `qc-agent:todo` → **ERROR**: khoá QA trỏ vào team không tồn tại thì không ai duyệt được.
   - Danh sách owner mẫu đặt ở một hằng, có comment trỏ tới các chỗ docs dùng chúng.
3. **`ground-truth/` chỉ có `auth.yaml`**
   - Docs bảo thêm `auth.yaml` trước `gt generate`, nhưng hiện `validate` thấy thư mục `ground-truth/` là chạy kiểm GT và báo thiếu `test-cases.yaml`.
   - Thư mục chỉ có `auth.yaml` (chưa có `test-cases.yaml`) → **NOTE** "chưa sinh Ground-Truth, chạy `qc-agent gt generate`", không ERROR. Vẫn kiểm `auth.yaml` nếu đã có kiểm đó. Có `test-cases.yaml` thì hành vi giữ nguyên.
   - Yêu cầu CODEOWNERS khi đã có `ground-truth/` giữ nguyên (auth.yaml cũng phải được khoá).
4. **Docs**: thay ví dụ gắn với repo thật trong `docs/user-guide-sprint-1.md` (quanh dòng 145 và 165: `apps/api-server/Dockerfile`, `apps/api-server`) bằng ví dụ trung tính (vd `services/api/Dockerfile`), và ghi rõ team QA trong ví dụ `--qa-team` là chỗ phải thay (validate sẽ báo lỗi nếu giữ nguyên). Rà `docs/onboarding.md`, `docs/groundtruth.md` cho cùng hai ví dụ.

## Test bắt buộc

`tests/test_scaffold_validate.py`, mỗi lỗi một test dương và một test âm:
- Dockerfile `COPY apps/api-server/pyproject.toml` với `sut_context: apps/api-server` → ERROR; cùng Dockerfile với context `.` → không lỗi.
- CODEOWNERS `/.qc-agent/ @my-org/qa-team` → ERROR; team thật dạng `@acme/qa` → không lỗi.
- `ground-truth/` chỉ có `auth.yaml` → NOTE, exit không phải 3; thêm `test-cases.yaml` có TC draft → lỗi như cũ.
- Repo fixture `tests/fixtures/sut/noteboard` vẫn qua `validate` như trước (team giả `@Muteen-Felix/qa-team` không nằm trong danh sách mẫu).

## Ngoài phạm vi

Scanner của `init` (S4-08, đã xong). Dịch vụ DB phụ và cảnh báo `DATABASE_URL` (S4-09). Không đụng `core/` và contract.
