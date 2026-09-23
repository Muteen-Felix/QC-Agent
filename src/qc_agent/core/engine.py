"""Lõi chạy plan dùng CHUNG cho CLI và (sau này) executor của service: plan -> resolve -> worker -> verdict -> report.
Không print, không đọc argv, không sửa os.environ: gọi được nhiều lần, song song, trong cùng một tiến trình.
Không LLM, không tên worker cụ thể."""
from __future__ import annotations

import dataclasses
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from qc_agent import settings
from qc_agent.core import registry, report, runner, signature
from qc_agent.core.plan import ROOT, PlanError, load_plan, resolve, toposort
from qc_agent.core.verdict import FAIL, PASS, YELLOW, GateVerdict, canary_alerts, gate_verdict

_RUN_ID = re.compile(r"^r-(\d{4})$")


class _Registry:
    """runner chỉ cần `.pick(spec, prefer=)`; registry.pick nhận dict worker ở tham số đầu."""

    def __init__(self, workers: dict):
        self.workers = workers

    def pick(self, spec, prefer=()):
        return registry.pick(self.workers, spec, prefer)


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    exit_code: int
    gate: GateVerdict
    report_md: str
    run_ctx: report.RunContext


def run_plan(plan_path, runs_dir, *, only: str | None = None, yellow_exit: int = 0,
             workers_dirs=None, run_id: str | None = None) -> RunResult:
    """Chạy một plan. `run_id=None` => cấp `r-NNNN` (nguyên tử: hai lời gọi song song không bao giờ trùng id).
    Lỗi plan/cấu hình raise PlanError/ManifestError TRƯỚC khi worker chạy, và dọn thư mục run đã tạo."""
    plan = load_plan(plan_path)
    runs_dir = Path(runs_dir)
    plan_id = signature.plan_id(plan["text"])
    order = _select(plan, only)
    by_id = {task["task_id"]: task for task in plan["tasks"]}

    runs_dir_existed = runs_dir.exists()
    run_id, run_dir = _reserve_run_dir(runs_dir, run_id)
    try:
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

        workers = registry.load_many(list(workers_dirs) if workers_dirs else settings.get().workers_dirs)
        needed = {spec["capability"] for spec in specs.values()}
        for worker in workers.values():  # chỉ probe worker mà plan này cần: probe worker thừa tốn thời gian và có thể treo
            if needed & set(worker.capabilities):
                registry.probe(worker)

        signature.write_sut_identity(identity, run_dir)
        (run_dir / "plan.yaml").write_bytes(plan["text"].encode("utf-8"))  # bản lưu để --rerender dựng lại mục AUDIT
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)  # thư mục này do chính lời gọi này tạo và chưa có worker nào chạy
        if not runs_dir_existed:
            try:
                runs_dir.rmdir()  # lỗi plan không được để lại thư mục runs/ rỗng (chỉ xoá nếu rỗng)
            except OSError:
                pass
        raise

    started = time.perf_counter()
    results = runner.run_all(specs, extras, _Registry(workers), run_dir)  # runner tự truyền QC_RUNS_DIR cho từng worker
    wallclock = time.perf_counter() - started

    signature_hex, gate = judge(specs, results, plan_id, sut, yellow_exit)
    run_ctx = report.RunContext(
        run_id=run_id, plan_id=plan_id, plan_name=plan["name"], plan_path=Path(plan_path).as_posix(),
        plan_text=plan["text"], sut_id=sut, run_signature=signature_hex, generated_at=now(),
        wallclock_s=round(wallclock, 3), specs=specs, results=results, gate=gate,
        canary=canary_alerts(results, extras))
    md, _ = report.write(run_ctx, run_dir)
    return RunResult(run_id, run_dir, gate.exit_code, gate, md, run_ctx)


def judge(specs: dict, results: dict, plan_id: str, sut: str, yellow_exit: int):
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


def _reserve_run_dir(runs_dir: Path, run_id: str | None) -> tuple[str, Path]:
    """Giữ chỗ run_dir bằng mkdir không exist_ok (nguyên tử trên filesystem); tự cấp `r-NNNN` = lớn nhất + 1
    (không phải đếm số thư mục: xoá r-0002 giữa chừng sẽ đếm ra id đã dùng) và thử lại nếu bị chiếm."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    if run_id is not None:
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            raise PlanError(f"run_id đã tồn tại: {run_id}") from None
        return run_id, run_dir
    while True:
        seen = [int(m.group(1)) for p in runs_dir.glob("r-*") if p.is_dir() and (m := _RUN_ID.match(p.name))]
        candidate = f"r-{max(seen, default=0) + 1:04d}"
        try:
            (runs_dir / candidate).mkdir()
        except FileExistsError:
            continue
        return candidate, runs_dir / candidate


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
