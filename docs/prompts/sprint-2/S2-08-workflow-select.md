# S2-08 · Bước Select trong CI + `workflow_dispatch` cho manual (plan S2.10)

- Branch: `feat/s2-08-workflow-select`
- Tiền điều kiện: S2-05 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S2.10; README "Hiệu chỉnh" #7 (merge-base, `fetch-depth`)
- `.github/workflows/qc-gate.reusable.yml`:
  - checkout đang dùng `fetch-depth: 2` vì coverage-debt dùng `HEAD^1`;
  - bước "Run qc-agent gate" dùng `set +e` và ghi `exit_code`;
  - comment an toàn ở đầu file.
- `tests/test_workflow_static.py`, `tests/test_reusable_workflow.py`, `tools/run_reusable_locally.py`
- `docs/usage-ci.md`

## Mục tiêu

Trên `pull_request`, CI chạy bước **Select** trước gate. Bước Select không bao giờ làm job đỏ: lỗi thì gate chạy FULL SET.

Trên `workflow_dispatch` có danh sách worker, gate đi đường manual: không LLM, không floor.

## Việc cần làm

1. **Input và secret mới** trong reusable workflow:
   - input `workers` (string, mặc định rỗng);
   - secret `ANTHROPIC_API_KEY` (không bắt buộc).

   PR từ fork không có secret, nên sẽ fallback `missing_api_key` → FULL SET. Đây là hành vi đúng thiết kế.
2. **Checkout** đổi sang `fetch-depth: 0`. Ghi comment giải thích lý do (merge-base của Select), và xác nhận coverage-debt với `HEAD^1` vẫn chạy đúng. Nêu trade-off về thời gian clone trong `docs/usage-ci.md`.
3. **Step `Select (PR)`**, `if: github.event_name == 'pull_request'`, đặt **trước** gate:
   - `BASE_SHA` và `HEAD_SHA` lấy từ `github.event.pull_request.base.sha` và `.head.sha`, đi qua `env:` chứ không nội suy vào script.
   - Chạy `qc-agent select --project … --mode … --sut-root /work --base "$BASE_SHA" --head "$HEAD_SHA" --out /work/runs/selection.json` trong image. Key truyền bằng `-e ANTHROPIC_API_KEY`, không đặt trên dòng lệnh.
   - Ghi output `ok=true` chỉ khi exit 0 **và** file tồn tại. Step dùng `set +e`, và **không** được làm job đỏ.
4. **Step gate**
   - `pull_request` và Select ok → thêm `--trigger pr --selection /work/runs/selection.json`.
   - `pull_request` và Select lỗi → không truyền selection (FULL SET), kèm `::warning::`.
   - `workflow_dispatch` có `workers` khác rỗng → kiểm bằng regex `^[a-z0-9][a-z0-9,_-]*$` trong bash, sai thì `::error::` và exit 1. Sau đó thêm `--trigger manual --workers "$WORKERS"` như **một argv**.
   - Các trường hợp còn lại giữ nguyên như cũ.
5. **Artifact**: `selection.json` nằm trong `runs/`, nên đã được upload theo `runs/`. Kiểm lại đường dẫn.
6. **`docs/usage-ci.md`**
   - Bước Select, secret `ANTHROPIC_API_KEY` (dùng key có trần ngân sách), hành vi với fork.
   - Cách đọc mục "Phạm vi chạy".
   - Cách chạy manual bằng `workflow_dispatch`. Caller template sẽ được thêm input này ở S4-01.
   - Chi phí trung bình dự kiến: để trống, sẽ điền ở S4.

## Test bắt buộc

- `tests/test_workflow_static.py`
  - Có step Select, và nó nằm trước gate.
  - Không có `${{ inputs.workers }}`, `${{ secrets.* }}`, hay `github.event.pull_request.*` bên trong khối `run:`.
  - Mọi `uses:` vẫn ghim SHA. `fetch-depth: 0`.
  - Step Select không thể làm job đỏ: có `set +e` hoặc `continue-on-error`, và gate không phụ thuộc exit code của nó.
- `tests/test_reusable_workflow.py` / harness local (nếu chạy được trên máy):
  - PR có `FakeAnthropic` → `--selection` được truyền;
  - thiếu key → FULL SET;
  - dispatch `workers=semgrep` → manual.

## Ngoài phạm vi

Cache (S4-02), caller template `qc-gate.yml` (S4-01), bước review và Jira (S3).
