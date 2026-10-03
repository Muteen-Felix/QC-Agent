# QC-Agent

Quality gate cho Pull Request, kèm bộ sinh test case từ PRD. Dùng chung cho nhiều repo sản phẩm (SUT).

> **LLM được sinh ứng viên và chọn phạm vi ngoài floor. LLM không được phán quyết.**
> Mọi thứ chặn merge đều tất định và đã qua người duyệt một lần.

## Hệ thống làm gì

Hai vòng lồng nhau, chạy ở hai nhịp khác nhau, nối với nhau chỉ qua **một thư mục file đã có người duyệt** (`.qc-agent/**` trong repo SUT):

```
 VÒNG NGOÀI (theo PRD, có LLM)          VÒNG TRONG (mỗi PR, verdict tất định)
 PRD ─► Ground-Truth Engine             PR ─► Chọn phạm vi ─► Gate ─► Phản hồi
        LLM sinh test case (JSON)             prune · path rules      chạy worker      inline review
        render tất định ─► PR cho QA          Diff Agent · floor      chấm tất định    Jira (Low)
        QA: draft ─► approved                 ⇒ selection.json        BLOCKED / PASSED
                 │                                   ▲
                 └──── .qc-agent/** (đã duyệt) ──────┘
```

| Thành phần | Việc | LLM |
|---|---|---|
| Ground-Truth (`groundtruth/`) | PRD → test case → PR để QA duyệt | Có, chỉ sinh dữ liệu JSON |
| Selector (`selector/`) | Chọn worker cần chạy cho PR này, luôn kèm floor (secrets + SAST) | Có, bị bao vây; lỗi ⇒ chạy toàn bộ |
| Gate (`core/`) | Chạy worker, chấm, ra verdict | **Không bao giờ** |

Verdict: `BLOCKED` (exit 1) khi có finding critical/medium hoặc task gate bị lỗi/bỏ qua · `PASSED_WITH_WARNINGS` khi chỉ còn Low (exit theo `--warn-exit`, mặc định 0) · `PASSED` (exit 0). Lỗi plan/hệ thống: exit 3.

Đọc đầy đủ ở [docs/architecture.md](docs/architecture.md).

## Worker

| Khâu | Worker | Công cụ |
|---|---|---|
| Functional | `schemathesis`, `pytest` (chỉ TC `approved`), `midscene`, `coverage-debt` | Schemathesis · pytest · Midscene |
| Performance | `k6` | k6 |
| Integration | `playwright` | Playwright (bản ghi HAR) |
| Security | `semgrep`, `gitleaks`, `trivy` | Semgrep · gitleaks · Trivy |
| AI app | `deepeval` | DeepEval |

Mỗi worker là `workers/<tên>.yaml` + `src/qc_agent/adapters/<tên>_adapter.py`, giao tiếp qua `schemas/task_spec.json` và `schemas/result.json`. Thêm worker chỉ là thêm file, không sửa `core/`. Chi tiết: [docs/worker.md](docs/worker.md), [docs/core-rules.md](docs/core-rules.md).

## Bắt đầu nhanh

Yêu cầu: Python 3.11, Node 22 (Midscene CLI và Playwright), [uv](https://docs.astral.sh/uv/). Docker chỉ cần cho Postgres và image.

```bash
pip install uv && uv sync        # .venv + qc-agent (editable) từ uv.lock
npm ci                           # Midscene CLI + Playwright
npx playwright install chromium  # trình duyệt cho test Playwright/web
cp .env.example .env             # điền khoá khi cần dùng LLM
```

Chạy gate demo (worker giả, không cần dịch vụ ngoài). Chạy từ thư mục gốc repo:

```bash
export QC_WORKERS_PATH="workers;tests/fixtures/workers"   # Linux/macOS dùng ":" thay cho ";"
qc-agent --plan tests/fixtures/plans/demo.yaml            # exit 0
qc-agent --plan tests/fixtures/plans/demo_fail.yaml       # exit 1
```

Chạy theo project với SUT mẫu `noteboard`:

```bash
python -m uvicorn --app-dir tests/fixtures/sut/noteboard toyapp.app:app --port 8000
APP_BASE_URL=http://127.0.0.1:8000 \
  qc-agent run --project noteboard --mode pr --sut-root tests/fixtures/sut/noteboard
```

## Lệnh chính

| Lệnh | Việc |
|---|---|
| `qc-agent run --project P --mode pr\|manual` | Chạy gate theo policy của project |
| `qc-agent select ... --out selection.json` | Chọn phạm vi cho PR (prune → path rules → Diff Agent → floor) |
| `qc-agent run --trigger pr --selection selection.json` | Chạy đúng phạm vi đã chọn, gộp lại floor |
| `qc-agent run --trigger manual --workers a,b` | Chạy đúng các worker chỉ định, không LLM, không floor |
| `qc-agent gt generate\|regen\|validate\|info` | Ground-Truth: sinh TC từ PRD, gộp khi PRD đổi, cổng duyệt của QA |
| `qc-agent gt import-xlsx\|export-xlsx` | Trao đổi test case với QA qua Excel |
| `qc-agent init` / `validate` | Onboard repo SUT / kiểm cấu hình offline |
| `qc-agent doctor` | Kiểm binary, env, version của mọi worker |
| `qc-agent user ...` / `token ...` | Quản trị tài khoản và token (cần Postgres) |

Dịch vụ (API + dashboard + executor) cần Postgres:

```bash
docker compose up -d postgres
export QC_DATABASE_URL=postgresql://qc:qc-dev-only@127.0.0.1:5433/qc_agent
python -m qc_agent.jobs.migrate upgrade
python -m qc_agent.jobs.executor &
QC_ALLOWED_EMAIL_DOMAINS=congty.com QC_COOKIE_SECURE=false \
  uvicorn qc_agent.api.app:create_app --factory --port 8080
```

PR và chạy tay qua CLI **không** phụ thuộc Postgres; chỉ dịch vụ mới cần.

## Kiểm thử

```bash
pytest -q                          # toàn bộ (rất lâu, xem lưu ý)
pytest tests/test_runner.py -q     # một file
```

- Test cần DB (`test_jobs_db`, `test_executor`, `test_api`, `test_web`) bị skip nếu thiếu `QC_TEST_DATABASE_URL`, ví dụ `postgresql://qc:qc-dev-only@127.0.0.1:5433/postgres`.
- Test không bao giờ gọi API LLM thật; LLM được giả bằng `httpx.MockTransport`.
- Cổng hợp lệ trước khi merge: `qc-agent --plan tests/fixtures/plans/demo.yaml` thoát 0, `demo_fail.yaml` thoát 1, `python tools/freeze_contract.py --check` thoát 0.

## Cấu trúc thư mục

```
src/qc_agent/
  core/          gate tất định: plan → worker → verdict → report (không LLM)
  selector/      chọn phạm vi PR (Diff Agent), sinh selection.json
  groundtruth/   PRD → test case → render, bộ chấm coverage, Excel
  llm/           client Claude/Gemini qua httpx, vòng lặp agent
  adapters/      adapter của từng worker       oracle/   luật chấm pass/fail
  scaffold/      qc-agent init / validate      integrations/  GitHub, Jira, webhook
  api/ jobs/ auth/   dịch vụ (FastAPI, executor, Postgres)
workers/         manifest từng worker          schemas/  contract (đóng băng, SemVer)
configs/projects/  policy theo project         rules/semgrep/  luật SAST
web/             dashboard (HTML/JS/CSS thuần) tools/    đo đạc, kiểm contract, tiện ích
tests/           test + fixture (SUT mẫu noteboard)       docs/   tài liệu
.github/workflows/  CI, gate và Ground-Truth tái sử dụng, kiểm contract
```

## Tài liệu

| Đọc | Khi nào |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Hiểu hệ thống; tài liệu chuẩn |
| [docs/adr/](docs/adr/README.md) | Vì sao thiết kế như vậy |
| [docs/groundtruth.md](docs/groundtruth.md) | Vận hành vòng ngoài: PRD → test case → QA duyệt |
| [docs/usage-ci.md](docs/usage-ci.md) | Gắn gate vào repo SUT |
| [docs/onboarding.md](docs/onboarding.md) | Onboard repo mới bằng một lệnh |
| [docs/worker.md](docs/worker.md) | Từng worker, giới hạn công cụ |
| [docs/core-rules.md](docs/core-rules.md) | Luật code của `core/`, cách thêm worker |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Code đã tới đâu, DoD từng sprint, việc còn lại |
| [docs/requirement-spec.md](docs/requirement-spec.md) | SRS |

## Trạng thái

Gate, selector, Ground-Truth, verdict mới, PR review và Jira đã có code và test. Phần dùng LLM **chưa được đo bằng model thật** và **chưa có PR thật trên repo SUT thật**. Bảng trạng thái từng thành phần ở [docs/implementation-plan.md](docs/implementation-plan.md).
