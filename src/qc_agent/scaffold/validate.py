"""`qc-agent validate`: kiểm OFFLINE cấu hình của một project trước khi đẩy lên CI (không Docker, không mạng, không gọi SUT, không probe worker).

Bắt được trong vài giây những lỗi mà CI chỉ báo sau 10 phút: suite/project sai schema, lane xung đột policy, task không có worker, file tham chiếu
không tồn tại, biến môi trường mà workflow không cấp, `qc.yml` chưa ghim SHA/digest, và mọi dấu `qc-agent:todo` còn sót.
Mức: ERROR (làm exit 3), WARN (chỉ báo; `--strict` coi là lỗi), NOTE (thông tin, vd. secret cần đặt ở repo SUT).
"""
from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from qc_agent import settings
from qc_agent.core import plan as plan_lib
from qc_agent.core import project as pj
from qc_agent.core import registry
from qc_agent.core.plan import PlanError
from qc_agent.scaffold import templates as t

SYSTEM_ERROR = 3
ERROR, WARN, NOTE = "ERROR", "WARN", "NOTE"
DUMMY_URL = "http://validate.invalid"
ENV_REF = re.compile(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}")
SHA40 = re.compile(r"[0-9a-f]{40}")
IMAGE_DIGEST = re.compile(r"[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}")
REUSABLE = "qc-gate.reusable.yml"
# Biến môi trường mà workflow tái sử dụng chuyển vào container gate (khớp bước "Run qc-agent gate"; test đối chiếu với file workflow thật).
PASSTHROUGH = frozenset({"OPENAI_API_KEY", "GEMINI_API_KEY", "MIDSCENE_MODEL_BASE_URL", "MIDSCENE_MODEL_API_KEY", "MIDSCENE_MODEL_NAME",
                         "MIDSCENE_MODEL_FAMILY", "QC_JUDGE_PROVIDER", "QC_JUDGE_MODEL", "QC_JUDGE_FALLBACK_PROVIDER", "QC_JUDGE_FALLBACK_MODEL"})
FILE_INPUTS = ("flow", "script")   # inputs.<khoá> là đường dẫn file trong repo SUT (cộng inputs.collect.golden)


@dataclass(frozen=True)
class Finding:
    level: str
    where: str
    message: str


class Report:
    def __init__(self):
        self.findings: list[Finding] = []

    def add(self, level: str, where: str, message: str) -> None:
        item = Finding(level, where, message)
        if item not in self.findings:
            self.findings.append(item)

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)


def _fill_env(node, names: set[str]):
    """Thay `${env.X}` bằng URL giả để dựng spec offline; ghi lại tên biến đã dùng."""
    if isinstance(node, str):
        names.update(ENV_REF.findall(node))
        return ENV_REF.sub(DUMMY_URL, node)
    if isinstance(node, dict):
        return {key: _fill_env(value, names) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill_env(value, names) for value in node]
    return node


def _todo_lines(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return []
    return [(number, line.strip()) for number, line in enumerate(text.splitlines(), 1) if t.TODO in line]


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _reusable_inputs() -> set[str] | None:
    path = settings.get().project_root / ".github" / "workflows" / REUSABLE
    try:
        return set(yaml.safe_load(path.read_text(encoding="utf-8"))[True]["workflow_call"]["inputs"])
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return None  # bản cài đặt không kèm workflow (vd. trong image): bỏ qua kiểm tra tên input


def _workflow_jobs(sut_root: Path) -> list[tuple[Path, str, dict]]:
    found = []
    folder = sut_root / ".github" / "workflows"
    for path in sorted(list(folder.glob("*.yml")) + list(folder.glob("*.yaml"))) if folder.is_dir() else []:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        for name, job in ((data or {}).get("jobs") or {}).items() if isinstance(data, dict) else []:
            if isinstance(job, dict) and REUSABLE in str(job.get("uses", "")):
                found.append((path, name, job))
    return found


def validate(slug: str, sut_root: Path, *, projects_dir: Path | None = None, workers_dirs: list[Path] | None = None,
             modes: list[str] | None = None) -> Report:
    report = Report()
    sut_root = Path(sut_root)
    projects_dir = Path(projects_dir) if projects_dir else settings.get().resolved_projects_dir
    where_project = f"<projects>/{slug}.yaml"

    try:
        project = pj.load_project(slug, projects_dir)
    except PlanError as error:
        report.add(ERROR, where_project, str(error))
        return report
    for line, text in _todo_lines(projects_dir / f"{slug}.yaml"):
        report.add(ERROR, f"{where_project}:{line}", f"còn dấu {t.TODO}: {text}")

    suites_root = sut_root / project["suites_dir"]
    try:
        suites = pj.load_suites(suites_root)
    except PlanError as error:
        report.add(ERROR, project["suites_dir"], str(error))
        return report
    for path in sorted(suites_root.glob("*.y*ml")):
        for line, text in _todo_lines(path):
            report.add(ERROR, f"{project['suites_dir']}/{path.name}:{line}", f"còn dấu {t.TODO}: {text}")

    try:
        workers = registry.load_many(workers_dirs or settings.get().workers_dirs)
    except registry.ManifestError as error:
        report.add(ERROR, "workers", str(error))
        return report
    workers = {name: dataclasses.replace(worker, probe_ok=True, probe_reason=None) for name, worker in workers.items()}  # offline: không probe

    wanted_modes = modes or sorted(project["modes"])
    env_by_mode: dict[str, set[str]] = {}
    referenced: dict[str, str] = {}   # đường dẫn file tham chiếu -> nơi khai
    for mode in wanted_modes:
        if mode not in project["modes"]:
            report.add(ERROR, where_project, f"không có mode {mode!r} (có: {', '.join(sorted(project['modes']))})")
            continue
        try:
            plan, meta = pj.build_plan(project, mode, suites)
        except PlanError as error:
            report.add(ERROR, f"mode {mode}", str(error))
            continue
        if mode == "pr" and not any(task.get("lane") == "gate" for task in plan["tasks"]):
            report.add(WARN, f"mode {mode}", "không có task nào ở lane gate: PR luôn PASS, gate không chặn được gì")
        used: set[str] = set()
        secrets: dict[str, list[str]] = {}   # secret cần có -> các task cần nó (gộp thành MỘT ghi chú mỗi mode)
        for task in plan["tasks"]:
            task_id = task.get("task_id", "?")
            where = f"mode {mode} / {task_id}"
            filled = _fill_env(task, used)
            try:
                spec, extras = plan_lib.resolve(filled, {"plan_id": "plan-validate", "run_id": "r-0000", "runs_dir": str(sut_root / "runs"),
                                                          "sut_identity_ref": "sut-validate"})
            except PlanError as error:
                report.add(ERROR, where, str(error))
                continue
            worker, reason = registry.pick(workers, spec, tuple(extras.get("prefer", ())))
            if worker is None:
                report.add(ERROR, where, f"không có worker chạy được task này: {reason}")
            elif mode == "pr":
                for variable in worker.requires.get("env", []):
                    if variable in PASSTHROUGH:
                        secrets.setdefault(variable, []).append(task_id)
                    elif variable not in ("APP_BASE_URL", "APP_UI_URL"):
                        report.add(ERROR, where, f"worker {worker.name} cần biến {variable} mà workflow không cấp cho gate")
            inputs = spec.get("inputs") or {}
            for key in FILE_INPUTS:
                if isinstance(inputs.get(key), str):
                    referenced[inputs[key]] = f"{where} inputs.{key}"
            if isinstance((inputs.get("collect") or {}).get("golden"), str):
                referenced[inputs["collect"]["golden"]] = f"{where} inputs.collect.golden"
        if secrets:
            needed_by = sorted({task_id for tasks in secrets.values() for task_id in tasks})
            report.add(NOTE, f"mode {mode}", f"cần secret {', '.join(sorted(secrets))} ở repo SUT cho task {', '.join(needed_by)} (thiếu thì các task đó bị skipped)")
        env_by_mode[mode] = used

    for rel, origin in sorted(referenced.items()):
        target = sut_root / rel
        if Path(rel).is_absolute() or not _inside(sut_root, target):
            report.add(ERROR, origin, f"đường dẫn {rel!r} phải nằm trong repo SUT (tương đối, không thoát ra ngoài)")
        elif not target.is_file():
            report.add(ERROR, origin, f"file tham chiếu không tồn tại: {rel}")
        else:
            for line, text in _todo_lines(target):
                report.add(ERROR, f"{rel}:{line}", f"còn dấu {t.TODO}: {text}")

    _check_workflow(report, slug, project, suites, sut_root, env_by_mode.get("pr", set()))
    return report


def _check_workflow(report: Report, slug: str, project: dict, suites: dict, sut_root: Path, pr_env: set[str]) -> None:
    jobs = [j for j in _workflow_jobs(sut_root) if str((j[2].get("with") or {}).get("project")) == slug]
    if not jobs:
        anywhere = _workflow_jobs(sut_root)
        hint = f" (có {len(anywhere)} job gọi workflow nhưng project khác)" if anywhere else ""
        report.add(WARN, ".github/workflows", f"không thấy job nào gọi {REUSABLE} với project: {slug}{hint}")
        return
    known_inputs = _reusable_inputs()
    policy = project["modes"]
    for path, name, job in jobs:
        where = f"{path.relative_to(sut_root).as_posix()} job {name}"
        for line, text in _todo_lines(path):
            report.add(ERROR, f"{path.relative_to(sut_root).as_posix()}:{line}", f"còn dấu {t.TODO}: {text}")
        ref = str(job.get("uses", "")).rpartition("@")[2]
        if not SHA40.fullmatch(ref):
            report.add(ERROR, where, f"`uses` phải ghim commit SHA 40 ký tự của qc-agent, đang là @{ref or '(trống)'}")
        with_ = job.get("with") or {}
        image = str(with_.get("image", ""))
        if not IMAGE_DIGEST.fullmatch(image):
            report.add(ERROR, where, f"`image` phải ghim theo digest (tên@sha256:<64 hex>), đang là {image or '(trống)'}")
        if known_inputs is not None:
            for key in sorted(set(with_) - known_inputs):
                report.add(ERROR, where, f"input `{key}` không có trong workflow tái sử dụng")
        mode = str(with_.get("mode", "pr"))
        if mode not in policy:
            report.add(ERROR, where, f"mode {mode!r} không có trong project (có: {', '.join(sorted(policy))})")
        elif with_.get("suites"):
            allowed = set(policy[mode].get("blocking_suites", [])) | set(policy[mode].get("advisory_suites", []))
            if policy[mode].get("suites") == "*":
                allowed = set(suites)
            for suite in (s.strip() for s in str(with_["suites"]).split(",") if s.strip()):
                if suite not in allowed:
                    report.add(ERROR, where, f"suite {suite!r} nằm ngoài policy của mode {mode!r}")
        provided = {"APP_BASE_URL"} | PASSTHROUGH | ({"APP_UI_URL"} if with_.get("sut_ui_dockerfile") else set())
        if mode == "pr":
            for variable in sorted(pr_env - provided):
                hint = " (workflow chỉ cấp APP_UI_URL khi khai sut_ui_dockerfile)" if variable == "APP_UI_URL" else ""
                report.add(ERROR, where, f"suite dùng ${{env.{variable}}} nhưng workflow không cấp biến này cho gate{hint}")


def _format(report: Report) -> list[str]:
    order = {ERROR: 0, WARN: 1, NOTE: 2}
    return [f"{f.level:<5} {f.where}: {f.message}" for f in sorted(report.findings, key=lambda f: (order[f.level], f.where, f.message))]


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PlanError(f"tham số sai: {message}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = _Parser(prog="qc-agent validate", description="Kiểm cấu hình project offline (không Docker/mạng/SUT).")
    ap.add_argument("--project", required=True, help="slug project")
    ap.add_argument("--sut-root", required=True, metavar="DIR", help="thư mục gốc repo SUT")
    ap.add_argument("--projects-dir", metavar="DIR", help="thư mục configs/projects (mặc định $QC_PROJECTS_DIR hoặc của repo)")
    ap.add_argument("--workers-dir", action="append", metavar="DIR", help="thư mục manifest worker (mặc định như `run`)")
    ap.add_argument("--mode", action="append", help="chỉ kiểm mode này (lặp được); mặc định mọi mode")
    ap.add_argument("--strict", action="store_true", help="coi WARN là lỗi")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    except PlanError as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return SYSTEM_ERROR
    if not Path(args.sut_root).is_dir():
        print(f"LỖI: --sut-root không phải thư mục: {args.sut_root}", file=sys.stderr)
        return SYSTEM_ERROR
    report = validate(args.project, Path(args.sut_root), projects_dir=Path(args.projects_dir) if args.projects_dir else None,
                      workers_dirs=[Path(d) for d in args.workers_dir] if args.workers_dir else None, modes=args.mode)
    for line in _format(report):
        print(line)
    errors, warnings = report.count(ERROR), report.count(WARN)
    failed = errors > 0 or (args.strict and warnings > 0)
    print(f"{'FAIL' if failed else 'OK'}: {errors} lỗi, {warnings} cảnh báo, {report.count(NOTE)} ghi chú")
    return SYSTEM_ERROR if failed else 0
