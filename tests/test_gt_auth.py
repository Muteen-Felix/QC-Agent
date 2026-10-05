"""Xác thực cho test Ground-Truth: `.qc-agent/ground-truth/auth.yaml` + runtime trong gt-conftest.py.tmpl.

Phần tích hợp bật một SUT mẫu có `POST /api/auth/login` (token HMAC kiểu phiên, KHÔNG phải JWT), `GET /api/me` (cần Bearer) và `POST /api/auth/logout` (thu hồi phiên),
rồi chạy pytest thật trên `tests_gt/` đã render. Mật khẩu và token không được lọt vào đầu ra của test.
"""
import importlib.util
import os
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
import yaml

from qc_agent.groundtruth import auth as gt_auth
from qc_agent.groundtruth import check as gt_check
from qc_agent.groundtruth import generate as gt_generate
from qc_agent.groundtruth import render as gt_render
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.agent import build_first_user
from qc_agent.groundtruth.prd import parse_prd

ROOT = Path(__file__).resolve().parent.parent
GT = ".qc-agent/ground-truth"
PASSWORD = "s3cret-Pass-123"

APP = '''
import secrets
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

app = FastAPI()
SESSIONS = set()


class Login(BaseModel):
    username: str
    password: str


def session(authorization):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if token not in SESSIONS:
        raise HTTPException(401, "unauthorized")
    return token


@app.post("/api/auth/login")
def login(body: Login, authorization: str | None = Header(default=None)):
    if authorization is not None:
        raise HTTPException(400, "login must not receive credentials")   # phân biệt được việc runtime gắn token nhầm vào endpoint đăng nhập
    if (body.username, body.password) != ("qc-user", "%s"):
        raise HTTPException(401, "bad credentials")
    token = "tok-" + secrets.token_hex(12)
    SESSIONS.add(token)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/header-present")
def header_present(request: Request):
    return {"present": "authorization" in request.headers}   # phân biệt "header rỗng" với "không gửi header"


@app.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    session(authorization)
    return {"username": "qc-user"}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    SESSIONS.discard(session(authorization))
    return {"ok": True}
''' % PASSWORD

PROFILE = {"version": 1, "login": {"method": "POST", "path": "/api/auth/login", "token_path": "$.access_token",
                                   "json": {"username": {"env": "QC_TEST_USERNAME"}, "password": {"env": "QC_TEST_PASSWORD"}}},
           "header": {"name": "Authorization", "scheme": "Bearer"}}


def step(method, path, status, *, headers=None, body=None, assertions=None):
    request = {"method": method, "path": path}
    if headers is not None:
        request["headers"] = headers
    if body is not None:
        request["json"] = body
    expect = {"status": [status]}
    if assertions:
        expect["json"] = assertions
    return {"request": request, "expect": expect}


def case(number, title, st, ac=None):
    return {"tc_id": f"TC-AC-1.{number}-00000{number}", "title": title, "ac_refs": [ac or f"AC-1.{number}"], "kind": "api_functional",
            "status": "approved", "origin": "qa", "steps": [st]}


def catalog(*cases):
    acs = sorted({ref for tc in cases for ref in tc["ac_refs"]})
    return {"version": 1, "prd": {"id": "auth", "sha256": "0" * 64, "source": "docs/prd/auth.md"},
            "generated_by": {"model": "claude-sonnet-5", "prompt_version": "gt-generate/1"}, "status": "approved",
            "stories": [{"story_id": "US-1", "title": "Xác thực", "acs": [{"ac_id": ac, "text": "x"} for ac in acs]}],
            "test_cases": list(cases), "uncovered_acs": []}


STANDARD = (
    case(1, "đăng nhập tự động, gọi được endpoint bảo vệ", step("GET", "/api/me", 200, assertions=[{"path": "$.username", "op": "eq", "value": "qc-user"}])),
    case(2, "thiếu token thì 401", step("GET", "/api/me", 401, headers={"Authorization": ""})),
    case(3, "token sai thì 401", step("GET", "/api/me", 401, headers={"Authorization": "Bearer invalid"})),
    case(4, "sai mật khẩu thì 401, endpoint đăng nhập không nhận token tự động",
         step("POST", "/api/auth/login", 401, body={"username": "qc-user", "password": "wrong"})),
    case(5, "runtime tự gắn header", step("GET", "/api/header-present", 200, assertions=[{"path": "$.present", "op": "eq", "value": True}])),
    case(6, "header rỗng nghĩa là không gửi header", step("GET", "/api/header-present", 200, headers={"Authorization": ""},
                                                         assertions=[{"path": "$.present", "op": "eq", "value": False}])),
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def sut_url(tmp_path_factory):
    app_dir = tmp_path_factory.mktemp("authapp")
    (app_dir / "authapp.py").write_text(APP, encoding="utf-8")
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir", str(app_dir), "authapp:app", "--host", "127.0.0.1", "--port", str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{url}/openapi.json", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        server.terminate()
        raise RuntimeError("SUT mẫu không lên")
    yield url
    server.terminate()
    server.wait(timeout=20)


def build(tmp_path, cat, profile=PROFILE):
    root = tmp_path / "sut"
    assert gt_schema.validate_catalog(cat) == []
    gt_render.write(gt_render.render(cat, sut_root=root), root)
    if profile is not None:
        (root / GT / "auth.yaml").write_text(profile if isinstance(profile, str) else yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return root


def run(root, url, **env):
    full = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "", "PYTHONUTF8": "1", "APP_BASE_URL": url,
            "QC_TEST_USERNAME": "qc-user", "QC_TEST_PASSWORD": PASSWORD, **env}
    for name in [n for n, v in env.items() if v is None]:
        full.pop(name, None)
    junit = root / "junit.xml"
    junit.unlink(missing_ok=True)
    done = subprocess.run([sys.executable, "-m", "pytest", f"{GT}/tests_gt", "-q", "-p", "no:cacheprovider", "--junitxml", str(junit)],
                          cwd=root, env=full, capture_output=True, text=True, encoding="utf-8", timeout=180)
    cases = {}
    if junit.exists():
        for node in ET.parse(junit).getroot().iter("testcase"):
            cases[node.get("name")] = "failed" if node.find("failure") is not None else "error" if node.find("error") is not None else "passed"
    return done, cases


def outcome(done):
    return done.stdout + done.stderr


# ---------------- cấu hình ----------------

def test_a_valid_profile_has_no_problems_and_defaults_are_filled():
    assert gt_auth.validate(PROFILE) == []
    assert gt_auth.header_name({"login": PROFILE["login"]}) == "Authorization"
    assert gt_auth.validate({**PROFILE, "scope": "case", "header": {"name": "X-Token", "scheme": ""}}) == []


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=1),
    lambda p: p.update(version=2),
    lambda p: p["login"].update(path="https://evil.example/login"),
    lambda p: p["login"].update(path="//evil.example/login"),
    lambda p: p["login"].update(path="/a/../b"),
    lambda p: p["login"].update(method="GET"),
    lambda p: p["login"].update(token_path="access_token"),
    lambda p: p["login"].update(token_path="$..token"),
    lambda p: p["login"]["json"].update(password={"env": "ANTHROPIC_API_KEY"}),
    lambda p: p["login"]["json"].update(password={"env": "QC_TEST_PW", "x": 1}),
    lambda p: p["login"]["json"].update(password=["list"]),
    lambda p: p["login"]["json"].update({"bad key": "x"}),
    lambda p: p.update(header={"name": "Bad Header"}),
    lambda p: p.update(header={"name": "Authorization", "scheme": "Bad Scheme"}),
    lambda p: p.update(scope="forever"),
    lambda p: p.pop("login"),
])
def test_invalid_profiles_are_rejected_and_the_runtime_agrees(mutate, tmp_path):
    profile = yaml.safe_load(yaml.safe_dump(PROFILE))
    mutate(profile)
    assert gt_auth.validate(profile)
    assert _runtime(tmp_path)._auth_problem(profile) != ""


def test_the_runtime_and_the_validator_accept_exactly_the_same_profiles(tmp_path):
    runtime = _runtime(tmp_path)
    good = [PROFILE, {**PROFILE, "scope": "case"}, {**PROFILE, "header": {"name": "X-Auth", "scheme": ""}},
            {"version": 1, "login": {"path": "/login", "token_path": "$.data[0].token"}},
            {"version": 1, "login": {"path": "/login", "token_path": "$.t", "json": {"grant_type": "password", "n": 1, "ok": True, "x": None}}}]
    for profile in good:
        assert gt_auth.validate(profile) == [] and runtime._auth_problem(profile) == ""
    for bad in ([], "x", None, {"version": 1}, {"version": 1, "login": {}}, {"version": 1, "login": {"path": "/l"}}):
        assert gt_auth.validate(bad) and runtime._auth_problem(bad) != ""


def _runtime(tmp_path):
    root = tmp_path / "rt"
    gt_render.write(gt_render.render(catalog(STANDARD[0]), sut_root=root), root)
    spec = importlib.util.spec_from_file_location("gt_runtime_under_test", root / GT / "tests_gt" / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_returns_none_without_a_file_and_raises_without_leaking_values(tmp_path):
    assert gt_auth.load(tmp_path) is None
    (tmp_path / GT).mkdir(parents=True)
    (tmp_path / GT / "auth.yaml").write_text("version: 1\nlogin: {path: 'https://leak.example/secret-xyz', token_path: $.t}\n", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        gt_auth.load(tmp_path)
    assert "leak.example" not in str(error.value) and "secret-xyz" not in str(error.value)


def test_gt_validate_reports_a_bad_profile_and_warns_about_variables_the_ci_does_not_pass(tmp_path):
    root = build(tmp_path, catalog(STANDARD[0]), profile="version: 1\nlogin: {path: /x}\n")
    result = gt_check.CheckResult()
    gt_check._auth(root, result)
    assert [where for where, _ in result.errors] == [gt_auth.PROFILE_PATH] and "token_path" in result.errors[0][1]

    other = yaml.safe_load(yaml.safe_dump(PROFILE))
    other["login"]["json"]["api_key"] = {"env": "QC_TEST_API_KEY"}
    (root / GT / "auth.yaml").write_text(yaml.safe_dump(other, sort_keys=False), encoding="utf-8")
    result = gt_check.CheckResult()
    gt_check._auth(root, result)
    assert not result.errors and len(result.warnings) == 1 and "QC_TEST_API_KEY" in result.warnings[0][1]

    (root / GT / "auth.yaml").unlink()
    result = gt_check.CheckResult()
    gt_check._auth(root, result)
    assert not result.errors and not result.warnings


# ---------------- prompt ----------------

def test_the_auth_block_is_added_to_the_prompts_only_when_a_profile_exists():
    prd = parse_prd(ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md")
    block = gt_auth.prompt_block(PROFILE)
    assert gt_auth.prompt_block(None) is None and "<auth>" not in gt_generate.build_user(prd)
    for text in (gt_generate.build_user(prd, auth=block), build_first_user(prd, "", None, "", block)):
        assert text.count("<auth>") == 1 and "Authorization" in text and "Bearer invalid" in text
    assert "<auth>" not in build_first_user(prd, "", None, "")
    for secret in ("QC_TEST_USERNAME", "QC_TEST_PASSWORD", PASSWORD):
        assert secret not in block


# ---------------- runtime trên SUT có đăng nhập ----------------

def test_the_runtime_logs_in_attaches_the_token_and_honours_empty_and_explicit_headers(tmp_path, sut_url):
    root = build(tmp_path, catalog(*STANDARD))
    done, cases = run(root, sut_url)
    assert done.returncode == 0 and set(cases.values()) == {"passed"} and len(cases) == 6, outcome(done)
    assert PASSWORD not in outcome(done) and "tok-" not in outcome(done)


def test_without_a_profile_nothing_is_attached_and_an_empty_header_is_simply_not_sent(tmp_path, sut_url):
    root = build(tmp_path, catalog(*STANDARD[:3]), profile=None)
    done, cases = run(root, sut_url)
    states = [cases[name] for name in sorted(cases)]
    assert states.count("failed") == 1 and states.count("passed") == 2, outcome(done)   # chỉ TC cần token là đỏ; thiếu token và token sai vẫn 401 như kỳ vọng


def test_missing_credentials_are_an_infrastructure_error_that_names_the_variable_only(tmp_path, sut_url):
    root = build(tmp_path, catalog(*STANDARD))
    done, _ = run(root, sut_url, QC_TEST_PASSWORD=None)
    assert done.returncode == 4 and "QC_TEST_PASSWORD" in outcome(done) and PASSWORD not in outcome(done)


def test_a_rejected_login_is_an_error_not_a_test_failure_and_the_password_is_not_printed(tmp_path, sut_url):
    root = build(tmp_path, catalog(*STANDARD))
    done, _ = run(root, sut_url, QC_TEST_PASSWORD="another-secret-value")
    assert done.returncode == 4 and "HTTP 401" in outcome(done) and "another-secret-value" not in outcome(done)


def test_a_broken_profile_stops_the_run_with_an_error(tmp_path, sut_url):
    bad = yaml.safe_load(yaml.safe_dump(PROFILE))
    bad["login"]["json"]["password"] = {"env": "ANTHROPIC_API_KEY"}
    root = build(tmp_path, catalog(*STANDARD), profile=bad)
    done, _ = run(root, sut_url, ANTHROPIC_API_KEY="sk-ant-should-never-be-sent")
    assert done.returncode == 4 and "login.json" in outcome(done) and "sk-ant-should-never-be-sent" not in outcome(done)


LOGOUT_THEN_ME = (case(1, "đăng xuất thu hồi phiên", step("POST", "/api/auth/logout", 200), ac="AC-1.1"),
                  case(2, "sau đó vẫn gọi được với phiên mới", step("GET", "/api/me", 200), ac="AC-1.2"))


def test_scope_case_gives_every_test_case_its_own_session_so_a_logout_does_not_poison_the_next(tmp_path, sut_url):
    isolated = build(tmp_path, catalog(*LOGOUT_THEN_ME), profile={**PROFILE, "scope": "case"})
    done, cases = run(isolated, sut_url)
    assert done.returncode == 0 and set(cases.values()) == {"passed"}, outcome(done)


def test_scope_session_shares_one_login_so_a_revoked_session_breaks_later_cases(tmp_path, sut_url):
    shared = build(tmp_path, catalog(*LOGOUT_THEN_ME))
    done, cases = run(shared, sut_url)
    assert sorted(cases.values()) == ["failed", "passed"], outcome(done)   # hành vi được ghi trong tài liệu: dùng scope: case khi có test đăng xuất
