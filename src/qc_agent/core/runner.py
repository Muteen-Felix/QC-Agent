"""Chạy các task theo thứ tự đã xếp sẵn: chọn worker -> spawn adapter (subprocess) -> validate -> retry/budget -> ghi file.
Runner chỉ biết `worker.module` do registry trả về. KHÔNG được có tên worker cụ thể nào trong file này."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from qc_agent.core import schema

SLACK_S = 30  # buffer cho adapter đóng gói kết quả; timeout của chính worker vẫn là budget.wallclock_s (ở _base)


def run_all(specs: dict[str, dict], plan_only: dict[str, dict], registry, run_dir: Path,
            parallel: bool = False) -> dict[str, dict]:
    """Chạy tuần tự theo thứ tự của `specs` (caller đã toposort: phụ thuộc đứng trước). Ghi run_dir/{specs,results}/<task_id>.json.
    Lỗi của một task thành result error/skipped, không dừng cả run. `registry.pick(spec, prefer=()) -> (Worker | None, reason)`."""
    if parallel:
        raise NotImplementedError("chạy song song là STEP 39; runner hiện chỉ chạy tuần tự")
    bad = [t for t in specs if Path(t).name != t]
    if bad:  # task_id là chuỗi tự do: không cho thoát khỏi run_dir
        raise ValueError(f"task_id không được chứa dấu phân cách đường dẫn: {bad}")
    run_dir, results = Path(run_dir), {}
    for tid, spec in specs.items():
        _write(run_dir / "specs" / f"{tid}.json", spec)
        results[tid] = _run_task(spec, plan_only.get(tid, {}), registry, results)
        _write(run_dir / "results" / f"{tid}.json", results[tid])
    return results


def _run_task(spec: dict, extra: dict, registry, done: dict) -> dict:
    worker, reason = registry.pick(spec, prefer=tuple(extra.get("prefer", ())))
    if worker is None:
        return schema.make_result(spec, "skipped", reason)
    if not worker.probe_ok:
        return schema.make_result(spec, "skipped", f"probe hỏng ({worker.name}): {worker.probe_reason}", worker.name)
    for dep in extra.get("depends_on", []):
        st = done.get(dep, {}).get("status")
        if st != "pass":
            return schema.make_result(spec, "skipped", f"phụ thuộc {dep} không đạt (status={st or 'chưa chạy'})", worker.name)

    result = _attempt(spec, worker)
    if result["status"] == "error" and spec["retry"]["max"] > 0:
        first = result["verdict"].get("rationale")
        result = _attempt(spec, worker)  # ĐÚNG 1 lần, kể cả khi lần 2 lại error. Không nhánh nào retry `fail`
        result.setdefault("adapter_notes", []).append(f"retry 1/1 sau error lần đầu: {first}")  # đừng che flakiness
    over = _over_budget(spec, result)
    if over:  # sau retry: vượt budget không được chạy lại (sẽ tiêu thêm)
        cost = result["cost"]
        result = _error(spec, worker, over)
        result["cost"] = cost  # giữ chi phí thật đã tiêu
    return result


def _attempt(spec: dict, worker) -> dict:
    """Một lần chạy adapter. Mọi thất bại (timeout/exit≠0/không phải JSON/sai contract) là `error`, không bao giờ `fail`."""
    t0 = time.perf_counter()
    try:
        code, out, err = _spawn(worker.module, spec)
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


def _spawn(module: str, spec: dict) -> tuple[int, str, str]:
    # Popen thay vì run(timeout=): run() chỉ giết tiến trình con trực tiếp và trên Windows còn treo ở communicate()
    # nếu tiến trình cháu (worker) giữ pipe.
    proc = subprocess.Popen(
        [sys.executable, "-m", module], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        start_new_session=(os.name != "nt"),  # POSIX: để killpg diệt được cả nhóm
    )
    try:
        out, err = proc.communicate(json.dumps(spec, ensure_ascii=False).encode("utf-8"),
                                    timeout=spec["budget"]["wallclock_s"] + SLACK_S)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass  # cây đã chết sẵn
    try:
        proc.kill()  # dự phòng cho tiến trình gốc
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)  # gom nốt output, tránh zombie
    except subprocess.TimeoutExpired:
        pass  # cháu còn giữ pipe: bỏ qua, đóng pipe ở đây sẽ treo luồng đọc


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ghi bytes: write_text trên Windows đổi \n thành \r\n
    path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
