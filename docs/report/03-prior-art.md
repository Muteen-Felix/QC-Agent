# 03 — PRIOR ART: cái gì đã có rồi

**Nguồn:** `[R4 S14.2]` + **`[R5 CORRECTION]`** (bản đúng — đã sửa một claim sai của RUN 4).
Số liệu GitHub truy cập **2026-09-18** qua `api.github.com`.

> **Đọc mục này trước mục kiến trúc.** Nếu bản propose không nêu Hercules, mentor tìm 10 phút là
> ra, và toàn bộ độ tin cậy sụp. Đây là mục ghi điểm tin cậy, không phải mục phòng thủ.

---

## 3.0 Trả lời thẳng câu hỏi trung tâm

**Đã có OSS nào làm sẵn tầng orchestration cho QC chưa?**

**CÓ. Có ba, và một trong ba gần như chính là kiến trúc chúng tôi đề xuất.** Chúng tôi đã phải
sửa lại định vị của bản propose sau phát hiện này `[R4 S14.2]`.

Vì vậy luận điểm của chúng tôi **hẹp hơn nhiều** so với bản nháp đầu, nhưng nó thật:

> Ba prior art trên **chia nhau** ba mảnh của bài toán và **không mảnh nào mang được
> `verdict_source` đi tới báo cáo**. Hercules có tầng lập kế hoạch nhưng đặt LLM trong đường phán
> quyết. Testkube mở với mọi worker nhưng không lập kế hoạch và không phân biệt loại phán quyết.
> ReportPortal phân tích lỗi nhưng đứng **sau**, không điều khiển. **Không cái nào coi AI
> application là đối tượng kiểm thử hạng nhất.**

---

## 3.1 Bốn prior art — từng cái làm tới đâu

### (a) TestZeus Hercules — `test-zeus-ai/testzeus-hercules`

| | |
|---|---|
| **Nó là gì** | "Testing agent" chạy trên **LangGraph state machine**: node **Planner → Executor → Assertion**, với các nav agent chuyên biệt (browser, api, sec, sql, time_keeper, mcp). Gherkin vào, **JUnit XML + HTML ra**. Phủ UI / API / Security (qua Nuclei) / Accessibility (WCAG 2.0–2.2) / Visual |
| **Nhãn** | `VERIFIED` |
| **Số liệu** | AGPL-3.0 · Python · push 2026-08-04 (~6 tuần) · created 2024-11 · 44 issue mở · 190 fork · 15 watcher |
| **Làm tới đâu** | **Gần như chính là kiến trúc của chúng tôi, đã build xong.** Planner/executor/assertion ↔ orchestrator/worker/oracle. Chi phí công bố ~**$0.20/kịch bản phức tạp** (gpt-4o) |

**Hercules cắm được tool bên thứ ba — nói rõ để không nói sai về họ.** RUN 4 viết rằng Hercules
"không có registry cho tool bên thứ ba". Đó là điều **suy ra từ README, chưa đọc code**, và đã
được kiểm lại trong RUN 5: **claim đó SAI.** `VERIFIED`
[`docs/MCP_Usage.md` của repo + doc dự án, truy cập 2026-09-18]:

- Hercules có **`ADDITIONAL_TOOL_DIRS`** để nạp tool tự viết.
- Hercules có **MCP Navigation Agent**, hỗ trợ transport **stdio / sse / streamable-http** nên
  kết nối được tới MCP server bất kỳ.

**Ranh giới đúng không phải "đóng vs mở" — mà là VỊ TRÍ CỦA LLM:**

| | **Hercules** | **Kiến trúc đề xuất** |
|---|---|---|
| Tool bên thứ ba | ✅ cắm được (`ADDITIONAL_TOOL_DIRS` / MCP) | ✅ cắm được (adapter + manifest) |
| **Vị trí của LLM** | Planner + Assertion node nằm **TRONG** đường chạy của **mọi** run | LLM nằm **NGOÀI** đường chạy CI; gate xanh = 0 lời gọi LLM |
| **Nguồn phán quyết** | Mọi kết quả chảy qua assertion node rồi ra **JUnit XML** nên **không phân biệt được** assert tất định với phán quyết LLM | `verdict_source` ở cấp **từng finding**; verdict tổng **chỉ** tính `deterministic_assert` |
| **Plan** | Planner chạy **mỗi lần** nên không tái lập | Plan là artifact **đã commit** nên tái lập |
| **AI application** | Không phải đối tượng hạng nhất | Là một `capability` như mọi capability khác |

> **Nhãn trung thực cho dòng "assertion node là LLM":** suy ra từ kiến trúc LangGraph
> planner/executor/assertion + input Gherkin. `FROM-INDEX`, **chưa đọc code node đó**. Nếu
> assertion node hoá ra là tất định thì cột 3 của bảng trên phải sửa, và luận điểm phân biệt của
> chúng tôi phải **hẹp lại thêm một bậc**. Đây là dòng đầu trong danh sách cần double-check trước
> khi lên slide `[arch §9.3 #7]`.

### (b) Testkube — `kubeshop/testkube`

| | |
|---|---|
| **Nó là gì** | **Test orchestration platform**: chạy **bất kỳ tool nào đã container hoá** (k6, Playwright, Cypress, JMeter, Postman, Selenium) trong Kubernetes; gom kết quả/log/artifact về một control plane. Workflow/trigger/webhook là **CRD của K8s** nên hợp GitOps |
| **Nhãn** | `VERIFIED` |
| **Số liệu** | **NOASSERTION** (thực tế: MIT + Testkube Community License cho phần enterprise) · Go · push 2026-09-18 · created 2021 · 66 issue mở · 11 watcher |
| **Làm tới đâu** | **Đây chính là phần "registry + thực thi + gom kết quả", đã build xong và tốt hơn chúng tôi làm được trong 2.5 ngày** |
| **Thiếu gì** | (1) không có tầng lập plan từ intent; (2) **không có khái niệm `verdict_source`**; (3) không có discovery lane / đường promote; (4) **buộc phải có Kubernetes** |

### (c) ReportPortal — `reportportal/reportportal`

| | |
|---|---|
| **Nó là gì** | Gom kết quả từ mọi framework về một nơi + **Auto-Analysis bằng ML**, phân loại lỗi thành **Product Bug / Automation Bug / System Issue** trước khi người nhìn |
| **Nhãn** | `VERIFIED` |
| **Số liệu** | Apache-2.0 · push 2026-08-20 · created **2016** (10 năm) · 450 issue mở · 100 watcher · 504 fork |
| **Làm tới đâu** | Bước **failure analysis** — thứ chúng tôi kết luận là nơi LLM tạo giá trị rõ nhất — **đã có sản phẩm OSS trưởng thành 10 năm làm bằng ML** |
| **Thiếu gì** | (1) chỉ phân tích *sau khi chạy*, không điều phối; (2) không lập plan; (3) không biết AI app; (4) triển khai nặng (Docker Compose nhiều service) |

### (d) Playwright Test Agents `[R7]` — prior art **ủng hộ** hướng của chúng tôi

| | |
|---|---|
| **Nó là gì** | 3 agent built-in: **Planner** (xuất Markdown plan vào `specs/`), **Generator** (plan thành test trong `tests/`), **Healer** (trả **code patch** cho test hỏng, không sửa lúc runtime). Khởi tạo bằng `npx playwright init-agents`; sinh cấu hình MCP cho Claude Code / Codex / VS Code / OpenCode |
| **Nhãn** | `VERIFIED` (doc, 2026-09-18) — **chưa đọc code** |
| **Nguồn** | https://playwright.dev/docs/test-agents |
| **Làm tới đâu** | **Prior art trực tiếp cho plan-as-artifact** và cho nguyên tắc "LLM ở design-time, runtime tất định". Nó **ủng hộ** hướng của chúng tôi, không phải đối thủ |
| **Thiếu gì** | Chỉ trong **một** framework (Playwright); không có verdict policy xuyên nhiều loại worker; **không có `verdict_source`**; không có AI-app eval |

**Vì sao mục này quan trọng cho tính trung thực của bản propose:** Playwright Test Agents **đã**
đặt LLM ra ngoài runtime — trong phạm vi một framework. Vì vậy câu "chưa có ai đặt LLM ngoài
đường phán quyết" là **quá mạnh và chúng tôi không dùng nó** `[R7]`. Câu đúng, hẹp hơn:

> **Chưa có ai áp nguyên tắc đó xuyên nhiều loại worker kèm `verdict_source`, đặc biệt cho AI
> application.**

---

## 3.2 Điểm thiếu hụt chí mạng chung: **không có verdict provenance**

Đây là chỗ thiếu hụt duy nhất mà cả bốn prior art đều có, và nó là lý do tồn tại của bản propose
này.

**Cơ chế kỹ thuật của chỗ thiếu hụt** `[R4 S14.2]`:

1. Hercules và Testkube đều **hội tụ về JUnit XML** làm mẫu số chung.
2. Schema JUnit có `testcase` / `failure` / `error` / `skipped` và `properties`. Về mặt lý thuyết
   **nhét được** `verdict_source` vào `<properties>`.
3. Nhưng **không consumer chuẩn nào (CI UI, dashboard) đọc và gate trên trường tuỳ biến đó**
   `[CHƯA VERIFY — arch §9.3 #6]`.
4. Kết quả: trên thực tế, một verdict LLM và một assert tất định **vào JUnit XML là không phân
   biệt được nữa**. Sau bước đó, không ai — kể cả tác giả hệ — trả lời được câu hỏi *"cái chặn
   merge PR này là một phép so số, hay là ý kiến của một model?"*

Trong RUN 3, **không tool nào trong 14 ứng viên được khảo sát phát ra `verdict_source`**
`[R4 S14.1]`.

**Phân loại chỗ thiếu hụt — để biết cái nào là bài toán tooling, cái nào là bài toán kỹ thuật:**

| Gap | Loại | Bằng chứng |
|---|---|---|
| **Verdict provenance xuyên suốt tới report** | **TOOLING** | Không tool nào trong 14 của RUN 3 phát ra `verdict_source` |
| **Contract chung cho worker không đồng nhất** | **TOOLING** | Mỗi tool một schema; JUnit XML là mẫu số chung nhưng **quá nghèo** |
| **SUT identity đa chiều cho AI app** | **TOOLING** | Không tool nào pin **đồng thời** prompt hash + model snapshot + corpus id |
| **Hai lane gate/discovery + đường promote** | **PRODUCT** | Đủ mảnh rời, chưa ai ghép |
| **Một verdict thống nhất cho cả phần mềm thường lẫn AI app** | **PRODUCT** | Testkube/ReportPortal không biết AI app; DeepEval/promptfoo không biết k6 |
| Agent tự chấm pass/fail cho E2E | **KỸ THUẬT** | Self-assessment: sai ở cả hai đầu và hai cái sai **che nhau** |
| LLM-judge dùng được làm ngưỡng tuyệt đối | **KỸ THUẬT** | Position bias **mạnh nhất đúng lúc hai phương án gần nhau** — tức đúng vùng regression testing |

Bốn gap đầu là chỗ một team 3 người **lấp được trong 2.5 ngày**, vì chúng là **quyết định thiết
kế, không phải khối lượng code** `[R4 S14.3]`. Ba gap cuối là giới hạn kỹ thuật — không ai lấp
được bằng cách code nhiều hơn.

---

## 3.3 Vì sao không dùng thẳng — và Hercules thuộc lane nào

### Hercules là công cụ TỐT cho Discovery lane, KHÔNG phù hợp làm Gatekeeper tự động

Đây là kết luận, không phải lời từ chối lịch sự:

> **Hercules là lựa chọn hợp lệ cho discovery lane** và phải được nêu như vậy trong báo cáo — nó
> có thể **thay** Explorbot/Midscene nếu team muốn một công cụ phủ rộng hơn (UI + API + security +
> a11y + visual trong một lần chạy) `[R5 CORRECTION]`.

**Vì sao nó phù hợp discovery lane:** lane này **không yêu cầu tái lập**, không chặn merge, và
giá trị của nó là **sinh ứng viên** cho gate lane. Một planner LLM chạy mỗi lần là **đúng thứ cần
có** ở đó — nó khám phá được vùng mà plan tĩnh không mô tả.

**Vì sao nó không phù hợp làm gatekeeper tự động** — ba lý do, đều là lý do kỹ thuật:

| # | Lý do | Hệ quả cụ thể |
|---|---|---|
| 1 | **Planner chạy mỗi lần** nên plan không phải artifact | Hai lần chạy **cùng một commit** có thể chạy hai tập test khác nhau. PR pass vì worker đúng đã **không được gọi** — và **không có gì đỏ để ai đó phát hiện** `[R1 S4e3]` |
| 2 | **Assertion node nằm trong đường chạy mọi run** | Phương sai LLM đi thẳng vào cái chặn merge. Gate đỏ không tái lập nên dev chạy lại là xanh, rồi **team học cách bấm re-run**, và gate mất hết giá trị `[arch §6.3]` |
| 3 | **Output là JUnit XML** | Không mang được `verdict_source` (xem 3.2) nên không ai phân biệt được cái chặn merge là assert hay là ý kiến model |

**Cộng thêm hai rào phi kỹ thuật, phải nói ra:** **AGPL-3.0** (cần hỏi pháp chế nếu có ý định
phân phối `[arch Q9]`), và nó **không cắm được k6/DeepEval/Schemathesis vào vị trí worker** theo
cách contract của chúng tôi cần — worker của Hercules là nav agent nội bộ, được mở rộng qua
`ADDITIONAL_TOOL_DIRS`/MCP chứ không qua một manifest khai `lane` + `verdict_sources` +
`data_egress`.

### Testkube và ReportPortal: không phải "không dùng" — mà là "chưa dùng trong sprint này"

| Phương án | Đánh giá |
|---|---|
| **Dùng thẳng Hercules làm gate** | ❌ Ba lý do ở bảng trên + AGPL-3.0. ✅ **Nhưng phải nêu như prior art và nói rõ vì sao không dùng** — không nêu là lỗ hổng lớn nhất của bản propose `[R4 (b)]` |
| **Dựng Testkube + ReportPortal rồi viết lớp mỏng ở trên** | ✅ **Đúng về kiến trúc dài hạn.** ❌ `[KHÔNG KỊP SPRINT NÀY]` — cần Kubernetes + nhiều service Docker; team đang trên Windows với 2.5 ngày. Đây là **lộ trình**, không phải **sprint** — xem `09-roadmap.md` |
| **Tự build lớp orchestrator mỏng cho PoC** | ✅ cho sprint — nhưng **phải định vị là "thin contract layer", không phải "một orchestrator mới"**, và phải nói rõ đường di cư: registry của chúng tôi ↔ Testkube CRD; report của chúng tôi ↔ ReportPortal |

### Gap nào chúng tôi CHỦ ĐỘNG không lấp

| Gap | Lấp? | Vì sao |
|---|---|---|
| Contract có `verdict_source` + adapter cho 4 worker | ✅ **Và đây là toàn bộ giá trị** | Phạm vi nhỏ; là quyết định thiết kế chứ không phải khối lượng code |
| Chính sách hai lane + đường promote | ✅ | Chủ yếu là chính sách, ít code |
| Plan-as-artifact + run signature | ✅ | Vài chục dòng + kỷ luật |
| SUT identity đa chiều cho AI app | ✅ bản tối thiểu | Là một file JSON có hash |
| Execution engine đa tool, đa môi trường | ❌ **Không nên** | **Testkube** đã làm 5 năm. Chúng tôi sẽ ra bản tệ hơn |
| ML/LLM triage lỗi | ❌ **Không nên** | **ReportPortal** có model huấn luyện trên dữ liệu thật; chúng tôi không có dữ liệu đó |
| Agent lái UI | ❌ **Không nên** | **Midscene / Hercules** đã làm. *Fork, đừng viết* |
| Judge calibration harness | ⚠️ | Cần tập nhãn human — việc nhiều tuần. `[KHÔNG KỊP SPRINT NÀY]` |
| Device farm / hạ tầng mobile | ❌ Không nên | Bài toán **hạ tầng**, không phải bài toán QC |

---

## 3.4 Ba câu chúng tôi CỐ Ý không nói

Ghi lại ở đây để người review biết đây là lựa chọn có ý thức, không phải sơ suất `[R11]`:

Ba câu đó **không xuất hiện ở bất kỳ đâu trong bộ báo cáo này**, kể cả dưới dạng trích dẫn — nên
bảng dưới **mô tả** chúng thay vì viết lại nguyên văn.

| # | Loại khẳng định bị loại | Vì sao nó sai | Câu đúng đã dùng thay thế |
|---|---|---|---|
| 1 | Khẳng định rằng **chưa có ai xây tầng orchestration cho QC** | Hercules, Testkube, ReportPortal đều đã làm — một trong ba gần như chính là kiến trúc này | "Ba prior art chia nhau ba mảnh; **không mảnh nào mang được `verdict_source`**" (§3.0) |
| 2 | Khẳng định rằng **Hercules là hệ khép kín, không cắm được tool bên thứ ba** | Sai về thực tế: có `ADDITIONAL_TOOL_DIRS` + MCP nav agent (stdio/sse/streamable-http) | "Ranh giới đúng là **vị trí của LLM**, không phải khép kín vs mở rộng được" (§3.1a) |
| 3 | Khẳng định rằng **chưa có ai đặt LLM ra ngoài đường phán quyết** | Playwright Test Agents đã làm điều đó, trong phạm vi một framework | "Chưa có ai áp nguyên tắc đó **xuyên nhiều loại worker kèm `verdict_source`**, đặc biệt cho **AI application**" (§3.1d) |
