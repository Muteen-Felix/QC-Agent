# qc-agent Sprint 1: Hướng dẫn sử dụng & Onboarding (Ground-Truth)

Dành cho **team dự án bất kỳ** trong công ty. Đọc và làm theo từng bước là tích hợp được qc-agent vào dịch vụ của bạn, **không cần biết code nội bộ của qc-agent**.
Phạm vi: Sprint 1, tức **kiểm thử chức năng API dựa trên PRD** (xem mục 6).

| Bạn là | Đọc mục |
|---|---|
| Tech lead / DevOps (người cài đặt, làm một lần) | 2, 3, 4 |
| BA / PO (người viết PRD) | 2.1, 5.1 |
| QA (người duyệt test case) | 5.2, 5.3, 5.4 |
| Dev | 5.1, 5.5 |

---

## 1. Tổng quan & Lợi ích

### Bài toán thực tế

BA viết PRD, QA ngồi chuyển từng tiêu chí chấp nhận (AC) thành test case API, dev merge code xong mới phát hiện API trả sai mã lỗi. Viết test thủ công chậm, hay sót biên (200/201 ký tự, thiếu field bắt buộc…), và không có gì **chặn** một PR làm hỏng hành vi mà PRD đã chốt.

### qc-agent giải quyết thế nào

- **Từ PRD ra test case tự động.** Bạn push PRD lên `main`, một workflow GitHub Actions gọi LLM (Gemini hoặc Claude) đề xuất test case API. LLM chỉ sinh **dữ liệu** (request + kỳ vọng), không bao giờ sinh code; phần render ra file test do chương trình tất định làm.
- **QA là người quyết định.** Test case ra dưới dạng **PR** với trạng thái `draft`. QA duyệt trong `test-cases.yaml` hoặc file **Excel** `test-cases.xlsx`, đổi sang `approved` (hoặc `rejected` kèm lý do), và thêm edge case của riêng mình.
- **Quality Gate chặn merge.** Sau khi QA duyệt, gate trên mọi PR của dev chỉ chạy các test case `approved`. Fail thì không merge được. LLM **không bao giờ** là người phán xanh/đỏ.

```
BA/Dev sửa PRD ──push main──► Bot (LLM) ──► PR "Ground-Truth: <prd-id>"  (mọi TC = draft)
                                                   │
                         QA: draft → approved / rejected, thêm edge case (YAML hoặc Excel)
                                                   │ push lên PR
                                                   ▼
                          check `gt validate` XANH ──► QA (code owner) approve ──► merge
                                                                                     │
                                         từ đây mọi PR của dev: gate chạy TC approved ◄┘ (đỏ thì chặn merge)
```

### Điều bạn nhận được

| Bạn có | Ở đâu |
|---|---|
| Test case API bám sát PRD, truy vết được về từng AC | `.qc-agent/ground-truth/test-cases.yaml` |
| Bản Excel cho QA không thích sửa YAML | `.qc-agent/ground-truth/test-cases.xlsx` |
| Bộ chấm coverage tất định (AC, kỹ thuật biên/validation, mã trạng thái OpenAPI) | chạy trong `gt validate` |
| Khoá quyền: chỉ QA duyệt được thay đổi `.qc-agent/**` | CODEOWNERS + branch protection |
| Cổng chặn merge của dev dựa trên test case đã duyệt | suite `gt-functional` |

---

## 2. Điều kiện chuẩn bị (Prerequisites)

### 2.0. Checklist nhanh

- [ ] Repo SUT trên GitHub, có **Dockerfile của API** (build và chạy được bằng `docker build` + `docker run`).
- [ ] Docker trên máy người cài đặt (không cần Python, không cần SUT đang chạy).
- [ ] Một PRD đúng quy chuẩn (2.1) nằm dưới `docs/prd/`.
- [ ] File `openapi.json` đã commit trong repo (2.2).
- [ ] Quyền **Admin** trên repo SUT (đặt secret, bật quyền workflow, branch protection).
- [ ] Một **team QA** (hoặc username) có quyền **Write** trên repo, dùng làm code owner.
- [ ] Một API key LLM: `GEMINI_API_KEY` **hoặc** `ANTHROPIC_API_KEY` (2.3).
- [ ] Từ team qc-agent: **image digest** `ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>` (xem Job Summary của workflow `image` bên repo qc-agent, hoặc xin team qc-agent).

### 2.1. Quy chuẩn viết PRD để máy đọc được

Parser PRD là code tất định (không LLM), nên **định dạng quyết định chất lượng đầu ra**. Mẫu chuẩn:

```markdown
---
id: orders                    # (1) tên nhánh/PR của bot; chỉ [a-z0-9-], ví dụ orders, my-sut-orders
title: Đơn hàng
---

# PRD: Đơn hàng

## Phạm vi
- Dịch vụ HTTP/JSON. Lỗi luôn trả JSON có trường `detail`.   # (2) bối cảnh chung, LLM đọc cả phần này

## US-1: Tạo đơn hàng                                          # (3) mỗi user story một heading `US-<n>`

Là khách hàng, tôi muốn tạo đơn để mua hàng.

### Tiêu chí chấp nhận                                         # (4) heading này báo "bên dưới là các AC"

- AC-1.1: `POST /orders` với JSON `{"sku": "A1", "quantity": 2}` hợp lệ trả 201. Response có `id` (số nguyên) và `status` bằng `"new"`.
- AC-1.2: `quantity` ≤ 0 thì trả 422.                          # (5) mỗi AC một dòng `- AC-<n>.<m>:`
- AC-1.3: `sku` dài hơn 50 ký tự thì trả 422; đúng 50 ký tự vẫn hợp lệ (201).
- AC-1.4: Nút "Đặt hàng" hiển thị tổng tiền.                   # (6) AC giao diện: không kiểm được bằng HTTP, sẽ vào `uncovered_acs`

## US-2: Xem đơn hàng
...
```

| # | Quy tắc | Vì sao |
|---|---|---|
| 1 | Front-matter có `id:` (chỉ `a-z`, `0-9`, `-`; chữ Việt có dấu bị bỏ dấu) | `id` đặt tên nhánh `qc-agent/gt/<id>`. Thiếu thì lấy tên file |
| 2 | Heading story dạng `## US-<n>: <tên>` | parser nhận story theo `US-<n>` ở **đầu** heading |
| 3 | Mỗi AC có **ID tường minh** `AC-<n>.<m>:` | thiếu ID thì code băm nội dung ra ID, **sửa chữ là ID đổi** và làm lệch `regen` |
| 4 | **Không trùng ID** (cả `US-` lẫn `AC-`) | trùng ID là lỗi, lệnh dừng (exit 3) |
| 5 | AC phải **kiểm được bằng HTTP**: nêu **method + path + mã trạng thái**, trường trong response, giá trị biên | thiếu thì LLM phải đoán, và QA sẽ phải loại |
| 6 | Tối đa 256 KB/PRD, UTF-8; đuôi `.md` `.markdown` `.txt` `.json` `.yaml`; tối đa **5 PRD** đổi trong một lần push | giới hạn của workflow |

AC tốt và xấu:

| ❌ Mơ hồ (LLM sẽ đoán) | ✅ Máy kiểm được |
|---|---|
| Hệ thống phải phản hồi nhanh | `GET /orders` luôn trả 200 và một mảng JSON |
| Báo lỗi khi dữ liệu sai | `quantity` ≤ 0 thì trả 422, response có trường `detail` |
| Tên không được quá dài | `name` dài hơn 200 ký tự trả 422; đúng 200 ký tự vẫn trả 201 |

Mẹo: nêu rõ **mã lỗi cho từng trường hợp sai** và **giá trị biên** (độ dài, min/max). Bộ chấm coverage sẽ đòi các biên này nếu OpenAPI khai ràng buộc (mục 5.3).

> Đừng đưa khoá, mật khẩu, dữ liệu khách hàng thật vào PRD: **toàn bộ PRD được gửi tới nhà cung cấp LLM** (mục 6).

Kiểm tra parse **offline**, không tốn tiền, trước khi push:

```bash
docker run --rm -v "$PWD:/work:ro" -w /work ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> \
  gt info --prd docs/prd/orders.md
# in JSON: prd_id, sha256, số story, số AC. Số AC lệch với PRD => sửa định dạng trước khi push.
```

### 2.2. File đặc tả API `openapi.json`

CI **chỉ đọc file nằm trong repo** (không với tới URL của SUT đang chạy), nên bạn phải xuất rồi commit. OpenAPI giúp LLM biết chính xác endpoint/ràng buộc, và bật **bộ chấm coverage** (mục 5.3).

Cách trích từ backend (chọn đúng framework của bạn):

```bash
# FastAPI: chạy SUT ở máy bạn rồi tải về
curl -s http://127.0.0.1:8000/openapi.json -o openapi.json

# FastAPI: không cần chạy server
python -c "import json; from app.main import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=2))" > openapi.json

# Spring Boot (springdoc)
curl -s http://127.0.0.1:8080/v3/api-docs -o openapi.json

# ASP.NET Core (Swashbuckle)
curl -s http://127.0.0.1:5000/swagger/v1/swagger.json -o openapi.json

# NestJS: dùng SwaggerModule.createDocument(app, config) rồi JSON.stringify ra file
```

```bash
git add openapi.json && git commit -m "docs: thêm openapi.json cho qc-agent"
```

Lưu ý:
- Đặt ở **gốc repo** (`openapi.json`) cho đơn giản; đường dẫn này sẽ được truyền ở bước `init`.
- Khi API đổi (thêm endpoint, đổi ràng buộc), **xuất lại và commit**. File cũ làm bộ chấm coverage chấm theo API cũ.
- Dùng OpenAPI 3.x. Không có OpenAPI vẫn chạy được, nhưng bộ chấm chỉ chấm được chiều AC (mất chiều kỹ thuật và mã trạng thái).

### 2.3. Quyền hạn GitHub và API key LLM

| Cần | Chi tiết |
|---|---|
| Quyền **Admin** repo SUT | đặt secret, bật quyền workflow, branch protection (mục 4) |
| Team QA có quyền **Write** | team này được ghi vào CODEOWNERS. Sai tên team thì GitHub báo "Unknown owner" và không ai bị ép duyệt. Repo cá nhân: dùng `@username` của cộng tác viên có quyền Write |
| Image qc-agent kéo được | package `qc-agent` public thì bỏ qua. Private: xin team qc-agent cấp quyền *Manage Actions access* cho repo của bạn, hoặc đặt secret `GHCR_PULL_TOKEN` (PAT có `read:packages`) |
| **API key LLM** (chọn một) | `GEMINI_API_KEY` (khoá Google AI Studio) **hoặc** `ANTHROPIC_API_KEY` (mặc định, model `claude-sonnet-5`) |
| `qc_bot_token` (tuỳ chọn, nên có) | PAT/GitHub App của bot (Contents + Pull requests: Read and write). Giúp PR do bot mở **kích hoạt ngay** check `gt validate` (xem mục 5.1) |

Chọn nhà cung cấp LLM theo tiền tố model: model `gemini-*` dùng `GEMINI_API_KEY`, mọi model khác dùng `ANTHROPIC_API_KEY`. Workflow chỉ đưa **đúng một** khoá vào container.

> **Gói GitHub Free + repo private:** branch protection và bắt buộc review **không dùng được** (cần Pro/Team/Enterprise, hoặc để repo public). Khi đó khoá QA chỉ còn là quy ước, không phải cơ chế cưỡng chế.

---

## 3. Tích hợp vào repo SUT: một lệnh Docker

Chạy ở **gốc repo SUT**:

```bash
# Linux / macOS / Git Bash (Windows: đặt MSYS_NO_PATHCONV=1 trước lệnh)
docker run --rm -v "$PWD:/sut" \
  ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> \
  init \
  --sut-root /sut \
  --qa-team @my-org/qa-team \
  --image ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> \
  --prd-glob 'docs/prd/**' \
  --openapi /sut/openapi.json
```

```powershell
# PowerShell (Windows)
docker run --rm -v "${PWD}:/sut" `
  ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> `
  init --sut-root /sut --qa-team @my-org/qa-team `
  --image ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> `
  --prd-glob 'docs/prd/**' --openapi /sut/openapi.json
```

Linux: thêm `--user "$(id -u):$(id -g)" -e HOME=/tmp` sau `docker run --rm` để file sinh ra thuộc về bạn, không thuộc root.

**Xem trước, không ghi gì:** thêm `--dry-run` cuối lệnh. Nên chạy thử một lần.

### Giải thích tham số

| Phần của lệnh | Ý nghĩa |
|---|---|
| `-v "$PWD:/sut"` | gắn thư mục repo hiện tại vào `/sut` trong container; `init` chỉ đọc cây thư mục và **chỉ ghi trong repo của bạn** |
| `ghcr.io/…@sha256:<DIGEST>` (đứng trước `init`) | image qc-agent dùng để chạy lệnh. **Ghim theo digest**, không dùng tag `latest` |
| `--sut-root /sut` | thư mục gốc repo SUT **trong container** (khớp điểm gắn ở trên; mặc định đã là `/sut` nếu có gắn, nên có thể bỏ) |
| `--qa-team @my-org/qa-team` | team QA (hoặc `@username`) được ghi làm **code owner** của `/.qc-agent/`. Thiếu tham số này thì CODEOWNERS dùng owner giữ chỗ kèm `qc-agent:todo` và `qc-agent validate` từ chối |
| `--image …@sha256:<DIGEST>` | image mà **workflow trên CI** sẽ kéo về để chạy `gt generate`/`gt validate`. Ghi vào `qc-groundtruth.yml`. Thiếu thì file có dấu `qc-agent:todo` để bạn điền tay |
| `--prd-glob 'docs/prd/**'` | PRD nào kích hoạt workflow khi push lên `main`. Mặc định đã là `docs/prd/**`. Cần dấu nháy đơn để shell không bung glob |
| `--openapi /sut/openapi.json` | OpenAPI **đã commit trong repo** (đường dẫn trong container). `init` ghi `openapi: "openapi.json"` vào workflow. Bỏ qua tham số này là CI không có OpenAPI |

Tham số tuỳ chọn hay gặp: `--qc-ref <SHA 40 ký tự>` ghim phiên bản workflow tái sử dụng (mặc định lấy từ chính image, thường không cần điền), `--sut-dockerfile PATH` khi `init` báo *không thấy Dockerfile API*, `--slug` đặt tên project.

### File được tự động sinh trong repo SUT

```
repo-sut/
├── .github/
│   ├── workflows/
│   │   ├── qc-groundtruth.yml     ← workflow Ground-Truth: PRD → test case → PR; kiểm `gt validate` trên PR   (CỦA BẠN TRONG SPRINT 1)
│   │   └── qc.yml                 ← workflow Quality Gate cho PR của dev (chạy suite, chặn merge)
│   └── CODEOWNERS                 ← thêm VÙNG do qc-agent quản lý (giữa 2 dấu qc-agent:begin/end), giữ nguyên dòng cũ của bạn
└── .qc-agent/
    └── suites/                    ← định nghĩa các suite của gate (api-contract.yaml, perf-smoke.yaml, sast/secrets/deps…)
```

Vùng CODEOWNERS được sinh:

```
# qc-agent:begin codeowners
/.qc-agent/ @my-org/qa-team                  # chỉ QA duyệt được thay đổi Ground-Truth
/.github/CODEOWNERS @my-org/qa-team          # chỉ QA sửa được chính CODEOWNERS
/.github/workflows/qc-*.yml @my-org/qa-team  # chỉ QA sửa được workflow qc-agent
# qc-agent:end
```

Chạy lại `init` chỉ cập nhật **vùng giữa hai dấu**; mọi dòng ngoài vùng giữ nguyên từng byte.

**Chưa có trong lúc này:** thư mục `.qc-agent/ground-truth/` (test-cases.yaml, test-cases.xlsx, tests_gt/, module-map.yaml…) và suite `gt-functional.yaml`. Chúng do **bot tạo ở lần chạy workflow đầu tiên** (mục 5.1).

### Sau khi chạy `init`

1. Đọc phần in ra: mục *"Còn việc cho người (qc-agent:todo)"* liệt kê dòng cần xử lý. Với Sprint 1, bạn chủ yếu cần: `image` đã điền, `--qa-team` đã đúng.
2. Kiểm nhanh: `docker run --rm -v "$PWD:/sut" <IMAGE> validate --sut-root /sut` (từ chối khi còn TODO hoặc thiếu CODEOWNERS).
3. `git checkout -b chore/qc-agent-onboarding`, commit, **mở PR vào chính repo của bạn**, nhờ QA duyệt (vì PR đụng CODEOWNERS), rồi merge vào `main`.

> `qc.yml` và các suite khác thuộc **Quality Gate chung** của qc-agent; `init` sinh sẵn nhưng phần tinh chỉnh (ghim digest ở `qc.yml`, xoá TODO còn sót, đăng ký project…) nằm ngoài tài liệu này: xem [onboarding.md](onboarding.md). PR onboarding **có thể đỏ lúc đầu** vì `validate` chặn mọi `qc-agent:todo` còn sót; đó là chủ ý.

> **Phải merge vào `main` trước.** `workflow_dispatch` và `pull_request` chỉ có tác dụng khi workflow đã nằm trên nhánh mặc định.

---

## 4. Cấu hình một lần trên giao diện GitHub của repo SUT

Làm theo đúng thứ tự. Tất cả ở **repo SUT**.

### 4.1. Đặt Secret

Vào **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value | Khi nào |
|---|---|---|
| `ANTHROPIC_API_KEY` | khoá Anthropic | mặc định (model Claude) |
| `GEMINI_API_KEY` | khoá Google AI Studio | khi dùng model `gemini-*` (xem bên dưới) |
| `qc_bot_token` | PAT/GitHub App của bot | nên có (mục 5.1, bẫy 1) |

Chỉ cần **một** khoá LLM. Hoặc dùng GitHub CLI:

```bash
gh secret set GEMINI_API_KEY --repo my-org/my-sut        # sẽ hỏi giá trị, không lưu vào lịch sử shell
```

**Dùng Gemini thay Claude:** mở `.github/workflows/qc-groundtruth.yml`, bỏ comment các dòng trong khối `with:`:

```yaml
    with:
      project: my-sut
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      prd_path: ${{ inputs.prd_path || 'docs/prd/**' }}
      openapi: "openapi.json"
      model: gemini-3.6-flash                                  # tiền tố gemini-* => dùng secret GEMINI_API_KEY
      llm_min_interval_s: "12"                                 # giãn cách giữa hai request (≈ 5 RPM, free tier)
      llm_max_retries: "5"                                     # thử lại (exponential backoff) khi 429/5xx
      llm_fallback_models: "gemini-3.8-flash,gemini-2.5-flash" # model dự phòng khi hết quota
    secrets: inherit
```

Thiếu secret thì job `generate` dừng ngay với thông báo rõ, **trước khi gửi bất cứ thứ gì ra ngoài**.

### 4.2. Bật quyền cho Bot tự tạo PR

**Settings → Actions → General → Workflow permissions** → tích **"Allow GitHub Actions to create and approve pull requests"** → Save.

- Không bật thì bước *Open or update the pull request* lỗi.
- Repo thuộc tổ chức: bật ở cấp tổ chức (**Organization settings → Actions → General**) trước, rồi mới bật được ở repo.
- Mức mặc định *Read repository contents* vẫn được: `qc-groundtruth.yml` tự xin đúng quyền `contents: write`, `pull-requests: write`, `packages: read` cho job cần.
- Nếu **Actions permissions** đang là *Allow select actions*, thêm: `actions/checkout`, `actions/upload-artifact`, `docker/login-action` và workflow tái sử dụng `Muteen-Felix/QC-Agent/.github/workflows/*`.

### 4.3. Branch Protection cho `main`

Check `validate` **chỉ chọn được sau khi nó đã chạy ít nhất một lần**. Vì vậy:

1. Làm bước 4.1 và 4.2, rồi chạy PRD đầu tiên (mục 5.1) để có PR Ground-Truth đầu tiên và check `gt validate` chạy.
2. Quay lại đây cấu hình.

**Settings → Branches → Add branch protection rule** (hoặc **Rulesets**), *Branch name pattern*: `main`:

- ✅ **Require a pull request before merging**
  - ✅ **Require approvals** = 1 (hoặc hơn)
  - ✅ **Require review from Code Owners**: bắt buộc team QA duyệt mọi PR đụng `.qc-agent/**`
- ✅ **Require status checks to pass before merging**
  - Ô tìm kiếm, chọn check có tên dạng: **`groundtruth / qc-groundtruth / gt validate (<project>)`** (đây là job `validate`; `<project>` là slug trong `qc-groundtruth.yml`). Tên chính xác hiện trong tab *Checks* của PR Ground-Truth.
  - Nên chọn thêm check của gate dev: `qc-agent / <project>` (xem [onboarding.md](onboarding.md)).
- ✅ **Do not allow bypassing the above settings** / *Include administrators*: nếu muốn cả admin cũng phải qua duyệt
- ✅ Chặn force-push

Cơ chế này làm gì (nói đúng, không hơn):

| Lớp | Chặn cái gì |
|---|---|
| CODEOWNERS + *Require review from Code Owners* | **merge** PR có đụng `.qc-agent/**` khi thiếu duyệt của team QA |
| *Require a pull request before merging* | **push thẳng** vào `main` |
| Required status check `gt validate` | merge khi test case chưa được duyệt xong (còn `draft`, lệch Excel/YAML…) |

Không có lớp nào ngăn người khác *mở* PR sửa `.qc-agent/**`; chúng chỉ làm PR đó không merge được khi thiếu QA.

**Kiểm tay một lần** (nên làm):
1. Tài khoản **không** thuộc team QA push thẳng lên `main` một thay đổi trong `.qc-agent/`: phải bị từ chối.
2. Cũng tài khoản đó mở PR sửa `.qc-agent/ground-truth/test-cases.yaml`: nút merge phải báo cần review của code owner.
3. Tài khoản QA approve: merge được.

---

## 5. Vận hành hằng ngày (Day-to-day Workflow)

### 5.1. BA/Dev sửa PRD, bot tự mở PR

**Bước của BA/Dev:** sửa PRD dưới `docs/prd/`, mở PR vào `main`, merge như thường lệ. Khi merge (tức có push lên `main` đổi file khớp `--prd-glob`):

```
push main (PRD đổi)
   └─► workflow qc-groundtruth
         ├─ job select   : chọn các PRD VỪA ĐỔI khớp glob (tối đa 5)
         └─ job generate : (mỗi PRD một lượt, tuần tự)
               1. lấy prd_id từ front-matter  →  nhánh `qc-agent/gt/<prd-id>`
               2. chưa có test-cases.yaml → `gt generate`   |   đã có → `gt regen` (giữ nguyên TC QA đã quyết)
               3. commit CHỈ thư mục .qc-agent/ lên nhánh bot (không force-push)
               4. mở PR "Ground-Truth: <prd-id>"  (PR đã có → chỉ comment, không ghi đè mô tả của QA)
```

Bạn thấy gì: tab **Actions → qc-groundtruth** chạy xanh, rồi tab **Pull requests** có PR **"Ground-Truth: `<prd-id>`"** do `qc-agent[bot]` mở. Thân PR có số story/AC/test case, AC mồ côi, bảng coverage và **checklist cho QA**. Artifact `qc-groundtruth-<prd-id>-<n>` của run chứa `egress.jsonl` (nhật ký dữ liệu gửi ra LLM) và `summary.json` (số token), giữ 14 ngày.

**Lần đầu tiên (hoặc muốn sinh lại tay):** PRD chưa "vừa đổi" nên workflow không tự chạy. Vào **Actions → qc-groundtruth → Run workflow**, nhập `prd_path` (ví dụ `docs/prd/orders.md`), bấm chạy.

**Bẫy cần biết:**

| # | Bẫy | Cách xử lý |
|---|---|---|
| 1 | PR/commit tạo bằng `GITHUB_TOKEN` **không kích hoạt** workflow `pull_request`, nên PR vừa mở **chưa có check `gt validate`** | Không sao: QA đằng nào cũng phải sửa draft rồi push, lúc đó check chạy. Muốn có ngay: đặt secret `qc_bot_token` (mục 4.1). PR mở bằng PAT của một người thì người đó **không tự approve được** PR của mình: dùng tài khoản bot/GitHub App |
| 2 | Thiếu secret LLM | Job `generate` dừng với thông báo "thiếu secret …"; chưa có gì rời máy |
| 3 | Push chồng lên nhánh bot đúng lúc QA đang push | Push của bot bị từ chối, job đỏ: bấm *Re-run* |
| 4 | Một lần push đổi quá 5 PRD | Job `select` lỗi: dùng *Run workflow* với đường dẫn cụ thể |

### 5.2. Hướng dẫn chi tiết cho QA: duyệt test case

QA làm việc **trên nhánh bot** `qc-agent/gt/<prd-id>` (push thẳng lên đó, PR tự cập nhật).

```bash
git fetch origin
git checkout qc-agent/gt/orders        # <prd-id> là id trong front-matter của PRD
```

Trong `.qc-agent/ground-truth/` có:

| File | Là gì | QA làm gì |
|---|---|---|
| `test-cases.yaml` | **nguồn sự thật** của gate: story/AC và mọi test case | duyệt, sửa, thêm |
| `test-cases.xlsx` | bản Excel của **cùng nội dung** (QA mở file này nếu thích bảng tính) | duyệt, sửa, thêm; rồi `gt import-xlsx` (5.4) |
| `module-map.yaml` | nháp ánh xạ module → file mã nguồn | điền `paths`, đổi `status: approved` |
| `openapi.snapshot.json` | ảnh chụp OpenAPI để chấm coverage | **không sửa** (máy sở hữu) |
| `tests_gt/` | mã pytest sinh máy từ catalog | **KHÔNG sửa tay**: sửa một dòng là `gt validate` báo *drift* |

QA chọn **một** trong hai đường (A: YAML, B: Excel). Đừng sửa cả hai song song.

#### Vòng đời của một test case

```
   LLM đề xuất
   ──────────► draft ──► approved   (gate chạy; fail thì chặn merge)
                  │
                  └────► rejected + rejected_reason   (regen KHÔNG đề xuất lại đúng TC này)
```

| `status` | Ý nghĩa | Gate chạy? | `gt validate` |
|---|---|---|---|
| `draft` | LLM đề xuất, chưa ai duyệt | không | **lỗi** |
| `approved` | QA đã duyệt | **có**, chặn merge nếu fail | ok |
| `rejected` | loại; **bắt buộc** có `rejected_reason` | không | lỗi nếu thiếu lý do |

#### Đường A: sửa `test-cases.yaml`

Mở file, tìm `test_cases:`. Mỗi TC trông như sau (đã chú thích):

```yaml
- tc_id: TC-AC-1.2-3f9a1c            # định danh, do máy đặt: ĐỪNG sửa
  title: quantity bằng 0 bị từ chối
  ac_refs: [AC-1.2]                  # TC này kiểm AC nào (AC phải có trong `stories`)
  kind: api_functional               # api_contract | api_functional (1 bước) | flow (từ 2 bước)
  status: draft                      # ◄── BẠN ĐỔI DÒNG NÀY
  origin: llm                        # llm = máy đề xuất; qa = do QA viết (regen không bao giờ đè)
  steps:
  - request: {method: POST, path: /orders, json: {sku: A1, quantity: 0}}
    expect:
      status: [422]
      json: [{path: $.detail, op: exists}]
```

**Với từng TC**, đối chiếu với **PRD** (không phải với code):

| Tình huống | Làm gì |
|---|---|
| Đúng với PRD | `status: approved` |
| Sai, hoặc đoán điều PRD không nêu (thông điệp lỗi, giá trị cụ thể…) | `status: rejected` + `rejected_reason: "PRD không nêu thông điệp lỗi"` |
| Đúng PRD nhưng chạy đỏ trên SUT sạch | **bug thật của SUT**: giữ `approved`, báo dev |
| Không hiểu vì sao nó đúng | đừng duyệt |

```yaml
  status: rejected
  rejected_reason: "PRD không nêu thông điệp lỗi cụ thể"
```

Ghi chú của QA phải đặt trong trường `notes` của TC (comment YAML **không sống qua `regen`**).

**Thêm edge case của QA** (`origin: qa`): thêm một mục vào `test_cases`, không cần render lại:

```yaml
- tc_id: TC-AC-1.2-qa-quantity-am            # dạng TC-<ac_id>-<mô-tả>, không trùng tc_id khác
  title: quantity âm bị từ chối
  ac_refs: [AC-1.2]
  kind: api_functional
  status: approved
  origin: qa
  steps:
  - request: {method: POST, path: /orders, json: {sku: A1, quantity: -5}}
    expect: {status: [422], json: [{path: $.detail, op: exists}]}
```

Với `flow` (nhiều bước), bước sau dùng `{{tên}}` cho giá trị đã `capture` ở bước trước: `capture: {order_id: $.id}` rồi `path_params: {order_id: "{{order_id}}"}`. Assertion hợp lệ: `eq`, `ne`, `exists`, `absent`, `type`, `len_eq`, `len_gte`, `contains`.

**AC giao diện** (không kiểm được bằng HTTP): khai trong `uncovered_acs` kèm lý do, ví dụ `- {ac_id: AC-1.4, reason: chỉ kiểm được ở giao diện}`.

**Hoàn tất**, làm ba việc cuối:

1. `module-map.yaml`: điền `paths` thật (glob tới **file khai báo route** của từng module, ví dụ `src/orders/**`), **xoá** dòng `qc-agent:todo`, đổi `status: draft` → `status: approved`.
2. `test-cases.yaml`: đổi `status:` **ngoài cùng** (cạnh `stories:`) thành `approved`. Khi đó mọi TC phải là `approved` hoặc `rejected`.
3. Nếu có file xlsx đi kèm: chạy `gt export-xlsx` (5.4) để xlsx khớp YAML.

#### Đường B: sửa `test-cases.xlsx` (Excel)

Mở `.qc-agent/ground-truth/test-cases.xlsx` bằng **Excel hoặc LibreOffice** (Google Sheets có thể làm mất sheet ẩn và danh sách chọn).

| Sheet | Nội dung | QA làm gì |
|---|---|---|
| `HuongDan` | hướng dẫn ngay trong file | đọc |
| `Catalog` | PRD, model, `status` của catalog | đổi `status` thành `approved` khi xong |
| `TestCases` | mỗi dòng một test case | đổi `status` (có danh sách chọn), điền `rejected_reason`/`notes`/`priority`, thêm dòng mới |
| `Steps` / `Assertions` | bước HTTP và assertion của từng TC | sửa/thêm bước và assertion |
| `Uncovered` / `Waivers` | AC không kiểm được bằng HTTP; waiver coverage | thêm/sửa; **duyệt waiver** (`approved`) |
| `SpecConflicts` | chỗ mã nguồn khác PRD (chỉ khi dùng chế độ agent) | đổi `open` → `resolved` sau khi quyết |
| `Coverage` | từng AC/kỹ thuật/API: phủ, miễn hay **gap** | đọc để biết còn thiếu gì |

Quy ước ô: tiêu đề **vàng** = sửa được, **xám** = chỉ đọc (`origin`, `evidence`). Cột nhận theo **tên** nên đổi thứ tự cột được. **Không xoá sheet ẩn `_meta`** (nó giữ ảnh chụp để gộp ba chiều).

Luật quan trọng:
- Chỉ **duyệt** (`status`, `rejected_reason`, `notes`, `priority`): TC giữ `origin: llm`.
- **Sửa nội dung** TC do LLM sinh (title, `ac_refs`, bước, assertion…): được phép, nhưng TC đó thành `origin: qa` (giữ `tc_id`) để `regen` không ghi đè công sức của bạn.
- **TC mới**: thêm dòng ở `TestCases` với `tc_id = NEW-<tên>` (ví dụ `NEW-1`), rồi thêm các dòng `Steps`/`Assertions` **cùng** `tc_id`. Mặc định `approved` và `origin: qa`; import sẽ cấp `tc_id` thật.
- **Xoá**: không xoá được dòng TC do LLM sinh (hãy `rejected` kèm lý do); TC `origin: qa` xoá được.
- Ô công thức (bắt đầu bằng `=`) là **lỗi**.

Sau khi sửa xong **phải đồng bộ về YAML** (mục 5.4), vì gate đọc YAML.

### 5.3. Điều kiện để `gt validate` xanh

`gt validate` là cổng kiểm **offline, tất định, không LLM** (không mạng, không secret). Chạy thử trước khi push:

```bash
# Khai báo một lần cho tiện (đổi <DIGEST>)
export QC_IMAGE=ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
qc() { docker run --rm -v "$PWD:/sut" "$QC_IMAGE" "$@"; }       # Linux: thêm --user "$(id -u):$(id -g)" -e HOME=/tmp

qc gt validate --sut-root /sut
echo $?        # 0 sạch · 1 còn việc cho người · 3 file hỏng/sai schema
```

```powershell
# PowerShell
$env:QC_IMAGE = "ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>"
function qc { docker run --rm -v "${PWD}:/sut" $env:QC_IMAGE @args }
qc gt validate --sut-root /sut
```

(Repo private trên GHCR: `docker login ghcr.io` trước.)

**Exit 1 là bình thường** cho tới khi QA làm xong. Bảng dưới là các lỗi thường gặp và cách xử lý (chữ thông báo có thể khác đôi chút):

| `gt validate` báo | Nghĩa | Cách xử lý |
|---|---|---|
| `N test case còn draft, cần duyệt` | còn TC `draft` | đổi sang `approved` hoặc `rejected` |
| `catalog còn status: draft` | chưa chốt catalog | đổi `status` ngoài cùng thành `approved` |
| `test case rejected thiếu rejected_reason` | loại mà không nêu lý do | điền `rejected_reason` |
| `module-map còn status: draft` / `còn dấu qc-agent:todo` | chưa điền module-map | điền `paths`, xoá TODO, `status: approved` (mục 5.2) |
| `khác bản render lại … (drift)` | ai đó sửa tay `tests_gt/` | **đừng sửa `tests_gt/`**: hoàn nguyên file, sửa `test-cases.yaml`, hoặc chạy `gt regen` |
| xlsx lệch YAML ("QA đã quyết trong xlsx mà YAML chưa thấy") | sửa Excel mà chưa import | `gt import-xlsx` (mục 5.4) |
| `coverage ac 24/25 (96%) dưới ngưỡng 100%` | còn AC chưa có TC `approved` | thêm TC cho AC đó, hoặc khai `uncovered_acs` kèm lý do |
| `coverage api 9/12 … còn thiếu: DELETE /orders/{id} 422` | còn ô (endpoint × mã trạng thái) trong OpenAPI chưa có TC | thêm TC, hoặc `waivers` có lý do (bên dưới) |

**Bộ chấm coverage** (chỉ có khi repo có `openapi.snapshot.json`, tức đã truyền `--openapi`): mặc định đòi **100%** ở ba chiều. Chấm **chỉ TC `approved`**. Catalog còn `draft` thì thiếu coverage chỉ là *cảnh báo*; QA đổi catalog sang `approved` thì thiếu coverage là **lỗi**. Mỗi gap có hai cách đóng:

```yaml
# Cách 1 (ưu tiên): thêm TC có đúng bước/mã trạng thái mà gap yêu cầu.

# Cách 2: miễn có lý do. `target` phải GIỐNG TỪNG KÝ TỰ id gap mà validate in ra.
waivers:
- kind: api                                        # api | technique
  target: "DELETE /orders/{order_id} 422"
  reason_code: not_applicable                      # not_http_reachable | needs_infra_fault | not_applicable | out_of_scope
  reason: order_id là chuỗi tự do nên không có đầu vào nào gây 422
  status: approved                                 # waiver chỉ có hiệu lực khi QA đặt approved
```

Ngưỡng nằm ở `.qc-agent/ground-truth/coverage-policy.yaml` (do QA khoá bằng CODEOWNERS), ví dụ `thresholds: {ac: 1, technique: 1, api: 0.9}`. Chi tiết các ràng buộc OpenAPI nào sinh ra yêu cầu nào: [groundtruth.md §5b](groundtruth.md).

Xanh rồi: `git add .qc-agent && git commit -m "qa: duyệt test case orders" && git push`. Check `groundtruth / qc-groundtruth / gt validate (<project>)` trên PR chuyển xanh → team QA (code owner) **Approve** → **Merge**.

### 5.4. Đồng bộ Excel với YAML (`gt import-xlsx`)

Chỉ cần khi QA làm việc trên Excel. Quan hệ hai chiều:

```
test-cases.yaml  ◄────────── gt import-xlsx ──────────  test-cases.xlsx  ◄── QA sửa trong Excel
   (gate đọc file này)                                        ▲
        └────────── gt generate | regen | export-xlsx ────────┘
                  `gt validate`: hai file phải KHỚP NHAU
```

Quy trình sau khi sửa xong Excel (đóng file Excel trước khi chạy):

```bash
qc gt import-xlsx --sut-root /sut --dry-run     # 1. xem sẽ đổi gì, chưa ghi
qc gt import-xlsx --sut-root /sut               # 2. ghi vào YAML; cấp tc_id thật cho TC mới; xuất lại xlsx
qc gt validate    --sut-root /sut               # 3. phải xanh (exit 0)
git add .qc-agent && git commit -m "qa: duyệt test case (import từ Excel)"   # 4. commit CẢ HAI file
git push
```

Điểm cần nhớ:

- **Commit cả `test-cases.yaml` lẫn `test-cases.xlsx`.** Chỉ commit xlsx là gate không thấy quyết định của bạn.
- Gộp **ba chiều** (ảnh chụp lúc xuất / xlsx / YAML): một bên sửa thì lấy bên đó. **Hai bên cùng sửa một trường theo hai cách khác nhau** là *xung đột*: lệnh thoát exit 1, **không ghi gì**, in `tc_id.trường`; sửa tay một bên rồi chạy lại.
- Ô sai (JSON hỏng, công thức `=…`, sửa cột chỉ đọc) báo lỗi kèm **địa chỉ ô**.
- Lỡ sửa YAML tay (đường A) mà xlsx cũ: `gt validate` chỉ **cảnh báo**; chạy `qc gt export-xlsx --sut-root /sut` để xlsx khớp. Lệnh này từ chối ghi đè nếu xlsx có sửa chưa import (thêm `--force` chỉ khi chắc chắn bỏ các sửa đó).
- `gt regen` (do bot chạy khi PRD đổi) tự gộp xlsx chưa import vào YAML trước khi merge, nên sửa của QA không mất.

### 5.5. Sau khi merge: gate chạy test case đã duyệt

Từ khi PR Ground-Truth được merge, suite `gt-functional` nằm trong `.qc-agent/suites/`. Ở mọi PR của dev, gate chạy **các TC `approved`**, đỏ thì chặn merge (khi đã bật required check ở 4.3).

Gate không có TC `approved` nào thì suite **fail** (gate rỗng không được xanh). TC `approved` mà AC đã bị xoá khỏi PRD thì gate báo lỗi cho tới khi QA sửa `ac_refs` hoặc chuyển `rejected` (chủ ý: đỏ để QA biết PRD đã bỏ một tính năng).

**PRD đổi lần sau** (BA/Dev push lên `main`): bot chạy `gt regen` trên nhánh bot và xếp commit mới lên trên:
- **Giữ nguyên văn** mọi TC `approved`, `rejected` và `origin: qa`.
- Thay các TC `draft` do LLM; TC mới luôn là `draft` → QA duyệt phần mới rồi đi lại mục 5.3.
- TC đã `rejected` không bị đề xuất lại (`tc_id` băm theo nội dung).

Một PRD khác là một nhánh và PR riêng (`qc-agent/gt/<prd-id>`).

---

## 6. Lưu ý và Giới hạn phạm vi

### Phạm vi của tài liệu này

Tài liệu này **chốt ở Sprint 1: kiểm thử chức năng API dựa trên PRD**, gồm: PRD → test case (LLM đề xuất) → QA duyệt (YAML/Excel) → `gt validate` → gate chặn merge bằng TC đã duyệt.

**Chưa nằm trong tài liệu này** (sẽ bổ sung ở các phiên bản sau khi hoàn thiện):

- **Sprint 2:** điều phối chọn test thông minh (Diff Agent chọn suite theo thay đổi của PR, `selection.json`).
- **Sprint 3:** kiểm thử giao diện UI (Midscene) và mô hình mức độ nghiêm trọng mới của gate.

Các AC giao diện (nút bấm, hiển thị…) trong PRD **không sinh test ở Sprint 1**: chúng được khai `uncovered_acs`.

### Quyền riêng tư và chi phí

- **PRD và danh sách endpoint OpenAPI được gửi tới nhà cung cấp LLM bạn chọn** (Anthropic hoặc Google). **Gói miễn phí của Gemini cho phép Google dùng nội dung để cải thiện sản phẩm**: PRD nhạy cảm nên dùng khoá trả phí. Hãy xác nhận với bộ phận bảo mật/pháp chế của công ty trước khi chạy với PRD thật.
- Mọi lời gọi LLM ghi `egress.jsonl` **trước khi gửi** (artifact của workflow, giữ 14 ngày). Log không chứa nội dung PRD, prompt hay phản hồi.
- Chi phí một lần sinh: **một lời gọi LLM** (tối đa 16 000 token ra), cộng tối đa một lần sửa khi đầu ra sai định dạng. Số token nằm trong `summary.json`. Gemini free tier có hạn mức thấp: dùng `llm_min_interval_s`, `llm_max_retries`, `llm_fallback_models` (mục 4.1).

### Chế độ agent (tuỳ chọn, mặc định TẮT)

Workflow có tuỳ chọn `agent: true`: LLM đọc cả **mã nguồn** repo qua nhiều lượt để sinh test sát hơn. Lưu ý:
- **Mã nguồn bị gửi tới Anthropic**; chỉ bật khi đã được phép, và chạy `gitleaks` xoá bí mật đã commit trước.
- Chỉ dùng được với Claude (cần `ANTHROPIC_API_KEY`).
- **Chưa được kiểm chứng với API thật**; tài liệu này không khuyến nghị dùng cho onboarding. Chi tiết: [groundtruth.md §5c](groundtruth.md).

### Giới hạn và lưu ý vận hành

| Giới hạn | Ghi chú |
|---|---|
| Chỉ kiểm thử **HTTP/JSON API** | không có UI, hiệu năng, bảo mật trong luồng Ground-Truth |
| Chất lượng test phụ thuộc chất lượng PRD | AC mơ hồ thì TC do LLM đoán, QA phải loại |
| LLM chỉ **đề xuất** | QA phải duyệt mọi TC; **đừng duyệt TC bạn không hiểu vì sao nó đúng** |
| Tối đa 5 PRD/lần push; mỗi PRD ≤ 256 KB | chia nhỏ hoặc dùng *Run workflow* |
| Không sửa tay `tests_gt/` | sinh máy, sửa là *drift* (exit 1). Đổi image sang phiên bản có mẫu khác cũng gây drift: chạy `gt regen` rồi commit |
| PR từ **fork** không có secret | job `generate` chỉ chạy trên push/`workflow_dispatch` của chính repo; trên PR chỉ chạy `validate` (chỉ-đọc, không mạng) |
| Image phải ghim theo **digest** | không dùng tag trôi; muốn nâng cấp thì đổi digest trong `qc-groundtruth.yml` |
| Repo private + GitHub Free | không có branch protection: khoá QA chỉ là quy ước |

---

## Phụ lục: Checklist onboarding (in ra tick dần)

**Chuẩn bị**
- [ ] PRD đúng quy chuẩn (`id:`, `## US-n`, `- AC-n.m:` có method/path/status), `gt info` ra đúng số AC
- [ ] `openapi.json` đã commit ở gốc repo
- [ ] Có digest image từ team qc-agent; có team QA với quyền Write; có API key LLM

**Tích hợp**
- [ ] Chạy `init` (đã thử `--dry-run`), commit, mở PR onboarding, merge vào `main`
- [ ] Secret `GEMINI_API_KEY` hoặc `ANTHROPIC_API_KEY` (+ `qc_bot_token` nếu có)
- [ ] Bật *Allow GitHub Actions to create and approve pull requests*

**Chạy thử**
- [ ] Actions → qc-groundtruth: *Run workflow* với `prd_path` → có PR "Ground-Truth: `<prd-id>`"
- [ ] QA duyệt (YAML hoặc Excel + `gt import-xlsx`), điền module-map, catalog `approved`
- [ ] `gt validate` exit 0, check xanh trên PR

**Khoá chặt**
- [ ] Branch protection `main`: review Code Owners + required check `groundtruth / qc-groundtruth / gt validate (<project>)`
- [ ] Kiểm tay 3 bước ở mục 4.3
- [ ] QA approve và merge PR Ground-Truth đầu tiên → gate dev bắt đầu chạy TC đã duyệt

Tài liệu tham chiếu sâu hơn: [groundtruth.md](groundtruth.md) (luồng, bộ chấm coverage, Excel, bẫy), [groundtruth-real-sut.md](groundtruth-real-sut.md) (chạy trên SUT thật), [onboarding.md](onboarding.md) (Quality Gate chung).
