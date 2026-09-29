# S1-00 · Viết lại tài liệu kiến trúc theo v2 (plan S1.0)

- Branch: `docs/s1-00-architecture-v2`
- Tiền điều kiện: không có
- Làm **trước mọi bước code** để code và tài liệu không mâu thuẫn nhau.

## Đọc trước

- `docs/prompts/_common.md`, `docs/prompts/README.md` (hai mục "Hiệu chỉnh" và "Cần bạn quyết")
- `docs/implementation-plan.md`: đọc toàn bộ, kỹ nhất §0. Thêm `docs/requirement-spec.md` và `docs/worker.md`.
- `docs/architecture.md`, `docs/core-rules.md`, `CLAUDE.md`, và phần đầu `docs/phase2/plan-debt.md`

## Bối cảnh

Plan yêu cầu viết lại tài liệu "theo Phần 1", nhưng Phần 1 không có trong repo. Việc của bạn gồm hai phần:
- tự dựng Phần 1, tức một bảng delta kiến trúc cũ → mới, lấy từ plan §0, các sprint và SRS;
- để tôi duyệt bảng đó, rồi mới sửa tài liệu.

Sau PR này, `architecture.md`, `core-rules.md` và `CLAUDE.md` không còn câu nào mâu thuẫn với plan. Code sẽ đuổi theo ở các sprint sau, nên mỗi thành phần phải ghi trạng thái thật của nó.

## Việc 1: HỎI TRƯỚC, trình bày bảng delta và chờ tôi duyệt

Bảng có dạng `Chủ đề | Hiện tại (trích tài liệu, kèm § / dòng) | v2 theo plan | Sprint`. Tối thiểu phải có các dòng sau (thêm nếu thấy còn thiếu):

- **Vòng ngoài**
  - Hiện tại: LLM sinh và sửa test, nối với gate qua sổ nợ.
  - v2: Ground-Truth Engine. Luồng: PRD → LLM sinh TC dạng JSON (không sinh code) → render tất định → PR `qc-agent/gt/<prd-id>` → QA đổi `draft → approved` và thêm edge case `origin: qa`. Thư mục `.qc-agent/**` được khoá bằng CODEOWNERS và branch protection. Gate chỉ chạy TC `approved`.
- **LLM lúc điều phối**
  - Hiện tại: "không bao giờ" (§1.2, §4.7, §5.2); "LLM chỉ được THÊM, không được BỚT" (§5.3).
  - v2: Diff Agent (Haiku 4.5) được *chọn* worker trong allowlist của policy. Sản phẩm duy nhất của nó là `selection.json`.
    - Floor (secrets + sast) không thương lượng và được gộp ở 2 lớp: selector và `core/`.
    - Diff chạm `full_set_paths` → FULL SET, không gọi LLM.
    - LLM lỗi → FULL SET.
    - `core/` vẫn không gọi LLM.
- **Rủi ro còn lại**: injection trong trường hợp xấu nhất làm bỏ sót worker ngoài floor ở những file chưa có trong module-map. Cách giảm nhẹ:
  - kết quả chọn = floor ∪ rules(module-map) ∪ LLM, nghĩa là map tất định chỉ *thêm*, không bị LLM bớt;
  - `full_set_paths`;
  - bộ test injection.

  Rủi ro này được chấp nhận theo quyết định #1.
- **Trigger**: thêm trục trigger, tách biệt với mode:
  - `pr`: đi qua selector;
  - `manual`: nhận danh sách worker, không LLM, không floor. DoD S2 đòi "không suite nào khác".
- **Severity và verdict**
  - Hiện tại: `FAIL > YELLOW > PASS`; `severity_hint` ∈ low/medium/high.
  - v2: severity ∈ low/medium/critical (contract 2.0.0). Verdict:
    - `BLOCKED`: có finding critical/medium, hoặc task gate bị error/skip;
    - `PASSED_WITH_WARNINGS`: chỉ còn Low;
    - `PASSED`.
- **Phản hồi**
  - Hiện tại: comment dính + review security.
  - v2: `pr_review` gắn inline cho mọi worker, và tạo ticket Jira cho finding Low.
- **Sổ nợ test**: gỡ khỏi luồng nhưng giữ bảng `test_debt` và migration `0005`. `coverage-debt` phát finding Low, sau đó đi vào Jira.
- **Postgres**: tuỳ chọn với luồng PR và manual; chỉ service (dashboard/executor) còn cần.
- **Chi phí**: prune diff, cache selection và GT, prompt caching, trần token (S4).

## Việc 2: sau khi tôi duyệt

- **`docs/architecture.md`**
  - Viết lại §1: hai vòng theo v2, luồng PR gồm `prune → path rules → Diff Agent → floor → Task Runner`, và luồng manual.
  - §1.3 và §4: thay nguyên tắc "LLM không được phép lúc chạy" bằng ranh giới mới: *LLM được sinh ứng viên và chọn phạm vi ngoài floor; LLM không được phán quyết, và mọi thứ nó ảnh hưởng đều là một file đọc được.*
  - §1.4–1.5: chuyển sổ nợ thành phần lịch sử.
  - §5.3: **giữ nguyên văn khối ví dụ injection** (S2-07 dùng lại), đổi kết luận thành các lớp phòng thủ của v2, và ghi rõ rủi ro còn lại.
  - Mỗi thành phần có nhãn trạng thái thật: *Đã chạy*, *Đang triển khai — S<n>*, hoặc *Thiết kế*. Không nói quá.
- **`docs/core-rules.md`**
  - Mục "Chạy": thay `pip install -r requirements.txt` và `python orchestrator.py` (đã cũ) bằng `uv sync` + `qc-agent`.
  - Mục "Cấm": thêm luật import lười cho `llm/`, `groundtruth/`, `selector/`; luật "dữ liệu PR/PRD/diff không tin cậy"; và luật ghi egress trước mọi lời gọi LLM/Jira.
  - Ghi chú rằng metric `critical` và `severity_hint` sẽ đổi ở S3.1.
- **`CLAUDE.md`**: sửa phần Architecture và Hard rules theo v2 (vòng ngoài mới, Diff Agent, floor, severity v2 ghi "(S3)"). **Chưa** thêm những lệnh chưa tồn tại.
- **`docs/phase2/plan-debt.md`**: thêm banner ở đầu file: "Superseded bởi `docs/implementation-plan.md` — S3 gỡ sổ nợ khỏi luồng; bảng `test_debt` và migration 0005 giữ nguyên."
- **`docs/worker.md`**: sửa cột "Chặn merge?" cho khớp severity v2, ghi "(từ S3)". Thêm dòng `pytest` · `api.functional` · `gt-functional` với trạng thái *Đang triển khai — S1*.
- Commit luôn các file đang untracked: `docs/implementation-plan.md`, `docs/requirement-spec.md`, `docs/worker.md`, `docs/prompts/`.

## Kiểm tra

- Grep `YELLOW|sổ nợ|chỉ được THÊM|không bao giờ — vì đây là chỗ cầm quyền` trong ba tài liệu gốc. Mọi kết quả còn lại phải là ngữ cảnh lịch sử và có ghi rõ là lịch sử.
- Link nội bộ (anchor `#…`) vẫn đúng.
- Chạy `pytest -q`: một số test đọc docs/workflow.

## Ngoài phạm vi

Mọi thay đổi về code, schema và workflow.
