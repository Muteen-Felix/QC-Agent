"""`qc-agent init`: sinh khung cấu hình cho một repo SUT — suite, script k6, flow Midscene, qc.yml, và config project bên qc-agent.

Tất định, không LLM: mọi giá trị đến từ tham số dòng lệnh hoặc từ OpenAPI của SUT (xem openapi.py). Chỗ cần hiểu sản phẩm được để `qc-agent:todo`.
Không bao giờ ghi đè file đã có trừ khi `--force`; `--dry-run` chỉ in kế hoạch (kèm diff) và không ghi gì.
Exit: 0 = xong, 3 = tham số/nguồn OpenAPI sai (cùng quy ước với `qc-agent run`).
"""
from __future__ import annotations

import argparse
import difflib
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from qc_agent import settings
from qc_agent.scaffold import openapi, suggest
from qc_agent.scaffold import templates as t

SYSTEM_ERROR = 3
SUITES_DIR = ".qc-agent/suites"
K6_SCRIPT = ".qc-agent/perf/smoke.js"
EXPLORE_FLOW = ".qc-agent/midscene/explore.yaml"
CANARY_FLOW = ".qc-agent/midscene/canary.yaml"
WORKFLOW = ".github/workflows/qc.yml"


class InitError(ValueError):
    """Tham số hoặc nguồn dữ liệu không dùng được; không có gì được ghi."""


@dataclass
class Options:
    sut_root: Path
    slug: str
    repo: str
    name: str | None = None
    openapi_source: str | None = None
    openapi_path: str = "/openapi.json"
    no_api: bool = False
    projects_dir: Path | None = None
    no_project: bool = False
    qc_repo: str = t.DEFAULT_QC_REPO
    qc_ref: str | None = None
    image: str | None = None
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
    label: str        # hiển thị (tương đối repo SUT, hoặc `<project>` cho config bên qc-agent)
    content: str


@dataclass
class Plan:
    files: list[Planned] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    label: str
    status: str       # created | overwritten | kept | would-create | would-overwrite | would-keep
    diff: str = ""
    todos: list[str] = field(default_factory=list)


def build(opts: Options) -> Plan:
    root = Path(opts.sut_root)
    if not root.is_dir():
        raise InitError(f"--sut-root không phải thư mục: {root}")
    plan = Plan()
    if not opts.openapi_source and not opts.no_api:
        raise InitError("cần --openapi (file hoặc URL của OpenAPI) để sinh api-contract/perf-smoke, hoặc --no-api để chỉ sinh phần UI "
                        "(khi đó project chưa có suite chặn merge)")
    try:
        analysis = None
        openapi_path = opts.openapi_path
        if opts.openapi_source and not opts.no_api:
            analysis = openapi.analyze(openapi.load(opts.openapi_source))
            source = urlsplit(opts.openapi_source)
            if source.scheme in ("http", "https") and source.path:
                openapi_path = source.path  # suite sẽ tải OpenAPI từ ${APP_BASE_URL}<path> của SUT trong runner
            plan.warnings.extend(analysis.warnings)
        if opts.no_api and opts.openapi_source:
            plan.warnings.append("--no-api: bỏ qua --openapi")
        name = opts.name or (analysis.title if analysis and analysis.title else opts.slug)

        def add(rel: str, content: str) -> None:
            plan.files.append(Planned(root / rel, rel, content))

        advisory: list[str] = []
        blocking: list[str] = []
        if analysis is not None:
            add(f"{SUITES_DIR}/api-contract.yaml", t.api_contract_suite(openapi_path=openapi_path, exclude=tuple(analysis.exclude)))
            blocking.append("api-contract")
            if analysis.get_paths:
                add(f"{SUITES_DIR}/perf-smoke.yaml", t.perf_smoke_suite(script=K6_SCRIPT))
                add(K6_SCRIPT, t.k6_smoke_script(paths=analysis.get_paths))
                advisory.append("perf-smoke")
            plan.notes.append(f"OpenAPI: {analysis.title or '(không tên)'} {analysis.version}; loại khỏi fuzz {len(analysis.exclude)} đường dẫn; "
                              f"k6 smoke gọi {analysis.get_paths or 'không có'}")
        if opts.ui_dockerfile:
            add(f"{SUITES_DIR}/ui-explore.yaml", t.ui_explore_suite(entry_path=opts.ui_entry_path, explore_flow=EXPLORE_FLOW, canary_flow=CANARY_FLOW))
            add(EXPLORE_FLOW, _explore_flow(opts, root, plan))
            add(CANARY_FLOW, t.midscene_canary_flow())
            advisory.append("ui-explore")
        elif any((opts.ui_context, opts.ui_port, opts.ui_health_path, opts.ui_build_args, opts.suggest_ui, opts.ui_urls)):
            raise InitError("có tham số --ui-*/--suggest-ui nhưng thiếu --ui-dockerfile")
        if opts.ui_urls and not opts.suggest_ui:
            raise InitError("--ui-url chỉ dùng cùng --suggest-ui")
        if opts.no_api and not opts.ui_dockerfile:
            raise InitError("--no-api mà không có UI (--ui-dockerfile) thì không có gì để sinh")
        add(WORKFLOW, t.qc_workflow(
            project=opts.slug, qc_repo=opts.qc_repo, qc_ref=opts.qc_ref, image=opts.image, sut_port=opts.sut_port,
            sut_health_path=opts.sut_health_path, sut_env=opts.sut_env, ui_dockerfile=opts.ui_dockerfile, ui_context=opts.ui_context,
            ui_port=opts.ui_port, ui_health_path=opts.ui_health_path, ui_build_args=opts.ui_build_args))
        if not opts.no_project:
            projects_dir = Path(opts.projects_dir) if opts.projects_dir else settings.get().resolved_projects_dir
            plan.files.append(Planned(projects_dir / f"{opts.slug}.yaml", f"<projects>/{opts.slug}.yaml",
                                      t.project_config(slug=opts.slug, name=name, repo=opts.repo, advisory=tuple(advisory), blocking=tuple(blocking))))
    except (t.TemplateError, openapi.OpenApiError) as error:
        raise InitError(str(error)) from None
    return plan


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


def apply(plan: Plan, *, force: bool = False, dry_run: bool = False) -> list[Outcome]:
    outcomes = []
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
            _write(planned.path, planned.content)
        outcomes.append(Outcome(planned.label, status, diff, _todos(planned.content) if status in ("created", "overwritten", "would-create", "would-overwrite") else []))
    return outcomes


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".qc-init-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content if content.endswith("\n") else content + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse mặc định exit 2; qc-agent dùng 3 cho mọi lỗi cấu hình/tham số
        raise InitError(f"tham số sai: {message}")


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="qc-agent init", description="Sinh khung cấu hình qc-agent cho một repo SUT (tất định, không LLM).")
    ap.add_argument("--sut-root", required=True, metavar="DIR", help="thư mục gốc repo SUT (nơi ghi .qc-agent/ và .github/workflows/qc.yml)")
    ap.add_argument("--slug", required=True, help="slug project (chữ thường, số, - _)")
    ap.add_argument("--repo", required=True, metavar="OWNER/REPO", help="repo GitHub của SUT")
    ap.add_argument("--name", help="tên hiển thị (mặc định: info.title của OpenAPI, hoặc slug)")
    ap.add_argument("--openapi", metavar="FILE|URL", help="OpenAPI của SUT (file, hoặc URL của SUT đang chạy)")
    ap.add_argument("--openapi-path", default="/openapi.json", help="đường dẫn OpenAPI trên SUT khi --openapi là file (mặc định /openapi.json)")
    ap.add_argument("--no-api", action="store_true", help="không sinh api-contract/perf-smoke (project chưa có suite chặn merge)")
    ap.add_argument("--projects-dir", metavar="DIR", help="thư mục configs/projects của qc-agent (mặc định $QC_PROJECTS_DIR hoặc của repo)")
    ap.add_argument("--no-project", action="store_true", help="không sinh config project bên qc-agent")
    ap.add_argument("--qc-repo", default=t.DEFAULT_QC_REPO, help="repo chứa workflow tái sử dụng")
    ap.add_argument("--qc-ref", metavar="SHA40", help="commit SHA của qc-agent để ghim (thiếu => TODO)")
    ap.add_argument("--image", metavar="REF@sha256:...", help="image qc-agent ghim theo digest (thiếu => TODO)")
    ap.add_argument("--sut-port", help="cổng của SUT (mặc định của workflow: 8000)")
    ap.add_argument("--health-path", help="đường dẫn thăm dò sẵn sàng của SUT (mặc định của workflow: /)")
    ap.add_argument("--sut-env", action="append", default=[], metavar="KEY=VALUE", help="biến môi trường cho container SUT (KHÔNG đặt bí mật)")
    ap.add_argument("--ui-dockerfile", metavar="PATH", help="Dockerfile của web UI: bật suite ui-explore và container ui")
    ap.add_argument("--ui-context", help="context build của UI")
    ap.add_argument("--ui-port", help="cổng của UI (mặc định của workflow: 8080)")
    ap.add_argument("--ui-health-path", help="đường dẫn thăm dò sẵn sàng của UI")
    ap.add_argument("--ui-build-arg", action="append", default=[], metavar="KEY=VALUE", help="build-arg cho UI (KHÔNG đặt bí mật)")
    ap.add_argument("--ui-entry-path", default="/", help="trang Midscene mở đầu tiên (mặc định /)")
    ap.add_argument("--suggest-ui", action="store_true", help="dùng LLM (khoá MIDSCENE_MODEL_*) gợi ý flow explore từ NHÃN hiển thị của UI; "
                                                              "gửi nhãn ra nhà cung cấp LLM, ghi log egress; kết quả vẫn phải duyệt (dấu qc-agent:todo)")
    ap.add_argument("--ui-url", action="append", default=[], metavar="URL", help="URL của UI đang chạy để đọc nhãn (lặp được, tối đa 5; cần --suggest-ui)")
    ap.add_argument("--force", action="store_true", help="ghi đè file đã có")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch (kèm diff khi ghi đè), không ghi gì")
    return ap


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        opts = Options(
            sut_root=Path(args.sut_root), slug=args.slug, repo=args.repo, name=args.name, openapi_source=args.openapi,
            openapi_path=args.openapi_path, no_api=args.no_api, projects_dir=Path(args.projects_dir) if args.projects_dir else None,
            no_project=args.no_project, qc_repo=args.qc_repo, qc_ref=args.qc_ref, image=args.image, sut_port=args.sut_port,
            sut_health_path=args.health_path, sut_env=args.sut_env, ui_dockerfile=args.ui_dockerfile, ui_context=args.ui_context,
            ui_port=args.ui_port, ui_health_path=args.ui_health_path, ui_build_args=args.ui_build_arg, ui_entry_path=args.ui_entry_path,
            suggest_ui=args.suggest_ui, ui_urls=args.ui_url, force=args.force, dry_run=args.dry_run)
        plan = build(opts)
        outcomes = apply(plan, force=args.force, dry_run=args.dry_run)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    except InitError as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return SYSTEM_ERROR
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
    if args.dry_run:
        print("(dry-run: chưa ghi gì)")
    return 0
