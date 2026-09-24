"""Executor: nhận job từ PostgreSQL và chạy bằng CHÍNH CLI của qc-agent trong một tiến trình con (một core, hai cách chạy).

Chạy trong tiến trình con là chủ đích: huỷ/timeout giết được cả cây tiến trình (worker, npx, chrome...) và lỗi của worker
không kéo sập service. Executor chỉ điều phối: nhận job, heartbeat, huỷ, timeout, khoá môi trường, ghi kết quả.

  - nhận job:   priority cao trước, `FOR UPDATE SKIP LOCKED` (nhiều executor không nhận trùng)
  - môi trường: job.params["concurrency_key"] -> pg_advisory_lock giữ suốt job; bận thì job ở lại queued (vd. perf trên staging)
  - huỷ:        cancel_requested (đọc ở mỗi heartbeat) -> dừng cây tiến trình -> cancelled
  - timeout:    job.params["timeout_s"] hoặc mặc định -> timed_out
  - mất executor: heartbeat quá cũ -> requeue (tối đa max_attempts) hoặc failed("executor lost")
  - bảo mật:    tiến trình con KHÔNG thấy QC_DATABASE_URL; env do người dùng đưa chỉ qua allowed_env; sut_root chỉ từ resolver phía server
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sqlalchemy import Engine, text

from qc_agent import logging_setup
from qc_agent.core import runner
from qc_agent.core.proctree import kill_tree
from qc_agent.core.project import ProjectResolver
from qc_agent.core.evidence import sha256_file
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import make_engine, session_scope
from qc_agent.jobs.models import TERMINAL_STATUSES, Job, Project

_LOG_TAIL = 2000
log = logging.getLogger("qc_agent.executor")


@dataclass
class ExecutorConfig:
    runs_root: Path
    projects_dir: Path | None = None
    poll_interval_s: float = 2.0
    tick_s: float = 0.5  # nhịp kiểm tra tiến trình con / timeout
    heartbeat_interval_s: float = 5.0  # cũng là độ trễ tối đa để thấy yêu cầu huỷ
    stale_after_s: float = 60.0
    max_attempts: int = 2
    default_timeout_s: float = 3600.0
    cancel_grace_s: float = 10.0
    max_concurrent: int = 1
    allowed_env: frozenset = frozenset()  # tên biến môi trường người dùng được đặt qua job.params["env"]
    scrub_env: frozenset = frozenset({"QC_DATABASE_URL", "QC_TEST_DATABASE_URL"})
    base_env: dict = field(default_factory=dict)
    sut_root_for: Callable[[Job, str], Path | None] | None = None  # (job, project_slug) -> checkout của SUT
    max_artifacts: int = 2000
    max_hash_bytes: int = 100 * 1024 * 1024
    on_finish: Callable[[Job, str, Path], None] | None = None  # (job đã kết thúc, slug, run_dir): thông báo... Lỗi của hook bị nuốt


class _AdvisoryLock:
    """pg_advisory_lock mức phiên trên một kết nối riêng: giữ suốt job, tự nhả nếu tiến trình chết (kết nối đứt)."""

    def __init__(self, engine: Engine, key: str):
        self.key = key
        self.conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")

    def try_acquire(self) -> bool:
        return bool(self.conn.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": self.key}).scalar())

    def close(self) -> None:
        try:
            with suppress(Exception):
                self.conn.execute(text("SELECT pg_advisory_unlock_all()"))  # trả kết nối về pool phải sạch khoá
        finally:
            self.conn.close()


class Executor:
    def __init__(self, engine: Engine, config: ExecutorConfig):
        self.engine, self.cfg = engine, config
        # cấu hình project phía server: checkout của SUT + biến môi trường của environment (bí mật ${env.X} thay ở đây, không vào DB)
        self.resolver = ProjectResolver(config.projects_dir) if config.projects_dir else None
        self._sut_root_for = config.sut_root_for or (
            (lambda job, slug: self.resolver.sut_checkout(slug)) if self.resolver else None)

    # ---- vòng đời ----

    def run_once(self) -> bool:
        """Nhận và chạy MỘT job (chặn tới khi xong). False nếu không có job chạy được."""
        claimed = self._claim()
        if claimed is None:
            return False
        self._run_job(*claimed)
        return True

    def serve(self, stop: threading.Event) -> None:
        pool, active, last_reap = ThreadPoolExecutor(max_workers=self.cfg.max_concurrent), set(), 0.0
        try:
            while not stop.is_set():
                active = {f for f in active if not f.done()}
                if time.monotonic() - last_reap >= self.cfg.stale_after_s / 2:
                    self.reap()
                    last_reap = time.monotonic()
                if len(active) < self.cfg.max_concurrent:
                    claimed = self._claim()
                    if claimed is not None:
                        active.add(pool.submit(self._run_job, *claimed))
                        continue
                stop.wait(self.cfg.poll_interval_s)
        finally:
            pool.shutdown(wait=True)  # job đang chạy được chạy nốt

    def reap(self) -> dict:
        with session_scope(self.engine) as s:
            return repo.requeue_stale(s, stale_after_s=self.cfg.stale_after_s, max_attempts=self.cfg.max_attempts)

    # ---- nhận job ----

    def _claim(self):
        held: dict[uuid.UUID, _AdvisoryLock] = {}

        def can_run(job: Job) -> bool:
            key = (job.params or {}).get("concurrency_key")
            if not key:
                return True
            lock = _AdvisoryLock(self.engine, str(key))
            if lock.try_acquire():
                held[job.id] = lock
                return True
            lock.close()
            return False

        try:
            with session_scope(self.engine) as s:
                job = repo.claim_next(s, can_run=can_run)
                slug = s.get(Project, job.project_id).slug if job else None
        except BaseException:
            for lock in held.values():
                lock.close()
            raise
        for job_id, lock in held.items():
            if job is None or job_id != job.id:
                lock.close()  # ứng viên đã giữ khoá môi trường nhưng không được nhận: nhả
        if job is None:
            return None
        return job, slug, held.get(job.id)

    # ---- chạy job ----

    def _run_job(self, job: Job, slug: str, lock: _AdvisoryLock | None) -> None:
        with logging_setup.bind(job_id=str(job.id), project=slug):  # mỗi job chạy trong luồng riêng: ngữ cảnh không lẫn giữa các job
            self._run_job_logged(job, slug, lock)

    def _run_job_logged(self, job: Job, slug: str, lock: _AdvisoryLock | None) -> None:
        logging_setup.event(log, "job.start", mode=job.mode, source=job.source, attempts=job.attempts,
                            concurrency_key=(job.params or {}).get("concurrency_key"))
        try:
            outcome = self._supervise(job, slug)
            self._finalize(job, slug, outcome)
        except BaseException as exc:  # lỗi của executor: job phải kết thúc chứ không kẹt ở running
            self._end(job.id, "failed", error=f"executor error: {type(exc).__name__}: {exc}")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            if lock is not None:
                lock.close()
        with suppress(Exception):  # log không được làm hỏng việc kết thúc job
            with session_scope(self.engine) as s:
                done = repo.get_job(s, job.id)
                logging_setup.event(log, "job.end", status=done.status, gate=done.gate_verdict, exit_code=done.exit_code,
                                    error=logging_setup.short(done.error) if done.error else None)
        self._notify_finished(job.id, slug)

    def _notify_finished(self, job_id: uuid.UUID, slug: str) -> None:
        """Gọi hook khi job đã ở trạng thái kết thúc. Hook hỏng không được ảnh hưởng job/executor."""
        if self.cfg.on_finish is None:
            return
        try:
            with session_scope(self.engine) as s:
                job = repo.get_job(s, job_id)
            if job.status in TERMINAL_STATUSES:
                self.cfg.on_finish(job, slug, self.cfg.runs_root / slug / str(job_id))
        except Exception as exc:  # noqa: BLE001
            logging_setup.event(log, "job.on_finish_failed", logging.ERROR, error_type=type(exc).__name__)

    def _argv(self, job: Job, slug: str) -> list[str]:
        args = ["run", "--project", slug, "--mode", job.mode, "--runs-dir", str(self.cfg.runs_root / slug),
                "--run-id", str(job.id)]
        if job.suites:
            args += ["--suites", ",".join(job.suites)]
        if job.task_ids:
            args += ["--only", ",".join(job.task_ids)]
        sut_root = self._sut_root_for(job, slug) if self._sut_root_for else None
        if sut_root:
            args += ["--sut-root", str(sut_root)]
        if self.cfg.projects_dir:
            args += ["--projects-dir", str(self.cfg.projects_dir)]
        if job.sha:
            args += ["--sut-ref", job.sha]
        return args

    def _child_env(self, job: Job, slug: str | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in self.cfg.scrub_env}
        env.update(self.cfg.base_env)
        if self.resolver and slug:
            env.update(self.resolver.env_vars(slug, (job.params or {}).get("environment")))
        env.update({k: str(v) for k, v in ((job.params or {}).get("env") or {}).items() if k in self.cfg.allowed_env})
        env["PYTHONUTF8"] = "1"
        env["QC_JOB_ID"] = str(job.id)  # tiến trình con gắn vào mọi dòng log (logging_setup.configure)
        if slug:
            env["QC_PROJECT"] = slug
        return env

    def _supervise(self, job: Job, slug: str) -> dict:
        log_path = self.cfg.runs_root / slug / f"{job.id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._archive_previous_attempt(job, slug)
        timeout = float((job.params or {}).get("timeout_s") or self.cfg.default_timeout_s)
        deadline, next_heartbeat = time.monotonic() + timeout, 0.0
        with log_path.open("wb") as log:
            proc = subprocess.Popen([sys.executable, "-m", "qc_agent.core.cli", *self._argv(job, slug)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=self._child_env(job, slug),
                                    start_new_session=(os.name != "nt"))
            while True:
                try:
                    return {"kind": "exited", "code": proc.wait(timeout=self.cfg.tick_s), "timeout": timeout}
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                if now >= next_heartbeat:
                    next_heartbeat = now + self.cfg.heartbeat_interval_s
                    with session_scope(self.engine) as s:
                        cancel = repo.heartbeat(s, job.id)
                    if cancel is None:  # không còn running (đã bị requeue/kết thúc bởi bên khác): dừng, không ghi đè
                        self._stop(proc)
                        return {"kind": "lost"}
                    if cancel:
                        self._stop(proc)
                        return {"kind": "cancelled"}
                if now >= deadline:
                    self._stop(proc)
                    return {"kind": "timeout", "timeout": timeout}

    def _archive_previous_attempt(self, job: Job, slug: str) -> None:
        """Job bị requeue chạy lại với CÙNG run_id: dời run_dir/log của lần trước sang `<id>.attemptN` (giữ để điều tra)
        thay vì để CLI từ chối vì run_id đã tồn tại."""
        base = self.cfg.runs_root / slug
        for name in (str(job.id), f"{job.id}.log"):
            old = base / name
            if old.exists():
                suffix = f".attempt{max(job.attempts - 1, 1)}"
                target = base / (name.removesuffix(".log") + suffix + (".log" if name.endswith(".log") else ""))
                n = 1
                while target.exists():
                    n += 1
                    target = base / (name.removesuffix(".log") + f"{suffix}-{n}" + (".log" if name.endswith(".log") else ""))
                old.rename(target)

    def _stop(self, proc: subprocess.Popen) -> None:
        """POSIX: SIGTERM cho CLI (handler giết cây worker ở session riêng) rồi mới ép; Windows: taskkill /T giết cả cây."""
        if os.name != "nt":
            with suppress(OSError):
                os.kill(proc.pid, signal.SIGTERM)
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=self.cfg.cancel_grace_s)
                return
        kill_tree(proc)
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)

    # ---- kết thúc job ----

    def _finalize(self, job: Job, slug: str, outcome: dict) -> None:
        kind = outcome["kind"]
        if kind == "lost":
            return
        run_dir = self.cfg.runs_root / slug / str(job.id)
        log_path = self.cfg.runs_root / slug / f"{job.id}.log"
        stored = self._store_results(job.id, run_dir, log_path)  # trước khi chuyển trạng thái: terminal => dữ liệu đã có
        if kind == "cancelled":
            self._end(job.id, "cancelled", run_id=str(job.id) if stored else None)
        elif kind == "timeout":
            self._end(job.id, "timed_out", run_id=str(job.id) if stored else None,
                      error=f"vượt timeout job {outcome['timeout']:g}s; tiến trình bị dừng")
        else:
            code, report = outcome["code"], _read_json(run_dir / "report.json")
            if code in (0, 1) and isinstance(report, dict) and report.get("gate_verdict") in ("PASS", "YELLOW", "FAIL"):
                verdict = report["gate_verdict"]
                self._end(job.id, "failed" if verdict == "FAIL" else "succeeded", gate_verdict=verdict, exit_code=code,
                          run_id=str(job.id))
            else:  # exit 3 (lỗi plan/cấu hình/nội bộ) hoặc không có report: lỗi hệ thống, không phải kết quả gate
                self._end(job.id, "failed", exit_code=code, run_id=str(job.id) if stored else None,
                          error=_tail(log_path) or f"qc-agent thoát với mã {code}")

    def _end(self, job_id: uuid.UUID, status: str, **fields) -> None:
        try:
            with session_scope(self.engine) as s:
                repo.transition(s, job_id, status, **fields)
        except (repo.InvalidTransition, repo.JobNotFound):
            pass  # job đã được bên khác kết thúc (vd. huỷ lúc queued->...): không ghi đè

    def _store_results(self, job_id: uuid.UUID, run_dir: Path, log_path: Path) -> bool:
        report = _read_json(run_dir / "report.json")
        tasks = []
        for path in sorted((run_dir / "results").glob("*.json")):
            result, spec = _read_json(path) or {}, _read_json(run_dir / "specs" / path.name) or {}
            verdict, cost = result.get("verdict") or {}, result.get("cost") or {}
            tasks.append({
                "task_id": result.get("task_id", path.stem), "worker": (result.get("worker") or {}).get("name"),
                "capability": spec.get("capability"), "lane": spec.get("lane"), "status": result.get("status", "error"),
                "gating": bool(verdict.get("gating")), "duration_s": cost.get("wallclock_s"),
                "tokens": cost.get("tokens"), "usd": cost.get("usd"),
                "summary": {"metrics": result.get("metrics") or {},
                            "findings": [f.get("title") for f in result.get("findings") or []],
                            "rationale": verdict.get("rationale")}})
        artifacts = self._artifacts(run_dir)
        if log_path.is_file():
            artifacts.append(self._artifact_row(log_path, "executor.log"))
        if not tasks and not artifacts and report is None:
            return False
        with session_scope(self.engine) as s:
            repo.replace_job_results(s, job_id, tasks, artifacts)
        return True

    def _artifacts(self, run_dir: Path) -> list[dict]:
        rows = []
        if run_dir.is_dir():
            for path in sorted(p for p in run_dir.rglob("*") if p.is_file())[: self.cfg.max_artifacts]:
                rows.append(self._artifact_row(path, path.relative_to(run_dir).as_posix()))
        return rows

    def _artifact_row(self, path: Path, rel: str) -> dict:
        size = path.stat().st_size
        return {"path": rel, "size_bytes": size, "sha256": sha256_file(path) if size <= self.cfg.max_hash_bytes else None,
                "storage_uri": path.resolve().as_uri()}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _tail(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")[-_LOG_TAIL:].strip()
    except OSError:
        return ""


def main() -> int:
    """python -m qc_agent.jobs.executor   (QC_DATABASE_URL, QC_RUNS_DIR, QC_PROJECTS_DIR từ env)"""
    from qc_agent import settings
    logging_setup.configure()
    cfg_env = settings.get()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(ValueError, OSError):
            signal.signal(sig, lambda *_: stop.set())
    def notify_hook(job: Job, slug: str, run_dir: Path) -> None:
        from qc_agent.integrations import notify
        base = os.environ.get("DASHBOARD_URL", "").rstrip("/")
        notify.notify_run(run_dir, exit_code=job.exit_code, status=job.status, verdict=job.gate_verdict,
                          label=f"{slug}/{job.mode}", link=f"{base}/#project={slug}&job={job.id}" if base else None)

    Executor(make_engine(), ExecutorConfig(runs_root=Path(cfg_env.runs_dir).resolve(), projects_dir=cfg_env.resolved_projects_dir,
                                           on_finish=notify_hook if os.environ.get("ALERT_WEBHOOK_URL") else None)).serve(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
