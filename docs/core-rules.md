`core/` chỉ nối plan → worker → verdict → report. Không LLM, không biết tên worker nào: orchestrator "ngu" là orchestrator đúng.

## Chạy

Chạy từ thư mục gốc repo (adapter được spawn bằng `python -m`, đường dẫn trong plan là tương đối).

```powershell
pip install -r requirements.txt                                        # 1. cài phụ thuộc
python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000  # 2. bật toy app
python orchestrator.py --plan tests\fixtures\plans\demo.yaml      # 3. chạy gate, in report, ghi runs\r-NNNN\
```

Exit code: `PASS` 0 · `YELLOW` = `--yellow-exit` (mặc định 0) · `FAIL` 1 · lỗi plan/hệ thống **3**.
`demo.yaml` dùng worker giả nên bước 2 chỉ cần khi plan trỏ vào toy app. Cờ khác: `--only t-a,t-b`, `--runs-dir`, `--rerender RUN_DIR`.

## Thêm worker

Đúng 5 bước, mỗi bước là **thêm file**:

1. `workers/<tên>.yaml` (manifest, `adapter: "qc_agent/adapters/<tên>_adapter.py"`): copy `workers/_template.yaml` (bản mẫu đã đóng băng, không sửa). Capability mới thì thêm một dòng vào `schemas/capabilities.json`.
2. `schemas/<cap>.inputs.json`: schema của `inputs`. Tuỳ chọn ở PoC (D-08).
3. `src/qc_agent/adapters/<tên>_adapter.py`: kế thừa `Adapter` (`src/qc_agent/adapters/_base.py`), khai `NAME` + `ADAPTER_VERSION`, **chỉ override `build_cmd` và `parse_output`**.
4. `src/qc_agent/oracle/<kind>.py` nếu `oracle.kind` là mới: dùng `@register("<kind>")`, không phải sửa `oracle/__init__.py`.
5. Thêm task vào `tests/fixtures/plans/<plan>.yaml`. Nhớ quote khoá `"on":` trong `retry`, vì YAML đọc `on` trần thành `True`.

**Tuyệt đối không sửa `core/`.** Nếu bạn thấy phải sửa, thiết kế đang sai: dừng lại và hỏi cả nhóm.

## Ai đỡ được gì

Nếu A (chủ `core/`) kẹt, hai người còn lại nhận việc theo bảng này:

| Người nhận | File | Test từng file |
|---|---|---|
| B | `core/runner.py` | `pytest tests\test_runner.py -q` |
| B | `core/cli.py` + `orchestrator.py` | `pytest tests\test_cli.py -q` |
| C | `core/plan.py` | `pytest tests\test_plan.py -q` |
| C | `core/schema.py` | `pytest tests\test_schema.py -q` |

Toàn bộ: `pytest -q`. Cổng kiểm tay: `python orchestrator.py --plan tests\fixtures\plans\demo.yaml` phải `exit=0`, `tests\fixtures\plans\demo_fail.yaml` phải `exit=1`.

## Cấm

- **Cấm gọi LLM trong `core/`**: verdict phải tái lập được; LLM chỉ được nằm trong worker và chỉ cho finding không chặn gate.
- **Cấm `if worker == ...` trong `core/`**: mọi khác biệt giữa worker phải nằm ở manifest, adapter hoặc oracle.
- **Cấm thêm trường riêng của một worker vào schema**: một schema chung là thứ cho phép thêm worker mà không sửa `core/`.
- **Cấm retry khi `fail`**: chỉ `error` (hạ tầng) được retry đúng 1 lần; retry `fail` che flakiness và làm gate xanh giả.
- **Cấm sửa file contract ngoài quy trình SemVer** (`schemas/task_spec.json`, `schemas/result.json`, `workers/_template.yaml`): `python tools/freeze_contract.py --check` phải exit 0. Thêm field an toàn (v1.x): bump minor, CI `contract-check` tự phân loại, cần 1 approval. Sửa/xoá/thu hẹp (v2.0): bump major, cần 3 approval trong đó có Lead/Core, rồi mới `freeze_contract.py --write --version X.Y.Z`. Danh sách người duyệt: `.github/contract-reviewers.yaml`.
