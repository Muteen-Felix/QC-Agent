> Orchestrator "ngu" là orchestrator đúng. Routing bằng LLM là thừa và có hại.

Đang refactor từ PoC sang `qc-agent` (xem `docs/README.md`). Bản PoC đầy đủ: tag `poc-final`.

- **Chạy orchestrator:** `python orchestrator.py --plan tests/fixtures/plans/demo.yaml` (exit 0), `demo_fail.yaml` (exit 1).
- **Toy app (SUT tham chiếu):** `python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000`.
- **Cài đặt:** `pip install uv && uv sync` (tạo `.venv`, cài editable `qc-agent` + phụ thuộc từ `uv.lock`).
- **Cấu hình:** `QC_RUNS_DIR`, `QC_WORKERS_PATH` (nhiều thư mục, ngăn cách `os.pathsep`), `QC_SCHEMAS_DIR`. Xem `src/qc_agent/settings.py`.
- **CLI:** `qc-agent --plan ...` (tương đương `python orchestrator.py`). Plan demo cần worker giả: `QC_WORKERS_PATH="workers;tests/fixtures/workers"` (Windows; dùng `:` trên Linux).
- **Chạy theo project (đa dự án):** `qc-agent run --project noteboard --mode pr --sut-root tests/fixtures/sut/noteboard` (cần `APP_BASE_URL`). Project + policy chặn/không chặn ở `configs/projects/<slug>.yaml` (tập trung); suite ở repo SUT tại `.qc-agent/suites/` (worker chạy với cwd = SUT root). Chỉ một số suite: `--suites api-contract`.
- **Job store + executor (service):** `docker compose up -d postgres`, `export QC_DATABASE_URL=postgresql://qc:qc-dev-only@127.0.0.1:5433/qc_agent`, `python -m qc_agent.jobs.migrate upgrade`, rồi `python -m qc_agent.jobs.executor` (nhận job, chạy bằng CLI trong tiến trình con; hỗ trợ huỷ, timeout, khoá môi trường, requeue). Test DB: `QC_TEST_DATABASE_URL=... pytest tests/test_jobs_db.py tests/test_executor.py`.
- **Tài khoản (chỉ admin mời):** đặt `QC_ALLOWED_EMAIL_DOMAINS=congty.com` (để trống = không ai được thêm), rồi `qc-agent user add ten@congty.com` (in mật khẩu tạm một lần), `user reset|deactivate|activate|list`; token CI theo project: `qc-agent token create --project noteboard --name ci`. Mọi user quyền như nhau; sai mật khẩu 5 lần thì khoá 15 phút.
- **API (lớp chính; web chỉ là client):** `uvicorn qc_agent.api.app:create_app --factory --port 8080` (cần `QC_DATABASE_URL`, `QC_ALLOWED_EMAIL_DOMAINS`; dev bằng http đặt `QC_COOKIE_SECURE=false`). Endpoint dưới `/api/v1`: `auth/*`, `projects`, `projects/{p}/suites`, `workers`, `projects/{p}/jobs` (tạo job thủ công), `jobs` (lịch sử), `jobs/{id}` (+`/cancel`, `/report.json|.md`, `/artifacts`, `/log`), `projects/{p}/runs` (CI đẩy kết quả bằng token). Project cần `sut_checkout` (đường dẫn checkout repo SUT trên máy chủ) và tuỳ chọn `environments` trong `configs/projects/<slug>.yaml`; `/healthz`, `/readyz`.
- **Test:** `pytest -q`
