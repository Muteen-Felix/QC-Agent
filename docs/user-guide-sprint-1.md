# qc-agent Sprint 1: Hướng dẫn nhanh (Ground-Truth)

Dành cho team dự án bất kỳ: làm theo từng bước là tích hợp được, không cần biết code nội bộ của qc-agent.
Phạm vi: **kiểm thử chức năng API dựa trên PRD** (mục 6).

| Bạn là | Đọc mục |
|---|---|
| Tech lead / DevOps (cài một lần) | 2, 3, 4 |
| BA / PO (viết PRD) | 2.1, 5.1 |
| QA (duyệt test case) | 5.2 đến 5.4 |

---

## 1. Tổng quan

BA viết PRD, QA chuyển từng tiêu chí chấp nhận (AC) thành test case API, và không có gì chặn một PR làm hỏng hành vi PRD đã chốt. qc-agent làm ba việc:

1. **PRD → test case.** Push PRD lên `main`, một workflow gọi LLM (Claude) đề xuất test case. LLM chỉ sinh **dữ liệu** (request + kỳ vọng), không sinh code; file test do chương trình tất định render.
2. **QA quyết định.** Test case ra dưới dạng **PR** với trạng thái `draft`. QA đổi sang `approved` hoặc `rejected`, và thêm edge case.
3. **Gate chặn merge.** Sau khi QA duyệt, mọi PR của dev chỉ chạy các test case `approved`. Đỏ thì không merge được. LLM **không bao giờ** phán xanh/đỏ.

```
Sửa PRD ──push main──► Bot (LLM) ──► PR "Ground-Truth: <prd-id>" (mọi TC = draft)
                                          │  QA: draft → approved / rejected, thêm edge case
                                          ▼
                          check `gt validate` XANH ──► QA (code owner) approve ──► merge
                                                                                     │
                                  từ đây mọi PR của dev: gate chạy TC approved ◄─────┘
```

---

## 2. Chuẩn bị

### 2.0. Checklist

- [ ] Repo SUT trên GitHub, có **Dockerfile của API**; máy cài đặt có Docker.
- [ ] PRD đúng quy chuẩn (2.1) dưới `docs/prd/`, và `openapi.json` đã commit (2.2).
- [ ] Quyền **Admin** repo; một **team QA** (hoặc username) có quyền **Write**.
- [ ] `ANTHROPIC_API_KEY` (nên có spend limit trong Console).
- [ ] Từ team qc-agent: **image digest** `ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>` (Job Summary của workflow `image` bên repo qc-agent). Dưới đây gọi là `<IMAGE>`.

### 2.1. Quy chuẩn viết PRD

Parser PRD là code tất định, nên **định dạng quyết định chất lượng đầu ra**:

```markdown
---
id: orders                    # tên nhánh/PR của bot; chỉ [a-z0-9-]
title: Đơn hàng
---

## Phạm vi
- Dịch vụ HTTP/JSON. Lỗi luôn trả JSON có trường `detail`.

## US-1: Tạo đơn hàng

### Tiêu chí chấp nhận

- AC-1.1: `POST /orders` với `{"sku": "A1", "quantity": 2}` trả 201. Response có `id` (số nguyên) và `status` là `"new"`.
- AC-1.2: `quantity` ≤ 0 thì trả 422.
- AC-1.3: `sku` dài hơn 50 ký tự trả 422; đúng 50 ký tự vẫn trả 201.
- AC-1.4: Nút "Đặt hàng" hiển thị tổng tiền.     # giao diện: không kiểm được bằng HTTP
```

| Quy tắc | Vì sao |
|---|---|
| Story là `## US-<n>: <tên>`; mỗi AC một dòng `- AC-<n>.<m>:` với **ID tường minh** | Thiếu ID thì code băm nội dung ra ID, sửa chữ là ID đổi và `regen` lệch |
| **Không trùng ID** (`US-` lẫn `AC-`) | Trùng là lỗi, lệnh dừng (exit 3) |
| AC phải kiểm được bằng HTTP: nêu **method + path + mã trạng thái**, trường response, giá trị biên | Thiếu thì LLM phải đoán và QA phải loại |
| Tối đa 256 KB/PRD, UTF-8; tối đa 5 PRD đổi trong một lần push | Giới hạn của workflow |

Ví dụ: ❌ "Hệ thống phản hồi nhanh" → ✅ "`GET /orders` luôn trả 200 và một mảng JSON". Nêu rõ **mã lỗi cho từng trường hợp sai** và **giá trị biên**; bộ chấm coverage sẽ đòi các biên này nếu OpenAPI có ràng buộc.

> **Đừng đưa khoá, mật khẩu, dữ liệu khách hàng thật vào PRD:** toàn bộ PRD được gửi tới nhà cung cấp LLM (mục 6).

Kiểm tra parse **offline**, không tốn tiền, trước khi push:

```bash
docker run --rm -v "$PWD:/work:ro" -w /work <IMAGE> gt info --prd docs/prd/orders.md
```

> **PowerShell:** viết `"${PWD}:/work:ro"` thay vì `"$PWD:/work:ro"`, và đặt biến bằng `$env:TEN = "giá trị"`. Hoặc chạy các khối bash trong Git Bash.

Kết quả là JSON có `prd_id`, số story, số AC. Số AC lệch với PRD thì sửa định dạng trước khi push.

### 2.2. File `openapi.json`

CI **chỉ đọc file nằm trong repo**, nên phải xuất rồi commit. Có OpenAPI thì LLM biết đúng endpoint/ràng buộc và bật được bộ chấm coverage (5.3); không có vẫn chạy nhưng chỉ chấm được chiều AC.

```bash
curl -s http://127.0.0.1:8000/openapi.json -o openapi.json          # FastAPI đang chạy
curl -s http://127.0.0.1:8080/v3/api-docs -o openapi.json           # Spring Boot (springdoc)
git add openapi.json && git commit -m "docs: thêm openapi.json cho qc-agent"
```

Đặt ở **gốc repo**. API đổi thì **xuất lại và commit**, nếu không bộ chấm chấm theo API cũ. PowerShell: dùng `curl.exe` (có đuôi `.exe`), đừng dùng `>` vì ghi UTF-16.

### 2.3. Quyền hạn và khoá

| Cần | Chi tiết |
|---|---|
| Quyền **Admin** repo | đặt secret, bật quyền workflow, branch protection |
| Team QA có quyền **Write** | được ghi vào CODEOWNERS; sai tên thì GitHub báo "Unknown owner" và không ai bị ép duyệt. Repo cá nhân: dùng `@username` |
| Kéo được image | package public thì bỏ qua; private thì xin cấp *Manage Actions access* hoặc đặt secret `GHCR_PULL_TOKEN` (PAT `read:packages`) |
| `ANTHROPIC_API_KEY` | khoá LLM duy nhất qc-agent dùng. Model mặc định `claude-sonnet-5` |
| `qc_bot_token` (nên có) | PAT/GitHub App của bot (Contents + Pull requests: Read and write); giúp PR do bot mở kích hoạt ngay check `gt validate` |

> **GitHub Free + repo private:** không có branch protection (cần Pro/Team/Enterprise, hoặc để repo public). Khi đó khoá QA chỉ còn là quy ước.

### 2.4. API có đăng nhập (tuỳ chọn)

Nếu API đòi token, thêm `.qc-agent/ground-truth/auth.yaml` **trước khi chạy `gt generate`**. Có file này thì LLM biết xác thực đã được xử lý tự động; không có thì test case cho endpoint bảo vệ sẽ đỏ vì thiếu token.

```yaml
version: 1
login:
  path: /api/auth/login
  json:
    username: {env: QC_TEST_USERNAME}     # giá trị lấy từ biến môi trường lúc chạy, KHÔNG viết thẳng vào file
    password: {env: QC_TEST_PASSWORD}
  token_path: $.token                     # chỗ lấy token trong response (tập con JSONPath); ĐỔI theo đúng tên trường trong response đăng nhập của SUT
header: {name: Authorization, scheme: Bearer}
scope: session                            # session: đăng nhập một lần cho cả lượt chạy; case: mỗi test case một phiên
```

- Runtime tự đăng nhập và gắn header vào **mọi request**, trừ chính endpoint đăng nhập. Test "thiếu token": đặt header `Authorization` là chuỗi rỗng `""` (runtime không gửi header đó). Test "token sai": `Bearer invalid`.
- Dùng một **tài khoản TEST riêng, quyền thấp**. Tên đăng nhập và mật khẩu đặt ở secret `QC_TEST_USERNAME`, `QC_TEST_PASSWORD` của repo (mục 4.1), không bao giờ vào file. Chạy cục bộ thì đặt hai biến môi trường đó.
- Chỉ nhận biến `QC_TEST_*`, và CI hiện chỉ truyền hai biến trên (`gt validate` cảnh báo nếu file dùng biến khác). Sai cấu hình hoặc đăng nhập thất bại là **`error`** (hạ tầng), không phải `fail`; thông báo chỉ nêu tên biến hoặc mã HTTP, không lộ mật khẩu hay token.
- Phiên đăng nhập có thể bị thu hồi. Nếu có test case gọi đăng xuất, đặt `scope: case` để mỗi test case có phiên riêng; nếu không, test case đăng xuất sẽ làm các test sau đỏ.
- **Chưa hỗ trợ:** token cố định riêng của endpoint nội bộ (QA thêm header tĩnh, đừng để khoá thật), OAuth và cookie phiên, nhiều vai trò (admin/user).

---

## 3. Tích hợp vào repo SUT: một lệnh Docker

Chạy ở **gốc repo SUT** (ví dụ monorepo có Dockerfile ở `apps/api-server/`):

```bash
docker run --rm -v "$PWD:/sut" -w /sut <IMAGE> init \
  --sut-root /sut --qa-team @my-org/qa-team --image <IMAGE> \
  --prd-glob "docs/prd/**" --openapi /sut/openapi.json \
  --sut-dockerfile "apps/api-server/Dockerfile" --sut-context "apps/api-server"
```

Thêm `--dry-run` để xem trước, không ghi gì (nên chạy thử một lần). Windows Git Bash: đặt `MSYS_NO_PATHCONV=1` trước lệnh. Linux: thêm `--user "$(id -u):$(id -g)" -e HOME=/tmp` để file sinh ra không thuộc root.

| Tham số | Ý nghĩa |
|---|---|
| `--qa-team` | team QA (hoặc `@username`) làm **code owner** của `/.qc-agent/`. Thiếu thì CODEOWNERS dùng owner giữ chỗ và `validate` từ chối |
| `--image` | image mà **workflow trên CI** kéo về để chạy `gt generate`/`gt validate` |
| `--prd-glob` | PRD nào kích hoạt workflow khi push `main` (mặc định `docs/prd/**`) |
| `--openapi` | OpenAPI đã commit. **Bỏ thì CI không có OpenAPI** (mất chiều kỹ thuật và mã trạng thái của bộ chấm) |
| `--sut-dockerfile`, `--sut-context` | chỉ cần khi Dockerfile không nằm ở gốc, `*/Dockerfile` hay `docker/*Dockerfile*` (monorepo `apps/<tên>/…` thì bắt buộc) |

Tuỳ chọn khác: `--qc-ref` (ghim workflow), `--slug`, `--sut-port`, `--health-path`, `--sut-env KEY=VALUE` khi scanner đoán sai.

`init` sinh: `.github/workflows/qc-groundtruth.yml` (PRD → test case → PR), `.github/workflows/qc.yml` (gate cho PR của dev, xem [onboarding.md](onboarding.md)), vùng quản lý trong `.github/CODEOWNERS` (`/.qc-agent/`, `/.github/CODEOWNERS`, `/.github/workflows/qc-*.yml` thuộc team QA), và `.qc-agent/suites/`. Thư mục `.qc-agent/ground-truth/` do **bot tạo ở lần chạy đầu** (5.1).

Sau `init`:
1. Đọc mục "Còn việc cho người (qc-agent:todo)": với Sprint 1 cần `image` đã điền và `--qa-team` đúng.
2. Kiểm: `docker run --rm -v "$PWD:/sut" <IMAGE> validate --sut-root /sut`.
3. Commit trên nhánh mới, mở PR vào chính repo, nhờ QA duyệt (vì đụng CODEOWNERS), **merge vào `main`**. Workflow chỉ có tác dụng khi đã nằm trên nhánh mặc định. PR onboarding có thể đỏ lúc đầu vì `validate` chặn mọi `qc-agent:todo` còn sót; đó là chủ ý.

---

## 4. Cấu hình một lần trên GitHub

### 4.1. Secret

**Settings → Secrets and variables → Actions → New repository secret**: `ANTHROPIC_API_KEY` (bắt buộc), `qc_bot_token` (nên có), và `QC_TEST_USERNAME`, `QC_TEST_PASSWORD` (chỉ khi API có đăng nhập, mục 2.4). Hoặc `gh secret set ANTHROPIC_API_KEY --repo my-org/my-sut`.

- Dùng khoá **riêng** và đặt **spend limit** trong Anthropic Console: đó là lớp bảo vệ cuối cùng.
- Đặt secret **sau cùng**, ngay trước lần chạy đầu (5.1). Thiếu secret thì job `generate` dừng với thông báo rõ, **trước khi gửi gì ra ngoài**.
- File `qc-groundtruth.yml` do `init` sinh đã dùng Claude, không cần sửa. Vài dòng ví dụ Gemini đang bị comment: bỏ qua, đừng bỏ comment.

### 4.2. Cho phép bot tạo PR

**Settings → Actions → General → Workflow permissions** → tích **"Allow GitHub Actions to create and approve pull requests"**. Repo thuộc tổ chức thì bật ở cấp tổ chức trước. Nếu đang dùng *Allow select actions*, thêm `actions/checkout`, `actions/upload-artifact`, `docker/login-action` và `Muteen-Felix/QC-Agent/.github/workflows/*`.

### 4.3. Branch protection cho `main`

Check `validate` **chỉ chọn được sau khi đã chạy ít nhất một lần**, nên làm bước này sau PR Ground-Truth đầu tiên (5.1).

**Settings → Branches → Add branch protection rule** cho `main`:
- ✅ Require a pull request before merging → Require approvals ≥ 1 → ✅ **Require review from Code Owners**
- ✅ Require status checks → chọn **`groundtruth / qc-groundtruth / gt validate (<project>)`** (và `qc-agent / <project>` của gate dev)
- ✅ Do not allow bypassing / Include administrators (nếu muốn cả admin qua duyệt); chặn force-push

Cách nhanh (cần token **admin**): `GITHUB_TOKEN=<token> python tools/protect_ground_truth.py --repo OWNER/SUT --dry-run` để xem trước, bỏ `--dry-run` để ghi.

| Lớp | Chặn gì |
|---|---|
| CODEOWNERS + Require review from Code Owners | **merge** PR đụng `.qc-agent/**` khi thiếu duyệt của QA |
| Require a pull request | **push thẳng** vào `main` |
| Required check `gt validate` | merge khi test case chưa duyệt xong (còn `draft`, lệch Excel/YAML) |

Không lớp nào ngăn người khác *mở* PR sửa `.qc-agent/**`; chúng chỉ làm PR đó không merge được khi thiếu QA.

**Kiểm tay một lần** (cần hai tài khoản: một QA, một không phải QA):
1. Tài khoản không phải QA push thẳng lên `main` một thay đổi trong `.qc-agent/`: phải bị từ chối.
2. Cũng tài khoản đó mở PR sửa `.qc-agent/ground-truth/test-cases.yaml`: nút merge phải báo cần review của code owner.
3. Tài khoản QA approve: merge được.

---

## 5. Vận hành hằng ngày

### 5.1. Sửa PRD, bot mở PR

BA/Dev sửa PRD dưới `docs/prd/`, mở PR, merge như thường. Khi push lên `main` đổi file khớp `--prd-glob`, workflow `qc-groundtruth` chạy: chọn các PRD vừa đổi (tối đa 5), mỗi PRD một lượt `gt generate` (chưa có `test-cases.yaml`) hoặc `gt regen` (đã có, giữ nguyên TC QA đã quyết), commit **chỉ** `.qc-agent/` lên nhánh `qc-agent/gt/<prd-id>`, và mở PR **"Ground-Truth: `<prd-id>`"**. Thân PR có số story/AC/TC, AC mồ côi, bảng coverage và checklist cho QA. Artifact của run chứa `egress.jsonl` (nhật ký dữ liệu gửi ra LLM) và `summary.json` (số token).

**Lần đầu** (PRD chưa "vừa đổi" nên workflow không tự chạy): **Actions → qc-groundtruth → Run workflow**, nhập `prd_path` (vd. `docs/prd/orders.md`).

| Bẫy | Cách xử lý |
|---|---|
| PR tạo bằng `GITHUB_TOKEN` **không kích hoạt** `pull_request`, nên chưa có check `gt validate` | Không sao: QA push là check chạy. Muốn có ngay: đặt `qc_bot_token`. PR mở bằng PAT của một người thì người đó không tự approve được |
| Bot push đúng lúc QA đang push | Push của bot bị từ chối, job đỏ: bấm *Re-run* |
| Một push đổi quá 5 PRD | Job `select` lỗi: dùng *Run workflow* với đường dẫn cụ thể |

### 5.2. QA duyệt test case

QA làm **trên nhánh bot** (push thẳng lên đó, PR tự cập nhật): `git fetch origin && git checkout qc-agent/gt/<prd-id>`. Trong `.qc-agent/ground-truth/`:

| File | QA làm gì |
|---|---|
| `test-cases.yaml` | **nguồn sự thật** của gate: duyệt, sửa, thêm |
| `test-cases.xlsx` | bản Excel cùng nội dung: duyệt, sửa, thêm rồi `gt import-xlsx` (5.4) |
| `module-map.yaml` | điền `paths`, đổi `status: approved` |
| `tests_gt/` | **KHÔNG sửa tay**: sửa một dòng là `gt validate` báo *drift* |
| `openapi.snapshot.json` | không sửa (máy sở hữu) |

Chọn **một** trong hai đường (YAML hoặc Excel), đừng sửa song song.

```
LLM đề xuất ──► draft ──► approved   (gate chạy; fail thì chặn merge)
                  └────► rejected + rejected_reason   (regen không đề xuất lại TC này)
```

**Đường A: YAML.** Mỗi TC trong `test_cases:`:

```yaml
- tc_id: TC-AC-1.2-3f9a1c            # do máy đặt: ĐỪNG sửa
  title: quantity bằng 0 bị từ chối
  ac_refs: [AC-1.2]
  kind: api_functional               # api_contract | api_functional (1 bước) | flow (từ 2 bước)
  status: draft                      # ◄── BẠN ĐỔI DÒNG NÀY
  origin: llm                        # llm = máy đề xuất; qa = QA viết (regen không bao giờ đè)
  steps:
  - request: {method: POST, path: /orders, json: {sku: A1, quantity: 0}}
    expect: {status: [422], json: [{path: $.detail, op: exists}]}
```

Đối chiếu từng TC với **PRD** (không phải với code):

| Tình huống | Làm gì |
|---|---|
| Đúng PRD | `status: approved` |
| Sai, hoặc đoán điều PRD không nêu | `status: rejected` + `rejected_reason: "..."` |
| Đúng PRD nhưng đỏ trên SUT sạch | **bug thật của SUT**: giữ `approved`, báo dev |
| Không hiểu vì sao nó đúng | **đừng duyệt** |

Ghi chú của QA đặt trong trường `notes` (comment YAML không sống qua `regen`).

**Thêm edge case** (`origin: qa`): thêm một mục vào `test_cases` với `tc_id` dạng `TC-<ac_id>-<mô-tả>`, `status: approved`, `origin: qa`. Với `flow` (nhiều bước), bước sau dùng `{{tên}}` cho giá trị đã `capture` ở bước trước. Assertion hợp lệ: `eq`, `ne`, `exists`, `absent`, `type`, `len_eq`, `len_gte`, `contains`. **AC giao diện** khai trong `uncovered_acs` kèm lý do.

**Hoàn tất:**
1. `module-map.yaml`: điền `paths` thật (glob tới file khai báo route của từng module), xoá dòng `qc-agent:todo`, đổi `status: approved`.
2. `test-cases.yaml`: đổi `status:` ngoài cùng (cạnh `stories:`) thành `approved`; khi đó mọi TC phải là `approved` hoặc `rejected`.
3. Có xlsx đi kèm thì chạy `gt export-xlsx` để xlsx khớp YAML.

**Đường B: Excel.** Mở `test-cases.xlsx` bằng Excel hoặc LibreOffice (Google Sheets có thể làm mất sheet ẩn và danh sách chọn). Sheet `TestCases`: đổi `status`, điền `rejected_reason`/`notes`, thêm dòng mới; `Steps`/`Assertions`: sửa bước và assertion; `Coverage`: xem còn thiếu gì. Tiêu đề **vàng** = sửa được, **xám** = chỉ đọc; **không xoá sheet ẩn `_meta`**.
- Chỉ **duyệt** (`status`, `rejected_reason`, `notes`): TC giữ `origin: llm`. **Sửa nội dung** TC do LLM sinh: được, nhưng TC thành `origin: qa` để `regen` không đè.
- **TC mới**: thêm dòng `TestCases` với `tc_id = NEW-<tên>`, rồi thêm dòng `Steps`/`Assertions` cùng `tc_id`. TC do LLM sinh không xoá được (hãy `rejected`). Ô công thức (bắt đầu bằng `=`) là lỗi.
- Sửa xong **phải đồng bộ về YAML** (5.4), vì gate đọc YAML.

### 5.3. Điều kiện để `gt validate` xanh

`gt validate` là cổng kiểm **offline, tất định, không LLM**. Chạy thử trước khi push:

```bash
docker run --rm -v "$PWD:/sut" <IMAGE> gt validate --sut-root /sut     # exit 0 sạch · 1 còn việc cho người · 3 file hỏng/sai schema
```

Exit 1 là bình thường cho tới khi QA làm xong. Lỗi thường gặp:

| Báo | Cách xử lý |
|---|---|
| `N test case còn draft` / `catalog còn status: draft` | đổi sang `approved` hoặc `rejected`; đổi `status` ngoài cùng thành `approved` |
| `test case rejected thiếu rejected_reason` | điền `rejected_reason` |
| `module-map còn status: draft` / `còn dấu qc-agent:todo` | điền `paths`, xoá TODO, `status: approved` |
| `khác bản render lại … (drift)` | đừng sửa `tests_gt/`: hoàn nguyên file, sửa `test-cases.yaml`, hoặc `gt regen` |
| xlsx lệch YAML | `gt import-xlsx` (5.4) |
| `coverage ac 24/25 … dưới ngưỡng 100%` | thêm TC cho AC đó, hoặc khai `uncovered_acs` kèm lý do |
| `coverage api 9/12 … còn thiếu: DELETE /orders/{id} 422` | thêm TC, hoặc `waivers` có lý do |

**Bộ chấm coverage** (chỉ có khi có `openapi.snapshot.json`): mặc định đòi **100%** ở ba chiều (AC, kỹ thuật, API), chấm **chỉ TC `approved`**. Catalog còn `draft` thì thiếu coverage chỉ là cảnh báo; catalog `approved` thì là **lỗi**. Đóng gap bằng TC mới (ưu tiên), hoặc miễn có lý do (`target` phải giống từng ký tự id gap mà validate in ra):

```yaml
waivers:
- kind: api                                        # api | technique
  target: "DELETE /orders/{order_id} 422"
  reason_code: not_applicable                      # not_http_reachable | needs_infra_fault | not_applicable | out_of_scope
  reason: order_id là chuỗi tự do nên không có đầu vào nào gây 422
  status: approved                                 # waiver chỉ có hiệu lực khi QA đặt approved
```

Ngưỡng nằm ở `.qc-agent/ground-truth/coverage-policy.yaml` (QA khoá bằng CODEOWNERS). Chi tiết: [groundtruth.md §5b](groundtruth.md).

Xanh rồi: `git add .qc-agent && git commit -m "qa: duyệt test case" && git push`. Check `gt validate` trên PR xanh → QA (code owner) **Approve** → **Merge**.

### 5.4. Đồng bộ Excel với YAML

Chỉ cần khi QA làm việc trên Excel. Đóng file Excel trước khi chạy (`qc` = `docker run --rm -v "$PWD:/sut" <IMAGE>`):

```bash
qc gt import-xlsx --sut-root /sut --dry-run     # 1. xem sẽ đổi gì, chưa ghi
qc gt import-xlsx --sut-root /sut               # 2. ghi vào YAML, cấp tc_id thật cho TC mới, xuất lại xlsx
qc gt validate    --sut-root /sut               # 3. phải xanh
git add .qc-agent && git commit -m "qa: duyệt test case (import từ Excel)" && git push
```

- **Commit cả `test-cases.yaml` lẫn `test-cases.xlsx`.** Chỉ commit xlsx thì gate không thấy quyết định của bạn.
- Gộp **ba chiều**: hai bên cùng sửa một trường theo hai cách khác nhau là *xung đột*: lệnh thoát 1, **không ghi gì**, in `tc_id.trường`; sửa tay một bên rồi chạy lại.
- Lỡ sửa YAML tay mà xlsx cũ: chạy `gt export-xlsx` (từ chối ghi đè nếu xlsx có sửa chưa import; `--force` chỉ khi chắc chắn bỏ các sửa đó).

### 5.5. Sau khi merge

Suite `gt-functional` nằm trong `.qc-agent/suites/`; mọi PR của dev chạy các TC `approved`, đỏ thì chặn merge (khi đã bật required check ở 4.3). Không có TC `approved` nào thì suite **fail**. TC `approved` mà AC đã bị xoá khỏi PRD thì gate báo lỗi tới khi QA sửa `ac_refs` hoặc `rejected`.

**PRD đổi lần sau:** bot chạy `gt regen` trên nhánh bot. **Giữ nguyên văn** mọi TC `approved`, `rejected`, `origin: qa`; thay các TC `draft` do LLM; TC mới luôn là `draft` → QA duyệt phần mới rồi đi lại 5.3. Mỗi PRD là một nhánh và PR riêng.

---

## 6. Giới hạn

**Phạm vi.** Tài liệu này chỉ gồm **Ground-Truth** (PRD → test case → QA duyệt → `gt validate` → gate). Bước chọn phạm vi theo diff, mức nghiêm trọng, PR review và Jira nằm ở gate chung: xem [usage-ci.md](usage-ci.md) và [onboarding.md](onboarding.md). AC giao diện **không sinh test** (khai `uncovered_acs`).

**Quyền riêng tư và chi phí.**
- **PRD và danh sách endpoint OpenAPI được gửi tới Anthropic.** Xác nhận với bảo mật/pháp chế trước khi chạy với PRD thật. Mọi lời gọi LLM ghi `egress.jsonl` **trước khi gửi**; log không chứa nội dung PRD, prompt hay phản hồi.
- Chế độ mặc định là **một lời gọi LLM** mỗi lần sinh (tối đa 16.000 token ra), cộng tối đa một lần sửa khi sai định dạng. Số token nằm trong `summary.json`.

**Chế độ agent** (`agent: true`, **tắt mặc định**): LLM đọc cả **mã nguồn** nhiều lượt để sinh test sát hơn. **Mã nguồn bị gửi tới Anthropic**: chỉ bật khi được phép, và chạy `gitleaks` trước. Chỉ dùng với Claude. Đã chạy thật một lượt trên một SUT (kết quả và giới hạn: `eval/vahan/EVIDENCE.md`); đo có kiểm soát chi phí trên CI: [measure-sprint-1-on-sut.md](measure-sprint-1-on-sut.md); chi tiết: [groundtruth.md §5c](groundtruth.md).

| Giới hạn | Ghi chú |
|---|---|
| Chỉ kiểm thử **HTTP/JSON API** | không UI, hiệu năng, bảo mật trong luồng Ground-Truth |
| Auth chỉ hỗ trợ **đăng nhập một endpoint lấy Bearer token** | xem mục 2.4. Token cố định riêng, OAuth, cookie phiên, nhiều vai trò chưa hỗ trợ: AC liên quan sẽ không kiểm được hoặc QA thêm header tĩnh (file được commit: **đừng để khoá thật**) |
| Chất lượng test phụ thuộc chất lượng PRD | AC mơ hồ thì LLM đoán, QA phải loại; LLM chỉ **đề xuất**, **đừng duyệt TC bạn không hiểu vì sao đúng** |
| PRD lớn | lần thử một lời gọi với PRD 98 AC bị lỗi (nghi do vượt 16.000 token đầu ra, **chưa xác minh**): nên chia PRD nhỏ theo story |
| Tối đa 5 PRD/lần push, mỗi PRD ≤ 256 KB | chia nhỏ hoặc dùng *Run workflow* |
| Không sửa tay `tests_gt/` | sinh máy, sửa là *drift*; đổi image sang bản có mẫu khác cũng gây drift: chạy `gt regen` rồi commit |
| PR từ **fork** không có secret | `generate` chỉ chạy trên push/`workflow_dispatch` của chính repo; trên PR chỉ chạy `validate` |
| Image ghim theo **digest** | muốn nâng cấp thì đổi digest trong `qc-groundtruth.yml` |

---

## Phụ lục: Checklist onboarding

- [ ] PRD đúng quy chuẩn, `gt info` ra đúng số AC; `openapi.json` đã commit
- [ ] API có đăng nhập: `auth.yaml` đã commit (2.4) và hai secret `QC_TEST_USERNAME`, `QC_TEST_PASSWORD` đã đặt
- [ ] Có digest image, team QA (quyền Write), API key LLM
- [ ] `init` (đã thử `--dry-run`) → PR onboarding → merge vào `main`
- [ ] Secret `ANTHROPIC_API_KEY` (đặt sau cùng, đã có spend limit); bật *Allow GitHub Actions to create and approve pull requests*
- [ ] *Run workflow* với `prd_path` → có PR "Ground-Truth: `<prd-id>`"
- [ ] QA duyệt (YAML hoặc Excel + `gt import-xlsx`), điền module-map, catalog `approved`; `gt validate` exit 0
- [ ] Branch protection `main` + kiểm tay 3 bước ở 4.3
- [ ] QA approve và merge PR Ground-Truth đầu tiên → gate dev bắt đầu chạy TC đã duyệt

Tham khảo sâu: [groundtruth.md](groundtruth.md) (luồng, bộ chấm coverage, Excel), [groundtruth-real-sut.md](groundtruth-real-sut.md) (chạy trên SUT thật), [onboarding.md](onboarding.md) (Quality Gate chung).
