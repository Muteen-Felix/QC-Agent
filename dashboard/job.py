"""Chạy `python orchestrator.py --plan plan.yaml` nền, đúng như scripts/demo.ps1 (tái dùng scripts/env.ps1).

Một job tại một thời điểm. Lock chỉ trong process dashboard: CLI chạy song song vẫn có thể đụng run_id
(core/cli.py:_next_run_id lấy max+1) — hạn chế của core, không sửa ở đây.
"""
from __future__ import annotations

import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import yaml

from dashboard.runs_reader import RUN_ID

SUT_PROBE = "http://127.0.0.1:8000/__qc/config"  # probe_url trong plan.yaml
COMMAND = ". .\\scripts\\env.ps1; $env:APP_BASE_URL='http://127.0.0.1:8000'; python orchestrator.py --plan plan.yaml"
DONE_CODES = {0, 1}  # 0 PASS, 1 FAIL: cả hai là kết quả gate hợp lệ; còn lại (3 = lỗi hệ thống) là error


def _plan_tasks(path: Path) -> int:
    """specs/*.json được ghi dần khi task bắt đầu nên không dùng làm mẫu số được."""
    try:
        return len(yaml.safe_load(path.read_text(encoding="utf-8")).get("tasks") or [])
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return 0


def sut_alive(url: str = SUT_PROBE, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except OSError:
        return False


class Job:
    def __init__(self, root: Path, runs_dir: Path, state_dir: Path, on_finish=None):
        self.root, self.runs_dir, self.state_dir = root, runs_dir, state_dir
        self.on_finish = on_finish  # callback(run_id, exit_code) khi job kết thúc (done hoặc error); lỗi bị nuốt
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.state, self.exit_code, self.log_path = "idle", None, None
        self.started = self.ended = None
        self._before: set[str] = set()

    def running(self) -> bool:
        return self.state == "running"

    def start(self) -> None:
        with self._lock:
            if self.running():
                raise RuntimeError("busy")
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._before = self._existing()
            self.log_path = self.state_dir / f"job-{time.strftime('%Y%m%d-%H%M%S')}.log"
            log = open(self.log_path, "wb")  # noqa: SIM115 — đóng trong _wait
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", COMMAND],
                cwd=self.root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self.state, self.exit_code, self.started, self.ended = "running", None, time.time(), None
            threading.Thread(target=self._wait, args=(self._proc, log), daemon=True).start()

    def _wait(self, proc: subprocess.Popen, log) -> None:
        code = proc.wait()
        log.close()
        self.exit_code, self.ended = code, time.time()
        self.state = "done" if code in DONE_CODES else "error"
        if self.on_finish is not None:
            try:
                self.on_finish(self.new_run_id(), code)
            except Exception:  # webhook lỗi không được làm hỏng trạng thái job
                pass

    def _existing(self) -> set[str]:
        if not self.runs_dir.is_dir():
            return set()
        return {p.name for p in self.runs_dir.iterdir() if p.is_dir() and RUN_ID.match(p.name)}

    def new_run_id(self) -> str | None:
        new = sorted(self._existing() - self._before, key=lambda n: int(n[2:]))
        return new[-1] if new else None

    def status(self) -> dict:
        run_id = self.new_run_id() if self.started else None
        done = total = 0
        if run_id:  # progress thật: số result đã ghi / số task trong bản plan.yaml orchestrator lưu vào run
            run_dir = self.runs_dir / run_id
            done = len(list((run_dir / "results").glob("*.json")))
            total = max(_plan_tasks(run_dir / "plan.yaml"), len(list((run_dir / "specs").glob("*.json"))), done)
        end = self.ended or time.time()
        return {
            "state": self.state,
            "exit_code": self.exit_code,
            "run_id": run_id,
            "elapsed_s": round(end - self.started, 1) if self.started else 0,
            "progress": {"done": done, "total": total},
            "log_tail": self._tail(),
        }

    def _tail(self, lines: int = 40) -> str:
        if not self.log_path or not self.log_path.is_file():
            return ""
        text = self.log_path.read_bytes().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
