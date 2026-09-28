# QC-Agent — Kiến trúc và hướng đi

- Phiên bản: **DRAFT** · 2026-09-28 · Trình: mentor, chiều thứ Ba 2026-09-29
- Người viết: nhóm QC-Agent (3 người)
- Sản phẩm dùng làm ví dụ xuyên suốt: **vahan-rpa** (web app → browser extension → trang B của chính phủ)
- Tài liệu nguồn (ngoài repo, thư mục `knowledge/_derived/`): spec yêu cầu `18-…`, drift check
  `19-…`, khảo sát công cụ open-source theo khâu `21-…`.
- Quy ước nhãn nguồn: `[EXTERNAL GAP]` = kiến thức ngoài repo, chưa kiểm chứng.
- Tài liệu trong repo đọc kèm: [core-rules.md](core-rules.md) · [usage-ci.md](usage-ci.md) ·
  [onboarding.md](onboarding.md).

> **Tài liệu này là kiến trúc, không phải báo cáo research.** Phần research (các cách tiếp cận,
> agent open-source, ma trận loại testing) đã nộp vòng trước và không lặp lại ở đây.

---

## 1. Sơ đồ tổng thể — 4 khâu × 2 mode

### 1.1 Hai vòng — đọc cái này trước

Hệ thống là **hai vòng lồng nhau**, khác nhau ở *quyền*, ở *nhịp chạy* và ở *chỗ LLM được phép có mặt*.

```
  ┌── VÒNG NGOÀI · AGENT · CÓ LLM · KHÔNG có quyền chặn ──────────────────────┐
  │                                                                            │
  │   PRD của SUT ─┐                                                           │
  │                ├─→  ĐỌC & CHẨN ĐOÁN (LLM)  ─→  sinh test mới              │
  │   lịch sử chạy ┘                                sửa test đã hỏng           │
  │        ▲                                        gom lỗi cùng nguyên nhân   │
  │        │                                              │                    │
  │        │                                              ▼                    │
  │        │                                     PR đề xuất → NGƯỜI DUYỆT      │
  └────────┼───────────────────────────────────────────────────┬───────────────┘
           │ kết quả · log · ảnh chụp          file đã đóng băng│
           │                                                    ▼
  ┌────────┴── VÒNG TRONG · GATE · TẤT ĐỊNH · CÓ quyền chặn merge ────────────┐
  │                                                                            │
  │   PR / bấm chạy  →  chạy đúng plan đã duyệt  →  đỏ hoặc xanh trong ~5 phút │
  │                                                                            │
  └────────────────────────────────────────────────────────────────────────────┘
```

| | Vòng trong (gate) | Vòng ngoài (agent) |
|---|---|---|
| Làm gì | Chạy những gì **đã được duyệt** | **Sinh ra** và **bảo trì** những thứ đó |
| LLM | Không, lúc chạy | Có |
| Quyền | Chặn merge | Chỉ đề xuất, người duyệt |
| Nhịp | Mọi PR, vài phút | Theo lịch, chạy lâu được |
| Hỏng thì sao | Cả phòng tắc | Không ai chặn ai, sửa sau |

**Vòng lặp có khép kín**: kết quả của gate chảy ngược lên nuôi agent. Nó chỉ không khép bằng
cách để model ứng biến lúc chạy, mà khép qua **một file có người duyệt**.

> **Tự động hóa tăng nhờ có thêm test và test sống lâu, không nhờ việc ai chọn test để chạy.**
> Đó là lý do vòng ngoài mới là chỗ LLM tạo ra giá trị, còn vòng trong thì không.

### 1.2 Chi tiết vòng trong — gate

```
  MODE 1 — TỰ ĐỘNG TRÊN PR                          MODE 2 — THỦ CÔNG THEO SPRINT/PHASE
  ════════════════════════                          ═══════════════════════════════════
  dev mở PR trên repo SUT                           QA/dev chọn project + suite trên dashboard
          │                                                     │
          ▼                                                     ▼
  CI gate (qc-gate.reusable.yml)                    API  POST /projects/{slug}/jobs
   1. pull image qc-agent (ghim digest)              1. executor nhận job, khoá môi trường
   2. fetch policy từ qc-agent@main  ← KHÔNG lấy được thì ĐỎ    2. checkout SUT
   3. dựng + chạy SUT trong container                3. trỏ vào môi trường staging
   4. phạm vi: diff của PR                           4. phạm vi: toàn sản phẩm
          │                                                     │
          └────────────────────────┬────────────────────────────┘
                                   ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  ĐIỀU PHỐI · core/engine.py · KHÔNG gọi LLM LÚC CHẠY             │
        │  thi hành policy ĐÃ ĐƯỢC NGƯỜI DUYỆT, không tự nghĩ ra việc      │
        │  policy: configs/projects/_default.yaml  +  <slug>.yaml          │
        │  plan → resolve → chọn worker theo capability → chạy → verdict   │
        └──────────────────────────────────────────────────────────────────┘
                                   │
     ┌──────────────┬──────────────┼──────────────┬──────────────┐
     ▼              ▼              ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│FUNCTIONAL│  │PERFORM.  │  │INTEGRAT. │  │SECURITY  │  │(AI APP)      │
├──────────┤  ├──────────┤  ├──────────┤  ├──────────┤  ├──────────────┤
│api-      │  │perf-smoke│  │runner/job│  │quét SAST │  │metric tất    │
│contract  │  │  (PR)    │  │contract  │  │dependency│  │định          │
│(Schemath)│  │perf-full │  │(runner   │  │secret    │  │──────────────│
│──────────│  │  (manual)│  │ giả)     │  │──────────│  │G-Eval        │
│TC từ PRD │  │   (k6)   │  │──────────│  │quyền ext.│  │(DeepEval)    │
│──────────│  │          │  │ext ↔ B   │  │cred vào B│  │              │
│ui-explore│  │          │  │(bản ghi) │  │          │  │              │
│(Midscene)│  │          │  │ext ↔ B   │  │          │  │              │
│          │  │          │  │(B thật)  │  │          │  │              │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘
     └─────────────┴─────────────┴─────────────┴───────────────┘
                                   │
                                   ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  VERDICT  ·  core/verdict.py  ·  FAIL > YELLOW > PASS            │
        │  lane=gate  → tính vào exit code (CHẶN)                          │
        │  lane=discovery → chỉ vào report (TƯ VẤN)                        │
        │  task gate bị bỏ qua → KHÔNG xanh giả (on_skipped_gate_task)     │
        └──────────────────────────────────────────────────────────────────┘
                                   │
          ┌────────────────────────┴────────────────────────┐
          ▼                                                 ▼
   ┌─────────────┐                                   ┌─────────────┐
   │  CHẶN       │  exit 1 → PR đỏ                   │  TƯ VẤN     │  exit 0
   │  (tất định) │  → dev sửa                        │  (có LLM /  │  → report + comment PR
   └─────────────┘                                   │   chạm B)   │       │
                                                     └─────────────┘       ▼
                                                                     ┌───────────┐
                                                                     │ HITL      │
                                                                     │ QA/dev    │
                                                                     │ duyệt     │
                                                                     └─────┬─────┘
                                                                           ▼
                                                                  lỗi thật → ticket cho dev
                                                                  sai → bỏ qua, ghi vết
```

**LLM nằm ở đâu trong hình trên** — câu "không gọi LLM lúc chạy" chỉ nói về **ô điều phối**,
không nói về cả hệ thống:

```
  ✔ LÚC SOẠN (vòng ngoài)  sinh TC từ PRD · gợi ý flow UI · sửa test hỏng
                           → người duyệt → đóng băng thành file
  ✔ TRONG WORKER           Midscene dò UI bằng VLM · G-Eval chấm chất lượng
                           → luôn ở lane tư vấn, không bao giờ chặn
  ✘ LÚC ĐIỀU PHỐI          không bao giờ — vì đây là chỗ cầm quyền chặn merge
```

### 1.3 Quy tắc phán quyết — hình nhỏ, đọc kèm 1.2

```
             Ai sinh ra test?                 Chạy thế nào?              Quyền
  ───────────────────────────────────────────────────────────────────────────────
   người viết ─┐                        ┌─ không LLM lúc chạy  ─┐
               ├─→ HITL duyệt 1 lần ───→┤  không chạm hệ thống  ├──→  CHẶN MERGE
   LLM sinh  ──┘   (qc-agent:todo)      └─ ngoài không kiểm soát┘
                                        ┌─ có LLM lúc chạy      ─┐
                                        │  (VLM, LLM-as-judge)   ├──→ TƯ VẤN + HITL
                                        └─ hoặc chạm trang B thật┘
```

Cột trái cho thấy **nguồn gốc của test không quyết định quyền của nó**. Test do LLM sinh, sau khi
người duyệt và nếu chạy không cần LLM, vẫn được chặn merge như test người viết.

> **Một câu tóm tắt kiến trúc:**
> *LLM được sinh ứng viên và điều khiển thao tác. LLM không được phán quyết.*
> Mọi thứ chặn merge đều tất định và đã qua người duyệt một lần.

---

## 2. Ma trận 4 khâu × 2 mode

> **[T2 — cần điền]** Mỗi ô: worker/tool · input từ SUT · output · tất định? · chặn/tư vấn ·
> HITL ở đâu · **trạng thái thật** (*Đã chạy* / *Chạy tay một lần* / *Thiết kế*) · giới hạn.
> Nguồn để điền: doc 19 §1.1. Không ô nào được để trống hay ghi "…".

| Khâu | Mode 1 — PR | Mode 2 — Thủ công | Công cụ | Trạng thái |
|---|---|---|---|---|
| **Functional** | `api-contract` (chặn) · `ui-explore` (tư vấn) | cùng suite, phạm vi toàn sản phẩm | Schemathesis · Midscene | *Đã chạy* (hợp đồng API) · *Thiết kế* (TC theo PRD) |
| **Performance** | `perf-smoke` (tư vấn) | `perf-full` (staging, chặn) | k6 | *Đã chạy* |
| **Integration** | hợp đồng Socket.IO runner/job với runner giả (chặn) | chuỗi đầy đủ qua bản ghi HAR của B (chặn) · B thật (tư vấn, tần suất thấp) | Playwright `routeFromHAR` | *Thiết kế* → T6 |
| **Security** | SAST + secret + dependency (chặn ở mức high/critical) | DAST trên web app của team · rà quyền extension và credential vào B | Semgrep · gitleaks · Trivy · (ZAP) | *Thiết kế* (worker + test đã có, chưa chạy thật — xem §2.4) |

Căn cứ chọn công cụ, kèm số sao, lần push cuối và giấy phép: `knowledge/_derived/21-…`.
Nguyên tắc chọn: **ưu tiên công cụ tất định** (được chặn merge) hơn agent LLM (chỉ tư vấn).

### 2.1 Functional
> **[T2/T7]** Hai bài toán khác nhau, viết tách: **(a) kiểm hợp đồng API** — Schemathesis, đang
> chạy; **(b) kiểm theo PRD** — chuỗi PRD → LLM sinh TC ứng viên → người duyệt một lần
> (`qc-agent:todo` + `validate`) → biên dịch thành assert tất định. Ghi rõ (b) mới là thiết kế.

### 2.2 Performance
> **[T2]** k6, đã chạy. Nêu giới hạn: số đo trên localhost hoặc runner dùng chung **không đại
> diện production**; **không bắn tải vào trang B**. Việc còn thiếu là đo thời gian một lượt RPA thật.

### 2.3 Integration

Ba tầng dùng chung worker Playwright nhưng khác quyền phán quyết:

1. `integration/t-020` dùng Socket.IO runner giả để kiểm đăng ký runner, nhận đúng job, chuỗi trạng thái hợp lệ và từ chối chuyển trạng thái sai. Tầng này chặn merge.
2. `integration/t-021` chạy UI/runner/extension với host B được phát lại từ `.qc-agent/har/vahan-b.har`. Guard giữ cố định `update:false`, `notFound:'abort'`, chặn host ngoài allowlist và biến request thiếu thành `har_covers_all_requests=false`. Tầng này chặn merge.
3. `integration-live/t-022` chạy cùng flow nhưng không có HAR. Đây là discovery chỉ chạy manual, một luồng, không retry; fail nghĩa là cần kiểm tra B và ghi lại HAR, không chặn PR.

HAR phải được ghi bằng tài khoản thử nghiệm/dữ liệu ẩn danh, qua `tools/har_scrub.py`, grep credential và được người thứ hai xem trước khi commit. Tier 2 xanh chỉ chứng minh PR tương thích với hợp đồng đã ghi, không chứng minh B thật đang hoạt động hôm nay.

Trước khi chuyển trạng thái sang *Đã chạy* phải spike extension thật: request từ service worker có thể không đi qua `BrowserContext.routeFromHAR`. Nếu không intercept được và extension không đổi được base URL, Tier 2 dừng ở runner; đoạn extension ↔ B thuộc Tier 3 và phải được ghi rõ trong report.

### 2.4 Security

Ba công cụ **tất định** chạy ở lane gate, mỗi công cụ là một worker (manifest + adapter) và một suite trong `.qc-agent/suites/` của repo SUT:

| Suite | Công cụ | Bắt gì | Chặn merge khi (ngưỡng nằm trong file suite) | Bỏ qua có lý do bằng |
|---|---|---|---|---|
| `sast` | Semgrep | lỗi trong mã nguồn theo rule đã ghim (`rules/semgrep/`) | `semgrep.high == 0`, `semgrep.critical == 0`, `semgrep.files_scanned >= 1` | `# nosemgrep: <rule-id>` ngay dòng đó |
| `secrets` | gitleaks | secret/token nằm trong mã | `gitleaks.count == 0` | fingerprint trong `.gitleaksignore` |
| `deps` | Trivy | dependency có CVE đã công bố | `trivy.critical == 0`, `trivy.high == 0`, `trivy.db_age_days <= 14` | dòng trong `.trivyignore` |

**PR bị chặn vì sao.** Adapter chỉ *đếm* finding theo mức thành metric phẳng (`semgrep.high`, `trivy.critical`, `gitleaks.count`…); oracle `threshold` (đã có sẵn) so metric với ngưỡng ghi trong file suite. Muốn biết vì sao PR đỏ: mở `.qc-agent/suites/<suite>.yaml`, đọc `oracle.assertions`, rồi xem finding `rule @ file:dòng` trong comment PR (một review riêng gắn đúng dòng nếu dòng đó nằm trong diff; ngoài diff và mọi lỗ hổng thư viện thì nằm ở thân review). Đổi ngưỡng = sửa một dòng YAML, không sửa code. Mọi dòng bỏ qua (`nosemgrep`, `.gitleaksignore`, `.trivyignore`) nằm trong repo SUT nên **hiện trong diff của PR** để người review thấy.

**Chạy offline để tái lập được.** Rule Semgrep vendored trong `rules/semgrep/` và copy vào image; DB CVE của Trivy nướng vào image lúc build (runtime `--skip-db-update`); gitleaks quét working tree (checkout nông, chỉ bắt secret *mới* đưa vào PR này; `inputs.history: true` quét lịch sử nhưng cần `fetch-depth: 0`). Đổi giá: rule/DB chỉ cập nhật khi có người build lại image, và `trivy.db_age_days` ép việc đó (image quá 14 ngày ⇒ gate đỏ). Container gate **có** ra được internet (mạng docker `qc-net` không `--internal`, `core/egress.py` chỉ ghi nhận khai báo, không chặn), nên tính offline nằm ở thiết kế của công cụ chứ không dựa vào mạng.

**Công cụ hỏng không bao giờ là xanh.** Thiếu binary ⇒ `skipped` ⇒ gate FAIL (mode `pr` đặt `on_skipped_gate_task: fail`); công cụ chết, JSON hỏng, Semgrep báo `errors[]`, thiếu DB Trivy, repo không có lockfile ⇒ `error` ⇒ gate đỏ nhãn hạ tầng. gitleaks luôn chạy với `--redact` và adapter từ chối (rồi xoá) báo cáo còn giá trị secret, để chính gate quét secret không làm rò secret vào artifact. Đường dẫn trong suite bị từ chối nếu có `..`, tuyệt đối hoặc bắt đầu bằng `-`.

**Trạng thái và giới hạn (nói thẳng).**
- Đã có worker, suite mẫu (`qc-agent init` sinh sẵn), review gắn dòng và test âm tính; **chưa chạy bằng công cụ thật trong image** và chưa bật ở `configs/projects/` (suite mới chưa nằm trong `blocking_suites` cho tới bước ghép).
- **Xanh không có nghĩa là an toàn.** Semgrep so khớp mẫu nên chắc chắn bỏ sót; bộ rule khởi điểm còn nhỏ. Gate chỉ bắt *lỗi đã có luật* và *CVE đã công bố*.
- gitleaks mặc định không thấy secret đã bị xoá khỏi cây nhưng còn trong lịch sử; Trivy mù với CVE chưa vào DB của nó.
- **Chưa kiểm tự động:** quyền của extension (`manifest.json`) và **đường đi của credential vào trang B** — vẫn là mục đọc tay khi review. DAST (ZAP) và quét container image để Later (§5.4).

---

## 3. Áp vào vahan-rpa

> **[T2 — cần điền]** Mỗi khâu một ví dụ **lấy từ sản phẩm thật**, không ví dụ chung chung.
> Vật liệu có sẵn: endpoint `GET /api/health`, `/api/runners`, `POST /api/jobs`,
> `POST /api/jobs/{job_id}/upload-excel`; UI `apps/web-ui` (Vite); luồng nghiệp vụ
> **chọn filter → apply → tải report** trên trang B.

---

## 4. Nguyên tắc và ranh giới

1. **Chặn merge chỉ dành cho kiểm tra tất định** = không LLM lúc chạy **và** không phụ thuộc hệ thống ngoài không kiểm soát.
2. **Mọi test đều qua người duyệt một lần lúc init** (`qc-agent:todo` + `validate`), sau đó chạy và phán tự động.
3. **Không bắn tải vào trang B** (hệ thống chính phủ). Performance chỉ đo thành phần của team. *(D5 — chờ owner xác nhận)*
4. **Test chạm B thật không được chặn merge**, vì B có thể chậm, đổi giao diện hoặc bật captcha. Gate dùng bản ghi của B. *(D6 — chờ owner xác nhận)*
5. **Policy tập trung tại qc-agent@main**, lấy không được thì gate đỏ. Không dùng bản chụp trong image.
6. **Multi-repo, không hardcode**: mọi thứ riêng của một sản phẩm nằm trong cấu hình, không nằm trong code.
7. **LLM được phép lúc soạn, không được phép lúc chạy điều phối.** Sản phẩm của LLM phải là một
   file đọc được, diff được và duyệt được, trước khi nó có quyền ảnh hưởng tới ai.
8. **Vòng ngoài không bao giờ tự merge.** Mọi thứ nó sinh ra đều đi qua PR và người duyệt.
9. **Không làm ở giai đoạn này:** mobile, red-team, LLM phán quyết lúc chạy.

---

## 5. Hướng đi: từ dây chuyền thành agent

### 5.1 Agentic nghĩa là gì, và hiện thiếu gì

Một hệ thống được gọi là agentic khi có đủ 5 tính chất. Trạng thái hôm nay:

| Tính chất | Hôm nay | Nằm ở vòng nào |
|---|---|---|
| Dùng được công cụ | ✅ 5 worker | trong |
| Tự sinh việc cần làm | ❌ test sinh từ OpenAPI, tức từ **code**, không từ **yêu cầu** | ngoài |
| Vòng lặp: chạy → đọc → thử lại | ❌ chạy một lượt rồi dừng | ngoài |
| Nhớ giữa các lần chạy | ❌ mỗi lần chạy là một tờ giấy trắng | ngoài |
| Tự sửa khi hỏng | ❌ hỏng thì người sửa | ngoài |

**Bốn thứ còn thiếu đều nằm ở vòng ngoài.** Vòng trong đã làm xong việc của nó.

### 5.2 Vì sao ô điều phối không dùng LLM — trả lời trước câu hỏi khó nhất

Ô điều phối chỉ quyết 4 thứ: chạy suite nào (policy quy định), worker nào nhận task (capability
quy định), thứ tự nào (phụ thuộc quy định), đỏ hay xanh (lane và ngưỡng quy định). **Cả bốn đều
chỉ có một đáp án đúng.** Đưa LLM vào đây không thêm năng lực nào, chỉ thêm chi phí, độ trễ và
sai số.

Quan trọng hơn: gate có quyền chặn code của người khác, nên phải trả lời được câu *"vì sao PR
của tôi bị chặn"*. Tất định thì mở file policy ra là thấy. Dùng LLM thì chạy lại lần nữa có khi
ra kết quả khác — và như vậy không còn là quality gate.

Ranh giới thật **không phải** "có LLM hay không", mà là:

> **LLM được phép lúc soạn. Không được phép lúc chạy.**

Vì lúc soạn, sản phẩm của LLM là **một file**: đọc được, diff được trong PR, duyệt được, và đóng
băng được để chạy lại y hệt nghìn lần. Còn quyết định lúc chạy thì không để lại gì để xem trước,
không ai duyệt được, và lần sau sẽ khác.

Ba lý do phải tách vòng ngoài khỏi vòng trong, chứ không nhập làm một:

1. **Quyền.** Nhập lại là LLM thừa hưởng quyền chặn merge.
2. **Nhịp.** Vòng trong phải xong trong vài phút ở *mọi* PR; vòng ngoài được chạy 20 phút, chạy đêm, thử lại. Nhập lại thì mọi PR của mọi team gánh chi phí đó.
3. **Hỏng.** LLM hết quota thì vòng ngoài dừng, không sao. Nhập lại thì gate của cả phòng phụ thuộc một nhà cung cấp bên ngoài.

### 5.3 Now / Next / Later

| Chặng | Làm gì | Đầu ra kiểm được |
|---|---|---|
| **Now** — đã có | Gate PR tất định (hợp đồng API) · perf smoke và full · onboarding một lệnh cho repo mới · chạy thủ công qua dashboard | Đã chạy trên bản copy vahan-rpa |
| **Next** — 1–2 sprint | Worker security (chặn high/critical) · Integration qua bản ghi HAR · **PRD → TC → người duyệt** (vòng ngoài, bước 1) | Mỗi khâu có ít nhất một suite gate chạy trên PR thật |
| **Later** | **Trí nhớ finding** · **tự chữa khi trang B đổi** · gom lỗi cùng nguyên nhân · EvalGate chấm TC · GitLab · mobile · AI app | Có số liệu sau ít nhất một sprint chạy thật |

Thứ tự trong vòng ngoài, xếp theo *rẻ và cứu được nhiều nhất trước*:

1. **Trí nhớ finding.** Rẻ nhất, đã có sẵn Postgres. Thiếu nó thì sau khoảng hai tuần QA ngừng đọc nhánh tư vấn, vì không có gì phân biệt test chập chờn với lỗi thật — và lúc đó cả nửa phải của sơ đồ thành đồ trang trí.
2. **Tự chữa khi trang B đổi giao diện.** Đúng nỗi đau lớn nhất của một sản phẩm RPA.
3. **PRD → TC.** Nặng nhất, nhưng là thứ duy nhất chứng minh đây là *Agent* QC chứ không phải CI.
4. **Gom lỗi cùng nguyên nhân.** Đẹp, không cấp thiết.

### 5.4 Cần mentor chốt

1. Tiếp tục pilot trên vahan-rpa, hay đổi sang sản phẩm khác?
2. GitHub hay GitLab đi trước?
3. Có được gửi PRD, mã nguồn hoặc nhãn giao diện của sản phẩm qua LLM bên ngoài không?
4. Chấp nhận cách đo 80–90% theo định nghĩa ở spec (`knowledge/_derived/18-…` §6) không?
   Hiện **chưa có baseline thời gian QC**, nên tài liệu này không hứa con số nào.
