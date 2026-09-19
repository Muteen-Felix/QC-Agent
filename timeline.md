# timeline.md — QC Agent PoC · 2.5 ngày × 3 người

**Nguồn:** [plan-execution.md](plan-execution.md) (RUN 7, 57 STEP) · [architecture.md](architecture.md) (RUN 6).
File này **không thêm STEP mới**, chỉ xếp 57 STEP lên trục giờ thật và nói chỗ nào không vừa.
**Cách dùng:** mở mục 2 mỗi sáng, mục 5 mỗi lần trễ, mục 3 ở 4 mốc đồng hồ.

> **Phân vai (cố định cho sprint này):** **Nghĩa = Role A** (Orchestrator & Contract) · **Đức = Role B** (Authoring & Governance) · **Huy = Role C** (Deterministic & Eval).
> Trong file này, "A", "B", "C" đứng một mình luôn chỉ đúng người trên: A = Nghĩa, B = Đức, C = Huy. Bảng phân vai (mục 1) và tiêu đề cột của bảng timeline (mục 2) ghi cả tên lẫn role.

---

## ⚠️ ĐỌC TRƯỚC — kế hoạch RUN 7 KHÔNG vừa 5 slot × 3.5h

| Phép tính | Con số |
|---|---|
| Năng lực: 3 người × 5 slot × 3.5h | **52.5 h** |
| Tải RUN 7 nguyên bản (tổng `Time` của 57 STEP, tính STEP 07/57 cho cả 3 người) | **57.4 h** |
| Mức dùng | **109 %** · buffer **−9 %** |
| Đường găng thuần (chỉ phụ thuộc, vô hạn người) | **920′** / 1050′ có ⟹ slack **12 %** |
| Tải bắt buộc của A trong **ngày 1** (STEP 03,05,07,08,12,13,16,20) | **455′** / 420′ có ⟹ **quá tải 35′** |

**Ba kết luận, không thương lượng:**

1. **Phải cắt TRƯỚC khi bắt đầu, không phải khi chậm.** Cut ladder của RUN 7 (Phụ lục 4) chạy hết 4 nấc đầu vẫn còn 103 %. Mục 5 dưới đây đã cắt sẵn 5 STEP trước D1-AM.
2. **Walking skeleton cuối D1-PM chỉ đạt được nếu hạ scope STEP 20 ngay từ đầu** — xem SP2. Giữ nguyên scope RUN 7 thì STEP 20 xong sớm nhất **445′** = ~09:00 D2-AM, tức trễ mốc sống còn.
3. **"Minimum shippable" là kế hoạch chính thức, không phải phương án dự phòng.** 4 worker chạy thật là *stretch goal*. Ở 3.5h/slot, bản 4-worker-thật đạt buffer 0 %; bản minimum shippable đạt 10 %.

> RUN 7 mục 0.6 giả định 1 slot = **4 giờ**. Ràng buộc ở đây là **3.5 giờ**. Chênh 0.5h × 15 slot-người = **7.5 giờ biến mất** — đúng bằng khoảng lệch giữa 109 % và 95 %. Đây là toàn bộ lý do file này phải cắt.

---

## 1. Phân vai 3 role

Giữ phân vai RUN 7 mục 0.3 #6 (đã chuyển tải khỏi A), **thêm một thay đổi** ở STEP 16 — lý do ở cột cuối.

| Role | Người | Trách nhiệm chính | STEP phụ trách | Kỹ năng cần có |
|---|---|---|---|---|
| **Role A — Orchestrator & Contract** | **Nghĩa** | Chủ **duy nhất** của contract; `core/` lõi; verdict engine chạy qua B; gác cổng review mọi adapter; báo cáo mục 0/3/4/9 + slide | 03, 05, **07**, 08, 12, 13, 16*, 20, 26, 27, 32, 33, 34, 38, 40, **41**, 53, 54, **57** | JSON Schema, `subprocess`, thiết kế API nội bộ, **nói "không" với trường riêng** |
| **Role B — Authoring & Governance** | **Đức** | Nhánh worker tự hành (Midscene), canary, frontend toy app, Diff-Guard/mutation, báo cáo mục 1/2/8 | 01, 04, 09, 15, 17, 19, 22, **23**, 28, 29, 42, 43, 44, 47, 48, 52, 55 | Node/npm, prompt YAML của agent UI, chịu được tool flaky |
| **Role C — Deterministic & Eval** | **Huy** | Worker tất định (Schemathesis, k6), nhánh eval AI (DeepEval), backend toy app, registry, demo script, báo cáo mục 5/6/7 | 02, 06, 10, **11**, 14, 16b*, 18, 24, 25, 30, 31, 35, 36, **37**, 51, 56 | FastAPI, pytest, đọc CLI lạ nhanh, hiệu chuẩn ngưỡng |

**\* Thay đổi duy nhất so với RUN 7:** STEP 16 (`core/plan.py` + `core/runner.py`, 90′) **tách đôi** —
**16a** `core/runner.py` (A, 55′) · **16b** `core/plan.py` (C, 45′, phụ thuộc STEP 13).
**Vì sao:** không tách thì A gánh 455′ trong ngày 1 trên 420′ có ⟹ skeleton chắc chắn trễ. Tách xong A còn **400′**. C có chỗ vì C đã viết `core/registry.py` (STEP 14) nên đã ở trong `core/`.
**Rủi ro của việc tách:** hai người sửa `core/` cùng lúc ⟹ `plan.py` và `runner.py` phải có **ranh giới hàm rõ trước khi gõ** — A khai chữ ký `run_all(specs, plan_only, registry, run_dir)` và `load_plan/resolve/toposort` ngay ở SP1, không đợi.

---

## 2. Bảng timeline chính

Giờ thật giả định: **AM 08:30–12:00** · **PM 13:30–17:00** (mỗi slot 210′ làm việc thực).
Ô ghi: `STEP` | *bàn giao* | 🔒 bị block bởi / 🔓 đang block ai.

| Slot | **Nghĩa (A) — Orchestrator** | **Đức (B) — Authoring** | **Huy (C) — Deterministic & Eval** |
|---|---|---|---|
| **D1-AM**<br>08:30–12:00<br>*PHASE 0 — cả 3 cùng làm* | `03` repo skeleton (30′)<br>`05` nháp contract (60′)<br>**`07` họp chốt (45′)**<br>`08` clone sạch (20′)<br>`12` schema.py *bắt đầu* (55′)<br>📦 *3 file schema + tag `qrs-v0.1`*<br>🔓 **block cả B lẫn C tới 12:00** | `01` kiểm key VLM (20′) ⬅ **việc số 0**<br>`04` bootstrap/doctor/lock (90′)<br>**`07` họp (45′)**<br>`09` clone sạch (20′)<br>📦 *`ENV-FINGERPRINT` của B*<br>🔒 `04` block bởi `03` | `02` kiểm key model chấm (20′) ⬅ **việc số 0**<br>`06` stub summarizer + 5 golden (60′)<br>**`07` họp (45′)**<br>`10` clone + đối chiếu 3 fingerprint (20′)<br>`11` toy backend *bắt đầu* (55/75′)<br>📦 *quyết định M1 bằng mã*<br>🔓 `11` block B(`22`) và C(`24,30,35`) |
| **D1-PM**<br>13:30–17:00<br>*PHASE 1 — WALKING SKELETON* | `12` xong (5′)<br>**`13` `_base.py` + `oracle/` (90′)** ⬅ nút thắt, block 8 STEP<br>`16a` `runner.py` (55′)<br>**`20` CLI + chạy e2e với mock (60′, scope hẹp)**<br>📦 **`report.md` in ra từ worker giả**<br>🔓 `13` block 15,25,27,28,31,36,37 | `17` `verdict.py` (45′)<br>`19` `report.py` (75′)<br>`15` mock adapter + fixture (60′)<br>*(dư 30′ → `22` frontend)*<br>📦 *3 fixture mock + 2 module core*<br>🔒 `15` bị `13` chặn tới ~15:05 | `11` xong (20′)<br>`14` `registry.py` (60′)<br>`16b` `plan.py` (45′)<br>`18` `signature.py` (45′)<br>*(dư 40′ → `24` Schemathesis hello)*<br>🔒 `16b` bị `13` chặn |
| **D2-AM**<br>08:30–12:00<br>*PHASE 2 — INTEGRATION THẬT* | `20` đuôi + 4 test CLI (30′)<br>`26` **review adapter Schemathesis** (30′)<br>`27` `oracle/threshold+signals` (60′)<br>`32` **plan.yaml thật: mock → Schemathesis** (60′)<br>`21` README core (30′)<br>📦 **dòng `deterministic_assert` THẬT đầu tiên trong report**<br>🔓 `27` block B(`28`) và C(`31`) | `22` frontend + BUG-2 (60′)<br>**`23` Midscene hello + ma trận exit code (90′ TIMEBOX CỨNG)**<br>`28` adapter Midscene *bắt đầu* (60/90′)<br>📦 *4 file mẫu trong `tests/samples/`*<br>🔒 `28` chờ `27` của A (~11:00) | `24` Schemathesis hello + hiệu chuẩn BUG-1 (60′)<br>`25` **adapter Schemathesis** (90′) ⬅ *khuôn cho 2 adapter sau*<br>`35` DeepEval hello (60′) ⬅ **kéo sớm từ D2-PM**<br>📦 *adapter thật đầu tiên, qua checklist 5 câu*<br>🔓 `25` block `26` của A |
| **D2-PM**<br>13:30–17:00<br>*PHASE 2 hoàn thiện + tiêu chí PoC* | `33` review Midscene → k6 (45′)<br>`40` **plan đầy đủ + tiêu chí #1–#3** (45′)<br>**`41` 🛑 tiêu chí #4: chạy 2 lần, diff rỗng (60′)**<br>`53` báo cáo mục 3 *bắt đầu* (45′)<br>📦 **`IDENTICAL` + ảnh chụp màn hình**<br>🔒 `40` **KHÔNG** chờ Midscene — xem mục 4 | `28` xong (30′)<br>`29` test Midscene 3 ca (60′)<br>`42` canary task (45′)<br>`52` **Midscene ổn định ×3 (60′ TIMEBOX)** ⬅ *đây là buffer*<br>📦 *canary `fail` 3/3 lần*<br>🔓 `29` block `33` của A | `31` adapter k6 (75′)<br>`36` collect adapter (45′)<br>**`37` DeepEval adapter, 2 `verdict_source` (90′)**<br>📦 *một result, hai nguồn phán quyết*<br>🔒 `37` là việc **cuối cùng** vào được trước freeze |
| **D3-AM**<br>08:30–12:00<br>*PHASE 3/4 — KHÔNG CODE TÍNH NĂNG MỚI* | **08:30 SP4 freeze (15′)**<br>`38` review DeepEval (45′)<br>`34` thêm t-002/t-101 vào plan (30′)<br>`53` báo cáo mục 0/4/9 (45′)<br>`54` slide 12 trang (60′)<br>**`57` 🛑 dry-run bấm giờ (60′)** | **SP4 (15′)**<br>`43` kiểm canary trong verdict (45′)<br>`44` chứng minh N5 (`mock2`) (45′)<br>`55` báo cáo mục 1/2/8 (60′)<br>*(45′ **BUFFER THẬT** — không giao việc)*<br>**`57` dry-run** | **SP4 (15′)**<br>`51` **demo script + GHI RUN DỰ PHÒNG (75′)** ⬅ *làm sớm nhất có thể*<br>`56` báo cáo mục 5/6/7 (60′, đã rút)<br>**`57` dry-run** |

**Bốn ràng buộc bắt buộc — trạng thái:**

| Ràng buộc | Đạt? | Ghi chú |
|---|---|---|
| Phase 0 trọn D1-AM, không ai code adapter trước khi xong | ✅ | Ngoại lệ đã có trong RUN 7 mục 344: A được bắt đầu `12` ngay sau `07` (chỉ cần schema). Đuôi `11` của C tràn ~20′ sang D1-PM — chấp nhận, `11` không chặn ai trong D1-PM. |
| Walking skeleton chậm nhất cuối D1-PM | ⚠️ **chỉ đạt nếu hạ scope** | Giữ nguyên scope RUN 7 ⟹ xong 445′ = 09:00 D2-AM. Xem SP2 để biết bản hẹp gồm gì. |
| Integration thật bắt đầu D2-AM | ✅ | `26` + `32` ở D2-AM = report có dòng tất định thật. Không để sang D2-PM. |
| D3-AM không code tính năng mới | ✅ | `38`/`34` là review + sửa plan, không phải tính năng. `43`/`44` là bằng chứng đã thiết kế xong ở D2. Cắt ngay nếu SP4 báo đỏ. |

---

## 3. Sync points

| | Thời điểm | Ai | Quyết định gì | **Nếu không đạt** |
|---|---|---|---|---|
| **SP0** | **D1-AM 08:30, 15′** | cả 3 | Hai key (VLM + model chấm) có gọi được không (`01`, `02`) | Không có key ⟹ **chốt ngay** SUT giả lập + Midscene replay. Không chờ tới chiều rồi mới biết. Đây là rủi ro V2/#4 của RUN 5. |
| **SP1** | **D1-AM 11:15–12:00** (họp `07`, 45′ cứng) | cả 3, cùng màn hình | ① chốt **D-01…D-10** ② duyệt 2 cặp task↔result (k6 tất định, Midscene discovery) ③ `git tag qrs-v0.1` ④ A khai **chữ ký hàm `plan.py` ↔ `runner.py`** cho việc tách STEP 16 | **Chưa chốt ⟹ DỪNG MỌI VIỆC KHÁC cho tới khi chốt.** Bất đồng quá 20′ ⟹ **A quyết** (contract có đúng một chủ), ghi lý do vào `docs/decisions.md`, đi tiếp. Không kéo họp quá 45′ — skeleton phải chạy hết ngày 1. |
| **SP2** | **D1-PM 16:40–17:00** | cả 3, 20′ | 🛑 **GO/NO-GO ĐẦU TIÊN.** Bar tối thiểu: `python orchestrator.py --plan plans/demo.yaml` in `report.md` có đủ 5 heading + `exit=0`; `demo_fail.yaml` → `exit=1`. **Không** yêu cầu 4 test CLI, **không** yêu cầu README — hai thứ đó trôi sang D2-AM một cách có kế hoạch. | **NO-GO ⟹ kích hoạt ngay:** A bỏ mọi thứ ngoài skeleton và **dừng nhận việc mới**; B+C ngồi ghép cùng A vào `core/` 60′ đầu D2-AM; toàn bộ `23`/`24`/`25` lùi. Đồng thời **hạ mục tiêu sprint xuống minimum shippable ngay tại đây**, không đợi SP3. |
| **SP3** | **D2-PM 15:00, 15′** | cả 3, đứng | ① `37` (DeepEval) có kịp 17:00 không? ② `29` (Midscene) đã ra 3 ca pass/fail/error chưa? ③ `41` có chạy được trong chiều nay không? | **①  không ⟹ cắt nấc 5 NGAY (DeepEval → mock có nhãn), A chạy `41` lúc 15:15 với t-003 mock.** ② không ⟹ cắt nấc 6 (Midscene → `QC_REPLAY_MIDSCENE`). ③ không ⟹ `41` chuyển sang 08:45 D3-AM và **bỏ `53`/`55`/`56` phần phụ thuộc số đo**. Đây là mốc kích hoạt cut ladder — **quyết trong 15′, không tranh luận lại ở D3.** |
| **SP4** | **D3-AM 08:30, 15′** | cả 3 | 🛑 **FREEZE TÍNH NĂNG.** Liệt kê thành tiếng: cái gì THẬT, cái gì MOCK, cái gì REPLAY — và ghi vào slide 6 + slide 9 ngay lúc đó. Phân lại việc viết nếu buffer của B đã bị tiêu. | Tiêu chí #1–#4 chưa đạt ⟹ **không thêm tính năng, chỉ sửa**; hạ "PoC thành công" xuống #1–#3 và **nói rõ với mentor**, không giấu. Còn ≤ 90′ ⟹ chỉ làm `57` + đảm bảo bản ghi dự phòng chạy được. |

---

## 4. Sơ đồ phụ thuộc — đường găng

```
D1-AM ──────────── D1-PM ──────────── D2-AM ──────────── D2-PM ──────────── D3-AM
                                                                          
 ╔═══════════════════════ ĐƯỜNG GĂNG (920′ / 1050′ có — slack 12%) ═══════════════════════╗
 ║                                                                                        ║
 ║  03 ─► 05 ─► ⟦07⟧ ─► 10 ─► 11 ─► 22 ─► ⟦23⟧ ─► 28 ─► 29 ─► 33 ─┐                       ║
 ║  A     A      ALL    C      C      B     B      B     B     A  │                       ║
 ║  30′   60′    45′    20′    75′   60′    90′    90′   60′   45′│                       ║
 ║                             └──── NHÁNH MIDSCENE: 375′ liên tục ────┘                  ║
 ║                                                                 ├─► 34 ─► 57           ║
 ╚═════════════════════════════════════════════════════════════════╪═══════════════════════╝
                                                                   │
 NHÁNH SKELETON (phải xong trước 17:00 D1-PM):                     │
   ⟦07⟧ ─► 12 ─► 13 ─► 16a ─► ⟦20⟧                                 │
    ALL    A     A      A      A        ▲                          │
           60′   90′   55′    60′       │ 15,17,18,19,16b cùng đổ vào đây
                                        │                          │
 NHÁNH TẤT ĐỊNH (tách khỏi Midscene — xem ⚡):                      │
   11 ─► 24 ─► 25 ─► 26 ─► 32 ─┬─► 40 ─► ⟦41⟧ ─► 53 ─► 54 ─► ⟦57⟧ ─┘
   C     C     C     A     A   │   A      A       A      A     ALL
                               │
   11 ─► 35 ─► 36 ─► 37 ─► 38 ─┘

 ⟦ ⟧ = cổng bắt buộc      ⚡ = thay đổi so với RUN 7
```

**Hai đầu việc mà trễ là trễ cả sprint:**

| # | Đầu việc | Vì sao | Biện pháp đã cài |
|---|---|---|---|
| **1** | **STEP 13** — `adapters/_base.py` + `oracle/` (A, D1-PM, 90′) | Một người giữ **khuôn của mọi adapter**; chặn **8 STEP** của cả B lẫn C (15, 25, 27, 28, 31, 36, 37, 16b). Trễ 30′ ở đây = trễ 30′ cho hai người kia. | Làm **ngay sau STEP 12**, A không xen việc khác. Quá 90′ ⟹ A phát hành **phiên bản khung** (chữ ký hàm + `main()` rỗng) rồi hoàn thiện sau — B/C code tiếp được ngay. |
| **2** | **STEP 23** — Midscene hello world (B, D2-AM, 90′) | Nằm giữa nhánh Midscene dài **375′ liên tục** (22→23→28→29). Chỉ B làm được, không chia nhỏ được, và là chỗ **duy nhất** có hành vi LLM khó đoán giờ. | **Timebox cứng 90′.** Hết giờ ⟹ dừng, chuyển `tests/samples/midscene_summary.fixture.json` viết tay, gắn nhãn mock, `28` code trên cấu trúc giả định. |

**⚡ Thay đổi so với RUN 7 — tách tiêu chí PoC khỏi Midscene:**
RUN 7 cho `40` phụ thuộc `34` (thêm t-002 **và t-101 Midscene** vào plan), nên `41` (tiêu chí #4 — "khoảnh khắc mạnh nhất của demo") **treo sau cả nhánh Midscene**: sớm nhất **710′**.
Ở đây `40` chỉ phụ thuộc `32` + `38`, và `34` chuyển về **sau** `41`. Hệ quả: **`41` sớm nhất 610′** — vào giữa D2-PM thay vì sát cuối.
**Lý do kỹ thuật, không phải để cho đẹp lịch:** STEP 41 biến thể (a) của RUN 7 vốn đã chạy `--only t-000,t-001,t-002,t-003` — **không có task Midscene nào trong đó**. Phụ thuộc `34` là phụ thuộc thừa.
**Giá phải trả:** biến thể (b) của STEP 41 (diff trên run đầy đủ *có* Midscene) lùi sang D3-AM và **là thứ bị cắt đầu tiên** nếu SP4 báo đỏ. Bản (a) một mình vẫn chứng minh được tiêu chí #4.

---

## 5. Cut ladder gắn mốc giờ

### 5.1 Cắt TRƯỚC khi bắt đầu (đã trừ vào mục 2 — không cần ai quyết)

| Bỏ | Tiết kiệm | Mất gì | Vì sao bỏ được |
|---|---|---|---|
| `49` Selection theo diff | 45′ (A) | Demo chọn-theo-diff | Không ảnh hưởng 6 tiêu chí PoC; nói bằng sơ đồ. RUN 5 xếp **nấc 0️⃣** |
| `46` DG-2/DG-3 protected paths | 45′ (C) | Bằng chứng "input xấu bị chặn" | PoC không có healer ⟹ không có gì để chặn *thật* |
| `45` DG-1 plan guard | 45′ (C) | Bằng chứng chống plan mục (V3) | Luật vẫn nằm trong báo cáo mục 4 và 8 |
| `39` runner song song | 75′ (A) | Luận điểm `max()` vs `sum()` | Không ảnh hưởng đúng/sai của verdict |
| `50` golden-file test của report | 45′ (B) | Khoá định dạng report bằng test | Giữ **checklist 10 dòng** của STEP 50 — rẻ hơn, đủ dùng cho 2.5 ngày |
| `48` thu gọn còn M0/M1/M3/M5 | 45′ (B) | Bảng mutant đầy đủ | 4 mutant vẫn chứng minh: xanh khi sạch · đỏ khi có lỗi · **crash ≠ fail** |
| **Tổng** | **300′ = 5.0 h** | | 57.4 h → **52.4 h** (mức dùng 109 % → **100 %**) |

### 5.2 Cắt theo mốc đồng hồ — quyết tại chỗ, không tranh luận

| Mốc kiểm tra | Nếu CHƯA đạt trạng thái X | Cắt Y **ngay** | Hậu quả của việc cắt |
|---|---|---|---|
| **D1-AM 08:45** | Key VLM **hoặc** key model chấm chưa gọi được | Chốt **SUT giả lập + Midscene replay** cho cả sprint; B bỏ `23` khỏi kế hoạch D2-AM | Không còn worker (a) chạy thật. Contract vẫn chứng minh được bằng dữ liệu ghi sẵn — **phải gắn nhãn REPLAY trên slide** |
| **D1-AM 12:00** | `git tag qrs-v0.1` chưa tồn tại | **Không cắt — dừng.** Cả 3 ở lại tới khi xong, ăn trưa muộn | Mất ≤ 30′ của D1-PM. Rẻ hơn nhiều so với 3 người code trên 3 phiên bản schema khác nhau |
| **D1-PM 15:05** | `13` (`_base.py`) chưa push | A phát hành **bản khung** (chữ ký + `main()` rỗng) trong 10′ | Adapter phải sửa lại 1 lần khi bản thật lên. Vẫn rẻ hơn để B và C ngồi không 40′ |
| **D1-PM 17:00** | **Walking skeleton chưa chạy** (SP2 NO-GO) | ① B+C ghép vào `core/` 60′ đầu D2-AM ② **hạ mục tiêu xuống minimum shippable ngay** ③ bỏ worker thứ 4 (Midscene), giữ mock cho nhánh discovery | Mất 1 trong 2 lane chạy thật. **Đây là nấc đau nhất** — nhưng skeleton trễ nghĩa là V1 (contract bị ép méo) sẽ lộ ở D2-PM, lúc không còn giờ sửa schema |
| **D2-AM 10:15** | `23` Midscene hello hết 90′ timebox mà chưa ra file `--summary` | Dừng ngay. Viết tay `midscene_summary.fixture.json`, `28` dựng trên cấu trúc giả định, gắn ⚠ | Adapter Midscene **chưa xác nhận với tool thật** — phải nói câu đó trên slide 9 |
| **D2-AM 12:00** | `32` chưa đổi được mock → Schemathesis thật | Bỏ `31` (k6 adapter) khỏi kế hoạch D2-PM, C dồn hết vào `37` (DeepEval) | Còn 1 worker tất định thay vì 2. Minimum shippable yêu cầu **Schemathesis + k6**; mất k6 là tụt dưới bar ⟹ ưu tiên giữ `31`, cắt `36`/`37` trước |
| **D2-PM 15:00 (SP3)** | `37` (DeepEval) sẽ không kịp 17:00 | **Nấc 5:** DeepEval → mock có kết quả sẵn, **gắn nhãn**. A chạy `41` lúc 15:15 | Mất worker AI-app chạy thật — trả lời trực tiếp cho đề mentor bị yếu. Nhưng **`41` đúng giờ quan trọng hơn** |
| **D2-PM 15:00 (SP3)** | `29` chưa ra đủ 3 ca (pass/fail/error) | **Nấc 6:** bật `QC_REPLAY_MIDSCENE`, B chuyển sang `42` canary | Mất worker (a) chạy thật. Contract **vẫn** được chứng minh với dữ liệu thật đã ghi |
| **D2-PM 17:00** | `41` chưa in `IDENTICAL` | Bỏ `43`, `44`, `47` khỏi D3-AM. Sáng D3 A làm **một việc duy nhất**: `41` | Mất bằng chứng canary + N5. Tiêu chí #4 nằm trong **KHÔNG BAO GIỜ CẮT** ⟹ mọi thứ khác nhường đường |
| **D3-AM 08:30 (SP4)** | Tiêu chí #1–#4 chưa đủ | Hạ "PoC thành công" xuống #1–#3, ghi vào slide 9, **báo mentor trước khi bị hỏi** | Bản propose yếu đi rõ rệt nhưng **trung thực**. Giấu thì mentor tìm ra trong 2 câu hỏi |
| **D3-AM 10:30** | `51` chưa ghi được run dự phòng | Dừng mọi việc viết. C ghi bằng lần chạy tốt gần nhất, gắn nhãn *replay* | Demo mất lưới an toàn. Không chấp nhận được — **`51` ưu tiên trên mọi mục báo cáo** |
| **D3-AM 11:00** | Còn < 60′ | Chỉ làm `57` dry-run. Bỏ mọi thứ khác | Báo cáo dở dang nhưng demo chạy được. Ngược lại thì không present được |

**KHÔNG BAO GIỜ CẮT — kể cả ở 11:55 D3-AM:**
① Contract + validate schema (`05`, `07`, `12`, `13`) · ② report tách 3 mục theo `verdict_source` (`19`) · ③ tiêu chí tái lập, chạy 2 lần diff rỗng (`41`).
*Cắt ba cái này thì còn lại là một script gọi 4 tool — và mentor sẽ nói đúng như vậy.*

---

## 6. Buffer & rủi ro

### 6.1 Buffer — nói thẳng

| Kịch bản | Tải | Mức dùng | **Buffer** |
|---|---|---|---|
| RUN 7 nguyên bản | 57.4 h | 109 % | **−9 %** ❌ |
| Sau pre-cut 5.1 (4 worker thật) | 52.4 h | 100 % | **0 %** ❌ |
| Sau pre-cut + Midscene REPLAY | 50.8 h | 97 % | **+3 %** ❌ |
| **Minimum shippable** (2 thật + 2 mock) | 47.4 h | 90 % | **+10 %** ⚠️ |
| Ngưỡng an toàn (15 %) | ≤ 44.6 h | ≤ 85 % | — |

> **Không kịch bản nào đạt 15 %. Kế hoạch này quá sát và cần nói ra chứ không giấu.**
> Bản khả thi nhất — minimum shippable — mới đạt **10 %**, tức **~5 giờ** dự phòng cho cả sprint 52.5 giờ. Một sự cố tool cỡ trung (Midscene không chạy, quota hết) tiêu hết 5 giờ đó trong nửa slot.
>
> **Vì vậy mục 5.2 tồn tại:** khi buffer thực chất bằng không, thứ thay thế buffer **không phải là làm nhanh hơn** — mà là **danh sách cắt có ngưỡng giờ cố định, quyết trước, thực thi không tranh luận**.

**Buffer nằm ở đâu (cụ thể, không nói chung chung):**
- **45′ của B ở D3-AM** — buffer duy nhất được bảo vệ. **Cấm** giao việc viết vào đó.
- **60′ của `52`** (Midscene ổn định ×3, D2-PM) — đây *là* buffer đội lốt một STEP, không phải việc mới.
- **~40′ dư của C ở D1-PM** và **~30′ dư của B ở D1-PM** — đã tiêu sẵn vào `24`/`22`, mất ngay khi `13` trễ.

### 6.2 Năm rủi ro lớn nhất

| # | Rủi ro | **Dấu hiệu nhận biết SỚM** | Slot dễ vỡ nhất | Phương án |
|---|---|---|---|---|
| **1** | **Contract bị ép méo bởi Midscene** (V1) | Xuất hiện `if worker == "midscene"` trong `core/`, **hoặc** ai đó nói câu *"thêm một trường nhỏ chỉ cho Midscene thôi"* | **D2-AM → D2-PM** (`28`) | Dừng 15′, cả 3 quyết: sửa schema cho **cả 4** worker, hoặc để adapter **mất thông tin**. Quy tắc vàng: *được mất, không được bịa*. **Cấm** trường riêng. `tools/review_adapter.py` câu 5 bắt tự động |
| **2** | **Skeleton không kịp cuối D1-PM** | 15:05 D1-PM mà `13` chưa push; hoặc 16:00 mà `15`/`19` chưa xong | **D1-PM** | A phát hành bản khung `13` lúc 15:05. SP2 NO-GO ⟹ B+C ghép vào `core/`, hạ xuống minimum shippable **ngay tại 17:00**, không đợi D2 |
| **3** | **Midscene tốn giờ vì hành vi LLM** (V5) | `23` chạy 2 lần ra kết quả khác hẳn nhau; hoặc hoá đơn nhảy bất thường; hoặc một run > 5′ | **D2-AM (`23`) và D2-PM (`29`)** | Giảm `max_steps` → 8, thu hẹp `avoid`, viết lại flow rõ hơn. Vẫn không ổn ⟹ `QC_REPLAY_MIDSCENE`, gắn nhãn *replay*. **Contract vẫn được chứng minh — đó mới là thứ đang demo** |
| **4** | **Thiếu key / hết quota** (V2) | 08:45 D1-AM chưa ai xác nhận key gọi được. Muộn hơn: HTTP 429 giữa `37` | **D1-AM**, tái phát ở **D2-PM** | Việc số 0 của sprint (`01`, `02`) — **trước cả chốt schema**. Không có key ⟹ SUT giả lập + replay, chốt ngay ở SP0 chứ không để lửng |
| **5** | **Sa đà làm orchestrator thông minh** | Nghe thấy *"hay là cho LLM tự chọn worker"* / *"cho LLM đọc mô tả PR"* | **D2-AM** (lúc routing bắt đầu có vẻ thủ công) | Routing bằng LLM đưa phi tất định vào **coverage** — thừa và có hại. Orchestrator "ngu" là orchestrator đúng. Câu này dán ở đầu `core/README.md` |

### 6.3 Rủi ro riêng của việc làm với agent LLM — đã phản ánh vào buffer chưa?

**Chưa đủ. Nói thẳng:**

| Câu hỏi | Trả lời |
|---|---|
| Hành vi flaky làm giờ debug không đoán được — đã tính vào đâu? | Chỉ vào **60′ của `52`** + **45′ buffer của B ở D3-AM** = **105′** cho toàn bộ nhánh Midscene |
| 105′ có đủ không? | **Không có cơ sở để nói là đủ.** Debug một agent LLM không có phân phối thời gian giống debug một hàm thuần: nó không hội tụ theo số lần thử, vì cùng input có thể ra khác output. Ước lượng `Time` của RUN 7 vốn đã *"có thể lệch ×2"* — với `23`/`28`/`29` con số đó lạc quan hơn nữa |
| Vậy xử lý bằng gì? | **Bằng timebox + đường thoát dựng sẵn, không bằng buffer.** `23` timebox 90′, `52` timebox 60′, `QC_REPLAY_MIDSCENE` code sẵn từ `29`. Nghĩa là: ta không *dự phòng giờ* cho rủi ro này — ta **giới hạn tổn thất tối đa** của nó ở 150′ và chấp nhận mất tính "chạy thật" |
| Hệ quả phải nói với mentor | Nhánh discovery **có thể** là replay. Nói **trước khi bị hỏi**, ở slide 6 và slide 9 |

---

## 7. Definition of Done toàn sprint

### 7.1 Checklist nhị phân — "sprint thành công"

Mỗi dòng chỉ có ☐ hoặc ☑. Không có "gần xong".

| ☐ | Điều kiện | Kiểm bằng |
|---|---|---|
| ☐ | Contract đóng băng, có tag | `git ls-remote --tags origin qrs-v0.1` in 1 dòng |
| ☐ | Mọi result đi qua **một** schema, không trường riêng | `python tools/validate.py result runs/<run>/results/*.json` → `PASS` hết, exit 0 · **tiêu chí #2** |
| ☐ | `core/` không biết tên worker nào | `Select-String core\*.py -Pattern 'schemathesis\|k6\|deepeval\|midscene'` → **không in gì** |
| ☐ | Gate **đỏ** khi bug bật, **xanh** khi bug tắt | `--plan plan.yaml` → `exit=1` / `-Bugs none` → `exit=0` · **tiêu chí #1** |
| ☐ | Điểm LLM **không** đổi được verdict tổng | ép G-Eval xuống 0.01 → `--rerender` cho `## VERDICT` **giống hệt** · **tiêu chí #3** |
| ☐ | **Chạy 2 lần, phần tất định giống hệt** | `tools/diff_runs.py` in `IDENTICAL`, exit 0 · **tiêu chí #4** ⭐ |
| ☐ | Công cụ diff **không mù** | sửa tay 1 verdict → `DIFFERENT`, exit 1 |
| ☐ | Report tách **3 mục** theo `verdict_source` + khối SKIPPED/ERROR ở **đầu** | `report.md` có đủ `## 1.` … `## 5.` |
| ☐ | Thêm worker thứ 5 **không** đụng `core/` | `git show --stat` của commit `mock2` không có `core/` · **tiêu chí #5** |
| ☐ | Demo ≤ 10 phút, không ai gõ lệnh ngoài `demo.ps1` | `57` in `TOTAL elapsed ≤ 10:00` |
| ☐ | **Có bản ghi dự phòng đã thử phát lại một lần** | `recordings/demo-good-run/report.md` + transcript tồn tại |
| ☐ | Mọi thứ MOCK/REPLAY/SUT-giả-lập **đã liệt kê trên slide** | slide 6 và slide 9 |

### 7.2 "Minimum shippable" — bản tệ nhất mà vẫn present được

> **Orchestrator + Schemathesis + k6 chạy THẬT + DeepEval MOCK + Midscene MOCK/REPLAY
> + report tách 3 mục + chạy 2 lần cho verdict giống hệt + slide có nhãn trung thực.**

**Vì sao bản này vẫn present được đầy đủ luận điểm** — luận điểm không nằm ở số lượng worker:

| Luận điểm | Chứng minh bằng gì trong bản tối thiểu |
|---|---|
| **N4** — một contract cho mọi worker | 2 worker thật + 2 mock đi qua **cùng** một `result.json` |
| **N2** — LLM không phán quyết | report có mục `llm_judgment` (dù dữ liệu từ mock) **không** cộng vào verdict |
| **Tái lập** | `diff_runs.py` in `IDENTICAL` |
| **N5** — thêm worker không sửa lõi | `git show --stat` của `mock2` |

**Bar tuyệt đối — dưới mức này thì không đem lên:**
① tiêu chí #4 không in `IDENTICAL` ⟹ **dừng**, hiểu nguyên nhân đã rồi mới demo. Đây vừa là khoảnh khắc mạnh nhất vừa là thứ nằm trong KHÔNG BAO GIỜ CẮT.
② Không có bản ghi dự phòng ⟹ không demo live.

**Bốn worker dở dang tệ hơn hai worker chạy thật + hai mock trung thực.** Mentor đánh giá cao sự trung thực hơn số lượng; một demo có mock gắn nhãn rõ mạnh hơn một demo 4 worker mà cái nào cũng lắp ghép.

---

## Phụ lục — 5 STEP bị cắt trước và chỗ chúng vẫn xuất hiện

| STEP đã cắt | Vẫn nói được ở đâu |
|---|---|
| `49` Selection | Báo cáo mục 4 (sơ đồ SELECTION) + slide 4 |
| `45` DG-1 plan guard | Báo cáo mục 4 (chống V3 plan mục) + mục 8 (chưa cài) |
| `46` DG-2/DG-3 | Báo cáo mục 4 + mục 8 (chưa cài, PoC không có healer) |
| `39` runner song song | Báo cáo mục 7 (kinh tế: `max()` vs `sum()` — trên giấy) |
| `50` golden test report | Thay bằng checklist 10 dòng của STEP 50, chạy tay ở D3-AM |

**Mọi thứ trong bảng này phải có mặt ở `08-limitations.md`.** Mục 8 là mục **ghi điểm**, không phải mất điểm: *một bản propose không có mục "chúng tôi chưa chắc về…" thì mentor mặc định là team chưa kiểm.*
