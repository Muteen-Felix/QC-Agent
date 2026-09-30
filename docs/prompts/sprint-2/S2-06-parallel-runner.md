# S2-06 · Task Runner chạy song song theo tầng toposort (plan S2.7)

- Branch: `feat/s2-06-parallel-runner`
- Tiền điều kiện: S2-02 đã merge (có key `max_parallel`). Chạy song song được với S2-04/05.

## Đọc trước

- `docs/prompts/_common.md`; plan S2.7
- `src/qc_agent/core/runner.py`: `run_all` (hiện raise `NotImplementedError` khi `parallel=True`), `_run_task`, `_attempt`, `_spawn`, `_ACTIVE`, `terminate_active`
- `src/qc_agent/core/engine.py` (`_execute`, `_select`), `src/qc_agent/core/plan.py: toposort`
- `workers/_template.yaml`: `parallel_safe` được khai **theo capability** (mặc định `true`). k6, midscene và playwright để `false`.
- `src/qc_agent/core/egress.py: record` (ghi file từ nhiều luồng), `src/qc_agent/logging_setup.py: bind` (ngữ cảnh theo luồng), `tests/test_logging.py::test_context_is_isolated_between_threads_running_jobs`

## Mục tiêu

Task cùng một tầng toposort chạy đồng thời, tối đa `max_parallel` task, theo policy của mode. Hành vi quan sát được phải **y như chạy tuần tự**, chỉ khác thời gian:
- cùng `results`, cùng thứ tự key;
- cùng file `specs/` và `results/`;
- cùng verdict và `run_signature`.

`max_parallel: 1` (mặc định) nghĩa là giữ nguyên hành vi hiện tại.

## Việc cần làm

1. **`runner.run_all(specs, plan_only, registry, run_dir, *, layers=None, max_parallel=1, …)`**
   - `layers=None` hoặc `max_parallel == 1` → đi đúng đường tuần tự như cũ.
   - Còn lại: chạy từng tầng. Mỗi tầng dùng `ThreadPoolExecutor(max_workers=max_parallel)`, và đợi cả tầng xong mới sang tầng sau, để `depends_on` thấy đúng status cuối cùng.
   - **Độc quyền**: task mà `worker.capabilities[cap].get("parallel_safe", True)` là `False` chạy **một mình**. Trước nó không còn task nào đang chạy, và trong lúc nó chạy không task nào được bắt đầu. Cách đơn giản nhất: tách những task này ra, chạy tuần tự sau các task an toàn của cùng tầng. Cách làm này vẫn đúng với `depends_on`, vì chúng cùng tầng.
   - Worker chỉ biết sau khi `pick`, nên phải `pick` trước khi lập lịch. Làm việc này mà **không** thêm tên worker nào vào `runner.py`.
   - `results` trả về theo **thứ tự toposort**, không theo thứ tự hoàn thành. Mỗi task ghi file của riêng nó như cũ.
2. **`core/egress.py: record`**: thêm `threading.Lock` quanh đoạn ghi `egress.jsonl`. Mỗi dòng phải là JSON trọn vẹn, không bị xen giữa các luồng.
3. **`core/engine.py`**: truyền `layers` (lọc theo `--only`) và `max_parallel` lấy từ `meta` của policy. Với `--plan` thì mặc định là 1.
4. SIGTERM vẫn diệt được mọi worker đang chạy: `_ACTIVE` đã có khoá, chỉ cần kiểm lại.
5. Log `task.start`/`task.end` vẫn mang đúng `task_id` của luồng mình, nhờ `logging_setup.bind` dùng contextvars. Kiểm lại cho chắc.

## Test bắt buộc: `tests/test_runner_parallel.py`

Dùng worker giả ngủ vài trăm ms và ghi mốc thời gian start/end vào file. **Không** assert theo đồng hồ tường một cách mong manh; assert theo *chồng lấn* giữa các khoảng.

- `max_parallel=2` với 2 task an toàn ở cùng tầng → hai khoảng thời gian chồng lấn nhau.
- Có một task `parallel_safe: false` → khoảng của nó không chồng lấn với task nào.
- `depends_on` A → B: B bắt đầu sau khi A kết thúc. A `fail` → B `skipped` với lý do như cũ.
- Cùng một plan chạy song song và tuần tự → `results` giống nhau (bỏ qua `cost.wallclock_s`), cùng verdict, cùng `run_signature`.
- `egress.jsonl` có đúng N dòng JSON hợp lệ khi N task chạy song song.
- Cổng kiểm chung và các test runner/engine cũ vẫn xanh.

## Ngoài phạm vi

Đổi `max_parallel` mặc định trong `_default.yaml`. Nếu muốn bật (vd 4) thì đề xuất trong báo cáo, không tự đổi.
