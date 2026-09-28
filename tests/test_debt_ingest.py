"""P2-5: nợ test đi vào sổ từ hai đường — CI ingest (report.json đẩy lên) và executor (Mode 2, full-scan) — cùng một hàm apply_debt.
Phần DB cần QC_TEST_DATABASE_URL (PostgreSQL); test executor chạy CLI thật trong tiến trình con và cần git."""
import json
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from debtkit import MAIN_PY, PING, Repo
from qc_agent.api.app import create_app
from qc_agent.auth import service
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope
from qc_agent.jobs.debt_report import DebtReportError, debt_scan_from_report, github_pr_url
from qc_agent.jobs.executor import Executor, ExecutorConfig
from qc_agent.jobs.models import DebtEntry, JobTask
from qc_agent.scaffold import templates as t
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)

PW = "correct horse battery"
JSON = {"Content-Type": "application/json"}
A, B, C = ("api_endpoint", "GET /ping"), ("api_endpoint", "POST /items"), ("ui_route", "/settings")


def fid(pair):
    return f"debt:{pair[0]}:{pair[1]}"


def debt_result(pairs, *, full=False, status=None, new=None, lane="discovery"):
    pairs = list(pairs)
    return {"status": status or ("fail" if pairs else "pass"), "lane": lane, "cost": {"wallclock_s": 0.2, "tokens": 0, "usd": 0.0},
            "metrics": {"debt.new": len(pairs) if new is None else new, "debt.full_scan": full},
            "findings": [{"finding_id": fid(p), "title": f"Nợ test [{p[0]}]: {p[1]} chưa có test"} for p in pairs]
                        + ([{"finding_id": "f-thr-debt.new", "title": "debt.new vi phạm"}] if pairs else [])}


def report(debt=None, verdict="YELLOW"):
    results = {"t-001": {"status": "pass", "lane": "gate", "cost": {"wallclock_s": 1.5, "tokens": 0, "usd": 0.0}, "metrics": {"m": 1}, "findings": []}}
    if debt is not None:
        results["t-103"] = debt
    return {"run_id": "r-0001", "gate_verdict": verdict, "exit_code": 0,
            "deterministic_view": [{"task_id": "t-001", "worker": "schemathesis", "capability": "api.property", "status": "pass", "gating": True}],
            "details": {"generated_at": "2026-09-24T00:00:00+00:00", "wallclock_s": 3.2, "results": results}}


# ───────────────────────── trích nợ từ report (hàm thuần, không cần DB) ─────────────────────────

def test_scan_extracts_only_debt_findings_and_ignores_the_oracle_finding():
    scan = debt_scan_from_report(report(debt_result([A, C], full=True)))
    assert scan.findings == {A, C} and scan.full_scan is True


def test_scan_ignores_reports_without_a_debt_task_and_untrusted_statuses():
    assert debt_scan_from_report(report()) is None
    assert debt_scan_from_report(report(debt_result([A], status="error"))) is None  # error không có quyền nói "hết nợ"
    assert debt_scan_from_report(report(debt_result([], status="skipped", full=True))) is None
    assert debt_scan_from_report({"details": "x"}) is None


def test_scan_mixed_full_and_diff_tasks_is_treated_as_diff_so_nothing_gets_closed():
    rep = report(debt_result([A], full=True))
    rep["details"]["results"]["t-104"] = debt_result([B], full=False)
    scan = debt_scan_from_report(rep)
    assert scan.findings == {A, B} and scan.full_scan is False


@pytest.mark.parametrize("bad", [
    debt_result([A], new=2),  # số nợ lệch danh sách (report bị cắt)
    {**debt_result([A]), "findings": [{"finding_id": "debt:api_endpoint", "title": "x"}]},  # sai dạng
    {**debt_result([A]), "findings": [{"finding_id": "debt::GET /x", "title": "x"}]},
])
def test_scan_refuses_to_guess_when_the_report_contradicts_itself(bad):
    with pytest.raises(DebtReportError):
        debt_scan_from_report(report(bad))


def test_github_pr_url():
    assert github_pr_url("Muteen-Felix/vahan-rpa", 7) == "https://github.com/Muteen-Felix/vahan-rpa/pull/7"
    assert github_pr_url(None, 7) is None and github_pr_url("o/r", None) is None and github_pr_url("o/r", 0) is None
    assert github_pr_url("not a repo", 7) is None and github_pr_url("o/r", True) is None


# ───────────────────────── CI ingest ─────────────────────────

@pytest.fixture
def api(tmp_path, engine, monkeypatch):
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "corp.test")
    monkeypatch.setenv("QC_COOKIE_SECURE", "false")
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "demo.yaml").write_text(yaml.safe_dump({"slug": "demo", "name": "Demo", "repo": "acme/demo", "sut": {"files": []},
                                                        "modes": {"pr": {"blocking_suites": ["core"]}, "manual": {"suites": "*"}}}), encoding="utf-8")
    app = create_app(engine, runs_root=tmp_path / "runs", projects_dir=projects)
    with TestClient(app, base_url="http://testserver") as client:
        with session_scope(engine) as s:
            _, raw = service.create_api_token(s, "demo", "ci")
            service.create_user(s, "alice@corp.test", PW)
        yield type("Api", (), {"client": client, "engine": engine, "token": raw})


def ingest(api, rep, external_id="gh-1-1", **over):
    body = {"external_id": external_id, "mode": "pr", "pr_number": 7, "sha": "abc", "branch": "feat/x", "report": rep, "report_md": "# r", **over}
    return api.client.post("/api/v1/projects/demo/runs", content=json.dumps(body), headers={"Authorization": f"Bearer {api.token}", **JSON})


def login(api):
    assert api.client.post("/api/v1/auth/login", json={"email": "alice@corp.test", "password": PW}).status_code == 200


def open_keys(api) -> set:
    with session_scope(api.engine) as s:
        return {(r.kind, r.surface) for r in repo.list_debt(s, "demo")}


def count_rows(api) -> int:
    with session_scope(api.engine) as s:
        return s.scalar(select(func.count()).select_from(DebtEntry))


@requires_pg
def test_ingest_twice_with_same_external_id_does_not_duplicate_debt(api):
    first = ingest(api, report(debt_result([A, C])))
    assert first.status_code == 201 and first.json()["created"] is True
    assert open_keys(api) == {A, C} and count_rows(api) == 2
    again = ingest(api, report(debt_result([A, C])))
    assert again.status_code == 200 and again.json()["created"] is False and again.json()["id"] == first.json()["id"]
    assert count_rows(api) == 2


@requires_pg
def test_second_run_of_same_pr_refreshes_last_seen_instead_of_duplicating(api):
    first = ingest(api, report(debt_result([A])), external_id="gh-1-1").json()["id"]
    second = ingest(api, report(debt_result([A, B])), external_id="gh-2-1").json()["id"]
    assert count_rows(api) == 2
    with session_scope(api.engine) as s:
        rows = {(r.kind, r.surface): r for r in repo.list_debt(s, "demo")}
    assert str(rows[A].opened_job_id) == first and str(rows[A].last_seen_job_id) == second
    assert str(rows[B].opened_job_id) == second
    assert rows[A].pr_url == "https://github.com/acme/demo/pull/7"  # repo của project + pr_number


@requires_pg
def test_ingest_keeps_the_real_lane_of_each_task_and_stores_finding_titles(api):
    assert ingest(api, report(debt_result([A]))).status_code == 201
    with session_scope(api.engine) as s:
        tasks = {row.task_id: row for row in s.scalars(select(JobTask))}
    assert tasks["t-103"].lane == "discovery" and tasks["t-001"].lane == "gate"  # không còn lane=None cho task không gating
    assert tasks["t-103"].gating is False and tasks["t-001"].gating is True
    assert "Nợ test [api_endpoint]: GET /ping chưa có test" in tasks["t-103"].summary["findings"]


@requires_pg
def test_legacy_payload_without_lane_falls_back_to_the_old_inference(api):
    rep = report(debt_result([], status="skipped"))
    for result in rep["details"]["results"].values():
        result.pop("lane")
        result.pop("findings")
    assert ingest(api, rep).status_code == 201
    with session_scope(api.engine) as s:
        tasks = {row.task_id: row for row in s.scalars(select(JobTask))}
    assert tasks["t-001"].lane == "gate" and tasks["t-103"].lane is None and tasks["t-001"].summary["findings"] == []


@requires_pg
def test_diff_scan_ingest_never_closes_and_full_scan_ingest_closes_what_is_gone(api):
    ingest(api, report(debt_result([A, B])), external_id="pr-1")
    ingest(api, report(debt_result([C])), external_id="pr-2")  # PR khác, diff-scan: chỉ mở C
    assert open_keys(api) == {A, B, C}
    ingest(api, report(debt_result([], full=False)), external_id="pr-3")  # diff-scan sạch: không đóng gì
    assert open_keys(api) == {A, B, C}
    ingest(api, report(debt_result([B], full=True)), external_id="main-1", mode="manual")  # full-scan: A, C đã có test
    assert open_keys(api) == {B}
    login(api)
    closed = api.client.get("/api/v1/projects/demo/debt?open=false").json()["items"]
    assert {(i["kind"], i["surface"]): i["closed_reason"] for i in closed if i["closed_at"]} == {A: "covered", C: "covered"}


@requires_pg
def test_error_task_and_inconsistent_report_write_no_debt_but_still_record_the_job(api):
    ingest(api, report(debt_result([A])), external_id="pr-1")
    assert ingest(api, report(debt_result([], status="error", full=True)), external_id="main-err", mode="manual").status_code == 201
    assert open_keys(api) == {A}  # lần quét lỗi không được nói "hết nợ"
    assert ingest(api, report(debt_result([B], new=5, full=True)), external_id="main-bad", mode="manual").status_code == 201
    assert open_keys(api) == {A}  # report tự mâu thuẫn: bỏ, không đoán; job vẫn được ghi
    with session_scope(api.engine) as s:
        assert len(repo.list_jobs(s, project_slug="demo")) == 3


@requires_pg
def test_debt_failure_does_not_lose_the_job(api, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "apply_debt", boom)
    assert ingest(api, report(debt_result([A]))).status_code == 201
    with session_scope(api.engine) as s:
        assert len(repo.list_jobs(s, project_slug="demo")) == 1 and repo.list_debt(s, "demo") == []


# ───────────────────────── GET /projects/{slug}/debt ─────────────────────────

@requires_pg
def test_debt_endpoint_requires_login_and_filters(api):
    ingest(api, report(debt_result([A, B, C])))
    assert api.client.get("/api/v1/projects/demo/debt").status_code == 401
    login(api)
    body = api.client.get("/api/v1/projects/demo/debt?open=true").json()
    assert body["open"] is True and body["truncated"] is False
    assert [(i["kind"], i["surface"]) for i in body["items"]] == sorted([A, B, C])
    assert set(body["items"][0]) == {"id", "kind", "surface", "opened_at", "closed_at", "closed_reason", "pr_url", "opened_job_id",
                                     "last_seen_job_id", "last_seen_at"}
    assert [i["surface"] for i in api.client.get("/api/v1/projects/demo/debt?kind=ui_route").json()["items"]] == ["/settings"]
    assert api.client.get("/api/v1/projects/demo/debt?limit=2").json()["truncated"] is True
    assert api.client.get("/api/v1/projects/demo/debt?limit=0").status_code == 422
    assert api.client.get("/api/v1/projects/khong-co/debt").status_code == 404
    ingest(api, report(debt_result([B], full=True)), external_id="main-1", mode="manual")
    assert [i["surface"] for i in api.client.get("/api/v1/projects/demo/debt").json()["items"]] == ["POST /items"]  # mặc định open=true
    everything = api.client.get("/api/v1/projects/demo/debt?open=false").json()
    assert everything["open"] is False and len(everything["items"]) == 3


# ───────────────────────── executor (Mode 2, full-scan thật) ─────────────────────────

@requires_pg
@pytest.mark.skipif(shutil.which("git") is None, reason="cần binary git")
def test_executor_full_scan_job_opens_debt_then_closes_it_once_a_test_exists(tmp_path, engine, monkeypatch):
    sut = Repo(tmp_path / "sut")
    sut.write("app/main.py", MAIN_PY + PING)
    sut.write(".qc-agent/suites/coverage-debt.yaml", t.coverage_debt_suite())
    sut.commit("base")
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "demo.yaml").write_text(yaml.safe_dump({"slug": "demo", "sut": {"files": []}, "modes": {"manual": {"suites": "*"}}}), encoding="utf-8")
    with session_scope(engine) as s:
        repo.sync_project(s, "demo", name="Demo")
    monkeypatch.setenv("APP_BASE_URL", "http://sut.invalid")
    monkeypatch.delenv("QC_DIFF_BASE", raising=False)  # Mode 2: không có base => full-scan
    executor = Executor(engine, ExecutorConfig(runs_root=tmp_path / "runs", projects_dir=projects, tick_s=0.1, heartbeat_interval_s=0.3,
                                               default_timeout_s=120, cancel_grace_s=5, sut_root_for=lambda job, slug: sut.root))

    def run_job() -> str:
        with session_scope(engine) as s:
            job_id = repo.create_job(s, "demo", mode="manual", source="web", suites=["coverage-debt"]).id
        assert executor.run_once() is True
        with session_scope(engine) as s:
            job = repo.get_job(s, job_id)
            assert job.status == "succeeded" and job.gate_verdict == "PASS"  # mode manual chưa bật advisory_yellow: chỉ ghi sổ
            assert {task.task_id: task.lane for task in job.tasks}["t-103"] == "discovery"
        return str(job_id)

    first = run_job()
    with session_scope(engine) as s:
        rows = {(r.kind, r.surface): r for r in repo.list_debt(s, "demo")}
    assert set(rows) == {("api_endpoint", "GET /health"), ("api_endpoint", "GET /ping")}
    assert all(str(r.opened_job_id) == first for r in rows.values())

    sut.write("tests/test_ping.py", 'def test_ping(client):\n    client.get("/ping")\n')  # thêm test cho /ping
    sut.commit("thêm test")
    second = run_job()
    with session_scope(engine) as s:
        assert {(r.kind, r.surface) for r in repo.list_debt(s, "demo")} == {("api_endpoint", "GET /health")}
        closed = [r for r in repo.list_debt(s, "demo", open_only=False) if r.closed_at]
        assert [(r.surface, r.closed_reason) for r in closed] == [("GET /ping", "covered")]
        health = next(r for r in repo.list_debt(s, "demo") if r.surface == "GET /health")
        assert str(health.opened_job_id) == first and str(health.last_seen_job_id) == second  # chạy lại: làm mới, không nhân đôi
        assert s.scalar(select(func.count()).select_from(DebtEntry)) == 2
