# S3-07 · Nối dây CI + cấu hình + web + E2E harness cho Gatekeeper (phần "File sửa" còn lại của S3)

- Branch: `feat/s3-07-gatekeeper-wiring`
- Tiền điều kiện: S3-04, S3-05 và S3-06 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan: mục "Sửa Tích hợp" và "Config & CI" của Sprint 3, DoD Sprint 3 (toàn bộ)
- `src/qc_agent/integrations/ci.py: report_run`, `.github/workflows/qc-gate.reusable.yml` (thứ tự step, cách truyền secret bằng `-e NAME`)
- `configs/projects/{_default.yaml, noteboard.yaml}`, `web/app.js`
- `tests/fakes.py` (`FakeGitHub`, `FakeJira`, `FakeAnthropic`), `tools/run_reusable_locally.py`, `tests/test_reusable_workflow.py`

## Mục tiêu

Chuỗi sau PR chạy trọn: `gate → PR review → báo cáo (Check Run, comment, lịch sử, webhook) → Jira → enforce`.
- Mỗi bước tích hợp là **fail-open**, và chỉ exit code của gate quyết định xanh/đỏ.
- Chứng minh DoD Sprint 3 bằng E2E với fake GitHub và fake Jira.

## Quyết định cần nêu trong báo cáo

Plan viết "`ci.py` gọi pr_review + Jira" **và** "bước 'PR review' thay 'Security review'". Chọn **một** trong hai và giải thích. Đề xuất: mỗi việc là một step riêng trong workflow, mỗi step có `continue-on-error` để cô lập lỗi; còn `ci.py` chỉ giữ Check Run, comment, lịch sử và webhook. Cách này cũng khớp thứ tự `review → jira` của S4.1.

## Việc cần làm

1. **Workflow `qc-gate.reusable.yml`**
   - Thêm secret tuỳ chọn `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. Truyền vào container bằng `-e NAME`, không đặt trên dòng lệnh.
   - Thêm step **"Jira (Low)"**: `if: always() && github.event_name == 'pull_request'`, `continue-on-error: true`, gọi `python -m qc_agent.integrations.jira --run-dir "$RUN" --project "$PROJECT"`.
   - Thứ tự: gate → PR review → Report → Jira → upload → cleanup → enforce.
   - Step enforce giữ nguyên: chỉ `exit_code == 0` mới xanh. `PASSED_WITH_WARNINGS` là 0 khi `--warn-exit 0`.
2. **`integrations/ci.py`**: bỏ phần ingest nợ nếu còn. Kết quả JSON thêm `severity_counts` và thông tin lấy từ report.
3. **Cấu hình**
   - `_default.yaml`: thêm khối `severity` (`block_on` + `default_severity`, **không** thêm override). Giữ comment cảnh báo ảnh hưởng mọi team.
   - `noteboard.yaml`: thêm `jira: {project_key: QCSB, issue_type: Task, user_map: {}}` làm ví dụ, và comment rằng key thật được điền lúc chạy S4-06.
   - `tests/test_shipped_projects.py` xanh.
4. **`web/`**: nhãn và màu cho `BLOCKED` / `PASSED_WITH_WARNINGS` / `PASSED`, và vẫn hiện đúng giá trị cũ `PASS/YELLOW/FAIL` của lịch sử. `tests/test_web.py` xanh (cần Postgres và Chromium; không có thì ghi là đã skip).
5. **E2E harness**: `tests/test_gatekeeper_e2e.py`, dùng `FakeGitHub` và `FakeJira`, **không cần DB**.
   - Có thể chạy các step của workflow qua `tools/run_reusable_locally.py`, hoặc gọi trực tiếp CLI và các module tích hợp theo đúng thứ tự workflow. Chọn cách nhanh và ổn định hơn, và ghi lý do.
   - Kịch bản đúng theo DoD:

     | Kịch bản | Kỳ vọng |
     |---|---|
     | `QC_BUGS=1` (toyapp thật, hoặc fixture result 5xx) | Critical → `BLOCKED`, exit 1, Check `failure`, có inline comment |
     | Fixture semgrep `WARNING` | Medium → `BLOCKED` |
     | Fixture chỉ có Low | exit 0, Check `success` với tiêu đề "✅ PASS · N cảnh báo Low", inline đúng dòng, **đúng 1** ticket gán cho tác giả PR |
     | Chạy lại kịch bản Low | 0 comment mới, 0 ticket mới |
     | `FakeJira` trả 401, rồi 503 | verdict và exit code không đổi, summary có cảnh báo |

   - Không đặt `QC_DATABASE_URL` trong toàn bộ file test.
6. **Tài liệu**: `docs/usage-ci.md` (secret Jira, chuỗi step, cách đọc Check Run mới), `docs/onboarding.md` (cấu hình `jira` và `user_map`).

## Nghiệm thu

Mọi dòng DoD Sprint 3 có test tương ứng xanh. Riêng `CONTRACT.lock = 2.0.0` đã xong ở S3-01. Phiên `dod-verify` sẽ tổng hợp.

## Ngoài phạm vi

Jira thật và repo thật (S4-06), cache và chi phí token (S4).
