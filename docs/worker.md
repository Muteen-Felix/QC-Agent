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
  │sis       │  │──────────│  │──────────│  │──────────│
  │cap:      │  │cap:      │  │cap:      │  │cap:      │
  │api.      │  │http.load │  │flow.     │  │code.sast │
  │property  │  │──────────│  │integration│ │──────────│
  │suite:    │  │perf-smoke│  │suite:    │  │suite:    │
  │api-      │  │ (PR,     │  │t-020     │  │sast      │
  │contract  │  │  discov.)│  │(runner   │  │ (gate,   │
  │ → GATE   │  │perf-full │  │ giả)     │  │  chặn)   │
  │──────────│  │ (manual, │  │ → GATE   │  │──────────│
  │worker:   │  │  gate)   │  │t-021     │  │worker:   │
  │midscene- │  │          │  │(HAR B)   │  │gitleaks  │
  │cli       │  │          │  │ → GATE   │  │──────────│
  │cap:      │  │          │  │t-022     │  │cap:      │
  │ui.explore│  │          │  │(B thật)  │  │code.     │
  │suite:    │  │          │  │ → DISCOV.│  │secret    │
  │ui-explore│  │          │  │          │  │suite:    │
  │ → DISCOV.│  │          │  │          │  │secrets   │
  │          │  │          │  │          │  │ → GATE   │
  │          │  │          │  │          │  │──────────│
  │          │  │          │  │          │  │worker:   │
  │          │  │          │  │          │  │trivy     │
  │          │  │          │  │          │  │cap:      │
  │          │  │          │  │          │  │deps.vuln │
  │          │  │          │  │          │  │suite:deps│
  │          │  │          │  │          │  │ → GATE   │
  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │             │             │             │
       ▼             ▼             ▼             ▼
   GATE nếu     GATE (perf-   GATE (t-020,  GATE (SAST +
   api-contract; full) hoặc   t-021) +      secret + dep,
   DISCOVERY    DISCOVERY     DISCOVERY     high/critical
   nếu ui-      (perf-smoke)  (t-022, B     chặn)
   explore                    thật)

  ═══════════════════════════════════════════════════════════════════════════
  (ngoài 4 khâu) — worker phụ trợ, không phải một khâu chức năng:
  ┌──────────────┐   ┌──────────────┐
  │ coverage-debt│   │  deepeval    │
  │ cap: repo.   │   │ cap: llmapp. │
  │  coverage_   │   │  eval        │
  │  debt        │   │ → AI APP,    │
  │ → DÒ NỢ,     │   │   DISCOVERY  │
  │   DISCOVERY  │   │   (G-Eval    │
  │   (YELLOW)   │   │    advisory) │
  └──────────────┘   └──────────────┘
```

## Bảng tra nhanh — khâu → worker → capability → lane

| Khâu | Worker (manifest) | Capability | Suite ví dụ | Lane | Chặn merge? |
|---|---|---|---|---|---|
| Functional | `schemathesis` | `api.property` | `api-contract` | gate | có |
| Functional | `midscene-cli` | `ui.explore` | `ui-explore` | discovery | không |
| Performance | `k6` | `http.load` | `perf-smoke` (PR) · `perf-full` (manual) | discovery (PR) / gate (manual) | chỉ ở Mode 2 |
| Integration | `playwright` | `flow.integration` | `t-020` (runner giả) · `t-021` (HAR B) · `t-022` (B thật) | gate (t-020, t-021) / discovery (t-022) | có, trừ t-022 |
| Security | `semgrep` | `code.sast` | `sast` | gate | có (high/critical) |
| Security | `gitleaks` | `code.secret` | `secrets` | gate | có |
| Security | `trivy` | `deps.vuln` | `deps` | gate | có |
| (dò nợ, không phải khâu) | `coverage-debt` | `repo.coverage_debt` | `coverage-debt` | discovery | YELLOW, không FAIL |
| (AI app, chưa chạy thật) | `deepeval` | `llmapp.eval` | — | gate/discovery | metric tất định có, G-Eval chỉ tư vấn |

**Đọc bảng này kèm quy tắc ở [core-rules.md](core-rules.md):** thêm worker mới = thêm file
(`workers/<name>.yaml` + `src/qc_agent/adapters/<name>_adapter.py`), không sửa `core/`; capability
`id` phải có sẵn trong `schemas/capabilities.json`; `parallel_safe: false` (k6, midscene, playwright)
nghĩa là worker đó độc quyền — không task nào khác chạy song song trong cùng job.
