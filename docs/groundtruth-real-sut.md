# Ground-Truth trên một SUT thật với Gemini: hướng dẫn từng bước

Tài liệu này đi cùng [groundtruth.md](groundtruth.md) (luồng và checklist QA), [onboarding.md](onboarding.md) (lệnh `init`) và `tools/eval_gt_sut.py` (đo). Không dùng `noteboard` để đo nữa: mọi lệnh dưới đây chạy trên **SUT của bạn**, thay `<SUT>` bằng đường dẫn repo SUT (bản clone trên máy) và `OWNER/SUT` bằng tên repo trên GitHub.

## 0. Bài toán và bức tranh chung

**Bài toán thực tế.** Team có một API đang chạy thật và một PRD do BA viết. Câu hỏi để nghiệm thu Sprint 1: *"bộ test case do Gemini đề xuất, sau khi QA duyệt, có đủ tốt để làm cổng chặn merge không?"* Trả lời bằng số, không bằng cảm giác: (a) phủ bao nhiêu AC, (b) chạy xanh bao nhiêu trên SUT sạch, (c) bắt được bao nhiêu lỗi khi ta cố tình chèn lỗi vào SUT.

Luồng đúng theo thiết kế (CI sinh, người duyệt, máy đo):

```
BA push PRD lên main ─► CI (workflow qc-groundtruth, image qc-agent ghim digest, secret GEMINI_API_KEY)
                          │  gt generate | regen  →  test-cases.yaml (mọi TC = draft) + tests_gt/ (mã render tất định)
                          ▼
              PR `qc-agent/gt/<prd-id>` ──► QA (code owner) sửa draft → approved/rejected, thêm edge case
                          │                         │  push lên PR → check `gt validate` chạy (offline, không LLM)
                          ▼                         ▼
                   check xanh + QA approve ──► merge ──► từ đây gate PR của dev chỉ chạy TC đã duyệt
                                                            │
     đo (a) AC coverage, (b) TC xanh, (c) tỷ lệ bắt lỗi:  eval_gt_sut.py (chạy trên máy bạn, SUT sạch + SUT chèn lỗi)
```

Ba việc dễ nhầm:

- `gt generate` **đã gồm bước render** ra file test; không có lệnh render riêng. LLM chỉ trả dữ liệu (JSON), code render ra `tests_gt/` bằng mẫu cố định.
- Gate chỉ chạy TC `approved`. Bộ Gemini sinh ra *chưa được duyệt* thì (c) chưa có nghĩa: phải qua QA trước.
- Người viết mutant và gán nhãn `non_testable` **không nên là người viết prompt/PRD**, nếu không là tự chấm bài mình.

**Thứ tự làm (phụ thuộc quan trọng).** CI chạy `docker run <image@sha256:…> gt generate`, nên mã Gemini chỉ dùng được trong CI **sau khi** qc-agent đã merge vào `main` và workflow `image` build xong. Thứ tự: (1) merge Sprint 1 vào `main` của qc-agent → (2) lấy digest image và SHA commit → (3) `init` và cấu hình repo SUT (mục 2) → (4) chạy CI sinh PR (mục 3) → (5) QA duyệt → (6) đo (mục 4). Trước bước (3) nên smoke test 1 request thật ở máy (mục 1.4).

## 1. Trước khi bắt đầu

### 1.1. Điều kiện của SUT (kiểm trước, đỡ mất quota)

| Điều kiện | Vì sao |
|---|---|
| SUT là **HTTP/JSON API** | Test sinh ra là `pytest` + `httpx` gọi `APP_BASE_URL`; giao diện (UI) không kiểm được, phải khai `non_testable` |
| Chạy được bằng **một lệnh**, nhận cổng qua `{port}` hoặc biến `PORT` | `eval_gt_sut.py` tự bật/tắt SUT cho từng mutant |
| Endpoint đang kiểm **không đòi đăng nhập** ở môi trường test | Runtime của test **chưa hỗ trợ auth động**: `headers` trong catalog là chuỗi tĩnh (không nội suy `{{biến}}`) và không đọc token từ env. Cách tạm: bật chế độ test/tắt auth cho môi trường đo, hoặc để QA thêm header tĩnh của tài khoản test (nhớ: file này được commit, đừng để khoá thật). Đây là giới hạn của Sprint 1 |
| Dùng **DB dùng một lần** (SQLite tạm, container riêng) | Test tạo dữ liệu thật; TC được viết tự đủ nhưng dữ liệu vẫn tồn tại sau lượt chạy |
| Có **OpenAPI** (file trong repo) | Cho LLM danh sách endpoint, kiểm endpoint từng TC, sinh suite `api-contract`. CI chỉ đọc được file **trong repo**, không với tới URL của SUT đang chạy ở máy bạn |

### 1.2. Cài đặt qc-agent và khoá Gemini trên máy bạn (dùng cho smoke test, `init` và đo)

```bash
# trong repo qc-agent
pip install uv && uv sync            # tạo .venv có lệnh `qc-agent`
```

Đặt biến môi trường (PowerShell; Git Bash dùng `export`). Đây là cấu hình **máy bạn**; cấu hình của **CI** đặt ở mục 2.4 và `qc-groundtruth.yml`:

```powershell
$env:GEMINI_API_KEY          = "<khoá AI Studio>"
$env:QC_GT_MODEL             = "gemini-3.6-flash"       # sinh test case
$env:QC_SELECTOR_MODEL       = "gemini-3.5-flash-lite"  # Diff Agent (S2, chưa dùng trong Sprint 1)
$env:QC_LLM_FALLBACK_MODELS  = "gemini-3.8-flash,gemini-2.5-flash"   # dự phòng theo thứ tự
$env:QC_LLM_MIN_INTERVAL_S   = "12"                     # giãn cách ≥ 12 s giữa hai request ≈ 5 RPM (an toàn cho free tier thấp nhất)
$env:QC_LLM_MAX_RETRIES      = "5"                      # backoff khi 429/5xx
```

Provider chọn theo **tiền tố model**: `gemini-*` đi Gemini và đọc `GEMINI_API_KEY`; `claude-*` vẫn đi Claude. Khoá chỉ đọc lúc gọi, không vào log.

Kiểm nhanh model nào **khoá của bạn thực sự gọi được** (danh sách model thay đổi theo thời gian và theo tài khoản):

```bash
curl -s -H "x-goog-api-key: $GEMINI_API_KEY" "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200" | grep '"name"' | grep -E "gemini-(3\.[5-8]|2\.5)-flash"
```

Theo tài liệu Google (kiểm ngày 2026-09-30): `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite` ổn định; `gemini-2.5-flash` chỉ còn cho tài khoản từng dùng. Model nào không có trong danh sách của bạn thì client nhận 404 và tự chuyển sang model dự phòng kế tiếp.

### 1.3. Cách client đối phó với free tier (đã cài sẵn)

| Tình huống | Hành vi |
|---|---|
| Hai request quá sát nhau | ngủ cho đủ `QC_LLM_MIN_INTERVAL_S` (tính cho mọi lời gọi trong cùng tiến trình, kể cả giữa các lượt `--runs`) |
| 429 / 5xx / lỗi mạng | chờ `2·2ⁿ` giây (+ jitter, trần 60 s), tối đa `QC_LLM_MAX_RETRIES` lần; nếu server gửi `Retry-After` hoặc `retryDelay` thì chờ đúng bấy nhiêu |
| 429 do **hết quota ngày**, hoặc server bảo chờ > 90 s | không ngủ vô ích: bỏ model này, thử model dự phòng (quota tính riêng theo model) |
| 404 (model không tồn tại/không có quyền), hoặc hết lượt retry | thử model dự phòng kế tiếp; `test-cases.yaml` ghi đúng model đã trả lời (`generated_by.model`) |
| Timeout, 400/401/403, bị chặn an toàn, sai schema | **không** retry (timeout đã chờ đủ; còn lại retry không giúp gì). Sai schema thì GT generator tự sửa đúng một lần |

Mỗi request rời máy, kể cả lần retry, ghi **một dòng** vào `egress.jsonl` trước khi gửi (trong CI: artifact `qc-groundtruth-<prd-id>-<lần chạy>`, giữ 14 ngày). Ước lượng: một lần `gt generate` = 1–2 request; `--runs 3` = 3–6 request, mất tối thiểu ~1–2 phút khi giãn cách 12 s. Quota ngày của free tier nhỏ: đừng chạy vòng lặp `--runs 10` trừ khi cần.

> **Quyền riêng tư.** Bạn đã đồng ý gửi PRD ra Gemini để nghiên cứu/kiểm thử. Lưu ý điều khoản của Google: nội dung gửi qua **gói miễn phí** có thể được Google dùng để cải thiện sản phẩm. PRD thật của công ty nên chạy bằng khoá trả phí, hoặc chỉ dùng PRD đã lược thông tin nhạy cảm.

### 1.4. Smoke test một request (làm trước khi cấu hình CI)

Code Gemini mới được kiểm bằng test giả lập, **chưa gọi API thật**. Một request thật nhỏ là cách rẻ nhất để phát hiện Google từ chối tham số nào đó, trước khi bạn mất công cấu hình CI:

```bash
qc-agent gt generate --prd <SUT>/docs/prd/smoke.md --sut-root <SUT-scratch> --egress-dir runs/gt-smoke
```

`smoke.md` là PRD 1 story, 2–3 AC (mục 2.1); `<SUT-scratch>` là thư mục tạm, không phải repo thật. Nếu lỗi, thông điệp chỉ nêu mã (`bad_request: HTTP 400 INVALID_ARGUMENT`…): thêm `QC_LOG_FORMAT=json` để xem event `llm.call`; báo lại mã lỗi cho dev qc-agent sửa `llm/client.py`, đừng sửa tay `tests_gt/`.

## 2. Bước 1: chuẩn bị PRD và tích hợp qc-agent vào repo SUT

### 2.1. Viết PRD cho máy đọc được

Parser tất định (không LLM) chia PRD theo cấu trúc; LLM chỉ thấy phần đã parse. Quy tắc tối thiểu:

```markdown
---
id: my-sut-orders            # tên nhánh/PR: chỉ [a-z0-9-]
title: Đơn hàng
---
## US-1: Tạo đơn hàng
Là khách hàng, tôi muốn tạo đơn để mua hàng.

### Tiêu chí chấp nhận
- AC-1.1: `POST /orders` với JSON hợp lệ trả 201; response có `id` (số nguyên) và `status` = "new".
- AC-1.2: `quantity` ≤ 0 thì trả 422.
- AC-1.3: nút "Đặt hàng" hiển thị tổng tiền.          ← UI: sẽ khai non_testable
```

- Mỗi AC có **ID tường minh** `AC-<n>.<m>:` (thiếu ID thì code băm nội dung, ID đổi khi sửa chữ và làm lệch `regen`).
- Viết AC **kiểm được bằng HTTP**: nêu method, path, mã trạng thái, trường trong response, biên (200/201 ký tự…). AC mơ hồ ("nhanh", "thân thiện") sẽ sinh TC do LLM đoán, QA sẽ phải loại.
- Đặt PRD dưới `docs/prd/` (hoặc glob bạn truyền cho `init --prd-glob`); đuôi `.md/.markdown/.txt/.json/.yaml`, tối đa 256 KB, tối đa 5 PRD đổi trong một lần push. PRD là dữ liệu **không tin cậy** với qc-agent (được rào trong prompt), nhưng đừng nhét khoá/mật khẩu vào PRD.

Kiểm tra parse **offline**, không tốn quota:

```bash
qc-agent gt info --prd <SUT>/docs/prd/orders.md
# in JSON: prd_id, sha256, số story, số AC. Số AC lệch với PRD => sửa định dạng trước khi sinh.
```

### 2.2. Đưa OpenAPI vào repo

CI chỉ đọc file **trong repo**. Lấy từ SUT đang chạy ở máy bạn rồi commit:

```bash
curl -s http://127.0.0.1:8000/openapi.json -o <SUT>/openapi.json
```

### 2.3. Lấy image digest và SHA của qc-agent, rồi chạy `init` trong repo SUT

Sau khi Sprint 1 đã merge vào `main` của repo qc-agent:

1. Mở Actions của repo qc-agent → workflow **image** (chạy khi push `main`) → **Job Summary**: chép `ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>`.
2. Chép SHA 40 ký tự của commit `main` (dùng để ghim workflow tái sử dụng; không dùng `@main`).

Chạy `init` ở repo SUT (xem trước bằng `--dry-run`; lệnh ghi file trong repo SUT, không đụng gì ngoài đó):

```bash
qc-agent init --sut-root <SUT> --qa-team @OWNER/qa-team --prd-glob 'docs/prd/**' \
  --openapi <SUT>/openapi.json \
  --qc-ref <SHA-40-KÝ-TỰ> --image ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> --dry-run
```

Bỏ `--dry-run` để ghi. `--qa-team` nhận `@org/team` (repo của tổ chức) hoặc `@username` (repo cá nhân: team không tồn tại). Thiếu `--qc-ref`/`--image` thì file có dấu `qc-agent:todo`; điền tay rồi xoá dòng đó. Kiểm lại: `qc-agent validate --sut-root <SUT>` (từ chối khi còn TODO hoặc thiếu CODEOWNERS).

`init` sinh, ngoài các suite gate: `.github/workflows/qc-groundtruth.yml` (file gọi workflow tái sử dụng) và vùng `/.qc-agent/` trong `.github/CODEOWNERS`.

**Bật Gemini trong `.github/workflows/qc-groundtruth.yml`**: bỏ comment các dòng trong khối `with:` (mặc định vẫn là Claude):

```yaml
    with:
      project: my-sut
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      prd_path: ${{ inputs.prd_path || 'docs/prd/**' }}
      model: gemini-3.6-flash                              # nhà cung cấp theo tiền tố: gemini-* cần secret GEMINI_API_KEY
      llm_min_interval_s: "12"                             # giãn cách giữa hai request (≈ 5 RPM, free tier)
      llm_max_retries: "5"                                 # thử lại (exponential backoff) khi 429/5xx
      llm_fallback_models: "gemini-3.8-flash,gemini-2.5-flash"   # model dự phòng khi 404/hết quota
    secrets: inherit
```

Workflow chỉ đưa **đúng một** khoá LLM vào container (model `gemini-*` → `GEMINI_API_KEY`; còn lại → `ANTHROPIC_API_KEY`) và dừng với thông báo rõ, **trước khi gửi gì ra ngoài**, nếu thiếu khoá.

Commit các file này lên một nhánh, mở PR vào `main` của repo SUT rồi merge (nên có QA duyệt vì đụng CODEOWNERS). `workflow_dispatch` và `pull_request` chỉ có tác dụng khi workflow đã nằm trên nhánh mặc định.

### 2.4. Cấu hình trên repo GitHub của SUT

Làm theo thứ tự; mọi mục ở **repo SUT** (`OWNER/SUT`) trừ khi ghi khác.

**A. Secrets** (Settings → Secrets and variables → Actions → New repository secret; hoặc secret cấp tổ chức):

| Secret | Bắt buộc? | Nội dung |
|---|---|---|
| `GEMINI_API_KEY` | **Có** (khi `model: gemini-*`) | khoá AI Studio. Chỉ job `generate` đọc nó; job `select` và `validate` không có |
| `qc_bot_token` | Nên có | Fine-grained PAT (chỉ repo SUT: *Contents* + *Pull requests* = Read and write) hoặc GitHub App. Không có thì bot dùng `GITHUB_TOKEN` và PR nó mở **không kích hoạt** check `gt validate` (chỉ chạy khi QA push commit lên PR). Lưu ý: PR mở bằng PAT của một người thì người đó **không tự approve được** PR của mình; dùng tài khoản bot/GitHub App |
| `GHCR_PULL_TOKEN` | Chỉ khi image ở GHCR **private** và chưa cấp quyền cho repo SUT | PAT có `read:packages` |

Không cần *Variables* nào. Cấu hình quota Gemini nằm ngay trong `with:` của `qc-groundtruth.yml` (không phải secret).

**B. Cho phép workflow tạo PR** (Settings → Actions → General):
- *Workflow permissions* → bật **"Allow GitHub Actions to create and approve pull requests"**. Không bật thì bước mở PR lỗi. (Nếu là tổ chức, bật ở cấp tổ chức trước rồi mới bật được ở repo.) Mức mặc định *Read* vẫn được: `qc-groundtruth.yml` tự xin `contents: write`, `pull-requests: write`, `packages: read` cho job cần.
- *Actions permissions*: cho phép chạy Actions. Nếu chọn "Allow select actions" thì thêm `actions/checkout`, `actions/upload-artifact`, `docker/login-action` và workflow tái sử dụng `Muteen-Felix/QC-Agent/.github/workflows/*`. Mọi action trong workflow đã ghim SHA.

**C. Cho repo SUT dùng được thứ của repo qc-agent** (làm ở repo/gói của **qc-agent**, một lần):
- Nếu repo qc-agent là **private/internal**: Settings → Actions → General → *Access* → cho phép repo khác cùng chủ sở hữu/tổ chức dùng workflow của nó. Repo public thì bỏ qua.
- Image GHCR: nếu package `qc-agent` **public** thì bỏ qua. Nếu private: trang package → *Package settings* → *Manage Actions access* → thêm repo SUT (quyền Read); hoặc dùng secret `GHCR_PULL_TOKEN` ở mục A.

**D. Đội QA và CODEOWNERS**:
- Team `@OWNER/qa-team` phải **tồn tại** và có quyền **Write** trên repo SUT (repo cá nhân thì dùng `@username` của cộng tác viên có quyền Write). Sai owner thì tab CODEOWNERS báo "Unknown owner" và không ai bị ép duyệt: kiểm bước này trước.
- `.github/CODEOWNERS` (do `init` sinh) phải nằm trên **nhánh mặc định** thì mới có hiệu lực. Vùng do qc-agent quản lý:
  ```
  # qc-agent:begin codeowners
  /.qc-agent/ @OWNER/qa-team
  /.github/CODEOWNERS @OWNER/qa-team
  /.github/workflows/qc-*.yml @OWNER/qa-team
  # qc-agent:end
  ```

**E. Bảo vệ nhánh `main`** (Settings → Branches → Add branch protection rule cho `main`; hoặc Rulesets):
- ✅ *Require a pull request before merging* → *Require approvals* ≥ 1 → ✅ **Require review from Code Owners**.
- ✅ *Require status checks to pass before merging* → thêm check của job `gt validate` (tên hiện trong tab Checks của một PR Ground-Truth, dạng `groundtruth / qc-groundtruth / gt validate (<project>)`; **chỉ tìm được sau khi nó đã chạy ít nhất một lần**, nên làm bước này sau PR Ground-Truth đầu tiên).
- ✅ *Do not allow bypassing* / *Include administrators* nếu muốn cả admin phải qua duyệt; chặn force-push.
- Cách nhanh thay cho bấm tay (cần token **admin** repo; **xem payload trước**):
  ```bash
  GITHUB_TOKEN=<token admin> python tools/protect_ground_truth.py --repo OWNER/SUT --dry-run
  GITHUB_TOKEN=<token admin> python tools/protect_ground_truth.py --repo OWNER/SUT
  ```
  Nó **gộp** vào bảo vệ hiện có (không ghi đè status check/`enforce_admins`), bật `require_code_owner_reviews` và ≥ 1 approval. Không thay cho việc thêm required status check ở trên nếu bạn muốn `gt validate` chặn merge.
- **Gói GitHub Free + repo private**: branch protection/required reviews **không dùng được** (cần Pro/Team/Enterprise, hoặc để repo public). Khi đó khoá QA chỉ còn là quy ước, không phải cơ chế cưỡng chế.

**F. Kiểm tay một lần** (đây cũng là dòng DoD "kiểm tay trên repo thật"):
1. Tài khoản **không** thuộc QA push thẳng lên `main` một thay đổi trong `.qc-agent/`: phải bị từ chối.
2. Cũng tài khoản đó mở PR sửa `.qc-agent/ground-truth/test-cases.yaml`: nút merge phải báo cần review của code owner.
3. Tài khoản QA approve: merge được.

## 3. Bước 2: CI sinh test case, render và QA duyệt (HITL)

### 3.1. Kích hoạt CI sinh Ground-Truth

Hai cách:
- **Tự động:** BA push/merge PRD (đường dẫn khớp `prd-glob`) lên `main` → workflow `qc-groundtruth` chọn các PRD **vừa đổi**.
- **Thủ công (lần đầu, hoặc sinh lại):** repo SUT → Actions → **qc-groundtruth** → *Run workflow* → nhập `prd_path` (ví dụ `docs/prd/orders.md`). Lần push đầu của workflow không tự chạy vì PRD chưa đổi: dùng cách này.

Job `select` chọn PRD → job `generate` (mỗi PRD một lượt, tuần tự) chạy `gt generate` (hoặc `gt regen` nếu đã có `test-cases.yaml`) trong container image ghim digest, commit **chỉ** `.qc-agent/` lên nhánh `qc-agent/gt/<prd-id>` và mở PR "Ground-Truth: `<prd-id>`" (PR đã có thì comment). Không force-push: nhánh bot đã có thì bản mới xếp lên trên, công sức QA không mất. Xem egress/số token ở artifact của run.

Kết quả trong PR (đều dưới `.qc-agent/`):

| File | Là gì |
|---|---|
| `ground-truth/test-cases.yaml` | **thứ QA duyệt**: story/AC, mọi TC `status: draft`, `uncovered_acs` (AC LLM cho là không kiểm được bằng HTTP) |
| `ground-truth/tests_gt/` | mã pytest sinh máy từ catalog; **đừng sửa tay** (`gt validate` sẽ báo drift) |
| `ground-truth/module-map.yaml` | nháp ánh xạ module → file mã nguồn (dùng ở S2) |
| `suites/gt-functional.yaml` | suite cho gate: chỉ chạy TC `approved` |

### 3.2. Chạy cục bộ (smoke, debug, hoặc khi chưa có CI)

Cùng lệnh mà CI chạy, từ máy bạn (mục 1.2 đã đặt biến môi trường):

```bash
qc-agent gt generate --prd <SUT>/docs/prd/orders.md --sut-root <SUT> --openapi <SUT>/openapi.json --summary-json runs/gt/summary.json
```

`summary.json` có số story/AC/TC, AC mồ côi, `usage` (token), `model` đã trả lời. Đã có `test-cases.yaml` thì lệnh từ chối: dùng `regen` (mục 3.5), `--force` sẽ **xoá** TC đã duyệt. Kết quả cục bộ commit lên nhánh rồi mở PR bằng tay, thay cho bot.

### 3.3. QA duyệt trên nhánh bot (checklist rút gọn, bản đầy đủ ở [groundtruth.md](groundtruth.md) §4)

QA clone/checkout nhánh `qc-agent/gt/<prd-id>`, sửa `test-cases.yaml` và **push lên chính nhánh đó** (PR tự cập nhật, check `gt validate` chạy lại). Runtime chỉ chạy TC `approved`, nên để biết TC nào sai sẵn: đổi thử một nhóm TC sang `approved` (không cần render lại) rồi chạy với SUT sạch đang bật:

```bash
APP_BASE_URL=http://127.0.0.1:8000 python -m pytest <SUT>/.qc-agent/ground-truth/tests_gt -q
```

Với **từng** TC:

1. Đối chiếu PRD: assertion có đúng điều PRD nói không? TC đoán thông điệp lỗi/giá trị mà PRD không nêu → `status: rejected` + `rejected_reason: "PRD không nêu"`.
2. TC đúng → `status: approved`.
3. TC đỏ trên SUT sạch: hoặc **LLM đoán sai** (reject), hoặc **SUT lệch PRD** (đó là bug thật: báo dev, giữ TC `approved`).
4. AC mồ côi: thêm TC hoặc ghi vào `uncovered_acs` kèm lý do.
5. Thêm edge case còn thiếu với `origin: qa` (mẫu ở groundtruth.md §5).
6. Điền `module-map.yaml` (`paths`, xoá `qc-agent:todo`, `status: approved`); đổi `status` ngoài cùng của catalog thành `approved`.

### 3.4. Cổng HITL và merge

Check `gt validate` (job `validate`, chỉ đọc, không mạng, không secret) phải xanh. Chạy tay để xem trước khi push:

```bash
qc-agent gt validate --sut-root <SUT>
echo $?     # 0 sạch · 1 còn draft/drift/module-map chưa duyệt/rejected thiếu lý do · 3 file hỏng
```

Exit 1 là **bình thường** cho tới khi QA làm xong. Khi check xanh và code owner (QA) approve thì merge; từ đó gate PR của dev chạy suite `gt-functional` với các TC đã duyệt.

### 3.5. PRD đổi

BA sửa PRD và push lên `main` → CI chạy `gt regen` trên nhánh bot (xếp lên trên). Chạy tay:

```bash
qc-agent gt regen --prd <SUT>/docs/prd/orders.md --sut-root <SUT> --openapi <SUT>/openapi.json
```

Giữ nguyên văn mọi TC `approved`/`rejected`/`origin: qa`; thay TC `draft` do LLM; TC mới là `draft`. Ghi chú của QA phải để trong trường `notes` của TC (comment YAML không sống qua `regen`).

## 4. Bước 3: đo trên SUT thật

Phần đo chạy **trên máy bạn** (không phải CI): nó cần bật/tắt SUT nhiều lần với lỗi chèn vào. Nó đọc bộ đã duyệt từ chính bản clone `<SUT>` (sau khi PR Ground-Truth đã merge và bạn `git pull`).

### 4.1. File cấu hình đo

Tạo `eval/<sut>.yaml` (đặt trong repo qc-agent hoặc repo SUT; đường dẫn tương đối tính từ **file này**):

```yaml
name: my-sut-orders
sut_root: ../my-sut                     # thư mục gốc repo SUT
prd: ../my-sut/docs/prd/orders.md
openapi: ../my-sut/openapi.json         # hoặc URL của SUT đang chạy (chỉ dùng khi đo ở máy bạn)
labeled_by: "QA Nguyễn Văn A, 2026-10-02"     # AI gán nhãn bên dưới: số đo chỉ đáng tin khi là QA
non_testable: [AC-1.3]                  # AC không kiểm được bằng HTTP (UI…): loại khỏi mẫu số coverage

sut:
  start:                                # công cụ tự bật/tắt SUT trên BẢN SAO của sut_root
    cmd: 'C:\repos\my-sut\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port {port}'
    cwd: .                              # tương đối trong bản sao
    health_path: /health                # GET trả < 500 nghĩa là đã lên
    env: {DATABASE_URL: "sqlite:///./eval.db", AUTH_DISABLED: "1"}
    timeout_s: 60
  # copy_ignore: [".git", "node_modules"]   # mặc định chỉ bỏ .git/__pycache__/cache; SUT cần deps trong repo thì đừng bỏ
thresholds: {ac_coverage: 0.9, green_rate: 0.9, mutant_kill_rate: 0.9}
mutants: []                             # điền ở mục 4.4
```

Mẹo: dùng đường dẫn **tuyệt đối tới interpreter/`node`** trong `cmd` (bản sao không có `.venv` nếu bạn bỏ nó). Công cụ đặt sẵn `PORT` và `APP_BASE_URL` cho tiến trình SUT. Chạy tuần tự từng bản một nên một DB tạm là đủ.

### 4.2. Đo (a) độ phủ yêu cầu: AC coverage

**Ý nghĩa:** trong các AC kiểm được bằng HTTP, bao nhiêu AC có ≥ 1 TC do Gemini sinh (TC `rejected` không tính). Đây đo *Gemini bỏ sót yêu cầu* chứ chưa đo TC có đúng không. Lệnh này **tự sinh lại** (`--runs` lượt) trong thư mục tạm, không đè `test-cases.yaml` của SUT và không phụ thuộc CI.

```bash
python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --runs 3 --yes --skip-mutants --out-json eval/out-coverage.json
```

- Cờ `--yes` xác nhận việc gửi PRD ra ngoài; công cụ in ước tính token trước (Gemini free tier: `$0`, tốn quota) rồi mới gọi.
- `--runs 3` sinh 3 lượt độc lập và lấy **median** (LLM không tất định); báo cáo liệt kê AC bị bỏ sót ở lượt thấp nhất. Muốn thử nhanh: `--runs 1`.
- Ngưỡng nghiệm thu: **≥ 90%**.
- Thấp thì xem `missing` trong báo cáo: AC mơ hồ (sửa PRD), AC thật sự không kiểm được qua HTTP (thêm vào `non_testable`, có QA xác nhận), hay Gemini bỏ sót (QA thêm TC `origin: qa`; nếu bỏ sót lặp lại nhiều lượt thì báo dev chỉnh prompt).

Đo độ phủ của **bộ đã duyệt** (sau QA) cũng có trong báo cáo ở bước 4.4 (dòng "thông tin").

### 4.3. Đo (b) độ ổn định khi chạy trên SUT sạch

**Ý nghĩa:** mỗi lượt sinh, TC được ép `approved` **trong thư mục tạm** (file thật không bị đụng) rồi chạy với SUT sạch; tỷ lệ TC pass = mức Gemini "đoán đúng hành vi". Cùng lệnh ở 4.2 đã đo luôn (b), cả hai xuất hiện trong bảng.

```text
| (a) AC coverage của bộ do LLM sinh (median 3 lượt) | 94.4% (17/18 …) | ≥ 90% | ✅ |
| (b) TC xanh trên SUT sạch (median)                 | 91.0%           | ≥ 90% | ✅ |
```

- Ngưỡng nghiệm thu: **≥ 90%** (median).
- TC đỏ ở SUT sạch có hai nguyên nhân, phải phân biệt: **Gemini bịa** (giá trị/thông điệp không có trong PRD → QA `rejected`) hoặc **SUT sai PRD** (bug thật). Danh sách TC đỏ nằm trong `--out-json` (`generation.runs[].green_rate.failing`); chạy lại riêng TC đó bằng pytest để xem response thật.
- Chưa xanh do môi trường (DB bẩn, SUT chưa seed, auth) chứ không do TC thì sửa môi trường trước; đừng hạ ngưỡng.
- Đo ổn định theo nghĩa "chạy lại có ra kết quả như nhau": chạy lại bước 4.4 hai lần với cùng bộ đã duyệt; bộ đã duyệt là dữ liệu cố định nên phải cho cùng kết quả (khác nhau tức SUT/môi trường flaky).

### 4.4. Tạo lỗi thử nghiệm (mutant) và đo tỷ lệ bắt lỗi

**Ý tưởng (mutation testing thủ công):** cố tình chèn một lỗi *hợp lý* vào SUT; nếu bộ TC đã duyệt **đỏ** thì nó "bắt" được lỗi, nếu vẫn **xanh** thì lỗi "sống sót" = lỗ hổng của bộ test. Chỉ có nghĩa khi bộ đã duyệt xanh trên SUT sạch (công cụ tự kiểm và dừng nếu không).

**Nguyên tắc chọn mutant** (để phép đo công bằng):

| Loại lỗi | Ví dụ |
|---|---|
| Mã trạng thái sai | 201 → 200, 404 → 204, 422 → 201 |
| Biên lệch một | giới hạn 200 ký tự thành 199; `>=` thành `>` |
| Thiếu kiểm tra | nhận `title` rỗng, `quantity` âm |
| Sai tác dụng phụ | xoá trả 204 nhưng dữ liệu vẫn còn; ghi đè trường khác |
| Sai dữ liệu trả về | trả mảng rỗng, trả bản ghi của id khác, cắt khoảng trắng |

- **10–15 mutant**, mỗi story ít nhất một; mỗi mutant vi phạm **một AC cụ thể** (ghi vào `acs`).
- Mutant phải **hợp lệ theo schema, không 5xx**: nếu lỗi làm SUT sập thì `api-contract` (Schemathesis) cũng bắt được, không chứng minh được giá trị của bộ Gemini.
- Người chọn mutant **không phải người viết prompt** và tốt nhất là QA; đừng thiết kế lỗi *sau khi* xem TC nào tồn tại.
- Lỗi phải có thật trong hành vi (không phải sửa comment): kiểm bằng tay 1 lần bằng `curl`.

**Cách khai mutant** (chọn **một** trong bốn cho mỗi mutant; chỉ áp lên bản sao, repo thật không bị sửa):

```yaml
mutants:
  # 1) edits: thay đúng một đoạn mã. `find` phải khớp ĐÚNG 1 lần (khác thì công cụ báo lỗi, không đo "lỗi ảo")
  - id: M1-status-201-to-200
    description: POST /orders trả 200 thay vì 201
    acs: [AC-1.1]
    required: true                      # bộ test mà không bắt được mutant này thì KHÔNG đạt
    edits:
      - {file: app/routes/orders.py, find: "status_code=201", replace: "status_code=200"}

  # 2) patch: diff (tạo bằng `git diff > eval/mutants/m2.patch` sau khi sửa tay trên nhánh tạm)
  - id: M2-off-by-one-quantity
    acs: [AC-1.2]
    patch: mutants/m2.patch             # tương đối so với file cấu hình

  # 3) env: SUT đã có cờ lỗi/feature flag
  - id: M3-flag
    acs: [AC-2.1]
    env: {ORDERS_BUG_EMPTY_LIST: "1"}

  # 4) base_url: bạn tự chạy bản lỗi (docker compose profile, staging…); dùng khi không có sut.start
  - id: M4-external
    acs: [AC-3.2]
    base_url: http://127.0.0.1:8100
```

Đo (bộ đã duyệt, **không gọi LLM**, không tốn quota; chạy sau khi PR Ground-Truth đã merge và bạn `git pull` bản clone `<SUT>`):

```bash
python tools/eval_gt_sut.py --config eval/my-sut.yaml --skip-generate --out-json eval/out-mutants.json
```

Công cụ: chạy bộ đã duyệt trên SUT sạch (phải xanh) → với từng mutant, chèn lỗi vào bản sao, bật SUT, chạy bộ → ghi TC nào đỏ. Đọc kết quả:

```text
| Mutant | Bắt? | AC bị vi phạm | TC fail |
| M1-status-201-to-200 | ✅ | AC-1.1 | TC-AC-1.1-3fa2 |
| M2-off-by-one-quantity | ❌ | AC-1.2 | — |          ← sống sót
Mutant sống sót (bộ test bỏ sót): M2-off-by-one-quantity
```

- Ngưỡng nghiệm thu: **kill rate ≥ 90%** và **mọi mutant `required` đều bị bắt**.
- Mutant sống sót → thêm TC `origin: qa` cho AC đó (biên, giá trị âm…) qua một PR Ground-Truth mới rồi đo lại. Đây là cách mở rộng bộ có căn cứ.
- "(bắt bởi TC ngoài AC dự kiến)": mutant bị bắt nhưng không phải bởi TC của AC bạn khai: xem lại nhãn `acs` hoặc TC đang kiểm quá rộng.
- `(đo lỗi)`: pytest thoát mã khác 0/1 (SUT sập giữa chừng, TC approved sai cấu trúc). Không tính là bắt; sửa nguyên nhân rồi đo lại.
- Lỗi `find khớp N lần`/`git apply thất bại`: SUT đã đổi so với lúc viết mutant; cập nhật mutant.

### 4.5. Chạy đủ ba metric một lần và exit code

```bash
python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --runs 3 --yes --out-json eval/out.json
echo $?      # 0 đạt mọi ngưỡng đã đo · 1 không đạt · 3 sai cấu hình/lỗi hệ thống
```

Lệnh này đo (a), (b) trên bộ Gemini **mới** sinh (thư mục tạm, không đè file trong SUT) và (c) trên bộ **đã duyệt** trong SUT; chưa có TC nào `approved` thì bỏ (c) và nhắc trên stderr. Thứ tự khuyến nghị: 4.2/4.3 → QA duyệt và merge (mục 3) → 4.4.

## 5. Tóm tắt các bước theo thứ tự

```text
[qc-agent]  merge Sprint 1 vào main → workflow `image` build → lấy digest + SHA commit            (mục 2.3)
[máy bạn]   smoke test 1 request Gemini thật                                                       (mục 1.4)
[máy bạn]   qc-agent init --qa-team … --qc-ref <SHA> --image <digest>  → bỏ comment `model: gemini-…` → PR → merge   (mục 2.3)
[GitHub]    secrets, Actions permissions, quyền GHCR/workflow, CODEOWNERS + team, branch protection    (mục 2.4)
[máy bạn]   python tools/eval_gt_sut.py … --llm real --runs 3 --yes --skip-mutants                 # đo (a),(b)   (mục 4.2)
[GitHub]    Actions → qc-groundtruth → Run workflow (prd_path)  → PR `qc-agent/gt/<prd-id>`         (mục 3.1)
[QA]        sửa test-cases.yaml trên nhánh bot, `gt validate` xanh, code owner approve, merge       (mục 3.3–3.4)
[máy bạn]   git pull → python tools/eval_gt_sut.py … --skip-generate                                # đo (c)       (mục 4.4)
```

## 6. Điều cần nhớ

- **Cơ chế:** `gt generate` = parse PRD (tất định) → một lời gọi Gemini ép trả JSON qua function calling (tối đa một lần sửa) → render bằng mẫu cố định. LLM không bao giờ viết mã hay phát verdict.
- **Đánh đổi:** chỉ kiểm được điều PRD nêu **và** gọi được bằng HTTP. Auth động, UI, hiệu năng, LLM trong SUT nằm ngoài GT Sprint 1.
- **Số đo phụ thuộc người gán nhãn:** `non_testable` và mutant do QA chọn; dev tự chọn thì "đạt 90%" không đáng tin.
- **Free tier:** chậm (giãn cách + backoff) và có trần quota ngày; đặt `llm_min_interval_s` theo RPM thực của khoá (xem AI Studio → Rate limit). Dữ liệu gói miễn phí có thể được Google dùng để cải thiện sản phẩm.
- **Kết quả Gemini không tất định:** vì vậy dùng median của nhiều lượt, và chỉ bộ **đã duyệt** (cố định) mới làm cổng chặn merge.
- **Image ghim digest phải mới:** image build trước khi có provider Gemini coi `gemini-*` là model Claude và lỗi. Đổi image thì đổi cả `qc_ref` cho khớp.
- **Chưa xác minh với API thật:** provider Gemini được kiểm bằng test giả lập; lần đầu chạy thật hãy làm smoke test ở mục 1.4.

## 7. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Job generate: `thiếu secret GEMINI_API_KEY cho model gemini-…` | chưa đặt secret, hoặc đặt nhầm `ANTHROPIC_API_KEY` | đặt `GEMINI_API_KEY` ở repo SUT (mục 2.4A); chưa có gì rời máy |
| Job generate: `llm_min_interval_s không hợp lệ` (hoặc input khác) | giá trị sai định dạng | số thập phân (`12`, `0.5`); `llm_max_retries` số nguyên ≤ 2 chữ số; `llm_fallback_models` chỉ `gemini-*`, tối đa 5, cách nhau dấu phẩy |
| Job generate lỗi `bad_request`/`unavailable` ngay khi vừa bật Gemini | image ghim cũ, build trước Sprint 1 | ghim digest mới và `qc_ref` mới (mục 2.3) |
| Bước mở PR lỗi `GitHub Actions is not permitted to create or approve pull requests` | chưa bật quyền tạo PR | mục 2.4B |
| `denied to github-actions` / 403 khi pull image | package GHCR private, repo SUT chưa có quyền | mục 2.4C hoặc secret `GHCR_PULL_TOKEN` |
| Không thấy workflow trong Actions / nút *Run workflow* | file chưa nằm trên nhánh mặc định | merge `qc-groundtruth.yml` vào `main` trước |
| PR bot mở ra nhưng không có check `gt validate` | PR tạo bằng `GITHUB_TOKEN` không kích hoạt `pull_request` | push một commit lên PR, hoặc đặt `qc_bot_token` (mục 2.4A) |
| Không tìm thấy check `gt validate` khi thêm required status check | check chưa chạy lần nào | mở PR Ground-Truth đầu tiên cho nó chạy, rồi thêm (mục 2.4E) |
| Tab CODEOWNERS: "Unknown owner" | team không tồn tại hoặc không có quyền Write | mục 2.4D |
| `missing_key: chưa đặt GEMINI_API_KEY` (chạy máy bạn) | có `ANTHROPIC_API_KEY` nhưng model là `gemini-*` | đặt `GEMINI_API_KEY`; chưa có gì rời máy |
| `unavailable: HTTP 429 RESOURCE_EXHAUSTED (đã thử 6 lần)` | vượt RPM/TPM ngay cả sau backoff | tăng `llm_min_interval_s`/`QC_LLM_MIN_INTERVAL_S`, chờ, hoặc thêm model dự phòng |
| `… (hết quota ngày) (đã thử 3 model)` | hết RPD của mọi model | chờ sang ngày (giờ Thái Bình Dương) hoặc dùng khoá trả phí |
| `bad_request: HTTP 400 INVALID_ARGUMENT` | Google từ chối tham số/schema | báo mã lỗi cho dev qc-agent (mục 1.4) |
| `bad_output: bị cắt do max_tokens` | token "thinking" ăn hết ngân sách đầu ra | đặt `QC_GEMINI_THINKING_LEVEL=low` (chạy máy bạn) hoặc chia PRD nhỏ hơn |
| `refused` | bộ lọc an toàn của Gemini chặn | xem lại nội dung PRD; không có cách "vượt" bộ lọc |
| `SUT không lên …` + 8 dòng log | `sut.start.cmd`/`health_path`/deps sai | chạy đúng lệnh đó tay ở thư mục bản sao để xem lỗi |
| `bộ Ground-Truth chưa có TC nào approved` | QA chưa duyệt hoặc chưa `git pull` | duyệt/merge rồi đo lại với `--skip-generate` |
| Bộ đã duyệt không xanh trên SUT sạch | TC sai, SUT lệch PRD, hoặc môi trường bẩn | sửa trước; `baseline_green: false` làm phép đo (c) vô nghĩa nên công cụ dừng |
