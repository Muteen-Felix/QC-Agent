# S4-05 · Harness local chạy trọn chuỗi (plan S4.5)

- Branch: `feat/s4-05-local-harness`
- Tiền điều kiện: S4-01 đến S4-03 và **S4-05b** đã merge (S4-05b cho `tools/run_reusable_locally.py` truyền SHA thật; bước này xây tiếp trên đó, không làm song song vì cùng sửa một file). Máy cần Docker.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.5, S4.6 (5 kịch bản A–E)
- `tools/run_reusable_locally.py` (`run_workflow`, `--policy-dir`, `--runner-temp`, `--github-api`, cách bỏ qua step "Pull qc-agent image")
- `tests/fakes.py` (`FakeGitHub`, `FakeJira`, `FakeAnthropic`), `tests/test_reusable_workflow.py`, `tests/test_gt_workflow_local.py`
- `docs/prompts/README.md`, hiệu chỉnh #15 (harness chỉ chạy được job `validate` của workflow GT) và #16 (`docker build` lỗi TLS trên mạng công ty)
- `.github/workflows/qc-gate.reusable.yml`, `.github/workflows/qc-groundtruth.reusable.yml`

## Mục tiêu

Chạy **cùng một chuỗi** với E2E thật ngay trên máy local, không cần GitHub, Jira hay Anthropic thật:

```
PRD → workflow GT (fake LLM) → PR sinh GT → (giả lập QA duyệt) → PR của dev → Select → Gate → Review → Jira
```

Harness này cũng là lưới an toàn cho mọi thay đổi workflow sau này.

## HỎI TRƯỚC

Kịch bản A ở bảng dưới kỳ vọng `FakeGitHub` nhận yêu cầu mở PR. **Harness hiện không làm được điều này**: nó chỉ chạy được job `validate` của `qc-groundtruth.reusable.yml`; job `select`/`generate` cần `actions/checkout` và `gh` thật. Hai cách:
- (a) cho harness bỏ qua `actions/checkout` (workspace đã có sẵn) và trỏ `gh` vào `FakeGitHub` (`GH_HOST`/`GITHUB_API_URL`), để chạy job `generate` gần như nguyên vẹn;
- (b) kịch bản A ở local chỉ chạy `qc-agent gt generate` + `python -m qc_agent.groundtruth.pr_body` trong container và assert file sinh ra + thân PR; phần push nhánh và mở PR thật để S4-06 kiểm.

**Đề xuất (b)**: ít sửa harness, không giả lập `gh`. Báo lựa chọn rồi chờ tôi trả lời.

## Việc cần làm

1. **`tools/run_reusable_locally.py`**
   - Thêm `--anthropic-api URL` (đặt `ANTHROPIC_BASE_URL` cho container) và `--jira-api URL`.
   - Thêm `--scenario full-chain`, chạy tuần tự các kịch bản dưới đây trên một workspace dựng từ `tests/fixtures/sut/noteboard`, và sinh một bảng kết quả.
   - Server giả phải truy cập được từ container qua mạng docker, vì trong container `localhost` không trỏ về máy host. Có hai cách: `host.docker.internal` với `--add-host=host.docker.internal:host-gateway` trên Linux, hoặc chạy fake trong cùng mạng. Chọn một cách và ghi vào docstring.
2. **Kịch bản** (ánh xạ sang A–E của S4-06)

   | # | Kịch bản | Kỳ vọng |
   |---|---|---|
   | A | Sinh GT từ PRD mẫu (theo cách đã chọn ở HỎI TRƯỚC) | (a): có branch `qc-agent/gt/<prd-id>` và `FakeGitHub` nhận yêu cầu mở PR · (b): `gt generate` exit 0, file GT sinh ra đúng, thân PR dựng được và đã làm sạch |
   | B | Áp bản GT "đã duyệt" (fixture của S1-08) | `gt validate` exit 0; nếu còn draft thì exit 1 |
   | C | PR của dev có lỗi Critical (`QC_BUGS=1`) | Gate exit 1, Check `failure`, có inline comment |
   | D | PR của dev chỉ có lỗi Low | Exit 0, Check `success` với tiêu đề có số cảnh báo, inline đúng dòng, đúng 1 ticket trong `FakeJira` |
   | E | `workflow_dispatch` với `workers=semgrep` | Chỉ suite `sast` chạy; `FakeAnthropic` đếm được 0 lời gọi |

   Chạy lại D → 0 comment mới, 0 ticket mới, và Select cache hit (0 lời gọi LLM).
   - **Image**: mạng công ty làm `docker build` lỗi TLS, nên cho phép dùng image dựng sẵn qua `QC_TEST_DOCKER_IMAGE` (giống `tests/test_gt_workflow_local.py`). Image phải có `workers/pytest.yaml` (thiếu thì `t-030` bị skip và gate đỏ).
3. **`tests/test_full_chain_local.py`**: mark `docker` hoặc `slow`, và `skip` kèm lý do khi máy không có Docker. Chạy đủ A–E cộng lượt chạy lại, rồi assert đúng các kỳ vọng ở bảng trên.
4. **Tài liệu**: phần "Chạy harness local" trong `docs/e2e-runbook.md`. File được tạo ở bước này hoặc S4-06, bước nào tới trước thì tạo.

## Ngoài phạm vi

Repo, Jira và key thật (S4-06). Test đường Select thành công với SHA thật (S4-05b, đã xong trước bước này).
