# Slide outline — QC Agent · 12 slide / 10 phút

**Nguồn duy nhất:** `docs/report/00-tldr.md`, `03-prior-art.md`, `04-architecture.md`,
`09-roadmap.md` + `knowledge/_derived/08` (slide 11). Không có nội dung mới ngoài 4 file báo cáo.
**Thứ tự:** thuyết phục, **không** theo thứ tự nghiên cứu — mở bằng kết luận gây tranh cãi nhất,
đóng bằng bằng chứng `[R5 P5.1]`.

---

## Ngân sách thời gian — 10 phút, và chỗ phải cắt

| Slide | Phút | Ghi chú |
|---|---|---|
| 01 TL;DR | 0:00–0:50 | Nói hết thông điệp ngay. Nếu bị cắt ở phút 1 thì slide này đã đủ |
| 02 Vì sao dễ sập | 0:50–1:40 | |
| 03 Prior art | 1:40–2:40 | **Không rút ngắn.** Đây là slide ghi điểm tin cậy |
| 04 Hai lane | 2:40–3:30 | |
| 05 Universal Contract | 3:30–4:30 | Chiếu JSON thật, không chiếu schema |
| **06 Demo** | **4:30–7:00** | ⭐ 2.5 phút, **không phải 10 phút** — xem cảnh báo dưới |
| 07 Tự động hoá 7/10 | 7:00–7:40 | |
| 08 Kinh tế | 7:40–8:20 | |
| 09 Giới hạn | 8:20–9:10 | **Không cắt.** Đây là mục ghi điểm, không phải mục mất điểm |
| 10 Lộ trình | 9:10–10:00 | |
| 11, 12 | — | **Slide dự phòng cho Q&A.** Không trình bày trong 10 phút |

> ⚠️ **Cảnh báo về demo.** Kịch bản demo trong `[R5 5.2]` dài **10 phút**, tức bằng toàn bộ thời
> lượng. Với 10 phút tổng, demo **phải cắt còn ~2.5 phút** và chỉ giữ hai khoảnh khắc mạnh nhất:
> (1) report tách 3 mục, (2) **chạy 2 lần → `diff` phần tất định rỗng**. Bỏ: chạy live 4 worker
> (40 giây log cuộn), `git show --stat`, canary. Ba thứ đó chuyển sang Q&A bằng ảnh chụp sẵn.
> **Không chạy live thứ phụ thuộc API bên ngoài** — dùng bản ghi của một run tốt.

---

## Slide 01 — TL;DR: chúng tôi không đề xuất "một AI agent làm QC"

- **Không** đề xuất một AI agent làm QC. Trong một run gate xanh, cái được gọi là "orchestrator
  agent" **không phải agent**: routing là phép lọc registry, thứ tự là sắp xếp topo, so ngưỡng là
  số học, gộp verdict là phép `AND`.
- Đề xuất: **Universal Contract phân biệt nguồn phán quyết (`verdict_source`) + chính sách 2 lane**,
  chạy trên một lớp điều phối mỏng, **LLM nằm ngoài đường chạy CI**.
- Bằng chứng: **PoC 4 worker khác loại, 1 schema, verdict tái lập 100% (IDENTICAL)**. Gate xanh =
  **0 lời gọi LLM**.

*Câu định vị chốt `[R11]`, đọc nguyên văn:* "Phần nhóm đào sâu và chứng minh bằng PoC là **hợp đồng
kết quả và chính sách phán quyết** — để lớp giữa của sản phẩm QC Agent **không nói dối**, kể cả khi
kiểm thử một AI application."

nguồn: `docs/report/00-tldr.md` §1–§4 + câu định vị `[R11]`

---

## Slide 02 — Vì sao "Agent QC" dễ sập: bài toán 57% của TestGen-LLM

- **6 khái niệm bị marketing trộn lẫn** thành một: traditional automation → AI-assisted →
  AI test generation → AI testing agent → autonomous agent → **agentic quality engineering**. Khác
  biệt không nằm ở "có LLM" mà ở **thời điểm quyết định** (design-time vs runtime) và **chỗ nào LLM
  được phép quyết**.
- **Neo định lượng — TestGen-LLM của Meta trên Instagram Reels/Stories:** 75% test build được,
  **57% pass ổn định**, 25% tăng coverage. Đọc theo hướng kiến trúc: **43% output bị vứt, và cái làm
  nó dùng được không phải LLM — mà là bộ filter tất định** (build được? pass ổn định? có tăng
  coverage?). Meta gọi đó là *"assured LLM-based software engineering"*.
- **Câu chốt của cả bài:** LLM sinh ứng viên, **oracle tất định quyết định cái nào sống**. Nếu hệ
  này làm đúng một việc thôi, nên là việc này.

nguồn: `[R1 S1]` qua `docs/report/04-architecture.md` §4.2 · `VERIFIED` arxiv.org/abs/2402.09171

---

## Slide 03 — Đứng trên vai người khổng lồ: landscape & prior art

- **Trả lời thẳng: ĐÃ CÓ.** Hercules (LangGraph planner→executor→assertion, AGPL-3.0),
  **Testkube** (orchestration mọi tool container hoá trên K8s, 5 năm), **ReportPortal** (gom kết quả
  + triage ML, **10 năm**). Cộng **Playwright Test Agents** — prior art **ủng hộ** hướng của chúng tôi
  (planner xuất Markdown plan vào `specs/`, healer trả code patch → LLM ở design-time).
- **Điểm thiếu hụt chí mạng duy nhất mà cả bốn đều có: không có verdict provenance.** Hercules và
  Testkube đều hội tụ về **JUnit XML** — nhét `verdict_source` vào `<properties>` được về lý thuyết,
  nhưng **không consumer chuẩn nào gate trên trường đó**. Kết quả: một verdict LLM và một assert tất
  định **không phân biệt được nữa**. Trong 14 ứng viên khảo sát, **không tool nào** phát ra
  `verdict_source`.
- **Vì sao không dùng thẳng — ranh giới là VỊ TRÍ CỦA LLM, không phải đóng vs mở.** Hercules **có**
  `ADDITIONAL_TOOL_DIRS` + MCP nav agent (stdio/sse/streamable-http) — chúng tôi **không** nói nó
  khép kín. Nhưng planner + assertion nằm trong đường chạy **mọi** run ⟹ gate không tái lập.
  **Hercules là lựa chọn hợp lệ cho Discovery lane** (có thể thay Midscene nếu muốn phủ rộng hơn),
  **không phù hợp làm Gatekeeper tự động**.

*Ba câu cố ý KHÔNG nói, vì chúng sai:* rằng chưa có ai xây orchestration cho QC · rằng Hercules là
hệ khép kín · rằng chưa có ai đặt LLM ra ngoài đường phán quyết. Câu đúng: **chưa có ai áp nguyên
tắc đó xuyên nhiều loại worker kèm `verdict_source`, đặc biệt cho AI application.**

nguồn: `docs/report/03-prior-art.md` §3.0–§3.4 · `[R4 S14.2]` + `[R5 CORRECTION]`

---

## Slide 04 — Kiến trúc đề xuất: hai làn đường (Gate vs Discovery)

- **Cùng MỘT contract, hai lane.** Gate: worker tất định, `expected_result_kind: verdict`,
  `gating: true`, chạy mỗi PR, **tái lập bắt buộc**, 0 lời gọi LLM. Discovery: worker tự hành,
  `candidate_finding`, `gating: false`, nightly có trần ngân sách, **không yêu cầu tái lập**.
- **Lý do tách — không phải vì gọn, mà vì N2.** Cho worker tự hành quyền chặn merge là đưa thẳng
  phương sai LLM vào gate: *"vi phạm N2 dù report có dán nhãn 'LLM judgment' đi nữa, vì cái bị chặn
  là merge, không phải cái nhãn"*. Cưỡng chế bằng `allOf` trong schema: `gating: true` **⟹**
  `verdict_source = deterministic_assert`. Vi phạm thì **không qua được cửa validate**, kể cả khi
  người viết quên.
- **Mũi tên *promote* là giá trị thật của Discovery** — không phải "agent tìm thêm bug", mà **sinh
  ứng viên cho gate lane** (đúng mô hình TestGen-LLM slide 02). Điều kiện: `deterministic_assert`
  **∧** tái lập ≥3/3 **∧** có `suggested_assertion`.

*Nếu bị hỏi "sao chọn orchestrator–worker?"* → **không** trả lời "vì QC cần tính xác định" (đó là
category error). Trả lời: **worker QC không có phụ thuộc lẫn nhau** — k6 không cần biết DeepEval
nghĩ gì — nên không có gì để thương lượng ngang hàng. **Tính xác định không đến từ hình dạng sơ đồ;
nó đến từ việc quy định chỗ nào LLM được phép quyết** (N1) `[R10]`.

nguồn: `docs/report/04-architecture.md` §4.0–§4.2 (N1–N5 · Two-Lane Policy)

---

## Slide 05 — Universal Contract: hợp đồng 3 mảnh, minh hoạ JSON thật

- **Ba mảnh, không phải hai:** `workers/<name>.yaml` (manifest — worker tự khai capability/lane/
  `data_egress`; **không có manifest thì orchestrator buộc phải biết tên worker, và N5 chết**) →
  `task_spec.json` (orchestrator **khai `oracle`**, worker không tự chọn — cưỡng chế N1 bằng **cấu
  trúc** thay vì quy ước) → `result.json` (worker trả kết quả **+ nguồn phán quyết**).
- **Chiếu đúng một khối JSON: `examples/result.ai_eval.json`.** Một result của **một** worker chứa
  **hai** `verdict_source`: `f-11` (`deterministic_assert`, metric độ dài) và `f-12` (`llm_judgment`,
  G-Eval 0.71, `confidence: 0.64`). `verdict.value = "fail"` **chỉ do `f-11`**; điểm G-Eval **không
  tham gia**. Đây là lý do `verdict_source` phải ở cấp **từng finding** — phát hiện **nhờ verify
  DeepEval**, không nhờ suy luận.
- **`capability`, không phải tên worker.** Plan nói `http.load`, không nói `k6`. Đổi sang Locust là
  sửa một dòng registry, **không đụng plan** (N5). Và `confidence` **buộc phải `null`** khi
  `deterministic_assert` — nên câu "assert tất định có độ tin 0.9" là câu **không biểu diễn được**
  trong hệ này.

nguồn: `docs/report/04-architecture.md` §4.3 (3 khối JSON thật từ `examples/`) · `schemas/CONTRACT.sha256`

---

## Slide 06 — Demo thực chiến: PoC 4 worker · nói rõ cái gì là MOCK / REPLAY trước khi bị hỏi

- **Chiếu (2.5 phút):** (1) `runs/r-0019/report.md` — **3 mục không bao giờ cộng vào nhau**,
  `gate_verdict = pass AND pass AND pass AND pass`, header ghi `LLM tokens: 0 · cost $0.00`;
  (2) ⭐ **`python tools/diff_runs.py runs/r-0022 runs/r-0023` → `IDENTICAL — 4 kết quả gating`**.
  Không ai cãi được một `diff` rỗng. Bốn run `r-0020…r-0023` cùng `run_signature f0df138d…`,
  **3/3 cặp liên tiếp IDENTICAL**.
- **4 worker CHẠY THẬT, cùng một `result.json`, không worker nào cần trường riêng:** `http-collect`
  (`http.collect`) · `schemathesis` (`api.property`) · `k6` (`http.load`) · `deepeval` (`llmapp.eval`).
  Worker thứ 5 `midscene-cli` (`ui.explore`) chạy thật ở Discovery lane, cho finding
  `implicit_signal:dom_unchanged` **có sẵn `suggested_assertion`** → ứng viên promote.
  Đo được: `tools/validate.py` → **4/4 PASS** trên result thật, **4/4 PASS** trên `examples/`,
  result Midscene cũng **PASS** trên **cùng** schema.
- **🔴 LIỆT KÊ TRUNG THỰC — cái gì KHÔNG thật, nói TRƯỚC khi bị hỏi:**

| Thành phần | Trạng thái | Chi tiết |
|---|---|---|
| AI feature của SUT (`/summarize`) | **MOCK — SUT GIẢ LẬP** | `toyapp/summarizer.py`: stub **tất định**, `MODEL = "stub-rule-v1"`, **KHÔNG gọi model nào**. Chúng tôi đang test một AI app giả |
| 3 lỗi bị bắt | **CÀI SẴN** (`QC_BUGS=1,2,3`) | BUG-1 id dài → 500 (Schemathesis bắt) · BUG-2 bấm Xoá không vẽ lại (Midscene bắt) · BUG-3 `[[long]]` → summary dài hơn body (DeepEval bắt). **Không phải bug tự tìm thấy** |
| `baseline` của G-Eval | **VIẾT TAY** | `baselines/geval.json` = `{"GEval": 0.78}`. **Không có baseline store** ⟹ chưa gate được theo delta |
| Mục 2 (LLM JUDGMENT) của report | **TRỐNG trong mọi run gate** | `r-0019` `adapter_notes`: *"G-Eval advisory report không hợp lệ; deterministic checks không đổi"*. Phần advisory **hỏng thật** |
| Ngưỡng k6 `p95 < 300ms` | **NGƯỠNG DEMO** | `plan.yaml` ghi rõ: *"ngưỡng rộng: demo cơ chế, không phải số đo thật"*. Đo được p95 ≈ 15–25ms |
| Trigger (PR webhook / cron / `/qc run`) | **VẼ, KHÔNG CÀI** | PoC dùng **1 lệnh CLI** đóng vai trigger |
| GitHub Checks / Slack · auto-promote | **VẼ / THIẾT KẾ XONG, KHÔNG CÀI** | Promote demo **bằng tay** |
| `REPLAY` của Midscene | **có sẵn, KHÔNG dùng** | Adapter hỗ trợ `QC_REPLAY_MIDSCENE`; run trên slide là **live thật** (3 lượt: 2 pass, 1 error do Gemini 503 — **giữ nguyên số đo, không retry để biến lỗi thành pass**) |

- **Một chi tiết không dàn dựng, đáng giá hơn cả bảng tiêu chí:** trong `r-0019`, phần **advisory
  G-Eval hỏng thật** mà `gate_verdict` **không bị ảnh hưởng**. Đó là N2 hoạt động trong điều kiện
  xấu, không phải trong slide.

nguồn: `docs/report/04-architecture.md` §4.4, §4.7 · `runs/r-0019`…`r-0023` · `docs/decisions.md` (STEP 29) · `[R5 quy tắc 2]`

---

## Slide 07 — Mức độ tự động hoá thực tế: 7/10 bước tự động, 3 bước giữ con người

- **7 bước tự động hoàn toàn:** test data · execution · failure detection · regression selection ·
  reporting · aggregation · và phần **soạn** của bug reporting.
- **3 bước giữ con người — và cả ba nằm đúng một chỗ:** nơi **hành động không đảo ngược rẻ được**
  hoặc **chuẩn mực bị thay đổi**. (1) **Duyệt diff `plan.yaml`** — bỏ human thì coverage trôi âm
  thầm: PR xanh vì worker đúng **không được gọi**, không ai phát hiện vì **không có gì đỏ**. Mức tự
  động cao nhất còn an toàn: **tự merge diff chỉ THÊM task, bắt buộc người khi XOÁ/THU HẸP**.
  (2) **Sửa golden set / baseline** — đây là **gian lận khó phát hiện nhất** (sửa chuẩn cho test
  pass) ⟹ **không tự động hoá**, bắt buộc qua PR có review. (3) **Kết luận bất đồng LLM vs
  deterministic** — tự gom + đếm + mở task khi ≥3 run; **kết luận thì người**.
- **Ticket không bao giờ tự nộp.** Phân loại sai **không đối xứng**: coi bug là flaky ⟹ **mất bug**;
  coi flaky là bug ⟹ spam. Nên ticket là `tickets_draft[]` trong `report.json`, **người bấm một nút
  cho cả lô**. Tương tự: self-heal **ra PR riêng**, không tự merge — *heal rate cao là mùi hỏng,
  không phải thành tích*.

nguồn: `docs/report/04-architecture.md` §4.4 (N3 · 4 điểm escalation · 7 điểm HITL) · `[R4 S13.3, S13.7]`

---

## Slide 08 — Bài toán kinh tế: gate xanh = 0 token LLM

- **Ba khoản, và con số quan trọng nhất là số 0.** Gate mỗi PR: **0 token** — vì plan **đã pin**,
  routing là tra bảng, gộp verdict là phép `AND`. Lập plan (`plan-gen`): **một lần** khi spec đổi,
  **ngoài CI**, người duyệt diff. Triage: **chỉ khi gate đỏ**. Đo thật trên `r-0019`:
  `LLM tokens: 0 · cost $0.00`, wallclock 40s.
- **Hệ quả ngoài tiền: gate KHÔNG PHỤ THUỘC MẠNG.** Không cần provider nào sống để cho ra verdict.
  Latency điều phối là **mili giây**; latency tổng là **`max(worker)` không phải `sum(worker)`** —
  chạy song song là **bắt buộc, không phải tối ưu hoá**. Con số ~15× token của một orchestrator
  LLM-mỗi-PR **không áp dụng** cho kiến trúc này.
- **Toàn bộ tính khả thi trong CI phụ thuộc ĐÚNG MỘT quyết định: plan-as-artifact.** Bỏ nó (re-plan
  mỗi PR) thì câu trả lời **đảo ngược** — thêm một lần gọi LLM đọc spec + manifest vào **mọi** PR,
  và lúc đó 15× bắt đầu áp dụng. Discovery lane là khoản chi thật: Midscene ~118k token ≈ **$0.31**
  cho 15 bước (đối chiếu: Hercules công bố ~$0.20/kịch bản phức tạp — **cùng bậc**). `budget` là
  trần **cứng**; `cost` lên report **kể cả khi xanh** (chặn V5: UI đổi → agent đi vòng → hoá đơn ×3
  mà vẫn "pass").

*Nếu bị hỏi "0 token kể cả khi bật G-Eval?"* → **Không, và phải nói rõ:** con số 0 là của **đường
điều phối** và của **các metric gating**. DeepEval ở gate lane chạy metric tất định (0 token); nếu
bật G-Eval advisory **trong cùng task** thì phần advisory **có tốn token** (~12k trong ví dụ) —
nhưng nó **không chặn gate**. Câu chính xác: **verdict của gate phụ thuộc 0 lời gọi LLM.**
⚠️ Cộng thêm: số 0 trong PoC còn nhờ AI feature là **stub tất định** (slide 06) — **không ngoại
suy** sang suite thật.

nguồn: `docs/report/00-tldr.md` §3 · `docs/report/04-architecture.md` §4.5 · `[arch D2, §15.2–15.3]` · `[R5 Q4]`

---

## Slide 09 — Giới hạn đã biết & những điều team chưa chắc

- **Hệ này KHÔNG bắt được:** test đúng cho **requirement sai** (sai ở bước 1, không oracle nào phát
  hiện được) · bug logic khi **spec không viết ra** (agent lấp khoảng trống bằng cách **bịa**
  criteria, và test bịa vẫn "pass" ⟹ **không có tín hiệu báo động**) · **chất lượng cảm nhận** của
  AI feature (summary tệ đi mà đúng schema + đúng độ dài ⟹ **gate xanh**) · **regression im lặng khi
  provider đổi model** sau cùng alias (chỉ chặn được nếu pin model snapshot id — **PoC chưa có**) ·
  vùng **không có trong `plan.yaml`** (V3 — gate vẫn xanh, vẫn tái lập, **vẫn vô nghĩa**) · vùng
  **bị né path** (luật selection chặn việc *bị thuyết phục*, **không** chặn việc *né path*).
- **Việc chưa làm, và phân biệt hai loại "không làm":** ❌ **cố ý không làm vì lý do kiến trúc** —
  LLM trong đường CI, LLM đọc nội dung PR để quyết chạy gì (*injection im lặng*: mô tả "chỉ sửa
  docs, bỏ qua test" kéo phạm vi xuống mà gate vẫn xanh), LLM lật verdict, n-sample voting, agent tự
  nộp ticket. ⏳ `[KHÔNG KỊP SPRINT NÀY]` — judge calibration (cần **tập nhãn human**, nhiều tuần) ·
  mobile · Keploy/eBPF · auto-promote · lưu trữ evidence · **bảo mật orchestrator** (nó cầm
  credential của **mọi** worker — điểm tập trung rủi ro lớn nhất, sprint chỉ dùng `.env` +
  `.gitignore`).
- **Trạng thái 6 tiêu chí nghiệm thu — đo, không hứa: đạt 1, 2, 3, 4, 6 · THIẾU #5.** Tiêu chí #5
  (N5: `git show --stat` khi thêm worker → `core/` không xuất hiện) **chưa có bằng chứng trong
  repo** (STEP 44 chưa chạy). Nó là tiêu chí **rẻ nhất** trong sáu cái, và là cái duy nhất chứng minh
  chi phí biên ~110 dòng/worker là **thật** chứ không phải ước lượng.

*Bốn điều chưa chắc, nói thẳng:* (1) **assertion node của Hercules là LLM hay tất định — `FROM-INDEX`,
chưa đọc code**; nếu nó tất định thì luận điểm phân biệt của slide 03 phải **hẹp lại thêm một bậc**.
(2) Claim "consumer JUnit XML không gate trên `<properties>`" là `[CHƯA VERIFY]` — sai thì luận điểm
prior art **yếu đi, không sụp**. (3) Ước lượng **680 dòng dùng chung / ~110 dòng mỗi worker** là
**phán đoán kỹ thuật, không phải số đo**, **có thể lệch hệ số 2** — đừng đưa con số chính xác lên
slide. (4) **Mọi số đo là của toy app** — không ngoại suy.

nguồn: `docs/report/04-architecture.md` §4.7–§4.8 · `docs/report/00-tldr.md` (bảng giới hạn) · `[arch §9]`

---

## Slide 10 — Lộ trình phát triển 4 pha

- **Pha 1 Web+API** (gate PR trên 1–2 repo pilot) → **Pha 2 Mobile** → **Pha 3 Performance**
  (nightly) → **Pha 4 Continuous AI Eval**. Mỗi pha đo bằng **"phần contract/policy ta thêm vào"**,
  **không** bằng số tool cắm thêm: cắm một tool là ~1.5 giờ và **0 dòng trong `core/`** — việc đó đã
  giải. Cái đắt là khi một pha đòi **`oracle.kind` mới** (Pha 1: `rule_violations`; Pha 2:
  `crash_signals`; Pha 3: `threshold_delta`), **baseline store** (Pha 3–4), hoặc **cách bóc state
  mới** (Pha 2).
- **Chúng tôi phản đối một phần thứ tự này, và nói ra:** Perf và AI-Eval **đã chạy thật trong PoC**
  (chi phí biên thấp); **Mobile là pha đắt nhất và là pha duy nhất không phải bài toán QC** — phải
  viết lại **cách bóc UI state** (a11y tree/UiAutomator/logcat thay DOM) + chi phí emulator/device
  farm. **Khuyến nghị: đổi chỗ Pha 2 và Pha 3** nếu chưa có device farm chạy được. *(Điểm sáng thật
  của Android: **crash/ANR trong logcat là oracle tất định mạnh và MIỄN PHÍ** — web không có.)*
- **Đường di cư, không phải đường cạnh tranh:** `core/registry.py` + `runner.py` → **Testkube CRD**;
  `core/report.py` + failure-analysis → **ReportPortal Auto-Analysis**. **GIỮ LẠI, KHÔNG DI CƯ:**
  `result.json` · `verdict_source` · 2 lane · `AND(gating)` · 4 trạng thái · canary — **đây là lớp
  không ai có**. ⚠️ Rào đã biết: Testkube/ReportPortal đều hội tụ JUnit XML, nên khi di cư
  `result.json` phải đi **song song**, **không được** bị thay thế — nếu mất nó thì mất toàn bộ phần
  mới của bản propose.

*Lưu ý Pha 1:* **Keploy là TUỲ CHỌN, không bắt buộc** — trên Windows cần **WSL2 (Ubuntu 22.04) hoặc
Docker Desktop** vì rào **eBPF** (kernel ≥ 5.10), ước tính **nửa ngày**. Nó vào khi team dựng
WSL2/Docker **vì lý do khác**. Và giới hạn của record–replay phải nói cùng lúc: **replay khoá chặt
hành vi hiện tại, kể cả hành vi sai** — nó đóng băng nguyên trạng, không phát hiện bug.

nguồn: `docs/report/09-roadmap.md` §9.0–§9.4

---

## Slide 11 — So với QC_Agent_Proposal gốc: giống / khác / kế thừa gì

**Slide dự phòng cho Q&A — không trình bày trong 10 phút.** Mục đích: khi mentor so hai bản của
cùng một team, câu trả lời đã có sẵn và **không biến thành tranh luận thắng–thua**.

- **GIỐNG (hai bản đứng cùng tiền đề):** cùng pattern orchestrator/supervisor · cùng chia worker
  nhóm (a) tự hành / (b) tất định · cùng roster ứng viên (Midscene, Explorbot, agent-device, Keploy,
  k6, DeepEval) · cùng nguyên tắc "càng nhiều quyết định tự chủ, càng cần điểm kiểm soát con người ở
  output cuối" (≡ N3) · cùng cảnh báo **đừng bật hết mọi nhánh ngay** · cùng thấy mobile là phần khó
  (đẩy sang Pha 2). **Và cùng một chỗ yếu:** lý do chọn hierarchical (*"QC cần tính xác định"*) là
  đúng cái **category error** đã bị bác ở RUN 1 — cần sửa **cho cả hai bản** trước khi ai đó nộp lên
  mentor.
- **KHÁC — 4 chỗ thực chất, khác biệt lớn nhất KHÔNG phải chọn tool mà là CHỖ ĐẶT LLM:**

| # | Bản gốc `QC_Agent_Proposal` | Bản này (RUN 1–5 + PoC) | Lý do chúng tôi giữ quan điểm |
|---|---|---|---|
| 1 | **Planner (Claude) đọc diff PR mỗi lần**, đối chiếu app map, quyết loại test | LLM biên dịch spec → `plan.yaml` **một lần, commit**; CI chạy plan đã pin | (a) **Prompt injection vào coverage** — nội dung PR là **dữ liệu không có thẩm quyền** nhưng lại quyết định cái gì được chạy; (b) cùng commit → hai phạm vi khác nhau; (c) thêm 1 lời gọi LLM vào **mọi** PR. **Điểm hội tụ:** chính bản gốc viết *"bắt đầu từ rule-based routing rồi nâng dần"* — **pha đầu của bản gốc đã là tất định** |
| 2 | **Triage agent tự xây** + **tự tạo Jira** cho lỗi "thật" | Không tự xây; failure-analysis chỉ khi đỏ; ticket là **draft, người bấm cả lô** | Hành động ra ngoài, không đảo ngược rẻ; phân loại sai **không đối xứng** (coi bug là flaky ⟹ **mất bug**). Và **ReportPortal** đã có Auto-Analysis bằng ML huấn luyện trên dữ liệu thật — ta không có dữ liệu đó |
| 3 | Thực thi "**pluggable theo domain**" — không có đặc tả plug thế nào | **Contract 3 mảnh** + adapter + registry YAML; `verdict_source` ở từng finding | Bản gốc **thiếu đúng chỗ kiến trúc sống-hoặc-chết** (N4), và không trả lời được *"LLM-judge fail + assert pass thì gộp thế nào vào một GitHub Check?"* |
| 4 | **Lộ trình** pilot 2–3 tuần trên 1–2 repo thật | **Sprint 2.5 ngày** trên toy app | **Không mâu thuẫn — hai quy mô khác nhau.** Bản gốc là "lộ trình 30/60/90" của bản này (slide 10). Cảnh báo: Pha 1 bản gốc dùng **Keploy** trên repo thật — ổn cho pilot 2–3 tuần, **không ổn cho sprint** |

- **KẾ THỪA — 5 thứ bản gốc có mà bản này thiếu, đã LẤY:** (1) **Trigger layer** cụ thể (PR webhook /
  nightly cron / comment `/qc run`) — cụ thể hoá "hai chế độ chạy" mà bản này mới nêu ở mức nguyên
  tắc; (2) **luật risk theo domain** (`auth/**`, `payment/**` ⟹ luôn bật security) — nhưng cài thành
  **luật tất định** `always_on` trên plan đã pin, **không** để LLM đọc PR; (3) **self-heal phải ra PR
  riêng**, không tự merge; (4) **lỗi bảo mật route sang queue riêng**, không lên board công khai;
  (5) **app map + flaky list** vào knowledge store — nhưng flaky list **có hạn 14 ngày**, vì list do
  agent tự nuôi có thể thành **cái mặt nạ che bug thật**. Cộng: **judge ≠ model production** (bản gốc
  phát biểu thành rule, bản này có ý nhưng chưa tường minh) và **ẩn danh dữ liệu người dùng trước khi
  vào golden set**.
- **Fact-check tài liệu gốc (2026-09-18): không có claim nào BỊA.** 2 chỗ dễ gây hiểu sai — Explorbot
  gọi là "mã nguồn mở" nhưng README ghi **ELv2** (đúng phải là *source-available*); agent-device
  **không chứa LLM nào** (là tool layer, agent bên ngoài mới chọn lệnh). 1 số lệch nhẹ — Keploy
  "20K+ sao" thực tế **18.465**. **Không phát hiện lỗi làm đổi kết luận kiến trúc.** ⚠️ Tài liệu gốc
  **không có URL nguồn, không có ngày truy cập, không có nhãn độ tin** — nên **không trích số từ đó
  lên slide khi chưa tự kiểm**. Đây không phải chê: đó là tài liệu đề xuất, không phải bản research
  có evidence protocol.
- **Kết luận phải nói ra: đây là HAI LỚP BỔ SUNG NHAU, không phải hai phương án loại trừ nhau.**
  Đề xuất hợp nhất: **khung 5 lớp của bản gốc làm hình ngoài** (Trigger → … → GitHub Checks/Slack),
  **contract + policy của bản này làm ruột lớp 3→5**.

nguồn: `knowledge/_derived/08-qcagent-compare-teammate-proposal.md` §1–§7 (+ `_derived/09`) · `[R5 P3]`

---

## Slide 12 — Phụ lục & bản đồ nghiên cứu

**Slide dự phòng cho Q&A — không trình bày trong 10 phút.**

- **Bản đồ nghiên cứu (chuỗi nguồn, 5 run):** `03` RUN 1 (S1–S4: taxonomy 6 khái niệm · thang
  agentic 9 trục · 4 phản biện kiến trúc) → `04` RUN 2 (S5–S8: 7 surface · 7 bậc oracle · taxonomy
  9 nhóm) → `05` RUN 3 (S9–S11: verify **14 ứng viên** bằng repo thật · kinh tế tầng điều phối) →
  `06` RUN 4 (S12–S15: contract 3 mảnh · policy · **research gaps** · 5 kịch bản vỡ V1–V5) → `07`
  RUN 5 (P1–P5: chốt PoC · **CORRECTION về Hercules** · plan 2.5 ngày) → `docs/architecture.md`
  (bản chốt để code) → `docs/plan-execution.md` (56 STEP) → `docs/report/`.
- **Bảng fact cần double-check — 4 dòng còn ảnh hưởng tới kết luận:** (1) **assertion node của
  Hercules là LLM hay tất định** (`FROM-INDEX`, chưa đọc code) — **phải kiểm trước khi lên slide**;
  (2) consumer JUnit XML **không** gate trên `<properties>` tuỳ biến `[CHƯA VERIFY]`; (3) ước lượng
  **680/110 dòng** — phán đoán, không phải số đo, **có thể lệch hệ số 2**; (4) ngưỡng **"tái lập
  ≥3/3"** để auto-promote — *"con số tôi chọn, không phải con số đo được"*.
- **Nhãn nguồn dùng xuyên suốt:** `VERIFIED` (đã mở repo/doc, có URL + ngày truy cập) ·
  `FROM-INDEX` / `FROM-DOC` (chỉ đọc doc/README, **chưa đọc code**) · `[SUY RA]` (bắc cầu từ hai fact
  đã có, **kèm chuỗi suy luận**) · `[CHƯA VERIFY]` (nguồn tự gắn nhãn là chưa kiểm bằng thực nghiệm)
  · `[KHÔNG KỊP SPRINT NÀY]` · `[EXTERNAL GAP]` (ngoài knowledge base). **Mọi trích dẫn `DayNN` là
  gián tiếp** (qua `02-pareto-tiers.md`, không mở lại deck gốc) — các số như 4.5× cost, 15× token,
  ngưỡng 80% **có thể lệch so với deck gốc**.

- **Năm câu hỏi khó nhất, đã chuẩn bị câu trả lời:** Q1 *"Khác gì Jenkins gọi mấy tool này tuần
  tự?"* → **"Về phần thực thi, gần như không khác — và đó là chủ ý."** Ba thứ Jenkins không cho:
  `verdict_source`, registry theo capability, hai lane cưỡng chế bằng schema. *"Bỏ ba thứ đó ra thì
  đúng, cái này là Jenkins. Chúng tôi không claim gì hơn."* · Q2 *"Sao không dùng 1 agent cho đơn
  giản?"* → tái lập, chi phí, cô lập lỗi — **nhưng agent đơn tốt hơn cho việc điều tra một lỗi sâu**,
  và đó đúng là chỗ chúng tôi định dùng LLM (chỉ khi gate đã đỏ) · Q3 *"LLM sai thì sao?"* → **thiết
  kế với giả định LLM SẼ sai**; ở gate lane nó **không có mặt** · Q4 *"Chi phí token?"* → slide 08 ·
  Q5 *"Đã có Hercules rồi sao còn build?"* → slide 03 (**câu nguy hiểm nhất, RUN 4 suýt trả lời
  sai**).

nguồn: `knowledge/_derived/01`–`09` · `docs/architecture.md` §0, §9.3 · `docs/report/*` · `[R5 5.3]`
