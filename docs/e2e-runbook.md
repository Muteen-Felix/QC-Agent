# Runbook E2E

Phần "Chạy harness local" mô tả cách chạy chuỗi PR → Select → Gate → Review → Jira → Report **trên máy**, không cần GitHub, Jira hay Anthropic thật (S4-05). Phần chạy trên GitHub thật là việc của S4-06 và chưa có ở đây.

Vận hành gate thật (xoay khoá, chi phí, event log, đổi nhà cung cấp): [operations.md](operations.md).

## Chạy harness local

Harness là `tools/run_reusable_locally.py`: chạy các bước `run:` của `.github/workflows/qc-gate.reusable.yml` trên Docker cục bộ, với GitHub/Anthropic/Jira là server giả (`tests/fakes.py`). Nó kiểm **luồng workflow** (bước shell, truyền biến, mạng docker, Check Run/comment/review), **không** kiểm chất lượng Selector (LLM là giả).

### 1. Có image đúng commit

Harness từ chối image không chứng minh được là build từ commit đang thử (`tools/image_check.py`). Image cũ vẫn chạy được nhưng kiểm mã cũ, nên test xanh mà không nói gì về commit này.

```bash
docker build --build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD) -t qc-agent:harness-$(git rev-parse --short=7 HEAD) .
python tools/image_check.py --image qc-agent:harness-<sha7> --db     # exit 0 = đạt
```

Quy tắc kiểm (mặc định, chặt): `QC_AGENT_GIT_SHA` trong image phải bằng `git rev-parse HEAD` và cây làm việc không đổi ở các đường dẫn đầu vào của image (`src schemas workers configs web rules docker pyproject.toml uv.lock package*.json Dockerfile`). Thiếu `--build-arg` (giá trị `unknown`) cũng bị từ chối.

Nguồn image cho test, theo thứ tự: biến `QC_TEST_DOCKER_IMAGE`; hoặc image `qc-agent:harness-<sha7>` nếu có; hoặc build (chỉ khi đặt `QC_HARNESS_BUILD=1`, vì build tải vài GB và mất khoảng 10 phút).

| Biến | Tác dụng |
|---|---|
| `QC_TEST_DOCKER_IMAGE` | image dựng sẵn (vẫn bị kiểm). Dùng khi `docker build` lỗi mạng/TLS (xem "Sự cố") |
| `QC_HARNESS_IMAGE_CHECK=content` | thay lớp 1 bằng so băm `qc_agent/**/*.py` trong image với `src/qc_agent/**/*.py`, cho vòng lặp khi chưa commit. **Giới hạn:** chỉ thấy mã Python của gói, không thấy `schemas/workers/configs/rules/Dockerfile/phụ thuộc` |
| `QC_HARNESS_ALLOW_STALE_IMAGE=1` | cho chạy với image không xác minh được; in cảnh báo, kết quả mang nhãn `IMAGE CHƯA XÁC MINH`. **Không dùng để nghiệm thu** |
| `QC_HARNESS_REQUIRE_DOCKER=1` | thiếu Docker/image là **lỗi**, không skip. Dùng cho lượt kiểm bắt buộc để không xanh vì skip |
| `QC_HARNESS_BUILD=1` | cho test tự `docker build` khi chưa có image |
| `QC_TEST_DOCKER_HOST` | địa chỉ máy chủ nhìn từ container trên Linux (IP của `docker0`); Docker Desktop dùng `host.docker.internal` |

**DB CVE của image.** Suite `deps` chặn khi `trivy.db_age_days > 14`. Build có cache vẫn có thể ra DB cũ (lần build thử ngày 2026-10-07 có DB 9 ngày). Kịch bản nào chạy `deps` (N4) khai `expect_deps=True` và **lỗi cứng trước khi chạy Gate** nếu DB quá 14 ngày; kịch bản khác chỉ cảnh báo. Sau Gate, test đối chiếu `expect_deps` với suite thực sự chạy; lệch là test fail có tên. Làm mới DB bằng build **không cache** (khoảng 10 phút, tải lại Chromium):

```bash
docker build --no-cache --build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD) -t qc-agent:harness-<sha7> .
python tools/image_check.py --image qc-agent:harness-<sha7> --db      # đọc lại tuổi DB
```

Đã đo ngày 2026-10-07 (DB cũ `UpdatedAt` 2026-09-28): `--no-cache-filter trivy` (có trong `docker build --help`) chạy lại stage `trivy` nhưng image cuối **vẫn giữ DB cũ** (hai lần liền, chưa giải thích được), trong khi `--no-cache` cho DB `UpdatedAt` 2026-10-07 (tuổi 1 ngày). Đừng dùng `--no-cache-filter` để làm mới DB.

### 2. Chạy test (nhanh → chậm)

Từ thư mục gốc repo. Test Docker không chạy song song (container `sut`/`ui`/`db` và mạng `qc-net` có tên cố định). Workflow tự dọn các tên này bằng `docker rm -f sut ui db`, nên harness **hỏi Docker trước khi chạy** (chỉ đọc); tên nào đang bị việc khác giữ thì harness dừng với `DockerNamesBusy` (CLI exit 1) và **không xoá gì**. Tự dọn phần dư của lần chạy trước (`docker ps -a`, `docker network inspect qc-net`) rồi chạy lại.

```bash
# (a) không Docker
pytest tests/test_image_check.py tests/test_harness_rewrites.py tests/test_harness_docker_guard.py tests/test_full_chain_exit_codes.py tests/test_node_api_harness_fixture.py tests/test_workflow_static.py -q
# (b) Docker nhẹ
pytest tests/test_image_check_docker.py tests/test_node_api_harness_serve.py -q -rs
# (c) noteboard A–E            (d) node-api-harness N1–N4
pytest tests/test_full_chain_local.py -q -rs -s
pytest tests/test_node_api_harness_local.py -q -rs
# bảng A–E bằng CLI
python tools/run_reusable_locally.py --scenario full-chain
```

`skip` nghĩa là **chưa kiểm chứng**, không phải xanh. Thêm `QC_HARNESS_REQUIRE_DOCKER=1` để thiếu Docker/image thành lỗi. Khi báo kết quả, ghi lệnh, image, SHA và số pass/fail/skip thật.

### 3. Các kịch bản

| Nhóm | Project | Kịch bản | Kiểm |
|---|---|---|---|
| noteboard (đã đăng ký, Python) | `noteboard` | A sinh GT · B duyệt GT · C Critical · D chỉ Low + chạy lại · E `workflow_dispatch` | `tests/full_chain.py` |
| `node-api-harness` (chưa đăng ký, Node, policy mặc định) | `node-api-harness` | N1 sửa route · N2 `eval` + LLM trả rỗng · N3 operation mới bị `exclude_path` · N4 `package-lock.json` gốc | `tests/test_node_api_harness_local.py` |

Mỗi kịch bản assert **từng bước** (Start SUT, Select, Gate, PR review, Jira, Report, Enforce) và các fake. A–E có dòng riêng cho **mã thoát Report** (Enforce chỉ nhìn `exit_code` của gate nên không bắt được Report hỏng): Report exit khác 0 thì kịch bản FAIL (`tests/test_full_chain_exit_codes.py`, không cần Docker).

### 4. Điểm lệch có tên của harness so với workflow thật

1. **`step_rewrites` cho bước "Select (PR)"**: workflow chỉ chuyển `-e ANTHROPIC_API_KEY` vào container, không chuyển `ANTHROPIC_BASE_URL`; harness thêm đúng `-e ANTHROPIC_BASE_URL`. Chuỗi cần đổi phải khớp **đúng một lần**, không thì harness dừng; `tests/test_harness_rewrites.py` canh workflow. Đây là chỗ **duy nhất** harness sửa nội dung workflow.
2. Fake bind `0.0.0.0`, container gọi `host.docker.internal` (hoặc `QC_TEST_DOCKER_HOST`).
3. `HOME` của bước Select trỏ vào thư mục tạm của test (cache Select ở `$HOME/.cache/qc-agent/select`).
4. Giữa hai lần chạy gate trên cùng workspace, harness cất `runs/` ra ngoài workspace (CI thật checkout mới mỗi lần). Để lại thì gitleaks quét `runs/r-0001/report.json` của lần trước và báo `generic-api-key`, lần chạy lại BLOCKED.
5. Kịch bản A chạy `gt generate` và `pr_body` trong container bằng lệnh giống job `generate` (thêm `-e ANTHROPIC_BASE_URL`); **không** chạy nguyên job (checkout, `gh`, push, mở PR).

### 5. Phạm vi kết luận

| Việc | Rút ra được | Không rút ra |
|---|---|---|
| Noteboard A–E | Workflow đúng cho project **đã đăng ký** (policy riêng có `gt-functional`, `ai-eval`, Jira), stack Python | Gì về repo dùng policy mặc định hoặc stack khác |
| `node-api-harness` N1–N4 | Select → Gate → Report chạy cho repo **Node, chưa đăng ký, policy mặc định** | `pytest`/GT/Jira (policy mặc định không có); chất lượng Selector; hiệu quả trên SUT sản phẩm |
| Cả hai | Luồng shell, biến, mạng docker, Check Run/comment/review ở GitHub **giả** | GitHub Actions thật (checkout, cache, quyền), Anthropic/Jira thật, tạo PR thật (S4-06), image GHCR theo digest |
| Kiểm image | Image dùng để chạy được build từ HEAD sạch (hoặc mã Python khớp ở chế độ `content`) | Image trên GHCR đúng với commit đã merge |

LLM là giả: harness chỉ chứng minh Select chạy với payload hiện tại (pruner và định dạng của S4-04), **không** chứng minh recall. Không gọi S4.5 là "đã chạy trên GitHub".

### 6. Giới hạn đã biết (đừng viết kỳ vọng trái chúng)

- **Ticket Jira ở D chưa kiểm chứng trong harness.** `integrations/jira.py` chỉ nhận `JIRA_BASE_URL` là `https` hoặc `http` tới loopback; từ container, FakeJira ở máy chủ chỉ tới được qua `http://host.docker.internal`, nên bước Jira báo `skipped: thiếu cấu hình Jira hoặc secret` và không tạo ticket. Dedup/tạo ticket vẫn được kiểm bằng `tests/test_jira_sync.py`. Muốn kiểm trong harness cần một quyết định riêng (thêm một rewrite thứ hai cho bước Jira, hoặc đổi điều kiện URL).
- **C không có inline comment cho lỗi `QC_BUGS=1`**: finding của schemathesis/pytest không có path/line nên review liệt kê chúng là "ngoài diff". Inline comment cho finding Critical có vị trí được kiểm ở N2 (`src/routes/debug.ts:2`).
- **Policy mặc định**: không có `gt-functional` (không chọn được `pytest`) và không có `jira.project_key` (Jira luôn bị bỏ qua); lockfile/manifest lồng và `*.Dockerfile` không phải FULL SET; `.qc-agent/**` là FULL SET nên PR sửa suite kéo cả `deps` vào (vì thế N3 không sửa suite).
- **App không có dependency prod làm `deps` ra error** (Trivy trả `Results` rỗng) và gate BLOCKED. Fixture `node-api-harness` né bằng `left-pad@1.3.0` chỉ có trong lockfile/`package.json`. Đây là giới hạn của QC-Agent, **không** được coi là đã giải quyết: cần việc sửa riêng (hoặc quyết định chính thức) trước nghiệm thu SUT cuối.
- N3 kiểm **đường Low-only**, không kiểm schemathesis và không kiểm handler (`tests/test_node_api_harness_serve.py` kiểm handler độc lập với gate).

### 7. Sự cố thường gặp

- **`docker build` lỗi mạng/TLS khi tải Chromium** (`ENOTFOUND cdn.playwright.dev`, `UNABLE_TO_GET_ISSUER_CERT_LOCALLY`; thường ở mạng công ty): thử lại **một lần** (các lớp đã cache). Vẫn lỗi thì build ở mạng khác và đặt `QC_TEST_DOCKER_IMAGE`.
- **`StaleImage`**: làm theo lệnh build in kèm trong thông báo.
- **`StaleTrivyDb`**: build lại không cache như ở mục 1.
- Windows: thư mục workspace phải nằm trong repo (`tests/.docker-workspaces/`), Docker Desktop không mount được thư mục tạm; nếu `-v` ra thư mục rỗng thì kiểm tra điều này.
