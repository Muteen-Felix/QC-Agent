# S4-02 · Cache kết quả Select theo hash + cache GT theo PRD (plan S4.2)

- Branch: `feat/s4-02-cache`
- Tiền điều kiện: S4-01 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.2, mô tả `selector/cache.py` (khoá cache), DoD S4 ("chạy lại cùng commit: cache hit, 0 lời gọi LLM")
- `src/qc_agent/selector/{agent.py, cli.py, pruner.py}` (`PrunedDiff.sha256`), `schemas/selection.json` (`source: cache`)
- `src/qc_agent/groundtruth/{generate.py, agent.py, cli.py, xlsx.py}` (`prompt_version`, `prd.sha256`, cờ `--agent`/`--no-agent`, `QC_GT_GENERATOR`)
- `.github/workflows/qc-gate.reusable.yml`, `.github/workflows/qc-groundtruth.reusable.yml`: chỗ đặt `actions/cache` từ S4-01

## Mục tiêu

Chạy lại cùng một thay đổi thì **không gọi LLM**. Cache không bao giờ được làm lỏng floor, và không bao giờ giữ lại kết quả của một lần gọi lỗi.

## Việc cần làm

1. **`src/qc_agent/selector/cache.py`**
   - **Khoá** = `sha256(pruned.sha256 + module_map_sha + policy_sha + model + prompt_version + allowlist_sha)`.
     - `allowlist_sha` được thêm so với plan: đổi tập worker thì phải bỏ cache.
     - `policy_sha` lấy từ `resolve_project(...)[1]["sha256"]`.
   - **Chỉ lưu phần của LLM**: `selections` đã validate, cộng `usage` lúc tạo. Không lưu phần floor, rules hay FULL SET, vì các phần đó luôn được tính lại tất định mỗi lần chạy.
   - **Không cache**: kết quả fallback (mọi `fallback_reason` ≠ `null`), rules `full_set`, `floor_only`. Nếu cache những thứ này thì một lần 529 thoáng qua sẽ bị "đóng băng".
   - **Nơi lưu**: `QC_SELECT_CACHE_DIR`, mặc định `~/.cache/qc-agent/select`; thêm vào `settings.py` và `.env.example`. Mỗi entry là một file `<khoá>.json`, ghi nguyên tử (ghi tạm rồi `os.replace`).
   - **Khi đọc**: validate lại theo schema, và worker phải nằm trong allowlist *hiện tại*. File hỏng hoặc không hợp lệ → bỏ qua và coi như miss, không lỗi.
   - Hit → `source: "cache"`, `llm` giữ usage lúc tạo và thêm `cache_hit: true`.
   - **Sự thật về `schemas/selection.json`** (đã kiểm 2026-10-06): enum `source` **đã có** `cache`, enum `fallback_reason` **đã có** `token_cap`. Khối `llm` có `additionalProperties: false` nên muốn thêm `cache_hit` phải thêm trường này (tuỳ chọn, không đưa vào `required`) vào schema. File này **không** nằm trong `CONTRACT.lock`, nên không cần quy trình SemVer, nhưng `tests/test_selector_cli.py`, `tests/test_floor_enforced.py`, `tests/test_trigger_manual.py` phải xanh.
   - **Mô hình rủi ro**, ghi vào docstring và `docs/usage-ci.md`:
     - Cache của Actions theo ref; PR không ghi được vào cache của `main`.
     - Kịch bản xấu nhất là cache bị đầu độc làm *thiếu* worker ngoài floor trong đúng PR đó. Kịch bản này tương đương injection, và floor vẫn được `core` ép.
2. **Nối vào `selector/agent.select`** (hoặc cli): tra cache trước khi gọi LLM; ghi cache sau khi gọi thành công.
3. **Cache GT**
   - GT hiện có **hai bộ sinh**: một lời gọi (`generate.py`, mặc định) và agent nhiều lượt (`agent.py`, bật bằng `--agent` hoặc `QC_GT_GENERATOR=agent`). Plan viết trước khi có agent.
   - **Đề xuất: chỉ cache bộ sinh một lời gọi.** Agent đọc mã nguồn SUT qua nhiều lượt, nên input không chỉ là PRD + OpenAPI; cache theo PRD sẽ trả kết quả cũ khi code đổi. Agent đi đường cũ (không tra, không ghi cache), log `gt.cache` với `skipped: agent`. Nếu thấy cần cache agent thì **HỎI TRƯỚC** và nêu khoá đề xuất (phải gồm hash cây mã nguồn agent được đọc).
   - Khoá = `sha256(generator + prd.sha256 + openapi_sha + model + prompt_version)`. Provider (Claude/Gemini) đã nằm trong `model` (chọn theo tiền tố).
   - Lưu output của tool **đã validate**, tức ứng viên trước khi merge và render.
   - `gt generate/regen` gặp hit thì không gọi LLM, render tất định ra đúng các file như cũ.
   - Thư mục: `QC_GT_CACHE_DIR`.
4. **Workflow**
   - `actions/cache` (ghim SHA) với `key: qc-select-${{ hashFiles(...) }}` hoặc key gốc cộng `restore-keys` cho thư mục select. Mount vào container bằng `-v`. Làm tương tự cho GT trong `qc-groundtruth.reusable.yml`.
   - Workflow GT: **không** bật cache khi input `agent: true` (khớp với mục 3).
   - Quan trọng là tính đúng: đọc/ghi cache là best-effort, lỗi không được làm hỏng job.
5. **Log**: `selector.cache` và `gt.cache`, gồm hit/miss và 8 ký tự đầu của khoá. Không có nội dung.

## Test bắt buộc

- `tests/test_selector_cache.py`, dùng `FakeAnthropic` có đếm lời gọi:
  - lần 1 → 1 lời gọi, ghi cache;
  - lần 2 cùng diff → **0** lời gọi, `source=cache`, và selection cuối **giống hệt** lần 1 (trừ `source` và `cache_hit`).
  - Đổi policy, module-map, prompt_version hoặc model → miss.
  - Fallback 529 → không có file cache.
  - Entry hỏng hoặc chứa worker lạ → miss, không lỗi.
  - Floor luôn có mặt, kể cả khi entry cache bị sửa tay để bỏ floor.
- `tests/test_gt_cache.py`
  - Chạy `gt generate` hai lần → lần 2 có 0 lời gọi, và file sinh ra giống hệt từng byte, **kể cả `test-cases.xlsx`** (mặc định được ghi; `xlsx.py` đã cố định ngày tạo nên kỳ vọng vẫn giống byte; nếu không giống thì báo lại, không tắt xlsx để test xanh).
  - `gt generate --agent` hai lần → không có file cache nào, lần 2 vẫn gọi LLM (FakeAnthropic đếm được).
  - Đổi `generator`, `model` hoặc `prompt_version` → miss.

## Ngoài phạm vi

`cache_control` / prompt caching phía Anthropic và trần token (S4-03).
