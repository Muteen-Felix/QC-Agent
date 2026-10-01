"""Cổng HITL của Ground-Truth (S1-06): `gt validate` và `qc-agent validate` dùng CHUNG bộ kiểm này. Offline, tất định, không LLM.

Kết quả có ba mức:
  - lỗi (`errors`)      -> exit 1: chưa được merge. Còn TC draft, catalog draft, rejected thiếu lý do, trùng tc_id, TC draft trỏ AC không có,
                           module-map còn draft/`qc-agent:todo`, hoặc DRIFT (file trong tests_gt/ khác bản render lại từ catalog).
                           Cộng với coverage dưới ngưỡng khi catalog đã `approved` (coverage.py; chỉ khi repo có `openapi.snapshot.json` hoặc `coverage-policy.yaml`).
  - cảnh báo (`warnings`) -> exit 0: AC mồ côi, TC approved/rejected trỏ AC đã mất (PRD đổi), chưa có TC approved nào, thiếu module-map,
                           coverage dưới ngưỡng khi catalog còn `draft`.
  - `GTCheckError`      -> exit 3: không đọc được file hoặc sai schema (không có gì để phán tiếp).
Thông điệp chỉ có vị trí, ID (đã qua regex) và số đếm; không bao giờ trích lại nội dung catalog/PRD (chỉ ID mới lọt vào).
"""
from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from qc_agent.groundtruth import coverage as gt_coverage
from qc_agent.groundtruth import render as gt_render
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.scaffold import templates as t

MAX_BYTES = 8 * 1024 * 1024
LIST_MAX = 10
Finding = tuple[str, str]   # (nơi, thông điệp)


class GTCheckError(ValueError):
    """File Ground-Truth không đọc được hoặc sai schema. CLI trả exit 3."""


@dataclass
class CheckResult:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)


def _read_yaml(path: Path, what: str):
    try:
        if path.stat().st_size > MAX_BYTES:
            raise GTCheckError(f"{what} lớn hơn {MAX_BYTES} byte")
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GTCheckError(f"không có {what}") from None
    except (OSError, UnicodeError) as error:
        raise GTCheckError(f"không đọc được {what} ({type(error).__name__})") from None
    except yaml.YAMLError:
        raise GTCheckError(f"{what} không phải YAML hợp lệ") from None   # không kèm thông điệp của yaml: nó trích lại nội dung dòng lỗi


def load_catalog(sut_root: Path) -> dict:
    """Đọc catalog (mới chỉ đảm bảo là object có `stories`/`test_cases`/`uncovered_acs` dạng list). Sai schema thì `check`/`regen` báo tiếp."""
    data = _read_yaml(Path(sut_root) / gt_render.CATALOG_PATH, gt_render.CATALOG_PATH)
    if not isinstance(data, dict) or not all(isinstance(data.get(key), list) for key in ("stories", "test_cases", "uncovered_acs")):
        raise GTCheckError(f"{gt_render.CATALOG_PATH} sai cấu trúc (cần object có stories, test_cases, uncovered_acs)")
    return data


def _probe(data: dict) -> dict:
    """Bản dùng để kiểm schema: hai luật của schema mà cổng HITL báo RIÊNG (exit 1 thay vì 3) được nới ra."""
    probe = copy.deepcopy(data)
    if probe.get("status") == "approved":
        probe["status"] = "draft"   # "catalog approved => mọi TC đã duyệt": báo bằng lỗi draft
    for tc in probe["test_cases"]:
        if isinstance(tc, dict) and tc.get("status") == "rejected" and not (isinstance(tc.get("rejected_reason"), str) and tc["rejected_reason"].strip()):
            tc["rejected_reason"] = "-"   # "rejected thiếu rejected_reason": báo bằng lỗi riêng
    return probe


def _ids(ids, limit: int = LIST_MAX) -> str:
    ids = list(ids)
    return ", ".join(str(i) for i in ids[:limit]) + (f" … (+{len(ids) - limit})" if len(ids) > limit else "")


def _module_map(sut_root: Path, result: CheckResult) -> None:
    path = Path(sut_root) / gt_render.MODULE_MAP_PATH
    if not path.exists():
        result.warnings.append((gt_render.MODULE_MAP_PATH, "chưa có module-map.yaml (S2 dùng nó để gợi ý suite)"))
        return
    data = _read_yaml(path, gt_render.MODULE_MAP_PATH)
    problems = gt_schema.validate_module_map(data)
    if problems:
        raise GTCheckError(f"{gt_render.MODULE_MAP_PATH} sai schema: " + "; ".join(problems[:5]))
    if data["status"] == "draft":
        result.errors.append((gt_render.MODULE_MAP_PATH, "module-map còn status: draft (điền paths rồi đổi thành approved)"))
    todos = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if t.TODO in line]
    if todos:
        result.errors.append((gt_render.MODULE_MAP_PATH, f"còn dấu {t.TODO} ở dòng {_ids(todos)}"))


def _drift(sut_root: Path, probe: dict, result: CheckResult) -> None:
    try:
        expected = {f.path: f.content for f in gt_render.render(probe, sut_root=sut_root) if f.path.startswith(gt_render.TESTS_DIR + "/")}
    except gt_render.GTRenderError as error:
        raise GTCheckError(f"catalog không render được: {error}") from None
    directory = Path(sut_root) / gt_render.TESTS_DIR
    on_disk = {p.relative_to(sut_root).as_posix() for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts} if directory.is_dir() else set()
    for path in sorted(expected):
        try:
            actual = (Path(sut_root) / path).read_bytes().decode("utf-8").replace("\r\n", "\n")
        except FileNotFoundError:
            result.errors.append((path, "thiếu file sinh ra (chạy `qc-agent gt regen`)"))
            continue
        except (OSError, UnicodeError):
            raise GTCheckError(f"không đọc được {path}") from None
        if actual != expected[path]:
            result.errors.append((path, "khác bản render lại từ test-cases.yaml (drift: file sinh ra bị sửa tay). Sửa test-cases.yaml, không sửa file này"))
    for path in sorted(on_disk - set(expected)):
        result.errors.append((path, "file thừa trong tests_gt/: không phải đầu ra của render (drift)"))


def _coverage(sut_root: Path, data: dict, result: CheckResult) -> None:
    """Bộ chấm coverage (coverage.py) trên tập TC APPROVED + waiver đã approved. Chỉ chạy khi repo đã opt-in (có `openapi.snapshot.json` hoặc
    `coverage-policy.yaml`): repo sinh GT trước bộ chấm giữ hành vi cũ. Dưới ngưỡng là LỖI khi catalog đã `approved` (QA tuyên bố xong; reject TC làm thủng
    coverage thì không merge được); khi catalog còn `draft` chỉ là cảnh báo vì lúc đó còn TC draft chưa tính."""
    try:
        facts = gt_coverage.load_snapshot(sut_root)
        thresholds, has_policy = gt_coverage.load_policy(sut_root)
    except gt_coverage.CoverageError as error:
        raise GTCheckError(str(error)) from None
    if facts is None and not has_policy:
        return
    report = gt_coverage.score(data, facts, tc_statuses=("approved",), waiver_statuses=("approved",))
    sink = result.errors if data["status"] == "approved" else result.warnings
    for name, dim in report.shortfalls(thresholds).items():
        sink.append((gt_render.CATALOG_PATH, f"coverage {name} {dim.covered + dim.waived}/{dim.total} ({dim.ratio:.0%}) dưới ngưỡng {thresholds[name]:.0%} "
                                             f"(tính TC approved + waiver approved); còn thiếu: {_ids(dim.gaps)}"))


_XLSX_ADVICE = {
    "xlsx_ahead": "xlsx có thay đổi chưa vào test-cases.yaml: chạy `qc-agent gt import-xlsx` rồi commit cả hai file",
    "yaml_ahead": "test-cases.yaml đã đổi sau lần xuất xlsx (sửa tay YAML hoặc regen): Excel đã cũ, chạy `qc-agent gt export-xlsx` rồi commit",
    "diverged": "xlsx và test-cases.yaml khác nhau: chạy `qc-agent gt import-xlsx` (báo xung đột nếu cả hai bên cùng sửa một chỗ) rồi `qc-agent gt export-xlsx`",
}


def _xlsx_sync(sut_root: Path, data: dict, result: CheckResult) -> None:
    """Có `test-cases.xlsx` thì nó PHẢI khớp YAML (so theo ngữ nghĩa, offline). Không có xlsx thì bỏ qua (repo sinh GT trước khi có Excel). openpyxl chỉ được nạp khi có file."""
    path = Path(sut_root) / gt_render.XLSX_PATH
    if not path.is_file():
        return
    from qc_agent.groundtruth import xlsx as gt_xlsx   # import lười: gate và repo không dùng Excel không nạp openpyxl
    try:
        theirs, base, syntax = gt_xlsx.read_xlsx(path)
    except gt_xlsx.XlsxError as error:
        raise GTCheckError(f"{gt_render.XLSX_PATH}: {error}") from None
    for where, message in syntax[:LIST_MAX]:
        result.errors.append((gt_render.XLSX_PATH, f"{where}: {message}"))
    state = gt_xlsx.sync_status(data, theirs, base)
    if state == "yaml_ahead":
        # Chỉ YAML đi trước: gate đọc YAML nên không mất gì, và import sau này an toàn (gộp ba chiều không bao giờ hoàn nguyên sửa của YAML). Chỉ là Excel đã cũ.
        result.warnings.append((gt_render.XLSX_PATH, _XLSX_ADVICE[state]))
    elif state != "in_sync":
        result.errors.append((gt_render.XLSX_PATH, _XLSX_ADVICE[state]))   # quyết định của QA nằm trong xlsx mà chưa vào YAML: gate không thấy chúng


def check(sut_root: Path) -> CheckResult:
    """Chạy toàn bộ cổng HITL trên `<sut_root>/.qc-agent/ground-truth/`. Ném `GTCheckError` khi không phán được (exit 3)."""
    sut_root = Path(sut_root)
    where = gt_render.CATALOG_PATH
    data = load_catalog(sut_root)
    probe = _probe(data)
    problems = gt_schema.validate_catalog(probe)
    if problems:
        raise GTCheckError(f"{where} sai schema: " + "; ".join(problems[:5]))
    result = CheckResult()

    tcs = data["test_cases"]
    ac_ids = {ac["ac_id"] for story in data["stories"] for ac in story["acs"]}
    if data["status"] == "draft":
        result.errors.append((where, "catalog còn status: draft (QA duyệt xong thì đổi thành approved)"))
    drafts = [tc["tc_id"] for tc in tcs if tc["status"] == "draft"]
    if drafts:
        result.errors.append((where, f"{len(drafts)} test case còn draft, cần duyệt (approved/rejected): {_ids(drafts)}"))
    blank = [tc["tc_id"] for tc in tcs if tc["status"] == "rejected" and not (isinstance(tc.get("rejected_reason"), str) and tc["rejected_reason"].strip())]
    if blank:
        result.errors.append((where, f"test case rejected thiếu rejected_reason: {_ids(blank)}"))
    twice = sorted(tc_id for tc_id, n in Counter(tc["tc_id"] for tc in tcs).items() if n > 1)
    if twice:
        result.errors.append((where, f"tc_id bị trùng: {_ids(twice)}"))
    dangling_draft = sorted({tc["tc_id"] for tc in tcs if tc["status"] == "draft" and any(ref not in ac_ids for ref in tc["ac_refs"])})
    if dangling_draft:
        result.errors.append((where, f"test case draft trỏ tới AC không có trong catalog: {_ids(dangling_draft)}"))
    lost = sorted({tc["tc_id"] for tc in tcs if tc["status"] != "draft" and any(ref not in ac_ids for ref in tc["ac_refs"])})
    if lost:
        result.warnings.append((where, f"{len(lost)} test case approved/rejected trỏ tới AC không còn trong catalog (PRD đã đổi): {_ids(lost)}. "
                                       f"TC approved có AC đầu tiên đã mất làm gate báo error (exit 4) cho tới khi QA sửa ac_refs hoặc chuyển sang rejected"))
    covered = {ref for tc in tcs if tc["status"] != "rejected" for ref in tc["ac_refs"]}
    listed = {u["ac_id"] for u in data["uncovered_acs"]}
    orphans = [ac["ac_id"] for story in data["stories"] for ac in story["acs"] if ac["ac_id"] not in covered and ac["ac_id"] not in listed]
    if orphans:
        result.warnings.append((where, f"{len(orphans)} AC không có test case và không nằm trong uncovered_acs: {_ids(orphans)}"))
    if not any(tc["status"] == "approved" for tc in tcs):
        result.warnings.append((where, "chưa có test case approved nào: suite gt-functional sẽ fail (pytest.tests >= 1)"))

    _coverage(sut_root, data, result)
    _xlsx_sync(sut_root, data, result)
    _module_map(sut_root, result)
    _drift(sut_root, probe, result)
    return result
