"""pytest (capability api.functional): chạy bộ test chức năng sinh từ ground-truth, ĐẾM kết quả từ JUnit XML thành metric phẳng `pytest.<...>`.
Adapter không phán: ngưỡng (`pytest.failures == 0`, `pytest.tests >= 1`...) nằm trong file suite và do oracle `threshold` so.

Ánh xạ exit code của pytest (quan sát bằng pytest 9.1.1, xem tests/fixtures/pytest/):
  0, 1  -> parse JUnit. Mâu thuẫn giữa exit code và JUnit (exit 0 mà có failure, exit 1 mà không có failure/error, không có testcase nào)
           là AdapterParseError, không đoán bên nào đúng.
  5     -> không collect được test nào: mọi metric bằng 0 để oracle `pytest.tests >= 1` biến nó thành `fail`. "Gate rỗng" không bao giờ là gate xanh.
  2     -> bị ngắt, gồm cả LỖI COLLECT (import hỏng, cú pháp sai): pytest dừng cả phiên chứ không chạy phần còn lại => `error`, không phải `fail`.
  3, 4  -> lỗi nội bộ / lỗi cách dùng (thiếu APP_BASE_URL do conftest gọi pytest.exit(4), đường dẫn không tồn tại, -m sai) => `error`.
Mọi exit code khác (kể cả bị signal giết) cũng là `error`.

Đầu vào chỉ có `paths` và `markers`; KHÔNG có "extra_args" và mọi khoá lạ bị từ chối (suite nằm trong repo SUT nên là input không tin cậy):
mỗi phần tử là MỘT argv (không qua shell), `paths` qua `safe_relpath` (không tuyệt đối, không `..`, không bắt đầu bằng `-`), `markers` khớp regex chặt.
`title` của finding chỉ có `<classname>::<name> — <message>` đã làm sạch: traceback (nội dung của <failure>) không bao giờ được đưa vào vì nó chứa mã và dữ liệu của SUT.
Môi trường của tiến trình pytest: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 (plugin cài trong venv như deepeval/schemathesis không được tự nạp và đổi kết quả)
và PYTEST_ADDOPTS rỗng (không kế thừa cờ từ môi trường).

GIỚI HẠN ĐÃ BIẾT (đã kiểm bằng thực nghiệm, S1-03): pytest tự đọc `pyproject.toml`/`pytest.ini` và `conftest.py` của repo SUT, mà các file đó nằm NGOÀI
`.qc-agent/**` nên không bị CODEOWNERS khoá. Một PR thêm `addopts = "--deselect <test đang fail>"` hoặc một `conftest.py` gốc lọc bớt test thì gate ra
XANH GIẢ (metric vẫn `pytest.tests >= 1`). argv của adapter cố định theo thiết kế; cách chặn là đặt một `pytest.ini` NGAY TRONG thư mục test GT
(`.qc-agent/ground-truth/tests_gt/`, do QA khoá): pytest lấy nó làm rootdir và confcutdir nên không đọc cấu hình/conftest của SUT. Việc SINH file đó thuộc S1-05,
còn adapter ép nó FAIL-CLOSED: thư mục (hoặc thư mục chứa file/node id) trong `inputs.paths` mà thiếu `pytest.ini` thì `build_cmd` ném AdapterParseError => `error`,
không bao giờ chạy để rồi ra xanh giả. Test `test_integration_a_pytest_ini_next_to_the_tests_shields_them_from_sut_config` giữ cách bố trí này.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from qc_agent.adapters import _security as sec
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
DEFAULT_PATHS = [".qc-agent/ground-truth/tests_gt"]
JUNIT_NAME = "junit.xml"
STDOUT_NAME = "stdout.log"
MAX_XML_BYTES = 10 * 1024 * 1024
TITLE_MAX = 200
DETECTOR_NAME_MAX = 100
INPUT_KEYS = ("paths", "markers")
EXIT_NO_TESTS = 5
EXIT_MEANING = {2: "bị ngắt (thường là lỗi collect)", 3: "lỗi nội bộ của pytest", 4: "lỗi cách dùng (đường dẫn/cờ sai, hoặc conftest gọi pytest.exit)"}
_MARKERS = re.compile(r"[A-Za-z0-9_ ()]+")
_TC_ID = re.compile(r"TC-[A-Za-z0-9][A-Za-z0-9._-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _one_line(text: str, limit: int) -> str:
    """Bỏ ký tự điều khiển, gộp mọi khoảng trắng/xuống dòng thành một dấu cách, cắt còn `limit` ký tự (có dấu … nếu bị cắt)."""
    clean = " ".join(_CONTROL.sub(" ", text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _metrics(tests: int, passed: int, failures: int, errors: int, skipped: int) -> dict:
    """Luôn đủ 5 khoá, kể cả khi bằng 0: oracle không phán được nếu thiếu metric. `skipped` gồm cả xfail (pytest ghi xfail là <skipped>)."""
    return {"pytest.tests": tests, "pytest.passed": passed, "pytest.failures": failures, "pytest.errors": errors, "pytest.skipped": skipped}


def _read_junit(path: Path) -> ET.Element:
    if not path.is_file():
        raise AdapterParseError("pytest không ghi ra junit.xml")
    size = path.stat().st_size
    if size > MAX_XML_BYTES:
        raise AdapterParseError(f"junit.xml lớn {size} byte, vượt trần {MAX_XML_BYTES}")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise AdapterParseError("junit.xml có DOCTYPE/ENTITY: JUnit của pytest không bao giờ có, không parse")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, RecursionError):
        raise AdapterParseError("junit.xml không phải XML hợp lệ") from None   # không nhúng nội dung lỗi: có thể trích dữ liệu của file
    if root.tag not in ("testsuites", "testsuite"):
        raise AdapterParseError("junit.xml có gốc không phải <testsuites>/<testsuite>")
    return root


def _rule_id(name: str) -> str:
    found = _TC_ID.search(name or "")
    return found.group(0) if found else (_one_line(name, DETECTOR_NAME_MAX) or "unknown")


class PytestAdapter(Adapter):
    NAME = "pytest"
    ADAPTER_VERSION = "0.1.0"
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs") or {}
        unknown = sorted(str(key) for key in inputs if key not in INPUT_KEYS)
        if unknown:
            raise AdapterParseError(f"inputs không hỗ trợ: {unknown}; chỉ có {list(INPUT_KEYS)} (không có extra_args)")
        paths = sec.safe_relpaths(inputs.get("paths"), "inputs.paths", default=DEFAULT_PATHS) or list(DEFAULT_PATHS)
        for index, entry in enumerate(paths):
            self._require_isolating_ini(entry, f"inputs.paths[{index}]")
        junit = (workdir / JUNIT_NAME).resolve()
        junit.unlink(missing_ok=True)   # không đọc nhầm file của lần chạy trước
        cmd = [sys.executable, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", "--junitxml", str(junit), "-o", "junit_family=xunit2"]
        markers = inputs.get("markers")
        if markers is not None:
            if not isinstance(markers, str) or not markers.strip() or not _MARKERS.fullmatch(markers):
                raise AdapterParseError("inputs.markers chỉ được gồm chữ, số, '_', khoảng trắng và ngoặc đơn")
            cmd += ["-m", markers]   # MỘT argv, không qua shell
        return cmd

    @staticmethod
    def _require_isolating_ini(entry: str, what: str) -> None:
        """Fail-closed: thư mục test phải có `pytest.ini` riêng (xem "GIỚI HẠN ĐÃ BIẾT"), nếu không cấu hình/conftest của repo SUT có thể lọc bớt test
        và làm gate xanh giả. Entry có thể là thư mục, file hoặc node id (`file.py::test`). Đường dẫn chưa tồn tại thì bỏ qua: pytest tự trả exit 4 => `error`."""
        target = Path(entry.split("::", 1)[0])
        directory = target if target.is_dir() else target.parent if target.is_file() else None
        if directory is not None and not (directory / "pytest.ini").is_file():
            raise AdapterParseError(f"{what}: thiếu {directory.as_posix()}/pytest.ini — không có nó, pytest đọc cấu hình/conftest của repo SUT và gate có thể xanh giả")

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        log = proc.stdout or ""
        if proc.stderr:
            log += ("" if not log or log.endswith("\n") else "\n") + "--- stderr ---\n" + proc.stderr
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(log, encoding="utf-8")
        junit = workdir / JUNIT_NAME
        code = proc.returncode
        notes = [f"PARSER_VERSION={PARSER_VERSION}", f"pytest exit_code={code}"]
        replay = shlex.join(str(a) for a in (proc.args if isinstance(proc.args, (list, tuple)) else [proc.args]))

        if code == EXIT_NO_TESTS:
            notes.append("pytest exit 5: không collect được test nào; metric = 0 để oracle quyết (pytest.tests >= 1)")
            evidence = [("raw_output", junit)] if junit.is_file() else []
            return ParsedOutput(metrics=_metrics(0, 0, 0, 0, 0), evidence_paths=[*evidence, ("stdout", stdout_path)],
                                tokens=0, usd=0.0, exit_code=code, replay_cmd=replay, adapter_notes=notes)
        if code not in (0, 1):
            raise AdapterParseError(f"pytest kết thúc với exit code {code} ({EXIT_MEANING.get(code, 'không rõ')}): không phải kết quả test")

        root = _read_junit(junit)
        cases = []
        for element in root.iter("testcase"):
            cases.append((element.get("classname") or "", element.get("name") or "", element.find("failure"), element.find("error"), element.find("skipped")))
        cases.sort(key=lambda row: (row[0], row[1]))   # sort ổn định: thứ tự tất định => hậu tố chống trùng của finding_id ổn định

        failures = sum(1 for row in cases if row[2] is not None)
        errors = sum(1 for row in cases if row[3] is not None)
        bad = sum(1 for row in cases if row[2] is not None or row[3] is not None)
        skipped = sum(1 for row in cases if row[4] is not None and row[2] is None and row[3] is None)
        if not cases:
            raise AdapterParseError(f"pytest exit {code} nhưng junit.xml không có testcase nào")
        if code == 0 and bad:
            raise AdapterParseError("pytest exit 0 nhưng junit.xml có failure/error: mâu thuẫn, không đoán bên nào đúng")
        if code == 1 and not bad:
            raise AdapterParseError("pytest exit 1 nhưng junit.xml không có failure/error nào: mâu thuẫn, không đoán bên nào đúng")

        findings, seen = [], {}
        for classname, name, failure, error, _skipped in cases:
            bad_child = failure if failure is not None else error
            if bad_child is None:
                continue
            where = f"{classname}::{name}" if classname else name
            message = _one_line(bad_child.get("message") or "", TITLE_MAX)   # CHỈ thuộc tính message; text của phần tử là traceback nên bị bỏ
            title = _one_line(f"{where} — {message}" if message else where, TITLE_MAX)
            fid = sec.finding_id("pytest", where, seen)
            findings.append(sec.finding(fid, title, f"pytest:{_rule_id(name)}", "medium"))

        metrics = _metrics(len(cases), len(cases) - bad - skipped, failures, errors, skipped)
        return ParsedOutput(
            metrics=metrics, findings=sec.cap_findings(findings, notes), evidence_paths=[("raw_output", junit), ("stdout", stdout_path)],
            tokens=0, usd=0.0, exit_code=code, replay_cmd=replay, adapter_notes=notes)


if __name__ == "__main__":
    raise SystemExit(PytestAdapter().main())
