# plan-execution.md — QC Agent PoC · Checklist thực thi

**Nguồn:** [architecture.md](architecture.md) (RUN 6). File này là tài liệu **thao tác**: không thêm quyết định
kiến trúc, chỉ biến `architecture.md` thành 57 bước làm được ngay.
**Người dùng:** Nghĩa (Role A) / Đức (Role B) / Huy (Role C) — 3 dev code PoC · **Ngày soạn:** 2026-09-19
**Nhãn trích dẫn:** `[arch §x]` = `architecture.md` mục x · `[R5 x]` = RUN 5 mục x.

 > **Phân vai (cố định cho sprint này):** **Nghĩa = Role A** (Orchestrator & Contract) · **Đức = Role B** (Authoring & Governance) · **Huy = Role C** (Deterministic & Eval).
> Trong file này, "A", "B", "C" đứng một mình luôn chỉ đúng người trên: A = Nghĩa, B = Đức, C = Huy. Trường `Owner` và bảng chỉ mục STEP (Phụ lục 2) ghi cả tên lẫn role.

> **Một câu để nhớ cả kế hoạch:** ngày 1 phải có *walking skeleton* chạy hết với worker giả (STEP 20).
> Nếu contract sai, phát hiện ở đó tốn 1 giờ; phát hiện lúc ghép chiều ngày 2 thì mất cả nhánh demo `[R5 P3]`.

---

# 0. ĐỌC TRƯỚC (5 phút — bỏ qua mục này là nguồn gốc của 80% câu hỏi ngược lại)

## 0.1 Cách đọc một STEP

Mỗi STEP có **6 trường bắt buộc** + 1 trường `Time`:

| Trường | Nghĩa |
|---|---|
| **Owner** | Một người: Nghĩa (Role A) / Đức (Role B) / Huy (Role C). `Nghĩa (Role A) (+Đức, Huy)` = Nghĩa chủ trì, cả ba phải có mặt |
| **Depends on** | STEP phải xong **và đã push** trước khi bắt đầu. `none` = độc lập |
| **Parallel-safe** | STEP mà **người khác** có thể làm cùng lúc mà không đụng file / không chờ kết quả của nhau. Hai STEP cùng owner thì luôn tuần tự trong thực tế |
| **Actions** | Lệnh và file cụ thể. `F-xx` = file **nguyên văn** ở Phụ lục 6 (copy nguyên, đã chạy thử — xem 0.5) |
| **DoD** | Tiêu chí nhị phân: *chạy lệnh Y ra kết quả Z*. Chưa ra Z = chưa xong |
| **Nếu fail** | Fallback → cắt → escalate. Đọc **trước** khi bắt đầu, không phải sau khi kẹt |
| **Time** | Ước lượng phút. Là **phán đoán, không phải số đo**, có thể lệch ×2 — cut ladder (Phụ lục 4) tồn tại vì lý do đó |

Ký hiệu: ⚠ = chi tiết của tool mà RUN 1–5 **chưa verify** (tên cờ CLI, cấu trúc file output…) → STEP đó có
lệnh *khám phá* trước, đừng code theo trí nhớ · 🛑 = cổng (gate) — chưa qua thì không đi tiếp.

## 0.2 Quy ước thao tác chung

| Mục | Quy ước |
|---|---|
| Shell | **Windows PowerShell 5.1** trên Windows 11 (không WSL, không Docker `[arch §1.2]`) |
| Thư mục làm việc | `$HOME\qc-agent-poc` (mỗi người tự clone; không dùng ổ `D:\` vì máy người khác có thể không có) |
| Mỗi terminal mới | `cd $HOME\qc-agent-poc` rồi `. .\scripts\env.ps1` (nạp `.env`, ép UTF-8, đặt `.venv` vào PATH). Sau đó `python`, `pytest`, `st` là bản trong `.venv` |
| Lệnh chạy orchestrator | `python orchestrator.py --plan <file>` (xem 0.3 #1) |
| Đường dẫn | `/` trong YAML / JSON / Python; `\` chỉ trong PowerShell |
| Git | Nhánh `main`. Mỗi STEP ≥ 1 commit, message `STEP NN: <tên>`. `git pull --rebase` trước mỗi STEP, push ngay khi DoD đạt. Không force-push |
| Contract sau freeze (STEP 07) | Sửa `schemas/task_spec.json`, `schemas/result.json`, `workers/_template.yaml` chỉ khi **cả ba đồng ý** và sửa **cùng lúc mọi adapter** `[R5 2.4]`. `tests/test_contract_frozen.py` sẽ đỏ nếu ai sửa lén |
| Review | Mọi adapter phải qua A theo checklist 5 câu `[arch §3.2]` (STEP 26, 33, 38). Không merge adapter chưa review |
| Không đoán | Gặp ⚠: chạy lệnh khám phá, **lưu output mẫu vào `tests/samples/`** và commit. Test của adapter đọc file mẫu đó — không gọi tool thật |

Biến duy nhất file này **không thể biết** và không viết sẵn: URL remote git của team (`$REMOTE` ở STEP 03),
3 giá trị của VLM (STEP 01) và key của model chấm (STEP 02). Mọi thứ khác đều viết cụ thể.

## 0.3 Chỗ file này LỆCH với yêu cầu RUN 7 hoặc với architecture.md — đọc để khỏi cãi nhau

| # | Chỗ lệch | Bên A | Bên B | **File này dùng** | Vì sao |
|---|---|---|---|---|---|
| 1 | Lệnh chạy | RUN 7: `python orchestrator.py --plan demo.yaml` | arch/R5: `orchestrate run --plan plan.yaml` | `python orchestrator.py --plan <file>`; Phase 1 dùng `plans/demo.yaml`, từ Phase 2 dùng `plan.yaml` ở root | Thoả cả hai. `orchestrator.py` là shim 4 dòng gọi `core/cli.py`; ở arch/R5 đọc `orchestrate run` = lệnh này |
| 2 | Tên contract | RUN 7: "QRS schema v0.1" | arch §4.1: `title … v1.0.0` | Giữ `title` nguyên; **tag git `qrs-v0.1`** đánh dấu bản đóng băng | Đổi `title` = sửa file contract sau freeze. Tag là nhãn của sprint, chưa qua mentor |
| 3 | Mutation testing | RUN 7: "nếu arch có mutation testing" | arch **không có** | **Không thêm công cụ mutation.** Phase 3 dùng bảng *mutant cài sẵn*: 3 lỗi seed của toy app `[R5 2.1]` + 2 mutant harness + 9 mutant hợp đồng | Đây là thứ arch đã có (lỗi cài sẵn + `allOf` của N2). Không có gì mới về công cụ |
| 4 | Tiêu chí #4 "lọc `llm_judgment` và timestamp" `[R5 2.3]` | `diff report.json` sau khi lọc hai thứ đó phải rỗng | **Không đủ:** k6 p95, `wallclock_s`, sha256 evidence **luôn khác** giữa hai run | Định nghĩa "lọc" = **chiếu về `deterministic_view`** (chỉ `status`, `verdict`, `verdict_source`, `gating` của result `gating=true`). Công cụ: `tools/diff_runs.py` (F-11) | Nếu so nguyên `report.json` thì #4 **không bao giờ đạt**, mà #4 là "khoảnh khắc mạnh nhất" của demo. **Phải nói rõ với mentor** định nghĩa này |
| 5 | Diff-Guard | arch §7 có DG-1…DG-4 + AST diff (§7.4, ngoài nguồn) | RUN 7: "từng luật, cách implement, cách test chính nó" | DG-1 → STEP 45. DG-2/DG-3 → STEP 46 (một guard `protected-paths`). **DG-4 không có bước code** (PoC không có healer nên không có sự kiện heal để log). **AST diff không làm** `[arch §7.4]` | Cài DG-4 khi không có nguồn sự kiện = làm giả |
| 6 | Phân vai `[arch §3.2]` | A: toàn bộ `core/` | — | **Chuyển** `core/registry.py` + `core/signature.py` → **C**; `core/verdict.py` + `core/report.py` → **B**. A vẫn giữ `schema.py`, `evidence.py`, `plan.py`, `_base.py`, `runner.py`, `cli.py` và **review tất cả** | Tải: A theo arch = ~24 giờ trên ~20 giờ có. Sau chuyển: A 19.8h · B 17.4h · C 16.7h (Phụ lục 2). **A đồng ý ở STEP 07** |
| 7 | `plan-gen` (LLM sinh plan từ spec) `[arch §3.2, A]` | Có trong bảng component | **Không có STEP nào** | **Plan viết tay** ngay từ đầu (đây đã là *cut #1* của [R5 4.2] — cắt trước khi bắt đầu, không phải khi chậm) | Tổng tải đã ~19.8h/20h; `[R5 4.2 #1]`: *"plan viết tay vẫn chứng minh plan-as-artifact"*. Nói trên slide: *"plan-gen là bước offline, không nằm trong đường CI — đúng với kiến trúc, không phải bào chữa"*. Dư giờ ở slot 5 thì A tự làm `tools/plan_gen.py` (file này không viết DoD cho nó) |
| 8 | Thêm file mà arch chưa liệt kê | — | — | `oracle/checks.py` · `adapters/collect_adapter.py` (worker `http.collect`) · `core/schema.py` · `core/plan.py` · `core/cli.py` · `core/signature.py` · `guards/` · `tools/review_adapter.py` · `pytest.ini` · endpoint `/__qc/events` của toy app · cờ `--only`, `--yellow-exit`, `--base` | Cần để code chạy. `collect` là task thu output riêng mà `[R3 rủi ro #4]` yêu cầu. **Không** phải "trường riêng cho một worker" (N4): không file nào ở đây nằm trong contract |

## 0.4 Quyết định kickoff — có sẵn giá trị mặc định (chốt ở STEP 07, ghi vào `docs/decisions.md`)

Nếu team im lặng, **giá trị mặc định có hiệu lực**. Đổi mặc định = đọc cột "Nếu đổi". (D-10 đứng trước D-08 chỉ vì nó được thêm sau khi kiểm; thứ tự số không quan trọng.)

| ID | Quyết định | **Mặc định** | Lý do | Nếu đổi |
|---|---|---|---|---|
| **D-01** | ⛔ M1 `[arch §0.1]`: lỗi cài sẵn #3 tất định hay ngẫu nhiên | **(a) Tất định:** `summarize` là stub thuần (`toyapp/summarizer.py`), body chứa `[[long]]` ⟹ summary dài hơn body. Case g3 của 5 golden case luôn fail metric `summary_shorter_than_body` | Tiêu chí #4 nằm trong danh sách **không bao giờ cắt** `[R5 4.2]`; (b) làm #4 gãy với task eval. `[R5 2.1]` cho phép SUT giả lập, chỉ cần ghi "SUT giả lập" trong báo cáo | (b) ⟹ sửa STEP 06, 11, 37, 41 và hạ #4 thành "loại `llmapp.eval` khỏi diff" |
| **D-02** | G1: exit code khi gate 🟡 (có `skipped`) | `0` + khối SKIPPED **ở đầu** report; cờ `--yellow-exit N` (mặc định `0`) để CI của team tự đổi | Nguồn không nói `[arch §0.3 G1]`. Cờ giải Q2 mà không phải quyết thay team | — |
| **D-03** | Python | `3.11` (`.python-version`) | Bản có sẵn trên máy soạn file này (có cả 3.11.9 và 3.13). **Giả định**, RUN 1–5 không nêu | Đổi minor: sửa `.python-version`, cả 3 người chạy lại bootstrap |
| **D-04** | Node | Cùng **major** trên cả 3 máy, ≥ 22.12 (ràng buộc Node duy nhất trong RUN 1–5, của agent-device; của Midscene chưa verify) | Máy soạn file có v23.6.0. STEP 04 ghi major vào `.node-version` | Midscene đòi khác ⟹ STEP 23 phát hiện, sửa `.node-version` |
| **D-05** | Toy app | `http://127.0.0.1:8000` (`APP_BASE_URL`) | `[R5 replay_cmd]` dùng `localhost:8000` | — |
| **D-06** | Model VLM / model chấm | **File này không chọn.** Tên model do team điền ở `.env`. Quy tắc cứng `[arch D5]`: model chấm **≠** model của SUT (SUT là stub `stub-rule-v1` nên luôn thoả) | Không có dữ kiện trong RUN 1–5 để chọn | — |
| **D-07** | Encoding | Ép UTF-8 ở mọi tiến trình (`PYTHONUTF8=1`); JSON qua stdin/stdout của adapter dùng `ensure_ascii=True`; **mọi nơi đọc JSON dùng `encoding="utf-8-sig"`** (PowerShell 5.1 `Out-File -Encoding utf8` chèn BOM làm `json.loads` lỗi) | **Đã gặp thật khi soạn file:** Python trên Windows dùng cp1252 khi stdout bị redirect ⟹ `UnicodeEncodeError` với tiếng Việt | — |
| **D-10** | Nguồn của `confidence` cho finding `llm_judgment` `[SUY RA]` | **Chỉ lấy từ một con số mà tool thật sự báo.** G-Eval (DeepEval) trả điểm 0–1 ⟹ dùng điểm đó làm `confidence` **và ghi vào `adapter_notes`** "confidence := G-Eval score". Midscene `aiAssert` chỉ trả đúng/sai ⟹ **không phát** finding `llm_judgment` (adapter được mất thông tin, không được bịa) | `[arch §4.2]` ví dụ 3 ghi `confidence 0.64` cho điểm 0.71 mà không nói lấy ở đâu; schema **bắt buộc** `confidence` ≠ null cho `llm_judgment` (ở adapter) nên phải có luật nguồn gốc. Hệ quả: mục 2 của report dựa vào **DeepEval** + mock; Midscene có thể không sinh `llm_judgment` nào — **lệch với [R5 P3 slot 3]** ("≥1 finding `llm_judgment`"), nói rõ với mentor | Chọn hằng số cố định (vd 0.5) ⟹ đó là **bịa**; không khuyến nghị |
| **D-08** | `inputs_schema` của capability | **Tuỳ chọn** trong PoC (không ai bắt buộc viết) | Cắt để giữ tải; ghi vào Known Limitations. `[R4 S12a]` muốn validate `inputs` — PoC chưa làm | Bật sau sprint |
| **D-09** | Rule chéo trong `task_spec.json` (F-06): `lane=gate ⟹ expected_result_kind=verdict`, `lane=discovery ⟹ candidate_finding` | **Bật** `[SUY RA]` | Nguồn chỉ nêu chiều discovery. Chiều gate là suy ra: không có nó, task gate khai `candidate_finding` sẽ **tụt khỏi gate trong im lặng** (đúng V2) | Bỏ = xoá 2 khối `allOf` cuối F-06, trước freeze |

## 0.5 Đã kiểm gì khi soạn file này — và chưa kiểm gì

**Đã chạy thật (thư mục tạm, Python 3.11.9 + `jsonschema`, FastAPI):**

| Kiểm | Kết quả |
|---|---|
| Trích `schemas/result.json` + 3 ví dụ từ `architecture.md` (F-04) rồi validate (F-05) | **3/3 PASS** |
| `task_spec.json` (F-06) với 2 ví dụ task (F-07, F-08) | **2/2 PASS** |
| 9 mutant hợp đồng (C1–C9, bảng ở STEP 47) chống `result.json` | **9/9 bị chặn** |
| 7 mutant task spec (retry `fail`, `max=3`, lane/kind sai, thêm `worker`, thiếu `usd`, evidence `screenshots`) | **7/7 bị chặn** |
| Lỗ hổng đã biết G4 `[arch §0.3]`: finding `llm_judgment` thiếu `confidence` | **Không bị chặn** (đúng như arch ghi; adapter phải ép) |
| `core/verdict.py` (F-10) + `tests/test_verdict.py` (F-25): 10 case + banner (AND rỗng, `error` ở gate vs discovery, skipped, thiếu result…) | **11 passed** |
| `core/schema.py` (F-22) + `tests/test_schema.py` (F-24): validate 4 result + 2 task, luật liên-trường (gate tụt `gating`, discovery đòi chặn, llm thiếu `confidence`, thiếu evidence, run_id lệch), `make_result` UTF-8 | **19 passed** |
| `tools/review_adapter.py` (F-23): 5 câu checklist; cố ý đặt tên worker vào `core/` | 5/5 PASS; câu 5 **FAIL** đúng khi có tên worker |
| `scripts/env.ps1`, `doctor.ps1`, `toyapp.ps1`, `bootstrap.ps1` trên PowerShell 5.1 | 0 lỗi cú pháp, **0 byte ngoài ASCII**; `env.ps1` nạp `.env` đúng (bỏ dòng `#`, bỏ nháy); `doctor` báo `FAIL` đúng cho k6/st/deepeval/midscene chưa cài; `toyapp.ps1` start/status/stop thật với uvicorn |
| `pytest` trần **không** có `pytest.ini` / **có** `pytest.ini` | collect lỗi / 39 passed |
| `scripts/repeat.ps1` với orchestrator **giả** (mỗi lần ghi một result hợp lệ) / có result `status=error` | `ALL 3 RUNS OK` exit 0 / exit 1 |
| **Dựng lại repo trống CHỈ từ Phụ lục 6** (32 file F + `pytest.ini` + `test_summarizer.py` trong STEP 06), rồi chạy đúng chuỗi lệnh STEP 05 (extract, validate ×2) + STEP 07 (freeze) + `pytest` | mọi lệnh exit 0; **`71 passed`** — nghĩa là Phụ lục 6 tự đủ, không thiếu file |

**Hai phát hiện khi kiểm (đã sửa trong file này, không phải lỗi của bạn khi làm theo):**
1. `-Bugs ""` **không tắt được bug**: PowerShell coi gán biến môi trường rỗng là *xoá* nó ⟹ app quay về mặc định `1,2,3`. Dùng **`-Bugs none`**.
2. Spec ví dụ Midscene của RUN 4 đòi `screenshot` + `trace` ở cấp result, nhưng hai result ví dụ của [arch §4.2] không có ⟹ F-08 hạ `evidence_required` còn `["raw_output"]` (screenshot nằm ở cấp finding). Khi STEP 23 biết Midscene xuất gì thì mới nâng lại.
| `tools/diff_runs.py` (F-11): 2 báo cáo giống / khác | Đúng cả hai, exit 0 / 1 |
| `tools/freeze_contract.py` (F-12): sửa nội dung / chỉ đổi CRLF↔LF | Bắt sửa; **bỏ qua** CRLF |
| Toy app (F-14…F-17) bằng TestClient: BUG-1 → 500, BUG-3 chỉ ở g3, `/__qc/*` không nằm trong OpenAPI | Đúng |

**Chưa chạy:** mọi thứ liên quan **k6, Schemathesis, DeepEval, Midscene** (máy soạn file chưa cài k6; RUN 1–5 chưa chạy
tool nào), `winget`, `bootstrap.ps1`, toàn bộ `core/` ngoài `verdict.py`. Mọi chỗ đó là ⚠ và có STEP khám phá.

## 0.6 Bản đồ slot ↔ phase ↔ cổng

**Giả định của file này:** 1 slot ≈ **4 giờ làm việc thực** (RUN 1–5 chia 5 slot nhưng không định nghĩa độ dài).

| Slot | Khi nào | Phase | STEP | 🛑 Cổng cuối slot |
|---|---|---|---|---|
| 1 | Ngày 1 sáng | **0** Kickoff + đầu Phase 1 | 01–12 | Contract đã tag `qrs-v0.1` · 3 máy `doctor` xanh · toy backend `/docs` chạy |
| 2 | Ngày 1 chiều | **1** Walking skeleton | 13–26 | **STEP 20 DoD** (chỉ cổng này bắt buộc; 23, 25, 26 được phép trượt sang slot 3) |
| 3 | Ngày 2 sáng | **2** Worker khó nhất trước | 27–36 | 3 adapter thật qua checklist; report có dòng thật |
| 4 | Ngày 2 chiều | **2** hoàn thiện + AI branch, **3** bắt đầu | 37–44, 46, 47 | **Tiêu chí PoC #1–#4 đạt** (STEP 41) — chưa đạt thì sáng ngày 3 **không** thêm tính năng |
| 5 | Ngày 3 sáng | **3** còn lại, **4** | 45, 48–57 | Dry-run bấm giờ ≤ 10 phút (STEP 57) |

---

# PHASE 0 — KICKOFF (bắt buộc làm chung; không ai viết adapter/core trước khi xong)

**Mục tiêu:** contract đóng băng · repo giống nhau trên 3 máy · toy app chạy được.
**🛑 DoD Phase 0:** cả 3 người `git clone` + chạy **1 lệnh** (`bootstrap.ps1`) ⟹ `doctor.ps1` in `ENV-FINGERPRINT` **giống hệt nhau** trên 3 máy; `git tag qrs-v0.1` tồn tại; toy backend mở được `/docs`.

### STEP 01 — Kiểm key VLM (việc số 0, trước cả chốt schema `[R5 P4.1 #4]`)
- **Owner:** Đức (Role B) · **Depends on:** none · **Parallel-safe:** STEP 02, 03 · **Time:** 20'
- **Actions:** mở PowerShell (chưa cần repo), điền **3 giá trị team đã có** rồi chạy nguyên khối:
  ```powershell
  $env:MIDSCENE_MODEL_BASE_URL = "https://..."   # <- URL gốc endpoint model (giả định tương thích OpenAI /chat/completions)
  $env:MIDSCENE_MODEL_API_KEY  = "..."
  $env:MIDSCENE_MODEL_NAME     = "..."           # <- tên model VLM
  $body = @{ model = $env:MIDSCENE_MODEL_NAME; messages = @(@{ role = "user"; content = "ping" }); max_tokens = 5 } | ConvertTo-Json -Depth 5
  Invoke-RestMethod -Method Post -Uri ($env:MIDSCENE_MODEL_BASE_URL.TrimEnd('/') + "/chat/completions") `
    -Headers @{ Authorization = "Bearer $env:MIDSCENE_MODEL_API_KEY" } -ContentType "application/json" -Body $body |
    Select-Object -ExpandProperty choices | Select-Object -First 1
  ```
  Ghi 3 giá trị vào `.env` ở STEP 09 (không dán key vào chat/commit). Biến thứ 4 `MIDSCENE_MODEL_FAMILY`: ⚠ giá trị hợp lệ do doc Midscene quy định — STEP 23 xác nhận.
- **DoD:** lệnh in ra một đối tượng có `message.content` khác rỗng; **không** ném lỗi 401/403/404/429.
- **Nếu fail:** 401/403 → key/quyền sai · 429 → hết quota (xin thêm, đừng chờ) · 404/405 → endpoint không tương thích OpenAI: bỏ smoke REST, để STEP 23 kiểm bằng chính Midscene. **Quá 30' không có key dùng được** ⟹ báo cả nhóm: Midscene chạy bằng **fixture giả lập viết tay** (`tests/samples/midscene_summary.fixture.json`, **gắn nhãn mock** trên slide `[R5 4.3]`). Cả nhóm vẫn đi tiếp.

### STEP 02 — Kiểm key model chấm cho DeepEval
- **Owner:** Huy (Role C) · **Depends on:** none · **Parallel-safe:** STEP 01, 03 · **Time:** 20'
- **Actions:**
  ```powershell
  python tools\judge_provider.py
  ```
  Điền key **chỉ trong `.env` local**: `OPENAI_API_KEY`, có thể thêm `GEMINI_API_KEY` (hoặc `GOOGLE_API_KEY`). Mặc định probe OpenAI trước; nếu không dùng được thì probe Gemini bằng `x-goog-api-key` ([Google auth](https://ai.google.dev/gemini-api/docs/api-key), [model list](https://ai.google.dev/api/models)). Script chỉ liệt kê model ID, **không gửi input/output của SUT**, không in hoặc ghi key. Để dùng Gemini trong DeepEval, chọn provider/model do smoke check báo ra trong `.env`; DeepEval hỗ trợ `GeminiModel` với `api_key` truyền tường minh ([docs](https://deepeval.com/integrations/models/gemini)).
  Nhắc `[arch D5]`: model chấm **phải khác** model của SUT. SUT là stub `stub-rule-v1` nên luôn thoả; adapter (STEP 37) vẫn phải kiểm và trả `error` nếu trùng.
- **DoD:** in `provider=...` và ≥ 1 model ID, không lỗi. Test fallback: OpenAI trả 401 + Gemini key hợp lệ ⟹ chọn provider `gemini`.
- **Nếu fail:** cả hai provider đều không dùng được ⟹ **bỏ G-Eval, giữ metric tất định**; worker vẫn chạy deterministic checks và ghi rõ lỗi G-Eval. Dùng **G-Eval mock** (điểm cố định trong fixture, gắn nhãn mock) để mục 2 của report vẫn có dữ liệu. Ghi `[KHÔNG KỊP SPRINT NÀY]` vào báo cáo.

### STEP 03 — Dựng repo skeleton
- **Owner:** Nghĩa (Role A) · **Depends on:** none · **Parallel-safe:** STEP 01, 02 · **Time:** 30'
- **Actions:** (A tạo trước một repo **rỗng** trên git host của team, rồi dán URL vào `$REMOTE`)
  ```powershell
  $REMOTE = "..."                                   # <- URL repo rỗng của team (file này không biết host)
  cd $HOME
  mkdir qc-agent-poc; cd qc-agent-poc
  git init -b main
  git remote add origin $REMOTE
  $dirs = "core","adapters","oracle","guards","schemas","workers","plans","toyapp\static","tests\perf","tests\eval","tests\fixtures","tests\samples","tests\guards","midscene","baselines","tools","scripts","docs\research","examples","recordings"
  foreach ($d in $dirs) { New-Item -ItemType Directory -Force $d | Out-Null; New-Item -ItemType File -Force "$d\.gitkeep" | Out-Null }
  "3.11" | Out-File -Encoding ascii .python-version
  Copy-Item "D:\AI-ENGINEERING\projects\QC-Agent\architecture.md"    docs\architecture.md
  Copy-Item "D:\AI-ENGINEERING\projects\QC-Agent\plan-execution.md"  docs\plan-execution.md
  Copy-Item "D:\AI-ENGINEERING\projects\QC-Agent\qcagent-run*.md"    docs\research\
  ```
  Tạo 5 file văn bản (nội dung ngay dưới), rồi commit:
  - `pytest.ini` (3 dòng: `[pytest]` · `pythonpath = .` · `testpaths = tests`). **Thiếu file này thì `pytest` trần không thấy `core/`, `toyapp/`** ⟹ lỗi collect (đã gặp khi soạn file).
  - `.gitignore`: `.venv/` · `node_modules/` · `.env` · `runs/` · `__pycache__/` · `*.pyc` · `.pytest_cache/` · `.deepeval/` · `*.log` · `.toyapp.pid` (mỗi mục một dòng). **`recordings/` KHÔNG ignore** (chứa run dự phòng cho demo).
  - `.gitattributes`: `* text=auto` · `*.json text eol=lf` · `*.yaml text eol=lf` · `*.py text eol=lf` · `*.md text eol=lf`.
  - `.env.example`: các biến ở **Phụ lục 3**, mỗi dòng `TEN=` (giá trị rỗng, trừ `APP_BASE_URL=http://127.0.0.1:8000` và `QC_RUNS_DIR=runs`).
  - `README.md`: dòng 1 là `> Orchestrator "ngu" là orchestrator đúng. Routing bằng LLM là thừa và có hại. [arch §6.2]`; dưới đó 3 dòng: cách dựng môi trường (STEP 04), cách chạy toy app (STEP 11), cách chạy orchestrator (STEP 20).
  ```powershell
  git add -A; git commit -m "STEP 03: repo skeleton"; git push -u origin main
  ```
- **DoD:** (1) `git ls-remote origin main` in đúng 1 dòng có hash; (2) `(Get-ChildItem -Directory -Exclude .git).Count` in **`16`**; (3) `git status --porcelain` rỗng.
- **Nếu fail:** push bị từ chối (không quyền / repo không rỗng) → xin quyền ngay, **không ai code được khi chưa có remote**. Tạm thời: `git bundle create qc.bundle main`, gửi file cho B, C, họ `git clone qc.bundle qc-agent-poc`; đổi `origin` sau.

### STEP 04 — Môi trường chung: bootstrap + doctor + khoá phiên bản
- **Owner:** Đức (Role B) · **Depends on:** 03 · **Parallel-safe:** STEP 05, 06 · **Time:** 90'
- **Actions:**
  ```powershell
  cd $HOME; git clone $REMOTE qc-agent-poc; cd qc-agent-poc      # $REMOTE do A gửi
  ```
  1. Tạo **F-01** `scripts/env.ps1`, **F-02** `scripts/bootstrap.ps1`, **F-03** `scripts/doctor.ps1` (Phụ lục 6). **Chỉ ASCII** trong `.ps1` — PowerShell 5.1 đọc file không BOM theo ANSI, ký tự tiếng Việt có thể làm vỡ cú pháp (Troubleshooting #4).
  2. `requirements.txt` (một gói mỗi dòng): `fastapi` · `uvicorn` · `httpx` · `pydantic` · `jsonschema` · `pyyaml` · `pytest` · `schemathesis` · `deepeval`.
  3. Cài k6 (⚠ cách cài trên Windows **không có** trong RUN 1–5; `winget` là cách phổ biến), ghi phiên bản, ghi major của Node:
     ```powershell
     winget install k6 --source winget --accept-package-agreements --accept-source-agreements
     # MỞ TERMINAL MỚI (PATH), rồi:
     cd $HOME\qc-agent-poc
     (k6 version | Select-String -Pattern 'v\d+\.\d+\.\d+').Matches[0].Value | ForEach-Object { "k6 $_" } | Out-File -Encoding ascii .k6-version
     (node --version).TrimStart('v').Split('.')[0] | Out-File -Encoding ascii .node-version
     npm init -y
     npm install --save-dev --save-exact @midscene/cli       # ⚠ tên gói lấy từ RUN 3 (trang npm)
     ```
  4. Dựng môi trường + khoá phiên bản (đây là "pin"):
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
     .\.venv\Scripts\python.exe -m pip freeze | Out-File -Encoding ascii requirements.lock
     powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1; "exit=$LASTEXITCODE"
     git add -A; git commit -m "STEP 04: env bootstrap/doctor/lock"; git push
     ```
     Phiên bản của Schemathesis / DeepEval / Midscene **do `requirements.lock` và `package-lock.json` giữ** — RUN 1–5 không nêu số nào đáng tin để pin trước.
- **DoD:** `doctor.ps1` in **toàn dòng `OK`** + dòng `ENV-FINGERPRINT xxxxxxxxxxxx`, `exit=0`; `git ls-files requirements.lock package-lock.json .k6-version .node-version scripts/env.ps1 scripts/bootstrap.ps1 scripts/doctor.ps1` in **7 dòng**.
- **Nếu fail:** (a) **`pip` báo xung đột** giữa `deepeval` và `schemathesis` → timebox 30': tạo venv thứ hai `.venv-eval` chỉ cho `deepeval` (`py -3.11 -m venv .venv-eval`), đặt `QC_DEEPEVAL_PYTHON=.venv-eval/Scripts/python.exe` trong `.env`; adapter DeepEval (STEP 37) chạy `pytest` bằng python đó (không cần sửa manifest — contract đã đóng băng); (b) `winget` không có → tải zip k6 từ trang phát hành của Grafana, giải nén, thêm vào PATH tay; (c) `npm install @midscene/cli` lỗi (thường là tải Chrome) → ghi nguyên văn lỗi vào `docs/decisions.md`, B đi tiếp, **STEP 23 xử lý** — không chặn Phase 0 vì `doctor` chỉ kiểm thư mục gói tồn tại.

### STEP 05 — Bản nháp contract: task_spec + result + ví dụ + công cụ đóng băng
- **Owner:** Nghĩa (Role A) · **Depends on:** 03 · **Parallel-safe:** STEP 04, 06 · **Time:** 60'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase
  py -3.11 -m venv .venv                        # venv tạm; bootstrap của B trên máy B là venv riêng, không xung đột
  .\.venv\Scripts\python.exe -m pip install --quiet jsonschema pyyaml pytest
  $env:PYTHONUTF8 = "1"
  ```
  Tạo (nguyên văn từ Phụ lục 6): **F-04** `tools/extract_from_arch.py` · **F-05** `tools/validate.py` · **F-06** `schemas/task_spec.json` · **F-07** `examples/task.k6.json` · **F-08** `examples/task.midscene.json` · **F-09** `schemas/capabilities.json` · **F-12** `tools/freeze_contract.py` · **F-13** `workers/_template.yaml` · **F-19** `tests/test_contract_frozen.py` · **F-20** `examples/result.k6_pass.json`. Rồi:
  ```powershell
  .\.venv\Scripts\python.exe tools\extract_from_arch.py          # -> schemas\result.json + 3 examples\result.*.json (từ docs\architecture.md §4.1, §4.2)
  .\.venv\Scripts\python.exe tools\validate.py result examples\result.e2e_pass.json examples\result.e2e_fail.json examples\result.ai_eval.json examples\result.k6_pass.json; "exit=$LASTEXITCODE"
  .\.venv\Scripts\python.exe tools\validate.py task examples\task.k6.json examples\task.midscene.json; "exit=$LASTEXITCODE"
  git add -A; git commit -m "STEP 05: contract draft"; git push
  ```
  Nguồn duy nhất của `result.json` là `docs/architecture.md` §4.1 — **không** gõ tay bản thứ hai.
- **DoD:** lệnh thứ nhất in **4 dòng `PASS`** + `exit=0`; lệnh thứ hai in **2 dòng `PASS`** + `exit=0`.
- **Nếu fail:** một ví dụ `FAIL` → **sửa ví dụ trong `docs/architecture.md`** rồi extract lại (schema là đặc tả, ví dụ là minh hoạ). Chỉ khi thấy *đầu ra thật của worker không biểu diễn được* bằng schema thì mới ghi vào `docs/decisions.md` để **STEP 07** bàn — **không** tự sửa schema một mình.

### STEP 06 — Stub summarizer + 5 golden case (giải mâu thuẫn M1 bằng mã)
- **Owner:** Huy (Role C) · **Depends on:** 03 · **Parallel-safe:** STEP 04, 05 · **Time:** 60'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase
  py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install --quiet pytest
  New-Item -ItemType File -Force toyapp\__init__.py | Out-Null
  ```
  Tạo **F-14** `toyapp/summarizer.py`, **F-15** `tests/eval/golden.json` (Phụ lục 6), và `tests/test_summarizer.py`:
  ```python
  import json, pathlib
  from toyapp.summarizer import summarize

  G = json.loads(pathlib.Path("tests/eval/golden.json").read_text(encoding="utf-8"))

  def test_bug_off_all_shorter():
      assert all(0 < len(summarize(g["body"], False)) < len(g["body"]) for g in G)

  def test_bug_on_only_g3_longer():
      assert [g["id"] for g in G if len(summarize(g["body"], True)) >= len(g["body"])] == ["g3"]

  def test_deterministic():
      assert all(summarize(g["body"]) == summarize(g["body"]) for g in G)
  ```
  ```powershell
  $env:PYTHONUTF8 = "1"; .\.venv\Scripts\python.exe -m pytest tests\test_summarizer.py -q
  ```
  Tạo `docs/decisions.md` với mục `D-01: chọn (a) tất định — lý do — đề xuất bởi C, chốt ở STEP 07`. Commit + push.
- **DoD:** `pytest` in `3 passed`. (Đã chạy thử khi soạn file: bug bật ⟹ `[True, True, False, True, True]`; bug tắt ⟹ toàn `True`.)
- **Nếu fail:** case khác g3 cũng dài hơn body → body quá ngắn (< 2 ký tự) → sửa body trong golden, **không** sửa stub. Nếu team chọn (b) ở STEP 07 → xoá stub, ghi lại hệ quả ở mục 0.4 D-01.

### STEP 07 — 🛑 Họp chốt contract + quyết định kickoff + ĐÓNG BĂNG
- **Owner:** Nghĩa (Role A) (chủ trì; **Đức (Role B) và Huy (Role C) bắt buộc có mặt**, cả ba cùng màn hình) · **Depends on:** 05, 06 · **Parallel-safe:** none (mọi người đều ở cuộc họp) · **Time:** 45'
- **Actions:** theo đúng thứ tự, có bấm giờ:

  | Phút | Việc | Kết quả ghi vào `docs/decisions.md` |
  |---|---|---|
  | 0–10 | Đọc mục 0.4: chốt hoặc đổi **D-01…D-10**, từng dòng một | Mỗi quyết định một dòng `D-0x: ACCEPT` hoặc `D-0x: CHANGED — <giá trị mới> — <lý do>` |
  | 10–20 | Cặp **tất định**: `examples/task.k6.json` ↔ `examples/result.k6_pass.json` | Câu trả lời cho 2 câu hỏi bắt buộc bên dưới |
  | 20–32 | Cặp **discovery**: `examples/task.midscene.json` ↔ `result.e2e_pass.json` + `result.e2e_fail.json` (canary) | như trên |
  | 32–38 | `result.ai_eval.json`: một result, hai `verdict_source`; xác nhận `gating` **chỉ** ở `verdict`, không ở finding `[arch §0.3 G3]` | Xác nhận G3, G4 |
  | 38–45 | A đồng ý phân vai lệch (mục 0.3 #6); đóng băng + tag | — |

  **Hai câu hỏi bắt buộc cho mỗi cặp** `[R5 P3 slot 1]`: (1) trường nào worker này **không có dữ liệu** để điền — trường đó có `null` được không? (2) có ai muốn thêm trường **chỉ dành cho worker này** không? Nếu **có** ⟹ dừng 15': hoặc sửa schema cho **cả 4** worker, hoặc để adapter **mất thông tin** (quy tắc vàng: được mất, không được bịa). **Cấm** thêm trường riêng `[R5 P4.1 #1]`.

  Sau khi thống nhất (nếu có sửa `schemas/*.json`, `_template.yaml` thì sửa **trước** lệnh dưới):
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase
  $env:PYTHONUTF8 = "1"
  .\.venv\Scripts\python.exe tools\freeze_contract.py --write
  .\.venv\Scripts\python.exe -m pytest tests\test_contract_frozen.py -q
  git add -A; git commit -m "STEP 07: freeze contract qrs-v0.1"
  git tag qrs-v0.1; git push; git push --tags
  ```
- **DoD:** (1) `git ls-remote --tags origin qrs-v0.1` in 1 dòng; (2) `python tools/freeze_contract.py --check` in `CONTRACT NGUYÊN VẸN: 3 file`, exit 0; (3) `(Select-String -Path docs\decisions.md -Pattern '^D-(0[1-9]|10)').Count` in **`10`**.
- **Nếu fail:** bất đồng về schema quá 20' → **A quyết** (contract có đúng một chủ `[arch §3.2]`), ghi lý do, đi tiếp; không kéo họp quá 45' vì skeleton phải chạy hết ngày 1. Nếu ai đó vẫn muốn đổi sau freeze ⟹ chỉ được qua luật ở mục 0.2 (cả ba đồng ý + sửa cùng lúc mọi adapter).

### STEP 08 — Clone sạch + 1 lệnh trên máy A
- **Owner:** Nghĩa (Role A) · **Depends on:** 04, 07 · **Parallel-safe:** STEP 09, 10 · **Time:** 20'
- **Actions:** (dùng lại nguyên khối cho STEP 09, 10; mỗi người chạy trên máy mình)
  ```powershell
  cd $HOME
  if (Test-Path qc-agent-poc) { Rename-Item qc-agent-poc qc-agent-poc.bak }
  git clone $REMOTE qc-agent-poc; cd qc-agent-poc
  if (Test-Path ..\qc-agent-poc.bak\.env) { Copy-Item ..\qc-agent-poc.bak\.env .env }
  powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1; "exit=$LASTEXITCODE"
  ```
  Dán dòng `ENV-FINGERPRINT xxxxxxxxxxxx` vào kênh chat của nhóm. Sau khi ba dòng khớp (STEP 10) mới xoá `qc-agent-poc.bak`.
- **DoD:** `bootstrap.ps1` kết thúc `exit=0` và in dòng `ENV-FINGERPRINT`.
- **Nếu fail:** lỗi nào thì đối chiếu **Troubleshooting #4** (môi trường lệch). Nếu `k6` vừa cài mà `doctor` báo không thấy → **mở terminal mới**. Không tiếp tục STEP kế tiếp khi `doctor` còn `FAIL`.

### STEP 09 — Clone sạch + 1 lệnh trên máy B
- **Owner:** Đức (Role B) · **Depends on:** 04, 07 · **Parallel-safe:** STEP 08, 10 · **Time:** 20'
- **Actions:** như STEP 08. Thêm: điền `.env` với 3 giá trị của STEP 01 + `MIDSCENE_MODEL_FAMILY` (để trống nếu chưa biết), rồi `. .\scripts\env.ps1; $env:MIDSCENE_MODEL_NAME` phải in tên model.
- **DoD:** như STEP 08 + `$env:MIDSCENE_MODEL_NAME` không rỗng sau khi nạp `env.ps1`.
- **Nếu fail:** như STEP 08.

### STEP 10 — Clone sạch + 1 lệnh trên máy C, và ĐỐI CHIẾU 3 fingerprint
- **Owner:** Huy (Role C) · **Depends on:** 04, 07 · **Parallel-safe:** STEP 08, 09 · **Time:** 20'
- **Actions:** như STEP 08. Thêm: điền `OPENAI_API_KEY` (STEP 02) vào `.env`. Khi cả ba dòng `ENV-FINGERPRINT` đã có trong chat, C dán ba dòng vào mục `## Phase 0 gate` của `docs/decisions.md`, commit + push.
- **DoD:** ba fingerprint là **một giá trị duy nhất**.
- **Nếu fail:** giá trị lệch → mỗi người chạy `.\.venv\Scripts\python.exe -m pip freeze | Out-File -Encoding ascii freeze_<A|B|C>.txt` rồi `Compare-Object (Get-Content freeze_A.txt) (Get-Content freeze_B.txt)`. Nguyên nhân thường gặp: ai đó `pip install` thêm gói ngoài lock, hoặc khác minor Python. Sửa bằng cách xoá `.venv` + chạy lại bootstrap. **Không** sửa `requirements.lock` để "cho khớp".

### STEP 11 — Toy backend `noteboard` + script chạy/dừng
- **Owner:** Huy (Role C) · **Depends on:** 10, 06 · **Parallel-safe:** STEP 12 (A) · **Time:** 75'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  ```
  Tạo **F-16** `toyapp/app.py`, **F-18** `scripts/toyapp.ps1`, một `toyapp/static/index.html` **tạm** chỉ gồm `<!doctype html><title>noteboard</title><script>window.QC_BUGS = __QC_BUGS__;</script>` (B thay bằng bản thật ở STEP 22), và `tests/test_toyapp.py` (nguyên văn **F-21**, Phụ lục 6). Có 3 lỗi cài sẵn — đọc docstring đầu `app.py`, đừng "sửa" chúng.
  ```powershell
  pytest tests\test_toyapp.py -q
  .\scripts\toyapp.ps1 start
  (Invoke-RestMethod http://127.0.0.1:8000/openapi.json).paths.PSObject.Properties.Name
  try { Invoke-WebRequest -UseBasicParsing ("http://127.0.0.1:8000/notes/" + ("x" * 65)) } catch { [int]$_.Exception.Response.StatusCode }
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 11: toy backend + toyapp.ps1"; git push
  ```
  Cảnh báo `StarletteDeprecationWarning` / `DeprecationWarning` của TestClient là **bình thường** (đã gặp khi soạn file), không phải lỗi.
- **DoD:** (1) `pytest tests\test_toyapp.py -q` in **`6 passed`**; (2) `toyapp.ps1 start` in `started pid=… port=8000 bugs=1,2,3 latency_ms=0`; (3) lệnh `paths` in đúng 3 dòng: `/notes`, `/notes/{note_id}`, `/notes/{note_id}/summarize`; (4) lệnh `GET` id dài in **`500`** (lỗi BUG-1 đang bật); (5) `http://127.0.0.1:8000/docs` mở được, thấy 5 operation; (6) `(Invoke-RestMethod http://127.0.0.1:8000/__qc/config).bugs -join ","` in `1,2,3`; (7) `toyapp.ps1 stop` in `stopped`.
- **Nếu fail:** cổng 8000 bận → `Get-NetTCPConnection -LocalPort 8000 | Select OwningProcess` rồi `Stop-Process -Id <pid>`, hoặc `-Port 8001` **và** đổi `APP_BASE_URL` (mọi người phải đổi cùng lúc — tránh, ưu tiên giải phóng 8000). App không lên → `Get-Content runs\toyapp.err.log`. **Tuyệt đối** không "sửa" BUG-1 để test cho xanh: demo cần gate đỏ có lý do.

## 🛑 Cổng Phase 0 — dừng lại kiểm trước khi sang Phase 1

| ☐ | Điều kiện | Lệnh kiểm |
|---|---|---|
| ☐ | Contract đã tag | `git ls-remote --tags origin qrs-v0.1` |
| ☐ | Contract nguyên vẹn | `python tools/freeze_contract.py --check` → exit 0 |
| ☐ | 3 máy cùng fingerprint | STEP 10 DoD |
| ☐ | Toy backend chạy, có lỗi seed | STEP 11 DoD (4) |
| ☐ | M1 đã chốt | `docs/decisions.md` có dòng `D-01` |

Chưa đủ 5 dấu ☐ ⟹ **không ai bắt đầu STEP 12+**. (Ngoại lệ duy nhất: STEP 12 của A có thể bắt đầu ngay khi STEP 07 xong, vì nó chỉ cần schema.)

---

# PHASE 1 — WALKING SKELETON (ưu tiên số 1, xong trong ngày 1)

**Mục tiêu:** `python orchestrator.py --plan plans/demo.yaml` chạy hết đường ống với **worker giả**, in report có đủ field QRS, exit code đúng.
**Vì sao làm trước worker thật:** contract sai mà phát hiện ở đây tốn 1 giờ; phát hiện lúc ghép chiều ngày 2 thì mất cả nhánh demo `[R5 P3]`. Ba lỗi kiến trúc chỉ lộ ra khi *ghép* (V1 contract méo, V2 skipped im lặng, thiếu evidence) — skeleton là chỗ bắt chúng.
**🛑 DoD Phase 1 = DoD STEP 20.**

### STEP 12 — `core/schema.py` + `core/evidence.py` (nền của mọi thứ còn lại)
- **Owner:** Nghĩa (Role A) · **Depends on:** 07 · **Parallel-safe:** STEP 08–11 (B, C đang clone/dựng backend) · **Time:** 60'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  foreach ($d in "core","adapters","oracle","guards") { New-Item -ItemType File -Force "$d\__init__.py" | Out-Null }
  ```
  Tạo **F-22** `core/schema.py` và **F-24** `tests/test_schema.py` (nguyên văn). Rồi viết `core/evidence.py` (~25 dòng) với đúng 3 hàm:
  - `sha256_file(path) -> str` — hash **byte thô** của file.
  - `collect(items, base=Path.cwd()) -> list[dict]` — `items = [(kind, Path)]`; trả `[{"kind","uri","sha256"}]`, `uri` là đường dẫn **posix tương đối** so với `base` (`runs/r-0001/t-001/x.json`); file không tồn tại ⟹ `raise FileNotFoundError` (adapter bắt và thành `error`).
  - `missing_kinds(evidence, required) -> list[str]`.
  Viết `tests/test_evidence.py`: (1) hash khớp `hashlib` trên file mẫu; (2) `uri` không chứa `\`; (3) file thiếu ⟹ `FileNotFoundError`; (4) `missing_kinds` đúng.
  ```powershell
  pytest tests\test_schema.py tests\test_evidence.py -q
  git add -A; git commit -m "STEP 12: core/schema + evidence"; git push
  ```
- **DoD:** `pytest tests\test_schema.py -q` in **`19 passed`**; `pytest tests\test_evidence.py -q` in **`4 passed`**; `Select-String -Path core\*.py -Pattern 'k6|schemathesis|deepeval|midscene' -CaseSensitive:$false` **không in gì**.
- **Nếu fail:** một test của F-24 đỏ ⟹ đừng sửa test cho xanh: đọc thông báo — thường là `examples/*.json` chưa có ở `main` (STEP 05 chưa push) hoặc `pytest.ini` thiếu (STEP 03).

### STEP 13 — `adapters/_base.py` + gói `oracle/` (khuôn cho mọi adapter)
- **Owner:** Nghĩa (Role A) · **Depends on:** 12 · **Parallel-safe:** STEP 14 (C), 17 (B), 18 (C) · **Time:** 90'
- **Actions:** viết 4 file rồi `tests/test_base.py`.
  1. **`oracle/__init__.py`** — tự nạp mọi module con (để thêm `oracle.kind` mới **không phải sửa file này** — đó là điều kiện của N5 ở bước 4 của mục 8.2 arch):
     ```python
     import importlib, pkgutil
     from dataclasses import dataclass, field

     class OracleError(ValueError): ...            # dữ liệu đưa vào oracle không đủ để phán -> adapter đổi thành status=error

     @dataclass
     class OracleOutcome:
         value: str | None                          # "pass" | "fail" | None (oracle không phán: discovery)
         findings: list = field(default_factory=list)
         notes: list = field(default_factory=list)

     _KINDS = {}
     def register(kind):
         def deco(fn): _KINDS[kind] = fn; return fn
         return deco

     def evaluate(oracle_spec: dict, metrics: dict, signals: dict) -> OracleOutcome:
         kind = oracle_spec["kind"]
         if kind not in _KINDS: raise OracleError(f"oracle.kind chưa hỗ trợ: {kind}")
         return _KINDS[kind](oracle_spec, metrics, signals)

     for _m in pkgutil.iter_modules(__path__):
         importlib.import_module(f"{__name__}.{_m.name}")
     ```
  2. **`oracle/trivial.py`**: `@register("trivial")` ⟹ `OracleOutcome("pass")`.
  3. **`oracle/checks.py`**: `@register("checks")`; `oracle_spec = {"kind":"checks","required":[tên,…]}`, `signals["checks"] = {tên: bool}`. Tên `required` thiếu trong `signals["checks"]` ⟹ `raise OracleError` (worker chưa chạy check đó thì **không được** khẳng định pass). Có ≥1 check `False` ⟹ `value="fail"` kèm **mỗi check hỏng một finding** `{"finding_id": "f-check-<tên>", "title": "check <tên> không đạt", "detected_by": "check:<tên>", "verdict_source": "deterministic_assert", "confidence": None, "severity_hint": "high"}`.
  4. **`adapters/_base.py`**: khung dưới đây, hành vi theo danh sách ngay sau.
     ```python
     class AdapterParseError(Exception): ...

     @dataclass
     class ParsedOutput:
         metrics: dict = field(default_factory=dict)
         findings: list = field(default_factory=list)         # ĐÃ đúng dạng findings[] của result.json
         signals: dict = field(default_factory=dict)          # đầu vào cho oracle: {"checks": {...}} | {"detected": [...]}
         evidence_paths: list = field(default_factory=list)   # [(kind, Path)]
         tokens: int | None = None
         usd: float | None = None
         exit_code: int | None = None
         flow_failed: bool = False                            # CHỈ discovery: luồng tự hành thất bại (vd canary)
         replay_cmd: str | None = None
         adapter_notes: list = field(default_factory=list)

     class Adapter(ABC):
         NAME: str; ADAPTER_VERSION: str
         env: dict = {}                                       # biến môi trường thêm cho tiến trình worker
         @abstractmethod
         def build_cmd(self, spec, workdir: Path) -> list[str]: ...
         @abstractmethod
         def parse_output(self, proc, workdir: Path, spec) -> ParsedOutput: ...
         def run(self, spec) -> dict: ...                     # KHÔNG override
         def main(self, argv=None) -> int: ...                # KHÔNG override
     ```
     **`run(spec)` phải làm đúng thứ tự này** (một bước sai = một loại lỗi im lặng):
     1. `workdir = Path(os.environ.get("QC_RUNS_DIR","runs")) / spec["run_id"] / spec["task_id"]`; tạo thư mục.
     2. `cmd = self.build_cmd(...)`; chạy `subprocess.run(cmd, capture_output=True, timeout=spec["budget"]["wallclock_s"], env={**os.environ, **self.env, "PYTHONUTF8":"1"})`, giải mã `stdout/stderr` bằng `utf-8, errors="replace"`. **Timeout = `budget.wallclock_s`, không có con số thứ hai** `[arch §5.3]`. ⚠ **Windows:** `subprocess.run(timeout=…)` chỉ giết tiến trình con *trực tiếp*, để lại tiến trình mồ côi (`npx` → `node` → trình duyệt) — dùng `Popen` + `communicate(timeout=…)` và khi hết giờ chạy `taskkill /PID <pid> /T /F` (giết cả cây) rồi mới báo `error` `"timeout"` (Troubleshooting #2).
     3. `out = self.parse_output(...)`.
     4. `TimeoutExpired` ⟹ `error` `"timeout: budget.wallclock_s"`; `AdapterParseError`, `OracleError`, `FileNotFoundError` ⟹ `error` `"parse: …"`; **mọi `Exception` khác** ⟹ `error` `"crash: …"`. **Không có nhánh nào biến ngoại lệ thành `fail`.**
     5. `outcome = oracle.evaluate(spec["oracle"], out.metrics, out.signals)`; `findings = out.findings + outcome.findings`.
     6. Dựng `verdict` **theo `spec.expected_result_kind`, không theo ý worker**: `verdict` ⟹ `{value: outcome.value, verdict_source:"deterministic_assert", gating:true, confidence:None}` và `status = outcome.value`; `candidate_finding` ⟹ `gating:false`, nếu `out.flow_failed` thì `status:"fail"`, `value:"fail"`, `verdict_source:"deterministic_assert"` (như ví dụ canary [arch §4.2]), ngược lại `status:"pass"`, `value:"non_gating"`, `verdict_source:"heuristic"`.
     7. `cost = {wallclock_s: đo thật, tokens: out.tokens, usd: out.usd}` — worker không báo ⟹ `None`. **Cấm ước lượng.**
     8. `evidence = evidence.collect(out.evidence_paths)`.
     9. Chạy `schema.validate_result` và `schema.check_result_against_spec`; **có vi phạm ⟹ trả `schema.make_result(spec, "error", "; ".join(vi_phạm))`. Không sửa cho vừa.**
     `main()`: đọc spec từ `--spec FILE` (mở bằng **`utf-8-sig`**, chịu được BOM của PowerShell 5.1) hoặc **stdin** (bytes → `utf-8-sig`); ghi result ra `--out FILE` hoặc **stdout** bằng `json.dumps(result, ensure_ascii=True)`; **exit 0 kể cả khi result là `fail`/`error`**; exit 2 + thông báo ở stderr nếu spec không parse/không hợp lệ `[arch §5]`. Adapter con chạy được bằng `python -m adapters.<tên>` (cuối file: `if __name__ == "__main__": Xxx().main()`).
  5. **`tests/test_base.py`** — dùng một `FakeAdapter` trong file test (spec lấy từ `examples/task.k6.json`, đổi `capability=demo.echo`, `oracle={"kind":"trivial"}`, `evidence_required=["raw_output"]`). **10 test:** (1) đường lành ⟹ result validate + không vi phạm; (2) worker ngủ quá `budget.wallclock_s` ⟹ `status=error`, `rationale` chứa `timeout`; (3) `parse_output` ném `AdapterParseError` ⟹ `error`; (4) `parse_output` ném `KeyError` ⟹ `error` `crash`; (5) oracle `checks` có check `False` ⟹ `status=fail`, `gating=true`; (6) finding `llm_judgment` thiếu `confidence` ⟹ `error`; (7) thiếu evidence bắt buộc ⟹ `error`; (8) `oracle.kind` lạ ⟹ `error`; (9) chạy `main()` qua `subprocess` với `--spec/--out` ⟹ exit 0 **cả khi** result là `fail`; (10) `rationale` tiếng Việt có dấu đi qua stdin→stdout không mất chữ (`UnicodeEncodeError` = lỗi cp1252 đã nêu ở D-07).
  ```powershell
  pytest tests\test_base.py -q
  git add -A; git commit -m "STEP 13: adapters/_base + oracle pkg"; git push
  ```
- **DoD:** `pytest tests\test_base.py -q` in **`10 passed`**; `Select-String -Path adapters\_base.py,oracle\*.py -Pattern 'k6|schemathesis|deepeval|midscene' -CaseSensitive:$false` không in gì.
- **Nếu fail:** kẹt ở bước 9 (`check_result_against_spec`) quá 30' ⟹ đừng nới luật: hỏi ở chat, vì đó là chỗ N2 sống. Nếu chậm cả STEP: cắt test (8) và (10) trước, **không** cắt (2), (6), (7).

### STEP 14 — `core/registry.py` (quét manifest, probe, chọn worker)
- **Owner:** Huy (Role C) · **Depends on:** 12 · **Parallel-safe:** STEP 13 (A), 17 (B), 18 (C — cùng owner nên thực tế tuần tự) · **Time:** 60'
- **Actions:** viết `core/registry.py` (~60 dòng) với:
  - `Worker` (dataclass): `name`, `adapter` (đường dẫn), `module` (**dẫn xuất**: `adapters/k6_adapter.py` → `adapters.k6_adapter`), `lanes`, `capabilities` (dict `id → khối capability`), `requires`, `version_probe`, `data_egress`, `probe_ok`, `probe_reason`, `version`.
  - `load(dir="workers") -> dict[str, Worker]`: bỏ file bắt đầu bằng `_`; thiếu `name`/`adapter`/`lanes`/`capabilities` ⟹ `raise ManifestError` nêu tên file.
  - `probe(worker)`: (1) mọi `requires.binaries` có trong PATH (`shutil.which`); (2) mọi `requires.env` **không rỗng** trong `os.environ`; (3) chạy `version_probe` (timeout 20s), exit 0 ⟹ `version` = dòng đầu stdout. Thất bại ⟹ `probe_ok=False` + **`probe_reason` nêu đúng cái thiếu** (vd `thiếu biến môi trường OPENAI_API_KEY`) — `[arch V2]`: lý do này sẽ được in **ở đầu report**.
  - `pick(registry, spec, prefer=()) -> (Worker | None, reason)`: lọc theo `capability` ∈ `w.capabilities`, `spec.lane` ∈ `w.lanes`, `spec.oracle.kind` ∈ `oracle_kinds` của capability đó, **và `probe_ok`**. Nhiều ứng viên ⟹ theo thứ tự `prefer`, còn lại **sắp theo tên** (tất định). **Không có LLM, không có ngẫu nhiên** `[arch §6.2]`. Không ứng viên ⟹ `(None, reason)`, `reason` phân biệt *"không worker nào có capability này"* với *"có worker nhưng probe hỏng: <lý do>"*.
  - `tests/test_registry.py` (manifest dựng trong `tmp_path`): (1) bỏ qua `_template.yaml`; (2) thiếu binary ⟹ `probe_ok=False` + đúng lý do; (3) env rỗng ⟹ `probe_ok=False`; (4) worker lane `[discovery]` **không** được chọn cho task `gate`; (5) `oracle.kind` không nằm trong `oracle_kinds` ⟹ không chọn; (6) hai ứng viên: `prefer` thắng, không `prefer` ⟹ theo tên; (7) `(None, reason)` phân biệt hai loại lý do.
  ```powershell
  pytest tests\test_registry.py -q
  git add -A; git commit -m "STEP 14: core/registry"; git push
  ```
- **DoD:** `pytest tests\test_registry.py -q` in **`7 passed`**.
- **Nếu fail:** `version_probe` của worker chạy lâu/treo ⟹ timeout 20s là bắt buộc; `probe_ok=False` + `probe_reason="version_probe timeout"`. Không "bỏ qua probe cho nhanh": probe là cách duy nhất ta biết worker có mặt.

### STEP 15 — Worker giả `mock` + fixture (để skeleton chạy trước mọi worker thật)
- **Owner:** Đức (Role B) · **Depends on:** 13 · **Parallel-safe:** STEP 14 (C), 16 (A) · **Time:** 60'
- **Actions:**
  1. `adapters/mock_adapter.py` (~30 dòng), lớp `MockAdapter(Adapter)`: `NAME="mock"`, `ADAPTER_VERSION="0.1.0"`; `build_cmd` trả `[sys.executable, "-c", "pass"]` (để **đi qua đường subprocess thật**); `parse_output` đọc `spec["inputs"]["fixture"]` (JSON `{"metrics":{}, "signals":{}, "findings":[], "flow_failed":false}`), ghi `workdir/mock-output.json` (evidence `raw_output`) và `workdir/stdout.log` (evidence `stdout`), trả `ParsedOutput(tokens=0, usd=0.0, …)`. Thiếu file fixture ⟹ `raise AdapterParseError`.
  2. `workers/mock.yaml` theo `workers/_template.yaml`: `name: mock`, `version_probe: "python --version"`, `adapter: "adapters/mock_adapter.py"`, `lanes: [gate, discovery]`, hai capability: `demo.echo` (`oracle_kinds: [trivial, checks]`) và `security.stub` (`[trivial]`), cả hai `verdict_sources: [deterministic_assert]`, `parallel_safe: true`; `requires: {env: [], binaries: []}`; `data_egress: []`.
  3. Fixture: `tests/fixtures/mock_ok.json` = `{"metrics":{"ping_ms":1},"signals":{"checks":{"always_true":true}},"findings":[]}`; `mock_fail.json` = như trên nhưng `"always_true":false`; `mock_finding.json` = `{"metrics":{},"signals":{},"findings":[{"finding_id":"f-m1","title":"Thông báo lỗi khó hiểu","detected_by":"llm_observation","verdict_source":"llm_judgment","confidence":0.6,"rationale":"chuỗi mã kỹ thuật hiện ra cho người dùng"}]}`.
  4. Spec mẫu: `tests/fixtures/task_mock_pass.json` = sao chép `examples/task.k6.json`, đổi: `task_id:"t-e01"`, `capability:"demo.echo"`, `intent:"mock"`, `inputs:{"fixture":"tests/fixtures/mock_ok.json"}`, `oracle:{"kind":"checks","required":["always_true"]}`, `evidence_required:["raw_output","stdout"]`, `budget:{"wallclock_s":30,"tokens":0,"usd":0}`. `task_mock_fail.json` = như trên nhưng fixture `mock_fail.json`, `task_id:"t-e02"`.
  ```powershell
  python -m adapters.mock_adapter --spec tests\fixtures\task_mock_pass.json --out runs\mock_pass.json; "exit=$LASTEXITCODE"
  python -m adapters.mock_adapter --spec tests\fixtures\task_mock_fail.json --out runs\mock_fail.json; "exit=$LASTEXITCODE"
  python tools\validate.py result runs\mock_pass.json runs\mock_fail.json
  git add -A; git commit -m "STEP 15: mock adapter + fixtures"; git push
  ```
- **DoD:** hai lệnh adapter đều `exit=0`; `validate.py` in `PASS` cho **cả hai**; `(Get-Content runs\mock_pass.json | ConvertFrom-Json).status` = `pass` và `(Get-Content runs\mock_fail.json | ConvertFrom-Json).status` = `fail`.
- **Nếu fail:** result mock không validate ⟹ **đây chính là tín hiệu contract có vấn đề** — dừng, báo A, mở lại STEP 07 nếu cần. Đừng thêm trường vào mock cho vừa.

### STEP 16 — `core/plan.py` + `core/runner.py` (DAG, spawn, retry, budget)
- **Owner:** Nghĩa (Role A) · **Depends on:** 13, 14 · **Parallel-safe:** STEP 15, 17, 18 · **Time:** 90'
- **Actions:**
  1. **`core/plan.py`** (~60 dòng): `load_plan(path) -> Plan` (dict `name`, `sut`, `tasks`, `selection` tuỳ chọn, `text`); `PlanError`. Mỗi task trong `plan.yaml` = **task spec trừ `plan_id`, `run_id`, `sut_identity_ref`** (core điền) **cộng 3 khoá chỉ của plan**: `depends_on` (list), `prefer` (list tên worker), `expect_status` (chỉ canary). `resolve(task, run_ctx) -> (spec, plan_only)`: (a) thay `${env.NAME}` (**biến thiếu ⟹ `PlanError` nêu tên biến**) và `${run_id}` trong mọi chuỗi **của task và của khối `sut`**; (b) tách 3 khoá plan-only ra; (c) điền `plan_id`, `run_id`, `sut_identity_ref`; (d) `schema.validate_task` — **lỗi ⟹ `PlanError`** (đây là lỗi của plan, không phải của worker); (e) kiểm `capability` ∈ `schemas/capabilities.json`. `toposort(tasks) -> list[list[task_id]]` (mỗi tầng chạy được cùng lúc); chu trình ⟹ `PlanError`.
  2. **`core/runner.py`** (~80 dòng): `run_all(specs, plan_only, registry, run_dir, parallel=False) -> dict[task_id, result]`. Với mỗi task theo tầng: `registry.pick(...)` — `None` ⟹ `schema.make_result(spec,"skipped",reason)`; task có `depends_on` mà phụ thuộc **không `pass`** ⟹ `skipped` (`"phụ thuộc t-000 không đạt"`); còn lại `subprocess.run([sys.executable,"-m",worker.module], input=json.dumps(spec).encode("utf-8"), capture_output=True, timeout=spec["budget"]["wallclock_s"]+30, env={**os.environ,"PYTHONUTF8":"1"})`. (`+30` chỉ là lưới an toàn cho *adapter* đóng gói kết quả — timeout của *worker* vẫn là `budget.wallclock_s` ở `_base`.) Exit ≠ 0 / stdout không phải JSON / timeout ⟹ `make_result(...,"error",...)`. Có result thì `validate_result` + `check_result_against_spec` — **vi phạm ⟹ `error`** (phòng thủ 2 lớp, ngay cả khi adapter đã kiểm). **Retry:** `status=="error"` **và** `spec.retry.max>0` ⟹ chạy lại **đúng 1 lần**; **không bao giờ retry `fail`**. **Budget:** result có `cost.tokens > budget.tokens` hoặc `cost.usd > budget.usd` ⟹ đổi thành `error` `"vượt budget"` (⚠ kiểm *sau khi chạy*: giới hạn thật lúc chạy là wallclock và `max_steps` của worker — ghi vào Known Limitations). Ghi `runs/<run_id>/specs/<task_id>.json` và `runs/<run_id>/results/<task_id>.json` (UTF-8, `\n`). Song song là việc của STEP 39 — bây giờ `parallel=False` (tuần tự theo tầng).
  3. **`tests/test_runner.py` — đúng 8 test** (adapter giả là script nhỏ trong `tests/fixtures/fake_adapters/`, manifest dựng trong `tmp_path`): (1) `toposort` đúng thứ tự `depends_on`; (2) chu trình ⟹ `PlanError`; (3) `${env.X}` được thay, thiếu biến ⟹ `PlanError` nêu tên; (4) 3 khoá plan-only bị tách và spec vẫn validate; (5) không worker nào ⟹ result `skipped` có lý do; (6) adapter exit 1 ⟹ `error`; (7) `error` được retry **đúng 1 lần**, `fail` **không** bị retry (đếm số lần spawn); (8) result vi phạm spec (gate task `gating=false`) ⟹ đổi thành `error`.
  ```powershell
  pytest tests\test_runner.py -q
  git add -A; git commit -m "STEP 16: core/plan + runner"; git push
  ```
- **DoD:** `pytest tests\test_runner.py -q` in **`8 passed`**.
- **Nếu fail:** quá giờ ⟹ **bỏ DAG song song, giữ tuần tự** (cut #2 ở Phụ lục 4) — và bỏ test (2) nếu topo-sort chưa xong (chạy theo thứ tự khai báo, kiểm `depends_on` xuất hiện trước). **Không** bỏ (7) và (8): chúng là N2/N4 ở dạng test. Bắt gặp mình viết `if worker.name == …` trong `runner.py` ⟹ dừng, đó là V1.

### STEP 17 — `core/verdict.py` (phép gộp — nơi N2 thành một hàm thuần)
- **Owner:** Đức (Role B) · **Depends on:** 12 · **Parallel-safe:** STEP 13, 14, 16, 18 · **Time:** 45'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  ```
  Tạo **F-10** `core/verdict.py` và **F-25** `tests/test_verdict.py` (nguyên văn, đã chạy thử). Đọc kỹ bảng ưu tiên trong docstring: `FAIL > YELLOW > PASS`; `error` ở **gate lane** ⟹ FAIL *theo `status`* (không theo `gating`); `error` ở discovery **không** làm đỏ gate nhưng vẫn vào `banner`; `skipped` ở gate ⟹ YELLOW; **AND rỗng ⟹ FAIL** (không có result `gating` nào = không có gate — chặn kiểu "gate xanh vì không ai được kiểm", chính là V2). Hàm **không** biết `--yellow-exit`: đó là việc của `cli.py` (STEP 20). Ghi chú ở đầu file: *LLM không bao giờ đọc/ảnh hưởng hàm này*.
  ```powershell
  pytest tests\test_verdict.py -q
  git add -A; git commit -m "STEP 17: core/verdict"; git push
  ```
- **DoD:** `pytest tests\test_verdict.py -q` in **`11 passed`**; `Select-String -Path core\verdict.py -Pattern 'k6|schemathesis|deepeval|midscene|openai|llm' -CaseSensitive:$false` chỉ khớp dòng **docstring/comment** (không có mã).
- **Nếu fail:** ai đó đề nghị "AND rỗng thì cho PASS cho gọn" ⟹ **không**: đó là lỗi im lặng nguy hiểm nhất `[arch V2]`. Ai đó muốn để LLM "lật" một verdict ⟹ từ chối, dẫn `[arch §6.3 luật 3, 4]`.

### STEP 18 — `core/signature.py` (SUT identity tối thiểu + run signature)
- **Owner:** Huy (Role C) · **Depends on:** 12 · **Parallel-safe:** STEP 13, 14 (cùng owner), 16, 17 · **Time:** 45'
- **Actions:** viết `core/signature.py` (~50 dòng) với:
  - `plan_id(plan_text) -> "plan-" + sha256(text)[:8]` — text chuẩn hoá `\r\n → \n` trước khi hash.
  - `sut_identity(cfg, root) -> dict` với `cfg = plan["sut"] = {"files": ["toyapp"], "attrs": {"model": "stub-rule-v1"}, "probe_url": "http://127.0.0.1:8000/__qc/config"}` ⟹ `{"code_commit": <git rev-parse HEAD, hoặc "no-git">, "files_sha256": <hash các file dưới `files`, sắp theo đường dẫn, bỏ __pycache__/*.pyc, chuẩn hoá CRLF>, "attrs": {...}, "probe": <JSON GET từ probe_url, hoặc {"error": "..."} nếu lỗi — KHÔNG được làm sập>}`. **`probe_url` là cách đưa cấu hình runtime của SUT (`QC_BUGS`, `QC_LATENCY_MS`) vào identity**: nếu thiếu, hai run với bug bật/tắt có *cùng* `run_signature` mà khác verdict — và `diff_runs` sẽ kết luận nhầm là "bug của hệ thống". `probe_url` là tuỳ chọn (Phase 1 không cần); cơ chế này **không** biết gì về toy app (chỉ GET một URL do plan khai). Đây là bản tối thiểu của SUT identity `[R2 S7.2]`; **chưa** có corpus/judge-version (ghi vào Known Limitations).
  - `sut_id(identity) -> "sut-" + sha256(json.dumps(identity, sort_keys=True))[:8]`; `write_sut_identity(identity, run_dir)` ghi `runs/<run_id>/sut_identity.json`.
  - `run_signature(plan_id, sut_id, results, specs) -> hex` = `sha256` của JSON chuẩn hoá `{plan_id, sut_id, workers: sorted(set((worker.name, worker.version, worker.adapter_version))), seeds: sorted((task_id, determinism.seed))}` `[arch §6.4 cơ chế 1]`, **chỉ tính các task thuộc gate lane** (tra `specs[task_id]["lane"]`). Lý do: discovery lane nằm **ngoài đường verdict** nên không được làm đổi chữ ký của phần tất định — nhờ vậy demo có thể chạy lần 2 **chỉ các task gate** (rẻ) mà vẫn so được với lần 1 đầy đủ (STEP 41, 57). `--only` không đi vào signature. Task gate `skipped` mang `worker.name="unknown"` ⟹ signature **khác** run có worker chạy thật ⟹ `diff_runs` báo *"không so được"* (đúng ý đồ).
  - `tests/test_signature.py` — **7 test:** (1) cùng đầu vào ⟹ cùng signature; (2) đổi `adapter_version` của worker **gate** ⟹ đổi; (3) đổi `worker.version` ⟹ đổi; (4) đổi nội dung plan ⟹ `plan_id` đổi; (5) plan chỉ khác CRLF/LF ⟹ `plan_id` **không** đổi; (6) sửa một file dưới `files` ⟹ `sut_id` đổi; (7) thêm một result **discovery** (worker mới) ⟹ signature **không** đổi.
  ```powershell
  pytest tests\test_signature.py -q
  git add -A; git commit -m "STEP 18: core/signature"; git push
  ```
- **DoD:** `pytest tests\test_signature.py -q` in **`7 passed`**; `python -c "from core.signature import sut_identity; import json; print(json.dumps(sut_identity({'files':['toyapp'],'attrs':{}}, __import__('pathlib').Path('.')), indent=1))"` in ra `code_commit` có 40 ký tự hex.
- **Nếu fail:** không có git trên máy ⟹ `code_commit="no-git"` (không được làm sập). `files_sha256` khác nhau giữa 3 máy dù cùng commit ⟹ gần như chắc chắn là CRLF (Troubleshooting #4): kiểm bước chuẩn hoá.

### STEP 19 — `core/report.py` (3 mục tách theo `verdict_source` — "không bao giờ cắt" #2)
- **Owner:** Đức (Role B) · **Depends on:** 17 · **Parallel-safe:** STEP 13, 14, 16, 18 (không đụng file) · **Time:** 75'
- **Actions:** `render(ctx) -> (md, report_json)` và `write(ctx, run_dir)`; `ctx` là dataclass `RunContext` trong chính file này: `run_id, plan_id, plan_name, plan_path, plan_text, sut_id, run_signature, generated_at (str, do gọi truyền vào để test được), wallclock_s, specs, results, gate (GateVerdict), canary=[], tickets_draft=[]`.
  **Tiêu đề phải đúng từng chữ** (test dò chuỗi):
  ```
  # QC Gate Report — run r-0001
  plan: plans/demo.yaml (plan-1a2b3c4d) · SUT: sut-9f2c0a11 · 2026-09-19 14:32
  wallclock 3m12s · LLM tokens: 118430 (tasks: t-101) · cost $0.31
  ## ⚠ SKIPPED / ERROR — ĐỌC TRƯỚC        <- CHỈ in khi banner không rỗng, và PHẢI đứng TRƯỚC "## VERDICT"
  ## VERDICT: ❌ FAIL                        <- ✅ PASS | 🟡 YELLOW
  ## 1. DETERMINISTIC ASSERT (chặn gate)
  ## 2. LLM JUDGMENT (KHÔNG chặn gate — chỉ tham khảo)
  ## 3. HEURISTIC / DISCOVERY (KHÔNG chặn gate)
  ## 4. SKIPPED / ERROR
  ## 5. AUDIT
  ```
  - Mục 1: bảng `task | worker | capability | status | chi tiết` cho result `gating=true` (chi tiết = tiêu đề finding đầu tiên nếu fail, không thì tối đa 2 cặp `metric=giá trị`), rồi **đúng một dòng** `→ gate_verdict = pass AND fail AND pass = **FAIL**` (liệt kê `value` từng task, để tính tay được).
  - Mục 2: bảng `task | metric | điểm | baseline | delta | confidence` cho **mọi finding `llm_judgment`** ở mọi lane. `baseline` đọc từ `baselines/geval.json` (`{"GEval": 0.78}`, **viết tay** — PoC chưa có baseline store `[arch D5]`); không có ⟹ `—`. Ghi câu cố định dưới bảng: *"Các số này không cộng vào 'pass rate' và không chặn gate."*
  - Mục 3: mỗi result `gating=false` của lane discovery: dòng `worker · bước · $ · token`, rồi từng finding `[f-3] tiêu đề` + `detected_by: … ← TẤT ĐỊNH` (nếu `deterministic_assert`) hoặc `← LLM, confidence 0.62`; có `promote_candidate` ⟹ thêm dòng `→ ứng viên promote: <capability>`; có `ctx.canary` ⟹ dòng `CANARY: …`.
  - Mục 5: mỗi task gating: `oracle: <plan_path>:L<dòng>` (tìm dòng đầu chứa `task_id: <id>` trong `plan_text`), `evidence: <uri> (sha256 <8 ký tự đầu>…)`, `replay: <replay_cmd hoặc —>`.
  - `report.json` **đúng các khoá này**: `run_id, plan_id, run_signature, sut_id, gate_verdict, exit_code, deterministic_view, banner, canary, tickets_draft, details`. `deterministic_view` = danh sách, **chỉ result `gating=true`**, sắp theo `task_id`, mỗi phần tử `{task_id, worker, capability, status, value, verdict_source, gating}` — **không** metric, không cost, không hash (đó là lý do #4 đạt được, xem mục 0.3 #4). `details` chứa phần còn lại (`generated_at`, `wallclock_s`, từng result: `status/cost/metrics`). Ghi file UTF-8, xuống dòng `\n`.
  - **`tests/test_report.py` — 6 test:** (1) đủ 5 heading, đúng thứ tự; (2) đổi một finding `llm_judgment` thành điểm rất thấp ⟹ dòng `## VERDICT` **không đổi**; (3) có `skipped` ⟹ heading banner **đứng trước** `## VERDICT`; (4) dòng `→ gate_verdict =` khớp `gate.value`; (5) `deterministic_view` **không** chứa result `gating=false` và không có khoá `metrics`/`cost`; (6) ghi/đọc lại file với chuỗi tiếng Việt và emoji không lỗi.
  ```powershell
  pytest tests\test_report.py -q
  git add -A; git commit -m "STEP 19: core/report"; git push
  ```
- **DoD:** `pytest tests\test_report.py -q` in **`6 passed`**.
- **Nếu fail:** quá giờ ⟹ làm mục 1, 2, 3 và `report.json` **trước**; mục 5 (AUDIT) để sau — nhưng **mục 1/2/3 tách nhau là "không bao giờ cắt"** `[R5 4.2]`. Nếu ai đó muốn gộp mục 1 và 2 vào một bảng "cho gọn" ⟹ từ chối: đó là chỗ N2 nhìn thấy được.

### STEP 20 — 🛑 `core/cli.py` + `orchestrator.py` + plan demo + chạy end-to-end với mock
- **Owner:** Nghĩa (Role A) · **Depends on:** 15, 16, 17, 18, 19 · **Parallel-safe:** none (đây là điểm ghép) · **Time:** 60'
- **Actions:**
  1. `orchestrator.py` (root), 4 dòng: `import sys` · `from core.cli import main` · `if __name__ == "__main__":` · `    sys.exit(main(sys.argv[1:]))`.
  2. `core/cli.py` (~80 dòng), `main(argv) -> int`: tham số `--plan` (bắt buộc **trừ khi** có `--rerender`) · `--only t-a,t-b` (chạy tập con) · `--yellow-exit N` (mặc định `0`) · `--runs-dir` (mặc định `$QC_RUNS_DIR` hoặc `runs`) · `--base` (dành cho STEP 49, bây giờ bỏ qua) · `--rerender RUN_DIR` (**không chạy worker**: đọc `RUN_DIR/specs` + `RUN_DIR/results`, tính lại verdict, ghi `RUN_DIR/report.rerender.md` — dùng để chứng minh tiêu chí #3 ở STEP 40). Luồng: `load_plan` → `run_id = r-NNNN` (đếm thư mục có sẵn, **4 chữ số**) → `plan_id`, `sut_identity` ghi ra file → `resolve` từng task → `registry.load("workers")` + `probe` → `run_all` → `run_signature` → `gate_verdict` → `report.write` → in `report.md` ra console. **Exit code:** `PASS`→0 · `YELLOW`→`--yellow-exit` · `FAIL`→1 · `PlanError`/lỗi nội bộ của orchestrator→**3** (lỗi của *hệ thống*, tách khỏi verdict). `--only` mà chọn thiếu task được `depends_on` ⟹ `PlanError`.
  3. `plans/demo.yaml`:
     ```yaml
     plan_version: 1
     name: demo-mock
     sut: {files: ["toyapp"], attrs: {model: "stub-rule-v1"}}
     tasks:
       - task_id: t-e01
         capability: demo.echo
         lane: gate
         intent: "Worker giả, oracle trivial: chứng minh đường ống thông"
         target: {kind: none, base_url: "http://127.0.0.1:8000"}
         inputs: {fixture: "tests/fixtures/mock_ok.json"}
         oracle: {kind: trivial}
         expected_result_kind: verdict
         budget: {wallclock_s: 30, tokens: 0, usd: 0}
         determinism: {seed: null, replayable: true}
         evidence_required: [raw_output, stdout]
         retry: {max: 1, on: [error]}
       - task_id: t-e02
         capability: demo.echo
         lane: gate
         intent: "Worker giả, oracle checks đạt"
         target: {kind: none, base_url: "http://127.0.0.1:8000"}
         inputs: {fixture: "tests/fixtures/mock_ok.json"}
         oracle: {kind: checks, required: [always_true]}
         expected_result_kind: verdict
         budget: {wallclock_s: 30, tokens: 0, usd: 0}
         determinism: {seed: null, replayable: true}
         evidence_required: [raw_output, stdout]
         retry: {max: 1, on: [error]}
       - task_id: t-e03
         capability: demo.echo
         lane: discovery
         intent: "Worker giả trả một finding llm_judgment (không chặn)"
         target: {kind: none, base_url: "http://127.0.0.1:8000"}
         inputs: {fixture: "tests/fixtures/mock_finding.json"}
         oracle: {kind: trivial}
         expected_result_kind: candidate_finding
         budget: {wallclock_s: 30, tokens: 0, usd: 0}
         determinism: {seed: null, replayable: false}
         evidence_required: [raw_output]
         retry: {max: 0, on: []}
     ```
     `plans/demo_fail.yaml` = sao chép, đổi `t-e02` sang `inputs.fixture: "tests/fixtures/mock_fail.json"` (gate phải **đỏ** do `always_true=false`).
  4. `tests/test_cli.py` — 4 test bằng `subprocess`: (1) `demo.yaml` ⟹ exit 0, có `runs/r-NNNN/report.md`; (2) `demo_fail.yaml` ⟹ exit 1; (3) plan sai (`capability` lạ) ⟹ exit **3**; (4) `--only t-e01` ⟹ `report.json` có đúng 1 phần tử trong `deterministic_view`. Dùng `--runs-dir` trỏ vào `tmp_path`.
  ```powershell
  cd $HOME\qc-agent-poc; . .\scripts\env.ps1
  python orchestrator.py --plan plans/demo.yaml; "exit=$LASTEXITCODE"
  python orchestrator.py --plan plans/demo_fail.yaml; "exit=$LASTEXITCODE"
  python tools\validate.py result (Get-ChildItem runs\r-0001\results\*.json).FullName
  pytest -q
  git add -A; git commit -m "STEP 20: cli + demo plans (walking skeleton)"; git push
  ```
- **DoD (🛑 cổng của Phase 1):** (1) lệnh đầu in report có đủ 5 heading `## 1.` … `## 5.` và `exit=0`; (2) lệnh thứ hai `exit=1`; (3) `validate.py` in `PASS` cho **cả 3** file result của `r-0001` (= đủ field QRS); (4) `pytest -q` toàn repo **không đỏ**; (5) `Select-String -Path core\*.py -Pattern 'schemathesis|k6|deepeval|midscene' -CaseSensitive:$false` **không in gì**.
- **Nếu fail:** đây là điểm quan trọng nhất của cả sprint. **Cuối slot 2 mà chưa có (1)** ⟹ *cắt ngay* `[R5 P4.1 #2]`: A bỏ `plan-gen`, hard-code plan, dừng nhận việc mới; **B và C đỡ `core/` theo `core/README.md`** (STEP 21 — nếu README chưa có, A viết trong 15' đầu). Không ai chuyển sang STEP 27+ khi chưa qua cổng này.

### STEP 21 — `core/README.md` (≤ 1 trang: chạy, thêm worker, ai đỡ được gì)
- **Owner:** Nghĩa (Role A) · **Depends on:** 20 · **Parallel-safe:** STEP 22, 23, 24, 25 · **Time:** 30'
- **Actions:** viết `core/README.md` ≤ 60 dòng, **đúng 4 tiêu đề**: `## Chạy` (3 lệnh: bootstrap, toyapp start, orchestrator) · `## Thêm worker` (đúng 5 bước như bảng ở [arch §8.2]: `workers/<tên>.yaml`, `schemas/<cap>.inputs.json` *(tuỳ chọn ở PoC — D-08)*, `adapters/<tên>_adapter.py` kế thừa `_base` chỉ override `build_cmd` + `parse_output`, `oracle/<kind>.py` nếu `oracle.kind` mới, thêm task vào `plan.yaml`; và **"không sửa `core/`"**) · `## Ai đỡ được gì` (bảng: nếu A kẹt thì B nhận `runner.py`+`cli.py`, C nhận `plan.py`+`schema.py`; kèm cách chạy test từng file) · `## Cấm` (5 gạch đầu dòng: LLM trong `core/` · `if worker == …` trong `core/` · trường riêng của một worker trong schema · retry `fail` · sửa file contract sau freeze).
- **DoD:** `(Get-Content core\README.md | Measure-Object -Line).Lines` ≤ 60 **và** `(Select-String -Path core\README.md -Pattern '^## (Chạy|Thêm worker|Ai đỡ được gì|Cấm)$').Count` = **4**.
- **Nếu fail:** không kịp ⟹ viết đúng `## Ai đỡ được gì` trước (đó là phần cứu cả nhóm nếu A trượt).

### STEP 22 — Toy frontend: trang `/` với BUG-2 và telemetry
- **Owner:** Đức (Role B) · **Depends on:** 11 · **Parallel-safe:** STEP 12–21 (A, C không đụng `toyapp/static/`) · **Time:** 60'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  ```
  Thay `toyapp/static/index.html` bằng **F-17**. Trang có form (`Tiêu đề`, `Nội dung`, nút `Thêm`) + danh sách, mỗi dòng một nút `Xoá`. **BUG-2** nằm ở dòng `if (!window.QC_BUGS.includes('2')) await render();` — bật bug thì bấm `Xoá` xong danh sách **không** vẽ lại. Trang tự gửi *telemetry* `render_done` / `delete_clicked` / `console_error` tới `/__qc/events` — đây là nguồn **tín hiệu ngầm tất định** cho STEP 28 (không dùng LLM để nhận ra "danh sách kẹt").
  ```powershell
  .\scripts\toyapp.ps1 start
  Invoke-RestMethod -Method Delete http://127.0.0.1:8000/__qc/events | Out-Null
  # Mở http://127.0.0.1:8000 bằng Chrome: thêm 1 note, rồi bấm "Xoá" (nhìn thấy note VẪN CÒN trong danh sách)
  ((Invoke-RestMethod http://127.0.0.1:8000/__qc/events).type)[-1]
  .\scripts\toyapp.ps1 start -Bugs none
  Invoke-RestMethod -Method Delete http://127.0.0.1:8000/__qc/events | Out-Null
  # Lặp lại đúng thao tác trên (lần này note BIẾN MẤT sau khi Xoá)
  ((Invoke-RestMethod http://127.0.0.1:8000/__qc/events).type)[-1]
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 22: toy frontend + telemetry"; git push
  ```
- **DoD:** dòng `[-1]` thứ nhất in **`delete_clicked`** (bug bật: không có `render_done` sau khi xoá); dòng thứ hai in **`render_done`** (bug tắt). Bằng mắt: bug bật thì note còn nguyên trên trang sau khi bấm Xoá.
- **Nếu fail:** không thấy sự kiện nào ⟹ `F12` xem lỗi JS/CORS; `/__qc/events` phải trả 200. Nếu BUG-2 không tái hiện ⟹ kiểm `window.QC_BUGS` trong console có chứa `"2"`.

### STEP 23 — Midscene "hello world" + ma trận exit code (khám phá, KHÔNG viết adapter)
- **Owner:** Đức (Role B) · **Depends on:** 22, 01, 04 · **Parallel-safe:** STEP 24, 25 (C), 26 (A) · **Time:** 90' (timebox cứng)
- **Actions:** mục tiêu là **biết Midscene thật sự xuất gì** để adapter (STEP 28) dựa trên bằng chứng — RUN 1–5 mới verify *tài liệu*, chưa chạy `[R3 (a)#9]`.
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  .\scripts\toyapp.ps1 start
  npx @midscene/cli --help          # ⚠ ghi lại: tên lệnh, cách truyền file YAML, cờ --summary, --headed
  ```
  Mở trang runner mà RUN 3 đã dẫn (`midscenejs.com/yaml-script-runner`), copy ví dụ tối thiểu, đổi `url` sang `http://127.0.0.1:8000`. Hình dạng *dự kiến* (⚠ **tên khoá YAML lấy từ trí nhớ về doc, không phải từ RUN 1–5** — sửa theo doc thật):
  ```yaml
  web:
    url: http://127.0.0.1:8000
  tasks:
    - name: them-va-xoa-note
      flow:
        - aiAct: 'nhập "Họp" vào ô Tiêu đề, nhập "Chốt scope" vào ô Nội dung, bấm nút Thêm'
        - aiAct: 'bấm nút Xoá của note đầu tiên'
  ```
  Tạo **4 flow** trong `midscene/` và chạy từng cái (`⚠` thứ tự đối số theo `--help`):
  ```powershell
  npx @midscene/cli midscene\hello.yaml                 --summary runs\ms_pass.json;      "pass exit=$LASTEXITCODE"
  npx @midscene/cli midscene\hello_missing.yaml         --summary runs\ms_missing.json;   "missing_element exit=$LASTEXITCODE"   # bấm nút "Thanh toán" (KHÔNG tồn tại)
  npx @midscene/cli midscene\hello_assert_false.yaml    --summary runs\ms_assert.json;    "aiassert_false exit=$LASTEXITCODE"   # aiAssert một điều sai: "trang có nút Thanh toán"
  $k = $env:MIDSCENE_MODEL_API_KEY; Remove-Item Env:MIDSCENE_MODEL_API_KEY
  npx @midscene/cli midscene\hello.yaml                 --summary runs\ms_nokey.json;     "no_key exit=$LASTEXITCODE"
  $env:MIDSCENE_MODEL_API_KEY = $k
  ```
  Lưu mẫu: `Copy-Item runs\ms_pass.json tests\samples\midscene_summary.pass.json` (tương tự `fail_missing_element`, `fail_aiassert`; chạy `no_key` thì lưu stdout+stderr vào `tests\samples\midscene_stdout.no_key.txt`). Mở **một** file summary, ghi vào `docs/decisions.md`: khoá nào cho biết *từng bước pass/fail*, có đường dẫn screenshot không, có số token không. Điền bảng (đúng 4 hàng, cột đầu **đúng chữ này**):

  | case | exit code | có file summary? | khoá/giá trị phân biệt pass–fail | ghi chú |
  |---|---|---|---|---|
  | pass | | | | |
  | missing_element | | | | |
  | aiassert_false | | | | |
  | no_key | | | | |

  Dưới bảng, một dòng kết luận cho `[R4 double-check #3, #4, #5]`: *`--summary` có đủ trường để sinh `findings[]` không · `aiAssert` sai có làm exit ≠ 0 không*. Đặt tiêu đề bảng là `## Midscene exit-code matrix`. Cũng ghi giá trị `MIDSCENE_MODEL_FAMILY` đã dùng và có cần Chrome/`--headed` không.
  ```powershell
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 23: midscene hello + exit-code matrix + samples"; git push
  ```
- **DoD:** (1) `(git ls-files tests/samples | Select-String midscene).Count` ≥ **4**; (2) `(Select-String -Path docs\decisions.md -Pattern '^\| (pass|missing_element|aiassert_false|no_key) \|').Count` = **4** và cột `exit code` **đã điền số** ở cả 4 hàng; (3) `docs/decisions.md` có dòng chữ `## Midscene exit-code matrix`.
- **Nếu fail:** **hết 90' mà chưa chạy được** ⟹ dừng, ghi nguyên văn lỗi; **không** cố thêm. Chuyển sang kịch bản `mock`: viết tay `tests/samples/midscene_summary.fixture.json` **đặt tên có chữ `fixture`, ghi rõ trong file và trên slide là mock** `[R5 4.3]`, adapter (STEP 28) dựng trên cấu trúc giả định đó và **đánh dấu ⚠ chưa xác nhận với Midscene thật**. Lỗi hay gặp: tải Chrome/puppeteer bị chặn (Troubleshooting #4) · VLM không nhận ảnh (STEP 01 mới chỉ thử văn bản) · `429` (Troubleshooting #3).

### STEP 24 — Schemathesis "hello world": khám phá cờ, hiệu chuẩn BUG-1, ma trận exit code
- **Owner:** Huy (Role C) · **Depends on:** 11 · **Parallel-safe:** STEP 22, 23 (B), 21 (A) · **Time:** 60'
- **Actions:** RUN 1–5 chỉ biết lệnh `st run <url> --checks all` (`[R5 replay_cmd]`) — mọi cờ khác là ⚠.
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  st --version
  st run --help | Select-String -Pattern "seed|example|checks|junit|report|endpoint|include|exclude|phases|workers"
  .\scripts\toyapp.ps1 start -Bugs 1
  st run http://127.0.0.1:8000/openapi.json --checks all; "bug_on exit=$LASTEXITCODE"
  ```
  Từ đầu ra của `--help`, **chọn và ghi vào `docs/decisions.md`**: (a) cờ giới hạn số example (mục tiêu ~25/endpoint `[R5 2.2]`); (b) cờ **ghim seed** (⚠ nếu không có ⟹ tiêu chí #4 phải hạ chuẩn `[R5 (a)#5]` — nói ngay với A); (c) cờ **giới hạn danh sách check** — RUN 5 chỉ muốn *schema conformance + không 5xx*; nếu `--checks all` cho **false positive** ngoài BUG-1 thì thu hẹp về đúng các check tương ứng; (d) cờ xuất **báo cáo máy đọc được** (JUnit XML hoặc định dạng khác) và (e) cờ lọc endpoint (`-E` xuất hiện ở replay_cmd của RUN 5).
  **Hiệu chuẩn** — *đây là bước quyết định tiêu chí #4 có đạt được không*: chạy **5 lần** mỗi cấu hình:
  ```powershell
  1..5 | ForEach-Object { st run http://127.0.0.1:8000/openapi.json --checks all; "bug_on run $_ exit=$LASTEXITCODE" }
  .\scripts\toyapp.ps1 start -Bugs none
  1..5 | ForEach-Object { st run http://127.0.0.1:8000/openapi.json --checks all; "bug_off run $_ exit=$LASTEXITCODE" }
  .\scripts\toyapp.ps1 stop
  st run http://127.0.0.1:8000/openapi.json --checks all; "server_down exit=$LASTEXITCODE"
  ```
  Khối trên dùng `--checks all` vì đó là lệnh duy nhất RUN 1–5 có. **Nếu DoD (1) hoặc (2) không đạt**, thêm vào *cả ba* lệnh các cờ đã chọn ở (a)–(c) (số example, seed, danh sách check) — tên cờ là kết quả đọc `--help` ở trên, RUN 1–5 chưa chạy Schemathesis nên không viết sẵn được — rồi chạy lại đúng khối này. Lưu mẫu vào `tests/samples/`: `st_bug_on.txt` + tệp báo cáo máy đọc được của lần bắt được lỗi, `st_bug_off.txt` + tệp báo cáo sạch, `st_server_down.txt`. Điền bảng `## Schemathesis exit-code matrix` trong `docs/decisions.md` (hàng `bug_on`, `bug_off`, `server_down`; cột `exit code`, `có báo cáo?`, `chỗ nào nói check nào hỏng`).
  ```powershell
  git add -A; git commit -m "STEP 24: schemathesis hello + calibration + samples"; git push
  ```
- **DoD:** (1) **5/5** lần `bug_on` có exit ≠ 0 **và** đầu ra nhắc tới lỗi 500 ở `GET /notes/{note_id}`; (2) **5/5** lần `bug_off` có exit **0** (không false positive — nếu không thì "gate xanh khi không có bug" là chuyện không có thật); (3) `git ls-files tests/samples | Select-String st_` ≥ **3** dòng; (4) `docs/decisions.md` có bảng với 3 hàng `bug_on|bug_off|server_down` đã điền số.
- **Nếu fail:** **(a) bug_on không 5/5** ⟹ Hypothesis không luôn sinh id đủ dài: hạ ngưỡng — sửa **mặc định** `QC_LONG_ID_LEN` trong `toyapp/app.py` từ `64` xuống `32`, rồi `16`, `8` cho tới khi 5/5 (đây là **hiệu chuẩn lỗi cài sẵn**, không phải gian lận, nhưng **phải nói với mentor lỗi là cài sẵn** `[R5 (a)#6]`; commit thay đổi + báo B, A vì `toyapp/` đổi ⟹ `sut_id` đổi). **(b) bug_off có false positive** ⟹ thu hẹp danh sách check (c). **(c) không tải/cài được Schemathesis trên Windows** ⟹ báo A ngay: Schemathesis là worker PoC dễ nhất `[R4 double-check #6]`; phương án tối thiểu: `tests/samples/` viết tay + đánh dấu **mock**.

### STEP 25 — `adapters/schemathesis_adapter.py` (adapter THẬT đầu tiên — là khuôn cho hai cái sau)
- **Owner:** Huy (Role C) · **Depends on:** 13, 24 · **Parallel-safe:** STEP 23 (B), 26 (A, nhưng 26 chờ 25) · **Time:** 90'
- **Actions:**
  1. `workers/schemathesis.yaml` từ `_template.yaml`: `name: schemathesis`, `version_probe: "st --version"`, `adapter: "adapters/schemathesis_adapter.py"`, `lanes: [gate]`, capability `api.property` (`oracle_kinds: [checks]`, `verdict_sources: [deterministic_assert]`, `parallel_safe: true`), `requires: {env: [APP_BASE_URL], binaries: [st]}`, `data_egress: []`.
  2. `tests/fixtures/task_st.json`: spec `t-001`, `capability:"api.property"`, `lane:"gate"`, `target:{"kind":"http_service","base_url":"http://127.0.0.1:8000"}`, `inputs:{"schema_url":"http://127.0.0.1:8000/openapi.json","checks":[<các check đã chọn ở STEP 24>],"max_examples":25,"seed":1337}`, `oracle:{"kind":"checks","required":[<các check đã chọn>]}`, `expected_result_kind:"verdict"`, `budget:{"wallclock_s":120,"tokens":0,"usd":0}`, `determinism:{"seed":1337,"replayable":true}`, `evidence_required:["raw_output","stdout"]`, `retry:{"max":1,"on":["error"]}`.
  3. `adapters/schemathesis_adapter.py` (~90 dòng), kế thừa `Adapter`; **chỉ hai hàm**:
     - `build_cmd`: ráp lệnh `st run …` từ `inputs` theo **cờ đã ghi ở STEP 24** (không đoán cờ mới); ghi báo cáo máy đọc được vào `workdir`. `replay_cmd` = chuỗi lệnh đã chạy **nguyên văn**.
     - `parse_output`: (a) **không có tệp báo cáo ⟹ `raise AdapterParseError`** (worker hỏng ≠ test fail) — **kể cả khi exit ≠ 0**; (b) đọc báo cáo, dựng `signals["checks"] = {tên_check: True/False}` **cho đúng các check trong `oracle.required`**; mỗi thất bại phải quy được về một check đã biết, **quy không được ⟹ `AdapterParseError`** (không đoán); parser khai `PARSER_VERSION = "1"` trong `adapter_notes`; (c) **đối chiếu tín hiệu**: exit ≠ 0 mà báo cáo không có thất bại nào (hoặc ngược lại) ⟹ `AdapterParseError("mâu thuẫn exit code/báo cáo")` `[arch §5.3 bước 3]`; (d) **không so sánh gì** — việc đó của `oracle/checks.py`.
  4. `tests/test_schemathesis_adapter.py` — **5 test**, dựng `CompletedProcess` giả và đọc `tests/samples/st_*`, không chạy `st`: (1) mẫu `bug_on` ⟹ `signals["checks"]` có check 5xx = `False`; (2) mẫu `bug_off` ⟹ mọi check `True`; (3) mẫu `server_down` (không báo cáo) ⟹ `AdapterParseError`; (4) exit ≠ 0 nhưng báo cáo sạch ⟹ `AdapterParseError`; (5) báo cáo mà có thất bại không quy về check nào ⟹ `AdapterParseError`.
  ```powershell
  pytest tests\test_schemathesis_adapter.py -q
  .\scripts\toyapp.ps1 start -Bugs 1
  python -m adapters.schemathesis_adapter --spec tests\fixtures\task_st.json --out runs\st_bug.json; "exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 start -Bugs none
  python -m adapters.schemathesis_adapter --spec tests\fixtures\task_st.json --out runs\st_clean.json; "exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 stop
  python -m adapters.schemathesis_adapter --spec tests\fixtures\task_st.json --out runs\st_down.json; "exit=$LASTEXITCODE"
  python tools\validate.py result runs\st_bug.json runs\st_clean.json runs\st_down.json
  git add -A; git commit -m "STEP 25: schemathesis adapter"; git push
  ```
- **DoD (từng worker, `[RUN 7]`):** `pytest tests\test_schemathesis_adapter.py -q` in **`5 passed`**; ba lệnh adapter đều `exit=0`; `validate.py` in `PASS` cho **cả ba**; và `status` lần lượt là **`fail`** (`st_bug.json`), **`pass`** (`st_clean.json`), **`error`** (`st_down.json`) — kiểm bằng `(Get-Content runs\st_bug.json | ConvertFrom-Json).status`.
- **Nếu fail:** không thể phân biệt `fail` với `error` từ output của tool ⟹ **báo A**, đây là quyết định phải đọc doc từng tool `[R5 1.3]`; mặc định an toàn: **`error`**. Tuyệt đối không "để LLM đọc output giúp" (`[arch D3]`, tầng 4).

### STEP 26 — A review adapter Schemathesis (checklist 5 câu)
- **Owner:** Nghĩa (Role A) · **Depends on:** 25 · **Parallel-safe:** STEP 23, 27 (cùng owner nên tuần tự) · **Time:** 30'
- **Actions:** tạo **F-23** `tools/review_adapter.py`, rồi chạy trên kết quả C đã sinh:
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  python tools\review_adapter.py --result runs\st_bug.json --spec tests\fixtures\task_st.json --crash runs\st_down.json; "exit=$LASTEXITCODE"
  ```
  (Cần tệp `runs\st_bug.json` và `runs\st_down.json`: A tự chạy lại hai lệnh của STEP 25 nếu chưa có — `runs/` không nằm trong git.) Đọc **mã** của adapter thêm 2 phút: `Select-String -Path adapters\schemathesis_adapter.py -Pattern 'assert |threshold|<|>|passed|failed'` — adapter **không được** có phép so sánh ngưỡng; nếu có, nó đang làm việc của `oracle/`.
- **DoD:** `review_adapter.py` in **5 dòng `PASS`**, `exit=0`; và A ghi vào PR/commit: `STEP 25: reviewed by A, 5/5`.
- **Nếu fail:** câu nào `FAIL` ⟹ trả lại C với dòng lỗi nguyên văn; **không merge**. Câu 2 (crash → error) `FAIL` là nghiêm trọng nhất: adapter đang biến worker hỏng thành `fail` hoặc `pass`. Câu 5 `FAIL` ⟹ V1 đã bắt đầu — dừng, xử lý theo Troubleshooting mục "contract bị ép méo".

> **Cổng cuối slot 2:** chỉ **STEP 20** bắt buộc. STEP 23, 25, 26 được **trượt sang đầu slot 3** (slot 3 của B còn ~1.5h, của A ~0.8h dư). Nếu STEP 23 (Midscene hello) trượt quá **nửa slot 3** ⟹ kích hoạt kịch bản mock của STEP 23 và báo cả nhóm.

---

# PHASE 2 — TÍCH HỢP WORKER THẬT (song song 3 nhánh)

**Quy tắc chung cho mọi worker** (đúng thứ tự, `[RUN 7]`): **khám phá tool** (hello + ma trận exit code + lưu mẫu vào `tests/samples/`) → **viết adapter theo mẫu đã lưu** → **test adapter ĐỘC LẬP bằng `--spec/--out`** (đừng debug adapter và orchestrator cùng lúc) → mới **cắm vào `plan.yaml`**.
**DoD từng worker:** adapter trả QRS **hợp lệ cho cả ca `pass`, ca `fail`, và ca `error`** (crash) — không chỉ ca vui.
**Thứ tự có chủ ý:** worker **khó nhất trước** (Midscene: STEP 23 → 28 → 29) vì V1 (contract bị ép méo) chỉ lộ khi worker khó va vào schema `[R5 P3 slot 3]`.

**Cài đặt và pin từng worker** — cài **một lần ở STEP 04**; bảng này chỉ chỉ ra *ở đâu* và *pin bằng gì*:

| Worker (roster PoC của RUN 3) | Lệnh cài | Pin phiên bản bằng | `doctor.ps1` kiểm | Hello-world | Adapter | Test độc lập (ca pass / fail / error) |
|---|---|---|---|---|---|---|
| **Schemathesis** | `pip install schemathesis` (dòng trong `requirements.txt`) | `requirements.lock` (`pip freeze`) | `st --version` | STEP 24 | STEP 25 | STEP 25 (3 ca, có `--spec/--out`) |
| **k6** | `winget install k6 --source winget` | `.k6-version` (chuỗi `k6 vX.Y.Z` thực tế cài được; `doctor` so khớp) | `k6 version` | STEP 30 | STEP 31 | STEP 31 (3 ca) |
| **DeepEval** | `pip install deepeval` (trong `requirements.txt`) | `requirements.lock` | `import deepeval` | STEP 35 (+36 collect) | STEP 37 | STEP 37 (3 ca) |
| **Midscene CLI** | `npm install --save-dev --save-exact @midscene/cli` | `package-lock.json` (+ `--save-exact`) | có thư mục `node_modules\@midscene\cli` | STEP 23 | STEP 28 | STEP 29 (3 ca) |

**"Pin" ở đây nghĩa là: đóng băng đúng phiên bản *cài được và chạy được ngày 1*** (đọc từ `pip freeze` / `package-lock.json` / `k6 version`), không phải một con số viết sẵn — RUN 1–5 **không có** số phiên bản đáng tin nào (các số như `k6 1.3.0`, `midscene-cli 1.7.2` trong ví dụ JSON của kiến trúc chỉ là minh hoạ). Sau STEP 07, ba người dùng cùng phiên bản nhờ `ENV-FINGERPRINT`.

## Slot 3 — Ngày 2 sáng

### STEP 27 — `oracle/threshold.py` + `oracle/signals.py` (bộ so sánh dùng chung)
- **Owner:** Nghĩa (Role A) · **Depends on:** 13 · **Parallel-safe:** STEP 23, 25, 28 (B, C không sửa `oracle/`) · **Time:** 60'
- **Actions:** hai module tự đăng ký (`@register`) — **không sửa `oracle/__init__.py`** (nó tự nạp).
  - **`threshold`**: `oracle = {"kind":"threshold","assertions":[{"metric":"http_req_duration.p95","op":"<","value":300,"unit":"ms"}, …]}`; `metrics` là dict **phẳng**; `op ∈ {<,<=,>,>=,==,!=}`. Thiếu metric ⟹ `raise OracleError` (chưa đo thì không được phán). Có assertion sai ⟹ `value="fail"` + **mỗi assertion sai một finding** `{"finding_id":"f-thr-<metric>","title":"<metric> = <giá trị> vi phạm <op> <value>","detected_by":"threshold:<metric>","verdict_source":"deterministic_assert","confidence":None,"severity_hint":"high"}`; không sai ⟹ `value="pass"`.
  - **`signals`**: `oracle = {"kind":"implicit_signals","signals":[…tên…],"llm_observations_allowed":true}`; `signals["detected"] = [{"name","title","evidence":[…đã băm…]?,"promote_candidate":{…}?}]` do **adapter** cấp. Oracle **chỉ** giữ tín hiệu có tên trong `oracle.signals` (tên lạ bị bỏ, ghi vào `notes`), biến mỗi cái thành finding `deterministic_assert` `detected_by:"implicit_signal:<name>"`, `severity_hint` theo bảng cố định trong code `{http_5xx: high, dom_unchanged: high, element_not_found: medium, console_error: medium}`, gộp `evidence`/`promote_candidate` nếu có. **`value=None`** (oracle discovery không phán pass/fail). `[R4 S12a]`: LLM chỉ được *mô tả*, không được *phán* — nên oracle này không nhận nguồn LLM nào.
  - **`tests/test_oracle.py` — 8 test:** (1) threshold đạt ⟹ pass; (2) threshold sai ⟹ fail + đúng 1 finding; (3) thiếu metric ⟹ `OracleError`; (4) đủ 6 toán tử; (5) `checks` (STEP 13) cả pass/fail/thiếu tên; (6) signals lọc tên lạ; (7) signals ⟹ `value is None`; (8) signals gộp `promote_candidate` vào finding.
  ```powershell
  pytest tests\test_oracle.py -q
  git add -A; git commit -m "STEP 27: oracle threshold + signals"; git push
  ```
- **DoD:** `pytest tests\test_oracle.py -q` in **`8 passed`**; `git diff --stat HEAD~1 -- oracle/__init__.py` **rỗng** (không sửa file này).
- **Nếu fail:** adapter nào cũng đang chờ file này — nếu quá 60', giao `signals` cho B tự viết (B cần nó nhất), A giữ `threshold`.

### STEP 28 — `adapters/midscene_adapter.py` (LÀM TRƯỚC k6 — worker khó nhất)
- **Owner:** Đức (Role B) · **Depends on:** 23, 13, 27 · **Parallel-safe:** STEP 30, 31 (C), 32 (A) · **Time:** 90'
- **Actions:**
  1. `workers/midscene.yaml`: `name: midscene-cli`, `version_probe`: lệnh in phiên bản **đã xác nhận ở STEP 23** (⚠ nếu `--version` không tồn tại, dùng `npx @midscene/cli --help` — miễn là exit 0 khi có mặt), `adapter: "adapters/midscene_adapter.py"`, `lanes: [discovery]`, capability `ui.explore` (`oracle_kinds: [implicit_signals]`, `verdict_sources: [deterministic_assert, llm_judgment, heuristic]`, **`parallel_safe: false`** — dùng chung một trình duyệt và telemetry của toy app), `requires: {env: [MIDSCENE_MODEL_API_KEY, MIDSCENE_MODEL_NAME, MIDSCENE_MODEL_BASE_URL, APP_BASE_URL], binaries: [node]}`, **`data_egress: [screenshot, dom]`** (khai TRƯỚC: đây là mức nghiêm trọng thứ 2 `[R3 S11(5)]`).
  2. `midscene/explore_notes.yaml`: flow ≤ 15 bước — thêm một note rồi xoá nó (dựa trên `hello.yaml` của STEP 23; chỉ dùng khoá YAML **đã chạy được** ở đó).
  3. `tests/fixtures/task_ms.json` (spec `t-101`, sao chép `examples/task.midscene.json`) với `inputs: {"flow":"midscene/explore_notes.yaml","max_steps":15,"promote_hints":{"dom_unchanged":{"repro_steps":["mở /","thêm một note","bấm Xoá"],"suggested_assertion":"sau khi bấm Xoá, note không còn trong danh sách"}}}` và `tests/fixtures/task_ms_canary.json` (`t-canary-01`, `inputs.flow:"midscene/hello_missing.yaml"`).
  4. `adapters/midscene_adapter.py` (~120 dòng), **hai hàm**:
     - `build_cmd`: `[shutil.which("npx"), "@midscene/cli", <flow>, "--summary", <workdir>/summary.json]` (⚠ thứ tự/cờ **đúng như STEP 23 đã chạy**). **Phải dùng `shutil.which("npx")`**: trên Windows `npx` là `npx.cmd`, truyền chuỗi `"npx"` vào `subprocess` không tìm thấy. Trước khi chạy: `DELETE {base_url}/__qc/events` (xoá telemetry cũ); lỗi kết nối ⟹ `AdapterParseError` (không có telemetry thì không có tín hiệu ngầm để phán).
     - `parse_output`: (a) **không có file summary ⟹ `AdapterParseError`** (ma trận STEP 23 sẽ cho biết `no_key` có sinh summary không; nếu Midscene exit 1 cho cả fail lẫn crash thì **file summary là thứ duy nhất phân biệt** chúng `[R3 9.1]`); (b) đọc summary (⚠ theo mẫu `tests/samples/midscene_summary.*.json`): `flow_failed` = có bước fail; lỗi kiểu "không thấy phần tử" ⟹ tín hiệu `element_not_found` (regex có **`PARSER_VERSION`**, khớp không được ⟹ vẫn `flow_failed=True` nhưng **không** tự suy tín hiệu); (c) `GET {base_url}/__qc/events` ⟹ tín hiệu **tất định**: `dom_unchanged` = có `delete_clicked` mà **không** có `render_done` sau nó; `console_error` = có sự kiện `console_error`; `http_5xx` = có sự kiện `http_5xx` (do **trang** báo, không lẫn request của Schemathesis); ghi `events.json` vào `workdir` làm evidence `raw_output`; tín hiệu `dom_unchanged` mang `promote_candidate` lấy từ `inputs.promote_hints` (adapter **không** hard-code nội dung BUG-2); (d) **đối chiếu**: exit 0 mà summary báo bước fail, hoặc exit ≠ 0 mà summary sạch ⟹ `AdapterParseError("mâu thuẫn")`; (e) token/usd: **chỉ** điền nếu summary có số (⚠), không thì `None`; (f) **`llm_judgment`**: chỉ phát nếu summary có con số dùng được làm confidence — **theo D-10 mặc định là không**; ghi `adapter_notes: "aiAssert không có confidence → không phát finding llm_judgment"`.
  5. `tests/test_midscene_adapter.py` — **6 test** (đọc `tests/samples/midscene_summary.*`, telemetry truyền qua hàm `_events()` bị monkeypatch): (1) mẫu `pass` + telemetry sạch ⟹ không finding, `status=pass`, `verdict.value=non_gating`; (2) mẫu `pass` + `delete_clicked` không `render_done` ⟹ **đúng 1 finding** `implicit_signal:dom_unchanged`, `deterministic_assert`, có `promote_candidate`; (3) mẫu `fail_missing_element` ⟹ `status=fail`, `gating=false`, có finding `element_not_found`; (4) không có summary ⟹ `AdapterParseError`; (5) exit code mâu thuẫn summary ⟹ `AdapterParseError`; (6) `/__qc/events` không truy cập được ⟹ `AdapterParseError`.
  ```powershell
  pytest tests\test_midscene_adapter.py -q
  git add -A; git commit -m "STEP 28: midscene adapter"; git push
  ```
- **DoD:** `pytest tests\test_midscene_adapter.py -q` in **`6 passed`**; `Select-String -Path adapters\midscene_adapter.py -Pattern 'assert |threshold|<|>'` không có phép so sánh ngưỡng.
- **Nếu fail:** **`if worker == "midscene"` hoặc "thêm một trường chỉ cho Midscene" xuất hiện ở bất cứ đâu ngoài `adapters/midscene_adapter.py`** ⟹ 🛑 dừng 15' cả nhóm quyết: sửa schema cho **cả 4** worker, hoặc để adapter **mất thông tin** (quy tắc vàng); **cấm** thêm trường riêng `[R5 P4.1 #1]`. Nếu STEP 23 đã chuyển sang kịch bản mock: test (1)–(6) chạy trên fixture, và ghi ⚠ vào `adapter_notes` rằng cấu trúc summary **chưa xác nhận** với Midscene thật.

### STEP 29 — Test Midscene adapter ĐỘC LẬP: đủ ca pass / fail (canary) / error
- **Owner:** Đức (Role B) · **Depends on:** 28 · **Parallel-safe:** STEP 30, 31, 32, 33 · **Time:** 60'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  .\scripts\toyapp.ps1 start
  python -m adapters.midscene_adapter --spec tests\fixtures\task_ms.json        --out runs\ms_result.json; "explore exit=$LASTEXITCODE"
  python -m adapters.midscene_adapter --spec tests\fixtures\task_ms_canary.json --out runs\ms_canary.json; "canary  exit=$LASTEXITCODE"
  $k = $env:MIDSCENE_MODEL_API_KEY; Remove-Item Env:MIDSCENE_MODEL_API_KEY
  python -m adapters.midscene_adapter --spec tests\fixtures\task_ms.json        --out runs\ms_nokey.json;  "nokey   exit=$LASTEXITCODE"
  $env:MIDSCENE_MODEL_API_KEY = $k
  python tools\validate.py result runs\ms_result.json runs\ms_canary.json runs\ms_nokey.json
  (Get-Content runs\ms_result.json -Raw -Encoding UTF8 | ConvertFrom-Json).findings | Select-Object finding_id, detected_by, verdict_source | Format-Table
  ```
  Chạy **`explore` 3 lần liên tiếp** và ghi số finding mỗi lần vào `docs/decisions.md` (đây là số đo đầu tiên về phương sai của agent: `[R5 (a)#4]` — chưa ai chạy Midscene với toy app).
- **DoD:** ba lệnh adapter đều `exit=0`; `validate.py` in `PASS` cho **cả ba**; `status` lần lượt **`pass`** (`ms_result`), **`fail`** (`ms_canary` — canary phải fail đúng chỗ `[R4 S15.4 lớp 3]`), **`error`** (`ms_nokey`); và `ms_result.json` có **≥ 1 finding `deterministic_assert`** (`dom_unchanged` — BUG-2 bật).
- **Nếu fail:** `explore` không cho `dom_unchanged` (VLM không bấm được nút Xoá) ⟹ giảm `max_steps` xuống 8 và viết lại câu lệnh flow rõ hơn (Troubleshooting #1, #2); vẫn không được ⟹ **chuyển sang run ghi sẵn**: lưu **một** lần chạy tốt vào `recordings/midscene/` (summary + `events.json`), thêm biến `QC_REPLAY_MIDSCENE=recordings/midscene` để adapter đọc thay vì chạy (`[R5 P4.1 #3]`), **gắn nhãn** "replay" trong `adapter_notes`. Contract **vẫn được chứng minh** — đó mới là thứ đang demo.

### STEP 30 — k6 "hello world" + ma trận exit code (đóng double-check #2)
- **Owner:** Huy (Role C) · **Depends on:** 11 · **Parallel-safe:** STEP 28, 29 (B), 27 (A) · **Time:** 45'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  k6 version
  ```
  Tạo `tests/perf/notes_list.js` (**không có threshold** — ngưỡng do plan khai, không do script `[arch D3]`):
  ```javascript
  import http from 'k6/http';
  import { check } from 'k6';

  export const options = { vus: 10, duration: '30s' };

  export default function () {
    const r = http.get(`${__ENV.APP_BASE_URL}/notes`);
    check(r, { 'status 200': (x) => x.status === 200 });
  }
  ```
  và `tests/perf/notes_list_thresholds.js` = cùng nội dung + `thresholds: { http_req_duration: ['p(95)<300'], http_req_failed: ['rate<0.01'] }` trong `options` (**chỉ để khám phá** exit code khi threshold hỏng), và `tests/perf/broken.js` chứa đúng một dòng sai cú pháp `this is not javascript`. (⚠ cú pháp k6 viết theo trí nhớ về doc — RUN 3 chỉ xác nhận `options.thresholds` và exit ≠ 0 khi threshold hỏng; **sửa theo lỗi thật** nếu k6 phàn nàn.)
  ```powershell
  $env:APP_BASE_URL = "http://127.0.0.1:8000"
  .\scripts\toyapp.ps1 start -Bugs none
  k6 run --summary-export=runs\k6_pass.json tests\perf\notes_list.js;                          "pass exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 start -Bugs none -LatencyMs 400
  k6 run --summary-export=runs\k6_thr.json  tests\perf\notes_list_thresholds.js;               "threshold_fail exit=$LASTEXITCODE"
  k6 run --summary-export=runs\k6_broken.json tests\perf\broken.js;                            "script_error exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 stop
  k6 run --summary-export=runs\k6_down.json tests\perf\notes_list_thresholds.js;               "server_down exit=$LASTEXITCODE"
  ```
  Lưu mẫu: `k6_summary.pass.json`, `k6_summary.threshold_fail.json`, `k6_summary.server_down.json` (từ `runs/`) và `k6_stdout.script_error.txt` vào `tests/samples/`. Mở `k6_summary.pass.json`, ghi vào `docs/decisions.md` **tên khoá thật** chứa p95 của `http_req_duration` và tỉ lệ của `http_req_failed` (⚠ RUN 4 giả định `["p(95)"]` và `.rate` — có thể là `value`). Điền bảng `## k6 exit-code matrix` với đúng 4 hàng `pass | threshold_fail | script_error | server_down` (cột: `exit code`, `có file summary?`).
  ```powershell
  git add -A; git commit -m "STEP 30: k6 hello + exit-code matrix + samples"; git push
  ```
- **DoD:** `(Select-String -Path docs\decisions.md -Pattern '^\| (pass|threshold_fail|script_error|server_down) \|').Count` = **4** (trong bảng k6, cột exit code đã điền số); `(git ls-files tests/samples | Select-String k6_).Count` ≥ **4**; `docs/decisions.md` ghi tên khoá p95 và failed-rate.
- **Nếu fail:** k6 chưa cài/không chạy ⟹ `doctor` đã báo từ STEP 04 — nếu vẫn chưa xong ở đây thì **báo cả nhóm** (k6 là worker "hình mẫu" của contract). Nếu `threshold_fail` và `script_error` cho **cùng** exit code ⟹ đó chính là lý do adapter phải dựa vào *sự có mặt của file summary*, không dựa vào exit code (STEP 31 đã thiết kế như vậy).

### STEP 31 — `adapters/k6_adapter.py` + `workers/k6.yaml`
- **Owner:** Huy (Role C) · **Depends on:** 27, 30, 13 · **Parallel-safe:** STEP 28, 29, 32 · **Time:** 75'
- **Actions:**
  1. `workers/k6.yaml` theo [arch §5.5] (nguyên văn khối k6): `lanes: [gate]`, `http.load`, `oracle_kinds: [threshold]`, `verdict_sources: [deterministic_assert]`, **`parallel_safe: false`** (chiếm tài nguyên đo lường — STEP 39 hiểu là *độc quyền*), `requires: {env: [APP_BASE_URL], binaries: [k6]}`, `data_egress: []`.
  2. `tests/fixtures/task_k6.json`: `examples/task.k6.json` với `inputs: {"script":"tests/perf/notes_list.js","vus":10,"duration":"30s"}`, `budget.wallclock_s: 90`.
  3. `adapters/k6_adapter.py` (~80 dòng), **hai hàm**, viết theo mẫu ở **mục 5.4 của arch** *nhưng* với các sửa sau (mẫu đó có ⚠ chưa verify): `build_cmd` dùng `--summary-export=<workdir>/k6-summary.json`, `--vus`, `--duration` (⚠ xác nhận bằng `k6 run --help`) và `env = {"APP_BASE_URL": spec.target.base_url}`; `parse_output`: **không có file summary ⟹ `AdapterParseError`** (script hỏng ≠ threshold hỏng); **exit ≠ 0 ⟹ `AdapterParseError`** (script không có threshold nên k6 thành công phải exit 0 — ma trận STEP 30 xác nhận); trích **đúng hai số** vào `metrics` bằng **tên khoá đã ghi ở STEP 30**: `http_req_duration.p95` và `http_req_failed.rate`; thiếu khoá ⟹ `AdapterParseError`; `tokens=0, usd=0.0`; `replay_cmd` = chuỗi lệnh đã chạy nguyên văn. **Adapter không có một phép so sánh nào** — `oracle/threshold.py` so.
  4. `tests/test_k6_adapter.py` — **5 test** trên `tests/samples/k6_*`: (1) mẫu `pass` ⟹ 2 metric đúng số; (2) mẫu `threshold_fail`/`server_down` với oracle `p95<300` ⟹ oracle `fail` (dựng `signals` rồi gọi `oracle.evaluate`); (3) không file summary ⟹ `AdapterParseError`; (4) exit ≠ 0 nhưng có summary ⟹ `AdapterParseError`; (5) summary thiếu khoá metric ⟹ `AdapterParseError`.
  ```powershell
  pytest tests\test_k6_adapter.py -q
  $env:APP_BASE_URL = "http://127.0.0.1:8000"
  .\scripts\toyapp.ps1 start -Bugs none
  python -m adapters.k6_adapter --spec tests\fixtures\task_k6.json --out runs\k6_ok.json;   "ok exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 start -Bugs none -LatencyMs 400
  python -m adapters.k6_adapter --spec tests\fixtures\task_k6.json --out runs\k6_slow.json; "slow exit=$LASTEXITCODE"
  (Get-Content tests\fixtures\task_k6.json -Raw -Encoding UTF8).Replace("notes_list.js","broken.js") | Set-Content -Encoding UTF8 tests\fixtures\task_k6_broken.json
  python -m adapters.k6_adapter --spec tests\fixtures\task_k6_broken.json --out runs\k6_broken.json; "broken exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 stop
  python tools\validate.py result runs\k6_ok.json runs\k6_slow.json runs\k6_broken.json
  git add -A; git commit -m "STEP 31: k6 adapter"; git push
  ```
- **DoD:** `pytest tests\test_k6_adapter.py -q` in **`5 passed`**; ba lệnh adapter `exit=0`; `validate.py` `PASS` cả ba; `status` lần lượt **`pass`**, **`fail`** (LatencyMs 400 ⟹ p95 > 300ms), **`error`** (`broken.js`).
- **Nếu fail:** `slow` không thành `fail` (p95 vẫn < 300ms) ⟹ tăng `-LatencyMs` (đây là *nút vặn của mutant*, không phải bug); `ok` bị `fail` (máy nhiễu, R3 rủi ro #7) ⟹ nới ngưỡng trong `oracle.assertions` của `task_k6.json` **và ghi rõ đây là demo cơ chế, không phải số đo thật** `[R3 rủi ro #7]`.

### STEP 32 — `plan.yaml` thật: đổi mock → Schemathesis thật (report có dòng tất định thật đầu tiên)
- **Owner:** Nghĩa (Role A) · **Depends on:** 26, 20 · **Parallel-safe:** STEP 28–31 · **Time:** 60'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  ```
  Tạo `plan.yaml` (root). Khối đầu + task đầu (các khối sau thêm ở STEP 34, 40, 42, 49):
  ```yaml
  plan_version: 1
  name: noteboard-poc
  sut:
    files: ["toyapp"]
    attrs: {model: "stub-rule-v1"}
    probe_url: "${env.APP_BASE_URL}/__qc/config"
  tasks:
    - task_id: t-001
      capability: api.property
      lane: gate
      intent: "GET /notes/{id} và POST /notes: schema conformance + không 5xx (property-based)"
      target: {kind: http_service, base_url: "${env.APP_BASE_URL}", spec_ref: "${env.APP_BASE_URL}/openapi.json"}
      inputs: {...}      # SAO CHÉP nguyên khối inputs từ tests/fixtures/task_st.json (C đã chốt danh sách check ở STEP 24)
      oracle: {...}      # SAO CHÉP nguyên khối oracle từ tests/fixtures/task_st.json
      expected_result_kind: verdict
      budget: {wallclock_s: 120, tokens: 0, usd: 0}
      determinism: {seed: 1337, replayable: true}
      evidence_required: [raw_output, stdout]
      retry: {max: 1, on: [error]}
      prefer: [schemathesis]
  ```
  (Hai dấu `{...}` **không** phải để trống: đó là hai khối bạn sao chép từ file fixture — nguồn duy nhất, không gõ lại.) Đặt `$env:APP_BASE_URL = "http://127.0.0.1:8000"` (hoặc để `env.ps1` nạp từ `.env`), rồi:
  ```powershell
  .\scripts\toyapp.ps1 start
  python orchestrator.py --plan plan.yaml; "exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 start -Bugs none
  python orchestrator.py --plan plan.yaml; "exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 32: plan.yaml real (t-001)"; git push
  ```
- **DoD:** lần đầu (bug bật) `exit=1`, report mục 1 có **một dòng thật** `t-001 | schemathesis | api.property | ❌ fail` kèm chi tiết nhắc `GET /notes/{note_id}`; lần hai (`-Bugs none`) `exit=0` (**không false positive**).
- **Nếu fail:** lần hai vẫn đỏ ⟹ **check nào đỏ?** nếu không phải BUG-1 ⟹ quay về STEP 24 thu hẹp danh sách check (đừng sửa oracle cho xanh). Report không hiện `t-001` ⟹ `registry.pick` trả `None`: đọc `probe_reason` trong banner (thường `thiếu biến môi trường APP_BASE_URL` hoặc `st` không có trong PATH ⟹ quên `. .\scripts\env.ps1`).

### STEP 33 — A review `midscene_adapter` rồi `k6_adapter` (Midscene TRƯỚC)
- **Owner:** Nghĩa (Role A) · **Depends on:** 29, 31 · **Parallel-safe:** STEP 32 (cùng owner), 35, 36 · **Time:** 45'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1; .\scripts\toyapp.ps1 start
  python tools\review_adapter.py --result runs\ms_result.json --spec tests\fixtures\task_ms.json --crash runs\ms_nokey.json; "midscene exit=$LASTEXITCODE"
  python tools\review_adapter.py --result runs\k6_ok.json --spec tests\fixtures\task_k6.json --crash runs\k6_broken.json; "k6 exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 stop
  ```
  (Dùng lại các file `runs/ms_*.json`, `runs/k6_*.json` mà B, C đã sinh ở STEP 29, 31 — nếu chưa có trên máy A, chạy lại đúng các lệnh ở hai STEP đó.) Đọc mã **Midscene trước**: đây là nơi V1 hay chớm.
- **DoD:** cả hai lệnh `review_adapter.py` in **5 `PASS`** và `exit=0`; `Select-String -Path core\*.py -Pattern 'midscene|k6' -CaseSensitive:$false` **không in gì**.
- **Nếu fail:** trả về đúng người với dòng lỗi nguyên văn, **không merge**. Câu 5 `FAIL` ở bất kỳ adapter nào ⟹ Troubleshooting mục "contract bị ép méo".

### STEP 34 — Thêm t-002 (k6) và t-101 (Midscene) vào `plan.yaml`
- **Owner:** Nghĩa (Role A) · **Depends on:** 33, 32 · **Parallel-safe:** STEP 35, 36 · **Time:** 30'
- **Actions:** thêm hai khối vào `plan.yaml` (sao chép `inputs`/`oracle`/`budget` từ `tests/fixtures/task_k6.json` và `task_ms.json`):
  ```yaml
    - task_id: t-002
      capability: http.load
      lane: gate
      intent: "GET /notes chịu 10 VU trong 30s với p95 < 300ms (ngưỡng rộng: demo cơ chế, không phải số đo thật)"
      target: {kind: http_service, base_url: "${env.APP_BASE_URL}"}
      inputs: {...}      # từ tests/fixtures/task_k6.json
      oracle: {...}      # từ tests/fixtures/task_k6.json
      expected_result_kind: verdict
      budget: {wallclock_s: 90, tokens: 0, usd: 0}
      determinism: {seed: null, replayable: true}
      evidence_required: [raw_output, stdout]
      retry: {max: 1, on: [error]}
      prefer: [k6]
    - task_id: t-101
      capability: ui.explore
      lane: discovery
      intent: "Khám phá trang /: thêm note, xoá note; tìm trạng thái kẹt hoặc lỗi runtime"
      target: {kind: web_app, base_url: "${env.APP_BASE_URL}", entry_path: "/"}
      inputs: {...}      # từ tests/fixtures/task_ms.json (gồm promote_hints)
      oracle: {kind: implicit_signals, signals: [console_error, http_5xx, dom_unchanged, element_not_found], llm_observations_allowed: true}
      expected_result_kind: candidate_finding
      budget: {wallclock_s: 600, tokens: 150000, usd: 1.0}
      determinism: {seed: null, replayable: false}
      evidence_required: [raw_output]
      retry: {max: 0, on: []}
      prefer: [midscene-cli]
  ```
  ```powershell
  .\scripts\toyapp.ps1 start
  python orchestrator.py --plan plan.yaml; "exit=$LASTEXITCODE"
  git add -A; git commit -m "STEP 34: plan + t-002 (k6) + t-101 (midscene)"; git push
  ```
- **DoD:** `exit=1` (do BUG-1); report có **mục 1 với 2 dòng thật** (`t-001` ❌, `t-002` ✅) và **mục 3 với ≥ 1 finding** của `midscene-cli`; `validate.py result` `PASS` cho mọi file `runs\r-NNNN\results\*.json`.
- **Nếu fail:** Midscene chạy quá `budget.wallclock_s` ⟹ `error` + banner ở đầu report (đúng thiết kế: **không** làm gate đỏ vì nó ở discovery). Muốn tạm bỏ nó khỏi lần chạy: `--only t-001,t-002`. Nếu `t-002` đỏ vì máy nhiễu ⟹ STEP 31 "Nếu fail".

> **🛑 Cổng cuối slot 3:** 3 adapter (Schemathesis, Midscene, k6) đã qua checklist 5 câu; `plan.yaml` cho report có dòng thật ở mục 1 **và** mục 3.

## Slot 3 (tiếp) và Slot 4 — Ngày 2: nhánh AI + hoàn thiện

### STEP 35 — DeepEval "hello world": metric tất định không cần key, G-Eval cần key, đọc kết quả bằng cách nào
- **Owner:** Huy (Role C) · **Depends on:** 02, 04, 11 · **Parallel-safe:** STEP 33, 34 (A), 28–31 · **Time:** 60'
- **Actions:** RUN 3 xác nhận DeepEval **cắm vào pytest** (thừa hưởng exit code) nhưng **chưa** xác nhận xuất JSON/JUnit `[R3 9.6, R4 double-check #3]`. Vì vậy adapter (STEP 37) đọc kết quả qua **`pytest --junitxml`** (chuẩn của pytest, không phụ thuộc định dạng riêng của DeepEval) — bước này kiểm điều đó chạy được. Judge dùng `QC_JUDGE_PROVIDER` đã qua smoke check; nếu OpenAI không xác thực được, STEP 02 thử Gemini và STEP 37 phải ghi rõ provider thật sự đã chấm.
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  ```
  Tạo `tests/eval/hello_probe.py` — **đặt tên KHÔNG khớp `test_*.py`/`*_test.py`** để `pytest -q` toàn repo không nhặt nó (nó có ca **cố ý đỏ**; chạy bằng cách chỉ đích danh file). ⚠ **tên API DeepEval viết theo trí nhớ về doc** — sửa theo lỗi thật; ý đồ: một metric tất định tự viết + một G-Eval:
  ```python
  import os
  import pytest
  from deepeval import assert_test
  from deepeval.metrics import BaseMetric, GEval
  from deepeval.test_case import LLMTestCase, LLMTestCaseParams


  class NotEmpty(BaseMetric):
      def __init__(self, threshold: float = 1.0):
          self.threshold = threshold

      def measure(self, test_case: LLMTestCase) -> float:
          self.score = 1.0 if test_case.actual_output.strip() else 0.0
          self.success = self.score >= self.threshold
          self.reason = "ok" if self.success else "rỗng"
          return self.score

      async def a_measure(self, test_case: LLMTestCase) -> float:
          return self.measure(test_case)

      def is_successful(self) -> bool:
          return self.success

      @property
      def __name__(self):
          return "summary_not_empty"


  @pytest.mark.parametrize("cid,out", [("ok", "Tóm tắt ngắn"), ("empty", "")])
  def test_not_empty(cid, out):
      assert_test(LLMTestCase(input="x", actual_output=out), [NotEmpty()])


  def test_geval_score():
      provider = os.environ.get("QC_JUDGE_PROVIDER", "openai").lower()
      model_name = os.environ.get("QC_JUDGE_MODEL")
      if not model_name:
          pytest.skip("thiếu QC_JUDGE_MODEL")
      if provider == "gemini":
          from deepeval.models import GeminiModel
          api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
          if not api_key:
              pytest.skip("không có Gemini API key")
          model = GeminiModel(model=model_name, api_key=api_key, temperature=0)
      else:
          if not os.environ.get("OPENAI_API_KEY"):
              pytest.skip("không có OpenAI API key")
          model = model_name
      m = GEval(name="giữ ý chính", criteria="Bản tóm tắt có giữ ý chính của đầu vào không?",
                evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
                model=model)
      m.measure(LLMTestCase(input="Họp lúc 9h. Mang laptop.", actual_output="Họp 9h, mang laptop"))
      print("GEVAL_SCORE", m.score)
  ```
  ```powershell
  pytest tests\eval\hello_probe.py -q -s --junitxml=runs\de_junit.xml; "metric+geval exit=$LASTEXITCODE"      # case "empty" phải đỏ
  $openai = $env:OPENAI_API_KEY; $gemini = $env:GEMINI_API_KEY; $google = $env:GOOGLE_API_KEY
  Remove-Item Env:OPENAI_API_KEY,Env:GEMINI_API_KEY,Env:GOOGLE_API_KEY -ErrorAction SilentlyContinue
  pytest tests\eval\hello_probe.py -q -s --junitxml=runs\de_junit_nokey.xml; "no_key exit=$LASTEXITCODE"       # metric tất định vẫn chạy, geval skip
  $env:OPENAI_API_KEY = $openai; $env:GEMINI_API_KEY = $gemini; $env:GOOGLE_API_KEY = $google
  pytest tests\eval\hello_probe.py -q -k "ok" --junitxml=runs\de_junit_pass.xml; "metric_pass exit=$LASTEXITCODE"
  ```
  Ghi lại: (a) `metric_fail` có exit ≠ 0 và JUnit có phần tử `<failure>` cho **đúng** `test_not_empty[empty-]`; (b) `no_key` metric tất định **vẫn chạy được không cần key** (quyết định D5/STEP 02 fallback); (c) **DeepEval có hỏi đăng nhập / mở trình duyệt / in prompt tương tác không** (nếu có: adapter chạy trong `subprocess` sẽ **treo** — tìm biến môi trường tắt trong doc DeepEval và ghi vào `.env.example`); (d) `deepeval test run tests\eval\hello_probe.py` có sinh file JSON trong `DEEPEVAL_RESULTS_FOLDER` không (đóng double-check #3; **không** bắt buộc dùng). Lưu `runs\de_junit.xml` → `tests/samples/de_junit_mixed.xml`, `de_junit_pass.xml` → `tests/samples/de_junit_pass.xml`, và stdout có dòng `GEVAL_SCORE` → `tests/samples/de_geval_stdout.txt`. Điền bảng `## DeepEval matrix` trong `docs/decisions.md`: 4 hàng `metric_pass | metric_fail | geval_with_key | geval_no_key`, cột `exit code`, `có JUnit?`, `ghi chú`.
  ```powershell
  git add -A; git commit -m "STEP 35: deepeval hello + junit samples"; git push
  ```
- **DoD:** `(Select-String -Path docs\decisions.md -Pattern '^\| (metric_pass|metric_fail|geval_with_key|geval_no_key) \|').Count` = **4** với cột exit code điền số; `(git ls-files tests/samples | Select-String de_).Count` ≥ **3**; `docs/decisions.md` trả lời (c) và (d) bằng `có`/`không`.
- **Nếu fail:** không có key ⟹ hàng `geval_with_key` ghi `N/A — không có key` và dùng **G-Eval mock** (fixture cố định, gắn nhãn) — theo STEP 02. Nếu `assert_test` **bắt** key/đăng nhập ngay cả với metric tự viết ⟹ dùng `pytest` thuần với `assert` cho 3 metric tất định (mất "chạy qua DeepEval" nhưng giữ được **contract** — điều đang chứng minh) và báo A.

### STEP 36 — `adapters/collect_adapter.py` (worker `http.collect`: thu output của AI feature)
- **Owner:** Huy (Role C) · **Depends on:** 13, 11 · **Parallel-safe:** STEP 35, 33, 34 · **Time:** 45'
- **Actions:** DeepEval **không tự chạy app** — nó cần sẵn `input`/`actual_output` `[R3 9.6, rủi ro #4]`; nên việc thu output là **một task riêng trong plan**, không giấu trong worker `[R3 rủi ro #4]`.
  1. `workers/http-collect.yaml`: `name: http-collect`, `version_probe: "python --version"`, `adapter: "adapters/collect_adapter.py"`, `lanes: [gate]`, capability `http.collect` (`oracle_kinds: [checks]`, `verdict_sources: [deterministic_assert]`, `parallel_safe: true`), `requires: {env: [APP_BASE_URL], binaries: []}`, `data_egress: []`.
  2. `adapters/collect_adapter.py` (~60 dòng): `build_cmd` trả `[sys.executable, "-c", "pass"]` (việc thật làm trong `parse_output` bằng `httpx.Client`; client tiêm được qua thuộc tính lớp để test bằng `httpx.MockTransport`). Với mỗi case trong `inputs.golden` (`tests/eval/golden.json`): `POST /notes` `{title, body}` → `id`; `POST /notes/{id}/summarize`; ghi `{id: "g1", note_id, input, actual_output, model, prompt_hash, http_status}` vào `workdir/outputs.json`; cuối cùng `DELETE` các note đã tạo. `signals["checks"] = {"all_http_2xx": <mọi status 2xx>, "count_matches_golden": <số record == số case>}`; evidence `raw_output` = `outputs.json`. **Không** kết luận gì về *chất lượng* summary (việc của DeepEval); kết nối lỗi ⟹ `AdapterParseError`.
  3. `tests/test_collect_adapter.py` — **4 test** (`MockTransport`): (1) mọi request 2xx ⟹ `all_http_2xx=True` và `outputs.json` có đúng 5 record có `actual_output`; (2) `summarize` trả 500 ở một case ⟹ `all_http_2xx=False`; (3) lỗi kết nối ⟹ `AdapterParseError`; (4) record giữ `model` và `prompt_hash` từ response.
  ```powershell
  pytest tests\test_collect_adapter.py -q
  .\scripts\toyapp.ps1 start
  python -m adapters.collect_adapter --spec tests\fixtures\task_collect.json --out runs\collect.json; "exit=$LASTEXITCODE"
  python tools\validate.py result runs\collect.json
  (Get-Content runs\r-0001\t-000\outputs.json -Raw -Encoding UTF8 | ConvertFrom-Json).Count
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 36: collect adapter (http.collect)"; git push
  ```
  (`tests/fixtures/task_collect.json`: spec `t-000`, `capability:"http.collect"`, `lane:"gate"`, `run_id:"r-0001"`, `inputs:{"golden":"tests/eval/golden.json"}`, `oracle:{"kind":"checks","required":["all_http_2xx","count_matches_golden"]}`, `evidence_required:["raw_output"]`, `target.base_url:"http://127.0.0.1:8000"`.)
- **DoD:** `pytest tests\test_collect_adapter.py -q` in **`4 passed`**; lệnh adapter `exit=0`, `validate.py` `PASS`, `status=pass`; số record trong `outputs.json` = **`5`**.
- **Nếu fail:** không có `t-000` thì DeepEval không có gì để chấm ⟹ nhánh AI đứt. Fallback: DeepEval đọc thẳng `inputs.records` viết tay trong plan (mất một task, giữ được chứng minh `verdict_source`) — ghi `[KHÔNG KỊP]`.

### STEP 37 — `tests/eval/test_summarize.py` + `adapters/deepeval_adapter.py` (một worker, hai `verdict_source`)
- **Owner:** Huy (Role C) · **Depends on:** 35, 36, 06, 13 · **Parallel-safe:** STEP 38 (A chờ), 39 (A) · **Time:** 90'
- **Actions:**
  1. **`tests/eval/test_summarize.py`** (dựa trên `hello_probe.py` **đã chạy được** ở STEP 35). **Đầu file bắt buộc có** `pytestmark = pytest.mark.skipif(not os.environ.get("QC_EVAL_OUTPUTS"), reason="chỉ chạy qua deepeval_adapter")` — nếu thiếu, `pytest -q` toàn repo sẽ đỏ vì file này nằm trong `tests/` và cần `outputs.json`. Đọc `QC_EVAL_OUTPUTS` (đường dẫn `outputs.json`), `QC_EVAL_OUT_DIR`. **3 metric tất định tự viết** (không gọi LLM): `summary_not_empty`, `summary_shorter_than_body` (`len(actual_output) < len(input)`), `summary_json_valid` (record có `summary: str`, `model: str`, `prompt_hash: str` — kiểm bằng `jsonschema`). Tham số hoá **`test_metric[<metric>-<case_id>]`** ⟹ JUnit cho **từng cặp (metric, case)**. **Advisory**: `test_geval_advisory` chạy G-Eval "giữ ý chính" bằng `QC_JUDGE_PROVIDER`/`QC_JUDGE_MODEL`; nếu primary lỗi thì thử fallback provider/model **tối đa một lần**. Khi dùng Gemini, tạo `GeminiModel(model=..., api_key=..., temperature=0)` ([DeepEval Gemini docs](https://deepeval.com/integrations/models/gemini)). Ghi `QC_EVAL_OUT_DIR/geval.json` = `{"score": <trung bình>, "cases": {...}, "judge_provider": "...", "judge_model": "..."}` hoặc `{"error": "..."}`; không ghi key hay response body lỗi; **không bao giờ làm test đỏ** — *advisory không được phép ảnh hưởng gate, kể cả khi cả hai provider đều hỏng.*
  2. `workers/deepeval.yaml` theo [arch §5.5]: `lanes: [gate, discovery]`, capability `llmapp.eval` (`oracle_kinds: [checks]`, `verdict_sources: [deterministic_assert, llm_judgment]`, `gating_metrics: [summary_not_empty, summary_shorter_than_body, summary_json_valid]`, `advisory_metrics: [GEval]`, `parallel_safe: true`), `requires: {env: [], binaries: []}` (registry không có `one-of` cho OpenAI/Gemini; key chỉ dùng cho advisory), **`data_egress: [app_input, app_output]`**. Khi fallback sang Gemini, các trường này được gửi tới Google; provider/model thật phải được ghi vào `geval.json`.
  3. `adapters/deepeval_adapter.py` (~110 dòng), **hai hàm**:
     - `build_cmd`: **kiểm judge ≠ SUT trước tiên**: `inputs.judge_config.model == inputs.sut_model` ⟹ `AdapterParseError("judge trùng model của SUT")` (`[arch D5]`: **không âm thầm chạy**). Lệnh: `[os.environ.get("QC_DEEPEVAL_PYTHON", sys.executable), "-m", "pytest", "tests/eval/test_summarize.py", f"--junitxml={workdir}/junit.xml", "-q", "-p", "no:cacheprovider"]`; env: `QC_EVAL_OUTPUTS=<inputs.outputs_path>`, `QC_EVAL_OUT_DIR=<workdir>`, `DEEPEVAL_RESULTS_FOLDER=<workdir>/deepeval-results`.
     - `parse_output`: (a) **không có `junit.xml` ⟹ `AdapterParseError`**; (b) parse JUnit: tên test khớp regex `test_metric\[(?P<metric>[a-z_]+)-(?P<case>[a-z0-9]+)\]` (`PARSER_VERSION="1"`); `signals["checks"][metric] = mọi case của metric đó đều pass`; mỗi cặp hỏng ⟹ finding `deterministic_assert`, `detected_by:"metric:<metric>"`, tiêu đề `"<metric>: case <case> không đạt"`, `severity_hint:"high"`; test hỏng **không quy về metric nào** ⟹ `AdapterParseError` (không đoán); (c) **đối chiếu**: pytest exit ≠ 0 mà JUnit không có thất bại (hoặc ngược lại) ⟹ `AdapterParseError`; (d) đọc `geval.json`: có `score` ⟹ **một finding `llm_judgment`**, `detected_by:"metric:GEval"`, `confidence = score` (**D-10**, kèm `adapter_notes: "confidence := G-Eval score"`), `rationale = "G-Eval 'giữ ý chính' <score> (judge=<model>)"`, `metrics["GEval.score"] = score`; `{"error":…}` hoặc không có file ⟹ **không** phát finding, chỉ ghi `adapter_notes` — phần tất định **không bị ảnh hưởng**; (e) `tokens`/`usd` = `None` (DeepEval không báo); (f) evidence: `junit.xml`, `geval.json` (nếu có), log stdout.
  4. `tests/test_deepeval_adapter.py` — **8 test** trên `tests/samples/de_junit_*` + `geval.json` dựng tay: (1) JUnit sạch + G-Eval 0.71 ⟹ mọi check `True`, đúng 1 finding `llm_judgment` với `confidence=0.71`; (2) JUnit có `test_metric[summary_shorter_than_body-g3]` hỏng ⟹ check đó `False`, có finding `deterministic_assert` cho `g3`; (3) `judge_config.model == sut_model` ⟹ `AdapterParseError`; (4) không có JUnit ⟹ `AdapterParseError`; (5) pytest exit ≠ 0 nhưng JUnit sạch ⟹ `AdapterParseError`; (6) `geval.json` = `{"error": "429"}` ⟹ **không** finding `llm_judgment`, phần tất định vẫn hợp lệ; (7) OpenAI lỗi xác thực + Gemini hợp lệ ⟹ retry một lần, finding ghi provider `gemini`; (8) cả hai provider lỗi ⟹ không có `llm_judgment`, deterministic metrics vẫn hợp lệ, key không xuất hiện trong output.
  ```powershell
  pytest tests\test_deepeval_adapter.py -q
  .\scripts\toyapp.ps1 start
  python -m adapters.collect_adapter --spec tests\fixtures\task_collect.json --out runs\collect.json
  python -m adapters.deepeval_adapter --spec tests\fixtures\task_de.json --out runs\de_bug.json;  "bug3_on  exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 start -Bugs none
  python -m adapters.collect_adapter --spec tests\fixtures\task_collect.json --out runs\collect.json
  python -m adapters.deepeval_adapter --spec tests\fixtures\task_de.json --out runs\de_clean.json; "bug3_off exit=$LASTEXITCODE"
  python -m adapters.deepeval_adapter --spec tests\fixtures\task_de_badpath.json --out runs\de_crash.json; "crash exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 stop
  python tools\validate.py result runs\de_bug.json runs\de_clean.json runs\de_crash.json
  git add -A; git commit -m "STEP 37: deepeval adapter + test_summarize"; git push
  ```
  (`tests/fixtures/task_de.json`: spec `t-003`, `capability:"llmapp.eval"`, `lane:"gate"`, `inputs:{"outputs_path":"runs/r-0001/t-000/outputs.json","judge_config":{"model":"…"},"sut_model":"stub-rule-v1"}` (chỗ `"…"` là **tên model chấm C đang dùng, cùng giá trị `QC_JUDGE_MODEL` trong `.env`** — tên model không phải bí mật; file fixture này commit được), `oracle:{"kind":"checks","required":["summary_not_empty","summary_shorter_than_body","summary_json_valid"]}`, `evidence_required:["raw_output"]`, `budget:{"wallclock_s":180,"tokens":50000,"usd":0.5}`; `task_de_badpath.json` = như trên nhưng `outputs_path` trỏ file không tồn tại.)
- **DoD:** `pytest tests\test_deepeval_adapter.py -q` in **`8 passed`**; `validate.py` `PASS` cả ba; `status` lần lượt **`fail`** (`de_bug`: case g3 hỏng), **`pass`** (`de_clean`), **`error`** (`de_crash`); **`de_bug.json` và `de_clean.json` đều có finding `llm_judgment`** *(nếu có provider dùng được)* — chính hai file này chứng minh **một result, hai `verdict_source`**.
- **Nếu fail:** cả OpenAI và Gemini đều không dùng được ⟹ ca `llm_judgment` dùng G-Eval **mock** (fixture `geval.json` viết tay, `adapter_notes` ghi `mock`); đây là cắt #5 một phần — vẫn còn nhánh AI chạy thật với metric tất định. Nếu `error` vì DeepEval treo chờ tương tác ⟹ áp biến tắt đã ghi ở STEP 35 (c).

### STEP 38 — A review `deepeval_adapter` + `collect_adapter`
- **Owner:** Nghĩa (Role A) · **Depends on:** 37 · **Parallel-safe:** STEP 39 (cùng owner — tuần tự) · **Time:** 45'
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  python tools\review_adapter.py --result runs\de_clean.json --spec tests\fixtures\task_de.json --crash runs\de_crash.json; "deepeval exit=$LASTEXITCODE"
  python tools\review_adapter.py --result runs\collect.json  --spec tests\fixtures\task_collect.json --crash runs\collect_down.json; "collect exit=$LASTEXITCODE"
  ```
  (`runs\collect_down.json`: chạy `collect_adapter` khi toy app **đã dừng** ⟹ phải ra `status=error`.) Kiểm riêng điểm **D-10** và **kiểm judge ≠ SUT**: `(Get-Content runs\de_bug.json -Raw -Encoding UTF8 | ConvertFrom-Json).adapter_notes` phải có dòng `confidence := G-Eval score`; và đổi tay `sut_model` trong một bản sao spec thành đúng tên model chấm ⟹ adapter phải trả `error`.
- **DoD:** hai lệnh `review_adapter.py` in **5 `PASS`** + `exit=0`; thử `judge == sut` ⟹ `status=error`.
- **Nếu fail:** như STEP 33. **Đặc biệt** với DeepEval: nếu `gating` xuất hiện ở finding (ngoài `verdict`) hoặc metric advisory làm đổi `verdict.value` ⟹ đó là vi phạm N2 — không merge.

### STEP 39 — Runner song song (`parallel_safe`, độc quyền cho task đo lường)
- **Owner:** Nghĩa (Role A) · **Depends on:** 16, 34 · **Parallel-safe:** STEP 35–38 · **Time:** 75'
- **Actions:** sửa `core/runner.py` (đúng file này, **không** đụng adapter). Quy tắc **[arch §6.2]**: trong mỗi tầng DAG — task `parallel_safe: true` chạy **song song** (`ThreadPoolExecutor`, số luồng = số task trong tầng); task `parallel_safe: false` (k6, Midscene) chạy **độc quyền**: sau khi nhóm song song xong, lần lượt từng cái **một mình** (không task nào khác cùng chạy). Lý do độc quyền chứ không chỉ "xếp hàng riêng": k6 đo p95 — Schemathesis đang bắn request vào cùng app thì p95 hỏng `[R3 rủi ro #7]`. Khai `parallel_safe` lấy từ **manifest** của worker được chọn (`registry.pick`), core **không** biết tên worker. Kết quả ghi/gộp theo `task_id` (không phụ thuộc thứ tự hoàn thành). `tests/test_runner_parallel.py` — **3 test** với adapter giả ngủ 2 giây: (1) 3 task `parallel_safe` ⟹ tổng < 4 giây; (2) task độc quyền **không** giao thời gian chạy với bất kỳ task nào (ghi mốc bắt đầu/kết thúc rồi so); (3) kết quả **giống hệt** chạy tuần tự.
  ```powershell
  pytest tests\test_runner_parallel.py tests\test_runner.py -q
  git add -A; git commit -m "STEP 39: parallel runner"; git push
  ```
- **DoD:** `pytest tests\test_runner_parallel.py -q` in **`3 passed`** và `pytest tests\test_runner.py -q` vẫn **`8 passed`**; `Select-String -Path core\runner.py -Pattern 'k6|midscene|schemathesis|deepeval' -CaseSensitive:$false` không in gì.
- **Nếu fail:** **cắt ngay** (cut #2 ở Phụ lục 4): giữ tuần tự. Chỉ mất luận điểm `max(worker)` vs `sum(worker)`; **không** ảnh hưởng đúng/sai của verdict. Đừng để STEP này chặn STEP 40.

### STEP 40 — `plan.yaml` đầy đủ + kiểm tiêu chí PoC #1, #2, #3
- **Owner:** Nghĩa (Role A) · **Depends on:** 34, 38 · **Parallel-safe:** STEP 42 (B), 39 (cùng owner) · **Time:** 45'
- **Actions:** tạo `baselines/geval.json` = `{"GEval": <điểm G-Eval của lần chạy bug-off đầu tiên, làm tròn 2 số>}` (**viết tay** — PoC chưa có baseline store `[arch D5]`; nếu không có key: `{"GEval": 0.78}` lấy từ mẫu [R5 2.5] và ghi nhãn *mock*); rồi thêm hai task vào `plan.yaml` (sao chép `inputs`/`oracle` từ `tests/fixtures/task_collect.json` và `task_de.json`; `judge_config.model` đọc từ `${env.QC_JUDGE_MODEL}`; **thêm biến `QC_JUDGE_MODEL` vào `.env.example`**):
  ```yaml
    - task_id: t-000
      capability: http.collect
      lane: gate
      intent: "Thu output của AI feature (POST /notes/{id}/summarize) cho 5 golden case"
      target: {kind: http_service, base_url: "${env.APP_BASE_URL}"}
      inputs: {...}      # từ tests/fixtures/task_collect.json
      oracle: {...}
      expected_result_kind: verdict
      budget: {wallclock_s: 60, tokens: 0, usd: 0}
      determinism: {seed: null, replayable: true}
      evidence_required: [raw_output]
      retry: {max: 1, on: [error]}
    - task_id: t-003
      capability: llmapp.eval
      lane: gate
      intent: "Eval AI feature: 3 metric tất định (gating) + G-Eval (advisory)"
      target: {kind: llm_app, base_url: "${env.APP_BASE_URL}"}
      depends_on: [t-000]
      inputs: {outputs_path: "runs/${run_id}/t-000/outputs.json", judge_config: {model: "${env.QC_JUDGE_MODEL}"}, sut_model: "stub-rule-v1"}
      oracle: {kind: checks, required: [summary_not_empty, summary_shorter_than_body, summary_json_valid]}
      expected_result_kind: verdict
      budget: {wallclock_s: 180, tokens: 50000, usd: 0.5}
      determinism: {seed: null, replayable: true}
      evidence_required: [raw_output]
      retry: {max: 1, on: [error]}
  ```
  Rồi kiểm 3 tiêu chí (chạy Midscene tốn tiền — dùng `--only` để lặp nhanh):
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  $only = "t-000,t-001,t-002,t-003"
  .\scripts\toyapp.ps1 start                                   # bug BẬT
  python orchestrator.py --plan plan.yaml --only $only; "#1 bug_on  exit=$LASTEXITCODE"
  .\scripts\toyapp.ps1 start -Bugs none                        # bug TẮT
  python orchestrator.py --plan plan.yaml --only $only; "#1 bug_off exit=$LASTEXITCODE"
  $run = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
  python tools\validate.py result (Get-ChildItem "$run\results\*.json").FullName; "#2 exit=$LASTEXITCODE"
  # ---- tiêu chí #3: ép điểm G-Eval xuống thấp, verdict tổng KHÔNG được đổi ----
  $p = Join-Path $run "results\t-003.json"
  $j = Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json
  $j.findings | Where-Object { $_.verdict_source -eq "llm_judgment" } | ForEach-Object { $_.confidence = 0.01; $_.title = "G-Eval ép thấp để thử" }
  [System.IO.File]::WriteAllText($p, ($j | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding($false)))
  python orchestrator.py --rerender $run
  Select-String -Path "$run\report.md","$run\report.rerender.md" -Pattern '^## VERDICT'
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 40: plan t-000 + t-003; criteria 1-3"; git push
  ```
- **DoD (tiêu chí PoC `[R5 2.3]`):** **#1** `bug_on exit=1` (mục 1 có `t-000 ✅`, `t-001 ❌`, `t-002 ✅`, `t-003 ❌` do case `g3`) và `bug_off exit=0` (cả bốn ✅); **#2** `validate.py` in `PASS` cho **mọi** file result và `exit=0` (một schema, không trường riêng); **#3** hai dòng `## VERDICT` (của `report.md` và `report.rerender.md`) **giống hệt nhau** (`✅ PASS`) dù điểm G-Eval đã bị ép xuống 0.01; report có **đủ 3 mục** tách theo `verdict_source`.
- **Nếu fail:** `bug_off` không xanh ⟹ *false positive* — phải tìm ra vì sao **trước khi** đi tiếp (đây là điều kiện để "gate đỏ khi bug bật" có ý nghĩa). `t-003` bị `skipped` ⟹ `depends_on` sai hoặc `t-000` không `pass` (đúng thiết kế: xem banner). #3 đổi verdict ⟹ **vi phạm N2 — lỗi nghiêm trọng nhất**: xem `core/verdict.py` (chỉ được đọc `verdict.gating`) và `deepeval_adapter` (advisory có lọt vào `verdict` không).

### STEP 41 — 🛑 Tiêu chí PoC #4: chạy 2 lần, `diff` phần tất định phải rỗng
- **Owner:** Nghĩa (Role A) · **Depends on:** 40 · **Parallel-safe:** STEP 42, 43, 44 (B), 46, 47 · **Time:** 60'
- **Actions:** tạo **F-11** `tools/diff_runs.py` (đã chạy thử). Quy tắc sống còn: **không `git commit` và không sửa file nào trong `toyapp/` giữa hai lần chạy** (`code_commit` và `files_sha256` nằm trong SUT identity ⟹ đổi là `run_signature` đổi ⟹ `diff_runs` từ chối so, đúng thiết kế).
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  .\scripts\toyapp.ps1 start
  # (a) bản rẻ, không gọi LLM:
  python orchestrator.py --plan plan.yaml --only t-000,t-001,t-002,t-003; "A exit=$LASTEXITCODE"
  python orchestrator.py --plan plan.yaml --only t-000,t-001,t-002,t-003; "B exit=$LASTEXITCODE"
  $r = Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 2
  python tools\diff_runs.py $r[0].FullName $r[1].FullName; "diff(a) exit=$LASTEXITCODE"
  # (b) bản đầy đủ (có Midscene, tốn tiền — làm 1 lần), rồi lần 2 CHỈ các task gate của lần 1 (rẻ):
  python orchestrator.py --plan plan.yaml; "C exit=$LASTEXITCODE"
  $c = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
  $ids = ((Get-Content "$c\report.json" -Raw -Encoding UTF8 | ConvertFrom-Json).deterministic_view | ForEach-Object { $_.task_id }) -join ","
  python orchestrator.py --plan plan.yaml --only $ids; "D exit=$LASTEXITCODE"
  $r = Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 2
  python tools\diff_runs.py $r[0].FullName $r[1].FullName; "diff(b) exit=$LASTEXITCODE"
  # (c) chứng minh `diff_runs` KHÔNG mù: sửa tay một verdict trong bản sao thì phải bắt được
  Copy-Item $r[1].FullName runs\r-9999 -Recurse
  $p = "runs\r-9999\report.json"; $j = Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json
  $j.deterministic_view[0].status = "fail"; $j.deterministic_view[0].value = "fail"
  [System.IO.File]::WriteAllText($p, ($j | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding($false)))
  python tools\diff_runs.py $r[0].FullName runs\r-9999; "diff(c) exit=$LASTEXITCODE"
  # (d) đổi cấu hình runtime của SUT ⟹ phải TỪ CHỐI so sánh
  .\scripts\toyapp.ps1 start -Bugs none
  python orchestrator.py --plan plan.yaml --only t-000,t-001,t-002,t-003; "E exit=$LASTEXITCODE"
  $e = Get-ChildItem runs -Directory | Where-Object { $_.Name -like "r-0*" } | Sort-Object Name | Select-Object -Last 1
  python tools\diff_runs.py $r[1].FullName $e.FullName; "diff(d) exit=$LASTEXITCODE"
  Remove-Item -Recurse -Force runs\r-9999; .\scripts\toyapp.ps1 stop
  ```
  **Chụp màn hình** hai lần chạy + dòng `IDENTICAL` (cho demo, `[R5 slot 4]`).
- **DoD (🛑 cổng slot 4 = tiêu chí #4):** (a) `diff(a) exit=0` in `IDENTICAL`; (b) `diff(b) exit=0` in `IDENTICAL`; (c) `diff(c) exit=1` in `DIFFERENT — đây là BUG CỦA HỆ THỐNG`; (d) `diff(d) exit=2` in `KHÔNG SO ĐƯỢC: run_signature khác nhau`. Cả **bốn** phải đúng — (c) và (d) chứng minh công cụ không "xanh vì mù".
- **Nếu fail:** (a)/(b) `DIFFERENT` ⟹ đọc dòng `-`/`+` để biết task nào lật: **`t-001` lật** ⟹ Schemathesis phát hiện BUG-1 không ổn định ⟹ quay lại STEP 24 hiệu chuẩn (hạ `QC_LONG_ID_LEN`, ghim seed); **`t-002` lật** ⟹ k6 nhiễu ⟹ nới ngưỡng (STEP 31); **`t-003` lật** ⟹ phần "tất định" của DeepEval không tất định ⟹ kiểm D-01/stub. (a)/(b) exit **2** ⟹ có gì đó trong SUT identity/plan/adapter đổi giữa hai lần chạy (đã commit? sửa `toyapp/`? `QC_BUGS` khác?). **Nếu #4 không đạt được thì "PoC thành công" chỉ còn #1–#3 — phải hạ xuống và nói rõ với mentor**, không được giấu.

### STEP 42 — Canary: một task **chắc chắn phải fail** chạy trên chính worker tự hành (lớp phòng thủ #3)
- **Owner:** Đức (Role B) · **Depends on:** 29 · **Parallel-safe:** STEP 41 (A), 44 (B — cùng owner), 46, 47 · **Time:** 45'
- **Actions:** `Copy-Item midscene\hello_missing.yaml midscene\canary_checkout.yaml` (bấm nút **"Thanh toán"** — nút không tồn tại trên toy app `[R5 2.2]`). Thêm vào `plan.yaml`:
  ```yaml
    - task_id: t-canary-01
      capability: ui.explore
      lane: discovery
      intent: "CANARY: task chắc chắn phải fail (nút 'Thanh toán' không tồn tại). Worker báo pass = worker hỏng, không phải app tốt"
      target: {kind: web_app, base_url: "${env.APP_BASE_URL}", entry_path: "/"}
      expect_status: fail          # khoá CHỈ của plan — core dùng nó ở STEP 43, không gửi cho worker
      inputs: {flow: "midscene/canary_checkout.yaml", max_steps: 8}
      oracle: {kind: implicit_signals, signals: [element_not_found], llm_observations_allowed: false}
      expected_result_kind: candidate_finding
      budget: {wallclock_s: 300, tokens: 80000, usd: 0.5}
      determinism: {seed: null, replayable: false}
      evidence_required: [raw_output]
      retry: {max: 0, on: []}
      prefer: [midscene-cli]
  ```
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1; .\scripts\toyapp.ps1 start
  python orchestrator.py --plan plan.yaml --only t-canary-01; "exit=$LASTEXITCODE"
  $run = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
  (Get-Content "$run\results\t-canary-01.json" -Raw -Encoding UTF8 | ConvertFrom-Json).status
  python tools\validate.py result "$run\results\t-canary-01.json"
  .\scripts\toyapp.ps1 stop
  git add -A; git commit -m "STEP 42: canary task"; git push
  ```
- **DoD:** lệnh in `fail` (worker báo **fail** cho task bất khả thi — đúng kỳ vọng), `validate.py` `PASS`, `exit=0` (chỉ có task discovery ⟹ không có gate để đỏ).
- **Nếu fail:** in `pass` ⟹ **canary phát hiện self-assessment hỏng — và việc phát hiện được đó CŨNG là thành công** `[R5 2.3 #6]`: ghi vào `docs/decisions.md` (kèm video/ảnh) và báo cả nhóm; đừng "sửa" cho canary fail. In `error` ⟹ Midscene không chạy (STEP 29 "Nếu fail"). Nếu Midscene đã chuyển sang replay: canary dùng `recordings/midscene-canary` tương ứng.

### STEP 43 — Kiểm canary trong `core/verdict.py` + `report.py` (báo động riêng, không đổi verdict)
- **Owner:** Đức (Role B) · **Depends on:** 42, 40 · **Parallel-safe:** STEP 41, 46, 47 · **Time:** 45'
- **Actions:** thêm hàm `canary_alerts(results, plan_only) -> list[dict]` vào `core/verdict.py` (**không** đụng `gate_verdict`): với mỗi task có `plan_only[tid]["expect_status"]` ⟹ `{"task_id","expected","actual","ok": actual == expected, "message"}`; thiếu result ⟹ `ok=False`. `cli.py` truyền kết quả vào `RunContext.canary`; **`ok=False` ⟹ thêm dòng vào banner ở đầu report** (`CANARY HỎNG: <task_id> báo <actual> cho task chắc chắn phải <expected> — worker tự hành không đáng tin`), còn `ok=True` ⟹ dòng `CANARY <task_id>: OK (<expected> như kỳ vọng)` trong mục 3. **Verdict tổng và exit code không đổi** `[arch §6.3 luật 8]`. `tests/test_canary.py` — **4 test:** (1) canary `fail` như kỳ vọng ⟹ `ok=True`; (2) canary `pass` ⟹ `ok=False`; (3) thiếu result ⟹ `ok=False`; (4) canary hỏng **không** làm `gate_verdict` đổi. Thêm `plans/demo_canary_broken.yaml` = `plans/demo.yaml` + `expect_status: fail` trên `t-e03` (mock discovery **luôn `pass`** ⟹ canary hỏng — thử không tốn LLM).
  ```powershell
  pytest tests\test_canary.py -q
  python orchestrator.py --plan plans/demo_canary_broken.yaml; "exit=$LASTEXITCODE"
  $run = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
  Select-String -Path "$run\report.md" -Pattern "CANARY"
  git add -A; git commit -m "STEP 43: canary check"; git push
  ```
- **DoD:** `pytest tests\test_canary.py -q` in **`4 passed`**; chạy `demo_canary_broken` in **`exit=0`** (verdict không đổi) và `Select-String` thấy dòng `CANARY HỎNG` **nằm trước** `## VERDICT` (số dòng nhỏ hơn); `pytest tests\test_verdict.py -q` vẫn **`11 passed`**.
- **Nếu fail:** chậm ⟹ cut #3 (Phụ lục 4): giữ task canary trong plan, bỏ phần cảnh báo — mất lớp phòng thủ #3 (vẫn nói được bằng slide).

### STEP 44 — Chứng minh N5: thêm worker `mock2` mà không sửa `core/`
- **Owner:** Đức (Role B) · **Depends on:** 20 · **Parallel-safe:** STEP 41, 46, 47 · **Time:** 45'
- **Actions:** làm **đúng như một dev mới sẽ làm**, chỉ đọc `core/README.md` (STEP 21) — nếu README thiếu, đó là lỗi của README, sửa README (không sửa `core/`). Tạo 4 thay đổi:
  1. `workers/mock2.yaml` (từ `_template.yaml`): `name: mock2`, `adapter: adapters/mock2_adapter.py`, capability `demo.echo2` (`oracle_kinds: [trivial]`).
  2. `adapters/mock2_adapter.py`: `class Mock2Adapter(MockAdapter): NAME = "mock2"; ADAPTER_VERSION = "0.1.0"` + dòng `if __name__ == "__main__": Mock2Adapter().main()`.
  3. `schemas/capabilities.json`: thêm mục `demo.echo2` (file **dữ liệu**, không thuộc contract đóng băng `[arch §5.5]`).
  4. `plans/demo.yaml`: thêm task `t-e04` (`capability: demo.echo2`, `oracle: {kind: trivial}`, `inputs.fixture: tests/fixtures/mock_ok.json`).
  ```powershell
  git add -A; git commit -m "add mock2 worker (N5 proof)"
  python orchestrator.py --plan plans/demo.yaml; "exit=$LASTEXITCODE"
  git show --stat HEAD
  (git show --name-only --format= HEAD | Select-String -Pattern '^(core/|adapters/_base|oracle/__init__|schemas/(result|task_spec)|workers/_template)').Count
  git push
  ```
- **DoD (tiêu chí #5 `[R5 2.3]`):** `exit=0`, report có dòng `t-e04`; lệnh đếm in **`0`** (không file nào của `core/`, `_base`, `oracle/__init__`, hai schema, `_template` bị đụng); `git show --stat` có đúng 4 file. **Chụp màn hình `git show --stat`** — đây là "bằng chứng, không phải lời" cho demo (phút 7:00).
- **Nếu fail:** phải sửa `core/` mới chạy được ⟹ **đó là kết quả quan trọng nhất của STEP này**: kiến trúc không thoả N5. Ghi nguyên nhân (đọc dòng nào trong `core/` phải sửa), báo cả nhóm, và **không** giấu — đây là chỗ `V1` đã xảy ra. Sửa nguyên nhân gốc (thường là `core/` đang hard-code danh sách capability hoặc oracle) rồi chạy lại từ đầu.

---

# PHASE 3 — GOVERNANCE & CHỨNG MINH (phần tạo khác biệt)

`architecture.md` §7 có **Diff-Guard** (4 luật DG-1…DG-4 + AST diff không làm) và **không có Mutation Testing** (mục 0.3 #3, #5). Phase này:

| Luật | Cơ chế | STEP | Test chính nó |
|---|---|---|---|
| **DG-1** plan không thu hẹp phạm vi | `guards/plan_guard.py` — so 2 bản `plan.yaml` | 45 | 8 test, gồm **input xấu bị chặn** và **giới hạn đã biết** |
| **DG-2** patch của agent/healer không tự merge | `guards/protected_paths.py` (commit có trailer `Agent-Patch: true` đụng file được bảo vệ ⟹ BLOCK) + bất biến "core không sửa artifact" | 46 | 7 + 1 test |
| **DG-3** golden/baseline chỉ đổi qua người duyệt | cùng `protected_paths.py` (đổi ⟹ `NEEDS_REVIEW`) | 46 | (gộp ở trên) |
| **DG-4** heal-rate | **Không có bước code**: PoC không có healer nên không có sự kiện heal để log; cài = làm giả `[arch §7.3]`. Điều kiện kích hoạt: khi cài G4 | — | — |
| **Selection bất biến** "đổi tiêu đề PR ⟹ `selected` không đổi" | `core/selection.py` | 49 | 6 test |
| **Mutation (hợp đồng)** | 16 mutant của `result.json`/`task_spec.json` | 47 | schema **phải** từ chối cả 16 |
| **Mutation (lỗi cài sẵn + harness)** | 3 bug seed + 3 mutant harness | 48 | `tools/run_mutants.py` ra bảng bắt/lọt |

**🛑 DoD Phase 3:** `python tools\run_mutants.py` in ra **bảng** mutant nào bị bắt / mutant nào lọt, và `pytest tests\guards -q` xanh.

### STEP 45 — DG-1: `guards/plan_guard.py`
- **Owner:** Huy (Role C) · **Depends on:** 40 · **Parallel-safe:** STEP 41, 46–49 · **Time:** 45'
- **Actions:** tạo **F-27** `guards/plan_guard.py` và **F-28** `tests/guards/test_plan_guard.py` (nguyên văn, đã chạy thử). Luật: một diff `plan.yaml` bị **BLOCK** (`REQUIRES_HUMAN`, exit 1) nếu **xoá task**, **hạ task khỏi gate lane**, bỏ task khỏi `selection.floor`, xoá/thu hẹp một mục `selection.impact` hoặc `selection.always_on`; diff **chỉ thêm** ⟹ `AUTO_MERGE_OK` (exit 0). Thay đổi `oracle`/`budget`/`inputs` ⟹ chỉ **`NOTICE`** (giới hạn đã biết `[arch §7.3]`: nới `p95 < 300` thành `p95 < 3000` **không bị chặn**). Chạy trên **input xấu thật**:
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  pytest tests\guards\test_plan_guard.py -q
  Copy-Item plan.yaml runs\plan_removed.yaml
  (Get-Content runs\plan_removed.yaml -Encoding UTF8) | Where-Object { $_ -notmatch 'task_id: t-002' } | Set-Content -Encoding UTF8 runs\plan_removed.yaml   # BOM-an toàn: guard đọc utf-8-sig
  python guards\plan_guard.py --old plan.yaml --new runs\plan_removed.yaml; "removed exit=$LASTEXITCODE"
  python guards\plan_guard.py --base-ref HEAD~1 --path plan.yaml; "vs HEAD~1 exit=$LASTEXITCODE"
  git add -A; git commit -m "STEP 45: DG-1 plan guard"; git push
  ```
  (Lệnh `Where-Object` chỉ bỏ **một dòng** `task_id` — plan lúc đó có thể không còn hợp lệ về cấu trúc; guard phải hoặc báo `REQUIRES_HUMAN` hoặc exit 3 "LỖI ĐẦU VÀO" — **cả hai đều là "không tự merge"**; nếu ra `AUTO_MERGE_OK` thì guard hỏng.)
- **DoD:** `pytest tests\guards\test_plan_guard.py -q` in **`8 passed`**; lệnh `removed` **không** in `AUTO_MERGE_OK` (exit 1 hoặc 3); test `test_KNOWN_GAP_…` xanh (nghĩa là giới hạn *đã được ghi bằng test*, không bị giấu).
- **Nếu fail:** không kịp ⟹ cut theo Phụ lục 4 (DG guards cắt ngay sau Selection). Nếu chỉ kịp một luật: **"xoá task"** (test 3) — luật này chặn chính kịch bản `[arch V3]`/`[R4 S13.7 #1]`.

### STEP 46 — DG-2 / DG-3: `guards/protected_paths.py` + bất biến "core không sửa artifact"
- **Owner:** Huy (Role C) · **Depends on:** 37 · **Parallel-safe:** STEP 41–45 · **Time:** 45'
- **Actions:** tạo **F-29** `guards/protected_paths.py`, **F-30** `tests/guards/test_protected_paths.py` (nguyên văn, đã chạy thử với repo git tạm) và `tests/guards/test_core_readonly.py` (1 test): chạy `python orchestrator.py --plan plans/demo.yaml --runs-dir <tmp_path>` rồi so `git status --porcelain` **trước và sau** — không được có file nào được theo dõi bị đổi (**core chỉ ghi vào `runs/`**; đây là DG-2 ở phía hệ thống: chính orchestrator không bao giờ sửa `tests/`, `plans/`, `schemas/`, golden). Luật: commit có trailer `Agent-Patch: true` mà đụng file test/plan/schema/golden ⟹ `BLOCK` (exit 1); **golden set / baseline bị sửa bởi bất kỳ ai** ⟹ `NEEDS_REVIEW` (exit 2 — *"sửa golden set để test pass là dạng gian lận khó phát hiện nhất"* `[R2 S7.3]`); `BLOCK` thắng `NEEDS_REVIEW`.
  ```powershell
  pytest tests\guards -q
  git checkout -q -b guard-demo
  Add-Content tests\eval\golden.json " "                        # thay đổi vô hại về nội dung, nhưng LÀ đụng golden
  git commit -qam "soften golden`n`nAgent-Patch: true"                  # ASCII: PowerShell 5.1 truyen tieng Viet cho git khong dang tin cay
  python guards\protected_paths.py --base-ref HEAD~1; "exit=$LASTEXITCODE"
  git checkout -q main; git branch -q -D guard-demo
  git add -A; git commit -m "STEP 46: DG-2/DG-3 protected paths guard"; git push
  ```
- **DoD:** `pytest tests\guards -q` in **`16 passed`** (8 của STEP 45 + 7 + 1); lệnh demo in `BLOCK: … DG-2: commit Agent-Patch …` và `exit=1` (input xấu **bị chặn**); sau `checkout main`, `git status --porcelain` rỗng.
- **Nếu fail:** không kịp ⟹ giữ `test_core_readonly` (rẻ, chứng minh được ngay) và bỏ phần trailer. **Nói thẳng trong báo cáo** hai giới hạn: (1) `Agent-Patch` là **quy ước của PoC** (chưa có healer thật) — agent quên trailer thì guard không thấy; (2) guard **phát hiện, không phê duyệt** — cổng merge thật (PR review) nằm ngoài PoC.

### STEP 47 — Mutation testing của HỢP ĐỒNG: 16 mutant, schema phải từ chối cả 16
- **Owner:** Đức (Role B) · **Depends on:** 12 · **Parallel-safe:** STEP 41–46 · **Time:** 45'
- **Actions:** tạo **F-26** `tests/test_contract_mutants.py` (nguyên văn — đã chạy: **16 passed**). Mỗi mutant là một bản sao **cố ý làm hỏng** của một result/task hợp lệ; test đỏ nghĩa là schema **không** chặn ⟹ contract có lỗ hổng.
  | ID | Mutant (file · trường) | Lỗi mô phỏng | Phải bị chặn bởi |
  |---|---|---|---|
  | C1 | `result.json` · `verdict` | verdict chặn gate mà nguồn là `llm_judgment` | `allOf` #2 (N2) |
  | C2 | `verdict.confidence` | assert tất định mang confidence | `allOf` #1 |
  | C3 | `evidence[0].sha256` | hash ngắn/sai định dạng | `pattern` |
  | C4 | `status` | thiếu trạng thái | `required` |
  | C5 | `status` | trạng thái ngoài 4 giá trị (`timeout`) | `enum` |
  | C6 | top-level | thêm trường riêng (`worker_id`) | `additionalProperties:false` (N4) |
  | C7 | `verdict.gating` | `heuristic` mà `gating=true` | `allOf` #2 |
  | C8 | `evidence[0].kind` | ngoài từ vựng (`screenshots`) | `enum` |
  | C9 | `cost.wallclock_s` | thiếu duration | `required` |
  | T1 | `task_spec.json` · `retry.on` | retry `fail` | `const: error` |
  | T2 | `retry.max` | retry 3 lần | `maximum: 1` |
  | T3 | `expected_result_kind` | task discovery đòi `verdict` | `allOf` (D-09) |
  | T4 | `expected_result_kind` | task gate đòi `candidate_finding` | `allOf` (D-09) |
  | T5 | top-level | tên worker trong spec | `additionalProperties:false` |
  | T6 | `budget.usd` | thiếu trần chi phí | `required` |
  | T7 | `evidence_required[]` | ngoài từ vựng | `enum` |
  ```powershell
  pytest tests\test_contract_mutants.py -q
  git add -A; git commit -m "STEP 47: contract mutants"; git push
  ```
  **Lỗ hổng đã biết (không phải mutant lọt do lỗi của bạn):** finding `llm_judgment` thiếu `confidence` **không** bị schema chặn `[arch §0.3 G4]` — nó bị chặn ở `check_result_against_spec` (test trong `tests/test_schema.py`). Ghi câu này vào báo cáo mục 8.
- **DoD:** `pytest tests\test_contract_mutants.py -q` in **`16 passed`**.
- **Nếu fail:** một mutant **lọt** (test đỏ) ⟹ đó là phát hiện, không phải lỗi test: báo A; nếu là C1/C2/C7 thì N2 **không** được cưỡng chế bằng dữ liệu và luận điểm chính của bản propose yếu đi — phải sửa `result.json` và **chạy lại cả STEP 05, 07** (freeze lại, tag `qrs-v0.1.1`).

### STEP 48 — Mutation của LỖI CÀI SẴN: `tools/run_mutants.py` ra bảng bắt/lọt
- **Owner:** Đức (Role B) · **Depends on:** 40, 43, 31, 37 · **Parallel-safe:** STEP 45, 46, 47, 49, 50 · **Time:** 90'
- **Actions:** viết `tools/run_mutants.py` (~120 dòng). Mỗi mutant = *một cấu hình*: (bug/latency của toy app) + (biến môi trường) + (biến thể plan) → chạy orchestrator vào `--runs-dir runs/mutants` → đọc `report.json` → so với kỳ vọng. Số dòng trong cột `file/dòng` **tính động** bằng cách tìm chuỗi đánh dấu trong file (không gõ cứng số dòng).

  | Mutant | file/dòng (đánh dấu tìm trong file) | Loại lỗi mô phỏng | Test nào **phải** bắt | Kỳ vọng |
  |---|---|---|---|---|
  | **M0** (đối chứng) | — (`-Bugs none`, mọi biến đủ) | không có lỗi | — | gate **PASS** (exit 0). Đỏ ⟹ *false positive* |
  | **M1** BUG-1 | `toyapp/app.py` · `len(note_id) > LONG_ID_LEN` | id dài ⟹ 500 thay vì 4xx | `t-001` (Schemathesis) | gate **FAIL**, `t-001` ❌ |
  | **M2** BUG-2 | `toyapp/static/index.html` · `QC_BUGS.includes('2')` | UI không cập nhật sau Xoá | `t-101` (Midscene + telemetry) | finding `dom_unchanged`; gate **không** đổi. *Chỉ chạy với `--with-midscene` (tốn LLM)* |
  | **M3** BUG-3 | `toyapp/summarizer.py` · `[[long]]` | summary dài hơn body | `t-003` metric `summary_shorter_than_body` | gate **FAIL**, `t-003` ❌ |
  | **M4** latency | `toyapp/app.py` · `await asyncio.sleep(LATENCY_MS` (`-LatencyMs 400`) | hồi quy hiệu năng | `t-002` (k6) `p95 < 300` | gate **FAIL**, `t-002` ❌ |
  | **M5** crash | plan tạm: `t-002.inputs.script = tests/perf/broken.js` | **worker hỏng ≠ test fail** | `t-002` phải `status=error` | gate **FAIL** nhãn hạ tầng, `status` **là `error`, không phải `fail`** |
  | **M6** skipped | bỏ `OPENAI_API_KEY` khỏi môi trường của lần chạy (bug tắt) | thiếu key ⟹ worker không chạy (V2) | `t-003` phải `skipped` | gate **YELLOW**, banner **ở đầu** report, exit 0 |
  | **C1–C9, T1–T7** | (từ STEP 47) chạy `pytest tests\test_contract_mutants.py --junitxml=…` rồi đọc theo **tên test** | contract | schema | mọi mutant bị chặn |

  Đầu ra: bảng in ra console **và** ghi `docs/mutants.md` với đúng 5 cột `Mutant | file/dòng | loại lỗi mô phỏng | test nào phải bắt | kết quả thực tế`, cột cuối là **`BẮT`** / **`LỌT`** (M0: **`XANH`** / **`ĐỎ`**; M2 khi không có `--with-midscene`: **`BỎ QUA`**). Exit code `0` nếu **mọi mutant thuộc gate lane đều `BẮT`** và M0 `XANH`; `1` nếu có mutant gate lọt.
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  python tools\run_mutants.py; "exit=$LASTEXITCODE"
  python tools\run_mutants.py --with-midscene; "exit(with midscene)=$LASTEXITCODE"      # làm 1 lần
  Get-Content docs\mutants.md
  git add -A; git commit -m "STEP 48: seeded-fault mutation table"; git push
  ```
- **DoD (🛑 DoD Phase 3):** `run_mutants.py` (không Midscene) in bảng với **M0, M1, M3, M4, M5, M6 + 16 mutant hợp đồng** — **M0 `XANH`, các mutant còn lại `BẮT`** — và `exit=0`; `docs/mutants.md` có đúng 5 cột; M5 ghi rõ `status=error`.
- **Nếu fail:** mutant nào **`LỌT`** là *phát hiện thật*, không phải lỗi bảng: **M5 ra `fail` thay vì `error`** ⟹ `k6_adapter` đang biến worker hỏng thành test fail (vi phạm nguyên tắc "hai thứ này không được lẫn"); **M6 ra `PASS`** ⟹ `skipped` đang bị nuốt (V2); **M0 `ĐỎ`** ⟹ false positive. Sửa adapter/oracle tương ứng (owner của nó), **không** nới bảng kỳ vọng. Quá giờ ⟹ chỉ làm M0, M1, M3, M5 (bốn mutant chứng minh: xanh khi sạch, đỏ khi có lỗi, crash ≠ fail).

### STEP 49 — Selection tất định: chọn phạm vi theo diff, KHÔNG đọc nội dung PR (**cắt trước nhất**)
- **Owner:** Nghĩa (Role A) · **Depends on:** 41 (chỉ bắt đầu khi tiêu chí #1–#4 đã đạt `[R5 R9]`) · **Parallel-safe:** STEP 45–48 · **Time:** 45'
- **Actions:** thêm vào `plan.yaml` một task giả `t-sec-01` (`capability: security.stub`, `lane: gate`, `oracle: {kind: trivial}`, `inputs.fixture: tests/fixtures/mock_ok.json`, mock worker xử lý) và khối:
  ```yaml
  selection:
    floor: [t-001, t-002]                     # luôn chạy — chặn selection thu hẹp về 0
    impact:                                   # path -> task (PoC: bảng tay, arch §6.1)
      "toyapp/app.py":         [t-000, t-001, t-003]
      "toyapp/summarizer.py":  [t-000, t-003]
      "toyapp/static/*":       [t-101]
    always_on:
      - when: "toyapp/auth/*"
        run: [t-sec-01]
  ```
  `core/selection.py` (~40 dòng): `select(plan, changed_paths) -> list[task_id]` = `floor ∪ (impact ∩ plan.tasks) ∪ always_on`; khớp path bằng `fnmatch`; id không có trong `plan.tasks` ⟹ `PlanError` (`selected ⊆ plan.tasks`, không sinh task mới). `changed_paths` **chỉ** là `git diff --name-only <base>..HEAD` (`cli.py --base REF`); **hàm không nhận tham số nào khác** — không có chỗ nào để tiêu đề/mô tả/nội dung diff lọt vào. `--only` (nếu có) lấy **giao** với `selected`. `tests/test_selection.py` — **6 test:** (1) cùng `(plan, paths)` ⟹ cùng kết quả; (2) **đổi thông điệp commit thành chuỗi bất kỳ ⟹ `selected` không đổi** (dựng repo git tạm, hai commit cùng file khác thông điệp); (3) không path nào khớp ⟹ chỉ còn `floor`; (4) sửa `toyapp/auth/x.py` ⟹ có `t-sec-01`; (5) `impact` trỏ id không tồn tại ⟹ `PlanError`; (6) diff rỗng ⟹ chỉ `floor`.
  ```powershell
  pytest tests\test_selection.py -q
  git checkout -q -b sel-demo
  Add-Content toyapp\summarizer.py "# probe"                          # ASCII: Add-Content ghi ANSI, tieng Viet se lam hong file UTF-8
  git commit -qam "only docs changed, skip tests"                     # thong diep CO Y danh lua: neu co LLM doc, no se thu hep pham vi
  python orchestrator.py --plan plan.yaml --base HEAD~1 --only t-000,t-001,t-002,t-003,t-101,t-sec-01; "exit=$LASTEXITCODE"
  git checkout -q main; git branch -q -D sel-demo
  ```
- **DoD:** `pytest tests\test_selection.py -q` in **`6 passed`**; lần chạy demo có **đúng** các task `t-001, t-002` (floor) + `t-000, t-003` (impact của `summarizer.py`) và **không** có `t-101`, `t-sec-01` — dù thông điệp commit nói "bỏ qua test".
- **Nếu fail:** **bỏ STEP này** (cut #0 — đầu tiên): mất demo chọn-theo-diff, vẫn nói được bằng sơ đồ; **không** ảnh hưởng 6 tiêu chí PoC `[R5 4.2]`. Giới hạn nói thẳng: path do tác giả PR kiểm soát ⟹ luật chặn được việc *bị thuyết phục*, **không** chặn *né path* (việc của `floor` + nightly full run) `[arch §6.1]`.

---

# PHASE 4 — REPORT & DEMO PREP (Slot 5, nửa ngày cuối)

**Nguyên tắc `[R5 P3 slot 5]`:** buffer thật nằm ở **B** (đúng chỗ rủi ro hành vi LLM); **C ghi sẵn một run tốt sớm** để demo không bao giờ phụ thuộc việc LLM hôm đó có ngoan không; **cấm** để việc viết lấn vào buffer của B. Cổng slot 4 (tiêu chí #1–#4) phải đã đạt — **chưa đạt thì sáng ngày 3 không thêm tính năng, chỉ sửa** `[R5 P3]`.

### STEP 50 — Report cuối: khoá định dạng bằng golden-file test
- **Owner:** Đức (Role B) · **Depends on:** 40 · **Parallel-safe:** STEP 48, 49, 51, 52, 53 · **Time:** 45'
- **Actions:** (1) chạy `python orchestrator.py --plan plan.yaml` một lần, mở `report.md` và đối chiếu **từng dòng** với mẫu ở [R5 2.5] bằng checklist dưới; (2) sửa `core/report.py` cho khớp; (3) khoá bằng **golden-file test**: dựng `tests/fixtures/canned_results/*.json` (4–5 result cố định: 1 gate pass, 1 gate fail, 1 deepeval với G-Eval, 1 discovery có finding, 1 skipped) + `RunContext` với `generated_at="2026-01-01 00:00"`, viết `tests/test_report_golden.py` so **đúng từng ký tự** với `tests/fixtures/report_expected.md`. Cho phép tái sinh file kỳ vọng khi *chủ ý* đổi định dạng: `if os.environ.get("QC_REGEN"): ghi file`; sinh lần đầu bằng `$env:QC_REGEN = "1"; pytest tests\test_report_golden.py; Remove-Item Env:QC_REGEN` rồi **đọc bằng mắt** file vừa sinh trước khi commit.
  Checklist đối chiếu (mỗi dòng phải `True`):
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  $run = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
  $md = Get-Content "$run\report.md" -Raw -Encoding UTF8
  "# QC Gate Report","plan:","wallclock","## VERDICT","## 1. DETERMINISTIC ASSERT","gate_verdict =","## 2. LLM JUDGMENT","## 3. HEURISTIC / DISCOVERY","## 4. SKIPPED / ERROR","## 5. AUDIT" | ForEach-Object { "{0,-30} {1}" -f $_, $md.Contains($_) }
  Copy-Item "$run\report.md" docs\sample_report.md             # cho slide / báo cáo
  pytest tests\test_report_golden.py tests\test_report.py -q
  git add -A; git commit -m "STEP 50: report golden test + sample"; git push
  ```
  Thêm vào header dòng 3 đúng dạng `wallclock <mm>m<ss>s · LLM tokens: <N> (tasks: <ids>) · cost $<x>`: `N` = tổng `cost.tokens` không-null; `<ids>` = task có `tokens > 0`; `cost` = tổng `cost.usd` không-null. *(Mẫu ở [R5 2.5] ghi "LLM calls: 3" — QRS **không** có số lần gọi, chỉ có token; ta báo cái đo được.)*
- **DoD:** `pytest tests\test_report_golden.py -q` in **`1 passed`**; **10 dòng** checklist đều `True`; `docs/sample_report.md` tồn tại; tiêu chí #3 vẫn đúng (`pytest tests\test_report.py -q` vẫn `6 passed`).
- **Nếu fail:** golden test quá dễ vỡ (đổi một chữ là đỏ) ⟹ đó là *có chủ đích* — ai đổi định dạng phải chạy `QC_REGEN` và đọc diff. Không có thời gian ⟹ bỏ golden test, giữ checklist 10 dòng.

### STEP 51 — Kịch bản demo + ghi sẵn một run tốt (bảo hiểm cho live demo)
- **Owner:** Huy (Role C) · **Depends on:** 41, 48 · **Parallel-safe:** STEP 50, 52, 53, 55 · **Time:** 75'
- **Actions:** tạo **F-31** `scripts/repeat.ps1` và **F-32** `scripts/demo.ps1` (nguyên văn, đã kiểm cú pháp và `repeat.ps1` đã chạy thử với orchestrator giả). `demo.ps1` = 8 chặng đúng timeline [R5 5.2]; `-Auto` bỏ chờ Enter (để bấm giờ); **`-Fast`** chỉ chạy các task **gate** thật và lấy phần discovery từ **run ghi sẵn** (có in dòng cảnh báo "RECORDED") — dùng khi Midscene quá chậm cho 10 phút.
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  .\scripts\toyapp.ps1 start
  .\scripts\repeat.ps1 -Times 3; "repeat exit=$LASTEXITCODE"          # [R5 quy tắc 3]: chạy thử 3 lần liên tiếp TRƯỚC khi demo
  $good = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
  New-Item -ItemType Directory -Force recordings | Out-Null
  Copy-Item $good recordings\demo-good-run -Recurse -Force
  Start-Transcript -Path recordings\demo-transcript.txt -Force
  .\scripts\demo.ps1 -Auto
  Stop-Transcript
  .\scripts\toyapp.ps1 stop
  git add recordings; git commit -m "STEP 51: demo scripts + recorded good run"; git push
  ```
  Quay **video màn hình** một lần chạy đầy đủ bằng Xbox Game Bar (`Win + Alt + R`, có sẵn trong Windows 11 — không cài gì thêm); **không** commit video (nặng) — ghi đường dẫn file vào `docs/decisions.md`.
- **DoD:** `repeat.ps1 -Times 3` in **`ALL 3 RUNS OK`** và `repeat exit=0`; `git ls-files recordings/demo-good-run/report.md recordings/demo-transcript.txt` in **2 dòng**; `demo.ps1 -Auto` in đủ 8 dòng `stage1…stage8` và dòng `TOTAL elapsed`.
- **Nếu fail:** `repeat` không `OK` do **Midscene** ⟹ đó chính là lý do STEP 52 tồn tại; C chuyển sang chạy `-Fast` và ghi `recordings/demo-good-run` từ lần chạy tốt gần nhất (nhãn "replay"). Một lần chạy 3/3 chỉ do LLM lúc lên lúc xuống ⟹ **không** ép demo phụ thuộc vào nó: dùng `-Fast` + nói rõ "discovery lane: bản ghi sẵn" **trước khi bị hỏi** `[R5 quy tắc 2]`.

### STEP 52 — Midscene ổn định: chạy 3 lần liên tiếp (buffer có giới hạn thời gian)
- **Owner:** Đức (Role B) · **Depends on:** 29 · **Parallel-safe:** STEP 48–51, 53–56 · **Time:** 60' (timebox cứng — đây là buffer, không phải việc mới)
- **Actions:**
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  .\scripts\toyapp.ps1 start
  .\scripts\repeat.ps1 -Times 3 -Only "t-101,t-canary-01"; "repeat exit=$LASTEXITCODE"
  Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 3 | ForEach-Object {
      $c = (Get-Content "$($_.FullName)\results\t-canary-01.json" -Raw -Encoding UTF8 | ConvertFrom-Json).status
      $e = (Get-Content "$($_.FullName)\results\t-101.json" -Raw -Encoding UTF8 | ConvertFrom-Json)
      "{0}: canary={1} explore_status={2} findings={3} tokens={4}" -f $_.Name, $c, $e.status, @($e.findings).Count, $e.cost.tokens
  }
  .\scripts\toyapp.ps1 stop
  ```
  Ghi 3 dòng kết quả vào `docs/decisions.md` (đây là **số đo phương sai của agent** — báo cáo mục 8 cần nó).
- **DoD:** `repeat.ps1` in **`ALL 3 RUNS OK`** (không result `error`, mọi result hợp lệ QRS) **và** `canary=fail` ở **cả 3 dòng** (worker tự hành không báo pass cho task bất khả thi).
- **Nếu fail:** làm theo thứ tự (`[R5 P4.1 #3]`): (1) giảm `max_steps` của `t-101` xuống **8** và thu hẹp `avoid`; (2) viết lại câu lệnh flow rõ hơn; (3) **dùng run ghi sẵn** — adapter đọc `QC_REPLAY_MIDSCENE` (STEP 29), gắn nhãn *replay* trong `adapter_notes` và trên slide. Contract **vẫn được chứng minh**. Hết 60' mà chưa xong ⟹ dừng và chuyển (3); **buffer của B không được bị lấn bởi việc viết** — nếu buffer bị dùng hết, mục 1–2, 8 chuyển sang A (mục 8) và C (mục 1–2) `[R5 P3]`.

### STEP 53 — Báo cáo: mục 0 (TL;DR), 3 (prior art), 4 (đề xuất kiến trúc), 9 (lộ trình)
- **Owner:** Nghĩa (Role A) · **Depends on:** 41 · **Parallel-safe:** STEP 50–52, 55, 56 · **Time:** 90'
- **Actions:** tạo `docs/report/00-tldr.md`, `03-prior-art.md`, `04-architecture.md`, `09-roadmap.md`. Đây là **ráp lại** từ tài liệu đã có — không nghiên cứu mới `[R5 P3 nguyên tắc 3]`. Nội dung bắt buộc:
  | File | Bắt buộc có | Lấy từ |
  |---|---|---|
  | `00-tldr.md` (1 trang) | 4 ý: **không** propose "một AI agent làm QC" · propose **contract phân biệt nguồn phán quyết + chính sách 2 lane** trên lớp điều phối mỏng · **gate xanh = 0 lời gọi LLM** · bằng chứng = PoC 4 worker khác loại, 1 schema, verdict tái lập; + **câu định vị chốt [R11]** | `[R5 P5.1 mục 0]`, `[R4 R11]` |
  | `03-prior-art.md` | Playwright Test Agents (ủng hộ hướng ta) · **Hercules** / **Testkube** / **ReportPortal**: từng cái làm tới đâu, thiếu gì · "cái **không cái nào** có: verdict provenance" · vì sao không dùng thẳng = **LLM trong hay ngoài đường phán quyết** · câu: *"Hercules là lựa chọn hợp lệ cho discovery lane"* (kèm AGPL-3.0) · Hercules **có** `ADDITIONAL_TOOL_DIRS` và MCP nav agent | `[R4 S14.2]` + **`[R5 CORRECTION]`** (bản đúng, không phải bản R4) |
  | `04-architecture.md` | N1–N5 · 2 lane + mũi tên *promote* · contract 3 mảnh **với JSON thật** (chèn 3 ví dụ từ `examples/`) · "LLM là người tố giác, không phải quan toà" · AI app là capability hạng nhất (7 cấu phần) · **câu lý do chọn orchestrator–worker [R10]** | `[arch §2–§6]` |
  | `09-roadmap.md` | 4 pha: (1) web functional + API trên 1–2 repo pilot, gate PR · (2) mobile · (3) perf nightly · (4) AI-eval liên tục; mỗi pha có cột "phần contract/policy ta thêm vào"; **đường di cư sang Testkube/ReportPortal**; lưu ý Keploy ở pha 1 cần WSL2/Docker trên Windows | `[R5 P5.1 mục 9, R10]`, `[arch D2]` |
  **Ba câu KHÔNG được xuất hiện như một khẳng định** (`[R11]`): "chưa ai làm orchestration cho QC" · "Hercules đóng" · "chưa ai đặt LLM ngoài đường phán quyết" (đúng phải là: *chưa ai áp nguyên tắc đó xuyên nhiều loại worker kèm `verdict_source`, đặc biệt cho AI application*).
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase
  Select-String -Path docs\report\*.md -Pattern "chưa ai làm orchestration","Hercules đóng","chưa ai đặt LLM ngoài đường phán quyết" | Measure-Object | Select-Object -ExpandProperty Count
  git add -A; git commit -m "STEP 53: report sections 0,3,4,9"; git push
  ```
- **DoD:** 4 file tồn tại; lệnh `Select-String` in **`0`** (không có câu bị cấm); `Select-String -Path docs\report\03-prior-art.md -Pattern "ADDITIONAL_TOOL_DIRS","Testkube","ReportPortal","discovery lane"` khớp **cả 4** từ khoá; `00-tldr.md` chứa cụm `0 lời gọi LLM`; `04-architecture.md` chứa đúng 3 khối JSON ví dụ.
- **Nếu fail:** không kịp ⟹ **ưu tiên `03-prior-art.md`** — thiếu nó là "lỗ hổng lớn nhất của bản propose" `[R4 (b)]`: mentor tìm 10 phút là ra Hercules. Mục 9 rút gọn còn bảng 4 pha.

### STEP 54 — Bộ slide + slide "So với `QC_Agent_Proposal`"
- **Owner:** Nghĩa (Role A) · **Depends on:** 53 · **Parallel-safe:** STEP 55, 56 · **Time:** 60'
- **Actions:** viết `docs/slides/outline.md` với **12 slide**, mỗi slide một dòng `## Slide NN — <tiêu đề>` + 2–3 gạch đầu dòng + `nguồn:`. Thứ tự **thuyết phục, không theo thứ tự nghiên cứu** `[R5 P5.1]`: (1) TL;DR (2) vì sao "agent QC" dễ sai — bảng 6 khái niệm + TestGen-LLM 57% (3) prior art (4) kiến trúc + 2 lane (5) contract + JSON thật (6) **demo** (7) automate tối đa 7/10 (8) kinh tế: gate xanh 0 token (9) giới hạn & việc chưa làm (10) lộ trình 4 pha (11) **So với `QC_Agent_Proposal`** — 3 cột *giống / khác / lấy gì*, ~30 phút, nguồn `knowledge/_derived/08` (**file này không có trong thư mục tài liệu hiện tại — A lấy từ `knowledge/_derived/`**) (12) phụ lục: research map + bảng fact cần double-check. **Slide (6) và (9) phải liệt kê trung thực cái gì là MOCK / REPLAY / SUT giả lập** (stub summarizer; bug cài sẵn; Midscene replay nếu có; baseline viết tay; số đo chỉ của toy app) — nói **trước khi bị hỏi** `[R5 quy tắc 2]`. Dựng bộ slide thật bằng công cụ team quen dùng từ outline này.
  ```powershell
  (Select-String -Path docs\slides\outline.md -Pattern '^## Slide ').Count
  git add -A; git commit -m "STEP 54: slides outline"; git push
  ```
- **DoD:** `(Select-String -Path docs\slides\outline.md -Pattern '^## Slide ').Count` = **12**; outline chứa cụm `So với QC_Agent_Proposal` và cụm `MOCK` (hoặc `REPLAY`) ở slide 6 hoặc 9.
- **Nếu fail:** không có `_derived/08` trong tầm tay ⟹ slide 11 ghi *"[thiếu nguồn: knowledge/_derived/08 — A bổ sung]"* thay vì bịa nội dung.

### STEP 55 — Báo cáo: mục 1 (bối cảnh), 2 (landscape), 8 (giới hạn)
- **Owner:** Đức (Role B) · **Depends on:** 52 (chỉ để tránh lấn buffer; có thể bắt đầu ngay khi buffer còn dư) · **Parallel-safe:** STEP 53, 54, 56 · **Time:** 60'
- **Actions:** tạo `docs/report/01-context.md`, `02-landscape.md`, `08-limitations.md`. **Ráp lại** từ RUN 1–3 + [arch §9]:
  | File | Bắt buộc có | Lấy từ |
  |---|---|---|
  | `01-context.md` | bảng 6 khái niệm (S1) · neo TestGen-LLM: **57% pass ổn định, 43% bị vứt, filter tất định mới là sản phẩm** · câu chốt: *LLM sinh ứng viên, oracle tất định quyết định* | `[R1 S1]` |
  | `02-landscape.md` | 4 câu mentor hỏi: taxonomy **9 nhóm theo 2 trục** · roster **14** có nhãn độ tin + last activity (**không dùng số sao làm tín hiệu sống**) · capability matrix **tách [A] tự hành / [B] hỗ trợ** · **7 surface** xếp hạng độ chín · **3 chỗ bị overhype** kèm bằng chứng phản bác | `[R2 S5–S8]`, `[R3 S10]` |
  | `08-limitations.md` | **mọi mục của [arch §9]** + phần riêng của PoC: SUT giả lập (stub) · 3 lỗi là **cài sẵn** · định nghĩa "lọc" của tiêu chí #4 (mục 0.3 #4 file này) · DG-2 dựa trên trailer `Agent-Patch` (quy ước) · DG-4 chưa cài · AST diff không làm · schema không ép `confidence` ở finding (G4) · baseline G-Eval viết tay · Midscene: replay/mock nếu có · số đo chỉ của toy app · `[KHÔNG KỊP SPRINT NÀY]`: judge calibration, mobile, Keploy/eBPF, auto-promote, lưu trữ evidence, bảo mật orchestrator | `[arch §9]`, `[R5 4.2]` |
  **Mục 8 là mục ghi điểm, không phải mất điểm:** *một bản propose không có mục "chúng tôi chưa chắc về…" thì mentor mặc định là team chưa kiểm* `[R5 P5.1]`.
  ```powershell
  Select-String -Path docs\report\08-limitations.md -Pattern "SUT giả lập","cài sẵn","Agent-Patch","KHÔNG KỊP SPRINT NÀY","confidence" | ForEach-Object { $_.Pattern } | Sort-Object -Unique
  git add -A; git commit -m "STEP 55: report sections 1,2,8"; git push
  ```
- **DoD:** 3 file tồn tại; `08-limitations.md` khớp **cả 5** từ khoá ở lệnh trên; `02-landscape.md` chứa `14` (roster) và `9 nhóm`.
- **Nếu fail:** B dùng hết buffer cho Midscene ⟹ mục 8 chuyển **A**, mục 1–2 chuyển **C** `[R5 P3]`; **ưu tiên mục 8** (đó là chỗ ghi điểm).

### STEP 56 — Báo cáo: mục 5 (demo), 6 (automate tối đa), 7 (kinh tế) — có SỐ ĐO THẬT từ PoC
- **Owner:** Huy (Role C) · **Depends on:** 41 · **Parallel-safe:** STEP 53–55 · **Time:** 90'
- **Actions:** tạo `docs/report/05-demo.md`, `06-automation.md`, `07-economics.md`.
  | File | Bắt buộc có | Lấy từ |
  |---|---|---|
  | `05-demo.md` | bảng kịch bản 5–10 phút (đúng 8 chặng của `demo.ps1`) · **3 quy tắc an toàn** (có bản ghi sẵn; nói rõ cái nào là mock **trước khi bị hỏi**; không chạy live thứ phụ thuộc API ngoài khi chưa thử 3 lần sáng hôm đó) · **5 câu hỏi khó** Q1–Q5 + câu dự phòng "Đây có thật là agent không?" và Q6 (Planner đọc diff) — dùng lại câu trả lời ở [R5 5.3] | `[R5 5.2, 5.3]` |
  | `06-automation.md` | **7/10 bước tự động hoàn toàn, 3 bước còn human + lý do từng chỗ** (không đảo ngược rẻ được / chuẩn mực bị đổi) · bảng 7 điểm human ở [arch §6.5] | `[arch §6.5]`, `[R4 S13.7]` |
  | `07-economics.md` | **gate xanh = 0 token LLM** · 3 khoản (planning / routing / aggregation) và cách triệt tiêu · bảng chi phí theo 9 nhóm (S11) · dữ liệu ra ngoài xếp theo mức nghiêm trọng (G8 > G6/G7 > G5 > G1–G3) · **bảng số đo của PoC** (lệnh dưới) ghi rõ "toy app, không ngoại suy" | `[R3 S11]`, `[R5 Q4]` |
  Số đo thật (chạy sau STEP 51, trên run tốt nhất):
  ```powershell
  cd $HOME\qc-agent-poc; . .\scripts\env.ps1
  $run = "recordings\demo-good-run"
  Get-ChildItem "$run\results\*.json" | ForEach-Object { $j = Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
      [pscustomobject]@{ task = $j.task_id; worker = $j.worker.name; status = $j.status; wallclock_s = $j.cost.wallclock_s; tokens = $j.cost.tokens; usd = $j.cost.usd } } | Format-Table -AutoSize
  ```
  Dán bảng đó vào `07-economics.md`, kèm câu: *"Số đo của toy app; chi phí ở quy mô suite thật **chưa đo** `[R5 Q4]`."* Tính riêng: tổng token của **các task gate** (phải là **0** ở lane gate trừ DeepEval G-Eval nếu bật) — đó là bằng chứng của "gate xanh = 0 token" (nếu G-Eval bật thì nói rõ nó là **advisory** và nằm ngoài verdict).
- **DoD:** 3 file tồn tại; `07-economics.md` chứa bảng số đo với **≥ 5 dòng** task và cụm `chưa đo`; `06-automation.md` chứa `7/10` và `3 bước`; `05-demo.md` chứa `Q5` và `Hercules`.
- **Nếu fail:** không kịp ⟹ **ưu tiên `07-economics.md`** (lập luận kinh tế mạnh nhất: gate xanh 0 token `[R3]`). Bảng số đo có thể lấy thẳng từ `docs/sample_report.md`.

### STEP 57 — 🛑 Dry-run cuối CÙNG NHAU, bấm giờ thật
- **Owner:** Nghĩa (Role A) (chủ trì; **Đức (Role B) và Huy (Role C) bắt buộc có mặt**) · **Depends on:** 51, 54, 55, 56 · **Parallel-safe:** none · **Time:** 60'
- **Actions:** một người nói (A), một người gõ (C), một người bấm giờ và ghi lỗi (B). **Chỉ dùng lệnh trong `demo.ps1`** — ai phải gõ lệnh lạ thì đó là lỗi của kịch bản, ghi lại.
  ```powershell
  cd $HOME\qc-agent-poc; git pull --rebase; . .\scripts\env.ps1
  .\scripts\toyapp.ps1 start
  Start-Transcript -Path recordings\dryrun-transcript.txt -Force
  .\scripts\demo.ps1 -Auto             # thêm -Fast nếu stage2 vượt 3:00
  Stop-Transcript
  .\scripts\toyapp.ps1 stop
  git tag poc-demo-1; git push --tags
  ```
  Điền bảng thời gian thật vào `docs/decisions.md` (lấy từ dòng `stageN mm:ss` của `demo.ps1`):
  | Chặng | Kịch bản `[R5 5.2]` | Thời điểm thực (mm:ss, tích luỹ) | Vấn đề gặp |
  |---|---|---|---|
  | 1 | plan.yaml (0:00–0:45) | | |
  | 2 | chạy orchestrator (0:45–2:30) | | |
  | 3 | report 3 mục (2:30–4:30) | | |
  | 4 | finding f-* / promote (4:30–5:30) | | |
  | 5 | chạy lần 2 + `diff` (5:30–7:00) ⭐ | | |
  | 6 | `git show --stat` N5 (7:00–8:00) | | |
  | 7 | canary (8:00–9:00) | | |
  | 8 | bảng mutation | | |
- **DoD:** dòng `TOTAL elapsed` **≤ 10:00**; chặng 5 in `IDENTICAL`; **không ai phải gõ lệnh ngoài `demo.ps1`**; bảng thời gian đã điền; tag `poc-demo-1` có trên remote (`git ls-remote --tags origin poc-demo-1` in 1 dòng); và **đã thử phát lại bản ghi dự phòng** (`recordings/demo-transcript.txt` + video) **một lần**.
- **Nếu fail:** > 10:00 ⟹ (1) bật `-Fast`; (2) bỏ chặng 8 (bảng mutation là *phụ*); (3) rút chặng 3 còn cuộn qua mục 1–2–3. Chặng 5 không `IDENTICAL` ⟹ 🛑 **không đem demo lên** cho tới khi hiểu nguyên nhân (Troubleshooting #5) — đây là "khoảnh khắc mạnh nhất" và cũng là **tiêu chí #4**. Live hỏng ngay lúc trình bày ⟹ bật bản ghi dự phòng, nói thẳng "đây là bản ghi", tiếp tục.

---

# PHỤ LỤC

# Phụ lục 1 — Cây thư mục repo đầy đủ (mọi file sẽ tạo)

Chú thích: `(NN)` = STEP tạo file · `F-xx` = có bản nguyên văn ở Phụ lục 6 · `(git✗)` = không nằm trong git.

```
qc-agent-poc/
├─ README.md                               (03)   dòng 1: "Orchestrator 'ngu' là orchestrator đúng"
├─ .gitignore  .gitattributes  pytest.ini  (03)
├─ .python-version (03)  .node-version (04)  .k6-version (04)
├─ .env.example (03)     .env (git✗ — chứa key)
├─ requirements.txt (04)  requirements.lock (04)   ← "pin" phiên bản Python
├─ package.json  package-lock.json (04)            ← "pin" phiên bản @midscene/cli
├─ orchestrator.py                         (20)   shim 4 dòng → core.cli.main
├─ plan.yaml                               (32,34,40,42,49)   plan THẬT, viết tay, được commit
├─ plans/
│   ├─ demo.yaml  demo_fail.yaml           (20)   plan với worker giả (Phase 1)
│   └─ demo_canary_broken.yaml             (43)
├─ schemas/                                        ── CONTRACT (đóng băng ở STEP 07) ──
│   ├─ task_spec.json                      (05)  F-06
│   ├─ result.json                         (05)  trích từ docs/architecture.md §4.1 bằng F-04
│   ├─ capabilities.json                   (05)  F-09  (file DỮ LIỆU — không đóng băng)
│   └─ CONTRACT.sha256                     (07)  sinh bởi freeze_contract.py
├─ workers/
│   ├─ _template.yaml                      (05)  F-13  (đóng băng)
│   ├─ mock.yaml (15)  mock2.yaml (44)  schemathesis.yaml (25)  k6.yaml (31)
│   └─ midscene.yaml (28)  http-collect.yaml (36)  deepeval.yaml (37)
├─ core/                                           ── lõi orchestrator (KHÔNG có tên worker) ──
│   ├─ __init__.py (12)
│   ├─ schema.py (12) F-22    evidence.py (12)    plan.py (16)    runner.py (16)
│   ├─ registry.py (14)       verdict.py (17) F-10    signature.py (18)
│   ├─ report.py (19)         cli.py (20)             selection.py (49)
│   └─ README.md (21)
├─ oracle/                                         ── bộ so sánh dùng chung ──
│   ├─ __init__.py (13)   trivial.py (13)   checks.py (13)   threshold.py (27)   signals.py (27)
├─ adapters/
│   ├─ __init__.py (12)   _base.py (13)
│   ├─ mock_adapter.py (15)  mock2_adapter.py (44)
│   ├─ schemathesis_adapter.py (25)  k6_adapter.py (31)  midscene_adapter.py (28)
│   └─ collect_adapter.py (36)  deepeval_adapter.py (37)
├─ guards/                                         ── Diff-Guard (Phase 3) ──
│   ├─ __init__.py (12)  plan_guard.py (45) F-27  protected_paths.py (46) F-29
├─ toyapp/                                         ── SUT giả ──
│   ├─ __init__.py (06)  summarizer.py (06) F-14  app.py (11) F-16
│   └─ static/index.html                   (22)  F-17
├─ midscene/
│   ├─ hello.yaml  hello_missing.yaml  hello_assert_false.yaml   (23)
│   ├─ explore_notes.yaml (28)   canary_checkout.yaml (42)
├─ baselines/geval.json                    (40)   baseline viết tay
├─ tests/
│   ├─ test_summarizer.py (06)  test_toyapp.py (11) F-21  test_contract_frozen.py (05) F-19
│   ├─ test_schema.py (12) F-24  test_evidence.py (12)  test_base.py (13)  test_registry.py (14)
│   ├─ test_runner.py (16)  test_runner_parallel.py (39)  test_verdict.py (17) F-25
│   ├─ test_signature.py (18)  test_report.py (19)  test_report_golden.py (50)  test_cli.py (20)
│   ├─ test_oracle.py (27)  test_midscene_adapter.py (28)  test_k6_adapter.py (31)
│   ├─ test_schemathesis_adapter.py (25)  test_collect_adapter.py (36)  test_deepeval_adapter.py (37)
│   ├─ test_canary.py (43)  test_contract_mutants.py (47) F-26  test_selection.py (49)
│   ├─ guards/  test_plan_guard.py (45) F-28  test_protected_paths.py (46) F-30  test_core_readonly.py (46)
│   ├─ eval/    golden.json (06) F-15  hello_probe.py (35)  test_summarize.py (37)
│   ├─ perf/    notes_list.js  notes_list_thresholds.js  broken.js (30)
│   ├─ fixtures/  mock_ok.json mock_fail.json mock_finding.json task_mock_pass.json task_mock_fail.json (15)
│   │             task_st.json (25)  task_ms.json task_ms_canary.json (28)  task_k6.json task_k6_broken.json (31)
│   │             task_collect.json (36)  task_de.json task_de_badpath.json (37)
│   │             canned_results/*.json  report_expected.md (50)  fake_adapters/*.py (16)
│   └─ samples/  ← đầu ra THẬT của tool, lưu ở các bước "hello world"
│                  midscene_summary.{pass,fail_missing_element,fail_aiassert}.json  midscene_stdout.no_key.txt (23)
│                  st_bug_on.* st_bug_off.* st_server_down.txt (24)   k6_summary.*.json k6_stdout.script_error.txt (30)
│                  de_junit_mixed.xml de_junit_pass.xml de_geval_stdout.txt (35)
├─ tools/
│   ├─ extract_from_arch.py (05) F-04   validate.py (05) F-05   freeze_contract.py (05) F-12
│   ├─ diff_runs.py (41) F-11   review_adapter.py (26) F-23   run_mutants.py (48)
├─ scripts/
│   ├─ env.ps1 (04) F-01  bootstrap.ps1 (04) F-02  doctor.ps1 (04) F-03  toyapp.ps1 (11) F-18
│   ├─ repeat.ps1 (51) F-31   demo.ps1 (51) F-32
├─ examples/
│   ├─ result.e2e_pass.json  result.e2e_fail.json  result.ai_eval.json   (05, trích từ architecture.md §4.2)
│   ├─ result.k6_pass.json (05) F-20   task.k6.json (05) F-07   task.midscene.json (05) F-08
├─ docs/
│   ├─ architecture.md  plan-execution.md (03)   decisions.md (06→)   mutants.md (48)   sample_report.md (50)
│   ├─ research/qcagent-run1…run5-*.md (03)
│   ├─ report/  00-tldr  01-context  02-landscape  03-prior-art  04-architecture  05-demo  06-automation  07-economics  08-limitations  09-roadmap  (53,55,56)
│   └─ slides/outline.md (54)
├─ recordings/                                     ← run dự phòng cho demo (COMMIT)
│   ├─ demo-good-run/ (51)  demo-transcript.txt (51)  dryrun-transcript.txt (57)
│   └─ midscene/ (chỉ khi phải dùng replay, STEP 29/52)
└─ runs/   (git✗)   r-NNNN/{report.md, report.json, sut_identity.json, specs/, results/, t-XXX/…}
```

---

# Phụ lục 2 — Bảng phụ thuộc, đường găng, tải theo người

> Sinh ra bằng script đọc **chính file này** (`Depends on`, `Owner`, `Time` của 57 STEP) — nên khớp với nội dung STEP. Script cũng kiểm: đủ 57 STEP, đủ 6 trường, `Time` ≤ 90', mọi phụ thuộc trỏ về STEP có thật, **không có chu trình**.

## 2.1 Bảng phụ thuộc — cột "Chặn" cho biết STEP này đang giữ ai

| STEP | Owner | Slot | Phút | Depends on | **Chặn** (STEP chờ nó) | Việc |
|---|---|---|---|---|---|---|
| 01 | Đức (B) | 1 | 20 | none | 23 | Kiểm key VLM |
| 02 | Huy (C) | 1 | 20 | none | 35 | Kiểm key model chấm |
| 03 | Nghĩa (A) | 1 | 30 | none | 04, 05, 06 | Repo skeleton |
| 04 | Đức (B) | 1 | 90 | 03 | 08, 09, 10, 23, 35 | Môi trường: bootstrap/doctor/lock |
| 05 | Nghĩa (A) | 1 | 60 | 03 | 07 | Bản nháp contract |
| 06 | Huy (C) | 1 | 60 | 03 | 07, 11, 37 | Stub summarizer + golden (M1) |
| 07 | Nghĩa (A) + Đức, Huy | 1 | 45 | 05, 06 | 08, 09, 10, 12 | 🛑 Họp chốt + ĐÓNG BĂNG contract |
| 08 | Nghĩa (A) | 1 | 20 | 04, 07 | — | Clone sạch (A) |
| 09 | Đức (B) | 1 | 20 | 04, 07 | — | Clone sạch (B) |
| 10 | Huy (C) | 1 | 20 | 04, 07 | 11 | Clone sạch (C) + đối chiếu fingerprint |
| 11 | Huy (C) | 1 | 75 | 10, 06 | 22, 24, 30, 35, 36 | Toy backend + `toyapp.ps1` |
| 12 | Nghĩa (A) | 1 | 60 | 07 | 13, 14, 17, 18, 47 | `core/schema.py` + `evidence.py` |
| 13 | Nghĩa (A) | 2 | 90 | 12 | **15, 16, 25, 27, 28, 31, 36, 37** | `adapters/_base.py` + `oracle/` |
| 14 | Huy (C) | 2 | 60 | 12 | 16 | `core/registry.py` |
| 15 | Đức (B) | 2 | 60 | 13 | 20 | Worker giả `mock` + fixture |
| 16 | Nghĩa (A) | 2 | 90 | 13, 14 | 20, 39 | `core/plan.py` + `runner.py` |
| 17 | Đức (B) | 2 | 45 | 12 | 19, 20 | `core/verdict.py` |
| 18 | Huy (C) | 2 | 45 | 12 | 20 | `core/signature.py` |
| 19 | Đức (B) | 2 | 75 | 17 | 20 | `core/report.py` |
| 20 | Nghĩa (A) | 2 | 60 | 15, 16, 17, 18, 19 | 21, 32, 44 | 🛑 CLI + demo plan (walking skeleton) |
| 21 | Nghĩa (A) | 2 | 30 | 20 | — | `core/README.md` |
| 22 | Đức (B) | 2 | 60 | 11 | 23 | Toy frontend + BUG-2 + telemetry |
| 23 | Đức (B) | 2 | 90 | 22, 01, 04 | 28 | Midscene hello + ma trận exit code |
| 24 | Huy (C) | 2 | 60 | 11 | 25 | Schemathesis hello + hiệu chuẩn BUG-1 |
| 25 | Huy (C) | 2 | 90 | 13, 24 | 26 | Adapter Schemathesis |
| 26 | Nghĩa (A) | 2 | 30 | 25 | 32 | Review adapter Schemathesis |
| 27 | Nghĩa (A) | 3 | 60 | 13 | 28, 31 | `oracle/threshold` + `signals` |
| 28 | Đức (B) | 3 | 90 | 23, 13, 27 | 29 | Adapter Midscene (**khó nhất, làm trước**) |
| 29 | Đức (B) | 3 | 60 | 28 | 33, 42, 52 | Test Midscene độc lập (pass/fail/error) |
| 30 | Huy (C) | 3 | 45 | 11 | 31 | k6 hello + ma trận exit code |
| 31 | Huy (C) | 3 | 75 | 27, 30, 13 | 33, 48 | Adapter k6 |
| 32 | Nghĩa (A) | 3 | 60 | 26, 20 | 34 | `plan.yaml` thật (t-001) |
| 33 | Nghĩa (A) | 3 | 45 | 29, 31 | 34 | Review Midscene rồi k6 |
| 34 | Nghĩa (A) | 3 | 30 | 33, 32 | 39, 40 | Thêm t-002, t-101 vào plan |
| 35 | Huy (C) | 3 | 60 | 02, 04, 11 | 37 | DeepEval hello |
| 36 | Huy (C) | 3 | 45 | 13, 11 | 37 | Collect adapter (`http.collect`) |
| 37 | Huy (C) | 4 | 90 | 35, 36, 06, 13 | 38, 46, 48 | `test_summarize.py` + adapter DeepEval |
| 38 | Nghĩa (A) | 4 | 45 | 37 | 40 | Review DeepEval + collect |
| 39 | Nghĩa (A) | 4 | 75 | 16, 34 | — | Runner song song |
| 40 | Nghĩa (A) | 4 | 45 | 34, 38 | 41, 43, 45, 48, 50 | Plan đầy đủ + tiêu chí #1–#3 |
| 41 | Nghĩa (A) | 4 | 60 | 40 | 49, 51, 53, 56 | 🛑 Tiêu chí #4 (chạy 2 lần, diff) |
| 42 | Đức (B) | 4 | 45 | 29 | 43 | Canary task |
| 43 | Đức (B) | 4 | 45 | 42, 40 | 48 | Kiểm canary trong verdict/report |
| 44 | Đức (B) | 4 | 45 | 20 | — | Chứng minh N5 (`mock2`) |
| 45 | Huy (C) | 5 | 45 | 40 | — | DG-1 plan guard |
| 46 | Huy (C) | 4 | 45 | 37 | — | DG-2/DG-3 protected paths |
| 47 | Đức (B) | 4 | 45 | 12 | — | 16 mutant hợp đồng |
| 48 | Đức (B) | 5 | 90 | 40, 43, 31, 37 | 51 | Bảng mutant lỗi cài sẵn |
| 49 | Nghĩa (A) | 5 | 45 | 41 | — | Selection (cắt trước nhất) |
| 50 | Đức (B) | 5 | 45 | 40 | — | Report golden test |
| 51 | Huy (C) | 5 | 75 | 41, 48 | 57 | Demo script + run ghi sẵn |
| 52 | Đức (B) | 5 | 60 | 29 | 55 | Midscene ổn định ×3 (buffer) |
| 53 | Nghĩa (A) | 5 | 90 | 41 | 54 | Báo cáo mục 0, 3, 4, 9 |
| 54 | Nghĩa (A) | 5 | 60 | 53 | 57 | Slide + "So với Proposal" |
| 55 | Đức (B) | 5 | 60 | 52 | 57 | Báo cáo mục 1, 2, 8 |
| 56 | Huy (C) | 5 | 90 | 41 | 57 | Báo cáo mục 5, 6, 7 |
| 57 | Nghĩa (A) + Đức, Huy | 5 | 60 | 51, 54, 55, 56 | — | 🛑 Dry-run bấm giờ |

## 2.2 Đường găng và các nút thắt

```
ĐƯỜNG GĂNG (chỉ theo phụ thuộc) = 920' ≈ 15.3h  /  ~20h có
03 ─► 05 ─► 07 ─► 10 ─► 11 ─► 22 ─► 23 ─► 28 ─► 29 ─► 33 ─► 34 ─► 40 ─► 43 ─► 48 ─► 51 ─► 57
 A     A    A*     C     C     B     B     B     B     A     A     A     B     B     C    A*
30'   60'   45'   20'   75'   60'   90'   90'   60'   45'   30'   45'   45'   90'   75'   60'
                                └────── nhánh MIDSCENE ──────┘
```

- **Đường găng đi qua Midscene** (22→23→28→29→33): trùng với rủi ro V1 và rủi ro #3 `[R5 P4.1]`. Đó là lý do STEP 23 có *timebox cứng* và có kịch bản mock/replay.
- Con số 15.3h chỉ tính phụ thuộc. **Thực tế dài hơn** vì một người không làm hai việc cùng lúc và mỗi slot thường mất 20–30% cho họp/ốm `[R5 (a)#3]` — cut ladder (Phụ lục 4) tồn tại vì lý do đó.

| Nút thắt | Chặn | Vì sao nguy hiểm | Biện pháp |
|---|---|---|---|
| **STEP 13** (A: `_base` + `oracle/`) | **8 STEP** của cả B lẫn C | Một người giữ khuôn của mọi adapter | Làm **ngay sau STEP 12**, không xen việc khác; B (15, 28) và C (25, 31, 36, 37) chờ nó. Nếu quá 90' ⟹ A phát hành **phiên bản khung** (chữ ký hàm + `main()`) trước, hoàn thiện sau |
| STEP 04 (B: môi trường) | 5 STEP, cả 3 người | Không có môi trường thì ba máy lệch | Timebox 90'; fallback `.venv-eval` |
| STEP 12 (A: schema) | 5 STEP | — | 60'; A không nhận việc khác trước khi xong |
| STEP 11 (C: toy backend) | 5 STEP | SUT của mọi worker | 75'; nếu chậm, B/C dùng phiên bản `app.py` nguyên văn (F-16) — chỉ cần chép |
| STEP 40 (A: plan đầy đủ) | 5 STEP | Cổng vào tiêu chí #1–#4 | Chuẩn bị sẵn khối YAML từ STEP 34 |
| STEP 07 (họp contract) | 4 STEP | Cả ba phải có mặt | Đặt lịch trước; 45' cứng |

**Quy tắc khi bị chặn > 30 phút:** người chờ **không** ngồi chờ — (1) nhận STEP khác không phụ thuộc (bảng "Parallel-safe"), (2) nếu không có thì **ngồi ghép** với người đang giữ nút thắt.

## 2.3 Tải theo slot và theo người (phút, giả định 1 slot ≈ 240')

| Slot | Nghĩa (A) | Đức (B) | Huy (C) | Ghi chú |
|---|---|---|---|---|
| 1 | 215' (3.6h) | 130' (2.2h) | 175' (2.9h) | Phase 0 + STEP 12 |
| 2 | **300' (5.0h) ⚠ 125%** | **330' (5.5h) ⚠ 138%** | 255' (4.2h) ⚠ 106% | **Quá tải có chủ đích**: chỉ STEP 20 là cổng; 23, 25, 26 được trượt sang slot 3 |
| 3 | 195' (3.2h) | 150' (2.5h) | 225' (3.8h) | Slot 3 có chỗ để hấp thụ phần trượt của slot 2 |
| 4 | 225' (3.8h) | 180' (3.0h) | 135' (2.2h) | |
| 5 | 255' (4.2h) ⚠ 106% | 255' (4.2h) ⚠ 106% | 210' (3.5h) | Nửa ngày cuối — cut ladder áp dụng trước tiên ở đây |
| **Tổng** | **19.8h** | **17.4h** | **16.7h** | A cao nhất vì là đường găng **và** người review mọi adapter |

**Ngày 1:** A 8.6h · B 7.7h · C 7.2h ⟹ đầy ~97% — **không có buffer** ở ngày 1. Buffer thực sự của cả sprint nằm ở *slot 3 (dư ~1.5h ở B, ~0.8h ở A)* và ở *cut ladder*, không nằm ở việc "làm nhanh hơn".
*(Mọi số phút là ước lượng, có thể lệch ×2 — `[R3 (a)#8]`.)*

---

# Phụ lục 3 — Biến môi trường / secret

**Quy tắc:** key **chỉ** nằm ở `.env` (đã `.gitignore`), **không** dán vào chat/issue/commit; lỡ lộ ⟹ đổi key ngay. `.env.example` chỉ chứa **tên biến** (giá trị rỗng, trừ hai biến không bí mật). `scripts/env.ps1` nạp `.env` vào tiến trình; dòng bắt đầu `#` và giá trị rỗng bị bỏ qua.

| Biến | Bí mật? | Ai đặt | Đọc bởi | Cần từ STEP | Giá trị / ví dụ |
|---|---|---|---|---|---|
| `APP_BASE_URL` | không | `.env.example` | plan (`${env.APP_BASE_URL}`), `k6.yaml`, `midscene.yaml`, `schemathesis.yaml`, `http-collect.yaml` | 25 | `http://127.0.0.1:8000` |
| `MIDSCENE_MODEL_BASE_URL` | không | B | Midscene CLI, `midscene.yaml` `requires.env` | 01 | URL endpoint model VLM (giả định tương thích OpenAI) |
| `MIDSCENE_MODEL_API_KEY` | **CÓ** | B | Midscene CLI | 01 | key của team |
| `MIDSCENE_MODEL_NAME` | không | B | Midscene CLI | 01 | tên model VLM (D-06: file này không chọn) |
| `MIDSCENE_MODEL_FAMILY` | không | B | Midscene CLI | 23 | ⚠ giá trị hợp lệ do doc Midscene quy định |
| `OPENAI_API_KEY` | cần nếu provider là OpenAI | C | DeepEval (G-Eval) | 02 | key model chấm primary |
| `GEMINI_API_KEY` (`GOOGLE_API_KEY` alias) | cần nếu provider là Gemini | C | DeepEval `GeminiModel` | 02 | key của Gemini; chỉ lưu local trong `.env` |
| `QC_JUDGE_PROVIDER` | không | C | DeepEval G-Eval | 02 | `openai` hoặc `gemini`; provider đã qua smoke check |
| `QC_JUDGE_FALLBACK_PROVIDER` | không | C | DeepEval G-Eval | 02 | mặc định `gemini`; `none` để tắt fallback |
| `QC_JUDGE_MODEL` | không | C | plan `t-003` (`judge_config.model`) | 40 | model của provider primary; **phải ≠** model SUT (`stub-rule-v1`) |
| `QC_JUDGE_FALLBACK_MODEL` | không | C | DeepEval G-Eval | 02 | model của provider fallback |
| `QC_DEEPEVAL_PYTHON` | không | C (tuỳ chọn) | `deepeval_adapter` | 37 | chỉ khi phải dùng `.venv-eval`: `.venv-eval/Scripts/python.exe` |
| `QC_RUNS_DIR` | không | mặc định | `_base`, `cli` | 13 | `runs` |
| `QC_REPLAY_MIDSCENE` | không | B (tuỳ chọn) | `midscene_adapter` | 29 | `recordings/midscene` — bật chế độ *replay* (**phải gắn nhãn**) |
| `QC_BUGS` | không | `toyapp.ps1 -Bugs …` | `toyapp/app.py` | 11 | `1,2,3` (mặc định) · `none` = tắt hết · **không dùng chuỗi rỗng** |
| `QC_LATENCY_MS` | không | `toyapp.ps1 -LatencyMs …` | `toyapp/app.py` | 30 | `0` · mutant M4: `400` |
| `QC_LONG_ID_LEN` | không | mặc định | `toyapp/app.py` | 24 | `64` (hạ xuống nếu Schemathesis không bắt được BUG-1) |
| `QC_EVAL_OUTPUTS`, `QC_EVAL_OUT_DIR` | không | **adapter DeepEval tự đặt** | `tests/eval/test_summarize.py` | 37 | **không** ghi vào `.env` |
| `DEEPEVAL_RESULTS_FOLDER` | không | adapter tự đặt | DeepEval | 37 | thư mục trong `workdir` |
| `PYTHONUTF8`, `PYTHONIOENCODING` | không | `env.ps1` + `_base` + `runner` | mọi tiến trình Python | 04 | `1`, `utf-8` (D-07) |
| `QC_REGEN` | không | người chạy | `tests/test_report_golden.py` | 50 | `1` — **chỉ** khi cố ý tái sinh file kỳ vọng |
| *(tên biến tắt telemetry/đăng nhập của DeepEval)* | không | C | DeepEval | 35 | ⚠ **STEP 35 tìm ra** và ghi vào `.env.example` — RUN 1–5 không có |

---

# Phụ lục 4 — Cut ladder: chậm thì bỏ STEP nào trước

**Cắt từ trên xuống. Mỗi dòng: bỏ cái gì → mất gì → vì sao bỏ được.** Kế thừa `[R5 4.2]`, thêm các STEP của Phase 3.

| Thứ tự | Bỏ | Mất gì | Vì sao bỏ được |
|---|---|---|---|
| **0** | **STEP 49** Selection | Demo chọn-theo-diff | Vẫn nói được bằng sơ đồ [arch §3]; không ảnh hưởng 6 tiêu chí PoC |
| **0b** | **STEP 46** DG-2/DG-3 (giữ `test_core_readonly` nếu rẻ) | Bằng chứng "input xấu bị chặn" cho patch của agent / golden | Luật vẫn nằm trong báo cáo (mục 4, 8); PoC không có healer nên chưa có gì để chặn *thật* |
| **0c** | **STEP 45** DG-1 plan guard (nếu chỉ kịp một luật: "xoá task") | Bằng chứng chống V3 (plan mục / bị thu hẹp) | Như trên |
| **0d** | **STEP 48** thu gọn: chỉ M0, M1, M3, M5 | Bảng bắt/lọt đầy đủ | Bốn mutant vẫn chứng minh: xanh khi sạch · đỏ khi có lỗi · **crash ≠ fail** |
| **1** | `plan-gen` | (đã cắt từ đầu — mục 0.3 #7) | Plan viết tay vẫn chứng minh plan-as-artifact |
| **2** | **STEP 39** chạy song song (chạy tuần tự) | Luận điểm `max(worker)` vs `sum(worker)` | Không ảnh hưởng đúng/sai verdict |
| **3** | **STEP 43** kiểm canary (giữ task canary trong plan) | Lớp phòng thủ #3 hoạt động | Vẫn nói được bằng slide |
| **4** | `promote_candidate` (`promote_hints` ở STEP 28 + dòng promote ở report) | Demo mũi tên *promote* | Vẫn mô tả được trên sơ đồ |
| **5** ⚠ đắt | **STEP 37** DeepEval → thay bằng `mock` có kết quả có sẵn | Worker AI-app **chạy thật** | Nhánh AI là câu trả lời trực tiếp cho đề mentor — chỉ cắt khi bắt buộc; **gắn nhãn mock** |
| **6** ⚠ đắt | **STEP 28–29** Midscene → run ghi sẵn (`QC_REPLAY_MIDSCENE`) | Worker (a) **chạy thật** | Contract **vẫn** được chứng minh với dữ liệu thật đã ghi; **gắn nhãn replay** |
| **KHÔNG BAO GIỜ CẮT** | **① Contract + validate schema** (STEP 05, 07, 12, 13) · **② tách 3 mục theo `verdict_source` trong report** (STEP 19) · **③ tiêu chí tái lập — chạy 2 lần, diff rỗng** (STEP 41) | — | Đây là **toàn bộ phần mới** của bản propose `[arch §1.1]`. Cắt ba cái này thì còn lại là *một script gọi 4 tool* — và mentor sẽ nói đúng như vậy |

**Quy tắc kích hoạt:** (a) cuối **slot 2** chưa có STEP 20 ⟹ A bỏ mọi thứ ngoài skeleton, B/C đỡ `core/` theo README `[R5 P4.1 #2]`; (b) cuối **slot 4** chưa đạt tiêu chí #1–#4 ⟹ sáng ngày 3 **không** thêm tính năng, chỉ sửa; (c) còn ≤ 90' thì chỉ làm STEP 57 và đảm bảo **bản ghi dự phòng chạy được**.
**Minimum shippable** `[R5 4.3]`: *orchestrator + Schemathesis + k6 chạy thật + DeepEval mock + report tách 3 mục + chạy 2 lần cho verdict giống hệt.* **Bốn worker dở dang tệ hơn hai worker chạy thật + hai mock trung thực.**
**Trượt được ở slot 2:** STEP 23, 25, 26 (chỉ STEP 20 là cổng).

---

# Phụ lục 5 — Troubleshooting

Năm lỗi khả năng cao nhất `[RUN 7]` + hai lỗi kiến trúc (#6, #7) mà các STEP trỏ tới.

## #1. LLM trả sai định dạng
**Triệu chứng:** Midscene chạy nhưng `--summary` thiếu/không parse được; flow vòng lặp không dứt; G-Eval trả chuỗi không phải số; `AdapterParseError` trong `adapter_notes`; `status=error`, `rationale` bắt đầu bằng `parse:`.
**Nguyên nhân thường gặp:** VLM không hiểu câu lệnh flow · model chấm trả văn xuôi thay vì điểm · giới hạn token cắt cụt câu trả lời.
**Xử lý (theo thứ tự):**
1. Đọc `runs\r-N\t-XXX\stdout.log` và `adapter_notes` của result — biết nó hỏng ở đâu **trước khi** sửa.
2. Midscene: viết lại câu lệnh flow ngắn/tường minh hơn; giảm `max_steps` xuống 8; thu hẹp `avoid`; thử lại **bằng tay** (`npx @midscene/cli …`) để tách lỗi flow khỏi lỗi adapter.
3. G-Eval: không cần sửa gì để gate đúng — adapter đã ghi `geval.json = {"error": …}` và **bỏ finding `llm_judgment`** (advisory hỏng không được phép ảnh hưởng gate `[STEP 37]`).
4. Vẫn không ổn ⟹ replay/mock có nhãn (Phụ lục 4, bậc 5–6).
**Tuyệt đối không:** nhờ một LLM khác "đọc giúp và đoán ý nghĩa" — đó là tầng 4 bị **loại có chủ đích** `[arch D3]`, chỗ pattern supervisor hỏng trong thực tế. Adapter được **mất** thông tin, không được **bịa**.

## #2. Worker timeout
**Triệu chứng:** `status=error`, `rationale` chứa `timeout: budget.wallclock_s`; task Midscene/k6 chạy mãi; sau đó máy chậm bất thường (tiến trình mồ côi).
**Nguyên nhân:** VLM chậm · số bước UI tăng gấp ba (V5) · `duration` của k6 + khởi động > `budget.wallclock_s` · Schemathesis `max_examples` quá lớn · judge bị 429 rồi treo.
**Xử lý:**
```powershell
# 1) xem task nào tốn bao lâu (số đo thật, không đoán) — trên run gần nhất
$r = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
Get-ChildItem "$r\results\*.json" | ForEach-Object { $j = Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json; "{0} {1} wallclock={2}s" -f $j.task_id, $j.status, $j.cost.wallclock_s }
# 2) dọn tiến trình mồ côi (chỉ node/k6 mới sinh ra, KHÔNG đụng Chrome đang dùng của bạn)
Get-Process node, k6 -ErrorAction SilentlyContinue | Where-Object { $_.StartTime -gt (Get-Date).AddMinutes(-30) } | Stop-Process -Force
```
Nới trần **trong `plan.yaml`** (`budget.wallclock_s`) chứ không sửa adapter — và biết rằng DG-1 sẽ in `NOTICE` (nới budget không bị chặn, chỉ báo). Giảm việc thay vì tăng giờ: `max_steps`, `max_examples`, `duration`. Nếu `_base` chưa `taskkill /T /F` ⟹ sửa theo STEP 13 (Windows chỉ giết tiến trình con trực tiếp).

## #3. Rate limit / hết quota (HTTP 429)
**Triệu chứng:** STEP 01/02 trả 429; Midscene có bước thất bại giữa chừng; G-Eval `{"error": "429"}`; hoá đơn tăng bất thường.
**Xử lý:**
1. **Kiểm key/quota trước ngày sprint** (STEP 01, 02 là "việc số 0" vì lý do này) `[R5 P4.1 #4]`.
2. Đừng bọc vòng lặp retry-mù trong code. Runner chỉ retry `error` **một lần** — với task gọi LLM thì đặt **`retry: {max: 0, on: []}`** trong plan (t-101, canary đã vậy) để không nhân đôi chi phí khi bị 429.
3. Không chạy Midscene và DeepEval cùng lúc trên cùng một key: Midscene là **độc quyền** (STEP 39); G-Eval nằm ở `t-003` — chạy `--only` từng nhóm khi lặp.
4. Giảm quy mô: 5 golden case → giữ 5 (đã nhỏ); Midscene `max_steps` 8.
5. Hết quota giữa demo ⟹ **bản ghi dự phòng** (`recordings/`), nói rõ đó là bản ghi.
`error` do 429 làm gate **đỏ** nếu nó nằm ở task **gate** (đúng thiết kế: thà đỏ trung thực còn hơn xanh gian dối `[R4 S12c]`) — G-Eval là ngoại lệ vì nó advisory.

## #4. Môi trường lệch nhau giữa 3 máy (Windows)
| Triệu chứng | Nguyên nhân | Sửa |
|---|---|---|
| `UnicodeEncodeError: 'charmap' codec can't encode…` | Python dùng cp1252 khi stdout bị redirect (**đã gặp thật khi soạn file này**) | Luôn `. .\scripts\env.ps1` (đặt `PYTHONUTF8=1`); JSON qua pipe dùng `ensure_ascii=True` (D-07) |
| `cannot be loaded because running scripts is disabled` | ExecutionPolicy | Chạy `powershell -ExecutionPolicy Bypass -File .\scripts\<x>.ps1`; hoặc `Set-ExecutionPolicy -Scope Process Bypass` |
| File `.ps1` báo lỗi cú pháp lạ, ký tự `â€œ` | Có ký tự non-ASCII trong `.ps1` không BOM (PowerShell 5.1 đọc theo ANSI, dấu ngoặc kép cong bị coi là dấu nháy) | **Chỉ ASCII** trong `.ps1` |
| `json.decoder.JSONDecodeError: Unexpected UTF-8 BOM` | `Out-File -Encoding utf8` / `Set-Content -Encoding UTF8` của PS 5.1 chèn BOM | Đọc JSON bằng `encoding="utf-8-sig"` (D-07); ghi bằng `[IO.File]::WriteAllText(p, s, (New-Object Text.UTF8Encoding $false))` |
| `test_contract_frozen` đỏ chỉ trên một máy; `files_sha256` khác giữa các máy | CRLF/LF (`core.autocrlf`) | Hash đã chuẩn hoá `\r\n→\n`; kiểm `.gitattributes` có mặt; `git config core.autocrlf` |
| `'k6' is not recognized` ngay sau khi cài | PATH chưa nạp | Mở **terminal mới** |
| `doctor` `FAIL python` dù đã cài | Sai minor (D-03) hoặc `.venv` tạo từ Python khác | Xoá `.venv`, chạy lại `bootstrap.ps1` |
| `pytest` báo `ModuleNotFoundError: core` / `toyapp` | Thiếu `pytest.ini` | Tạo `pytest.ini` (STEP 03) hoặc dùng `python -m pytest` |
| `subprocess`: `FileNotFoundError: npx` | Trên Windows là `npx.cmd` | `shutil.which("npx")` (STEP 28) |
| `-Bugs ""` mà bug vẫn bật | PowerShell coi env rỗng là **xoá** biến | Dùng `-Bugs none` |
| `Address already in use` (8000) | Toy app cũ chưa dừng | `.\scripts\toyapp.ps1 stop`; hoặc `Get-NetTCPConnection -LocalPort 8000` rồi `Stop-Process -Id <pid>` |
| `pip` báo xung đột `deepeval` ↔ `schemathesis` | Ràng buộc phiên bản gói | Timebox 30': `.venv-eval` + `QC_DEEPEVAL_PYTHON` (STEP 04) |
| `ENV-FINGERPRINT` lệch | Ai đó `pip install` thêm gói / khác minor Python | `pip freeze` từng máy rồi `Compare-Object`; xoá `.venv` + bootstrap lại. **Không** sửa `requirements.lock` cho khớp |
| Chrome/Puppeteer không tải được (Midscene) | Mạng công ty chặn | Ghi lỗi nguyên văn vào `docs/decisions.md`; STEP 23 mock; hỏi cả nhóm |
Điểm chung: **chạy `doctor.ps1` trước khi báo "máy tôi lỗi"** — nếu nó xanh mà vẫn lỗi thì mới là lỗi của mã.

## #5. Test flaky
**Triệu chứng:** `diff_runs` báo `DIFFERENT` (tiêu chí #4 đỏ) · `t-002` lúc đỏ lúc xanh (p95 nhiễu) · `t-001` bắt được BUG-1 lần có lần không · `t-101` số finding mỗi lần một khác.
**Nguyên tắc cứng `[R4 S13.3]`:** **chỉ retry `error`, không bao giờ retry `fail`** — *retry một assert fail là cách người ta biến flakiness thành chính sách*. Đừng "cho chạy lại đến khi xanh".
**Chẩn đoán bằng số, không bằng cảm giác** (chạy 5 lần, đếm):
```powershell
1..5 | ForEach-Object {
    python orchestrator.py --plan plan.yaml --only t-001 | Out-Null
    $r = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
    (Get-Content "$r\results\t-001.json" -Raw -Encoding UTF8 | ConvertFrom-Json).status
} | Group-Object | Select-Object Name, Count
```
| Task lật | Nguyên nhân thường gặp | Sửa gốc |
|---|---|---|
| `t-001` (Schemathesis) | Hypothesis không luôn sinh id đủ dài | Hạ `QC_LONG_ID_LEN` mặc định (STEP 24) · ghim seed nếu có cờ · chấp nhận và **nói rõ** đây là hiệu chuẩn lỗi cài sẵn |
| `t-002` (k6) | Máy dev nhiễu, task khác chạy cùng lúc | Đảm bảo `parallel_safe: false` (độc quyền, STEP 39) · **nới ngưỡng** và nói "demo cơ chế, không phải số đo" `[R3 rủi ro #7]` |
| `t-003` (DeepEval) | Phần "tất định" hoá ra không tất định | Kiểm D-01: stub có thật thuần không? `test_summarizer` xanh chưa? |
| `t-101` (Midscene) | Phương sai vốn có của agent | **Đúng thiết kế:** discovery lane không chặn, không yêu cầu tái lập. Đó là lý do nó bị loại khỏi `deterministic_view` |
Nếu #4 vẫn không đạt: **hạ tiêu chí và nói rõ với mentor**, đừng giấu.

## #6. Contract bị ép méo (V1) — rủi ro số 1
**Dấu hiệu sớm:** xuất hiện `if worker == "…"` trong `core/` · có người nói "thêm một trường nhỏ chỉ cho Midscene" · `review_adapter.py` câu 5 `FAIL` · `Select-String -Path core\*.py -Pattern 'schemathesis|k6|deepeval|midscene'` có kết quả.
**Xử lý `[R5 P4.1 #1]`:** dừng 15', **cả 3 người** quyết định: (a) sửa schema cho **cả 4** worker (quay lại STEP 07, freeze lại, tag `qrs-v0.1.1`, sửa **cùng lúc** mọi adapter) **hoặc** (b) để adapter **mất thông tin** (quy tắc vàng). **Cấm** thêm trường riêng. Nếu không thống nhất trong 15' ⟹ A quyết (contract có đúng một chủ).

## #7. Gate xanh vì worker không chạy (V2) — lỗi im lặng nguy hiểm nhất
**Dấu hiệu:** report có banner `## ⚠ SKIPPED / ERROR` **hoặc** verdict `🟡 YELLOW`; hoặc verdict `❌ FAIL` với lý do `không có result gating nào`.
**Nguyên nhân:** thiếu `OPENAI_API_KEY`/binary/`APP_BASE_URL` trên runner ⟹ `registry.probe` đặt worker `skipped`.
**Xử lý:** đọc `probe_reason` ở banner (nó nêu đúng cái thiếu). Đừng đổi `skipped` thành `pass`. Với CI của team: đặt `--yellow-exit 2` nếu muốn vàng **chặn** (D-02 mặc định `0`).

---

# Phụ lục 6 — Mã nguyên văn (đã chạy thử)

Mọi file dưới đây **được sinh tự động từ chính các file đã chạy** — không gõ lại — nên tài liệu và bản đã kiểm trùng nhau từng byte. Tạo file đúng tên/đường dẫn, dán **nguyên văn**. (Trạng thái kiểm ở cột cuối.)

| F | Đường dẫn trong repo | STEP | Trạng thái kiểm khi soạn tài liệu |
|---|---|---|---|
| F-01 | `scripts/env.ps1` | 04 | Cú pháp 0 lỗi, 0 byte ngoài ASCII; nạp `.env` đúng (bỏ `#`, bỏ nháy, bỏ giá trị rỗng) |
| F-02 | `scripts/bootstrap.ps1` | 04 | Cú pháp 0 lỗi, ASCII. **Chưa chạy** (cần `winget`/mạng) |
| F-03 | `scripts/doctor.ps1` | 04 | Chạy thật: `OK` cho python/node/libs/.env/pip-freeze, `FAIL` đúng cho k6/st/deepeval/midscene chưa cài |
| F-04 | `tools/extract_from_arch.py` | 05 | Chạy thật trên `architecture.md` |
| F-05 | `tools/validate.py` | 05 | Chạy thật (4 result + 2 task PASS) |
| F-06 | `schemas/task_spec.json` | 05 | 2 ví dụ PASS, 7 mutant bị chặn |
| F-07 | `examples/task.k6.json` | 05 | PASS |
| F-08 | `examples/task.midscene.json` | 05 | PASS (`evidence_required` đã hạ còn `raw_output`) |
| F-09 | `schemas/capabilities.json` | 05 | JSON hợp lệ |
| F-10 | `core/verdict.py` | 17 | 11 test pass |
| F-11 | `tools/diff_runs.py` | 41 | Chạy thật: IDENTICAL / DIFFERENT / không so được |
| F-12 | `tools/freeze_contract.py` | 05, 07 | Bắt sửa nội dung; bỏ qua CRLF↔LF |
| F-13 | `workers/_template.yaml` | 05 | YAML hợp lệ, đủ khoá |
| F-14 | `toyapp/summarizer.py` | 06 | 3 test pass |
| F-15 | `tests/eval/golden.json` | 06 | Đúng `[T,T,F,T,T]` / toàn T |
| F-16 | `toyapp/app.py` | 11 | 6 test pass + chạy thật bằng uvicorn qua `toyapp.ps1` |
| F-17 | `toyapp/static/index.html` | 22 | JS đã `node --check`; **chưa thử bằng trình duyệt** (STEP 22 thử tay) |
| F-18 | `scripts/toyapp.ps1` | 11 | Chạy thật: start/status/stop, `-Bugs none` tắt bug, `-Bugs 1` bật |
| F-19 | `tests/test_contract_frozen.py` | 05 | pass |
| F-20 | `examples/result.k6_pass.json` | 05 | PASS |
| F-21 | `tests/test_toyapp.py` | 11 | 6 pass |
| F-22 | `core/schema.py` | 12 | 19 test pass |
| F-23 | `tools/review_adapter.py` | 26 | 5 PASS; câu 5 FAIL đúng khi có tên worker trong `core/` |
| F-24 | `tests/test_schema.py` | 12 | 19 pass |
| F-25 | `tests/test_verdict.py` | 17 | 11 pass |
| F-26 | `tests/test_contract_mutants.py` | 47 | 16 pass |
| F-27 | `guards/plan_guard.py` | 45 | Chạy thật trên input xấu: xoá task → exit 1; nới ngưỡng → exit 0 + NOTICE |
| F-28 | `tests/guards/test_plan_guard.py` | 45 | 8 pass |
| F-29 | `guards/protected_paths.py` | 46 | 7 test (repo git tạm) pass |
| F-30 | `tests/guards/test_protected_paths.py` | 46 | 7 pass |
| F-31 | `scripts/repeat.ps1` | 51 | Chạy thật với orchestrator giả: sạch → `ALL 3 RUNS OK`; có `error` → exit 1 |
| F-32 | `scripts/demo.ps1` | 51 | Cú pháp 0 lỗi, ASCII; `GateIds` đã thử. **Chưa chạy end-to-end** (cần orchestrator) |

**Tổng cộng khi ráp các file F-xx vào một repo mô phỏng: `pytest -q` in `71 passed`.**

#### F-01 — `scripts/env.ps1`

```powershell
# scripts/env.ps1 - dot-source it:   . .\scripts\env.ps1
# ASCII ONLY. Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI, so non-ASCII text can break parsing.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)

if (Test-Path .env) {
    foreach ($line in Get-Content .env -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$' -and $line.TrimStart()[0] -ne '#') {
            $val = $Matches[2].Trim().Trim('"')
            if ($val -ne "") { Set-Item -Path ("Env:" + $Matches[1]) -Value $val }
        }
    }
}

$venvScripts = Join-Path (Get-Location) ".venv\Scripts"
if ((Test-Path $venvScripts) -and ($env:PATH -notlike "*$venvScripts*")) {
    $env:PATH = "$venvScripts;$env:PATH"
}
```

#### F-02 — `scripts/bootstrap.ps1`

```powershell
# scripts/bootstrap.ps1 - ONE command to build the environment. ASCII ONLY (see env.ps1).
#   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
# (-ExecutionPolicy Bypass avoids "running scripts is disabled on this system" on a fresh Windows.)
$ErrorActionPreference = "Stop"
$want = (Get-Content .python-version).Trim()

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "== creating .venv with Python $want"
    & py "-$want" -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "py -$want failed. Install Python $want or change .python-version (decision D-03)." }
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip

$req = "requirements.txt"
if (Test-Path requirements.lock) { $req = "requirements.lock" }
Write-Host "== pip install -r $req"
& $py -m pip install -r $req
if ($LASTEXITCODE -ne 0) { throw "pip install failed - read the resolver message above (see Troubleshooting #4)" }

if (Test-Path package-lock.json) {
    Write-Host "== npm ci"
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
}

if (-not (Get-Command k6 -ErrorAction SilentlyContinue)) {
    Write-Host "== installing k6 with winget (afterwards open a NEW terminal so PATH refreshes)"
    winget install k6 --source winget --accept-package-agreements --accept-source-agreements
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "== created .env from .env.example - fill in the keys, then rerun doctor"
}

Write-Host "== doctor"
& powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
exit $LASTEXITCODE
```

#### F-03 — `scripts/doctor.ps1`

```powershell
# scripts/doctor.ps1 - environment check. Exit 0 = all OK. ASCII ONLY (see env.ps1).
# Run from repo root:  powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
$script:bad = 0

function Run-Native([string]$exe, [string[]]$argv) {
    $out = & $exe @argv 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$exe $($argv -join ' ') -> exit $LASTEXITCODE" }
    return $out.Trim()
}
function Check([string]$name, [scriptblock]$test) {
    try   { $r = & $test; Write-Host ("OK   {0,-11} {1}" -f $name, $r) }
    catch { Write-Host ("FAIL {0,-11} {1}" -f $name, $_.Exception.Message); $script:bad++ }
}

$py = ".\.venv\Scripts\python.exe"
$script:fp = ""

Check "python" {
    $want = (Get-Content .python-version).Trim()
    $v = Run-Native $py @("--version")
    if ($v -notmatch [regex]::Escape("Python $want")) { throw "need Python $want, got $v" }
    $script:fp += $v
    $v
}
Check "node" {
    $v = (Run-Native "node" @("--version")).TrimStart("v")
    $major = [int](Get-Content .node-version).Trim()
    if ([version]$v -lt [version]"22.12" -or [int]$v.Split(".")[0] -ne $major) { throw "need major $major and >= 22.12, got $v" }
    $script:fp += "node$major"
    "v$v"
}
Check "k6" {
    $want = (Get-Content .k6-version).Trim()
    $v = Run-Native "k6" @("version")
    if ($v -notmatch [regex]::Escape($want)) { throw "need k6 $want, got: $v" }
    $script:fp += "k6$want"
    $want
}
Check "st"         { Run-Native ".\.venv\Scripts\st.exe" @("--version") }
Check "deepeval"   { Run-Native $py @("-c", "import deepeval; print('import ok')") }
Check "libs"       { Run-Native $py @("-c", "import jsonschema, yaml, fastapi, uvicorn, httpx, pytest; print('import ok')") }
Check "midscene"   { if (-not (Test-Path "node_modules\@midscene\cli")) { throw "node_modules\@midscene\cli missing (run npm ci)" }; "installed" }
Check ".env"       { if (-not (Test-Path .env)) { throw ".env missing (copy .env.example)" }; "present" }
Check "pip-freeze" {
    $freeze = (Run-Native $py @("-m", "pip", "freeze")) -replace "`r", ""
    $lines = ($freeze -split "`n" | Sort-Object) -join "`n"
    $script:fp += $lines
    "{0} packages" -f ($freeze -split "`n").Count
}

if ($script:bad -eq 0) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hex = ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:fp)) | ForEach-Object { $_.ToString("x2") }) -join ""
    Write-Host ("ENV-FINGERPRINT {0}   (3 machines must print the same value)" -f $hex.Substring(0, 12))
    exit 0
}
Write-Host ("{0} check(s) FAILED" -f $script:bad)
exit 1
```

#### F-04 — `tools/extract_from_arch.py`  *(rào 4 backtick vì file có chứa ba backtick)*

````python
"""Trích schemas/result.json + examples/*.json từ docs/architecture.md (nguồn duy nhất).
Chạy:  python tools/extract_from_arch.py [--arch docs/architecture.md]
"""
import argparse, json, pathlib, re, sys

def strip_jsonc(text: str) -> str:
    out = []
    for line in text.splitlines():
        # '//' chỉ là comment khi đứng đầu dòng hoặc đứng sau khoảng trắng (URL có '://' nên không dính)
        line = re.sub(r'(^|\s)//.*$', '', line)
        out.append(line)
    return "\n".join(out)

def fence_after(md: str, heading_regex: str, lang: str, nth: int = 0) -> list[str]:
    m = re.search(heading_regex, md, re.M)
    if not m:
        sys.exit(f"KHÔNG THẤY heading: {heading_regex}")
    tail = md[m.end():]
    nxt = re.search(r'^#{1,2} ', tail, re.M)          # dừng ở heading cấp 1-2 kế tiếp
    tail = tail[: nxt.start()] if nxt else tail
    return re.findall(rf'^```{lang}\n(.*?)^```', tail, re.M | re.S)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="docs/architecture.md")
    a = ap.parse_args()
    md = pathlib.Path(a.arch).read_text(encoding="utf-8")

    blocks = fence_after(md, r'^## 4\.1 ', 'jsonc')
    if len(blocks) != 1:
        sys.exit(f"Mục 4.1 phải có đúng 1 khối jsonc, thấy {len(blocks)}")
    schema = json.loads(strip_jsonc(blocks[0]))
    pathlib.Path("schemas").mkdir(exist_ok=True)
    pathlib.Path("schemas/result.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ex = fence_after(md, r'^## 4\.2 ', 'json')
    if len(ex) != 3:
        sys.exit(f"Mục 4.2 phải có đúng 3 ví dụ json, thấy {len(ex)}")
    names = ["result.e2e_pass.json", "result.e2e_fail.json", "result.ai_eval.json"]
    pathlib.Path("examples").mkdir(exist_ok=True)
    for n, b in zip(names, ex):
        obj = json.loads(b)
        pathlib.Path("examples", n).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("OK: schemas/result.json + 3 examples")

if __name__ == "__main__":
    main()
````

#### F-05 — `tools/validate.py`

```python
"""Validate file JSON theo contract.
    python tools/validate.py result examples/result.*.json
    python tools/validate.py task   examples/task.*.json
Exit 0 = tất cả PASS · 1 = có file FAIL.
"""
import json, pathlib, sys

from jsonschema import Draft202012Validator

SCHEMAS = {"result": "schemas/result.json", "task": "schemas/task_spec.json"}


def main(kind, paths):
    schema = json.loads(pathlib.Path(SCHEMAS[kind]).read_text(encoding="utf-8-sig"))
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    bad = 0
    for p in paths:
        obj = json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))
        errs = sorted(v.iter_errors(obj), key=lambda e: list(map(str, e.path)))
        if errs:
            bad += 1
            print(f"FAIL {p}")
            for e in errs:
                print("   -", "/".join(map(str, e.path)) or "<root>", ":", e.message[:160])
        else:
            print(f"PASS {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in SCHEMAS:
        sys.exit("dùng: python tools/validate.py <result|task> file...")
    sys.exit(main(sys.argv[1], sys.argv[2:]))
```

#### F-06 — `schemas/task_spec.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://qc-agent.local/schemas/task_spec.json",
  "title": "Task Spec v1.0.0",
  "type": "object",
  "additionalProperties": false,
  "required": ["task_id","plan_id","run_id","capability","lane","intent","target","inputs",
               "oracle","expected_result_kind","budget","determinism","sut_identity_ref",
               "evidence_required","retry"],
  "properties": {
    "task_id": {"type": "string"},
    "plan_id": {"type": "string"},
    "run_id":  {"type": "string"},
    "capability": {"type": "string", "pattern": "^[a-z0-9_]+([.][a-z0-9_]+)+$"},
    "lane": {"enum": ["gate", "discovery"]},
    "intent": {"type": "string"},
    "target": {
      "type": "object",
      "required": ["kind", "base_url"],
      "properties": {
        "kind": {"type": "string"},
        "base_url": {"type": "string"},
        "spec_ref": {"type": "string"},
        "entry_path": {"type": "string"}
      }
    },
    "inputs": {"type": "object"},
    "oracle": {
      "type": "object",
      "required": ["kind"],
      "properties": {"kind": {"type": "string"}}
    },
    "expected_result_kind": {"enum": ["verdict", "candidate_finding"]},
    "budget": {
      "type": "object",
      "additionalProperties": false,
      "required": ["wallclock_s", "tokens", "usd"],
      "properties": {
        "wallclock_s": {"type": "number", "exclusiveMinimum": 0},
        "tokens": {"type": "integer", "minimum": 0},
        "usd": {"type": "number", "minimum": 0}
      }
    },
    "determinism": {
      "type": "object",
      "required": ["seed", "replayable"],
      "properties": {
        "seed": {"type": ["integer", "null"]},
        "replayable": {"type": "boolean"}
      }
    },
    "sut_identity_ref": {"type": "string"},
    "evidence_required": {
      "type": "array",
      "items": {"enum": ["raw_output", "stdout", "metrics", "screenshot", "trace"]}
    },
    "retry": {
      "type": "object",
      "additionalProperties": false,
      "required": ["max", "on"],
      "properties": {
        "max": {"type": "integer", "minimum": 0, "maximum": 1},
        "on": {"type": "array", "items": {"const": "error"}}
      }
    },
    "risk_tags": {"type": "array", "items": {"type": "string"}}
  },
  "allOf": [
    {"if":   {"properties": {"lane": {"const": "discovery"}}},
     "then": {"properties": {"expected_result_kind": {"const": "candidate_finding"}}}},
    {"if":   {"properties": {"lane": {"const": "gate"}}},
     "then": {"properties": {"expected_result_kind": {"const": "verdict"}}}}
  ]
}
```

#### F-07 — `examples/task.k6.json`

```json
{
  "task_id": "t-002", "plan_id": "plan-poc-001", "run_id": "r-0001",
  "capability": "http.load", "lane": "gate",
  "intent": "GET /notes chịu 10 VU trong 30s với p95 < 300ms",
  "target": {"kind": "http_service", "base_url": "http://127.0.0.1:8000"},
  "inputs": {"script": "tests/perf/notes_list.js", "vus": 10, "duration": "30s"},
  "oracle": {"kind": "threshold", "assertions": [
    {"metric": "http_req_duration.p95", "op": "<", "value": 300, "unit": "ms"},
    {"metric": "http_req_failed.rate", "op": "<", "value": 0.01}]},
  "expected_result_kind": "verdict",
  "budget": {"wallclock_s": 90, "tokens": 0, "usd": 0},
  "determinism": {"seed": null, "replayable": true},
  "sut_identity_ref": "sut-poc",
  "evidence_required": ["raw_output", "stdout"],
  "retry": {"max": 1, "on": ["error"]}
}
```

#### F-08 — `examples/task.midscene.json`

```json
{
  "task_id": "t-101", "plan_id": "plan-poc-001", "run_id": "r-0001",
  "capability": "ui.explore", "lane": "discovery",
  "intent": "Khám phá trang /: thêm note, xoá note; tìm trạng thái kẹt hoặc lỗi runtime",
  "target": {"kind": "web_app", "base_url": "http://127.0.0.1:8000", "entry_path": "/"},
  "inputs": {"flow": "midscene/explore_notes.yaml", "max_steps": 15},
  "oracle": {"kind": "implicit_signals",
             "signals": ["console_error", "http_5xx", "dom_unchanged", "element_not_found"],
             "llm_observations_allowed": true},
  "expected_result_kind": "candidate_finding",
  "budget": {"wallclock_s": 600, "tokens": 150000, "usd": 1.0},
  "determinism": {"seed": null, "replayable": false},
  "sut_identity_ref": "sut-poc",
  "evidence_required": ["raw_output"],
  "retry": {"max": 0, "on": []}
}
```

#### F-09 — `schemas/capabilities.json`

```json
{
  "_note": "Từ vựng capability (file DỮ LIỆU, không thuộc contract đóng băng). Thêm capability mới = thêm một dòng ở đây.",
  "capabilities": {
    "api.property":  "API property-based / contract  (oracle: checks)",
    "http.load":     "Load test, ngưỡng do plan khai  (oracle: threshold)",
    "http.collect":  "Thu output của app cho task khác dùng  (oracle: checks)",
    "llmapp.eval":   "Eval AI app: metric tất định + advisory judge  (oracle: checks)",
    "ui.explore":    "Khám phá UI, discovery lane  (oracle: implicit_signals)",
    "demo.echo":     "Worker giả để dựng skeleton  (oracle: trivial | checks)",
    "demo.echo2":    "Worker giả thứ hai — chứng minh N5 (STEP 44)",
    "security.stub": "Task security giả cho luật always_on (STEP 49)  (oracle: trivial)"
  },
  "oracle_kinds": ["trivial", "checks", "threshold", "implicit_signals"]
}
```

#### F-10 — `core/verdict.py`

```python
"""Gộp verdict. Hàm THUẦN: không I/O, không LLM. Luật: architecture.md §6.3.
FAIL > YELLOW > PASS.  exit_code: FAIL=1, YELLOW=0 (D-02), PASS=0.
"""
from dataclasses import dataclass, field

PASS, YELLOW, FAIL = "PASS", "YELLOW", "FAIL"

@dataclass
class GateVerdict:
    value: str
    exit_code: int
    reasons: list = field(default_factory=list)      # mỗi phần tử: (task_id, lý do)
    banner: list = field(default_factory=list)       # in Ở ĐẦU report: skipped / error

def gate_verdict(results: dict, specs: dict) -> GateVerdict:
    """results: {task_id: result_dict}; specs: {task_id: spec_dict} — chỉ gồm task ĐƯỢC CHỌN."""
    reasons, banner = [], []
    fail = yellow = False
    gating_seen = 0
    for tid, spec in specs.items():
        r = results.get(tid)
        lane = spec["lane"]
        if r is None:                                            # task được chọn mà không có result
            (reasons if lane == "gate" else banner).append((tid, "không có result"))
            fail = fail or lane == "gate"
            continue
        st = r["status"]
        if st == "error":
            banner.append((tid, "error: " + str((r["verdict"].get("rationale") or ""))[:120]))
            if lane == "gate":
                fail = True; reasons.append((tid, "error (hạ tầng)"))
        elif st == "skipped":
            banner.append((tid, "skipped: " + str((r["verdict"].get("rationale") or ""))[:120]))
            if lane == "gate":
                yellow = True
        elif r["verdict"]["gating"]:
            gating_seen += 1
            if r["verdict"]["value"] != "pass":
                fail = True; reasons.append((tid, "assert tất định fail"))
    if lane_has_gate(specs) and gating_seen == 0 and not fail:
        fail = True; reasons.append(("*", "không có result gating nào — không có gate"))   # chặn AND-rỗng
    if fail:
        return GateVerdict(FAIL, 1, reasons, banner)
    if yellow:
        return GateVerdict(YELLOW, 0, reasons, banner)
    return GateVerdict(PASS, 0, reasons, banner)

def lane_has_gate(specs: dict) -> bool:
    return any(s["lane"] == "gate" for s in specs.values())
```

#### F-11 — `tools/diff_runs.py`

```python
"""Tiêu chí PoC #4. So HAI run theo phần TẤT ĐỊNH. Dùng:  python tools/diff_runs.py runs/r-0001 runs/r-0002
Exit 0 = giống hệt · 1 = KHÁC · 2 = không so được (run_signature khác nhau).
"""
import json, pathlib, sys

KEYS = ("run_signature", "gate_verdict", "exit_code", "deterministic_view")

def load(run_dir):
    return json.loads(pathlib.Path(run_dir, "report.json").read_text(encoding="utf-8-sig"))

def main(a, b):
    ra, rb = load(a), load(b)
    if ra["run_signature"] != rb["run_signature"]:
        print("KHÔNG SO ĐƯỢC: run_signature khác nhau (plan/SUT/worker/adapter/seed đổi).")
        print(" ", ra["run_signature"], "\n ", rb["run_signature"])
        return 2
    va = {k: ra[k] for k in KEYS}
    vb = {k: rb[k] for k in KEYS}
    if va == vb:
        print(f"IDENTICAL — {len(va['deterministic_view'])} kết quả gating, gate_verdict={va['gate_verdict']}")
        return 0
    print("DIFFERENT — đây là BUG CỦA HỆ THỐNG (cùng signature, khác verdict tất định):")
    for ta, tb in zip(va["deterministic_view"], vb["deterministic_view"]):
        if ta != tb:
            print("  -", ta, "\n  +", tb)
    if va["gate_verdict"] != vb["gate_verdict"]:
        print("  gate_verdict:", va["gate_verdict"], "->", vb["gate_verdict"])
    return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

#### F-12 — `tools/freeze_contract.py`

```python
"""Đóng băng contract (RUN 5 2.4).  Hash được tính sau khi chuẩn hoá CRLF -> LF (Windows autocrlf).
    python tools/freeze_contract.py --write   # CHỈ chạy khi cả 3 người đồng ý đổi contract
    python tools/freeze_contract.py --check   # exit 1 nếu file contract lệch lock
"""
import hashlib, pathlib, sys

FILES = ["schemas/task_spec.json", "schemas/result.json", "workers/_template.yaml"]
LOCK = pathlib.Path("schemas/CONTRACT.sha256")


def digest(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main(mode):
    now = {f: digest(f) for f in FILES}
    if mode == "--write":
        LOCK.write_text("".join(f"{h}  {f}\n" for f, h in now.items()), encoding="utf-8", newline="\n")
        print("ĐÃ GHI", LOCK)
        return 0
    locked = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        h, f = line.split("  ", 1)
        locked[f] = h
    bad = [f for f in FILES if locked.get(f) != now[f]]
    for f in bad:
        print("CONTRACT ĐÃ BỊ SỬA:", f)
    if not bad:
        print("CONTRACT NGUYÊN VẸN:", len(FILES), "file")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "--check"))
```

#### F-13 — `workers/_template.yaml`

```yaml
# workers/_template.yaml — bản mẫu manifest. Copy thành workers/<name>.yaml.  FILE NÀY ĐÓNG BĂNG (STEP 07).
# Registry bỏ qua mọi file bắt đầu bằng "_".
name: CHANGEME                            # == result.worker.name
version_probe: "CHANGEME --version"       # lệnh in phiên bản; exit 0 = worker có mặt. Chạy ở preflight (timeout 20s)
adapter: "adapters/CHANGEME_adapter.py"   # chạy bằng: python -m adapters.CHANGEME_adapter
lanes: [gate]                             # gate | discovery | cả hai. Worker KHÔNG được nhận task ngoài lane đã khai
capabilities:
  - id: CHANGEME.capability               # phải có trong schemas/capabilities.json
    inputs_schema: schemas/CHANGEME.inputs.json   # TUỲ CHỌN trong PoC (quyết định D-08)
    oracle_kinds: [threshold]             # oracle.kind worker này chấp nhận (xem oracle/)
    verdict_sources: [deterministic_assert]   # TẬP GIÁ TRỊ ĐƯỢC PHÉP PHÁT RA — không phải nhãn của một kết quả
    parallel_safe: true                   # false = ĐỘC QUYỀN: không task nào khác chạy cùng lúc (k6, Midscene)
requires:
  env: []                                 # biến môi trường bắt buộc (không rỗng)
  binaries: []                            # lệnh phải có trong PATH
data_egress: []                           # khai TRƯỚC cái gì rời máy: app_input | app_output | screenshot | dom | source_code | spec
cost_profile: { tokens_per_run: 0, typical_wallclock_s: 60 }
```

#### F-14 — `toyapp/summarizer.py`

```python
"""Stub summarizer TẤT ĐỊNH — SUT GIẢ LẬP của AI feature (quyết định D-01). KHÔNG gọi model.
BUG-3 (cài sẵn): body chứa marker [[long]] thì summary DÀI HƠN body.
"""
import hashlib

MODEL = "stub-rule-v1"
PROMPT = "summarize:first-sentence-v1"
PROMPT_HASH = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]


def summarize(body: str, bug3: bool = True) -> str:
    body = body.strip()
    if bug3 and "[[long]]" in body:
        return (body + " ") * 2 + "(tóm tắt mở rộng)"
    first = body.split(".")[0].strip() or body
    return first[: max(1, len(body) - 1)]
```

#### F-15 — `tests/eval/golden.json`

```json
[
  {"id": "g1", "title": "Họp sprint",    "body": "Họp sprint lúc 9h. Chốt scope PoC. Mang laptop."},
  {"id": "g2", "title": "Deadline",      "body": "Deadline nộp báo cáo là thứ Sáu. Cần slide và demo."},
  {"id": "g3", "title": "Đi chợ",        "body": "Mua sữa, trứng, bánh mì. Nhớ mang túi. [[long]]"},
  {"id": "g4", "title": "Lỗi đăng nhập", "body": "Khách hàng phản hồi lỗi đăng nhập trên Android. Đã tái hiện."},
  {"id": "g5", "title": "Ghi chú",       "body": "Ghi chú ngắn nhưng đủ ý. Không có gì thêm."}
]
```

#### F-16 — `toyapp/app.py`

```python
"""noteboard — toy app cho QC Agent PoC.  Chạy từ repo root:
    python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000
Lỗi CÀI SẴN (bật/tắt bằng env QC_BUGS, mặc định "1,2,3"; "none" = tắt hết — KHÔNG dùng chuỗi rỗng, PowerShell coi env rỗng là xoá):
  BUG-1  GET /notes/{id} với id dài hơn LONG_ID_LEN -> 500 (đáng lẽ 4xx)        -> Schemathesis bắt
  BUG-2  frontend: bấm Xoá xong không vẽ lại danh sách (xem static/index.html)   -> Midscene + telemetry
  BUG-3  summarize: body chứa [[long]] -> summary dài hơn body (summarizer.py)   -> DeepEval bắt
Mutant harness (KHÔNG phải bug thứ 4): QC_LATENCY_MS=400 làm GET /notes chậm -> k6 threshold fail.
"""
import asyncio, json, os, pathlib, sqlite3, threading, time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from toyapp import summarizer

BUGS = {b.strip() for b in os.environ.get("QC_BUGS", "1,2,3").split(",") if b.strip()}
LATENCY_MS = int(os.environ.get("QC_LATENCY_MS", "0"))
LONG_ID_LEN = int(os.environ.get("QC_LONG_ID_LEN", "64"))
INDEX = pathlib.Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="noteboard", version="0.1.0")
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL)")
_lock = threading.Lock()
EVENTS: list = []                      # telemetry cho implicit signals (KHÔNG nằm trong OpenAPI)


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    id: int
    title: str
    body: str


class Summary(BaseModel):
    summary: str
    model: str
    prompt_hash: str


def _row(r):
    return {"id": r[0], "title": r[1], "body": r[2]}


def _get(note_id: str):
    if "1" in BUGS and len(note_id) > LONG_ID_LEN:
        raise RuntimeError("BUG-1: id quá dài")
    if not note_id.isdigit():
        raise HTTPException(404, "not found")
    with _lock:
        r = _db.execute("SELECT id,title,body FROM notes WHERE id=?", (int(note_id),)).fetchone()
    if not r:
        raise HTTPException(404, "not found")
    return _row(r)


@app.middleware("http")
async def track(request: Request, call_next):
    if LATENCY_MS and request.method == "GET" and request.url.path == "/notes":
        await asyncio.sleep(LATENCY_MS / 1000)
    try:
        resp = await call_next(request)
    except Exception:
        resp = JSONResponse({"detail": "internal error"}, status_code=500)
    if resp.status_code >= 500 and not request.url.path.startswith("/__qc"):
        EVENTS.append({"type": "server_5xx", "path": request.url.path, "status": resp.status_code, "t": time.time()})
    return resp


@app.post("/notes", status_code=201, response_model=NoteOut)
def create_note(n: NoteIn):
    with _lock:
        cur = _db.execute("INSERT INTO notes(title,body) VALUES (?,?)", (n.title, n.body))
        _db.commit()
        return {"id": cur.lastrowid, "title": n.title, "body": n.body}


@app.get("/notes", response_model=list[NoteOut])
def list_notes():
    with _lock:
        return [_row(r) for r in _db.execute("SELECT id,title,body FROM notes ORDER BY id")]


@app.get("/notes/{note_id}", response_model=NoteOut, responses={404: {"description": "not found"}})
def get_note(note_id: str):
    return _get(note_id)


@app.delete("/notes/{note_id}", status_code=204, responses={404: {"description": "not found"}})
def delete_note(note_id: str):
    _get(note_id)
    with _lock:
        _db.execute("DELETE FROM notes WHERE id=?", (int(note_id),))
        _db.commit()
    return Response(status_code=204)


@app.post("/notes/{note_id}/summarize", response_model=Summary, responses={404: {"description": "not found"}})
def summarize(note_id: str):
    n = _get(note_id)
    return {"summary": summarizer.summarize(n["body"], bug3="3" in BUGS),
            "model": summarizer.MODEL, "prompt_hash": summarizer.PROMPT_HASH}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return INDEX.read_text(encoding="utf-8").replace("__QC_BUGS__", json.dumps(sorted(BUGS)))


@app.post("/__qc/events", include_in_schema=False)
async def add_event(request: Request):
    EVENTS.append({**(await request.json()), "t": time.time()})
    return {"ok": True}


@app.get("/__qc/events", include_in_schema=False)
def get_events():
    return EVENTS


@app.get("/__qc/config", include_in_schema=False)
def get_config():
    """Cấu hình runtime của SUT — orchestrator đưa vào SUT identity (plan.sut.probe_url)."""
    return {"bugs": sorted(BUGS), "latency_ms": LATENCY_MS, "long_id_len": LONG_ID_LEN}


@app.delete("/__qc/events", include_in_schema=False)
def reset_events():
    EVENTS.clear()
    return {"ok": True}
```

#### F-17 — `toyapp/static/index.html`

```html
<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>noteboard</title></head>
<body>
<h1>noteboard</h1>
<form id="f">
  <input id="title" placeholder="Tiêu đề" required>
  <input id="body" placeholder="Nội dung" required>
  <button type="submit">Thêm</button>
</form>
<ul id="notes"></ul>
<script>
window.QC_BUGS = __QC_BUGS__;
const ev = (type, extra) => fetch('/__qc/events', {method: 'POST', headers: {'content-type': 'application/json'},
                                                  body: JSON.stringify({type, ...extra})}).catch(() => {});
window.onerror = (m) => { ev('console_error', {message: String(m)}); };
const _fetch = window.fetch.bind(window);          // http_5xx do TRANG thấy (không lẫn request của Schemathesis/k6)
window.fetch = async (...a) => {
  const r = await _fetch(...a);
  if (r.status >= 500 && !String(a[0]).startsWith('/__qc')) ev('http_5xx', {url: String(a[0]), status: r.status});
  return r;
};
async function render() {
  const notes = await (await fetch('/notes')).json();
  const ul = document.getElementById('notes'); ul.innerHTML = '';
  for (const n of notes) {
    const li = document.createElement('li'); li.textContent = n.title + ' — ' + n.body + ' ';
    const b = document.createElement('button'); b.textContent = 'Xoá';
    b.onclick = () => del(n.id); li.appendChild(b); ul.appendChild(li);
  }
  ev('render_done', {count: notes.length});
}
async function del(id) {
  ev('delete_clicked', {id});
  await fetch('/notes/' + id, {method: 'DELETE'});
  if (!window.QC_BUGS.includes('2')) await render();      // BUG-2: bỏ dòng này → danh sách không cập nhật
}
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  await fetch('/notes', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({title: title.value, body: body.value})});
  title.value = ''; body.value = ''; await render();
};
render();
</script>
</body></html>
```

#### F-18 — `scripts/toyapp.ps1`

```powershell
# scripts/toyapp.ps1 - start | stop | status of the noteboard toy app. ASCII ONLY.
#   .\scripts\toyapp.ps1 start [-Bugs "1,2,3"] [-LatencyMs 0] [-Port 8000]
#   .\scripts\toyapp.ps1 stop
#   .\scripts\toyapp.ps1 status        (exit 1 if not running)
param(
    [Parameter(Mandatory = $true)][ValidateSet("start", "stop", "status")][string]$Action,
    [string]$Bugs = "1,2,3",
    [int]$LatencyMs = 0,
    [int]$Port = 8000
)
$pidFile = ".toyapp.pid"

function Get-Live {
    if (Test-Path $pidFile) {
        $p = Get-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
        if ($p) { return $p }
    }
    return $null
}

switch ($Action) {
    "stop" {
        $p = Get-Live
        if ($p) { Stop-Process -Id $p.Id -Force }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
        "stopped"
    }
    "status" {
        $p = Get-Live
        if ($p) { "running pid=$($p.Id)" } else { "not running"; exit 1 }
    }
    "start" {
        if (Get-Live) { & $PSCommandPath -Action stop | Out-Null }
        New-Item -ItemType Directory -Force runs | Out-Null
        $env:QC_BUGS = $Bugs
        $env:QC_LATENCY_MS = "$LatencyMs"
        $py = (Resolve-Path ".venv\Scripts\python.exe").Path
        $p = Start-Process -FilePath $py -PassThru -WindowStyle Hidden `
            -ArgumentList "-m", "uvicorn", "toyapp.app:app", "--host", "127.0.0.1", "--port", "$Port" `
            -RedirectStandardOutput "runs\toyapp.out.log" -RedirectStandardError "runs\toyapp.err.log"
        $p.Id | Out-File -Encoding ascii $pidFile
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/openapi.json"; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch { }
        }
        if (-not $ok) { Write-Host "toyapp did not come up in 15s - see runs\toyapp.err.log"; exit 1 }
        "started pid=$($p.Id) port=$Port bugs=$Bugs latency_ms=$LatencyMs"
    }
}
```

#### F-19 — `tests/test_contract_frozen.py`

```python
import subprocess
import sys


def test_contract_files_match_lock():
    """Ai sửa schemas/task_spec.json, schemas/result.json hoặc workers/_template.yaml sau STEP 07 mà không
    chạy `freeze_contract.py --write` (tức là không có sự đồng ý của cả ba) sẽ làm test này đỏ."""
    r = subprocess.run([sys.executable, "tools/freeze_contract.py", "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout
```

#### F-20 — `examples/result.k6_pass.json`

```json
{
  "task_id": "t-002",
  "run_id": "r-0001",
  "worker": {
    "name": "k6",
    "version": null,
    "adapter_version": "0.1.0"
  },
  "status": "pass",
  "verdict": {
    "value": "pass",
    "verdict_source": "deterministic_assert",
    "gating": true,
    "confidence": null,
    "rationale": null
  },
  "findings": [],
  "metrics": {
    "http_req_duration.p95": 214.7,
    "http_req_failed.rate": 0.002
  },
  "evidence": [
    {
      "kind": "raw_output",
      "uri": "runs/r-0001/t-002/k6-summary.json",
      "sha256": "6b59cdf8c95d86bc32706f762862e08ccb7e9b83d60359c9f913f218c0f6b7a1"
    },
    {
      "kind": "stdout",
      "uri": "runs/r-0001/t-002/stdout.log",
      "sha256": "275f8eb0b3670719242ead5792bf63d698bda7b3a0506a3a7d2880a8b44fecb9"
    }
  ],
  "cost": {
    "wallclock_s": 31.4,
    "tokens": 0,
    "usd": 0.0
  },
  "sut_identity_ref": "sut-poc",
  "determinism": {
    "seed": null,
    "replay_cmd": "k6 run --summary-export=k6-summary.json tests/perf/notes_list.js"
  },
  "adapter_notes": [
    "k6 exit_code=0"
  ]
}
```

#### F-21 — `tests/test_toyapp.py`

```python
import importlib, sys

import pytest
from fastapi.testclient import TestClient

BODY = "Mua sữa. Nhớ túi. [[long]]"


@pytest.fixture
def fresh(monkeypatch):
    """fresh(bugs) -> TestClient với DB rỗng và bộ bug chỉ định ("none" = tắt hết)."""
    def make(bugs):
        monkeypatch.setenv("QC_BUGS", bugs)
        for name in [m for m in sys.modules if m.startswith("toyapp")]:
            del sys.modules[name]
        app = importlib.import_module("toyapp.app").app
        return TestClient(app, raise_server_exceptions=False)
    return make


def test_crud_roundtrip(fresh):
    c = fresh("none")
    r = c.post("/notes", json={"title": "a", "body": "b"})
    assert r.status_code == 201 and r.json()["id"] == 1
    assert c.get("/notes").json() == [{"id": 1, "title": "a", "body": "b"}]
    assert c.get("/notes/1").status_code == 200
    assert c.delete("/notes/1").status_code == 204
    assert c.get("/notes/1").status_code == 404


def test_unknown_id_404(fresh):
    assert fresh("none").get("/notes/abc").status_code == 404


def test_bug1_on_long_id_500(fresh):
    assert fresh("1").get("/notes/" + "x" * 65).status_code == 500


def test_bug1_off_long_id_404(fresh):
    assert fresh("none").get("/notes/" + "x" * 65).status_code == 404


def test_bug3_on_summary_longer(fresh):
    c = fresh("3")
    c.post("/notes", json={"title": "a", "body": BODY})
    assert len(c.post("/notes/1/summarize").json()["summary"]) > len(BODY)


def test_openapi_hides_telemetry(fresh):
    paths = fresh("none").get("/openapi.json").json()["paths"]
    assert not any(p.startswith("/__qc") for p in paths) and "/notes" in paths
```

#### F-22 — `core/schema.py`

```python
"""Helper của contract: validate theo JSON Schema, kiểm luật liên-trường, dựng result tổng hợp (error/skipped).
KHÔNG được có tên worker nào trong file này (checklist A, câu 5)."""
import json
import pathlib
from functools import lru_cache

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_FILES = {"task": "schemas/task_spec.json", "result": "schemas/result.json"}


@lru_cache(maxsize=None)
def _validator(kind):
    schema = json.loads((ROOT / SCHEMA_FILES[kind]).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _errors(kind, obj):
    errs = sorted(_validator(kind).iter_errors(obj), key=lambda e: list(map(str, e.path)))
    return [("/".join(map(str, e.path)) or "<root>") + ": " + e.message[:200] for e in errs]


def validate_task(spec):
    return _errors("task", spec)


def validate_result(result):
    return _errors("result", result)


def check_result_against_spec(spec, result):
    """Luật liên-trường mà JSON Schema không diễn đạt được. Trả về danh sách vi phạm (rỗng = ổn)."""
    v = []
    if result.get("task_id") != spec["task_id"] or result.get("run_id") != spec["run_id"]:
        v.append("task_id/run_id không khớp spec")
    st = result.get("status")
    vd = result.get("verdict", {})
    if st in ("error", "skipped"):
        if vd.get("gating"):
            v.append(f"status={st} nhưng verdict.gating=true")
        return v
    if spec["expected_result_kind"] == "verdict":
        if vd.get("gating") is not True:
            v.append("expected_result_kind=verdict nhưng gating != true (task gate sẽ tụt khỏi gate trong im lặng)")
        if vd.get("value") not in ("pass", "fail"):
            v.append("expected_result_kind=verdict nhưng verdict.value không phải pass/fail")
        elif vd.get("value") != st:
            v.append(f"status={st} lệch verdict.value={vd.get('value')}")
    elif vd.get("gating") is not False:
        v.append("expected_result_kind=candidate_finding nhưng gating=true (discovery không được chặn gate)")
    for f in result.get("findings", []):
        if f.get("verdict_source") == "llm_judgment" and f.get("confidence") is None:
            v.append(f"finding {f.get('finding_id')}: llm_judgment thiếu confidence")     # arch §0.3 G4
    have = {e["kind"] for e in result.get("evidence", [])}
    miss = [k for k in spec["evidence_required"] if k not in have]
    if miss:
        v.append(f"thiếu evidence bắt buộc: {miss}")                                     # arch §6.6 bất biến 1
    return v


def make_result(spec, status, reason, worker_name="unknown", adapter_version="core"):
    """Result tổng hợp khi worker không cho ra kết quả hợp lệ. status ∈ {error, skipped}.
    error -> verdict.value='fail', gating=false: verdict.py vẫn đánh gate ĐỎ theo status (không theo gating)."""
    assert status in ("error", "skipped")
    return {
        "task_id": spec["task_id"], "run_id": spec["run_id"],
        "worker": {"name": worker_name, "version": None, "adapter_version": adapter_version},
        "status": status,
        "verdict": {"value": "fail" if status == "error" else "non_gating",
                    "verdict_source": "heuristic", "gating": False,
                    "confidence": None, "rationale": reason},
        "findings": [], "metrics": {}, "evidence": [],
        "cost": {"wallclock_s": 0.0, "tokens": None, "usd": None},
        "sut_identity_ref": spec.get("sut_identity_ref"),
        "determinism": {}, "adapter_notes": [reason],
    }
```

#### F-23 — `tools/review_adapter.py`

```python
"""Checklist 5 câu của A cho một adapter (arch §3.2).
    python tools/review_adapter.py --result runs/x/result.json --spec tests/fixtures/task_x.json --crash runs/x/crash.json
  --result : result của một lần chạy BÌNH THƯỜNG   --spec : task spec đã dùng để sinh ra nó
  --crash  : result của một lần chạy mà worker BỊ HỎNG có chủ đích (tắt toy app / gỡ key / script lỗi cú pháp)
Exit 0 = 5/5 · 1 = có câu FAIL.
"""
import argparse, hashlib, json, pathlib, re, sys

sys.path.insert(0, ".")
from core import schema  # noqa: E402


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--crash", required=True)
    a = ap.parse_args()
    res = json.loads(pathlib.Path(a.result).read_text(encoding="utf-8-sig"))
    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8-sig"))
    crash = json.loads(pathlib.Path(a.crash).read_text(encoding="utf-8-sig"))
    rows = []

    e = schema.validate_result(res)
    rows.append(("1. validate qua ĐÚNG result.json, không trường riêng", not e, "; ".join(e[:3])))

    ok2 = crash.get("status") == "error" and not schema.validate_result(crash)
    rows.append(("2. worker hỏng -> status=error (không phải fail/pass)", ok2, f"status={crash.get('status')}"))

    bad = [ev["uri"] for ev in res.get("evidence", [])
           if not pathlib.Path(ev["uri"]).exists() or sha(ev["uri"]) != ev["sha256"]]
    rows.append(("3. mọi evidence tồn tại và sha256 khớp file", not bad and bool(res.get("evidence")), f"lệch: {bad}"))

    v = schema.check_result_against_spec(spec, res)
    rows.append(("4. verdict_source / gating đúng luật allOf + expected_result_kind", not v, "; ".join(v[:3])))

    names = [m.group(1) for f in pathlib.Path("workers").glob("*.yaml") if not f.name.startswith("_")
             for m in [re.search(r"^name:\s*(\S+)", f.read_text(encoding="utf-8-sig"), re.M)] if m]
    hits = [f"{p}:{n}" for p in pathlib.Path("core").glob("*.py")
            for n in names if re.search(rf"\b{re.escape(n)}\b", p.read_text(encoding="utf-8-sig"), re.I)]
    rows.append(("5. không có tên worker nào trong core/", not hits, f"thấy: {hits[:3]}"))

    for label, ok, why in rows:
        print(("PASS " if ok else "FAIL ") + label + ("" if ok else "   <- " + why))
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
```

#### F-24 — `tests/test_schema.py`

```python
import copy, json, pathlib

import pytest

from core import schema


def L(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


K6S, K6R = L("examples/task.k6.json"), L("examples/result.k6_pass.json")
MS, MP, MF = L("examples/task.midscene.json"), L("examples/result.e2e_pass.json"), L("examples/result.e2e_fail.json")
EV = L("examples/result.ai_eval.json")


def fit(spec, res):
    s = copy.deepcopy(spec)
    s["task_id"], s["run_id"] = res["task_id"], res["run_id"]
    return s


AI_SPEC = fit(K6S, EV)


@pytest.mark.parametrize("res", [K6R, MP, MF, EV], ids=["k6", "e2e_pass", "e2e_fail", "ai_eval"])
def test_examples_validate(res):
    assert schema.validate_result(res) == []


@pytest.mark.parametrize("spec", [K6S, MS], ids=["k6", "midscene"])
def test_task_examples_validate(spec):
    assert schema.validate_task(spec) == []


@pytest.mark.parametrize("spec,res", [(K6S, K6R), (fit(MS, MP), MP), (fit(MS, MF), MF), (AI_SPEC, EV)],
                         ids=["k6", "e2e_pass", "canary_fail", "ai_eval"])
def test_good_pairs_have_no_violations(spec, res):
    assert schema.check_result_against_spec(spec, res) == []


def _mut(base, fn):
    o = copy.deepcopy(base)
    fn(o)
    return o


def test_gate_task_silently_non_gating():
    r = _mut(K6R, lambda o: o["verdict"].update(gating=False, value="non_gating"))
    assert schema.check_result_against_spec(K6S, r)


def test_discovery_task_cannot_gate():
    r = _mut(MP, lambda o: o["verdict"].update(gating=True, verdict_source="deterministic_assert", value="pass"))
    assert any("discovery" in v for v in schema.check_result_against_spec(fit(MS, r), r))


def test_llm_finding_without_confidence():
    r = _mut(EV, lambda o: o["findings"][2].pop("confidence"))
    assert any("confidence" in v for v in schema.check_result_against_spec(AI_SPEC, r))


def test_missing_required_evidence():
    r = _mut(K6R, lambda o: o.update(evidence=o["evidence"][:1]))
    assert any("evidence" in v for v in schema.check_result_against_spec(K6S, r))


def test_status_disagrees_with_verdict():
    assert schema.check_result_against_spec(K6S, _mut(K6R, lambda o: o.update(status="fail")))


def test_run_id_mismatch():
    assert schema.check_result_against_spec(dict(K6S, run_id="r-9999"), K6R)


@pytest.mark.parametrize("status", ["error", "skipped"])
def test_make_result_is_valid_and_utf8(status):
    r = schema.make_result(K6S, status, "lý do có dấu: không thấy k6", worker_name="k6")
    assert schema.validate_result(r) == [] and schema.check_result_against_spec(K6S, r) == []
    assert json.loads(json.dumps(r, ensure_ascii=True)) == r


def test_known_gap_g4_schema_does_not_reject_llm_finding_without_confidence():
    """Lỗ hổng ĐÃ BIẾT (arch §0.3 G4): schema không chặn. Adapter/check_result_against_spec phải chặn."""
    r = _mut(EV, lambda o: o["findings"][2].pop("confidence"))
    assert schema.validate_result(r) == []
```

#### F-25 — `tests/test_verdict.py`

```python
import pytest

from core.verdict import gate_verdict


def R(status="pass", value="pass", gating=True, src="deterministic_assert", rat=None):
    return {"status": status, "verdict": {"value": value, "gating": gating, "verdict_source": src, "rationale": rat}}


G = {"lane": "gate"}
D = {"lane": "discovery"}
SKIP = R("skipped", "non_gating", False, "heuristic", "thiếu key")
ERR = R("error", "fail", False, "heuristic", "crash")

CASES = [
    ("all_pass",              {"a": G, "b": G},           {"a": R(), "b": R()},                    "PASS", 0),
    ("one_fail",              {"a": G, "b": G},           {"a": R(), "b": R("fail", "fail")},      "FAIL", 1),
    ("error_in_gate",         {"a": G, "b": G},           {"a": R(), "b": ERR},                    "FAIL", 1),
    ("error_in_discovery",    {"a": G, "m": D},           {"a": R(), "m": ERR},                    "PASS", 0),
    ("skipped_gate_yellow",   {"a": G, "b": G},           {"a": R(), "b": SKIP},                   "YELLOW", 0),
    ("discovery_finding_ok",  {"a": G, "m": D},           {"a": R(), "m": R("pass", "non_gating", False, "heuristic")}, "PASS", 0),
    ("empty_and_is_fail",     {"a": G},                   {"a": SKIP},                             "FAIL", 1),
    ("missing_gate_result",   {"a": G, "b": G},           {"a": R()},                              "FAIL", 1),
    ("fail_beats_skipped",    {"a": G, "b": G, "c": G},   {"a": R("fail", "fail"), "b": SKIP, "c": R()}, "FAIL", 1),
    ("llm_never_in_verdict",  {"a": G},                   {"a": R()},                              "PASS", 0),
]


@pytest.mark.parametrize("name,specs,results,value,code", CASES, ids=[c[0] for c in CASES])
def test_gate_verdict(name, specs, results, value, code):
    g = gate_verdict(results, specs)
    assert (g.value, g.exit_code) == (value, code)


def test_banner_lists_skipped_and_error():
    g = gate_verdict({"a": R(), "b": SKIP, "c": ERR}, {"a": G, "b": G, "c": G})
    assert {t for t, _ in g.banner} == {"b", "c"}
```

#### F-26 — `tests/test_contract_mutants.py`

```python
"""Mutation testing CỦA HỢP ĐỒNG: cố tình làm hỏng một result/task hợp lệ, schema PHẢI từ chối.
Mutant nào không bị từ chối = lỗ hổng của contract (đây là N2/N4 ở dạng test).
Tên test = ID mutant (C1.., T1..) — tools/run_mutants.py đọc kết quả theo tên này."""
import copy, json, pathlib

import pytest

from core import schema


def L(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))


GATE = L("examples/result.ai_eval.json")          # verdict gating=true, deterministic_assert
DISC = L("examples/result.e2e_pass.json")         # discovery: heuristic, gating=false
TASK = L("examples/task.k6.json")
TASK_DISC = L("examples/task.midscene.json")


def mutate(base, fn):
    o = copy.deepcopy(base)
    fn(o)
    return o


RESULT_MUTANTS = {
    "C1_gating_true_with_llm_judgment":   (GATE, lambda o: o["verdict"].update(verdict_source="llm_judgment", confidence=0.7)),
    "C2_deterministic_with_confidence":   (GATE, lambda o: o["verdict"].update(confidence=0.9)),
    "C3_evidence_sha256_too_short":       (GATE, lambda o: o["evidence"][0].update(sha256="abc123")),
    "C4_missing_status":                  (GATE, lambda o: o.pop("status")),
    "C5_status_not_in_enum":              (GATE, lambda o: o.update(status="timeout")),
    "C6_extra_top_level_field":           (GATE, lambda o: o.update(worker_id="k6")),
    "C7_heuristic_gating_true":           (DISC, lambda o: o["verdict"].update(gating=True)),
    "C8_evidence_kind_outside_vocab":     (GATE, lambda o: o["evidence"][0].update(kind="screenshots")),
    "C9_missing_wallclock":               (GATE, lambda o: o["cost"].pop("wallclock_s")),
}

TASK_MUTANTS = {
    "T1_retry_on_fail":                   (TASK, lambda o: o["retry"].update(on=["fail"])),
    "T2_retry_max_3":                     (TASK, lambda o: o["retry"].update(max=3)),
    "T3_discovery_expects_verdict":       (TASK_DISC, lambda o: o.update(expected_result_kind="verdict")),
    "T4_gate_expects_candidate_finding":  (TASK, lambda o: o.update(expected_result_kind="candidate_finding")),
    "T5_worker_name_in_spec":             (TASK, lambda o: o.update(worker="k6")),
    "T6_budget_missing_usd":              (TASK, lambda o: o["budget"].pop("usd")),
    "T7_evidence_required_outside_vocab": (TASK, lambda o: o["evidence_required"].append("screenshots")),
}


@pytest.mark.parametrize("name", RESULT_MUTANTS)
def test_result_mutant_is_rejected(name):
    base, fn = RESULT_MUTANTS[name]
    assert schema.validate_result(mutate(base, fn)), f"{name}: schema KHÔNG chặn — contract có lỗ hổng"


@pytest.mark.parametrize("name", TASK_MUTANTS)
def test_task_mutant_is_rejected(name):
    base, fn = TASK_MUTANTS[name]
    assert schema.validate_task(mutate(base, fn)), f"{name}: schema KHÔNG chặn — contract có lỗ hổng"
```

#### F-27 — `guards/plan_guard.py`

```python
"""DG-1 — guard diff của plan.yaml (arch §7.2). Thu hẹp phạm vi kiểm thử ⟹ BLOCK (cần người duyệt);
chỉ THÊM ⟹ tự merge được.
    python guards/plan_guard.py --old plan_old.yaml --new plan_new.yaml
    python guards/plan_guard.py --base-ref HEAD~1 --path plan.yaml      # so bản ở ref với bản hiện tại
Exit: 0 = không thu hẹp · 1 = BLOCK (REQUIRES_HUMAN) · 3 = dùng sai.
KHÔNG bắt được (arch §7.3): nới ngưỡng (p95 < 300 -> < 3000), giảm budget, đổi inputs — chỉ in NOTICE."""
import argparse
import subprocess
import sys

import yaml


def analyze(old: dict, new: dict):
    """-> (blocks, notices, added_task_ids)"""
    blocks, notices = [], []
    ot = {t["task_id"]: t for t in old.get("tasks") or []}
    nt = {t["task_id"]: t for t in new.get("tasks") or []}
    for tid, t in ot.items():
        if tid not in nt:
            blocks.append(f"task {tid} bị XOÁ")
            continue
        if t.get("lane") == "gate" and nt[tid].get("lane") != "gate":
            blocks.append(f"task {tid} bị HẠ khỏi gate lane ({t.get('lane')} -> {nt[tid].get('lane')})")
        for key in ("oracle", "budget", "inputs"):
            if t.get(key) != nt[tid].get(key):
                notices.append(f"task {tid}: `{key}` đổi — guard này KHÔNG kiểm (arch §7.3)")
    osel, nsel = old.get("selection") or {}, new.get("selection") or {}
    for tid in osel.get("floor") or []:
        if tid not in (nsel.get("floor") or []):
            blocks.append(f"{tid} bị bỏ khỏi selection.floor")
    for path, ids in (osel.get("impact") or {}).items():
        now = (nsel.get("impact") or {}).get(path)
        if now is None:
            blocks.append(f"selection.impact['{path}'] bị XOÁ")
        elif set(ids) - set(now):
            blocks.append(f"selection.impact['{path}'] mất {sorted(set(ids) - set(now))}")
    old_ao = {a["when"]: set(a["run"]) for a in osel.get("always_on") or []}
    new_ao = {a["when"]: set(a["run"]) for a in nsel.get("always_on") or []}
    for when, run in old_ao.items():
        if when not in new_ao:
            blocks.append(f"selection.always_on['{when}'] bị XOÁ")
        elif run - new_ao[when]:
            blocks.append(f"selection.always_on['{when}'] mất {sorted(run - new_ao[when])}")
    return blocks, notices, [t for t in nt if t not in ot]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--base-ref")
    ap.add_argument("--path", default="plan.yaml")
    a = ap.parse_args(argv)
    try:
        if a.base_ref:
            old_text = subprocess.run(["git", "show", f"{a.base_ref}:{a.path}"], capture_output=True, text=True,
                                      encoding="utf-8", check=True).stdout
            new_text = open(a.path, encoding="utf-8-sig").read()
        elif a.old and a.new:
            old_text, new_text = open(a.old, encoding="utf-8-sig").read(), open(a.new, encoding="utf-8-sig").read()
        else:
            print("dùng: --old X --new Y  hoặc  --base-ref REF [--path plan.yaml]")
            return 3
        blocks, notices, added = analyze(yaml.safe_load(old_text) or {}, yaml.safe_load(new_text) or {})
    except (OSError, subprocess.CalledProcessError, yaml.YAMLError) as e:
        print("LỖI ĐẦU VÀO:", e)
        return 3
    for n in notices:
        print("NOTICE:", n)
    if blocks:
        for b in blocks:
            print("REQUIRES_HUMAN:", b)
        return 1
    if notices:
        print(f"NO_NARROWING_BUT_REVIEW_SUGGESTED: không xoá/hạ task nhưng có {len(notices)} NOTICE ở trên — người nên liếc")
    else:
        print(f"AUTO_MERGE_OK: không thu hẹp phạm vi; thêm {len(added)} task {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

#### F-28 — `tests/guards/test_plan_guard.py`

```python
import copy, pathlib, sys

import yaml

sys.path.insert(0, ".")
from guards import plan_guard  # noqa: E402

BASE = yaml.safe_load("""
tasks:
  - {task_id: t-001, lane: gate,      oracle: {kind: checks, required: [a]}, budget: {wallclock_s: 120}, inputs: {x: 1}}
  - {task_id: t-002, lane: gate,      oracle: {kind: threshold, assertions: [{metric: p95, op: "<", value: 300}]}}
  - {task_id: t-101, lane: discovery, oracle: {kind: implicit_signals, signals: [dom_unchanged]}}
selection:
  floor: [t-001, t-002]
  impact: {"toyapp/app.py": [t-001, t-002]}
  always_on:
    - {when: "toyapp/auth/**", run: [t-sec-01]}
""")


def run(fn):
    new = copy.deepcopy(BASE)
    fn(new)
    return plan_guard.analyze(BASE, new)


def test_identical_is_ok():
    blocks, notices, added = run(lambda p: None)
    assert (blocks, notices, added) == ([], [], [])


def test_adding_a_task_is_auto_mergeable():
    blocks, _, added = run(lambda p: p["tasks"].append({"task_id": "t-003", "lane": "gate"}))
    assert blocks == [] and added == ["t-003"]


def test_removing_a_task_blocks():
    blocks, _, _ = run(lambda p: p["tasks"].pop(1))
    assert any("t-002" in b and "XOÁ" in b for b in blocks)


def test_renaming_a_task_blocks_because_old_id_disappears():
    def rename(p):
        p["tasks"][0]["task_id"] = "t-001b"
    assert run(rename)[0]


def test_removing_from_floor_blocks():
    assert any("floor" in b for b in run(lambda p: p["selection"]["floor"].remove("t-002"))[0])


def test_removing_impact_entry_or_always_on_blocks():
    assert run(lambda p: p["selection"]["impact"].clear())[0]
    assert run(lambda p: p["selection"]["always_on"].clear())[0]


def test_demoting_gate_task_to_discovery_blocks():
    def demote(p):
        p["tasks"][1]["lane"] = "discovery"
    assert any("HẠ" in b for b in run(demote)[0])


def test_KNOWN_GAP_loosening_a_threshold_is_NOT_blocked_only_noticed():
    """arch §7.3: guard này KHÔNG bắt được việc nới ngưỡng — chỉ in NOTICE. Test này ghi lại giới hạn."""
    def loosen(p):
        p["tasks"][1]["oracle"]["assertions"][0]["value"] = 3000
    blocks, notices, _ = run(loosen)
    assert blocks == [] and any("oracle" in n for n in notices)
```

#### F-29 — `guards/protected_paths.py`

```python
"""DG-2 + DG-3 — guard các artifact định nghĩa chuẩn mực (arch §7.2). Đọc git, không đọc nội dung file.
    python guards/protected_paths.py [--base-ref HEAD~1]
Xét mọi commit trong  <base-ref>..HEAD.
  DG-3 golden/baseline: BẤT KỲ commit nào sửa golden set / baseline  ⟹ exit 2 NEEDS_REVIEW (phải qua PR có người duyệt).
  DG-2 patch của agent/healer: commit có trailer `Agent-Patch: true` mà đụng file test/plan/schema ⟹ exit 1 BLOCK
       (không tự merge; phải là PR riêng do người duyệt). Commit agent-patch trộn lẫn file được bảo vệ và file thường ⟹ BLOCK.
Exit: 0 sạch · 1 BLOCK · 2 NEEDS_REVIEW · 3 dùng sai.  BLOCK thắng NEEDS_REVIEW.
GIỚI HẠN: `Agent-Patch` là QUY ƯỚC của PoC (chưa có healer thật); agent quên trailer thì guard không thấy. Guard PHÁT HIỆN,
không PHÊ DUYỆT — cổng merge thật (PR review) nằm ngoài PoC."""
import argparse
import fnmatch
import subprocess
import sys

DG3_GOLDEN = ["tests/eval/golden.json", "baselines/*", "baselines/**"]
DG2_TESTS = ["tests/*", "tests/**", "midscene/*", "midscene/**", "plan.yaml", "plans/*", "schemas/*", "workers/_template.yaml"]


def _match(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def analyze(base_ref):
    """-> list[(level, sha, message)] với level ∈ {"BLOCK", "NEEDS_REVIEW"}"""
    findings = []
    for sha in _git("rev-list", "--reverse", f"{base_ref}..HEAD").split():
        files = [f for f in _git("show", "--name-only", "--format=", sha).splitlines() if f]
        body = _git("show", "-s", "--format=%B", sha)
        agent = any(line.strip().lower() == "agent-patch: true" for line in body.splitlines())
        golden = [f for f in files if _match(f, DG3_GOLDEN)]
        tests = [f for f in files if _match(f, DG2_TESTS)]
        if agent and (tests or golden):
            mixed = any(f not in tests and f not in golden for f in files)
            why = "trộn file được bảo vệ với file thường" if mixed else "sửa file được bảo vệ"
            findings.append(("BLOCK", sha[:8], f"DG-2: commit Agent-Patch {why}: {sorted(set(tests + golden))}"))
        elif golden:
            findings.append(("NEEDS_REVIEW", sha[:8], f"DG-3: sửa golden set/baseline: {golden}"))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="HEAD~1")
    a = ap.parse_args(argv)
    try:
        findings = analyze(a.base_ref)
    except subprocess.CalledProcessError as e:
        print("LỖI GIT:", e.stderr.strip() or e)
        return 3
    for level, sha, msg in findings:
        print(f"{level}: {sha} {msg}")
    if any(f[0] == "BLOCK" for f in findings):
        return 1
    if findings:
        return 2
    print("CLEAN: không có thay đổi vào artifact được bảo vệ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

#### F-30 — `tests/guards/test_protected_paths.py`

```python
import os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from guards import protected_paths  # noqa: E402


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Repo git tạm với 1 commit gốc; trả hàm commit(files, msg)."""
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")

    def commit(files, msg="change"):
        for f in files:
            p = tmp_path / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(p.read_text(encoding="utf-8") + "x" if p.exists() else "x", encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", msg)
    return commit


def levels():
    return [f[0] for f in protected_paths.analyze("HEAD~1")]


def test_ordinary_change_is_clean(repo):
    repo(["core/x.py"])
    assert levels() == []


def test_golden_change_needs_review(repo):
    repo(["tests/eval/golden.json"])
    assert levels() == ["NEEDS_REVIEW"]


def test_baseline_change_needs_review(repo):
    repo(["baselines/geval.json"])
    assert levels() == ["NEEDS_REVIEW"]


def test_agent_patch_on_test_file_is_blocked(repo):
    repo(["tests/test_x.py"], "heal selector\n\nAgent-Patch: true")
    assert levels() == ["BLOCK"]


def test_agent_patch_on_docs_only_is_clean(repo):
    repo(["docs/x.md"], "doc\n\nAgent-Patch: true")
    assert levels() == []


def test_agent_patch_mixing_protected_and_ordinary_is_blocked(repo):
    repo(["tests/test_x.py", "core/y.py"], "mixed\n\nAgent-Patch: true")
    assert levels() == ["BLOCK"]


def test_agent_patch_on_golden_is_blocked_not_just_reviewed(repo):
    repo(["tests/eval/golden.json"], "làm mềm golden\n\nAgent-Patch: true")
    assert levels() == ["BLOCK"]        # BLOCK thắng NEEDS_REVIEW
```

#### F-31 — `scripts/repeat.ps1`

```powershell
# scripts/repeat.ps1 - run the orchestrator N times and summarize each run. ASCII ONLY (see env.ps1).
#   .\scripts\repeat.ps1 -Times 3 [-Only "t-101,t-canary-01"] [-Plan plan.yaml]
# Exit 0 only if EVERY run produced valid QRS files and zero results with status=error.
param([int]$Times = 3, [string]$Only = "", [string]$Plan = "plan.yaml")
$bad = 0
for ($i = 1; $i -le $Times; $i++) {
    $argv = @("orchestrator.py", "--plan", $Plan)
    if ($Only -ne "") { $argv += @("--only", $Only) }
    & python @argv | Out-Null
    $code = $LASTEXITCODE
    $run = (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
    $files = @(Get-ChildItem "$run\results\*.json" | ForEach-Object { $_.FullName })
    & python tools\validate.py result @files | Out-Null
    $valid = ($LASTEXITCODE -eq 0)
    $errors = @($files | Where-Object { (Get-Content $_ -Raw -Encoding UTF8 | ConvertFrom-Json).status -eq "error" }).Count
    "run {0}: orchestrator_exit={1} valid_qrs={2} error_results={3} dir={4}" -f $i, $code, $valid, $errors, (Split-Path $run -Leaf)
    if ((-not $valid) -or ($errors -gt 0)) { $bad++ }
}
if ($bad -eq 0) { "ALL $Times RUNS OK"; exit 0 }
"$bad of $Times runs had invalid or error results"
exit 1
```

#### F-32 — `scripts/demo.ps1`

```powershell
# scripts/demo.ps1 - scripted demo (timeline of R5 5.2). ASCII ONLY (see env.ps1).
#   .\scripts\demo.ps1                interactive: press Enter between stages
#   .\scripts\demo.ps1 -Auto          no prompts (used for the timed dry-run, STEP 57)
#   .\scripts\demo.ps1 -Fast          run only the GATE tasks live; show the discovery lane from the RECORDED run
# Prerequisite: toy app running with the seeded bugs ON:  .\scripts\toyapp.ps1 start
param([switch]$Auto, [switch]$Fast, [string]$Plan = "plan.yaml")

$rec = "recordings\demo-good-run"

function Stage([string]$title) {
    Write-Host ""
    Write-Host ("=== " + $title + " ===") -ForegroundColor Cyan
    if (-not $Auto) { Read-Host "Enter to run" | Out-Null }
}
function LastRun {
    (Get-ChildItem runs -Directory | Where-Object Name -like "r-*" | Sort-Object Name | Select-Object -Last 1).FullName
}
function GateIds([string]$runDir) {
    ((Get-Content "$runDir\report.json" -Raw -Encoding UTF8 | ConvertFrom-Json).deterministic_view | ForEach-Object { $_.task_id }) -join ","
}

$sw = [Diagnostics.Stopwatch]::StartNew()
$marks = @()

Stage "1. plan.yaml is COMMITTED. CI runs exactly this file. No LLM decides what runs."
Get-Content $Plan -Encoding UTF8 -TotalCount 40
$marks += ("stage1 {0:mm\:ss}" -f $sw.Elapsed)

Stage "2. run the orchestrator (safe workers in parallel; k6 and Midscene run alone)"
if ($Fast) {
    Write-Host "(-Fast: gate tasks run LIVE; the discovery lane is shown from the RECORDED run in $rec)" -ForegroundColor Yellow
    python orchestrator.py --plan $Plan --only (GateIds $rec)
    $show = $rec
} else {
    python orchestrator.py --plan $Plan
    $show = $null
}
"orchestrator exit code = $LASTEXITCODE"
$r1 = LastRun
if (-not $show) { $show = $r1 }
$marks += ("stage2 {0:mm\:ss}" -f $sw.Elapsed)

Stage "3. report: three sections that NEVER add up into one pass-rate"
Get-Content "$show\report.md" -Encoding UTF8
$marks += ("stage3 {0:mm\:ss}" -f $sw.Elapsed)

Stage "4. one finding was detected by a deterministic signal -> promote candidate"
Select-String -Path "$show\report.md" -Pattern "dom_unchanged|promote" | ForEach-Object { $_.Line }
$marks += ("stage4 {0:mm\:ss}" -f $sw.Elapsed)

Stage "5. run the GATE tasks AGAIN (same commit), then diff the deterministic part -> must be IDENTICAL"
python orchestrator.py --plan $Plan --only (GateIds $r1) | Out-Null
$r2 = LastRun
python tools\diff_runs.py $r1 $r2
$marks += ("stage5 {0:mm\:ss}" -f $sw.Elapsed)

Stage "6. add a 5th worker: how many lines of core/ changed?"
$h = git log --grep="add mock2 worker" -1 --format=%h
git show --stat $h
$marks += ("stage6 {0:mm\:ss}" -f $sw.Elapsed)

Stage "7. canary: a task that MUST fail, run on the autonomous worker"
Select-String -Path "$show\report.md" -Pattern "CANARY" | ForEach-Object { $_.Line }
$marks += ("stage7 {0:mm\:ss}" -f $sw.Elapsed)

Stage "8. mutation table: which seeded faults the gate catches"
Get-Content docs\mutants.md -Encoding UTF8
$marks += ("stage8 {0:mm\:ss}" -f $sw.Elapsed)

Write-Host ""
$marks | ForEach-Object { Write-Host $_ }
"TOTAL elapsed {0:mm\:ss}" -f $sw.Elapsed
```

---

**HẾT `plan-execution.md`.** Nguồn: [architecture.md](architecture.md) (RUN 6).
