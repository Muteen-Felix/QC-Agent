# Ground-Truth: từ PRD tới test case được QA duyệt

Bài toán: BA viết PRD, nhưng nếu để LLM tự viết *và* tự chấm test thì gate xanh không còn nghĩa gì. Ground-Truth tách hai việc: **LLM chỉ đề xuất test case (dạng dữ liệu)**, **QA duyệt**, còn gate chỉ chạy những test case đã được người duyệt. Tài liệu này là hướng dẫn vận hành; thiết kế nằm ở [architecture.md](architecture.md) §2 và [ADR 0003](adr/0003-gt-llm-sinh-du-lieu-khong-sinh-code.md).

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
| 5 | QA | duyệt/loại từng TC, thêm edge case, điền `module-map.yaml`: sửa `test-cases.yaml` **hoặc** mở `test-cases.xlsx` rồi `gt import-xlsx` (mục 5d) |
| 6 | CI `gt validate` | exit 1 cho tới khi hết `draft` và hết drift |
| 7 | Code owner | duyệt PR, merge |

Hai vòng khác nhau: vòng ngoài (đây) có LLM, sản phẩm là **file đi qua PR**; vòng trong (gate) **không LLM**, chặn merge của dev. Điểm nối duy nhất là `.qc-agent/**` được khoá bằng CODEOWNERS và branch protection.

## 2. Lệnh

Chạy từ gốc repo SUT (hoặc `docker run --rm -v "$PWD:/sut" <image> gt …`). Cần `ANTHROPIC_API_KEY`, hoặc `GEMINI_API_KEY` nếu đặt `QC_GT_MODEL=gemini-*` (provider chọn theo tiền tố model; chi tiết và cách đo trên SUT thật: [groundtruth-real-sut.md](groundtruth-real-sut.md)).

```bash
qc-agent gt generate --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json   # lần đầu
qc-agent gt regen    --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json   # PRD đổi: merge theo tc_id
qc-agent gt validate --sut-root .                                                      # cổng HITL, offline
qc-agent gt info     --prd docs/prd/noteboard.md                                       # prd_id, sha256, số story/AC (offline, không LLM)
qc-agent gt generate --agent --prd … --sut-root . --openapi openapi.json               # bộ sinh agent: đọc mã nguồn + OpenAPI đầy đủ (mục 5c)
qc-agent gt import-xlsx --sut-root .                                                    # ghi các sửa của QA trong test-cases.xlsx ngược vào YAML (mục 5d)
qc-agent gt export-xlsx --sut-root .                                                    # xuất lại xlsx từ YAML
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
- [ ] **Coverage** (mục 5b): `gt validate` báo gap nào thì thêm TC hoặc ghi `waivers` kèm lý do; thân PR có bảng điểm.
- [ ] `module-map.yaml`: điền `paths` (glob tới file khai báo route của từng module), xoá `qc-agent:todo`, đổi `status: approved`.
- [ ] Đổi `status` của catalog thành `approved`, push, chờ check `gt validate` xanh. Làm trên Excel thì chạy `gt import-xlsx` và commit cả hai file (mục 5d).

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

## 5b. Bộ chấm coverage (tất định, đòi 100%)

Bộ chấm (`groundtruth/coverage.py`) không dùng LLM: cùng catalog + cùng snapshot OpenAPI luôn ra cùng điểm. **Mẫu số do code quyết định**, người sinh TC không tự khai mình cần phủ gì.

```
catalog (test-cases.yaml) ──┐
                            ├─► coverage.score() ─► ac        23+1 / 25
openapi.snapshot.json ──────┘                       technique 10 / 10
  (gt generate/regen ghi, QA khoá)                  api        9 / 12   ◄── gap: "DELETE /notes/{note_id} 422"
```

**Làm thử** (noteboard, bộ catalog single-shot hiện tại):

```text
$ qc-agent gt validate --sut-root .
LỖI  …/test-cases.yaml: coverage ac 24/25 (96%) dưới ngưỡng 100% (tính TC approved + waiver approved); còn thiếu: AC-3.5
LỖI  …/test-cases.yaml: coverage api 9/12 (75%) dưới ngưỡng 100% …; còn thiếu: GET /notes/{note_id} 422, DELETE /notes/{note_id} 422, …
```

Mỗi gap có hai cách đóng. **Thêm TC** (ưu tiên), hoặc **miễn có lý do**; id của gap chính là `target`:

```yaml
uncovered_acs:                       # AC không kiểm được bằng HTTP
- {ac_id: AC-3.5, reason: chỉ kiểm được ở giao diện}
waivers:                             # miễn một ô technique/API; chỉ có hiệu lực khi status: approved
- kind: api                          # api | technique
  target: "DELETE /notes/{note_id} 422"          # đúng từng ký tự với id gap mà validate in ra
  reason_code: not_applicable        # not_http_reachable | needs_infra_fault | not_applicable | out_of_scope
  reason: note_id là chuỗi tự do nên không có đầu vào nào gây 422
  status: approved
```

Ngưỡng nằm ở `.qc-agent/ground-truth/coverage-policy.yaml` (QA khoá bằng CODEOWNERS, mặc định 1.0 cho cả ba chiều): `version: 1` rồi `thresholds: {ac: 1, technique: 1, api: 0.9}`.

### Cách chấm

| Chiều | Mẫu số | Một TC tính là phủ khi |
|---|---|---|
| `ac` | mọi AC trong catalog | có TC (`approved`) trỏ tới AC; AC nằm trong `uncovered_acs` tính là *miễn* |
| `technique` | yêu cầu **suy ra từ ràng buộc OpenAPI** của từng operation (bảng dưới) | một bước thật sự làm đúng việc đó **và** mong đúng loại mã |
| `api` | mọi (operation × mã số khai trong OpenAPI); `default`, 1xx, 3xx, 5xx được miễn sẵn | một bước mong **đúng một** mã đó (`status: [422]`) |

| Ràng buộc trong OpenAPI | Yêu cầu (id gap) | Bước phải có |
|---|---|---|
| field `required` | `negative_validation:missing:body.title` | thiếu đúng field đó, các field bắt buộc khác có đủ, mong 4xx |
| `maxLength: 200` | `boundary:max_length:body.title@200` và `@201` | độ dài đúng 200 mong 2xx; đúng 201 mong 4xx |
| `minLength: 1` | `…@1` và `…@0` | tương tự |
| `minimum/maximum` (số nguyên) | `boundary:maximum:body.qty@99` và `@100` | giá trị đúng biên mong 2xx, biên + 1 mong 4xx; biên mở (`exclusive`) lùi một đơn vị |
| `minimum/maximum` (số thực) | `boundary:minimum:body.price@0` | một giá trị bị từ chối (biên mở thì chính biên cũng tính) |
| `enum` / `pattern` | `equivalence:enum:body.kind` | giá trị ngoài tập / không khớp pattern, mong 4xx |
| `security` | `authz:unauthenticated` | một bước mong 401 hoặc 403 |

Gate chấm **chỉ TC `approved`** và waiver `approved`. Catalog còn `status: draft` thì thiếu coverage chỉ là *cảnh báo* (còn TC draft chưa tính); QA đổi catalog sang `approved` thì thiếu coverage là **lỗi** (nên reject TC làm thủng coverage thì không merge được).

**Chống ăn gian** (có test): nhãn `technique` của TC bị bỏ qua vì chỉ có cấu trúc request mới được tính; một bước mong nhiều mã (`[200, 404, 422]`) không phủ mã nào; giá trị `{{biến}}` không tính vì chưa biết lúc chấm; TC thiếu hai field bắt buộc cùng lúc không phủ field nào (lỗi không quy được về một field).

**Tương thích:** repo chưa có `openapi.snapshot.json` lẫn `coverage-policy.yaml` giữ hành vi cũ (không chấm). Chạy `gt generate|regen --openapi …` sẽ ghi snapshot và bật cổng. Không có `--openapi` thì chỉ chấm được chiều `ac`. Code coverage của SUT (line/branch khi chạy `tests_gt`) là con số riêng, **chưa** có trong `gt validate`; kế hoạch là chỉ đo (không chặn) ở `tools/eval_gt_sut.py`.

## 5c. Bộ sinh AGENT (`--agent`): đọc cả repo, nộp từng story, chấm tới khi đủ

Bộ sinh mặc định gọi LLM **một lần** chỉ với PRD và danh sách endpoint rút gọn nên hay thiếu biên, đoán sai mã lỗi. Bộ sinh agent chạy **nhiều lượt**, được đọc mã nguồn và OpenAPI đầy đủ, và chỉ dừng khi bộ chấm coverage (mục 5b) hết gap hoặc hết ngân sách.

```
PRD + <endpoints> + cây thư mục ──► agent (Claude, nhiều lượt)
   │ khám phá : list_dir · read_file · grep (chỉ đọc, có sandbox)   openapi_operation · openapi_schema (đủ body/response)
   │ lập kế hoạch : record_coverage_plan  (AC × technique)
   │ viết     : submit_test_cases(story) ──► code kiểm TỪNG TC ngay ──► TC sai: trả lý do, agent sửa và nộp lại
   │ ghi nhận : report_spec_conflict  (mã khác PRD: KHÔNG viết TC theo mã, QA quyết)
   └ kết thúc : finish_generation ──► coverage.score() ──► còn gap: trả danh sách gap chính xác (tối đa 5 lần) ──► agent nộp thêm
                                                       └► hết gap hoặc hết ngân sách ──► catalog `draft` (+ cảnh báo nếu chưa đủ)
```

**Chạy thử** (cần `ANTHROPIC_API_KEY`; chỉ Claude, model mặc định `claude-sonnet-5-5` (rẻ; Opus chỉ khi bạn chủ động đặt)):

```bash
qc-agent gt generate --agent --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json
qc-agent gt regen    --agent --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json   # giữ TC QA đã quyết, agent biết để không nộp trùng
#  --source-root DIR   thư mục mã nguồn được ĐỌC (mặc định --sut-root; CI nên truyền bản origin/<base> mount :ro)
#  --no-agent          ép bộ sinh một lời gọi dù QC_GT_GENERATOR=agent
```

Kết quả in ra có dòng `Bộ sinh: AGENT — N lượt, đọc M file, …, hoàn tất: có|KHÔNG`. `summary.json` thêm `generator`, `agent{turns, stop, completed, files_read, bytes_read, submissions, dropped_in_loop, finish_rejections, waivers, spec_conflicts, cost_usd_est, techniques}` và thân PR có dòng tương ứng cộng hai việc mới cho QA khi có `waivers` / `spec_conflicts`.

**Điều agent không bao giờ làm được** (do code chặn, có test):
- Quyết kỳ vọng từ mã: kỳ vọng lấy từ PRD/OpenAPI; mã khác PRD thì chỉ được `report_spec_conflict`.
- Tự đặt `tc_id`, `status`, `origin`: code đặt (`draft`, `llm`); catalog cuối đi qua đúng `_assemble` của bộ sinh một lời gọi nên cùng response cho ra cùng từng byte.
- Tự duyệt: `waivers` và `uncovered_acs` do agent đề xuất luôn là `draft`; gate `gt validate` chỉ tính waiver `approved` (QA duyệt).
- Đọc bí mật hay thoát khỏi repo: xem bảng sandbox bên dưới.
- Trích `evidence` từ file chưa đọc: đường dẫn không nằm trong tập file sandbox đã trả bị bỏ.

**Sandbox đọc** (`groundtruth/repo_tools.py`):

| Lớp | Cách chặn |
|---|---|
| Path | từ chối tuyệt đối, ổ đĩa, UNC, `..`, `~`; `resolve()` rồi phải nằm trong root, nên symlink/junction trỏ ra ngoài bị chặn (cả khi duyệt cây và grep) |
| Deny-list | `.git`, `.qc-agent`, `node_modules`, `.venv`, `dist/build`, `.env*`, `*.pem/*.key/*.p12`, `id_rsa*`, `.ssh`, `.aws`, tên chứa `secret`/`credential`, `*.tfstate`, file lock, `*.sqlite/*.db`… Không phân biệt hoa thường; áp lên cả path đã resolve (symlink vào `.env` vẫn bị chặn); mục bị chặn biến mất khỏi `list_dir`/`grep`; đọc path bị chặn trả cùng một thông điệp dù có tồn tại hay không |
| Kích thước | 256 KB/file, 3 MB tổng (`QC_GT_AGENT_MAX_READ_BYTES`), 400 mục/lần liệt kê, 100 kết quả grep, 400 dòng/lần đọc, dòng dài bị cắt ở 400 ký tự, grep dừng sau 10 s. Hết ngân sách đọc thì tool **từ chối**, không cắt âm thầm |
| Bí mật | che theo mẫu (khoá AWS/GitHub/Anthropic/Google/Slack, JWT, private key, `password = "…"`, mật khẩu trong URL kết nối) thành `«REDACTED»` mà **không đổi số dòng**. Chỉ là lớp phụ: lớp chính là deny-list và repo mount `:ro` |
| Dữ liệu không tin cậy | kết quả nằm trong `<file>`/`<listing>`/`<matches>`/`<openapi>`; mọi thẻ trùng tên trong nội dung bị vô hiệu hoá; prompt khai báo mã, comment, docstring, mô tả OpenAPI chỉ là dữ liệu |

**Ngân sách cứng** (hết là dừng và trả bộ dở, không raise; `agent.stop` ghi lý do): `QC_GT_AGENT_MAX_TURNS=40`, `QC_GT_AGENT_MAX_COST_USD=3` (chỉ khi biết giá model), `QC_GT_AGENT_MAX_WALL_S=1800`. Khác: `QC_GT_AGENT_MODEL`, `QC_GT_AGENT_EFFORT=high`, `QC_GT_AGENT_TIMEOUT_S=600`, `QC_GT_AGENT_FALLBACKS`. Bộ dở vẫn được ghi (`completed: false`, cảnh báo, bảng coverage cho thấy gap) vì TC chỉ là bản nháp để QA duyệt.

**Trạng thái và giới hạn hiện tại**
- Vòng lặp (`llm/agent_loop.py`), sandbox, agent, bộ chấm và cờ CLI đã có và được test bằng LLM giả (hội thoại nhiều lượt soạn sẵn). **Chưa gọi API thật lần nào**: schema tool strict và tham số `fallbacks` chưa được API xác nhận; cần một lần gọi thử có chi phí (xin phép trước) rồi đo A/B bằng `tools/eval_gt_sut.py` (chưa có cờ `--generator`).
- **Trong CI** bật bằng input `agent: true` của workflow `qc-groundtruth` (bỏ comment trong workflow gọi do `init` sinh; mặc định TẮT). Workflow xuất `git archive origin/<base>` (chỉ file đã commit ở đầu nhánh gốc, không `.git`, không file chưa commit) ra ngoài workspace và mount `:ro` ở `/src` làm `--source-root`, vì nhánh bot chỉ có `.qc-agent/**` mới và có thể mang mã cũ. Agent luôn dùng khoá `ANTHROPIC_API_KEY` (input `model` của bộ sinh một lời gọi không ảnh hưởng); `agent_model`, `agent_max_turns`, `agent_max_cost_usd` ghi đè mặc định. Đặt `timeout_minutes: 60`.
- **Repo map (tầng 1)**: trước khi vào vòng lặp, code tự quét mã Python (FastAPI + Pydantic) bằng `ast` ra bản tóm tắt: route → hàm xử lý (`file:dòng`), mã trạng thái mà hàm có thể ném (kể cả qua hàm phụ trợ trong file), dependency xác thực, ràng buộc Query/Path và model, enum, hằng số giới hạn, route không có trong OpenAPI. Bản này nằm trong khối `<repo_map>` của tin nhắn đầu (cũng là `source_code` trong egress) và chỉ là **gợi ý nơi cần đọc**: kỳ vọng vẫn lấy từ PRD/OpenAPI. Best-effort (ràng buộc tính bằng biểu thức được giữ nguyên dạng mã), lỗi quét chỉ làm mất bản tóm tắt. Mã khác (không phải Python) chưa có extractor: agent dùng `list_dir`/`grep`/`read_file`.
- Chỉ Claude: model `gemini-*` cho agent bị từ chối (`bad_request`).

## 5d. Excel cho QA (`test-cases.xlsx`)

`gt generate|regen` ghi thêm `.qc-agent/ground-truth/test-cases.xlsx` (tắt bằng `--no-xlsx` hoặc `QC_GT_XLSX=false`). QA mở file này thay cho YAML. **YAML vẫn là nguồn sự thật của gate**; xlsx là bản để đọc và sửa, hai chiều.

```
test-cases.yaml ◄──────── gt import-xlsx ─────────  test-cases.xlsx  ◄── QA sửa trong Excel
       │  (gate đọc file này)                              ▲
       └────────────── gt generate | regen | export-xlsx ──┘          gt validate: hai file phải KHỚP NHAU
```

| Sheet | Nội dung | QA làm gì |
|---|---|---|
| `HuongDan` | hướng dẫn ngay trong file | đọc |
| `Catalog` | PRD, model, `status` của catalog | đổi `status` thành `approved` khi xong |
| `TestCases` | mỗi dòng một TC | `status`, `rejected_reason`, `notes`, `priority`; sửa `title`, `ac_refs`, `kind`, `technique`…; thêm dòng mới |
| `Steps` / `Assertions` | các bước HTTP và assertion của từng TC (dạng dòng) | sửa/thêm bước và assertion |
| `Uncovered` / `Waivers` | AC không kiểm được bằng HTTP; waiver coverage | thêm/sửa, **duyệt waiver** (`approved`) |
| `SpecConflicts` | chỗ mã nguồn khác PRD (do agent báo) | đổi `open` thành `resolved` sau khi quyết |
| `Coverage` / `AgentPlan` | từng AC, technique, API: phủ / miễn / **gap** (kèm TC phủ); kế hoạch của agent | đọc |

Ô tiêu đề **vàng** là sửa được, **xám** là chỉ đọc. Cột được nhận theo **tên** nên đổi thứ tự cột được. Cột `status` và các enum có danh sách chọn.

**Làm thử**

```bash
qc-agent gt generate --agent --prd docs/prd/noteboard.md --sut-root . --openapi openapi.json   # có test-cases.xlsx
# QA mở xlsx: duyệt TC, thêm một TC mới (sheet TestCases: tc_id = NEW-1; Steps/Assertions: cùng tc_id NEW-1)
qc-agent gt import-xlsx --sut-root . --dry-run     # xem sẽ đổi gì
qc-agent gt import-xlsx --sut-root .               # ghi YAML, cấp tc_id thật cho TC mới, xuất lại xlsx
git add .qc-agent && git commit                    # commit CẢ HAI file
qc-agent gt validate --sut-root .                  # xlsx và YAML phải khớp
```

**Quy tắc**
- **Duyệt** (`status`, `rejected_reason`, `notes`, `priority`) sửa thoải mái, TC giữ `origin: llm`.
- **Sửa nội dung** một TC do LLM sinh (title, `ac_refs`, `kind`, `technique`, `preconditions`, `rationale`, hay bước/assertion) được phép, nhưng TC đó thành `origin: qa` (giữ `tc_id`, thêm ghi chú) để `regen` không ghi đè công sức của bạn.
- **TC mới**: dòng `TestCases` có `tc_id = NEW-<tên>`, rồi `Steps`/`Assertions` cùng `tc_id`. Mặc định `status: approved`, `origin: qa`; import cấp `TC-<ac>-qa-<mã>` và xuất lại file.
- **Xoá**: không được xoá dòng TC do LLM sinh (hãy `rejected` kèm lý do); TC `origin: qa` xoá được.
- Cột chỉ đọc (`origin`, `evidence`) bị sửa là lỗi. Ô công thức (bắt đầu bằng `=`) là lỗi, và mọi chuỗi ghi ra đều là text nên `=HYPERLINK(…)` không bao giờ chạy.
- `regen` tự **gộp xlsx chưa import** vào YAML trước khi merge nên không ghi đè sửa của QA; có xung đột thì dừng (exit 3) và nói rõ chỗ nào.

**Gộp ba chiều.** Mỗi lần xuất, sheet ẩn `_meta` giữ ảnh chụp lúc đó (`base`). Khi import, mỗi trường được so ba bên: `base` / xlsx / YAML. Một bên sửa thì lấy bên đó; hai bên cùng sửa khác nhau thì là **xung đột** (exit 1, không ghi gì, in `tc_id.trường`). Nhờ vậy một xlsx cũ không bao giờ hoàn nguyên sửa mới của YAML. Đừng xoá sheet `_meta`: thiếu nó thì import gộp hai chiều (xlsx thắng) kèm cảnh báo. Google Sheets có thể làm mất sheet ẩn và danh sách chọn: dùng Excel hoặc LibreOffice.

**`gt validate` và xlsx** (offline, chỉ khi có file xlsx; không có thì bỏ qua):

| Trạng thái | Nghĩa | Kết quả |
|---|---|---|
| khớp | xlsx và YAML cùng nội dung | ok |
| `xlsx_ahead` / khác nhau ở cả hai bên | QA đã quyết trong xlsx mà YAML (gate) chưa thấy | **lỗi**: chạy `gt import-xlsx` |
| `yaml_ahead` | YAML đi trước (sửa tay YAML, regen): xlsx đã cũ | cảnh báo: chạy `gt export-xlsx` (an toàn: import sau này không hoàn nguyên YAML) |
| ô sai cú pháp (JSON hỏng, công thức…) | | **lỗi**, kèm địa chỉ ô |

`gt export-xlsx` từ chối ghi đè khi xlsx đang có sửa chưa import (thêm `--force` để bỏ các sửa đó). So sánh theo **ngữ nghĩa**, không theo byte (zip có dấu thời gian), và `generate|regen|export` không ghi lại file khi nội dung đã tương đương nên commit của bot không đổi vô cớ. File xlsx vào bị giới hạn 5 MB, tỉ lệ nén, 5000 dòng/sheet; thông điệp lỗi chỉ nêu địa chỉ ô, không trích nội dung.

## 5e. Xác thực cho test (`auth.yaml`)

API đòi đăng nhập thì QA (hoặc dev) thêm `.qc-agent/ground-truth/auth.yaml` **trước khi sinh**. File được CODEOWNERS khoá như phần còn lại của `.qc-agent/` và **không chứa bí mật**:

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

| Thành phần | Hành vi |
|---|---|
| Runtime (`tests_gt/conftest.py`) | đăng nhập lười ở request đầu tiên, gắn `header` vào mọi request **trừ** chính `POST login.path`; token chỉ ở bộ nhớ |
| Header rỗng (`Authorization: ""`) | **không gửi** header đó: cách duy nhất để một test case kiểm "thiếu token" |
| Header tường minh khác (`Bearer invalid`) | giữ nguyên, không bị ghi đè |
| `scope: session` (mặc định) / `case` | một phiên cho cả lượt chạy / một phiên cho mỗi test case (dùng khi có test đăng xuất hay thu hồi phiên) |
| Khoá | chỉ `{env: QC_TEST_*}`; tên biến khác bị từ chối để file commit được không thể đưa biến môi trường khác (vd khoá LLM) vào request tới SUT |
| Lỗi | thiếu biến, sai cấu hình, đăng nhập bị từ chối, response không có token ⇒ `pytest.exit(4)` ⇒ gate tính **`error`** (hạ tầng), không phải `fail`. Thông báo chỉ nêu tên biến hoặc mã HTTP |
| LLM | thấy khối `<auth>` trong prompt (chỉ khi repo có file): không được waive vì "cần token", không được tự viết token; không bao giờ thấy khoá hay token |
| `gt validate` | kiểm cấu trúc file (lỗi ⇒ exit 1) và cảnh báo nếu dùng biến mà workflow `qc-gate` không truyền (hiện chỉ `QC_TEST_USERNAME`, `QC_TEST_PASSWORD`) |

Đổi `conftest.py` làm `gt validate` báo *drift* ở repo đã có Ground-Truth: chạy `gt regen` rồi commit. Chưa hỗ trợ: token cố định riêng của endpoint nội bộ, OAuth/cookie phiên, nhiều vai trò. Muốn kiểm AC về đăng nhập (sai mật khẩu, khoá tài khoản...), viết test case cho `POST login.path` với body riêng; endpoint đăng nhập không nhận header tự động.

## 6. Cài đặt cho một repo SUT

```bash
docker run --rm -v "$PWD:/sut" <image> init --qa-team @org/qa-team --prd-glob 'docs/prd/**'
```

`@org/qa-team` chỉ là ví dụ: thay bằng team QA thật. `qc-agent validate` báo ERROR nếu quy tắc `/.qc-agent/` còn trỏ tới owner mẫu.

`init` sinh, ngoài các suite gate: `.github/workflows/qc-groundtruth.yml` (gọi workflow tái sử dụng), và **vùng CODEOWNERS** do qc-agent quản lý:

```
# qc-agent:begin codeowners
/.qc-agent/ @org/qa-team
/.github/CODEOWNERS @org/qa-team
/.github/workflows/qc-*.yml @org/qa-team
# qc-agent:end
```

Chạy lại `init` chỉ cập nhật vùng giữa hai dấu; dòng ngoài vùng giữ nguyên từng byte. Quy tắc **cuối cùng** khớp đường dẫn thắng, nên vùng này nằm cuối file. Thiếu `--qa-team` thì owner là giữ chỗ kèm `qc-agent:todo` và `qc-agent validate` từ chối. Có `.qc-agent/ground-truth/` mà CODEOWNERS không giao `/.qc-agent/` cho ai cũng là lỗi của `qc-agent validate`.

Secret ở repo SUT: **một** khoá LLM cho việc sinh: `ANTHROPIC_API_KEY` (mặc định, model `claude-sonnet-5`) hoặc `GEMINI_API_KEY` (khi đặt `model: gemini-...` ở khối `with:` của `qc-groundtruth.yml`; workflow chọn khoá theo tiền tố model và chỉ đưa đúng khoá đó vào container). Ba input tuỳ chọn chống nghẽn quota Gemini (chỉ có tác dụng với model `gemini-*`; để trống = mặc định của image): `llm_min_interval_s` (giãn cách giữa hai request, `"12"` ≈ 5 RPM), `llm_max_retries` (mặc định 5), `llm_fallback_models` (`gemini-3.8-flash,gemini-2.5-flash`). Image ghim digest phải được build từ mã có provider Gemini. `qc_bot_token` (tuỳ chọn, xem bẫy 1). Bật **Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"**.

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

- **PRD (và danh sách endpoint OpenAPI) được gửi tới nhà cung cấp LLM đã chọn** (Anthropic API hoặc Google Gemini API, theo model). Gói miễn phí của Gemini cho phép Google dùng nội dung để cải thiện sản phẩm: PRD nhạy cảm nên dùng khoá trả phí. Mọi lời gọi ghi `egress.jsonl` (loại dữ liệu `prd_text`, `api_spec`, host đích) **trước khi gửi**; chính sách `deny` thì không có request nào.
- `egress.jsonl` và `summary.json` là **artifact của workflow** (giữ 14 ngày), không bao giờ được commit. Không file nào ghi nội dung PRD, prompt hay response vào log.
- **Với `--agent`, MÃ NGUỒN của SUT cũng rời máy** (loại dữ liệu mới `source_code` trong `egress.jsonl`, ghi trước mỗi request; cùng với `prd_text` và `api_spec`). Chỉ file qua sandbox (mục 5c) được gửi, nhưng việc che bí mật là best-effort: **chạy `gitleaks` trên repo SUT và xoá bí mật đã commit trước khi bật agent**, và chỉ bật khi đã được phép gửi mã nguồn ra Anthropic (cùng câu hỏi #3 bên dưới, nay gồm cả mã).
- **Câu hỏi #3 ở [implementation-plan.md](implementation-plan.md) (mục "Các câu hỏi chờ chốt") chưa được chốt** ("có được gửi PRD ra LLM bên ngoài không"): nó chặn mọi lượt chạy LLM thật. Cho tới khi có câu trả lời, chỉ chạy bằng PRD mẫu/PRD không nhạy cảm.
- Chi phí một lần sinh: một lời gọi (tối đa 16 000 token ra), cộng tối đa một lần sửa khi đầu ra sai schema. Số token nằm trong `summary.json`.
- **Cache (chỉ bộ sinh một lời gọi, `QC_GT_CACHE_DIR`, mặc định `~/.cache/qc-agent/gt`, `none` = tắt):** chạy lại với cùng PRD + OpenAPI + model + `prompt_version` + `auth.yaml` thì không gọi LLM và không ghi `egress.jsonl`; `summary.json` có `cache_hit: true` và `usage` là của lần sinh gốc (đừng cộng vào chi phí). Sửa `prompts/gt_generate.md` thì **phải tăng `prompt_version`**, nếu không cache trả kết quả của prompt cũ. Bộ sinh agent không dùng cache. Đo model thật (`tools/eval_groundtruth.py`, `tools/eval_gt_sut.py`) tự tắt cache.
- Chi phí bộ sinh agent: tới 40 lượt, mỗi lượt gửi lại cả lịch sử (được prompt cache). Ước tính ban đầu vài USD tới chục USD mỗi PRD với Opus (**chưa đo bằng API thật**); `agent.cost_usd_est` trong `summary.json` là ước tính theo bảng giá trong `llm/agent_loop.py` và `QC_GT_AGENT_MAX_COST_USD` là trần cứng.

## 9. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `gt validate` exit 1: "khác bản render lại (drift)" | ai đó sửa tay `tests_gt/` hoặc image đổi mẫu | sửa `test-cases.yaml` thay vì `.py`; hoặc `gt regen` |
| Gate `error` (exit 4) với thông báo "TC approved … không thuộc story nào" | TC `approved` có `ac_refs[0]` không có trong `stories` (gõ nhầm hoặc PRD đã bỏ AC) | sửa `ac_refs` hoặc chuyển `rejected` |
| Gate `fail` vì `pytest.tests >= 1` | chưa có TC nào `approved` | QA duyệt ít nhất một TC |
| Workflow "thiếu secret ANTHROPIC_API_KEY" / "thiếu secret GEMINI_API_KEY cho model gemini-…" | chưa đặt secret của đúng nhà cung cấp (model `gemini-*` cần `GEMINI_API_KEY`, còn lại cần `ANTHROPIC_API_KEY`) | đặt ở repo SUT; chưa có gì rời máy |
| Job generate lỗi `bad_request`/`unavailable` với model `gemini-*` khi image ghim cũ | image được build trước khi có provider Gemini nên coi `gemini-*` là model Claude | ghim digest image mới (build từ `main` sau S1-09) và `qc_ref` tương ứng |
| PR không có check `gt validate` | bẫy 1 | push một commit lên PR, hoặc dùng `qc_bot_token` |
