# S1-03 · Worker `pytest` (plan S1.6)

- Branch: `feat/s1-03-worker-pytest`
- Tiền điều kiện: S1-00 đã merge. Chạy song song được với S1-01 và S1-02.

## Đọc trước

- `docs/prompts/_common.md`; `docs/core-rules.md`, mục "Thêm worker" (5 bước, mỗi bước là **thêm file**)
- `workers/_template.yaml` (đóng băng, **không sửa**) và `workers/semgrep.yaml` (mẫu manifest)
- `src/qc_agent/adapters/_base.py` (`Adapter`, `ParsedOutput`, `AdapterParseError`) và `src/qc_agent/adapters/_security.py` (`safe_relpath`, `finding_id`, `read_json`)
- `src/qc_agent/oracle/threshold.py`, `schemas/capabilities.json`
- `Dockerfile`: dòng `uv sync --frozen --no-dev` · `pyproject.toml`: `pytest` đang nằm trong nhóm `dev`

## Mục tiêu

Thêm worker chạy bộ test chức năng sinh từ ground-truth: `python -m pytest` → JUnit XML → metric/finding → oracle `threshold` phán. Chỉ override `build_cmd` và `parse_output`. **Không sửa `core/`.**

## Việc cần làm

1. **`schemas/capabilities.json`**: thêm `"api.functional": "Test chức năng API sinh từ ground-truth (pytest + JUnit)  (oracle: threshold)"`.
2. **`workers/pytest.yaml`**
   - `name: pytest`, `adapter: "qc_agent/adapters/pytest_adapter.py"`, `lanes: [gate]`
   - capability `api.functional` với `oracle_kinds: [threshold]`, `verdict_sources: [deterministic_assert]`, `parallel_safe: true`
   - `data_egress: []`
   - `version_probe` phải chạy được cả trong image (`/opt/venv`) lẫn trong `.venv` local. Kiểm bằng `registry.probe`.
3. **`src/qc_agent/adapters/pytest_adapter.py`**
   - **`build_cmd`**: `[sys.executable, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", "--junitxml", <workdir>/junit.xml, "-o", "junit_family=xunit2"]`.
     - `inputs.paths`: mỗi phần tử đi qua `sec.safe_relpath`; mặc định `[".qc-agent/ground-truth/tests_gt"]`.
     - `inputs.markers` (tuỳ chọn): đi vào **một** argv `-m <expr>`, validate bằng regex `^[A-Za-z0-9_ ()]+$`.
     - Không có input nào khác. Tuyệt đối không có "extra_args".
   - **`parse_output`**, ánh xạ theo exit code của pytest:
     - `0`, `1` → parse JUnit;
     - `5` (không collect được test nào) → metric bằng 0, để oracle `pytest.tests >= 1` biến thành `fail`, tức "gate rỗng không phải gate xanh";
     - `2`, `3`, `4` → `AdapterParseError`, thành `error`.
   - **Metric**: luôn đủ 5 key `pytest.tests/passed/failures/errors/skipped`, kể cả khi bằng 0.
   - **Finding**: mỗi testcase có `failure` hoặc `error` sinh một finding.
     - `finding_id` qua `sec.finding_id("pytest", classname::name, seen)`.
     - `title` = `<classname>::<name> — <message>`: bỏ ký tự điều khiển, gộp thành một dòng, cắt ≤ 200 ký tự. Message có thể chứa dữ liệu của SUT; không bao giờ nhét traceback vào.
     - `detected_by: "pytest:<tc_id nếu tìm thấy trong tên test, không thì tên test>"` (quy ước `<tool>:<rule_id>`, xem README mục "Cần bạn quyết" #3).
     - `severity_hint: "medium"`. Giá trị này hợp lệ ở cả contract 1.x lẫn 2.0.0, nên S3-01 không phải sửa file này. Mức cuối cùng do policy ở S3-02 quyết định.
   - **Evidence**: `raw_output` = `junit.xml`, `stdout` = log của pytest.
   - **XML**: dùng `xml.etree.ElementTree` với trần kích thước 10 MB (vượt → `AdapterParseError`). Không thêm lxml. Chấp nhận cả gốc `testsuites` lẫn `testsuite`.
4. **Đưa `pytest` vào image**: chuyển `pytest==9.1.1` từ nhóm `dev` sang `dependencies` (đúng phiên bản đang ghim), chạy `uv lock` (không được đổi phiên bản gói nào khác), rồi kiểm `git diff uv.lock`.
   - Có Docker thì build image và chạy `docker run --rm --entrypoint python <img> -m pytest --version`.
   - Không có Docker thì ghi rõ trong báo cáo là **chưa kiểm được trong image**.
5. **Tài liệu**: thêm dòng worker vào `docs/worker.md`, sửa danh sách worker trong `CLAUDE.md`, ghi trạng thái *Đang triển khai — S1*.

## Test bắt buộc: `tests/test_pytest_adapter.py`

- Parse các fixture XML: toàn pass, có fail, có error, có skipped, rỗng, hỏng, quá cỡ.
- Ánh xạ đủ các exit code 0/1/2/3/4/5.
- Đường dẫn `..`, tuyệt đối, hay bắt đầu bằng `-` đều bị từ chối. Marker có ký tự lạ bị từ chối.
- `finding_id` ổn định giữa hai lần chạy. `title` đã được làm sạch.
- **Tích hợp**: tạo trong `tmp_path` một thư mục test nhỏ (1 pass, 1 fail), gọi `PytestAdapter().run(spec)` với spec hợp lệ theo `schemas/task_spec.json` và oracle `threshold` (`pytest.failures == 0`, `pytest.errors == 0`, `pytest.tests >= 1`). Kết quả phải là `status=fail` và validate đúng `schemas/result.json`.
- Test shipped/registry hiện có vẫn xanh: manifest nạp được, capability có trong `capabilities.json`.

## Ngoài phạm vi

Suite `gt-functional` và runtime của test GT (S1-05), `location` của finding (S3-01), và bật suite cho noteboard (S1-08).
