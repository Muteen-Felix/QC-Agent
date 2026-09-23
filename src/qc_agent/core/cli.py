"""Điểm ghép của orchestrator: plan -> resolve -> worker -> verdict -> report. Không LLM, không tên worker cụ thể.
Exit code: PASS=0 · YELLOW=--yellow-exit · FAIL=1 · lỗi của HỆ THỐNG (PlanError, lỗi nội bộ, gọi sai lệnh)=3.
Phải chạy từ thư mục gốc repo: adapter được spawn bằng `python -m <module>` và đọc đường dẫn tương đối theo cwd."""
import argparse
import json
import sys
from pathlib import Path

from qc_agent import settings
from qc_agent.core import engine, registry, report, signature
from qc_agent.core.plan import PlanError, load_plan
from qc_agent.core.verdict import canary_alerts

SYSTEM_ERROR = 3


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse mặc định exit 2 — trùng với `--yellow-exit 2` của CI, nên ép về 3
        raise PlanError(f"tham số sai: {message}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # report có tiếng Việt + emoji; console Windows mặc định là cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        if not 0 <= args.yellow_exit <= 255:
            raise PlanError(f"tham số sai: --yellow-exit phải trong 0..255, nhận {args.yellow_exit}")
        return _rerender(args) if args.rerender else _run(args)
    except SystemExit as exit_:  # --help
        return exit_.code if isinstance(exit_.code, int) else 0
    except (PlanError, registry.ManifestError) as error:
        print(f"LỖI PLAN/CẤU HÌNH: {error}", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 — lỗi nội bộ của orchestrator: exit 3, không bao giờ được lẫn với verdict
        print(f"LỖI NỘI BỘ: {type(error).__name__}: {error}", file=sys.stderr)
    return SYSTEM_ERROR


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="orchestrator.py", description="QC gate: chạy plan, gộp verdict tất định, ghi report.")
    ap.add_argument("--plan", help="file plan YAML (bắt buộc trừ khi có --rerender)")
    ap.add_argument("--only", help="chỉ chạy các task này, vd t-a,t-b (phải kèm đủ task được depends_on)")
    ap.add_argument("--yellow-exit", type=int, default=0, metavar="N", help="exit code khi gate YELLOW (mặc định 0)")
    ap.add_argument("--on-skipped-gate-task", choices=engine.SKIPPED_POLICIES, default="yellow",
                    help="task gate bị skipped: yellow (mặc định, theo --yellow-exit) hoặc fail (gate FAIL, exit 1)")
    ap.add_argument("--runs-dir", default=str(settings.get().runs_dir), help="mặc định $QC_RUNS_DIR hoặc runs")
    ap.add_argument("--workers-dir", action="append", metavar="DIR",
                    help="thư mục manifest worker (lặp được); mặc định $QC_WORKERS_PATH hoặc workers/")
    ap.add_argument("--rerender", metavar="RUN_DIR",
                    help="không chạy worker: tính lại verdict từ RUN_DIR/specs + results, ghi RUN_DIR/report.rerender.md")
    return ap


def _run(args) -> int:
    if not args.plan:
        raise PlanError("thiếu --plan (chỉ được bỏ khi dùng --rerender)")
    result = engine.run_plan(
        args.plan, Path(args.runs_dir), only=args.only, yellow_exit=args.yellow_exit,
        workers_dirs=[Path(d) for d in args.workers_dir] if args.workers_dir else None,
        on_skipped_gate_task=args.on_skipped_gate_task)
    print(result.report_md, end="")
    return result.exit_code


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
    signature_hex, gate = engine.judge(specs, results, plan_id, sut, args.yellow_exit, args.on_skipped_gate_task)
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


