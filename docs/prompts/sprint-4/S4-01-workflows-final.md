# S4-01 · Workflow tái sử dụng bản cuối + caller `qc-gate.yml` (plan S4.1)

- Branch: `feat/s4-01-workflows-final`
- Tiền điều kiện: Sprint 3 đã nghiệm thu; **S4-09 đã merge** (S4-09 thêm input dịch vụ DB phụ và sửa bước "Start SUT" của reusable workflow, làm trước để workflow không bị sửa hai lần).

## Đọc trước

- `docs/prompts/_common.md`; plan: mục tiêu Sprint 4, S4.1; SRS §4 (Sprint 4 giao `.github/workflows/qc-gate.yml`)
- `.github/workflows/qc-gate.reusable.yml` (bản sau S2-08 và S3-07), `.github/workflows/qc-groundtruth.reusable.yml` (S1-07)
- `src/qc_agent/scaffold/{templates.py (qc_workflow), init.py (hằng `WORKFLOW`, dòng 31), validate.py (_workflow_jobs, REUSABLE), refine.py (hằng `WORKFLOW`, dùng để so `sut_health_path` với OpenAPI)}`, `src/qc_agent/scaffold/tmpl/{qc.yml.tmpl, qc-groundtruth.yml.tmpl}`
- Step "Refine" trong reusable workflow: đang grep `.github/workflows/qc.yml` để tìm marker
- `tests/test_workflow_static.py`, `tests/test_scaffold_{init,templates,validate}.py`, `tests/test_reusable_workflow.py`

## Mục tiêu

Hai workflow tái sử dụng ở trạng thái cuối, đúng thứ tự:

```
select → gate → review → jira → enforce
```

Repo SUT chỉ cần hai file caller: `.github/workflows/qc-gate.yml` (có `pull_request` và `workflow_dispatch` với input `workers`) và `.github/workflows/qc-groundtruth.yml`.

## Việc cần làm

1. **`qc-gate.reusable.yml`**
   - Rà lại thứ tự và điều kiện: checkout (`fetch-depth: 0`) → pull image → fetch policy → start SUT (gồm dịch vụ DB phụ của S4-09 nếu có) → refine (tuỳ chọn) → **Select (PR)** → **gate** → **PR review** → **Jira** → **Report** → upload artifact → cleanup → **enforce**.
   - **Giữ Jira trước Report** (đúng như code hiện tại): Report dựng Check Run, nên phải chạy sau Jira thì cảnh báo Jira 401/503 mới hiện trong Check Run (`tests/test_gatekeeper_e2e.py::test_jira_401_and_503_are_visible_in_check_run`, một dòng DoD S3 đã tick). Plan ghi "review → jira" nhưng không nói vị trí Report; đừng đảo lại.
   - Thêm `actions/cache` (**ghim SHA** giống các action khác) cho thư mục cache Select mà S4-02 dùng. Khoá cache và thư mục sẽ được chốt ở S4-02; bước này chỉ cần đặt chỗ với path `~/.cache/qc-agent/select` và mount vào container.
   - `workflow_dispatch` có `workers` → đi đường manual (đã có từ S2-08); không có `workers` → FULL SET theo policy.
2. **Caller template**: đổi đầu ra của `qc.yml.tmpl` thành `.github/workflows/qc-gate.yml`.
   ```yaml
   on:
     pull_request: {branches: [main]}
     workflow_dispatch:
       inputs:
         workers: {description: "vd semgrep,schemathesis — để trống = toàn bộ theo policy", type: string, default: ""}
   ```
   Truyền `workers: ${{ inputs.workers }}` vào reusable workflow, là input của `with:` chứ không phải `run:`. Thêm `secrets: inherit` như cũ.
3. **Tương thích với repo đang dùng `qc.yml`**:
   - `scaffold/validate.py` nhận cả `qc.yml` lẫn `qc-gate.yml`;
   - `init` thấy đã có `qc.yml` thì **không** sinh thêm `qc-gate.yml` mà in hướng dẫn đổi tên (có `--force` để ghi);
   - step Refine grep cả hai tên file;
   - `scaffold/refine.py` và `scaffold/init.py` đang hard-code `WORKFLOW = ".github/workflows/qc.yml"`: đổi thành tìm `qc-gate.yml` trước rồi tới `qc.yml`. Quên `refine.py` thì refine lặng lẽ không tìm thấy workflow và không đề xuất sửa `sut_health_path`.
4. **`qc-groundtruth.reusable.yml`**: rà lại theo cùng quy ước (ghim SHA, digest, `env:`), và chừa chỗ cho cache theo `prd_sha256` mà S4-02 dùng.
5. **Tài liệu**: `docs/usage-ci.md`, `docs/onboarding.md` (một lệnh `init` sinh đủ hai caller cộng CODEOWNERS), `CLAUDE.md`.

## Test bắt buộc

- `tests/test_workflow_static.py`
  - Đúng thứ tự step.
  - Mọi `uses:`, kể cả `actions/cache`, đều ghim SHA 40 ký tự.
  - Không có biểu thức `${{ }}` chứa input hay secret trong `run:`.
  - Có `workflow_dispatch.inputs.workers` trong template caller.
- `tests/test_scaffold_init.py`: repo mới → sinh `qc-gate.yml`; repo có `qc.yml` → không đè và có hướng dẫn.
- `tests/test_scaffold_validate.py`: cả hai tên file đều qua được `validate`.
- `tests/test_scaffold_refine.py`: repo chỉ có `qc-gate.yml` vẫn được refine tìm thấy.
- `tests/test_reusable_workflow.py` hiện có vẫn xanh (cần Docker; không có thì ghi rõ là skip). Harness trọn chuỗi với `FakeGitHub`, `FakeJira`, `FakeAnthropic` là việc của S4-05, không làm ở đây.

## Ngoài phạm vi

Logic cache (S4-02), prompt caching (S4-03), harness trọn chuỗi (S4-05), kịch bản E2E thật (S4-06). Dịch vụ DB phụ, tài liệu `sut_base_url` và SUT cần DB thuộc S4-09: S4-01 chỉ giữ nguyên những gì S4-09 đã thêm.
