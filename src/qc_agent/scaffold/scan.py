"""Scanner TẤT ĐỊNH của `qc-agent init` (bước 32): đọc cây thư mục repo SUT và đề xuất Dockerfile API, cổng, health path, biến CORS, thư mục UI...

Hàm thuần, chỉ ĐỌC file: không mạng, không chạy code SUT, không LLM. Mỗi giá trị trả về là một `Finding` (giá trị, nguồn flag|detected|default, các ứng viên,
lý do). Luật: có flag thì dùng flag và không kèm VERIFY; gặp >= 2 ứng viên thì chọn theo luật ưu tiên cố định và đặt `verify` để `init` ghi
`# qc-agent:todo VERIFY: chọn X trong [X, Y] vì ...` (`validate` chặn tới khi người xác nhận). Dương tính giả CHỈ MỘT ứng viên thì không có VERIFY (giới hạn đã biết).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

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

_HEALTH_ROUTE = re.compile(r"""@\w+(?:\.\w+)*\.(?:get|head|api_route)\(\s*['"](/[^'"{}]*)['"]""")
_CORS_ENV_PY = re.compile(r"""(?:getenv|environ(?:\.get)?)\s*[(\[]\s*['"]([A-Z0-9_]*CORS[A-Z0-9_]*)['"]""")
_CORS_ENV_FILE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]*CORS[A-Z0-9_]*)\s*=", re.M)
_OPENAPI_URL = re.compile(r"""openapi_url\s*=\s*['"](/[^'"]*)['"]""")
_EXPOSE = re.compile(r"^\s*EXPOSE\s+(\d{2,5})(?:/\w+)?", re.M | re.I)
_CMD_PORT = re.compile(r"""--port["',\s=]+(\d{2,5})""")
_NEXT_EXPORT = re.compile(r"""output\s*:\s*['"]export['"]""")
_INT = re.compile(r"\d+")


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
    port: Finding = field(default_factory=lambda: Finding(DEFAULT_PORT, "default"))
    health_path: Finding = field(default_factory=lambda: Finding(DEFAULT_HEALTH, "default"))
    openapi_path: Finding | None = None       # None: không thấy FastAPI (api-contract vẫn sinh, với REFINE)
    fastapi: bool = False
    cors_env: Finding | None = None
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
    """Dockerfile ở gốc > `*/Dockerfile` (sâu 1) > `docker/*Dockerfile*`; trong cùng nhóm thì theo thứ tự chữ."""
    found: list[tuple[int, str]] = []
    if (root / "Dockerfile").is_file():
        found.append((0, "Dockerfile"))
    for child in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")) if root.is_dir() else []:
        if (child / "Dockerfile").is_file():
            found.append((1, f"{child.name}/Dockerfile"))
    docker_dir = root / "docker"
    if docker_dir.is_dir():
        for path in sorted(docker_dir.iterdir()):
            if path.is_file() and "dockerfile" in path.name.lower():
                found.append((2, f"docker/{path.name}"))
    return [name for _, name in sorted(dict.fromkeys(found))]


def _scan_dockerfile(root: Path, flag: str | None) -> tuple[Finding, str]:
    if flag:
        if not (root / flag).is_file():
            raise ScanError(f"--sut-dockerfile {flag!r} không tồn tại trong repo")
        return _flag(flag), _context_for(flag)
    candidates = _dockerfile_candidates(root)
    if not candidates:
        raise ScanError("không thấy Dockerfile của API (thử: Dockerfile, */Dockerfile, docker/*Dockerfile*). "
                        "Chỉ đường dẫn bằng --sut-dockerfile PATH (và --sut-context DIR nếu context không phải gốc repo)")
    chosen = candidates[0]
    return (Finding(chosen, "detected", tuple(candidates), "ưu tiên Dockerfile ở gốc, rồi nông hơn, rồi theo thứ tự chữ",
                    _rank_note("ưu tiên Dockerfile ở gốc > nông hơn > thứ tự chữ", chosen, candidates)), _context_for(chosen))


def _context_for(dockerfile: str) -> str:
    parts = dockerfile.split("/")
    return parts[0] if len(parts) == 2 and parts[0] != "docker" else "."


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


def _scan_health(root: Path, flag: str | None) -> Finding:
    if flag:
        return _flag(flag)
    paths: set[str] = set()
    for rel in _python_files(root):
        for route in _HEALTH_ROUTE.findall(_read(root / rel)):
            if re.search(r"health|ready", route, re.I):
                paths.add(route)
    ranked = _rank_health(paths)
    if not ranked:
        return Finding(DEFAULT_HEALTH, "default", (), "không thấy route health/ready: readiness của workflow nhận mọi mã HTTP")
    rule = "ưu tiên /api/health > /health > /healthz > còn lại theo thứ tự chữ"
    return Finding(ranked[0], "detected", tuple(ranked), rule, _rank_note(rule, ranked[0], ranked))


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
    port: str | None = None
    health_path: str | None = None


def scan(root, overrides: Overrides | None = None, *, api: bool = True) -> ScanResult:
    """Quét repo. `api=False` (--no-api): bỏ phần API, chỉ quét UI. Lỗi duy nhất: không thấy Dockerfile API mà không có flag (ScanError)."""
    root = Path(root)
    overrides = overrides or Overrides()
    result = ScanResult()
    if api:
        result.dockerfile, result.context = _scan_dockerfile(root, overrides.dockerfile)
        result.port = _scan_port(root, result.dockerfile.value, overrides.port)
        result.health_path = _scan_health(root, overrides.health_path)
        result.fastapi = _deps_mention_fastapi(root)
        result.openapi_path = _scan_openapi(root, result.fastapi)
        result.cors_env = _scan_cors(root)
    _scan_ui(root, result)
    return result
