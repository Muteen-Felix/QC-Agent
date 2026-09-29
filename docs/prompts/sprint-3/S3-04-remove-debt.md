# S3-04 · Gỡ sổ nợ khỏi luồng (giữ Postgres) + coverage-debt phát finding Low (plan S3.4, S3.5)

- Branch: `feat/s3-04-remove-debt-flow`
- Tiền điều kiện: S3-03 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan §0 quyết định #4, S3.4, S3.5, mục "Gỡ Sổ nợ (Giữ Postgres)" và "Test gỡ theo"
- Grep `debt|Debt|apply_debt|DebtScan` trong `src/`, `tests/`, `web/`, `tools/`, `.github/`
- `src/qc_agent/{debt.py, jobs/debt_report.py, api/routes/debt.py}`, `src/qc_agent/integrations/github.py` (import `qc_agent.debt`, `_debt_of`, `_yellow_is_only_debt`, `_debt_lines`)
- `src/qc_agent/adapters/{coverage_debt_adapter.py, coverage_debt_worker.py}`
- `src/qc_agent/jobs/{models.py, migrations/versions/0005_test_debt.py}`: **GIỮ NGUYÊN**

## Mục tiêu

Sổ nợ không còn nằm trong bất kỳ luồng nào: PR, manual, ingest, executor, API, web. Bảng `test_debt`, model `DebtEntry` và migration `0005` giữ nguyên để không mất dữ liệu và không phải viết migration drop.

`coverage-debt` vẫn chạy như suite discovery, nhưng phát finding **Low có location**. Những finding này đi sang Jira ở S3-06/07 thay vì vào DB.

## Việc cần làm

1. **Xoá**: `src/qc_agent/debt.py`, `src/qc_agent/jobs/debt_report.py`, `src/qc_agent/api/routes/debt.py`.
2. **Gỡ phần debt** trong:
   - `api/app.py` (router debt);
   - `api/routes/ingest.py`, `api/serialize.py`;
   - `jobs/executor.py`;
   - `jobs/repository.py` (`apply_debt` và các hàm debt khác);
   - `integrations/github.py`: bỏ import `qc_agent.debt` và các hàm `_debt_*`. Phải làm trong PR này, vì xoá `debt.py` sẽ làm vỡ import.
   - `web/`: màn hình hoặc mục "Nợ test", nếu có.
3. **Test**
   - Xoá: `tests/test_ci_debt.py`, `tests/test_debt_ingest.py`, `tests/debtkit.py`.
   - Xử lý các test debt khác mà grep tìm ra: `test_test_debt_repository.py`, `test_workflow_debt.py`, `test_p2_8_e2e.py`, … Test nào chỉ kiểm sổ nợ thì xoá; test nào còn kiểm thứ khác (fake GitHub, harness) thì viết lại theo luồng mới. Liệt kê quyết định cho từng file trong báo cáo.
   - Viết lại `tests/test_coverage_debt_adapter.py` theo finding Low.
4. **`coverage-debt` phát finding Low có location**
   - Worker (`coverage_debt_worker.py`) đã biết bề mặt mới nằm ở đâu, vì nó parse diff và code. Thêm `path` và `line` (dòng khai báo route/endpoint) vào từng khoản trong `debt.json`.
   - Adapter phát `location` và `severity_hint: "low"`, với `detected_by: "coverage-debt:<kind>"` như cũ. Bước làm sạch `surface` giữ nguyên.
   - Đọc lại đoạn "full-scan dùng phần VẮNG MẶT để đóng nợ" trong docstring adapter: cơ chế đóng nợ không còn ý nghĩa khi không còn DB. Sửa docstring và bỏ những kiểm tra chỉ phục vụ việc đóng nợ, nhưng không nới các kiểm tra chống "0 nợ giả".
5. **Luồng PR không cần DB**:
   - `integrations/ci.py` không import gì từ `jobs/`;
   - ingest (`QC_API_URL`) vẫn là tuỳ chọn;
   - chạy `qc-agent run` với noteboard mode `pr` mà **không** đặt `QC_DATABASE_URL` → chạy trọn.
6. **Tài liệu**: `docs/architecture.md` (§ sổ nợ chuyển thành lịch sử; S1-00 có thể đã làm phần này), `CLAUDE.md` (bỏ "test-debt ledger"), `docs/usage-ci.md`.

## Nghiệm thu (DoD S3, các dòng liên quan)

- Grep `apply_debt` trong `src/` ra **rỗng**.
- Migration `0005` giữ nguyên: `git diff main -- src/qc_agent/jobs/migrations/versions/0005_test_debt.py src/qc_agent/jobs/models.py` rỗng, trừ migration `0006` nếu S3-03 đã chọn phương án (a).
- `python -m qc_agent.jobs.migrate upgrade` trên Postgres test vẫn chạy (`alembic upgrade head`).
- Luồng PR chạy khi không có `QC_DATABASE_URL`, có test chứng minh.
- `pytest -q` xanh.

## Ngoài phạm vi

Gửi finding Low sang Jira (S3-06/07), inline review (S3-05).
