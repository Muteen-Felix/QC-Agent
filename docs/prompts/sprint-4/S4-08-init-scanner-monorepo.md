# S4-08 · Scanner Dockerfile của `init` cho monorepo (plan S4.8, nợ từ onboarding)

- Branch: `fix/s4-08-init-scanner-monorepo`
- Tiền điều kiện: Sprint 3 đã nghiệm thu. Độc lập với S4-01…S4-07, làm song song được. S4-10 làm sau bước này.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.8 (đủ 4 việc và "Bằng chứng khi đóng")
- `src/qc_agent/scaffold/scan.py`: `_dockerfile_candidates`, `_context_for`, `_scan_dockerfile`, `_rank_note`, `SKIP_DIRS`
- `src/qc_agent/scaffold/init.py` (`--dry-run`), `tests/test_scaffold_scan.py`, `tests/test_scaffold_init.py`
- `docs/user-guide-sprint-1.md` mục 3 (dòng ví dụ `--sut-dockerfile` và dòng bảng `--sut-dockerfile`, `--sut-context`), `docs/onboarding.md`

## Mục tiêu

`qc-agent init` tự tìm đúng Dockerfile API trong monorepo kiểu `apps/api-server/Dockerfile` (sâu 2 cấp) và suy đúng `--sut-context`, thay vì báo lỗi hoặc chọn nhầm `web/Dockerfile`.

Sự thật cần biết (onboarding VAHAN, 2026-10-06): `apps/api-server/Dockerfile` của VAHAN `COPY apps/api-server/pyproject.toml`, tức context đúng là **gốc repo**, không phải `apps/api-server`. Vì vậy "suy đúng context" không thể chỉ lấy thư mục chứa Dockerfile.

## Việc cần làm

1. **`_dockerfile_candidates`**: thêm nhóm `apps/*/Dockerfile`, `services/*/Dockerfile`, `packages/*/Dockerfile` (sâu 2 cấp), xếp sau nhóm sâu 1 hiện có. Nhiều ứng viên vẫn chọn một và đánh dấu `qc-agent:todo VERIFY` như cũ. Giữ thứ tự tất định (theo nhóm rồi theo chữ).
   - Nêu trong kế hoạch (≤ 15 dòng) cách xếp hạng khi cùng lúc có `web/Dockerfile` (sâu 1) và `apps/api-server/Dockerfile` (sâu 2): luật hiện tại "nông hơn thắng" sẽ chọn `web/`. Đề xuất một tín hiệu tất định để ưu tiên Dockerfile API (vd tên thư mục chứa `api`/`server`/`backend`, hoặc Dockerfile có `EXPOSE` cổng API), rồi chờ tôi duyệt trước khi code.
2. **`_context_for`**: suy context cho Dockerfile sâu 2 cấp.
   - Đọc các nguồn của `COPY`/`ADD` (bỏ `--from=...`, URL, và nguồn chứa biến). Chọn context ứng viên (thư mục chứa Dockerfile, rồi gốc repo) mà mọi nguồn đều tồn tại tính từ đó.
   - Không ứng viên nào khớp, hoặc không đọc được → giữ mặc định hiện tại và đánh dấu `VERIFY`, không lỗi.
   - Logic kiểm "nguồn `COPY` tồn tại" viết thành hàm dùng lại được, vì S4-10 dùng nó cho `qc-agent validate`.
3. **Test** (`tests/test_scaffold_scan.py`, thêm cây mẫu trong `tmp_path`):
   - chỉ có `apps/api-server/Dockerfile` với `COPY . .` → chọn đúng, context `apps/api-server`;
   - `apps/api-server/Dockerfile` với `COPY apps/api-server/pyproject.toml ...` → context `.` (kiểu VAHAN);
   - nhiều ứng viên sâu 2 cấp → chọn một, có `VERIFY`;
   - `web/Dockerfile` cạnh `apps/api-server/Dockerfile` → hành vi theo luật đã duyệt ở mục 1.
4. **Tài liệu**: sửa dòng `--sut-dockerfile` trong `docs/user-guide-sprint-1.md` mục 3 (bảng tham số và đoạn "Chỉ thêm các tham số tuỳ chọn") cho khớp luật mới. Ví dụ cụ thể của một repo thật (`apps/api-server`) sẽ được thay bằng ví dụ trung tính ở S4-10; ở đây chỉ sửa phần mô tả luật tìm.

## Test bắt buộc

- `pytest tests/test_scaffold_scan.py tests/test_scaffold_init.py -q` xanh.
- Bằng chứng khi đóng (plan): `qc-agent init --dry-run` trên cây monorepo mẫu tự chọn đúng Dockerfile và context; dán output vào báo cáo.

## Ngoài phạm vi

Kiểm `COPY` trong `qc-agent validate` và ba lỗi cấu hình khác (S4-10). Không đụng `core/` và contract.
