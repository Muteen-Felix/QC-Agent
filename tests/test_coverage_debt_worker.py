"""P2-2: worker dò nợ test (coverage_debt_worker). Repo git dựng thật trong tmp_path; hàm thuần test riêng, không cần git.
Nợ = bề mặt MỚI THÊM (head − base) mà chưa có test chạm tới; full-scan (không base) = mọi bề mặt hiện có chưa có test."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from debtkit import FILLER, MAIN_PY, OLD, PING, Repo
from debtkit import GIT_ENV as _GIT_ENV
from qc_agent.adapters import coverage_debt_worker as cd
from qc_agent.adapters.coverage_debt_worker import DebtError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="cần binary git")


@pytest.fixture()
def repo(tmp_path):
    return Repo(tmp_path / "sut")


# ───────────────────────── trích bề mặt: hàm thuần ─────────────────────────

def test_api_surfaces_decorators_prefixes_and_skips():
    src = textwrap.dedent('''
        from fastapi import FastAPI, APIRouter
        app = FastAPI()
        router = APIRouter(prefix="/items")
        v1: APIRouter = APIRouter(prefix="/v1")

        @router.get("/{item_id}")
        async def a(): ...
        @router.post("")
        def b(): ...
        @router.get("/hidden", include_in_schema=False)
        def c(): ...
        @app.get(f"/dyn/{x}")
        def d(): ...
        @app.get("relative")
        def e(): ...
        @app.api_route("/z")
        def f(): ...
        @app.route("/u/<int:uid>", methods=["GET", "POST"])
        def g(): ...
        @app.route("/m", methods=METHODS)
        def h(): ...
        @cache
        def i(): ...
        @v1.get("/x")
        def j(): ...
        app.include_router(v1, prefix="/api")
    ''')
    assert set(cd.api_surfaces(src)) == {
        (cd.KIND_ENDPOINT, s) for s in ("GET /items/{item_id}", "POST /items", "GET /z", "GET /u/{uid}", "POST /u/{uid}", "GET /api/v1/x")}


def test_api_surfaces_syntax_error_is_error_not_empty():
    with pytest.raises(DebtError, match="không parse được"):
        cd.api_surfaces("def broken(:\n", "app/x.py")


def test_openapi_surfaces_only_http_methods():
    text = json.dumps({"paths": {"/a": {"get": {}, "post": {}, "parameters": [], "summary": "x"}, "/b/{id}": {"delete": {}}}})
    assert set(cd.openapi_surfaces(text)) == {(cd.KIND_CONTRACT, s) for s in ("GET /a", "POST /a", "DELETE /b/{id}")}
    with pytest.raises(DebtError):
        cd.openapi_surfaces("không phải json")


def test_ui_routes_paths_components_and_skips():
    src = textwrap.dedent('''
        <Route path="/" element={<Home />} />
        <Route path="/settings" element={<SettingsPage />} />
        const routes = [{ path: '/users/:id', element: <UserPage/> }, { path: "/billing/", component: BillingPage }];
        res.cookie("a", "b", { path: '/' });
        const dyn = { path: `/x/${id}` };
        const rel = { path: "relative" };
    ''')
    got = cd.ui_routes(src)
    assert {s: set(a) for (_, s), a in got.items()} == {"/settings": {"SettingsPage"}, "/users/:id": {"UserPage"}, "/billing": {"BillingPage"}}


@pytest.mark.parametrize("template, text, hit", [
    ("/items/{item_id}", 'client.get("/items/42")', True),
    ("/items/{item_id}", 'const u = `/items/${id}`', True),
    ("/items/{item_id}", "GET /items/{item_id}", True),
    ("/items/{item_id}", "/items/42/comments", False),  # dài hơn template
    ("/items/{item_id}", 'url = "/items/" + id', False),  # nối chuỗi: không đoán
    ("/items", 'get("/items/42")', False),  # /items không được coi là bị chạm bởi /items/42
    ("/items", 'get("/items")', True),
    ("/items", 'get("/items?page=2")', True),
    ("/items", 'get("/itemsx")', False),
    ("/users/:id", "goto('/users/7')", True),
])
def test_path_regex(template, text, hit):
    assert bool(cd.path_regex(template).search(text)) is hit


def test_glob_regex():
    assert cd.glob_regex("tests/**").match("tests/a/b.py") and not cd.glob_regex("tests/**").match("src/tests/a.py")
    assert cd.glob_regex("**/*.spec.ts").match("a.spec.ts") and cd.glob_regex("**/*.spec.ts").match("x/y/a.spec.ts")
    assert cd.glob_regex("*.md").match("a.md") and not cd.glob_regex("*.md").match("a/b.md")
    assert cd.glob_regex(".qc-agent/**").match(".qc-agent/suites/x.yaml")


# ───────────────────────── git: changed_files ─────────────────────────

def test_changed_files_parses_add_rename_modify_delete_with_odd_names(repo):
    repo.write("app/keep.py", "x = 1\n" + FILLER)
    repo.write("app/old name.py", "y = 1\n" + FILLER)
    repo.write("app/gone.py", "z = 1\n" + FILLER)
    repo.commit("base")
    repo.git("mv", "app/old name.py", "app/tên mới.py")
    repo.write("app/keep.py", "x = 2\n" + FILLER)
    repo.git("rm", "-q", "app/gone.py")
    repo.write("app/added.py", "w = 1\n")
    repo.commit("pr")

    got = {(c.status, c.path, c.old_path) for c in cd.changed_files(repo.root, "HEAD^1")}
    assert got == {("A", "app/added.py", ""), ("R", "app/tên mới.py", "app/old name.py"),
                   ("M", "app/keep.py", "app/keep.py"), ("D", "app/gone.py", "app/gone.py")}


# ───────────────────────── api_endpoint ─────────────────────────

def _pr_with_items(repo: Repo):
    repo.write("app/main.py", MAIN_PY)
    repo.commit("base")
    repo.write("app/main.py", MAIN_PY.replace('return "ok"', 'return "OK!"') + PING)
    repo.write("app/routers/items.py", '''
        from fastapi import APIRouter
        router = APIRouter(prefix="/items")

        @router.get("/{item_id}")
        def get_item(item_id: int): ...

        @router.post("")
        def create_item(): ...

        @router.get("/secret", include_in_schema=False)
        def secret(): ...
    ''')


def test_new_endpoints_are_debt_but_modified_and_internal_are_not(repo):
    _pr_with_items(repo)
    repo.commit("pr")
    assert repo.ids() == ["debt:api_endpoint:GET /items/{item_id}", "debt:api_endpoint:GET /ping", "debt:api_endpoint:POST /items"]
    # /health chỉ bị SỬA thân hàm (D5: không phải nợ); /items/secret có include_in_schema=False


def test_endpoint_with_template_param_is_covered_by_test_mentioning_a_concrete_path(repo):
    _pr_with_items(repo)
    repo.write("tests/test_items.py", 'def test_get(client):\n    client.get("/items/42")\n')
    repo.commit("pr")
    assert repo.ids() == ["debt:api_endpoint:GET /ping", "debt:api_endpoint:POST /items"]  # "/items/42" không phủ POST /items


def test_test_globs_default_and_override(repo):
    _pr_with_items(repo)
    repo.write("spec/items.spec.ts", 'await request.get("/items/42"); await request.post("/items"); await request.get("/ping");\n')
    repo.write("src/notes.ts", 'fetch("/ping")\n')  # mã ứng dụng không phải test
    repo.commit("pr")
    assert len(repo.ids()) == 3  # spec/** không nằm trong glob mặc định

    repo.write(".qc-agent/coverage.yaml", "test_globs: ['spec/**']\n")
    repo.commit("cấu hình")
    assert cd.scan(repo.root, "HEAD^1")["findings"] == []  # diff HEAD^1 chỉ còn coverage.yaml; nợ cũ không tính lại
    # full-scan: 3 endpoint của PR đã được spec phủ; còn lại /health (có từ trước, chưa ai test)
    assert [f["surface"] for f in cd.scan(repo.root, None)["findings"]] == ["GET /health"]


def test_suite_exclude_path_does_not_count_as_test_coverage(repo):
    _pr_with_items(repo)
    repo.write(".qc-agent/suites/api-contract.yaml", """
        suite: api-contract
        tasks:
        - task_id: t-001
          capability: api.property
          inputs: {exclude_path: [/items/{item_id}]}
    """)
    repo.commit("pr")
    assert "debt:api_endpoint:GET /items/{item_id}" in repo.ids()  # path bị LOẠI khỏi test không được tính là có test


def test_files_matching_test_globs_never_create_surfaces(repo):
    repo.write("app/main.py", MAIN_PY)
    repo.commit("base")
    repo.write("tests/helpers/fake_app.py", 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/x")\ndef x(): ...\n')
    repo.write("e2e/pages.tsx", 'const r = { path: "/y", element: <Y/> };\n')
    repo.commit("pr")
    assert repo.ids() == []


# ───────────────────────── đổi tên ─────────────────────────

def test_pure_rename_is_not_new_and_rename_plus_addition_reports_only_the_addition(repo):
    body = MAIN_PY + FILLER
    repo.write("app/old.py", body)
    repo.commit("base")
    (repo.root / "app" / "routers").mkdir()
    repo.git("mv", "app/old.py", "app/routers/renamed.py")
    repo.commit("rename")
    assert [c.status for c in cd.changed_files(repo.root, "HEAD^1")] == ["R"]  # git thật sự thấy đổi tên
    assert repo.ids() == []

    repo.write("app/routers/renamed.py", body + '\n@app.delete("/health")\ndef drop(): ...\n')
    repo.commit("rename+add")
    assert repo.ids() == ["debt:api_endpoint:DELETE /health"]


# ───────────────────────── ignore ─────────────────────────

def test_ignore_lists_reason_but_is_not_debt_and_covered_surface_is_never_listed(repo):
    _pr_with_items(repo)
    repo.write("tests/test_items.py", 'client.get("/items/1")\n')
    repo.write(".qc-agent/coverage.yaml", """
        ignore:
          - {surface: "GET /ping", reason: health probe của k8s}
          - {surface: "api_endpoint:POST /items*", reason: đã có test ở dịch vụ khác}
          - {surface: "GET /items/{item_id}", reason: đã được test nên không được liệt kê}
    """)
    repo.commit("pr")
    got = cd.scan(repo.root, "HEAD^1")
    assert got["findings"] == [] and got["metrics"]["debt.new"] == 0
    assert got["ignored"] == [{"finding_id": "debt:api_endpoint:GET /ping", "reason": "health probe của k8s"},
                              {"finding_id": "debt:api_endpoint:POST /items", "reason": "đã có test ở dịch vụ khác"}]


@pytest.mark.parametrize("content", [
    "ignore: [{surface: 'GET /x'}]",  # thiếu reason
    "ignore: [{surface: 'GET /x', reason: '  '}]",
    "ignore: ['GET /x']",
    "test_glob: ['tests/**']",  # gõ sai khoá: không được lặng lẽ dùng mặc định
    "test_globs: []",
])
def test_invalid_coverage_yaml_is_error(repo, content):
    repo.write("app/main.py", MAIN_PY)
    repo.write(".qc-agent/coverage.yaml", content + "\n")
    repo.commit("base")
    with pytest.raises(DebtError, match="coverage.yaml"):
        cd.scan(repo.root, None)


# ───────────────────────── api_contract ─────────────────────────

def _openapi(*paths):
    return json.dumps({"openapi": "3.0.0", "paths": dict(paths)})


def _contract_pr(repo: Repo, suite: str | None):
    repo.write("openapi.json", _openapi(("/a", {"get": {}})))
    repo.commit("base")
    repo.write("openapi.json", _openapi(("/a", {"get": {}}), ("/b", {"get": {}, "post": {}}), ("/c", {"get": {}}),
                                        ("/items/{item_id}", {"get": {}, "parameters": []})))
    if suite:
        repo.write(".qc-agent/suites/api-contract.yaml", suite)
    repo.commit("pr")


def test_api_contract_new_operations_without_any_property_task_are_debt(repo):
    _contract_pr(repo, None)
    assert repo.ids() == ["debt:api_contract:GET /b", "debt:api_contract:GET /c", "debt:api_contract:GET /items/{item_id}",
                          "debt:api_contract:POST /b"]


def test_api_contract_covered_by_property_task_unless_path_is_excluded(repo):
    _contract_pr(repo, """
        suite: api-contract
        tasks:
        - {task_id: t-000, capability: api.load, inputs: {exclude_path: []}}
        - task_id: t-001
          capability: api.property
          inputs: {exclude_path: [/c, "/items/{id}"]}
    """)
    # /b được task phủ; /c bị loại; /items/{item_id} bị loại dù khác tên tham số ({id} vs {item_id})
    assert repo.ids() == ["debt:api_contract:GET /c", "debt:api_contract:GET /items/{item_id}"]


def test_api_contract_exclude_path_may_be_a_single_string(repo):
    _contract_pr(repo, "suite: s\ntasks:\n- {task_id: t, capability: api.property, inputs: {exclude_path: /c}}\n")
    assert repo.ids() == ["debt:api_contract:GET /c"]


# ───────────────────────── ui_route ─────────────────────────

APP_TSX = """
    <Route path="/" element={<Home />} />
"""
NEW_ROUTES = APP_TSX + """
    <Route path="/settings" element={<SettingsPage />} />
    const more = [{ path: '/users/:id', element: <UserPage/> }, { path: "/billing", component: BillingPage }];
    res.cookie("a", "b", { path: '/' });
"""


def test_ui_routes_new_routes_are_debt_and_root_and_dynamic_are_not(repo):
    repo.write("src/App.tsx", APP_TSX)
    repo.commit("base")
    repo.write("src/App.tsx", NEW_ROUTES)
    repo.commit("pr")
    assert repo.ids() == ["debt:ui_route:/billing", "debt:ui_route:/settings", "debt:ui_route:/users/:id"]


def test_ui_route_covered_by_path_in_e2e_or_component_name_in_midscene_flow(repo):
    repo.write("src/App.tsx", APP_TSX)
    repo.commit("base")
    repo.write("src/App.tsx", NEW_ROUTES)
    repo.write("e2e/app.spec.ts", "await page.goto('/settings');\n")  # phủ bằng path
    repo.write("midscene/flow.yaml", "tasks:\n- name: xem UserPage\n  flow: [{aiTap: mở UserPage}]\n")  # phủ bằng tên component
    repo.commit("pr")
    assert repo.ids() == ["debt:ui_route:/billing"]


# ───────────────────────── diff-scan vs full-scan ─────────────────────────

def _diff_vs_full_repo(repo: Repo):
    repo.write("app/main.py", MAIN_PY + '\n@app.get("/old")\ndef old(): ...\n')
    repo.write("tests/test_old.py", 'client.get("/old")\n')
    repo.commit("base")
    repo.write("app/main.py", MAIN_PY + '\n@app.get("/old")\ndef old(): ...\n\n@app.get("/ping")\ndef ping(): ...\n')
    repo.commit("pr")


def test_diff_scan_reports_only_new_full_scan_reports_every_uncovered_surface(repo):
    _diff_vs_full_repo(repo)
    diff, full = cd.scan(repo.root, "HEAD^1"), cd.scan(repo.root, None)
    assert [f["surface"] for f in diff["findings"]] == ["GET /ping"]
    assert (diff["mode"], diff["base"], diff["metrics"]) == ("diff", "HEAD^1", {"debt.new": 1, "debt.full_scan": False})
    assert [f["surface"] for f in full["findings"]] == ["GET /health", "GET /ping"]  # /old đã có test
    assert (full["mode"], full["base"], full["metrics"]) == ("full", None, {"debt.new": 2, "debt.full_scan": True})
    assert full["findings"][0] == {"finding_id": "debt:api_endpoint:GET /health", "kind": "api_endpoint", "surface": "GET /health"}


def test_full_scan_needs_no_git(tmp_path):
    plain = tmp_path / "plain"
    (plain / "app").mkdir(parents=True)
    (plain / "app" / "main.py").write_text(textwrap.dedent(MAIN_PY), encoding="utf-8")
    assert [f["surface"] for f in cd.scan(plain, None)["findings"]] == ["GET /health"]


def test_sut_root_may_be_a_subdirectory_of_the_repository(repo):
    repo.write("backend/app/main.py", MAIN_PY)
    repo.write("other/svc.py", 'from fastapi import FastAPI\napp = FastAPI()\n')
    repo.commit("base")
    repo.write("backend/app/main.py", MAIN_PY + '\n@app.get("/ping")\ndef ping(): ...\n')
    repo.write("other/svc.py", 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/elsewhere")\ndef e(): ...\n')
    repo.commit("pr")
    assert repo.ids(root=repo.root / "backend") == ["debt:api_endpoint:GET /ping"]  # đường dẫn tương đối SUT root, không lẫn `other/`


# ───────────────────────── bảo thủ: không đoán ─────────────────────────

def test_missing_base_is_error(repo):
    repo.write("app/main.py", MAIN_PY)
    repo.commit("base")
    with pytest.raises(DebtError, match="fetch-depth: 2"):
        cd.scan(repo.root, "0123456789abcdef0123456789abcdef01234567")
    with pytest.raises(DebtError, match="fetch-depth: 2"):
        cd.scan(repo.root, "HEAD^1")  # commit gốc không có cha
    with pytest.raises(DebtError, match="base không hợp lệ"):
        cd.scan(repo.root, "--output=/tmp/x")


def test_shallow_clone_without_parent_is_error_not_empty_diff(tmp_path):
    origin = Repo(tmp_path / "origin")
    origin.write("app/main.py", MAIN_PY)
    origin.commit("one")
    origin.write("app/main.py", MAIN_PY + '\n@app.get("/ping")\ndef ping(): ...\n')
    origin.commit("two")
    dst = tmp_path / "ci"
    subprocess.run(["git", "clone", "-q", "--depth", "1", origin.root.as_uri(), str(dst)], check=True, capture_output=True,
                   env={**os.environ, **_GIT_ENV})
    with pytest.raises(DebtError, match="fetch-depth: 2"):
        cd.scan(dst, "HEAD^1")


def test_not_a_git_repo_with_base_is_error(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(DebtError):
        cd.scan(tmp_path / "x", "HEAD^1")


def test_unparseable_changed_python_file_is_error(repo):
    repo.write("app/main.py", MAIN_PY)
    repo.commit("base")
    repo.write("app/broken.py", "def x(:\n")
    repo.commit("pr")
    with pytest.raises(DebtError, match="app/broken.py"):
        cd.scan(repo.root, "HEAD^1")


# ───────────────────────── CLI ─────────────────────────

def test_cli_reads_base_from_env_and_writes_debt_json(repo, tmp_path, monkeypatch, capsys):
    _diff_vs_full_repo(repo)
    out = tmp_path / "out" / "debt.json"
    monkeypatch.setenv("QC_DIFF_BASE", "HEAD^1")
    assert cd.main(["--root", str(repo.root), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "ok" and data["mode"] == "diff" and [f["surface"] for f in data["findings"]] == ["GET /ping"]
    assert "new=1" in capsys.readouterr().out

    monkeypatch.delenv("QC_DIFF_BASE")  # không có QC_DIFF_BASE => full-scan (D4)
    assert cd.main(["--root", str(repo.root), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["metrics"] == {"debt.new": 2, "debt.full_scan": True}


def test_cli_error_still_writes_debt_json_with_no_findings_and_exit_3(repo, tmp_path):
    repo.write("app/main.py", MAIN_PY)
    repo.commit("base")
    out = tmp_path / "debt.json"
    assert cd.main(["--root", str(repo.root), "--base", "deadbeef", "--out", str(out)]) == 3
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "error" and "fetch-depth" in data["error"] and data["findings"] == []
    assert data["metrics"] == {"debt.new": 0, "debt.full_scan": False}


def test_cli_runs_as_module_from_sut_root(repo, tmp_path):
    _diff_vs_full_repo(repo)
    out = tmp_path / "m.json"
    done = subprocess.run([sys.executable, "-m", "qc_agent.adapters.coverage_debt_worker", "--out", str(out)], cwd=repo.root,
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "QC_DIFF_BASE": "HEAD^1", "PYTHONUTF8": "1"})
    assert done.returncode == 0, done.stderr
    assert [f["surface"] for f in json.loads(out.read_text(encoding="utf-8"))["findings"]] == ["GET /ping"]
