# S4-05 · Harness local chạy trọn chuỗi (plan S4.5)

- Branch: `feat/s4-05-local-harness`
- Tiền điều kiện: S4-01 đến S4-03 đã merge. Máy cần Docker.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.5, S4.6 (5 kịch bản A–E)
- `tools/run_reusable_locally.py` (`run_workflow`, `--policy-dir`, `--runner-temp`, `--github-api`, cách bỏ qua step "Pull qc-agent image")
- `tests/fakes.py` (`FakeGitHub`, `FakeJira`, `FakeAnthropic`), `tests/test_reusable_workflow.py`
- `.github/workflows/qc-gate.reusable.yml`, `.github/workflows/qc-groundtruth.reusable.yml`

## Mục tiêu

Chạy **cùng một chuỗi** với E2E thật ngay trên máy local, không cần GitHub, Jira hay Anthropic thật:

```
PRD → workflow GT (fake LLM) → PR sinh GT → (giả lập QA duyệt) → PR của dev → Select → Gate → Review → Jira
```

Harness này cũng là lưới an toàn cho mọi thay đổi workflow sau này.

## Việc cần làm

1. **`tools/run_reusable_locally.py`**
   - Thêm `--anthropic-api URL` (đặt `ANTHROPIC_BASE_URL` cho container) và `--jira-api URL`.
   - Thêm `--scenario full-chain`, chạy tuần tự các kịch bản dưới đây trên một workspace dựng từ `tests/fixtures/sut/noteboard`, và sinh một bảng kết quả.
   - Server giả phải truy cập được từ container qua mạng docker, vì trong container `localhost` không trỏ về máy host. Có hai cách: `host.docker.internal` với `--add-host=host.docker.internal:host-gateway` trên Linux, hoặc chạy fake trong cùng mạng. Chọn một cách và ghi vào docstring.
2. **Kịch bản** (ánh xạ sang A–E của S4-06)

   | # | Kịch bản | Kỳ vọng |
   |---|---|---|
   | A | Chạy workflow GT với PRD mẫu | Có commit hoặc branch `qc-agent/gt/<prd-id>`, và `FakeGitHub` nhận yêu cầu mở PR |
   | B | Áp bản GT "đã duyệt" (fixture của S1-08) | `gt validate` exit 0; nếu còn draft thì exit 1 |
   | C | PR của dev có lỗi Critical (`QC_BUGS=1`) | Gate exit 1, Check `failure`, có inline comment |
   | D | PR của dev chỉ có lỗi Low | Exit 0, Check `success` với tiêu đề có số cảnh báo, inline đúng dòng, đúng 1 ticket trong `FakeJira` |
   | E | `workflow_dispatch` với `workers=semgrep` | Chỉ suite `sast` chạy; `FakeAnthropic` đếm được 0 lời gọi |

   Chạy lại D → 0 comment mới, 0 ticket mới, và Select cache hit (0 lời gọi LLM).
3. **`tests/test_full_chain_local.py`**: mark `docker` hoặc `slow`, và `skip` kèm lý do khi máy không có Docker. Chạy đủ A–E cộng lượt chạy lại, rồi assert đúng các kỳ vọng ở bảng trên.
4. **Tài liệu**: phần "Chạy harness local" trong `docs/e2e-runbook.md`. File được tạo ở bước này hoặc S4-06, bước nào tới trước thì tạo.

## Ngoài phạm vi

Repo, Jira và key thật (S4-06).
