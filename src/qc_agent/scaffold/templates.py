"""Mẫu cấu hình đóng gói kèm qc-agent (dùng cho `qc-agent init`, bước 26): suite, script k6, flow Midscene, qc.yml, config project.

Không dùng Jinja: mẫu là file văn bản (`tmpl/*.tmpl`) với chỗ trống `{{tên}}`, và mỗi hàm dựng ở đây kiểm tra + trích giá trị an toàn cho
đúng ngữ cảnh (chuỗi YAML/JS được JSON-quote; đường dẫn/tên chỉ nhận ký tự cho phép) nên dữ liệu từ OpenAPI của SUT không thể chèn cấu trúc.
Một chỗ trống chiếm trọn một dòng mà giá trị rỗng thì cả dòng bị bỏ.

Chỗ cần người quyết định được đánh dấu bằng `TODO` (= `qc-agent:todo`); `qc-agent validate` (bước 27) từ chối file còn dấu này.
Các mẫu suite bám các suite đã kiểm chứng của SUT tham chiếu noteboard (tests/fixtures/sut/noteboard/.qc-agent/suites).
"""
from __future__ import annotations

import json
import re
from importlib import resources

TODO = "qc-agent:todo"
TODO_KINDS = ("VERIFY", "REFINE", "SUGGESTED")   # bốn dạng dấu: `todo` trơn + ba dạng này (validate hướng dẫn riêng cho từng dạng)
REFINE_BEGIN = "qc-agent:begin refine"           # vùng do `init --refine` (Pha 2) được viết lại; người xoá marker = vùng thuộc về người
REFINE_END = "qc-agent:end"
DEFAULT_QC_REPO = "Muteen-Felix/QC-Agent"
MIDSCENE_COMMANDS = ("aiAct", "aiTap", "aiAssert", "aiWaitFor")

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_WHOLE_LINE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]*")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_URL_PATH = re.compile(r"/[A-Za-z0-9/._~{}-]*")
_ROUTE_PATH = re.compile(r"/[A-Za-z0-9/._~-]*")  # đường dẫn thăm dò/nhúng vào scalar không quote: không cho `{}`
_FILE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9/._-]*")  # đường dẫn file tương đối trong repo SUT
_DURATION = re.compile(r"[1-9][0-9]{0,3}[smh]")
_SHA = re.compile(r"[0-9a-f]{40}")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}")
_ENV_LINE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^\r\n]*")
_BUILD_ARG = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^\r\n]*")


class TemplateError(ValueError):
    """Giá trị đưa vào mẫu không hợp lệ (hoặc mẫu và bộ giá trị lệch nhau)."""


def template_names() -> list[str]:
    return sorted(p.name for p in resources.files("qc_agent.scaffold").joinpath("tmpl").iterdir() if p.name.endswith(".tmpl"))


def _read(name: str) -> str:
    return resources.files("qc_agent.scaffold").joinpath("tmpl", name).read_text(encoding="utf-8")


def placeholders(name: str) -> set[str]:
    return set(_PLACEHOLDER.findall(_read(name)))


def render(name: str, values: dict[str, str]) -> str:
    """Điền mẫu. Bộ khoá của `values` phải khớp CHÍNH XÁC các chỗ trống của mẫu (thừa/thiếu đều là lỗi, không đoán)."""
    text = _read(name)
    wanted = set(_PLACEHOLDER.findall(text))
    if set(values) != wanted:
        raise TemplateError(f"{name}: chỗ trống {sorted(wanted)} không khớp giá trị {sorted(values)}")
    lines: list[str] = []
    for line in text.split("\n"):
        whole = _WHOLE_LINE.fullmatch(line)
        if whole:
            value = values[whole.group(1)]
            if value != "":
                lines.append(value)
            continue
        def substitute(match: re.Match) -> str:
            value = values[match.group(1)]
            if "\n" in value:
                raise TemplateError(f"{name}: chỗ trống '{match.group(1)}' nằm giữa dòng nên không được chứa xuống dòng")
            return value
        lines.append(_PLACEHOLDER.sub(substitute, line))
    return "\n".join(lines)


# ---------- trích giá trị an toàn ----------

def _q(value) -> str:
    """Chuỗi/số dạng JSON: hợp lệ cả trong YAML lẫn JavaScript, không thể thoát ra khỏi ngữ cảnh."""
    return json.dumps(value, ensure_ascii=False)


def _need(pattern: re.Pattern, value, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise TemplateError(f"{what} không hợp lệ: {value!r}")
    return value


def _rel_path(value, what: str) -> str:
    """Đường dẫn tương đối trong repo SUT: ký tự an toàn và KHÔNG có đoạn `..` (không thoát ra ngoài context/repo)."""
    _need(_FILE, value, what)
    if ".." in value.split("/"):
        raise TemplateError(f"{what} không được chứa '..': {value!r}")
    return value


def _comment(text: str) -> str:
    return " ".join(str(text).split())[:120].replace("#", "")


def _todo(text: str) -> str:
    return f"# {TODO} {text}"


def todo_mark(kind: str | None, text: str) -> str:
    """`qc-agent:todo[ KIND]: text` (không có ký tự comment; nơi gọi tự chọn `#` hay `//`)."""
    if kind is None:
        return f"{TODO} {text}"
    if kind not in TODO_KINDS:
        raise TemplateError(f"dạng TODO không hợp lệ: {kind!r}")
    return f"{TODO} {kind}: {' '.join(str(text).split())[:400]}"


# ---------- suite ----------

def api_contract_suite(*, openapi_path: str = "/openapi.json", exclude: tuple = (), refine: bool = False, verify_openapi: str | None = None) -> str:
    """`exclude` = [(đường dẫn OpenAPI, lý do)]: endpoint không nên bị fuzz (upload, xoá dữ liệu thật...). Lý do ghi thành comment.
    `refine=True` (Pha 1, chưa có OpenAPI sống): khối exclude_path nằm giữa marker begin/end kèm TODO REFINE để Pha 2 điền.
    `verify_openapi` (lý do) => TODO VERIFY về đường dẫn OpenAPI."""
    lines = []
    for path, reason in exclude:
        _need(_URL_PATH, path, "exclude_path")
        lines.append(f"    - {_q(path)}  # {_comment(reason)}".rstrip())
    if refine:
        if lines:
            raise TemplateError("refine=True nghĩa là chưa có OpenAPI: không kèm exclude")
        hint = "chưa biết endpoint nào không nên fuzz (upload, xoá dữ liệu thật): CI đề xuất từ OpenAPI sống, hoặc chạy lại init --openapi"
        block = "\n".join([f"    # {REFINE_BEGIN} exclude_path", f"    # {todo_mark('REFINE', hint)}", f"    # {REFINE_END}"])
    else:
        block = "    exclude_path:\n" + "\n".join(lines) if lines else ""
    note = f"    # {todo_mark('VERIFY', verify_openapi)}" if verify_openapi else ""
    return render("api-contract.yaml.tmpl", {"openapi_path": _need(_ROUTE_PATH, openapi_path, "openapi_path"), "exclude_block": block,
                                             "schema_note": note})


def perf_smoke_suite(*, script: str = ".qc-agent/perf/smoke.js", vus: int = 2, duration: str = "10s") -> str:
    if isinstance(vus, bool) or not isinstance(vus, int) or not 1 <= vus <= 50:
        raise TemplateError(f"vus phải là số nguyên 1..50, nhận {vus!r}")
    return render("perf-smoke.yaml.tmpl", {"script": _q(_need(_FILE, script, "script")), "vus": str(vus),
                                           "duration": _need(_DURATION, duration, "duration")})


def k6_smoke_script(*, paths: list[str], refine: bool = False) -> str:
    """`refine=True`: danh sách path tạm (thường chỉ health path) nằm trong vùng REFINE để Pha 2 thay bằng GET không tham số từ OpenAPI sống."""
    if not paths:
        raise TemplateError("k6 smoke cần ít nhất một đường dẫn GET")
    for path in paths:
        _need(_ROUTE_PATH, path, "đường dẫn k6")
    line = f"const PATHS = {_q(list(paths))};"
    if refine:
        hint = "danh sách tạm (health path): CI đề xuất các GET không tham số từ OpenAPI sống"
        line = "\n".join([f"// {REFINE_BEGIN} k6_paths", f"// {todo_mark('REFINE', hint)}", line, f"// {REFINE_END}"])
    return render("k6-smoke.js.tmpl", {"paths_block": line})


_NODE_MAJOR = re.compile(r"[1-9][0-9]")
_ARG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOCKFILES = {"package-lock.json": ("npm", "npm ci"), "pnpm-lock.yaml": ("pnpm", "corepack enable && pnpm install --frozen-lockfile"),
              "yarn.lock": ("yarn", "corepack enable && yarn install --frozen-lockfile")}


def ui_dockerfile(*, node_major: int, lockfile: str, output_dir: str, arg_names: list[str] = ()) -> str:
    """`.qc-agent/Dockerfile.ui` cho SPA TĨNH: build bằng đúng lockfile (`npm ci`/pnpm/yarn frozen) rồi phục vụ output bằng nginx:8080 (SPA fallback).
    Chỉ nhận lockfile đã biết: không lockfile = build không tất định = không sinh (scanner đã từ chối trước đó)."""
    if lockfile not in _LOCKFILES:
        raise TemplateError(f"lockfile không hỗ trợ: {lockfile!r} (có: {', '.join(_LOCKFILES)})")
    if isinstance(node_major, bool) or not isinstance(node_major, int) or not _NODE_MAJOR.fullmatch(str(node_major)):
        raise TemplateError(f"node_major không hợp lệ: {node_major!r}")
    pm, install = _LOCKFILES[lockfile]
    args = "\n".join(f"ARG {_need(_ARG_NAME, name, 'tên build-arg')}" for name in arg_names)
    return render("Dockerfile.ui.tmpl", {"node_major": str(node_major), "lockfile": lockfile, "install_cmd": install, "arg_lines": args, "pm": pm,
                                         "output_dir": _rel_path(output_dir, "output_dir")})


def ui_explore_suite(*, entry_path: str = "/", explore_flow: str = ".qc-agent/midscene/explore.yaml",
                     canary_flow: str = ".qc-agent/midscene/canary.yaml",
                     intent: str = "Khám phá giao diện chính: tìm trạng thái kẹt hoặc lỗi runtime") -> str:
    return render("ui-explore.yaml.tmpl", {
        "intent": _q(intent), "entry_path": _q(_need(_ROUTE_PATH, entry_path, "entry_path")),
        "explore_flow": _q(_need(_FILE, explore_flow, "explore_flow")), "canary_flow": _q(_need(_FILE, canary_flow, "canary_flow"))})


def _flow_lines(steps: list[tuple[str, str]]) -> list[str]:
    lines = []
    for command, text in steps:
        if command not in MIDSCENE_COMMANDS:
            raise TemplateError(f"lệnh Midscene không được phép: {command!r} (cho phép {MIDSCENE_COMMANDS})")
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise TemplateError("mô tả bước phải là chuỗi không rỗng, tối đa 200 ký tự")
        lines.append(f"      - {command}: {_q(text.strip())}")
    return lines


_TASK_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,80}")


def midscene_explore_flow(*, steps: list[tuple[str, str]] | None = None, tasks: list[tuple[str, list[tuple[str, str]]]] | None = None,
                          suggested_by: str | None = None) -> str:
    """Không có steps/tasks => khung chờ người viết (đánh dấu TODO). `steps` = một task `kham-pha`; `tasks` = nhiều task [(tên, steps)].
    `suggested_by` (tên model) => đầu ra của LLM: VẪN mang TODO "GỢI Ý" để `validate` từ chối cho tới khi người duyệt."""
    if steps is not None and tasks is not None:
        raise TemplateError("chỉ dùng một trong steps/tasks")
    if steps is None and tasks is None:
        return render("midscene-explore.yaml.tmpl", {
            "todo_line": _todo("thay bằng các bước người dùng thật làm trên UI (aiTap / aiAssert...) rồi xoá dòng này"),
            "tasks_block": "  - name: kham-pha\n    flow:\n      - aiWaitFor: trang đã tải xong và hiển thị nội dung chính"})
    if tasks is None:
        tasks = [("kham-pha", steps)]
    if not tasks or any(not steps_ for _, steps_ in tasks):
        raise TemplateError("steps/tasks rỗng: bỏ tham số nếu muốn khung TODO")
    blocks = []
    for name, task_steps in tasks:
        _need(_TASK_NAME, name, "tên task Midscene")
        blocks.append(f"  - name: {name}\n    flow:\n" + "\n".join(_flow_lines(task_steps)))
    todo = ""
    if suggested_by is not None:
        todo = _todo(f"GỢI Ý bởi LLM ({_need(_MODEL_NAME, suggested_by, 'tên model')}): duyệt từng bước, sửa/xoá cho đúng sản phẩm rồi xoá dòng này")
    return render("midscene-explore.yaml.tmpl", {"todo_line": todo, "tasks_block": "\n".join(blocks)})


def midscene_canary_flow() -> str:
    return render("midscene-canary.yaml.tmpl", {})


# ---------- workflow ----------

_WORKFLOW_INPUTS = ("sut_dockerfile", "sut_context", "sut_port", "sut_health_path", "sut_env", "sut_ui_dockerfile", "sut_ui_context",
                    "sut_ui_port", "sut_ui_health_path", "sut_ui_build_args")


def qc_workflow(*, project: str, qc_ref: str | None = None, image: str | None = None, qc_repo: str = DEFAULT_QC_REPO,
                sut_dockerfile: str | None = None, sut_context: str | None = None,
                sut_port: str | None = None, sut_health_path: str | None = None, sut_env: list[str] = (),
                ui_dockerfile: str | None = None, ui_context: str | None = None, ui_port: str | None = None,
                ui_health_path: str | None = None, ui_build_args: list[str] = (), marks: dict[str, str] | None = None) -> str:
    """qc.yml của repo SUT. `qc_ref`/`image` thiếu => điền chỗ giữ + TODO (ghim SHA/digest là việc người làm). Không khai `suites:`.
    `marks` = {tên input: nội dung dấu (dùng `todo_mark`)}: ghi thành comment cuối dòng của input đó (block `|` thì ở dòng khai báo)."""
    _need(_SLUG, project, "project")
    _need(_REPO, qc_repo, "qc_repo")
    marks = marks or {}
    unknown = set(marks) - set(_WORKFLOW_INPUTS)
    if unknown:
        raise TemplateError(f"marks có input lạ: {sorted(unknown)}")

    def tail(key: str) -> str:
        return f"  # {' '.join(marks[key].split())}" if key in marks else ""

    ref_text = _need(_SHA, qc_ref, "qc_ref") if qc_ref else f"qc-agent-todo-pin-commit-sha  # {TODO} ghim commit SHA 40 ký tự của qc-agent (không dùng @main)"
    image_text = _q(_need(_IMAGE, image, "image")) if image else f"ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>  # {TODO} điền digest từ Job Summary của workflow image"
    with_lines = []
    for key, value in (("sut_dockerfile", sut_dockerfile), ("sut_context", sut_context), ("sut_port", sut_port), ("sut_health_path", sut_health_path),
                       ("sut_ui_dockerfile", ui_dockerfile), ("sut_ui_context", ui_context), ("sut_ui_port", ui_port),
                       ("sut_ui_health_path", ui_health_path)):
        if value is not None:
            if key in ("sut_dockerfile", "sut_context", "sut_ui_dockerfile", "sut_ui_context"):
                _rel_path(value, key)
            with_lines.append(f"      {key}: {_q(str(value))}{tail(key)}")
    for key, items, pattern in (("sut_env", sut_env, _ENV_LINE), ("sut_ui_build_args", ui_build_args, _BUILD_ARG)):
        if items:
            with_lines.append(f"      {key}: |{tail(key)}")
            for item in items:
                with_lines.append("        " + _need(pattern, item, key))
    return render("qc.yml.tmpl", {"project": project, "qc_repo": qc_repo, "qc_ref": ref_text, "image": image_text,
                                  "with_block": "\n".join(with_lines)})
