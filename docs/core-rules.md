`core/` chỉ nối plan → worker → verdict → report. Không LLM, không biết tên worker nào: orchestrator "ngu" là orchestrator đúng.
Từ v2, `core/` còn đọc `selection.json` và `floor_workers` từ policy (dữ liệu, không hardcode tên worker), và **không import** `qc_agent.llm` / `qc_agent.groundtruth` / `qc_agent.selector` ở top-level (xem mục "Cấm"). Kiến trúc v2 và nhãn trạng thái từng thành phần: [architecture.md](architecture.md).

## Chạy

Chạy từ thư mục gốc repo (adapter được spawn bằng `python -m`, đường dẫn trong plan là tương đối).

```powershell
pip install uv; uv sync                                                    # 1. cài phụ thuộc: .venv + qc-agent (editable) từ uv.lock
python -m uvicorn --app-dir tests/fixtures/sut/noteboard toyapp.app:app --host 127.0.0.1 --port 8000   # 2. bật toy app (chỉ khi plan trỏ vào nó)
$env:QC_WORKERS_PATH = "workers;tests/fixtures/workers"                    # 3. để demo.yaml thấy worker giả (Linux/macOS dùng dấu ":")
qc-agent --plan tests/fixtures/plans/demo.yaml                             # 4. chạy gate, in report, ghi runs\r-NNNN\
```

Ground-Truth (S1, lệnh dùng import lười, không nằm trong gate): `qc-agent gt generate --prd FILE --sut-root DIR [--openapi FILE|URL] [--egress-dir DIR] [--summary-json FILE]` sinh catalog + `tests_gt/` + suite `gt-functional` từ PRD (cần `ANTHROPIC_API_KEY`, hoặc `GEMINI_API_KEY` khi `QC_GT_MODEL=gemini-*`); `gt regen` (PRD đổi) merge theo `tc_id` và giữ nguyên TC `approved`/`rejected`/`origin: qa`; `gt validate` là cổng HITL: exit **1** còn TC `draft`, drift của `tests_gt/`, `rejected` thiếu lý do, trùng `tc_id`, module-map chưa duyệt; exit **3** file không đọc được/sai schema. `qc-agent validate` chạy cùng bộ kiểm khi repo có `.qc-agent/ground-truth/` và đòi CODEOWNERS có quy tắc `/.qc-agent/`. Workflow, khoá QA và checklist: [groundtruth.md](groundtruth.md).

Exit code **hiện tại**: `PASS` 0 · `YELLOW` = `--yellow-exit` (mặc định 0) · `FAIL` 1 · lỗi plan/hệ thống **3**.
**Từ S3** đổi thành: `BLOCKED` 1 · `PASSED_WITH_WARNINGS` = `--warn-exit` (mặc định 0; `--yellow-exit` còn làm alias deprecated) · `PASSED` 0 · lỗi plan/hệ thống 3.
`demo.yaml` dùng worker giả nên bước 2 chỉ cần khi plan trỏ vào toy app. Cờ khác: `--only t-a,t-b`, `--runs-dir`, `--rerender RUN_DIR`.

## Thêm worker

Đúng 5 bước, mỗi bước là **thêm file**:

1. `workers/<tên>.yaml` (manifest, `adapter: "qc_agent/adapters/<tên>_adapter.py"`): copy `workers/_template.yaml` (bản mẫu đã đóng băng, không sửa). Capability mới thì thêm một dòng vào `schemas/capabilities.json`.
2. `schemas/<cap>.inputs.json`: schema của `inputs`. Tuỳ chọn ở PoC (D-08).
3. `src/qc_agent/adapters/<tên>_adapter.py`: kế thừa `Adapter` (`src/qc_agent/adapters/_base.py`), khai `NAME` + `ADAPTER_VERSION`, **chỉ override `build_cmd` và `parse_output`**.
4. `src/qc_agent/oracle/<kind>.py` nếu `oracle.kind` là mới: dùng `@register("<kind>")`, không phải sửa `oracle/__init__.py`.
5. Thêm task vào `tests/fixtures/plans/<plan>.yaml`. Nhớ quote khoá `"on":` trong `retry`, vì YAML đọc `on` trần thành `True`.

**Tuyệt đối không sửa `core/`.** Nếu bạn thấy phải sửa, thiết kế đang sai: dừng lại và hỏi cả nhóm.

## Ai đỡ được gì

Nếu A (chủ `core/`) kẹt, hai người còn lại nhận việc theo bảng này:

| Người nhận | File | Test từng file |
|---|---|---|
| B | `core/runner.py` | `pytest tests\test_runner.py -q` |
| B | `core/cli.py` + `orchestrator.py` | `pytest tests\test_cli.py -q` |
| C | `core/plan.py` | `pytest tests\test_plan.py -q` |
| C | `core/schema.py` | `pytest tests\test_schema.py -q` |

Toàn bộ: `pytest -q`. Cổng kiểm tay (cần `QC_WORKERS_PATH` như mục "Chạy"): `qc-agent --plan tests/fixtures/plans/demo.yaml` phải exit 0, `tests/fixtures/plans/demo_fail.yaml` phải exit 1; và `python tools/freeze_contract.py --check` phải exit 0.

## Quy ước đặt tên (Phase 1)

| Thứ | Quy ước | Ví dụ |
|---|---|---|
| Metric worker đếm | `<worker>.<mức>` và `<worker>.total`, mức ∈ `critical/high/medium/low` | `semgrep.high`, `trivy.critical` |
| Check của worker luồng | `snake_case` theo bước nghiệp vụ | `filter_applied`, `report_downloaded` |
| Tên suite | một từ, trùng tên file `.qc-agent/suites/<tên>.yaml` và tên trong policy | `sast`, `secrets`, `deps`, `integration` |
| `task_id` | Làn A (Security) dùng `t-010…t-019`, Làn B (Integration) dùng `t-020…t-029`, Ground-Truth dùng `t-030…t-039`; không bao giờ trùng | `t-010` |
| Nhánh | `feat/<worker>-<việc>`, một PR một việc, ≤ ~400 dòng | `feat/worker-semgrep` |

Khâu Security (Làn A) — tên đã chốt, suite và test dựa vào chúng:

| Suite (`task_id`) | Worker · capability | Metric adapter phát ra (luôn đủ key, kể cả khi bằng 0) |
|---|---|---|
| `sast` (`t-010`) | `semgrep` · `code.sast` | `semgrep.critical/high/medium/low/total`, `semgrep.files_scanned` |
| `secrets` (`t-011`) | `gitleaks` · `code.secret` | `gitleaks.count` (secret không có "mức") |
| `deps` (`t-012`) | `trivy` · `deps.vuln` | `trivy.critical/high/medium/low/unknown/total`, `trivy.targets`, `trivy.db_age_days` |

Khâu Ground-Truth (S1) — `gt-functional` (`t-030`): worker `pytest` · `api.functional`, metric luôn đủ 5 khoá `pytest.tests/passed/failures/errors/skipped` (`skipped` gồm cả xfail).
Suite chặn `pytest.failures == 0`, `pytest.errors == 0` và `pytest.tests >= 1` (để "gate rỗng" là `fail`, không phải xanh). Finding: `detected_by: "pytest:<tc_id>"`
(quy ước `<tool>:<rule_id>`, `severity_hint: medium`). Thư mục test phải có `pytest.ini` riêng (`groundtruth/render.py` sinh), nếu không worker trả `error`: chặn cấu hình/`conftest.py` của repo SUT lọc bớt test làm gate xanh giả. Exit code của pytest: 0/1 → parse JUnit; 5 → metric 0; 2/3/4 và mọi mã khác → `error`.

- Contract 2.0.0 dùng `severity_hint` ∈ low/medium/critical. Metric gốc của công cụ vẫn có `high` (`semgrep.high`, `trivy.high`) để giữ ý nghĩa của số đếm. Adapter đặt `detected_by: "<tool>:<rule_id>"` để policy có thể chọn luật cụ thể.
- Adapter **đếm**, ngưỡng nằm trong file suite (oracle `threshold`); adapter không có nhánh nào phán pass/fail. Finding có `location` tuỳ chọn gồm `path`, `line`, `end_line`; title có thể giữ `file:dòng` để người đọc nhận ra vị trí.

## Cấm

- **Cấm gate PR chạm trang B thật**: Integration Tier 2 bắt buộc dùng HAR với `update:false`, `notFound:'abort'`; host ngoài allowlist phải bị chặn và làm check đỏ. Tier 3 chạm B thật chỉ chạy manual, một luồng, không retry và không gating.
- **Cấm gọi LLM trong `core/`**: verdict phải tái lập được. LLM chỉ được xuất hiện ở đúng ba chỗ:
  - **worker**: chỉ cho finding không chặn gate (tối đa Low);
  - **`groundtruth/`** (S1): sản phẩm là file, đi qua PR và QA duyệt;
  - **`selector/`** (S2): sản phẩm duy nhất là `selection.json`; `core/` luôn gộp floor lần hai.
- **Cấm import `qc_agent.llm`, `qc_agent.groundtruth`, `qc_agent.selector` ở top-level trong `core/`**: lệnh CLI mới (`gt`, `select`, …) trong `core/cli.py` dùng **import lười**, đúng cách `init`/`validate`/`doctor` đang làm, để chạy `--trigger manual` không kéo LLM vào tiến trình. `llm/` (S1-01) và `groundtruth/` (S1-02) đã có, `tests/test_llm_client.py` và `tests/test_gt_prd.py` kiểm import của `core.cli`/`core.engine` không kéo chúng vào; `selector/` (S2) chưa tồn tại, luật áp dụng từ khi nó được thêm.
- **Cấm `if worker == ...` trong `core/`**: mọi khác biệt giữa worker phải nằm ở manifest, adapter hoặc oracle. Floor và allowlist là dữ liệu của policy (`floor_workers`), không phải tên hardcode.
- **Cấm tin dữ liệu từ PR, PRD, diff hay SUT**: coi là không tin cậy. Đặt trong vùng phân cách khi đưa vào prompt; ép output LLM vào schema và enum allowlist; làm sạch trước khi đưa vào Markdown; không đưa vào lệnh shell (truyền argv, không `shell=True`); không nhúng vào code Python được sinh ra. Log không bao giờ chứa nội dung PRD, diff, prompt, response hay rationale do LLM viết, API key hoặc token.
- **Cấm gọi ra ngoài mà chưa ghi egress**: mọi lời gọi LLM (kể cả `count_tokens`) và Jira phải gọi `core/egress.record(...)` **trước khi gửi**; quyết định `deny` thì không được có request nào.
- **Cấm để LLM, chi phí hay Jira làm đổi verdict**: LLM lỗi → FULL SET, không đỏ; Jira/GitHub lỗi → chỉ ghi cảnh báo, verdict giữ nguyên.
- **Cấm thêm trường riêng của một worker vào schema**: một schema chung là thứ cho phép thêm worker mà không sửa `core/`.
- **Cấm retry khi `fail`**: chỉ `error` (hạ tầng) được retry đúng 1 lần; retry `fail` che flakiness và làm gate xanh giả.
- **Cấm sửa file contract ngoài quy trình SemVer** (`schemas/task_spec.json`, `schemas/result.json`, `workers/_template.yaml`): `python tools/freeze_contract.py --check` phải exit 0. Thêm field an toàn (v1.x): bump minor, CI `contract-check` tự phân loại, cần 1 approval. Sửa/xoá/thu hẹp (v2.0): bump major, cần 3 approval trong đó có Lead/Core, rồi mới `freeze_contract.py --write --version X.Y.Z`. Danh sách người duyệt: `.github/contract-reviewers.yaml`. Lần nâng MAJOR đã lên kế hoạch là **S3.1** (contract 2.0.0).
