# S3-06 · Đồng bộ finding Low sang Jira (plan S3.8)

- Branch: `feat/s3-06-jira-sync`
- Tiền điều kiện: S3-03 đã merge. Chạy song song được với S3-04/05.

## Đọc trước

- `docs/prompts/_common.md`; plan S3.8 và DoD S3 (1 ticket gán cho tác giả PR; chạy lại thì 0 ticket mới; Jira 401/5xx thì verdict không đổi)
- `src/qc_agent/core/egress.py`, `src/qc_agent/scaffold/suggest.py: suggest_flows` (mẫu: ghi egress trước khi gửi, deny thì không gửi)
- `src/qc_agent/integrations/ci.py: context_from_env` (lấy tác giả PR, số PR, link run)
- `src/qc_agent/core/project.py`: schema policy, nơi thêm khối `jira`

## Mục tiêu

Mỗi finding **Low** (theo fingerprint) có đúng **một** ticket Jira, gán cho tác giả PR nếu map được.
- Không tạo trùng khi chạy lại.
- Không bao giờ làm đổi verdict hay exit code: fail-open.

## Việc cần làm

1. **Cấu hình**
   - Project config có khối (`core/project.py`, deep-merge được):
     ```yaml
     jira: {project_key: QCSB, issue_type: Task, user_map: {"github-login": "<jira accountId>"}, max_new_per_run: 20}
     ```
   - Secret lấy từ env: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. Token chỉ nằm trong header Basic auth; không log, không nằm trong exception.
   - Thiếu cấu hình hoặc thiếu secret → `skipped` kèm lý do.
2. **`src/qc_agent/integrations/jira.py`**, client REST **v3** dùng `httpx`, cho phép tiêm `transport` khi test:
   - `sync_low_findings(findings, *, cfg, ctx, env, egress_dir, transport=None) -> dict`:
     - chỉ xử lý `severity == "low"`;
     - chống trùng bằng label `qcagent-<fingerprint>`; label không được chứa khoảng trắng.
   - **Tra ticket đã có**: JQL `project = <KEY> AND labels = "qcagent-<fp>"`, gom theo lô.
     - **Kiểm lại endpoint trong tài liệu Atlassian hiện hành trước khi code.** Endpoint search cũ `/rest/api/3/search` đã bị Atlassian ngừng, thay bằng `/rest/api/3/search/jql`.
     - Đã có ticket (bất kể trạng thái) → bỏ qua.
   - **Tạo ticket**: `POST /rest/api/3/issue`.
     - `description` phải ở dạng **ADF** (Atlassian Document Format), chỉ gồm text node. Nội dung: tiêu đề đã cắt ngắn, `rule_id`, `path:line`, link PR, link run, fingerprint.
     - **Không** chứa đoạn mã.
   - **Gán người**: tác giả PR → `user_map` → `assignee.accountId`. Map không được thì để trống và ghi chú "không map được <login>" trong description.
   - Tối đa `max_new_per_run` ticket mỗi lượt; phần vượt ghi vào kết quả.
   - **Egress**: ghi `record()` trước mỗi lượt gọi, với `data_categories=["finding_title", "code_location"]` và host của `JIRA_BASE_URL`. `deny` → không có request nào.
   - **Fail-open**: 401/403/404/429/5xx/timeout/mạng → trả `{"jira": "error: <mã>", "created": n}` và không raise ra ngoài.
   - CLI: `python -m qc_agent.integrations.jira --run-dir … --project …` luôn exit 0 và in JSON kết quả.
   - Log `jira.sync`: số tạo mới, số trùng, số lỗi, mã lỗi. Không có nội dung.
3. **`tests/fakes.py: FakeJira`**, là HTTP server, để E2E ở S3-07 dùng lại. Test unit của bước này dùng `httpx.MockTransport` theo đúng plan.

## Test bắt buộc: `tests/test_jira_sync.py`

- Tạo đúng 1 ticket cho 1 finding Low; `assignee` đúng theo `user_map`; description là ADF hợp lệ và không chứa chuỗi đánh dấu đặt trong "đoạn mã".
- Chạy lại: JQL tìm thấy label → 0 ticket mới.
- Finding `medium`/`critical` → không có ticket.
- Không map được tác giả → không có assignee, description có ghi chú.
- 401 và 503 → kết quả có lỗi, hàm không raise.
- `egress deny` → transport raise nếu bị gọi (chứng minh không có request).
- Vượt `max_new_per_run` → dừng đúng số lượng.
- Log và kết quả không chứa `JIRA_API_TOKEN`.

## Ngoài phạm vi

Gọi Jira trong workflow và `ci.py`, thêm secret vào workflow (S3-07). Không gọi Jira thật trong phiên này.
