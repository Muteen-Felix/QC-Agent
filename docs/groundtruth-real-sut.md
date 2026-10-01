# Ground-Truth trên một SUT thật với Gemini: hướng dẫn từng bước

Tài liệu này đi cùng [groundtruth.md](groundtruth.md) (luồng và checklist QA) và `tools/eval_gt_sut.py` (đo). Không dùng `noteboard` để đo nữa: mọi lệnh dưới đây chạy trên **SUT của bạn**, thay `<SUT>` bằng đường dẫn repo SUT.

## 0. Bài toán và bức tranh chung

**Bài toán thực tế.** Team có một API đang chạy thật và một PRD do BA viết. Câu hỏi để nghiệm thu Sprint 1: *"bộ test case do Gemini đề xuất, sau khi QA duyệt, có đủ tốt để làm cổng chặn merge không?"* Trả lời bằng số, không bằng cảm giác: (a) phủ bao nhiêu AC, (b) chạy xanh bao nhiêu trên SUT sạch, (c) bắt được bao nhiêu lỗi khi ta cố tình chèn lỗi vào SUT.

```
PRD (.md, có ID AC-x.y) ──► gt generate (Gemini) ──► test-cases.yaml (mọi TC = draft) + tests_gt/ (mã render tất định)
                                                          │
                                   QA đọc, draft → approved/rejected, thêm edge case (HITL) ──► gt validate xanh
                                                          │
   đo (a) AC coverage ─┐                                  ▼
   đo (b) TC xanh ─────┼── eval_gt_sut.py ◄── SUT sạch  +  SUT bị chèn lỗi (mutant M1…Mn)  ──►  đo (c) tỷ lệ bắt lỗi
```

Ba việc dễ nhầm:

- `gt generate` **đã gồm bước render** ra file test; không có lệnh render riêng. LLM chỉ trả dữ liệu (JSON), code render ra `tests_gt/` bằng mẫu cố định.
- Gate chỉ chạy TC `approved`. Bộ Gemini sinh ra *chưa được duyệt* thì (c) chưa có nghĩa: phải qua QA trước.
- Người viết mutant và gán nhãn `non_testable` **không nên là người viết prompt/PRD**, nếu không là tự chấm bài mình.

## 1. Trước khi bắt đầu

### 1.1. Điều kiện của SUT (kiểm trước, đỡ mất quota)

| Điều kiện | Vì sao |
|---|---|
| SUT là **HTTP/JSON API** | Test sinh ra là `pytest` + `httpx` gọi `APP_BASE_URL`; giao diện (UI) không kiểm được, phải khai `non_testable` |
| Chạy được bằng **một lệnh**, nhận cổng qua `{port}` hoặc biến `PORT` | `eval_gt_sut.py` tự bật/tắt SUT cho từng mutant |
| Endpoint đang kiểm **không đòi đăng nhập** ở môi trường test | Runtime của test **chưa hỗ trợ auth động**: `headers` trong catalog là chuỗi tĩnh (không nội suy `{{biến}}`) và không đọc token từ env. Cách tạm: bật chế độ test/tắt auth cho môi trường đo, hoặc để QA thêm header tĩnh của tài khoản test (nhớ: file này được commit, đừng để khoá thật). Đây là giới hạn của Sprint 1 |
| Dùng **DB dùng một lần** (SQLite tạm, container riêng) | Test tạo dữ liệu thật; TC được viết tự đủ nhưng dữ liệu vẫn tồn tại sau lượt chạy |
| Có **OpenAPI** (file hoặc URL) | Cho LLM danh sách endpoint, kiểm endpoint từng TC, sinh suite `api-contract` |

### 1.2. Cài đặt qc-agent và khoá Gemini

```bash
# trong repo qc-agent
pip install uv && uv sync            # tạo .venv có lệnh `qc-agent`
```

Đặt biến môi trường (PowerShell; Git Bash dùng `export`):

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

Mỗi request rời máy, kể cả lần retry, ghi **một dòng** vào `egress.jsonl` trước khi gửi. Ước lượng: một lần `gt generate` = 1–2 request; `--runs 3` = 3–6 request, mất tối thiểu ~1–2 phút khi giãn cách 12 s. Quota ngày của free tier nhỏ: đừng chạy vòng lặp `--runs 10` trừ khi cần.

> **Quyền riêng tư.** Bạn đã đồng ý gửi PRD ra Gemini để nghiên cứu/kiểm thử. Lưu ý điều khoản của Google: nội dung gửi qua **gói miễn phí** có thể được Google dùng để cải thiện sản phẩm. PRD thật của công ty nên chạy bằng khoá trả phí, hoặc chỉ dùng PRD đã lược thông tin nhạy cảm.

### 1.4. Smoke test một request (làm trước khi đo)

Code Gemini mới được kiểm bằng test giả lập, **chưa gọi API thật**. Một request thật nhỏ là cách rẻ nhất để phát hiện Google từ chối tham số nào đó:

```bash
qc-agent gt generate --prd <SUT>/docs/prd/smoke.md --sut-root <SUT-scratch> --egress-dir runs/gt-smoke
```

`smoke.md` là PRD 1 story, 2–3 AC (mục 2.1). Nếu lỗi, thông điệp chỉ nêu mã (`bad_request: HTTP 400 INVALID_ARGUMENT`…): thêm `QC_LOG_FORMAT=json` để xem event `llm.call`; báo lại mã lỗi cho dev qc-agent sửa `llm/client.py`, đừng sửa tay `tests_gt/`.

## 2. Bước 1: chuẩn bị PRD và nạp vào SUT

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
- Tối đa 256 KB. PRD là dữ liệu **không tin cậy** với qc-agent (được rào trong prompt), nhưng đừng nhét khoá/mật khẩu vào PRD.

Kiểm tra parse **offline**, không tốn quota:

```bash
qc-agent gt info --prd <SUT>/docs/prd/orders.md
# in JSON: prd_id, sha256, số story, số AC. Số AC lệch với PRD => sửa định dạng trước khi sinh.
```

### 2.2. Nạp OpenAPI và đặt SUT vào đúng chỗ

```bash
# SUT đang chạy ở máy bạn: lấy OpenAPI về file để commit/đối chiếu
curl -s http://127.0.0.1:8000/openapi.json -o <SUT>/openapi.json
```

`--sut-root` là thư mục gốc repo SUT: qc-agent ghi vào `<SUT>/.qc-agent/`. Repo nên sạch (`git status` không có thay đổi dở) để xem diff dễ.

### 2.3. Tích hợp CI (tuỳ chọn, không cần cho việc đo)

Để gate chạy ở PR của SUT và khoá `.qc-agent/**` bằng CODEOWNERS: `init` một lệnh (xem [onboarding.md](onboarding.md)):

```bash
qc-agent init --sut-root <SUT> --qa-team @org/qa-team --prd-glob 'docs/prd/**' --openapi <SUT>/openapi.json
```

Workflow tái sử dụng chọn khoá theo model: bỏ comment `model: gemini-3.6-flash` (cùng `llm_min_interval_s`, `llm_max_retries`, `llm_fallback_models`) trong `.github/workflows/qc-groundtruth.yml` và đặt secret `GEMINI_API_KEY` ở repo SUT (chi tiết: [groundtruth.md](groundtruth.md) §6). Điều kiện: `image` ghim digest và `qc_ref` phải lấy từ bản `main` đã có provider Gemini. Chưa cấu hình CI thì chạy `gt generate` ở máy dev như dưới đây và commit kết quả vào PR.

## 3. Bước 2: sinh test case, render và QA duyệt (HITL)

### 3.1. Sinh và render

```bash
qc-agent gt generate --prd <SUT>/docs/prd/orders.md --sut-root <SUT> --openapi <SUT>/openapi.json --summary-json runs/gt/summary.json
```

Kết quả (đều trong `<SUT>/.qc-agent/`):

| File | Là gì |
|---|---|
| `ground-truth/test-cases.yaml` | **thứ QA duyệt**: story/AC, mọi TC `status: draft`, `uncovered_acs` (AC LLM cho là không kiểm được bằng HTTP) |
| `ground-truth/tests_gt/` | mã pytest sinh máy từ catalog; **đừng sửa tay** (`gt validate` sẽ báo drift) |
| `ground-truth/module-map.yaml` | nháp ánh xạ module → file mã nguồn (dùng ở S2) |
| `suites/gt-functional.yaml` | suite cho gate: chỉ chạy TC `approved` |

`summary.json` có số story/AC/TC, AC mồ côi, `usage` (token), `model` đã trả lời. Đã có `test-cases.yaml` thì lệnh từ chối: dùng `regen` (mục 3.4), `--force` sẽ **xoá** TC đã duyệt.

### 3.2. QA duyệt (checklist rút gọn, bản đầy đủ ở [groundtruth.md](groundtruth.md) §4)

Runtime chỉ chạy TC `approved`, nên để biết TC nào sai sẵn: đổi thử một nhóm TC sang `approved` (không cần render lại) rồi chạy với SUT sạch đang bật:

```bash
APP_BASE_URL=http://127.0.0.1:8000 python -m pytest <SUT>/.qc-agent/ground-truth/tests_gt -q
```

Mở `test-cases.yaml` và với **từng** TC:

1. Đối chiếu PRD: assertion có đúng điều PRD nói không? TC đoán thông điệp lỗi/giá trị mà PRD không nêu → `status: rejected` + `rejected_reason: "PRD không nêu"`.
2. TC đúng → `status: approved`.
3. TC đỏ trên SUT sạch: hoặc **LLM đoán sai** (reject), hoặc **SUT lệch PRD** (đó là bug thật: báo dev, giữ TC `approved`).
4. AC mồ côi: thêm TC hoặc ghi vào `uncovered_acs` kèm lý do.
5. Thêm edge case còn thiếu với `origin: qa` (mẫu ở groundtruth.md §5).
6. Điền `module-map.yaml` (`paths`, xoá `qc-agent:todo`, `status: approved`); đổi `status` ngoài cùng của catalog thành `approved`.

### 3.3. Cổng HITL

```bash
qc-agent gt validate --sut-root <SUT>
echo $?     # 0 sạch · 1 còn draft/drift/module-map chưa duyệt/rejected thiếu lý do · 3 file hỏng
```

Exit 1 là **bình thường** cho tới khi QA làm xong. Xanh (0) rồi mới commit `.qc-agent/` lên nhánh và mở PR cho code owner.

### 3.4. PRD đổi

```bash
qc-agent gt regen --prd <SUT>/docs/prd/orders.md --sut-root <SUT> --openapi <SUT>/openapi.json
```

Giữ nguyên văn mọi TC `approved`/`rejected`/`origin: qa`; thay TC `draft` do LLM; TC mới là `draft`. Ghi chú của QA phải để trong trường `notes` của TC (comment YAML không sống qua `regen`).

## 4. Bước 3: đo trên SUT thật

### 4.1. File cấu hình đo

Tạo `eval/<sut>.yaml` (đặt trong repo qc-agent hoặc repo SUT; đường dẫn tương đối tính từ **file này**):

```yaml
name: my-sut-orders
sut_root: ../my-sut                     # thư mục gốc repo SUT
prd: ../my-sut/docs/prd/orders.md
openapi: ../my-sut/openapi.json         # hoặc URL của SUT đang chạy
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

**Ý nghĩa:** trong các AC kiểm được bằng HTTP, bao nhiêu AC có ≥ 1 TC do Gemini sinh (TC `rejected` không tính). Đây đo *Gemini bỏ sót yêu cầu* chứ chưa đo TC có đúng không.

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

Đo (bộ đã duyệt, **không gọi LLM**, không tốn quota):

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
- Mutant sống sót → thêm TC `origin: qa` cho AC đó (biên, giá trị âm…) rồi đo lại. Đây là cách mở rộng bộ có căn cứ.
- "(bắt bởi TC ngoài AC dự kiến)": mutant bị bắt nhưng không phải bởi TC của AC bạn khai: xem lại nhãn `acs` hoặc TC đang kiểm quá rộng.
- `(đo lỗi)`: pytest thoát mã khác 0/1 (SUT sập giữa chừng, TC approved sai cấu trúc). Không tính là bắt; sửa nguyên nhân rồi đo lại.
- Lỗi `find khớp N lần`/`git apply thất bại`: SUT đã đổi so với lúc viết mutant; cập nhật mutant.

### 4.5. Chạy đủ ba metric một lần và exit code

```bash
python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --runs 3 --yes --out-json eval/out.json
echo $?      # 0 đạt mọi ngưỡng đã đo · 1 không đạt · 3 sai cấu hình/lỗi hệ thống
```

Lệnh này đo (a), (b) trên bộ Gemini **mới** sinh (thư mục tạm, không đè file trong SUT) và (c) trên bộ **đã duyệt** trong SUT; chưa có TC nào `approved` thì bỏ (c) và nhắc trên stderr. Thứ tự khuyến nghị: 4.2/4.3 → QA duyệt (bước 3) → 4.4.

## 5. Tóm tắt lệnh theo thứ tự

```bash
qc-agent gt info --prd <SUT>/docs/prd/orders.md                                   # 0. PRD parse ra bao nhiêu AC (offline)
qc-agent gt generate --prd <SUT>/docs/prd/orders.md --sut-root <SUT> --openapi <SUT>/openapi.json   # 1. Gemini sinh + render
python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --runs 3 --yes --skip-mutants      # 2. đo (a), (b)
# 3. QA duyệt test-cases.yaml (draft → approved/rejected, thêm edge case)
qc-agent gt validate --sut-root <SUT>                                             # 4. cổng HITL: 0 mới đi tiếp
python tools/eval_gt_sut.py --config eval/my-sut.yaml --skip-generate                               # 5. đo (c) trên bộ đã duyệt
```

## 6. Điều cần nhớ

- **Cơ chế:** `gt generate` = parse PRD (tất định) → một lời gọi Gemini ép trả JSON qua function calling (tối đa một lần sửa) → render bằng mẫu cố định. LLM không bao giờ viết mã hay phát verdict.
- **Đánh đổi:** chỉ kiểm được điều PRD nêu **và** gọi được bằng HTTP. Auth động, UI, hiệu năng, LLM trong SUT nằm ngoài GT Sprint 1.
- **Số đo phụ thuộc người gán nhãn:** `non_testable` và mutant do QA chọn; dev tự chọn thì "đạt 90%" không đáng tin.
- **Free tier:** chậm (giãn cách + backoff) và có trần quota ngày; đặt `QC_LLM_MIN_INTERVAL_S` theo RPM thực của khoá (xem AI Studio → Rate limit). Dữ liệu gói miễn phí có thể được Google dùng để cải thiện sản phẩm.
- **Kết quả Gemini không tất định:** vì vậy dùng median của nhiều lượt, và chỉ bộ **đã duyệt** (cố định) mới làm cổng chặn merge.
- **Chưa xác minh với API thật:** provider Gemini được kiểm bằng test giả lập; lần đầu chạy thật hãy làm smoke test ở mục 1.4.

## 7. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `missing_key: chưa đặt GEMINI_API_KEY` | có `ANTHROPIC_API_KEY` nhưng model là `gemini-*` | đặt `GEMINI_API_KEY`; chưa có gì rời máy |
| `unavailable: HTTP 429 RESOURCE_EXHAUSTED (đã thử 6 lần)` | vượt RPM/TPM ngay cả sau backoff | tăng `QC_LLM_MIN_INTERVAL_S`, chờ, hoặc thêm model vào `QC_LLM_FALLBACK_MODELS` |
| `… (hết quota ngày) (đã thử 3 model)` | hết RPD của mọi model | chờ sang ngày (giờ Thái Bình Dương) hoặc dùng khoá trả phí |
| `bad_request: HTTP 400 INVALID_ARGUMENT` | Google từ chối tham số/schema | báo mã lỗi cho dev qc-agent (mục 1.4) |
| `bad_output: bị cắt do max_tokens` | token "thinking" ăn hết ngân sách đầu ra | đặt `QC_GEMINI_THINKING_LEVEL=low` hoặc chia PRD nhỏ hơn |
| `refused` | bộ lọc an toàn của Gemini chặn | xem lại nội dung PRD; không có cách "vượt" bộ lọc |
| `SUT không lên …` + 8 dòng log | `sut.start.cmd`/`health_path`/deps sai | chạy đúng lệnh đó tay ở thư mục bản sao để xem lỗi |
| `bộ Ground-Truth chưa có TC nào approved` | QA chưa duyệt | duyệt rồi đo lại với `--skip-generate` |
| Bộ đã duyệt không xanh trên SUT sạch | TC sai, SUT lệch PRD, hoặc môi trường bẩn | sửa trước; `baseline_green: false` làm phép đo (c) vô nghĩa nên công cụ dừng |
