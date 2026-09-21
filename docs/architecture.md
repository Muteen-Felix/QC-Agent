# architecture.md — QC Agent PoC

**Trạng thái:** bản chốt để code · **Ngày:** 2026-09-18
**Nguồn duy nhất:** `qcagent-run1-S1-S4.md` … `qcagent-run5-P1-P5.md`. File này **không thêm**
tool, số liệu, hay kết luận mới.
**Đối tượng:** 3 dev code PoC (A/B/C) + người maintain sau sprint.

## Quy ước nhãn trong file này

| Nhãn | Nghĩa |
|---|---|
| `[R1 S4e3]` | Fact lấy từ RUN 1, mục S4 phản biện 3. Tương tự `[R2 S7.1]`, `[R3 9.6]`, `[R4 S13.4]`, `[R5 P2.4]` |
| **[SUY RA]** | Không có câu nào trong RUN 1–5 nói thẳng, nhưng suy ra được **bằng phép bắc cầu từ hai fact đã có**. Chuỗi suy luận được ghi kèm. Dev được phép cãi chỗ này |
| **[NGOÀI RUN 1–5]** | Không có trong nguồn. Chỉ xuất hiện đúng 1 chỗ (Mục 7), được đánh dấu rõ, **không phải quyết định đã chốt** |
| `[CHƯA VERIFY]` | RUN 1–5 tự gắn nhãn là chưa kiểm bằng thực nghiệm. Đừng code như thể nó đúng |

---

# 0. MÂU THUẪN & KHOẢNG TRỐNG TRONG RUN 1–5 — ĐỌC TRƯỚC KHI CODE

Ràng buộc của task này là: phát hiện RUN 1–5 mâu thuẫn thì **nêu ra, không tự chọn bên**.
Dưới đây là toàn bộ những gì tìm được. Ba nhóm, xử lý khác nhau.

## 0.1 Mâu thuẫn THẬT — cần team quyết trước khi code (1 chỗ)

### ⛔ M1. Lỗi cài sẵn #3 của toy app là ngẫu nhiên hay tất định?

| Nguồn | Câu |
|---|---|
| `[R5 2.1]` | Lỗi cài sẵn #3: "`summarize` **đôi khi** trả summary dài hơn bản gốc" — DeepEval bắt bằng **metric tất định** |
| `[R5 2.3 #4]` | Tiêu chí PoC #4: chạy 2 lần cùng plan + cùng commit → **phần deterministic giống hệt**, `diff` sau khi lọc `llm_judgment` phải **rỗng** |
| `[R5 2.5]` | Báo cáo mẫu lại in `t-003 deepeval ✅ pass — 3/3 metric tất định đạt` |

**Mâu thuẫn:** nếu bug fire *"đôi khi"* theo nghĩa ngẫu nhiên, thì metric tất định của DeepEval
pass ở run 1 và fail ở run 2 → tiêu chí #4 **không thể đạt**, mà #4 nằm trong danh sách
**KHÔNG BAO GIỜ CẮT** `[R5 4.2]`. Đây không phải chuyện nhỏ: #4 là khoảnh khắc mạnh nhất của
demo `[R5 5.2, phút 5:30]`.

**Hai cách đọc, tôi KHÔNG chọn hộ:**

| Cách đọc | Hệ quả | Giá phải trả |
|---|---|---|
| (a) "Đôi khi" = **tất định theo input**: có 1–2 note trong 5 golden case mà stub sinh summary dài hơn body | #4 đạt. Cần dùng SUT giả lập (hàm stub), không gọi model thật | Mất tính "thật" của AI feature — nhưng `[R5 2.1]` đã cho phép stub và yêu cầu ghi rõ "SUT giả lập" |
| (b) "Đôi khi" = **ngẫu nhiên thật** (gọi model thật, temperature > 0) | AI feature thật hơn | **#4 gãy** → phải hạ tiêu chí #4 thành "lọc thêm cả task `llmapp.eval` ra khỏi diff" → lúc đó #4 chỉ còn chứng minh cho 2 worker, yếu đi rõ rệt |

**Ai quyết:** C (chủ toy app backend + adapter DeepEval) đề xuất, A (chủ contract) chốt,
**trong slot 1**, vì nó quyết định luôn nội dung 5 golden case.

## 0.2 Chỗ RUN sau đã tự đính chính / thay thế RUN trước — KHÔNG phải mâu thuẫn treo (4 chỗ)

| # | Chỗ | Bản cũ | **Bản dùng** | Ai đính chính |
|---|---|---|---|---|
| R1 | Hercules có cắm được tool bên thứ ba không | `[R4 S14.2]`: "không có registry cho tool bên thứ ba" | **Cắm được** (`ADDITIONAL_TOOL_DIRS` + MCP nav agent). Ranh giới đúng là **vị trí của LLM**, không phải đóng/mở | `[R5 CORRECTION]`, và `[R4]` đã có ghi chú đính chính chèn ngược |
| R2 | `verdict_kind` khai ở đâu | `[R2 S7 kết luận]`: "worker manifest phải khai `verdict_kind`" | **Nằm ở từng kết quả và từng finding** `[R3 9.6]`, `[R4 S12 nguyên lý 4]`. Manifest vẫn khai `verdict_sources` nhưng **với nghĩa khác**: đó là *tập giá trị worker được phép phát ra* (dùng để routing + validate), không phải nhãn của kết quả | `[R3 9.6]` — phát hiện nhờ verify DeepEval |
| R3 | Từ vựng | `[R3 (b)]` gọi 3 giá trị là `deterministic` / `llm-judgment` / `candidate_finding` — trộn 2 trục vào 1 | **Hai trục tách hẳn:** `verdict_source ∈ {deterministic_assert, llm_judgment, heuristic}` và `expected_result_kind ∈ {verdict, candidate_finding}` | `[R4 S12]` + `[R5 P2.4]` (schema chốt) |

| R4 | Midscene có chứng minh "N1 tách đôi" (agent lái, `expect()` phán quyết) trong PoC không | `[R3 (b)]` bảng PoC: Midscene "chứng minh N1 tách đôi: agent lái, `expect()` phán quyết"; `[R2 S5-2]` "PoC nên demo đúng chỗ tách này" | **Không, không theo nghĩa đó.** `[R5 2.2]` (mới hơn) đặt Midscene ở **discovery lane** với `oracle.kind: implicit_signals`, **không có `expect()` của Playwright**, `gating: false`. Phần "tách đôi" trong PoC thể hiện ở chỗ khác: **tín hiệu tất định** (`deterministic_assert`) vs **nhận xét LLM** (`llm_judgment`) trong cùng một result. Chưa có worker E2E nào ở **gate** lane chạy thật trong PoC → **đừng hứa với mentor** demo "agent lái + expect() phán" trừ khi có người thêm nó | `[R5 2.2]` thắng `[R3 (b)]` |

> **Luật đọc nguồn cho cả team:** RUN sau thắng RUN trước khi hai bên nói khác nhau về **cùng
> một** thứ. Không mở lại R1–R3 ở trên trừ khi có bằng chứng mới (vd đọc code Hercules).

## 0.3 Khoảng trống — nguồn không nói, dev sẽ phải đoán (5 chỗ)

Mỗi chỗ tôi đưa **giá trị đề xuất** có đánh dấu **[SUY RA]** kèm chuỗi suy luận, để dev code
được ngay; ai không đồng ý thì sửa ở đúng một chỗ.

| # | Khoảng trống | Nguồn nói gì | **[SUY RA]** — giá trị dùng, và suy từ đâu |
|---|---|---|---|
| G1 | **Exit code khi gate "vàng"** (có `skipped`) | `[R4 S13.3]`: `skipped` → gate **vàng**, "chạy tiếp, report ghi rõ vùng không được kiểm". `[R5 2.3 #1]` chỉ định nghĩa `1` khi fail, `0` khi xanh | Vàng **không chặn** ⟹ `exit 0`, **nhưng** khối `SKIPPED/ERROR` in ở **đầu** report `[R4 V2]`. Suy từ: "chạy tiếp" = không chặn = cùng nghĩa với xanh ở tầng exit code. ⚠️ Đây đúng là failure mode V2 (gate xanh vì worker không chạy) — xem Mục 9 |
| G2 | **Từ vựng `evidence.kind`** | `[R4 S12a]` task spec dùng `"screenshots"` (số nhiều) và `"metrics"`; `[R4 S12b]` result dùng `"screenshot"` (số ít), `"raw_output"`, `"stdout"`, `"trace"` | Chốt **một** từ vựng dùng chung cho cả `evidence_required` và `evidence[].kind`: `raw_output` \| `stdout` \| `metrics` \| `screenshot` \| `trace`. Suy từ: hai trường này so khớp nhau ở bước validate "evidence bắt buộc có đủ" `[R4 S13.6 bất biến 1]` — khác từ vựng thì bước đó không chạy được |
| G3 | **`gating` ở cấp finding** | `[R4 S12c]`: adapter DeepEval "chỉ những metric `deterministic_assert` mới được `gating: true`" — nhưng schema `[R5 P2.4]` **không có** trường `gating` trong `findings[]` | `gating` chỉ tồn tại ở `verdict`, **không** ở finding. Với worker lai: `verdict` = AND các metric tất định (`gating: true`), metric advisory thành `findings[]` mang `llm_judgment`. Suy từ: công thức gộp `[R4 S13.4]` chỉ đọc `r.verdict.gating`, finding **không** tham gia phép AND ⟹ finding không cần `gating` |
| G4 | **Schema không ép `confidence` khi finding là `llm_judgment`** | `[R4 S12c]` bắt buộc có `confidence` cho phần văn xuôi; schema `[R5 P2.4]` chỉ ép ràng buộc `allOf` ở `verdict`, không ở `findings[]` | **Adapter** cưỡng chế, không phải schema. `_base.py` kiểm: `finding.verdict_source == llm_judgment ⟹ confidence != null`, sai thì `error`. Không sửa schema trong sprint vì schema đã đóng băng từ slot 1 `[R5 2.4]`. Ghi vào Mục 9 như một giới hạn |

| G5 | **Canary là worker hay là task?** | `[R5 2.2]` liệt "canary (mock)" như một *worker* riêng; `[R4 S15.4 lớp 3]` mô tả nó là *task cài sẵn vào mỗi run discovery* để kiểm worker tự hành | Dùng nghĩa của `[R4 S15.4]`: **canary là một TASK chạy trên chính worker discovery (Midscene)**, khai trong plan với `expected: fail`. Ví dụ 2 ở 4.2 viết theo nghĩa này. Suy từ: canary chỉ có ý nghĩa nếu nó kiểm *đúng worker* sẽ chạy task thật; một worker mock riêng thì không kiểm được gì về Midscene. Nhãn "(mock)" ở R5 hiểu là *bản nháp có kết quả kỳ vọng cố định*, không phải worker giả |

## 0.4 Lỗi nhỏ trong nguồn, không ảnh hưởng code

- `[R5 4.1]` ghi tiêu đề "**Sáu** rủi ro lớn nhất" nhưng bảng chỉ có **5 dòng**. Dùng 5.
- `[R3 (b)]` gọi Midscene là `verdict_kind: candidate_finding` — đó là giá trị của
  `expected_result_kind`, xem R3 ở mục 0.2.

---

# 1. CONTEXT & NON-GOALS

## 1.1 Bài toán

Team đang được yêu cầu đề xuất một "QC Agent". RUN 1–5 kết luận rằng phần **mới thật sự** không
nằm ở chỗ có agent, mà nằm ở chỗ: ba prior art trưởng thành nhất của lĩnh vực này — **TestZeus
Hercules** (planner/executor/assertion trên LangGraph), **Testkube** (chạy mọi tool đã container
hoá trên K8s), **ReportPortal** (gom kết quả + triage lỗi bằng ML, 10 năm tuổi) — **không cái nào
mang được nguồn phán quyết đi tới báo cáo**; tất cả hội tụ về JUnit XML, nơi một `assert` tất
định và một điểm LLM-judge trông y hệt nhau `[R4 S14.2]`. Vì vậy hệ này xây **một hợp đồng kết
quả phân biệt nguồn phán quyết (`verdict_source`) + chính sách hai lane**, chạy trên một lớp
điều phối **mỏng, không có LLM trong đường chạy CI** `[R4 S15.6]` `[R5 CORRECTION]`.

**Một câu cho mentor:** *Hercules đặt LLM vào đường phán quyết của mọi lần chạy; chúng tôi đặt
LLM ra ngoài nó. Với một quality gate, đó không phải khác biệt tính năng — đó là khác biệt về
việc gate có tái lập được hay không* `[R5 CORRECTION]`.

## 1.2 Ràng buộc cứng

| Ràng buộc | Con số | Hệ quả thiết kế |
|---|---|---|
| Thời gian | **2.5 ngày**, 5 slot `[R5 P3]` | Walking skeleton phải chạy **hết ngày 1**, không phải ngày 2 `[R5 P3 cổng ra slot 2]` |
| Người | **3** (A/B/C), A là đường găng `[R5 P3]` | Contract có **đúng một chủ** (A). Mọi adapter qua A review theo checklist 5 câu |
| Nền tảng | **Windows 11**, không WSL2, không emulator, không macOS runner | Loại Keploy (eBPF), mobile, iOS `[R3 9.5]` `[R2 S6]` |
| Cách làm | **Fork, không build framework** | *"Agent lái UI: Midscene/Hercules đã làm. Fork, đừng viết"* `[R4 S14.3]`. *"Không cần framework supervisor; cần một vòng lặp tool-calling và một registry worker"* `[R1 S4a]` |
| Quy mô lõi | ~400 dòng `core/` không LLM `[R5 P1.1]` | Nếu lõi phình, kiến trúc đã sai |

## 1.3 NON-GOALS — ta CHỦ ĐỘNG không làm

Chia hai loại. Phân biệt được hai loại này chính là thứ mentor gọi là hiểu sâu `[R5 1.2]`.

### (a) Không làm vì LÝ DO KIẾN TRÚC — làm là sai, không phải làm là chậm

| Không làm | Lý do kỹ thuật |
|---|---|
| **LLM trong đường chạy CI** | Toàn bộ tính tái lập của gate phụ thuộc đúng quyết định này `[R4 S15.3]`. Gate xanh = **0 lời gọi LLM** |
| **LLM đọc mô tả/comment/nội dung diff của PR để quyết định chạy gì** | (1) *Injection im lặng*: nội dung PR là **dữ liệu không có thẩm quyền** nhưng sẽ thành chỉ thị nếu nó quyết định coverage — mô tả "chỉ sửa docs, bỏ qua test" kéo phạm vi xuống mà gate vẫn xanh; (2) cùng commit → hai phạm vi khác nhau; (3) thêm 1 lời gọi LLM vào **mọi** PR `[R5 1.2 R2]` `[R4 S13.2b]` |
| **LLM chọn worker (routing)** | Chọn bằng LLM là đưa non-determinism vào **coverage** `[R4 S13.1]`. Routing là phép lọc trên registry, rẻ hơn và debug được `[R4 S15.2]` |
| **LLM lật verdict** (cả hai chiều) | Lật thành `fail`: gate đỏ không tái lập → team học cách bấm re-run. Lật thành `pass`: phi tất định ghi đè tất định `[R4 S13.4]` |
| **n-sample voting cho LLM judge** | Nhân chi phí ×n để mua ổn định cho thứ vốn **không được chặn gate** — đổi chác sai `[R4 S13.5]` |
| **Agent tự nộp ticket ra tracker** | Action ra ngoài, không đảo ngược rẻ; phân loại sai **không đối xứng** (coi bug là flaky ⟹ mất bug). Ticket là `tickets_draft[]`, người bấm một nút cho cả lô `[R4 S13.7 #3]` |
| **Agent tự merge patch tự sửa (self-heal)** | Heal rate cao là **mùi hỏng**, không phải thành tích; heal có thể **che regression thật** `[R1 S1]` `[R2 S8 G4]`. Patch đi thành **PR riêng** |

### (b) Không làm vì ĐÃ CÓ NGƯỜI LÀM TỐT HƠN

| Không làm | Ai đã làm |
|---|---|
| Execution engine đa tool/đa môi trường | **Testkube**, 5 năm trên K8s. Ta sẽ ra bản tệ hơn `[R4 S14.3]` |
| Triage lỗi bằng ML/LLM | **ReportPortal** có model huấn luyện trên dữ liệu thật; ta không có dữ liệu đó `[R4 S14.3]` |
| Agent lái UI | **Midscene / Hercules** `[R4 S14.3]` |

### (c) Không làm vì NGOÀI TẦM SPRINT — có nhãn `[KHÔNG KỊP SPRINT NÀY]`

Judge calibration harness (cần tập nhãn human, nhiều tuần) · mobile / agent-device / Maestro
(cần emulator) · Keploy & eBPF (WSL2/Docker ≈ nửa ngày = 20% sprint) · auto-promote finding
thành PR (**thiết kế xong** `[R4 S13.7 #2]`, demo bằng tay) · lưu trữ evidence dài hạn + dọn
rác · bảo mật orchestrator (sprint dùng `.env` local + `.gitignore`) `[R5 1.2]`.

### (d) Phạm vi bề mặt

**API + Web. Hết.** Android chỉ khi đã có sẵn emulator chạy được; iOS và Desktop Win32 loại vì
**rào hạ tầng**, không vì rào kỹ thuật (ký app / macOS runner; WinAppDriver mục nát) `[R2 S6]`.

---

# 2. ARCHITECTURE DECISION RECORDS

Năm quyết định, đúng năm chỗ RUN 1–5 gọi là D1–D5.

---

## D1 — Roster PoC: 4 worker, chọn theo A9 và theo tính đa dạng, không theo độ nổi tiếng

**Decision.** PoC chạy đúng 4 worker thật: **Schemathesis** (`api.property`, gate) ·
**k6** (`http.load`, gate) · **DeepEval** (`llmapp.eval`, gate theo metric) ·
**Midscene CLI** (`ui.explore`, discovery); cộng 2 task đặc biệt: **canary** và **mock-worker**.

**Context.**
- **Luật veto A9** `[R1 S2]`: worker không có CLI/SDK + structured output + exit code đáng tin
  (`A9 = 0`) bị **loại khỏi PoC bất kể A1–A8 đẹp đến đâu**, vì ép nó vào contract buộc phải viết
  adapter đặc thù = vi phạm N4/N5 ngay tại chỗ kiến trúc sống hoặc chết.
- **Luật lane A5** `[R1 S2]`: `A5 = 2` (LLM là oracle chính) → **không được giữ verdict chặn gate**.
- Verify 2026-09-18 `[R3 S9]`: Midscene là worker nhóm (a) **duy nhất** đạt `A9 = 2` (CLI
  `@midscene/cli`, mặc định headless, exit code 1 khi fail, `--summary <path>` xuất JSON).
- DeepEval sinh **cả hai** loại verdict — `Tool Correctness`/`JSON Correctness`/DAG là tất định,
  `G-Eval` là judge `[R3 9.6]`. Đây là worker duy nhất làm `verdict_source` **nhìn thấy được**
  trong demo.
- Tiêu chí chọn `[R3 (b)]`: A9=2 · setup ≤ ~30 phút trên Windows · **mỗi worker một loại test
  khác nhau** · đủ cả ba `verdict_source` · ít nhất một worker mỗi lane.

**Alternatives rejected.**

| Bỏ | Lý do **kỹ thuật** |
|---|---|
| **Explorbot** (agentic nhất, 15/18) | `A9 = 1`: thiết kế xoay quanh phiên tương tác, chưa xác nhận có JSON summary máy-đọc-được ⟹ ta phải **parse HTML/Markdown report** = đúng loại adapter đặc thù mà N4 cấm. Cộng: một lần chạy tính bằng **giờ**, không hợp PR gate. Cộng: **ELv2, không phải OSI** — gọi nó "open-source" là sai `[R3 9.2]` |
| **Keploy** | Rào **eBPF**: Linux kernel ≥ 5.10; Windows phải WSL2 (Ubuntu 22.04) hoặc Docker Desktop hoặc native chỉ-AMD-cần-admin. Ước tính nửa ngày = **20% ngân sách sprint** `[R3 9.5]` |
| **agent-device** | Xếp sai nhóm ban đầu: **nó không chứa LLM nào** (A1–A8 ≈ 0, tổng ~3/18), là *tool layer* "mắt và tay" cho mobile — đối trọng của Appium, không phải của Explorbot. Loại vì **cần emulator**, không vì chất lượng `[R3 9.3]` |
| **browser-use** (cộng đồng lớn nhất bảng) | Sinh ra cho **tự động hoá tác vụ**, **không có khái niệm oracle** ⟹ không phải công cụ QC `[R3 S10 #11]` |
| **Ragas** | Repo **đổi chủ** (`explodinggradients/` → `vibrantlabsai/`) và **7 tháng không push** (last push 2026-02-24) `[R3 S10 #9]` |
| **Skyvern** | AGPL-3.0 + kiến trúc server phải dựng `[R3 (b)]` |

**Consequences — ta MẤT gì.**
- Mất **toàn bộ bề mặt mobile**. Roster báo cáo vẫn có agent-device/Maestro nhưng PoC không
  chứng minh được gì cho mobile.
- Mất worker nhóm G2 (record–replay) — tức mất đường nhanh nhất để có coverage tích hợp cho hệ
  legacy `[R3 (a) #3]`, đúng thứ một team QC thật cần nhất.
- Mất worker **autonomous thật** (Explorbot 15/18). Midscene là GUI agent **step-bound** (nhóm
  G6), action space là **enum đóng** ⟹ `A2 = 1`, không phải explorer `[R3 D1]`. Demo "discovery
  lane" của ta do đó yếu hơn khả năng thật của lane này.
- 2/4 worker cần API key bên ngoài (VLM cho Midscene, model chấm cho DeepEval) ⟹ nhận luôn rủi
  ro V2 và rủi ro chi phí.

**Revisit trigger.**
1. Có sẵn emulator Android chạy được → cân nhắc lại agent-device/Maestro `[R2 S6]`.
2. Team quyết dựng WSL2/Docker vì lý do khác → Keploy trở lại, vì rào của nó đã được trả tiền.
3. Explorbot ra JSON summary máy-đọc-được + exit code có nghĩa (`A9` lên 2) → nó **mạnh hơn**
   Midscene ở discovery lane vì đã tự cài sẵn mũi tên *promote* (xuất spec Playwright commit
   được) `[R3 9.2]`.
4. Ràng buộc dữ liệu được siết (xem D4) → thứ tự egress `G8 > G6/G7 > G5 > G1–G3` quyết định
   roster **hơn mọi tiêu chí kỹ thuật** `[R3 S11(5)]` và roster này phải xếp lại từ đầu.

---

## D2 — Hai chế độ chạy trên cùng một contract; LLM nằm NGOÀI đường chạy CI

**Decision.** Orchestrator có **hai chế độ**: (1) **gate** — chạy mỗi PR, thực thi `plan.yaml`
**đã commit**, 0 lời gọi LLM; (2) **discovery** — chạy nightly, có trần ngân sách, không bao giờ
chặn merge. LLM chỉ xuất hiện ở **hai công cụ rời nằm ngoài đường chạy CI**: `plan-gen` (khi spec
đổi) và `failure-analysis` (chỉ khi gate đỏ).

**Context.**
- `[R1 S3]`: bước 9+10 hợp CI gate; bước 3+4 hợp dev-time; bước 5-bằng-exploration và bước 7
  hợp nightly ⟹ *"D2 nhiều khả năng không phải chọn một trong ba, mà là tách orchestrator
  thành hai chế độ chạy trên cùng một contract"*.
- **Plan-as-artifact** `[R1 S4e3]`: nếu orchestrator sinh lại plan mỗi lần chạy thì hai lần chạy
  cùng một commit có thể chạy **hai tập test khác nhau** → PR pass vì worker đúng đã không được
  gọi, và **không có gì đỏ để ai đó phát hiện**.
- Kinh tế `[R3 S11]`: planning tốn nhiều nhất nhưng chỉ chạy khi spec đổi; routing bị **triệt
  tiêu hoàn toàn** nếu plan đã pin; aggregation/triage chỉ chạy khi có fail ⟹ **gate xanh = 0
  token LLM**. Con số ~15× của Anthropic `[R1 S4b]` **không áp dụng** cho kiến trúc này.
- Ngược lại `[R4 S15.3]`: bỏ plan-as-artifact thì câu trả lời **đảo ngược** — thêm một lời gọi
  LLM đọc spec + manifest vào **mọi** PR, và lúc đó 15× bắt đầu áp dụng. *Toàn bộ tính khả thi
  trong CI phụ thuộc đúng một quyết định thiết kế đó.*
- Exploration không đặt được ở gate: $16.31/app (DroidAgent) → $0.11 (LLM-Explorer), trần
  coverage ~65% activity `[R1 S3]` `[R2 S5-5]`.

**Alternatives rejected.**

| Bỏ | Lý do **kỹ thuật** |
|---|---|
| **Re-plan mỗi PR** (orchestrator "thông minh") | Mất reproducibility ở *coverage* — dạng vi phạm N2 im lặng hơn: gate xanh vì worker đúng không được gọi `[R1 S4e3]`. Cộng: kéo ~15× token vào mọi PR `[R4 S15.3]` |
| **Một lane duy nhất** (cho worker tự hành chặn merge luôn) | Đưa thẳng phương sai LLM vào gate. *"Vi phạm N2 dù report có dán nhãn 'LLM judgment' đi nữa, vì cái bị chặn là merge, không phải cái nhãn"* `[R1 S4e4]` |
| **Chỉ có gate lane** (bỏ discovery) | Mất mũi tên *promote* — tức mất đúng giá trị thật của nhóm (a): sinh ứng viên cho gate lane, đúng mô hình TestGen-LLM `[R1 S4e4]` |
| **Dựng Testkube + ReportPortal rồi viết lớp mỏng ở trên** | ✅ **đúng về kiến trúc dài hạn**, ❌ cần Kubernetes + nhiều service Docker; team trên Windows với 2.5 ngày `[R4 S14.3]`. Đây là *lộ trình*, không phải *sprint* |

**Consequences — ta MẤT gì.**
- **Plan mục (V3)** `[R4 S15.3/V3]`: plan-as-artifact chữa non-determinism nhưng **đẻ ra**
  staleness. Ba tháng sau, app có 5 endpoint mới không nằm trong plan; gate vẫn xanh, vẫn tái
  lập, và **vẫn vô nghĩa**. Đây là **cái giá của giải pháp**, không phải lỗi người thực hiện.
  ⟹ job định kỳ chạy planner + xuất diff + mở PR là **bắt buộc**, không phải tuỳ chọn.
- Mất khả năng phản ứng với thứ chưa có trong plan ở thời điểm PR.
- Discovery lane có số phận mặc định là **nghĩa địa (V4)**: 40 finding/tuần, không ai triage,
  một tháng sau 160 finding và người ta tắt nightly `[R4 V4]`.

**Revisit trigger.**
1. Tần suất chạy `plan-gen` vượt ~1 lần/tuần → plan-as-artifact đang chống lại team, không giúp
   team; xem lại ranh giới giữa plan tĩnh và selection động.
2. Team lên Kubernetes → chuyển execution sang Testkube, giữ lại đúng lớp verdict provenance
   `[R4 S15.5 hạng 2]`.
3. Một tuần không ai triage discovery → **tự động tắt discovery và báo**; thà tắt công khai còn
   hơn tích nợ `[R4 V4]`.

---

## D3 — Contract ba mảnh: worker manifest + task spec + result. `verdict_source` ở cấp từng finding

**Decision.** Hợp đồng gồm ba mảnh — `workers/<name>.yaml` (manifest), `task_spec.json`,
`result.json`. Orchestrator địa chỉ hoá **`capability`**, không địa chỉ hoá tên worker.
`oracle` do **orchestrator khai**, worker không tự chọn. `expected_result_kind` khai **trước**
khi chạy. `verdict_source` nằm ở **từng kết quả và từng finding**.

**Context.**
- Ba mảnh, không phải hai `[R1 S4e3]`: *"không có manifest thì orchestrator buộc phải biết tên
  worker, và N5 chết"*.
- `capability: "http.load"` chứ không phải `worker: "k6"` — thay k6 bằng Locust là đổi một dòng
  trong registry, không đụng plan `[R4 S12 nguyên lý 1]`.
- `oracle` do orchestrator khai = **cưỡng chế N1 bằng cấu trúc thay vì bằng quy ước**. Worker
  không có quyền quyết định "tôi sẽ tự chấm bằng LLM" `[R4 S12 nguyên lý 2]`.
- `expected_result_kind` khai trước: worker discovery trả verdict chặn gate thì **adapter từ chối
  tại tầng validate schema** — *non-determinism bị chặn ở cửa, không phải bị chặn bằng thiện chí*
  `[R4 S12 nguyên lý 3]`.
- **Bốn** trạng thái, không phải hai: `pass | fail | error | skipped`. *Gộp `error` vào `fail` là
  cách nhanh nhất giết niềm tin vào gate* `[R3 S11(3)]` `[R4 S12b]`.
- `verdict_source` ở cấp finding là **sửa đổi bắt buộc phát hiện nhờ verify**, không phải nhờ suy
  luận: DeepEval chứng minh một worker sinh cả hai loại `[R3 9.6]`.
- Không tool nào trong 14 ứng viên của RUN 3 phát ra `verdict_source`; JUnit XML là mẫu số chung
  nhưng **quá nghèo** `[R4 S14.1]`.

**Alternatives rejected.**

| Bỏ | Lý do **kỹ thuật** |
|---|---|
| **JUnit XML làm contract** | Về lý thuyết nhét được `verdict_source` vào `<properties>`, nhưng **không consumer chuẩn nào (CI UI, dashboard) đọc và gate trên trường tuỳ biến đó** `[CHƯA VERIFY — R4 double-check #1]` ⟹ trên thực tế một verdict LLM và một assert tất định vào JUnit XML là **không phân biệt được nữa**. *Đó chính là chỗ N2 chết trong mọi hệ hiện có* `[R4 S14.2]` |
| **`verdict_kind` ở worker manifest** | Sai về thực tế: DeepEval là **một** worker sinh **hai** loại `[R3 9.6]`. Manifest vẫn khai `verdict_sources` nhưng chỉ để routing/validate (mục 0.2 R2) |
| **Verdict nằm trong exit code của adapter** | Exit code chỉ có **một chiều thông tin** còn ta cần **bốn trạng thái** `[R4 S12c]`. Adapter exit 0 nếu **adapter** chạy được, kể cả khi test fail |
| **Cho LLM đọc output rác của worker và suy ra ý nghĩa** (tầng 4 của S11) | Chi phí cao **và** đưa phi tất định vào **đúng chỗ nguy hiểm nhất**. *Orchestrator không tự phục hồi bằng trí thông minh, nó tự phục hồi bằng schema và bằng ba trạng thái* `[R3 S11(3)]` |

**Consequences — ta MẤT gì.**
- **Adapter được phép mất thông tin.** Mọi thứ không vừa schema sẽ bị vứt. Đó là chủ đích
  (*"được phép mất thông tin, KHÔNG được phép bịa thông tin"* `[R4 S12c]`) nhưng cái giá thật là:
  output giàu của tool (HTML report của Midscene, cấu trúc metric lồng nhau của DeepEval) bị ép
  phẳng.
- **Thêm `verdict_source` thứ tư = sửa lõi + sửa quy tắc gộp** `[R4 S12d]`. Từ vựng cố tình chỉ
  có ba giá trị; đây là chi phí trả trước cho tính bất biến.
- `metrics` dạng phẳng **nhiều khả năng sẽ phải đổi khi gặp DeepEval** `[R4 chưa chắc #3]` —
  thiết kế trên giấy luôn đẹp hơn thiết kế đã va vào tool thật.
- Rủi ro V1 (xem Mục 9) là hệ quả trực tiếp: contract càng chặt, cám dỗ thêm `if worker == ...`
  càng lớn ở phút gấp gáp.

**Revisit trigger.**
1. Xuất hiện **trường chỉ-dành-cho-một-worker** trong schema → *"đó là tín hiệu contract đang
   hỏng"* `[R4 S12a]`. Dừng 15 phút, cả 3 quyết định.
2. Cần `verdict_source` thứ tư (vd `statistical` từ S7 cấu phần #7) → mở lại D3 **và** D5 cùng
   lúc, không sửa lẻ.
3. Đi lên Testkube/ReportPortal → phải ánh xạ registry ↔ Testkube CRD và report ↔ ReportPortal
   `[R4 S14.3]`.

---

## D4 — Ranh giới tự hành / tất định: worker tự hành được LÁI, không được PHÁN

**Decision.** Worker nhóm (a) chỉ được **lái** và **mô tả**; assert cuối luôn là câu lệnh tất
định do task spec khai. `gating: true` **về mặt schema** chỉ tồn tại được khi
`verdict_source = deterministic_assert`.

**Context.**
- E2E là chỗ nguy hiểm nhất: agent **vừa thực hiện vừa tự chấm** → self-assessment. *Nếu agent
  hiểu sai mục tiêu, nó sai ở cả hai đầu và hai cái sai **che nhau*** `[R2 S5-2]`.
- Ràng buộc `allOf` trong `result.json` `[R5 P2.4]` — *"hai ràng buộc này là toàn bộ N2 viết
  thành code… không ai có thể vi phạm N2 mà vẫn validate qua, kể cả khi quên"*.
- Ngay ở discovery lane, tín hiệu **phát hiện** vẫn tất định: `oracle.kind: implicit_signals` =
  console error, 5xx, unhandled rejection, navigation stuck. LLM chỉ được
  `llm_observations_allowed` — được **mô tả**, không được **phán quyết** `[R4 S12a ví dụ 2]`.
- Năm lớp phòng thủ chống "worker tự hành báo pass sai" `[R4 S15.4]`, lớp 1 là **triệt để**:
  nếu nó không phán quyết thì nó không thể báo pass sai. Lớp 3 — **canary task chắc chắn phải
  fail** — là *"đóng góp thực tế nhất và chưa xuất hiện trong bất kỳ prior art nào"*.
- Blast radius `[R3 S11(4)]`: worker tự hành **không được cấp credential ghi**; chỉ chạy trên môi
  trường test, **không bao giờ trỏ vào production**.

**Alternatives rejected.**

| Bỏ | Lý do **kỹ thuật** |
|---|---|
| **Cho `aiAssert` của Midscene giữ verdict** | `A5 = 2` ⟹ luật lane cấm `[R1 S2]`. Và Midscene **dùng chung được với `expect()` của Playwright** `[R3 9.1]` ⟹ có sẵn đường tách, không phải hy sinh gì |
| **Tin worker vì `confidence` cao** | Trục phân loại là **reversibility, không phải confidence** `[R1 S2 A8, Day27]` — một phán quyết 0.99 vẫn phải dừng nếu hậu quả không đảo được |
| **Dựa vào code review / quy ước để giữ N1** | *"Một prompt rule không phải là một control"* (`Day16`, qua `[R2 S7.5]`). Cưỡng chế bằng `allOf` trong schema mạnh hơn mọi tài liệu `[R5 (b)]` |
| **Chỉ dùng screencast + người xem định kỳ để bắt worker báo sai** | Lớp 5/5, *"thấp, không mở rộng được"* `[R4 S15.4]` |

**Consequences — ta MẤT gì.**
- Mất khả năng để agent bắt lỗi mà **không** oracle tất định nào mô tả được. Finding kiểu
  "thông báo lỗi khó hiểu với người dùng cuối" mãi mãi là advisory, không bao giờ chặn được gì —
  trừ khi có người biến nó thành assert.
- Mất tốc độ: mỗi finding giá trị phải đi qua vòng *promote* (điều kiện: `deterministic_assert`
  **và** tái lập ≥3/3 **và** có `suggested_assertion` `[R4 S13.7 #2]`).
- Chi phí canary: mỗi run discovery tốn thêm một task luôn chạy.

**Revisit trigger.**
1. Canary **báo pass** → worker hỏng. Dừng lane đó, không dừng hệ `[R4 S15.4 lớp 3]`.
2. Bất đồng `llm_judgment` vs `deterministic_assert` lặp **≥3 run** → mở task review; nếu người
   kết luận LLM đúng thì **viết assert tất định mới**, không nới quyền cho LLM `[R4 S13.4]`.
3. Heal rate (nếu sau này bật G4) tăng → *mùi hỏng*, không phải thành tích `[R1 S1]`.

---

## D5 — Oracle strategy: luôn dùng bậc cao nhất còn áp dụng được; LLM-judge là bậc 6

**Decision.** Mỗi task khai `oracle.kind`. Chọn oracle theo **thang 7 bậc**, luôn lấy bậc **cao
nhất còn áp dụng được**. Bậc 1–5 = "test", gate được. Bậc 6 (LLM-judge) = "eval", chỉ gate được
ở dạng **delta so baseline đã pin**. Bậc 7 (human) không gate.

**Context.**

| Bậc | Oracle | Gate? |
|---|---|---|
| 1 | **Structural assert** — parse được, đúng schema, đủ trường | ✅ |
| 2 | **Property/invariant** — không rò secret, không gọi tool cấm, luôn có citation | ✅ |
| 3 | **Behavioral assert trên tool call** — đúng tool, đúng tham số, đúng thứ tự | ✅ |
| 4 | **Retrieval metric có nhãn** — recall@k / precision@k trên golden set | ✅ |
| 5 | **Reference-based text metric** — khi có đáp án đóng | ✅ |
| 6 | **LLM-as-judge** | ⚠️ chỉ theo delta |
| 7 | **Human review** | ❌ |

`[R2 S7.1]` — và nguyên tắc rút ra: *phần lớn thứ người ta định chấm bằng LLM ở bậc 6 thật ra
chấm được ở bậc 1–3. "Chatbot trả lời có lịch sự không" thì cần judge; "agent có gọi `refund()`
khi user chưa xác nhận không" là assert cứng — và cái thứ hai mới là thứ làm hỏng production.*

- Neo định lượng `[R1 S1]`: TestGen-LLM trên Instagram Reels/Stories — 75% build được, **57% pass
  ổn định**, 25% tăng coverage. ⟹ **43% output bị vứt, và cái làm nó dùng được không phải LLM mà
  là bộ filter tất định.** Oracle strategy mặc định = *LLM sinh ứng viên + filter tất định quyết
  định cái nào sống*, **không** phải *LLM-as-judge* `[R1 (b)]`.
- Bằng chứng chống LLM-judge `[R2 S7.4]`: position bias đo trên **15 judge · 22 task · >150.000
  instance**, và bias **mạnh nhất đúng lúc hai lời giải gần nhau về chất lượng** — tức **đúng
  vùng của regression testing (A vs A')**. Cộng self-preference bias ⟹ **judge phải KHÁC model
  của hệ đang test**; adapter DeepEval **từ chối chạy** (`status: error`) nếu hai model trùng,
  không âm thầm chạy `[R2 S7.4 R6]`.
- API có **ba oracle tất định độc lập** (schema/contract · differential/replay · invariant) —
  mạnh nhất trong 7 surface, **không cần LLM ở bất kỳ tầng nào** `[R2 S5-3, S6]`.
- G3 (property/fuzz) là *"nhóm bị bỏ quên nhất và hợp với QC nhất"*: nó giải chính xác bài toán
  "không biết expected output" bằng invariant, trong khi thị trường bán G8 cho cùng bài toán đó
  với giá cao hơn và độ tin cậy thấp hơn `[R2 S8]`.

**Alternatives rejected.**

| Bỏ | Lý do **kỹ thuật** |
|---|---|
| **LLM-as-judge làm oracle chính cho AI feature** | Bậc 6/7. Với `summarize` của toy app, bậc 1 (JSON đúng schema) + bậc 2 (summary không rỗng, ngắn hơn body) đã phủ được phần quan trọng `[R5 2.2]` |
| **Ngưỡng tuyệt đối trên điểm judge** | Điểm của judge **không có đơn vị ổn định qua thời gian** `[R2 S7.4]` |
| **Faithfulness một mình cho RAG** | Reference-free ⟹ **mù trước retrieval sai**: điểm 0.95 hoàn toàn tương thích với câu trả lời sai vì tài liệu đã hết hiệu lực. Phải có cả context recall/precision `[R2 S7.7]` |
| **Golden set tự sinh bằng LLM** | *Đang đo model bằng chính model.* Bắt đầu từ **vài chục case có nhãn đúng** hơn vài nghìn case tự sinh `[R2 S7.3]` |

**Consequences — ta MẤT gì.**
- Mất khả năng gate trên **chất lượng cảm nhận**. Nếu bản tóm tắt tệ đi mà vẫn đúng schema, đúng
  độ dài, đúng JSON — gate **xanh**. Đây là giới hạn thật, phải nói trong báo cáo.
- Kéo theo **7 cấu phần** mà QC truyền thống không cần `[R2 S7 kết luận]`: SUT identity đa chiều ·
  dataset store · **baseline store** · judge calibration · sampling policy · cost budget như
  assert hạng nhất · verdict provenance. Ít nhất 2 trong 7 chắc chắn `[KHÔNG KỊP SPRINT NÀY]` —
  trong PoC: judge calibration và sampling policy không có.
- Không có baseline store ⟹ **không gate được theo delta** ⟹ trong PoC, cột `baseline` trong
  báo cáo mẫu `[R5 2.5]` là giá trị viết tay, phải nói rõ.

**Revisit trigger.**
1. Bất đồng LLM/deterministic lặp ≥3 lần → review; kết luận (a) LLM đúng ⟹ **viết assert tất
   định mới**, (b) LLM sai ⟹ chỉnh rubric/bỏ metric, (c) mơ hồ ⟹ giữ advisory `[R4 S13.4]`.
2. Có baseline store thật + tập hiệu chuẩn có nhãn human → mở lại việc gate theo delta.
3. Provider đổi model sau cùng một tên alias → **regression im lặng**: không có commit, không có
   PR, không có ai để đổ lỗi `[R2 S7.2]`. Đây là lý do `pin model snapshot id` **không phải tuỳ
   chọn**, và là trigger để dựng SUT identity đầy đủ.

---

# 3. COMPONENT ARCHITECTURE

## 3.1 Sơ đồ — kèm trust boundary

```
 TRIGGER ── PR webhook · nightly cron · comment "/qc run <capability>"
    │        (PoC: 1 lệnh CLI đóng vai trigger; 3 loại còn lại VẼ, không cài)
    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ SELECTION  (tất định — KHÔNG gọi LLM)                                    │
 │   đọc   : plan.yaml (đã commit)  +  git diff --name-only <base>..HEAD    │
 │   KHÔNG đọc: tiêu đề PR · mô tả PR · comment · NỘI DUNG diff             │
 │   ra    : selected = floor ∪ (impact ∩ plan.tasks) ∪ always_on           │
 └──────────────────────────────┬───────────────────────────────────────────┘
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ ORCHESTRATOR CORE   (~400 dòng · 0 lời gọi LLM)                          │
 │  preflight/probe → lọc registry → dựng DAG → spawn song song             │
 │  → validate result (JSON Schema) → gộp verdict (AND) → render report     │
 └───┬──────────────┬──────────────┬──────────────┬─────────────────────────┘
     │ task spec JSON qua stdin    ▲ result JSON qua stdout
     ▼              ▼              ▼              ▼
 ┌────────┐   ┌────────┐     ┌──────────┐   ┌────────────┐   ┌──────────┐
 │adapter │   │adapter │     │ adapter  │   │  adapter   │   │ adapter  │
 │schemat.│   │  k6    │     │ deepeval │   │  midscene  │   │mock/canary│
 └───┬────┘   └───┬────┘     └────┬─────┘   └─────┬──────┘   └────┬─────┘
     │            │               │               │               │
     │            │        ╔══════╪═══════════════╪══════╗        │
     │            │        ║  ⚠ TRUST BOUNDARY    │      ║        │
     │            │        ║  dữ liệu RỜI máy team│      ║        │
     │            │        ║  ↓                   ↓      ║        │
     │            │        ║ LLM judge       VLM provider║        │
     │            │        ║ (app_input +    (screenshot ║        │
     │            │        ║  app_output)     + DOM)     ║        │
     │            │        ╚═════════════════════════════╝        │
     ▼            ▼               ▼               ▼               ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ TOY APP  "noteboard"  — FastAPI 1 file ~150 dòng · SQLite in-memory     │
 │  REST /notes (OpenAPI tại /openapi.json) · trang web GET / ·           │
 │  AI feature POST /notes/{id}/summarize                                  │
 └────────────────────────────────────────────────────────────────────────┘

 ══ NGOÀI ĐƯỜNG CHẠY CI ═══════════════════════════════════════════════════
   plan-gen (LLM) : spec đổi → plan.draft.yaml → NGƯỜI duyệt DIFF → commit
                    ⚠ TRUST BOUNDARY: spec/acceptance criteria rời máy team
 ══ CHỈ KHI GATE ĐỎ ═══════════════════════════════════════════════════════
   failure-analysis (LLM hoặc ReportPortal) → tickets_draft[] → NGƯỜI bấm nộp
                    ⚠ TRUST BOUNDARY: log/trace rời máy team
 ══ ĐẦU RA ════════════════════════════════════════════════════════════════
   runs/r-NNNN/{report.md, report.json, results/t-*.json, evidence/ + sha256}
   exit code = verdict   ·   security finding → QUEUE RIÊNG, không lên board
   [GitHub Checks / Slack: VẼ, không cài]

 LANE:  ├─────────── GATE (chặn merge) ───────────┤   ├── DISCOVERY ──┤
        schemathesis      k6      deepeval*            midscene · canary
                                  * chỉ metric deterministic mới gating
```

**Ba đường egress, xếp theo mức nghiêm trọng** `[R3 S11(5)]`:
`G8 (DeepEval — gửi cả input lẫn output của app cho model chấm)` **>**
`G6 (Midscene — gửi screenshot/DOM, có thể chứa dữ liệu khách hàng trên màn hình)` **>**
`G5/plan-gen (gửi spec/mã nguồn)` **>** `Schemathesis, k6 (không gửi gì)`.
Mỗi worker **phải khai `data_egress` trong manifest** `[R4 S12d]` — khai **trước**, không khai sau.

## 3.2 Bảng component

| Component | Trách nhiệm | Input | Output | Công nghệ | Ai code |
|---|---|---|---|---|---|
| `core/registry.py` | Quét `workers/*.yaml`, chạy `version_probe`, lọc ứng viên | thư mục `workers/` | danh sách worker + trạng thái probe | Python, YAML | **A** |
| `core/runner.py` | Dựng DAG theo `depends_on`, spawn song song, timeout, cưỡng chế `budget` | `selected` tasks + registry | `results/t-*.json` | Python `subprocess` | **A** |
| `core/verdict.py` | `AND` trên `gating=true`; xử lý `error`/`skipped` | results[] | `gate_verdict` | Python thuần | **A** |
| `core/report.py` | Render **3 mục tách theo `verdict_source`** + mục SKIPPED/ERROR **ở đầu** + mục AUDIT | results[] | `report.md`, `report.json` | Python | **A** |
| `core/evidence.py` | Băm sha256, đường dẫn chuẩn `runs/<run_id>/<task_id>/` | file evidence | evidence[] có hash | hashlib | **A** |
| `oracle/threshold.py`, `oracle/signals.py` | **Bộ so sánh dùng chung.** Adapter chỉ *trích số*, so sánh nằm ở đây | metrics + `oracle.assertions` | pass/fail tất định | Python | **A** |
| `selection` | `floor ∪ (impact ∩ plan) ∪ always_on` từ `git diff --name-only` | plan.yaml + changed_paths | tập task id | Python + git | **A** |
| `plan-gen` *(ngoài CI)* | spec → `plan.draft.yaml` | `openapi.json` + acceptance criteria | plan draft cho người duyệt | LLM | **A** *(cắt đầu tiên sau Selection)* |
| `adapters/_base.py` | stdin→stdout, validate, bắt lỗi → `error`, đo wallclock | Task Spec JSON | Result JSON | Python | **A** |
| `adapters/midscene_adapter.py` | Parse `--summary` JSON → `findings[]`; **tách tín hiệu ngầm khỏi nhận xét LLM** | Task Spec | Result | Python + `@midscene/cli` | **B** |
| `adapters/mock_adapter.py`, canary | Worker giả (slot 1) · task chắc chắn phải fail | Task Spec | Result | Python | **B** |
| Toy app **frontend** | Trang `/`: form + list + nút Xoá **có lỗi cài sẵn** | — | HTML tĩnh | HTML/JS | **B** |
| `adapters/schemathesis_adapter.py` | **Adapter thật đầu tiên — là mẫu cho các adapter sau** | Task Spec | Result | Python + `st` | **C** |
| `adapters/k6_adapter.py` | Map summary → metrics; phân biệt `fail` vs `error` | Task Spec | Result | Python + k6 binary | **C** |
| `adapters/deepeval_adapter.py` | **3 metric tất định gating + 1 G-Eval advisory** trong **một** result | Task Spec | Result | Python + pytest | **C** |
| Toy app **backend** | 4 REST endpoint + `/openapi.json` + `/summarize` + lỗi cài sẵn | — | HTTP | FastAPI, SQLite in-memory | **C** |

**Nguyên tắc sở hữu** `[R5 P3]`: (1) ai sở hữu một lớp thì viết lớp đó **và** viết phần báo cáo
nói về lớp đó; (2) **contract có đúng một chủ là A** — mọi adapter qua A review trước khi merge,
lý do kỹ thuật là N4/N5 là chỗ kiến trúc sống hoặc chết; (3) A là **đường găng** — nếu cuối slot
2 skeleton chưa chạy, A **dừng nhận thêm việc**, B/C đỡ `core/` theo README của A.

**Checklist review adapter của A — 5 câu có/không** `[R5 P3]`:
1. Result validate qua đúng `result.json`, **không trường riêng**?
2. Có `error` khi worker hỏng, **không gộp vào `fail`**?
3. Evidence có **sha256**?
4. `verdict_source` / `gating` đúng ràng buộc `allOf`?
5. **Không có `if worker == ...` trong `core/`**?

---

# 4. QUALITY RESULT SCHEMA (QRS) — ĐẶC TẢ ĐẦY ĐỦ

Đây là contract lõi. Ba file (`schemas/task_spec.json`, `schemas/result.json`,
`workers/_template.yaml`) được commit **trong 90 phút đầu của slot 1, trước khi bất kỳ ai viết
adapter**, rồi **đóng băng** — đổi phải có sự đồng ý của cả ba và phải sửa cùng lúc mọi adapter
`[R5 2.4]`.

## 4.1 JSON Schema — `schemas/result.json`

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://qc-agent.local/schemas/result.json",
  "title": "Quality Result Schema v1.0.0",
  "type": "object",
  "required": ["task_id","run_id","worker","status","verdict","evidence","cost"],
  "additionalProperties": false,
  "properties": {

    "task_id": {"type":"string"},                    // trỏ về task_spec.task_id
    "run_id":  {"type":"string"},                    // trỏ về run hiện tại

    // worker_id = worker.name. KHÔNG có trường "worker_id" riêng.
    "worker": {
      "type":"object",
      "required":["name","version","adapter_version"],
      "additionalProperties": false,
      "properties":{
        "name":            {"type":"string"},                 // "k6", "midscene-cli"
        "version":         {"type":["string","null"]},        // null nếu probe không lấy được
        "adapter_version": {"type":"string"}                  // semver của adapter
      }
    },

    // BỐN trạng thái, không phải hai.
    //   pass    = worker chạy xong, oracle thoả
    //   fail    = oracle KHÔNG thoả              → KHÔNG BAO GIỜ retry
    //   error   = worker hỏng / timeout / output không parse được → retry ≤1
    //   skipped = preflight probe thất bại (thiếu binary/env)     → gate VÀNG
    "status": {"enum":["pass","fail","error","skipped"]},

    "verdict": {
      "type":"object",
      "required":["value","verdict_source","gating"],
      "additionalProperties": false,
      "properties":{
        "value":          {"enum":["pass","fail","non_gating"]},
        "verdict_source": {"enum":["deterministic_assert","llm_judgment","heuristic"]},
        "gating":         {"type":"boolean"},
        "confidence":     {"type":["number","null"], "minimum":0, "maximum":1, "default": null},
        "rationale":      {"type":["string","null"], "default": null}
      },
      "allOf":[
        { "if":   {"properties":{"verdict_source":{"const":"deterministic_assert"}}},
          "then": {"properties":{"confidence":{"const":null}}} },
        { "if":   {"properties":{"gating":{"const":true}}},
          "then": {"properties":{"verdict_source":{"const":"deterministic_assert"}}} }
      ]
    },

    "findings": {
      "type":"array", "default": [],
      "items":{
        "type":"object",
        "required":["finding_id","title","detected_by","verdict_source"],
        "additionalProperties": false,
        "properties":{
          "finding_id":   {"type":"string"},
          "title":        {"type":"string"},
          // "implicit_signal:<tên>" | "llm_observation" | "metric:<tên>"
          "detected_by":  {"type":"string"},
          "verdict_source":{"enum":["deterministic_assert","llm_judgment","heuristic"]},
          "confidence":   {"type":["number","null"], "minimum":0, "maximum":1, "default": null},
          "rationale":    {"type":["string","null"], "default": null},
          "severity_hint":{"enum":["low","medium","high",null], "default": null},
          "evidence":     {"type":"array","default":[],
                           "items":{"$ref":"#/$defs/evidence_item"}},
          "promote_candidate":{"type":["object","null"], "default": null,
            "properties":{
              "suggested_capability": {"type":"string"},
              "repro_steps":          {"type":"array","items":{"type":"string"}},
              "suggested_assertion":  {"type":"string"}
            }}
        }
      }
    },

    "metrics": {"type":"object", "default": {}},     // PHẲNG: {"http_req_duration.p95": 214.7}

    "evidence": {"type":"array","items":{"$ref":"#/$defs/evidence_item"}},

    "cost": {
      "type":"object","required":["wallclock_s"],
      "additionalProperties": false,
      "properties":{
        "wallclock_s":{"type":"number"},                       // duration — LUÔN có
        "tokens":     {"type":["integer","null"], "default": null},
        "usd":        {"type":["number","null"],  "default": null}
      }
    },

    "sut_identity_ref":{"type":["string","null"], "default": null},
    "determinism": {
      "type":"object", "default": {},
      "properties":{
        "seed":       {"type":["integer","null"]},
        "replay_cmd": {"type":["string","null"]}    // chạy lại worker KHÔNG cần orchestrator
      }
    },
    "adapter_notes":{"type":"array","items":{"type":"string"}, "default": []}
  },

  "$defs": {
    "evidence_item": {
      "type":"object",
      "required":["kind","uri","sha256"],
      "additionalProperties": false,
      "properties":{
        // Từ vựng CHỐT — dùng chung với task_spec.evidence_required  [SUY RA, mục 0.3 G2]
        "kind":  {"enum":["raw_output","stdout","metrics","screenshot","trace"]},
        "uri":   {"type":"string"},      // đường dẫn tương đối: runs/<run_id>/<task_id>/...
        "sha256":{"type":"string","pattern":"^[a-f0-9]{64}$"}
      }
    }
  }
}
```

### Bảng trường — đọc nhanh khi code

| Trường | Kiểu | Bắt buộc | Default | Ghi chú cưỡng chế |
|---|---|---|---|---|
| `task_id`, `run_id` | string | ✅ | — | Truy vết |
| `worker.name` | string | ✅ | — | **Đây là `worker_id`** |
| `worker.version` | string\|null | ✅ | — | `null` nếu `version_probe` không trả về |
| `worker.adapter_version` | string | ✅ | — | Vào **run signature** ⟹ đổi adapter = không so với baseline cũ |
| `status` | enum(4) | ✅ | — | `error ≠ fail`. Trộn hai thứ = giết niềm tin vào gate |
| `verdict.value` | enum(3) | ✅ | — | `non_gating` dành cho discovery lane |
| `verdict.verdict_source` | enum(3) | ✅ | — | **Từ vựng cố tình chỉ 3 giá trị**; thêm giá trị thứ 4 = sửa lõi + sửa quy tắc gộp |
| `verdict.gating` | bool | ✅ | — | `true` ⟹ **bắt buộc** `deterministic_assert` (ràng buộc `allOf` #2) |
| `verdict.confidence` | number\|null | ❌ | `null` | `deterministic_assert` ⟹ **bắt buộc `null`** (`allOf` #1). Một con số tin cậy gắn vào assert tất định là vô nghĩa |
| `findings[]` | array | ❌ | `[]` | Discovery lane sống ở đây |
| `findings[].verdict_source` | enum(3) | ✅ (nếu có finding) | — | **Ở CẤP TỪNG FINDING** — một result chứa được cả hai loại |
| `findings[].confidence` | number\|null | ❌ | `null` | `llm_judgment` ⟹ **adapter** ép phải có (schema không ép — mục 0.3 G4) |
| `findings[].promote_candidate` | object\|null | ❌ | `null` | Đường sang gate lane |
| `metrics` | object phẳng | ❌ | `{}` | `[CHƯA VERIFY]` — có thể phải đổi khi gặp DeepEval |
| `evidence[]` | array | ✅ | — | **Không có evidence thì không có verdict**: thiếu evidence bắt buộc ⟹ `error` |
| `evidence[].sha256` | hex64 | ✅ | — | Không hash thì không audit |
| `cost.wallclock_s` | number | ✅ | — | **duration** |
| `cost.tokens`,`cost.usd` | \|null | ❌ | `null` | Worker không báo ⟹ `null`. **Cấm ước lượng** |
| `sut_identity_ref` | string\|null | ❌ | `null` | Trỏ tới bản ghi SUT identity đa chiều |
| `determinism.replay_cmd` | string\|null | ❌ | `null` | Chạy lại worker **độc lập**, không cần orchestrator |

## 4.2 Ba ví dụ JSON THẬT

> **"Thật" nghĩa là gì ở đây:** cả 3 ví dụ **validate qua schema 4.1** và **dựng từ ví dụ có sẵn
> trong RUN 4/RUN 5** (k6/Midscene `[R4 S12b]`, báo cáo mẫu t-003 `[R5 2.5]`: G-Eval 0.71 /
> baseline 0.78 / delta −0.07 / confidence 0.64). Các con số phụ (số bước, token, USD, wallclock,
> tên file evidence) và **toàn bộ chuỗi sha256** là **giá trị minh hoạ do file này đặt ra — không
> phải kết quả đo**. RUN 1–5 chưa chạy dòng code nào `[R4 (a)#3]`. Đừng trích chúng lên slide như
> số liệu.

### Ví dụ 1 — E2E **PASS** (Midscene, discovery lane, không tìm thấy gì)

```json
{
  "task_id": "t-101",
  "run_id": "r-0090",
  "worker": { "name": "midscene-cli", "version": "1.7.2", "adapter_version": "0.1.0" },
  "status": "pass",
  "verdict": {
    "value": "non_gating",
    "verdict_source": "heuristic",
    "gating": false,
    "confidence": null,
    "rationale": "Discovery lane: worker hoàn thành 15/15 bước, không phát hiện tín hiệu ngầm nào."
  },
  "findings": [],
  "metrics": { "steps_executed": 15, "unique_states_visited": 11 },
  "evidence": [
    { "kind": "raw_output", "uri": "runs/r-0090/t-101/midscene-summary.json", "sha256": "7b1e9c4a2d5f8036c1ab47e9d0f2358a6c4b19de7f03a5218cbd6e4079f31a2c" },
    { "kind": "trace",      "uri": "runs/r-0090/t-101/trace.zip",             "sha256": "2f4d81ac93e07b5612cd0fa3487be91d6503ac7218fe4d09b3a5c61780ef2d4b" }
  ],
  "cost": { "wallclock_s": 512, "tokens": 96210, "usd": 0.26 },
  "sut_identity_ref": "sut-9f2c",
  "determinism": { "seed": null, "replay_cmd": null },
  "adapter_notes": ["Parse từ --summary JSON, KHÔNG parse HTML report"]
}
```

> **Đọc kỹ `status: "pass"` ở đây:** nó nghĩa là **worker chạy xong**, **không** phải "sản phẩm
> tốt". Với `gating: false`, `status` **không tham gia** verdict tổng. *Đây là chỗ dễ hiểu sai
> nhất trong cả schema và phải ghi thành comment trong code* `[R4 S12b]`.

### Ví dụ 2 — E2E **FAIL** (canary task: worker được giao việc chắc chắn phải fail)

Canary = lớp phòng thủ #3 chống "worker tự hành làm sai nhưng báo pass" `[R4 S15.4]`. Task yêu
cầu bấm nút **"Thanh toán"** — nút **không tồn tại** trên toy app `[R5 2.2]`.

```json
{
  "task_id": "t-canary-01",
  "run_id": "r-0090",
  "worker": { "name": "midscene-cli", "version": "1.7.2", "adapter_version": "0.1.0" },
  "status": "fail",
  "verdict": {
    "value": "fail",
    "verdict_source": "deterministic_assert",
    "gating": false,
    "confidence": null,
    "rationale": "Canary: element mục tiêu không tồn tại sau 15 bước. Worker báo fail ĐÚNG như kỳ vọng."
  },
  "findings": [
    {
      "finding_id": "f-canary-1",
      "title": "Không tìm thấy nút 'Thanh toán' trên trang /",
      "detected_by": "implicit_signal:element_not_found",
      "verdict_source": "deterministic_assert",
      "confidence": null,
      "rationale": null,
      "severity_hint": null,
      "evidence": [
        { "kind": "screenshot", "uri": "runs/r-0090/t-canary-01/step-15.png", "sha256": "a93c7e1b05d42f6890ac3e571bd204f8963ca7e015bd48f2703c9a61de85427f" }
      ],
      "promote_candidate": null
    }
  ],
  "metrics": { "steps_executed": 15, "unique_states_visited": 3 },
  "evidence": [
    { "kind": "raw_output", "uri": "runs/r-0090/t-canary-01/midscene-summary.json", "sha256": "c1d05fa72be349608d2fa71e4b9c3850ad7e62f194bc0d3a8571ef26049b3cda" },
    { "kind": "stdout",     "uri": "runs/r-0090/t-canary-01/stdout.log",            "sha256": "5e2ab904c7d316f8402be9d1750ca36f8b41ed79025ca6f3bd819e40c7a52d16" }
  ],
  "cost": { "wallclock_s": 143, "tokens": 21870, "usd": 0.06 },
  "sut_identity_ref": "sut-9f2c",
  "determinism": { "seed": null, "replay_cmd": null },
  "adapter_notes": ["Canary task: kỳ vọng status=fail. status=pass ⟹ WORKER HỎNG, không phải app tốt."]
}
```

**Ba điều dev phải đọc ra từ ví dụ này:**
1. `gating: false` **dù** `value: "fail"` — canary ở discovery lane, nó **không** chặn merge. Nó
   kiểm **chính worker**, không kiểm app.
2. Vì `gating: false`, canary **không** vào công thức gộp. Orchestrator so kết quả canary với
   **kỳ vọng khai trong plan** và báo động riêng nếu lệch `[R4 S15.4 lớp 3]`.
3. `verdict_source` vẫn là `deterministic_assert` — "element không tồn tại" là **sự kiện đo
   được**, không phải LLM phán.

### Ví dụ 3 — **AI EVAL** (DeepEval: một result, hai `verdict_source`)

Đây là ví dụ quan trọng nhất trong ba — nó là chỗ contract trả công `[R3 9.6]` `[R4 S12b]`.

```json
{
  "task_id": "t-003",
  "run_id": "r-0088",
  "worker": { "name": "deepeval", "version": null, "adapter_version": "0.1.0" },
  "status": "fail",
  "verdict": {
    "value": "fail",
    "verdict_source": "deterministic_assert",
    "gating": true,
    "confidence": null,
    "rationale": null
  },
  "findings": [
    {
      "finding_id": "f-10",
      "title": "JSONCorrectness: 5/5 case đúng schema",
      "detected_by": "metric:JSONCorrectness",
      "verdict_source": "deterministic_assert",
      "confidence": null,
      "rationale": null,
      "severity_hint": null,
      "evidence": [],
      "promote_candidate": null
    },
    {
      "finding_id": "f-11",
      "title": "summary_shorter_than_body: 4/5 case đạt — case #3 summary dài hơn body",
      "detected_by": "metric:summary_shorter_than_body",
      "verdict_source": "deterministic_assert",
      "confidence": null,
      "rationale": null,
      "severity_hint": "high",
      "evidence": [
        { "kind": "raw_output", "uri": "runs/r-0088/t-003/case-3.json", "sha256": "d740be21c93f5a0861ed4c7b92038fa5716ce0db43829af150c6be73048d921e" }
      ],
      "promote_candidate": null
    },
    {
      "finding_id": "f-12",
      "title": "G-Eval 'summary có giữ ý chính không': 0.71 (baseline 0.78, delta -0.07)",
      "detected_by": "metric:GEval",
      "verdict_source": "llm_judgment",
      "confidence": 0.64,
      "rationale": "Judge cho rằng case #2 và #5 bỏ mất ý về deadline. Ngưỡng cảnh báo delta = -0.10, chưa đạt.",
      "severity_hint": "low",
      "evidence": [
        { "kind": "raw_output", "uri": "runs/r-0088/t-003/geval-detail.json", "sha256": "9ab3c05e178d24f6b0e91dc7350af426185ed9c0b7f342a8619de507cb42130f" }
      ],
      "promote_candidate": null
    }
  ],
  "metrics": {
    "JSONCorrectness.pass_rate": 1.0,
    "summary_not_empty.pass_rate": 1.0,
    "summary_shorter_than_body.pass_rate": 0.8,
    "GEval.score": 0.71
  },
  "evidence": [
    { "kind": "raw_output", "uri": "runs/r-0088/t-003/deepeval-results.json", "sha256": "31f8ae06c24b7d95013ecaf6289b5710de4c3a8f60219bd7ce4530a81f962d0b" },
    { "kind": "stdout",     "uri": "runs/r-0088/t-003/pytest-stdout.log",     "sha256": "6c0e93b18af527d40312ba97ed6f85410c9d72be3105fa84d7be209163cf4a8d" }
  ],
  "cost": { "wallclock_s": 48, "tokens": 12440, "usd": 0.04 },
  "sut_identity_ref": "sut-9f2c",
  "determinism": { "seed": null, "replay_cmd": "deepeval test run tests/eval/test_summarize.py" },
  "adapter_notes": [
    "version=null: RUN 1-5 không ghi phiên bản DeepEval; điền từ version_probe lúc chạy thật.",
    "judge_config.model != SUT model — đã kiểm, không trùng.",
    "GEval là advisory: KHÔNG tham gia verdict.value."
  ]
}
```

**Bốn luật mà ví dụ này minh hoạ** (đọc kỹ trước khi viết `deepeval_adapter`):
1. `verdict` của result = **AND của các metric tất định gating** (`JSONCorrectness`,
   `summary_not_empty`, `summary_shorter_than_body`). Ở đây một metric fail ⟹ `value: "fail"`,
   `gating: true`.
2. Metric advisory (`G-Eval`) đi vào `findings[]` với `llm_judgment` + `confidence`. Nó
   **không có** trường `gating` và **không thể** ảnh hưởng verdict — xem **[SUY RA] G3** ở mục
   0.3: công thức gộp chỉ đọc `r.verdict.gating`.
3. Đổi điểm G-Eval thành fail ⟹ **verdict tổng không đổi**. Đó chính là tiêu chí PoC #3
   `[R5 2.3]`, kiểm được bằng tay trong 10 giây.
4. `judge_config`: **model chấm phải KHÁC model của hệ đang test**; trùng nhau thì adapter trả
   `status: "error"`, **không âm thầm chạy** `[R2 S7.4 R6]`.

## 4.3 Quy tắc versioning — schema đổi thì adapter cũ có vỡ không

`adapter_version` nằm trong **run signature** = `sha256(plan_id ‖ sut_identity ‖ {worker,
version, adapter_version} ‖ seeds)` `[R4 S13.5]`. Vì vậy **mọi thay đổi adapter đều cố ý phá
so sánh với baseline cũ** — đó là tính năng, không phải phiền toái.

| Loại thay đổi | Adapter cũ có vỡ? | Xử lý | Nguồn |
|---|---|---|---|
| **Thêm trường TUỲ CHỌN** (vd `task_spec.risk_tags`) | ❌ Không | Cứ thêm. `result.json` **không đổi** ⟹ mọi adapter đã viết vẫn validate, N4/N5 giữ nguyên | `[R4 S12a R4]` `[R5 2.4 R4]` |
| **Thêm khối vào `plan.yaml`** (vd `selection`) hoặc vào `report.json` (vd `tickets_draft[]`) | ❌ Không | `plan.yaml`/`report.json` là file **của orchestrator**, **không thuộc contract worker** | `[R5 2.4 R4]` |
| **Thêm `oracle.kind` mới** | ❌ Không | Thêm **file mới** vào `oracle/`. Có đụng thư viện dùng chung, **không** đụng logic điều phối | `[R4 S12d]` |
| **Thêm `capability` mới** | ❌ Không | Thêm YAML + adapter + 1 JSON Schema cho `inputs` + 1 mục vào từ vựng `capability` (từ vựng là **file dữ liệu**) | `[R4 S12d]` |
| **Thêm giá trị `verdict_source` thứ tư** | ⚠️ **CÓ — vỡ ở tầng gộp** | **Sửa lõi + sửa quy tắc gộp.** *Đây là lý do từ vựng này cố tình chỉ có ba giá trị.* Phải: (1) sửa `core/verdict.py`, (2) sửa `core/report.py` (mục thứ 4 trong report), (3) sửa `allOf`, (4) bump **major** của schema, (5) bump `adapter_version` của **mọi** adapter | `[R4 S12d]` |
| **Thêm trường BẮT BUỘC vào `result.json`** | ⚠️ **CÓ — vỡ hết** | Mọi adapter fail validate cùng lúc → mọi task thành `error` → gate đỏ (trung thực, nhưng đau). Phải sửa **cùng lúc** mọi adapter. `[R5 2.4]`: sau slot 1 thì phải có **sự đồng ý của cả ba** | `[R5 2.4]` |
| **Siết ràng buộc `allOf`** | ⚠️ Vỡ **có chọn lọc** | Chỉ adapter đang vi phạm mới vỡ — và nó **đang vi phạm N2**, nên vỡ là đúng | **[SUY RA]** từ `[R5 P2.4]` |

**Luật vận hành:** schema mang semver ở `title`. Trong sprint **không đổi major**. Sau sprint,
đổi major ⟹ chạy lại toàn bộ baseline, vì run signature đã khác.

---

# 5. ADAPTER CONTRACT

Adapter là **tiến trình mỏng, không trạng thái** đứng giữa orchestrator và worker `[R4 S12c]`.

```
Task Spec JSON  ──stdin──►  [ADAPTER]  ──stdout──►  Result JSON
                                │
                                └── exit 0 nếu ADAPTER chạy được
                                    (KỂ CẢ KHI TEST FAIL)
```

Verdict nằm **trong JSON**, **không** nằm trong exit code của adapter — vì exit code chỉ có
một chiều thông tin còn ta cần bốn trạng thái `[R4 S12c]`.

## 5.1 Interface chuẩn

```python
# adapters/_base.py  —  ~70 dòng, A viết, adapter mới chỉ override 2 hàm
from abc import ABC, abstractmethod

class Adapter(ABC):
    NAME: str                # "k6"          → result.worker.name
    ADAPTER_VERSION: str     # "0.1.0"       → result.worker.adapter_version

    # ───────── HAI HÀM DUY NHẤT MỖI ADAPTER PHẢI VIẾT ─────────
    @abstractmethod
    def build_cmd(self, spec: TaskSpec, workdir: Path) -> list[str]:
        """Dịch spec.inputs + spec.oracle → dòng lệnh của worker.
        KHÔNG được quyết định oracle. KHÔNG được đọc spec.intent để điều khiển."""

    @abstractmethod
    def parse_output(self, proc: CompletedProcess, workdir: Path,
                     spec: TaskSpec) -> ParsedOutput:
        """Đọc output THÔ của worker → (metrics, findings, evidence_paths).
        KHÔNG so ngưỡng ở đây — việc đó của oracle/.
        Không parse được → raise AdapterParseError  (→ status=error)"""

    # ───────── PHẦN _base LO, ADAPTER KHÔNG ĐỤNG ─────────
    def run(self, spec: TaskSpec) -> Result:
        t0 = now()
        try:
            cmd  = self.build_cmd(spec, workdir)
            proc = exec(cmd, timeout=spec.budget.wallclock_s)     # timeout = budget
            out  = self.parse_output(proc, workdir, spec)
        except TimeoutExpired:      return self._error(spec, "budget.wallclock_s exceeded", t0)
        except AdapterParseError as e: return self._error(spec, f"parse: {e}", t0)
        except Exception as e:      return self._error(spec, f"crash: {e}", t0)

        verdict = oracle.evaluate(spec.oracle, out.metrics, out.signals)  # ← THƯ VIỆN CHUNG
        self._enforce_expected_result_kind(spec, verdict, out.findings)   # ← cưỡng chế N1/N2
        self._enforce_llm_confidence(out.findings)                        # ← mục 0.3 G4
        evidence = evidence_lib.collect_and_hash(out.evidence_paths,      # ← sha256 BẮT BUỘC
                                                 required=spec.evidence_required)
        return Result(..., cost=Cost(wallclock_s=now()-t0,
                                     tokens=out.tokens,   # None nếu worker không báo
                                     usd=out.usd))        # CẤM ước lượng
```

### Bốn việc mọi adapter đều phải làm — không có ngoại lệ `[R4 S12c]`

1. Dịch `task_spec.inputs` + `oracle` → tham số của worker.
2. **Ép `expected_result_kind`** — worker trả sai loại thì adapter chuyển thành `error`,
   **tuyệt đối không "sửa cho vừa"**.
3. Thu evidence và **băm sha256** từng file (không có hash thì không có audit).
4. Ghi `cost` **thật** — wallclock luôn có; `tokens`/`usd` nếu worker báo, `null` nếu không.
   **Không được ước lượng.**

### Quy tắc vàng — viết lên tường `[R4 S12c]`

> **Adapter được phép MẤT thông tin. Adapter KHÔNG được phép BỊA thông tin.**
> Không chắc → `error`. `error` làm gate đỏ một cách **trung thực**; đoán bừa làm gate xanh một
> cách **gian dối**.

## 5.2 Ba ca khó

| Ca | Vấn đề | Adapter làm gì | Cưỡng chế |
|---|---|---|---|
| **A. CLI-only, có exit code + JSON** (k6, Schemathesis, Midscene CLI) | Dễ nhất | Map exit code → `status`; parse file JSON → `metrics`; **so `oracle.assertions` trong adapter nếu worker không tự so** | `verdict_source = deterministic_assert`. Hợp lệ vì phép so là **số học**, không phải suy luận |
| **B. Chỉ in text người-đọc** (nhiều tool cũ) | Không có cấu trúc | **Parse bằng regex CÓ PHIÊN BẢN**, khai trong manifest (`parser_version`). Regex không khớp → `status: "error"`, **không đoán** | Regex fail **phải** thành `error`. Đây là chỗ cám dỗ "nhờ LLM đọc giúp" — **CẤM** (tầng 4 của `[R3 S11(3)]`) |
| **C. Agent trả văn xuôi tự do** (Explorbot, agent thô) | Không có cấu trúc **và** nội dung là suy luận | **Hai bước:** (1) trích **tín hiệu tất định trước** — có exception không? có 5xx không? có file trace không? (2) phần văn xuôi còn lại → `findings[].rationale` với `verdict_source: llm_judgment`, `gating: false`, **bắt buộc có `confidence`** | **Văn xuôi KHÔNG BAO GIỜ trở thành `verdict.value`. Nó chỉ có thể trở thành một `finding`.** |

**Ca lai (DeepEval):** một lần chạy sinh nhiều metric, mỗi metric có `verdict_source` riêng.
Adapter phát ra **nhiều** `findings[]`, mỗi cái mang `verdict_source` của chính metric đó; chỉ
metric `deterministic_assert` tham gia `verdict.value` `[R4 S12c]` + **[SUY RA] G3**.

## 5.3 Timeout · retry · **worker crash ≠ test fail**

Hai thứ này **KHÔNG được lẫn**. Gộp `error` vào `fail` là cách nhanh nhất giết niềm tin vào
gate `[R3 S11(3)]`.

| Tình huống | **Ai** xác định | `status` | Hành động | Ảnh hưởng gate |
|---|---|---|---|---|
| Assert không thoả | **Worker** | `fail` | **KHÔNG retry** | 🔴 đỏ |
| Worker crash / timeout / output không parse được | **Adapter** | `error` | Retry **≤1 lần** cùng input | 🔴 đỏ, nhãn **"hạ tầng"** |
| Worker không probe được (thiếu binary/env) | **Preflight** | `skipped` | Chạy tiếp | 🟡 **vàng** — in ở **ĐẦU** report |
| Vượt `budget` (wallclock/tokens/usd) | **Orchestrator** | `error` | **Dừng task, KHÔNG dừng run** | 🔴 đỏ |
| Discovery worker không tìm thấy gì | **Worker** | `pass` + `findings: []` | — | ⚪ không ảnh hưởng |

`[R4 S13.3]`

> **Quy tắc retry, một câu:** **chỉ retry `error`, không bao giờ retry `fail`.**
> *Retry một assert fail là cách người ta biến flakiness thành chính sách* `[R4 S13.3]`.

**Timeout = `spec.budget.wallclock_s`.** Không có timeout riêng của adapter — nếu có hai con số
thì sẽ có ngày chúng lệch nhau.

**Phân biệt crash vs fail khi tool không nói rõ** — thứ tự kiểm `[SUY RA]` từ
`[R4 S12c ca A]` + `[R4 S15.4 lớp 4]`:
1. File output có tồn tại và **parse được** không? → không: `error`.
2. Parse được → **giao metrics cho `oracle/`** để so. Kết quả so = `pass`/`fail`.
3. Diễn giải exit code theo contract của worker. Nếu worker mã hoá assertion trong exit code,
   so exit với oracle; hai tín hiệu mâu thuẫn ⟹ `error`. Nếu worker không có threshold trong
   script như k6 adapter STEP 31, mã khác 0 ⟹ `error` (lỗi worker), còn ngưỡng do oracle quyết.

k6 v2.2.0 exit 99 ở script khám phá riêng có threshold; adapter chạy `notes_list.js` không có
threshold và vì vậy yêu cầu exit 0. Chi tiết đã xác minh ở `docs/decisions.md`.

## 5.4 Adapter mẫu viết đầy đủ — `adapters/k6_adapter.py`

Chọn k6 làm mẫu vì nó là worker **được nguồn mô tả đầy đủ nhất**: có `--summary-export`
`[R3 9.4]` `[R4 S12b]`. Trong adapter của STEP 31, script chỉ kiểm tra HTTP 200 và không có
threshold; adapter đòi exit code 0 rồi chuyển số đo cho oracle chung. Script khám phá riêng có
threshold có thể exit khác 0 khi ngưỡng bị vượt.
*(Trong sprint, adapter **thật đầu tiên** là Schemathesis ở slot 2 `[R5 P3]` — nó được viết theo
đúng khuôn này và do A review làm mẫu cho hai cái sau.)*

```python
# adapters/k6_adapter.py   —  ~90 dòng, C viết, A review theo checklist 5 câu
#
# Đã xác minh trên k6 v2.2.0 (darwin/arm64), xem `docs/decisions.md`:
#     các cờ --vus / --duration / --summary-export;
#     p95 ở metrics.http_req_duration["p(95)"], failed-rate ở metrics.http_req_failed.value.
# Seed không được truyền vào k6 trong adapter này; spec dùng determinism.seed = null.
from pathlib import Path
from subprocess import CompletedProcess
from adapters._base import Adapter, ParsedOutput, AdapterParseError
import json

class K6Adapter(Adapter):
    NAME            = "k6"
    ADAPTER_VERSION = "0.1.0"

    # ───────────────────────── 1. DỊCH inputs → CLI ─────────────────────────
    def build_cmd(self, spec, workdir: Path) -> list[str]:
        script = spec.inputs["script"]                    # "tests/perf/checkout.js"
        summary = workdir / "k6-summary.json"
        cmd = [
            "k6", "run",
            f"--summary-export={summary}",                # ✔ có trong RUN 4 S12b
            "--vus",      str(spec.inputs["vus"]),
            "--duration", spec.inputs["duration"],
            script,
        ]
        # APP_BASE_URL khai ở workers/k6.yaml `requires.env`; seed không dùng, spec đặt null.
        self.env = {"APP_BASE_URL": spec.target["base_url"]}
        self._summary_path = summary
        return cmd

    # ───────────────────────── 2. ĐỌC output thô ────────────────────────────
    def parse_output(self, proc: CompletedProcess, workdir: Path, spec) -> ParsedOutput:
        cmd_used = proc.args                              # subprocess.CompletedProcess.args
        # (a) Không có file summary ⟹ worker hỏng, KHÔNG phải test fail.
        if not self._summary_path.exists():
            raise AdapterParseError("k6 không xuất được summary — coi là crash, không phải fail")
        if proc.returncode != 0:
            raise AdapterParseError(f"k6 kết thúc với exit code {proc.returncode}")

        try:
            raw = json.loads(self._summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise AdapterParseError(f"summary không parse được: {e}")   # → status=error

        # (b) TRÍCH SỐ. Tuyệt đối KHÔNG so ngưỡng ở đây — việc đó của oracle/threshold.py
        #     Dùng đúng khoá nguồn đã xác minh từ summary thật của k6 v2.2.0.
        try:
            m = raw["metrics"]
            metrics = {
                "http_req_duration.p95": m["http_req_duration"]["p(95)"],
                "http_req_failed.rate":  m["http_req_failed"]["value"],
            }
        except (KeyError, TypeError) as e:
            raise AdapterParseError(f"thiếu metric bắt buộc trong summary: {e}")

        # (c) Ghi stdout ra file để làm evidence
        stdout_path = workdir / "stdout.log"
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")

        notes = []
        # (d) Ghi lại exit code. Script adapter không khai báo threshold nên mã khác 0 là lỗi worker.
        notes.append(f"k6 exit_code={proc.returncode}")

        return ParsedOutput(
            metrics        = metrics,
            findings       = [],            # k6 không sinh finding, chỉ sinh metric
            signals        = {},            # không có implicit signal
            evidence_paths = [("raw_output", self._summary_path),
                              ("stdout",     stdout_path)],
            tokens         = 0,             # k6 không gọi model
            usd            = 0.0,
            exit_code      = proc.returncode,
            replay_cmd     = " ".join(cmd_used),   # đúng lệnh đã chạy, ghi lại nguyên văn
            adapter_notes  = notes,
        )
```

Result sinh ra từ adapter này chính là ví dụ k6 ở `[R4 S12b]`:
`verdict_source: "deterministic_assert"`, `gating: true`, `confidence: null`,
`metrics: {"http_req_duration.p95": 214.7, "http_req_failed.rate": 0.002}`,
`cost: {"wallclock_s": 126, "tokens": 0, "usd": 0.0}`.

### Chỗ ranh giới dễ trượt — nói trước để khỏi cãi nhau lúc code `[R5 1.3]`

> `oracle` là chỗ nguy hiểm: cám dỗ là nhét logic so sánh đặc thù vào adapter.
> **Quy tắc: bộ so sánh nằm ở `oracle/`, adapter chỉ TRÍCH SỐ rồi giao cho bộ so sánh.**
> Worker cần `oracle.kind` mới ⟹ **thêm file mới vào `oracle/`**, không nhét vào adapter.

Nhìn lại `k6_adapter` ở trên: nó **không có một phép so sánh nào**. Đó là tiêu chuẩn để review
hai adapter còn lại.

## 5.5 Manifest đi kèm — `workers/k6.yaml`

```yaml
name: k6
version_probe: "k6 version"        # probe động: có cài không, bản nào
adapter: "adapters/k6_adapter.py"
lanes: [gate]                      # KHÔNG được nhận task discovery
capabilities:
  - id: http.load
    inputs_schema: schemas/http_load.inputs.json
    oracle_kinds: [threshold]
    verdict_sources: [deterministic_assert]  # tập giá trị worker ĐƯỢC PHÉP phát ra
    parallel_safe: false                     # chiếm tài nguyên đo lường → xếp hàng riêng
requires:
  env: [APP_BASE_URL]
  binaries: [k6]
data_egress: []                    # KHÔNG gửi gì ra ngoài
cost_profile: { tokens_per_run: 0, typical_wallclock_s: 120 }
```

```yaml
# workers/deepeval.yaml — worker LAI
name: deepeval
lanes: [gate, discovery]
capabilities:
  - id: llmapp.eval
    verdict_sources: [deterministic_assert, llm_judgment]   # CẢ HAI
    # RUN 4 S12d ghi gating = [ToolCorrectness, JSONCorrectness]. Toy app KHÔNG có tool-call, nên PoC dùng
    # 3 metric tất định của RUN 5 §2.2 (không rỗng · ngắn hơn body · JSON đúng schema). Hai cái sau
    # là metric TỰ VIẾT (DAG hoặc custom) — RUN 1-5 không nói cách viết; ngày 1 kiểm với DeepEval.
    gating_metrics:   [JSONCorrectness, summary_not_empty, summary_shorter_than_body]
    advisory_metrics: [GEval]
    parallel_safe: true
requires:
  env: []   # OpenAI/Gemini are alternatives; STEP 02 probes primary + fallback
data_egress: [app_input, app_output]   # sent to selected judge provider; record actual provider/model
```

Registry nạp lúc khởi động bằng cách quét `workers/*.yaml`; `version_probe` chạy ở bước
preflight và ghi kết quả vào metadata của run. **Worker không probe được → task của nó thành
`skipped` kèm lý do, KHÔNG phải `fail`** `[R4 S12d]`.

---

# 6. ORCHESTRATOR POLICY

## 6.1 Planning — từ input gì → sinh test plan thế nào

**Planner chạy khi nào: KHÔNG phải mỗi PR.** Chạy khi (1) spec/contract đổi, (2) có
capability/worker mới, (3) người gọi tay `[R4 S13.2]`.

```
intent/spec ──LLM(plan-gen)──► plan.draft.yaml ──diff──► NGƯỜI duyệt ──► plan.yaml (COMMIT)
                                                                            │
                                            mỗi PR: CI đọc plan.yaml ───────┘──► thực thi
                                            (0 lời gọi LLM nếu gate xanh)
```

**Đầu vào của planner, xếp theo độ tin cậy giảm dần** `[R4 S13.2]`:

| Nguồn | Dùng để | Độ tin |
|---|---|---|
| **OpenAPI / schema / contract** | Sinh task `api.contract`, `api.property` | **Cao** — máy đọc được |
| **Code diff** (file + symbol đổi) | Chọn tập regression, quyết capability nào cần chạy | Cao |
| **Coverage map** (test ↔ code) | Chọn tập tối thiểu | Cao |
| **Requirement / acceptance criteria** | Sinh task functional — chỗ cần LLM nhất | TB — spec hay thiếu |
| **Lịch sử bug + lịch sử flaky** | Tăng trọng số vùng hay hỏng; đề xuất quarantine | TB |

**Duyệt diff plan — tự động hoá một phần** `[R4 S13.7 #1]`:
**tự merge** diff chỉ **THÊM** task · **bắt buộc người duyệt** khi diff **XOÁ hoặc THU HẸP** task.
Đây là điểm HITL đắt giá nhất **và** rẻ nhất — vài lần mỗi tháng.

### Selection — chọn phạm vi chạy, TẤT ĐỊNH

```
selected = floor ∪ ( impact(changed_paths, coverage_map) ∩ plan.tasks ) ∪ always_on(changed_paths)
```

| Thành phần | Định nghĩa | Vì sao |
|---|---|---|
| `changed_paths` | **CHỈ** `git diff --name-only <base>..HEAD` | Đầu vào duy nhất từ PR. **Tiêu đề / mô tả / comment / nội dung diff KHÔNG được đọc** |
| `floor` | Tập task tối thiểu **luôn chạy** (khai trong plan) | Chặn selection thu hẹp về 0: luật có sai thì smoke vẫn chạy |
| `impact` | Ánh xạ path → task từ `coverage_map` (PoC: **bảng tay** trong plan) | Chọn tập con |
| `always_on` | Luật risk theo path: `auth/** ⟹ security.*`, `payment/** ⟹ security.* + http.load` | Luật risk theo domain |
| `selected ⊆ plan.tasks` | Chỉ chọn **trong plan đã pin**, không sinh task mới | Giữ plan-as-artifact |

```yaml
# khối selection trong plan.yaml
selection:
  floor: [t-001, t-002]                 # luôn chạy
  impact:
    "app/notes/**":     [t-001, t-003]
    "app/summarize/**": [t-003]
  always_on:
    - when: "app/auth/**"
      run:  [t-sec-01]
```

**Bất biến phải viết thành test của chính orchestrator** `[R4 S13.2b]`:
cùng `(plan.yaml, changed_paths)` ⟹ cùng `selected`; **đổi tiêu đề PR thành chuỗi bất kỳ ⟹
`selected` KHÔNG đổi**.

**Giới hạn — nói thẳng:** path do tác giả PR kiểm soát. Luật này chặn được việc *bị thuyết phục*,
**không** chặn được việc *né path* — đó là việc của `floor` và của nightly full run `[R4 S13.2b]`.

## 6.2 Routing — luật chọn worker

Routing **không phải là suy luận** — nó là **phép lọc trên registry** `[R4 S13.1]`:

```python
candidates = [w for w in registry
              if task.capability     in w.capabilities
              and task.lane          in w.lanes
              and task.oracle.kind   in w.capability.oracle_kinds
              and w.probe_ok]
```

| Số ứng viên | Hành động |
|---|---|
| **0** | Task `skipped`, **ghi lý do vào report**. Không được im lặng bỏ qua |
| **1** | Chạy |
| **>1** | Chọn theo thứ tự ưu tiên khai trong plan (`prefer: [k6, locust]`). **KHÔNG để LLM chọn** — chọn bằng LLM là đưa non-determinism vào *coverage* |

### Song song vs tuần tự

- Xếp task thành **DAG** theo `depends_on`. Ví dụ bắt buộc: task **thu output của AI app** phải
  xong trước task DeepEval chấm output đó — DeepEval **không tự chạy app**, nó cần sẵn cặp
  `input`/`actual_output` `[R3 9.6]` `[R3 rủi ro #4]`.
- Trong mỗi tầng DAG: chạy **song song** mọi task có `parallel_safe: true`.
- Task `parallel_safe: false` (**k6**, và mọi thứ dùng thiết bị/môi trường đo) **xếp hàng riêng**.
- **Discovery lane không bao giờ nằm trên đường tới verdict** — chạy độc lập, kết quả không chặn.

> **Chạy song song là BẮT BUỘC, không phải tối ưu hoá** `[R3 S11(2)]`: latency = `max(worker)`,
> không phải `sum(worker)`. Gọi tuần tự vì code dễ viết là **tự nhận `sum()` mà không có lý do
> kiến trúc nào**.

## 6.3 Aggregation — gộp nhiều QRS thành 1 verdict

```python
gate_verdict = AND( r.verdict.value == "pass"
                    for r in results
                    if r.verdict.gating == True )
```

Chỉ kết quả `gating: true` tham gia. Theo ràng buộc `allOf` của schema, `gating: true` **chỉ có
thể** đến từ `deterministic_assert`. ⟹ **verdict tổng là một hàm tất định của các assert tất
định.** *Đây là N2 ở dạng một dòng công thức, chứng minh được chứ không phải hứa hẹn*
`[R4 S13.4]`.

### Bảng luật khi xung đột — viết thành luật cụ thể

| # | Tình huống | **Verdict tổng** | Hành động kèm theo |
|---|---|---|---|
| 1 | Mọi `gating=true` đều `pass` | 🟢 **PASS** · exit 0 | — |
| 2 | Có ≥1 `gating=true` = `fail` | 🔴 **FAIL** · exit 1 | In task nào gây fail ở mục 1 của report |
| 3 | **`deterministic PASS` + `llm_judgment FAIL`** | 🟢 **PASS** — LLM **không** lật verdict | Ghi vào mục **"LLM disagreement"** **TÁCH HẲN** khỏi verdict. Đếm tần suất. **≥3 run cùng loại bất đồng ⟹ tự động mở task review cho người** |
| 4 | **`deterministic FAIL` + `llm_judgment PASS`** | 🔴 **FAIL** — LLM **không** lật verdict | Như trên, cùng bộ đếm |
| 5 | Có `error` (sau khi retry ≤1) | 🔴 **FAIL** · exit 1 | Nhãn **"hạ tầng"**, không phải "code sai" |
| 6 | Có `skipped` | 🟡 **VÀNG** · **[SUY RA] exit 0** (mục 0.3 G1) | **In khối SKIPPED ở ĐẦU report**, không phải cuối. Ghi rõ *vùng nào không được kiểm* |
| 7 | Discovery lane có finding (kể cả `severity_hint: high`) | **không ảnh hưởng** | Vào mục 3 của report |
| 8 | Canary báo `pass` (lẽ ra phải `fail`) | **không ảnh hưởng gate** | 🚨 **Báo động worker hỏng** — không phải app tốt |

**Vì sao luật 3 và 4 đối xứng — lý do cho cả hai chiều đều hỏng** `[R4 S13.4]`:

| Nếu cho LLM lật thành `fail` | Nếu cho LLM lật thành `pass` |
|---|---|
| Gate đỏ **không tái lập được** → dev chạy lại là xanh → **team học cách bấm re-run** → gate mất hết giá trị | Một phán quyết **phi tất định ghi đè một assert tất định** → đây là **thảm hoạ**, không phải trade-off |

**Bất đồng được xử lý như DỮ LIỆU, không như phán quyết** `[R4 S13.4]`. Sau ≥3 lần, người review
kết luận một trong ba: (a) LLM đúng → **viết assert tất định mới** (bất đồng trở thành test —
đây là mũi tên *promote* ở cấp metric); (b) LLM sai → chỉnh rubric / loại metric; (c) mơ hồ →
ghi nhận, giữ advisory.

> **Nói ngắn: LLM là NGƯỜI TỐ GIÁC, không phải QUAN TOÀ.** Nó được quyền làm ồn cho tới khi ai
> đó biến tiếng ồn đó thành một assert — lúc đó nó mới có quyền chặn.

## 6.4 Determinism guard

### Phần tất định — 4 cơ chế `[R4 S13.5]`

1. **Run signature** = `sha256(plan_id ‖ sut_identity ‖ {worker, version, adapter_version} ‖ seeds)`.
   Hai run **cùng signature mà khác verdict** ở phần `deterministic_assert` = **bug của hệ thống
   chúng ta**, không phải bug của SUT. → viết thành một **test của chính orchestrator**.
2. **Plan đã pin** (6.1).
3. **`replay_cmd` trong mỗi result** — chạy lại một worker độc lập, không cần orchestrator.
4. **Pin phiên bản worker trong registry** — đổi version ⟹ signature đổi ⟹ không so nhầm với
   baseline cũ.

### Phần LLM — kiểm soát phương sai theo thứ tự ưu tiên `[R4 S13.5]`

| Bậc | Cơ chế | Hiệu lực | **Dùng trong PoC?** |
|---|---|---|---|
| **1** | **Không cho LLM vào đường verdict** | **Triệt để** | ✅ **Gate lane — đây là bậc mặc định** |
| 2 | Pin model snapshot + `temperature = 0` | Giảm, **không triệt tiêu** — cùng prompt vẫn lệch | ✅ Advisory |
| 3 | n-sample voting (n lẻ) | Giảm phương sai, **nhân chi phí ×n** | ❌ **Không** — đổi chác sai |
| 4 | Hoán vị vị trí A/B, chỉ nhận kết quả nhất quán | Khử position bias | ❌ Không trong sprint |
| 5 | Gate theo **delta so baseline**, không theo ngưỡng tuyệt đối | Cách **duy nhất** khiến điểm judge dùng được cho gate | ✅ Advisory (baseline viết tay trong PoC) |

**Tiêu chí nghiệm thu tính tái lập** `[R5 2.3 #4]`: chạy 2 lần cùng plan + cùng commit →
`diff run1/report.json run2/report.json` **sau khi lọc trường `llm_judgment` và timestamp** →
**rỗng**. `[CHƯA VERIFY]` — giả định Schemathesis pin seed được `[R5 (a)#5]`; nếu không, cố định
số example và chấp nhận so sánh yếu hơn.

## 6.5 Human escalation — đúng 4 chỗ, không hơn `[R4 S13.3]`

| # | Tín hiệu kích hoạt | Ai xử lý |
|---|---|---|
| 1 | Gate đỏ do **`error` lặp ≥2 run liên tiếp** trên cùng worker | → nghi **hạ tầng**, không nghi code |
| 2 | **`llm_judgment` mâu thuẫn `deterministic_assert` ≥3 run** | → task review (6.3 luật 3/4) |
| 3 | Discovery sinh finding `deterministic_assert` **tái lập được** | → hàng chờ **promote** |
| 4 | **Vượt trần chi phí toàn run** | → người quyết có nâng trần không |

### Bảy điểm human còn chạm tay — và mức tự động cao nhất còn an toàn `[R4 S13.7]`

| # | Điểm human | Bỏ human thì **MẤT GÌ** | Mức tự động cao nhất còn an toàn |
|---|---|---|---|
| 1 | Duyệt **diff `plan.yaml`** | Coverage trôi âm thầm: PR xanh vì worker đúng đã **không được gọi**; không ai phát hiện vì **không có gì đỏ** | Tự merge diff chỉ **thêm**; **bắt buộc người** khi **xoá/thu hẹp** |
| 2 | **Promote** candidate → gate test | Test nhiễu vào gate → gate đỏ vô cớ → team mất tin | Tự động khi **đủ ba**: `deterministic_assert` **và** tái lập **≥3/3** **và** có `suggested_assertion` → **tự mở PR** (PR, **không** commit thẳng). *(Thiết kế xong, không cài trong sprint)* |
| 3 | **Nộp ticket** | Spam ticket trùng; action không đảo ngược rẻ; phân loại sai **không đối xứng** | Soạn + dedup tự động → `tickets_draft[]` trong `report.json`; **người bấm một nút cho cả lô**. **Không bao giờ tự nộp/tự đóng** |
| 4 | **Sửa golden set / baseline** | **Gian lận khó phát hiện nhất**: sửa chuẩn cho test pass | **Không tự động hoá.** Bắt buộc qua PR có review. Chỉ tự động phần *đề xuất case mới từ trace* |
| 5 | **Quarantine test flaky** | Bug thật bị tắt tiếng | Tự quarantine khi vượt ngưỡng, **kèm hạn 14 ngày**: tự mở lại + tự tạo task điều tra. **Quarantine im lặng vĩnh viễn là CẤM** |
| 6 | **Cấp credential + đặt trần ngân sách** | Blast radius không kiểm soát | **Không tự động hoá.** Cấu hình một lần, review định kỳ |
| 7 | **Kết luận bất đồng LLM vs deterministic** | Hoặc mất tín hiệu thật, hoặc nhận nhiễu vào gate | Tự gom + đếm + mở task khi ≥3. **Kết luận thì người** |

**Tổng kết mức tự động hoá** `[R4 S13.7]`: trong 10 bước vòng đời của S3 — **7 bước tự động hoàn
toàn** (test data, execution, failure detection, regression selection, reporting, aggregation, và
phần *soạn* của bug reporting); **3 bước còn human**, và cả ba đều nằm đúng ở chỗ **hành động
không đảo ngược rẻ được** hoặc **chuẩn mực bị thay đổi**.

## 6.6 Auditability — chuỗi truy ngược

```
gate_verdict (run r-0088)
   └─ results[]  (chỉ gating=true)
        └─ result t-042
             ├─ verdict.verdict_source = deterministic_assert
             ├─ oracle đã dùng   ──► task_spec t-042 ──► plan.yaml @ commit abc123
             ├─ evidence[]       ──► k6-summary.json (sha256 a1b2…)
             ├─ determinism.replay_cmd ──► chạy lại ĐỘC LẬP, không cần orchestrator
             ├─ worker k6@1.3.0 + adapter@0.1.0
             └─ sut_identity_ref sut-9f2c ──► { code commit, prompt hash, model snapshot,
                                                corpus id, decoding params }
```

**Ba bất biến phải giữ** `[R4 S13.6]`:
1. **Không có evidence thì không có verdict** — result thiếu evidence bắt buộc là `error`.
2. **Mọi evidence có sha256.**
3. **`plan.yaml` có commit hash** — nếu không thì không biết đã chạy *cái gì*.

## 6.7 Knowledge store — ai được đọc `[R4 S13.7 R5]`

Gồm 4 phần: **dataset** · **baseline** · **app map** · **flaky list (có hạn 14 ngày)**.

> **Chỉ `plan-gen` (ngoài CI) đọc store. Đường chạy CI KHÔNG đọc** ⟹ store đổi **không làm gate
> mất tái lập.**

**Security finding** route sang **queue riêng**, không lên board bug công khai `[R4 S13.7 R5]`.

---

# 7. GOVERNANCE LAYER (DIFF-GUARD)

## 7.1 Trước hết: RUN 1–5 kết luận gì, và KHÔNG kết luận gì

Phải nói thẳng vì mục này dễ bị viết vượt nguồn:

| Câu hỏi | Trả lời từ RUN 1–5 |
|---|---|
| RUN 1–5 có phát hiện **sự cố thật** "agent tự sửa assertion để ép pass" không? | **KHÔNG.** Không RUN nào ghi nhận sự cố này |
| RUN 1–5 có kết luận cần một lớp **chặn mutation lên artifact** không? | **CÓ** — bằng 4 luật cụ thể, ở 4 chỗ khác nhau (7.2) |
| RUN 1–5 có đặc tả cơ chế **AST diff trên file spec** (bắt xoá `expect`, hạ cấp matcher, nới timeout) không? | **KHÔNG.** Cơ chế này **không tồn tại** trong nguồn |

⟹ Mục 7.2 là **luật đã chốt, code được**. Mục 7.4 là **[NGOÀI RUN 1–5]** — đề xuất, **không phải
quyết định**, và **không nằm trong sprint**.

## 7.2 Bốn luật diff-guard ĐÃ CHỐT trong RUN 1–5

Bề mặt cần bảo vệ = mọi đường mà **máy** sửa được **artifact định nghĩa chuẩn mực**. Đúng 4 đường:

| # | Luật | Phát hiện cái gì | Mức | **Hành động** | Nguồn |
|---|---|---|---|---|---|
| **DG-1** | **Plan diff-guard** | Diff `plan.yaml` **XOÁ** hoặc **THU HẸP** task (so với diff chỉ **thêm**) | 🔴 chặn | **Bắt buộc người duyệt.** Diff chỉ-thêm thì tự merge | `[R4 S13.7 #1]` |
| **DG-2** | **Self-heal / agent patch** | Bất kỳ patch nào do healer hoặc agent sinh ra cho file test | 🔴 chặn | **KHÔNG tự merge.** Đi thành **PR riêng**, người duyệt. Áp cho cả healer của Playwright và mọi công cụ nhóm G4 | `[R2 S8 G4 R5]` `[R4 S13.7 R5]` |
| **DG-3** | **Golden set / baseline guard** | Mọi thay đổi lên golden set hoặc baseline | 🔴 chặn | **Không tự động hoá.** Bắt buộc qua **PR có người review**. *Sửa golden set để test pass là dạng gian lận khó phát hiện nhất trong cả hệ thống* | `[R2 S7.3]` `[R4 S13.7 #4]` |
| **DG-4** | **Heal-rate monitor** | Tỉ lệ heal **tăng** theo thời gian | 🟡 cảnh báo | **Log MỌI lần heal** + cảnh báo khi heal rate tăng. *Heal rate cao là **mùi hỏng**, không phải thành tích; heal có thể **che regression thật*** | `[R1 S1]` `[R3 S11 G4]` |

Cộng thêm hai luật **cùng họ** đã nằm ở chỗ khác của tài liệu này, nhắc lại để không sót:
- **Auto-promote chỉ được mở PR, không được commit thẳng**, và chỉ khi đủ ba điều kiện (6.5 #2).
- **Selection không được đọc nội dung PR** — luật bất biến "đổi tiêu đề PR ⟹ `selected` không
  đổi" (6.1) chính là một diff-guard cho *coverage*.

## 7.3 Giới hạn — chỗ senior sẽ soi, nói thẳng

| DG-x **KHÔNG** bắt được | Vì sao |
|---|---|
| **Né path** để selection không chọn task đúng | Path do tác giả PR kiểm soát. Luật chặn được *bị thuyết phục*, không chặn được *né*. Đối phó: `floor` + nightly full run `[R4 S13.2b]` |
| Sửa **chính bản thân assert** trong file test bằng tay (người, không phải agent) | DG-2 chỉ nhắm patch do máy sinh. Người sửa assert là chuyện của code review thường |
| **Nới threshold trong `plan.yaml`** mà vẫn là diff "thêm/sửa", không phải "xoá" | DG-1 phân loại theo *thêm vs xoá/thu hẹp*; "đổi `p95 < 300` thành `p95 < 3000`" là **sửa**, và nguồn **không nói** nó rơi vào nhánh nào ⟹ **khoảng trống thật** |
| Worker tự hành **báo pass sai** | Không phải việc của diff-guard. Đó là 5 lớp phòng thủ của D4 (canary là lớp 3) |
| Golden set bị "làm mềm" dần qua nhiều PR nhỏ, mỗi PR đều được duyệt | DG-3 chặn từng PR, không nhìn xu hướng. Không có cơ chế nào trong RUN 1–5 nhìn xu hướng này |

## 7.4 **[NGOÀI RUN 1–5]** — AST diff-guard: đề xuất, KHÔNG phải quyết định

Ý tưởng "AST diff trên file spec để bắt xoá `expect`, hạ cấp matcher, nới timeout" **không có
trong RUN 1–5**. Nó giải đúng hai khoảng trống ở 7.3 (nới threshold, làm mềm dần), nên đáng ghi
lại — **nhưng không ai được code nó trong sprint này**, vì:
1. PoC **không có** đường nào cho máy tự sửa file spec (auto-promote chưa cài `[R5 1.2]`).
2. Thêm nó vào là thêm một cấu phần không được nguồn nào biện minh — đúng thứ mục 1.3 tồn tại để
   chặn.

**Điều kiện để mở lại:** khi auto-promote (6.5 #2) được cài thật, tức khi **máy bắt đầu mở PR
sửa file test**. Lúc đó DG-2 (người duyệt PR) là tuyến phòng thủ duy nhất, và việc bổ sung một
lớp phân tích tự động mới có lý do tồn tại.

---

# 8. REUSE vs REWRITE BOUNDARY

Đây là phần chứng minh chiều sâu kiến trúc: **cái gì là tài sản, cái gì là chi phí lặp lại**
`[R5 1.3]`.

## 8.1 Bảng hai cột

| **TÁI SỬ DỤNG khi mở rộng surface** — viết một lần | **BẮT BUỘC VIẾT LẠI cho từng surface/worker** |
|---|---|
| `schemas/task_spec.json` (~80 dòng) — **bất biến** | **Cách bóc UI state**: DOM+a11y tree+screenshot (web) vs a11y tree/UiAutomator+logcat (Android) vs UIA tree (desktop) vs *không có GUI, schema + query log là interface* (DB) `[R2 S6]` |
| `schemas/result.json` (~120 dòng) gồm từ vựng `verdict_source` 3 giá trị — **bất biến** *(thêm giá trị thứ 4 mới phải sửa — cố ý khó)* | **Định nghĩa oracle cho surface đó**: API có **3 oracle tất định độc lập** (schema/differential/invariant); web phải tự định nghĩa; Android có **crash/ANR trong logcat là oracle tất định mạnh và miễn phí** `[R2 S6]` |
| `core/registry.py` (~60) — quét YAML, probe, lọc | **Dịch `inputs` → tham số CLI** (15–30 dòng): mỗi tool một bề mặt CLI — **đây là bản chất, không phải thiếu sót thiết kế** |
| `core/runner.py` (~120) — DAG, spawn song song, timeout, budget | **Đọc output thô → `metrics`/`findings`** (20–50 dòng): k6 xuất JSON summary; Schemathesis xuất khác; Midscene xuất `--summary`; DeepEval qua pytest |
| `core/verdict.py` (~50) — `AND` trên `gating=true`, xử lý `error`/`skipped` | **Cách áp `oracle`** (10–30 dòng): worker tự so ngưỡng (k6) hay adapter phải trích số giao cho `oracle/` (Midscene)? |
| `core/report.py` (~90) — render 3 mục tách theo `verdict_source` | **Map trạng thái → 4 trạng thái của ta** (10–20 dòng): exit code mỗi tool mang nghĩa khác nhau; **phân biệt `fail` với `error` là quyết định phải ĐỌC DOC từng tool** |
| `core/evidence.py` (~30) — sha256, đường dẫn chuẩn | **`workers/<name>.yaml`** (~25 dòng): capability, lane, `verdict_sources`, `parallel_safe`, `data_egress` |
| `adapters/_base.py` (~70) — stdin→stdout, validate, `error`, wallclock. **Adapter mới kế thừa, chỉ override 2 hàm** | |
| `oracle/threshold.py`, `oracle/signals.py` (~60) — **dùng lại nếu `oracle.kind` trùng** | |
| **Chính sách**: 2 lane · luật gộp · luật retry · 4 điểm escalation · 7 điểm HITL — **không phụ thuộc surface** | |
| **≈ 680 dòng — ĐÂY LÀ TÀI SẢN.** Nó tồn tại sau khi mọi worker hôm nay đã lỗi thời | **≈ 80–150 dòng/worker — đây là chi phí biên** |

> **Tỉ lệ này CHÍNH LÀ luận điểm kiến trúc** `[R5 1.3]`: ~680 dòng dùng chung / ~110 dòng chi phí
> biên mỗi worker. **Nếu tỉ lệ đảo ngược — mỗi worker mới cần sửa lõi — thì kiến trúc đã hỏng và
> ta chỉ đang viết một script gọi 4 tool.**

## 8.2 Thêm worker thứ 5 sau sprint tốn bao nhiêu — ước lượng bằng giờ

**Kịch bản chuẩn: thêm `axe-core` (accessibility, web)** `[R5 1.4]`.

| Bước | Việc | **Giờ** | Sửa `core/`? |
|---|---|---|---|
| 1 | `workers/axe.yaml`: `capability: web.a11y`, `lanes: [gate]`, `verdict_sources: [deterministic_assert]`, `parallel_safe: true` | 10 phút | ❌ |
| 2 | `schemas/web_a11y.inputs.json` (url, rule tags) | 10 phút | ❌ |
| 3 | `adapters/axe_adapter.py` kế thừa `_base`, override `build_cmd()` + `parse_output()` | 45 phút | ❌ |
| 4 | `oracle.kind: "rule_violations"` là **mới** → thêm `oracle/rule_violations.py` | 20 phút | ⚠️ thêm **file** vào thư viện dùng chung, **không sửa logic điều phối** |
| 5 | Thêm task vào `plan.yaml` (hoặc chạy `plan-gen` rồi duyệt diff) | 5 phút | ❌ |
| | **TỔNG** | **≈ 1.5 giờ** | **0 dòng trong `core/`** |

**Cơ sở của ước lượng** (để người đọc tự đánh giá độ tin, không phải để tin):
- Nó là **tổng của 5 việc đã liệt kê**, mỗi việc gắn với một file cụ thể — không phải một con số
  cảm tính.
- Nó khớp với chi phí biên ở 8.1 (~110 dòng/worker).
- ⚠️ **Đây là phán đoán kỹ thuật, KHÔNG phải số đo** `[R3 (a)#8]` `[R5 (a)#2]`. **Có thể lệch hệ
  số 2.** Đừng đưa con số chính xác lên slide — nói **"khoảng"**.

**Cách chứng minh bằng BẰNG CHỨNG, không bằng lời** — đưa thẳng vào demo `[R5 1.4]`:

```
git log --stat -1        # commit "add axe worker"
 workers/axe.yaml              | 26 ++++++
 schemas/web_a11y.inputs.json  | 18 +++++
 adapters/axe_adapter.py       | 94 ++++++++++++++
 oracle/rule_violations.py     | 41 +++++++
 plan.yaml                     |  9 ++
 5 files changed, 188 insertions(+)
 # core/  KHÔNG xuất hiện trong diff   ◄── ĐÂY LÀ N5
```

### Khi nào ước lượng 1.5 giờ này KHÔNG còn đúng

| Điều kiện | Chi phí thật | Vì sao |
|---|---|---|
| Worker có `A9 = 2` (CLI + structured output + exit code đáng tin) | **~1.5 giờ** ✅ | Đúng kịch bản trên |
| Worker có `A9 = 1` (vd **Explorbot**: chỉ có HTML/Markdown report) | **Nhiều hơn hẳn, và KHÔNG NÊN LÀM** | Phải parse HTML → *"đúng loại adapter đặc thù mà N4 cấm"* `[R3 9.2]` |
| Worker cần `oracle.kind` **đã có** | **~1.1 giờ** | Bỏ bước 4 |
| Worker cần **surface mới** (mobile, desktop) | ❌ Không phải bài toán 1.5 giờ | Cột phải của 8.1: cách bóc UI state và định nghĩa oracle phải viết lại từ đầu; cộng chi phí hạ tầng (emulator/device farm) — *"bài toán hạ tầng, không phải bài toán QC"* `[R4 S14.3]` |
| Worker sinh **`verdict_source` thứ tư** | ❌ Sửa lõi | Xem 4.3 |

---

# 9. KNOWN LIMITATIONS & OPEN QUESTIONS

## 9.1 Hệ thống này KHÔNG bắt được loại bug nào

| Loại bug | Vì sao không bắt được | Nguồn |
|---|---|---|
| **Test đúng cho requirement SAI** | Sai ở bước 1 (requirement) nhân lên toàn bộ hạ nguồn, và **không có oracle nào phát hiện được** "test đúng cho requirement sai" | `[R1 S3 bước 1]` |
| **Bug logic nghiệp vụ khi spec không viết ra** | Oracle đến từ spec; spec không đầy đủ là **trạng thái mặc định của mọi team**. Agent lấp khoảng trống bằng cách **bịa** criteria hợp lý — và test bịa vẫn "pass", nên **không có tín hiệu báo động** | `[R2 S5-1]` |
| **Bug mà record-replay đã đóng băng** | Replay khoá chặt **hành vi hiện tại, kể cả hành vi sai** *(không áp dụng cho PoC vì không có Keploy, nhưng áp dụng cho roster)* | `[R2 S5-3]` |
| **Chất lượng cảm nhận của AI feature** | Gate chỉ dùng bậc 1–5. Summary tệ đi nhưng đúng schema + đúng độ dài ⟹ **gate xanh** | D5 Consequences |
| **Regression im lặng khi provider đổi model** sau cùng một alias | Không có commit, không có PR, không có ai để đổ lỗi. Chỉ chặn được nếu pin model snapshot id — PoC **chưa có** SUT identity đầy đủ | `[R2 S7.2]` |
| **Vùng không có trong `plan.yaml`** (V3 — plan mục) | Gate vẫn xanh, vẫn tái lập, và **vẫn vô nghĩa**. Đây là **cái giá của plan-as-artifact** | `[R4 V3]` |
| **Vùng bị né path** trong selection | Luật chặn *bị thuyết phục*, không chặn *né path* | `[R4 S13.2b]` |
| Phần **43% issue a11y** mà rule engine không bắt | Con số 57% là của **chính vendor** (Deque làm ra axe-core), đo theo *số lượng* issue ⟹ **thổi phồng** mức phủ | `[R2 S5 COARSE]` `[R2 (a)#2]` |
| Bug chỉ lộ ngoài trần ~65% activity coverage của exploration | Exploration là **bổ sung**, không phải **thay thế** regression suite | `[R1 S3]` |

## 9.2 Chỗ còn phụ thuộc phán đoán LLM — và rủi ro đi kèm

| Chỗ | Ở đâu | Rủi ro | Cái gì chặn |
|---|---|---|---|
| `plan-gen` sinh plan từ spec | **Ngoài CI**, khi spec đổi | Plan bịa task hoặc bỏ sót vùng | **Người duyệt DIFF** (DG-1), không duyệt từng test |
| `findings[].rationale` của Midscene | Discovery lane | Nhận xét sai → finding nhiễu → V4 (nghĩa địa) | `gating: false` + trần cứng số finding/run + tự tắt lane nếu không ai triage |
| G-Eval của DeepEval | Advisory | Position bias **mạnh nhất đúng lúc hai phương án gần nhau** = **đúng vùng regression testing** | Không gate theo điểm tuyệt đối; chỉ theo delta; judge ≠ model của SUT |
| `failure-analysis` khi gate đỏ | Chỉ khi đỏ | Giả thuyết **nghe hợp lý mà sai**; suy nhân quả từ tương quan | Không hành động tự động; ticket là **draft** |

**Rủi ro vận hành đã biết trước (V1–V5)** `[R4 S15.1]` `[R5 4.1]`:

| # | Kịch bản | Dấu hiệu **sớm** | Chặn |
|---|---|---|---|
| **V1** | **Contract bị Midscene ép méo** — *xác suất cao nhất*. Ngày 2, ai đó thêm `if worker == "midscene"` vào lõi. **Đúng 30 giây đó, N4 và N5 chết** | Tên worker xuất hiện trong `core/`, **hoặc** nghe câu "thêm một trường nhỏ chỉ cho Midscene" | **Viết adapter Midscene TRƯỚC** (slot 3, không phải slot 4). Dừng 15 phút, cả 3 quyết định: sửa schema cho **cả 4** worker, hoặc để adapter **mất thông tin**. **CẤM** thêm trường riêng |
| **V2** | **Gate xanh vì worker không chạy.** Không có provider chấm dùng được (OpenAI và Gemini đều thiếu/lỗi) | Không có — đó là vấn đề | STEP 02 thử primary rồi fallback; registry không thể biểu diễn `one-of` cho env nên worker chạy deterministic metrics với `requires.env: []`. Nếu G-Eval không chạy, ghi rõ `error`/`mock`, không giả là điểm thật; deterministic verdict vẫn chỉ dựa trên các metric gating |
| **V3** | **Plan mục** | Số endpoint trong app > số task trong plan | Job định kỳ chạy planner, xuất diff, mở PR tự động. **Bắt buộc**, không phải tuỳ chọn |
| **V4** | **Discovery lane thành nghĩa địa** | Hàng chờ triage tăng đơn điệu | Trần cứng số finding/run; ưu tiên `deterministic_assert`; **tuần nào không ai triage thì tự động TẮT và báo** |
| **V5** | **Chi phí VLM trôi.** UI đổi → agent đi vòng → số bước ×3 → hoá đơn ×3 mà **vẫn "pass"** | `cost.tokens` tăng giữa hai run cùng plan | `budget` là trần **cứng** trong task spec; `cost` lên report **kể cả khi xanh** |

## 9.3 `[CHƯA VERIFY]` — không dòng nào được code như thể nó đúng

Rút từ bảng double-check của `[R4]` + `[R5 (a)]`, giữ những dòng **ảnh hưởng tới code**:

k6 exit code đã được xác minh trên v2.2.0 và được ghi ở `docs/decisions.md`, nên không còn là
mục chưa kiểm: script threshold fail exit 99 có summary; script cú pháp sai exit 107, không có
summary. Adapter STEP 31 dùng script không threshold và xử lý exit khác 0 như lỗi worker.

| # | Claim chưa kiểm | Kiểm bằng cách nào (≤10 phút) | Sai thì hỏng gì |
|---|---|---|---|
| 1 | **DeepEval có export JSON/JUnit không** | `deepeval test run` rồi xem `DEEPEVAL_RESULTS_FOLDER` | Adapter phải parse stdout — xấu nhưng làm được |
| 2 | **Midscene `--summary` JSON đủ trường để sinh `findings[]`** (doc-verified, **chưa chạy**) | Chạy 1 file YAML, mở file summary | **Rủi ro V1 tăng mạnh** |
| 3 | **Midscene `aiAssert` fail có làm exit code ≠ 0** | Viết assert chắc chắn sai | Ảnh hưởng quyết định lane của Midscene |
| 4 | **Schemathesis chạy được trên Windows không cần WSL** | `pip install schemathesis && st --version` | **Mất worker PoC dễ nhất** |
| 5 | **Schemathesis pin seed được** | Chạy 2 lần, so | **Tiêu chí #4 (tái lập) phải hạ chuẩn** |
| 6 | Consumer JUnit XML chuẩn **không** gate trên `<properties>` tuỳ biến | Xuất JUnit XML có property lạ, nạp vào ReportPortal/GitHub Actions | Luận điểm chính của prior art yếu đi (không sụp) |
| 7 | **Assertion node của Hercules là LLM hay tất định** (`FROM-INDEX`, **chưa đọc code**) | Đọc code node đó | Nếu nó tất định, luận điểm phân biệt phải **hẹp lại thêm một bậc** — phải kiểm **trước khi lên slide** |
| 8 | **4 worker PoC cắm chung một contract chạy được** | Đây là toàn bộ nội dung PoC | Nếu sai, bản propose **không có demo** |
| 9 | Ước lượng setup cost (10–45 phút/worker) và dòng code (680/110) | Ngày 1 sprint | Kế hoạch 2.5 ngày trượt |

## 9.4 Câu hỏi còn treo — cần quyết sau sprint

| # | Câu hỏi | Vì sao chưa quyết được bây giờ |
|---|---|---|
| **Q1** | **Lỗi cài sẵn #3 là ngẫu nhiên hay tất định?** (mục 0.1 M1) | ⛔ **Phải quyết TRONG slot 1**, không phải sau sprint — nó quyết định 5 golden case |
| **Q2** | Exit code cho gate **vàng** (`skipped`) | Nguồn không nói. Tạm dùng `exit 0` + in đầu report (**[SUY RA] G1**). ⚠️ Nhưng đây **chính là** V2 — nếu CI của team không đọc report, gate vàng **im lặng như gate xanh**. Cần quyết: có nên `exit 2` riêng cho vàng? |
| **Q3** | Evidence để ở **đâu**, giữ **bao lâu**, **ai dọn** | PoC dùng thư mục local. Ngoài PoC đây là câu hỏi thật `[R4 (a)#6]` |
| **Q4** | **Bảo mật chính orchestrator** — nó cầm credential của **mọi** worker, là điểm tập trung rủi ro lớn nhất trong hệ | Sprint dùng `.env` local + `.gitignore`; nêu thành hạn chế `[R3 S11(4)]` `[R4 (a)#7]` |
| **Q5** | Ngưỡng **"tái lập ≥3/3 lần"** để auto-promote | *"Con số tôi chọn, không phải con số đo được"* `[R4 (a)#5]` |
| **Q6** | `metrics` dạng phẳng có sống nổi với DeepEval không | *"Nhiều khả năng sẽ phải đổi khi gặp DeepEval"* `[R4 (a)#3]` |
| **Q7** | Ràng buộc `confidence = null` khi `deterministic_assert` — có worker nào bị gò khó chịu vì nó không | *"Sạch về lý thuyết, chưa biết"* `[R4 (a)#4]` |
| **Q8** | Đường di cư sang **Testkube** (execution) + **ReportPortal** (gom/triage) | Kiến trúc dài hạn đúng nhưng cần K8s. Registry của ta ↔ Testkube CRD; report của ta ↔ ReportPortal `[R4 S14.3]`. **Báo cáo phải nêu đường này, không giả vờ sẽ thay thế họ** |
| **Q9** | Có nên fork **Hercules** cho discovery lane không | Nó là **lựa chọn hợp lệ** cho lane đó `[R5 CORRECTION]`; vướng **AGPL-3.0** ⟹ hỏi pháp chế nếu có ý định phân phối |
| **Q10** | Chi phí thật **ở quy mô suite thật** | Số duy nhất ta có là toy app: Midscene ~118k token ≈ $0.31 cho 15 bước `[R5 Q4]`. **Không ngoại suy** |

---

## Phụ lục A — Ba thứ KHÔNG BAO GIỜ CẮT `[R5 4.2]`

Khi chậm, cắt từ trên xuống: `Selection` → `plan-gen` → chạy song song → canary →
`promote_candidate` → DeepEval thành mock → Midscene thành run ghi sẵn.

**Nhưng ba thứ này cắt là mất toàn bộ phần mới của bản propose:**
1. **Contract + validate schema**
2. **Tách 3 mục theo `verdict_source` trong report**
3. **Tiêu chí tái lập (chạy 2 lần, diff rỗng)**

> *Cắt ba cái này thì còn lại là một script gọi 4 tool — và mentor sẽ nói đúng như vậy.*

**Minimum shippable** `[R5 4.3]`: orchestrator + Schemathesis + k6 **chạy thật** + DeepEval
**mock** + report tách 3 mục + chạy 2 lần verdict giống hệt.
*Bốn worker dở dang tệ hơn hai worker chạy thật + hai mock trung thực* — và **phải nói rõ trên
slide cái nào là mock**.

## Phụ lục B — Sáu tiêu chí nghiệm thu PoC (nhị phân) `[R5 2.3]`

| # | Tiêu chí | Cách kiểm |
|---|---|---|
| 1 | Chạy bằng **một lệnh**, exit code phản ánh verdict | `orchestrate run --plan plan.yaml; echo $LASTEXITCODE` → `1` khi fail, `0` khi xanh |
| **2** | **Cả 4 adapter validate qua CÙNG một `result.json`, không adapter nào cần trường riêng** | Validator trên 4 file result → **4/4 pass**. ⭐ **Đây là tiêu chí trung tâm — nó chính là N4** |
| 3 | Report tách đúng 3 mục theo `verdict_source`; verdict tổng **chỉ** tính `deterministic_assert` | Đổi kết quả G-Eval thành fail → **verdict tổng không đổi** |
| 4 | **Tái lập** | `diff run1/report.json run2/report.json` sau khi lọc `llm_judgment` + timestamp → **rỗng** |
| 5 | **N5** | `git show --stat` commit thêm worker → **`core/` không xuất hiện** |
| 6 | **Canary** | Worker tự hành báo `fail` cho task bất khả thi. **Nếu nó báo pass ⟹ ta phát hiện được self-assessment hỏng — và việc phát hiện được đó CŨNG là thành công** |

**Ngưỡng ship:** đạt **1, 2, 3, 4** = thành công, present được. Thêm **5, 6** = present mạnh.
**Không đạt #2 = kiến trúc chưa được chứng minh, dù chạy đẹp đến đâu.**
