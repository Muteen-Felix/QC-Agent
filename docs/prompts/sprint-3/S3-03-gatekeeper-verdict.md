# S3-03 · Gatekeeper: verdict mới, exit code, report theo severity (plan S3.3 + phần "Sửa Lõi")

- Branch: `feat/s3-03-gatekeeper`
- Tiền điều kiện: S3-02 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan: khối code `gate_verdict`, S3.3, "Sửa Lõi"; README "Cần bạn quyết" #1
- `src/qc_agent/core/{verdict.py, engine.py (judge, _execute), cli.py (--yellow-exit, _rerender), report.py, project.py (_check_yellow_subset, advisory_yellow_suites, yellow_on_fail)}`
- `src/qc_agent/jobs/models.py`: `Job.gate_verdict: String(8)` với `CHECK gate_verdict IN ('PASS','YELLOW','FAIL')`
- `src/qc_agent/jobs/executor.py`, `src/qc_agent/api/{serialize.py, schemas.py, routes/ingest.py}`, `web/app.js`, `src/qc_agent/integrations/{github.py, notify.py}`
- Grep `YELLOW|"PASS"|"FAIL"|gate_verdict` trong `src/`, `web/`, `tests/`, `tools/` để thấy **mọi** nơi tiêu thụ verdict.

## HỎI TRƯỚC

Cột `jobs.gate_verdict` không chứa được `PASSED_WITH_WARNINGS` (20 ký tự) và `BLOCKED` (không có trong CHECK). Executor và ingest sẽ lỗi. Hai phương án:
- **(a) Đề xuất:** migration `0006` nới cột lên `String(24)` và đổi CHECK thành `IN ('PASS','YELLOW','FAIL','PASSED','PASSED_WITH_WARNINGS','BLOCKED')`. Giữ giá trị cũ cho dữ liệu lịch sử, không drop gì, nên hợp quyết định #4.
- **(b)** Ánh xạ ở chỗ ghi DB (`BLOCKED→FAIL`, `PASSED_WITH_WARNINGS→YELLOW`, `PASSED→PASS`), và API/web dịch ngược lại.

Chờ tôi chọn. Nếu chọn (a) thì migration phải chạy được với `python -m qc_agent.jobs.migrate upgrade` và có test.

## Mục tiêu

Thay `FAIL > YELLOW > PASS` bằng hàm thuần của plan:

```python
def gate_verdict(findings, infra_blockers, block_on=("critical", "medium")) -> GateVerdict:
    if infra_blockers or any(f.severity in block_on for f in findings): return GateVerdict(BLOCKED, 1, ...)
    if findings: return GateVerdict(PASSED_WITH_WARNINGS, 0, ...)
    return GateVerdict(PASSED, 0, ...)
```

Mọi nơi tiêu thụ verdict phải cập nhật **trong cùng PR**, để `main` không bao giờ ở trạng thái vỡ.

## Việc cần làm

1. **`core/verdict.py`** (viết lại, vẫn là hàm thuần)
   - `BLOCKED`, `PASSED_WITH_WARNINGS`, `PASSED`.
   - `GateVerdict(value, exit_code, reasons, banner, counts={"critical": n, "medium": n, "low": n})`.
   - `block_on` lấy từ policy.
   - Giữ `canary_alerts` và `lane_has_gate`.
2. **`core/engine.py: judge`**: `normalize` (S3-02) → `gate_verdict` → exit code:
   - `BLOCKED` = 1;
   - `PASSED_WITH_WARNINGS` = `--warn-exit` (mặc định 0);
   - `PASSED` = 0.

   `run_signature` giữ nguyên cách tính.
3. **`core/cli.py`**
   - Thêm `--warn-exit N` (0–255).
   - `--yellow-exit` thành alias deprecated: vẫn nhận, in cảnh báo ra stderr, rồi ánh xạ sang `--warn-exit`.
   - `_rerender` dùng đường tính mới.
   - Docstring đầu file nêu lại exit code.
4. **`core/project.py`**: bỏ `advisory_yellow_suites`, `_check_yellow_subset`, `yellow_on_fail` khỏi schema và `build_plan`.
   - Sửa `configs/projects/noteboard.yaml`: bỏ `advisory_yellow_suites: [coverage-debt]`.
   - Plan cũ có `yellow_on_fail` (`--rerender` các run cũ): `load_plan` bỏ qua key đó kèm cảnh báo, không lỗi.
5. **`core/report.py`**
   - Biểu tượng verdict: ✅ `PASSED`, ✅⚠ `PASSED_WITH_WARNINGS`, ❌ `BLOCKED`.
   - Ba mục **Critical / Medium / Low**, liệt kê finding chuẩn hoá (một dòng mỗi finding, cắt ngắn, kèm `path:line` nếu có).
   - Các mục audit, discovery và skipped/error giữ nguyên.
   - `report.json`: `gate_verdict` mang giá trị mới, thêm `findings` (đúng hình dạng chuẩn hoá) và `severity_counts`.
6. **Mọi nơi tiêu thụ verdict** (từ kết quả grep):
   - `integrations/github.py`: chỉ đổi phần `_EMOJI`/nhãn cho khỏi vỡ; `conclusion_for` và `check_title` làm đầy đủ ở S3-05, nhưng ở PR này đã phải cho ra kết quả hợp lý;
   - `notify.py`;
   - `api/serialize.py`, `api/schemas.py`;
   - `jobs/executor.py`;
   - `web/app.js`: nhãn và màu, hiện được **cả** giá trị cũ lẫn mới vì còn lịch sử trong DB;
   - `tools/diff_runs.py`, nếu có dùng.
7. **Tài liệu**: bảng exit code ở `CLAUDE.md`, `docs/core-rules.md`, `docs/usage-ci.md`.

## Test bắt buộc

- `tests/test_gatekeeper.py`:
  - phủ **100% tổ hợp** severity × lane × verdict_source × status (sinh bằng `itertools.product`), đối chiếu với một bảng kỳ vọng viết tường minh;
  - property test: finding do LLM chấm **không bao giờ** chặn được;
  - AND rỗng → `BLOCKED`.
- `tests/test_verdict.py`, `tests/test_engine.py`, `tests/test_cli.py`, `tests/test_report.py`: sửa theo verdict mới; `--yellow-exit` vẫn chạy và có cảnh báo; `--warn-exit 2` với chỉ-Low → exit 2.
- Nếu chọn (a): test migration với `QC_TEST_DATABASE_URL` (ghi và đọc được `PASSED_WITH_WARNINGS`). Nếu chọn (b): test ánh xạ hai chiều.
- **Demo gate**: `demo.yaml` → 0, `demo_fail.yaml` → 1. Chạy noteboard mode `pr` với toyapp sạch → 0, `QC_BUGS=1` → 1 (BUG-1 thành `critical` → `BLOCKED`).

## Ngoài phạm vi

Gỡ sổ nợ (S3-04), inline review và Check Run đầy đủ (S3-05), Jira (S3-06).
