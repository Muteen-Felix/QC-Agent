# S4-09 · Dịch vụ DB phụ tuỳ chọn cho SUT cần database (plan S4.9, nợ từ onboarding VAHAN)

- Branch: `feat/s4-09-sut-db-service`
- Tiền điều kiện: Sprint 3 đã nghiệm thu. **Làm trước S4-01**: bước này thêm input và sửa bước "Start SUT" của `qc-gate.reusable.yml`, S4-01 rà workflow bản cuối sau đó.
- Mức rủi ro: **L2** (đổi kiến trúc workflow, đụng cách cấp bí mật). Trình kế hoạch và chờ duyệt trước khi code.

## Quyết định đã chốt (không hỏi lại)

Plan nêu ba phương án: (a) chỉ tài liệu + cảnh báo, (b) input dịch vụ phụ cho workflow, (c) SUT có chế độ test không DB. **Felix chọn (b) làm hướng mặc định ngày 2026-10-06**, khác đề xuất (a)-trước trong bản PLAN đầu (xem `docs/prompts/README.md`, mục "Hiệu chỉnh so với plan"). Phần tài liệu và cảnh báo của (a) vẫn làm, như một phần của (b).

Hai ràng buộc đi kèm:
- **DB là tuỳ chọn.** SUT không cần DB giữ nguyên đường chạy hiện tại; không cấu hình DB và không đặt secret SUT thì workflow chạy y như trước (lệnh `docker run` của SUT không đổi).
- **Không mặc định PostgreSQL cho mọi SUT.** Loại DB và cấu hình là của từng SUT, chọn khi triển khai.

Nếu thiếu dữ kiện cụ thể (loại DB của SUT pilot, tên secret, SUT có tự chạy migration không) thì chỉ hỏi đúng phần đó.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.9 (toàn văn, gồm ví dụ VAHAN)
- `.github/workflows/qc-gate.reusable.yml`: inputs (`sut_env` "KHÔNG đặt bí mật ở đây", `sut_base_url`), khối `secrets:`, bước "Start SUT" (`docker network create qc-net`, `wait_ready`, health 60 × 2 s = 120 s), bước "Clean up SUT"
- `src/qc_agent/scaffold/{tmpl/qc.yml.tmpl (secrets: inherit), templates.py, init.py, validate.py}`, `tests/test_workflow_static.py`, `tests/test_scaffold_{init,templates,validate}.py`
- `tools/run_reusable_locally.py`, `tests/test_reusable_workflow.py`
- `docs/usage-ci.md` (mục `sut_env`), `docs/onboarding.md`, `docs/user-guide-sprint-1.md`

## Sự thật cần biết (kiểm 2026-10-06)

- **VAHAN**: API cần `VAHAN_UI_AUTH_TOKEN_SECRET` dài ≥ 32 ký tự và một PostgreSQL kết nối được (`app/main.py`, hàm `lifespan`), nên không qua được health check 120 s khi chỉ có một container.
- **Secret của reusable workflow phải khai báo theo tên** trong `on.workflow_call.secrets`; không truyền được tập secret tuỳ ý. Cần chốt một cách gom (vd một secret nhiều dòng `KEY=VALUE`, như `sut_env` nhưng là secret).
- **Image Postgres trong repo chưa ghim digest** (`docker-compose.yml` và job `ci.yml` dùng `postgres:17`). Không chép mẫu đó: image DB của gate phải ghim `@sha256:` như mọi image khác.
- `sut_base_url` đã có (bỏ qua build/chạy SUT) nhưng docs chưa nhắc.

## Việc cần làm

1. **Kế hoạch (≤ 15 dòng, chờ duyệt)**: tên input, tên secret, thứ tự khởi động, cách chờ DB sẵn sàng, cách kiểm. Nêu rõ cách nhận thông tin kết nối không lộ ra log.
2. **Input mới của `qc-gate.reusable.yml`** (gợi ý tên, chốt ở kế hoạch):
   - `sut_db_image`: image DB **bắt buộc ghim digest** khi được đặt (trừ `allow_unpinned_image`, giống image qc-agent); rỗng = không có DB.
   - `sut_db_env`: `KEY=VALUE` mỗi dòng cho container DB, **không bí mật** (vd `POSTGRES_DB=app`).
   - `sut_db_ready_cmd` hoặc cổng TCP để chờ sẵn sàng; thời gian chờ có trần.
   - Secret mới (vd `SUT_SECRET_ENV`, nhiều dòng `KEY=VALUE`) cho cả SUT lẫn DB: mật khẩu DB, `DATABASE_URL`, `VAHAN_UI_AUTH_TOKEN_SECRET`…
3. **Bước "Start SUT"**
   - Có DB: chạy container `db` trong `qc-net` (tên mạng cố định để SUT gọi `db:<port>`), chờ sẵn sàng, **rồi mới** chạy SUT. DB không sẵn sàng → in `docker logs db | tail` (không in env) và `::error::`.
   - Bí mật: ghi vào file env trong `$RUNNER_TEMP` với quyền 600, truyền bằng `--env-file`, xoá khi cleanup. Gọi `::add-mask::` cho từng giá trị trước mọi lệnh có thể in ra. **Không** dùng `-e KEY=VALUE` trên dòng lệnh, không `echo` giá trị, không `set -x`.
   - Không có DB: không chạy container `db`; secret SUT (nếu có) vẫn truyền bằng `--env-file`; không có cả hai thì giữ nguyên lệnh hiện tại. Có `sut_base_url` → bỏ qua cả DB lẫn SUT.
   - "Clean up SUT" xoá thêm container `db` và file env.
   - SUT có cần chạy migration không là việc của image SUT; ghi rõ trong docs (workflow không chạy lệnh nào trong SUT ngoài `docker run`).
4. **Scaffold**
   - `qc.yml.tmpl`: thêm các input DB dưới dạng comment mẫu (không bật mặc định); `secrets: inherit` giữ nguyên.
   - `init`/`validate`: khi mã SUT tham chiếu `DATABASE_URL` (hoặc biến DB phổ biến) mà caller không khai DB và không có `sut_base_url` → **WARN** kèm hướng dẫn. Không ERROR: SUT có thể có chế độ không DB.
5. **Tài liệu**: `docs/usage-ci.md` (input DB, secret, `sut_base_url`, điều kiện "SUT khởi động được chỉ bằng `sut_env`" khi không dùng DB), `docs/onboarding.md` (SUT cần DB), `CLAUDE.md` chỉ nếu thêm lệnh/env cấp repo.

## Test bắt buộc

- `tests/test_workflow_static.py`
  - Input DB tồn tại; image DB phải ghim digest (giống kiểm tra image qc-agent).
  - Không có `${{ secrets.* }}` hay input trong `run:`; secret mới khai trong `workflow_call.secrets`.
  - Bước "Start SUT" không có `set -x` và không truyền secret bằng `-e KEY=VALUE`.
- Harness (cần Docker; không có thì ghi rõ là skip):
  - **SUT không cần DB** (noteboard): kết quả giống hệt trước (test hiện có của `test_reusable_workflow.py` xanh).
  - **SUT cần DB**: một SUT mẫu nhỏ trong `tests/fixtures/` (health trả 200 chỉ khi kết nối được DB, đọc `DATABASE_URL` từ env) chạy xanh khi có input DB và secret; không có DB thì job đỏ ở bước "Start SUT" với thông báo rõ.
  - Chuỗi đánh dấu đặt trong secret không xuất hiện trong log harness.
- `tests/test_scaffold_{init,validate,templates}.py`: cảnh báo `DATABASE_URL`; template có comment mẫu.

## Ngoài phạm vi

Thứ tự step bản cuối và đổi tên caller `qc-gate.yml` (S4-01). Không thêm dependency Python. Không chạy trên GitHub thật (S4-06). Không sửa `docs/implementation-plan.md`.
