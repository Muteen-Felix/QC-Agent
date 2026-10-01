"""Excel (`test-cases.xlsx`) cho QA duyệt Ground-Truth. YAML (`test-cases.yaml`) vẫn là NGUỒN SỰ THẬT của gate; xlsx là bản để đọc/sửa, hai chiều:

    gt generate|regen|export-xlsx : catalog -> xlsx        gt import-xlsx : xlsx -> catalog (YAML)        gt validate : xlsx và YAML phải khớp nhau

Vì xlsx là file nhị phân (PR không hiện diff) nên mọi đảm bảo nằm ở code:
  - So sánh theo NGỮ NGHĨA (`projection`), không theo byte: zip có dấu thời gian nên byte không ổn định. `write_if_changed` không ghi lại khi nội dung tương đương
    nên commit của bot không đổi vô cớ.
  - Gộp BA CHIỀU theo từng trường: `base` (ảnh chụp trong sheet ẩn `_meta` lúc xuất) / `theirs` (xlsx QA sửa) / `ours` (YAML hiện tại). Hai bên cùng sửa khác nhau
    thì là xung đột (exit 1), không bao giờ im lặng ghi đè. Thiếu `_meta` (vd Google Sheets làm rơi sheet ẩn) thì gộp hai chiều: xlsx thắng, kèm cảnh báo.
  - QA sửa được: `status`, `rejected_reason`, `notes`, `priority` của mọi TC; NỘI DUNG (title, ac_refs, kind, technique, preconditions, rationale, các bước) của TC;
    thêm TC mới; waiver, uncovered_acs, trạng thái spec_conflicts, trạng thái catalog. Sửa nội dung một TC của LLM thì TC chuyển thành `origin: qa` (giữ `tc_id`) để
    `regen` không ghi đè. Cột chỉ đọc (origin, evidence) bị sửa là lỗi. Xoá dòng TC của LLM là lỗi (hãy `rejected`); xoá TC `origin: qa` thì được.
  - Chống tiêm công thức: mọi chuỗi ghi kiểu text (`=HYPERLINK(...)` không bao giờ là công thức) và ô công thức khi ĐỌC là lỗi. Ép kiểu của Excel (`007`, `1.10`) tránh
    được vì ô có định dạng `@` (text). File vào bị chặn theo kích thước, tỉ lệ nén, số dòng; XML đọc bằng defusedxml.
  - Thông điệp lỗi chỉ có vị trí ô và tên cột/trường; không bao giờ trích lại nội dung ô.
`openpyxl` và `defusedxml` chỉ được import ở đây (và chỉ khi có file xlsx / khi xuất), nên gate và `core/` không nạp chúng.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import defusedxml  # noqa: F401  — phải import TRƯỚC openpyxl để openpyxl dùng bộ phân tích XML an toàn
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from qc_agent.groundtruth import coverage as gt_coverage
from qc_agent.groundtruth import render as gt_render
from qc_agent.groundtruth import schema as gt_schema

XLSX_PATH = gt_render.XLSX_PATH
FORMAT_VERSION = 1
MAX_BYTES = 5_000_000
MAX_UNCOMPRESSED = 50_000_000
MAX_RATIO = 200
MAX_ROWS = 5000
CELL_LIMIT = 32000               # Excel giới hạn 32767 ký tự/ô
CHUNK = 30000
Finding = tuple[str, str]        # (nơi, thông điệp)

REVIEW_FIELDS = ("status", "rejected_reason", "notes", "priority")
CONTENT_FIELDS = ("title", "ac_refs", "kind", "technique", "preconditions", "rationale", "steps")
READONLY_FIELDS = ("origin", "evidence")
TC_FIELDS = (*REVIEW_FIELDS, *CONTENT_FIELDS, *READONLY_FIELDS)
OPTIONAL_TEXT = ("rejected_reason", "notes", "priority", "technique", "preconditions", "rationale")

TC_COLUMNS = ("tc_id", "story_id", "ac_refs", "title", "kind", "priority", "technique", "status", "rejected_reason", "notes", "origin", "preconditions", "rationale", "evidence", "steps", "summary")
TC_EDITABLE = ("ac_refs", "title", "kind", "priority", "technique", "status", "rejected_reason", "notes", "preconditions", "rationale")
STEP_COLUMNS = ("tc_id", "step_no", "method", "path", "path_params", "query", "headers", "body_json", "expect_status", "capture")
ASSERT_COLUMNS = ("tc_id", "step_no", "n", "path", "op", "value", "value_type")
UNCOVERED_COLUMNS = ("ac_id", "reason")
WAIVER_COLUMNS = ("kind", "target", "reason_code", "reason", "status")
CONFLICT_COLUMNS = ("ac_id", "summary", "evidence", "status")
VALIDATIONS = {  # (sheet, cột) -> danh sách chọn
    ("TestCases", "kind"): "api_contract,api_functional,flow", ("TestCases", "priority"): "high,medium,low",
    ("TestCases", "technique"): "happy_path,boundary,equivalence,negative_validation,error_handling,state_transition,authz,idempotency,data_integrity",
    ("TestCases", "status"): "draft,approved,rejected", ("Steps", "method"): "GET,POST,PUT,PATCH,DELETE",
    ("Assertions", "op"): "eq,ne,contains,exists,absent,type,len_eq,len_gte", ("Assertions", "value_type"): "string,number,boolean,null",
    ("Waivers", "kind"): "technique,api", ("Waivers", "reason_code"): "not_http_reachable,needs_infra_fault,not_applicable,out_of_scope",
    ("Waivers", "status"): "draft,approved,rejected", ("SpecConflicts", "status"): "open,resolved",
}
_NEW = re.compile(r"NEW[-_A-Za-z0-9]*")
_TC_ID = re.compile(r"TC-[A-Za-z0-9][A-Za-z0-9._-]{0,76}")
_AC_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_HEAD_FILL = {"edit": "FFF2CC", "read": "D9D9D9"}


class XlsxError(ValueError):
    """Không đọc/ghi được xlsx (hỏng, quá lớn, nén bất thường, thiếu sheet/cột, nội dung không vừa ô). CLI trả exit 3. Thông điệp không trích nội dung ô."""


@dataclass
class ImportResult:
    catalog: dict
    changed: list[str] = field(default_factory=list)      # tc_id có thay đổi
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    converted: list[str] = field(default_factory=list)    # TC của LLM bị sửa nội dung => origin: qa
    conflicts: list[str] = field(default_factory=list)    # "tc_id.field": hai bên cùng sửa khác nhau
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.errors


# ---------------- projection (so sánh theo ngữ nghĩa) ----------------

def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _norm_text(value) -> str | None:
    text = " ".join(value.split()) if isinstance(value, str) else None
    return text or None


def _evidence_strings(items) -> list[str]:
    return [f"{e['path']}:{e['line']}" if e.get("line") else e["path"] for e in items or []]


def _tc_projection(tc: dict) -> dict:
    return {"status": tc["status"], "rejected_reason": _norm_text(tc.get("rejected_reason")), "notes": _norm_text(tc.get("notes")), "priority": tc.get("priority"),
            "title": " ".join(tc["title"].split()), "ac_refs": list(tc["ac_refs"]), "kind": tc["kind"], "technique": tc.get("technique"),
            "preconditions": _norm_text(tc.get("preconditions")), "rationale": _norm_text(tc.get("rationale")), "steps": copy.deepcopy(tc["steps"]),
            "origin": tc["origin"], "evidence": _evidence_strings(tc.get("evidence"))}


def projection(catalog: dict) -> dict:
    """Phần của catalog mà xlsx thể hiện (hoặc sửa được). `a == b` là phép so sánh ngữ nghĩa dùng cho sync/merge."""
    return {
        "prd": {"id": catalog["prd"]["id"], "sha256": catalog["prd"]["sha256"]},
        "status": catalog["status"],
        "test_cases": {tc["tc_id"]: _tc_projection(tc) for tc in catalog["test_cases"]},
        "uncovered": {u["ac_id"]: u["reason"] for u in catalog["uncovered_acs"]},
        "waivers": {f"{w['kind']}\t{w['target']}": {k: w[k] for k in WAIVER_COLUMNS} for w in catalog.get("waivers", [])},
        "conflicts": {f"{c['ac_id']}\t{c['summary']}": {"status": c["status"], "evidence": _evidence_strings(c.get("evidence"))} for c in catalog.get("spec_conflicts", [])},
    }


def digest(proj: dict) -> str:
    return hashlib.sha256(_canon(proj).encode("utf-8")).hexdigest()


# ---------------- ghi ----------------

def _put(ws, row: int, col: int, value) -> None:
    if value is None or value == "":
        return
    text = str(value)
    if len(text) > CELL_LIMIT:
        raise XlsxError(f"nội dung một ô ({ws.title} dòng {row}) dài hơn {CELL_LIMIT} ký tự: không xuất được ra Excel")
    cell = ws.cell(row=row, column=col)
    cell.value = text
    cell.data_type = "s"          # `=...`, `+...`, `@...` KHÔNG bao giờ thành công thức
    cell.number_format = "@"      # Excel không đổi `007` thành 7 khi QA sửa ô


def _header(ws, columns, editable=()) -> None:
    for index, name in enumerate(columns, 1):
        cell = ws.cell(row=1, column=index, value=name)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=_HEAD_FILL["edit" if name in editable else "read"])
        ws.column_dimensions[get_column_letter(index)].width = 18 if name not in ("title", "summary", "reason", "notes", "body_json", "path") else 44
    ws.freeze_panes = "A2"


def _validate(ws, columns) -> None:
    for index, name in enumerate(columns, 1):
        options = VALIDATIONS.get((ws.title, name))
        if options:
            check = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True)
            check.add(f"{get_column_letter(index)}2:{get_column_letter(index)}{MAX_ROWS}")
            ws.add_data_validation(check)


def _json_cell(value) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None


def _scalar_cell(value) -> tuple[str | None, str | None]:
    """(text, value_type) của giá trị assertion."""
    if value is None:
        return "null", "null"
    if isinstance(value, bool):
        return ("true" if value else "false"), "boolean"
    if isinstance(value, (int, float)):
        return json.dumps(value), "number"
    return str(value), "string"


def _summary(tc: dict) -> str:
    return " | ".join(f"{s['request']['method']} {s['request']['path']} -> {','.join(str(c) for c in s['expect']['status'])}" for s in tc["steps"])


def _instructions(ws) -> None:
    lines = [
        "Test case Ground-Truth: file để QA duyệt. test-cases.yaml vẫn là nguồn sự thật của gate; file này và YAML phải KHỚP NHAU (qc-agent gt validate kiểm).",
        "",
        "QUY TRÌNH: sửa trong Excel -> chạy `qc-agent gt import-xlsx` (ghi ngược vào YAML) -> commit CẢ HAI file -> `qc-agent gt validate` phải xanh.",
        "Lỡ sửa YAML trực tiếp? Chạy `qc-agent gt export-xlsx` để xuất lại xlsx từ YAML (từ chối nếu xlsx đang có sửa chưa import; thêm --force để bỏ).",
        "",
        "Ô tiêu đề VÀNG = QA được sửa; XÁM = chỉ đọc (sửa là lỗi khi import). Cột được nhận theo TÊN tiêu đề nên có thể đổi thứ tự cột, đừng đổi tên.",
        "",
        "DUYỆT: sheet TestCases, cột status: draft -> approved, hoặc rejected (BẮT BUỘC điền rejected_reason). Sheet Catalog: status = approved khi đã xong (lúc đó không còn draft).",
        "SỬA NỘI DUNG một TC do LLM sinh (title, ac_refs, kind, technique, các bước ở Steps/Assertions): được, nhưng TC đó chuyển thành origin: qa (giữ tc_id) để regen không ghi đè.",
        "THÊM TC MỚI: sheet TestCases thêm dòng với tc_id = NEW-1 (NEW-<chữ gì cũng được, không trùng), điền ac_refs/title/kind/status; rồi thêm các bước vào Steps và assertion vào Assertions",
        "  với CÙNG tc_id NEW-1. Import cấp tc_id thật (TC-<ac>-qa-<mã>) và xuất lại file. kind: api_functional 1 bước, flow từ 2 bước, api_contract 1 bước chỉ method+path.",
        "XOÁ: dòng TC của LLM không được xoá (hãy rejected + lý do); dòng TC origin: qa thì xoá được.",
        "",
        "Steps: path_params / query / headers / capture là JSON object (vd {\"note_id\": \"{{note_id}}\"}); body_json là JSON của body (để trống = không gửi body); expect_status: \"200\" hoặc \"200, 201\".",
        "  Biến {{tên}} chỉ dùng trong path_params, query, body_json, và phải do bước TRƯỚC capture (capture: {\"note_id\": \"$.id\"}).",
        "Assertions: một dòng một assertion theo (tc_id, step_no); op: eq, ne, contains, exists, absent, type, len_eq, len_gte; value_type: string | number | boolean | null.",
        "  exists/absent không có value; type nhận value là string|number|integer|boolean|null|object|array; len_eq/len_gte nhận số nguyên.",
        "Waivers: miễn một ô coverage; target chép ĐÚNG id gap trong sheet Coverage. Chỉ waiver status = approved mới được tính. Uncovered: AC không kiểm được bằng HTTP + lý do.",
        "SpecConflicts: nơi mã nguồn khác PRD/OpenAPI; chỉ sửa status (open -> resolved) sau khi đã quyết PRD sai hay SUT sai. Coverage / AgentPlan: chỉ đọc.",
        "",
        "KHÔNG gõ công thức (ô bắt đầu bằng =): import từ chối. KHÔNG xoá sheet _meta (ẩn): nó cho phép phát hiện xung đột khi cả YAML và Excel cùng bị sửa. Excel/LibreOffice được; Google Sheets có thể làm mất sheet ẩn và danh sách chọn.",
    ]
    for index, line in enumerate(lines, 1):
        cell = ws.cell(row=index, column=1)
        cell.value, cell.data_type = line, "s"
    ws.column_dimensions["A"].width = 160


def render_xlsx(catalog: dict, facts: dict | None = None) -> bytes:
    """catalog -> bytes xlsx. `facts` (coverage.snapshot) cho sheet Coverage; thiếu thì chỉ có phần AC. Ném `XlsxError` nếu một ô quá lớn cho Excel."""
    problems = gt_schema.validate_catalog(catalog)
    if problems:
        raise XlsxError("catalog không hợp lệ theo schema: " + "; ".join(problems[:3]))
    wb = Workbook()
    wb.properties.creator = "qc-agent"
    wb.properties.lastModifiedBy = None
    wb.properties.created = wb.properties.modified = datetime(2000, 1, 1)
    ws = wb.active
    ws.title = "HuongDan"
    _instructions(ws)
    story_of = {ac["ac_id"]: story["story_id"] for story in catalog["stories"] for ac in story["acs"]}

    ws = wb.create_sheet("Catalog")
    _header(ws, ("key", "value"), editable=("value",))
    for row, (key, value) in enumerate([("prd_id", catalog["prd"]["id"]), ("prd_sha256", catalog["prd"]["sha256"]), ("prd_source", catalog["prd"]["source"]),
                                        ("model", catalog["generated_by"]["model"]), ("prompt_version", catalog["generated_by"]["prompt_version"]),
                                        ("status", catalog["status"])], 2):
        _put(ws, row, 1, key)
        _put(ws, row, 2, value)
    check = DataValidation(type="list", formula1='"draft,approved"', allow_blank=False)
    check.add("B7")
    ws.add_data_validation(check)

    ws = wb.create_sheet("TestCases")
    _header(ws, TC_COLUMNS, TC_EDITABLE)
    _validate(ws, TC_COLUMNS)
    steps_ws, asserts_ws = wb.create_sheet("Steps"), wb.create_sheet("Assertions")
    _header(steps_ws, STEP_COLUMNS, STEP_COLUMNS[2:])
    _header(asserts_ws, ASSERT_COLUMNS, ASSERT_COLUMNS[2:])
    _validate(steps_ws, STEP_COLUMNS)
    _validate(asserts_ws, ASSERT_COLUMNS)
    step_row = assert_row = 2
    for row, tc in enumerate(catalog["test_cases"], 2):
        values = {"tc_id": tc["tc_id"], "story_id": story_of.get(tc["ac_refs"][0]), "ac_refs": ", ".join(tc["ac_refs"]), "title": tc["title"], "kind": tc["kind"],
                  "priority": tc.get("priority"), "technique": tc.get("technique"), "status": tc["status"], "rejected_reason": tc.get("rejected_reason"),
                  "notes": tc.get("notes"), "origin": tc["origin"], "preconditions": tc.get("preconditions"), "rationale": tc.get("rationale"),
                  "evidence": "; ".join(_evidence_strings(tc.get("evidence"))), "steps": len(tc["steps"]), "summary": _summary(tc)}
        for col, name in enumerate(TC_COLUMNS, 1):
            _put(ws, row, col, values[name])
        for number, step in enumerate(tc["steps"], 1):
            request, expect = step["request"], step["expect"]
            cells = {"tc_id": tc["tc_id"], "step_no": number, "method": request["method"], "path": request["path"], "path_params": _json_cell(request.get("path_params")),
                     "query": _json_cell(request.get("query")), "headers": _json_cell(request.get("headers")),
                     "body_json": _json_cell(request["json"]) if "json" in request else None, "expect_status": ", ".join(str(c) for c in expect["status"]),
                     "capture": _json_cell(step.get("capture"))}
            for col, name in enumerate(STEP_COLUMNS, 1):
                _put(steps_ws, step_row, col, cells[name])
            step_row += 1
            for n, assertion in enumerate(expect.get("json", []), 1):
                text, kind = _scalar_cell(assertion["value"]) if "value" in assertion else (None, None)
                cells = {"tc_id": tc["tc_id"], "step_no": number, "n": n, "path": assertion["path"], "op": assertion["op"], "value": text, "value_type": kind}
                for col, name in enumerate(ASSERT_COLUMNS, 1):
                    _put(asserts_ws, assert_row, col, cells[name])
                assert_row += 1

    ws = wb.create_sheet("Uncovered")
    _header(ws, UNCOVERED_COLUMNS, UNCOVERED_COLUMNS)
    for row, item in enumerate(catalog["uncovered_acs"], 2):
        _put(ws, row, 1, item["ac_id"])
        _put(ws, row, 2, item["reason"])
    ws = wb.create_sheet("Waivers")
    _header(ws, WAIVER_COLUMNS, WAIVER_COLUMNS)
    _validate(ws, WAIVER_COLUMNS)
    for row, waiver in enumerate(catalog.get("waivers", []), 2):
        for col, name in enumerate(WAIVER_COLUMNS, 1):
            _put(ws, row, col, waiver[name])
    ws = wb.create_sheet("SpecConflicts")
    _header(ws, CONFLICT_COLUMNS, ("status",))
    _validate(ws, CONFLICT_COLUMNS)
    for row, item in enumerate(catalog.get("spec_conflicts", []), 2):
        for col, value in enumerate((item["ac_id"], item["summary"], "; ".join(_evidence_strings(item.get("evidence"))), item["status"]), 1):
            _put(ws, row, col, value)

    _coverage_sheet(wb.create_sheet("Coverage"), catalog, facts, story_of)
    ws = wb.create_sheet("AgentPlan")
    _header(ws, ("ac_id", "technique", "decision", "scenario", "reason", "tc_ids"))
    for row, item in enumerate(catalog.get("coverage_plan", []), 2):
        for col, value in enumerate((item["ac_id"], item["technique"], item["decision"], item["scenario"], item.get("reason"), ", ".join(item.get("tc_ids", []))), 1):
            _put(ws, row, col, value)

    proj = projection(catalog)
    meta = wb.create_sheet("_meta")
    packed = base64.b64encode(zlib.compress(_canon(proj).encode("utf-8"), 9)).decode("ascii")
    for row, (key, value) in enumerate([("format_version", FORMAT_VERSION), ("prd_id", proj["prd"]["id"]), ("prd_sha256", proj["prd"]["sha256"]), ("digest", digest(proj))], 1):
        _put(meta, row, 1, key)
        _put(meta, row, 2, value)
    for index in range(0, len(packed), CHUNK):
        _put(meta, 5 + index // CHUNK, 1, "base")
        _put(meta, 5 + index // CHUNK, 2, packed[index:index + CHUNK])
    meta.sheet_state = "veryHidden"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _coverage_sheet(ws, catalog: dict, facts: dict | None, story_of: dict) -> None:
    _header(ws, ("dim", "id", "status", "detail"))
    counts: dict[str, dict[str, int]] = {}
    for tc in catalog["test_cases"]:
        for ref in tc["ac_refs"]:
            counts.setdefault(ref, {"draft": 0, "approved": 0, "rejected": 0})[tc["status"]] += 1
    listed = {u["ac_id"] for u in catalog["uncovered_acs"]}
    row = 2
    for story in catalog["stories"]:
        for ac in story["acs"]:
            have = counts.get(ac["ac_id"], {"draft": 0, "approved": 0, "rejected": 0})
            status = "covered" if have["approved"] + have["draft"] else ("waived" if ac["ac_id"] in listed else "gap")
            for col, value in enumerate(("ac", ac["ac_id"], status, f"approved {have['approved']}, draft {have['draft']}, rejected {have['rejected']}; story {story['story_id']}"), 1):
                _put(ws, row, col, value)
            row += 1
    if facts is None:
        return
    for item in gt_coverage.detail(catalog, facts):
        for col, value in enumerate((item.dim, item.id, item.status, ", ".join(item.tc_ids)), 1):
            _put(ws, row, col, value)
        row += 1


# ---------------- đọc ----------------

def _open(source) -> "Workbook":
    raw = source if isinstance(source, bytes) else None
    try:
        if raw is None:
            path = Path(source)
            if path.stat().st_size > MAX_BYTES:
                raise XlsxError(f"xlsx lớn hơn {MAX_BYTES} byte")
            raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise XlsxError(f"xlsx lớn hơn {MAX_BYTES} byte")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 200 or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED or any(i.compress_size and i.file_size / i.compress_size > MAX_RATIO and i.file_size > 1_000_000 for i in infos):
                raise XlsxError("xlsx giải nén quá lớn hoặc tỉ lệ nén bất thường")
        return load_workbook(io.BytesIO(raw), data_only=False)
    except XlsxError:
        raise
    except FileNotFoundError:
        raise XlsxError("không có file xlsx") from None
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError, EOFError):
        raise XlsxError("không đọc được xlsx (file hỏng hoặc không phải xlsx)") from None
    except Exception as error:  # noqa: BLE001 — openpyxl ném nhiều loại; chỉ báo loại, không trích nội dung
        raise XlsxError(f"không đọc được xlsx ({type(error).__name__})") from None


def _cell_text(cell, findings: list[Finding], *, strip: bool = True) -> str:
    """Giá trị ô dạng chuỗi. `strip=False` cho cột mà khoảng trắng đầu/cuối là NỘI DUNG (value của assertion `eq "  x  "` kiểm đúng khoảng trắng)."""
    value = cell.value
    if value is None:
        return ""
    if cell.data_type == "f":
        findings.append((f"{cell.parent.title}!{cell.coordinate}", "ô chứa công thức: không được phép (gõ giá trị, đừng gõ =...)"))
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        findings.append((f"{cell.parent.title}!{cell.coordinate}", "ô bị Excel đổi thành ngày giờ: đặt định dạng Text rồi nhập lại"))
        return ""
    return str(value).strip() if strip else str(value)


def _rows(wb, sheet: str, columns: tuple[str, ...], findings: list[Finding], *, required: tuple[str, ...] = (), raw: tuple[str, ...] = ()) -> list[tuple[int, dict]]:
    """[(số dòng, {cột: chuỗi})] theo TÊN tiêu đề; bỏ dòng trống. Thiếu sheet/cột bắt buộc thì XlsxError. Cột trong `raw` giữ nguyên khoảng trắng đầu/cuối."""
    if sheet not in wb.sheetnames:
        raise XlsxError(f"thiếu sheet {sheet}")
    ws = wb[sheet]
    if ws.max_row > MAX_ROWS + 1 or ws.max_column > 60:
        raise XlsxError(f"sheet {sheet} quá lớn (tối đa {MAX_ROWS} dòng, {ws.max_row} dòng đang bị tính): nếu phía dưới dữ liệu chỉ là dòng trống đã định dạng, "
                        "chọn các dòng đó rồi Delete (xoá hẳn dòng, không chỉ xoá nội dung)")
    header = {_cell_text(cell, findings).lower(): index for index, cell in enumerate(next(ws.iter_rows(min_row=1, max_row=1), ()), 0)}
    missing = [name for name in required if name not in header]
    if missing:
        raise XlsxError(f"sheet {sheet} thiếu cột: {', '.join(missing)}")
    out = []
    for number, cells in enumerate(ws.iter_rows(min_row=2), 2):
        values = {name: (_cell_text(cells[index], findings, strip=name not in raw) if index < len(cells) else "") for name, index in header.items() if name in columns}
        if any(v.strip() for v in values.values()):
            out.append((number, values))
    return out


def _json_value(text: str, where: str, findings: list[Finding], *, kind: type | None = None):
    if not text:
        return None
    try:
        value = json.loads(text, parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
    except (ValueError, RecursionError):
        findings.append((where, "không phải JSON hợp lệ"))
        return None
    if kind is not None and not isinstance(value, kind):
        findings.append((where, f"phải là JSON {'object' if kind is dict else 'giá trị'} (đang là {type(value).__name__})"))
        return None
    return value


def _coerce(text: str, value_type: str, op: str, where: str, findings: list[Finding]):
    """value của assertion theo `value_type`; `op` quyết định các luật riêng (type/len_*)."""
    if op in ("exists", "absent"):
        return None
    if op == "type":
        return text
    if op in ("len_eq", "len_gte"):
        if not re.fullmatch(r"\d{1,9}", text):
            findings.append((where, "len_eq/len_gte cần số nguyên không âm"))
            return None
        return int(text)
    kind = value_type or "string"
    if kind == "string":
        return text
    if kind == "null":
        return None
    if kind == "boolean":
        if text.lower() not in ("true", "false"):
            findings.append((where, "value_type boolean cần true hoặc false"))
        return text.lower() == "true"
    if kind == "number":
        value = _json_value(text, where, findings)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            findings.append((where, "value_type number cần một con số"))
            return None
        return value
    findings.append((where, "value_type phải là string, number, boolean hoặc null"))
    return None


def _steps_for(tc_id: str, steps_rows, assert_rows, findings: list[Finding]) -> list[dict]:
    mine = sorted((row for row in steps_rows if row[1]["tc_id"] == tc_id), key=lambda r: (int(r[1]["step_no"]) if r[1]["step_no"].isdigit() else 10**9, r[0]))
    steps = []
    for position, (number, row) in enumerate(mine, 1):
        where = f"Steps!{number}"
        if not row["step_no"].isdigit():
            findings.append((f"{where}", "step_no phải là số nguyên"))
        request: dict = {"method": row["method"].upper(), "path": row["path"]}
        for name in ("path_params", "query", "headers"):
            value = _json_value(row[name], f"{where}:{name}", findings, kind=dict)
            if value:
                request[name] = value
        if row["body_json"]:
            request["json"] = _json_value(row["body_json"], f"{where}:body_json", findings)
        codes = [c for c in re.split(r"[\s,;]+", row["expect_status"]) if c]
        if not codes or not all(re.fullmatch(r"\d{3}", c) for c in codes):
            findings.append((f"{where}", "expect_status phải là các mã 3 chữ số, vd 200 hoặc 200, 201"))
        expect: dict = {"status": [int(c) for c in codes if re.fullmatch(r"\d{3}", c)]}
        mine_asserts = sorted((r for r in assert_rows if r[1]["tc_id"] == tc_id and r[1]["step_no"] == row["step_no"]),
                              key=lambda r: (int(r[1]["n"]) if r[1]["n"].isdigit() else 10**9, r[0]))
        listing = []
        for a_number, a_row in mine_asserts:
            a_where = f"Assertions!{a_number}"
            item = {"path": a_row["path"], "op": a_row["op"]}
            if a_row["op"] not in ("exists", "absent"):
                item["value"] = _coerce(a_row["value"], a_row["value_type"], a_row["op"], a_where, findings)
            listing.append(item)
        if listing:
            expect["json"] = listing
        step: dict = {"request": request, "expect": expect}
        capture = _json_value(row["capture"], f"{where}:capture", findings, kind=dict)
        if capture:
            step["capture"] = capture
        steps.append(step)
    return steps


def _evidence_set(text: str) -> list[str]:
    return [part.strip() for part in text.split(";") if part.strip()]


def read_xlsx(source) -> tuple[dict, dict | None, list[Finding]]:
    """(theirs, base, lỗi cú pháp). `theirs` là projection của những gì đang có trong xlsx; `base` là ảnh chụp lúc xuất (None nếu thiếu `_meta`/hỏng).
    `lỗi cú pháp` (JSON sai, công thức, số sai...) KHÔNG làm ném lỗi: caller (import) báo hết một lượt."""
    wb = _open(source)
    findings: list[Finding] = []
    base, meta_ok = None, "_meta" in wb.sheetnames
    if meta_ok:
        rows = [(_cell_text(r[0], []), _cell_text(r[1], []) if len(r) > 1 else "") for r in wb["_meta"].iter_rows(min_row=1, max_row=MAX_ROWS)]
        version = next((v for k, v in rows if k == "format_version"), "")
        packed = "".join(v for k, v in rows if k == "base")
        try:
            base = json.loads(zlib.decompress(base64.b64decode(packed, validate=True), 0, MAX_UNCOMPRESSED).decode("utf-8")) if packed and version == str(FORMAT_VERSION) else None
        except (ValueError, zlib.error, UnicodeError):
            base = None
    catalog_rows = {r["key"]: r["value"] for _n, r in _rows(wb, "Catalog", ("key", "value"), findings, required=("key", "value"))}
    steps_rows = _rows(wb, "Steps", STEP_COLUMNS, findings, required=STEP_COLUMNS)
    assert_rows = _rows(wb, "Assertions", ASSERT_COLUMNS, findings, required=ASSERT_COLUMNS, raw=("value",))
    tests: dict[str, dict] = {}
    for number, row in _rows(wb, "TestCases", TC_COLUMNS, findings, required=("tc_id", "ac_refs", "title", "kind", "status")):
        tc_id = row["tc_id"]
        where = f"TestCases!{number}"
        if not tc_id:
            findings.append((where, "thiếu tc_id (TC mới: đặt NEW-1, NEW-2…)"))
            continue
        if tc_id in tests:
            findings.append((where, "tc_id bị trùng trong sheet"))
            continue
        tests[tc_id] = {"_row": number, "status": row["status"], "rejected_reason": _norm_text(row.get("rejected_reason")), "notes": _norm_text(row.get("notes")),
                        "priority": row.get("priority") or None, "title": " ".join(row["title"].split()), "ac_refs": list(dict.fromkeys(_AC_TOKEN.findall(row["ac_refs"]))),
                        "kind": row["kind"], "technique": row.get("technique") or None, "preconditions": _norm_text(row.get("preconditions")),
                        "rationale": _norm_text(row.get("rationale")), "steps": _steps_for(tc_id, steps_rows, assert_rows, findings),
                        "origin": row.get("origin") or None, "evidence": _evidence_set(row.get("evidence", ""))}
    known = set(tests)
    for number, row in steps_rows:
        if row["tc_id"] not in known:
            findings.append((f"Steps!{number}", "tc_id không có trong sheet TestCases"))
    for number, row in assert_rows:
        if row["tc_id"] not in known:
            findings.append((f"Assertions!{number}", "tc_id không có trong sheet TestCases"))
    uncovered, waivers, conflicts = {}, {}, {}
    for number, row in _rows(wb, "Uncovered", UNCOVERED_COLUMNS, findings, required=UNCOVERED_COLUMNS):
        uncovered[row["ac_id"]] = " ".join(row["reason"].split())
    for number, row in _rows(wb, "Waivers", WAIVER_COLUMNS, findings, required=WAIVER_COLUMNS):
        waivers[f"{row['kind']}\t{row['target']}"] = {**{k: row[k] for k in WAIVER_COLUMNS}, "reason": " ".join(row["reason"].split())}
    for number, row in _rows(wb, "SpecConflicts", CONFLICT_COLUMNS, findings, required=CONFLICT_COLUMNS):
        conflicts[f"{row['ac_id']}\t{row['summary']}"] = {"status": row["status"], "evidence": _evidence_set(row["evidence"])}
    meta = {name: value for name, value in (("id", catalog_rows.get("prd_id", "")), ("sha256", catalog_rows.get("prd_sha256", "")))}
    theirs = {"prd": meta, "status": catalog_rows.get("status", ""), "test_cases": tests, "uncovered": uncovered, "waivers": waivers, "conflicts": conflicts}
    return theirs, base, findings


def strip_rows(proj: dict) -> dict:
    """Bỏ khoá `_row` (chỉ để báo lỗi theo dòng) trước khi so sánh."""
    out = copy.deepcopy(proj)
    for tc in out["test_cases"].values():
        tc.pop("_row", None)
    return out


# ---------------- đồng bộ và import ----------------

def sync_status(catalog: dict, theirs: dict, base: dict | None) -> str:
    """in_sync | xlsx_ahead | yaml_ahead | diverged. Không có `base` thì chỉ phân biệt được in_sync / diverged."""
    ours, theirs = projection(catalog), strip_rows(theirs)
    if ours == theirs:
        return "in_sync"
    if base is None:
        return "diverged"
    if ours == base:
        return "xlsx_ahead"
    if theirs == base:
        return "yaml_ahead"
    return "diverged"


def _merge_value(path: str, ours, theirs, base, conflicts: list[str], *, have_base: bool):
    if theirs == ours:
        return ours
    if not have_base:
        return theirs                       # hai chiều: xlsx thắng
    if theirs == base:
        return ours
    if ours == base:
        return theirs
    conflicts.append(path)
    return ours


def import_into(catalog: dict, theirs: dict, base: dict | None, syntax: list[Finding] | None = None) -> ImportResult:
    """Pure: gộp xlsx (`theirs`) vào `catalog` (YAML) theo `base`. Không I/O. `result.ok` là điều kiện để ghi."""
    result = ImportResult(catalog=copy.deepcopy(catalog))
    result.errors.extend(syntax or [])
    ours = projection(catalog)
    rows = {tc_id: tc.get("_row") for tc_id, tc in theirs["test_cases"].items()}
    theirs = strip_rows(theirs)
    have_base = base is not None
    if not have_base:
        result.warnings.append(("_meta", "thiếu ảnh chụp gốc (sheet _meta): gộp hai chiều, xlsx thắng. Xuất lại bằng `gt export-xlsx` sau khi xong"))
    base = base or {"prd": {}, "status": None, "test_cases": {}, "uncovered": {}, "waivers": {}, "conflicts": {}}
    if theirs["prd"]["id"] != ours["prd"]["id"]:
        result.errors.append(("Catalog", "prd_id của xlsx khác catalog: đây là file của PRD khác"))
        return result
    cat = result.catalog
    position = {ac["ac_id"]: (s, a) for s, story in enumerate(cat["stories"]) for a, ac in enumerate(story["acs"])}

    cat["status"] = _merge_value("Catalog.status", ours["status"], theirs["status"], base["status"], result.conflicts, have_base=have_base)

    # --- test cases ---
    existing = {tc["tc_id"]: tc for tc in cat["test_cases"]}
    used = set(existing) | {i for i in theirs["test_cases"] if not _NEW.fullmatch(i)}
    order: list[dict] = []
    for tc_id, tc in existing.items():
        gone = tc_id not in theirs["test_cases"]
        if gone:
            if tc["origin"] != "qa":
                result.errors.append((f"TestCases:{tc_id}", "dòng TC do LLM sinh bị xoá: đặt status rejected kèm rejected_reason thay vì xoá"))
                order.append(tc)
            elif have_base and ours["test_cases"][tc_id] != base["test_cases"].get(tc_id):
                result.conflicts.append(f"{tc_id}.<xoá>")
                order.append(tc)
            else:
                result.removed.append(tc_id)
            continue
        merged, flipped = {}, False
        for name in TC_FIELDS:
            merged[name] = _merge_value(f"{tc_id}.{name}", ours["test_cases"][tc_id][name], theirs["test_cases"][tc_id][name], base["test_cases"].get(tc_id, {}).get(name),
                                        result.conflicts, have_base=have_base)
        for name in READONLY_FIELDS:
            if theirs["test_cases"][tc_id][name] != ours["test_cases"][tc_id][name] and (not have_base or theirs["test_cases"][tc_id][name] != base["test_cases"].get(tc_id, {}).get(name)):
                result.errors.append((f"TestCases!{rows.get(tc_id)}", f"cột {name} chỉ đọc nhưng đã bị sửa (tc_id {tc_id})"))
        new_tc = copy.deepcopy(tc)
        changed = False
        for name in (*REVIEW_FIELDS, *CONTENT_FIELDS):
            if merged[name] != ours["test_cases"][tc_id][name]:
                changed = True
                if name in OPTIONAL_TEXT and merged[name] is None:
                    new_tc.pop(name, None)
                else:
                    new_tc[name] = copy.deepcopy(merged[name])
                if name in CONTENT_FIELDS and tc["origin"] == "llm":
                    flipped = True
        if flipped:
            new_tc["origin"] = "qa"
            note = "sửa từ TC LLM (qua Excel)"
            new_tc["notes"] = f"{new_tc['notes']}; {note}" if new_tc.get("notes") and note not in new_tc["notes"] else new_tc.get("notes") or note
            result.converted.append(tc_id)
        if changed:
            result.changed.append(tc_id)
        order.append(new_tc)
    for tc_id, tc in theirs["test_cases"].items():
        if tc_id in existing:
            continue
        where = f"TestCases!{rows.get(tc_id)}"
        if not (_NEW.fullmatch(tc_id) or _TC_ID.fullmatch(tc_id)):
            result.errors.append((where, "tc_id mới phải là NEW-<tên> hoặc dạng TC-<ac>-<mô-tả>"))
            continue
        if have_base and tc_id in base["test_cases"]:
            result.removed.append(tc_id)    # YAML đã xoá TC này kể từ lúc xuất, xlsx vẫn còn: giữ quyết định của YAML
            continue
        if not tc["steps"]:
            result.errors.append((where, "TC mới chưa có bước nào trong sheet Steps"))
            continue
        bad = [ref for ref in tc["ac_refs"] if ref not in position]
        if not tc["ac_refs"] or bad:
            result.errors.append((where, "ac_refs phải liệt kê ít nhất một AC có trong catalog"))
            continue
        new_id = tc_id
        if _NEW.fullmatch(tc_id):
            digest6 = hashlib.sha1(_canon({"kind": tc["kind"], "steps": tc["steps"]}).encode("utf-8")).hexdigest()[:6]
            new_id = f"TC-{tc['ac_refs'][0]}-qa-{digest6}"
            serial = 1
            while new_id in used:
                serial += 1
                new_id = f"TC-{tc['ac_refs'][0]}-qa-{digest6}-{serial}"
        used.add(new_id)
        created = {"tc_id": new_id, "title": tc["title"], "ac_refs": tc["ac_refs"], "kind": tc["kind"], "status": tc["status"] or "approved", "origin": "qa", "steps": tc["steps"]}
        for name in OPTIONAL_TEXT:
            if tc[name] is not None:
                created[name] = tc[name]
        order.append(created)
        result.added.append(new_id)
    cat["test_cases"] = sorted(order, key=lambda tc: ((0, *position[tc["ac_refs"][0]]) if tc["ac_refs"][0] in position else (1, 0, 0), tc["tc_id"]))

    # --- uncovered_acs, waivers, spec_conflicts ---
    uncovered = {}
    for ac_id in sorted(set(ours["uncovered"]) | set(theirs["uncovered"]), key=lambda a: position.get(a, (9999, 0))):
        in_ours, in_theirs, in_base = ac_id in ours["uncovered"], ac_id in theirs["uncovered"], ac_id in base["uncovered"]
        if in_ours and in_theirs:
            value = _merge_value(f"uncovered.{ac_id}", ours["uncovered"][ac_id], theirs["uncovered"][ac_id], base["uncovered"].get(ac_id), result.conflicts, have_base=have_base)
        elif in_theirs:
            value = theirs["uncovered"][ac_id] if not (have_base and in_base) else None
        else:
            value = ours["uncovered"][ac_id] if not have_base or not in_base else (ours["uncovered"][ac_id] if ours["uncovered"][ac_id] != base["uncovered"][ac_id] else None)
        if value is not None:
            if ac_id not in position:
                result.errors.append(("Uncovered", f"ac_id {ac_id} không có trong catalog"))
            else:
                uncovered[ac_id] = value
    cat["uncovered_acs"] = [{"ac_id": a, "reason": r} for a, r in uncovered.items()]

    for name, key_names in (("waivers", WAIVER_COLUMNS), ("conflicts", ("status", "evidence"))):
        merged_items = {}
        for key in sorted(set(ours[name]) | set(theirs[name])):
            in_ours, in_theirs, in_base = key in ours[name], key in theirs[name], key in base[name]
            if in_ours and in_theirs:
                value = _merge_value(f"{name}[{key.replace(chr(9), ' ')}]", ours[name][key], theirs[name][key], base[name].get(key), result.conflicts, have_base=have_base)
            elif in_theirs:
                value = theirs[name][key] if not (have_base and in_base) else None      # xlsx thêm mới; YAML đã xoá kể từ base thì giữ xoá
            else:
                value = ours[name][key] if not (have_base and in_base and ours[name][key] == base[name][key]) else None   # QA xoá dòng trong xlsx
            if value is not None:
                merged_items[key] = value
        if name == "waivers":
            if merged_items:
                cat["waivers"] = [dict(merged_items[k]) for k in sorted(merged_items)]
            else:
                cat.pop("waivers", None)
        else:
            kept = []
            by_key = {f"{c['ac_id']}\t{c['summary']}": c for c in cat.get("spec_conflicts", [])}
            for key in sorted(merged_items, key=lambda k: (position.get(k.split("\t")[0], (9999, 0)), k)):
                entry = copy.deepcopy(by_key.get(key)) or {"ac_id": key.split("\t")[0], "summary": key.split("\t", 1)[1]}
                entry["status"] = merged_items[key]["status"]
                kept.append(entry)
            if kept:
                cat["spec_conflicts"] = kept
            else:
                cat.pop("spec_conflicts", None)

    problems = gt_schema.validate_catalog(_probe(cat))
    for problem in problems[:10]:
        result.errors.append(("catalog", f"sau khi gộp vi phạm schema tại {problem}"))
    return result


def _probe(catalog: dict) -> dict:
    """Như `check._probe`: hai luật mà cổng HITL báo RIÊNG (catalog approved => mọi TC đã duyệt; rejected cần lý do) không được làm import chết ở tầng schema."""
    probe = copy.deepcopy(catalog)
    if probe.get("status") == "approved":
        probe["status"] = "draft"
    for tc in probe["test_cases"]:
        if tc.get("status") == "rejected" and not (isinstance(tc.get("rejected_reason"), str) and tc["rejected_reason"].strip()):
            tc["rejected_reason"] = "-"
    return probe


# ---------------- file trên đĩa ----------------

def write_if_changed(root: Path, catalog: dict, facts: dict | None = None) -> str:
    """created | updated | unchanged. KHÔNG ghi lại khi xlsx hiện có đã tương đương catalog (cả projection lẫn ảnh chụp gốc): commit của bot không đổi vô cớ."""
    path = Path(root) / XLSX_PATH
    wanted = projection(catalog)
    existing = path.is_file()
    if existing:
        try:
            theirs, base, findings = read_xlsx(path)
            if not findings and strip_rows(theirs) == wanted and base == wanted:
                return "unchanged"
        except XlsxError:
            pass
    data = render_xlsx(catalog, facts)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".xlsx.tmp")
    temp.write_bytes(data)
    temp.replace(path)
    return "updated" if existing else "created"
