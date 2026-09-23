"""Job store trên PostgreSQL thật. Cần QC_TEST_DATABASE_URL (server có quyền CREATE DATABASE); thiếu thì skip.
Mỗi phiên test tạo một database riêng (qc_test_<hex>), chạy Alembic, rồi xoá; mỗi test bắt đầu từ bảng rỗng."""
import os
import threading
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from qc_agent.jobs import migrate, repository as repo
from qc_agent.jobs.db import make_engine, normalize_url, session_scope

ADMIN_URL = os.environ.get("QC_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="needs QC_TEST_DATABASE_URL (PostgreSQL)")

TABLES = ["artifacts", "job_tasks", "jobs", "api_tokens", "sessions", "users", "projects"]


def _url_for(name: str) -> str:
    return make_url(normalize_url(ADMIN_URL)).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def make_db():
    admin = make_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    created = []

    def factory() -> str:
        name = "qc_test_" + uuid.uuid4().hex[:10]
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return _url_for(name)

    yield factory
    with admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="module")
def db_url(make_db):
    url = make_db()
    migrate.upgrade(url)
    return url


@pytest.fixture
def engine(db_url):
    eng = make_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    yield eng
    eng.dispose()


@pytest.fixture
def project(engine):
    with session_scope(engine) as s:
        repo.sync_project(s, "noteboard", name="Noteboard")
    return "noteboard"


def new_job(engine, project, **kw):
    with session_scope(engine) as s:
        return repo.create_job(s, project, mode=kw.pop("mode", "manual"), source=kw.pop("source", "web"), **kw).id


def status_of(engine, job_id):
    with session_scope(engine) as s:
        return repo.get_job(s, job_id).status


# ---- migration ----

def test_migration_up_down_up_and_constraints(make_db):
    url = make_db()
    migrate.upgrade(url)
    eng = make_engine(url)
    assert set(TABLES) <= set(inspect(eng).get_table_names())
    with pytest.raises(IntegrityError):  # CHECK: email chữ thường
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO users (email) VALUES ('A@B.com')"))
    eng.dispose()
    migrate.downgrade(url)
    eng = make_engine(url)
    assert not (set(TABLES) & set(inspect(eng).get_table_names()))
    eng.dispose()
    migrate.upgrade(url)  # lên lại được sau khi xuống
    eng = make_engine(url)
    assert set(TABLES) <= set(inspect(eng).get_table_names())
    eng.dispose()


def test_normalize_url():
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"


# ---- project / job ----

def test_sync_project_is_an_idempotent_upsert(engine):
    with session_scope(engine) as s:
        a = repo.sync_project(s, "p1", name="Một", config_sha256="a" * 64)
    with session_scope(engine) as s:
        b = repo.sync_project(s, "p1", name="Đổi tên", config_sha256="b" * 64)
        assert s.execute(text("SELECT count(*) FROM projects")).scalar_one() == 1
    assert a.id == b.id and b.name == "Đổi tên" and b.config_sha256 == "b" * 64


def test_create_job_defaults_and_validation(engine, project):
    with session_scope(engine) as s:
        job = repo.create_job(s, project, mode="pr", source="ci", pr_number=7, sha="abc", suites=["api-contract"])
        assert job.status == "queued" and job.priority == 0 and job.cancel_requested is False
        assert job.created_at is not None and job.started_at is None and job.params == {}
        assert job.suites == ["api-contract"] and job.pr_number == 7
        with pytest.raises(repo.ProjectNotFound):
            repo.create_job(s, "khong-co", mode="pr", source="ci")
        with pytest.raises(ValueError):
            repo.create_job(s, project, mode="pr", source="cli")


def test_transitions_happy_path_sets_timestamps(engine, project):
    job_id = new_job(engine, project)
    with session_scope(engine) as s:
        running = repo.transition(s, job_id, "running", run_id="r-0001")
        assert running.status == "running" and running.started_at and running.heartbeat_at and running.finished_at is None
    with session_scope(engine) as s:
        done = repo.transition(s, job_id, "succeeded", gate_verdict="PASS", exit_code=0)
        assert done.status == "succeeded" and done.finished_at and done.gate_verdict == "PASS" and done.run_id == "r-0001"


@pytest.mark.parametrize("path,bad", [
    ([], "succeeded"),                      # queued -> succeeded: phải qua running
    ([], "timed_out"),
    (["running", "succeeded"], "running"),  # đã kết thúc: bất biến
    (["running", "failed"], "cancelled"),
    (["cancelled"], "running"),
])
def test_invalid_transitions_are_rejected_and_do_not_change_state(engine, project, path, bad):
    job_id = new_job(engine, project)
    for step in path:
        with session_scope(engine) as s:
            repo.transition(s, job_id, step)
    before = status_of(engine, job_id)
    with pytest.raises(repo.InvalidTransition):
        with session_scope(engine) as s:
            repo.transition(s, job_id, bad)
    assert status_of(engine, job_id) == before


def test_transition_unknown_job_or_field(engine, project):
    with session_scope(engine) as s:
        with pytest.raises(repo.JobNotFound):
            repo.transition(s, uuid.uuid4(), "running")
    job_id = new_job(engine, project)
    with session_scope(engine) as s:
        with pytest.raises(ValueError):
            repo.transition(s, job_id, "running", status="succeeded")  # không được sửa `status` qua fields
        with pytest.raises(repo.InvalidTransition):
            repo.transition(s, job_id, "queued")  # không có đường đi tới queued


def test_concurrent_claim_only_one_wins(engine, project):
    job_id = new_job(engine, project)
    barrier, results = threading.Barrier(6), []

    def claim():
        barrier.wait()
        try:
            with session_scope(engine) as s:
                repo.transition(s, job_id, "running")
            results.append("won")
        except repo.InvalidTransition:
            results.append("lost")

    threads = [threading.Thread(target=claim) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("won") == 1 and results.count("lost") == 5


def test_cancel_queued_running_and_terminal(engine, project):
    queued = new_job(engine, project)
    with session_scope(engine) as s:
        j = repo.request_cancel(s, queued)
        assert j.status == "cancelled" and j.finished_at

    running = new_job(engine, project)
    with session_scope(engine) as s:
        repo.transition(s, running, "running")
    with session_scope(engine) as s:
        j = repo.request_cancel(s, running)
        assert j.status == "running" and j.cancel_requested is True  # executor sẽ dừng rồi chuyển cancelled

    with pytest.raises(repo.InvalidTransition):
        with session_scope(engine) as s:
            repo.request_cancel(s, queued)  # đã kết thúc
    with session_scope(engine) as s:
        with pytest.raises(repo.JobNotFound):
            repo.request_cancel(s, uuid.uuid4())


def test_list_jobs_orders_newest_first_and_filters(engine):
    with session_scope(engine) as s:
        repo.sync_project(s, "a")
        repo.sync_project(s, "b")
    ids = [new_job(engine, "a", mode="pr", source="ci"), new_job(engine, "b", mode="manual"),
           new_job(engine, "a", mode="manual")]
    with session_scope(engine) as s:
        repo.transition(s, ids[0], "running")
        assert [j.id for j in repo.list_jobs(s)] == ids[::-1]
        assert {j.id for j in repo.list_jobs(s, project_slug="a")} == {ids[0], ids[2]}
        assert [j.id for j in repo.list_jobs(s, status="running")] == [ids[0]]
        assert [j.id for j in repo.list_jobs(s, mode="pr", source="ci")] == [ids[0]]
        assert len(repo.list_jobs(s, limit=2)) == 2 and len(repo.list_jobs(s, limit=2, offset=2)) == 1


def test_job_tasks_and_artifacts_uniqueness_and_cascade(engine, project):
    job_id = new_job(engine, project)
    with session_scope(engine) as s:
        repo.add_job_tasks(s, job_id, [
            {"task_id": "t-001", "worker": "schemathesis", "status": "pass", "gating": True, "duration_s": 1.5, "summary": {"k": 1}},
            {"task_id": "t-101", "status": "skipped", "lane": "discovery"}])
        repo.add_artifacts(s, job_id, [{"path": "report.json", "sha256": "a" * 64, "size_bytes": 10, "storage_uri": "file:///x/report.json"}])
    with session_scope(engine) as s:
        job = repo.get_job(s, job_id)
        assert [t.task_id for t in job.tasks] == ["t-001", "t-101"] and job.tasks[0].summary == {"k": 1}
        assert job.artifacts[0].path == "report.json"
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            repo.add_job_tasks(s, job_id, [{"task_id": "t-001", "status": "fail"}])  # (job_id, task_id) duy nhất
    with pytest.raises(IntegrityError):
        with session_scope(engine) as s:
            repo.add_artifacts(s, job_id, [{"path": "report.json", "storage_uri": "file:///y"}])
    with session_scope(engine) as s:
        s.execute(text("DELETE FROM jobs WHERE id = :i"), {"i": job_id})
        assert s.execute(text("SELECT count(*) FROM job_tasks")).scalar_one() == 0
        assert s.execute(text("SELECT count(*) FROM artifacts")).scalar_one() == 0


def test_status_check_constraint_blocks_garbage_at_the_database(engine, project):
    job_id = new_job(engine, project)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE jobs SET status = 'xong' WHERE id = :i"), {"i": job_id})
