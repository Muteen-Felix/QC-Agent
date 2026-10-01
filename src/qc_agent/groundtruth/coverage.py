"""Bộ chấm coverage TẤT ĐỊNH cho Ground-Truth. Không LLM, không mạng; cùng catalog + cùng snapshot OpenAPI cho ra đúng cùng kết quả.

Ba chiều, mẫu số do CODE quyết định (người/agent không tự khai mình cần phủ gì):
  - `ac`         mọi AC trong catalog. Phủ khi có TC (thuộc `tc_statuses`) trỏ tới AC, hoặc AC nằm trong `uncovered_acs` (tính là "waived").
  - `technique`  với mỗi operation, các yêu cầu SUY RA từ ràng buộc của OpenAPI (xem `requirements`): thiếu field bắt buộc, biên độ dài / giá trị, ngoài enum,
                 sai pattern, thiếu xác thực. Phủ khi một bước của TC thật sự làm đúng việc đó (kiểm cấu trúc request + mã mong đợi), KHÔNG tin nhãn `technique`
                 mà người sinh tự gắn (nhãn chỉ để hiển thị).
  - `api`        mọi (operation × mã trạng thái số được khai trong OpenAPI). `default`, 1xx, 3xx, 5xx được miễn sẵn (không tái hiện được qua HTTP có kiểm soát).
                 Một bước chỉ phủ mã c khi nó mong ĐÚNG một mã [c]: danh sách nhiều mã (`[200, 404, 422]`) không phủ mã nào, để một TC không "phủ" cả bảng.

Miễn (waiver): `catalog["waivers"]` với `kind` + `target` khớp ĐÚNG id của gap (xem `Dim.gaps`) và `status` thuộc `waiver_statuses` (QA duyệt = `approved`).

Hai file dữ liệu trong `.qc-agent/ground-truth/` (cả hai bị CODEOWNERS khoá cùng `/.qc-agent/`):
  - `openapi.snapshot.json`   OpenAPI RÚT GỌN do `gt generate|regen` ghi (operation, mã trả về, tham số, ràng buộc của body). Là thứ giúp `gt validate` chấm OFFLINE
                              (job validate không có mạng, không có OpenAPI gốc). Không chứa server URL, ví dụ hay mô tả.
  - `coverage-policy.yaml`    ngưỡng từng chiều (mặc định 1.0). Tuỳ chọn.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from qc_agent.groundtruth import render as gt_render
from qc_agent.scaffold import openapi

SNAPSHOT_PATH = f"{gt_render.GT_DIR}/openapi.snapshot.json"
POLICY_PATH = f"{gt_render.GT_DIR}/coverage-policy.yaml"
SNAPSHOT_VERSION = 1
DIMENSIONS = ("ac", "technique", "api")
MAX_FILE_BYTES = 5_000_000
MAX_BOUNDARY = 5000          # biên dài hơn mức này không đòi TC (body hàng chục KB không phải test hợp lý)
MAX_ENUM = 50
MAX_PATTERN = 200
MAX_GAPS_IN_REPORT = 50
_EPS = 1e-9
_TYPES = ("string", "integer", "number", "boolean", "array", "object")
_EXEMPT_CODE = re.compile(r"[135]\d\d")   # 1xx/3xx/5xx; `default` không phải số nên cũng bị miễn


class CoverageError(ValueError):
    """Snapshot hoặc policy không đọc được/sai cấu trúc. Thông điệp không trích nội dung file."""


# ---------------- snapshot OpenAPI ----------------

def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _flat(spec: dict, node, depth: int = 0) -> dict:
    """Schema đã giải `$ref`; `allOf` được gộp; `anyOf/oneOf` lấy nhánh không-null đầu tiên (kiểu Optional của Pydantic v2). Không giải được thì {}."""
    node = openapi._deref(spec, node)
    if not isinstance(node, dict) or depth > 4:
        return {}
    out = {key: value for key, value in node.items() if key not in ("allOf", "anyOf", "oneOf")}
    for part in node.get("allOf") or []:
        flat = _flat(spec, part, depth + 1)
        out = {**out, **{k: v for k, v in flat.items() if k not in ("properties", "required")}}
        out["properties"] = {**(out.get("properties") or {}), **(flat.get("properties") or {})}
        out["required"] = [*(out.get("required") or []), *(flat.get("required") or [])]
    for key in ("anyOf", "oneOf"):
        branches = [flat for flat in (_flat(spec, part, depth + 1) for part in node.get(key) or []) if flat and flat.get("type") != "null"]
        if branches:
            out = {**branches[0], **out}
            break
    return out


def _constraints(schema: dict) -> dict:
    out: dict = {}
    if schema.get("type") in _TYPES:
        out["type"] = schema["type"]
    for key in ("maxLength", "minLength"):
        value = schema.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    for key, flag, rank in (("maximum", "exclusiveMaximum", "max"), ("minimum", "exclusiveMinimum", "min")):
        limit, exclusive = schema.get(key), schema.get(flag)
        if _number(exclusive):            # OpenAPI 3.1: exclusiveMaximum là con số
            limit, exclusive = exclusive, True
        if _number(limit):
            out[key] = limit
            if exclusive is True:
                out[flag] = True
    enum = schema.get("enum")
    if isinstance(enum, list) and 0 < len(enum) <= MAX_ENUM and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in enum):
        out["enum"] = list(enum)
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and 0 < len(pattern) <= MAX_PATTERN:
        try:
            re.compile(pattern)
            out["pattern"] = pattern
        except re.error:
            pass
    return out


def _params(spec: dict, item: dict, op: dict) -> list[dict]:
    found: dict[tuple[str, str], dict] = {}
    for raw in [*(item.get("parameters") or []), *(op.get("parameters") or [])]:
        parameter = openapi._deref(spec, raw)
        if not isinstance(parameter, dict) or not isinstance(parameter.get("name"), str) or parameter.get("in") not in ("query", "path"):
            continue
        schema = _flat(spec, parameter["schema"]) if "schema" in parameter else _flat(spec, parameter)   # Swagger 2: ràng buộc nằm ngay trên tham số
        found[(parameter["in"], parameter["name"])] = {"name": parameter["name"], "in": parameter["in"],
                                                       "required": bool(parameter.get("required")) or parameter["in"] == "path", **_constraints(schema)}
    return [found[key] for key in sorted(found)]


def _fields(spec: dict, schema: dict) -> list[dict]:
    required = set(schema.get("required") or [])
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    return [{"name": name, "required": name in required, **_constraints(_flat(spec, props[name]))}
            for name in sorted(props) if isinstance(name, str) and len(name) <= 64]


def _body(spec: dict, op: dict) -> dict | None:
    body = openapi._deref(spec, op.get("requestBody"))
    if isinstance(body, dict):
        content = body.get("content") if isinstance(body.get("content"), dict) else {}
        media = next((m for m in sorted(content) if "json" in str(m).lower()), None)
        schema = _flat(spec, (content[media] or {}).get("schema")) if media else {}
        return {"required": bool(body.get("required")), "fields": _fields(spec, schema)}
    for raw in op.get("parameters") or []:   # Swagger 2
        parameter = openapi._deref(spec, raw)
        if isinstance(parameter, dict) and parameter.get("in") == "body":
            return {"required": bool(parameter.get("required")), "fields": _fields(spec, _flat(spec, parameter.get("schema")))}
    return None


def snapshot(spec: dict) -> dict:
    """OpenAPI -> sự kiện tối thiểu cho bộ chấm. Tất định (xếp theo path rồi method, khoá xếp tên)."""
    operations = []
    for endpoint in openapi.endpoints(spec):
        item = spec["paths"][endpoint["spec_path"]]
        op = item[endpoint["method"].lower()]
        operations.append({"method": endpoint["method"], "path": endpoint["path"], "secured": openapi._has_security(spec, op),
                           "responses": list(endpoint["responses"]), "params": _params(spec, item, op), "body": _body(spec, op)})
    return {"version": SNAPSHOT_VERSION, "operations": operations}


def snapshot_text(facts: dict) -> str:
    return json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _valid_facts(facts) -> bool:
    return (isinstance(facts, dict) and facts.get("version") == SNAPSHOT_VERSION and isinstance(facts.get("operations"), list)
            and all(isinstance(op, dict) and isinstance(op.get("method"), str) and isinstance(op.get("path"), str)
                    and isinstance(op.get("responses"), list) and isinstance(op.get("params"), list) for op in facts["operations"]))


def load_snapshot(sut_root: Path) -> dict | None:
    """None nếu chưa có file (repo sinh GT trước khi có bộ chấm). Có file mà hỏng thì `CoverageError`."""
    path = Path(sut_root) / SNAPSHOT_PATH
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise CoverageError(f"{SNAPSHOT_PATH} lớn hơn {MAX_FILE_BYTES} byte")
        facts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        raise CoverageError(f"không đọc được {SNAPSHOT_PATH}") from None
    except ValueError:
        raise CoverageError(f"{SNAPSHOT_PATH} không phải JSON hợp lệ") from None   # không kèm thông điệp của json: nó trích lại nội dung
    if not _valid_facts(facts):
        raise CoverageError(f"{SNAPSHOT_PATH} sai cấu trúc (cần version {SNAPSHOT_VERSION} và operations)")
    return facts


# ---------------- policy ----------------

def load_policy(sut_root: Path) -> tuple[dict[str, float], bool]:
    """(ngưỡng từng chiều, có file không). Mặc định 1.0 cho cả ba chiều."""
    thresholds = {name: 1.0 for name in DIMENSIONS}
    path = Path(sut_root) / POLICY_PATH
    if not path.is_file():
        return thresholds, False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        raise CoverageError(f"không đọc được {POLICY_PATH}") from None
    except yaml.YAMLError:
        raise CoverageError(f"{POLICY_PATH} không phải YAML hợp lệ") from None
    given = data.get("thresholds") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and data.get("version") == 1 and set(data) <= {"version", "thresholds"} and isinstance(given, dict)
            and set(given) <= set(DIMENSIONS) and all(_number(v) and 0 <= v <= 1 for v in given.values())):
        raise CoverageError(f"{POLICY_PATH} sai cấu trúc (cần version: 1 và thresholds: {{ac|technique|api: 0..1}})")
    thresholds.update({name: float(value) for name, value in given.items()})
    return thresholds, True


# ---------------- yêu cầu technique suy ra từ ràng buộc ----------------

@dataclass(frozen=True)
class Req:
    id: str            # phần sau "METHOD path " của id gap; cũng là `target` của waiver
    where: str         # body | query | status
    field: str
    rule: str          # missing | len_eq | len_gt | len_lt | val_eq | val_gt | val_lt | num_gt | num_lt | num_ge | num_le | not_enum | not_pattern | status
    arg: object
    expect: str        # ok (mọi mã 2xx) | reject (mọi mã 4xx) | auth (mọi mã 401/403)


def _bound_reqs(where: str, name: str, c: dict) -> list[Req]:
    ref = f"{where}.{name}"
    out: list[Req] = []
    integer = c.get("type") == "integer"
    top, low = c.get("maxLength"), c.get("minLength")
    if top is not None and 0 < top <= MAX_BOUNDARY:
        out += [Req(f"boundary:max_length:{ref}@{top}", where, name, "len_eq", top, "ok"), Req(f"boundary:max_length:{ref}@{top + 1}", where, name, "len_gt", top + 1, "reject")]
    if low is not None and 1 <= low <= MAX_BOUNDARY:
        out += [Req(f"boundary:min_length:{ref}@{low}", where, name, "len_eq", low, "ok"), Req(f"boundary:min_length:{ref}@{low - 1}", where, name, "len_lt", low - 1, "reject")]
    for key, flag, sign in (("maximum", "exclusiveMaximum", 1), ("minimum", "exclusiveMinimum", -1)):
        if key not in c:
            continue
        limit, exclusive = c[key], c.get(flag) is True
        label = "maximum" if sign == 1 else "minimum"
        if integer and float(limit).is_integer():
            edge = int(limit) - (sign if exclusive else 0)        # giá trị hợp lệ sát biên
            out += [Req(f"boundary:{label}:{ref}@{edge}", where, name, "val_eq", edge, "ok"),
                    Req(f"boundary:{label}:{ref}@{edge + sign}", where, name, "val_gt" if sign == 1 else "val_lt", edge + sign, "reject")]
        else:   # số thực: không có "biên ± 1" duy nhất, chỉ đòi MỘT giá trị bị từ chối. Biên mở (exclusive): chính giá trị biên cũng bị từ chối nên tính cả nó.
            rule = ("num_ge" if exclusive else "num_gt") if sign == 1 else ("num_le" if exclusive else "num_lt")
            out.append(Req(f"boundary:{label}:{ref}@{limit}", where, name, rule, limit, "reject"))
    if "enum" in c:
        out.append(Req(f"equivalence:enum:{ref}", where, name, "not_enum", tuple(c["enum"]), "reject"))
    if "pattern" in c:
        out.append(Req(f"equivalence:pattern:{ref}", where, name, "not_pattern", c["pattern"], "reject"))
    return out


def requirements(op: dict) -> list[Req]:
    """Yêu cầu technique của một operation. Chỉ suy ra từ thứ OpenAPI KHAI; không bịa thêm."""
    out: list[Req] = []
    fields = (op.get("body") or {}).get("fields") or []
    queries = [p for p in op["params"] if p.get("in") == "query"]
    for where, items in (("body", fields), ("query", queries)):
        for item in items:
            if item.get("required"):
                out.append(Req(f"negative_validation:missing:{where}.{item['name']}", where, item["name"], "missing", None, "reject"))
            out += _bound_reqs(where, item["name"], item)
    if op.get("secured"):
        out.append(Req("authz:unauthenticated", "status", "", "status", None, "auth"))
    return out


# ---------------- khớp bước <-> yêu cầu ----------------

def _expected(codes: list, expect: str) -> bool:
    if not codes or any(isinstance(c, bool) or not isinstance(c, int) for c in codes):
        return False
    if expect == "ok":
        return all(200 <= c < 300 for c in codes)
    if expect == "reject":
        return all(400 <= c < 500 for c in codes)
    return all(c in (401, 403) for c in codes)


def _literal(value) -> bool:
    return not (isinstance(value, str) and "{{" in value)   # biến capture: chưa biết giá trị lúc chấm, không tính


def _num(value):
    if isinstance(value, bool):
        return None
    if _number(value):
        return value
    if isinstance(value, str) and _literal(value):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _required_names(op: dict, where: str) -> list[str]:
    if where == "body":
        return [f["name"] for f in (op.get("body") or {}).get("fields") or [] if f.get("required")]
    return [p["name"] for p in op["params"] if p.get("in") == "query" and p.get("required")]


def _covers(req: Req, op: dict, step: dict) -> bool:
    request, codes = step["request"], step["expect"]["status"]
    if not _expected(codes, req.expect):
        return False
    if req.where == "status":
        return True
    container = request.get("json") if req.where == "body" else request.get("query")
    container = container if isinstance(container, dict) else None
    if req.rule == "missing":
        names = _required_names(op, req.where)
        if container is None:
            return names == [req.field]      # không gửi gì: chỉ tính khi đây là field bắt buộc DUY NHẤT (lỗi quy được về field này)
        return req.field not in container and all(name in container for name in names if name != req.field)
    if container is None or req.field not in container:
        return False
    value = container[req.field]
    if not _literal(value):
        return False
    if req.rule in ("len_eq", "len_gt", "len_lt"):
        return isinstance(value, str) and len(value) == req.arg
    if req.rule in ("val_eq", "val_gt", "val_lt"):
        number = _num(value)
        return number is not None and number == req.arg
    if req.rule in ("num_gt", "num_lt", "num_ge", "num_le"):
        number = _num(value)
        if number is None:
            return False
        return {"num_gt": number > req.arg, "num_lt": number < req.arg, "num_ge": number >= req.arg, "num_le": number <= req.arg}[req.rule]
    if req.rule == "not_enum":
        return isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) not in {str(item) for item in req.arg}
    if req.rule == "not_pattern":
        return isinstance(value, str) and re.search(req.arg, value) is None
    return False


# ---------------- chấm ----------------

@dataclass(frozen=True)
class Dim:
    name: str
    total: int
    covered: int
    waived: int
    gaps: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return (self.covered + self.waived) / self.total if self.total else 1.0


@dataclass(frozen=True)
class CoverageReport:
    ac: Dim
    technique: Dim | None     # None khi chưa có snapshot OpenAPI: không có mẫu số để chấm
    api: Dim | None

    def dims(self) -> dict[str, Dim]:
        return {name: dim for name, dim in (("ac", self.ac), ("technique", self.technique), ("api", self.api)) if dim is not None}

    def shortfalls(self, thresholds: dict[str, float]) -> dict[str, Dim]:
        return {name: dim for name, dim in self.dims().items() if dim.ratio + _EPS < thresholds.get(name, 1.0)}

    def complete(self, thresholds: dict[str, float] | None = None) -> bool:
        return not self.shortfalls(thresholds or {})

    def as_dict(self, limit: int = MAX_GAPS_IN_REPORT) -> dict:
        return {name: {"total": dim.total, "covered": dim.covered, "waived": dim.waived, "ratio": round(dim.ratio, 4),
                       "gaps": list(dim.gaps[:limit]), "gaps_total": len(dim.gaps)} for name, dim in self.dims().items()}


def _waived(catalog: dict, statuses: tuple[str, ...]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"technique": set(), "api": set()}
    for waiver in catalog.get("waivers") or []:
        if waiver.get("status") in statuses and waiver.get("kind") in out:
            out[waiver["kind"]].add(waiver["target"])
    return out


def score(catalog: dict, facts: dict | None, *, tc_statuses: tuple[str, ...] = ("approved",), waiver_statuses: tuple[str, ...] = ("approved",)) -> CoverageReport:
    """Chấm `catalog`. `tc_statuses` là trạng thái TC được tính (gate: approved; vòng agent: draft + approved). `facts` là kết quả `snapshot()` (None = chỉ chấm AC)."""
    counted = [tc for tc in catalog["test_cases"] if tc.get("status") in tc_statuses]
    refs = {ref for tc in counted for ref in tc["ac_refs"]}
    listed = {u["ac_id"] for u in catalog["uncovered_acs"]}
    ac_ids = [ac["ac_id"] for story in catalog["stories"] for ac in story["acs"]]
    ac_gaps = tuple(a for a in ac_ids if a not in refs and a not in listed)
    ac = Dim("ac", len(ac_ids), sum(1 for a in ac_ids if a in refs), sum(1 for a in ac_ids if a not in refs and a in listed), ac_gaps)
    if facts is None:
        return CoverageReport(ac, None, None)

    steps: dict[tuple[str, str], list[dict]] = {}
    for tc in counted:
        for step in tc["steps"]:
            steps.setdefault((step["request"]["method"], step["request"]["path"]), []).append(step)
    waived = _waived(catalog, waiver_statuses)

    t_total = t_cov = t_waived = 0
    a_total = a_cov = a_waived = 0
    t_gaps: list[str] = []
    a_gaps: list[str] = []
    for op in facts["operations"]:
        here = steps.get((op["method"], op["path"]), [])
        for req in requirements(op):
            gap_id = f"{op['method']} {op['path']} {req.id}"
            t_total += 1
            if any(_covers(req, op, step) for step in here):
                t_cov += 1
            elif gap_id in waived["technique"]:
                t_waived += 1
            else:
                t_gaps.append(gap_id)
        for code in op["responses"]:
            code = str(code)
            if not code.isdigit() or _EXEMPT_CODE.fullmatch(code):
                continue
            gap_id = f"{op['method']} {op['path']} {code}"
            a_total += 1
            if any(step["expect"]["status"] == [int(code)] for step in here):
                a_cov += 1
            elif gap_id in waived["api"]:
                a_waived += 1
            else:
                a_gaps.append(gap_id)
    return CoverageReport(ac, Dim("technique", t_total, t_cov, t_waived, tuple(t_gaps)), Dim("api", a_total, a_cov, a_waived, tuple(a_gaps)))


# ---------------- chi tiết từng ô (cho Excel của QA) ----------------

@dataclass(frozen=True)
class Row:
    dim: str            # technique | api
    id: str             # đúng id gap (cũng là `target` của waiver): "POST /notes boundary:max_length:body.title@201"
    status: str         # covered | waived | gap | exempt
    tc_ids: tuple[str, ...]


def detail(catalog: dict, facts: dict, *, tc_statuses: tuple[str, ...] = ("draft", "approved"), waiver_statuses: tuple[str, ...] = ("draft", "approved"),
           max_tc_ids: int = 5) -> list[Row]:
    """Từng ô technique/API và TC nào phủ nó (tối đa `max_tc_ids`). Dùng cùng luật với `score`; mã miễn sẵn (default/1xx/3xx/5xx) có status `exempt`."""
    counted = [tc for tc in catalog["test_cases"] if tc.get("status") in tc_statuses]
    waived = _waived(catalog, waiver_statuses)
    rows: list[Row] = []
    for op in facts["operations"]:
        here = [(tc["tc_id"], step) for tc in counted for step in tc["steps"] if (step["request"]["method"], step["request"]["path"]) == (op["method"], op["path"])]
        for req in requirements(op):
            gap_id = f"{op['method']} {op['path']} {req.id}"
            by = tuple(dict.fromkeys(tc_id for tc_id, step in here if _covers(req, op, step)))
            rows.append(Row("technique", gap_id, "covered" if by else ("waived" if gap_id in waived["technique"] else "gap"), by[:max_tc_ids]))
        for code in op["responses"]:
            code = str(code)
            gap_id = f"{op['method']} {op['path']} {code}"
            if not code.isdigit() or _EXEMPT_CODE.fullmatch(code):
                rows.append(Row("api", gap_id, "exempt", ()))
                continue
            by = tuple(dict.fromkeys(tc_id for tc_id, step in here if step["expect"]["status"] == [int(code)]))
            rows.append(Row("api", gap_id, "covered" if by else ("waived" if gap_id in waived["api"] else "gap"), by[:max_tc_ids]))
    return rows
