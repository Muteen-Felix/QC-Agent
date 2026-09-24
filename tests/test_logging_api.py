"""API ghi log job.created / job.cancel_requested / job.ingested với job_id và project. Cần QC_TEST_DATABASE_URL."""
import io
import json

from qc_agent import logging_setup
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.test_api import _cfg, env, login, make_user, user  # noqa: F401  (fixtures/helpers)

pytestmark = requires_pg


def test_create_and_cancel_are_logged_with_job_id_and_project(user):
    buffer = io.StringIO()
    logging_setup.configure(buffer)
    try:
        job = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "environment": "staging"}).json()
        assert user.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 202
    finally:
        text = buffer.getvalue()
        logging_setup.configure(io.StringIO())
    events = [json.loads(line) for line in text.splitlines()]
    assert [e["event"] for e in events] == ["job.created", "job.cancel_requested"]
    assert all(e["job_id"] == job["id"] for e in events) and events[0]["project"] == "demo" and events[0]["environment"] == "staging"
    assert "STAGING_URL_SECRET" not in text  # tên biến bí mật của môi trường không lọt vào log
