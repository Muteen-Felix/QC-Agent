# S4-08 · Scanner Dockerfile của `init` cho monorepo: tìm thấy ≠ xác nhận (plan S4.8, nợ từ onboarding)

- Branch: `fix/s4-08-init-scanner-monorepo`
- Tiền điều kiện: Sprint 3 đã nghiệm thu. Độc lập với S4-01…S4-07, làm song song được với chúng. **S4-10 và S4-11 làm sau bước này** (cùng dùng hàm đọc `COPY`/`ADD` và helper marker mà bước này tạo ra).

## Đọc trước

- `docs/prompts/_common.md`; plan S4.8 (4 việc và "Bằng chứng khi đóng"), S4.10, S4.11
- `src/qc_agent/scaffold/scan.py`: `_dockerfile_candidates`, `_context_for`, `_scan_dockerfile`, `_rank_note`, `Finding`, `SKIP_DIRS`, docstring đầu file (dòng "giới hạn đã biết")
- `src/qc_agent/scaffold/init.py`: `build` (nhất là đoạn ghi `sut_dockerfile`, `sut_context`, `sut_port`, `sut_health_path` và `marks`), `_verify`
- `src/qc_agent/scaffold/templates.py`: `qc_workflow`, `_WORKFLOW_INPUTS`, `todo_mark`; `src/qc_agent/scaffold/validate.py` (chặn mọi `qc-agent:todo` còn trong workflow caller)
- `tests/test_scaffold_scan.py`, `tests/test_scaffold_init.py`, `tests/test_scaffold_validate.py`
- `docs/user-guide-sprint-1.md` mục 3 (đoạn "Chỉ thêm các tham số tuỳ chọn" và dòng bảng `--sut-dockerfile`, `--sut-context`), `docs/onboarding.md`

## Mục tiêu

`qc-agent init` tìm được Dockerfile API trong monorepo kiểu `apps/api-server/Dockerfile` (sâu 2 cấp) và đề xuất `--sut-context`, **nhưng không bao giờ coi "tìm thấy" là "biết chắc đó là Dockerfile của API"**. Trường hợp rõ ràng thì tự chọn; trường hợp mơ hồ thì hiện cảnh báo `qc-agent:todo VERIFY` đến được file caller để `validate` chặn tới khi người dùng xác nhận.

Sự thật cần biết:
- **VAHAN** (onboarding 2026-10-06): `apps/api-server/Dockerfile` có `COPY apps/api-server/pyproject.toml`, nên context đúng là **gốc repo**, không phải `apps/api-server`. Context không thể suy chỉ từ vị trí Dockerfile.
- **Lỗi hiện có ở `init.py`** (đã kiểm): khi `scan` chọn `Dockerfile` ở gốc giữa nhiều ứng viên, `scan` có `verify` nhưng `init` đặt `dockerfile = None` (vì bằng mặc định của workflow) rồi chỉ gắn marker `if dockerfile and _verify(...)`. Kết quả: caller không có `sut_dockerfile`, không có VERIFY, `validate` không chặn. Context cũng vậy: `context = None` khi là `.`, và chưa có marker `sut_context` nào được tạo.
- **Test hiện có chọn đúng nhờ may**: `web/Dockerfile` + `api/Dockerfile` chọn `api/` chỉ vì `a` đứng trước `w` theo thứ tự chữ, không vì nhận biết API. Docstring `scan.py` đang chấp nhận "chỉ MỘT ứng viên thì không có VERIFY (giới hạn đã biết)".

## Nguyên tắc: tìm thấy ≠ xác nhận

1. **Luật ưu tiên chỉ là gợi ý để sắp xếp**: vị trí, tên thư mục (`api|server|backend`), `EXPOSE`... không được mô tả là "nhận biết API", "chắc chắn", "đã xác định" trong thông báo, docstring hay docs.
2. Mỗi giá trị suy ra (Dockerfile, context) thuộc một trong ba loại: **có flag** (không VERIFY); **có bằng chứng loại trừ đủ** (không VERIFY); hoặc **VERIFY kèm danh sách ứng viên, lý do chọn và bằng chứng**.
3. **Quy tắc chung:** mọi `Finding` có `verify` phải đến được caller, **kể cả khi giá trị trùng mặc định của workflow** (khi đó vẫn khai input để gắn dấu). Không có `verify` thì vẫn không khai giá trị bằng mặc định (giữ nguyên `tests/test_scaffold_init.py` dòng "bằng mặc định thì không khai"). Cài đặt thành **một helper dùng chung** trong `init.py`/`templates.py`, không vá riêng từng input: S4-11 dùng lại nó cho `sut_health_path`.

## Việc cần làm

1. **Tìm Dockerfile sâu hơn.** `_dockerfile_candidates` thêm `apps/*/Dockerfile`, `services/*/Dockerfile`, `packages/*/Dockerfile` (sâu 2 cấp), xếp sau nhóm sâu 1 hiện có, thứ tự tất định.
   - **≥ 2 ứng viên** → ghi **danh sách ứng viên, lý do chọn và `qc-agent:todo VERIFY`**. Lý do ghi rõ là *gợi ý, chưa xác nhận*.
   - **Một ứng viên duy nhất nhưng có dấu hiệu là web hoặc service khác** → cũng VERIFY (hoặc yêu cầu `--sut-dockerfile`); thông báo nói "chưa chắc đây là API", không nói "đây là web". Danh sách dấu hiệu để người thực hiện đề xuất trong kế hoạch rồi ghi vào docstring; ví dụ gợi ý: thư mục tên `web|ui|frontend|client|admin`, `FROM nginx|httpd|caddy`, `npm run build`/`serve`, không có dấu hiệu framework API trong dependency của thư mục đó.
   - Có `web/Dockerfile` và `apps/api-server/Dockerfile`: luật "nông hơn thắng" sẽ chọn `web/`. Nêu cách xếp hạng trong kế hoạch (tín hiệu tên thư mục/`EXPOSE` chỉ để sắp xếp, vẫn VERIFY) và chờ tôi duyệt trước khi code.
   - **Gỡ** câu "giới hạn đã biết" ở docstring đầu `scan.py` và thay bằng nguyên tắc ở trên.
2. **Sửa mất cảnh báo ở `init.py`** theo quy tắc chung (mục 3 của Nguyên tắc) cho `sut_dockerfile` **và** `sut_context` (thêm marker `sut_context`; `sut_context` đã có trong `_WORKFLOW_INPUTS`).
3. **Build context**: kiểm nguồn `COPY`/`ADD` theo **từng context ứng viên** (thư mục chứa Dockerfile, rồi các thư mục cha tới gốc repo; với `docker/*Dockerfile*` thì `docker/` rồi gốc).
   - Bỏ qua `--from=...`, URL, nguồn chứa biến/glob không phân giải được; nhưng **ghi lại** là đã bỏ qua và vì sao.
   - Đúng **một** context khiến mọi nguồn cục bộ đều tồn tại → chọn, không VERIFY.
   - **Nhiều** context đều hợp lệ (vd `COPY . .` hợp lệ ở mọi cấp), hoặc không context nào hợp lệ, hoặc cú pháp không phân tích được (heredoc, nối dòng lạ, `ARG` trong đường dẫn) → **VERIFY**, liệt kê các context hợp lệ (nếu có); không khẳng định đã suy đúng.
   - **Cờ `--sut-dockerfile` không kèm `--sut-context`**: context vẫn được suy theo cách trên và có VERIFY khi mơ hồ (flag Dockerfile không có nghĩa là context chắc chắn). Cờ `--sut-context` do người dùng đặt: giữ luật "flag không VERIFY", nhưng nếu nguồn `COPY` không tồn tại tính từ context đó thì in **cảnh báo** (không lỗi; S4-10 nâng thành ERROR ở `validate`).
   - **Hàm kiểm trả về cấu trúc**, không chỉ `bool`/chuỗi, vì S4-10 và S4-11 dùng chung: danh sách nguồn `COPY`/`ADD` đã phân tích, mỗi nguồn được **phân loại** (thư mục cụ thể / tệp / `.` là cả context / glob / bỏ qua kèm lý do) cùng đường dẫn đã phân giải tính từ gốc repo; tập context hợp lệ; và có/không VERIFY ở context. S4-11 cần phân biệt "thư mục cụ thể" với "tệp" và với `.`.
4. **Test** (qua `scan`, và qua `init` + `validate` với các ca có VERIFY):

   | Cây mẫu | `scan` | `init` + `validate` |
   |---|---|---|
   | chỉ `apps/api-server/Dockerfile`, `COPY . .`, repo một service | chọn, không VERIFY ở Dockerfile; context mơ hồ (`COPY . .` hợp lệ nhiều cấp) → VERIFY ở context | dấu context vào caller dù context là `.`; `validate` chặn |
   | chỉ `apps/api-server/Dockerfile`, `COPY apps/api-server/pyproject.toml ...` (kiểu VAHAN) | context `.`, đúng một ứng viên, không VERIFY | không khai `sut_context` (bằng mặc định), không dấu |
   | `apps/api-server/Dockerfile`, `COPY pyproject.toml ...` (nguồn tính từ thư mục service) | context `apps/api-server`, không VERIFY | khai `sut_context`, không dấu |
   | **`Dockerfile` ở gốc + `web/Dockerfile`** | chọn gốc, VERIFY, 2 ứng viên | caller có **cả** `sut_dockerfile: "Dockerfile"` và dấu VERIFY nêu hai ứng viên; `validate` ERROR tới khi người dùng xoá dấu |
   | **`web/Dockerfile` + `apps/api-server/Dockerfile`** | VERIFY, danh sách đủ hai ứng viên, kết quả ghi là gợi ý | dấu vào caller; `validate` ERROR |
   | chỉ `web/Dockerfile` (có dấu hiệu SPA) | VERIFY "chưa chắc là API" | dấu vào caller; `validate` ERROR |
   | nhiều ứng viên sâu 2 cấp | chọn một, VERIFY | như trên |
   | `COPY` không phân tích được (heredoc, `ARG`) | VERIFY "không suy được context" | dấu `sut_context`, `validate` ERROR |
   | `--sut-dockerfile X` (không `--sut-context`) | không VERIFY ở Dockerfile; context theo luật trên | đúng như `scan` |
   | `--sut-context D` mà `COPY` không khớp | không VERIFY | cảnh báo, không lỗi |

   - Test cũ `web/Dockerfile` + `api/Dockerfile` giữ lại, thêm chú thích: kết quả thắng nhờ thứ tự chữ, không phải nhận biết API; thêm biến thể đảo tên để chứng minh luật không "ăn may".
   - Nếu phải đổi test hiện có thì **liệt kê trong báo cáo kèm lý do**, không sửa lặng lẽ.
5. **Tài liệu**: sửa `docs/user-guide-sprint-1.md` mục 3 (bảng tham số `--sut-dockerfile`, `--sut-context` và đoạn "Chỉ thêm các tham số tuỳ chọn") cho khớp luật mới: nói rõ "tìm thấy ≠ xác nhận", khi nào có VERIFY, cách chốt. Ví dụ gắn repo thật (`apps/api-server`) để S4-10 thay bằng ví dụ trung tính.
6. **Kế hoạch ≤ 15 dòng trước khi code** (cách xếp hạng ứng viên, danh sách dấu hiệu "có thể không phải API", cấu trúc trả về của hàm kiểm `COPY`) và chờ tôi duyệt.

## Test bắt buộc và nghiệm thu

- `pytest tests/test_scaffold_scan.py tests/test_scaffold_init.py tests/test_scaffold_validate.py -q` xanh.
- **Fixture `vahan-rpa` (`tests/fixtures/scan/vahan-rpa`) và `noteboard` không được thêm VERIFY mới** ở Dockerfile/context (để cảnh báo không mất giá trị vì xuất hiện thừa).
- Nghiệm thu bằng `qc-agent init --dry-run` trên các cây mẫu ở bảng trên, dán output từng cây vào báo cáo:
  - cây **rõ ràng** (một service, context chỉ hợp lệ ở một cấp) tự chọn đúng, **không** cảnh báo thừa;
  - cây **mơ hồ** (gốc + `web/`, `web/` + `apps/api-server`, `COPY . .`) hiện cảnh báo/VERIFY nêu ứng viên và lý do; **không** có dòng nào nói "đã xác định".
- Kết luận trong báo cáo nêu rõ cây nào tự chọn, cây nào bắt xác nhận.

## Ngoài phạm vi

- **Health path** (`_scan_health` và `sut_health_path`): chuyển sang **S4-11**. S4-08 không đụng `_scan_health`. Helper marker ở mục 3 của Nguyên tắc chỉ cần tổng quát đủ để S4-11 dùng lại.
- Kiểm `COPY` trong `qc-agent validate` và ba lỗi cấu hình khác (S4-10).
- Giới hạn phạm vi quét của `openapi_url`/`fastapi`/`cors` (cùng kiểu lỗi quét toàn repo, không thuộc S4-08 hay S4-11).
- Không đụng `core/`, contract, workflow.
