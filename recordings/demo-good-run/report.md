# QC Gate Report — run r-0004
plan: plan.yaml (plan-f4a3467c) · SUT: sut-f560000f · 2026-09-22T07:59:29+00:00
wallclock 1m16s · LLM tokens: 0 (tasks: —) · cost $0.00

## VERDICT: ❌ FAIL

## 1. DETERMINISTIC ASSERT (chặn gate)

| task | worker | capability | status | chi tiết |
|---|---|---|---|---|
| t-000 | http-collect | http.collect | pass | — |
| t-001 | schemathesis | api.property | fail | GET /notes/{note_id}: not_a_server_error không đạt |
| t-002 | k6 | http.load | pass | http_req_duration.p95=21.038, http_req_failed.rate=0 |
| t-003 | deepeval | llmapp.eval | fail | summary_shorter_than_body: case g3 không đạt |

→ gate_verdict = pass AND fail AND pass AND fail = **FAIL**

## 2. LLM JUDGMENT (KHÔNG chặn gate — chỉ tham khảo)

| task | metric | điểm | baseline | delta | confidence |
|---|---|---:|---:|---:|---:|
| t-003 | GEval | 0.52 | 0.78 | -0.26 | 0.52 |

Các số này không cộng vào 'pass rate' và không chặn gate.

## 3. HEURISTIC / DISCOVERY (KHÔNG chặn gate)

- `t-101` · midscene-cli · bước — · $— · token —
  - [f-sig-dom_unchanged] DOM không render lại sau thao tác xoá
    detected_by: implicit_signal:dom_unchanged ← TẤT ĐỊNH
    → ứng viên promote: ui.explore
- `t-canary-01` · midscene-cli · bước — · $— · token —
- CANARY t-canary-01: OK (fail như kỳ vọng)

## 4. SKIPPED / ERROR

- Không có.

## 5. AUDIT

### t-000
- oracle: plan.yaml:L11
- evidence: runs/r-0004/t-000/outputs.json (sha256 b16565c4…)
- evidence: runs/r-0004/t-000/collection.json (sha256 624ee4b4…)
- evidence: runs/r-0004/t-000/stdout.log (sha256 e3b0c442…)
- replay: /Users/mac/Desktop/QC/QC-Agent/.venv/bin/python -m adapters.collect_worker --golden /Users/mac/Desktop/QC/QC-Agent/tests/eval/golden.json --out /Users/mac/Desktop/QC/QC-Agent/runs/r-0004/t-000/outputs.json --report /Users/mac/Desktop/QC/QC-Agent/runs/r-0004/t-000/collection.json

### t-001
- oracle: plan.yaml:L31
- evidence: runs/r-0004/t-001/schemathesis.junit.xml (sha256 c6f490cb…)
- evidence: runs/r-0004/t-001/stdout.log (sha256 eed485b1…)
- replay: st --config-file /Users/mac/Desktop/QC/QC-Agent/runs/r-0004/t-001/schemathesis.toml run http://127.0.0.1:8000/openapi.json --checks not_a_server_error,response_schema_conformance --max-examples 25 --seed 1337 --exclude-path '/notes/{note_id}/summarize' --generation-database none --report junit --report-junit-path /Users/mac/Desktop/QC/QC-Agent/runs/r-0004/t-001/schemathesis.junit.xml --no-color

### t-002
- oracle: plan.yaml:L56
- evidence: runs/r-0004/t-002/k6-summary.json (sha256 45a93759…)
- evidence: runs/r-0004/t-002/stdout.log (sha256 3c81c143…)
- replay: k6 run --summary-export=/Users/mac/Desktop/QC/QC-Agent/runs/r-0004/t-002/k6-summary.json --vus 10 --duration 30s tests/perf/notes_list.js

### t-003
- oracle: plan.yaml:L80
- evidence: runs/r-0004/t-003/junit.xml (sha256 1a9eba9d…)
- evidence: runs/r-0004/t-003/geval.json (sha256 328c3bcd…)
- evidence: runs/r-0004/t-003/stdout.log (sha256 ba0f7009…)
- replay: /Users/mac/Desktop/QC/QC-Agent/.venv/bin/python -m pytest /Users/mac/Desktop/QC/QC-Agent/tests/eval/test_summarize.py --junitxml=/Users/mac/Desktop/QC/QC-Agent/runs/r-0004/t-003/junit.xml -q -p no:cacheprovider
