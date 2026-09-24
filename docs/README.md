# Docs

Tài liệu và nhật ký của giai đoạn PoC (architecture, decisions, plan-execution, report, slides) đã được gỡ khỏi nhánh chính.
Toàn bộ vẫn nằm trong git: `git checkout poc-final -- docs`.

Kế hoạch refactor hiện hành: `knowledge/_derived/12-qc-agent-refactor-plan.md` (ngoài repo này).

## Log và dữ liệu rời máy

- **Log có cấu trúc**: mỗi dòng stderr là một JSON (`ts, level, logger, event` + `project, job_id, run_id, task_id, worker`). `QC_LOG_FORMAT=json|text`, `QC_LOG_LEVEL=INFO`.
  Lọc: `qc-agent run ... 2>&1 >/dev/null | jq 'select(.event=="task.end")'`; `executor.log` trộn báo cáo Markdown nên dùng `jq -R 'fromjson? | select(.event)'`.
  Không bao giờ log nội dung spec/inputs hay secret (xem `src/qc_agent/logging_setup.py`).
- **Dữ liệu rời máy**: `runs/<job>/egress.jsonl` (xem `src/qc_agent/core/egress.py`). Ghi theo khai báo `data_egress` của worker, không phải theo lưu lượng mạng thực tế.
