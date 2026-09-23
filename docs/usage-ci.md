# Dùng qc-agent làm gate cho một repo SUT

Chế độ tự động (mỗi PR) chạy bằng workflow tái sử dụng `qc-gate.reusable.yml`. Repo SUT chỉ cần một file gọi nó.

## 1. Điều kiện
1. **Project** của repo đã có file `configs/projects/<slug>.yaml` trong repo qc-agent (policy: suite nào chặn merge, suite nào chỉ tham khảo) và image qc-agent đã được phát hành lại sau khi thêm project (config được đóng gói vào image).
2. **Suite** nằm trong repo SUT tại `.qc-agent/suites/<tên>.yaml`. Đường dẫn trong suite tương đối theo gốc repo SUT (worker chạy với `cwd` = gốc repo SUT).
3. **SUT chạy được bằng Dockerfile** (mặc định `./Dockerfile`, cổng `8000`). Ví dụ tham chiếu: `tests/fixtures/sut/noteboard/Dockerfile`.
4. **Image qc-agent**: `ghcr.io/muteen-felix/qc-agent@sha256:<digest>` (digest hiện ở Job Summary của workflow `image`). Bắt buộc ghim theo digest. Nếu package ở chế độ private, repo SUT cần quyền đọc package (Package settings → *Manage Actions access*) hoặc secret `GHCR_PULL_TOKEN`.

## 2. File gọi (trong repo SUT: `.github/workflows/qc.yml`)

```yaml
name: qc
on:
  pull_request:
    branches: [main]

jobs:
  qc:
    permissions:               # quyền TỐI THIỂU; workflow tái sử dụng không xin thêm
      contents: read
      checks: write            # Check Run "qc-agent / <project>"
      pull-requests: write     # comment dính trên PR
      packages: read           # kéo image từ GHCR
    uses: Muteen-Felix/QC-Agent/.github/workflows/qc-gate.reusable.yml@<COMMIT-SHA>
    with:
      project: noteboard
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      # sut_env: |               # biến môi trường cho container SUT (KHÔNG đặt bí mật)
      #   QC_BUGS=none
      # qc_api_url: https://qc.example.com   # lưu lịch sử tập trung (cần secret QC_API_TOKEN)
    secrets: inherit
```

Ghim `@<COMMIT-SHA>` (không dùng `@main`) để một thay đổi ở qc-agent không tự động đổi gate của bạn.

## 3. Secret (khai ở repo SUT)
| Secret | Dùng cho |
|---|---|
| `QC_API_TOKEN` | đẩy kết quả lên service (tạo bằng `qc-agent token create --project <slug> --name ci`) |
| `OPENAI_API_KEY` / `GEMINI_API_KEY`, `QC_JUDGE_*` | judge của DeepEval (chỉ tham khảo, không chặn merge) |
| `MIDSCENE_MODEL_*` | Midscene (discovery, không chặn merge) |
| `ALERT_WEBHOOK_URL`, `ALERT_TELEGRAM_CHAT_ID`, `DASHBOARD_URL` | thông báo webhook |
| `GHCR_PULL_TOKEN` | kéo image private (nếu không dùng quyền của package) |

PR từ **fork** không nhận secret: task cần key sẽ `skipped`; project nên đặt `on_skipped_gate_task: fail` cho mode `pr` để gate không xanh giả.

## 4. Chặn merge
Branch protection của repo SUT → *Require status checks* → chọn job `qc-agent / <project>` (chính job trong workflow). Job **xanh/đỏ theo exit code của gate**: `0` PASS, `1` FAIL, `3` lỗi cấu hình/hệ thống. Check Run, comment và lịch sử chỉ là phần báo cáo: lỗi ở đó không làm đổi kết quả.

## 5. Kết quả ở đâu
- **Comment dính** trên PR (một comment, cập nhật tại chỗ mỗi lần push): bảng task chặn merge, skipped/error, finding tham khảo.
- **Check Run** `qc-agent / <project>`.
- **Lịch sử** trên dashboard (nếu bật `qc_api_url`), link nằm trong comment.
- **Artifact** `qc-runs-<project>-<attempt>` (14 ngày).

## 6. Rủi ro còn lại (biết trước)
- Worker eval của **chính repo SUT** (ví dụ `pytest tests/eval`) chạy mã của PR với các key LLM trong môi trường. Với PR cùng repo, tác giả là người có quyền ghi; hãy dùng key riêng cho CI với **giới hạn ngân sách**.
- Workflow chưa được chạy trên GitHub thật ở thời điểm viết tài liệu này: các bước shell đã được kiểm chứng trên Docker cục bộ bằng `tools/run_reusable_locally.py` và `tests/test_reusable_workflow.py`.
