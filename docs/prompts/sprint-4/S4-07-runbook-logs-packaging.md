# S4-07 · Log có cấu trúc, runbook vận hành, đóng gói image/wheel, tài liệu (plan S4.7 + các file đóng gói của S4)

- Branch: `feat/s4-07-ops-packaging`
- Tiền điều kiện: S4-05 đã merge. Làm được trước hoặc song song với S4-06.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.7, danh sách "File sửa" của Sprint 4 (Dockerfile, pyproject, `image.yml`, docs)
- `src/qc_agent/logging_setup.py`, `tests/test_logging.py` (nhất là `test_secrets_and_spec_inputs_never_appear_in_logs`), `docs/README.md` (mục log)
- `Dockerfile`, `pyproject.toml` (hatch `force-include`, `packages`), `.github/workflows/image.yml`, `tests/test_dockerfile_static.py`
- `README.md`, `docs/usage-ci.md`, `docs/onboarding.md`, `docs/groundtruth.md`, `docs/user-guide-sprint-1.md`, `docs/groundtruth-real-sut.md`
- `src/qc_agent/llm/{client.py, agent_loop.py, prices.py}` (provider Gemini, GT agent, bảng giá sau S4-03), `src/qc_agent/settings.py`

## Mục tiêu

Vận hành được mà không cần đọc code:
- log có event theo không gian tên, **không** chứa nội dung bí mật;
- image và wheel mang đủ prompt, template và schema;
- tài liệu khớp với hệ thống thật.

## Việc cần làm

1. **Rà log event**
   - Liệt kê mọi event `llm.*`, `gt.*`, `selector.*`, `review.*`, `jira.*` đang có. Chuẩn hoá tên và trường (không có nội dung; token/số lượng/thời gian/lý do là đủ).
   - Danh sách đã có trong code (kiểm 2026-10-06), cộng các event S4-02/03 thêm (`selector.cache`, `gt.cache`): `llm.call`, `llm.agent`, `llm.retry`, `llm.fallback` (Gemini), `gt.cli`, `gt.generate`, `gt.agent`, `gt.validate`, `selector.prune`, `selector.decision`, `review.posted`, `review.skipped`, `jira.sync`. Đổi tên event đang có thì ghi lại tên cũ → mới trong báo cáo.
   - Mở rộng test chống rò rỉ: một test "chuỗi đánh dấu" chạy **trọn chuỗi** với fake (PRD, diff, key, token Jira, rationale đều chứa marker), rồi assert stderr và mọi file trong `runs/` **trừ** evidence/selection/report đều không chứa marker.
   - Phủ cả GT agent (mã nguồn SUT chứa marker, đi qua `agent_loop`) và nhánh Gemini (`GEMINI_API_KEY` chứa marker, fake server trả 429 để đi qua `llm.retry`/`llm.fallback`).
   - Ghi rõ file nào được phép chứa gì: `selection.json` có rationale đã làm sạch; `report.md` có tiêu đề finding đã làm sạch.
2. **Đóng gói**
   - Xác nhận `groundtruth/prompts/*.md` (gồm `gt_generate.md` **và `gt_agent.md`**), `selector/prompts/*.md`, `scaffold/tmpl/gt-*.tmpl`, `llm/prices.py` và `schemas/{ground_truth,module_map,selection}.json` có trong wheel và trong image. Thiếu `gt_agent.md` thì `gt generate --agent` hỏng trong CI.
   - Xác nhận image đọc/ghi được `test-cases.xlsx` (`openpyxl` là dependency chính; chạy `gt export-xlsx` trên catalog mẫu trong container).
   - Test đọc bằng `importlib.resources` hoặc qua `settings.resolved_schemas_dir`, **không** dùng đường dẫn nguồn.
   - Có Docker thì chạy `docker run … python -c "…"` để đọc từng file.
   - `pyproject.toml` chỉ sửa nếu thiếu.
   - `Dockerfile`: `pytest` đã có từ S1-03; kiểm lại, và thêm dòng `--version` vào bước tự kiểm của image nếu hợp lý.
3. **`.github/workflows/image.yml`**: Job Summary in digest của image kèm lệnh gợi ý ghim cho cả hai caller. Action vẫn ghim SHA.
4. **Runbook vận hành** trong `docs/e2e-runbook.md`, mục "Vận hành", hoặc file riêng `docs/operations.md`:
   - xoay vòng `ANTHROPIC_API_KEY` và `JIRA_API_TOKEN`;
   - đọc `llm_usage.json` và dòng chi phí;
   - khi fallback FULL SET tăng đột biến (529/quota/token_cap);
   - khi Jira hỏng;
   - làm sạch cache;
   - rollback: tắt Select bằng cách không truyền secret, gate tự chạy FULL SET;
   - cập nhật bảng giá trong `llm/prices.py`;
   - đổi provider: Claude ↔ Gemini bằng tiền tố `QC_GT_MODEL`/`QC_SELECTOR_MODEL` (không có công tắc thứ hai); các biến chỉ-Gemini `QC_LLM_MAX_RETRIES`, `QC_LLM_MIN_INTERVAL_S`, `QC_LLM_FALLBACK_MODELS`, `QC_GEMINI_THINKING_LEVEL`; `est_usd` của Gemini là `null`;
   - GT agent: bật/tắt (`--agent`, `QC_GT_GENERATOR`), ngân sách (`QC_GT_AGENT_MAX_COST_USD`, `QC_GT_AGENT_MAX_TURNS`, `QC_GT_AGENT_MAX_WALL_S`), và việc nó gửi **mã nguồn SUT** ra ngoài (egress `source_code`): chỉ bật khi câu hỏi #3 đã cho phép;
   - SUT cần DB: dịch vụ DB phụ của S4-09, cách xoay secret kết nối DB.
5. **Tài liệu**:
   - `README.md` (tổng quan v2 và 3 lệnh chính);
   - `docs/groundtruth.md`, `docs/user-guide-sprint-1.md`, `docs/groundtruth-real-sut.md` (lệnh cụ thể dễ lệch sau S4-01…S4-10);
   - `docs/usage-ci.md` và `docs/onboarding.md` (một lệnh `init` sinh đủ file, secret cần có, chi phí);
   - `docs/README.md` (mục log và egress có thêm các event mới);
   - `CLAUDE.md` (Commands và env mới).

   Mọi đường dẫn và lệnh trong tài liệu phải chạy được: kiểm tay hoặc bằng test.

## Nghiệm thu

- `pytest -q` xanh, gồm cả test chống rò rỉ mở rộng.
- Có Docker: build image và đọc được đủ file.
- Mọi action và image vẫn ghim SHA/digest (`test_workflow_static`).

## Ngoài phạm vi

Tính năng mới. Sửa lỗi logic phát hiện ra → báo lại.
