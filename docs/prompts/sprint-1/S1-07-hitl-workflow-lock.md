# S1-07 · Workflow sinh GT + khoá `.qc-agent/` trên main (plan S1.8 phần workflow, S1.9)

- Branch: `feat/s1-07-gt-workflow-lock`
- Tiền điều kiện: S1-06 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S1.8, S1.9
- `.github/workflows/qc-gate.reusable.yml`: mẫu bắt buộc cho quy ước an toàn:
  - input và secret đi qua `env:` của step, **không** nội suy `${{ }}` vào script;
  - image ghim digest;
  - action ghim SHA;
  - container không chạy root.
- `src/qc_agent/scaffold/{init.py, templates.py, validate.py}`, `src/qc_agent/scaffold/tmpl/qc.yml.tmpl`
- `src/qc_agent/integrations/github.py` (`GitHubClient`), `tests/fakes.py` (`FakeGitHub`)
- `tests/test_workflow_static.py`, `tests/test_scaffold_init.py`

## Mục tiêu

Luồng HITL chạy được trên GitHub:

```
BA push PRD lên main → workflow sinh GT → PR `qc-agent/gt/<prd-id>` → QA sửa draft→approved + thêm edge case
→ CI `gt validate` xanh → QA (code owner) duyệt → merge
```

Người không phải QA không đưa được thay đổi nào vào `.qc-agent/**` trên main.

## Việc cần làm

1. **`.github/workflows/qc-groundtruth.reusable.yml`** (`workflow_call`)
   - **Input**: `project`, `image` (bắt buộc ghim digest, kiểm giống `qc-gate`), `prd_path`, `openapi` (tuỳ chọn), `base_branch` (mặc định `main`).
   - **Secret**: `ANTHROPIC_API_KEY`, `qc_bot_token` (tuỳ chọn).
   - **Job `generate`**, `permissions: {contents: write, pull-requests: write}`:
     - checkout;
     - validate `prd_path` bằng regex (tương đối, không `..`), và `prd-id` khớp `^[a-z0-9][a-z0-9-]{0,63}$`;
     - trong container: chạy `gt regen` nếu đã có catalog, không thì `gt generate`, kèm `--summary-json` và `--egress-dir` trỏ vào `$RUNNER_TEMP`;
     - commit vào branch `qc-agent/gt/<prd-id>`. Branch đã tồn tại thì cập nhật; chỉ được force-push lên branch của bot;
     - mở PR bằng `gh` hoặc REST, thân PR lấy từ summary: số lượng, orphan, warning, và checklist cho QA.
   - **Job `validate`**: dành cho `pull_request` chạm `.qc-agent/**`. Chạy `qc-agent gt validate` với quyền chỉ-đọc, không cần secret.
   - **Egress và usage**: upload làm artifact, **không** commit.
   - **Bẫy cần ghi vào tài liệu**: PR mở bằng `GITHUB_TOKEN` không kích hoạt workflow `pull_request`, nên CI `validate` chỉ chạy khi QA push commit lên PR. Điều này vẫn đúng quy trình, vì QA đằng nào cũng phải sửa. Nếu muốn check chạy ngay thì truyền `qc_bot_token` (App/PAT).
   - PR từ fork không có secret, nên job `generate` chỉ chạy trên `push` hoặc `workflow_dispatch` của chính repo.
2. **Template phía SUT**, trong `src/qc_agent/scaffold/tmpl/`:
   - **`qc-groundtruth.yml.tmpl`**: `on: push` (branch main, `paths: [<prd_glob>]`) + `workflow_dispatch` (input `prd_path`) + `pull_request` (`paths: [.qc-agent/**]`). Workflow gọi reusable workflow, ghim `@<qc_ref>` giống cách `qc_workflow()` làm.
   - **`CODEOWNERS.tmpl`**: vùng được quản lý nằm giữa `# qc-agent:begin codeowners` và `# qc-agent:end`, gồm:
     ```
     /.qc-agent/ {{qa_team}}
     /.github/CODEOWNERS {{qa_team}}
     /.github/workflows/qc-*.yml {{qa_team}}
     ```
3. **`scaffold/init.py` và `templates.py`**
   - Thêm `--qa-team @org/team` và `--prd-glob` (mặc định `docs/prd/**`).
   - Thiếu `--qa-team` → ghi placeholder kèm `qc-agent:todo`, để `validate` từ chối.
   - `CODEOWNERS` đã có sẵn → chỉ chèn hoặc cập nhật vùng được quản lý, không đè phần còn lại.
   - Sinh `.github/workflows/qc-groundtruth.yml`.
4. **`scaffold/validate.py`**: báo lỗi nếu có `.qc-agent/ground-truth/` nhưng CODEOWNERS thiếu quy tắc `/.qc-agent/`.
5. **`tools/protect_ground_truth.py --repo OWNER/REPO [--branch main] [--dry-run]`**
   - `GET` protection hiện tại, rồi **gộp** vào đó; không ghi đè `required_status_checks`, `enforce_admins`, `restrictions`.
   - Bật `required_pull_request_reviews.require_code_owner_reviews = true`, `required_approving_review_count >= 1`, và bắt buộc đi qua PR (chặn push thẳng).
   - Sau đó `PUT`.
   - `--dry-run` chỉ in payload.
   - Token lấy từ `GITHUB_TOKEN`, cần quyền admin, và không bao giờ được in ra.
   - Lỗi → exit 1, kèm thông điệp không lộ token.
6. **`docs/groundtruth.md`**: sơ đồ luồng, các lệnh, vòng đời trạng thái TC, checklist review cho QA, cách thêm edge case, cách cài CODEOWNERS và protection, các bẫy (GITHUB_TOKEN, fork), quyền riêng tư (PRD được gửi tới Anthropic, ghi egress `prd_text`; câu hỏi #3 ở `architecture.md` §5.5 cần được chốt).
   - Nói đúng cơ chế: CODEOWNERS + protection chặn **merge** thay đổi `.qc-agent/**` khi thiếu QA duyệt; "require PR" chặn **push thẳng**. Ghi rõ cách tự kiểm tay trên repo thật (kịch bản B ở S4-06).

## Test bắt buộc

- `tests/test_workflow_static.py` (mở rộng cho workflow mới):
  - mọi `uses:` đều ghim SHA 40 ký tự;
  - không có `${{ inputs.* }}` hay `${{ secrets.* }}` nằm trong khối `run:`;
  - image bắt buộc có `@sha256:`;
  - `permissions` tối thiểu cho từng job;
  - khoá `"on"` được parse đúng.
- `tests/test_scaffold_init.py`: CODEOWNERS mới, CODEOWNERS có sẵn (chỉ vùng được quản lý đổi), thiếu `--qa-team` sinh TODO, và file workflow caller.
- `tests/test_protect_ground_truth.py` với `FakeGitHub`:
  - payload `PUT` có `require_code_owner_reviews: true`;
  - status check cũ được giữ lại;
  - `--dry-run` không gọi `PUT`;
  - lỗi 403 → exit 1, stdout/stderr không có token.
- `tools/run_reusable_locally.py` chạy được job `validate` trên một workspace mẫu, nếu harness hỗ trợ; không thì ghi lý do.

## Ngoài phạm vi

Bật protection trên repo thật: **chỉ làm khi tôi xác nhận**, và thường để tới S4-06. Cache theo `prd_sha256` (S4-02).
