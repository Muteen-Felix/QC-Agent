# S3-02 · Chuẩn hoá finding + policy severity (plan S3.2)

- Branch: `feat/s3-02-findings-normalizer`
- Tiền điều kiện: S3-01 đã merge, lock đã ở 2.0.0.

## Đọc trước

- `docs/prompts/_common.md`; plan: mục tiêu Sprint 3, "Lõi của Gatekeeper", S3.2; README "Cần bạn quyết" #4
- `schemas/result.json` (v2), `src/qc_agent/core/verdict.py` (luật hiện tại, gồm cả luật chống "AND rỗng"), `src/qc_agent/core/project.py` (schema policy, `on_skipped_gate_task`)
- `src/qc_agent/oracle/threshold.py`: finding kiểu `threshold:<metric>`, không có location
- `src/qc_agent/adapters/_security.py: finding_id`: id **có chứa vị trí dòng** trong key, nên không dùng làm fingerprint được

## HỎI TRƯỚC

Các luật sau khi bỏ YELLOW. Đề xuất, chờ tôi chốt:
- **Infra blocker** chỉ tính task **lane gate**:
  - `status == error` → blocker;
  - `status == skipped` với `on_skipped_gate_task: fail` → blocker;
  - `status == skipped` với `on_skipped_gate_task: yellow` → **cảnh báo Low**, tức `PASSED_WITH_WARNINGS`.
- Task discovery bị error hoặc skip → **không chặn**, chỉ hiện ở banner như hiện nay.
- Plan có task gate nhưng không có kết quả gating nào → blocker (giữ luật chống AND rỗng).

## Mục tiêu

Một module thuần tất định, `core/findings.py`, biến `results` và `specs` thành danh sách finding chuẩn hoá (`_common.md` §3) cộng danh sách infra blocker. Module không gọi I/O và không dùng LLM.

## Việc cần làm

1. **`src/qc_agent/core/findings.py`**
   ```python
   @dataclass(frozen=True)
   class NormFinding:
       fingerprint: str; severity: str          # low | medium | critical
       task_id: str; suite: str | None; worker: str; rule_id: str | None
       title: str; path: str | None; line: int | None; end_line: int | None
       lane: str; verdict_source: str; source: str   # "finding" | "task_default"
   def normalize(results: dict, specs: dict, *, task_suite: dict[str, str], policy: dict) -> tuple[list[NormFinding], list[tuple[str, str]]]
   ```
   Luật, lấy đúng từ plan:
   - **Severity**: lấy `severity_hint`, rồi áp override của policy (theo suite, và theo `rule_id` dạng glob). `severity_hint = null` ở lane gate → dùng `default_severity` của suite.
   - **Task `fail` mà không có finding nào** → sinh một finding `source="task_default"` với `default_severity` của suite (mặc định `medium`, tức chặn, cho an toàn).
   - **Lane `discovery`, hoặc `verdict_source == "llm_judgment"`** → **trần Low**. Không có đường nào nâng lên được, kể cả override.
   - `rule_id` tách từ `detected_by` theo quy ước `<tool>:<rule_id>` (hoặc lấy từ field riêng, nếu S3-01 chọn cách b).
   - `path`/`line`/`end_line` lấy từ `location`, nếu có.
   - **Fingerprint** = `sha256("{worker}|{rule_id}|{path}|{title_không_vị_trí}")[:16]`. Trước khi băm, bỏ hậu tố ` @ path:line` và mọi số dòng khỏi `title`. **Không** dùng số dòng hay `finding_id`.
   - `task_suite` (task_id → suite) lấy từ `build_plan`. Plan chạy bằng `--plan` thì để `None`.
2. **Policy severity trong `core/project.py`**: key ở cấp project, deep-merge được.
   ```yaml
   severity:
     block_on: [critical, medium]
     default_severity: {"*": medium}          # theo suite; "*" là mặc định
     overrides:                                # áp theo thứ tự, luật khớp SAU thắng
       - {suite: deps, rule_id: "CVE-*", from: medium, to: low}   # ví dụ, KHÔNG tự bật
   ```
   Validate bằng schema: mức ∈ low/medium/critical; `block_on` ⊆ tập mức. `_default.yaml` chỉ khai `block_on` và `default_severity`, **không** tự thêm override. Nêu trong báo cáo: bật override `deps` hay không là quyết định của tôi, vì Trivy MEDIUM sẽ chặn mọi PR nếu không có override.
3. Chưa đụng `verdict.py`, `engine.judge` và report. Việc đó là S3-03.

## Test bắt buộc: `tests/test_findings_normalize.py`, fixture `tests/fixtures/results/*.json` (v2)

- Mỗi luật có ít nhất một test: hint → severity; override theo suite và theo `rule_id`; task fail không có finding → mặc định `medium`; discovery bị hạ trần Low; `llm_judgment` bị hạ trần Low **kể cả khi override nâng lên**.
- **Fingerprint ổn định** khi cùng finding bị dịch 5 dòng, hoặc `title` đổi đoạn ` @ path:12` thành ` @ path:17`. Fingerprint khác nhau khi khác `rule_id` hoặc khác `path`.
- Finding `threshold:<metric>` (không có location) có `rule_id = <metric>` và `path = None`.
- Infra blocker theo đúng các luật đã chốt ở mục HỎI TRƯỚC.
- Có property test (dùng `hypothesis`, đã có sẵn do schemathesis kéo theo): với mọi tổ hợp, finding có `verdict_source == llm_judgment` hoặc `lane == discovery` luôn có `severity == "low"`.

## Ngoài phạm vi

Verdict, exit code, report (S3-03); PR review (S3-05); Jira (S3-06).
