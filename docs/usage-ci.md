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

### Web UI (tuỳ chọn, cho suite UI/Midscene)
SUT có giao diện web dựng riêng khỏi API thì khai thêm; không khai thì workflow chạy như trước.

```yaml
    with:
      project: myapp
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      sut_ui_dockerfile: apps/web-ui/Dockerfile
      sut_ui_context: apps/web-ui
      sut_ui_port: "8080"                       # mặc định 8080
      sut_ui_health_path: "/"                   # mặc định /
      sut_ui_build_args: |                      # KHÔNG đặt bí mật: build-arg nằm trong lớp image
        VITE_API_URL=http://sut:8000
      sut_env: |
        MYAPP_CORS_ORIGINS=http://ui:8080       # cho phép origin của UI gọi API (tên biến do SUT quy định)
```

Workflow dựng container `ui` cùng mạng docker với `sut`, rồi gate nhận `APP_UI_URL=http://ui:<port>` (suite UI dùng `${env.APP_UI_URL}`). Trình duyệt của Midscene chạy trong container gate nên phân giải được cả `sut` và `ui`; UI nhúng địa chỉ API lúc build thì dùng `http://sut:<cổng>`.

## 2b. Sinh sẵn cấu hình (khuyến nghị) và kiểm trước khi đẩy lên CI

Thay vì viết tay suite/`qc.yml`/config project:

```
qc-agent init --sut-root <repo SUT> --slug myapp --repo owner/myapp   --openapi http://127.0.0.1:8000/openapi.json \      # file hoặc URL của SUT đang chạy
  --projects-dir <repo qc-agent>/configs/projects   [--ui-dockerfile apps/web-ui/Dockerfile --ui-port 8080 --ui-build-arg VITE_API_URL=http://sut:8000]   [--qc-ref <SHA 40 ký tự> --image ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>] [--dry-run] [--force]
```

`init` ghi `.qc-agent/suites/*.yaml`, script k6, flow Midscene, `.github/workflows/qc.yml` và `configs/projects/<slug>.yaml`; **không ghi đè** file đã có (trừ `--force`).
Phần cần hiểu sản phẩm (các bước UI của flow explore, ghim SHA/digest) được đánh dấu `qc-agent:todo`. Trên Git Bash (Windows) đặt `MSYS_NO_PATHCONV=1` để `/api/health` không bị đổi thành đường dẫn Windows.

**Gợi ý flow UI bằng LLM (tuỳ chọn, chỉ cho `ui-explore`, không chặn merge):** thêm `--suggest-ui --ui-url http://127.0.0.1:5173/` (lặp được, tối đa 5 trang).
`init` mở UI đang chạy bằng Chromium, đọc **nhãn hiển thị** (tiêu đề, nút, liên kết, ô nhập; không mã nguồn, không giá trị ô nhập, không query URL), gửi tới endpoint
`MIDSCENE_MODEL_*` (cùng khoá Midscene) và ghi `.qc-agent/midscene/explore.yaml`. Đầu ra bị ép vào lược đồ chặt (chỉ `aiAct/aiTap/aiAssert/aiWaitFor`, ≤3 flow, ≤8 bước),
luôn mang dấu `qc-agent:todo` "GỢI Ý" nên `validate` từ chối cho tới khi người duyệt và xoá dấu. Mỗi lần gửi được ghi vào `.qc-agent/egress.jsonl`; `--dry-run` không bao giờ gọi LLM;
thiếu khoá hay LLM lỗi thì chỉ cảnh báo và giữ khung TODO. Nhãn của trang là dữ liệu không tin cậy (có thể chứa chỉ dẫn nhằm thao túng LLM): đừng bỏ bước duyệt.

Kiểm offline (vài giây, không Docker/mạng/SUT) rồi mới mở PR:

```
qc-agent validate --project myapp --sut-root <repo SUT> --projects-dir <repo qc-agent>/configs/projects [--strict]
```

Exit `0` = ổn, `3` = có lỗi. Nó bắt: schema suite/project, lane xung đột policy, task không có worker, file tham chiếu thiếu, biến `${env.X}` mà workflow không cấp
(vd. `APP_UI_URL` khi chưa khai `sut_ui_dockerfile`), `qc.yml` chưa ghim SHA/digest hoặc sai tên input, và mọi dấu `qc-agent:todo` còn sót.

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
