"""Bước 32: scanner tất định. Fixture = bản cắt cấu trúc của vahan-rpa (chỉ đọc) cộng các cây nhỏ dựng tại chỗ."""
import shutil
from pathlib import Path

import pytest

from qc_agent.scaffold import dockerfile_copy, scan

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
    # S4-11: ứng viên kèm file nguồn; kết quả (giá trị, không VERIFY) giữ nguyên nhờ bằng chứng E2 (COPY apps/api-server/app).
    assert (health.value, health.candidates, health.verify) == ("/health", ("/health (apps/api-server/app/api/health.py)",), None)


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
    # S4-08: trước đây `api/` thắng chỉ vì `a` đứng trước `w`. Nay `web/` có dấu hiệu web nên xếp sau; vẫn chỉ là GỢI Ý nên có VERIFY.
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
    # S4-11: ứng viên kèm file nguồn (cây một service nên bằng chứng là E3); thứ tự và VERIFY giữ nguyên.
    assert health.value == "/api/health" and health.verify
    assert health.candidates == ("/api/health (main.py)", "/health (main.py)", "/healthz (main.py)", "/ready (main.py)")
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
    assert scan.scan(tmp_path).health_path.candidates == ("/healthz (a/b/c/d/shallow.py)",)      # S4-11: ứng viên kèm file nguồn


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


# ---------- S4-08: tìm thấy ≠ xác nhận ----------

API_DF = "FROM python:3.12\nCOPY . .\nEXPOSE 8000\n"
SPA_DF = "FROM node:22 AS build\nRUN npm run build\nFROM nginx:1.27\nCOPY --from=build /app/dist /usr/share/nginx/html\n"


def test_two_unhinted_candidates_are_ordered_by_letters_only_and_still_verify(tmp_path):
    """Không có dấu hiệu nào: thứ tự chữ là tiebreak duy nhất, nên kết quả KHÔNG phải nhận biết API và phải có VERIFY."""
    found = scan.scan(tree(tmp_path, {"alpha/Dockerfile": API_DF, "zeta/Dockerfile": API_DF})).dockerfile
    assert found.value == "alpha/Dockerfile" and found.candidates == ("alpha/Dockerfile", "zeta/Dockerfile") and found.verify
    flipped = scan.scan(tree(tmp_path / "f", {"zeta/Dockerfile": API_DF, "alpha/Dockerfile": API_DF})).dockerfile
    assert flipped.value == "alpha/Dockerfile"            # cùng cây: thứ tự tạo file không ảnh hưởng


def test_web_hint_demotes_a_candidate_regardless_of_letter_order(tmp_path):
    """Đảo tên để chứng minh luật không ăn may theo chữ cái: `client` đứng trước `zserver` mà vẫn xếp sau."""
    found = scan.scan(tree(tmp_path, {"client/Dockerfile": API_DF, "zserver/Dockerfile": API_DF})).dockerfile
    assert found.value == "zserver/Dockerfile" and found.candidates == ("zserver/Dockerfile", "client/Dockerfile")
    assert "chưa xác nhận" in found.verify and "client/Dockerfile (tên 'client'" in found.verify


def test_root_dockerfile_plus_web_picks_root_with_two_candidates_and_verify(tmp_path):
    result = scan.scan(tree(tmp_path, {"Dockerfile": API_DF, "web/Dockerfile": SPA_DF}))
    assert result.dockerfile.value == "Dockerfile" and result.dockerfile.candidates == ("Dockerfile", "web/Dockerfile")
    assert result.dockerfile.verify.startswith("chọn Dockerfile trong [Dockerfile, web/Dockerfile]")
    assert "FROM nginx" in result.dockerfile.verify


def test_web_beside_deep_api_server_prefers_the_api_server_but_asks(tmp_path):
    """Luật cũ "nông hơn thắng" sẽ chọn web/; dấu hiệu web xếp web/ xuống cuối. Kết quả chỉ là gợi ý."""
    result = scan.scan(tree(tmp_path, {"web/Dockerfile": SPA_DF, "apps/api-server/Dockerfile": API_DF}))
    assert result.dockerfile.value == "apps/api-server/Dockerfile"
    assert result.dockerfile.candidates == ("apps/api-server/Dockerfile", "web/Dockerfile")
    assert "gợi ý xếp hạng, chưa xác nhận" in result.dockerfile.verify
    for banned in ("đã xác định", "chắc chắn", "nhận biết"):
        assert banned not in result.dockerfile.verify


def test_single_candidate_with_web_hints_says_not_sure_it_is_the_api(tmp_path):
    found = scan.scan(tree(tmp_path, {"web/Dockerfile": SPA_DF})).dockerfile
    assert found.value == "web/Dockerfile" and found.verify.startswith("chưa chắc web/Dockerfile là Dockerfile của API")
    assert "FROM nginx" in found.verify and "đây là web" not in found.verify
    plain = scan.scan(tree(tmp_path / "p", {"service/Dockerfile": API_DF})).dockerfile
    assert plain.verify is None


def test_build_command_alone_is_not_a_web_hint(tmp_path):
    """`npm run build` có mặt cả trong API Node: nếu coi là dấu hiệu web thì VERIFY dư thừa làm mất giá trị cảnh báo."""
    node_api = 'FROM node:22\nRUN npm run build\nCMD ["node", "dist/server.js"]\n'
    assert scan.scan(tree(tmp_path, {"Dockerfile": node_api})).dockerfile.verify is None


def test_two_level_monorepo_candidates_come_after_the_shallow_group_in_stable_order(tmp_path):
    files = {"apps/b/Dockerfile": API_DF, "services/c/Dockerfile": API_DF, "apps/a/Dockerfile": API_DF, "packages/p/Dockerfile": API_DF}
    assert scan._dockerfile_candidates(tree(tmp_path, files)) == ["apps/a/Dockerfile", "apps/b/Dockerfile", "packages/p/Dockerfile", "services/c/Dockerfile"]
    found = scan.scan(tmp_path).dockerfile
    assert found.value == "apps/a/Dockerfile" and found.verify and len(found.candidates) == 4
    shallow = tree(tmp_path / "s", {**files, "zz/Dockerfile": API_DF})
    assert scan._dockerfile_candidates(shallow)[0] == "zz/Dockerfile"       # sâu 1 đứng trước sâu 2


def test_context_is_ambiguous_when_copy_dot_is_valid_at_every_level(tmp_path):
    result = scan.scan(tree(tmp_path, {"apps/api-server/Dockerfile": API_DF}))
    assert result.dockerfile.verify is None
    assert result.context == "apps/api-server" and result.context_finding.verify
    assert result.copy.valid_contexts == ("apps/api-server", "apps", ".")
    assert "gợi ý, chưa xác nhận" in result.context_finding.verify


def test_vahan_style_copy_from_repo_root_selects_dot_without_verify(tmp_path):
    files = {"apps/api-server/Dockerfile": "FROM x\nCOPY apps/api-server/pyproject.toml ./\nCOPY apps/api-server/app ./app\n",
             "apps/api-server/pyproject.toml": "", "apps/api-server/app/main.py": ""}
    result = scan.scan(tree(tmp_path, files))
    assert (result.context, result.context_finding.verify, result.copy.valid_contexts) == (".", None, (".",))
    chosen = result.copy.chosen_check
    assert [(r.kind, r.repo_path) for r in chosen.resolved] == [("file", "apps/api-server/pyproject.toml"), ("dir", "apps/api-server/app")]


def test_copy_relative_to_the_service_directory_selects_that_directory(tmp_path):
    files = {"apps/api-server/Dockerfile": "FROM x\nCOPY pyproject.toml ./\n", "apps/api-server/pyproject.toml": ""}
    result = scan.scan(tree(tmp_path, files))
    assert (result.context, result.context_finding.verify) == ("apps/api-server", None)


@pytest.mark.parametrize("dockerfile", [
    "FROM x\nCOPY <<EOF /app/run.sh\n#!/bin/sh\nEOF\n",
    "FROM x\nARG SRC=app\nCOPY ${SRC} ./app\n",
    "# escape=`\nFROM x\nCOPY app ./app\n",
    'FROM x\nCOPY ["app", "./app"\n',
])
def test_unparseable_copy_asks_instead_of_claiming_a_context(tmp_path, dockerfile):
    result = scan.scan(tree(tmp_path, {"api/Dockerfile": dockerfile, "api/app/main.py": ""}))
    assert result.context_finding.verify.startswith("không suy được context") and result.copy.unparsed


def test_no_context_satisfies_the_copy_sources_asks_with_the_missing_paths(tmp_path):
    result = scan.scan(tree(tmp_path, {"api/Dockerfile": "FROM x\nCOPY nowhere ./n\n"}))
    assert result.context_finding.verify.startswith("không context nào") and "nowhere" in result.context_finding.verify
    assert result.copy.valid_contexts == ()


def test_dockerfile_flag_without_context_flag_still_infers_context_and_asks(tmp_path):
    root = tree(tmp_path, {"apps/api-server/Dockerfile": API_DF, "web/Dockerfile": SPA_DF})
    flagged = scan.scan(root, scan.Overrides(dockerfile="apps/api-server/Dockerfile"))
    assert flagged.dockerfile.source == "flag" and flagged.dockerfile.verify is None and flagged.context_finding.verify
    decided = tree(tmp_path / "d", {"apps/api-server/Dockerfile": "FROM x\nCOPY pyproject.toml ./\n", "apps/api-server/pyproject.toml": ""})
    assert scan.scan(decided, scan.Overrides(dockerfile="apps/api-server/Dockerfile")).context_finding.verify is None


def test_context_flag_never_asks_but_warns_when_copy_sources_do_not_exist(tmp_path):
    root = tree(tmp_path, {"apps/api-server/Dockerfile": "FROM x\nCOPY pyproject.toml ./\n", "apps/api-server/pyproject.toml": ""})
    ok = scan.scan(root, scan.Overrides(context="apps/api-server"))
    assert (ok.context, ok.context_finding.source, ok.context_finding.verify, ok.warnings) == ("apps/api-server", "flag", None, [])
    bad = scan.scan(root, scan.Overrides(context="."))
    assert bad.context_finding.verify is None and bad.context_finding.source == "flag"
    assert len(bad.warnings) == 1 and "pyproject.toml" in bad.warnings[0] and "--sut-context ." in bad.warnings[0]


def test_vahan_fixture_and_noteboard_get_no_new_verify_on_dockerfile_or_context(vahan):
    result = scan.scan(vahan)
    assert result.dockerfile.verify is None and result.context_finding.verify is None and result.warnings == []
    noteboard = scan.scan(Path(__file__).resolve().parent / "fixtures" / "sut" / "noteboard")
    assert noteboard.context == "." and noteboard.context_finding.verify is None and noteboard.warnings == []
    assert noteboard.dockerfile.verify is not None          # đã có từ trước ở tầng scan (gốc + ui/); chỉ init từng làm rơi mất


# ---------- S4-08: hàm đọc COPY/ADD (dùng lại ở S4-10, S4-11) ----------

def test_copy_parser_classifies_every_source_and_records_why_it_skips():
    text = """FROM node AS build
COPY --from=build /app/dist /srv
COPY --chown=1:1 package.json package-lock.json ./
ADD https://example.com/x.tgz /tmp/
COPY . .
COPY src/*.py ./src/
COPY ../outside /x
ADD ["dir with space/f", "./"]
RUN cat <<EOF > /x
COPY not-an-instruction /y
EOF
COPY \\
  # comment in the middle
  last.txt ./
"""
    sources = dockerfile_copy.parse_sources(text)
    assert [(s.raw, s.kind) for s in sources] == [
        ("/app/dist", "skipped"), ("package.json", "path"), ("package-lock.json", "path"), ("https://example.com/x.tgz", "skipped"),
        (".", "context"), ("src/*.py", "glob"), ("../outside", "skipped"), ("dir with space/f", "path"), ("last.txt", "path")]
    reasons = {s.raw: s.reason for s in sources if s.kind == "skipped"}
    assert "--from" in reasons["/app/dist"] and "URL" in reasons["https://example.com/x.tgz"] and "context" in reasons["../outside"]


def test_copy_check_separates_dir_file_context_and_glob_per_context(tmp_path):
    root = tree(tmp_path, {"svc/Dockerfile": "FROM x\nCOPY app ./a\nCOPY pyproject.toml ./\nCOPY . .\nCOPY conf/*.ini /c/\nCOPY --from=b /x /y\n",
                           "svc/app/m.py": "", "svc/pyproject.toml": "", "svc/conf/a.ini": ""})
    analysis = dockerfile_copy.analyze(root, "svc/Dockerfile")
    assert analysis.valid_contexts == ("svc",) and analysis.chosen == "svc" and analysis.verify is None
    assert [(r.kind, r.repo_path) for r in analysis.check("svc").resolved] == [
        ("dir", "svc/app"), ("file", "svc/pyproject.toml"), ("context", "svc"), ("glob", "svc/conf/*.ini"), ("skipped", None)]
    assert not analysis.check(".").valid and {r.repo_path for r in analysis.check(".").missing} == {"app", "pyproject.toml", "conf/*.ini"}
    assert [s.raw for s in analysis.skipped] == ["/x"]


def test_context_candidates_follow_the_dockerfile_up_to_the_root():
    assert dockerfile_copy.context_candidates("Dockerfile") == ["."]
    assert dockerfile_copy.context_candidates("api/Dockerfile") == ["api", "."]
    assert dockerfile_copy.context_candidates("apps/api-server/Dockerfile") == ["apps/api-server", "apps", "."]
    assert dockerfile_copy.context_candidates("docker/prod.Dockerfile") == ["docker", "."]
    assert dockerfile_copy.default_context("docker/prod.Dockerfile") == "." and dockerfile_copy.default_context("api/Dockerfile") == "api"


# ---------- S4-08 (sửa sau review): cú pháp COPY và thiếu nguồn ----------

def test_copy_flags_before_json_form_and_tab_separators_are_parsed(tmp_path):
    text = 'FROM x\nCOPY --chown=1:1 ["src.txt", "/app/"]\nCOPY\tsrc.txt /app/\nADD  --chmod=644   a.txt\tb.txt  /app/\ncopy --from=b --chown=1:1 /x /y\n'
    sources = dockerfile_copy.parse_sources(text)
    assert [(s.instruction, s.raw, s.kind) for s in sources] == [
        ("COPY", "src.txt", "path"), ("COPY", "src.txt", "path"), ("ADD", "a.txt", "path"), ("ADD", "b.txt", "path"), ("COPY", "/x", "skipped")]
    root = tree(tmp_path, {"Dockerfile": text, "src.txt": "", "a.txt": "", "b.txt": ""})
    analysis = dockerfile_copy.analyze(root, "Dockerfile")
    assert analysis.chosen_check.valid and analysis.verify is None and not analysis.chosen_check.missing


def test_tab_after_the_instruction_name_still_counts_as_a_missing_source(tmp_path):
    result = scan.scan(tree(tmp_path, {"Dockerfile": "FROM x\nCOPY\tabsent.txt /app/\n"}))
    assert result.copy.valid_contexts == () and "absent.txt" in result.context_finding.verify


def test_root_dockerfile_with_a_missing_copy_source_asks_instead_of_staying_silent(tmp_path):
    """Chỉ một context khả dĩ cũng phải VERIFY khi không context nào hợp lệ: nguồn thiếu có thể là Dockerfile sai hoặc build từ chỗ khác."""
    result = scan.scan(tree(tmp_path, {"Dockerfile": "FROM x\nCOPY absent.txt /app/\n"}))
    assert result.copy.valid_contexts == () and result.context == "."
    assert result.context_finding.verify.startswith("không context nào") and "absent.txt" in result.context_finding.verify
    assert result.dockerfile.verify is None
    unparsed = scan.scan(tree(tmp_path / "u", {"Dockerfile": "FROM x\nARG S=a\nCOPY ${S} /app/\n"}))
    assert unparsed.context_finding.verify.startswith("không suy được context")


# ---------- S4-11: health path chỉ lấy từ phạm vi mã API đã chứng minh ----------

def svc(route=None):
    """Một service FastAPI nhỏ (có file khởi tạo app); `route` None = không có route health."""
    body = f'@app.get("{route}")\ndef health():\n    return {{}}\n' if route else ""
    return f"from fastapi import FastAPI\napp = FastAPI()\n{body}"


def health_of(root, **overrides):
    return scan.scan(root, scan.Overrides(**overrides)).health_path


def test_health_e1_copy_dot_with_context_equal_to_the_dockerfile_directory(tmp_path):
    files = {"apps/api-server/Dockerfile": "FROM x\nCOPY . .\n", "apps/api-server/main.py": svc("/health"), "apps/gateway/main.py": svc("/api/health")}
    root = tree(tmp_path, files)
    health = health_of(root, context="apps/api-server")
    assert (health.value, health.verify) == ("/health", None) and health.candidates == ("/health (apps/api-server/main.py)",)
    assert "E1 apps/api-server" in health.reason and not any("/api/health" in c for c in health.candidates)
    ambiguous = scan.scan(root)       # không có flag: context mơ hồ (VERIFY) nên COPY . . không phải bằng chứng
    assert ambiguous.context_finding.verify and ambiguous.health_path.value == "/" and ambiguous.health_path.verify


def test_health_e2_copy_of_a_specific_directory_with_repo_root_context(tmp_path):
    files = {"apps/api-server/Dockerfile": "FROM x\nCOPY apps/api-server/app ./app\n", "apps/api-server/app/main.py": svc("/health"),
             "apps/gateway/main.py": svc("/api/health")}
    result = scan.scan(tree(tmp_path, files))
    assert result.context == "." and result.context_finding.verify is None
    assert (result.health_path.value, result.health_path.verify) == ("/health", None)
    assert "E2 COPY apps/api-server/app -> apps/api-server/app" in result.health_path.reason


def test_health_e2_dockerfile_in_deploy_copies_code_from_another_directory(tmp_path):
    files = {"deploy/api/Dockerfile": "FROM x\nCOPY apps/api/ ./app\n", "apps/api/main.py": svc("/health"), "apps/other/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files), dockerfile="deploy/api/Dockerfile")   # deploy/ nằm ngoài nhóm tự tìm: đặt bằng flag
    assert (health.value, health.verify) == ("/health", None) and health.candidates == ("/health (apps/api/main.py)",)
    assert "E2 COPY apps/api/ -> apps/api" in health.reason


def test_health_dockerfile_location_alone_is_not_evidence(tmp_path):
    """deploy/api chỉ COPY requirements.txt: mã API ở nơi khác nên phạm vi chưa chứng minh, `/` chỉ là giá trị tạm kèm VERIFY."""
    files = {"deploy/api/Dockerfile": "FROM x\nCOPY requirements.txt ./\n", "deploy/api/requirements.txt": "",
             "apps/api/main.py": svc("/health"), "apps/other/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files), dockerfile="deploy/api/Dockerfile")
    assert health.value == "/" and health.source == "default"
    assert health.candidates == ("/api/health (apps/other/main.py)", "/health (apps/api/main.py)")
    for needle in ("TẠM", "--health-path", "sut_health_path", "2xx", "apps/api/main.py", "apps/other/main.py"):
        assert needle in health.verify, needle
    for banned in ("đã xác định", "chắc chắn", "an toàn"):
        assert banned not in health.verify


def test_health_unresolvable_copy_source_is_not_evidence(tmp_path):
    files = {"deploy/api/Dockerfile": "FROM x\nARG SRC=apps/api\nCOPY ${SRC} ./app\n", "apps/api/main.py": svc("/health"), "apps/other/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files), dockerfile="deploy/api/Dockerfile")
    assert health.value == "/" and health.verify and "context chưa chốt" in health.verify


def test_health_single_file_copy_does_not_make_the_dockerfile_directory_a_code_scope(tmp_path):
    """Dockerfile ở svc/ chỉ COPY pyproject.toml (tệp lẻ), svc/ không chứa mã: không đạt E1, không được chọn `/` im lặng."""
    files = {"svc/Dockerfile": "FROM x\nCOPY pyproject.toml ./\n", "svc/pyproject.toml": "", "apps/api/main.py": svc("/health"), "apps/other/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files))
    assert health.value == "/" and health.verify and "/health (apps/api/main.py)" in health.verify


def test_health_root_dockerfile_copying_one_directory_keeps_vahan_like_result(tmp_path):
    files = {"Dockerfile": "FROM x\nCOPY apps/api-server/app ./app\n", "apps/api-server/app/main.py": svc("/health"), "apps/web-ui/api.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files))
    assert (health.value, health.verify) == ("/health", None)


def test_health_root_copy_dot_with_two_services_asks_and_keeps_a_temporary_slash(tmp_path):
    files = {"Dockerfile": "FROM x\nCOPY . .\n", "apps/a/main.py": svc("/health"), "apps/b/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files))
    assert health.value == "/" and health.verify and health.candidates == ("/api/health (apps/b/main.py)", "/health (apps/a/main.py)")


def test_health_single_service_repo_is_e3_and_two_routes_still_ask(tmp_path):
    one = tree(tmp_path / "one", {"Dockerfile": "FROM x\nCOPY . .\n", "main.py": svc("/health")})
    health = health_of(one)
    assert (health.value, health.verify) == ("/health", None) and "E3" in health.reason
    two = tree(tmp_path / "two", {"Dockerfile": "FROM x\nCOPY . .\n", "main.py": svc("/health") + '@app.get("/healthz")\ndef z():\n    return {}\n'})
    assert health_of(two).verify.startswith("chọn /health trong [/health (main.py), /healthz (main.py)]")


@pytest.mark.parametrize("extra", [{"web/Dockerfile": "FROM nginx\n"}, {"requirements.txt": "", "sub/requirements.txt": ""}, {"other.py": svc()}])
def test_health_e3_needs_every_condition(tmp_path, extra):
    """Có dấu hiệu service thứ hai (Dockerfile khác, hai gốc dự án Python, hai file khởi tạo app): không đủ bằng chứng."""
    files = {"Dockerfile": "FROM x\nCOPY . .\n", "main.py": svc("/health"), **extra}
    health = health_of(tree(tmp_path, files), dockerfile="Dockerfile")
    assert health.value == "/" and health.verify and "TẠM" in health.verify


def test_health_proven_scope_without_routes_ignores_other_services(tmp_path):
    files = {"deploy/api/Dockerfile": "FROM x\nCOPY apps/api/ ./app\n", "apps/api/main.py": svc(), "apps/other/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files), dockerfile="deploy/api/Dockerfile")
    assert (health.value, health.source, health.verify, health.candidates) == ("/", "default", None, ())


def test_health_unproven_with_no_route_anywhere_stays_the_old_default(tmp_path):
    health = health_of(tree(tmp_path, {"deploy/api/Dockerfile": "FROM x\nCOPY requirements.txt ./\n", "deploy/api/requirements.txt": "", "apps/a/main.py": svc(), "apps/b/main.py": svc()}), dockerfile="deploy/api/Dockerfile")
    assert (health.value, health.source, health.verify) == ("/", "default", None)


def test_health_flag_wins_and_scans_nothing(tmp_path):
    files = {"deploy/api/Dockerfile": "FROM x\nCOPY requirements.txt ./\n", "deploy/api/requirements.txt": "", "apps/a/main.py": svc("/health"), "apps/b/main.py": svc("/api/health")}
    health = health_of(tree(tmp_path, files), dockerfile="deploy/api/Dockerfile", health_path="/x")
    assert (health.value, health.source, health.verify, health.candidates) == ("/x", "flag", None, ("/x",))
