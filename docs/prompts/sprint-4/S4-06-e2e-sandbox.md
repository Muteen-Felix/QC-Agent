# S4-06 · E2E trên repo sandbox thật: 5 kịch bản A–E, 9/10 lần xanh (plan S4.6)

- Branch: `feat/s4-06-e2e-sandbox` (trong repo qc-agent: script và runbook)
- Tiền điều kiện: S4-05 xanh trên máy local.
- **Bên ngoài** (plan §3): repo sandbox GitHub, Jira sandbox kèm `user_map`, `ANTHROPIC_API_KEY` có trần ngân sách, image qc-agent đã publish và ghim digest.

## Đọc trước

- `docs/prompts/_common.md`, mục 5: không đụng tới hệ thống bên ngoài khi chưa được xác nhận
- Plan S4.6 và DoD Sprint 4; `docs/onboarding.md`, `docs/usage-ci.md`, `docs/groundtruth.md`, `docs/e2e-runbook.md` (nếu đã có từ S4-05)
- `tools/protect_ground_truth.py`, `.github/workflows/image.yml` (cách lấy digest)

## HỎI TRƯỚC

Liệt kê cho tôi các thông tin cần có, rồi **dừng lại chờ tôi điền**:
- tên repo sandbox;
- team QA dùng trong CODEOWNERS;
- một tài khoản không phải QA để thử push;
- project key của Jira và `user_map`;
- digest của image.

Mọi hành động có tác động ra ngoài đều phải được tôi xác nhận **từng hành động một**: push, mở hay merge PR, bật branch protection, tạo secret, tạo ticket thật. Cách làm mặc định: viết script và lệnh để **tôi** tự chạy, còn bạn đọc kết quả qua `gh`/API ở chế độ chỉ-đọc.

## Việc cần làm

1. **`tools/e2e/`**
   - **Script dựng sandbox**, idempotent:
     - copy toy app noteboard và PRD mẫu vào repo sandbox;
     - chạy `qc-agent init --qa-team … --prd-glob docs/prd/**` để có `qc-gate.yml`, `qc-groundtruth.yml`, `CODEOWNERS`;
     - in danh sách secret cần tạo;
     - gọi `protect_ground_truth.py --dry-run` rồi mới chạy thật (**sau khi tôi xác nhận**).
   - **Script chạy từng kịch bản**: tạo branch/commit theo kịch bản, rồi in lệnh `gh` để mở PR.
   - **Script thu kết quả**: đọc check run, review comment, ticket Jira và artifact (`selection.json`, `llm_usage.json`), rồi đối chiếu với bảng kỳ vọng.
2. **Năm kịch bản** (plan S4.6; bảng kỳ vọng giống S4-05)
   - **(A)** BA commit PRD → PR sinh GT được mở.
   - **(B)** QA duyệt rồi merge. Tài khoản không phải QA thử đổi `.qc-agent/` → **bị chặn**: không merge được khi thiếu QA approve, và không push thẳng vào main được.
   - **(C)** Dev mở PR có lỗi Critical → PR đỏ.
   - **(D)** Dev mở PR chỉ có lỗi Low → PR xanh, có inline comment, có ticket Jira gán cho tác giả.
   - **(E)** `workflow_dispatch` với danh sách worker → chạy đúng những worker đó.
3. **Độ ổn định**
   - Script chạy lại chuỗi C+D **10 lần liên tiếp** và ghi lại kết quả từng lần.
   - Lần fail → phân loại: flaky hạ tầng, lỗi thật, hay LLM.
   - Tính tỉ lệ xanh; DoD cần ≥ 9/10.
4. **Hiệu năng và chi phí**
   - So thời gian job gate với baseline, tức cùng SUT nhưng dùng workflow trước v2 hoặc chạy FULL SET không có Select. DoD: chậm thêm ≤ 1 phút.
   - Tổng hợp dòng chi phí mỗi PR từ `llm_usage.json`.
   - Chạy lại cùng commit → cache hit và 0 lời gọi LLM. `cache_read_input_tokens > 0` từ lần 2: xem quyết định ở S4-03.
5. **`docs/e2e-runbook.md`**: điều kiện tiên quyết, các bước dựng sandbox, từng kịch bản (thao tác → kỳ vọng → bằng chứng cần chụp hoặc lưu), cách đọc artifact, xử lý sự cố (fork, `GITHUB_TOKEN` không kích hoạt workflow, 529/quota → FULL SET, Jira 401), và bảng kết quả của 10 lần chạy.

## Nghiệm thu

Điền bảng bằng chứng cho từng dòng DoD Sprint 4. Dòng nào chưa làm được vì thiếu tài nguyên bên ngoài thì ghi **PENDING** kèm lý do, không đánh dấu đạt.

## Ngoài phạm vi

Sửa logic sản phẩm. Lỗi phát hiện ở bước này → báo lại kèm prompt ngắn để sửa trong một phiên riêng.
