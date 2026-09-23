# Demo runbook v2 — chốt theo đúng problem statement

**Ngày:** 2026-09-23 · **Đối tượng:** người trình bày (không phải mentor)
**Nguồn:** `docs/report/00-tldr.md`, `03-prior-art.md`, `09-roadmap.md`, `docs/slides/outline.md`
(slide 06 + 07), `docs/architecture.md` §107/§144/§186, và code thật của dashboard v2.
**Quan hệ với `outline.md`:** file này **không thay** outline. Nó chỉ chi tiết hoá **slide 06
(Demo)** sau khi v2 đổi trạng thái 3 dòng trong bảng trung thực, và bổ sung kịch bản mở rộng
cho Q&A.

---

## 0. Problem statement của anh quản lý → đã phủ tới đâu

Anh hỏi 5 điều. Bảng này là cái phải trả lời được, không phải cái đẹp nhất để chiếu.

| Câu hỏi | Đã có gì | Mức |
|---|---|---|
| 1. Các **cách tiếp cận** dùng Agent cho QC | 6 khái niệm bị marketing trộn lẫn; trục phân loại thật không phải "có LLM hay không" mà là **thời điểm quyết định** (design-time vs runtime) + **chỗ nào LLM được phép quyết**. Neo định lượng: TestGen-LLM (Meta) 75% build được / **57% pass ổn định** / 43% output bị vứt — cái làm nó dùng được là **bộ filter tất định**, không phải LLM | ✅ Sâu |
| 2. **OSS nào đang được quan tâm** | Hercules (LangGraph planner→executor→assertion, AGPL-3.0) · Testkube (orchestration mọi tool container hoá trên K8s, từ 2021) · ReportPortal (gom + triage ML, từ **2016**) · Playwright Test Agents (prior art **ủng hộ** hướng của ta). 14 ứng viên khảo sát, **không tool nào phát ra `verdict_source`** | ✅ Mạnh nhất |
| 3. **Tham gia được loại testing nào** | API property/contract (Schemathesis) · load/perf (k6) · **AI-app eval (DeepEval)** · web GUI explore (Midscene) · integration qua `http.collect` + Schemathesis. **4 worker gating chạy thật trên 1 schema**, worker thứ 5 ở discovery lane | ✅ Có PoC thật |
| 3b. **Tự động trên mobile** | ❌ **KHÔNG CÓ.** Loại **có chủ ý** (Windows, không emulator/WSL2, không macOS runner). Là **Pha 2** roadmap | ⚠️ **Gap — phải nói trước khi bị hỏi** |
| 4. **Propose 1 con Agent QC** (kể cả AI app) | Universal Contract 3 mảnh (manifest → task_spec → result) + **2 lane** + `verdict_source` ở cấp **từng finding**; AI app là capability **hạng nhất** (`llmapp.eval`), không phải phần phụ | ✅ Sâu |
| 5. **"Automate càng nhiều càng tốt"** | 7/10 bước tự động (slide 07) **+ v2 vừa lật 3 dòng** từ "VẼ, KHÔNG CÀI" sang "CÀI THẬT" | ✅ Mạnh hơn deck hiện tại |

**Kết luận về định vị demo:** phần ghi điểm không phải dashboard. Là **mục 2 + 4** (prior art gap
→ verdict provenance) và bằng chứng tái lập. Dashboard chỉ là **phương tiện để mục 2+4 nhìn thấy
được**, không phải nhân vật chính. Nếu mở màn bằng UI đẹp, bạn thành người làm UI.

---

## 1. VIỆC PHẢI LÀM TRƯỚC: sửa bảng trung thực ở slide 06

Slide 06 hiện đang **tự hạ thấp** công việc của chính bạn. Ba dòng phải sửa:

| Dòng trong slide 06 | Đang ghi | Sự thật sau v2 |
|---|---|---|
| Trigger (PR webhook / cron / `/qc run`) | **VẼ, KHÔNG CÀI** | **CÀI THẬT (một phần):** nút 🚀 trên dashboard chạy `orchestrator.py` qua subprocess, có progress 0/6→6/6 và log tail. `.github/workflows/qc-gate.yml` là **template chưa chạy trên GitHub thật** — nói rõ hai mức này khác nhau |
| GitHub Checks / Slack | **VẼ, KHÔNG CÀI** | **CÀI THẬT:** `dashboard/notifier.py` bắn webhook thật Slack/Discord/Telegram khi run kết thúc, có test, che secret. *Điều kiện: phải set `ALERT_WEBHOOK_URL` — xem §4 mục 1* |
| auto-promote | **VẼ / THIẾT KẾ XONG, KHÔNG CÀI** — "Promote demo **bằng tay**" | **CÀI THẬT:** `tools/auto_promote.py` sinh test Playwright từ finding. **Đã verify: test sinh ra exit 1 khi BUG-2 bật, exit 0 khi tắt** — tức nó bắt đúng bug, không chỉ chạy được |

Để nguyên bảng cũ mà nói miệng khác slide ⟹ mất tin cậy. Sửa bảng ⟹ được điểm. Nhưng **giữ
nguyên toàn bộ các dòng MOCK khác** (SUT giả lập, 3 bug cài sẵn, baseline viết tay, ngưỡng k6
demo, Jira mock) — đó là phần ghi điểm trung thực, không phải phần phải che.

---

## 2. Demo 2.5 phút (khớp ngân sách slide 06) — 3 hồi

Chuẩn bị: 2 cửa sổ terminal đã `. .\scripts\env.ps1`, 1 tab browser mở sẵn dashboard, toyapp
đang chạy `-Bugs "1,2,3"`. **Không** chạy pipeline live trong 2.5 phút.

### Hồi 1 (40s) — "Gate không nói dối"
```powershell
code runs\r-0019\report.md      # hoặc mở sẵn, scroll tới 3 muc
python tools\diff_runs.py runs\r-0022 runs\r-0023
```
→ `IDENTICAL — 4 kết quả gating, gate_verdict=FAIL`

**Nói:** "Report có 3 mục **không bao giờ cộng vào nhau**: deterministic, LLM judgment, discovery.
Header ghi `LLM tokens: 0 · cost $0.00` — gate xanh không gọi LLM lần nào. Và đây là lý do:
chạy lại, `diff` phần tất định **rỗng**. Không ai cãi được một diff rỗng."

⭐ Đây là 40 giây quan trọng nhất của cả bài. Không rút ngắn.

### Hồi 2 (30s) — "Ai canh người canh gác?"
```powershell
python -c "import json;print(json.load(open('runs/r-0032/report.json',encoding='utf-8'))['canary'])"
```
→ `CANARY t-canary-01: OK (fail như kỳ vọng)`

**Nói:** "`t-canary-01` là task **chắc chắn phải fail** — bấm nút 'Thanh toán' không tồn tại. Nếu
worker báo pass thì **worker hỏng**, không phải app tốt. Đây là kiểm tra tính trung thực của chính
hệ kiểm thử." Ít ai xây canary → đây là tín hiệu độ sâu, tốn 30 giây.

### Hồi 3 (60s) ⭐ MỚI — vòng lặp đóng kín: finding → test tất định
Trên dashboard, ở finding `f-sig-dom_unchanged`: bấm **🧬 Promote to Test Case** → bấm "Xem chi
tiết" (modal hiện code). Rồi terminal:
```powershell
node tests_generated\promoted_f_sig_dom_unchanged.spec.mjs    # exit 1 — bug dang bat
.\scripts\toyapp.ps1 start -Bugs "none"
node tests_generated\promoted_f_sig_dom_unchanged.spec.mjs    # exit 0
```

**Nói:** "Midscene tìm ra bug này ở discovery lane — **không được chặn merge**, vì nó là heuristic.
Một nút bấm biến `repro_steps` thành **test Playwright tất định**. Test này đỏ khi bug bật, xanh
khi bug tắt — giờ nó đủ điều kiện sang gate lane. **Đúng mô hình TestGen-LLM ở slide 02: LLM sinh
ứng viên, oracle tất định quyết định cái nào được sống.**"

**Ngay sau đó nói giới hạn, đừng đợi bị hỏi:** "Bảng map bước→code là **template tất định cho
toyapp**, không phải codegen tổng quát. Bước nào không map được thì sinh `fail` tường minh chứ
không âm thầm pass. Tổng quát hoá cần LLM codegen có người review — đó là đánh đổi bọn em chọn có
ý thức."

---

## 3. Bản mở rộng 8–10 phút (khi anh nói "cho xem thêm" / Q&A)

Dùng thời gian chờ thay vì đứng nhìn log cuộn:

1. **Phút 0:** mở dashboard, bấm 🚀 **ngay** (chạy nền ~85s). Chỉ vào thanh tiến độ 0/6.
2. **Phút 0–2:** trong lúc chờ, chiếu `examples/result.ai_eval.json` — **một** result chứa **hai**
   `verdict_source`: `f-11` (deterministic, metric độ dài) và `f-12` (`llm_judgment`, G-Eval 0.71,
   `confidence 0.64`); `verdict.value = fail` **chỉ do f-11**, điểm G-Eval **không tham gia**. Đây
   là chỗ trả lời "test AI application thế nào".
3. **Phút 2–3:** Hồi 1 + Hồi 2 ở trên.
4. **Phút 3–4:** quay lại dashboard → 6/6, run mới xuất hiện, **webhook nổ** vào Slack/Discord
   (cần §4 mục 1). Bấm link trong message → dashboard mở đúng run qua `#run=r-NNNN`.
5. **Phút 4–6:** Hồi 3 (promote → test thật).
6. **Phút 6–8:** chiếu `.github/workflows/qc-gate.yml` + nói thẳng "template, **chưa chạy trên
   GitHub thật**" (hoặc bỏ được câu này nếu làm §4 mục 3).
7. **Phút 8–10:** iframe Midscene report (bằng chứng thị giác) + `tools/validate.py` 4/4 PASS.

---

## 4. Prep checklist — theo thứ tự giá trị/công sức

| # | Việc | Vì sao đáng | Công |
|---|---|---|---|
| 1 | **Set `ALERT_WEBHOOK_URL` thật** trong `.env` (Slack incoming webhook rẻ nhất) rồi restart dashboard | Hiện `alert_channel: "none"` ⟹ tính năng webhook **chưa được chứng minh chạy thật**, chỉ có log. Rẻ nhất, tác động lớn nhất | 10 phút |
| 2 | **Sửa 3 dòng bảng slide 06** (§1) | Không sửa thì tự hạ thấp mình; nói khác slide thì mất tin cậy | 15 phút |
| 3 | **Chạy `qc-gate.yml` 1 lần trên PR nháp** | CI chính là thứ anh quan tâm khi nói "automate". Đang là món duy nhất **hoàn toàn chưa kiểm chứng**. Xoá được một caveat lớn | 1–2 giờ (có thể lỗi trên Ubuntu) |
| 4 | Soạn sẵn **câu trả lời mobile** (§5) | Anh hỏi trực tiếp về mobile. Né ⟹ mất điểm; trả lời có cấu trúc ⟹ được điểm | 15 phút |
| 5 | Chạy lại `.mjs` **ngay trước** demo + kiểm `-Bugs` state | Test phụ thuộc trạng thái bug của toyapp | 2 phút |
| 6 | Screenshot dự phòng cho mọi thứ live | Mạng/API chết giữa demo | 20 phút |

---

## 5. Câu trả lời mobile — soạn sẵn, đọc gần như nguyên văn

> "Mobile bọn em **loại có chủ ý**, không phải bỏ sót. Hai lý do: (1) PoC chạy trên Windows không
> emulator, không WSL2 — hạ tầng device farm là **bài toán hạ tầng, không phải bài toán QC**;
> (2) phủ mobile đòi viết lại **cột phải** của contract: cách bóc UI state đổi từ DOM sang
> a11y-tree/UiAutomator + logcat, và oracle phải định nghĩa lại.
>
> Điểm tốt là **contract không phải sửa** — đó chính là thứ bọn em đi chứng minh. Pha 2 roadmap chỉ
> **thêm**: `capability: mobile.ui` / `mobile.explore`, `target.kind: android_app` (+ `sut_identity`
> theo APK hash/versionCode), và `oracle.kind: crash_signals` — vì **crash/ANR trong logcat là
> oracle tất định mạnh và miễn phí**, mobile có lợi thế này mà web không có. Thêm một khái niệm
> vận hành: `resource lock` theo **thiết bị**, không theo worker.
>
> Nếu mobile là ràng buộc sản phẩm thì đảo Pha 2 lên trước Pha 3/4 được — nhưng nó là pha **đắt
> nhất** và là pha duy nhất chi phí nằm ở hạ tầng chứ không ở QC."

*Nếu bị hỏi "Midscene có làm được Android không?"* → trả lời: **chưa verify trong repo này**, PoC
chỉ chạy web. Không đoán. (Đúng quy ước nhãn `VERIFIED` / `FROM-INDEX` / `CHƯA VERIFY` của repo.)

---

## 6. KHÔNG demo — và vì sao

| Không demo | Vì sao |
|---|---|
| Chạy full pipeline live trong bản 2.5 phút | 85s dead air + phụ thuộc API LLM bên ngoài. Bản mở rộng thì chạy nền từ phút 0, khác hẳn |
| **Mock Jira ticket** | Là mock (đúng thiết kế), nhưng demo nó mời đúng câu hỏi "vậy chưa nối thật à?" — đổi một điểm mạnh thành một điểm yếu. Giữ làm Q&A nếu bị hỏi về workflow ticket |
| Số đo k6 | `plan.yaml` ghi rõ ngưỡng `p95 < 300ms` là **ngưỡng demo**, đo thật 15–25ms. Chiếu số ⟹ mời hỏi "sao ngưỡng rộng thế" |
| Dashboard làm nhân vật chính | Anh hỏi **hiểu sâu + propose có giá trị**. UI là phương tiện. Mở màn bằng UI ⟹ bạn thành người làm UI |
| Con số "65% → 80% tự động hoá" | **Không có baseline nào trong repo định nghĩa hai số này** ⟹ không kiểm chứng được. Dùng khung **7/10 bước** của slide 07 (đã có tài liệu) + "v2 lật 3 dòng từ VẼ sang CÀI THẬT" — hai phát biểu này **verify được** |

---

## 7. Ba câu cố ý KHÔNG nói (đã chốt trong `03-prior-art.md`, giữ nguyên)

1. ❌ "Chưa có ai xây orchestration cho QC" — **sai**, có Testkube.
2. ❌ "Hercules là hệ khép kín" — **sai**, có `ADDITIONAL_TOOL_DIRS` + MCP nav agent.
3. ❌ "Chưa có ai đặt LLM ra ngoài đường phán quyết" — **sai**, Playwright Test Agents đã làm trong
   phạm vi 1 framework.

Câu đúng, hẹp hơn, và vẫn đủ mạnh: **"Chưa có ai áp nguyên tắc đó xuyên nhiều loại worker kèm
`verdict_source`, đặc biệt cho AI application."**
