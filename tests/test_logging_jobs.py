"""Log có ngữ cảnh job_id/project xuyên suốt executor (PostgreSQL thật) và tiến trình `qc-agent run` con. Cần QC_TEST_DATABASE_URL."""
import io
import json

from qc_agent import logging_setup
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.test_executor import env, job_of, submit  # noqa: F401  (fixtures/helpers)

pytestmark = requires_pg


def parse(text):
    """Giống `jq -R 'fromjson? | select(.event)'`: bỏ qua dòng không phải JSON (báo cáo Markdown trộn trong executor.log)."""
    out = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and "event" in entry:
            out.append(entry)
    return out


def test_job_logs_carry_job_id_and_project_from_executor_down_to_task_events(env, tmp_path):
    parent = io.StringIO()
    logging_setup.configure(parent)
    try:
        job_id = submit(env, mode="manual", suites=["core"])
        assert env.executor.run_once() is True
        job = job_of(env, job_id)
    finally:
        logging_setup.configure(io.StringIO())
    assert job.status == "succeeded"

    executor_events = parse(parent.getvalue())
    assert [e["event"] for e in executor_events] == ["job.start", "job.end"]
    assert all(e["job_id"] == str(job_id) and e["project"] == "demo" for e in executor_events)
    assert executor_events[1]["status"] == "succeeded" and executor_events[1]["gate"] == "PASS" and executor_events[0]["mode"] == "manual"

    log_text = (env.cfg.runs_root / "demo" / f"{job_id}.log").read_text(encoding="utf-8")  # executor.log = stdout + stderr của tiến trình con
    child = parse(log_text)
    names = [e["event"] for e in child]
    assert names[0] == "run.start" and names[-1] == "run.end" and "task.start" in names and "task.end" in names
    assert all(e["job_id"] == str(job_id) and e["project"] == "demo" for e in child)  # QC_JOB_ID/QC_PROJECT do executor cấp
    assert all(e["run_id"] == str(job_id) for e in child)  # executor dùng id của job làm run_id
    assert {e["task_id"] for e in child if e["event"] == "task.end"} and all(e["status"] == "pass" for e in child if e["event"] == "task.end")
