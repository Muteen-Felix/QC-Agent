"""Executor trên PostgreSQL thật, chạy CLI thật trong tiến trình con (worker giả). Cần QC_TEST_DATABASE_URL."""
import os
import shutil
import subprocess
import threading
import time
import uuid

import pytest
from sqlalchemy import text

from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope
from qc_agent.jobs.executor import Executor, ExecutorConfig
from qc_agent.jobs.models import Job
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.projkit import ROOT, make_sut, task, write_project, write_suite

pytestmark = requires_pg


def slow_suite(sut, name, seconds):
    write_suite(sut, name, [task(f"t-{name}", inputs={"fixture": "data/mock_ok.json", "sleep_s": seconds},
                                 budget={"wallclock_s": 300, "tokens": 0, "usd": 0})])


@pytest.fixture
def env(tmp_path, engine):
    sut = make_sut(tmp_path)
    shutil.copy(ROOT / "tests" / "fixtures" / "mock_fail.json", sut / "data" / "mock_fail.json")
    projects = write_project(tmp_path)
    with session_scope(engine) as s:
        repo.sync_project(s, "demo", name="Demo")
    cfg = ExecutorConfig(runs_root=tmp_path / "runs", projects_dir=projects, tick_s=0.1, heartbeat_interval_s=0.3,
                         default_timeout_s=120, cancel_grace_s=5, poll_interval_s=0.2,
                         sut_root_for=lambda job, slug: sut)
    return type("Env", (), {"sut": sut, "cfg": cfg, "engine": engine, "executor": Executor(engine, cfg)})


def submit(env, **kw):
    with session_scope(env.engine) as s:
        return repo.create_job(s, "demo", mode=kw.pop("mode", "pr"), source=kw.pop("source", "ci"), **kw).id


def job_of(env, job_id):
    with session_scope(env.engine) as s:
        job = repo.get_job(s, job_id)
        _ = job.tasks, job.artifacts
        return job


def wait_status(env, job_id, status, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if job_of(env, job_id).status == status:
            return
        time.sleep(0.1)
    raise AssertionError(f"job không tới {status}: {job_of(env, job_id).status}")


def procs_with(marker: str) -> int:
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-Command",
               f"@(Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{marker}*' -and $_.ProcessId -ne $PID }}).Count"]
        return int(subprocess.run(cmd, capture_output=True, text=True).stdout.strip() or 0)
    out = subprocess.run(["pgrep", "-fc", marker], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


# ---- kết quả job ----

def test_successful_job_stores_verdict_tasks_and_artifacts(env):
    job_id = submit(env)
    assert env.executor.run_once() is True
    job = job_of(env, job_id)
    assert (job.status, job.gate_verdict, job.exit_code, job.run_id) == ("succeeded", "PASS", 0, str(job_id))
    assert job.started_at and job.finished_at and job.attempts == 1 and job.error is None
    assert {t.task_id: (t.status, t.lane, t.gating) for t in job.tasks} == {
        "t-1": ("pass", "gate", True), "t-2": ("pass", "gate", True), "t-9": ("pass", "discovery", False)}
    paths = {a.path: a for a in job.artifacts}
    assert "report.json" in paths and len(paths["report.json"].sha256) == 64 and paths["report.json"].storage_uri.startswith("file:")
    assert "executor.log" in paths
    assert env.executor.run_once() is False  # hết job


def test_gate_fail_is_job_failed_with_verdict_not_an_error_message(env):
    write_suite(env.sut, "core", [task("t-1", oracle={"kind": "checks", "required": ["always_true"]}, fixture="data/mock_fail.json")])
    job_id = submit(env)
    env.executor.run_once()
    job = job_of(env, job_id)
    assert (job.status, job.gate_verdict, job.exit_code, job.error) == ("failed", "FAIL", 1, None)


def test_system_error_exit_3_is_failed_with_log_tail_and_no_verdict(env):
    job_id = submit(env, suites=["khong-co-suite"])
    env.executor.run_once()
    job = job_of(env, job_id)
    assert job.status == "failed" and job.exit_code == 3 and job.gate_verdict is None
    assert "khong-co-suite" in job.error


def test_executor_bug_fails_the_job_instead_of_leaving_it_running(env, monkeypatch):
    monkeypatch.setattr(Executor, "_supervise", lambda self, job, slug: (_ for _ in ()).throw(RuntimeError("boom")))
    job_id = submit(env)
    env.executor.run_once()
    job = job_of(env, job_id)
    assert job.status == "failed" and "executor error: RuntimeError: boom" in job.error


def test_task_ids_and_suites_are_passed_to_the_cli(env):
    job_id = submit(env, mode="manual", suites=["core"], task_ids=["t-1"])
    env.executor.run_once()
    job = job_of(env, job_id)
    assert job.status == "succeeded" and [t.task_id for t in job.tasks] == ["t-1"]


# ---- timeout / huỷ: tiến trình con bị giết, không mồ côi ----

def test_job_timeout_kills_the_process_tree(env):
    slow_suite(env.sut, "slow", 61.234)
    job_id = submit(env, mode="manual", suites=["slow"], params={"timeout_s": 3})
    started = time.monotonic()
    env.executor.run_once()
    job = job_of(env, job_id)
    assert job.status == "timed_out" and "timeout" in job.error and job.finished_at
    assert time.monotonic() - started < 40  # worker ngủ 61s: nếu không giết được cây thì test chậm/treo
    time.sleep(1)
    assert procs_with("61.234") == 0


def test_cancel_running_job_stops_the_process_tree(env):
    slow_suite(env.sut, "slow", 62.345)
    job_id = submit(env, mode="manual", suites=["slow"])
    thread = threading.Thread(target=env.executor.run_once)
    thread.start()
    wait_status(env, job_id, "running")
    time.sleep(2)  # để worker thật sự khởi động
    assert procs_with("62.345") >= 1
    with session_scope(env.engine) as s:
        assert repo.request_cancel(s, job_id).cancel_requested is True
    thread.join(60)
    assert not thread.is_alive()
    job = job_of(env, job_id)
    assert job.status == "cancelled" and job.finished_at
    time.sleep(1)
    assert procs_with("62.345") == 0


def test_cancel_queued_job_is_never_run(env):
    job_id = submit(env)
    with session_scope(env.engine) as s:
        repo.request_cancel(s, job_id)
    assert env.executor.run_once() is False
    assert job_of(env, job_id).status == "cancelled" and job_of(env, job_id).attempts == 0


# ---- khoá môi trường / ưu tiên / tranh chấp ----

def test_concurrency_key_serializes_jobs_but_other_jobs_still_run(env):
    slow_suite(env.sut, "slow", 4)
    key = {"concurrency_key": "env:staging"}
    first = submit(env, mode="manual", suites=["slow"], params=key)
    second = submit(env, mode="manual", suites=["core"], params=key)  # cùng môi trường: phải chờ
    third = submit(env, mode="manual", suites=["core"])  # không khoá: chạy được ngay
    thread = threading.Thread(target=env.executor.run_once)
    thread.start()
    wait_status(env, first, "running")
    other = Executor(env.engine, env.cfg)
    assert other.run_once() is True  # nhận `third`, bỏ qua `second`
    assert job_of(env, third).status == "succeeded" and job_of(env, second).status == "queued"
    assert other.run_once() is False  # `second` vẫn bị khoá
    thread.join(60)
    assert job_of(env, first).status == "succeeded"
    assert other.run_once() is True  # khoá đã nhả
    assert job_of(env, second).status == "succeeded"


def test_lock_is_released_after_a_failed_job(env):
    key = {"concurrency_key": "env:x"}
    a = submit(env, suites=["khong-co"], params=key)
    b = submit(env, params=key)
    env.executor.run_once()
    assert job_of(env, a).status == "failed"
    env.executor.run_once()
    assert job_of(env, b).status == "succeeded"


def test_priority_then_age_and_skip_locked(env):
    low = submit(env)
    high = submit(env, priority=5)
    other_high = submit(env, priority=5)
    with session_scope(env.engine) as s1:
        first = repo.claim_next(s1)  # giữ transaction mở => hàng bị khoá
        assert first.id == high  # priority cao, cũ hơn trước
        with session_scope(env.engine) as s2:
            second = repo.claim_next(s2)  # SKIP LOCKED: không nhận trùng, không chờ
            assert second.id == other_high
    with session_scope(env.engine) as s:
        assert repo.claim_next(s).id == low
        assert repo.claim_next(s) is None


def test_serve_runs_queued_jobs_concurrently_until_stopped(env):
    env.cfg.max_concurrent = 2
    ids = [submit(env), submit(env)]
    stop = threading.Event()
    thread = threading.Thread(target=Executor(env.engine, env.cfg).serve, args=(stop,))
    thread.start()
    try:
        for job_id in ids:
            wait_status(env, job_id, "succeeded", timeout=60)
    finally:
        stop.set()
        thread.join(30)
    assert not thread.is_alive()


# ---- mất executor ----

def _age(env, job_id, **sets):
    cols = ", ".join(f"{k} = :{k}" for k in sets)
    with env.engine.begin() as conn:
        conn.execute(text(f"UPDATE jobs SET heartbeat_at = now() - interval '1 hour', {cols} WHERE id = :i"), {"i": job_id, **sets})


def test_reap_requeues_fails_or_cancels_stale_running_jobs(env):
    ids = {k: submit(env) for k in ("requeue", "exhausted", "cancel", "fresh")}
    with session_scope(env.engine) as s:
        for job_id in ids.values():
            repo.transition(s, job_id, "running")
    _age(env, ids["requeue"], attempts=1)
    _age(env, ids["exhausted"], attempts=2)
    _age(env, ids["cancel"], attempts=1, cancel_requested=True)
    result = env.executor.reap()
    assert result["requeued"] == [ids["requeue"]] and result["failed"] == [ids["exhausted"]] and result["cancelled"] == [ids["cancel"]]
    assert job_of(env, ids["requeue"]).status == "queued" and job_of(env, ids["requeue"]).started_at is None
    exhausted = job_of(env, ids["exhausted"])
    assert exhausted.status == "failed" and "executor lost" in exhausted.error
    assert job_of(env, ids["cancel"]).status == "cancelled"
    assert job_of(env, ids["fresh"]).status == "running"  # heartbeat còn mới: không đụng


def test_requeued_job_runs_again_and_replaces_previous_results(env):
    job_id = submit(env)
    env.executor.run_once()
    with env.engine.begin() as conn:  # giả lập: đã ghi kết quả rồi executor chết trước khi kết thúc
        conn.execute(text("UPDATE jobs SET status='running', finished_at=NULL, heartbeat_at=now() - interval '1 hour' WHERE id = :i"), {"i": job_id})
    env.executor.reap()
    env.executor.run_once()
    job = job_of(env, job_id)
    assert job.status == "succeeded" and job.attempts == 2 and len(job.tasks) == 3  # không nhân đôi task/artifact
    assert (env.cfg.runs_root / "demo" / f"{job_id}.attempt1" / "report.json").is_file()  # lần trước được giữ lại để điều tra


# ---- bảo mật ----

def test_child_env_hides_database_url_and_filters_user_env(env, monkeypatch):
    monkeypatch.setenv("QC_DATABASE_URL", "postgresql://u:secret@h/db")
    monkeypatch.setenv("QC_TEST_DATABASE_URL", "postgresql://u:secret@h/db")
    monkeypatch.setenv("KEEP_ME", "1")
    env.cfg.allowed_env = frozenset({"APP_BASE_URL"})
    job = Job(params={"env": {"APP_BASE_URL": "http://sut", "EVIL": "x", "QC_DATABASE_URL": "hack"}})
    child = env.executor._child_env(job)
    assert child["APP_BASE_URL"] == "http://sut" and child["KEEP_ME"] == "1"
    assert "EVIL" not in child and "QC_DATABASE_URL" not in child and "QC_TEST_DATABASE_URL" not in child


def test_sut_root_comes_only_from_the_server_side_resolver(env):
    job = Job(id=uuid.uuid4(), mode="pr", params={"sut_root": "/etc", "env": {}}, suites=None, task_ids=None, sha="abc123")
    args = env.executor._argv(job, "demo")
    assert "/etc" not in args and args[args.index("--sut-root") + 1] == str(env.sut)
    assert args[args.index("--sut-ref") + 1] == "abc123" and args[args.index("--run-id") + 1] == str(job.id)


# ---- hook on_finish (thông báo) ----

def test_on_finish_hook_gets_the_finished_job_and_run_dir(env):
    seen = []
    env.cfg.on_finish = lambda job, slug, run_dir: seen.append((job.status, job.gate_verdict, slug, run_dir.name, (run_dir / "report.json").is_file()))
    ok_id = submit(env)
    bad_id = submit(env, suites=["khong-co-suite"])
    ex = Executor(env.engine, env.cfg)
    ex.run_once()
    ex.run_once()
    assert seen == [("succeeded", "PASS", "demo", str(ok_id), True), ("failed", None, "demo", str(bad_id), False)]


def test_on_finish_hook_errors_never_affect_the_job_or_the_executor(env):
    calls = []

    def boom(job, slug, run_dir):
        calls.append(job.id)
        raise RuntimeError("webhook sập")
    env.cfg.on_finish = boom
    first, second = submit(env), submit(env)
    ex = Executor(env.engine, env.cfg)
    assert ex.run_once() is True and ex.run_once() is True  # hook lỗi không làm executor dừng
    assert calls == [first, second]
    assert job_of(env, first).status == "succeeded" and job_of(env, second).status == "succeeded"


def test_no_hook_is_the_default(env):
    assert env.cfg.on_finish is None
    submit(env)
    assert env.executor.run_once() is True
