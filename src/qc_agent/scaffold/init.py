"""`qc-agent init`: sinh khung cấu hình cho MỘT repo SUT — suite, script k6, flow Midscene, qc.yml, và `.qc-agent/Dockerfile.ui` (SPA tĩnh).

Pha 1 của onboarding (plan 16): chạy offline, MỘT lệnh, không cần Python, không cần SUT đang chạy:
    docker run --rm -v "$PWD:/sut" ghcr.io/muteen-felix/qc-agent@sha256:<D> init
Chỉ đọc cây thư mục (scanner tất định, scan.py) và CHỈ GHI trong repo SUT; không sinh config bên qc-agent (repo chưa đăng ký dùng `_default`;
muốn dashboard thì đăng ký bằng PR tay). Chỗ đoán có nhiều ứng viên => `qc-agent:todo VERIFY`; chỗ cần SUT sống => `qc-agent:todo REFINE`
(CI điền ở Pha 2, xem refine.py). Không LLM trừ khi có `--suggest-ui`. Không bao giờ ghi đè file đã có trừ `--force`; `--dry-run` chỉ in kế hoạch.
Exit: 0 = xong, 3 = tham số/nguồn dữ liệu sai (cùng quy ước với `qc-agent run`).
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from qc_agent.scaffold import gitinfo, openapi, scan, suggest
from qc_agent.scaffold import templates as t

SYSTEM_ERROR = 3
SUITES_DIR = ".qc-agent/suites"
K6_SCRIPT = ".qc-agent/perf/smoke.js"
EXPLORE_FLOW = ".qc-agent/midscene/explore.yaml"
CANARY_FLOW = ".qc-agent/midscene/canary.yaml"
UI_DOCKERFILE = ".qc-agent/Dockerfile.ui"
WORKFLOW = ".github/workflows/qc.yml"
DEFAULT_SUT_MOUNT = "/sut"   # `docker run -v "$PWD:/sut" <image> init` chạy nguyên văn


class InitError(ValueError):
    """Tham số hoặc nguồn dữ liệu không dùng được; không có gì được ghi."""


@dataclass
class Options:
    sut_root: Path
    slug: str | None = None           # mặc định: tên repo trong origin, rồi tên thư mục (gitinfo.default_slug)
    openapi_source: str | None = None
    openapi_path: str | None = None
    no_api: bool = False
    qc_repo: str = t.DEFAULT_QC_REPO
    qc_ref: str | None = None         # mặc định: $QC_AGENT_GIT_SHA của image (nếu là SHA 40 ký tự)
    image: str | None = None
    sut_dockerfile: str | None = None
    sut_context: str | None = None
    sut_port: str | None = None
    sut_health_path: str | None = None
    sut_env: list[str] = field(default_factory=list)
    ui_dockerfile: str | None = None
    ui_context: str | None = None
    ui_port: str | None = None
    ui_health_path: str | None = None
    ui_build_args: list[str] = field(default_factory=list)
    ui_entry_path: str = "/"
    suggest_ui: bool = False          # LLM gợi ý flow explore từ nhãn của UI đang chạy (gửi nhãn ra nhà cung cấp LLM)
    ui_urls: list[str] = field(default_factory=list)
    force: bool = False               # build() cần biết để không gọi LLM khi file sẽ không được ghi
    dry_run: bool = False             # dry-run không bao giờ gửi dữ liệu ra ngoài


@dataclass
class Planned:
    path: Path        # tuyệt đối
    label: str        # hiển thị (tương đối repo SUT)
    content: str


@dataclass
class Plan:
    root: Path | None = None
    files: list[Planned] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    slug: str = ""


@dataclass
class Outcome:
    label: str
    status: str       # created | overwritten | kept | would-create | would-overwrite | would-keep
    diff: str = ""
    todos: list[str] = field(default_factory=list)


def _verify(finding) -> str | None:
    return t.todo_mark("VERIFY", finding.verify) if finding is not None and finding.verify else None


def build(opts: Options) -> Plan:
    root = Path(opts.sut_root)
    if not root.is_dir():
        raise InitError(f"--sut-root không phải thư mục: {root}")
    plan = Plan(root=root)
    try:
        plan.slug = t._need(t._SLUG, opts.slug or gitinfo.default_slug(root), "slug")
        if opts.no_api:
            plan.warnings.append("--no-api: project chỉ có UI không đủ điều kiện mode pr (cần suite chặn merge: Schemathesis hoặc DeepEval deterministic); "
                                 "chỉ dùng được mode manual")
            if opts.openapi_source:
                plan.warnings.append("--no-api: bỏ qua --openapi")
        try:
            found = scan.scan(root, scan.Overrides(dockerfile=opts.sut_dockerfile, port=opts.sut_port, health_path=opts.sut_health_path),
                              api=not opts.no_api)
        except scan.ScanError as error:
            raise InitError(str(error)) from None
        plan.notes.extend(found.notes)

        def add(rel: str, content: str) -> None:
            plan.files.append(Planned(root / rel, rel, content))

        marks: dict[str, str] = {}
        if not opts.no_api:
            _api_suites(opts, found, plan, add)
        if opts.no_api and not (opts.ui_dockerfile or found.ui):
            raise InitError("--no-api mà không có UI thì không có gì để sinh")

        ui_dockerfile, ui_context, ui_port = opts.ui_dockerfile, opts.ui_context, opts.ui_port
        ui_build_args = list(opts.ui_build_args)
        if not ui_dockerfile and found.ui:
            ui = found.ui
            ui_dockerfile, ui_context, ui_port = UI_DOCKERFILE, ui_context or ui.directory, ui_port or scan.UI_PORT
            arg_names = [name.split("=", 1)[0] for name in ui_build_args] or ([ui.api_var.value] if ui.api_var else [])
            add(UI_DOCKERFILE, t.ui_dockerfile(node_major=ui.node_major.value, lockfile=ui.lockfile, output_dir=ui.output_dir, arg_names=arg_names))
            if not ui_build_args:
                if ui.api_var:
                    ui_build_args = [f"{ui.api_var.value}=http://sut:{found.port.value}"]
                    if _verify(ui.api_var):
                        marks["sut_ui_build_args"] = _verify(ui.api_var)
                else:
                    plan.notes.append("không thấy biến URL API trong mã UI: không có build-arg (nếu UI gọi API, thêm --ui-build-arg KEY=http://sut:<cổng>)")
            if _verify(ui.directory_finding):
                marks["sut_ui_context"] = _verify(ui.directory_finding)
            plan.notes.append(f"UI {ui.directory}: {ui.kind}, {ui.package_manager}, node {ui.node_major.value}"
                              f"{' (mặc định)' if ui.node_major.source == 'default' else ''}, output {ui.output_dir}/ -> {UI_DOCKERFILE}")
        elif not ui_dockerfile and found.ui_refused:
            plan.notes.append(found.ui_refused)
        if ui_dockerfile:
            add(f"{SUITES_DIR}/ui-explore.yaml", t.ui_explore_suite(entry_path=opts.ui_entry_path, explore_flow=EXPLORE_FLOW, canary_flow=CANARY_FLOW))
            add(EXPLORE_FLOW, _explore_flow(opts, root, plan))
            add(CANARY_FLOW, t.midscene_canary_flow())
        elif any((opts.ui_context, opts.ui_port, opts.ui_health_path, opts.ui_build_args, opts.suggest_ui, opts.ui_urls)):
            raise InitError("có tham số --ui-*/--suggest-ui nhưng không có UI (không tự tìm thấy và thiếu --ui-dockerfile)")
        if opts.ui_urls and not opts.suggest_ui:
            raise InitError("--ui-url chỉ dùng cùng --suggest-ui")

        sut_env = list(opts.sut_env)
        if not sut_env and ui_dockerfile and found.cors_env:
            sut_env = [f"{found.cors_env.value}=http://ui:{ui_port or scan.UI_PORT}"]
            if _verify(found.cors_env):
                marks["sut_env"] = _verify(found.cors_env)
        elif not sut_env and ui_dockerfile and not opts.no_api:
            plan.notes.append("không thấy biến CORS trong mã API: nếu API chặn origin http://ui:<cổng UI> thì thêm --sut-env KEY=VALUE")

        dockerfile, context = None, None
        if found.dockerfile is not None:
            dockerfile = found.dockerfile.value if found.dockerfile.value != "Dockerfile" else None
            context = found.context if found.context != "." else None
            if dockerfile and _verify(found.dockerfile):
                marks["sut_dockerfile"] = _verify(found.dockerfile)
        port = found.port.value if not opts.no_api and (found.port.value != scan.DEFAULT_PORT or found.port.verify) else None
        if port and _verify(found.port):
            marks["sut_port"] = _verify(found.port)
        health = found.health_path.value if not opts.no_api and found.health_path.value != scan.DEFAULT_HEALTH else None
        if health and _verify(found.health_path):
            marks["sut_health_path"] = _verify(found.health_path)
        add(WORKFLOW, t.qc_workflow(
            project=plan.slug, qc_repo=opts.qc_repo, qc_ref=opts.qc_ref or _image_sha(), image=opts.image, sut_dockerfile=dockerfile, sut_context=opts.sut_context or context,
            sut_port=port, sut_health_path=health, sut_env=sut_env, ui_dockerfile=ui_dockerfile, ui_context=ui_context, ui_port=ui_port,
            ui_health_path=opts.ui_health_path, ui_build_args=ui_build_args, marks=marks))
    except (t.TemplateError, openapi.OpenApiError) as error:
        raise InitError(str(error)) from None
    return plan


def _image_sha() -> str | None:
    """Commit SHA của qc-agent mà image này được build từ đó (Dockerfile đặt QC_AGENT_GIT_SHA): dùng để ghim `uses:` sẵn. Digest thì image không tự biết."""
    sha = os.environ.get("QC_AGENT_GIT_SHA", "")
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _api_suites(opts: Options, found: scan.ScanResult, plan: Plan, add) -> None:
    """api-contract (+ perf-smoke). Có --openapi: nội dung từ OpenAPI đó, không REFINE. Không có (Pha 1): vẫn sinh, kèm vùng REFINE cho Pha 2."""
    openapi_path = opts.openapi_path or (found.openapi_path.value if found.openapi_path else "/openapi.json")
    if opts.openapi_source:
        analysis = openapi.analyze(openapi.load(opts.openapi_source))
        source = urlsplit(opts.openapi_source)
        if source.scheme in ("http", "https") and source.path:
            openapi_path = source.path  # suite sẽ tải OpenAPI từ ${APP_BASE_URL}<path> của SUT trong runner
        plan.warnings.extend(analysis.warnings)
        add(f"{SUITES_DIR}/api-contract.yaml", t.api_contract_suite(openapi_path=openapi_path, exclude=tuple(analysis.exclude)))
        if analysis.get_paths:
            add(f"{SUITES_DIR}/perf-smoke.yaml", t.perf_smoke_suite(script=K6_SCRIPT))
            add(K6_SCRIPT, t.k6_smoke_script(paths=analysis.get_paths))
        plan.notes.append(f"OpenAPI: {analysis.title or '(không tên)'} {analysis.version}; loại khỏi fuzz {len(analysis.exclude)} đường dẫn; "
                          f"k6 smoke gọi {analysis.get_paths or 'không có'}")
        return
    verify_openapi = None if found.openapi_path or opts.openapi_path else (
        "không thấy FastAPI trong phụ thuộc: xác nhận đường dẫn OpenAPI của SUT (đang là /openapi.json)")
    add(f"{SUITES_DIR}/api-contract.yaml", t.api_contract_suite(openapi_path=openapi_path, refine=True, verify_openapi=verify_openapi))
    add(f"{SUITES_DIR}/perf-smoke.yaml", t.perf_smoke_suite(script=K6_SCRIPT))
    add(K6_SCRIPT, t.k6_smoke_script(paths=[found.health_path.value], refine=True))
    plan.notes.append("không có --openapi: api-contract/perf-smoke được sinh với vùng REFINE (exclude_path, endpoint k6); "
                      "CI (Pha 2) điền từ OpenAPI sống, hoặc chạy lại với --openapi")


def _explore_flow(opts: Options, root: Path, plan: Plan) -> str:
    """Khung TODO, hoặc gợi ý của LLM (vẫn mang TODO "GỢI Ý"). Mọi lỗi của phần gợi ý chỉ là cảnh báo: init vẫn xong với khung TODO."""
    if not opts.suggest_ui:
        return t.midscene_explore_flow()
    if not opts.ui_urls:
        raise InitError("--suggest-ui cần ít nhất một --ui-url (URL của UI đang chạy)")
    if opts.dry_run:
        plan.notes.append("dry-run: KHÔNG gọi LLM (lần chạy thật sẽ gửi nhãn hiển thị của UI tới nhà cung cấp LLM)")
        return t.midscene_explore_flow()
    if (root / EXPLORE_FLOW).exists() and not opts.force:
        plan.notes.append(f"{EXPLORE_FLOW} đã có và không bị ghi đè: bỏ qua gọi LLM")
        return t.midscene_explore_flow()
    try:
        flows, model = suggest.suggest_flows(opts.ui_urls, root)
    except suggest.SuggestError as error:
        plan.warnings.append(f"--suggest-ui: {error}; giữ khung TODO")
        return t.midscene_explore_flow()
    plan.notes.append(f"LLM ({model}) gợi ý {len(flows)} flow từ {len(opts.ui_urls)} trang; đã ghi log egress vào .qc-agent/egress.jsonl")
    return t.midscene_explore_flow(tasks=flows, suggested_by=model)


def _todos(content: str) -> list[str]:
    return [f"dòng {number}: {line.strip()[:110]}" for number, line in enumerate(content.splitlines(), 1) if t.TODO in line]


def _owner(root: Path | None) -> tuple[int, int] | None:
    """Chạy bằng root trong container (`docker run -v $PWD:/sut`) thì file mới thuộc root: trả (uid, gid) của thư mục /sut để chown lại cho người dùng máy chủ."""
    if root is None or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    info = Path(root).stat()
    return (info.st_uid, info.st_gid) if info.st_uid != 0 else None


def apply(plan: Plan, *, force: bool = False, dry_run: bool = False) -> list[Outcome]:
    outcomes = []
    owner = None if dry_run else _owner(plan.root)
    for planned in plan.files:
        exists = planned.path.exists()
        if exists and planned.path.is_dir():
            raise InitError(f"{planned.label} là thư mục, không phải file")
        old = planned.path.read_text(encoding="utf-8") if exists and planned.path.is_file() else None
        same = old == planned.content
        if exists and not force:
            status = "would-keep" if dry_run else "kept"
        else:
            status = ("would-overwrite" if exists else "would-create") if dry_run else ("overwritten" if exists else "created")
        diff = ""
        if dry_run and exists and force and not same:
            diff = "".join(difflib.unified_diff(old.splitlines(True), planned.content.splitlines(True), f"a/{planned.label}", f"b/{planned.label}"))
        if not dry_run and status in ("created", "overwritten"):
            _write(planned.path, planned.content, plan.root, owner)
        outcomes.append(Outcome(planned.label, status, diff, _todos(planned.content) if status in ("created", "overwritten", "would-create", "would-overwrite") else []))
    return outcomes


def _default_mode() -> int:
    """0666 & ~umask của tiến trình (như file tạo bằng open())."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def _write(path: Path, content: str, root: Path | None = None, owner: tuple[int, int] | None = None) -> None:
    missing = []
    for parent in path.parents:
        if parent.exists() or (root is not None and parent == Path(root).parent):
            break
        missing.append(parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".qc-init-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content if content.endswith("\n") else content + "\n")
        os.chmod(tmp, _default_mode())   # mkstemp tạo 0600: file sinh ra phải đọc được như file thường (gate chạy bằng uid khác trong container)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if owner is not None:   # chỉ chạm file/thư mục VỪA tạo, không đổi quyền thứ đã có
        for target in (*missing, path):
            os.chown(target, *owner)


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse mặc định exit 2; qc-agent dùng 3 cho mọi lỗi cấu hình/tham số
        raise InitError(f"tham số sai: {message}")


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="qc-agent init", description="Sinh khung cấu hình qc-agent cho một repo SUT (tất định, offline, chỉ ghi trong repo SUT).")
    ap.add_argument("--sut-root", metavar="DIR", help=f"thư mục gốc repo SUT (mặc định {DEFAULT_SUT_MOUNT} nếu có, không thì thư mục hiện tại)")
    ap.add_argument("--slug", help="slug project (mặc định: tên repo trong origin, rồi tên thư mục)")
    ap.add_argument("--openapi", metavar="FILE|URL", help="OpenAPI của SUT (file đã commit, hoặc URL của SUT đang chạy); bỏ trống => vùng REFINE cho CI điền")
    ap.add_argument("--openapi-path", help="đường dẫn OpenAPI trên SUT khi --openapi là file hoặc không có (mặc định do scanner: /openapi.json)")
    ap.add_argument("--no-api", action="store_true", help="không sinh api-contract/perf-smoke (project không đủ điều kiện mode pr, chỉ dùng manual)")
    ap.add_argument("--qc-repo", default=t.DEFAULT_QC_REPO, help="repo chứa workflow tái sử dụng")
    ap.add_argument("--qc-ref", metavar="SHA40", help="commit SHA của qc-agent để ghim (mặc định: commit build của image này; thiếu => TODO)")
    ap.add_argument("--image", metavar="REF@sha256:...", help="image qc-agent ghim theo digest (thiếu => TODO)")
    ap.add_argument("--sut-dockerfile", metavar="PATH", help="Dockerfile của API (mặc định: scanner tìm; không thấy thì lỗi)")
    ap.add_argument("--sut-context", metavar="DIR", help="context build của API (mặc định suy từ vị trí Dockerfile)")
    ap.add_argument("--sut-port", help="cổng của SUT (mặc định: EXPOSE/--port trong Dockerfile, rồi 8000)")
    ap.add_argument("--health-path", help="đường dẫn thăm dò sẵn sàng của SUT (mặc định: scanner tìm route health, rồi /)")
    ap.add_argument("--sut-env", action="append", default=[], metavar="KEY=VALUE", help="biến môi trường cho container SUT (KHÔNG đặt bí mật)")
    ap.add_argument("--ui-dockerfile", metavar="PATH", help="Dockerfile của web UI do bạn tự viết (mặc định: tự sinh .qc-agent/Dockerfile.ui cho SPA tĩnh)")
    ap.add_argument("--ui-context", help="context build của UI")
    ap.add_argument("--ui-port", help="cổng của UI (mặc định của workflow: 8080)")
    ap.add_argument("--ui-health-path", help="đường dẫn thăm dò sẵn sàng của UI")
    ap.add_argument("--ui-build-arg", action="append", default=[], metavar="KEY=VALUE", help="build-arg cho UI (KHÔNG đặt bí mật)")
    ap.add_argument("--ui-entry-path", default="/", help="trang Midscene mở đầu tiên (mặc định /)")
    ap.add_argument("--suggest-ui", action="store_true", help="dùng LLM (khoá MIDSCENE_MODEL_*) gợi ý flow explore từ NHÃN hiển thị của UI; "
                                                              "gửi nhãn ra nhà cung cấp LLM, ghi log egress; kết quả vẫn phải duyệt (dấu qc-agent:todo)")
    ap.add_argument("--ui-url", action="append", default=[], metavar="URL", help="URL của UI đang chạy để đọc nhãn (lặp được, tối đa 5; cần --suggest-ui)")
    ap.add_argument("--refine", action="store_true", help="Pha 2 (chạy trên CI): điền vùng REFINE từ OpenAPI sống, ghi refine.patch + suggestions.json; dùng `init --refine --help`")
    ap.add_argument("--force", action="store_true", help="ghi đè file đã có")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch (kèm diff khi ghi đè), không ghi gì")
    return ap


def default_sut_root() -> Path:
    return Path(DEFAULT_SUT_MOUNT) if Path(DEFAULT_SUT_MOUNT).is_dir() else Path.cwd()


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if "--refine" in argv:   # Pha 2: điền vùng REFINE từ OpenAPI sống (refine.py có bộ tham số riêng)
        from qc_agent.scaffold import refine
        return refine.main([a for a in argv if a != "--refine"])
    try:
        args = _parser().parse_args(argv)
        opts = Options(
            sut_root=Path(args.sut_root) if args.sut_root else default_sut_root(), slug=args.slug, openapi_source=args.openapi,
            openapi_path=args.openapi_path, no_api=args.no_api, qc_repo=args.qc_repo, qc_ref=args.qc_ref, image=args.image,
            sut_dockerfile=args.sut_dockerfile, sut_context=args.sut_context, sut_port=args.sut_port, sut_health_path=args.health_path,
            sut_env=args.sut_env, ui_dockerfile=args.ui_dockerfile, ui_context=args.ui_context, ui_port=args.ui_port,
            ui_health_path=args.ui_health_path, ui_build_args=args.ui_build_arg, ui_entry_path=args.ui_entry_path,
            suggest_ui=args.suggest_ui, ui_urls=args.ui_url, force=args.force, dry_run=args.dry_run)
        plan = build(opts)
        outcomes = apply(plan, force=args.force, dry_run=args.dry_run)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    except InitError as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return SYSTEM_ERROR
    print(f"slug: {plan.slug}")
    for note in plan.notes:
        print(f"· {note}")
    for warning in plan.warnings:
        print(f"CẢNH BÁO: {warning}")
    todos = []
    for outcome in outcomes:
        print(f"{outcome.status:<16} {outcome.label}")
        if outcome.diff:
            print(outcome.diff.rstrip("\n"))
        todos.extend(f"{outcome.label} {todo}" for todo in outcome.todos)
    kept = [o.label for o in outcomes if o.status in ("kept", "would-keep")]
    if kept:
        print(f"Đã có sẵn, KHÔNG ghi đè (dùng --force để ghi đè): {', '.join(kept)}")
    if todos:
        print("Còn việc cho người (qc-agent:todo):")
        for todo in todos:
            print(f"  - {todo}")
        print(f"Tiếp theo: commit, mở PR vào chính repo này; `qc-agent validate --sut-root {opts.sut_root}` kiểm trước khi đẩy.")
    if args.dry_run:
        print("(dry-run: chưa ghi gì)")
    return 0
