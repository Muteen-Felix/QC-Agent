> Orchestrator "ngu" là orchestrator đúng. Routing bằng LLM là thừa và có hại.

Đang refactor từ PoC sang `qc-agent` (xem `docs/README.md`). Bản PoC đầy đủ: tag `poc-final`.

- **Chạy orchestrator:** `python orchestrator.py --plan tests/fixtures/plans/demo.yaml` (exit 0), `demo_fail.yaml` (exit 1).
- **Toy app (SUT tham chiếu):** `python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000`.
- **Cài đặt:** `pip install uv && uv sync` (tạo `.venv`, cài editable `qc-agent` + phụ thuộc từ `uv.lock`).
- **Cấu hình:** `QC_RUNS_DIR`, `QC_WORKERS_PATH` (nhiều thư mục, ngăn cách `os.pathsep`), `QC_SCHEMAS_DIR`. Xem `src/qc_agent/settings.py`.
- **CLI:** `qc-agent --plan ...` (tương đương `python orchestrator.py`). Plan demo cần worker giả: `QC_WORKERS_PATH="workers;tests/fixtures/workers"` (Windows; dùng `:` trên Linux).
- **Chạy theo project (đa dự án):** `qc-agent run --project noteboard --mode pr --sut-root tests/fixtures/sut/noteboard` (cần `APP_BASE_URL`). Project + policy chặn/không chặn ở `configs/projects/<slug>.yaml` (tập trung); suite ở repo SUT tại `.qc-agent/suites/` (worker chạy với cwd = SUT root). Chỉ một số suite: `--suites api-contract`.
- **Test:** `pytest -q`
