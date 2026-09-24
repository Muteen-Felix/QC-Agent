"""Structured JSON logging (bước 23): mỗi dòng là một JSON có ngữ cảnh job_id/task_id/worker/project; không lộ bí mật."""
import io
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from qc_agent import logging_setup

ROOT = Path(__file__).resolve().parent.parent
SECRET = "sk-very-secret-value"


@pytest.fixture
def stream(monkeypatch):
    monkeypatch.delenv("QC_LOG_FORMAT", raising=False)
    monkeypatch.delenv("QC_LOG_LEVEL", raising=False)
    monkeypatch.delenv("QC_JOB_ID", raising=False)
    monkeypatch.delenv("QC_PROJECT", raising=False)
    buffer = io.StringIO()
    logging_setup.configure(buffer)
    yield buffer
    logging_setup.configure(io.StringIO())


def records(buffer):
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


def test_each_record_is_one_json_line_with_standard_fields(stream):
    log = logging.getLogger("qc_agent.test")
    logging_setup.event(log, "thing.happened", worker="k6", count=3, missing=None)
    (rec,) = records(stream)
    assert rec["event"] == "thing.happened" and rec["level"] == "INFO" and rec["logger"] == "qc_agent.test"
    assert rec["worker"] == "k6" and rec["count"] == 3 and "missing" not in rec
    assert rec["ts"].endswith("Z") and len(rec["ts"]) == 24


def test_bound_context_is_merged_nested_and_restored(stream):
    log = logging.getLogger("qc_agent.test")
    with logging_setup.bind(project="noteboard", job_id="j1"):
        with logging_setup.bind(task_id="t-1"):
            logging_setup.event(log, "inner")
        logging_setup.event(log, "outer")
    logging_setup.event(log, "none")
    inner, outer, none = records(stream)
    assert (inner["project"], inner["job_id"], inner["task_id"]) == ("noteboard", "j1", "t-1")
    assert "task_id" not in outer and outer["job_id"] == "j1" and "job_id" not in none


def test_context_is_isolated_between_threads_running_jobs(stream):
    log = logging.getLogger("qc_agent.test")
    gate = threading.Barrier(2)

    def work(job_id):
        with logging_setup.bind(job_id=job_id):
            gate.wait()  # cả hai luồng cùng ở trong ngữ cảnh của mình
            logging_setup.event(log, "in.thread", who=job_id)

    threads = [threading.Thread(target=work, args=(j,)) for j in ("a", "b")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r["job_id"] == r["who"] for r in records(stream)) and len(records(stream)) == 2


def test_field_names_cannot_overwrite_the_standard_ones(stream):
    logging_setup.event(logging.getLogger("qc_agent.test"), "x", logger="HACK", event="spoof", ts="0")
    (rec,) = records(stream)
    assert rec["logger"] == "qc_agent.test" and rec["event"] == "x" and rec["field_logger"] == "HACK" and rec["field_event"] == "spoof"
    assert rec["field_ts"] == "0" and rec["ts"] != "0"


def test_exception_is_summarised_without_traceback(stream):
    try:
        raise ValueError(f"boom with {SECRET}")
    except ValueError:
        logging.getLogger("qc_agent.test").exception("failed")
    (rec,) = records(stream)
    assert rec["exc"].startswith("ValueError") and "Traceback" not in json.dumps(rec)


def test_ambient_job_and_project_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("QC_JOB_ID", "job-42")
    monkeypatch.setenv("QC_PROJECT", "noteboard")
    buffer = io.StringIO()
    logging_setup.configure(buffer)
    logging_setup.event(logging.getLogger("qc_agent.test"), "ambient")
    (rec,) = records(buffer)
    assert rec["job_id"] == "job-42" and rec["project"] == "noteboard"
    monkeypatch.delenv("QC_JOB_ID"); monkeypatch.delenv("QC_PROJECT")
    logging_setup.configure(io.StringIO())


def test_text_format_and_level_are_configurable(monkeypatch):
    monkeypatch.setenv("QC_LOG_FORMAT", "text")
    monkeypatch.setenv("QC_LOG_LEVEL", "WARNING")
    buffer = io.StringIO()
    logging_setup.configure(buffer)
    log = logging.getLogger("qc_agent.test")
    logging_setup.event(log, "quiet")
    logging_setup.event(log, "loud", logging.WARNING, worker="k6")
    (line,) = buffer.getvalue().splitlines()
    assert "loud" in line and "worker=k6" in line and not line.startswith("{")
    monkeypatch.delenv("QC_LOG_FORMAT"); monkeypatch.delenv("QC_LOG_LEVEL")
    logging_setup.configure(io.StringIO())


def test_configure_is_idempotent_no_duplicate_lines(stream):
    logging_setup.configure(stream)
    logging_setup.configure(stream)
    logging_setup.event(logging.getLogger("qc_agent.test"), "once")
    assert len(records(stream)) == 1


def test_short_truncates_and_flattens():
    assert logging_setup.short("a\n  b\tc") == "a b c"
    assert len(logging_setup.short("x" * 500)) == logging_setup.DETAIL_MAX


# ---- qua CLI thật: log ở stderr, báo cáo ở stdout, log parse được từng dòng ----

def run_cli(tmp_path, *args, **env):
    full = {**os.environ, "PYTHONUTF8": "1", "QC_WORKERS_PATH": str(ROOT / "tests" / "fixtures" / "workers"), **env}
    return subprocess.run([sys.executable, "-m", "qc_agent.core.cli", "run", *args, "--runs-dir", str(tmp_path / "runs")],
                          cwd=ROOT, env=full, capture_output=True, text=True, encoding="utf-8", timeout=120)


def test_cli_run_emits_parseable_lifecycle_events_on_stderr_and_keeps_stdout_for_the_report(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(ROOT / "tests" / "fixtures" / "plans" / "demo.yaml"))
    events = [json.loads(line) for line in proc.stderr.splitlines() if line.strip()]  # MỌI dòng stderr đều là JSON
    assert proc.stdout.startswith("# QC Gate Report") and all("event" in e for e in events)
    names = [e["event"] for e in events]
    assert names[0] == "run.start" and names[-1] == "run.end" and names.count("task.start") == names.count("task.end") == 4
    end = events[-1]
    assert end["gate"] == "PASS" and end["exit_code"] == 0 and end["counts"] == {"pass": 4}
    task_ends = [e for e in events if e["event"] == "task.end"]
    assert all(e["run_id"] == "r-0001" and e["worker"] and e["task_id"].startswith("t-e") and e["status"] == "pass" for e in task_ends)


def test_cli_logs_skipped_and_failed_tasks_with_a_short_reason(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(ROOT / "tests" / "fixtures" / "plans" / "demo_fail.yaml"))
    events = [json.loads(line) for line in proc.stderr.splitlines() if line.strip()]
    bad = [e for e in events if e["event"] == "task.end" and e["status"] != "pass"]
    assert bad and all(len(e.get("detail", "")) <= logging_setup.DETAIL_MAX for e in bad)
    assert events[-1]["event"] == "run.end" and events[-1]["exit_code"] == proc.returncode == 1


def test_cli_plan_error_is_logged_as_run_aborted_and_project_is_in_context(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(tmp_path / "missing.yaml"))
    events = [json.loads(line) for line in proc.stderr.splitlines() if line.startswith("{")]
    assert proc.returncode == 3 and events[-1]["event"] == "run.aborted" and events[-1]["reason"] == "plan_or_config"


def test_secrets_and_spec_inputs_never_appear_in_logs(tmp_path):
    proc = run_cli(tmp_path, "--plan", str(ROOT / "tests" / "fixtures" / "plans" / "demo.yaml"), OPENAI_API_KEY=SECRET, QC_JOB_ID="job-1")
    assert SECRET not in proc.stderr and SECRET not in proc.stdout
    assert all(json.loads(line)["job_id"] == "job-1" for line in proc.stderr.splitlines() if line.strip())  # QC_JOB_ID của executor tới từng dòng
