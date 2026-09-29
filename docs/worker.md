# QC-Agent — 4 khâu × worker

Sơ đồ vẽ theo phong cách §1.2 của [architecture.md](architecture.md), nhưng thu hẹp vào một câu hỏi:
**mỗi khâu trong 4 khâu (Functional, Performance, Integration, Security) dùng worker/công cụ nào,
qua capability nào, và chặn hay chỉ tư vấn.** Bảng chi tiết (input/output/HITL/trạng thái) đọc ở
[architecture.md §2](architecture.md#2-ma-trận-4-khâu--2-mode).

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
| Functional | `schemathesis` | `api.property` | `api-contract` | gate | có (từ S3: finding Critical/Medium) | *Đã chạy* |
| Functional | `pytest` | `api.functional` | `gt-functional` | gate | có, chỉ TC `approved` (từ S3: finding Critical/Medium) | *Đang triển khai — S1* |
| Functional | `midscene-cli` | `ui.explore` | `ui-explore` | discovery | không (từ S3: tối đa Low) | *Đã chạy* |
| Performance | `k6` | `http.load` | `perf-smoke` (PR) · `perf-full` (manual) | discovery (PR) / gate (manual) | chỉ ở Mode 2 (từ S3: Critical/Medium ở gate; discovery tối đa Low) | *Đã chạy* |
| Integration | `playwright` | `flow.integration` | `t-020` (runner giả) · `t-021` (HAR B) · `t-022` (B thật) | gate (t-020, t-021) / discovery (t-022) | có, trừ t-022 (từ S3: t-022 tối đa Low) | *Thiết kế* → T6 |
| Security | `semgrep` | `code.sast` | `sast` | gate | có (hôm nay: high/critical; từ S3: Critical/Medium) | *Đã chạy* trong image; chưa có PR thật |
| Security | `gitleaks` | `code.secret` | `secrets` | gate | có (từ S3: Critical/Medium) | *Đã chạy* trong image; chưa có PR thật |
| Security | `trivy` | `deps.vuln` | `deps` | gate | có (hôm nay: high/critical; từ S3: Critical/Medium) | *Đã chạy* trong image; chưa có PR thật |
| (AI app, chưa chạy thật) | `deepeval` | `llmapp.eval` | — | gate/discovery | metric tất định có, G-Eval chỉ tư vấn (từ S3: `verdict_source=llm_judgment` tối đa Low) | chưa chạy thật — Later |

Cột "Chặn merge?" mô tả hành vi **hôm nay**; phần "(từ S3)" là đích của contract 2.0.0 (severity
low/medium/critical): Critical/Medium chặn, Low không chặn (verdict `PASSED_WITH_WARNINGS`).

**Floor của trigger `pr` (từ S2):** `semgrep` (suite `sast`) và `gitleaks` (suite `secrets`) luôn
được chạy, dù Diff Agent chọn gì; `core/` gộp lại lần hai. Trigger `manual` không có floor.

Ngoài bảng: `workers/` còn manifest `coverage-debt` (`repo.coverage_debt`) — tạm ngoài luồng v2, không mô tả ở đây.

**Đọc bảng này kèm quy tắc ở [core-rules.md](core-rules.md):** thêm worker mới = thêm file
(`workers/<name>.yaml` + `src/qc_agent/adapters/<name>_adapter.py`), không sửa `core/`; capability
`id` phải có sẵn trong `schemas/capabilities.json`; `parallel_safe: false` (k6, midscene, playwright)
nghĩa là worker đó độc quyền — không task nào khác chạy song song trong cùng job.
