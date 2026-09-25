"""Bước 32: scanner tất định. Fixture = bản cắt cấu trúc của vahan-rpa (chỉ đọc) cộng các cây nhỏ dựng tại chỗ."""
import shutil
from pathlib import Path

import pytest

from qc_agent.scaffold import scan

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scan" / "vahan-rpa"


def tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def vahan(tmp_path):
    target = tmp_path / "vahan"
    shutil.copytree(FIXTURE, target)
    # dương tính giả phải bị bỏ qua: virtualenv (hàng chục kết quả CORS giả), thư mục test, node_modules
    tree(target, {".venv/lib/site-packages/starlette/cors.py": 'import os\nx = os.getenv("STARLETTE_CORS_ALLOW")\n',
                  "venv/lib/x/routes.py": '@router.get("/healthz")\n',
                  "apps/web-ui/node_modules/lib/index.js": "process.env.REACT_APP_API_URL\n",
                  "apps/api-server/tests/test_api.py": '@router.get("/ready")\n'})
    return target


def test_vahan_rpa_shape(vahan):
    result = scan.scan(vahan)
    assert result.dockerfile.value == "Dockerfile" and result.dockerfile.verify is None and result.context == "."
    assert result.port.value == "8000" and result.port.source == "detected" and result.port.verify is None      # EXPOSE 8000
    assert result.fastapi and result.openapi_path.value == "/openapi.json"


def test_false_positive_health_prefix_is_one_candidate_so_no_verify(vahan):
    """Scan tĩnh ra /health dù thực tế là /api/health (router có prefix /api): giới hạn đã biết, chỉ MỘT ứng viên nên không có VERIFY."""
    health = scan.scan(vahan).health_path
    assert (health.value, health.candidates, health.verify) == ("/health", ("/health",), None)


def test_two_cors_names_pick_the_http_one_and_ask_for_verification(vahan):
    cors = scan.scan(vahan).cors_env
    assert cors.value == "VAHAN_API_CORS_ORIGINS" and cors.candidates == ("VAHAN_API_CORS_ORIGINS", "VAHAN_API_SOCKETIO_CORS_ORIGINS")
    assert "chọn VAHAN_API_CORS_ORIGINS trong [VAHAN_API_CORS_ORIGINS, VAHAN_API_SOCKETIO_CORS_ORIGINS]" in cors.verify
    assert "STARLETTE_CORS_ALLOW" not in cors.candidates      # .venv bị bỏ qua


def test_vite_ui_with_lockfile_beside_it(vahan):
    ui = scan.scan(vahan).ui
    assert (ui.directory, ui.kind, ui.output_dir, ui.package_manager, ui.lockfile) == ("apps/web-ui", "vite", "dist", "npm", "package-lock.json")
    assert ui.api_var.value == "VITE_API_URL" and ui.api_var.verify is None
    assert ui.node_major.value == 22 and ui.node_major.source == "default"
    assert ui.directory_finding.verify is None            # vahan-chrome-extension không có vite => chỉ một ứng viên


def test_flags_win_and_carry_no_verify(vahan):
    result = scan.scan(vahan, scan.Overrides(dockerfile="Dockerfile", port="9000", health_path="/api/health"))
    assert (result.port.value, result.port.source, result.port.verify) == ("9000", "flag", None)
    assert (result.health_path.value, result.health_path.source, result.health_path.verify) == ("/api/health", "flag", None)
    assert result.dockerfile.source == "flag"


def test_missing_api_dockerfile_is_an_error_with_the_flag_to_use(tmp_path):
    with pytest.raises(scan.ScanError, match="--sut-dockerfile"):
        scan.scan(tree(tmp_path, {"README.md": "x"}))
    with pytest.raises(scan.ScanError, match="không tồn tại"):
        scan.scan(tmp_path, scan.Overrides(dockerfile="nope/Dockerfile"))


def test_dockerfile_priority_root_then_shallow_then_alphabetical_with_verify(tmp_path):
    tree(tmp_path, {"Dockerfile": "FROM x\n", "api/Dockerfile": "FROM x\n", "docker/prod.Dockerfile": "FROM x\n"})
    found = scan.scan(tmp_path).dockerfile
    assert found.value == "Dockerfile" and found.candidates == ("Dockerfile", "api/Dockerfile", "docker/prod.Dockerfile")
    assert found.verify.startswith("chọn Dockerfile trong [")
    nested = tree(tmp_path / "b", {"web/Dockerfile": "FROM x\n", "api/Dockerfile": "FROM x\n"})
    result = scan.scan(nested)
    assert result.dockerfile.value == "api/Dockerfile" and result.context == "api" and result.dockerfile.verify


def test_port_from_expose_cmd_or_default_with_verify(tmp_path):
    assert scan.scan(tree(tmp_path / "a", {"Dockerfile": "EXPOSE 5000/tcp\nEXPOSE 9000\n"})).port.verify.startswith("chọn 5000")
    cmd = scan.scan(tree(tmp_path / "b", {"Dockerfile": 'CMD ["uvicorn", "app:a", "--port", "7000"]\n'})).port
    assert (cmd.value, cmd.source, cmd.verify) == ("7000", "detected", None)
    default = scan.scan(tree(tmp_path / "c", {"Dockerfile": "FROM x\n"})).port
    assert (default.value, default.source) == ("8000", "default") and "EXPOSE" in default.verify


def test_health_priority_and_default(tmp_path):
    routes = '@app.get("/healthz")\n@app.get("/ready")\n@router.get("/api/health")\n@app.get("/health")\n@app.get("/users/{id}")\n'
    health = scan.scan(tree(tmp_path / "a", {"Dockerfile": "x", "main.py": routes})).health_path
    assert health.value == "/api/health" and health.candidates == ("/api/health", "/health", "/healthz", "/ready") and health.verify
    none = scan.scan(tree(tmp_path / "b", {"Dockerfile": "x", "main.py": "x = 1\n"})).health_path
    assert (none.value, none.source, none.verify) == ("/", "default", None)     # readiness nhận mọi mã HTTP


def test_openapi_path_needs_fastapi_and_honours_openapi_url(tmp_path):
    assert scan.scan(tree(tmp_path / "a", {"Dockerfile": "x"})).openapi_path is None
    custom = tree(tmp_path / "b", {"Dockerfile": "x", "requirements.txt": "FastAPI==0.1\n", "main.py": 'app = FastAPI(openapi_url="/v1/openapi.json")\n'})
    assert scan.scan(custom).openapi_path.value == "/v1/openapi.json"


def test_no_cors_env_means_no_finding(tmp_path):
    assert scan.scan(tree(tmp_path, {"Dockerfile": "x"})).cors_env is None


def test_env_example_and_environ_forms_are_recognised(tmp_path):
    tree(tmp_path, {"Dockerfile": "x", ".env.example": "APP_CORS=1\n", "a.py": 'os.environ["SITE_CORS_LIST"]\nos.environ.get("WS_CORS")\n'})
    cors = scan.scan(tmp_path).cors_env
    assert cors.value == "APP_CORS" and cors.candidates == ("APP_CORS", "SITE_CORS_LIST", "WS_CORS")


def test_depth_limit_is_four(tmp_path):
    tree(tmp_path, {"Dockerfile": "x", "a/b/c/d/e/deep.py": '@app.get("/health")\n', "a/b/c/d/shallow.py": '@app.get("/healthz")\n'})
    assert scan.scan(tmp_path).health_path.candidates == ("/healthz",)


def test_cra_and_next_export_and_yarn_pnpm_and_node_versions(tmp_path):
    cra = tree(tmp_path / "cra", {"Dockerfile": "x", "package.json": '{"dependencies": {"react-scripts": "5"}, "engines": {"node": ">=18"}}',
                                  "yarn.lock": "", "src/App.js": "fetch(process.env.REACT_APP_BACKEND_URL)\nprocess.env.REACT_APP_TITLE\n"})
    ui = scan.scan(cra).ui
    assert (ui.kind, ui.output_dir, ui.package_manager, ui.node_major.value, ui.api_var.value) == ("cra", "build", "yarn", 18, "REACT_APP_BACKEND_URL")
    nxt = tree(tmp_path / "nx", {"Dockerfile": "x", "package.json": '{"dependencies": {"next": "15"}}', "pnpm-lock.yaml": "", ".nvmrc": "v20.11.0\n",
                                 "next.config.mjs": "export default { output: 'export' }\n"})
    ui = scan.scan(nxt).ui
    assert (ui.kind, ui.output_dir, ui.package_manager, ui.node_major.value, ui.api_var) == ("next-export", "out", "pnpm", 20, None)


def test_next_ssr_is_refused_with_guidance(tmp_path):
    result = scan.scan(tree(tmp_path, {"Dockerfile": "x", "package.json": '{"dependencies": {"next": "15"}}', "package-lock.json": "{}"}))
    assert result.ui is None and "Next.js SSR" in result.ui_refused and "--ui-dockerfile" in result.ui_refused


def test_workspace_monorepo_and_missing_lockfile_are_refused(tmp_path):
    mono = tree(tmp_path / "m", {"Dockerfile": "x", "package.json": '{"workspaces": ["apps/*"]}', "package-lock.json": "{}",
                                 "apps/web/package.json": '{"devDependencies": {"vite": "7"}}'})
    result = scan.scan(mono)
    assert result.ui is None and "workspace monorepo" in result.ui_refused
    nolock = tree(tmp_path / "n", {"Dockerfile": "x", "web/package.json": '{"devDependencies": {"vite": "7"}}'})
    assert "không có lockfile" in scan.scan(nolock).ui_refused


def test_two_ui_dirs_pick_shallower_then_src_and_ask_for_verification(tmp_path):
    two = tree(tmp_path, {"Dockerfile": "x", "web/package.json": '{"devDependencies": {"vite": "7"}}', "web/package-lock.json": "{}",
                          "apps/admin/package.json": '{"devDependencies": {"vite": "7"}}'})
    finding = scan.scan(two).ui.directory_finding
    assert finding.value == "web" and finding.verify.startswith("chọn web trong [web, apps/admin]")


def test_no_ui_is_noted_not_an_error(tmp_path):
    result = scan.scan(tree(tmp_path, {"Dockerfile": "x"}))
    assert result.ui is None and result.ui_refused is None and any("không thấy UI" in n for n in result.notes)


def test_no_api_mode_skips_the_api_scan_entirely(tmp_path):
    result = scan.scan(tree(tmp_path, {"web/package.json": '{"devDependencies": {"vite": "7"}}', "web/package-lock.json": "{}"}), api=False)
    assert result.dockerfile is None and result.ui.directory == "web"


def test_scan_never_writes(vahan):
    before = sorted(p.relative_to(vahan).as_posix() for p in vahan.rglob("*"))
    scan.scan(vahan)
    assert before == sorted(p.relative_to(vahan).as_posix() for p in vahan.rglob("*"))
