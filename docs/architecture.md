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

### 1.1 Toàn cảnh

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
        │  ORCHESTRATOR  ·  core/engine.py  ·  TẤT ĐỊNH, KHÔNG LLM         │
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

### 1.2 Quy tắc phán quyết — hình nhỏ, đọc kèm 1.1

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
| **Integration** | hợp đồng runner/job với runner giả (chặn) | chuỗi đầy đủ qua bản ghi HAR của B (chặn) · B thật (tư vấn, tần suất thấp) | Playwright `routeFromHAR` · (Keploy) | *Thiết kế* → T6 |
| **Security** | SAST + secret + dependency (chặn ở mức high/critical) | DAST trên web app của team · rà quyền extension và credential vào B | Semgrep · gitleaks · Trivy · (ZAP) | *Thiết kế* → T5 |

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
> **[T6]** Thiết kế ba tầng: (1) runner giả, chặn; (2) bản ghi HAR của trang B, chặn;
> (3) B thật, tư vấn, tần suất thấp, dùng để phát hiện B đổi giao diện. Khi tầng 3 fail thì việc
> cần làm là **ghi lại HAR mới**, không phải chặn dev.

### 2.4 Security
> **[T5]** Ba công cụ tất định chạy ở lane gate (Semgrep, gitleaks, Trivy), ngưỡng chặn
> critical/high, có đường bỏ qua kèm lý do. Thêm một mục người đọc: **quyền của extension**
> (`manifest.json`) và **đường đi của credential vào trang B**.

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
7. **Không làm ở giai đoạn này:** mobile, red-team, LLM phán quyết, LLM phân loại lỗi.

---

## 5. Hướng đi

> **[T4 — cần điền]** Khung Now / Next / Later + 4 quyết định cần mentor chốt: xem doc 19 §4.3.