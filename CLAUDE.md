# CLAUDE.md

Hướng dẫn cho Claude Code khi làm việc trong repo này. Con người đọc [README.md](README.md); kiến trúc chuẩn ở [docs/architecture.md](docs/architecture.md). File này chỉ giữ những gì agent cần để làm đúng và không phá luật.

## Quy ước

- **Tài liệu và comment viết bằng tiếng Việt**; khi sửa docs, giữ tiếng Việt. Tên biến, hàm, lệnh giữ tiếng Anh.
- **Cách giải thích:** hình dung → chạy thử/code → lý thuyết sâu. Ưu tiên thực hành, không giảng dài.
- Code mới phải giống code xung quanh: mật độ comment, cách đặt tên, idiom.
- Chạy mọi lệnh từ thư mục gốc repo: adapter được spawn bằng `python -m`, đường dẫn trong plan là tương đối.
- Không thêm tài liệu, phụ thuộc hay lệnh mô tả thứ chưa có trong code. Trạng thái thật của từng thành phần nằm ở [docs/implementation-plan.md](docs/implementation-plan.md).

## Lệnh

```bash
pip install uv && uv sync                     # .venv + qc-agent (editable) từ uv.lock
npm ci && npx playwright install chromium     # Midscene CLI + Playwright (test_web, test_playwright_guard_live)
pytest -q                                     # toàn bộ; pythonpath = . src
pytest tests/test_runner.py -q                # một file
pytest tests/test_engine.py::test_x           # một test
```

- **Full suite chạy rất lâu** (khoảng 50 phút, tuần tự). Dùng `pytest -n 4` cho tập con file; trên toàn bộ suite xdist báo `Different tests were collected between workers`, nên chạy toàn bộ phải tuần tự. Khi sửa nhỏ, chạy các file liên quan thay vì cả bộ.
- Test cần DB (`test_jobs_db`, `test_executor`, `test_api`, `test_web`) cần `QC_TEST_DATABASE_URL`; không có thì bị skip, nghĩa là **chưa được kiểm chứng**. `docker compose up -d postgres` rồi dùng `postgresql://qc:qc-dev-only@127.0.0.1:5433/postgres`.
- Cổng phải đạt trước khi coi là xong:
  - `qc-agent --plan tests/fixtures/plans/demo.yaml` thoát 0, `demo_fail.yaml` thoát 1 (cần `QC_WORKERS_PATH="workers;tests/fixtures/workers"`, Linux dùng `:`).
  - `python tools/freeze_contract.py --check` thoát 0.
- Gate theo project: `qc-agent run --project noteboard --mode pr --sut-root tests/fixtures/sut/noteboard` (cần `APP_BASE_URL`). SUT mẫu: `python -m uvicorn --app-dir tests/fixtures/sut/noteboard toyapp.app:app --port 8000`.
- Chọn phạm vi PR: `qc-agent select --project noteboard --mode pr --sut-root DIR --base SHA --head SHA --out selection.json`, rồi `qc-agent run ... --trigger pr --selection selection.json`. `--trigger manual --workers a,b` chạy đúng các worker đó (không LLM, không floor). Không có `--trigger` giữ hành vi cũ.
- Dịch vụ: `python -m qc_agent.jobs.migrate upgrade` → `python -m qc_agent.jobs.executor` và `uvicorn qc_agent.api.app:create_app --factory --port 8080` (cần `QC_DATABASE_URL`, `QC_ALLOWED_EMAIL_DOMAINS`; `QC_COOKIE_SECURE=false` khi chạy http). Tài khoản/token: `qc-agent user add|reset|deactivate|list`, `qc-agent token create --project X --name ci`.
- Ground-Truth: `qc-agent gt generate|regen|validate|info|import-xlsx|export-xlsx`. `gt validate` là cổng duyệt: exit 1 khi còn TC `draft`, `tests_gt/` bị drift hoặc module-map chưa duyệt; exit 3 khi file không đọc được/sai schema. Quy trình đầy đủ: [docs/groundtruth.md](docs/groundtruth.md), đo trên SUT thật: [docs/groundtruth-real-sut.md](docs/groundtruth-real-sut.md).
- Onboard repo SUT: [docs/onboarding.md](docs/onboarding.md); gate dùng lại trong CI: [docs/usage-ci.md](docs/usage-ci.md); thử workflow tại máy: `tools/run_reusable_locally.py`.
- Không có linter hay formatter được cấu hình. Exit code gate: `BLOCKED` 1 · `PASSED_WITH_WARNINGS` = `--warn-exit` (mặc định 0) · `PASSED` 0 · lỗi plan/hệ thống 3. `--yellow-exit` là alias cũ.

## Kiến trúc tóm tắt

Hai vòng lồng nhau, nối nhau qua thư mục file đã duyệt `.qc-agent/**` của repo SUT. Đọc [docs/architecture.md](docs/architecture.md) và [docs/core-rules.md](docs/core-rules.md) trước khi sửa `core/`.

- **Vòng trong (gate, tất định, chặn merge):** `core/engine.py` chạy plan → resolve → chọn worker theo capability → chạy → verdict → report. Policy = `configs/projects/_default.yaml` gộp sâu (list thì thay) với `<slug>.yaml`. Suite đã duyệt của SUT nằm ở `.qc-agent/suites/` trong repo SUT; worker chạy với cwd = gốc SUT.
- **Selector (`selector/`):** với `--trigger pr`, Diff Agent chỉ *chọn* worker trong allowlist của policy. Sản phẩm duy nhất là `selection.json` = floor (secrets + sast) ∪ path rules ∪ LLM. Diff chạm `full_set_paths` hoặc LLM lỗi ⇒ FULL SET. `core/` gộp lại floor lần hai nên sửa tay `selection.json` không bỏ được floor.
- **Vòng ngoài (`groundtruth/`):** PRD → LLM sinh test case dạng JSON (không sinh code) → render tất định → PR `qc-agent/gt/<prd-id>` → QA đổi `draft → approved`, thêm edge case (`origin: qa`). Gate chỉ chạy TC `approved`. `.qc-agent/**` được khoá bằng CODEOWNERS + branch protection. LLM không bao giờ tự đặt `tc_id/status/origin`; giá trị kỳ vọng lấy từ PRD/OpenAPI, không lấy từ code.
- **Worker:** `workers/<tên>.yaml` (manifest) + `src/qc_agent/adapters/<tên>_adapter.py` (kế thừa `Adapter` trong `_base.py`, chỉ override `build_cmd` và `parse_output`) + tuỳ chọn `src/qc_agent/oracle/<kind>.py` đăng ký bằng `@register`. Worker là tiến trình con, nói chuyện qua `schemas/task_spec.json` và `result.json`; capability liệt kê ở `schemas/capabilities.json`. **Thêm worker = thêm file, không sửa `core/`.**
- **Dịch vụ:** `api/` (FastAPI, `/api/v1`, cũng phục vụ SPA tĩnh `web/`), `jobs/` (SQLAlchemy + Alembic, `executor.py` chạy job bằng cách gọi CLI), `auth/` (admin mời user, argon2, khoá khi sai nhiều lần), `integrations/` (GitHub Check Runs/comment, webhook, Jira, ingest CI). `scaffold/` hiện thực `init` và `validate`. Luồng PR và manual không được phụ thuộc `QC_DATABASE_URL`.
- Một image Docker (`Dockerfile`) phục vụ CLI, API và executor. `src/qc_agent/scaffold/tmpl/Dockerfile.ui.tmpl` dành cho SUT là SPA.

## Luật cứng

- **Không LLM trong `core/`**, không `if worker == ...` trong `core/`, không import top-level `qc_agent.llm` / `groundtruth` / `selector` trong `core/` (lệnh CLI mới dùng import lười như `init`, `validate`, `doctor`). LLM chỉ được có mặt ở ba nơi: worker (finding không chặn), `groundtruth/` (sản phẩm là file qua PR), `selector/` (sản phẩm là `selection.json`). **LLM không bao giờ ra verdict.** Lý do: [docs/adr/](docs/adr/README.md).
- Không thêm trường riêng của worker vào schema dùng chung.
- Chỉ retry `error` (hạ tầng), một lần. Không bao giờ retry `fail`.
- **Không sửa file contract** (`schemas/task_spec.json`, `schemas/result.json`, `workers/_template.yaml`) ngoài quy trình SemVer, được canh bởi `schemas/CONTRACT.lock`, `tools/freeze_contract.py` và workflow `contract-check` (người duyệt: `.github/contract-reviewers.yaml`). Cũng không xoá `uv.lock` hay thư mục `.qc-agent/`.
- Severity v2: `severity_hint` ∈ `low`/`medium`/`critical` (contract 2.0.0). `BLOCKED` khi có critical/medium hoặc task gate bị error/skip; `PASSED_WITH_WARNINGS` khi chỉ còn Low. Dòng PASS/YELLOW/FAIL cũ trong DB vẫn hợp lệ.
- **Log** là JSON có cấu trúc trên stderr và không bao giờ chứa nội dung spec/inputs, PRD, diff, prompt, phản hồi LLM hay secret (`logging_setup.py`). Dữ liệu rời máy ghi vào `runs/<job>/egress.jsonl` theo khai báo `data_egress` của worker: **ghi egress trước mỗi lời gọi LLM/Jira** (`core/egress.record`); nếu bị `deny` thì không gửi gì.
- Dữ liệu PR, PRD, diff, SUT là **không tin cậy**: đặt trong vùng phân cách khi đưa vào prompt, ép output vào schema + enum allowlist, làm sạch trước khi vào Markdown, không đưa vào lệnh shell, không nhúng vào code sinh ra. Gate **không bao giờ đỏ vì LLM, chi phí hay Jira**: LLM lỗi ⇒ FULL SET; Jira/GitHub lỗi ⇒ chỉ cảnh báo.
- Trong plan YAML, đặt khoá `"on":` trong dấu nháy (`on` trần được YAML đọc thành `True`).
- Image nền và GitHub Actions được ghim theo digest/SHA; giữ nguyên việc ghim.

## Cấu hình và LLM

- Biến môi trường định nghĩa ở `src/qc_agent/settings.py` (`QC_RUNS_DIR`, `QC_WORKERS_PATH`, `QC_SCHEMAS_DIR`, ...); log đọc thêm `QC_LOG_FORMAT`/`QC_LOG_LEVEL` ở `logging_setup.py`. Mẫu ở `.env.example` chưa liệt kê hết, `settings.py` là nguồn đúng.
- Khoá LLM (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `*_BASE_URL`) đọc từ `os.environ` lúc gọi, không qua `Settings`. Provider chọn theo tiền tố model: `gemini-*` → Gemini, còn lại → Claude. Mặc định trong code: `QC_GT_MODEL=claude-sonnet-5`, `QC_SELECTOR_MODEL=claude-haiku-4-5-20251001`, agent GT `claude-sonnet-5-5`; `.env.example` đang đặt mẫu Gemini cho bản miễn phí. Claude không tự retry; Gemini có `QC_LLM_MAX_RETRIES`, `QC_LLM_MIN_INTERVAL_S`.
- **Test không bao giờ gọi API thật**: dùng `httpx.MockTransport` hoặc fake server qua `*_BASE_URL`. Đo bằng model thật tốn tiền, chỉ làm khi người dùng đồng ý (`tools/eval_groundtruth.py`, `tools/eval_gt_sut.py` với `--llm real --yes`).
- Agent GT (`gt generate|regen --agent`) gửi mã nguồn SUT ra ngoài máy (egress `source_code`) và **chưa từng chạy với API thật**.

## Thận trọng

- `python tools/protect_ground_truth.py` đổi branch protection trên GitHub thật (cần token admin): **không chạy trên repo thật khi chưa hỏi**.
- Hỏi trước khi làm việc khó hoàn tác hoặc hướng ra ngoài: push, mở PR, xoá nhánh, gọi LLM/Jira thật, đổi cài đặt GitHub.
