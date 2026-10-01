"""Render TẤT ĐỊNH catalog Ground-Truth -> file trong repo SUT (S1-05). Hàm thuần: cùng catalog vào cho ra đúng cùng từng byte (LF, UTF-8 không BOM,
không thời gian). Không LLM, không mạng, không `eval`.

Test được ĐIỀU KHIỂN BẰNG DỮ LIỆU: file `.py` là mẫu cố định (scaffold/tmpl/gt-*.tmpl) và chỉ có `STORY_ID`; test case nằm trong `test-cases.yaml`.
Nhờ vậy QA sửa YAML (draft -> approved, thêm TC `origin: qa`) mà không phải render lại, chuỗi do LLM viết không bao giờ thành mã Python
(chỉ `story_id`, đã qua regex ID của schema, đi vào mã), và `gt validate` (S1-06) phát hiện file `.py` bị sửa tay bằng cách render lại rồi so.

Trạng thái: catalog được ghi đúng như nhận (`generate` luôn cho `draft`; render không đổi status của TC nào, để `gt regen` giữ được thứ QA đã duyệt).
`module-map.yaml` luôn `draft`. File `.py` không phụ thuộc status nên đổi status không làm `gt validate` báo lệch.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from qc_agent.groundtruth import schema as gt_schema
from qc_agent.scaffold import init as scaffold_init
from qc_agent.scaffold import templates as t
from qc_agent.scaffold.openapi import Analysis

GT_DIR = ".qc-agent/ground-truth"
TESTS_DIR = f"{GT_DIR}/tests_gt"
CATALOG_PATH = f"{GT_DIR}/test-cases.yaml"
MODULE_MAP_PATH = f"{GT_DIR}/module-map.yaml"
XLSX_PATH = f"{GT_DIR}/test-cases.xlsx"   # bản Excel cho QA (xlsx.py); không do `render` sinh vì là file nhị phân và cần openapi.snapshot cho sheet Coverage
SUITES_DIR = ".qc-agent/suites"
API_CONTRACT_PATH = f"{SUITES_DIR}/api-contract.yaml"
GT_SUITE_PATH = f"{SUITES_DIR}/gt-functional.yaml"
MODULE_SUITES = ("api-contract", "gt-functional")
# Thứ thực sự cô lập gate khỏi cấu hình của repo SUT (xem docstring adapters/pytest_adapter.py): nằm trong thư mục QA khoá nên pytest lấy nó làm rootdir.
PYTEST_INI = ("# qc-agent:generated gt — cô lập test Ground-Truth khỏi pytest.ini/pyproject.toml/conftest.py của repo SUT (không thì một PR có thể\n"
              "# `--deselect` test đang fail và làm gate xanh giả). Worker pytest từ chối chạy thư mục này nếu thiếu file. KHÔNG sửa tay.\n"
              "[pytest]\naddopts =\n")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}")
_VERSION = re.compile(r"[a-z0-9-]+/[0-9]+")
_SHA = re.compile(r"[0-9a-f]{64}")

_ORDER = {
    "catalog": ("version", "prd", "generated_by", "status", "stories", "test_cases", "uncovered_acs", "coverage_plan", "waivers", "spec_conflicts"),
    "prd": ("id", "sha256", "source"), "generated_by": ("model", "prompt_version"),
    "story": ("story_id", "title", "acs"), "ac": ("ac_id", "text"), "uncovered": ("ac_id", "reason"),
    "tc": ("tc_id", "title", "ac_refs", "kind", "priority", "technique", "status", "origin", "rejected_reason", "notes", "preconditions", "rationale", "evidence", "steps"),
    "evidence": ("path", "line"), "plan": ("ac_id", "technique", "scenario", "decision", "reason", "tc_ids"),
    "waiver": ("kind", "target", "reason_code", "reason", "status"), "conflict": ("ac_id", "summary", "evidence", "status"),
    "step": ("request", "expect", "capture"), "request": ("method", "path", "path_params", "query", "headers", "json"),
    "expect": ("status", "json"), "assertion": ("path", "op", "value"),
}
_SORTED_MAPS = ("path_params", "query", "headers", "capture")   # map không có thứ tự ngữ nghĩa: xếp theo khoá cho ổn định


class GTRenderError(ValueError):
    """Catalog không render được (sai schema, hai story trùng slug, giá trị không an toàn để nhúng vào comment). Thông điệp không trích nội dung catalog."""


@dataclass(frozen=True)
class RenderedFile:
    path: str      # POSIX, tương đối gốc repo SUT
    content: str


def story_slug(story_id: str) -> str:
    """`[a-z0-9_]` sinh từ story_id, KHÔNG từ title. Phải giống hệt `story_slug` trong gt-conftest.py.tmpl."""
    return re.sub(r"[^a-z0-9]+", "_", story_id.lower()).strip("_") or "story"


def _ordered(node, kind: str):
    """Sắp khoá theo thứ tự cố định. Khoá lạ (không có trong schema) đi cuối, xếp theo tên."""
    order = _ORDER[kind]
    return {key: node[key] for key in (*order, *sorted(k for k in node if k not in order)) if key in node}


def _deep_sorted(node):
    if isinstance(node, dict):
        return {key: _deep_sorted(node[key]) for key in sorted(node)}
    return [_deep_sorted(item) for item in node] if isinstance(node, list) else node


def _canonical(catalog: dict) -> dict:
    def assertion(item):
        out = _ordered(item, "assertion")
        if "value" in out:
            out["value"] = _deep_sorted(out["value"])
        return out

    def step(item):
        out = _ordered(item, "step")
        request = _ordered(item["request"], "request")
        for name in _SORTED_MAPS:
            if isinstance(request.get(name), dict):
                request[name] = dict(sorted(request[name].items()))
        if "json" in request:
            request["json"] = _deep_sorted(request["json"])   # thứ tự khoá của body không có ý nghĩa: xếp để đầu ra không phụ thuộc thứ tự nhập
        out["request"] = request
        expect = _ordered(item["expect"], "expect")
        if "json" in expect:
            expect["json"] = [assertion(a) for a in expect["json"]]
        out["expect"] = expect
        if isinstance(out.get("capture"), dict):
            out["capture"] = dict(sorted(out["capture"].items()))
        return out

    def case(item):
        out = _ordered(item, "tc")
        if "evidence" in out:
            out["evidence"] = [_ordered(e, "evidence") for e in item["evidence"]]
        out["steps"] = [step(s) for s in item["steps"]]
        return out

    out = _ordered(catalog, "catalog")
    out["prd"] = _ordered(catalog["prd"], "prd")
    out["generated_by"] = _ordered(catalog["generated_by"], "generated_by")
    out["stories"] = [{**_ordered(s, "story"), "acs": [_ordered(a, "ac") for a in s["acs"]]} for s in catalog["stories"]]
    out["test_cases"] = [case(tc) for tc in catalog["test_cases"]]
    out["uncovered_acs"] = [_ordered(u, "uncovered") for u in catalog["uncovered_acs"]]
    # Ba khoá dưới đây chỉ có khi agent/QA điền; catalog cũ không có thì KHÔNG được sinh thêm khoá (render phải giữ đúng từng byte).
    if "coverage_plan" in out:
        out["coverage_plan"] = [_ordered(p, "plan") for p in catalog["coverage_plan"]]
    if "waivers" in out:
        out["waivers"] = [_ordered(w, "waiver") for w in catalog["waivers"]]
    if "spec_conflicts" in out:
        out["spec_conflicts"] = [{**_ordered(c, "conflict"), **({"evidence": [_ordered(e, "evidence") for e in c["evidence"]]} if "evidence" in c else {})}
                                 for c in catalog["spec_conflicts"]]
    return out


def _catalog_file(catalog: dict) -> str:
    sha, model, version = catalog["prd"]["sha256"], catalog["generated_by"]["model"], catalog["generated_by"]["prompt_version"]
    if not (_SHA.fullmatch(sha) and _MODEL.fullmatch(model) and _VERSION.fullmatch(version)):
        raise GTRenderError("prd.sha256, generated_by.model hoặc generated_by.prompt_version không hợp lệ để ghi vào header")
    header = [
        f"# qc-agent:generated gt — test case Ground-Truth. prd.sha256={sha} prompt={version} model={model}",
        "# Đây là thứ QA duyệt. Trạng thái từng test case (status):",
        "#   draft     LLM đề xuất, CHƯA chạy trong gate.",
        "#   approved  đã duyệt: gate chạy và chặn merge nếu fail.",
        "#   rejected  loại bỏ; BẮT BUỘC điền rejected_reason. Regen không sinh lại TC có cùng tc_id.",
        "# Thêm test case của QA: origin: qa, status: approved, tc_id dạng TC-<ac_id>-<mô-tả-ngắn>, ac_refs trỏ tới AC có trong `stories`.",
        "# Kiểu (kind): api_contract = 1 bước chỉ method+path; api_functional = 1 bước; flow = từ 2 bước, bước sau dùng giá trị `capture` của bước trước.",
        "# Xong việc: đổi `status` ngoài cùng thành approved (khi đó mọi TC phải là approved hoặc rejected).",
        "# Sửa file này KHÔNG cần render lại. Thư mục tests_gt/ là mã sinh máy: đừng sửa tay (`qc-agent gt validate` sẽ báo lệch).",
    ]
    body = yaml.safe_dump(_canonical(catalog), sort_keys=False, allow_unicode=True)
    return "\n".join(header) + "\n" + body


def _modules(catalog: dict, openapi: Analysis | None) -> list[str]:
    paths = {step["request"]["path"] for tc in catalog["test_cases"] for step in tc["steps"]}
    if openapi is not None:
        paths.update(openapi.get_paths)
    names = set()
    for path in paths:
        first = path.split("/")[1] if path.count("/") >= 1 else ""
        static = "" if first.startswith("{") else re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")[:40].strip("-")
        names.add(static or "root")
    return sorted(names)


def _module_map(catalog: dict, openapi: Analysis | None) -> str:
    lines = [
        "# qc-agent:generated gt — module-map NHÁP (status: draft): ánh xạ đường dẫn mã nguồn -> module -> suite. Selector (S2) chỉ dùng để GỢI Ý thêm suite.",
        f"# {t.todo_mark('VERIFY', 'điền `paths` của từng module (glob tương đối gốc repo SUT, tới file khai báo route); scanner chưa biết file route của repo này, rồi đổi status thành approved')}",
        "version: 1",
        "status: draft",
    ]
    names = _modules(catalog, openapi)
    if not names:
        lines.append("modules: []")
    else:
        lines.append("modules:")
        for name in names:
            lines += [f"- name: {json.dumps(name)}", "  paths:", f"  - {json.dumps('TODO-route-files-of-' + name)}", "  suites:",
                      *(f"  - {suite}" for suite in MODULE_SUITES), "  source: openapi"]
    text = "\n".join(lines) + "\n"
    problems = gt_schema.validate_module_map(yaml.safe_load(text))
    if problems:   # lỗi lập trình, không phải lỗi dữ liệu
        raise GTRenderError("module-map sinh ra vi phạm schema: " + "; ".join(problems[:3]))
    return text


def render(catalog: dict, *, sut_root: Path, openapi: Analysis | None = None, force: bool = False) -> list[RenderedFile]:
    """Toàn bộ tài sản GT, xếp theo đường dẫn. `sut_root` chỉ để xem `api-contract.yaml` đã có chưa (có rồi thì KHÔNG sinh lại, trừ khi `force`).
    `openapi` (kết quả `scaffold.openapi.analyze`) cấp endpoint cho suite api-contract và module-map; thiếu thì api-contract có vùng REFINE."""
    problems = gt_schema.validate_catalog(catalog)
    if problems:
        raise GTRenderError("catalog không hợp lệ theo schema: " + "; ".join(problems[:5]))
    slugs: dict[str, str] = {}
    for story in catalog["stories"]:
        slug = story_slug(story["story_id"])
        if slug in slugs:
            raise GTRenderError(f"hai story ({slugs[slug]}, {story['story_id']}) cùng slug file {slug!r}")
        slugs[slug] = story["story_id"]

    try:
        story_tests = [RenderedFile(f"{TESTS_DIR}/test_{slug}.py", t.gt_story_test(story_id)) for slug, story_id in slugs.items()]
    except t.TemplateError:   # regex của schema cho phép xuống dòng ở cuối (`$`); mẫu dùng fullmatch nên chặt hơn
        raise GTRenderError("story_id không an toàn để đưa vào mã Python") from None
    files = [RenderedFile(CATALOG_PATH, _catalog_file(catalog)),
             RenderedFile(f"{TESTS_DIR}/pytest.ini", PYTEST_INI),
             RenderedFile(f"{TESTS_DIR}/conftest.py", t.gt_conftest()),
             RenderedFile(GT_SUITE_PATH, t.gt_functional_suite()),
             RenderedFile(MODULE_MAP_PATH, _module_map(catalog, openapi))]
    files += story_tests
    if force or not (Path(sut_root) / API_CONTRACT_PATH).exists():
        suite = t.api_contract_suite(exclude=tuple(openapi.exclude)) if openapi is not None else t.api_contract_suite(refine=True)
        files.append(RenderedFile(API_CONTRACT_PATH, suite))
    return sorted(files, key=lambda f: f.path)


def write(files: list[RenderedFile], sut_root: Path, *, force: bool = False) -> list[scaffold_init.Outcome]:
    """Ghi bằng đúng cơ chế của `init` (nguyên tử, không đè file có sẵn nếu không `force`, giữ chủ sở hữu thư mục khi chạy bằng root trong container)."""
    root = Path(sut_root).resolve()
    planned = []
    for file in files:
        target = (root / file.path).resolve()
        if root not in target.parents:
            raise GTRenderError(f"đường dẫn ra ngoài repo SUT: {file.path}")
        planned.append(scaffold_init.Planned(target, file.path, file.content))
    return scaffold_init.apply(scaffold_init.Plan(root=root, files=planned), force=force)
