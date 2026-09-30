# S3-05 · Inline review cho mọi worker + Check Run theo verdict mới (plan S3.6, S3.7)

- Branch: `feat/s3-05-pr-review`
- Tiền điều kiện: S3-03 đã merge (`report.json.findings` đã chuẩn hoá). Chạy song song được với S3-04/06.

## Đọc trước

- `docs/prompts/_common.md`; plan S3.6, S3.7, DoD S3 (≥ 90% đúng dòng, 100% finding không có vị trí nằm trong thân review, chạy lại thì 0 comment mới)
- `src/qc_agent/integrations/security_review.py`: **mẫu cần tổng quát hoá**. Để ý `split_by_diff`, `already_posted` (chỉ tin review của Bot), `render_body`, `MARKER`, cách làm sạch qua `clean_md`, và nguyên tắc không bao giờ đưa đoạn mã hay secret vào review.
- `src/qc_agent/integrations/refine_review.py` (`pr_diff_lines`, `MAX_COMMENTS`, `MAX_PAGES`), `src/qc_agent/integrations/github.py` (`conclusion_for`, `check_title`, `render_summary`, `clean_md`)
- `.github/workflows/qc-gate.reusable.yml`, step "Security review" (vùng `qc-agent:region security-review`)
- `tests/test_security_review.py`, `tests/fakes.py: FakeGitHub`

## Mục tiêu

Mọi finding chuẩn hoá của mọi worker lên PR theo cùng một cách:
- nằm **trong diff** → comment inline đúng dòng;
- nằm ngoài diff hoặc không có vị trí → vào thân review.

Không đăng lặp. Check Run: `BLOCKED` = failure; `PASSED_WITH_WARNINGS` = success, với tiêu đề kiểu "✅ PASS · 3 cảnh báo Low".

## Việc cần làm

1. **`src/qc_agent/integrations/pr_review.py`**
   - Đầu vào là `report.json.findings`, **không** đọc lại evidence thô: sau S3-01, finding đã có `location`.
   - `split_by_diff(findings, diff_lines)`: inline khi `path` (bỏ tiền tố `./`) có `line` nằm trong tập dòng phía RIGHT của diff. Tối đa `MAX_COMMENTS`; phần vượt đưa vào thân.
   - **Thân inline**: `{icon} **{severity}** · {worker} · {rule_id}`, kèm tiêu đề đã `clean_md`, và gợi ý cách bỏ qua có lý do tuỳ theo công cụ (`# nosemgrep: <rule>`, `.gitleaksignore`, `.trivyignore`, …).
     - Bảng gợi ý này nằm ở `integrations/`, là lớp tích hợp nên được phép biết tên công cụ. **Không** đặt ở `core/`.
     - **Không bao giờ** chứa đoạn mã, `Secret` hay `Match`.
   - **Thân review**: đếm theo severity; ba nhóm Critical/Medium/Low; các finding ngoài diff (tối đa 30 dòng, phần còn lại chỉ ghi số lượng); ghi chú "review chỉ để đọc — chặn hay không do gate quyết".
   - **Chống đăng lặp**: marker `<!-- qc-agent:review sha256=<digest> -->`, với digest băm trên tập `(fingerprint, severity)` đã sắp xếp. `already_posted` chỉ tin review có `user.type == "Bot"`, giống bản cũ.
   - **Fail-open**: mọi lỗi GitHub (fork dùng token chỉ-đọc → 403) chỉ ghi vào JSON kết quả. CLI `python -m qc_agent.integrations.pr_review --run-dir …` **luôn exit 0**.
   - Log: `review.posted` / `review.skipped` gồm số comment, số finding ngoài diff, và lý do. Không có nội dung.
2. **Thay thế `security_review.py`**:
   - chuyển các case trong `tests/test_security_review.py` sang `tests/test_pr_review.py`;
   - xoá `security_review.py` và test cũ;
   - trong workflow, thay step "Security review" bằng step **"PR review"** gọi `qc_agent.integrations.pr_review`. Giữ `continue-on-error`, workspace chỉ-đọc, và điều kiện `if: always() && github.event_name == 'pull_request'`.
3. **`integrations/github.py`**
   - `conclusion_for(verdict, exit_code)`: `BLOCKED` → failure; `PASSED` và `PASSED_WITH_WARNINGS` → success; `exit_code` ngoài {0, 1} → failure (lỗi hệ thống không được xanh).
   - `check_title`: `❌ BLOCKED · 2 critical, 1 medium` / `✅ PASS · 3 cảnh báo Low` / `✅ PASS`.
   - `render_summary`: dòng đếm theo severity cộng top finding (đã làm sạch), thay cho mục "Nợ test" cũ.
   - `web/` (nếu có dùng chung nhãn) phải nhất quán với các nhãn trên.

## Test bắt buộc

- `tests/test_pr_review.py`, dùng `FakeGitHub`:
  - **≥ 20 finding fixture có vị trí** (semgrep, gitleaks, pytest, coverage-debt), trộn cả trong lẫn ngoài diff → **≥ 90%** đúng `path` và `line`. Chính xác thì mục tiêu nên là 100% trên fixture, vì phép tính là tất định.
  - 100% finding không có vị trí (trivy, `threshold:*`) nằm trong thân.
  - Chạy lại lần hai → không có `POST` review mới.
  - Marker do người (không phải Bot) dán → vẫn đăng.
  - 403 → exit 0, kết quả có lỗi.
  - Tiêu đề chứa `@user`, `#123`, `<img>`, backtick → đều đã được làm sạch.
- `tests/test_github.py`: bảng `conclusion_for` và `check_title` cho 3 verdict và exit 3.
- `tests/test_workflow_static.py`: có step "PR review"; không còn step "Security review"; ghim SHA.

## Ngoài phạm vi

Jira (S3-06); gọi review và Jira từ `ci.py` và thêm secret `JIRA_*` (S3-07).
