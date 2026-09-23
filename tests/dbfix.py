"""Fixture PostgreSQL dùng chung (tests/test_jobs_db.py, tests/test_executor.py). Cần QC_TEST_DATABASE_URL, thiếu thì skip."""
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from qc_agent.jobs import migrate, repository as repo
from qc_agent.jobs.db import make_engine, normalize_url, session_scope

ADMIN_URL = os.environ.get("QC_TEST_DATABASE_URL", "")
requires_pg = pytest.mark.skipif(not ADMIN_URL, reason="needs QC_TEST_DATABASE_URL (PostgreSQL)")

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


