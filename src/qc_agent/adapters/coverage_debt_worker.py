"""Worker tất định dò "nợ test": bề mặt code MỚI THÊM mà chưa có test nào chạm tới (docs/phase2/plan-debt.md, P2-2).

  git diff base..HEAD  ->  bề mặt mới (head − base)  ->  đã có test chưa?  ->  debt.json (findings "debt:<kind>:<surface>")

3 loại bề mặt (D5), chỉ trả lời "có hay không", không đánh giá chất lượng test:
  api_contract  operation (METHOD /path) mới trong openapi.json đã commit.
                Đã có test = có task `api.property` không `exclude_path` path này.
  api_endpoint  endpoint mới trong code Python (AST: @app.get/@router.post/@x.route, APIRouter(prefix=), include_router(prefix=)).
                Đã có test = path template ({x} -> đoạn bất kỳ) xuất hiện trong ≥ 1 file khớp test_globs.
  ui_route      route mới trong code frontend (path: '/x' / path="/x" + tên component đi kèm).
                Đã có test = path HOẶC tên component xuất hiện trong file khớp test_globs (flow Midscene, e2e).

Không đoán (§1.1): base thiếu / checkout nông / cấu hình sai / file không parse được => status "error", findings rỗng.
Không QC_DIFF_BASE (--base) => full-scan: mọi bề mặt hiện có chưa có test (cơ sở để ĐÓNG nợ, D4).

Giới hạn đã biết (chấp nhận, xem plan §5): include_router giữa các file khác nhau không được nối prefix; route JS chỉ bắt được
dạng literal bắt đầu bằng '/'; "/" không bao giờ là nợ; khớp test theo chuỗi nên có thể sót (báo thiếu nợ), không bịa nợ.

Chỉ dùng stdlib + binary git (PyYAML đã là phụ thuộc của qc-agent, dùng cho coverage.yaml và suite).
Chạy:  python -m qc_agent.adapters.coverage_debt_worker [--root .] [--base REF] [--out debt.json]   (exit 0 ok · 3 error)
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

KIND_CONTRACT, KIND_ENDPOINT, KIND_UI = "api_contract", "api_endpoint", "ui_route"
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
DEFAULT_TEST_GLOBS = (".qc-agent/**", "tests/**", "e2e/**", "midscene/**")
COVERAGE_FILE = ".qc-agent/coverage.yaml"
SUITES_DIR = ".qc-agent/suites"
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", ".mypy_cache",
                       ".pytest_cache", ".next"})
UI_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx", ".vue", ".mjs"})
MAX_BYTES = 1_000_000  # file lớn hơn không đọc: không phải mã nguồn route / test

Surfaces = dict[tuple[str, str], frozenset]  # (kind, surface) -> alias (tên component để dò test)


class DebtError(Exception):
    """Không đủ căn cứ để kết luận -> status=error (không bao giờ là 'không có nợ')."""


# ───────────────────────────── git ─────────────────────────────

def _git(root: Path, *args: str) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True)
    except FileNotFoundError as error:
        raise DebtError("không có binary git") from error
    if done.returncode != 0:
        raise DebtError(f"git {args[0]} lỗi: {done.stderr.decode('utf-8', errors='replace').strip()[:200]}")
    return done.stdout


@dataclass(frozen=True)
class Change:
    status: str  # A M D R T
    path: str  # đường dẫn phía HEAD (D: đường dẫn cũ)
    old_path: str  # phía base (A: rỗng)


def changed_files(root: Path, base: str) -> list[Change]:
    """`git diff --name-status -M base HEAD`, đường dẫn tương đối với `root` (SUT root có thể là thư mục con của repo)."""
    if base.startswith("-"):
        raise DebtError(f"base không hợp lệ: {base!r}")
    try:
        _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    except DebtError as error:
        raise DebtError(f"không tìm thấy base {base!r} (checkout nông? workflow cần fetch-depth: 2): {error}") from error
    tokens = _git(root, "diff", "--name-status", "-M", "-z", "--relative", base, "HEAD", "--").decode("utf-8", errors="replace").split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()  # -z kết thúc bằng NUL
    changes, index = [], 0
    while index < len(tokens):
        status = tokens[index][:1]
        if status in ("R", "C"):
            old, new = tokens[index + 1], tokens[index + 2]
            index += 3
            changes.append(Change("R" if status == "R" else "A", new, old if status == "R" else ""))
        else:
            path = tokens[index + 1]
            index += 2
            changes.append(Change(status, path, "" if status == "A" else path))
    return changes


def read_at(root: Path, rev: str, path: str) -> str:
    """Nội dung `path` (tương đối với root) tại commit `rev`: `git show rev:./path`."""
    return _git(root, "show", f"{rev}:./{path}").decode("utf-8", errors="replace")


# ───────────────────────────── trích bề mặt (hàm thuần) ─────────────────────────────

def _add(dst: Surfaces, kind: str, surface: str, aliases=()) -> None:
    key = (kind, surface)
    dst[key] = dst.get(key, frozenset()) | frozenset(aliases)


def _merge(dst: Surfaces, src: Surfaces) -> None:
    for (kind, surface), aliases in src.items():
        _add(dst, kind, surface, aliases)


def _join_path(*parts: str) -> str:
    segments = [p.strip("/") for p in parts if p.strip("/")]
    return "/" + "/".join(segments)  # không có phần nào => "/"; bỏ '/' cuối để `/notes` và `/notes/` là một


_FLASK_PARAM = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _kw(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


_ROUTER_CTORS = frozenset({"APIRouter", "Blueprint", "FastAPI", "Flask"})


def api_surfaces(source: str, filename: str = "<source>") -> Surfaces:
    """Endpoint khai báo bằng decorator trong một file Python. Path dynamic (không phải literal bắt đầu bằng '/') bị bỏ qua."""
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as error:
        raise DebtError(f"{filename}: không parse được Python ({getattr(error, 'msg', error)})") from error

    prefixes: dict[str, str] = {}  # biến router -> prefix lúc tạo
    included: dict[str, str] = {}  # biến router -> prefix lúc include_router trong CÙNG file
    for node in ast.walk(tree):
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(value, ast.Call) and _callee_name(value) in _ROUTER_CTORS:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            prefix = _const_str(_kw(value, "prefix")) or _const_str(_kw(value, "url_prefix")) or ""
            prefixes.update({t.id: prefix for t in targets if isinstance(t, ast.Name)})
        elif isinstance(node, ast.Call) and _callee_name(node) in ("include_router", "register_blueprint"):
            if node.args and isinstance(node.args[0], ast.Name):
                included[node.args[0].id] = _const_str(_kw(node, "prefix")) or _const_str(_kw(node, "url_prefix")) or ""

    found: Surfaces = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            attr = dec.func.attr
            if attr in HTTP_METHODS:
                methods = [attr.upper()]
            elif attr in ("route", "api_route"):
                methods_node = _kw(dec, "methods")
                if methods_node is None:
                    methods = ["GET"]
                elif isinstance(methods_node, (ast.List, ast.Tuple, ast.Set)):
                    methods = [m.upper() for m in map(_const_str, methods_node.elts) if m and m.lower() in HTTP_METHODS]
                else:
                    methods = []  # methods=BIẾN: không biết => không bịa
            else:
                continue
            path = _const_str(dec.args[0]) if dec.args else _const_str(_kw(dec, "path") or _kw(dec, "rule"))
            if path is None or not (path == "" or path.startswith("/")):
                continue
            if isinstance(_kw(dec, "include_in_schema"), ast.Constant) and _kw(dec, "include_in_schema").value is False:
                continue  # nội bộ (/__qc/...): không thuộc contract công khai
            owner = dec.func.value.id if isinstance(dec.func.value, ast.Name) else ""
            full = _join_path(included.get(owner, ""), prefixes.get(owner, ""), _FLASK_PARAM.sub(r"{\1}", path))
            for method in methods:
                _add(found, KIND_ENDPOINT, f"{method} {full}")
    return found


def openapi_surfaces(text: str, filename: str = "openapi.json") -> Surfaces:
    try:
        paths = json.loads(text).get("paths", {})
    except (ValueError, AttributeError) as error:
        raise DebtError(f"{filename}: không phải OpenAPI JSON hợp lệ") from error
    if not isinstance(paths, dict):
        raise DebtError(f"{filename}: `paths` phải là object")
    found: Surfaces = {}
    for path, item in paths.items():
        for method in item if isinstance(item, dict) else ():
            if method.lower() in HTTP_METHODS:
                _add(found, KIND_CONTRACT, f"{method.upper()} {path}")
    return found


_UI_PATH = re.compile(r"""\bpath\s*[:=]\s*\{?\s*(['"`])(/[^'"`\s$]*)\1""")
_UI_COMPONENT = (re.compile(r"\belement\s*[:=]\s*\{?\s*<\s*([A-Z]\w*)"), re.compile(r"\b[cC]omponent\s*[:=]\s*\{?\s*([A-Z]\w*)"))
_UI_WINDOW = 300  # tên component được tìm trong ngần này ký tự SAU path, dừng ở route kế tiếp


def ui_routes(source: str) -> Surfaces:
    """Route literal trong code frontend. "/" bị bỏ (cookie `path: '/'`, và mọi test đều chạm tới nó)."""
    matches = list(_UI_PATH.finditer(source))
    found: Surfaces = {}
    for i, match in enumerate(matches):
        route = _join_path(match.group(2))
        if route == "/":
            continue
        end = min(match.end() + _UI_WINDOW, matches[i + 1].start() if i + 1 < len(matches) else len(source))
        window = source[match.end():end]
        _add(found, KIND_UI, route, {m.group(1) for rx in _UI_COMPONENT for m in rx.finditer(window)})
    return found


def surfaces_of(path: str, source: str) -> Surfaces:
    pure = PurePosixPath(path)
    if pure.name == "openapi.json":
        return openapi_surfaces(source, path)
    if pure.suffix == ".py":
        return api_surfaces(source, path)
    if pure.suffix in UI_SUFFIXES and not pure.name.endswith(".d.ts"):
        return ui_routes(source)
    return {}


def new_surfaces(head: Surfaces, base: Surfaces) -> Surfaces:
    """new = head − base (D5: chỉ bề mặt mới thêm; bề mặt bị sửa/xoá không phải nợ)."""
    return {key: aliases for key, aliases in head.items() if key not in base}


# ───────────────────────────── glob + khớp path ─────────────────────────────

def glob_regex(glob: str) -> re.Pattern:
    """`**` qua nhiều thư mục, `*` trong một thư mục (fnmatch để `*` ăn cả '/', không dùng được)."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            out.append("[^/]*" if glob[i] == "*" else "[^/]" if glob[i] == "?" else re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_PARAM_SPLIT = re.compile(r"(\{[^}/]*\}|:[A-Za-z_]\w*)")
_SEGMENT = r"[^/\s\"'`?&#<>]+"


def path_regex(template: str) -> re.Pattern:
    """`/notes/{id}` (hoặc `/notes/:id`) -> khớp `/notes/42`, `/notes/{note_id}`, `/notes/${id}`; KHÔNG khớp `/notes/42/x`
    hay `/notes` khi template dài hơn; `/notes` không khớp `/notes/42`."""
    parts = _PARAM_SPLIT.split(template)
    body = "".join(_SEGMENT if i % 2 else re.escape(part) for i, part in enumerate(parts))
    return re.compile(body + r"(?![\w\-]|/[\w{:$])")


def _norm_params(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


# ───────────────────────────── cấu hình + chỉ mục "đã có test" ─────────────────────────────

@dataclass(frozen=True)
class Config:
    ignore: tuple = ()  # ((mẫu surface, lý do), ...)
    test_globs: tuple = DEFAULT_TEST_GLOBS


def _yaml_load(path: Path):
    try:
        import yaml  # noqa: PLC0415 — chỉ cần khi repo có file cấu hình/suite
    except ImportError as error:
        raise DebtError("thiếu PyYAML để đọc coverage.yaml/suite") from error
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise DebtError(f"{path.name}: không đọc được: {error}") from error


def load_config(root: Path) -> Config:
    path = root / COVERAGE_FILE
    if not path.is_file():
        return Config()
    raw = _yaml_load(path) or {}
    if not isinstance(raw, dict) or set(raw) - {"ignore", "test_globs"}:
        raise DebtError(f"{COVERAGE_FILE}: chỉ nhận khoá ignore, test_globs")
    globs = raw.get("test_globs", list(DEFAULT_TEST_GLOBS))
    if not isinstance(globs, list) or not globs or any(not isinstance(g, str) or not g for g in globs):
        raise DebtError(f"{COVERAGE_FILE}: test_globs phải là danh sách chuỗi không rỗng")
    ignore = []
    for entry in raw.get("ignore") or []:
        surface = entry.get("surface") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(surface, str) or not surface or not isinstance(reason, str) or not reason.strip():
            raise DebtError(f"{COVERAGE_FILE}: mỗi dòng ignore cần surface và reason (lý do) không rỗng")
        ignore.append((surface, reason.strip()))
    return Config(ignore=tuple(ignore), test_globs=tuple(globs))


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            yield full.relative_to(root).as_posix(), full


def _read_small(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", errors="replace") if path.stat().st_size <= MAX_BYTES else None
    except OSError:
        return None


def _drop_key(node, key: str):
    if isinstance(node, dict):
        return {k: _drop_key(v, key) for k, v in node.items() if k != key}
    return [_drop_key(v, key) for v in node] if isinstance(node, list) else node


class CoverageIndex:
    """Tra "đã có test chưa". Đọc cây làm việc (checkout ở HEAD); tính lười, một lần."""

    def __init__(self, root: Path, config: Config, suites_dir: str = SUITES_DIR):
        self.root, self.config, self.suites_dir = root, config, suites_dir.strip("/")
        self.is_test = self._matcher(config.test_globs)

    @staticmethod
    def _matcher(globs):
        regexes = [glob_regex(g) for g in globs]
        return lambda rel: any(rx.match(rel) for rx in regexes)

    @functools.cached_property
    def corpus(self) -> list[tuple[str, str]]:
        """Văn bản các file test. Hai ngoại lệ để không tự "che" nợ: coverage.yaml (chứa chính surface bị ignore) và
        `exclude_path` trong suite (path bị LOẠI khỏi test không được tính là có test)."""
        out = []
        for rel, full in _walk(self.root):
            if rel == COVERAGE_FILE or not self.is_test(rel):
                continue
            text = _read_small(full)
            if text is None:
                continue
            if rel.startswith(self.suites_dir + "/") and rel.endswith((".yaml", ".yml")):
                try:
                    import yaml  # noqa: PLC0415
                    text = yaml.safe_dump(_drop_key(yaml.safe_load(text), "exclude_path"), allow_unicode=True)
                except Exception:  # noqa: BLE001 — suite hỏng: không cấp điểm "có test" (hướng an toàn = báo nợ)
                    continue
            out.append((rel, text))
        return out

    @functools.cached_property
    def property_excludes(self) -> list[frozenset]:
        """Mỗi task api.property -> tập path (đã chuẩn hoá tham số) mà nó LOẠI khỏi test."""
        directory = self.root / self.suites_dir
        excludes = []
        for file in sorted(directory.glob("*.y*ml")) if directory.is_dir() else []:
            suite = _yaml_load(file)
            for task in (suite.get("tasks") or []) if isinstance(suite, dict) else []:
                if isinstance(task, dict) and task.get("capability") == "api.property":
                    raw = (task.get("inputs") or {}).get("exclude_path", [])
                    raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
                    excludes.append(frozenset(_norm_params(p) for p in raw if isinstance(p, str)))
        return excludes

    @functools.cached_property
    def live_paths(self) -> frozenset:
        """Path có trong openapi.json đang có trong cây làm việc."""
        live = set()
        for rel, full in _walk(self.root):
            if PurePosixPath(rel).name == "openapi.json" and (text := _read_small(full)) is not None:
                live |= {_norm_params(s.split(" ", 1)[1]) for _, s in openapi_surfaces(text, rel)}
        return frozenset(live)

    def covered(self, kind: str, surface: str, aliases=()) -> bool:
        if kind == KIND_CONTRACT:
            path = _norm_params(surface.split(" ", 1)[1])
            return path in self.live_paths and any(path not in excluded for excluded in self.property_excludes)
        route = surface.split(" ", 1)[1] if kind == KIND_ENDPOINT else surface
        rx = path_regex(route)
        if any(rx.search(text) for _, text in self.corpus):
            return True
        names = [re.compile(rf"(?<![\w$]){re.escape(a)}(?![\w$])") for a in aliases]
        return any(n.search(text) for n in names for _, text in self.corpus)


# ───────────────────────────── quét ─────────────────────────────

def _diff_surfaces(root: Path, base: str, is_test) -> Surfaces:
    head: Surfaces = {}
    before: Surfaces = {}
    for change in changed_files(root, base):
        if change.status != "D" and not is_test(change.path):
            _merge(head, surfaces_of(change.path, read_at(root, "HEAD", change.path)))
        if change.status != "A" and not is_test(change.old_path):  # cả file bị xoá/đổi tên: bề mặt chuyển file không phải nợ mới
            _merge(before, surfaces_of(change.old_path, read_at(root, base, change.old_path)))
    return new_surfaces(head, before)


def _full_surfaces(root: Path, is_test) -> Surfaces:
    found: Surfaces = {}
    for rel, full in _walk(root):
        if is_test(rel) or PurePosixPath(rel).suffix not in {".py", ".json", *UI_SUFFIXES}:
            continue
        text = _read_small(full)
        if text is not None:
            _merge(found, surfaces_of(rel, text))
    return found


def _ignore_reason(config: Config, kind: str, surface: str) -> str | None:
    for pattern, reason in config.ignore:
        if fnmatch.fnmatchcase(surface, pattern) or fnmatch.fnmatchcase(f"{kind}:{surface}", pattern):
            return reason
    return None


def scan(root: Path, base: str | None, suites_dir: str = SUITES_DIR) -> dict:
    config = load_config(root)
    index = CoverageIndex(root, config, suites_dir)
    surfaces = _diff_surfaces(root, base, index.is_test) if base else _full_surfaces(root, index.is_test)
    findings, ignored = [], []
    for (kind, surface), aliases in sorted(surfaces.items()):
        if index.covered(kind, surface, sorted(aliases)):
            continue
        finding_id = f"debt:{kind}:{surface}"
        reason = _ignore_reason(config, kind, surface)
        if reason is not None:
            ignored.append({"finding_id": finding_id, "reason": reason})  # hiện ra để reviewer thấy, không thành nợ
        else:
            findings.append({"finding_id": finding_id, "kind": kind, "surface": surface})
    return report("ok", base, findings, ignored)


def report(status: str, base: str | None, findings=(), ignored=(), error: str | None = None) -> dict:
    return {
        "status": status, "error": error, "mode": "diff" if base else "full", "base": base,
        "findings": list(findings), "ignored": list(ignored),
        "metrics": {"debt.new": len(findings), "debt.full_scan": not base},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dò nợ test: bề mặt mới chưa có test -> debt.json")
    ap.add_argument("--root", default=".", help="SUT root (mặc định: cwd)")
    ap.add_argument("--base", help="commit gốc để diff (mặc định: $QC_DIFF_BASE; không có => full-scan)")
    ap.add_argument("--out", default="debt.json")
    ap.add_argument("--suites-dir", default=SUITES_DIR, help="thư mục suite tương đối với root")
    args = ap.parse_args(argv)
    base = (args.base if args.base is not None else os.environ.get("QC_DIFF_BASE", "")).strip() or None
    root = Path(args.root).resolve()
    try:
        result, code = scan(root, base, args.suites_dir), 0
    except DebtError as error:
        result, code = report("error", base, error=str(error)), 3
    try:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        sys.stderr.write(f"không ghi được {args.out}: {error}\n")
        return 1
    sys.stdout.write(f"coverage-debt: status={result['status']} mode={result['mode']} new={result['metrics']['debt.new']}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
