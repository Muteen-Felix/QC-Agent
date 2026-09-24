"""Điểm ghép của orchestrator: plan -> resolve -> worker -> verdict -> report. Không LLM, không tên worker cụ thể.
Exit code: PASS=0 · YELLOW=--yellow-exit · FAIL=1 · lỗi của HỆ THỐNG (PlanError, lỗi nội bộ, gọi sai lệnh)=3.
Phải chạy từ thư mục gốc repo: adapter được spawn bằng `python -m <module>` và đọc đường dẫn tương đối theo cwd."""
import argparse
import os
import signal
import json
import logging
import sys
import threading
from pathlib import Path

from qc_agent import logging_setup, settings
from qc_agent.core import engine, registry, report, runner, signature
from qc_agent.core.plan import PlanError, load_plan
from qc_agent.core.verdict import canary_alerts

SYSTEM_ERROR = 3
log = logging.getLogger("qc_agent.cli")


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse mặc định exit 2 — trùng với `--yellow-exit 2` của CI, nên ép về 3
        raise PlanError(f"tham số sai: {message}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # report có tiếng Việt + emoji; console Windows mặc định là cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    _install_sigterm_handler()
    logging_setup.configure()
    try:
        if argv and argv[0] in ("user", "token"):  # quản trị tài khoản/token (cần QC_DATABASE_URL)
            from qc_agent.auth.cli import main as admin_main
            return admin_main(argv)
        if argv and argv[0] == "run":  # `qc-agent run --project ...` và `qc-agent --plan ...` đều được
            argv = argv[1:]
        args = _parser().parse_args(argv)
        if not 0 <= args.yellow_exit <= 255:
            raise PlanError(f"tham số sai: --yellow-exit phải trong 0..255, nhận {args.yellow_exit}")
        return _rerender(args) if args.rerender else _run(args)
    except SystemExit as exit_:  # --help
        return exit_.code if isinstance(exit_.code, int) else 0
    except (PlanError, registry.ManifestError) as error:
        print(f"LỖI PLAN/CẤU HÌNH: {error}", file=sys.stderr)
        logging_setup.event(log, "run.aborted", logging.ERROR, reason="plan_or_config", detail=logging_setup.short(error))
    except Exception as error:  # noqa: BLE001 — lỗi nội bộ của orchestrator: exit 3, không bao giờ được lẫn với verdict
        print(f"LỖI NỘI BỘ: {type(error).__name__}: {error}", file=sys.stderr)
        logging_setup.event(log, "run.aborted", logging.ERROR, reason="internal", error_type=type(error).__name__)
    return SYSTEM_ERROR


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="orchestrator.py", description="QC gate: chạy plan, gộp verdict tất định, ghi report.")
    ap.add_argument("--plan", help="file plan YAML (hoặc dùng --project/--mode; bắt buộc một trong hai trừ khi có --rerender)")
    ap.add_argument("--project", help="slug project trong configs/projects/ (chạy các suite mà policy của --mode chọn)")
    ap.add_argument("--mode", help="mode trong policy của project, vd pr | manual (bắt buộc khi có --project)")
    ap.add_argument("--suites", help="chỉ chạy các suite này (tên, cách nhau dấu phẩy); phải nằm trong policy của mode")
    ap.add_argument("--suites-dir", metavar="DIR", help="thư mục suite (mặc định <sut-root>/<project.suites_dir>)")
    ap.add_argument("--sut-root", metavar="DIR", help="thư mục checkout của SUT (mặc định cwd); worker chạy với cwd này")
    ap.add_argument("--projects-dir", metavar="DIR", help="thư mục configs/projects (mặc định $QC_PROJECTS_DIR)")
    ap.add_argument("--only", help="chỉ chạy các task này, vd t-a,t-b (phải kèm đủ task được depends_on)")
    ap.add_argument("--yellow-exit", type=int, default=0, metavar="N", help="exit code khi gate YELLOW (mặc định 0)")
    ap.add_argument("--on-skipped-gate-task", choices=engine.SKIPPED_POLICIES, default=None,
                    help="task gate bị skipped: yellow (theo --yellow-exit) hoặc fail (gate FAIL, exit 1); mặc định: policy của mode, rồi yellow")
    ap.add_argument("--sut-ref", metavar="SHA",
                    help="commit/ref của SUT đang được gate (vd. PR head SHA); mặc định plan.sut.ref hoặc git HEAD của SUT root")
    ap.add_argument("--run-id", metavar="ID", help="id của run (mặc định r-NNNN); executor dùng id của job để run_dir khớp job")
    ap.add_argument("--runs-dir", default=str(settings.get().runs_dir), help="mặc định $QC_RUNS_DIR hoặc runs")
    ap.add_argument("--workers-dir", action="append", metavar="DIR",
                    help="thư mục manifest worker (lặp được); mặc định $QC_WORKERS_PATH hoặc workers/")
    ap.add_argument("--rerender", metavar="RUN_DIR",
                    help="không chạy worker: tính lại verdict từ RUN_DIR/specs + results, ghi RUN_DIR/report.rerender.md")
    return ap


def _run(args) -> int:
    if bool(args.plan) == bool(args.project):
        raise PlanError("cần đúng một trong --plan hoặc --project (chỉ được bỏ cả hai khi dùng --rerender)")
    common = dict(run_id=args.run_id, only=args.only, yellow_exit=args.yellow_exit, sut_ref=args.sut_ref,
                  workers_dirs=[Path(d) for d in args.workers_dir] if args.workers_dir else None)
    if args.plan:
        result = engine.run_plan(args.plan, Path(args.runs_dir), on_skipped_gate_task=args.on_skipped_gate_task or "yellow",
                                 sut_root=args.sut_root, **common)
    else:
        if not args.mode:
            raise PlanError("--project cần --mode")
        result = engine.run_project(
            args.project, args.mode, Path(args.runs_dir), projects_dir=args.projects_dir, suites_dir=args.suites_dir,
            sut_root=args.sut_root, on_skipped_gate_task=args.on_skipped_gate_task,
            only_suites=[s.strip() for s in args.suites.split(",") if s.strip()] if args.suites else None, **common)
    print(result.report_md, end="")
    return result.exit_code


def _install_sigterm_handler() -> None:
    """POSIX: SIGTERM (huỷ job) => giết cây worker đang chạy rồi thoát 143. Windows không có SIGTERM kiểu này:
    executor dùng `taskkill /T` giết cả cây từ ngoài."""
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return

    def handler(signum, frame):
        runner.terminate_active()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handler)


def console() -> None:  # entry point của script `qc-agent`
    sys.exit(main(sys.argv[1:]))


def _rerender(args) -> int:
    run_dir = Path(args.rerender)
    specs, results = _load_dir(run_dir / "specs"), _load_dir(run_dir / "results")
    if not specs:
        raise PlanError(f"{run_dir}: không có specs/*.json — không phải thư mục của một run")
    plan = load_plan(run_dir / "plan.yaml")
    by_id = {task["task_id"]: task for task in plan["tasks"]}
    plan_only = {
        task_id: {"expect_status": by_id[task_id]["expect_status"]}
        for task_id in specs
        if task_id in by_id and "expect_status" in by_id[task_id]
    }
    plan_id = signature.plan_id(plan["text"])
    sut = signature.sut_id(_read_json(run_dir / "sut_identity.json"))
    signature_hex, gate = engine.judge(specs, results, plan_id, sut, args.yellow_exit, args.on_skipped_gate_task or "yellow")
    try:
        wallclock = float(_read_json(run_dir / "report.json")["details"]["wallclock_s"])
    except (PlanError, KeyError, TypeError, ValueError):
        wallclock = 0.0  # report.json gốc mất/hỏng: chỉ ảnh hưởng dòng chi phí, không ảnh hưởng verdict
    run_ctx = report.RunContext(
        run_id=next(iter(specs.values()))["run_id"], plan_id=plan_id, plan_name=plan["name"],
        plan_path=f"{run_dir.name}/plan.yaml", plan_text=plan["text"], sut_id=sut, run_signature=signature_hex,
        generated_at=engine.now(), wallclock_s=wallclock, specs=specs, results=results, gate=gate,
        canary=canary_alerts(results, plan_only))
    md, _ = report.render(run_ctx)  # render, không write: write sẽ đè report.md/report.json gốc
    (run_dir / "report.rerender.md").write_text(md, encoding="utf-8", newline="\n")
    print(md, end="")
    return gate.exit_code


def _load_dir(directory: Path) -> dict:
    return {path.stem: _read_json(path) for path in sorted(directory.glob("*.json"))}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise PlanError(f"không đọc được {path}: {error}") from None


if __name__ == "__main__":
    console()
