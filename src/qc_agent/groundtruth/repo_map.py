"""Repo map (tầng 1 của việc đọc mã nguồn): quét TẤT ĐỊNH bằng `ast`, KHÔNG LLM, ra bản tóm tắt để đưa cho agent ngay từ đầu, nên agent không phải tự mò những thứ
máy trích được: route -> hàm xử lý (`file:dòng`), mã trạng thái mà hàm đó có thể ném (kể cả qua hàm phụ trợ), dependency xác thực, tham số Query/Path/Body có ràng buộc,
model Pydantic (ràng buộc từng field, validator), enum, hằng số giới hạn, exception handler.

Giới hạn trung thực (đưa cả vào văn bản gửi LLM): hiện chỉ hiểu Python/FastAPI+Pydantic; là BEST-EFFORT, nên có thể thiếu hoặc lệch (router lồng nhau, ràng buộc tính bằng
biểu thức, hàm phụ trợ ở file khác). Nó là GỢI Ý để chọn file cần đọc, KHÔNG phải nguồn kỳ vọng: kỳ vọng của test vẫn lấy từ PRD/OpenAPI. Ràng buộc không phải literal (vd
`max_length=TITLE_MAX`) được giữ nguyên dạng mã để agent biết tìm ở đâu.

Quét lại rẻ: mỗi file có `sha1`; `build(previous=...)` dùng lại kết quả của file không đổi và chỉ phân tích lại file đổi (regen sau khi mã đổi ít).

An toàn: đi qua `RepoSandbox.iter_files` (cùng deny-list, không theo symlink/junction, bỏ thư mục test); mọi chuỗi trích ra đều qua `redact`; hằng số có tên giống bí mật (KEY, SECRET,
TOKEN, PASSWORD…) bị bỏ hẳn. Không thực thi mã của SUT, chỉ phân tích cú pháp. Văn bản cuối đi qua `fence` vì mã nguồn là dữ liệu KHÔNG TIN CẬY.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from qc_agent.groundtruth.repo_tools import RepoSandbox, fence, redact

VERSION = 1
MAX_TEXT = 40_000
MAX_SRC = 90
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
PARAM_KINDS = ("query", "path", "body", "header", "cookie", "form", "file", "depends", "security")
FIELD_KEYS = ("min_length", "max_length", "ge", "gt", "le", "lt", "pattern", "regex", "min_items", "max_items", "multiple_of", "max_digits", "decimal_places")
LIMITS = {"routes": 300, "models": 120, "enums": 60, "consts": 80, "handlers": 40}
AUTH_NAME = re.compile(r"(?i)auth|current_user|token|permission|require|verify|oauth|api_?key|security|bearer|login|role")
SECRET_NAME = re.compile(r"(?i)key|secret|token|passw|credential|private")
CONST_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,40}")
_STATUS_NAME = re.compile(r"HTTP_(\d{3})_")


def _src(node, limit: int = MAX_SRC) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # noqa: BLE001 — AST lạ: bỏ qua, không làm hỏng cả file
        return "?"
    text = redact(" ".join(text.split()))
    return text if len(text) <= limit else text[:limit] + "…"


def _name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return None


def _status(node) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool) and 100 <= node.value <= 599:
        return node.value
    match = _STATUS_NAME.search(_src(node, 60)) if isinstance(node, (ast.Attribute, ast.Name)) else None
    return int(match.group(1)) if match else None


def _kw(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _deps(node) -> list[str]:
    out: list[str] = []
    for item in node.elts if isinstance(node, (ast.List, ast.Tuple)) else []:
        if isinstance(item, ast.Call) and _name(item.func) in ("Depends", "Security") and item.args:
            label = _name(item.args[0])
            if label:
                out.append(label)
    return out


def _param(arg: ast.arg, default) -> dict:
    out: dict = {"name": arg.arg, "type": _src(arg.annotation, 50) if arg.annotation is not None else None}
    if isinstance(default, ast.Call) and (_name(default.func) or "").lower() in PARAM_KINDS:
        kind = (_name(default.func) or "").lower()
        out["kind"] = kind
        if kind in ("depends", "security"):
            out["dep"] = _name(default.args[0]) if default.args else None
        else:
            if default.args:
                out["default"] = _src(default.args[0], 40)
            constraints = {k.arg: _src(k.value, 40) for k in default.keywords if k.arg in FIELD_KEYS}
            if constraints:
                out["constraints"] = constraints
    elif default is not None:
        out["kind"], out["default"] = "plain", _src(default, 40)
    else:
        out["kind"] = "plain"
    return out


def _func_facts(fn) -> tuple[list[int], list[str], list[str]]:
    """(mã HTTPException có thể ném trực tiếp, tên exception khác, tên hàm được gọi) trong thân hàm."""
    codes: list[int] = []
    others: list[str] = []
    calls: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            label = _name(exc)
            if label == "HTTPException" and isinstance(node.exc, ast.Call):
                code = _status(_kw(node.exc, "status_code")) if _kw(node.exc, "status_code") is not None else (_status(node.exc.args[0]) if node.exc.args else None)
                if code is not None:
                    codes.append(code)
            elif label:
                others.append(label)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append(node.func.id)
    return sorted(set(codes)), sorted(set(others))[:6], sorted(set(calls))


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def extract(source: str) -> dict | None:
    """Sự kiện của MỘT file Python, hoặc None nếu không phân tích được (lỗi cú pháp, quá sâu). Không bao giờ ném lỗi."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    routes, funcs, models, enums, consts, handlers, routers, includes = [], {}, [], [], {}, [], {}, []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            codes, others, calls = _func_facts(node)
            funcs.setdefault(node.name, {"raises": codes, "exceptions": others, "calls": calls, "line": node.lineno})
            for decorator in node.decorator_list:
                if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                    continue
                attr = decorator.func.attr
                if attr == "exception_handler" and decorator.args:
                    statuses = sorted({s for c in ast.walk(node) if isinstance(c, ast.Call) and (s := _status(_kw(c, "status_code")) if _kw(c, "status_code") is not None else None)})
                    handlers.append({"exception": _src(decorator.args[0], 40), "handler": node.name, "line": node.lineno, "statuses": statuses})
                    continue
                if attr not in (*HTTP_METHODS, "api_route"):
                    continue
                path_node = decorator.args[0] if decorator.args else _kw(decorator, "path")
                path = _literal(path_node) if path_node is not None else None
                if not isinstance(path, str):
                    continue
                methods = [attr.upper()] if attr != "api_route" else [str(m).upper() for m in (_literal(_kw(decorator, "methods")) or ["GET"])]
                declared_node = _kw(decorator, "responses")
                declared = sorted({c for key in (declared_node.keys if isinstance(declared_node, ast.Dict) else []) if (c := _status(key) if key is not None else None)})
                deps = _deps(_kw(decorator, "dependencies")) if _kw(decorator, "dependencies") is not None else []
                params = []
                positional = node.args.args
                defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
                for arg, default in [*zip(positional, defaults), *zip(node.args.kwonlyargs, node.args.kw_defaults)]:
                    if arg.arg in ("self", "cls"):
                        continue
                    info = _param(arg, default)
                    if info.get("dep"):
                        deps.append(info["dep"])
                    params.append(info)
                status = _status(_kw(decorator, "status_code")) if _kw(decorator, "status_code") is not None else None
                hidden = _literal(_kw(decorator, "include_in_schema")) is False if _kw(decorator, "include_in_schema") is not None else False
                for method in methods:
                    routes.append({"method": method, "path": path, "router": _name(decorator.func.value), "handler": node.name, "line": node.lineno, "status_code": status,
                                   "declared": declared, "deps": sorted(set(deps)), "params": params[:8], "hidden": hidden})
        elif isinstance(node, ast.ClassDef):
            bases = [_src(b, 40) for b in node.bases]
            if any(b.endswith("BaseModel") or b.endswith("SQLModel") for b in bases):
                models.append(_model(node))
            elif any(re.search(r"(?:^|\.)(?:Str|Int)?Enum$", b) for b in bases):
                members = {t.id: _src(s.value, 40) for s in node.body if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name) and not t.id.startswith("_")}
                enums.append({"name": node.name, "line": node.lineno, "members": dict(list(members.items())[:30])})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router" and node.args:
            prefix = _literal(_kw(node, "prefix")) if _kw(node, "prefix") is not None else ""
            includes.append({"router": _name(node.args[0]), "prefix": prefix if isinstance(prefix, str) else ""})
    for node in tree.body:   # chỉ mức module cho hằng số và khai báo router
        targets = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else \
                  [(node.target.id, node.value)] if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None else []
        for name, value in targets:
            if isinstance(value, ast.Call) and _name(value.func) in ("APIRouter", "FastAPI"):
                prefix = _literal(_kw(value, "prefix")) if _kw(value, "prefix") is not None else ""
                routers[name] = {"prefix": prefix if isinstance(prefix, str) else "", "deps": _deps(_kw(value, "dependencies")) if _kw(value, "dependencies") is not None else []}
            elif CONST_NAME.fullmatch(name) and not SECRET_NAME.search(name):
                literal = _literal(value)
                if isinstance(literal, (int, float, str, bool)) and not (isinstance(literal, str) and len(literal) > 120):
                    consts[name] = _src(value, 60)
                elif isinstance(literal, (tuple, list)) and 0 < len(literal) <= 10:
                    consts[name] = _src(value, 80)
    return {"routes": routes, "funcs": funcs, "models": models, "enums": enums, "consts": consts, "handlers": handlers, "routers": routers, "includes": includes}


def _model(node: ast.ClassDef) -> dict:
    fields, validators = [], []
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and not item.target.id.startswith("_"):
            annotation, value = item.annotation, item.value
            constraints: dict[str, str] = {}
            if isinstance(annotation, ast.Subscript) and _name(annotation.value) == "Annotated" and isinstance(annotation.slice, ast.Tuple):
                for extra in annotation.slice.elts[1:]:
                    if isinstance(extra, ast.Call):
                        constraints.update({k.arg: _src(k.value, 40) for k in extra.keywords if k.arg in FIELD_KEYS})
                annotation_text = _src(annotation.slice.elts[0], 50)
            else:
                annotation_text = _src(annotation, 50)
                if isinstance(annotation, ast.Call):
                    constraints.update({k.arg: _src(k.value, 40) for k in annotation.keywords if k.arg in FIELD_KEYS})
            required = value is None
            if isinstance(value, ast.Call) and _name(value.func) == "Field":
                constraints.update({k.arg: _src(k.value, 40) for k in value.keywords if k.arg in FIELD_KEYS})
                first = value.args[0] if value.args else _kw(value, "default")
                required = (isinstance(first, ast.Constant) and first.value is Ellipsis) or (first is None and _kw(value, "default_factory") is None)
            fields.append({"name": item.target.id, "type": annotation_text, "required": required, **({"constraints": constraints} if constraints else {})})
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in item.decorator_list:
                if isinstance(decorator, ast.Call) and _name(decorator.func) in ("validator", "field_validator", "root_validator", "model_validator"):
                    validators.append({"func": item.name, "line": item.lineno, "fields": [a.value for a in decorator.args if isinstance(a, ast.Constant) and isinstance(a.value, str)][:5]})
    return {"name": node.name, "line": node.lineno, "fields": fields[:40], "validators": validators[:10]}


def sha1(raw: bytes) -> str:
    return hashlib.sha1(raw).hexdigest()


def build(root: Path, *, previous: dict | None = None, sandbox: RepoSandbox | None = None, max_files: int = 400) -> dict:
    """{version, files: {rel: {sha1, data}}, stats}. `previous` (kết quả `build` lần trước) cho phép dùng lại file không đổi. Tất định: cùng cây file -> cùng kết quả."""
    sandbox = sandbox or RepoSandbox(root)
    old = (previous or {}).get("files", {}) if isinstance(previous, dict) and previous.get("version") == VERSION else {}
    files: dict[str, dict] = {}
    parsed = reused = skipped = 0
    for rel, path in sandbox.iter_files((".py",), max_files=max_files):
        try:
            raw = path.read_bytes()
            digest = sha1(raw)
            if rel in old and old[rel].get("sha1") == digest:
                files[rel] = old[rel]
                reused += 1
                continue
            data = extract(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError):
            skipped += 1
            continue
        if data is None:
            skipped += 1
            continue
        files[rel] = {"sha1": digest, "data": data}
        parsed += 1
    return {"version": VERSION, "files": files, "stats": {"files": len(files), "parsed": parsed, "reused": reused, "skipped": skipped}}


def load(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION and isinstance(data.get("files"), dict) else None


def save(path: Path, repo_map: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(repo_map, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")


# ---------------- tổng hợp và văn bản cho LLM ----------------

def _closure(name: str, funcs: dict, depth: int = 3, seen: frozenset = frozenset()) -> dict[int, str]:
    """mã -> tên hàm nơi nó được ném (theo đường gọi hàm cùng file, tối đa `depth` bước)."""
    info = funcs.get(name)
    if info is None or name in seen:
        return {}
    out = {code: name for code in info["raises"]}
    if depth > 0:
        for callee in info["calls"]:
            for code, where in _closure(callee, funcs, depth - 1, seen | {name}).items():
                out.setdefault(code, where)
    return out


def summarize(repo_map: dict) -> dict:
    """Gộp theo file thành các danh sách phẳng, đã xếp. Mỗi route có `raises` ({mã: hàm ném}) tính xuyên hàm phụ trợ trong file."""
    includes: dict[str, list[str]] = {}
    routers: dict[str, dict] = {}
    for entry in repo_map["files"].values():
        for item in entry["data"]["includes"]:
            if item["router"]:
                includes.setdefault(item["router"], []).append(item["prefix"])
        routers.update(entry["data"]["routers"])
    routes, models, enums, handlers, consts = [], [], [], [], {}
    for rel, entry in sorted(repo_map["files"].items()):
        data = entry["data"]
        for route in data["routes"]:
            prefixes = sorted(set(includes.get(route["router"], [""])))
            own = routers.get(route["router"], {}).get("prefix", "")
            raises = _closure(route["handler"], data["funcs"])
            deps = sorted(set(route["deps"]) | set(routers.get(route["router"], {}).get("deps", [])))
            routes.append({**{k: route[k] for k in ("method", "handler", "line", "status_code", "declared", "params", "hidden")}, "path": (prefixes[0] if len(prefixes) == 1 else "") + own + route["path"],
                           "file": rel, "raises": raises, "deps": deps, "auth": [d for d in deps if AUTH_NAME.search(d)], "ambiguous_prefix": len(prefixes) > 1,
                           "exceptions": data["funcs"].get(route["handler"], {}).get("exceptions", [])})
        models += [{**m, "file": rel} for m in data["models"]]
        enums += [{**e, "file": rel} for e in data["enums"]]
        handlers += [{**h, "file": rel} for h in data["handlers"]]
        consts.update({f"{name}": (value, rel) for name, value in data["consts"].items()})
    routes.sort(key=lambda r: (r["path"], r["method"]))
    return {"routes": routes, "models": models, "enums": enums, "handlers": handlers, "consts": consts}


def _cap(lines: list[str], limit: int, label: str) -> list[str]:
    return lines[:limit] + ([f"… (+{len(lines) - limit} {label} nữa, đọc mã để biết)"] if len(lines) > limit else [])


def render_text(repo_map: dict, max_chars: int = MAX_TEXT) -> str:
    """Văn bản gọn cho LLM (đã `fence`; người gọi bọc trong `<repo_map>`). Cắt theo mục, không cắt giữa dòng."""
    s = summarize(repo_map)
    lines = ["Code-extracted (Python/FastAPI+Pydantic only; BEST-EFFORT, may be incomplete: confirm by reading the file). A hint for where to read; expected values come from the PRD/OpenAPI, not from here."]
    routes = []
    for r in s["routes"]:
        parts = [f"{r['method']} {r['path']}{' (prefix uncertain)' if r['ambiguous_prefix'] else ''} -> {r['handler']} @{r['file']}:{r['line']}"]
        if r["hidden"]:
            parts.append("[NOT in OpenAPI: internal, do not test]")
        if r["status_code"]:
            parts.append(f"status={r['status_code']}")
        if r["declared"]:
            parts.append("declared=" + ",".join(map(str, r["declared"])))
        if r["raises"]:
            parts.append("raises=" + ",".join(f"{code}{'' if via == r['handler'] else '(via ' + via + ')'}" for code, via in sorted(r["raises"].items())))
        if r["exceptions"]:
            parts.append("exceptions=" + ",".join(r["exceptions"]))
        if r["auth"]:
            parts.append("auth=" + ",".join(r["auth"]))
        interesting = [p for p in r["params"] if p["kind"] not in ("plain", "depends", "security") or p.get("constraints")]
        if interesting:
            parts.append("params=" + "; ".join(f"{p['name']}:{p['kind']}" + (f"({', '.join(f'{k}={v}' for k, v in p['constraints'].items())})" if p.get("constraints") else "") for p in interesting[:5]))
        body = [p for p in r["params"] if p["kind"] == "plain" and p.get("type") and p["type"][:1].isupper() and p["name"] not in ("request", "response")]
        if body:
            parts.append("body=" + ",".join(f"{p['type']}" for p in body[:2]))
        routes.append(" ".join(parts))
    models = []
    for m in s["models"]:
        fields = "; ".join(f"{f['name']}: {f['type']}{'' if f['required'] else ' (optional)'}" + (" " + " ".join(f"{k}={v}" for k, v in f["constraints"].items()) if f.get("constraints") else "") for f in m["fields"])
        validators = " validators=" + ",".join(f"{v['func']}({','.join(v['fields'])})" for v in m["validators"]) if m["validators"] else ""
        models.append(f"{m['name']} @{m['file']}:{m['line']}: {fields}{validators}")
    enums = [f"{e['name']} @{e['file']}:{e['line']}: " + ", ".join(f"{k}={v}" for k, v in e["members"].items()) for e in s["enums"]]
    handlers = [f"{h['exception']} -> {h['handler']} @{h['file']}:{h['line']}" + (f" status={','.join(map(str, h['statuses']))}" if h["statuses"] else "") for h in s["handlers"]]
    consts = [f"{name} = {value} @{rel}" for name, (value, rel) in sorted(s["consts"].items())]
    sections = [("routes", "routes", routes), ("models", "models", models), ("exception handlers", "handlers", handlers), ("enums", "enums", enums), ("constants", "consts", consts)]
    out = lines[:]
    for title, key, items in sections:
        if not items:
            continue
        block = [f"## {title}", *_cap(items, LIMITS[key], title)]
        if sum(len(x) + 1 for x in [*out, *block]) > max_chars:
            out.append(f"## {title}: omitted (size limit); use grep")
            continue
        out += block
    if len(out) == len(lines):
        return ""   # không trích được gì: không đưa một dòng chú thích trơ trọi vào prompt
    text = "\n".join(out)
    return fence(text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0] + "\n… (cắt bớt)")
