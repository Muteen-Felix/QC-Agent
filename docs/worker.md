# QC-Agent — 4 khâu × worker

Sơ đồ vẽ theo phong cách §3 của [architecture.md](architecture.md), nhưng thu hẹp vào một câu hỏi:
**mỗi khâu trong 4 khâu (Functional, Performance, Integration, Security) dùng worker/công cụ nào,
qua capability nào, và chặn hay chỉ tư vấn.** Chi tiết từng khâu (giới hạn, cách chặn, cách bỏ qua)
ở mục "Chi tiết theo khâu" cuối file.

```
  4 KHÂU · WORKER · CAPABILITY
  ═══════════════════════════════════════════════════════════════════════════

       ┌─────────────┬─────────────┬─────────────┬─────────────┐
       ▼             ▼             ▼             ▼

  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │FUNCTIONAL│  │PERFORM.  │  │INTEGRAT. │  │SECURITY  │
  ├──────────┤  ├──────────┤  ├──────────┤  ├──────────┤
  │worker:   │  │worker:   │  │worker:   │  │worker:   │
  │schemathe-│  │k6        │  │playwright│  │semgrep   │
  │sis       │  │cap:      │  │cap:      │  │cap:      │
  │cap:      │  │http.load │  │flow.     │  │code.sast │
  │api.      │  │suite:    │  │integr.   │  │suite:    │
  │property  │  │perf-smoke│  │suite:    │  │sast      │
  │suite:    │  │ (PR,     │  │t-020     │  │ (gate,   │
  │api-      │  │  discov.)│  │(runner   │  │  chặn)   │
  │contract  │  │perf-full │  │ giả)     │  │──────────│
  │ → GATE   │  │ (manual, │  │ → GATE   │  │worker:   │
  │──────────│  │  gate)   │  │t-021     │  │gitleaks  │
  │worker:   │  │          │  │(HAR B)   │  │cap:      │
  │pytest    │  │          │  │ → GATE   │  │code.     │
  │cap:      │  │          │  │t-022     │  │secret    │
  │api.      │  │          │  │(B thật)  │  │suite:    │
  │functional│  │          │  │ → DISCOV.│  │secrets   │
  │suite:    │  │          │  │          │  │ → GATE   │
  │gt-       │  │          │  │          │  │──────────│
  │functional│  │          │  │          │  │worker:   │
  │ → GATE   │  │          │  │          │  │trivy     │
  │ (S1)     │  │          │  │          │  │cap:      │
  │──────────│  │          │  │          │  │deps.vuln │
  │worker:   │  │          │  │          │  │suite:deps│
  │midscene- │  │          │  │          │  │ → GATE   │
  │cli       │  │          │  │          │  │          │
  │cap:      │  │          │  │          │  │          │
  │ui.explore│  │          │  │          │  │          │
  │suite:    │  │          │  │          │  │          │
  │ui-explore│  │          │  │          │  │          │
  │ → DISCOV.│  │          │  │          │  │          │
  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │             │             │             │
       ▼             ▼             ▼             ▼
   GATE nếu     GATE (perf-   GATE (t-020,  GATE (SAST +
   api-contract full) hoặc    t-021) +      secret + dep,
   hoặc gt-     DISCOVERY     DISCOVERY     chặn)
   functional;  (perf-smoke)  (t-022, B
   DISCOVERY                  thật)
   nếu ui-
   explore

  ═══════════════════════════════════════════════════════════════════════════
  (ngoài 4 khâu) — worker phụ trợ, không phải một khâu chức năng:
  ┌──────────────┐
  │  deepeval    │
  │ cap: llmapp. │
  │  eval        │
  │ → AI APP,    │
  │   DISCOVERY  │
  │   (G-Eval    │
  │    advisory) │
  └──────────────┘
```

## Bảng tra nhanh — khâu → worker → capability → lane

| Khâu | Worker (manifest) | Capability | Suite ví dụ | Lane | Chặn merge? | Trạng thái |
|---|---|---|---|---|---|---|
| Functional | `schemathesis` | `api.property` | `api-contract` | gate | có (finding Critical/Medium) | *Đã chạy* |
| Functional | `pytest` | `api.functional` | `gt-functional` | gate | có, chỉ TC `approved` (finding Critical/Medium) | *Đã chạy* (worker + adapter, suite `gt-functional` do `render.py` sinh; `noteboard` bật trong policy). Chưa đo bằng LLM thật |
| Functional | `midscene-cli` | `ui.explore` | `ui-explore` | discovery | không (tối đa Low) | *Đã chạy* |
| Performance | `k6` | `http.load` | `perf-smoke` (PR) · `perf-full` (manual) | discovery (PR) / gate (manual) | chỉ ở Mode 2 (Critical/Medium ở gate; discovery tối đa Low) | *Đã chạy* |
| Integration | `playwright` | `flow.integration` | `t-020` (runner giả) · `t-021` (HAR B) · `t-022` (B thật) | gate (t-020, t-021) / discovery (t-022) | có, trừ t-022 (tối đa Low) | *Thiết kế* → T6 |
| Security | `semgrep` | `code.sast` | `sast` | gate | có (Critical/Medium) | *Đã chạy* trong image; chưa có PR thật |
| Security | `gitleaks` | `code.secret` | `secrets` | gate | có (Critical/Medium) | *Đã chạy* trong image; chưa có PR thật |
| Security | `trivy` | `deps.vuln` | `deps` | gate | có (Critical/Medium) | *Đã chạy* trong image; chưa có PR thật |
| (AI app, chưa chạy thật) | `deepeval` | `llmapp.eval` | — | gate/discovery | metric tất định có, G-Eval chỉ tư vấn (`verdict_source=llm_judgment` tối đa Low) | chưa chạy thật — Later |

Cột "Chặn merge?" theo contract 2.0.0 (severity low/medium/critical): Critical/Medium chặn, Low không
chặn (verdict `PASSED_WITH_WARNINGS`).

**Floor của trigger `pr` (từ S2):** `semgrep` (suite `sast`) và `gitleaks` (suite `secrets`) luôn
được chạy, dù Diff Agent chọn gì; `core/` gộp lại lần hai. Trigger `manual` không có floor.

Ngoài bảng: `workers/` còn manifest `coverage-debt` (`repo.coverage_debt`): dò bề mặt mới thêm chưa có test, phát finding Low có vị trí (đi vào inline comment và Jira); xem [usage-ci.md](usage-ci.md) §4c.

**Đọc bảng này kèm quy tắc ở [core-rules.md](core-rules.md):** thêm worker mới = thêm file
(`workers/<name>.yaml` + `src/qc_agent/adapters/<name>_adapter.py`), không sửa `core/`; capability
`id` phải có sẵn trong `schemas/capabilities.json`; `parallel_safe: false` (k6, midscene, playwright)
nghĩa là worker đó độc quyền — không task nào khác chạy song song trong cùng job.

## Chi tiết theo khâu

### Performance (k6)
`perf-smoke` chạy ở PR (discovery, tư vấn); `perf-full` chạy ở manual trên staging (gate). Giới hạn:
số đo trên localhost hoặc runner dùng chung **không đại diện production**; **không bắn tải vào trang B**
(hệ thống chính phủ). Việc còn thiếu là đo thời gian một lượt RPA thật.

### Integration (Playwright)
Ba tầng dùng chung worker `playwright` nhưng khác quyền phán quyết:

1. `integration/t-020`: Socket.IO runner giả; kiểm đăng ký runner, nhận đúng job, chuỗi trạng thái hợp lệ, từ chối chuyển trạng thái sai. **Chặn merge.**
2. `integration/t-021`: UI/runner/extension với host B phát lại từ `.qc-agent/har/vahan-b.har`. Guard cố định `update:false`, `notFound:'abort'`, chặn host ngoài allowlist; request thiếu thành `har_covers_all_requests=false`. **Chặn merge.**
3. `integration-live/t-022`: cùng flow, không HAR. Discovery chỉ chạy manual, một luồng, không retry; fail nghĩa là cần kiểm tra B và ghi lại HAR. **Không chặn.**

HAR ghi bằng tài khoản thử nghiệm/dữ liệu ẩn danh, qua `tools/har_scrub.py`, grep credential, và người thứ hai xem trước khi commit (vận hành: [usage-ci.md](usage-ci.md) mục "Integration với hệ thống ngoài qua HAR"). Tầng 2 xanh chỉ chứng minh PR tương thích với hợp đồng đã ghi, không chứng minh B thật đang hoạt động.

**Chưa làm:** spike với extension thật. Request từ service worker có thể không đi qua `BrowserContext.routeFromHAR`; nếu không intercept được và extension không đổi được base URL, tầng 2 dừng ở runner và đoạn extension ↔ B thuộc tầng 3 (phải ghi rõ trong report). Chưa có PR thật nào chạy suite này.

### Security (Semgrep, gitleaks, Trivy)
Ba công cụ tất định, mỗi công cụ một worker và một suite trong `.qc-agent/suites/` của repo SUT:

| Suite | Công cụ | Bắt gì | Chặn merge khi (ngưỡng nằm trong file suite) | Bỏ qua có lý do bằng |
|---|---|---|---|---|
| `sast` | Semgrep | lỗi trong mã theo rule đã ghim (`rules/semgrep/`) | `semgrep.high == 0`, `semgrep.critical == 0`, `semgrep.files_scanned >= 1` | `# nosemgrep: <rule-id>` ngay dòng đó |
| `secrets` | gitleaks | secret/token nằm trong mã | `gitleaks.count == 0` | fingerprint trong `.gitleaksignore` |
| `deps` | Trivy | dependency có CVE đã công bố | `trivy.critical == 0`, `trivy.high == 0`, `trivy.db_age_days <= 14` | dòng trong `.trivyignore` |

Metric `high`/`critical` giữ mức gốc của công cụ; contract 2.0.0 chuẩn hoá finding thành Critical/Medium/Low và policy chặn Critical/Medium.

- **Vì sao PR bị chặn:** adapter chỉ đếm finding thành metric phẳng; oracle `threshold` so với ngưỡng trong `oracle.assertions` của file suite. Đổi ngưỡng = sửa một dòng YAML. Finding `rule @ file:dòng` hiện trong review PR (inline nếu dòng nằm trong diff; ngoài diff và mọi lỗ hổng thư viện nằm ở thân review). Mọi dòng bỏ qua nằm trong repo SUT nên hiện trong diff PR. Cách bỏ qua: [usage-ci.md](usage-ci.md) §4b.
- **Chạy offline để tái lập:** rule Semgrep vendored; DB CVE của Trivy nướng vào image (`--skip-db-update`, `trivy.db_age_days` ép build lại sau 14 ngày); gitleaks quét working tree (checkout nông, chỉ bắt secret mới; `inputs.history: true` cần `fetch-depth: 0`). Container gate vẫn ra được internet, `core/egress.py` chỉ ghi nhận khai báo, không chặn.
- **Công cụ hỏng không bao giờ là xanh:** thiếu binary ⇒ `skipped` ⇒ gate đỏ (`on_skipped_gate_task: fail`); công cụ chết, JSON hỏng, Semgrep `errors[]`, thiếu DB Trivy, thiếu lockfile ⇒ `error`. gitleaks luôn `--redact` và adapter từ chối rồi xoá báo cáo còn giá trị secret. Đường dẫn trong suite bị từ chối nếu có `..`, tuyệt đối hoặc bắt đầu bằng `-`.
- **Giới hạn:** worker và suite mẫu đã chạy trong image với output công cụ thật, nhưng **chưa có PR thật** trên repo SUT thật (`noteboard` còn chạy trên fixture). Xanh không có nghĩa là an toàn: Semgrep so khớp mẫu nên bỏ sót, gitleaks không thấy secret đã xoá khỏi cây, Trivy mù với CVE chưa vào DB. **Chưa kiểm tự động:** quyền extension (`manifest.json`) và đường đi của credential vào trang B (đọc tay khi review). DAST (ZAP) và quét container image để Later.
- **Floor:** `secrets` + `sast` luôn chạy ở trigger `pr`; trigger `manual` không có floor ([ADR 0002](adr/0002-diff-agent-chon-pham-vi-co-floor.md)).

