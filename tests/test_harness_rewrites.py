"""Harness local (tools/run_reusable_locally.py): `step_rewrites` là điểm lệch DUY NHẤT so với workflow thật (S4-05) nên phải khớp đúng một lần và có test tĩnh canh workflow.
Không cần Docker: các bước giả chỉ là `echo` chạy bằng bash."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import image_check  # noqa: E402
import run_reusable_locally as harness  # noqa: E402

WORKFLOW = harness.DEFAULT_WORKFLOW


def _steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["gate"]["steps"]


def _step(name):
    return next(step for step in _steps() if step.get("name") == name)


# ---- canh workflow thật ----

def test_the_real_workflow_still_contains_the_string_the_harness_rewrites_exactly_once():
    old, _ = harness.SELECT_BASE_URL_REWRITE
    assert _step(harness.SELECT_STEP)["run"].count(old) == 1
    assert sum(step.get("run", "").count(old) for step in _steps()) == 1, "chuỗi này không được xuất hiện ở bước khác"


def test_the_workflow_really_does_not_forward_anthropic_base_url_so_the_rewrite_is_still_needed():
    """Nếu workflow tự chuyển ANTHROPIC_BASE_URL thì rewrite thành thừa (và sẽ làm khớp 0 lần): test này báo để gỡ rewrite có chủ đích."""
    assert "ANTHROPIC_BASE_URL" not in _step(harness.SELECT_STEP)["run"]
    assert "ANTHROPIC_BASE_URL" not in json.dumps(_step(harness.SELECT_STEP).get("env", {}))


def test_the_rewrite_adds_only_the_base_url_flag_and_leaves_the_rest_of_the_step_untouched():
    out = harness.apply_rewrites(_steps(), harness.anthropic_rewrites())
    original = _step(harness.SELECT_STEP)["run"]
    new = out[harness.SELECT_STEP]
    assert "-e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e QC_SELECT_CACHE_DIR=/cache" in new
    assert new.replace(" -e ANTHROPIC_BASE_URL", "") == original
    assert "--add-host" not in new      # lệnh docker run nằm trong workflow; địa chỉ máy chủ do URL quyết định


def test_the_harness_does_not_edit_any_other_step():
    assert set(harness.anthropic_rewrites()) == {"Select (PR)"} and len(harness.anthropic_rewrites()["Select (PR)"]) == 1


# ---- khớp đúng một lần, không âm thầm ----

def _fake_steps(text):
    return [{"name": "Select (PR)", "run": text}]


@pytest.mark.parametrize(("text", "count"), [("echo nothing", 0), ("A\nA", 2)])
def test_a_rewrite_that_does_not_match_exactly_once_stops_the_harness(text, count):
    with pytest.raises(SystemExit) as caught:
        harness.apply_rewrites(_fake_steps(text), {"Select (PR)": [("A", "B")] if count == 2 else [("missing", "B")]})
    assert f"khớp {count} lần" in str(caught.value) and "harness" in str(caught.value)


def test_a_rewrite_for_a_step_that_no_longer_exists_stops_the_harness():
    with pytest.raises(SystemExit, match="không có bước 'Select \\(PR\\)'"):
        harness.apply_rewrites([{"name": "Other", "run": "x"}], {"Select (PR)": [("x", "y")]})


def test_steps_without_run_are_ignored_when_looking_up_the_name():
    steps = [{"name": "Select (PR)", "uses": "some/action@v1"}, {"name": "Select (PR)", "run": "echo A"}]
    assert harness.apply_rewrites(steps, {"Select (PR)": [("A", "B")]}) == {"Select (PR)": "echo B"}


# ---- chạy thật bằng bash với workflow giả ----

MINI = """
name: mini
on:
  workflow_call:
    inputs:
      x: {type: string, default: ""}
jobs:
  gate:
    steps:
      - name: First
        run: echo first > "$OUT/first.txt"
      - name: Select (PR)
        run: |
          echo "FLAGS: -e ANTHROPIC_API_KEY -e QC_SELECT_CACHE_DIR=/cache base=$ANTHROPIC_BASE_URL home=$HOME" > "$OUT/select.txt"
"""


def _mini(tmp_path):
    path = tmp_path / "mini.yml"
    path.write_text(MINI, encoding="utf-8")
    (tmp_path / "out").mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    return path, ws, {"OUT": str(tmp_path / "out")}


def _github():
    return {"token": "t", "sha": "x", "actor": "a", "run_attempt": "1", "event_name": "pull_request", "event": {}, "env": {}}


def test_run_workflow_executes_the_rewritten_text_with_the_step_env(tmp_path):
    path, ws, env = _mini(tmp_path)
    results = harness.run_workflow(path, ws, {}, {}, _github(), skip=(), echo=lambda *_: None, step_env={"Select (PR)": {**env, "ANTHROPIC_BASE_URL": "http://h:1", "HOME": str(tmp_path / "homedir")}, "First": env},
                                   step_rewrites=harness.anthropic_rewrites())
    assert results["Select (PR)"]["returncode"] == 0
    line = (tmp_path / "out" / "select.txt").read_text(encoding="utf-8").strip()
    assert line.startswith("FLAGS: -e ANTHROPIC_API_KEY -e ANTHROPIC_BASE_URL -e QC_SELECT_CACHE_DIR=/cache base=http://h:1 home=")
    assert line.endswith("homedir")   # bash chuẩn hoá HOME (C:\\x -> /c/x) nên chỉ so phần đuôi


def test_without_rewrites_the_step_runs_verbatim(tmp_path):
    path, ws, env = _mini(tmp_path)
    harness.run_workflow(path, ws, {}, {}, _github(), skip=(), echo=lambda *_: None, step_env={"Select (PR)": env, "First": env})
    assert "ANTHROPIC_BASE_URL -e" not in (tmp_path / "out" / "select.txt").read_text(encoding="utf-8")


def test_a_bad_rewrite_stops_before_any_step_runs(tmp_path):
    path, ws, env = _mini(tmp_path)
    with pytest.raises(SystemExit):
        harness.run_workflow(path, ws, {}, {}, _github(), skip=(), echo=lambda *_: None, step_env={"First": env},
                             step_rewrites={"Select (PR)": [("chuỗi không có", "x")]})
    assert not (tmp_path / "out" / "first.txt").exists(), "bước đầu không được chạy khi rewrite sai"


# ---- build_github_context ----

def test_pull_request_context_carries_the_real_base_and_head(tmp_path):
    github = harness.build_github_context(tmp_path, api_url="http://api", sha="h" * 40, base_sha="b" * 40, pr=9)
    assert github["event_name"] == "pull_request"
    assert github["event"]["pull_request"] == {"number": 9, "head": {"sha": "h" * 40, "ref": "feat/x"}, "base": {"sha": "b" * 40}} or \
        github["event"]["pull_request"] == {"number": 9, "head": {"sha": "h" * 40}, "base": {"sha": "b" * 40}}
    file_event = json.loads(Path(github["env"]["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    assert file_event["pull_request"]["base"]["sha"] == "b" * 40 and github["env"]["GITHUB_API_URL"] == "http://api"


def test_pull_request_without_base_sha_has_no_base_so_select_falls_back_to_full_set(tmp_path):
    github = harness.build_github_context(tmp_path, api_url="http://api")
    assert "base" not in github["event"]["pull_request"]


def test_workflow_dispatch_context_has_no_pull_request(tmp_path):
    github = harness.build_github_context(tmp_path, api_url="http://api", event_name="workflow_dispatch", dispatch_inputs={"workers": "semgrep"})
    assert github["event_name"] == "workflow_dispatch" and "pull_request" not in github["event"]
    assert json.loads(Path(github["env"]["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))["inputs"] == {"workers": "semgrep"}


def test_unsupported_event_name_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="push"):
        harness.build_github_context(tmp_path, api_url="http://api", event_name="push")


def test_the_gate_step_really_branches_on_workflow_dispatch_with_workers():
    """Kịch bản E dựa vào nhánh này của workflow: nếu nó đổi, test Docker E không còn kiểm đúng thứ."""
    run = _step("Run qc-agent gate")["run"]
    assert 'elif [ "$EVENT_NAME" = "workflow_dispatch" ] && [ -n "$WORKERS" ]' in run and "--trigger manual --workers" in run
    assert _step("Select (PR)")["if"] == "github.event_name == 'pull_request'"


# ---- CLI ----

def _run_main(monkeypatch, argv, verdict=None, error=None):
    seen = {}

    def fake_run(workflow, workspace, inputs, secrets, github, **kwargs):
        seen.update(inputs=inputs, secrets=secrets, github=github, **kwargs)
        return {"Enforce gate result": {"returncode": 0}}

    def fake_verify(image):
        if error:
            raise error
        return verdict

    monkeypatch.setattr(harness, "run_workflow", fake_run)
    monkeypatch.setattr(image_check, "verify", fake_verify)
    return harness.main(argv), seen


VERIFIED = image_check.Verdict("qc-agent:x", True, "commit", "a" * 40, "a" * 40)


def test_cli_with_anthropic_api_wires_env_rewrite_and_a_fake_key(monkeypatch):
    code, seen = _run_main(monkeypatch, ["--workspace", "w", "--input", "project=p", "--input", "image=qc-agent:x", "--anthropic-api", "http://host.docker.internal:7"], VERIFIED)
    assert code == 0
    assert seen["step_env"] == {"Select (PR)": {"ANTHROPIC_BASE_URL": "http://host.docker.internal:7"}}
    assert seen["step_rewrites"] == harness.anthropic_rewrites() and seen["secrets"]["ANTHROPIC_API_KEY"] == harness.FAKE_ANTHROPIC_KEY


def test_cli_keeps_an_explicit_anthropic_key_and_adds_jira_secrets(monkeypatch):
    _, seen = _run_main(monkeypatch, ["--workspace", "w", "--input", "image=qc-agent:x", "--secret", "ANTHROPIC_API_KEY=mine", "--anthropic-api", "http://h:1",
                                      "--jira-api", "http://127.0.0.1:5"], VERIFIED)
    assert seen["secrets"]["ANTHROPIC_API_KEY"] == "mine" and seen["secrets"]["JIRA_BASE_URL"] == "http://127.0.0.1:5"
    assert seen["secrets"]["JIRA_EMAIL"] and seen["secrets"]["JIRA_API_TOKEN"]


def test_cli_without_the_new_flags_changes_nothing(monkeypatch):
    code, seen = _run_main(monkeypatch, ["--workspace", "w", "--input", "image=qc-agent:x"], VERIFIED)
    assert code == 0 and seen["step_env"] == {} and seen["step_rewrites"] is None and "JIRA_BASE_URL" not in seen["secrets"]


def test_cli_refuses_a_stale_image_before_running_any_step(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(harness, "run_workflow", lambda *a, **k: ran.append(1))
    monkeypatch.setattr(image_check, "verify", lambda image: (_ for _ in ()).throw(image_check.StaleImage("image cũ")))
    assert harness.main(["--workspace", "w", "--input", "image=qc-agent:old"]) == 1
    assert not ran and "image cũ" in capsys.readouterr().err


def test_cli_prints_the_unverified_label_when_the_escape_hatch_is_used(monkeypatch, capsys):
    unverified = image_check.Verdict("qc-agent:x", False, "commit", "0" * 40, "a" * 40, ("image build từ 0000000",))
    code, _ = _run_main(monkeypatch, ["--workspace", "w", "--input", "image=qc-agent:x"], unverified)
    assert code == 0 and "IMAGE CHƯA XÁC MINH" in capsys.readouterr().out


def test_cli_event_name_workflow_dispatch_builds_a_dispatch_context(monkeypatch):
    _, seen = _run_main(monkeypatch, ["--workspace", "w", "--input", "image=qc-agent:x", "--input", "workers=semgrep", "--event-name", "workflow_dispatch"], VERIFIED)
    assert seen["github"]["event_name"] == "workflow_dispatch" and seen["inputs"]["workers"] == "semgrep"


def test_cli_requires_a_workspace_unless_running_a_scenario(monkeypatch, capsys):
    with pytest.raises(SystemExit) as caught:
        harness.main(["--input", "project=p"])
    assert caught.value.code == 2 and "--workspace" in capsys.readouterr().err
    ran = []
    import types
    monkeypatch.setitem(sys.modules, "tests.full_chain", types.SimpleNamespace(main=lambda: ran.append(1) or 0))
    monkeypatch.setattr("tests.full_chain", sys.modules["tests.full_chain"], raising=False)
    assert harness.main(["--scenario", "full-chain"]) == 0 and ran == [1]
