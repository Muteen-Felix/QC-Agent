"""Hạ tầng chung cho các test harness local (S4-05): workspace là repo git thật có `base`/`head`, các PR mẫu của fixture `node-api-harness` (N1–N4), image đã kiểm, và
chạy workflow qua tools/run_reusable_locally.py với GitHub/Anthropic/Jira GIẢ. Import được khi không có Docker (test fixture không Docker dùng phần đầu file)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import image_check  # noqa: E402
import run_reusable_locally as harness  # noqa: E402

HOST = os.environ.get("QC_TEST_DOCKER_HOST", "host.docker.internal")   # địa chỉ máy chủ nhìn từ container (Linux: IP của docker0)
WORKSPACES = ROOT / "tests" / ".docker-workspaces"                    # Docker Desktop không mount được thư mục tạm trên C:
NODE_SUT = ROOT / "tests" / "fixtures" / "sut" / "node-api-harness"
NOTEBOARD_SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
POLICY_DIR = ROOT / "configs" / "projects"
NODE_PROJECT = "node-api-harness"   # slug CHƯA đăng ký: đi đường `_default.yaml`
ENV_REQUIRE = "QC_HARNESS_REQUIRE_DOCKER"
ENV_BUILD = "QC_HARNESS_BUILD"


# ───────────────────────── repo git ─────────────────────────

def rmtree(path: Path) -> None:
    """Windows: object trong .git là read-only nên rmtree thường bỏ sót; gỡ cờ rồi xoá lại."""
    shutil.rmtree(path, onerror=lambda func, target, _exc: (os.chmod(target, stat.S_IWRITE), func(target)))


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "-c", "core.autocrlf=false", *args], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def commit(repo: Path, message: str) -> str:
    """Commit mọi thay đổi NGOẠI TRỪ `runs/` (đầu ra của gate không được lọt vào repo: nó làm `checkout` thất bại và lần chạy sau đọc nhầm kết quả cũ)."""
    git(repo, "add", "-A", "--", ".", ":!runs")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@contextmanager
def workspace_dir():
    base = WORKSPACES / uuid.uuid4().hex
    base.mkdir(parents=True)
    try:
        yield base
    finally:
        rmtree(base)


def make_repo(source: Path, parent: Path, *, name: str | None = None) -> tuple[Path, str]:
    """Bản sao `source` thành repo git có commit `base` (chưa có `runs/`). Trả (đường dẫn, sha base)."""
    repo = parent / (name or source.name)
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns("runs", "__pycache__", ".git"))
    git(repo, "init", "-q")
    return repo, commit(repo, "base")


def drop_runs(repo: Path) -> None:
    shutil.rmtree(repo / "runs", ignore_errors=True)


# ───────────────────────── PR mẫu của node-api-harness ─────────────────────────

def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{path.name}: không thấy đúng một lần {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def _edit_json(path: Path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def apply_n1(repo: Path) -> None:
    """N1: sửa route người dùng và mô tả trong openapi.json (không đụng `.qc-agent/**` hay lockfile)."""
    _edit(repo / "src" / "routes" / "users.ts", "{ id, active: true }", "{ id, active: id % 2 === 0 }")
    _edit_json(repo / "openapi.json", lambda d: d["paths"]["/users/{id}"]["get"].update(summary="Lấy người dùng theo id"))


def apply_n2(repo: Path) -> None:
    """N2: route debug có `eval` (Semgrep js-eval-dynamic, Critical)."""
    (repo / "src" / "routes" / "debug.ts").write_text("export function debug(expr: string): unknown {\n  return eval(expr);\n}\n", encoding="utf-8", newline="\n")


PROFILE_PATH = "/users/{id}/profile"
PROFILE_OPERATION = {"get": {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}], "responses": {
    "200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "required": ["id", "bio"],
                                                                            "properties": {"id": {"type": "integer"}, "bio": {"type": "string"}}}}}},
    "404": {"description": "not found"}}}}


def apply_n3(repo: Path) -> None:
    """N3: operation mới + handler THẬT, chỉ đổi `openapi.json` và `src/**` (KHÔNG đụng `.qc-agent/**`: nó là FULL SET theo `_default.yaml` và kéo `deps` vào).
    `exclude_path` của operation này đã có sẵn trong suite gốc của fixture: đó mới là lý do phát sinh nợ Low."""
    _edit_json(repo / "openapi.json", lambda d: d["paths"].update({PROFILE_PATH: PROFILE_OPERATION}))
    (repo / "src" / "routes" / "profile.ts").write_text(
        "export const getProfile = (id: number): { status: number; body: unknown } =>\n"
        "  id > 1000 ? { status: 404, body: { error: \"not found\" } } : { status: 200, body: { id, bio: \"hi\" } };\n", encoding="utf-8", newline="\n")
    server = repo / "src" / "server.ts"
    _edit(server, 'import { getUser } from "./routes/users.ts";', 'import { getProfile } from "./routes/profile.ts";\nimport { getUser } from "./routes/users.ts";')
    _edit(server, '  const user = path.match(', '  const profile = path.match(/^\\/users\\/(\\d+)\\/profile$/);\n'
                                               '  if (req.method === "GET" && profile) {\n    const result = getProfile(Number(profile[1]));\n    return send(res, result.status, result.body);\n  }\n'
                                               '  const user = path.match(')


def apply_n4(repo: Path) -> None:
    """N4: đổi `package-lock.json` ở gốc (khớp glob FULL SET của `_default.yaml`); left-pad vẫn là dependency prod nên `deps` vẫn quét được."""
    _edit_json(repo / "package-lock.json", lambda d: d["packages"][""].update(version="1.0.1") or d.update(version="1.0.1"))


def strip_exclude_path(repo: Path) -> None:
    """Đối chứng N3: bỏ `exclude_path` khỏi suite api-contract (làm TRƯỚC commit base, để PR vẫn không đụng `.qc-agent/**`)."""
    suite = repo / ".qc-agent" / "suites" / "api-contract.yaml"
    text = suite.read_text(encoding="utf-8")
    start = text.index("    exclude_path:\n")
    end = text.index("  oracle:", start)
    suite.write_text(text[:start] + text[end:], encoding="utf-8", newline="\n")


# ───────────────────────── Docker + image ─────────────────────────

_VERDICT: dict = {}


def docker_required() -> bool:
    return os.environ.get(ENV_REQUIRE, "").strip() == "1"


def _unavailable(reason: str):
    if docker_required():
        pytest.fail(f"{ENV_REQUIRE}=1 nhưng {reason}", pytrace=False)
    pytest.skip(reason + " (SKIP = CHƯA KIỂM CHỨNG)")


def verified_image() -> image_check.Verdict:
    """Gọi từ fixture module-scope. Thiếu docker/image/build lỗi => skip có lý do (hoặc LỖI nếu QC_HARNESS_REQUIRE_DOCKER=1).
    Image CŨ (StaleImage) => LỖI, không bao giờ skip: image cũ làm MỌI test Docker fail."""
    if "verdict" in _VERDICT:
        return _VERDICT["verdict"]
    if not image_check.docker_available():
        _unavailable("không có docker trong PATH")
    try:
        verdict = image_check.ensure_image(build=os.environ.get(ENV_BUILD, "").strip() == "1")
    except image_check.StaleImage as error:
        pytest.fail(str(error), pytrace=False)
    except image_check.ImageCheckError as error:
        _unavailable(f"không có image dùng được: {error}")
    _VERDICT["verdict"] = verdict
    return verdict


def require_fresh_trivy_db(image: str, *, deps_expected: bool) -> int:
    """Preflight TRƯỚC Gate. `deps_expected=True` mà DB quá 14 ngày => lỗi cứng (không skip)."""
    try:
        return image_check.check_trivy_db(image, deps_expected=deps_expected)
    except image_check.StaleTrivyDb as error:
        pytest.fail(str(error), pytrace=False)


def cleanup_containers() -> None:
    subprocess.run(["docker", "rm", "-f", "sut", "ui", "db"], capture_output=True)
    subprocess.run(["docker", "network", "rm", "qc-net"], capture_output=True)


@dataclass
class Result:
    """Kết quả của một lần chạy workflow."""
    steps: dict
    log: str
    repo: Path
    run_dir: Path | None
    selection: dict | None = None
    report: dict | None = field(default=None)

    def outputs(self, step: str) -> dict:
        return self.steps[step]["outputs"]

    def code(self, step: str) -> int:
        return self.steps[step]["returncode"]


# ───────────────────────── dựng SUT và thăm dò HTTP (độc lập với gate) ─────────────────────────

_FETCH = ("import sys, urllib.request as u, urllib.error as e\n"
          "try:\n    r = u.urlopen(sys.argv[1], timeout=5); print(r.status); print(r.read().decode())\n"
          "except e.HTTPError as x:\n    print(x.code); print(x.read().decode())\n")


def _docker(*args: str, cwd: Path | None = None, timeout: int = 600):
    return subprocess.run(["docker", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def probe_sut(repo: Path, image: str, paths: list[str], *, port: int = 3000, health: str = "/health") -> dict[str, tuple[int, object]]:
    """Dựng SUT từ `repo` (docker build + run trên mạng qc-net, như bước Start SUT) rồi GET từng path bằng python của image qc-agent. Trả {path: (status, body)}.
    Dùng để khẳng định handler của PR thật sự trả 2xx/404, độc lập với gate (workflow dọn container `sut` khi chạy xong)."""
    tag = f"qc-harness-probe-{uuid.uuid4().hex[:8]}"
    cleanup_containers()
    try:
        assert _docker("network", "create", "qc-net").returncode == 0
        built = _docker("build", "-q", "-f", "Dockerfile", "-t", tag, ".", cwd=repo)
        assert built.returncode == 0, built.stderr
        started = _docker("run", "-d", "--name", "sut", "--network", "qc-net", tag)
        assert started.returncode == 0, started.stderr

        def get(path: str):
            done = _docker("run", "--rm", "--network", "qc-net", "--entrypoint", "python", image, "-c", _FETCH, f"http://sut:{port}{path}", timeout=60)
            return done

        for _ in range(30):
            if get(health).returncode == 0:
                break
            subprocess.run([sys.executable, "-c", "import time; time.sleep(1)"], check=False)
        else:
            raise AssertionError("SUT không sẵn sàng: " + _docker("logs", "sut").stdout[-500:])
        out = {}
        for path in paths:
            done = get(path)
            assert done.returncode == 0, done.stderr
            status, _, body = done.stdout.partition("\n")
            try:
                out[path] = (int(status), json.loads(body))
            except ValueError:
                out[path] = (int(status), body.strip())
        return out
    finally:
        cleanup_containers()
        _docker("rmi", "-f", tag)


# ───────────────────────── fake GitHub / Anthropic / Jira + chạy workflow ─────────────────────────

FAKE_KEY = harness.FAKE_ANTHROPIC_KEY


def select_response(*workers: str, reason: str = "harness fake LLM") -> dict:
    """Body Messages API của Anthropic mà Select (`select_workers`) chấp nhận. LLM là GIẢ: harness không kiểm chất lượng Selector."""
    return {"id": "msg_harness", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001", "stop_reason": "tool_use", "stop_sequence": None,
            "content": [{"type": "tool_use", "id": "toolu_harness", "name": "select_workers", "input": {"selections": [{"worker": w, "reason": reason} for w in workers]}}],
            "usage": {"input_tokens": 1200, "output_tokens": 40, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


@dataclass
class Stack:
    gh: object
    llm: object
    jira: object
    gh_url: str
    llm_url: str
    jira_url: str


@contextmanager
def fake_stack(*llm_script):
    """GitHub/Anthropic/Jira giả bind 0.0.0.0, URL nhìn từ container. `llm_script` = các response lần lượt của FakeAnthropic (hết thì lặp phần tử cuối)."""
    from tests.fakes import FakeAnthropic, FakeGitHub, FakeJira
    with FakeGitHub(host="0.0.0.0") as gh, FakeAnthropic(*llm_script, key=FAKE_KEY, host="0.0.0.0") as llm, FakeJira(host="0.0.0.0") as jira:
        yield Stack(gh, llm, jira, f"http://{HOST}:{gh.server.server_port}", f"http://{HOST}:{llm.server.server_port}", f"http://{HOST}:{jira.server.server_port}")


def default_only_policy(parent: Path) -> Path:
    """Policy dir chỉ có bản chép của `_default.yaml` (chứng minh đường mặc định)."""
    target = parent / "policy-default"
    target.mkdir(exist_ok=True)
    shutil.copy(POLICY_DIR / "_default.yaml", target / "_default.yaml")
    return target


def pr_files_from_git(repo: Path, base: str, head: str) -> list[dict]:
    """`GET /pulls/N/files` giả từ `git diff` thật: {"filename", "patch"} (patch chỉ gồm các hunk, như API của GitHub)."""
    files = []
    for name in git(repo, "diff", "--name-only", base, head).splitlines():
        patch = git(repo, "diff", "--unified=3", base, head, "--", name)
        files.append({"filename": name, "patch": patch[patch.index("@@"):] if "@@" in patch else ""})
    return files


def run_pr(stack: Stack, repo: Path, *, project: str, policy_dir: Path, image: str, base: str | None, head: str, run_id: str, home: Path,
           inputs: dict | None = None, secrets: dict | None = None, event_name: str = "pull_request", dispatch_inputs: dict | None = None,
           skip: tuple = ("Pull qc-agent image",), jira: bool = True, anthropic: bool = True) -> Result:
    """Chạy workflow qc-gate.reusable.yml NGUYÊN VĂN (trừ điểm lệch có tên: `step_rewrites` của Select) trên `repo`, rồi gom kết quả. Dọn container trong `finally`."""
    all_inputs = {"project": project, "image": image, "allow_unpinned_image": "true", "refine": "off", **(inputs or {})}
    all_secrets = {"ANTHROPIC_API_KEY": FAKE_KEY, **({"JIRA_BASE_URL": stack.jira_url, "JIRA_EMAIL": "qc@example.invalid", "JIRA_API_TOKEN": "harness-fake"} if jira else {}),
                   **(secrets or {})}
    if (repo / "runs").exists():
        # Checkout của CI luôn mới: không có `runs/` của lần trước. Để lại thì gitleaks (quét working tree) báo `generic-api-key` trên report.json cũ và lần chạy lại BLOCKED.
        # Cất ra ngoài workspace (vẫn đọc được để so sánh); cache Select nằm ở `home`, không mất.
        shutil.move(str(repo / "runs"), str(repo.parent / f"runs-before-{run_id}"))
    event_dir = repo.parent / f"event-{run_id}"
    event_dir.mkdir(exist_ok=True)
    github = harness.build_github_context(event_dir, api_url=stack.gh_url, sha=head, base_sha=base, run_id=run_id, event_name=event_name, dispatch_inputs=dispatch_inputs)
    home.mkdir(parents=True, exist_ok=True)
    step_env = {harness.SELECT_STEP: {"HOME": str(home), **({"ANTHROPIC_BASE_URL": stack.llm_url} if anthropic else {})}}
    logs: list[str] = []
    try:
        steps = harness.run_workflow(harness.DEFAULT_WORKFLOW, repo, all_inputs, all_secrets, github, skip=skip, echo=logs.append, policy_dir=policy_dir,
                                     step_env=step_env, step_rewrites=harness.anthropic_rewrites() if anthropic else None)
    finally:
        cleanup_containers()
    runs = sorted((repo / "runs").glob("r-*"))
    run_dir = runs[-1] if runs else None
    read = lambda name: json.loads((run_dir / name).read_text(encoding="utf-8")) if run_dir and (run_dir / name).is_file() else None  # noqa: E731
    return Result(steps, "\n".join(logs), repo, run_dir, read("selection.json"), read("report.json"))


# ───────────────────────── đối chiếu kết quả chạy ─────────────────────────

def plan_tasks(res: Result) -> list[dict]:
    import yaml
    return yaml.safe_load((res.run_dir / "plan.yaml").read_text(encoding="utf-8"))["tasks"] if res.run_dir else []


def capabilities(res: Result) -> set[str]:
    return {task["capability"] for task in plan_tasks(res)}


def preflight(image: str, scenario: str, *, expect_deps: bool) -> None:
    """TRƯỚC Gate: kịch bản khai `expect_deps`. True mà DB Trivy quá 14 ngày => LỖI CỨNG, không chạy Gate; False chỉ cảnh báo."""
    require_fresh_trivy_db(image, deps_expected=expect_deps)


def check_expect_deps(res: Result, scenario: str, *, expect_deps: bool, image: str | None = None) -> None:
    """SAU Gate: đối chiếu `expect_deps` với suite THỰC SỰ chạy (task `deps.vuln`). Lệch => test fail có tên (khai báo không được trôi âm thầm)."""
    ran = "deps.vuln" in capabilities(res)
    if ran == expect_deps:
        return
    note = ""
    if image:
        try:
            note = f" (DB Trivy của image {image_check.trivy_db_age_days(image)} ngày; ngưỡng {image_check.MAX_DB_AGE_DAYS})"
        except image_check.ImageCheckError:
            pass
    pytest.fail(f"kịch bản {scenario} khai expect_deps={expect_deps} nhưng deps {'ĐÃ' if ran else 'KHÔNG'} chạy{note}: sửa khai báo hoặc sửa PR mẫu", pytrace=False)


def check_runs(stack: Stack) -> list[tuple[str, str, str]]:
    return [(c["name"], c["conclusion"], (c.get("output") or {}).get("title", "")) for c in stack.gh.check_runs]
