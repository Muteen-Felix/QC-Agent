"""API v1 trên PostgreSQL thật (Starlette TestClient). Cần QC_TEST_DATABASE_URL."""
import json
import uuid

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from qc_agent.api.app import create_app
from qc_agent.auth import service
from qc_agent.core.plan import PlanError
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import make_engine, session_scope
from qc_agent.jobs.executor import Executor, ExecutorConfig
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures)
from tests.projkit import make_sut, task, write_suite

pytestmark = requires_pg
PW = "correct horse battery"
JSON = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setenv("QC_ALLOWED_EMAIL_DOMAINS", "corp.test")
    monkeypatch.setenv("QC_COOKIE_SECURE", "false")  # TestClient nói http
    monkeypatch.setenv("QC_LOGIN_MAX_FAILURES", "3")


def write_projects(tmp_path, sut, **extra):
    d = tmp_path / "projects"
    d.mkdir(exist_ok=True)
    cfg = {"slug": "demo", "name": "Demo", "sut": {"files": []}, "sut_checkout": str(sut),
           "environments": {"local": {"description": "cục bộ", "env": {"APP_BASE_URL": "http://127.0.0.1:1"}},
                            "staging": {"env": {"APP_BASE_URL": "${env.STAGING_URL_SECRET}"}, "concurrency_key": "env:demo-staging", "timeout_s": 90}},
           "modes": {"pr": {"blocking_suites": ["core"], "advisory_suites": ["extra"], "on_skipped_gate_task": "fail"},
                     "manual": {"suites": "*"}}}
    cfg.update(extra)
    (d / "demo.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path, engine):
    sut = make_sut(tmp_path)
    projects = write_projects(tmp_path, sut)
    app = create_app(engine, runs_root=tmp_path / "runs", projects_dir=projects)
    with TestClient(app, base_url="http://testserver") as client:
        yield type("Env", (), {"client": client, "engine": engine, "sut": sut, "projects": projects, "tmp": tmp_path,
                               "runs": tmp_path / "runs", "app": app})


def make_user(engine, email="alice@corp.test", password=PW):
    with session_scope(engine) as s:
        service.create_user(s, email, password)


def login(client, email="alice@corp.test", password=PW):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.fixture
def user(env):
    make_user(env.engine)
    assert login(env.client).status_code == 200
    return env.client


def executor_for(env):
    return Executor(env.engine, ExecutorConfig(runs_root=env.runs, projects_dir=env.projects, tick_s=0.1,
                                               heartbeat_interval_s=0.3, default_timeout_s=120, cancel_grace_s=5))


# ---- health / khởi động ----

def test_health_ready_headers_and_project_sync(env):
    assert env.client.get("/healthz").json() == {"status": "ok"}
    r = env.client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
    assert r.headers["x-content-type-options"] == "nosniff" and env.client.get("/api/v1/auth/me").headers["cache-control"] == "no-store"
    with session_scope(env.engine) as s:
        row = s.execute(text("SELECT slug, name, config_sha256 FROM projects")).one()
    assert row[0] == "demo" and row[1] == "Demo" and len(row[2]) == 64  # đồng bộ lúc khởi động


def test_readyz_is_503_when_not_migrated(tmp_path, make_db):
    sut = make_sut(tmp_path)
    app = create_app(make_engine(make_db()), runs_root=tmp_path / "runs", projects_dir=write_projects(tmp_path, sut))
    client = TestClient(app)  # không vào lifespan: DB chưa migrate nên sync project sẽ lỗi
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["reason"] == "database" and "postgres" not in r.text.lower()


def test_invalid_project_config_stops_startup(tmp_path, engine):
    sut = make_sut(tmp_path)
    projects = write_projects(tmp_path, sut)
    (projects / "hong.yaml").write_text("slug: hong\nmodes: {}\n", encoding="utf-8")
    with pytest.raises(PlanError, match="không hợp lệ"):
        create_app(engine, runs_root=tmp_path / "runs", projects_dir=projects)


# ---- đăng nhập ----

def test_login_cookie_flags_me_and_logout(env):
    make_user(env.engine)
    r = login(env.client)
    assert r.status_code == 200 and r.json() == {"user": {"email": "alice@corp.test", "display_name": None}}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert env.client.get("/api/v1/auth/me").json()["user"]["email"] == "alice@corp.test"
    assert env.client.post("/api/v1/auth/logout").status_code == 204
    assert env.client.get("/api/v1/auth/me").status_code == 401  # phiên đã bị thu hồi phía server


def test_secure_cookie_by_default(env, monkeypatch):
    monkeypatch.setenv("QC_COOKIE_SECURE", "true")
    app = create_app(env.engine, runs_root=env.runs, projects_dir=env.projects)
    make_user(env.engine)
    with TestClient(app, base_url="https://testserver") as client:
        assert "secure" in login(client).headers["set-cookie"].lower()


def test_failed_logins_are_indistinguishable_and_lockout_persists(env):
    make_user(env.engine)
    wrong = login(env.client, password="sai mat khau!!")
    unknown = login(env.client, email="khong@corp.test")
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()  # không lộ email tồn tại
    login(env.client, password="sai mat khau!!")
    login(env.client, password="sai mat khau!!")  # lần sai thứ 3 (kể cả lần đầu): khoá
    assert login(env.client).status_code == 401  # mật khẩu đúng cũng bị từ chối: bộ đếm/khoá PHẢI đã commit dù trả 401
    with env.engine.begin() as conn:
        assert conn.execute(text("SELECT locked_until > now() FROM users")).scalar_one()


def test_login_validation_rejects_extra_fields_and_huge_input(env):
    assert env.client.post("/api/v1/auth/login", json={"email": "a@corp.test", "password": "x", "admin": True}).status_code == 422
    assert env.client.post("/api/v1/auth/login", json={"email": "a@corp.test", "password": "x" * 500}).status_code == 422


def test_change_password_revokes_other_sessions(env):
    make_user(env.engine)
    other = TestClient(env.app, base_url="http://testserver")
    assert login(other).status_code == 200 and login(env.client).status_code == 200
    r = env.client.post("/api/v1/auth/password", json={"current_password": "sai mat khau!!", "new_password": "brand new password 1"})
    assert r.status_code == 403
    assert env.client.post("/api/v1/auth/password", json={"current_password": PW, "new_password": "short"}).status_code == 422
    assert env.client.post("/api/v1/auth/password", json={"current_password": PW, "new_password": "brand new password 1"}).status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401  # phiên cũ bị thu hồi
    assert env.client.get("/api/v1/auth/me").status_code == 200  # phiên hiện tại được cấp lại
    assert login(TestClient(env.app), password="brand new password 1").status_code == 200


PROTECTED = [("get", "/api/v1/projects"), ("get", "/api/v1/projects/demo/suites"), ("get", "/api/v1/workers"),
             ("get", "/api/v1/jobs"), ("get", f"/api/v1/jobs/{uuid.uuid4()}"), ("post", f"/api/v1/jobs/{uuid.uuid4()}/cancel"),
             ("get", f"/api/v1/jobs/{uuid.uuid4()}/report.json"), ("get", f"/api/v1/jobs/{uuid.uuid4()}/log"),
             ("post", "/api/v1/projects/demo/jobs"), ("get", "/api/v1/auth/me"), ("post", "/api/v1/auth/password")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_every_protected_endpoint_requires_login(env, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(env.client, method)(path, **kwargs).status_code == 401


def test_csrf_origin_check_on_cookie_writes(user):
    body = {"mode": "manual", "suites": ["core"]}
    assert user.post("/api/v1/projects/demo/jobs", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert user.post("/api/v1/projects/demo/jobs", json=body, headers={"Origin": "http://testserver"}).status_code == 202
    assert user.post("/api/v1/projects/demo/jobs", json=body).status_code == 202  # client không phải trình duyệt: không có Origin


# ---- danh mục ----

def test_projects_suites_and_workers(user):
    projects = user.get("/api/v1/projects").json()
    assert projects[0]["slug"] == "demo" and projects[0]["runnable"] is True
    assert projects[0]["environments"]["staging"]["exclusive"] is True and "STAGING_URL_SECRET" not in json.dumps(projects)
    suites = {s["name"]: s for s in user.get("/api/v1/projects/demo/suites").json()}
    assert suites["core"]["modes"] == {"pr": "blocking", "manual": "any"} and suites["extra"]["modes"]["pr"] == "advisory"
    assert [t["task_id"] for t in suites["core"]["tasks"]] == ["t-1", "t-2"]
    assert user.get("/api/v1/projects/khong/suites").status_code == 404
    names = {w["name"] for w in user.get("/api/v1/workers").json()}
    assert {"k6", "schemathesis", "deepeval", "midscene-cli"} <= names


def test_project_without_checkout_is_409(tmp_path, engine):
    sut = make_sut(tmp_path)
    projects = write_projects(tmp_path, sut)
    cfg = yaml.safe_load((projects / "demo.yaml").read_text(encoding="utf-8"))
    cfg.pop("sut_checkout")
    (projects / "demo.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    make_user(engine)
    with TestClient(create_app(engine, runs_root=tmp_path / "runs", projects_dir=projects)) as client:
        login(client)
        assert client.get("/api/v1/projects").json()[0]["runnable"] is False
        assert client.get("/api/v1/projects/demo/suites").status_code == 409
        assert client.post("/api/v1/projects/demo/jobs", json={"mode": "manual"}).status_code == 409


# ---- tạo job / lịch sử / huỷ ----

def test_create_job_stores_server_side_params_and_hides_them(user, env):
    r = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "suites": ["core"], "task_ids": ["t-1"],
                                                     "environment": "staging", "timeout_s": 5000})
    assert r.status_code == 202
    job = r.json()
    assert (job["status"], job["source"], job["mode"], job["suites"], job["task_ids"], job["environment"]) == (
        "queued", "web", "manual", ["core"], ["t-1"], "staging")
    assert job["created_by"] == "alice@corp.test" and "params" not in job and "STAGING" not in json.dumps(job)
    with session_scope(env.engine) as s:
        params = repo.get_job(s, uuid.UUID(job["id"])).params
    assert params == {"environment": "staging", "concurrency_key": "env:demo-staging", "timeout_s": 5000.0}  # khoá/timeout từ cấu hình server


def test_timeout_is_capped_and_environment_timeout_is_default(user, env, monkeypatch):
    a = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "environment": "staging"}).json()
    with session_scope(env.engine) as s:
        assert repo.get_job(s, uuid.UUID(a["id"])).params["timeout_s"] == 90.0
    monkeypatch.setenv("QC_MAX_JOB_TIMEOUT_S", "100")
    app = create_app(env.engine, runs_root=env.runs, projects_dir=env.projects)
    with TestClient(app) as client:
        login(client)
        b = client.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "timeout_s": 99999}).json()
    with session_scope(env.engine) as s:
        assert repo.get_job(s, uuid.UUID(b["id"])).params["timeout_s"] == 100.0


@pytest.mark.parametrize("body,fragment", [
    ({"mode": "khong-co"}, "không có mode"),
    ({"mode": "manual", "suites": ["khong-co"]}, "ngoài policy"),
    ({"mode": "pr", "suites": ["perf"]}, "ngoài policy"),
    ({"mode": "manual", "task_ids": ["t-zzz"]}, "t-zzz"),
    ({"mode": "manual", "environment": "prod"}, "environment"),
])
def test_create_job_validation_reuses_core_rules(user, body, fragment):
    r = user.post("/api/v1/projects/demo/jobs", json=body)
    assert r.status_code == 422 and fragment in r.json()["detail"]


def test_create_job_rejects_lane_conflict_and_extra_fields(user, env):
    write_suite(env.sut, "core", [task("t-1", lane="discovery")])
    r = user.post("/api/v1/projects/demo/jobs", json={"mode": "pr"})
    assert r.status_code == 422 and "blocking_suites" in r.json()["detail"]
    assert user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "priority": 99, "params": {}}).status_code == 422
    assert user.post("/api/v1/projects/khong/jobs", json={"mode": "manual"}).status_code == 404


def test_open_job_cap_returns_429(env, monkeypatch):
    monkeypatch.setenv("QC_MAX_OPEN_JOBS_PER_PROJECT", "2")
    app = create_app(env.engine, runs_root=env.runs, projects_dir=env.projects)
    make_user(env.engine)
    with TestClient(app) as client:
        login(client)
        codes = [client.post("/api/v1/projects/demo/jobs", json={"mode": "manual"}).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


def test_history_filters_pagination_and_detail_404s(user):
    ids = [user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "suites": ["core"]}).json()["id"] for _ in range(3)]
    listing = user.get("/api/v1/jobs?limit=2").json()
    assert [j["id"] for j in listing["items"]] == ids[::-1][:2] and listing["limit"] == 2  # mới nhất trước
    assert [j["id"] for j in user.get("/api/v1/jobs?limit=2&offset=2").json()["items"]] == [ids[0]]
    assert len(user.get("/api/v1/jobs?project=demo&status=queued&source=web&mode=manual").json()["items"]) == 3
    assert user.get("/api/v1/jobs?status=running").json()["items"] == []
    assert user.get("/api/v1/jobs?project=khong").status_code == 404
    assert user.get("/api/v1/jobs?limit=0").status_code == 422 and user.get("/api/v1/jobs?limit=999").status_code == 422
    assert user.get(f"/api/v1/jobs/{ids[0]}").json()["tasks"] == []
    assert user.get("/api/v1/jobs/khong-phai-uuid").status_code == 404
    assert user.get(f"/api/v1/jobs/{uuid.uuid4()}").status_code == 404


def test_cancel_queued_then_conflict_when_terminal(user):
    job_id = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual"}).json()["id"]
    r = user.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 202 and r.json()["status"] == "cancelled"
    assert user.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 409
    assert user.post(f"/api/v1/jobs/{uuid.uuid4()}/cancel").status_code == 404


def test_cancel_running_job_sets_flag_and_reports_progress(user, env):
    job_id = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual"}).json()["id"]
    with session_scope(env.engine) as s:
        repo.transition(s, uuid.UUID(job_id), "running")
    run_dir = env.runs / "demo" / job_id
    (run_dir / "results").mkdir(parents=True)
    (run_dir / "results" / "t-1.json").write_text("{}", encoding="utf-8")
    (run_dir / "plan.yaml").write_text(yaml.safe_dump({"tasks": [{"task_id": "t-1"}, {"task_id": "t-2"}, {"task_id": "t-9"}]}), encoding="utf-8")
    detail = user.get(f"/api/v1/jobs/{job_id}").json()
    assert detail["status"] == "running" and detail["progress"] == {"done": 1, "total": 3}
    r = user.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 202 and r.json()["status"] == "running" and r.json()["cancel_requested"] is True


# ---- chạy thật qua executor rồi đọc lại bằng API ----

def test_web_job_runs_and_results_are_readable_through_the_api(user, env):
    job_id = user.post("/api/v1/projects/demo/jobs", json={"mode": "pr", "environment": "local"}).json()["id"]
    assert executor_for(env).run_once() is True
    job = user.get(f"/api/v1/jobs/{job_id}").json()
    assert (job["status"], job["gate_verdict"], job["exit_code"], job["error"]) == ("succeeded", "PASS", 0, None)
    assert {t["task_id"]: (t["status"], t["gating"]) for t in job["tasks"]} == {"t-1": ("pass", True), "t-2": ("pass", True), "t-9": ("pass", False)}
    assert {a["path"] for a in job["artifacts"]} >= {"report.json", "executor.log"}
    report = user.get(f"/api/v1/jobs/{job_id}/report.json")
    assert report.status_code == 200 and report.json()["gate_verdict"] == "PASS"
    assert "QC Gate Report" in user.get(f"/api/v1/jobs/{job_id}/report.md").text
    art = user.get(f"/api/v1/jobs/{job_id}/artifacts/report.json")
    assert art.status_code == 200 and art.headers["content-type"].startswith("application/json") and art.headers["x-content-type-options"] == "nosniff"
    assert "QC Gate Report" in user.get(f"/api/v1/jobs/{job_id}/log?tail=100000").text  # log của executor = stdout của CLI
    assert 0 < len(user.get(f"/api/v1/jobs/{job_id}/log?tail=20").text) <= 20  # `tail` giới hạn phần đọc từ cuối file
    listed = {a["path"] for a in user.get(f"/api/v1/jobs/{job_id}/artifacts").json()}
    assert "report.json" in listed


def test_missing_server_secret_for_environment_fails_the_job_without_leaking(user, env, monkeypatch):
    monkeypatch.delenv("STAGING_URL_SECRET", raising=False)
    job_id = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "environment": "staging"}).json()["id"]
    executor_for(env).run_once()
    job = user.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed" and "STAGING_URL_SECRET" in job["error"]  # tên biến, không có giá trị


# ---- artifact: chống path traversal / HTML không tin cậy ----

def test_artifact_serving_is_confined_and_untrusted_types_are_downloads(user, env):
    job_id = user.post("/api/v1/projects/demo/jobs", json={"mode": "manual", "suites": ["core"]}).json()["id"]
    executor_for(env).run_once()
    run_dir = env.runs / "demo" / job_id
    (run_dir / "evil.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (run_dir / "pic.svg").write_text("<svg onload=alert(1)/>", encoding="utf-8")
    secret = env.tmp / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    with session_scope(env.engine) as s:
        repo.add_artifacts(s, uuid.UUID(job_id), [
            {"path": "evil.html", "storage_uri": "x"}, {"path": "pic.svg", "storage_uri": "x"},
            {"path": "../../../secret.txt", "storage_uri": "x"}, {"path": "khong-con.txt", "storage_uri": "x"}])
    for name in ("evil.html", "pic.svg"):
        r = user.get(f"/api/v1/jobs/{job_id}/artifacts/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
        assert "attachment" in r.headers["content-disposition"]  # không render trong origin của ứng dụng
    assert user.get(f"/api/v1/jobs/{job_id}/artifacts/../../../secret.txt").status_code == 404
    assert "TOP-SECRET" not in user.get(f"/api/v1/jobs/{job_id}/artifacts/%2e%2e/%2e%2e/%2e%2e/secret.txt").text
    assert user.get(f"/api/v1/jobs/{job_id}/artifacts/khong-con.txt").status_code == 404  # có trong DB nhưng file đã mất
    assert user.get(f"/api/v1/jobs/{job_id}/artifacts/chua-dang-ky.txt").status_code == 404  # không có trong bảng artifacts
    assert user.get(f"/api/v1/jobs/{job_id}/artifacts/../../../secret.txt").status_code == 404
    # cả trường hợp bảng artifacts bị ghi đường dẫn thoát: vẫn bị chặn ở tầng file
    assert user.get(f"/api/v1/jobs/{job_id}/artifacts/..%2F..%2F..%2Fsecret.txt").status_code == 404


# ---- CI ingest ----

def report(verdict="PASS", exit_code=0):
    return {"run_id": "r-0001", "gate_verdict": verdict, "exit_code": exit_code,
            "deterministic_view": [{"task_id": "t-001", "worker": "schemathesis", "capability": "api.property", "status": "pass", "gating": True}],
            "details": {"generated_at": "2026-09-24T00:00:00+00:00", "wallclock_s": 3.2,
                        "results": {"t-001": {"status": "pass", "cost": {"wallclock_s": 1.5, "tokens": 0, "usd": 0.0}, "metrics": {"m": 1}},
                                    "t-101": {"status": "skipped", "cost": {}, "metrics": {}}}}}


def run_body(**over):
    body = {"external_id": "gh-123-1", "mode": "pr", "pr_number": 7, "sha": "abc123", "branch": "feat/x", "report": report(),
            "report_md": "# QC Gate Report"}
    body.update(over)
    return body


@pytest.fixture
def token(env):
    with session_scope(env.engine) as s:
        _, raw = service.create_api_token(s, "demo", "ci")
    return raw


def ingest(env, token, body, **kw):
    headers = {"Authorization": f"Bearer {token}", **JSON} if token else JSON
    return env.client.post("/api/v1/projects/demo/runs", content=json.dumps(body) if not isinstance(body, (bytes, str)) else body, headers=headers, **kw)


def test_ingest_creates_job_visible_in_history_and_is_idempotent(env, token):
    r = ingest(env, token, run_body())
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["status"] == "succeeded"
    job_id = r.json()["id"]
    again = ingest(env, token, run_body(report=report("FAIL", 1)))  # cùng external_id: không tạo trùng, không ghi đè
    assert again.status_code == 200 and again.json() == {**again.json(), "id": job_id, "created": False}
    make_user(env.engine)
    login(env.client)
    items = env.client.get("/api/v1/jobs?source=ci").json()["items"]
    assert [(j["id"], j["source"], j["pr_number"], j["sha"], j["branch"], j["gate_verdict"]) for j in items] == [(job_id, "ci", 7, "abc123", "feat/x", "PASS")]
    detail = env.client.get(f"/api/v1/jobs/{job_id}").json()
    by = {t["task_id"]: t for t in detail["tasks"]}
    assert by["t-001"]["worker"] == "schemathesis" and by["t-001"]["gating"] is True and by["t-101"]["status"] == "skipped"
    assert env.client.get(f"/api/v1/jobs/{job_id}/report.json").json()["gate_verdict"] == "PASS"
    assert "QC Gate Report" in env.client.get(f"/api/v1/jobs/{job_id}/report.md").text


def test_ingest_fail_verdict_is_a_failed_job(env, token):
    r = ingest(env, token, run_body(external_id="gh-2-1", report=report("FAIL", 1)))
    assert r.status_code == 201 and r.json()["status"] == "failed"


def test_ingest_authentication_and_project_scope(env, token):
    with session_scope(env.engine) as s:
        repo.sync_project(s, "other")
        _, other_raw = service.create_api_token(s, "other", "ci-other")
        row_id = s.execute(text("SELECT id FROM api_tokens WHERE name='ci'")).scalar_one()
    assert ingest(env, None, run_body()).status_code == 401  # thiếu token
    assert ingest(env, "qca_khong-ton-tai", run_body()).status_code == 401
    assert ingest(env, other_raw, run_body()).status_code == 403  # token của project khác
    make_user(env.engine)
    login(env.client)
    assert env.client.post("/api/v1/projects/demo/runs", content=json.dumps(run_body()), headers=JSON).status_code == 401  # cookie phiên không thay được token
    with session_scope(env.engine) as s:
        service.revoke_api_token(s, row_id)
    assert ingest(env, token, run_body(external_id="x-1")).status_code == 401  # đã thu hồi
    assert env.client.post("/api/v1/projects/khong/runs", content=b"{}", headers={"Authorization": f"Bearer {token}"}).status_code == 404


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(external_id="có dấu cách"), lambda b: b.update(external_id=""), lambda b: b.update(mode=""),
    lambda b: b.update(extra=1), lambda b: b["report"].update(gate_verdict="XANH"), lambda b: b["report"].update(exit_code=True),
    lambda b: b["report"].pop("exit_code"), lambda b: b["report"].pop("details"), lambda b: b.pop("report"),
    lambda b: b.update(pr_number=-1), lambda b: b.update(sha="x" * 100)])
def test_ingest_rejects_malformed_bodies(env, token, mutate):
    body = run_body()
    mutate(body)
    assert ingest(env, token, body).status_code == 422


def test_ingest_rejects_garbage_and_oversized_bodies(env, token, monkeypatch):
    assert ingest(env, token, b"khong phai json").status_code == 422
    monkeypatch.setenv("QC_MAX_INGEST_BYTES", "200")
    app = create_app(env.engine, runs_root=env.runs, projects_dir=env.projects)
    with TestClient(app) as client:
        r = client.post("/api/v1/projects/demo/runs", content=json.dumps(run_body()), headers={"Authorization": f"Bearer {token}", **JSON})
    assert r.status_code == 413
    with session_scope(env.engine) as s:
        assert s.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 0  # bị từ chối thì không để lại bản ghi


def test_ingest_records_token_last_used_even_when_the_body_is_rejected(env, token):
    assert ingest(env, token, run_body(mode="")).status_code == 422
    with env.engine.begin() as conn:
        assert conn.execute(text("SELECT last_used_at IS NOT NULL FROM api_tokens")).scalar_one()
