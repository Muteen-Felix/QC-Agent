# 09 — LỘ TRÌNH: bốn pha mở rộng

**Nguồn:** `[R5 P5.1 mục 9]`, `[R10]`, `[arch D2, §8.1, §8.2, Q8]`. Không có nghiên cứu mới.

---

## 9.0 Cách đọc lộ trình này

Ba quy tắc, để bảng bên dưới không bị đọc thành một lời hứa:

1. **Mỗi pha được đo bằng "phần contract/policy ta thêm vào", không bằng số tool cắm thêm.** Cắm
   thêm một tool là ~1.5 giờ và **0 dòng trong `core/`** — đó là việc đã giải. Cái đắt là khi một
   pha đòi **một `oracle.kind` mới**, **một `verdict_source` thứ tư**, hoặc **một cách bóc state
   mới**.
2. **Không pha nào được phá N4/N5.** Nếu một pha chỉ khả thi bằng cách thêm trường riêng cho một
   worker vào `result.json`, thì pha đó **chưa được thiết kế xong** — không phải chưa đủ thời gian.
3. **Ước lượng thời gian là phán đoán kỹ thuật, không phải số đo** `[arch §8.2]`. Chúng có thể lệch
   **hệ số 2**. Đừng đưa con số chính xác lên slide.

### Một điểm chúng tôi không đồng ý với thứ tự mặc định — nói trước

Thứ tự **Web/API → Mobile → Performance → AI Eval** là thứ tự theo *bề mặt kiểm thử*. Xét theo
**giá trị trên chi phí** thì nó không tối ưu, và người quyết nên biết điều đó:

- **Performance (Pha 3) và AI Eval (Pha 4) đã chạy thật trong PoC** (`k6`, `deepeval`). Chúng là
  bài toán **mở rộng và vận hành**, chi phí biên thấp.
- **Mobile (Pha 2) là pha đắt nhất và là pha duy nhất không phải bài toán QC.** Nó đòi cột phải của
  bảng tài sản/chi phí biên: **cách bóc UI state phải viết lại từ đầu** (a11y tree / UiAutomator /
  logcat thay cho DOM), cộng chi phí hạ tầng emulator/device farm — *"bài toán hạ tầng, không phải
  bài toán QC"* `[R4 S14.3]`.

**Khuyến nghị:** nếu tổ chức chưa có device farm chạy được, **đổi Pha 2 và Pha 3** — làm perf
nightly và AI-eval liên tục trước, mobile sau. Nếu mobile là ràng buộc sản phẩm (app là kênh chính)
thì giữ nguyên thứ tự và **tính chi phí hạ tầng vào pha đó ngay từ đầu**, không tính sau.

Bảng dưới trình bày theo thứ tự đã yêu cầu; cột cuối ghi rõ pha nào đổi chỗ được.

---

## 9.1 Bảng bốn pha

| | **Pha 1 — Web + API** | **Pha 2 — Mobile** | **Pha 3 — Performance** | **Pha 4 — Continuous AI Eval** |
|---|---|---|---|---|
| **Mục tiêu** | Gate PR thật trên **1–2 repo pilot** | Phủ bề mặt Android | Perf thành tín hiệu **nightly có xu hướng**, không phải một lần chạy | Chất lượng AI feature được theo dõi **liên tục**, gate được theo **delta** |
| **Trigger** | PR webhook (mỗi PR) | nightly (không gate PR ở pha đầu) | nightly + so baseline | nightly + khi prompt/model/corpus đổi |
| **Worker thêm vào** | `schemathesis`, `k6`, `http-collect` (đã có) + **axe-core** (a11y) + tuỳ chọn **Keploy** (record–replay) | `agent-device` / **Maestro** (cần emulator) | `k6` mở rộng (đã có) + Locust nếu cần | `deepeval` mở rộng (đã có) + Ragas/promptfoo nếu cần retrieval |
| **PHẦN CONTRACT / POLICY TA THÊM VÀO** | • `oracle.kind: rule_violations` (cho axe) — **thêm FILE vào `oracle/`, không sửa logic điều phối**<br>• Khối `selection` thật: `floor ∪ (impact ∩ plan) ∪ always_on` + **`coverage_map` thật** thay bảng tay<br>• Luật `always_on` theo risk: `auth/** ⟹ security.*`, `payment/** ⟹ security.* + http.load`<br>• Job định kỳ chạy `plan-gen` → xuất diff → **tự mở PR** (chặn V3) | • **`capability` mới**: `mobile.ui`, `mobile.explore`<br>• **`target.kind: android_app`** + định nghĩa `sut_identity` cho app build (APK hash, versionCode)<br>• **`oracle.kind: crash_signals`** — logcat crash/ANR là **oracle tất định mạnh và miễn phí**<br>• Chính sách device: `parallel_safe: false` theo **thiết bị**, không theo worker → cần khái niệm **resource lock** trong runner | • **`baseline store`** — cấu phần #3 trong 7 cấu phần, PoC **chưa có**<br>• `oracle.kind: threshold_delta` — gate theo **delta so baseline đã pin**, không theo ngưỡng tuyệt đối<br>• Chính sách flaky/nhiễu: **quarantine có hạn 14 ngày**, tự mở lại + tự tạo task điều tra<br>• `cost` và `wallclock` thành **assert hạng nhất** có xu hướng, không chỉ là số in ra | • **`baseline store` cho điểm judge** (dùng lại từ Pha 3)<br>• **`sampling policy`** — cấu phần #5, PoC **chưa có**<br>• **`judge calibration harness`** — cấu phần #4; cần tập nhãn human, **việc nhiều tuần**<br>• **Pin model snapshot id** trong `sut_identity` (chặn regression im lặng khi provider đổi model sau cùng alias)<br>• Luật: judge **≠** model của SUT, cưỡng chế bằng `status: error` chứ không cảnh báo |
| **Cái gì KHÔNG đổi** | `result.json`, `task_spec.json`, `core/`, phép gộp verdict, 2 lane | `result.json`, `core/`, phép gộp verdict | `result.json`, `core/` | `result.json`, `core/` |
| **Rủi ro chính của pha** | **V3 plan mục** — app có endpoint mới không nằm trong plan; gate vẫn xanh, vẫn tái lập, **vẫn vô nghĩa** | **Chi phí hạ tầng lấn hết giá trị QC.** Đây không phải rủi ro kỹ thuật, là rủi ro phân bổ nguồn lực | So sánh xuyên môi trường vô nghĩa nếu runner không đồng nhất — **perf cần môi trường pin, không chỉ seed pin** | **V4 nghĩa địa** ở dạng khác: 200 điểm judge/tuần không ai đọc. Và **gian lận baseline** (sửa chuẩn cho test pass) — điểm HITL #4, **không tự động hoá** |
| **Điều kiện ra** | Gate PR chạy trên repo pilot ≥2 tuần; `plan-gen` chạy **≤1 lần/tuần** | Canary mobile báo `fail` đúng; crash/ANR bắt được | Gate theo delta chạy được, không phải theo ngưỡng viết tay | Judge được hiệu chuẩn trên tập nhãn human; delta gate hoạt động |
| **Đổi chỗ được?** | ❌ phải là pha 1 | ✅ hoãn được nếu chưa có device farm | ✅ đưa lên trước Pha 2 được | ✅ đưa lên trước Pha 2 được |

---

## 9.2 Ghi chú riêng từng pha

### Pha 1 — hai lưu ý không được bỏ

**(a) Keploy cần WSL2 hoặc Docker trên Windows.** Keploy là worker record–replay (nhóm G2) và là
**đường nhanh nhất để có coverage tích hợp cho hệ legacy** `[R3 (a)#3]` — đúng thứ một team QC thật
cần nhất, và là thứ PoC **đang thiếu**. Nhưng rào của nó là **eBPF**: Linux kernel ≥ 5.10; trên
Windows phải **WSL2 (Ubuntu 22.04)**, hoặc **Docker Desktop**, hoặc native chỉ-AMD-cần-admin. Ước
tính **nửa ngày** setup `[R3 9.5]`.

Vì vậy: **đặt Keploy là tuỳ chọn của Pha 1, không phải bắt buộc.** Nó vào khi team quyết dựng
WSL2/Docker **vì lý do khác** — lúc đó rào của nó đã được trả tiền.

Và một giới hạn của record–replay phải nói cùng lúc: **replay khoá chặt hành vi hiện tại, kể cả
hành vi sai** `[R2 S5-3]`. Nó không phát hiện bug, nó đóng băng nguyên trạng.

**(b) `axe-core` là kịch bản chuẩn để chứng minh N5.** Thêm nó tốn ≈**1.5 giờ**, phân rã thành 5
việc gắn với 5 file cụ thể, **0 dòng trong `core/`** `[arch §8.2]`. Và nói luôn giới hạn của nó:
con số "57% issue a11y bắt được" là của **chính vendor** (Deque làm ra axe-core), đo theo *số lượng*
issue ⟹ **thổi phồng** mức phủ. **43% còn lại rule engine không bắt được** `[R2 S5]`.

### Pha 2 — vì sao mobile không phải "thêm một worker"

Đây là chỗ bảng tài sản/chi phí biên đổi cột. Cột phải phải **viết lại từ đầu** `[R2 S6]`:

| Việc | Web (đã có) | Android (phải viết mới) |
|---|---|---|
| **Bóc UI state** | DOM + a11y tree + screenshot | a11y tree / UiAutomator + **logcat** |
| **Oracle tất định của surface** | phải tự định nghĩa | **crash/ANR trong logcat** — mạnh và **miễn phí** |
| **Hạ tầng** | trình duyệt trên runner | **emulator hoặc device farm** |

Điểm sáng thật của Android: nó có một oracle tất định **mạnh và miễn phí** mà web không có. Đó là lý
do kỹ thuật để làm mobile, chứ không phải vì "cần phủ mobile".

**iOS và Desktop Win32 nằm ngoài lộ trình này** — bị loại vì **rào hạ tầng**, không vì rào kỹ thuật
(ký app / macOS runner; WinAppDriver mục nát) `[R2 S6]`.

### Pha 3 — perf nightly là bài toán baseline, không phải bài toán k6

k6 đã chạy thật trong PoC (`http_req_duration.p95`, `http_req_failed.rate`, `parallel_safe: false`).
Cái Pha 3 thêm vào **không phải tool** — là **baseline store** và `oracle.kind: threshold_delta`.

Lý do: ngưỡng tuyệt đối viết tay (`p95 < 300ms` trong `examples/task.k6.json`) là **ngưỡng demo cơ
chế, không phải số đo thật**. Trên một suite thật, ngưỡng tuyệt đối hoặc quá lỏng (không bắt gì)
hoặc quá chặt (đỏ vô cớ → team mất tin → gate bị tắt). Gate theo **delta so baseline đã pin** là
cách duy nhất khiến con số perf dùng được lâu dài.

Cộng một điều kiện dễ bị bỏ: **perf cần môi trường pin, không chỉ seed pin.** So p95 giữa hai runner
khác cấu hình là so hai thứ khác nhau. `run_signature` hiện đã gồm plan + SUT identity + worker
version + seed; với Pha 3 nó phải gồm **định danh môi trường đo**.

### Pha 4 — AI Eval liên tục là pha khó nhất về phương pháp, không về code

Ba trong bảy cấu phần của AI-app testing **chưa có trong PoC** và cả ba đều ở đây: **baseline store**
(#3), **judge calibration** (#4), **sampling policy** (#5) `[arch D5]`.

**Cái đắt nhất là judge calibration**, và nó đắt vì lý do không thể mua đường tắt: nó cần **tập nhãn
human**. Đó là việc nhiều tuần, và đó cũng là lý do nó bị gắn `[KHÔNG KỊP SPRINT NÀY]` chứ không phải
"để sau cho gọn".

Hai luật phải giữ nguyên xuyên Pha 4, dù có calibration hay không:

1. **Không n-sample voting.** Nhân chi phí ×n để mua ổn định cho thứ vốn **không được chặn gate** —
   đổi chác sai `[R4 S13.5]`.
2. **Judge không bao giờ lật verdict tất định**, cả hai chiều. Nếu judge liên tục đúng mà assert sai
   ≥3 run, câu trả lời là **viết assert tất định mới**, **không** phải nới quyền cho judge
   `[R4 S13.4]`.

Và một cảnh báo cho ai định dùng Ragas/RAG metric ở pha này: **faithfulness một mình là mù trước
retrieval sai** — điểm 0.95 hoàn toàn tương thích với câu trả lời sai vì tài liệu đã hết hiệu lực.
Phải có cả **context recall/precision** `[R2 S7.7]`. Và **không dùng golden set tự sinh bằng LLM** —
đó là *đang đo model bằng chính model*; bắt đầu từ **vài chục case có nhãn đúng** hơn vài nghìn case
tự sinh `[R2 S7.3]`.

---

## 9.3 Đường di cư sang Testkube / ReportPortal

**Đây là phần bắt buộc của lộ trình, không phải phần lịch sự.** Chúng tôi không giả vờ sẽ thay thế
họ `[arch Q8]`.

```
   HÔM NAY (PoC)                        ĐÍCH DÀI HẠN
   ─────────────                        ────────────
   core/registry.py   ──────────────►   Testkube CRD (TestWorkflow / Trigger)
   core/runner.py     ──────────────►   Testkube execution trên K8s
   workers/*.yaml     ──────────────►   Testkube Workflow definition
   core/report.py     ──────────────►   ReportPortal launch + item
   failure-analysis   ──────────────►   ReportPortal Auto-Analysis (ML, không phải LLM)

   ╔══════════════════════════════════════════════════════════════════╗
   ║  GIỮ LẠI, KHÔNG DI CƯ — đây là lớp không ai có:                  ║
   ║    schemas/result.json  ·  verdict_source  ·  chính sách 2 lane  ║
   ║    phép gộp AND trên gating=true  ·  4 trạng thái  ·  canary     ║
   ╚══════════════════════════════════════════════════════════════════╝
```

**Vì sao di cư chứ không cạnh tranh:**

| Lớp | Ai làm tốt hơn | Bằng chứng |
|---|---|---|
| Execution engine đa tool, đa môi trường | **Testkube** | 5 năm trên K8s (created 2021). Chúng tôi sẽ ra bản tệ hơn |
| Gom kết quả + triage lỗi | **ReportPortal** | 10 năm (created 2016), model ML **huấn luyện trên dữ liệu thật** — chúng tôi không có dữ liệu đó |
| **Verdict provenance + chính sách phán quyết** | **không ai** | Không tool nào trong 14 ứng viên của RUN 3 phát ra `verdict_source` |

**Trigger để bắt đầu di cư** `[arch D2 Revisit trigger #2]`: team lên Kubernetes vì lý do khác. Lúc
đó chuyển execution sang Testkube và **giữ lại đúng lớp verdict provenance**.

**Rào kỹ thuật đã biết trước của bước di cư:** cả Testkube và ReportPortal đều hội tụ về **JUnit XML**
làm mẫu số chung, và JUnit XML **không mang được `verdict_source`** (xem `03-prior-art.md` §3.2). Vì
vậy khi di cư, `result.json` **không được** thay bằng JUnit XML — nó phải đi **song song**: JUnit XML
cho Testkube/ReportPortal tiêu thụ, `result.json` cho phép gộp verdict và cho audit. Nếu bước di cư
làm mất `result.json`, **nó làm mất toàn bộ phần mới của bản propose**.

`[CHƯA VERIFY]` — chưa kiểm rằng ReportPortal/GitHub Actions thực sự **không** gate được trên
`<properties>` tuỳ biến của JUnit XML `[arch §9.3 #6]`. Cách kiểm ≤10 phút: xuất JUnit XML có
property lạ, nạp vào ReportPortal/GitHub Actions. Nếu claim này sai, luận điểm prior art **yếu đi
(không sụp)** — và đường di cư sẽ **rẻ hơn** dự tính.

---

## 9.4 Ba việc phải làm ngay sau sprint, trước cả Pha 1

Không phải mở rộng — là trả nợ. Xếp theo mức nghiêm trọng:

| # | Việc | Vì sao không hoãn được |
|---|---|---|
| 1 | **Chứng minh N5 bằng bằng chứng** (`git show --stat` cho commit thêm worker thứ 5) | Tiêu chí nghiệm thu #5 **hiện chưa đạt** (STEP 44 chưa chạy). Nó là tiêu chí rẻ nhất trong sáu tiêu chí, và là tiêu chí duy nhất chứng minh chi phí biên ~110 dòng/worker là thật chứ không phải ước lượng |
| 2 | **Quyết exit code cho gate VÀNG (`skipped`)** | Hiện tạm dùng `exit 0` + in ở đầu report — **[SUY RA]**, nguồn không nói. Nhưng đây **chính là V2**: nếu CI của team không đọc report, **gate vàng im lặng như gate xanh**. Cần quyết: có nên `exit 2` riêng cho vàng? `[arch Q2]` |
| 3 | **Job định kỳ `plan-gen` → diff → tự mở PR** | Chặn V3 (plan mục). `[arch D2]` gọi nó là **bắt buộc, không phải tuỳ chọn** — vì nó là cái giá trực tiếp của plan-as-artifact |

Và một câu hỏi tổ chức, không phải câu hỏi kỹ thuật: **bảo mật chính orchestrator.** Nó cầm
credential của **mọi** worker — là điểm tập trung rủi ro lớn nhất trong hệ. Sprint dùng `.env` local
+ `.gitignore`; ngoài sprint đây là câu hỏi thật và **chưa có câu trả lời** `[arch Q4]`.
