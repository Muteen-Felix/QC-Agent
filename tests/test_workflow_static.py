"""Kiểm cấu trúc của .github/workflows/qc-gate.reusable.yml không cần Docker/GitHub (bước 31/35): policy từ main, mount chỉ-đọc, không cho SUT chọn ref."""
import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "qc-gate.reusable.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
DATA = yaml.safe_load(TEXT)
STEPS = DATA["jobs"]["gate"]["steps"]
NAMES = [s.get("name") or s.get("uses") for s in STEPS]


def step(name):
    return next(s for s in STEPS if s.get("name") == name)


def test_policy_repo_is_a_single_hardcoded_constant_and_main_is_not_configurable():
    assert DATA["env"]["QC_AGENT_REPO"] == "Muteen-Felix/QC-Agent"
    assert len(re.findall(r"Muteen-Felix/QC-Agent", TEXT.replace("uses: Muteen-Felix/QC-Agent/.github", ""))) == 1  # đúng một chỗ (ngoài ví dụ `uses:`)
    inputs = DATA["on"]["workflow_call"]["inputs"]
    assert not [name for name in inputs if "policy" in name or name.endswith("_ref")]      # Q1: SUT không chọn được ref của policy
    assert "qc_read_token" in DATA["on"]["workflow_call"]["secrets"] and DATA["on"]["workflow_call"]["secrets"]["qc_read_token"]["required"] is False


def test_fetch_policy_runs_before_the_sut_and_the_gate_mounts_it_read_only_outside_the_workspace():
    assert NAMES.index("Fetch policy") < NAMES.index("Start SUT") < NAMES.index("Run qc-agent gate")
    fetch = step("Fetch policy")
    assert fetch["env"]["POLICY_TOKEN"] == "${{ secrets.qc_read_token || github.token }}"
    assert '"$RUNNER_TEMP/qc-policy"' in fetch["run"] and "GITHUB_WORKSPACE" not in fetch["run"] and "?ref=main" in fetch["run"]
    assert "Authorization" in fetch["run"] and "Bearer $POLICY_TOKEN" not in fetch["run"].replace('"$POLICY_TOKEN"', "")   # token qua stdin, không trên dòng lệnh
    gate = step("Run qc-agent gate")["run"]
    assert '-v "$RUNNER_TEMP/qc-policy:/policy:ro"' in gate and "--projects-dir /policy" in gate and '--expect-repo "$GITHUB_REPOSITORY"' in gate
    assert step("Run qc-agent gate")["env"]["QC_POLICY_REF"] == "${{ steps.policy.outputs.ref }}"


def test_there_is_no_fallback_to_the_bundled_snapshot():
    for match in re.findall(r"(?:--projects-dir|QC_PROJECTS_DIR=)[ =]?(\S+)", TEXT):
        assert match == "/policy", match      # mọi chỗ trỏ thư mục policy đều là bản fetch, không có đường về snapshot trong image
    assert "|| true" not in step("Fetch policy")["run"]


def test_refine_step_is_advisory_read_only_and_runs_between_the_sut_and_the_gate():
    assert NAMES.index("Start SUT") < NAMES.index("Refine (onboarding suggestions)") < NAMES.index("Post refine review") < NAMES.index("Run qc-agent gate")
    refine = step("Refine (onboarding suggestions)")
    assert refine["continue-on-error"] is True and "github.event_name == 'pull_request'" in refine["if"] and "inputs.refine != 'off'" in refine["if"]
    assert '-v "$PWD:/work:ro"' in refine["run"] and "init --refine" in refine["run"] and "qc-agent:begin refine" in refine["run"]
    assert "GITHUB_TOKEN" not in refine["run"] and "contents: write" not in TEXT       # không push, không cần quyền ghi
    post = step("Post refine review")
    assert post["continue-on-error"] is True and "steps.refine.outputs.has_patch == 'true'" in post["if"] and "--refine-dir /out" in post["run"]
    assert DATA["permissions"] == {"contents": "read", "checks": "write", "pull-requests": "write", "packages": "read"}   # không thêm quyền nào
    assert DATA["on"]["workflow_call"]["inputs"]["refine"]["default"] == "auto"


def test_select_runs_before_gate_and_cannot_make_job_red():
    assert NAMES.index("Select (PR)") < NAMES.index("Run qc-agent gate")
    select = step("Select (PR)")
    assert "set +e" in select["run"] and "exit 0" in select["run"]
    assert "fetch-depth: 0" in TEXT
    assert '--trigger pr --selection /work/runs/selection.json' in step("Run qc-agent gate")["run"]
    assert '--trigger manual --workers "$WORKERS"' in step("Run qc-agent gate")["run"]
    assert "ANTHROPIC_API_KEY" in DATA["on"]["workflow_call"]["secrets"]


def test_suggest_ui_only_with_an_ui_and_a_model_key():
    run = step("Refine (onboarding suggestions)")["run"]
    assert '[ -n "${UI_URL:-}" ] && [ -n "${MIDSCENE_MODEL_API_KEY:-}" ]' in run and "--suggest-ui --ui-url" in run


def test_the_refine_artifact_is_uploaded_only_when_there_is_a_patch():
    upload = step("Upload refine patch")
    assert "has_patch == 'true'" in upload["if"] and upload["with"]["name"].startswith("qc-refine-") and "refine.patch" in upload["with"]["path"]


# ==================== qc-groundtruth.reusable.yml (S1-07) ====================

import json
import os
import shutil
import subprocess
import sys

import pytest

GT_PATH = WORKFLOW.with_name("qc-groundtruth.reusable.yml")
GT_TEXT = GT_PATH.read_text(encoding="utf-8")
GT = yaml.safe_load(GT_TEXT)
GT_JOBS = GT["jobs"]
GATE_SHAS = set(re.findall(r"uses: ([\w./-]+@[0-9a-f]{40})", TEXT))
USES = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")


def gt_steps(job):
    return GT_JOBS[job]["steps"]


def gt_step(job, name):
    return next(s for s in gt_steps(job) if s.get("name") == name)


def all_gt_steps():
    return [(job, s) for job in GT_JOBS for s in gt_steps(job)]


def test_gt_on_key_parses_as_workflow_call_with_the_documented_inputs_and_secrets():
    call = GT[True]["workflow_call"]                                     # `on` không quote => YAML parse thành True (bẫy đã biết)
    assert {n for n, spec in call["inputs"].items() if spec.get("required")} == {"project", "image", "prd_path"}
    assert call["inputs"]["base_branch"]["default"] == "main" and call["inputs"]["openapi"]["default"] == "" and call["inputs"]["allow_unpinned_image"]["default"] is False
    assert set(call["secrets"]) == {"ANTHROPIC_API_KEY", "qc_bot_token", "GHCR_PULL_TOKEN"} and all(s["required"] is False for s in call["secrets"].values())
    assert set(GT_JOBS) == {"select", "generate", "validate"}


def test_gt_every_action_is_pinned_to_a_commit_sha_shared_with_the_gate_workflow():
    used = [s["uses"] for _, s in all_gt_steps() if "uses" in s]
    assert used and all(USES.fullmatch(u) for u in used), used
    assert set(used) <= GATE_SHAS                                                  # chỉ dùng lại các SHA đã được duyệt ở qc-gate
    assert not re.search(r"uses:\s*\S+@(main|master|v\d)", GT_TEXT)


def test_gt_no_expression_is_ever_interpolated_into_a_run_script():
    for job, s in all_gt_steps():
        if "run" in s:
            assert "${{" not in s["run"], (job, s["name"])                        # inputs/secrets/matrix chỉ đi qua env: của step
    for secret in ("ANTHROPIC_API_KEY", "qc_bot_token", "GHCR_PULL_TOKEN"):
        holders = [(job, s.get("name")) for job, s in all_gt_steps() if f"secrets.{secret}" in json.dumps(s)]
        assert holders, secret
    assert [(j, s["name"]) for j, s in all_gt_steps() if "secrets.ANTHROPIC_API_KEY" in json.dumps(s)] == [("generate", "Generate Ground-Truth")]


def test_gt_the_gate_workflow_also_keeps_expressions_out_of_scripts():
    for s in STEPS:
        if "run" in s:
            assert "${{" not in s["run"], s.get("name")


def test_gt_the_image_must_be_pinned_by_digest_in_every_job_that_pulls_it():
    pulls = [(job, s) for job, s in all_gt_steps() if s.get("name") == "Pull qc-agent image"]
    assert [job for job, _ in pulls] == ["generate", "validate"]
    for _, s in pulls:
        assert "*@sha256:*) ;;" in s["run"] and 'ALLOW_UNPINNED" != "true"' in s["run"] and "exit 1" in s["run"]
        assert s["env"]["IMAGE"] == "${{ inputs.image }}" and s["env"]["ALLOW_UNPINNED"] == "${{ inputs.allow_unpinned_image }}"
    assert GT[True]["workflow_call"]["inputs"]["allow_unpinned_image"]["default"] is False


def test_gt_permissions_are_the_minimum_for_each_job():
    assert GT["permissions"] == {"contents": "read"}
    assert GT_JOBS["select"]["permissions"] == {"contents": "read"}
    assert GT_JOBS["generate"]["permissions"] == {"contents": "write", "pull-requests": "write", "packages": "read"}
    assert GT_JOBS["validate"]["permissions"] == {"contents": "read", "packages": "read"}
    text = json.dumps([j["permissions"] for j in GT_JOBS.values()])
    for forbidden in ("id-token", "actions", "checks", "statuses", "deployments", "administration", "workflows"):
        assert forbidden not in text


def test_gt_generate_never_runs_for_pull_requests_and_validate_only_does():
    assert GT_JOBS["select"]["if"] == "github.event_name != 'pull_request'"
    assert GT_JOBS["generate"]["if"] == "needs.select.outputs.count != '0'" and GT_JOBS["generate"]["needs"] == "select"
    assert GT_JOBS["validate"]["if"] == "github.event_name == 'pull_request'"      # fork PR: không có secret và không có quyền ghi, nên chỉ kiểm


def test_gt_prds_are_processed_one_at_a_time_from_a_selected_matrix():
    strategy = GT_JOBS["generate"]["strategy"]
    assert strategy == {"fail-fast": False, "max-parallel": 1, "matrix": "${{ fromJSON(needs.select.outputs.matrix) }}"}
    assert GT_JOBS["generate"]["concurrency"]["cancel-in-progress"] is False        # không huỷ giữa chừng một lần đẩy lên nhánh bot
    assert GT_JOBS["select"]["outputs"] == {"matrix": "${{ steps.pick.outputs.matrix }}", "count": "${{ steps.pick.outputs.count }}"}


def test_gt_generate_steps_run_in_the_documented_order():
    names = [s.get("name") or s.get("uses") for s in gt_steps("generate")]
    order = ["Log in to GHCR", "Pull qc-agent image", "Resolve PRD", "Prepare bot branch", "Generate Ground-Truth",
             "Commit and push to the bot branch", "Open or update the pull request", "Upload egress log and summary"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    assert gt_step("generate", "Upload egress log and summary")["if"] == "always()"
    assert gt_step("generate", "Open or update the pull request")["if"] == "steps.push.outputs.changed == 'true'"


def test_gt_checkouts_do_not_persist_credentials_into_the_mounted_workspace():
    for job, s in all_gt_steps():
        if str(s.get("uses", "")).startswith("actions/checkout@"):
            assert s["with"]["persist-credentials"] is False, job
    assert gt_step("generate", "Prepare bot branch")["env"]["GIT_TOKEN"] == "${{ secrets.qc_bot_token || github.token }}"
    assert "x-access-token" in gt_step("generate", "Prepare bot branch")["run"] and "https://x-access-token" not in GT_TEXT   # header qua GIT_CONFIG_*, không nhúng vào URL


def test_gt_pushes_only_to_the_validated_bot_branch_and_never_forces():
    prepare, push = gt_step("generate", "Prepare bot branch"), gt_step("generate", "Commit and push to the bot branch")
    assert "^qc-agent/gt/[a-z0-9][a-z0-9-]{0,63}$" in prepare["run"]
    pushes = [line.strip() for line in push["run"].splitlines() if "git push" in line]
    assert len(pushes) == 1 and 'git push origin "HEAD:refs/heads/$BRANCH"' in pushes[0]
    for _, s in all_gt_steps():
        for raw in s.get("run", "").splitlines():
            line = raw.split(" #", 1)[0]                                   # bỏ comment cuối dòng
            if "git push" in line or "git commit" in line:
                assert not re.search(r"--force|-f\b|--mirror|--delete|\+refs|--no-verify|--amend", line), line
    assert "git add -A -- .qc-agent" in push["run"] and not re.search(r"git add (\.|-A\s*$|--all)", push["run"], re.M) and "commit -a" not in push["run"]


def test_gt_egress_and_summary_live_outside_the_workspace_and_only_become_an_artifact():
    generate = gt_step("generate", "Generate Ground-Truth")["run"]
    assert '"$RUNNER_TEMP/gt-egress"' in generate and "--egress-dir /egress" in generate and "--summary-json /egress/summary.json" in generate
    upload = gt_step("generate", "Upload egress log and summary")
    assert upload["with"]["path"] == "${{ runner.temp }}/gt-egress/" and upload["with"]["retention-days"] == 14
    assert "gt-egress" not in gt_step("generate", "Commit and push to the bot branch")["run"]


def test_gt_containers_never_run_as_root_and_secrets_never_reach_the_command_line():
    for job, s in all_gt_steps():
        for command in re.findall(r"docker run[^\n]*(?:\\\n[^\n]*)*", s.get("run", "")):
            assert '--user "$(id -u):$(id -g)"' in command, (job, s["name"])
    generate = gt_step("generate", "Generate Ground-Truth")
    assert "-e ANTHROPIC_API_KEY " in generate["run"] and "ANTHROPIC_API_KEY=" not in generate["run"]                 # tên biến, không phải giá trị
    assert "gh pr create" in gt_step("generate", "Open or update the pull request")["run"]


def test_gt_validate_is_read_only_offline_and_has_no_llm_secret():
    validate = json.dumps(GT_JOBS["validate"])
    assert "ANTHROPIC" not in validate and "qc_bot_token" not in validate and "contents: write" not in validate
    run = gt_step("validate", "Run gt validate")["run"]
    assert "--network none" in run and '-v "$PWD:/work:ro"' in run and "gt validate --sut-root /work" in run
    assert "if [ ! -d .qc-agent/ground-truth ]" in run                                                                 # PR đụng suite khác: không có GT thì bỏ qua


def test_gt_prd_path_prd_id_and_openapi_are_validated_before_use():
    select = gt_step("select", "Select PRD files")["run"]
    assert "^[A-Za-z0-9_][A-Za-z0-9._*/-]{0,200}$" in select and '== *..*' in select and 'GITHUB_REF_NAME" != "$BASE_BRANCH"' in select
    resolve = gt_step("generate", "Resolve PRD")["run"]
    assert "^[a-z0-9][a-z0-9-]{0,63}$" in resolve and "gt info --prd" in resolve and "https?://" in resolve
    assert gt_step("generate", "Resolve PRD")["env"]["PRD"] == "${{ matrix.prd }}"


def test_gt_an_existing_pull_request_gets_a_comment_never_an_overwritten_description():
    run = gt_step("generate", "Open or update the pull request")["run"]
    assert "gh pr comment" in run and "gh pr edit" not in run and "pr_body /egress/summary.json" in run
    assert gt_step("generate", "Open or update the pull request")["env"]["GH_TOKEN"] == "${{ secrets.qc_bot_token || github.token }}"


# ---- chạy THẬT các bước shell (bash + git + python3), không cần Docker/GitHub ----

def _bash():
    candidates = []
    git = shutil.which("git")
    if git:
        base = Path(git).resolve().parent.parent
        candidates += [base / "bin" / "bash.exe", base / "usr" / "bin" / "bash.exe"]
    candidates += [Path(p) for p in (shutil.which("bash"),) if p]
    for candidate in candidates:
        if candidate.is_file() and "WindowsApps" not in str(candidate):
            try:
                probe = subprocess.run([str(candidate), "-c", "echo ok; command -v mapfile >/dev/null && echo mapfile"], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if probe.returncode == 0 and "mapfile" in probe.stdout:
                return str(candidate)
    return None


BASH = _bash()
needs_bash = pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="cần bash (>=4) và git")


@pytest.fixture
def shell(tmp_path):
    """Chạy `run:` của một step trong repo tạm. python3 (có trên runner GitHub) được giả bằng interpreter đang chạy test."""
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python3").write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
    (shim / "python3").chmod(0o755)

    def run(job, name, cwd, env):
        script = tmp_path / f"step-{abs(hash((job, name)))}.sh"
        script.write_text("set -eo pipefail\n" + gt_step(job, name)["run"], encoding="utf-8", newline="\n")
        output = tmp_path / f"output-{abs(hash((job, name)))}.txt"
        output.write_text("", encoding="utf-8")
        full = {**os.environ, "GITHUB_OUTPUT": output.as_posix(), "RUNNER_TEMP": (tmp_path / "rt").as_posix(), "GITHUB_SERVER_URL": "https://github.com",
                "PATH": str(shim) + os.pathsep + os.environ["PATH"], "GIT_TERMINAL_PROMPT": "0", **env}
        done = subprocess.run([BASH, script.as_posix()], cwd=cwd, env=full, capture_output=True, text=True, encoding="utf-8", timeout=120)
        outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
        return done, outputs
    return run


def git(cwd, *args):
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit_files(repo, files, message="c"):
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path, name="work"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


SELECT_ENV = {"PRD_PATH": "docs/prd/**", "BASE_BRANCH": "main", "EVENT_NAME": "push", "GITHUB_REF_NAME": "main"}


@needs_bash
def test_gt_select_picks_only_the_prds_a_push_added_or_modified(shell, tmp_path):
    repo = make_repo(tmp_path)
    before = commit_files(repo, {"docs/prd/same.md": "s", "docs/prd/notes.md": "1", "README.md": "r"})
    after = commit_files(repo, {"docs/prd/notes.md": "2", "docs/prd/new.md": "n", "docs/prd/chart.png": "x", "README.md": "r2", "docs/prd/my file.md": "sp"})
    done, out = shell("select", "Select PRD files", repo, {**SELECT_ENV, "BEFORE": before, "SHA": after})
    assert done.returncode == 0, done.stderr
    assert out["count"] == "2" and json.loads(out["matrix"]) == {"prd": ["docs/prd/new.md", "docs/prd/notes.md"]}
    assert "bỏ qua tệp không phải PRD hợp lệ" in done.stdout                                  # chart.png và tên có dấu cách bị loại kèm cảnh báo


@needs_bash
@pytest.mark.parametrize("event, before", [("workflow_dispatch", "0" * 40), ("push", "0" * 40), ("push", "f" * 40)])
def test_gt_select_takes_every_matching_prd_on_dispatch_first_push_or_unknown_before(shell, tmp_path, event, before):
    repo = make_repo(tmp_path)
    sha = commit_files(repo, {"docs/prd/a.md": "a", "docs/prd/sub/b.txt": "b", "docs/other.md": "o"})
    done, out = shell("select", "Select PRD files", repo, {**SELECT_ENV, "EVENT_NAME": event, "BEFORE": before, "SHA": sha})
    assert done.returncode == 0, done.stderr
    assert json.loads(out["matrix"]) == {"prd": ["docs/prd/a.md", "docs/prd/sub/b.txt"]}


@needs_bash
def test_gt_select_with_nothing_to_do_skips_generate(shell, tmp_path):
    repo = make_repo(tmp_path)
    before = commit_files(repo, {"docs/prd/a.md": "a"})
    after = commit_files(repo, {"README.md": "r"})
    done, out = shell("select", "Select PRD files", repo, {**SELECT_ENV, "BEFORE": before, "SHA": after})
    assert done.returncode == 0 and out["count"] == "0" and json.loads(out["matrix"]) == {"prd": []}


@needs_bash
@pytest.mark.parametrize("over, message", [
    ({"PRD_PATH": "../etc/passwd"}, "prd_path"), ({"PRD_PATH": "docs/../x.md"}, "prd_path"), ({"PRD_PATH": "/abs/x.md"}, "prd_path"),
    ({"PRD_PATH": "docs/prd/a b.md"}, "prd_path"), ({"PRD_PATH": "-rf"}, "prd_path"), ({"PRD_PATH": "x;rm -rf /"}, "prd_path"),
    ({"PRD_PATH": "docs/$(id).md"}, "prd_path"), ({"PRD_PATH": ""}, "prd_path"),
    ({"GITHUB_REF_NAME": "feature/x"}, "nhánh gốc"), ({"BASE_BRANCH": "ma in", "GITHUB_REF_NAME": "ma in"}, "nhánh gốc")])
def test_gt_select_refuses_hostile_paths_and_the_wrong_branch(shell, tmp_path, over, message):
    repo = make_repo(tmp_path)
    sha = commit_files(repo, {"docs/prd/a.md": "a"})
    done, out = shell("select", "Select PRD files", repo, {**SELECT_ENV, "EVENT_NAME": "workflow_dispatch", "BEFORE": "0" * 40, "SHA": sha, **over})
    assert done.returncode == 1 and message in done.stdout + done.stderr and "matrix" not in out


@needs_bash
def test_gt_select_caps_the_number_of_prds_per_run(shell, tmp_path):
    repo = make_repo(tmp_path)
    sha = commit_files(repo, {f"docs/prd/p{n}.md": "x" for n in range(6)})
    done, _ = shell("select", "Select PRD files", repo, {**SELECT_ENV, "EVENT_NAME": "workflow_dispatch", "BEFORE": "0" * 40, "SHA": sha})
    assert done.returncode == 1 and "quá 5 PRD" in done.stdout + done.stderr


def bot_env(branch="qc-agent/gt/notes", **extra):
    return {"BRANCH": branch, "BASE_BRANCH": "main", "PRD": "docs/prd/notes.md", "GIT_TOKEN": "ghs_SECRETTOKEN", **extra}


def clone_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    repo = make_repo(tmp_path)
    git(repo, "remote", "add", "origin", origin.as_posix())
    commit_files(repo, {"docs/prd/notes.md": "PRD v1\n", "README.md": "r\n"})
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    return repo, origin


@needs_bash
def test_gt_a_new_bot_branch_is_created_and_only_the_ground_truth_is_committed_and_pushed(shell, tmp_path):
    repo, origin = clone_with_origin(tmp_path)
    done, out = shell("generate", "Prepare bot branch", repo, bot_env())
    assert done.returncode == 0, done.stderr
    assert out["existing"] == "false" and git(repo, "branch", "--show-current") == "qc-agent/gt/notes" and "ghs_SECRETTOKEN" not in done.stdout + done.stderr
    (repo / ".qc-agent" / "ground-truth").mkdir(parents=True)
    (repo / ".qc-agent" / "ground-truth" / "test-cases.yaml").write_text("version: 1\n", encoding="utf-8")
    (repo / "runs").mkdir()
    (repo / "runs" / "egress.jsonl").write_text("{}\n", encoding="utf-8")            # KHÔNG được vào commit
    (repo / "stray.txt").write_text("x\n", encoding="utf-8")
    done, out = shell("generate", "Commit and push to the bot branch", repo, {"BRANCH": "qc-agent/gt/notes", "PRD_ID": "notes", "GIT_TOKEN": "ghs_SECRETTOKEN"})
    assert done.returncode == 0, done.stderr
    assert out["changed"] == "true" and "ghs_SECRETTOKEN" not in done.stdout + done.stderr
    files = git(origin, "ls-tree", "-r", "--name-only", "qc-agent/gt/notes").splitlines()
    assert ".qc-agent/ground-truth/test-cases.yaml" in files and "runs/egress.jsonl" not in files and "stray.txt" not in files
    assert git(origin, "log", "-1", "--format=%s", "qc-agent/gt/notes") == "chore(gt): cập nhật Ground-Truth cho notes (qc-agent bot)"
    assert "ghs_SECRETTOKEN" not in (repo / ".git" / "config").read_text(encoding="utf-8")   # token không nằm lại trong .git/config
    done, out = shell("generate", "Commit and push to the bot branch", repo, {"BRANCH": "qc-agent/gt/notes", "PRD_ID": "notes", "GIT_TOKEN": "t"})
    assert done.returncode == 0 and out["changed"] == "false" and git(origin, "rev-list", "--count", "qc-agent/gt/notes") == "2"   # không đổi thì không commit


@needs_bash
def test_gt_an_existing_bot_branch_is_built_upon_never_rewritten(shell, tmp_path):
    repo, origin = clone_with_origin(tmp_path)
    git(repo, "checkout", "-q", "-b", "qc-agent/gt/notes")
    commit_files(repo, {".qc-agent/ground-truth/test-cases.yaml": "status: draft\n"}, "bot")
    git(repo, "push", "-q", "origin", "qc-agent/gt/notes")
    git(repo, "checkout", "-q", "main")
    qa = tmp_path / "qa"
    subprocess.run(["git", "clone", "-q", "-b", "qc-agent/gt/notes", origin.as_posix(), str(qa)], check=True, capture_output=True)
    qa_sha = commit_files(qa, {".qc-agent/ground-truth/test-cases.yaml": "status: approved   # QA đã duyệt\n"}, "qa")
    git(qa, "push", "-q", "origin", "qc-agent/gt/notes")
    commit_files(repo, {"docs/prd/notes.md": "PRD v2\n"}, "ba đổi PRD")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    done, out = shell("generate", "Prepare bot branch", repo, bot_env())
    assert done.returncode == 0, done.stderr
    assert out["existing"] == "true" and git(repo, "branch", "--show-current") == "qc-agent/gt/notes"
    assert (repo / ".qc-agent/ground-truth/test-cases.yaml").read_text(encoding="utf-8").startswith("status: approved")   # công QA còn nguyên
    assert (repo / "docs/prd/notes.md").read_text(encoding="utf-8") == "PRD v2\n"                                          # PRD mới nhất lấy từ nhánh gốc
    (repo / ".qc-agent/ground-truth/test-cases.yaml").write_text("status: approved   # QA đã duyệt\n# regen\n", encoding="utf-8")
    done, out = shell("generate", "Commit and push to the bot branch", repo, {"BRANCH": "qc-agent/gt/notes", "PRD_ID": "notes", "GIT_TOKEN": "t"})
    assert done.returncode == 0, done.stderr
    assert out["changed"] == "true" and subprocess.run(["git", "merge-base", "--is-ancestor", qa_sha, "qc-agent/gt/notes"], cwd=origin).returncode == 0   # commit của QA là tổ tiên


@needs_bash
def test_gt_a_push_that_would_overwrite_someone_elses_work_fails_instead_of_forcing(shell, tmp_path):
    repo, origin = clone_with_origin(tmp_path)
    shell("generate", "Prepare bot branch", repo, bot_env())
    (repo / ".qc-agent").mkdir()
    (repo / ".qc-agent" / "a.txt").write_text("bot\n", encoding="utf-8")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", origin.as_posix(), str(other)], check=True, capture_output=True)
    git(other, "checkout", "-q", "-b", "qc-agent/gt/notes")
    theirs = commit_files(other, {".qc-agent/theirs.txt": "qa\n"}, "qa")
    git(other, "push", "-q", "origin", "qc-agent/gt/notes")                                # QA đẩy lên nhánh bot trong lúc workflow chạy
    done, _ = shell("generate", "Commit and push to the bot branch", repo, {"BRANCH": "qc-agent/gt/notes", "PRD_ID": "notes", "GIT_TOKEN": "t"})
    assert done.returncode != 0 and git(origin, "rev-parse", "qc-agent/gt/notes") == theirs      # bị từ chối, nhánh của QA nguyên vẹn


@needs_bash
@pytest.mark.parametrize("branch", ["main", "qc-agent/gt/../main", "qc-agent/gt/Notes", "qc-agent/gt/", "feature/x", "qc-agent/gt/a b", "qc-agent/gt/-x", "refs/heads/main"])
def test_gt_only_branches_in_the_bot_namespace_are_ever_touched(shell, tmp_path, branch):
    repo, origin = clone_with_origin(tmp_path)
    done, out = shell("generate", "Prepare bot branch", repo, bot_env(branch))
    assert done.returncode == 1 and "tên nhánh bot không hợp lệ" in done.stdout + done.stderr
    assert git(repo, "branch", "--show-current") == "main" and out == {}
