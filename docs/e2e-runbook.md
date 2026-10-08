# Runbook E2E

Phần "Chạy harness local" mô tả cách chạy chuỗi PR → Select → Gate → Review → Jira → Report **trên máy**, không cần GitHub, Jira hay Anthropic thật (S4-05). Phần "Chạy trên GitHub thật (S4-06)" ở cuối file mô tả công cụ `tools/e2e/` đã có; **chưa có lần chạy nào trên GitHub, Jira hay LLM thật**, nên mọi dòng bằng chứng của S4-06 đang là PENDING.

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

## Chạy trên GitHub thật (S4-06)

**Trạng thái:** công cụ cục bộ đã có và đã được test bằng fixture/fake (`tests/test_e2e_tools.py`, `tests/test_e2e_evidence.py`). Chưa có lần chạy nào trên GitHub, Jira hay LLM thật. Bằng chứng từ harness local hay dữ liệu dựng **không phải** bằng chứng GitHub thật và không được dùng để tick DoD.

### Bức tranh

```
 máy bạn (offline)                    bạn chạy (có tác động ngoài)             máy bạn (offline)
 ┌──────────────────────┐   in lệnh   ┌───────────────────────────┐  lưu JSON  ┌─────────────────────┐
 │ tools/e2e: intake,   │ ──────────▶ │ gh / git push / curl đọc  │ ─────────▶ │ collect: đối chiếu  │
 │ sandbox, prepare,    │             │ (đọc-chỉ khi thu bằng     │            │ bảng kỳ vọng → PASS │
 │ budget, script       │             │  chứng) trên repo sandbox │            │ / FAIL / PENDING    │
 └──────────────────────┘             └───────────────────────────┘            └─────────────────────┘
```

Công cụ **không bao giờ** chạy `gh`, `curl`, `git push` hay gọi LLM: các lệnh đó chỉ được in (test canh điều này). Thiếu file bằng chứng nào thì dòng đó là PENDING, không bao giờ PASS.

### 1. Điều kiện và thông tin cần có

```bash
python -m tools.e2e intake template --out runs/e2e/intake.yaml   # điền mọi giá trị null
python -m tools.e2e intake check runs/e2e/intake.yaml             # exit 1 = còn mục chặn
python -m tools.e2e preflight --intake runs/e2e/intake.yaml --out runs/e2e/preflight.md
```

Mục chặn điển hình (đủ danh sách ở đầu ra của `intake check`): repo và gói GitHub (Free + private không có branch protection, B không chạy được), team QA thật trong CODEOWNERS, tài khoản không phải QA, tài khoản dev, SHA qc-agent, digest image, Jira sandbox kèm `user_map`, xác nhận câu hỏi #3, trần ngân sách.

### 2. Ba nhóm lệnh

`python -m tools.e2e commands --intake runs/e2e/intake.yaml` in toàn bộ, tách ba nhóm:

| Nhóm | Ý nghĩa | Ví dụ |
|---|---|---|
| `offline` | không tốn tiền, không chạm hệ thống ngoài | `pytest tests/test_e2e_tools.py -q`, `sandbox`, `preflight`, `budget`, `collect`, `ledger`, `stability summary`, `timing` |
| `llm` | tốn tiền, gửi dữ liệu ra nhà cung cấp LLM, cần câu hỏi #3 và ngân sách đã duyệt | `eval_cost.py --llm-tokens count --yes`, `eval_selector.py --llm real --runs 3 --yes`, và mọi lượt gate/GT mà workflow gọi Anthropic |
| `external` | tác động GitHub/Jira: tạo repo, secret, branch protection, đẩy nhánh, mở PR, tạo ticket | `gh repo create`, `gh secret set`, `protect_ground_truth.py` |

Quy tắc: lệnh `external` chạy **từng cái một sau khi bạn xác nhận**. `protect_ground_truth.py --dry-run` vẫn gửi một lệnh GET lên GitHub (chỉ đọc), nên nó nằm ở nhóm `external`. Bước vừa kích hoạt workflow gọi LLM vừa tác động GitHub (đẩy PRD, mở PR C/D) được xếp vào nhóm `llm` vì nó tốn tiền.

### 3. Dựng sandbox

```bash
python -m tools.e2e sandbox --intake runs/e2e/intake.yaml                 # dry-run: dựng vào thư mục tạm rồi xoá
python -m tools.e2e sandbox --intake runs/e2e/intake.yaml --apply-local <thư-mục-rỗng>
python -m tools.e2e sandbox --plan                                         # danh sách việc trên GitHub (chỉ in)
```

Sandbox là bản sao của noteboard (kèm OpenAPI mẫu), đã chạy `qc-agent init` (tất định, offline) và có đúng một commit `main`. Nó **không** có PRD, không có `.qc-agent/ground-truth/` và không có suite `gt-functional`: `prepare A` thêm PRD, kịch bản A sinh GT, B duyệt GT. PRD cố ý không nằm trong commit base: workflow `qc-groundtruth` chạy khi `docs/prd/**` được đẩy lên `main`, nên nếu PRD đi cùng lần đẩy đầu tiên thì nó chạy trước khi secret sẵn sàng. Chạy lại lệnh `--apply-local` lên thư mục đã là sandbox thì không đụng gì. Thư mục khác rỗng thì từ chối.

Thứ tự bắt buộc: tạo repo → bật cho Actions mở PR → tạo secret → (PR user_map vào qc-agent) → **A (đẩy PRD lên `main`)** → bật branch protection → B → C, D, E. Đẩy PRD sau khi bật protection sẽ bị chặn vì protection cấm push thẳng vào `main`.

### 4. Năm kịch bản: thao tác → kỳ vọng → bằng chứng

Bằng chứng nằm ở `<evidence>/<kịch bản>/`, kèm `<evidence>/meta.json` (nguồn `github|local|fake`, ghi bằng `python -m tools.e2e meta <evidence> --repo OWNER/REPO --source github`). `python -m tools.e2e commands` in đúng lệnh thu (đọc-chỉ) cho từng file.

| KB | Thao tác (người chạy) | Kỳ vọng | Bằng chứng |
|---|---|---|---|
| A | `prepare A` rồi đẩy commit PRD lên `main` của sandbox | workflow `qc-groundtruth` mở PR từ `qc-agent/gt/<prd-id>`; đúng 1 lời gọi sinh GT; artifact không chứa nội dung PRD | `A/pr.json`, `A/runs.json`, `A/artifact/**/llm_usage.json` |
| B | QA duyệt và merge PR GT; tài khoản không phải QA (có quyền write, **không** phải admin, dùng bản clone riêng) push thẳng và merge PR đụng `.qc-agent/` | push bị từ chối (kèm thời điểm); PR không merge được khi thiếu QA approve; `require_code_owner_reviews=true` | `B/protection.json`, `B/push-attempt.txt`, `B/merge-attempt.json`, `B/qa-merge.json` |
| C | dev mở PR có hồi quy (`BUGS.add("1")` trong `toyapp/app.py`) | Check Run `qc-agent / <project>` failure, BLOCKED ≥ 1 critical; 1 comment dính; có dòng chi phí; `selection.json` có floor | `C/checks.json`, `C/comments.json`, `C/artifact/` |
| D | dev mở PR thêm endpoint không có test | success, `N cảnh báo Low`, inline comment đúng dòng, đúng 1 ticket gán `user_map[tác giả]`; chạy lại cùng commit: `source=cache`, 0 comment/ticket mới | `D/checks.json`, `D/review-comments.json`, `D/jira.json`, `D/rerun/` |
| E | `gh workflow run qc-gate.yml -f workers=semgrep` | `source=manual`, chỉ đúng worker yêu cầu, 0 dòng `diff-select` | `E/job.json`, `E/artifact/` |

```bash
python -m tools.e2e prepare A --repo <sandbox> --tag prd    # commit PRD cục bộ trên main, chưa đẩy đi đâu
python -m tools.e2e prepare C --repo <sandbox> --tag t1     # nhánh cục bộ, chưa đẩy đi đâu; D tương tự
python -m tools.e2e collect <evidence> --intake runs/e2e/intake.yaml --out runs/e2e/ket-qua.md
```

PR của C và D chỉ đụng `toyapp/app.py`: `.github/workflows/**`, `.qc-agent/**` và `Dockerfile` là đường FULL SET (0 lời gọi LLM) nên không đo được Select.

### 5. Mười lượt C+D

```bash
python -m tools.e2e stability script --intake runs/e2e/intake.yaml --out runs/e2e/stability.sh --iterations 10
bash runs/e2e/stability.sh          # mỗi lượt hỏi xác nhận; dừng khi `ledger` báo STOP
python -m tools.e2e stability summary runs/e2e/evidence/stability-results.json
```

Định nghĩa "một lượt xanh" là **giả định cần bạn xác nhận** (plan chỉ nói "trọn chuỗi xanh"): C đúng kỳ vọng (đỏ vì Critical) **và** D đúng kỳ vọng, trên bằng chứng nguồn `github`. Lượt có PENDING hoặc nguồn khác không được tính xanh. DoD cần 10 lượt liên tiếp (1..10, không thiếu số) và ≥ 9 xanh. Lượt đỏ được phân loại `product > llm > infra` theo `cause` của dòng FAIL, chỉ để chẩn đoán; lượt đỏ nào cũng tính vào tỉ lệ. `record` không ghi đè lượt đã ghi trừ khi có `--force`. Script **chưa từng chạy trên GitHub thật**; đã kiểm bằng `gh`/`curl` giả và remote git cục bộ.

### 6. Hiệu năng, token, recall

| Đo | Lệnh | Nhóm |
|---|---|---|
| thời gian gate so với baseline FULL SET | thu `job.json` của D và của `gh workflow run qc-gate.yml --ref <nhánh D>` (không workers); `python -m tools.e2e timing --with-select ... --baseline ...` | external + offline |
| token Diff Agent giảm ≥ 40% (ước lượng) | `python tools/eval_cost.py --dataset noteboard --llm-tokens estimate` | offline |
| token (count_tokens thật) | `python tools/eval_cost.py --dataset noteboard --llm-tokens count --yes` | llm |
| recall thật | `python tools/eval_selector.py --llm real --runs 3 --yes --max-usd <USD>` | llm |
| chi phí mỗi PR | `llm_usage.json` trong artifact; `python -m tools.e2e ledger <evidence> --cap <USD>` | offline |

Baseline là `workflow_dispatch` không workers: không có Select nhưng cũng không có các bước chỉ-PR (review, Jira, refine), nên độ chênh đo cả phần v2 thêm vào. Số mẫu nhỏ thì median yếu; báo kèm số mẫu.

### 7. Ngân sách và điều kiện dừng

`python -m tools.e2e budget --iterations 10` tính **offline** số lượt gọi, dữ liệu gửi đi và chi phí từ `llm/prices.py` và `eval/selector-real.json` (đo thật của S2, trước khi S4-03/S4-04 đổi prompt và pruner nên có thể lệch). Đây là ước tính để xin duyệt, không phải hoá đơn. Trần đề xuất = 1,5 × trường hợp xấu nhất, làm tròn lên $0,5, tối thiểu $1. Đặt trần đó cả ở spend limit của workspace API key, không chỉ ở công cụ.

Dừng và hỏi lại khi: cộng `llm_usage.json` ≥ 80% trần; có lời gọi chưa rõ chi phí (`usage_known: false`); số lời gọi vượt tối đa của phần đang chạy; chưa có xác nhận câu hỏi #3 (không bắt đầu).

### 8. Giới hạn đã biết của bước này

- **Policy lấy từ qc-agent@main.** `configs/projects/noteboard.yaml` có `jira.user_map: {}` và `project_key: QCSB`: ticket của D không có người nhận, và Jira sandbox phải có project `QCSB`, trừ khi sửa policy bằng một PR vào qc-agent (hành động ngoài, cần xác nhận).
- Workflow hard-code `QC_AGENT_REPO: Muteen-Felix/QC-Agent`; repo sandbox phải đọc được policy (qc-agent public, hoặc secret `qc_read_token`) và kéo được image GHCR.
- **Select thật có thể không chọn `schemathesis`** cho PR của C; khi đó C xanh là phát hiện về recall của Selector, không phải lỗi của công cụ.
- PR sinh GT mở bằng `GITHUB_TOKEN` không kích hoạt workflow `pull_request`: job validate chỉ chạy sau khi QA push commit lên PR (hoặc có secret `qc_bot_token`). Cần bật Settings > Actions > General > cho phép Actions tạo PR.
- `secrets: inherit` chuyển **mọi** secret của repo sandbox. Không tạo `OPENAI_API_KEY`/`GEMINI_API_KEY`/`MIDSCENE_*` thì không có kênh LLM nào ngoài Anthropic; G-Eval là advisory và được thiết kế lỗi mềm (`run_geval_advisory`), **nhưng hành vi trong gate thật khi thiếu khoá judge chưa được kiểm chứng**. Tạo các khoá này thì có thêm lời gọi (5 ca golden mỗi lần gate) không nằm trong ước tính Anthropic và không có giá trong `llm/prices.py`.
- GT agent (`agent: true`) gửi **mã nguồn SUT** ra ngoài và chưa từng chạy với API thật: giữ tắt trừ khi câu hỏi #3 cho phép rõ.
- Cache Select theo ref của Actions: PR mở trên nhánh mới không thấy cache của lượt trước, nên 10 lượt được tính 20 lời gọi Select (giả định, đo thật mới biết).
- SUT cần DB (S4-09) chưa nằm trong công cụ này ngoài việc intake nhắc đủ trường; noteboard không cần DB.

### 9. Bảng kết quả 10 lượt

| Lượt | C | D | Nguồn | Phân loại lỗi |
|---|---|---|---|---|
| 1–10 | PENDING | PENDING | — | chưa chạy |

Điền bằng `python -m tools.e2e stability summary`. Công cụ không tick DoD; việc đó thuộc phiên `dod-verify`.
