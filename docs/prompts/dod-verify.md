# Nghiệm thu một sprint (DoD)

Dùng lệnh: `Đọc docs/prompts/_common.md rồi thực hiện docs/prompts/dod-verify.md cho Sprint <N>`.

- Branch: `chore/s<N>-dod` (chỉ cần khi có sửa nhỏ hoặc tick checkbox)
- Tiền điều kiện: mọi prompt của Sprint N đã merge.

## Mục tiêu

Mỗi dòng trong **"DoD Sprint N"** và **"Điều kiện chung"** của plan có **bằng chứng** chạy được: một lệnh hoặc một test, kèm kết quả. Đây là phiên **kiểm**, không phải phiên xây.
- Sửa nhỏ (≤ 20 dòng, lỗi hiển nhiên hoặc thiếu test) thì được làm.
- Thiếu hụt lớn hơn → báo lại kèm một prompt ngắn để mở phiên sửa riêng.

## Quy trình

1. Đọc DoD Sprint N và "Điều kiện chung" (plan §2).
2. Ánh xạ từng dòng DoD sang bằng chứng, theo bảng gợi ý bên dưới. Test chưa có mà viết được nhanh thì viết.
3. Chạy mọi lệnh **không** tốn tiền và **không** chạm hệ thống bên ngoài.
4. Với dòng cần LLM thật, repo hay Jira thật:
   - liệt kê lệnh cần chạy và ước tính chi phí;
   - **HỎI TÔI** rồi mới chạy;
   - không được phép thì ghi **PENDING**.
5. Chỉ đánh `[x]` trong plan cho dòng có bằng chứng **đạt**.
6. Báo cáo bằng bảng `Dòng DoD | Bằng chứng (lệnh/test) | Kết quả | Ghi chú`, rồi liệt kê: PENDING (thiếu gì), FAIL (prompt sửa đề xuất), và rủi ro còn lại.

## Gợi ý bằng chứng theo sprint

**Điều kiện chung (mọi sprint)**: cổng kiểm ở `_common.md` §5, và test chống rò rỉ log (`tests/test_logging.py` cùng các test marker của từng module).

**Sprint 1**

| Dòng DoD | Bằng chứng |
|---|---|
| Fake LLM → đúng bộ golden, chạy lại 2 lần giống từng byte | `tests/test_gt_cli.py`, `tests/test_gt_render.py` |
| LLM thật (median 3): AC coverage ≥ 90%, TC xanh ≥ 90% | `python tools/eval_groundtruth.py --llm real --runs 3 --yes` (**hỏi**; Sonnet 5 $2/$10 mỗi MTok) |
| Suite đã duyệt bắt ≥ 9/10 mutant và bắt BUG-1 | `python tools/eval_groundtruth.py --llm fake` (phần mutant) + `qc-agent run --project noteboard --mode pr` với `QC_BUGS=4…13` |
| `gt regen` giữ 100% TC approved/qa | `tests/test_gt_cli.py` (property test) |
| `gt validate` exit 1 khi còn draft · protect đúng `require_code_owner_reviews` qua fake GitHub · kiểm tay trên repo thật | `tests/test_gt_cli.py`, `tests/test_protect_ground_truth.py`; kiểm tay → PENDING tới S4-06 nếu chưa có sandbox |
| Egress deny → 0 HTTP, có dòng `egress.jsonl`, log không có PRD | `tests/test_llm_client.py`, `tests/test_gt_generate.py` |

**Sprint 2**

| Dòng DoD | Bằng chứng |
|---|---|
| Manual 100%, không có lời gọi LLM (test chặn mạng) | `tests/test_trigger_manual.py` |
| PR: recall ≥ 90%, precision ≥ 80% (median 3); recall cuối 100% với core/security | `python tools/eval_selector.py --llm real --runs 3 --yes` (**hỏi**; Haiku 4.5 $1/$5 mỗi MTok) |
| Fallback 5 loại → FULL SET | `tests/test_selector_agent.py`, `tests/test_selector_cli.py` |
| Injection 10/10 · sửa tay selection vẫn có floor | `tests/test_selector_golden.py`, `tests/test_floor_enforced.py` |
| Docs → chỉ floor · Dockerfile → FULL SET, 0 LLM | `tests/test_selector_rules.py`, `tests/test_selector_cli.py` |
| P95 Select ≤ 20s · `selection.json` có trong artifact · lý do hiện trong report | output của `eval_selector` + harness local hoặc `tests/test_reusable_workflow.py` |

**Sprint 3**

| Dòng DoD | Bằng chứng |
|---|---|
| Phủ 100% tổ hợp · property "LLM không chặn" | `tests/test_gatekeeper.py`, `tests/test_findings_normalize.py` |
| E2E với fake GitHub và fake Jira (Critical, Medium, Low, chạy lại) | `tests/test_gatekeeper_e2e.py` |
| ≥ 90% đúng dòng · 100% finding không vị trí nằm trong thân | `tests/test_pr_review.py` |
| Jira 401/5xx → verdict giữ nguyên | `tests/test_jira_sync.py`, `tests/test_gatekeeper_e2e.py` |
| Không cần `QC_DATABASE_URL` · grep `apply_debt` trong `src/` rỗng · migration 0005 còn nguyên | test S3-04, lệnh grep, `python -m qc_agent.jobs.migrate upgrade` |
| `CONTRACT.lock = 2.0.0` và `freeze_contract --check` exit 0 | lệnh `python tools/freeze_contract.py --check` |

**Sprint 4**

| Dòng DoD | Bằng chứng |
|---|---|
| 5 kịch bản A–E tự động trên GitHub thật | `docs/e2e-runbook.md` + script `tools/e2e/` (**hỏi**; cần sandbox) |
| ≥ 9/10 lần xanh liên tiếp | script chạy lặp của S4-06 |
| Token giảm ≥ 40% (median), recall S2 vẫn ≥ 90% | `python tools/eval_cost.py --llm-tokens count` + `eval_selector --llm real` (**hỏi**) |
| Chạy lại → cache hit, 0 LLM · `cache_read_input_tokens > 0` từ lần 2 | `tests/test_selector_cache.py`, `tests/test_gt_cache.py`; dòng cache theo quyết định ở S4-03 (Haiku 4.5 cần prefix ≥ 4096 token) |
| Vượt trần → FULL SET, gate không đỏ · Check Run có dòng chi phí · gate chậm thêm ≤ 1 phút | test S4-03 + số đo ở S4-06 |
| Mọi action và image ghim SHA/digest (kể cả image DB phụ của S4-09) | `tests/test_workflow_static.py`, `tests/test_dockerfile_static.py` |
| *(Mang từ DoD S1)* Kiểm tay trên repo thật: tài khoản không phải QA push vào `.qc-agent/` thì bị từ chối | bằng chứng kịch bản (B) của S4-06; đạt thì ghi thêm link bằng chứng vào dòng DoD S1 tương ứng trong plan (**hỏi**; cần sandbox public/gói trả phí) |
