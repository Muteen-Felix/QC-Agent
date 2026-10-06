"""Scanner TẤT ĐỊNH của `qc-agent init` (bước 32): đọc cây thư mục repo SUT và đề xuất Dockerfile API, cổng, health path, biến CORS, thư mục UI...

Hàm thuần, chỉ ĐỌC file: không mạng, không chạy code SUT, không LLM. Mỗi giá trị trả về là một `Finding` (giá trị, nguồn flag|detected|default, các ứng viên,
lý do). Nguyên tắc "tìm thấy ≠ xác nhận": luật ưu tiên (vị trí, tên thư mục, EXPOSE...) CHỈ để sắp xếp gợi ý, không phải bằng chứng. Mỗi giá trị suy ra
thuộc một trong ba loại: có flag (không VERIFY); có bằng chứng loại trừ đủ (không VERIFY); hoặc có `verify` để `init` ghi
`# qc-agent:todo VERIFY: chọn X trong [X, Y] vì ...` (`validate` chặn tới khi người xác nhận).
Dockerfile: >= 2 ứng viên thì VERIFY; MỘT ứng viên mà có dấu hiệu web (thư mục tên web|ui|frontend|client|admin|www, FROM nginx|httpd|caddy,
`http.server`/`serve -s`/`npm run serve|preview`/`vite preview`) thì cũng VERIFY "chưa chắc là API". Dấu hiệu web làm ứng viên xếp SAU, không loại nó.
Context: xem dockerfile_copy.py (nguồn COPY/ADD tồn tại ở đúng một context thì chọn, còn lại VERIFY).
Health path (S4-11): chỉ đọc `.py` trong phạm vi mã API ĐÃ CHỨNG MINH; vị trí Dockerfile không bao giờ đủ một mình. Thang bằng chứng:
E1 = Dockerfile ở thư mục con D có nguồn COPY/ADD là thư mục nằm trong D, hoặc `COPY .` với context đúng bằng D; E2 = các nguồn COPY/ADD là thư mục cụ thể
(kể cả nằm ngoài thư mục chứa Dockerfile); E1/E2 chỉ dùng khi context không VERIFY; E3 = repo chỉ có một Dockerfile, một gốc dự án Python, một file khởi tạo
web app. Chưa đạt thì `/` chỉ là giá trị TẠM kèm VERIFY liệt kê route health toàn repo (để tham khảo). Route tĩnh không biết prefix `include_router`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from qc_agent.scaffold import dockerfile_copy

MAX_DEPTH = 4
MAX_FILE_BYTES = 512_000
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "out", ".qc-agent", "runs", "downloads",
                       "site-packages", "tests", "test", "__tests__"})
DEFAULT_PORT = "8000"
DEFAULT_HEALTH = "/"
DEFAULT_NODE = 22
UI_PORT = "8080"
HEALTH_ORDER = ("/api/health", "/health", "/healthz")
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template")
LOCKFILES = (("package-lock.json", "npm"), ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"))
UI_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte")
UI_OUTPUT = {"vite": "dist", "cra": "build", "next-export": "out"}
UI_ENV_PREFIX = {"vite": "VITE_", "cra": "REACT_APP_", "next-export": "NEXT_PUBLIC_"}

_APP_CTOR = re.compile(r"\b(?:FastAPI|Flask|Starlette|Sanic|Quart)\s*\(")
_HEALTH_ROUTE = re.compile(r"""@\w+(?:\.\w+)*\.(?:get|head|api_route)\(\s*['"](/[^'"{}]*)['"]""")
_CORS_ENV_PY = re.compile(r"""(?:getenv|environ(?:\.get)?)\s*[(\[]\s*['"]([A-Z0-9_]*CORS[A-Z0-9_]*)['"]""")
_CORS_ENV_FILE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]*CORS[A-Z0-9_]*)\s*=", re.M)
_OPENAPI_URL = re.compile(r"""openapi_url\s*=\s*['"](/[^'"]*)['"]""")
_EXPOSE = re.compile(r"^\s*EXPOSE\s+(\d{2,5})(?:/\w+)?", re.M | re.I)
_CMD_PORT = re.compile(r"""--port["',\s=]+(\d{2,5})""")
_NEXT_EXPORT = re.compile(r"""output\s*:\s*['"]export['"]""")
_INT = re.compile(r"\d+")
DB_ENV_NAMES = ("DATABASE_URL", "SQLALCHEMY_DATABASE_URI", "DB_URL", "MONGODB_URI", "MONGO_URL")
_DB_REF = re.compile(r"\b(" + "|".join(DB_ENV_NAMES) + r")\b")
_DB_FILE = re.compile(r"(?:.*\.(?:py|js|mjs|ts)|(?:docker-)?compose[\w.-]*\.ya?ml|\.env\.(?:example|sample|template))")
_TEST_FILE = re.compile(r"(?:test_.*\.py|.*_test\.py|conftest\.py|.*\.(?:test|spec)\.[jt]s)")
WEB_NAMES = frozenset({"web", "ui", "frontend", "client", "admin", "www"})
API_NAMES = frozenset({"api", "server", "backend"})
MONOREPO_PARENTS = ("apps", "services", "packages")
_WEB_BASE = re.compile(r"^\s*FROM\s+(?:\S+/)?(nginx|httpd|caddy)\b", re.M | re.I)
_WEB_CMD = re.compile(r"http\.server|\bnpm\s+run\s+(?:serve|preview)\b|\bvite\s+preview\b|\bserve\s+-s\b", re.I)


class ScanError(ValueError):
    """Thiếu một thành phần BẮT BUỘC mà không có flag: không đoán, báo lỗi kèm hướng dẫn."""


@dataclass(frozen=True)
class Finding:
    value: object
    source: str                       # flag | detected | default
    candidates: tuple = ()
    reason: str = ""
    verify: str | None = None         # có => init ghi `# qc-agent:todo VERIFY: <verify>`


@dataclass(frozen=True)
class UiScan:
    directory: str                    # tương đối gốc repo, posix ("." nếu ở gốc)
    kind: str                         # vite | cra | next-export
    output_dir: str
    package_manager: str
    lockfile: str
    node_major: Finding
    api_var: Finding | None
    directory_finding: Finding


@dataclass
class ScanResult:
    dockerfile: Finding | None = None
    context: str = "."
    context_finding: Finding | None = None    # None khi --no-api; có `verify` khi COPY/ADD không đủ dữ kiện để chọn context
    copy: dockerfile_copy.ContextAnalysis | None = None
    warnings: list[str] = field(default_factory=list)
    port: Finding = field(default_factory=lambda: Finding(DEFAULT_PORT, "default"))
    health_path: Finding = field(default_factory=lambda: Finding(DEFAULT_HEALTH, "default"))
    openapi_path: Finding | None = None       # None: không thấy FastAPI (api-contract vẫn sinh, với REFINE)
    fastapi: bool = False
    cors_env: Finding | None = None
    db_refs: dict[str, list[str]] = field(default_factory=dict)   # {biến DB mã SUT tham chiếu: [file]}; rỗng = không thấy
    ui: UiScan | None = None
    ui_refused: str | None = None             # lý do KHÔNG tự sinh được Dockerfile.ui (hướng dẫn dùng --ui-dockerfile)
    notes: list[str] = field(default_factory=list)


# ---------- đọc cây thư mục ----------

def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def walk(root: Path):
    """(thư mục tương đối, các file) — bỏ SKIP_DIRS và mọi thư mục dấu chấm, sâu tối đa MAX_DEPTH. Thứ tự tất định."""
    root = Path(root)
    for current, dirs, files in os.walk(root):
        rel = Path(current).relative_to(root)
        depth = len(rel.parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and depth < MAX_DEPTH)
        yield rel, sorted(files)


def _posix(rel: Path) -> str:
    text = rel.as_posix()
    return text or "."


def _rank_note(rule: str, chosen, candidates) -> str | None:
    """Nội dung VERIFY khi có >= 2 ứng viên."""
    if len(candidates) < 2:
        return None
    return f"chọn {chosen} trong [{', '.join(str(c) for c in candidates)}] vì {rule}"


def _flag(value) -> Finding:
    return Finding(value, "flag", (value,), "đặt bằng tham số dòng lệnh")


# ---------- API ----------

def _dockerfile_candidates(root: Path) -> list[str]:
    """Dockerfile ở gốc > `*/Dockerfile` (sâu 1) > `docker/*Dockerfile*` > `{apps,services,packages}/*/Dockerfile` (sâu 2); trong cùng nhóm theo thứ tự chữ."""
    found: list[tuple[int, str]] = []
    if (root / "Dockerfile").is_file():
        found.append((0, "Dockerfile"))
    for child in _subdirs(root):
        if (child / "Dockerfile").is_file():
            found.append((1, f"{child.name}/Dockerfile"))
    docker_dir = root / "docker"
    if docker_dir.is_dir():
        for path in sorted(docker_dir.iterdir()):
            if path.is_file() and "dockerfile" in path.name.lower():
                found.append((2, f"docker/{path.name}"))
    for parent in MONOREPO_PARENTS:
        for child in _subdirs(root / parent):
            if (child / "Dockerfile").is_file():
                found.append((3, f"{parent}/{child.name}/Dockerfile"))
    return [name for _, name in sorted(dict.fromkeys(found))]


def _subdirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith("."))


def _name_tokens(dockerfile: str) -> set[str]:
    """Các từ trong tên thư mục và tên file (bỏ "dockerfile"): `apps/web-ui/Dockerfile` -> {apps, web, ui}."""
    parts = dockerfile.lower().split("/")
    parts[-1] = parts[-1].replace("dockerfile", "")
    return {token for part in parts for token in re.split(r"[-_.]", part) if token}


def _web_hints(root: Path, dockerfile: str) -> list[str]:
    """Dấu hiệu CÓ THỂ không phải API. Chỉ để xếp hạng và để VERIFY; không đủ để kết luận nó là web."""
    hints = [f"tên '{token}' trong đường dẫn" for token in sorted(_name_tokens(dockerfile) & WEB_NAMES)]
    text = _read(root / dockerfile)
    base = _WEB_BASE.search(text)
    if base:
        hints.append(f"FROM {base.group(1).lower()}")
    command = _WEB_CMD.search(text)
    if command:
        hints.append(f"lệnh '{command.group().lower()}'")
    return hints


def _rank_key(root: Path, dockerfile: str) -> tuple:
    """(có dấu hiệu web, nhóm vị trí, không có tên api|server|backend, tên). Chỉ sắp xếp gợi ý; thứ tự chữ là tiebreak cuối."""
    parts = dockerfile.split("/")
    group = 0 if len(parts) == 1 else 2 if parts[0] == "docker" else 1 if len(parts) == 2 else 3
    return (bool(_web_hints(root, dockerfile)), group, not (_name_tokens(dockerfile) & API_NAMES), dockerfile)


_RANK_RULE = ("gợi ý xếp hạng, chưa xác nhận: ứng viên có dấu hiệu web xếp sau; rồi vị trí gốc > nông hơn > docker/ > sâu 2 cấp; "
              "rồi tên có api|server|backend; rồi thứ tự chữ")


def _scan_dockerfile(root: Path, flag: str | None) -> Finding:
    if flag:
        if not (root / flag).is_file():
            raise ScanError(f"--sut-dockerfile {flag!r} không tồn tại trong repo")
        return _flag(flag)
    found = _dockerfile_candidates(root)
    if not found:
        raise ScanError("không thấy Dockerfile của API (thử: Dockerfile, */Dockerfile, docker/*Dockerfile*, {apps,services,packages}/*/Dockerfile). "
                        "Chỉ đường dẫn bằng --sut-dockerfile PATH (và --sut-context DIR nếu context không phải gốc repo)")
    candidates = sorted(found, key=lambda name: _rank_key(root, name))
    chosen = candidates[0]
    web = {name: _web_hints(root, name) for name in candidates}
    seen = "; ".join(f"{name} ({', '.join(hints)})" for name, hints in web.items() if hints)
    if len(candidates) > 1:
        verify = _rank_note(_RANK_RULE + (f". Dấu hiệu web: {seen}" if seen else ""), chosen, candidates)
    elif web[chosen]:
        verify = (f"chưa chắc {chosen} là Dockerfile của API: dấu hiệu có thể là service khác ({', '.join(web[chosen])}); "
                  f"xác nhận hoặc đặt --sut-dockerfile PATH")
    else:
        verify = None
    return Finding(chosen, "detected", tuple(candidates), _RANK_RULE, verify)


def _scan_context(root: Path, dockerfile: str, flag: str | None) -> tuple[Finding, dockerfile_copy.ContextAnalysis, list[str]]:
    """Context build. `--sut-context` do người đặt thì không VERIFY, chỉ CẢNH BÁO khi nguồn COPY/ADD không tồn tại tính từ đó."""
    if flag:
        analysis = dockerfile_copy.analyze(root, dockerfile, [flag])
        check = analysis.check(flag)
        warnings = [f"--sut-context {flag}: {dockerfile} dòng {item.source.line} {item.source.instruction} '{item.source.raw}' không tồn tại tính từ context này "
                    f"({item.repo_path})" for item in check.missing]
        return _flag(flag), analysis, warnings
    analysis = dockerfile_copy.analyze(root, dockerfile)
    reason = "mọi nguồn COPY/ADD tồn tại ở đúng một context" if not analysis.verify else "gợi ý, chưa xác nhận"
    return Finding(analysis.chosen, "detected", tuple(c.context for c in analysis.checks), reason, analysis.verify), analysis, []


def _context_for(dockerfile: str) -> str:
    return dockerfile_copy.default_context(dockerfile)


def _scan_port(root: Path, dockerfile: str, flag: str | None) -> Finding:
    if flag:
        return _flag(str(flag))
    text = _read(root / dockerfile)
    exposed = list(dict.fromkeys(_EXPOSE.findall(text)))
    if exposed:
        return Finding(exposed[0], "detected", tuple(exposed), "EXPOSE đầu tiên trong Dockerfile",
                       _rank_note("EXPOSE đầu tiên", exposed[0], exposed))
    from_cmd = _CMD_PORT.findall(text)
    if from_cmd:
        return Finding(from_cmd[0], "detected", tuple(dict.fromkeys(from_cmd)), "--port trong CMD")
    return Finding(DEFAULT_PORT, "default", (), "không thấy EXPOSE/--port",
                   f"cổng mặc định {DEFAULT_PORT}: không thấy EXPOSE hoặc --port trong {dockerfile}")


def _python_files(root: Path):
    for rel, files in walk(root):
        for name in files:
            if name.endswith(".py"):
                yield rel / name


def _rank_health(paths: set[str]) -> list[str]:
    known = [p for p in HEALTH_ORDER if p in paths]
    return known + sorted(paths - set(known))


def _health_routes(root: Path, scope: list[str] | None) -> dict[str, list[str]]:
    """{route health/ready: [file .py chứa nó]}. `scope` = các thư mục tương đối gốc repo; None = cả repo."""
    routes: dict[str, set[str]] = {}
    for rel in _python_files(root):
        posix = rel.as_posix()
        if scope is not None and not any(posix.startswith(directory + "/") for directory in scope):
            continue
        for route in _HEALTH_ROUTE.findall(_read(root / rel)):
            if re.search(r"health|ready", route, re.I):
                routes.setdefault(route, set()).add(posix)
    return {route: sorted(files) for route, files in routes.items()}


def _describe_route(route: str, files: list[str]) -> str:
    shown = ", ".join(files[:2]) + (f", +{len(files) - 2} file" if len(files) > 2 else "")
    return f"{route} ({shown})"


def _single_service(root: Path) -> bool:
    """E3 (hẹp): repo trông như MỘT service. Cần cả ba, thiếu một là không đủ bằng chứng: chỉ một file Dockerfile* trong repo; chỉ một thư mục gốc dự án Python
    (pyproject.toml | setup.py | requirements*.txt); chỉ một file khởi tạo web app (FastAPI|Flask|Starlette|Sanic|Quart)."""
    dockerfiles, project_dirs, app_files = 0, set(), 0
    for rel, files in walk(root):
        for name in files:
            lower = name.lower()
            if lower == "dockerfile" or lower.startswith("dockerfile.") or lower.endswith(".dockerfile"):
                dockerfiles += 1
            if name in ("pyproject.toml", "setup.py") or re.fullmatch(r"requirements[\w.-]*\.txt", name):
                project_dirs.add(rel.as_posix())
            if name.endswith(".py") and _APP_CTOR.search(_read(root / rel / name)):
                app_files += 1
    return dockerfiles == 1 and len(project_dirs) <= 1 and app_files <= 1


def _health_scope(root: Path, dockerfile: str, copy: dockerfile_copy.ContextAnalysis | None, context: Finding | None) -> tuple[str, list[str], str]:
    """(loại, thư mục phạm vi, mô tả bằng chứng). Loại: `scope` (E1/E2: các thư mục mà COPY/ADD đưa vào image), `repo` (E3: cả repo), `unproven`.
    Vị trí Dockerfile KHÔNG BAO GIỜ đủ một mình. E1/E2 chỉ dùng khi context đã chốt không VERIFY: context chỉ để phân giải đường dẫn nguồn."""
    if copy is not None and context is not None and context.verify is None:
        check = copy.chosen_check
        dirs = {item.repo_path: item for item in check.resolved
                if item.kind == "dir" and item.source.kind == "path" and item.repo_path not in (None, ".")}   # thư mục cụ thể, không phải tệp/`.`/glob
        evidence = [f"E2 {item.source.instruction} {item.source.raw} -> {path}" for path, item in sorted(dirs.items())]
        parts = dockerfile.split("/")
        if len(parts) >= 2 and parts[0] != "docker":   # E1: thư mục chứa Dockerfile, có đối chiếu
            folder = "/".join(parts[:-1])
            inside = [path for path in dirs if path == folder or path.startswith(folder + "/")]
            copies_dot = check.context == folder and any(item.kind == "context" for item in check.resolved)
            if inside or copies_dot:
                dirs.setdefault(folder, None)
                evidence.insert(0, f"E1 {folder} ({'COPY . . với context đúng bằng thư mục này' if copies_dot and not inside else 'có nguồn COPY/ADD là thư mục trong đó'})")
        if dirs:
            return "scope", sorted(dirs), "; ".join(evidence)
    if _single_service(root):
        return "repo", [], "E3 một Dockerfile, một gốc dự án Python, một file khởi tạo web app"
    why = ("context chưa chốt nên không dùng COPY/ADD làm bằng chứng" if context is not None and context.verify
           else "COPY/ADD không có thư mục cụ thể nào làm bằng chứng mã API")
    return "unproven", [], why


def _scan_health(root: Path, flag: str | None, dockerfile: str | None = None, copy: dockerfile_copy.ContextAnalysis | None = None,
                 context: Finding | None = None) -> Finding:
    """Health path. Flag thắng. Còn lại chỉ đọc `.py` trong phạm vi mã API đã CHỨNG MINH (xem `_health_scope`); chưa chứng minh thì `/` TẠM + VERIFY."""
    if flag:
        return _flag(flag)
    kind, dirs, evidence = _health_scope(root, dockerfile, copy, context) if dockerfile else ("repo", [], "không có Dockerfile (--no-api)")
    everywhere = _health_routes(root, None)
    rule = "ưu tiên /api/health > /health > /healthz > còn lại theo thứ tự chữ"
    if kind == "unproven":
        if not everywhere:
            return Finding(DEFAULT_HEALTH, "default", (), "không thấy route health/ready: readiness của workflow nhận mọi mã HTTP")
        listed = tuple(_describe_route(route, everywhere[route]) for route in _rank_health(set(everywhere)))
        verify = (f"giá trị `{DEFAULT_HEALTH}` chỉ là TẠM, chưa chọn được health path của API ({evidence}). Route health thấy trong repo, chỉ để tham khảo "
                  f"(không biết cái nào thuộc API): {'; '.join(listed)}. Đặt --health-path hoặc sửa sut_health_path thành path trả 2xx thật rồi mới xoá dấu: "
                  f"readiness của workflow nhận mọi mã HTTP nhưng k6 smoke đòi 2xx, nên giữ `{DEFAULT_HEALTH}` mà API không trả 2xx ở đó vẫn làm k6 lỗi")
        return Finding(DEFAULT_HEALTH, "default", listed, f"chưa chứng minh phạm vi mã API: {evidence}", verify)
    routes = everywhere if kind == "repo" else _health_routes(root, dirs)
    basis = f"{evidence}" + (f"; phạm vi {', '.join(dirs)}" if dirs else "")
    if not routes:
        return Finding(DEFAULT_HEALTH, "default", (), f"không thấy route health/ready trong phạm vi ({basis}): readiness của workflow nhận mọi mã HTTP")
    ranked = _rank_health(set(routes))
    listed = tuple(_describe_route(route, routes[route]) for route in ranked)
    verify = f"chọn {ranked[0]} trong [{', '.join(listed)}] vì gợi ý xếp hạng, chưa xác nhận: {rule}" if len(ranked) > 1 else None
    return Finding(ranked[0], "detected", listed, f"{rule}; bằng chứng phạm vi: {basis}", verify)


def find_db_refs(root: Path, dockerfile: str | None, copy: dockerfile_copy.ContextAnalysis | None, context: Finding | None) -> dict[str, list[str]]:
    """{biến DB: [file]} mà MÃ SUT tham chiếu (DATABASE_URL, ...), để cảnh báo khi workflow không khai DB phụ. Cùng phạm vi mã API như health path
    (`_health_scope`): chứng minh được thư mục thì chỉ quét ở đó, còn lại quét cả repo (WARN chỉ là gợi ý, không quyết định gì)."""
    kind, dirs, _ = _health_scope(root, dockerfile, copy, context) if dockerfile else ("repo", [], "")
    scope = dirs if kind == "scope" else None
    found: dict[str, set[str]] = {}
    for rel, files in walk(root):
        for name in files:
            if not _DB_FILE.fullmatch(name) or _TEST_FILE.fullmatch(name):
                continue
            posix = (rel / name).as_posix()
            if scope is not None and not any(posix.startswith(directory + "/") for directory in scope):
                continue
            for var in set(_DB_REF.findall(_read(root / rel / name))):
                found.setdefault(var, set()).add(posix)
    return {var: sorted(files) for var, files in sorted(found.items())}


def db_warning(refs: dict[str, list[str]]) -> str:
    shown = "; ".join(f"{var} ({', '.join(files[:2])}{f', +{len(files) - 2} file' if len(files) > 2 else ''})" for var, files in refs.items())
    return (f"mã SUT tham chiếu biến DB: {shown}. Workflow chỉ chạy MỘT container SUT với sut_env nên SUT cần database sẽ không qua health check. "
            "Khai DB phụ trong qc.yml (sut_db_image ghim digest + sut_db_ready_cmd; secret SUT_SECRET_ENV, SUT_DB_SECRET_ENV) hoặc sut_base_url nếu đã có môi trường sẵn; "
            "bỏ qua nếu SUT có chế độ chạy không cần DB (docs/usage-ci.md, mục \"SUT cần database\")")


def _deps_mention_fastapi(root: Path) -> bool:
    for rel, files in walk(root):
        for name in files:
            if name == "pyproject.toml" or re.fullmatch(r"requirements[\w.-]*\.txt", name):
                if re.search(r"\bfastapi\b", _read(root / rel / name), re.I):
                    return True
    return False


def _scan_openapi(root: Path, fastapi: bool) -> Finding | None:
    if not fastapi:
        return None
    custom = sorted({m for rel in _python_files(root) for m in _OPENAPI_URL.findall(_read(root / rel))})
    if custom:
        return Finding(custom[0], "detected", tuple(custom), "openapi_url= trong code", _rank_note("thứ tự chữ", custom[0], custom))
    return Finding("/openapi.json", "detected", ("/openapi.json",), "FastAPI có sẵn /openapi.json")


def _cors_rank(name: str):
    websocket = bool(re.search(r"SOCKETIO|(?:^|_)WS(?:_|$)", name))
    return (websocket, len(name), name)


def _scan_cors(root: Path) -> Finding | None:
    names: set[str] = set()
    for rel, files in walk(root):
        for name in files:
            path = root / rel / name
            if name.endswith(".py"):
                names.update(_CORS_ENV_PY.findall(_read(path)))
            elif name in ENV_EXAMPLES:
                names.update(_CORS_ENV_FILE.findall(_read(path)))
    if not names:
        return None
    ranked = sorted(names, key=_cors_rank)
    rule = "tên không chứa SOCKETIO/WS trước, rồi tên ngắn hơn"
    return Finding(ranked[0], "detected", tuple(ranked), rule, _rank_note(rule, ranked[0], ranked))


# ---------- UI ----------

def _package_json(path: Path) -> dict | None:
    try:
        data = json.loads(_read(path))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _ui_kind(root: Path, rel: Path, package: dict) -> str | None:
    deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {}), **(package.get("peerDependencies") or {})}
    if "next" in deps:
        for name in ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs"):
            if _NEXT_EXPORT.search(_read(root / rel / name)):
                return "next-export"
        return "next-ssr"
    if "vite" in deps:
        return "vite"
    if "react-scripts" in deps:
        return "cra"
    return None


def _lockfile_in(directory: Path) -> tuple[str, str] | None:
    return next(((name, pm) for name, pm in LOCKFILES if (directory / name).is_file()), None)


def _node_major(root: Path, rel: Path, package: dict) -> Finding:
    engines = (package.get("engines") or {}).get("node") if isinstance(package.get("engines"), dict) else None
    for source, text in (("engines.node", str(engines) if engines else ""), (".nvmrc", _read(root / rel / ".nvmrc") or _read(root / ".nvmrc"))):
        match = _INT.search(text)
        if match and int(match.group()) >= 14:
            return Finding(int(match.group()), "detected", (int(match.group()),), source)
    return Finding(DEFAULT_NODE, "default", (), "không có engines.node/.nvmrc")


def _api_var(root: Path, rel: Path, kind: str) -> Finding | None:
    prefix = UI_ENV_PREFIX[kind]
    pattern = re.compile(r"(?:import\.meta\.env|process\.env)\.(" + re.escape(prefix) + r"[A-Z0-9_]+)")
    names: set[str] = set()
    ui_root = root / rel
    for sub, files in walk(ui_root):
        for name in files:
            if name.endswith(UI_SUFFIXES):
                names.update(n for n in pattern.findall(_read(ui_root / sub / name)) if re.search(r"API|BACKEND|BASE_URL", n))
    if not names:
        return None
    ranked = sorted(names, key=lambda n: ("API_URL" not in n, len(n), n))
    rule = "tên chứa API_URL trước, rồi tên ngắn hơn"
    return Finding(ranked[0], "detected", tuple(ranked), rule, _rank_note(rule, ranked[0], ranked))


def _scan_ui(root: Path, result: ScanResult) -> None:
    found: list[tuple[Path, str, dict]] = []
    for rel, files in walk(root):
        if "package.json" in files:
            package = _package_json(root / rel / "package.json")
            kind = _ui_kind(root, rel, package) if package else None
            if kind:
                found.append((rel, kind, package))
    if not found:
        result.notes.append("không thấy UI (package.json có vite/react-scripts/next): không sinh suite ui-explore")
        return
    found.sort(key=lambda item: (len(item[0].parts), not (root / item[0] / "src").is_dir(), item[0].as_posix()))
    rel, kind, package = found[0]
    directory = _posix(rel)
    names = [_posix(item[0]) for item in found]
    rule = "ưu tiên thư mục nông hơn, rồi thư mục có src/"
    directory_finding = Finding(directory, "detected", tuple(names), rule, _rank_note(rule, directory, names))
    if kind == "next-ssr":
        result.ui_refused = (f"UI ở {directory} là Next.js SSR (không phải output: 'export'): không tự sinh Dockerfile.ui, "
                             f"hãy tự viết Dockerfile và truyền --ui-dockerfile PATH")
        return
    lock = _lockfile_in(root / rel)
    if lock is None:
        anywhere = _lockfile_in(root)
        result.ui_refused = (f"lockfile không nằm trong {directory} ({'workspace monorepo: lockfile ở gốc repo' if anywhere else 'không có lockfile'}): "
                             f"build không tất định nên không tự sinh Dockerfile.ui, hãy dùng --ui-dockerfile PATH")
        return
    result.ui = UiScan(directory=directory, kind=kind, output_dir=UI_OUTPUT[kind], package_manager=lock[1], lockfile=lock[0],
                       node_major=_node_major(root, rel, package), api_var=_api_var(root, rel, kind), directory_finding=directory_finding)


# ---------- điểm vào ----------

@dataclass(frozen=True)
class Overrides:
    dockerfile: str | None = None
    context: str | None = None
    port: str | None = None
    health_path: str | None = None


def scan(root, overrides: Overrides | None = None, *, api: bool = True) -> ScanResult:
    """Quét repo. `api=False` (--no-api): bỏ phần API, chỉ quét UI. Lỗi duy nhất: không thấy Dockerfile API mà không có flag (ScanError)."""
    root = Path(root)
    overrides = overrides or Overrides()
    result = ScanResult()
    if api:
        result.dockerfile = _scan_dockerfile(root, overrides.dockerfile)
        result.context_finding, result.copy, result.warnings = _scan_context(root, result.dockerfile.value, overrides.context)
        result.context = result.context_finding.value
        result.port = _scan_port(root, result.dockerfile.value, overrides.port)
        result.health_path = _scan_health(root, overrides.health_path, result.dockerfile.value, result.copy, result.context_finding)
        result.fastapi = _deps_mention_fastapi(root)
        result.openapi_path = _scan_openapi(root, result.fastapi)
        result.cors_env = _scan_cors(root)
        result.db_refs = find_db_refs(root, result.dockerfile.value, result.copy, result.context_finding)
    _scan_ui(root, result)
    return result
