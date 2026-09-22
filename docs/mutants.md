# Seeded-fault mutation results

STEP 48 reduced scope: M0/M1/M3/M5. M2 needs Midscene and M4/M6 were cut from this sprint scope.
Run root: `runs/mutants/20260922T074329Z`

| Mutant | file/dòng | loại lỗi mô phỏng | test nào phải bắt | kết quả thực tế |
|---|---|---|---|---|
| M0 | — | đối chứng sạch: không bật lỗi cài sẵn | gate phải PASS (không false positive) | XANH |
| M1 | toyapp/app.py:51 | BUG-1: id dài trả HTTP 500 thay vì 4xx | t-001 Schemathesis | BẮT |
| M3 | toyapp/summarizer.py:2 | BUG-3: summary dài hơn input khi body có [[long]] | t-003 summary_shorter_than_body | BẮT |
| M5 | tests/perf/broken.js:1 | worker k6 crash vì script JavaScript không hợp lệ | t-002 phải là status=error, không phải fail | BẮT |
