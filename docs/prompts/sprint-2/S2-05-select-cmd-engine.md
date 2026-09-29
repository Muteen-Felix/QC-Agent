# S2-05 · `qc-agent select` + floor lớp 2 trong core + báo cáo "Phạm vi chạy" (plan S2.5 lớp core, S2.6)

- Branch: `feat/s2-05-select-engine`
- Tiền điều kiện: S2-04 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S2.5, S2.6; DoD các dòng "Injection" (sửa tay `selection.json`) và "`selection.json` có trong artifact, lý do chọn hiện trong report"
- `src/qc_agent/core/{cli.py, engine.py, project.py, report.py}`; `src/qc_agent/integrations/github.py` (`clean_md`, `render_summary`)
- `src/qc_agent/selector/*`

## Mục tiêu

Hoàn thiện đường đi của trigger `pr`:

```
qc-agent select --base … --head … --out selection.json      (có LLM, luôn exit 0 khi đã ghi được file)
qc-agent run --project X --mode pr --trigger pr --selection selection.json   (không LLM)
```

`core/` tự gộp lại floor. Vì vậy sửa tay `selection.json` để bỏ floor thì floor **vẫn chạy**, và report ghi rõ là selection thiếu floor.

## Việc cần làm

1. **`src/qc_agent/selector/cli.py`**, gọi lười từ `core/cli.py` qua nhánh `select`:
   - `qc-agent select --project --mode --sut-root --base --head --out [--projects-dir] [--workers-dir]`
   - Luồng: nạp policy, suite và registry (không probe) → `suites_by_worker` → `prune` → `rules.decide` → `agent.select` → ghi `--out` → in tóm tắt một dòng ra stdout.
   - **Exit code**:
     - `0` khi đã ghi được selection, kể cả khi là FULL SET do fallback;
     - `3` chỉ khi lỗi cấu hình (project/mode sai, `base`/`head` không hợp lệ, git lỗi). Khi đó workflow sẽ chạy FULL SET.
2. **`core/cli.py`**: thêm `--selection FILE`, chỉ đi với `--trigger pr`. Trigger `pr` mà thiếu `--selection` thì chạy FULL SET và ghi chú trong report.
3. **`core/engine.py`**, đọc và validate selection bằng `schemas/selection.json` (phần này nằm trong `core`, không import `selector`):
   - **Floor lớp 2**: `floor_suites` = các suite của `floor_workers` trong policy. `only_suites = selection.suites ∪ floor_suites`, trừ khi `full_set` thì chạy toàn bộ.
   - Nếu core phải tự thêm floor, ghi `floor_enforced_by_core: [...]` vào bản selection nằm trong plan text. Đây là bằng chứng selection đã bị sửa hoặc thiếu.
   - Suite nằm ngoài policy của mode → `PlanError` (exit 3), đi qua kiểm tra có sẵn của `build_plan`.
   - `--rerender` dựng lại đúng phạm vi và verdict nhờ selection nằm trong plan text.
4. **`core/report.py`**: thêm mục `## Phạm vi chạy`, đặt ngay sau VERDICT. Nội dung:
   - trigger, source, `full_set`, `fallback_reason`;
   - floor;
   - suite đã chạy / tổng số suite của policy;
   - danh sách `worker: lý do`: một dòng mỗi worker, cắt ngắn, bỏ ký tự điều khiển;
   - cảnh báo `floor_enforced_by_core` nếu có.

   `report.json` thêm khối `selection`, chỉ gồm tóm tắt, không có diff.
5. **`integrations/github.py: render_summary`**: thêm một dòng "Phạm vi: …". Rationale đi qua `clean_md` vì nó là văn bản do LLM viết từ diff của người gửi PR.
6. **`CLAUDE.md` và `docs/usage-ci.md`**: thêm lệnh `select` và các cờ `--trigger/--selection/--workers`.

## Test bắt buộc

- `tests/test_floor_enforced.py`
  - Tạo `selection.json` hợp lệ **không có** floor → floor vẫn chạy; report có cảnh báo; plan text có `floor_enforced_by_core`.
  - Selection có `full_set: true` → chạy mọi suite.
  - Selection có suite ngoài policy → exit 3.
  - Selection sai schema → exit 3.
- `tests/test_selector_cli.py`
  - Repo git tạm + `FakeAnthropic` → exit 0, file hợp lệ.
  - Thiếu key → exit 0, `fallback_reason=missing_api_key`, FULL SET.
  - Diff chỉ chạm docs → chỉ floor, **0** lời gọi LLM.
  - Diff chạm `Dockerfile` → FULL SET, **0** lời gọi LLM.
  - `--base "-x"` → exit 3.
- **Rerender**: chạy với selection rồi `--rerender` → cùng verdict, và `report.rerender.md` có mục "Phạm vi chạy".
- Không có `--trigger` → hành vi và `plan_id` như trước (test của S2-01 vẫn xanh).

## Ngoài phạm vi

Chạy song song (S2-06), golden set (S2-07), workflow (S2-08), cache (S4-02).
