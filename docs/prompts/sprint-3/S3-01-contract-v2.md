# S3-01 · Nâng contract lên 2.0.0: `high` → `critical`, thêm `findings[].location` (plan S3.1)

- Branch: `feat/s3-01-contract-v2`
- Tiền điều kiện: Sprint 2 đã nghiệm thu.
- **Bên ngoài**: cần approval của nhóm core (`.github/contract-reviewers.yaml`) thì mới được `--write` lock.

## Đọc trước

- `docs/prompts/_common.md`; plan §0 quyết định #3 và S3.1; README mục "Cần bạn quyết" #3
- `schemas/result.json` (`severity_hint`, `findings[]`), `schemas/CONTRACT.lock`
- `tools/freeze_contract.py`, `tools/contract_check.py`, `tools/contract_diff.py`, `.github/workflows/contract-check.yml`, `.github/contract-reviewers.yaml`
- `docs/core-rules.md`, mục "Cấm" (quy trình SemVer) và "Quy ước đặt tên"
- `tests/test_contract_frozen.py`, `tests/test_contract_mutants.py`, `tests/test_contract_check.py`

## HỎI TRƯỚC (chốt trước khi đụng schema, vì chỉ được nâng major một lần)

**`rule_id` lấy từ đâu** để S3-02 override severity theo rule? Có hai cách:

- **(a) Đề xuất: quy ước `detected_by: "<tool>:<rule_id>"`.** Đã có tiền lệ ở `coverage-debt:<kind>`, `threshold:<metric>`, và `pytest:<tc_id>` (S1-03). Cách này không phải thêm field.
- **(b)** Thêm field tuỳ chọn `findings[].rule_id` vào **cùng** lần bump 2.0.0.

Trình bày hai phương án và chờ tôi chọn.

## Việc cần làm

1. **`schemas/result.json`**
   - `severity_hint` → enum `["low", "medium", "critical", null]`.
   - Thêm `findings[].location` (tuỳ chọn): object `{path: string (bắt buộc), line: integer ≥ 1, end_line: integer ≥ 1}` với `additionalProperties: false`.
   - Nếu tôi chọn (b) thì thêm `rule_id` ở đây.
   - Đổi `title` của schema thành v2.0.0.
2. **Sửa mọi chỗ phát `high`, trong cùng PR.** Grep lại toàn repo (`src/`, `tests/`, `tools/`, `web/`, `rules/`, `docs/`), **đừng** tin danh sách "11 chỗ" của plan. Hiện biết có:
   - `adapters/_security.py`: `_HINT` đổi thành `critical→critical`, `high→critical`, và xoá comment "result.json không có severity_hint critical".
   - `adapters/semgrep_adapter.py`, `adapters/trivy_adapter.py`: bảng `SEVERITY` là mức gốc của công cụ, dùng cho **metric**. **Giữ** tên metric `semgrep.high`/`trivy.high`, vì suite đang assert trên đó; chỉ `severity_hint` đổi qua `_HINT`.
   - `adapters/gitleaks_adapter.py`, `adapters/playwright_adapter.py`, `adapters/deepeval_adapter.py`: `"high"` → `"critical"`.
   - `oracle/checks.py`, `oracle/threshold.py`: `"high"` → `"critical"`.
   - `oracle/signals.py`: `_SEVERITY` với `http_5xx`/`dom_unchanged` → `critical`. Đây là lane discovery nên S3-02 sẽ hạ trần về Low.
   - `integrations/security_review.py`: gitleaks đang dùng `"high"`. Nếu vẫn giữ bảng `_RANK`/`_ICON` thì dùng mức của công cụ, nhưng mức hiển thị theo contract phải thống nhất. File này sẽ bị thay ở S3-05.
   - `coverage_debt_adapter.py` phát `low`: **không** đổi ở bước này (S3-04).
   - `pytest_adapter.py` phát `medium`: không đổi.
3. **Phát `location`** ở những adapter đã biết vị trí:
   - semgrep: `path`, `start.line`, `end.line`;
   - gitleaks: `File`, `StartLine`;
   - trivy: `Target` (lockfile), không có `line`;
   - pytest: file và dòng của test, nếu JUnit có.

   Theo cách đã chọn ở mục HỎI TRƯỚC, đổi `detected_by` thành `<tool>:<rule>` cho semgrep (`check_id`), gitleaks (`RuleID`), trivy (`VulnerabilityID`).
4. **Test và fixture**: cập nhật kỳ vọng `"high"` trong test và trong `tests/fixtures/**` (kết quả mong đợi, không phải output thô của công cụ). Chạy `test_contract_frozen` và `test_contract_mutants`.
5. **Lock**:
   - chạy `python tools/contract_check.py` (hoặc đúng lệnh mà `contract-check.yml` chạy) để xác nhận được phân loại là **major**;
   - **chỉ sau khi có approval**, chạy `python tools/freeze_contract.py --write --version 2.0.0`.
   - Trong phiên này: chuẩn bị mọi thứ và **dừng trước bước `--write`**. Báo cho tôi câu lệnh để chạy sau khi đủ người duyệt, hoặc chạy nếu tôi xác nhận đã có approval.
6. **Tài liệu**: `docs/core-rules.md` (bỏ ghi chú "critical chỉ có ở metric…", thêm `location` và quy ước `detected_by`), `docs/architecture.md` §2.4. Thân PR liệt kê breaking change và mọi nơi đang tiêu thụ contract.

## Nghiệm thu

- `CONTRACT.lock = 2.0.0` và `freeze_contract --check` exit 0 (sau khi `--write`).
- Grep `severity_hint.*high` trong `src/` ra rỗng.
- `pytest -q` xanh.
- Demo gate: 0 / 1.

## Ngoài phạm vi

Normalizer và verdict mới (S3-02/03). Không đổi tên metric.
