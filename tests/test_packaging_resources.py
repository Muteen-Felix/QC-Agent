"""Gói qc-agent phải mang đủ tài nguyên ngoài `.py` mà code đọc lúc chạy (S4-07): prompt của GT/Selector, mẫu `scaffold/tmpl`, script `dom_labels.mjs`, migration Alembic, schema,
manifest worker, policy, web. Thiếu một file là lỗi chỉ lộ ở CI (vd thiếu `gt_agent.md` làm `gt generate --agent` hỏng).

Ba lớp, rẻ → đắt:
  1. Cây nguồn: mọi file không-.py dưới src/qc_agent phải được khai báo ở đây (thêm tài nguyên mới mà quên khai báo => đỏ), và mọi `*.tmpl` code nhắc tới phải tồn tại.
  2. `.dockerignore`: bộ khớp mô phỏng luật của Docker; không tài nguyên nào bị loại khỏi ngữ cảnh build. (Mô phỏng, không phải Docker thật: tests/test_packaging_image.py kiểm image thật.)
  3. Wheel: `uv build --wheel`, mở zip, rồi cài vào venv tạm và đọc từng file bằng importlib.resources từ bản ĐÃ CÀI (không dùng đường dẫn nguồn). SKIP (= CHƯA KIỂM CHỨNG, không phải xanh)
     CHỈ khi thiếu `uv` hoặc uv báo rõ KHÔNG LẤY ĐƯỢC build requirement (offline/mạng đứt, xem `build_requirement_unavailable`); mọi lỗi khác (cấu hình pyproject, backend hatchling,
     đóng gói thiếu file, venv/cài đặt) làm test FAIL.
Image: tests/test_packaging_image.py."""
import fnmatch
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "qc_agent"
# File không-.py dưới src/qc_agent (đường dẫn trong gói). Khai báo TƯỜNG MINH để thêm tài nguyên mới phải đi qua đây.
DECLARED = {
    "groundtruth/prompts/gt_generate.md", "groundtruth/prompts/gt_agent.md", "selector/prompts/diff_select.md",
    "scaffold/dom_labels.mjs", "jobs/migrations/script.py.mako",
    *{f"scaffold/tmpl/{p.name}" for p in (SRC / "scaffold" / "tmpl").glob("*.tmpl")},
}
# Phải có trong gói (module .py nhưng là dữ liệu tài nguyên với S4: bảng giá).
REQUIRED_MODULES = {"llm/prices.py", "llm/client.py", "llm/agent_loop.py"}
# Thư mục ở gốc repo được `force-include` vào wheel (pyproject.toml) và `COPY` vào image.
FORCE_INCLUDED = {"schemas": "qc_agent/schemas", "workers": "qc_agent/workers", "configs": "qc_agent/configs", "web": "qc_agent/web"}
REQUIRED_SCHEMAS = {"ground_truth.json", "module_map.json", "selection.json", "capabilities.json", "task_spec.json", "result.json", "CONTRACT.lock"}


def _nonpy_files() -> set[str]:
    return {p.relative_to(SRC).as_posix() for p in SRC.rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts}


def _repo_files(directory: str) -> set[str]:
    return {p.relative_to(ROOT / directory).as_posix() for p in (ROOT / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts}


# ───────────────────────── 1. cây nguồn ─────────────────────────

def test_every_non_python_file_under_the_package_is_declared_here():
    assert _nonpy_files() == DECLARED, {"chưa khai báo": sorted(_nonpy_files() - DECLARED), "khai báo mà không có": sorted(DECLARED - _nonpy_files())}


def test_the_required_prompts_and_the_agent_prompt_in_particular_exist_and_are_not_empty():
    for name in ("groundtruth/prompts/gt_generate.md", "groundtruth/prompts/gt_agent.md", "selector/prompts/diff_select.md"):
        assert (SRC / name).stat().st_size > 200, name       # gt_agent.md thiếu thì `gt generate --agent` hỏng trong CI


def test_every_template_the_code_names_exists():
    named = set()
    for path in SRC.rglob("*.py"):
        named |= set(re.findall(r'"([A-Za-z0-9._-]+\.tmpl)"', path.read_text(encoding="utf-8")))
    assert named, "không thấy tên template nào: bộ quét hỏng"
    assert {f"scaffold/tmpl/{name}" for name in named} <= DECLARED, sorted(named)


def test_the_resources_are_read_through_the_package_not_through_a_source_checkout_path():
    """Mẫu đọc phải đi qua `importlib.resources` hoặc `Path(__file__)` trong gói (đi theo gói khi cài), không bằng đường dẫn tương đối từ cwd hay `ROOT`."""
    from importlib import resources
    for name in sorted(DECLARED):
        assert resources.files("qc_agent").joinpath(*name.split("/")).is_file(), name


def test_the_schemas_the_code_loads_are_reachable_through_settings():
    from qc_agent import settings
    directory = settings.get().resolved_schemas_dir
    assert {p.name for p in directory.iterdir()} >= REQUIRED_SCHEMAS


def test_the_price_table_module_is_part_of_the_package():
    for name in REQUIRED_MODULES:
        assert (SRC / name).is_file(), name


# ───────────────────────── 2. .dockerignore ─────────────────────────

def _dockerignore_patterns() -> list[str]:
    return [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _segment(part: str) -> str:
    return re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")


def _pattern_regex(pattern: str) -> re.Pattern:
    """Luật khớp của Docker: so với đường dẫn tương đối gốc context; `*`/`?` không qua `/`; `**` khớp nhiều tầng; khớp một thư mục thì loại cả cây dưới nó."""
    parts = [part for part in pattern.strip("/").split("/") if part]
    regex = ""
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            regex += "(?:.*/)?" if not last else ".*"
        else:
            regex += _segment(part) + ("" if last else "/")
    return re.compile("^" + regex + "(?:/.*)?$")


def dockerignored(path: str, patterns: list[str]) -> bool:
    excluded = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        if _pattern_regex(pattern[1:] if negate else pattern).match(path):
            excluded = not negate
    return excluded


def test_the_dockerignore_matcher_follows_the_documented_rules():
    patterns = ["docs", "*.md", "!package.json", "**/*.tmp", "src/*/gen"]
    assert dockerignored("README.md", patterns)                             # `*.md` chỉ khớp ở GỐC context
    assert not dockerignored("src/qc_agent/groundtruth/prompts/gt_agent.md", patterns)
    assert dockerignored("docs/a/b.txt", patterns) and dockerignored("docs", patterns)    # khớp thư mục thì loại cả cây
    assert not dockerignored("package.json", patterns) and dockerignored("a/b/c.tmp", patterns) and dockerignored("x.tmp", patterns)
    assert dockerignored("src/qc_agent/gen/x.py", patterns) and not dockerignored("src/qc_agent/genx/x.py", patterns)
    assert dockerignored("src/a/gen", patterns) and not dockerignored("src/gen", patterns)


def test_no_declared_resource_is_dropped_from_the_docker_build_context():
    patterns = _dockerignore_patterns()
    assert any(p == "*.md" for p in patterns), "bài kiểm này tồn tại vì `*.md` trong .dockerignore: nếu đã bỏ thì xoá nhận xét này"
    for name in sorted(DECLARED | REQUIRED_MODULES):
        assert not dockerignored(f"src/qc_agent/{name}", patterns), f"{name} bị .dockerignore loại: image thiếu file"
    for directory in FORCE_INCLUDED:
        for name in sorted(_repo_files(directory)):
            assert not dockerignored(f"{directory}/{name}", patterns), f"{directory}/{name} bị .dockerignore loại"
    for name in ("pyproject.toml", "uv.lock", "package.json", "package-lock.json", "docker/npx", "docker/semgrep.lock"):
        assert not dockerignored(name, patterns), name


def test_every_directory_the_dockerfile_copies_is_not_ignored_wholesale():
    patterns = _dockerignore_patterns()
    copied = set(re.findall(r"^COPY (?!--)([A-Za-z0-9._/-]+) ", (ROOT / "Dockerfile").read_text(encoding="utf-8"), flags=re.M))
    assert {"src", "schemas", "workers", "configs", "web", "rules/semgrep"} <= copied, copied
    for source in sorted(copied):
        if (ROOT / source).is_dir():
            assert not dockerignored(source, patterns), f"COPY {source}: thư mục bị loại hoàn toàn khỏi ngữ cảnh"


# ───────────────────────── 3. wheel ─────────────────────────

# Mẫu thông báo THẬT của uv 0.12 (đã chụp ngày 2026-10-07 bằng cache rỗng + --offline, proxy chết, và pyproject hỏng). Cắt bớt, giữ nguyên các câu phân loại.
UV_OFFLINE = """error: Failed to build `D:/qc-agent`
  cause: Failed to resolve requirements from `build-system.requires`
  cause: No solution found when resolving: `hatchling>=1.25`
  cause: Because hatchling was not found in the cache and you require hatchling>=1.25, we can conclude that your requirements are unsatisfiable.

hint: Packages were unavailable because the network was disabled. When the network is disabled, registry packages may only be read from the cache."""
UV_NETWORK_DOWN = """error: Failed to build `D:/qc-agent`
  cause: Failed to resolve requirements from `build-system.requires`
  cause: No solution found when resolving: `hatchling>=1.25`
  cause: Request failed after 3 retries in 17.5s
  cause: Failed to fetch: `https://pypi.org/simple/hatchling/`
  cause: error sending request for url (https://pypi.org/simple/hatchling/)
  cause: tcp connect error"""
UV_BACKEND_ERROR = """Traceback (most recent call last):
  File "hatchling/builders/plugin/interface.py", line 248, in recurse_forced_files
    raise FileNotFoundError(msg)
FileNotFoundError: Forced include not found: D:/qc-agent/configs
error: Failed to build `D:/qc-agent`
  cause: The build backend returned an error
  cause: Call to `hatchling.build.build_wheel` failed (exit code: 1)"""
NETWORK_WORDS = ("network was disabled", "Failed to fetch", "error sending request", "dns error", "tcp connect error", "Request failed after", "certificate", "timed out")


def build_requirement_unavailable(stderr: str) -> bool:
    """Bằng chứng RÕ RÀNG rằng uv không lấy được build requirement (hatchling) vì offline/mạng: có câu 'Failed to resolve requirements from `build-system.requires`' VÀ một dấu vết mạng,
    VÀ backend chưa từng chạy. Thiếu một trong ba => KHÔNG phải lỗi môi trường, test phải fail."""
    return ("Failed to resolve requirements from `build-system.requires`" in stderr and any(word in stderr for word in NETWORK_WORDS)
            and "The build backend returned an error" not in stderr and "Traceback" not in stderr)


def test_only_a_missing_build_requirement_is_treated_as_an_environment_problem():
    assert build_requirement_unavailable(UV_OFFLINE) and build_requirement_unavailable(UV_NETWORK_DOWN)
    assert not build_requirement_unavailable(UV_BACKEND_ERROR), "lỗi backend/cấu hình phải làm test fail, không được SKIP"
    assert not build_requirement_unavailable("error: Failed to build `x`\n  cause: Failed to fetch: `https://pypi.org/simple/other/`"), "lỗi mạng không phải của build requirement"
    assert not build_requirement_unavailable("error: invalid pyproject.toml: unknown field `packagess`"), "lỗi cấu hình"
    assert not build_requirement_unavailable("")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("không có `uv` trong PATH: không dựng được wheel (SKIP = CHƯA KIỂM CHỨNG)")
    out = tmp_path_factory.mktemp("wheel")
    done = subprocess.run([uv, "build", "--wheel", "--out-dir", str(out)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if done.returncode != 0:
        if build_requirement_unavailable(done.stderr):
            pytest.skip(f"uv không lấy được build requirement (offline/mạng): {done.stderr.strip()[-300:]} (SKIP = CHƯA KIỂM CHỨNG)")
        pytest.fail(f"`uv build --wheel` lỗi (exit {done.returncode}), không phải do thiếu build requirement; kiểm pyproject.toml / backend / thư mục force-include:\n{done.stderr.strip()[-1500:]}",
                    pytrace=False)
    found = sorted(out.glob("qc_agent-*.whl"))
    assert len(found) == 1, found
    return found[0]


def test_the_wheel_contains_every_declared_resource_and_every_force_included_file(wheel):
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [f"qc_agent/{name}" for name in sorted(DECLARED | REQUIRED_MODULES) if f"qc_agent/{name}" not in names]
    for directory, target in FORCE_INCLUDED.items():
        missing += [f"{target}/{name}" for name in sorted(_repo_files(directory)) if f"{target}/{name}" not in names]
    assert not missing, missing
    assert {f"qc_agent/schemas/{name}" for name in REQUIRED_SCHEMAS} <= names


def test_the_wheel_has_no_file_the_source_tree_does_not_have(wheel):
    names = {n for n in zipfile.ZipFile(wheel).namelist() if n.startswith("qc_agent/") and not n.endswith("/")}
    expected = {f"qc_agent/{p.relative_to(SRC).as_posix()}" for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    for directory, target in FORCE_INCLUDED.items():
        expected |= {f"{target}/{name}" for name in _repo_files(directory)}
    assert names == expected, {"thừa": sorted(names - expected)[:10], "thiếu": sorted(expected - names)[:10]}


READ_INSTALLED = r"""
import json, sys
from importlib import resources
import qc_agent
root = resources.files("qc_agent")
sizes = {}
for name in json.loads(sys.argv[1]):
    node = root.joinpath(*name.split("/"))
    sizes[name] = len(node.read_bytes())
print(json.dumps({"file": qc_agent.__file__, "sizes": sizes}))
"""


def test_the_installed_wheel_reads_every_resource_through_importlib_resources(wheel, tmp_path):
    uv = shutil.which("uv")
    venv = tmp_path / "venv"
    made = subprocess.run([uv, "venv", "--python", sys.executable, str(venv)], capture_output=True, text=True, timeout=300)
    assert made.returncode == 0, f"`uv venv` lỗi (không cần mạng): {made.stderr.strip()[-500:]}"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    installed = subprocess.run([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)], capture_output=True, text=True, timeout=300)
    assert installed.returncode == 0, f"cài wheel (--no-deps, không cần mạng) lỗi: {installed.stderr.strip()[-500:]}"
    import json
    wanted = sorted(DECLARED | REQUIRED_MODULES | {f"schemas/{n}" for n in REQUIRED_SCHEMAS} | {"workers/semgrep.yaml", "configs/projects/_default.yaml", "web/index.html"})
    done = subprocess.run([str(python), "-I", "-c", READ_INSTALLED, json.dumps(wanted)], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert "site-packages" in result["file"] and not Path(result["file"]).resolve().is_relative_to(ROOT / "src"), result["file"]    # bản đã cài, không phải cây nguồn
    assert all(size > 0 for size in result["sizes"].values()), {k: v for k, v in result["sizes"].items() if not v}
