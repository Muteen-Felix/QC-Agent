# Đo Ground-Truth Sprint 1 trên SUT thật (dành cho trưởng nhóm SUT)

Tài liệu này hướng dẫn **trưởng nhóm của một SUT** chạy qc-agent trên **CI của repo SUT** và đo xem bộ test case do LLM sinh ra tốt tới đâu, **trong một ngân sách tiền cố định**. Nó bổ sung cho [user-guide-sprint-1.md](user-guide-sprint-1.md) (cách tích hợp) và [groundtruth-real-sut.md](groundtruth-real-sut.md) §4.6 (công cụ đo).

> **Đọc trước khi làm.** Lần đầu bộ sinh **agent** gọi API thật. Schema tool nghiêm ngặt (strict) và tham số `fallbacks` **chưa từng được API thật chấp nhận**, nên lần thử đầu có thể lỗi. Mã nguồn của SUT, PRD và OpenAPI **được gửi tới Anthropic**. Số tiền in ra là **ước tính theo bảng giá nội bộ**, không phải hoá đơn.

---

## 1. Hiểu nhanh: đo cái gì, đo ở đâu

Có **hai đường đo**, không thay thế nhau:

```
Đường A: CHẠY TRÊN CI CỦA REPO SUT (GitHub Actions)                 Đường B: CÔNG CỤ ĐO eval_gt_sut.py (máy người đo)
────────────────────────────────────────────────                  ──────────────────────────────────────────────
workflow qc-groundtruth (agent: true)                              bật/tắt SUT nhiều lần, chèn lỗi (mutant)
  └─► agent đọc mã nguồn + OpenAPI, sinh TC                          └─► đo: tỷ lệ bắt lỗi, TC xanh, so single vs agent
  └─► PR "Ground-Truth: <prd-id>" + summary.json                     cần repo qc-agent (không có trong image Docker)
Đo được: chi phí, số lượt, có HOÀN TẤT không,                       Đo được: mutant kill rate, green rate, A/B
         coverage (AC/technique/API), QA duyệt bao nhiêu %
```

| | Đường A: CI của SUT | Đường B: `eval_gt_sut.py` |
|---|---|---|
| Chạy ở đâu | GitHub Actions của **repo SUT** | **máy** có clone cả repo qc-agent và repo SUT |
| Cần gì | secret `ANTHROPIC_API_KEY`, image qc-agent | Python + `uv`, SUT chạy được ở máy, file `eval/<sut>.yaml`, danh sách mutant |
| Ai làm | trưởng nhóm SUT (hoặc DevOps của họ) | người đo (QA hoặc qc-agent team) |
| Trả lời câu hỏi | "Agent chạy ổn trên CI thật không, tốn bao nhiêu, coverage có đủ không, QA duyệt được bao nhiêu?" | "Bộ test có **bắt được lỗi** không? Agent có hơn single-shot không?" |

**Vì sao đường B không chạy trên CI được:** công cụ đo phải bật/tắt SUT nhiều lần trên bản sao có chèn lỗi, và nó nằm trong thư mục `tools/` của repo qc-agent, **không có trong image Docker** (Dockerfile không copy `tools/`). Dựng nó thành job CI riêng là việc chưa có sẵn và chưa được kiểm chứng, nên tài liệu này không khuyến nghị cho lần đo đầu.

**Khuyến nghị thứ tự:** đường A trước (rẻ, đúng môi trường thật), đường B sau khi QA đã duyệt và merge bộ test (cần để đo mutant trên bộ đã duyệt).

---

## 2. Điều kiện trước khi bắt đầu (checklist)

- [ ] **Được phép gửi mã nguồn ra Anthropic.** Câu hỏi #3 trong `architecture.md` §5.5 chưa chốt. Xin xác nhận bằng văn bản từ bảo mật/pháp chế của công ty. Chưa có thì **dừng**.
- [ ] **Quét bí mật trước:** chạy `gitleaks` trên repo SUT và xoá bí mật đã commit. Agent có lớp che bí mật nhưng đó chỉ là lớp phụ.
- [ ] **Đã onboarding xong** theo user-guide mục 3 và 4: `qc-groundtruth.yml` đã nằm trên `main`, secret `ANTHROPIC_API_KEY` đã đặt, đã bật *Allow GitHub Actions to create and approve pull requests*.
- [ ] **Đặt spend limit trên Anthropic Console** (ví dụ tổng $5). Đây là lớp bảo vệ thứ hai, vì giá trong code chỉ là ước tính.
- [ ] **Smoke call đã được qc-agent team chạy trước** bằng khoá của họ ở máy cục bộ (mục 3, bước 0). Đừng dùng CI của SUT để phát hiện lỗi schema/`fallbacks`: workflow **không có input nào để tắt `fallbacks`**, nên nếu API từ chối thì CI chỉ đỏ mà bạn không sửa được.
- [ ] PRD có **AC-x.y** tường minh (user-guide mục 2.1), `openapi.json` đã commit ở `main`.
- [ ] Hiểu rằng agent đọc **bản `git archive` của `main`** (chỉ file đã commit, không `.git`, không file chưa commit). Muốn agent thấy mã nào thì mã đó phải nằm trên `main`.
- [ ] Chọn **người gán nhãn là QA**, không phải người viết prompt: QA chọn mutant và khai `non_testable`. Nếu dev tự chọn thì số đo "đạt 90%" không đáng tin.

---

## 3. Đường A: đo trên CI của repo SUT, từng bước

Ngân sách mẫu **$5**: smoke $1, đo thật $3, dự phòng $1.

### Bước 0. Kiểm khô, **$0** (không gọi LLM)

Làm ở máy, tại gốc repo SUT:

PRD parse ra bao nhiêu story/AC? (offline, không LLM)

```powershell
# PowerShell (Windows): ${PWD} có dấu ngoặc nhọn, một dòng
docker run --rm -v "${PWD}:/work:ro" -w /work ghcr.io/muteen-felix/qc-agent@sha256:c662229edbd0b92b330547ed7a4cafaf612ce4b7ae86ddfada025d118441ab12 gt info --prd docs/prd/<tên>.md --openapi openapi.json
```

```bash
# Linux / macOS / Git Bash
docker run --rm -v "$PWD:/work:ro" -w /work \
  ghcr.io/muteen-felix/qc-agent@sha256:c662229edbd0b92b330547ed7a4cafaf612ce4b7ae86ddfada025d118441ab12 \
  gt info --prd docs/prd/<tên>.md --openapi openapi.json
```

Số AC phải khớp PRD. Đây chỉ là kiểm PRD. Bản kiểm khô **đầy đủ** của công cụ (`--check-only`: đọc cấu hình, quét repo map, bật SUT sạch một lần) thuộc đường B, và do qc-agent team chạy ở bước smoke cục bộ:

```bash
python tools/eval_gt_sut.py --config eval/my-sut.yaml --check-only      # $0, do qc-agent team chạy
```

### Bước 1. Smoke call trên CI, **trần $1**

Mục tiêu: xác nhận agent chạy được end-to-end trên CI thật. **Không** mong bộ test hoàn chỉnh.

1. Sửa `.github/workflows/qc-groundtruth.yml`, bỏ comment và đặt trong khối `with:`:

   ```yaml
       with:
         project: my-sut
         image: ghcr.io/muteen-felix/qc-agent@sha256:c662229edbd0b92b330547ed7a4cafaf612ce4b7ae86ddfada025d118441ab12
         prd_path: ${{ inputs.prd_path || 'docs/prd/**' }}
         openapi: "openapi.json"
         agent: true                       # bật bộ sinh agent
         timeout_minutes: 60               # agent chạy nhiều lượt, mất nhiều phút
         agent_model: claude-sonnet-5-5    # model rẻ; Opus chỉ khi chủ động đặt
         agent_max_cost_usd: "1"           # TRẦN chi phí ước tính cho MỘT lần chạy
       secrets: inherit
   ```

2. Mở PR, merge vào `main`. (Job `generate` chỉ chạy trên nhánh gốc, nên phải merge mới chạy được.)
3. **Actions → qc-groundtruth → Run workflow**, nhập `prd_path` (ví dụ `docs/prd/orders.md`).
4. Đợi xong rồi đọc kết quả (mục 4).

Kết quả có thể là:

| Thấy | Nghĩa | Làm gì |
|---|---|---|
| Job xanh, PR "Ground-Truth: …" mở ra, dòng `Bộ sinh: agent …` | API chấp nhận, đường ống chạy | sang bước 2 |
| PR nói **"Agent chưa hoàn tất"** (dừng vì hết trần $1) | bình thường ở bước smoke: trần thấp nên chưa đủ coverage | sang bước 2 |
| Job **đỏ** ở bước *Generate Ground-Truth* với lỗi từ API (thường `bad_request`) | schema tool/`fallbacks` bị API từ chối (đúng rủi ro đã nêu) | **dừng**, gửi log cho qc-agent team sửa code. Không chạy lại nhiều lần: mỗi lần gọi có thể tốn tiền |
| Đỏ vì "thiếu secret ANTHROPIC_API_KEY" | chưa đặt secret | đặt secret, chưa có gì rời máy |
| Đỏ vì hết thời gian (`timeout`) | PRD lớn | tăng `timeout_minutes`, hoặc chia PRD nhỏ hơn |

### Bước 2. Đo thật, **trần $3**

Chỉ làm khi bước 1 qua. Đổi `agent_max_cost_usd: "3"` (và nếu cần `agent_max_turns: "40"`), merge, *Run workflow* lại. **Một lần chạy, không chạy lặp.**

> **Lưu ý chi phí trên CI.** Khác công cụ đo (đường B), workflow **không** đòi `--max-total-usd`, và trần chỉ áp **cho mỗi lần chạy**. Mặc định không đặt `agent_max_cost_usd` thì trần là $3. Từ lúc `agent: true` nằm trên `main`, **mỗi lần push đổi PRD sẽ tự chạy lại agent** (`gt regen`) và tốn thêm. Vì vậy:
> - chỉ bật `agent: true` trong thời gian đo, xong thì **comment lại** (hoặc xoá);
> - đừng merge các thay đổi PRD khác khi đang đo;
> - theo dõi tổng tiền trên Anthropic Console.

### Bước 3. QA duyệt rồi merge (để có bộ đã duyệt)

QA làm đúng theo user-guide mục 5.2 đến 5.4 trên nhánh `qc-agent/gt/<prd-id>`. Với bản agent, PR có thêm hai việc cho QA: **duyệt waiver** do agent đề xuất (đồng ý lý do thì `status: approved`) và **quyết xung đột spec** (`spec_conflicts`: mã nguồn khác PRD, bên nào sai? xong thì `resolved`).

Khi `gt validate` xanh và code owner approve, merge. Đây là bộ test chính thức đầu tiên.

### Bước 4. Đo độ ổn định của bộ đã duyệt trên SUT sạch

Từ `main` mới nhất, bật SUT sạch ở máy rồi chạy:

```bash
APP_BASE_URL=http://127.0.0.1:8000 python -m pytest .qc-agent/ground-truth/tests_gt -q
```

Chạy **2 lần** liên tiếp: bộ đã duyệt là dữ liệu cố định, nên hai lần phải cho cùng kết quả (khác nhau nghĩa là SUT/môi trường flaky). TC đỏ có hai nguyên nhân phải phân biệt: **LLM đoán sai** (QA nên `rejected`) hoặc **SUT lệch PRD** (bug thật, giữ `approved` và báo dev).

Từ đây, gate PR của dev (`qc.yml`) tự chạy suite `gt-functional` trên mọi PR.

### Bước 5. Dọn dẹp

Comment lại `agent: true` (và các dòng `agent_*`) trong `qc-groundtruth.yml`, mở PR. Giữ bật hay không là quyết định **sau khi** các tiêu chí ở mục 6 đạt trên SUT thật.

---

## 4. Đọc kết quả của lần chạy CI

Có ba nơi:

1. **Thân PR "Ground-Truth: `<prd-id>`"**: dòng `Bộ sinh: agent · N lượt · đọc M file · nộp K lần · D TC bị bỏ khi nộp · ~$X`, cảnh báo nếu **chưa hoàn tất**, và **bảng coverage** (AC / technique / API, kèm các gap).
2. **Artifact** `qc-groundtruth-<prd-id>-<n>` của run (tab *Summary* của run → *Artifacts*, giữ 14 ngày), gồm:
   - `summary.json`: số liệu máy đọc được,
   - `egress.jsonl`: nhật ký dữ liệu đã gửi ra ngoài (loại `prd_text`, `api_spec`, `source_code`).
3. **Log của bước *Generate Ground-Truth***: dòng `Bộ sinh: AGENT — N lượt, đọc M file, …, hoàn tất: có|KHÔNG`.

Trích nhanh các chỉ số từ `summary.json` (cần `jq`):

```bash
jq '{generator, model, test_cases, by_status, dropped_test_cases, orphans,
     coverage,
     agent: (.agent | {turns, stop, completed, files_read, bytes_read, submissions,
                       dropped_in_loop, finish_rejections, waivers, spec_conflicts, cost_usd_est})}' summary.json
```

| Trường | Đọc thế nào |
|---|---|
| `agent.completed` | `true` nghĩa là agent kết thúc khi **hết gap coverage**. `false` thì xem `agent.stop` để biết lý do (hết lượt, hết tiền, hết thời gian, lỗi) |
| `agent.cost_usd_est` | chi phí **ước tính**, so với trần bạn đặt. Không phải hoá đơn: đối chiếu với Console |
| `agent.turns`, `files_read`, `bytes_read` | độ "tốn công" của agent; `bytes_read` chạm 3 MB nghĩa là tới giới hạn đọc |
| `agent.finish_rejections` | số lần agent xin kết thúc mà bộ chấm bác (còn gap); tối đa 5 |
| `agent.dropped_in_loop` / `dropped_test_cases` | TC bị loại vì sai cấu trúc: cao nghĩa là agent hay nộp sai |
| `agent.waivers`, `agent.spec_conflicts` | việc phát sinh cho QA (cả hai do agent đề xuất, luôn `draft`) |
| `coverage.ac / technique / api` | điểm bộ chấm tất định (đòi 100%); danh sách gap nằm trong bảng ở PR |
| `orphans` | AC không có TC và không nằm trong `uncovered_acs` |

**Số đo "QA duyệt bao nhiêu"** (chỉ số chất lượng thật nhất của đường A, tính sau bước 3):

```bash
python - <<'PY'
import yaml, collections
cat = yaml.safe_load(open(".qc-agent/ground-truth/test-cases.yaml", encoding="utf-8"))
tcs = cat["test_cases"]
print("tổng TC:", len(tcs))
print("theo status:", dict(collections.Counter(t["status"] for t in tcs)))
print("theo origin:", dict(collections.Counter(t["origin"] for t in tcs)))   # llm = giữ nguyên, qa = QA viết hoặc sửa nội dung
PY
```

- Tỷ lệ **`approved` còn `origin: llm`** = TC máy sinh mà QA giữ nguyên.
- `rejected` = máy sinh sai/đoán bừa; đọc `rejected_reason` để biết kiểu sai.
- `origin: qa` = QA phải tự viết thêm hoặc sửa nội dung: cho biết máy còn thiếu gì.

---

## 5. Đường B: đo mutant và so single với agent (máy người đo)

Chạy **sau** khi có bộ đã duyệt (bước 3 ở trên). Cần clone repo qc-agent (`pip install uv && uv sync`) và clone repo SUT cạnh nhau.

### 5.1. File cấu hình `eval/my-sut.yaml`

Đường dẫn tương đối tính từ **file này**. Ví dụ cho SUT là monorepo:

```yaml
name: my-sut-orders
sut_root: ../my-sut                         # gốc repo SUT
prd: ../my-sut/docs/prd/orders.md
openapi: ../my-sut/openapi.json
labeled_by: "QA <Tên>, 2026-10-02"          # người gán nhãn mutant/non_testable: nên là QA
non_testable: [AC-1.3]                      # AC giao diện, không kiểm được bằng HTTP

sut:
  start:                                    # công cụ tự bật/tắt SUT trên BẢN SAO của sut_root
    cmd: '<đường dẫn tuyệt đối tới interpreter> -m uvicorn app.main:app --host 127.0.0.1 --port {port}'
    cwd: apps/api-server                    # tương đối trong bản sao
    health_path: /health                    # GET trả < 500 nghĩa là đã lên
    env: {DATABASE_URL: "sqlite:///./eval.db"}
    timeout_s: 60

thresholds: {ac_coverage: 0.9, green_rate: 0.9, mutant_kill_rate: 0.9}

mutants:                                    # 10 đến 15 mutant, mỗi story ít nhất một; QA chọn
  - id: M1-status-201-to-200
    description: POST /orders trả 200 thay vì 201
    acs: [AC-1.1]
    required: true                          # không bắt được mutant này thì KHÔNG đạt
    edits:
      - {file: apps/api-server/app/routes/orders.py, find: "status_code=201", replace: "status_code=200"}
```

Quy tắc chọn mutant (để phép đo công bằng): mỗi mutant vi phạm **một AC cụ thể**; phải hợp lệ theo schema và **không 5xx** (lỗi làm SUT sập thì Schemathesis cũng bắt được, không chứng minh được giá trị của bộ test); thuộc các loại: mã trạng thái sai, biên lệch một, thiếu kiểm tra, sai tác dụng phụ, sai dữ liệu trả về. Đừng thiết kế lỗi **sau khi** xem TC nào đã tồn tại. Chi tiết: [groundtruth-real-sut.md](groundtruth-real-sut.md) §4.4.

### 5.2. Ba lệnh theo ngân sách $5

| Bước | Lệnh | Trần |
|---|---|---|
| 0. Kiểm khô, **không gọi LLM** | `python tools/eval_gt_sut.py --config eval/my-sut.yaml --check-only` | $0 |
| 1. Smoke call (1 lượt, không mutant) | `QC_GT_AGENT_MAX_COST_USD=1.0 python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --generator agent --skip-mutants --runs 1 --max-total-usd 1 --yes` | $1 |
| 2. Đo thật 1 SUT | `QC_GT_AGENT_MAX_COST_USD=3.0 python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --generator agent --generated-mutants --runs 1 --max-total-usd 3 --yes --out-json eval/real.json` | $3 |
| Dự phòng | $1 còn lại: chỉ dùng khi bước 1 hoặc 2 lỗi vì lý do ngoài tầm kiểm soát | |

(Biến môi trường dạng `VAR=… lệnh` là cú pháp bash. PowerShell: `$env:QC_GT_AGENT_MAX_COST_USD = "1.0"` ở dòng trước.)

Cũng cần `ANTHROPIC_API_KEY` trong môi trường. Bước 0 chạy trước, **bước 1 chỉ chạy khi bước 0 qua và người có thẩm quyền đồng ý**.

### 5.3. Chốt chặn chi phí của công cụ

Chạy agent thật bị **từ chối** nếu thiếu một trong các điều kiện:

1. `QC_GT_AGENT_MAX_COST_USD` được đặt **tường minh**;
2. có `--max-total-usd`;
3. `trần × số lượt` không vượt `--max-total-usd`;
4. có `--yes` (sau khi công cụ in **trần cứng**; nó không in được ước tính thật vì số lượt agent không đoán trước được).

Vòng lặp kiểm trần **trước mỗi request**, nên chi phí chỉ có thể vượt tối đa **một lời gọi cuối**. Giá theo bảng ước tính nội bộ, không phải hoá đơn: giữ spend limit trên Console.

Đo bằng `--generator agent` chỉ cho biết agent **một mình** làm được gì. Muốn biết nó **hơn single-shot bao nhiêu**, phải chạy `--generator both` và cần thêm ngân sách. Chỉ làm khi còn tiền.

Cờ `--llm fake` chỉ hợp với noteboard (hội thoại giả cố định). Với SUT khác, bản kiểm khô là `--check-only`.

### 5.4. Đọc kết quả `--out-json`

| Số đo | Ý nghĩa |
|---|---|
| (a) AC coverage | trong AC kiểm được bằng HTTP, bao nhiêu có ≥ 1 TC |
| (b) green rate | tỷ lệ TC xanh trên SUT sạch (TC đỏ là LLM bịa hoặc SUT lệch PRD) |
| (c) mutant kill rate | bao nhiêu lỗi chèn vào bị bộ **đã duyệt** bắt |
| (d) bộ chấm coverage | AC / technique / API trên bộ vừa sinh (tính cả `draft`: "nếu QA duyệt hết thì đủ chưa") |
| (e) mutant trên bộ vừa sinh | chỉ giữ TC xanh trên SUT sạch rồi chạy mutant: cách duy nhất so hai bộ sinh khi chưa có QA duyệt |
| (f) chi phí, thời gian, số lượt | của agent, ước tính |

Exit code: 0 đạt mọi ngưỡng đã đo · 1 không đạt · 3 sai cấu hình/lỗi hệ thống.

---

## 6. Tiêu chí đạt

Các ngưỡng dưới đây có sẵn trong công cụ (chỉnh bằng khoá `agent_thresholds` trong YAML). Chỉ **bật `agent: true` lâu dài** trên CI sau khi đạt trên **một SUT thật**.

| Tiêu chí | Ngưỡng mặc định | Lấy từ |
|---|---|---|
| Hoàn tất | mọi lượt agent hoàn tất (`completed: true`) | đường A, `summary.json` |
| Coverage tất định | **100%** ở AC, technique, API | đường A, bảng coverage; đường B (d) |
| Chi phí | ước tính ≤ **$10**/lượt (khuyến nghị giữ trần $3) | đường A/B (f) |
| Thời gian | lượt lâu nhất ≤ **30 phút** | đường A/B (f) |
| AC coverage (single-shot) | ≥ 90% | đường B (a) |
| Green rate | ≥ 90% (agent không được kém single, tối thiểu 85%) | đường B (b) |
| Mutant kill rate | ≥ 90% **và** mọi mutant `required` bị bắt (hoặc hơn single ≥ 20 điểm) | đường B (c)/(e) |

Chỉ có ở đường A và **chưa phải ngưỡng của công cụ**: tỷ lệ `approved` còn `origin: llm` (mục 4). Nhóm nên tự chốt một mức trước khi đo (ví dụ "đa số TC giữ nguyên") để khỏi chọn ngưỡng sau khi thấy kết quả.

**Lưu ý khi diễn giải:** mutant do người viết nên kết quả chỉ có nghĩa với đúng tập mutant đó. Kết quả LLM không tất định: một lượt chưa nói lên độ ổn định.

---

## 7. Mẫu báo cáo trả lại cho qc-agent team

Điền và gửi kèm `summary.json`, `eval/real.json` (nếu có) và link PR Ground-Truth. **Không gửi `egress.jsonl` ra ngoài công ty** nếu nó có đường dẫn nội bộ nhạy cảm; xem trước.

| Mục | Giá trị |
|---|---|
| SUT / PRD / commit `main` lúc chạy | |
| Image digest, model agent | |
| Bước 1 (smoke): kết quả, lỗi (nếu có) | |
| Bước 2: `completed`, `stop`, số lượt, file đọc | |
| Chi phí ước tính / chi phí trên Console | / |
| Coverage AC / technique / API | / / |
| Số TC: tổng, `approved`, `rejected`, `origin: qa` | |
| Số waiver / spec_conflict agent đề xuất; QA đồng ý bao nhiêu | |
| Bộ đã duyệt xanh trên SUT sạch (2 lần) | |
| Mutant kill rate, mutant sống sót | |
| Người gán nhãn mutant | |
| Vấn đề gặp phải | |

---

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Job `generate` đỏ `bad_request` ngay lượt đầu của agent | API từ chối schema tool strict hoặc `fallbacks` (chưa từng được xác nhận) | dừng, gửi log cho qc-agent team; **không** chạy lặp |
| "agent chỉ hỗ trợ model Claude" | `agent_model` bắt đầu bằng `gemini-` | đặt model Claude hoặc bỏ `agent_model` |
| "thiếu mã nguồn nhánh gốc cho agent" / không thấy `origin/<base>` | workflow chạy ngoài nhánh gốc | chạy từ `main` |
| PR nói **chưa hoàn tất**, bảng coverage còn gap | hết trần chi phí/lượt/thời gian trước khi đủ coverage | bình thường ở smoke; ở bước 2 thì xem `agent.stop`, cân nhắc nâng trần một cách có kiểm soát |
| Agent không thấy mã mới | agent chỉ đọc bản `git archive` của `main` | merge mã vào `main` rồi chạy lại |
| Chi phí Console cao hơn `cost_usd_est` | `cost_usd_est` theo bảng giá ước tính | tin Console; hạ trần |
| Mỗi lần merge PRD lại tốn tiền | `agent: true` vẫn bật | comment lại sau khi đo (bước 5) |
| `--max-total-usd` bị từ chối | `trần × số lượt` vượt tổng | hạ `--runs`/trần hoặc nâng tổng một cách có chủ ý |
| `SUT không lên …` khi đo đường B | `sut.start.cmd`/`health_path`/phụ thuộc sai | chạy đúng lệnh đó bằng tay ở thư mục bản sao |
| Bộ đã duyệt không xanh trên SUT sạch → đo mutant dừng | TC sai, SUT lệch PRD hoặc môi trường bẩn | sửa trước; không hạ ngưỡng |
