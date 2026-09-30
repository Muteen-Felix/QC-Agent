# Ground-Truth: từ PRD tới test case được QA duyệt

Bài toán: BA viết PRD, nhưng nếu để LLM tự viết *và* tự chấm test thì gate xanh không còn nghĩa gì. Ground-Truth tách hai việc: **LLM chỉ đề xuất test case (dạng dữ liệu)**, **QA duyệt**, còn gate chỉ chạy những test case đã được người duyệt. Tài liệu này là hướng dẫn vận hành; thiết kế nằm ở [architecture.md](architecture.md) §5.

## 1. Luồng

```
BA push PRD lên main
   │  (workflow qc-groundtruth: job select → generate)
   ▼
gt generate | gt regen ──► nhánh qc-agent/gt/<prd-id> ──► PR "Ground-Truth: <prd-id>"
   (LLM đề xuất TC, code render tất định)                      │
                                                               ▼
                      QA sửa test-cases.yaml: draft → approved, thêm edge case (origin: qa)
                                                               │  push commit lên PR
                                                               ▼
                                       job validate: `gt validate` xanh  ──►  QA (code owner) duyệt  ──►  merge
                                                                                                          │
                                                          từ đây gate PR chạy suite gt-functional: chỉ TC approved ◄┘
```

| Bước | Ai/cái gì | Kết quả |
|---|---|---|
| 1 | BA đổi PRD dưới `docs/prd/**` (glob chỉnh được bằng `init --prd-glob`) | workflow `qc-groundtruth` chạy trên `push` vào `main` |
| 2 | LLM (`groundtruth/generate.py`) | đề xuất test case (JSON theo schema, không bao giờ là code); code tính `tc_id`, sắp xếp, trạng thái `draft` |
| 3 | `render.py` (tất định) | `test-cases.yaml`, `tests_gt/`, suite `gt-functional`, `module-map.yaml` nháp |
| 4 | Bot | commit vào `qc-agent/gt/<prd-id>`, mở PR (hoặc comment nếu PR đã có) |
| 5 | QA | duyệt/loại từng TC, thêm edge case, điền `module-map.yaml` |
| 6 | CI `gt validate` | exit 1 cho tới khi hết `draft` và hết drift |
| 7 | Code owner | duyệt PR, merge |

Hai vòng khác nhau: vòng ngoài (đây) có LLM, sản phẩm là **file đi qua PR**; vòng trong (gate) **không LLM**, chặn merge của dev. Điểm nối duy nhất là `.qc-agent/**` được khoá bằng CODEOWNERS và branch protection.

## 2. Lệnh

Chạy từ gốc repo SUT (hoặc `docker run --rm -v "$PWD:/sut" <image> gt …`). Cần `ANTHROPIC_API_KEY`.

```bash
qc-agent gt generate --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json   # lần đầu
qc-agent gt regen    --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json   # PRD đổi: merge theo tc_id
qc-agent gt validate --sut-root .                                                      # cổng HITL, offline
qc-agent gt info     --prd docs/prd/noteboard.md                                       # prd_id, sha256, số story/AC (offline, không LLM)
```

- `generate` từ chối nếu đã có `test-cases.yaml` (dùng `regen`; `--force` sẽ **mất** các TC đã duyệt).
- `--egress-dir` (mặc định `$QC_RUNS_DIR/gt`) nhận `egress.jsonl`; **không** được nằm dưới `.qc-agent/`. `--summary-json` ghi số liệu cho workflow và cache.
- `regen` **giữ nguyên văn** mọi TC `approved`, `rejected`, `origin: qa`; thay các TC `draft` do LLM; thêm TC mới với `status: draft`. Comment YAML của QA trong `test-cases.yaml` không sống qua `regen`: ghi chú vào trường `notes` của TC.
- `validate`: exit **1** còn việc cho người · exit **3** file hỏng/sai schema · exit **0** sạch (có thể kèm cảnh báo).

## 3. Vòng đời của một test case

```
                 ┌────────────── QA đổi ──────────────┐
   LLM đề xuất   ▼                                     │
   ───────────► draft ──► approved ──► (gate chạy, chặn merge nếu fail)
                   │
                   └────► rejected + rejected_reason  (regen KHÔNG đề xuất lại đúng TC này: tc_id băm theo nội dung)
```

| `status` | Ý nghĩa | Gate chạy? | `gt validate` |
|---|---|---|---|
| `draft` | LLM đề xuất, chưa ai duyệt | không | **lỗi** |
| `approved` | QA đã duyệt | **có**, chặn merge nếu fail | ok |
| `rejected` | loại; **bắt buộc** `rejected_reason` | không | lỗi nếu thiếu lý do |

Catalog cũng có `status` ngoài cùng: đổi thành `approved` khi đã xong (lúc đó mọi TC phải là `approved` hoặc `rejected`). Gate chỉ chạy TC `approved`; nếu chưa có TC nào thì suite **fail** (`pytest.tests >= 1`): gate rỗng không được xanh.

## 4. Checklist cho QA (mở PR là có bản này trong thân PR)

- [ ] Đọc từng TC `draft`: đúng theo **PRD** chưa? Đổi `approved`, hoặc `rejected` kèm lý do. Đừng duyệt TC mà bạn không hiểu vì sao nó đúng.
- [ ] Assertion chỉ nên kiểm điều PRD nói. TC đoán giá trị/thông điệp lỗi mà PRD không nêu thì `rejected`.
- [ ] Mỗi TC tự đủ: tự tạo dữ liệu nó cần (bước `POST` rồi `capture`), không dựa thứ tự chạy hay dữ liệu có sẵn.
- [ ] **AC mồ côi** (không có TC, không nằm trong `uncovered_acs`): thêm TC hoặc ghi lý do vào `uncovered_acs`.
- [ ] `uncovered_acs`: các AC không kiểm được bằng HTTP (giao diện…): đúng thật chưa?
- [ ] Thêm **edge case** còn thiếu (mục 5).
- [ ] `module-map.yaml`: điền `paths` (glob tới file khai báo route của từng module), xoá `qc-agent:todo`, đổi `status: approved`.
- [ ] Đổi `status` của catalog thành `approved`, push, chờ check `gt validate` xanh.

Đừng sửa tay `tests_gt/`: chúng là mã sinh máy, render lại từ `test-cases.yaml`. `gt validate` render lại trong bộ nhớ rồi so, nên sửa tay một dòng là **drift** (exit 1).

## 5. Thêm edge case (`origin: qa`)

Thêm một mục vào `test_cases` của `test-cases.yaml`. Không cần render lại: runtime đọc thẳng file này.

```yaml
- tc_id: TC-AC-1.5-qa-title-only-spaces          # TC-<ac_id>-<mô-tả>; không trùng tc_id khác
  title: title chỉ toàn dấu cách bị từ chối
  ac_refs: [AC-1.5]                              # AC đầu tiên quyết định TC thuộc story nào; phải có trong `stories`
  kind: api_functional                           # api_contract (1 bước, chỉ method+path) | api_functional (1 bước) | flow (từ 2 bước)
  status: approved
  origin: qa
  steps:
    - request: {method: POST, path: /notes, json: {title: "   ", body: x}}
      expect: {status: [422], json: [{path: $.detail, op: exists}]}
```

- `flow`: bước sau dùng `{{tên}}` (trong `path_params`, `query`, `json`) cho giá trị đã `capture` ở bước trước: `capture: {note_id: $.id}` rồi `path_params: {note_id: "{{note_id}}"}`.
- Assertion là bộ đóng: `eq`, `ne`, `exists`, `absent`, `type`, `len_eq`, `len_gte`, `contains`; `path` là tập con JSONPath (`$`, `.khoá`, `[n]`). `ne`, `contains`, `len_*`, `type` yêu cầu path tồn tại.
- Chạy thử cục bộ với SUT đang chạy: `APP_BASE_URL=http://127.0.0.1:8000 python -m pytest .qc-agent/ground-truth/tests_gt`.

## 6. Cài đặt cho một repo SUT

```bash
docker run --rm -v "$PWD:/sut" <image> init --qa-team @org/qa-team --prd-glob 'docs/prd/**'
```

`init` sinh, ngoài các suite gate: `.github/workflows/qc-groundtruth.yml` (gọi workflow tái sử dụng), và **vùng CODEOWNERS** do qc-agent quản lý:

```
# qc-agent:begin codeowners
/.qc-agent/ @org/qa-team
/.github/CODEOWNERS @org/qa-team
/.github/workflows/qc-*.yml @org/qa-team
# qc-agent:end
```

Chạy lại `init` chỉ cập nhật vùng giữa hai dấu; dòng ngoài vùng giữ nguyên từng byte. Quy tắc **cuối cùng** khớp đường dẫn thắng, nên vùng này nằm cuối file. Thiếu `--qa-team` thì owner là giữ chỗ kèm `qc-agent:todo` và `qc-agent validate` từ chối. Có `.qc-agent/ground-truth/` mà CODEOWNERS không giao `/.qc-agent/` cho ai cũng là lỗi của `qc-agent validate`.

Secret ở repo SUT: `ANTHROPIC_API_KEY` (bắt buộc cho việc sinh), `qc_bot_token` (tuỳ chọn, xem bẫy 1). Bật **Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"**.

### Khoá nhánh: `tools/protect_ground_truth.py`

```bash
GITHUB_TOKEN=<token admin repo> python tools/protect_ground_truth.py --repo OWNER/REPO --dry-run   # xem payload
GITHUB_TOKEN=<token admin repo> python tools/protect_ground_truth.py --repo OWNER/REPO             # áp dụng
```

Nó `GET` bảo vệ hiện có rồi **gộp** (không ghi đè `required_status_checks`, `enforce_admins`, `restrictions`…), bật `require_code_owner_reviews`, đặt `required_approving_review_count ≥ 1` (không bao giờ hạ số cũ), rồi `PUT`. Token đọc từ `GITHUB_TOKEN`, không bao giờ được in ra. **Chưa chạy trên repo thật: chỉ làm khi có xác nhận** (dự kiến S4-06).

Nói đúng cơ chế, không hơn:

| Lớp | Chặn cái gì |
|---|---|
| CODEOWNERS + `require_code_owner_reviews` | **merge** một PR có đụng `.qc-agent/**` khi thiếu duyệt của team QA |
| `required_pull_request_reviews` (không null) | **push thẳng** vào `main` (mọi thay đổi phải qua PR) |
| `enforce_admins` (không đổi bởi script) | admin có bị ép hay không; bật nếu muốn cả admin phải qua duyệt |

Không có lớp nào ngăn người khác *mở* PR sửa `.qc-agent/**`; chúng chỉ làm PR đó không merge được khi thiếu QA.

**Kiểm tay một lần trên repo thật** (chưa làm; đi cùng E2E ở S4-06): (1) tài khoản **không** thuộc team QA push thẳng lên `main` thay đổi trong `.qc-agent/`: phải bị từ chối; (2) cũng tài khoản đó mở PR sửa `.qc-agent/ground-truth/test-cases.yaml`: nút merge phải báo cần review của code owner; (3) tài khoản QA duyệt: merge được. Đổi `@org/team` thành team không tồn tại thì GitHub báo "Unknown owner" trong tab CODEOWNERS: kiểm tra bước này trước.

## 7. Bẫy

1. **PR/commit tạo bằng `GITHUB_TOKEN` không kích hoạt workflow `pull_request`.** Job `validate` chỉ chạy khi có commit mới trên PR (QA sửa draft rồi push). Điều này vẫn đúng quy trình vì QA đằng nào cũng phải sửa. Muốn check chạy ngay khi PR mở, truyền secret `qc_bot_token` (GitHub App hoặc PAT).
2. **PR từ fork không có secret.** Job `generate` chỉ chạy trên `push` và `workflow_dispatch` của chính repo; trên `pull_request` chỉ chạy `validate` (quyền chỉ-đọc, không mạng, không secret).
3. **Nâng image/mẫu thì `gt validate` báo drift.** `tests_gt/` sinh từ mẫu của image lúc chạy `generate`/`regen`. Đổi image sang phiên bản có mẫu khác thì chạy `gt regen` rồi commit.
4. **Xoá AC khỏi PRD.** `regen` không sửa TC `approved` trỏ tới AC đó; `gt validate` chỉ **cảnh báo**, nhưng gate `pytest` thoát mã 4 (`error`, đỏ) cho tới khi QA sửa `ac_refs` hoặc chuyển `rejected`. Đây là chủ ý: đỏ để QA biết PRD đã bỏ một tính năng.
5. **Đẩy chồng, không đè.** Nhánh bot đã có thì bản sinh mới được xếp lên trên (commit thường, không force-push), nên sửa của QA trên nhánh đó không mất. Nếu QA đẩy lên nhánh đúng lúc workflow chạy, push của bot bị từ chối và job đỏ: chạy lại.
6. **Đa PRD.** Một lần push chỉ xử lý tối đa 5 PRD vừa đổi khớp glob, mỗi PRD một nhánh/PR (`prd-id` lấy từ front matter `id:` hoặc tên file, chuẩn hoá `[a-z0-9-]`).
7. **Trạng thái `draft` trong PR không chặn gate của dev**: gate chỉ chạy TC `approved`, còn merge PR Ground-Truth bị chặn bởi `gt validate` (nếu bạn đặt job `qc-groundtruth / gt validate` làm check bắt buộc trong branch protection).

## 8. Quyền riêng tư và chi phí

- **PRD (và danh sách endpoint OpenAPI) được gửi tới nhà cung cấp LLM** (Anthropic API). Mọi lời gọi ghi `egress.jsonl` (loại dữ liệu `prd_text`, `api_spec`, host đích) **trước khi gửi**; chính sách `deny` thì không có request nào.
- `egress.jsonl` và `summary.json` là **artifact của workflow** (giữ 14 ngày), không bao giờ được commit. Không file nào ghi nội dung PRD, prompt hay response vào log.
- **Câu hỏi #3 ở [architecture.md](architecture.md) §5.5 chưa được chốt** ("có được gửi PRD ra LLM bên ngoài không"): nó chặn mọi lượt chạy LLM thật. Cho tới khi có câu trả lời, chỉ chạy bằng PRD mẫu/PRD không nhạy cảm.
- Chi phí một lần sinh: một lời gọi (tối đa 16 000 token ra), cộng tối đa một lần sửa khi đầu ra sai schema. Số token nằm trong `summary.json`.

## 9. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `gt validate` exit 1: "khác bản render lại (drift)" | ai đó sửa tay `tests_gt/` hoặc image đổi mẫu | sửa `test-cases.yaml` thay vì `.py`; hoặc `gt regen` |
| Gate `error` (exit 4) với thông báo "TC approved … không thuộc story nào" | TC `approved` có `ac_refs[0]` không có trong `stories` (gõ nhầm hoặc PRD đã bỏ AC) | sửa `ac_refs` hoặc chuyển `rejected` |
| Gate `fail` vì `pytest.tests >= 1` | chưa có TC nào `approved` | QA duyệt ít nhất một TC |
| Workflow "thiếu secret ANTHROPIC_API_KEY" | chưa đặt secret | đặt ở repo SUT; chưa có gì rời máy |
| PR không có check `gt validate` | bẫy 1 | push một commit lên PR, hoặc dùng `qc_bot_token` |
