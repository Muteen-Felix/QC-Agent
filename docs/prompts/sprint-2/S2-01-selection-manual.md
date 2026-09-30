# S2-01 · `selection.json` + trigger manual chạy thẳng (plan S2.1 + khung S2.6)

- Branch: `feat/s2-01-selection-manual`
- Tiền điều kiện: Sprint 1 đã nghiệm thu (hoặc ít nhất S1-00 đã merge).

## Đọc trước

- `docs/prompts/_common.md`; plan: mục tiêu Sprint 2, "Định dạng `selection.json`", S2.1, S2.6, DoD dòng "Manual"
- `src/qc_agent/core/cli.py` (`_parser`, `_run`, `_rerender`)
- `src/qc_agent/core/engine.py` (`run_project`, `_execute`, `judge`)
- `src/qc_agent/core/project.py` (`build_plan`: `only_suites` và cách `yellow_on_fail` được đưa **vào plan text** để hash vào `plan_id`)
- `src/qc_agent/core/plan.py: load_plan` (đã chấp nhận key `selection`)
- `src/qc_agent/core/registry.py` (`Worker.capabilities`, `load_many`, `pick`)
- `tests/projkit.py`, `tests/securitykit.py`, `tests/fixtures/workers/`

## Mục tiêu

Tách **trigger** (chọn phạm vi) khỏi **mode** (chọn policy):

- `--trigger manual --workers semgrep,schemathesis` chạy **đúng** các suite của những worker này trong policy của mode. Không có suite nào khác, không có floor, không gọi LLM.
- Không truyền `--trigger` thì hành vi y như cũ (chạy toàn bộ policy): `plan_id` không đổi, test cũ không phải sửa.

Bước này cũng dựng sẵn đường ống `selection` trong engine để S2-05 dùng cho trigger `pr`.

## Việc cần làm

1. **`schemas/selection.json`** (schema dữ liệu, không thuộc contract), theo đúng ví dụ trong plan và bổ sung các trường sau:
   - `version: 1`
   - `trigger_type` ∈ {`pr`, `manual`}
   - `source` ∈ {`manual`, `rules`, `llm`, `fallback`, `cache`}
   - `full_set: bool`
   - `diff_sha256: string | null`
   - `floor`, `workers`, `suites`: mảng tên, không trùng lặp
   - `rationale`: map `worker → string` (≤ 200 ký tự mỗi giá trị)
   - `fallback_reason` ∈ {`null`, `timeout`, `unavailable`, `bad_output`, `unknown_worker`, `missing_api_key`, `egress_denied`, `token_cap`}
   - `llm`: `null` hoặc `{model, prompt_version, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}`
2. **`core/registry.py`**: thêm hàm tra ngược, chỉ đọc manifest và **không probe**:
   - `workers_for(registry, capability, lane, oracle_kind) -> list[str]`, dùng đúng điều kiện tương thích như `pick`.
3. **`core/project.py`**: thêm `suites_by_worker(project, mode, suites, workers) -> dict[str, list[str]]`.
   - Một suite thuộc về worker W khi có task trong suite đó mà W nhận được.
   - Dùng tập suite của policy của mode; kết quả sắp xếp tất định.
   - `build_plan(..., selection=None)`: khi có `selection` thì ghi nó vào plan dưới key `selection`. Nhờ vậy nó được hash vào `plan_id`, và `--rerender` dựng lại được phạm vi.
4. **`src/qc_agent/selector/__init__.py` và `payload.py`**
   - `TriggerPayload.manual(workers, project_cfg, mode, suites, registry) -> dict`: trả về một selection hợp lệ theo schema, với `source=manual`, `floor=[]`, `full_set=false`.
   - Tên worker lạ, hoặc worker không có suite nào trong policy của mode → `PlanError` (exit 3). Thông điệp liệt kê các worker hợp lệ.
   - File này **không** import `selector.agent` hay `qc_agent.llm`.
5. **`core/cli.py`**: thêm `--trigger {pr,manual}` và `--workers a,b`.
   - `--workers` chỉ đi với `--trigger manual`; `--trigger manual` bắt buộc có `--workers` và `--project`.
   - Cờ `--selection` để dành cho S2-05.
   - Import `selector.payload` theo kiểu **lười**, ngay trong nhánh manual.
6. **`core/engine.py: run_project(..., selection: dict | None = None)`**
   - validate selection theo `schemas/selection.json` (lỗi → `PlanError`);
   - lấy `only_suites = selection["suites"]`, trừ khi `full_set` → dùng `None`;
   - ghi `run_dir/selection.json`.
   - Floor chưa cần gộp ở bước này: manual không có floor; phần của `pr` làm ở S2-05.
7. **`RunContext`/report**: chỉ cần mang `selection` theo để S2-05 render mục "Phạm vi chạy". Mục đó chưa cần viết.

## Test bắt buộc

- `tests/test_trigger_manual.py`: dựng project và suite trong `tmp_path` bằng các helper sẵn có, worker giả cung cấp `code.sast` và `api.property`.
  - `--trigger manual --workers semgrep,schemathesis` → thư mục `results/` có **đúng** các `task_id` của hai suite tương ứng.
  - **Chặn mạng**: monkeypatch `socket.socket.connect` để raise → vẫn chạy xong. Trong subprocess, `qc_agent.llm` và `qc_agent.selector.agent` không có trong `sys.modules`.
  - Worker lạ và worker ngoài policy → exit 3.
  - `run_dir/selection.json` tồn tại và hợp lệ. `plan.yaml` trong `run_dir` có key `selection`. `--rerender RUN_DIR` ra đúng verdict.
  - Không truyền `--trigger` → `plan_id` giống hệt trước thay đổi. Chốt bằng một test so với giá trị tính từ plan cũ.
- `tests/test_selector_payload.py`: ánh xạ worker→suite; suite có nhiều capability; `prefer`.

## Ngoài phạm vi

`floor_workers` và `full_set_paths` (S2-02), pruner (S2-03), LLM (S2-04), `--selection` và `qc-agent select` (S2-05), workflow (S2-08).
