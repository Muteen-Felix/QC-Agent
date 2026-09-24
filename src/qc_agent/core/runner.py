"""Chạy các task theo thứ tự đã xếp sẵn: chọn worker -> spawn adapter (subprocess) -> validate -> retry/budget -> ghi file.
Runner chỉ biết `worker.module` do registry trả về. KHÔNG được có tên worker cụ thể nào trong file này."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from qc_agent.core import egress, schema
from qc_agent.core.proctree import kill_tree

_ACTIVE: set = set()  # worker đang chạy; terminate_active() giết cây của chúng khi tiến trình bị SIGTERM (huỷ job)
_ACTIVE_LOCK = threading.Lock()
SLACK_S = 30  # buffer cho adapter đóng gói kết quả; timeout của chính worker vẫn là budget.wallclock_s (ở _base)


def run_all(specs: dict[str, dict], plan_only: dict[str, dict], registry, run_dir: Path,
            parallel: bool = False, cwd: Path | None = None, egress_policy: egress.EgressPolicy | None = None) -> dict[str, dict]:
    """Chạy tuần tự theo thứ tự của `specs` (caller đã toposort: phụ thuộc đứng trước). Ghi run_dir/{specs,results}/<task_id>.json.
    Lỗi của một task thành result error/skipped, không dừng cả run. `registry.pick(spec, prefer=()) -> (Worker | None, reason)`."""
    if parallel:
        raise NotImplementedError("chạy song song là STEP 39; runner hiện chỉ chạy tuần tự")
    bad = [t for t in specs if Path(t).name != t]
    if bad:  # task_id là chuỗi tự do: không cho thoát khỏi run_dir
        raise ValueError(f"task_id không được chứa dấu phân cách đường dẫn: {bad}")
    run_dir, results = Path(run_dir), {}
    policy = egress_policy or egress.LogOnlyPolicy()
    runs_root = run_dir.resolve().parent  # adapter dựng workdir từ QC_RUNS_DIR: phải khớp run_dir, truyền qua env của từng tiến trình
    for tid, spec in specs.items():
        _write(run_dir / "specs" / f"{tid}.json", spec)
        results[tid] = _run_task(spec, plan_only.get(tid, {}), registry, results, runs_root, cwd, policy, run_dir)
        _write(run_dir / "results" / f"{tid}.json", results[tid])
    return results


def _run_task(spec: dict, extra: dict, registry, done: dict, runs_root: Path, cwd: Path | None,
              policy: egress.EgressPolicy, run_dir: Path) -> dict:
    worker, reason = registry.pick(spec, prefer=tuple(extra.get("prefer", ())))
    if worker is None:
        return schema.make_result(spec, "skipped", reason)
    if not worker.probe_ok:
        return schema.make_result(spec, "skipped", f"probe hỏng ({worker.name}): {worker.probe_reason}", worker.name)
    for dep in extra.get("depends_on", []):
        st = done.get(dep, {}).get("status")
        if st != "pass":
            return schema.make_result(spec, "skipped", f"phụ thuộc {dep} không đạt (status={st or 'chưa chạy'})", worker.name)

    result = _attempt(spec, worker, runs_root, cwd, policy, run_dir, 1)
    if result["status"] == "error" and spec["retry"]["max"] > 0:
        first = result["verdict"].get("rationale")
        result = _attempt(spec, worker, runs_root, cwd, policy, run_dir, 2)  # ĐÚNG 1 lần, kể cả khi lần 2 lại error. Không nhánh nào retry `fail`
        result.setdefault("adapter_notes", []).append(f"retry 1/1 sau error lần đầu: {first}")  # đừng che flakiness
    over = _over_budget(spec, result)
    if over:  # sau retry: vượt budget không được chạy lại (sẽ tiêu thêm)
        cost = result["cost"]
        result = _error(spec, worker, over)
        result["cost"] = cost  # giữ chi phí thật đã tiêu
    result["worker"]["version"] = worker.version  # phiên bản tool đo được ở preflight; adapter không biết
    return result


def _attempt(spec: dict, worker, runs_root: Path, cwd: Path | None, policy: egress.EgressPolicy, run_dir: Path, attempt: int) -> dict:
    """Một lần chạy adapter. Mọi thất bại (timeout/exit≠0/không phải JSON/sai contract) là `error`, không bao giờ `fail`."""
    decision = egress.record(policy, run_dir, spec, worker, attempt)  # mỗi lần gọi (kể cả retry) là một lần dữ liệu có thể rời máy
    if decision.action == "deny":
        return schema.make_result(spec, "skipped", f"egress: bị chính sách từ chối ({decision.reason or 'không nêu lý do'})", worker.name)
    if decision.action != "allow":  # mask: chưa có cách thực thi => không được cho dữ liệu đi qua như thể đã che
        return _error(spec, worker, f"egress: quyết định '{decision.action}' chưa được hỗ trợ thực thi")
    t0 = time.perf_counter()
    try:
        code, out, err = _spawn(worker.module, spec, runs_root, cwd)
    except subprocess.TimeoutExpired:
        limit = spec["budget"]["wallclock_s"] + SLACK_S
        return _error(spec, worker, f"timeout: adapter không trả kết quả sau {limit}s (budget.wallclock_s + {SLACK_S}s)",
                      time.perf_counter() - t0)
    except OSError as e:
        return _error(spec, worker, f"crash: không spawn được adapter: {e}", time.perf_counter() - t0)
    wall = time.perf_counter() - t0
    if code != 0:
        return _error(spec, worker, f"crash: adapter exit={code}: {err.strip()[-300:]}", wall)
    try:
        result = json.loads(out)
    except ValueError:
        return _error(spec, worker, f"parse: stdout không phải JSON: {out.strip()[:200]!r}", wall)
    # check_* giả định result đúng hình dạng, nên chỉ chạy khi validate_result đã sạch
    violations = schema.validate_result(result) or schema.check_result_against_spec(spec, result)
    if violations:
        return _error(spec, worker, "contract: " + "; ".join(violations), wall)
    return result


def _over_budget(spec: dict, result: dict) -> str | None:
    """Kiểm SAU khi chạy (giới hạn thật lúc chạy là wallclock + max_steps của worker). None = trong budget."""
    for key in ("tokens", "usd"):
        used, cap = result["cost"].get(key), spec["budget"][key]
        if used is not None and used > cap:  # None = worker không báo: không đoán
            return f"vượt budget: cost.{key}={used} > budget.{key}={cap}"
    return None


def _error(spec: dict, worker, why: str, wall: float = 0.0) -> dict:
    res = schema.make_result(spec, "error", why, worker.name)
    res["cost"]["wallclock_s"] = round(wall, 3)
    return res


def _spawn(module: str, spec: dict, runs_root: Path, cwd: Path | None = None) -> tuple[int, str, str]:
    # Popen thay vì run(timeout=): run() chỉ giết tiến trình con trực tiếp và trên Windows còn treo ở communicate()
    # nếu tiến trình cháu (worker) giữ pipe.
    proc = subprocess.Popen(
        [sys.executable, "-m", module], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "QC_RUNS_DIR": str(runs_root)},
        start_new_session=(os.name != "nt"),  # POSIX: để killpg diệt được cả nhóm
    )
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)
    try:
        out, err = proc.communicate(json.dumps(spec, ensure_ascii=False).encode("utf-8"),
                                    timeout=spec["budget"]["wallclock_s"] + SLACK_S)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(proc)
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def terminate_active() -> None:
    """Giết cây tiến trình của mọi worker đang chạy (gọi từ signal handler SIGTERM của CLI)."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        _kill_tree(proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    kill_tree(proc)  # giết CẢ CÂY kể cả hậu duệ ở session khác (xem core/proctree.py)


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ghi bytes: write_text trên Windows đổi \n thành \r\n
    path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
