# QC-Agent — Kiến trúc

Tài liệu này mô tả kiến trúc của QC-Agent: hệ thống nào chạy ở đâu, LLM được làm gì và không được
làm gì, và vì sao. Dành cho dev/QA mới vào team. Cập nhật lần cuối: 2026-10-03.

- **Code đã tới đâu** (trạng thái từng thành phần, DoD, việc còn lại): [implementation-plan.md](implementation-plan.md)
- **Vì sao thiết kế như vậy** (từng quyết định): [adr/](adr/README.md)

| Đọc tiếp | Khi nào |
|---|---|
| [groundtruth.md](groundtruth.md) | Vận hành vòng ngoài: PRD → test case → QA duyệt |
| [usage-ci.md](usage-ci.md) | Gắn gate vào repo SUT, secret, bỏ qua finding |
| [worker.md](worker.md) | 4 khâu × worker, giới hạn từng công cụ |
| [core-rules.md](core-rules.md) | Quy tắc code của `core/`, thêm worker |
| [onboarding.md](onboarding.md) | Onboard một repo mới bằng một lệnh |
| [requirement-spec.md](requirement-spec.md) | SRS |

---

## 1. Tổng quan

QC-Agent là một **quality gate cho PR**, kèm một **bộ sinh test từ PRD**. Hai việc này tách biệt về
quyền, nhịp chạy và chỗ LLM được có mặt:

> **LLM được sinh ứng viên và chọn phạm vi ngoài floor. LLM không được phán quyết, và mọi thứ nó
> ảnh hưởng đều là một file đọc được.**
> Mọi thứ chặn merge đều tất định (cùng `selection.json` và cùng kết quả worker thì cùng verdict)
> và đã qua người duyệt một lần.

Hệ thống dùng chung cho nhiều repo SUT (multi-repo): mọi thứ riêng của một sản phẩm nằm trong policy và `.qc-agent/` của repo đó. Tài liệu mô tả kiến trúc chung, không gắn với sản phẩm nào; sản phẩm pilot và dữ liệu mẫu nằm ở [implementation-plan.md](implementation-plan.md). "Hệ thống ngoài" nghĩa là bất kỳ hệ thống nào SUT gọi tới mà team không sở hữu.

## 2. Hai vòng, hai trục thời gian

Hệ thống là **hai vòng lồng nhau**. Chúng chạy trên hai trục thời gian khác nhau và **không nối trực
tiếp với nhau**: chúng nối qua **một thư mục file đã có người duyệt** (`.qc-agent/**` trên nhánh
mặc định).

```
  ══ VÒNG NGOÀI · theo PRD · CÓ LLM · sản phẩm là FILE ═══════════════════════════

     BA đưa PRD  (Markdown · OpenAPI · text)
            │
            ▼
     (1) GROUND-TRUTH ENGINE
         LLM sinh test case dạng JSON — KHÔNG sinh code
         (một lời gọi, hoặc agent đọc PRD + mã nguồn + OpenAPI)
         render TẤT ĐỊNH ──→ test-cases.yaml · tests_gt/ · suite · module-map.yaml
         bộ chấm coverage tất định (AC · technique · API)
            │   mọi thứ sinh ra mang status: draft
            ▼
         PR  qc-agent/gt/<prd-id>
            │
            ▼
         QA duyệt:  draft → approved  ·  thêm edge case (origin: qa)
            │   merge
            ▼
         .qc-agent/**  trên nhánh mặc định — khoá bằng CODEOWNERS + branch protection
                       │
  ─────────────────────│────────────────────────────────────────────────────────
     file ĐÃ DUYỆT, đóng băng  →  gate chỉ chạy TC status: approved
                       │
  ══ VÒNG TRONG · mỗi PR · vài phút · verdict TẤT ĐỊNH ══════│════════════════════
                       ▼
     dev mở PR ──→ (2) CHỌN PHẠM VI ─────────→ (3) GATE ────────→ PHẢN HỒI
                   prune → path rules →        chạy đúng phạm vi   inline review
                   Diff Agent → floor          chấm tất định       + Jira (Low)
                   ⇒ selection.json            BLOCKED / PASSED…
  ══════════════════════════════════════════════════════════════════════════════
```

| | (1) Ground-Truth | (2) Chọn phạm vi | (3) Gate |
|---|---|---|---|
| Làm gì | **sinh ứng viên** TC từ PRD | **chọn worker** cần chạy cho PR này | chạy cái **đã được duyệt** và **phán** |
| LLM | có (`QC_GT_MODEL`) | có (`QC_SELECTOR_MODEL`), bị bao vây — [ADR 0002](adr/0002-diff-agent-chon-pham-vi-co-floor.md) | không |
| Sản phẩm | file qua PR, QA duyệt | `selection.json` (nằm trong artifact) | verdict + findings |
| Quyền | chỉ đề xuất | chọn phạm vi **ngoài floor**; không phán | chặn merge |
| Nhịp | theo PRD, chạy lâu được | mỗi PR, mục tiêu P95 ≤ 20 giây | mỗi PR, vài phút |
| Hỏng thì sao | không ai chặn ai, sửa sau | FULL SET: chạy nhiều hơn, không tắc | cả phòng tắc |

Vòng lặp khép kín không bằng cách để model ứng biến lúc chạy, mà qua **một file có người duyệt**:
vòng ngoài đề xuất, QA duyệt, vòng trong lấy đúng file đó mà chạy.

Về câu *"diff chỉ có sau khi tạo PR, vậy sinh plan trước PR thì dựa vào gì"*:

> **Diff chọn worker chạy *trong* allowlist của policy. Diff không sinh ra bộ test.**

Bộ test có sẵn từ trước và đã qua QA duyệt (vòng ngoài). Selector chỉ thu hẹp lượt chạy, hợp lệ vì:
đầu ra là một file đọc được; **floor** (secrets + sast) luôn chạy; mọi lỗi của LLM rơi về **FULL SET**.
Tự động hóa tăng nhờ có thêm test và test sống lâu, không nhờ việc ai chọn test để chạy; Diff Agent
chỉ để chạy ít hơn, rẻ hơn.

## 3. Vòng trong — hai trigger

**Trigger** quyết *ai chọn phạm vi chạy*; **mode** quyết *policy nào* áp (`modes.pr` /
`modes.manual` trong `configs/projects/`). Hai trục tách biệt.

```
  TRIGGER = pr  (tự động trên PR)                 TRIGGER = manual  (CLI · workflow_dispatch)
  ═══════════════════════════════                 ═══════════════════════════════════════════
  dev mở PR trên repo SUT                         QA/dev chỉ định danh sách worker
          │                                       (dashboard/API: xem ghi chú bên dưới)
          ▼                                                     │
  CI gate (qc-gate.reusable.yml)                                │
   1. pull image qc-agent (ghim digest)                         │
   2. fetch policy từ qc-agent@main                             │
        ← KHÔNG lấy được thì ĐỎ                                 │
   3. dựng + chạy SUT trong container                           │
          │                                                     │
          ▼                                                     │
  ┌─────────────────────────────────────────────┐               │
  │ CHỌN PHẠM VI · selector/                    │               │
  │ 1. prune: git diff -w base...head; bỏ       │               │
  │    lockfile/generated/binary/hunk comment   │               │
  │ 2. path rules (tất định, KHÔNG LLM):        │               │
  │      khớp full_set_paths  → FULL SET        │               │
  │      chỉ chạm docs        → chỉ floor       │               │
  │      module-map.yaml      → gợi ý (chỉ thêm)│               │
  │ 3. Diff Agent (timeout 20s):                │               │
  │      chọn worker ∈ allowlist của policy     │               │
  │ 4. floor (secrets + sast) luôn được gộp     │               │
  │ ⇒ selection.json                            │               │
  │ LLM lỗi / hết thời gian ⇒ FULL SET          │               │
  └──────────────────────┬──────────────────────┘               │
                         └───────────────────┬──────────────────┘
                                             ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  ĐIỀU PHỐI · core/engine.py · KHÔNG gọi LLM                      │
        │  thi hành policy ĐÃ ĐƯỢC NGƯỜI DUYỆT + selection.json            │
        │  trigger=pr:     gộp floor LẦN 2 (dù selection.json bị sửa tay) │
        │  trigger=manual: đúng các worker được chỉ định, không floor      │
        │  plan → resolve → chọn worker theo capability → chạy → verdict   │
        └──────────────────────────────────────────────────────────────────┘
                                             │
       ┌─────────────┬─────────────┬─────────┴───┬─────────────┬─────────────┐
       ▼             ▼             ▼             ▼             ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │FUNCTIONAL│  │PERFORM.  │  │INTEGRAT. │  │SECURITY  │  │(AI APP)  │
  ├──────────┤  ├──────────┤  ├──────────┤  ├──────────┤  ├──────────┤
  │api-      │  │perf-smoke│  │tích hợp  │  │quét SAST │  │metric tất│
  │contract  │  │  (PR)    │  │nội bộ    │  │dependency│  │định      │
  │(Schemath)│  │perf-full │  │(giả lập) │  │secret    │  │──────────│
  │──────────│  │  (manual)│  │          │  │──────────│  │G-Eval    │
  │gt-func.  │  │   (k6)   │  │──────────│  │quyền truy│  │(DeepEval)│
  │(pytest,  │  │          │  │tích hợp  │  │cập,cred  │  │          │
  │ chỉ TC   │  │          │  │ngoài     │  │(đọc tay) │  │          │
  │ approved)│  │          │  │(bản ghi) │  │          │  │          │
  │──────────│  │          │  │──────────│  │          │  │          │
  │ui-explore│  │          │  │tích hợp  │  │          │  │          │
  │(Midscene)│  │          │  │ngoài     │  │          │  │          │
  │──────────│  │          │  │(thật)    │  │          │  │          │
  │coverage- │  │          │  │          │  │          │  │          │
  │debt (Low)│  │          │  │          │  │          │  │          │
  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
       └─────────────┴─────────────┼─────────────┴─────────────┘
                                   │
                                   ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  VERDICT · core/verdict.py · hàm thuần, không LLM                │
        │  BLOCKED   có finding critical/medium, hoặc task gate            │
        │            bị error/skipped                          → exit 1    │
        │  PASSED_WITH_WARNINGS   chỉ còn Low     → exit = --warn-exit (0) │
        │  PASSED    không có finding                          → exit 0    │
        │  lane=discovery hoặc verdict_source=llm_judgment → tối đa Low    │
        │  task gate bị bỏ qua → KHÔNG xanh giả (on_skipped_gate_task)     │
        └──────────────────────────────────────────────────────────────────┘
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
         ┌─────────────┐                      ┌─────────────────────────┐
         │  BLOCKED    │  exit 1              │  PASSED                 │  exit 0
         │             │  → PR đỏ             │  PASSED_WITH_WARNINGS   │  → Check xanh
         │             │  → dev sửa           │                         │
         └──────┬──────┘                      └────────────┬────────────┘
                └──────────────────┬───────────────────────┘
                                   ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  PHẢN HỒI · pr_review                                            │
        │  inline comment đúng dòng cho MỌI worker; finding ngoài diff     │
        │  hoặc không có vị trí nằm ở thân review; chống đăng lặp          │
        │  finding Low → ticket Jira gán tác giả PR (chống trùng bằng      │
        │  fingerprint). Jira/GitHub lỗi → chỉ cảnh báo, verdict giữ nguyên│
        └──────────────────────────────────────────────────────────────────┘
```

**Ghi chú kỹ thuật**
- Lệnh `qc-agent run` không kèm `--trigger` giữ hành vi gốc (toàn bộ policy của mode `pr`). Đường PR là
  `qc-agent select` → `selection.json` → `qc-agent run --trigger pr --selection …`.
- Chạy tay qua dashboard/API (executor khoá môi trường, checkout SUT, trỏ vào staging) hiện có phạm vi
  = toàn bộ policy; chưa nối với `--trigger manual --workers`.
- Diff của PR lấy từ **merge-base** (`base...head`, không phải `base..head`), nên bước Select cần `fetch-depth: 0`.
- `core/` **không** gọi LLM và **không** import `qc_agent.llm` / `groundtruth` / `selector` ở top-level
  ([ADR 0001](adr/0001-core-khong-dung-llm.md)). Lệnh CLI dùng import lười, để `--trigger manual` không kéo LLM vào tiến trình.
- Hộp VERDICT chuẩn hoá finding theo contract 2.0.0 (`severity_hint` ∈ low/medium/critical), áp severity
  policy rồi trả verdict. Vượt trần token ⇒ FULL SET là thiết kế của S4, chưa có code.

**LLM nằm ở đâu:**

```
  ✔ LÚC SOẠN (vòng ngoài)      Ground-Truth: PRD → TC dạng JSON → render tất định
                               → PR → QA duyệt → đóng băng thành file
  ✔ LÚC CHỌN PHẠM VI           Diff Agent chọn worker ∈ allowlist, ngoài floor
                               → sản phẩm duy nhất là selection.json; lỗi ⇒ FULL SET
  ✔ TRONG WORKER               Midscene dò UI bằng VLM · G-Eval chấm chất lượng
                               → tối đa Low, không bao giờ chặn
  ✘ LÚC PHÁN QUYẾT (core/)     không bao giờ: core/ không gọi LLM, verdict là hàm thuần
```

**Nguồn gốc test không quyết định quyền của nó.** Chỉ cần worker chạy không cần LLM, không chạm hệ
thống ngoài không kiểm soát, và test đã qua người duyệt một lần là được chặn merge, dù test do người
hay LLM sinh:

```
   người viết ─┐                        ┌─ không LLM trong worker ─┐
               ├─→ HITL duyệt 1 lần ───→┤  không chạm hệ thống     ├──→  CHẶN MERGE
   LLM sinh  ──┘   (qc-agent:todo ·     └─ ngoài không kiểm soát ──┘
                    draft → approved)   ┌─ có LLM lúc chạy         ─┐
                                        │  (VLM, LLM-as-judge)      ├──→ TƯ VẤN (tối đa Low)
                                        └─ hoặc chạm hệ thống ngoài thật ──┘
```

## 4. Khái niệm dễ nhầm

**"Plan" là hai thứ khác nhau.**

| | **Plan A — bộ test** | **Plan B — lượt chạy** |
|---|---|---|
| Là gì | TC / test / suite trong `.qc-agent/` (`ground-truth/` song song `suites/`) | danh sách task cụ thể của một lần chạy |
| Ai sinh | vòng ngoài: LLM sinh, render tất định, QA duyệt | `core/plan.py` + policy + `selection.json`, tất định |
| Khi nào | khi PRD mới hoặc đổi, không gắn với PR nào | sau khi PR của dev tồn tại, lúc gate chạy |
| Cần diff không | không | có (selector dùng diff để chọn phạm vi) |

Regression test phải có sẵn *từ trước* mới chặn được PR hôm nay. Test chỉ sinh sau khi thấy diff
là viết test theo code, không còn là gate.

**Hai loại PR.** PR **của agent** (`qc-agent/gt/<prd-id>`, nội dung là file ground-truth, merge vào
`.qc-agent/`) khác PR **của dev** (nội dung là code sản phẩm).

**Ba trigger.**

| Trigger | Tín hiệu | Việc |
|---|---|---|
| PRD mới hoặc đổi | BA commit PRD (`prd_sha256` đổi) | `gt generate` lần đầu; `gt regen` khi PRD đổi, **merge theo `tc_id`**, không bao giờ ghi đè TC `approved` hay `origin: qa` → PR `qc-agent/gt/<prd-id>` |
| PR của dev | `pull_request` | selector → gate (`--trigger pr`); Select lỗi thì gate chạy toàn policy |
| Chạy tay | `workflow_dispatch` hoặc CLI | `--trigger manual --workers a,b`: đúng các worker đó, không LLM, không floor |

Bẫy vận hành: PR do `GITHUB_TOKEN` mở **không** kích hoạt workflow `pull_request` khác, nên CI
`gt validate` trên PR sinh GT chỉ chạy khi QA push commit (đúng quy trình, vì QA phải sửa
`draft → approved`) hoặc khi dùng token của GitHub App/PAT.

**PR chưa có test thì vẫn xanh.** Gate chỉ đảm bảo *không làm hỏng cái đã duyệt*, không đảm bảo
*cái mới có được kiểm*. Thứ tự nghiệp vụ là PRD → Ground-Truth → QA duyệt → dev mở PR, nên hành vi
*có trong PRD* đã có TC `approved` để chặn; hành vi PRD không nói tới thì không được kiểm. Cách đóng
khoảng trống: BA cập nhật PRD → `gt regen` → QA duyệt, hoặc QA thêm edge case (`origin: qa`).

**Bước `refine` trên PR** (`qc-gate.reusable.yml`, `continue-on-error: true`) chỉ đề xuất bằng khối
review `suggestion` và artifact `refine.patch`, không có quyền chặn; nằm ngoài kiến trúc này.

## 5. Bốn khâu × hai mode

Mode `pr` là policy của trigger `pr`; mode `manual` là policy của trigger `manual` (hoặc dashboard/API).
"Chặn" theo severity policy: Critical/Medium chặn, Low chỉ cảnh báo. Nguyên tắc chọn công cụ:
**ưu tiên công cụ tất định** (được chặn merge) hơn agent LLM (chỉ tư vấn).

| Khâu | Mode `pr` | Mode `manual` | Công cụ |
|---|---|---|---|
| **Functional** | `api-contract` (chặn) · `gt-functional` (chặn, chỉ TC `approved`) · `ui-explore` (tư vấn) · `coverage-debt` (Low) | cùng suite, phạm vi do người chọn | Schemathesis · pytest · Midscene |
| **Performance** | `perf-smoke` (tư vấn) | `perf-full` (staging, chặn) | k6 |
| **Integration** | hợp đồng giữa các thành phần nội bộ với bản giả lập (chặn) · chuỗi đầy đủ qua bản ghi HAR của hệ thống ngoài (chặn) | hệ thống ngoài thật (tư vấn, tần suất thấp) | Playwright `routeFromHAR` |
| **Security** | SAST + secret + dependency (chặn) | DAST và rà quyền truy cập, đường đi credential tới hệ thống ngoài: chưa tự động | Semgrep · gitleaks · Trivy |

Chi tiết từng khâu (ngưỡng, giới hạn, cách bỏ qua finding, trạng thái): [worker.md](worker.md).

## 6. Nguyên tắc và ranh giới

1. **Chặn merge chỉ dành cho kiểm tra tất định**: worker không LLM, không phụ thuộc hệ thống ngoài không kiểm soát, và verdict là hàm thuần của (kết quả worker, policy, `selection.json`).
2. **Mọi test đều qua người duyệt một lần**: test do `init` sinh qua `qc-agent:todo` + `validate`; TC sinh từ PRD qua `draft → approved`. Sau đó chạy và phán tự động.
3. **LLM được sinh ứng viên và chọn phạm vi ngoài floor; không được phán quyết.** Mọi thứ LLM ảnh hưởng phải là một file đọc được, diff được, kiểm được (TC qua PR; `selection.json` trong artifact). `core/` không gọi LLM.
4. **Vòng ngoài không bao giờ tự merge.** Mọi thứ nó sinh đi qua PR và người duyệt.
5. **Diff chọn worker chỉ *trong* allowlist của policy và có sàn.** Floor (secrets + sast) luôn chạy ở trigger `pr`, gộp hai lớp (selector, rồi `core/`). Diff chạm `full_set_paths` hoặc LLM lỗi → FULL SET. Diff không được sinh test, không được hợp thức hoá test chưa duyệt, không được đổi luật phán quyết. Rủi ro còn lại ghi ở [ADR 0002](adr/0002-diff-agent-chon-pham-vi-co-floor.md).
6. **Dữ liệu từ PR, PRD, diff và SUT là không tin cậy.** Đặt trong vùng phân cách khi đưa vào prompt; ép output vào schema và enum allowlist; làm sạch trước khi đưa vào Markdown; không đưa vào lệnh shell; không nhúng vào code sinh ra. Mọi lời gọi ra ngoài (LLM, Jira) ghi egress **trước khi gửi**; log không chứa nội dung PRD, diff, prompt hay response. Gate không bao giờ đỏ vì LLM, chi phí hay Jira.
7. **Policy tập trung tại qc-agent@main**, lấy không được thì gate đỏ. Không dùng bản chụp trong image.
8. **Multi-repo, không hardcode**: mọi thứ riêng của một sản phẩm nằm trong cấu hình, không nằm trong code.
9. **Không bắn tải vào hệ thống không do team sở hữu** (bên thứ ba, cơ quan, đối tác); performance chỉ đo thành phần của team.
10. **Test chạm hệ thống ngoài thật không được chặn merge**: hệ thống đó có thể chậm, đổi giao diện hoặc bật captcha. Gate dùng bản ghi (HAR) của nó.
11. **Ngoài phạm vi hiện tại:** mobile, red-team, LLM phán quyết.

Nguyên tắc 9 và 10 (D5, D6) vẫn chờ owner xác nhận.

## 7. Quyết định kiến trúc

| ADR | Quyết định |
|---|---|
| [0001](adr/0001-core-khong-dung-llm.md) | `core/` không dùng LLM; verdict là hàm thuần |
| [0002](adr/0002-diff-agent-chon-pham-vi-co-floor.md) | LLM được chọn phạm vi chạy, nhưng bị bao vây và có floor |
| [0003](adr/0003-gt-llm-sinh-du-lieu-khong-sinh-code.md) | Ground-Truth: LLM sinh dữ liệu, code test render tất định |
| [0004](adr/0004-llm-qua-httpx-chon-provider-theo-model.md) | Gọi LLM qua `httpx`, không SDK; chọn provider theo tiền tố model |

## 8. Trạng thái và lộ trình

Kiến trúc trên là bản đích; code đã chạy phần lớn (gate, selector, Ground-Truth, verdict mới, PR review,
Jira) nhưng **phần LLM chưa được đo bằng model thật** và **chưa có PR thật trên repo SUT thật**. Trạng thái
từng thành phần, DoD từng sprint, việc còn lại (cache, trần token, E2E trên CI thật) và các câu hỏi chờ
chốt (kể cả câu chặn mọi lượt chạy LLM thật: có được gửi PRD/mã/diff ra LLM ngoài không) nằm ở
[implementation-plan.md](implementation-plan.md).

Việc để sau: tự chữa khi hệ thống ngoài đổi giao diện (đọc tín hiệu `har_covers_all_requests = false`),
gom lỗi cùng nguyên nhân, DAST (ZAP) và quét container image, GitLab, mobile, AI app.
