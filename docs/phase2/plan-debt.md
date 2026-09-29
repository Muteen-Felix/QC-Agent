> **Superseded** bởi [`docs/implementation-plan.md`](../implementation-plan.md) — S3 gỡ sổ nợ khỏi luồng; bảng `test_debt` và migration `0005` giữ nguyên.
> `docs/architecture.md` (v2) không còn mô tả sổ nợ, nên các tham chiếu `architecture.md §1.4–§1.5, §5.4` bên dưới chỉ còn nghĩa lịch sử. Code của Phase 2 vẫn nằm trong repo cho tới S3.

# Phase 2 — Sổ nợ test + khâu dò nợ

Một người làm, 1 sprint (~8 ngày + 2 ngày đệm). Bối cảnh: [architecture.md §1.4–§1.5, §5.4](../architecture.md).
Đầu vào của Phase 3 (PRD → TC) và các phase sau: không có sổ nợ thì "80% tự động" chỉ là con số đoán.

## 0. Đối chiếu với code thật (bản đề xuất ban đầu lệch 4 chỗ)

| Đề xuất nói | Code thật | Hệ quả |
|---|---|---|
| "chỉ cần `github.py` đẩy neutral" | `conclusion_for` đã đổi YELLOW → `neutral` | Chỉ sửa title + comment |
| "`verdict.py` đã có YELLOW" | YELLOW chỉ ra khi task **gate** bị skipped; task discovery fail không bao giờ ra YELLOW | Phải sửa core theo policy (P2-1) |
| "git diff → bề mặt" | checkout của workflow dùng `fetch-depth` mặc định (1) | Sửa workflow: `fetch-depth: 2` (P2-7) |
| "một bảng cạnh `job_tasks`" | ingest bỏ `findings` và gán `lane=None` | Sửa ingest (P2-5) |

## 1. Luồng dữ liệu

```
 PR (merge commit, fetch-depth 2)
   │  QC_DIFF_BASE=HEAD^1  (chỉ trên pull_request)
   ▼
 qc-agent run --mode pr                          ← CÙNG lượt chạy với gate, KHÔNG nằm trong refine
   ├─ api-contract   lane=gate       ─┐
   └─ coverage-debt  lane=discovery  ─┤ worker tất định: diff → bề mặt → so với test → findings "debt:…"
                                      ▼
 verdict.py: suite trong advisory_yellow_suites fail ⇒ YELLOW (exit 0)
                                      ▼
 report.json ─┬─→ ci.py → Check Run ⚪ neutral + comment "N bề mặt mới chưa có test"
              └─→ ingest (CI) / executor (Mode 2) → repo.apply_debt() → bảng test_debt
                                                     diff-scan: chỉ MỞ nợ
                                                     full-scan (Mode 2): MỞ + ĐÓNG
```

## 2. Quyết định chốt (ngày 0)

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Key policy dùng chung `modes.<m>.advisory_yellow_suites: [coverage-debt]`; suite advisory nằm trong list này mà fail ⇒ YELLOW | Không `if worker ==` trong core. Không áp cho mọi suite discovery: canary `t-canary-01` luôn fail nên gate sẽ vàng vĩnh viễn |
| D2 | Nợ đi qua `findings[]`, `finding_id = "debt:<kind>:<surface>"`; metrics `debt.new`, `debt.full_scan` | Không đổi contract |
| D3 | Oracle `threshold` có sẵn: `debt.new == 0` | Không viết oracle mới |
| D4 | Diff-scan chỉ **mở** nợ; full-scan (không có `QC_DIFF_BASE`, tức Mode 2) mới **đóng** | Idempotent; nợ của PR bị bỏ ngang tự đóng `surface_gone` ở full-scan kế tiếp |
| D5 | v1 có 3 loại: `api_contract`, `api_endpoint`, `ui_route`; chỉ bề mặt **mới thêm** | Chỉ trả lời "có hay không" (§1.5). Bề mặt bị *sửa* để Later |
| D6 | `.qc-agent/coverage.yaml` trong repo SUT: `ignore: [{surface, reason}]`, `test_globs` | Giống `.gitleaksignore`: mọi dòng bỏ qua hiện trong diff PR |

Luật "đã có test chưa" (tất định):
- `api_contract`: có task `api.property` không loại path này (`exclude_path`) và path có trong `openapi.json` sống.
- `api_endpoint`: path template (`{x}` → `[^/]+`) khớp ≥ 1 file trong `test_globs` (mặc định `.qc-agent/**`, `tests/**`, `e2e/**`, `midscene/**`).
- `ui_route`: route path/tên component xuất hiện trong flow Midscene hoặc file e2e.

**Bảo thủ (§1.1):** diff lỗi (thiếu base, shallow) thì không đoán — trả `error`; vì ở lane discovery nên chỉ lên banner, không xanh giả/đỏ giả.

## 3. Các bước (mỗi bước một commit, xanh `pytest -q`)

### P2-0 · Spike + ADR (½ ngày)
Trên PR nháp noteboard: xác nhận `HEAD^1` là base với `fetch-depth: 2`; adapter thừa hưởng env `QC_DIFF_BASE`. Ghi D1–D6 (file này).

### P2-1 · Verdict ra YELLOW theo policy (1 ngày) · core
- `project.py`: thêm `advisory_yellow_suites` vào schema; phải là tập con của `advisory_suites`; `build_plan` trả `meta["yellow_task_ids"]`.
- `verdict.py`: `gate_verdict(..., yellow_on_fail=frozenset())` — task trong tập mà `status == "fail"` ⇒ `yellow`. Hàm vẫn thuần.
- `engine.py`: `judge()` nhận `yellow_task_ids`.
- DoD: `test_verdict.py` 4 ca (gate PASS + debt fail ⇒ YELLOW/0 · gate FAIL + debt ⇒ FAIL · debt `error` ⇒ PASS + banner · `ui-explore` fail ⇒ PASS); `test_project.py` ca key sai ⇒ PlanError.

### P2-2 · Bộ trích bề mặt, hàm thuần (2,5 ngày)
`src/qc_agent/adapters/coverage_debt_worker.py`: `changed_files` (`git diff --name-status -M` + `git show base:path`), `api_surfaces` (AST decorator + `APIRouter(prefix=)`), `ui_routes`, `new = head − base`, `covered(...)`; CLI xuất `debt.json`.
DoD: test dựng repo git tạm cho từng loại nợ + ignore + template param + rename.

### P2-3 · Worker hoàn chỉnh (1 ngày)
`workers/coverage-debt.yaml` (`lanes: [discovery]`, `binaries: [git]`, `data_egress: []`), `coverage_debt_adapter.py` (chỉ `build_cmd` + `parse_output`), capability `repo.coverage_debt` trong `schemas/capabilities.json`, suite mẫu (noteboard fixture + `scaffold/templates.py`). Suite advisory vắng mặt bị bỏ qua im lặng → `doctor`/`validate` phải cảnh báo.
DoD: `python tools/freeze_contract.py --check` exit 0; `qc-agent run --project noteboard --mode manual --suites coverage-debt` chạy full-scan.

### P2-4 · Migration `0005_test_debt` + repository (1 ngày)
`test_debt(id, project_id, kind, surface, opened_at, closed_at, closed_reason[covered|surface_gone|ignored], pr_url, opened_job_id, last_seen_job_id, last_seen_at)`; UNIQUE `(project_id, kind, surface) WHERE closed_at IS NULL`. `kind` không CHECK (Phase 3 dùng chung bảng). `repository.apply_debt(...)`: upsert; đóng chỉ khi `full_scan`.
DoD: apply 2 lần không trùng; diff-scan không đóng; full-scan đóng khoản vắng; upgrade/downgrade sạch.

### P2-5 · Nối ingest (CI) + executor (Mode 2) (1 ngày)
`ingest.py`: lấy lane thật, gọi `apply_debt` cho `debt:*`. `executor.py`: cùng hàm sau khi đọc `report.json`. Tuỳ chọn: `GET /api/v1/projects/{slug}/debt?open=true`.
DoD: ingest lại cùng `external_id` không nhân đôi nợ.

### P2-6 · GitHub title + comment (½ ngày)
`ci.py` title `PASS hồi quy · N bề mặt mới chưa có test`; `render_summary` thêm mục "Nợ test (không chặn)"; surface qua `clean_md`.
DoD: YELLOW có nợ ⇒ `neutral` + danh sách; `@user`/`<img>` được làm sạch.

### P2-7 · Workflow (½ ngày, làm sau cùng để rebase ít)
Checkout `fetch-depth: 2`; bước gate thêm `QC_DIFF_BASE` (chỉ khi pull_request) + `-e QC_DIFF_BASE`. **Không thêm bước mới** — dò nợ là một suite trong `Run qc-agent gate`, nên tách khỏi refine và không có `continue-on-error`.
DoD: test tĩnh assert `fetch-depth: 2`, `QC_DIFF_BASE` không ở bước refine; `tools/run_reusable_locally.py` qua.

### P2-8 · Bật và chạy thật (1 ngày)
Bật ở `noteboard.yaml`/`vahan-rpa.yaml` trước, **chưa** đụng `_default.yaml`. PR thật trên bản copy vahan-rpa: thêm endpoint + màn hình không test ⇒ check ⚪, comment liệt kê, `test_debt` có 2 dòng; Mode 2 sau khi có test ⇒ đóng. Cập nhật architecture.md §1.5/§5.4 → *Đã chạy*, usage-ci.md, onboarding.md.

## 4. Lịch

| Ngày | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9–10 |
|---|---|---|---|---|---|---|---|---|---|
| Việc | P2-0+1 | P2-2 | P2-2 | P2-2½+3 | P2-4 | P2-5 | P2-6+7 | P2-8 | đệm |

- Làm ngay được khi B chưa xong: P2-1 → P2-6 (không đụng file B sở hữu; image đã có `git`).
- Chờ B merge rồi rebase: P2-7 (workflow), P2-8 (`vahan-rpa.yaml`).
- Cắt được nếu trễ: `ui_route`, endpoint GET debt. Không cắt: P2-1, P2-4, P2-5.

## 5. Rủi ro / lưu ý
- Nợ chỉ đóng khi có full-scan: cần job định kỳ / chạy trên push vào main (sau P2-8), nếu không sổ chỉ tăng.
- Ingest lỗi ⇒ mất nợ của PR đó; gate không ảnh hưởng (§1.1), full-scan kế tiếp bắt lại.
- Heuristic sai hai phía: sót (chấp nhận), báo thừa (`coverage.yaml ignore` là lối thoát, hiện trong diff). Chỉ nâng lên chặn (§5.5 câu 4, chờ mentor) sau khi đo tỉ lệ nợ ảo.
- Nâng lên chặn không phải một dòng policy: phải chuyển suite sang `blocking_suites` **và** đổi `lane: gate` trong suite (`_check_lanes` ép khớp).
