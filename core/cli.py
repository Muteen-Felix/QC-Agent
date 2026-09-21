"""Điểm ghép của orchestrator: plan -> resolve -> worker -> verdict -> report. Không LLM, không tên worker cụ thể.
Exit code: PASS=0 · YELLOW=--yellow-exit · FAIL=1 · lỗi của HỆ THỐNG (PlanError, lỗi nội bộ, gọi sai lệnh)=3.
Phải chạy từ thư mục gốc repo: adapter được spawn bằng `python -m <module>` và đọc đường dẫn tương đối theo cwd."""
import argparse
import dataclasses
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core import registry, report, runner, signature
from core.plan import ROOT, PlanError, load_plan, resolve, toposort
from core.verdict import FAIL, PASS, YELLOW, gate_verdict

SYSTEM_ERROR = 3
_RUN_ID = re.compile(r"^r-(\d{4})$")


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse mặc định exit 2 — trùng với `--yellow-exit 2` của CI, nên ép về 3
        raise PlanError(f"tham số sai: {message}")


class _Registry:
    """runner chỉ cần `.pick(spec, prefer=)`; registry.pick nhận dict worker ở tham số đầu."""

    def __init__(self, workers: dict):
        self.workers = workers

    def pick(self, spec, prefer=()):
        return registry.pick(self.workers, spec, prefer)


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
    ap.add_argument("--runs-dir", default=os.environ.get("QC_RUNS_DIR") or "runs", help="mặc định $QC_RUNS_DIR hoặc runs")
    ap.add_argument("--rerender", metavar="RUN_DIR",
                    help="không chạy worker: tính lại verdict từ RUN_DIR/specs + results, ghi RUN_DIR/report.rerender.md")
    return ap


def _run(args) -> int:
    if not args.plan:
        raise PlanError("thiếu --plan (chỉ được bỏ khi dùng --rerender)")
    plan = load_plan(args.plan)
    runs_dir = Path(args.runs_dir)
    run_id = _next_run_id(runs_dir)
    plan_id = signature.plan_id(plan["text"])

    order = _select(plan, args.only)
    by_id = {task["task_id"]: task for task in plan["tasks"]}
    ctx = {"plan_id": plan_id, "run_id": run_id, "sut_identity_ref": "sut-pending", "sut": plan["sut"]}
    specs, extras = {}, {}
    for task_id in order:  # chỉ resolve task được chọn: biến ${env.X} của task khác không được làm hỏng lần chạy này
        specs[task_id], extras[task_id] = resolve(by_id[task_id], ctx)

    try:  # ctx["sut"] đã được resolve thay biến; sut_id chỉ biết được sau đó nên điền ngược vào spec
        identity = signature.sut_identity(ctx["sut"], ROOT)
    except (OSError, ValueError, TypeError) as error:
        raise PlanError(f"khối sut không hợp lệ: {error}") from None
    sut = signature.sut_id(identity)
    for spec in specs.values():
        spec["sut_identity_ref"] = sut

    workers = registry.load(ROOT / "workers")
    needed = {spec["capability"] for spec in specs.values()}
    for worker in workers.values():  # chỉ probe worker mà plan này cần: probe worker thừa tốn thời gian và có thể treo
        if needed & set(worker.capabilities):
            registry.probe(worker)

    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    signature.write_sut_identity(identity, run_dir)
    (run_dir / "plan.yaml").write_bytes(plan["text"].encode("utf-8"))  # bản lưu để --rerender dựng lại mục AUDIT
    os.environ["QC_RUNS_DIR"] = str(runs_dir.resolve())  # adapter dựng workdir từ biến này: phải khớp run_dir

    started = time.perf_counter()
    results = runner.run_all(specs, extras, _Registry(workers), run_dir)
    wallclock = time.perf_counter() - started

    signature_hex, gate = _judge(specs, results, plan_id, sut, args.yellow_exit)
    run_ctx = report.RunContext(
        run_id=run_id, plan_id=plan_id, plan_name=plan["name"], plan_path=Path(args.plan).as_posix(),
        plan_text=plan["text"], sut_id=sut, run_signature=signature_hex, generated_at=_now(),
        wallclock_s=round(wallclock, 3), specs=specs, results=results, gate=gate)
    md, _ = report.write(run_ctx, run_dir)
    print(md, end="")
    return gate.exit_code


def _rerender(args) -> int:
    run_dir = Path(args.rerender)
    specs, results = _load_dir(run_dir / "specs"), _load_dir(run_dir / "results")
    if not specs:
        raise PlanError(f"{run_dir}: không có specs/*.json — không phải thư mục của một run")
    plan = load_plan(run_dir / "plan.yaml")
    plan_id = signature.plan_id(plan["text"])
    sut = signature.sut_id(_read_json(run_dir / "sut_identity.json"))
    signature_hex, gate = _judge(specs, results, plan_id, sut, args.yellow_exit)
    try:
        wallclock = float(_read_json(run_dir / "report.json")["details"]["wallclock_s"])
    except (PlanError, KeyError, TypeError, ValueError):
        wallclock = 0.0  # report.json gốc mất/hỏng: chỉ ảnh hưởng dòng chi phí, không ảnh hưởng verdict
    run_ctx = report.RunContext(
        run_id=next(iter(specs.values()))["run_id"], plan_id=plan_id, plan_name=plan["name"],
        plan_path=f"{run_dir.name}/plan.yaml", plan_text=plan["text"], sut_id=sut, run_signature=signature_hex,
        generated_at=_now(), wallclock_s=wallclock, specs=specs, results=results, gate=gate)
    md, _ = report.render(run_ctx)  # render, không write: write sẽ đè report.md/report.json gốc
    (run_dir / "report.rerender.md").write_text(md, encoding="utf-8", newline="\n")
    print(md, end="")
    return gate.exit_code


def _judge(specs: dict, results: dict, plan_id: str, sut: str, yellow_exit: int):
    """verdict.gate_verdict không biết --yellow-exit; gán exit_code thật ở đây để report.json khớp exit của tiến trình."""
    gate = gate_verdict(results, specs)
    code = {PASS: 0, YELLOW: yellow_exit, FAIL: 1}[gate.value]
    return signature.run_signature(plan_id, sut, results, specs), dataclasses.replace(gate, exit_code=code)


def _select(plan: dict, only: str | None) -> list[str]:
    """Thứ tự chạy = toposort cả plan (bắt chu trình/phụ thuộc thiếu kể cả ở task không chọn), rồi lọc theo --only."""
    if not plan["tasks"]:
        raise PlanError("plan không có task nào: gate rỗng không có nghĩa là gate xanh")
    order = [task_id for layer in toposort(plan["tasks"]) for task_id in layer]
    if only is None:
        return order
    wanted = list(dict.fromkeys(item.strip() for item in only.split(",") if item.strip()))
    if not wanted:
        raise PlanError("--only rỗng")
    unknown = [task_id for task_id in wanted if task_id not in order]
    if unknown:
        raise PlanError(f"--only chứa task không có trong plan: {', '.join(unknown)}")
    depends = {task["task_id"]: task.get("depends_on", []) for task in plan["tasks"]}
    missing = [f"{task_id} cần {dep}" for task_id in wanted for dep in depends[task_id] if dep not in wanted]
    if missing:
        raise PlanError("--only thiếu task được depends_on: " + "; ".join(missing))
    return [task_id for task_id in order if task_id in wanted]


def _next_run_id(runs_dir: Path) -> str:
    """Lớn nhất + 1 (không phải đếm số thư mục: xoá r-0002 giữa chừng sẽ đếm ra id đã dùng)."""
    seen = [int(m.group(1)) for p in runs_dir.glob("r-*") if p.is_dir() and (m := _RUN_ID.match(p.name))]
    return f"r-{max(seen, default=0) + 1:04d}"


def _load_dir(directory: Path) -> dict:
    return {path.stem: _read_json(path) for path in sorted(directory.glob("*.json"))}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise PlanError(f"không đọc được {path}: {error}") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
