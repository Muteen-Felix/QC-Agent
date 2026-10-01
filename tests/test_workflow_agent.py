"""qc-groundtruth.reusable.yml: đầu vào `agent` (sinh bằng agent đọc mã nguồn). Chạy THẬT các bước shell (bash + git), `docker` được giả; xem test_workflow_static.py.

Điều cần chứng minh: mã nguồn agent đọc là bản `git archive` của nhánh gốc (chỉ file đã commit, không .git), mount `:ro` ở /src; agent luôn dùng khoá Claude (không bao giờ Gemini);
mọi đầu vào được kiểm trước docker; không có gì rời máy khi thiếu khoá hoặc đầu vào xấu; hành vi cũ không đổi khi `agent` tắt.
"""
import json
import os
import re
from pathlib import Path

import pytest

from tests.test_workflow_static import (GEN_ENV, GT, GT_TEXT, SECRET_A, SECRET_G, clone_with_origin, commit_files, generate, git, gt_step, gt_steps, needs_bash, shell)  # noqa: F401

EXPORT = "Export the base-branch source for the agent (read-only)"
BASE = {"AGENT": "true", "ANTHROPIC_API_KEY": SECRET_A}


@pytest.fixture
def src(tmp_path):
    directory = tmp_path / "gt-src"
    (directory / "app").mkdir(parents=True)
    (directory / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    return directory


def test_agent_inputs_are_declared_with_safe_defaults():
    inputs = GT[True]["workflow_call"]["inputs"]
    assert inputs["agent"]["type"] == "boolean" and inputs["agent"]["default"] is False and not inputs["agent"].get("required")
    for name in ("agent_model", "agent_max_turns", "agent_max_cost_usd"):
        assert inputs[name]["type"] == "string" and inputs[name]["default"] == "" and not inputs[name].get("required"), name
    assert "Anthropic" in inputs["agent"]["description"]                                   # nói rõ mã nguồn được gửi đi
    assert "agent:" in GT_TEXT.split("name: qc-groundtruth (reusable)")[0] or "agent: true" in GT_TEXT.split("name: qc-groundtruth (reusable)")[0]


def test_the_export_step_only_runs_for_the_agent_and_before_generate():
    names = [s.get("name") or s.get("uses") for s in gt_steps("generate")]
    export = gt_step("generate", EXPORT)
    assert export["if"] == "inputs.agent" and export["id"] == "source" and names.index(EXPORT) < names.index("Generate Ground-Truth")
    assert names.index("Prepare bot branch") < names.index(EXPORT)                          # origin/<base> đã chắc chắn có
    assert export["env"] == {"BASE_BRANCH": "${{ inputs.base_branch }}"} and "${{" not in export["run"]
    assert "ANTHROPIC" not in json.dumps(export) and "GEMINI" not in json.dumps(export) and "docker" not in export["run"]


def test_the_agent_mounts_only_the_exported_copy_read_only_and_the_default_model_matches_the_image(monkeypatch):
    run = gt_step("generate", "Generate Ground-Truth")["run"]
    assert '-v "$AGENT_SRC:/src:ro"' in run and "--source-root /src" in run and "--agent" in run
    assert 'agent_mounts=(-v "$AGENT_SRC:/src:ro")' in run and "-v \"$PWD:/work\"" in run
    from qc_agent import settings
    monkeypatch.delenv("QC_GT_AGENT_MODEL", raising=False)
    found = re.search(r'agent_model="\$\{AGENT_MODEL:-([^}]+)\}"', run)
    assert found and found.group(1) == settings.Settings().gt_agent_model                  # workflow và image không được lệch nhau
    env = gt_step("generate", "Generate Ground-Truth")["env"]
    assert env["AGENT_SRC"] == "${{ steps.source.outputs.dir }}" and env["AGENT"] == "${{ inputs.agent }}"
    assert [s for s in re.findall(r"docker run[^\n]*(?:\\\n[^\n]*)*", run) if "$IMAGE" in s and '--user "$(id -u):$(id -g)"' not in s] == []


@needs_bash
def test_the_export_is_a_git_archive_of_the_base_branch_tip_without_git_or_untracked_files(shell, tmp_path):
    repo, _ = clone_with_origin(tmp_path)
    commit_files(repo, {"app/main.py": "tracked at main\n", "app/models.py": "class A: ...\n"}, "code")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-b", "qc-agent/gt/notes")                                  # nhánh bot: có thể mang mã khác/cũ
    (repo / "app" / "main.py").write_text("EDITED ON THE BOT BRANCH\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=untracked\n", encoding="utf-8")                      # file chưa commit: không được lọt vào bản xuất
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("artifact\n", encoding="utf-8")
    done, out = shell("generate", EXPORT, repo, {"BASE_BRANCH": "main"})
    assert done.returncode == 0, done.stderr + done.stdout
    exported = Path(out["dir"])
    assert exported == tmp_path / "rt" / "gt-src" and not exported.is_relative_to(repo)     # nằm ngoài workspace: không lọt vào commit của bot
    assert (exported / "app" / "main.py").read_text(encoding="utf-8") == "tracked at main\n"   # bản của nhánh gốc, không phải bản trên nhánh bot
    assert (exported / "app" / "models.py").is_file() and (exported / "README.md").is_file()
    assert not (exported / ".git").exists() and not (exported / ".env").exists() and not (exported / "build").exists()
    done, out = shell("generate", EXPORT, repo, {"BASE_BRANCH": "main"})                    # chạy lại: thay mới, không cộng dồn
    assert done.returncode == 0 and (Path(out["dir"]) / "app" / "main.py").read_text(encoding="utf-8") == "tracked at main\n"


@needs_bash
@pytest.mark.parametrize("branch", ["ma in", "main;id", "$(id)", "", "x" * 101])
def test_the_export_rejects_a_bad_base_branch(shell, tmp_path, branch):
    repo, _ = clone_with_origin(tmp_path)
    done, out = shell("generate", EXPORT, repo, {"BASE_BRANCH": branch})
    assert done.returncode == 1 and "base_branch không hợp lệ" in done.stdout + done.stderr and out == {} and not (tmp_path / "rt" / "gt-src").exists()


@needs_bash
def test_the_export_stops_when_the_base_branch_is_unknown(shell, tmp_path):
    repo, _ = clone_with_origin(tmp_path)
    done, out = shell("generate", EXPORT, repo, {"BASE_BRANCH": "no-such-branch"})
    assert done.returncode == 1 and "không thấy origin/no-such-branch" in done.stdout + done.stderr and out == {}


@needs_bash
def test_agent_mode_passes_the_agent_flags_the_read_only_mount_and_only_the_claude_key(generate, src):
    done, args = generate(**BASE, GEMINI_API_KEY=SECRET_G, AGENT_SRC=str(src))
    assert done.returncode == 0, done.stderr + done.stdout
    assert "--agent" in args and args[args.index("--source-root") + 1] == "/src" and f"{src}:/src:ro" in args
    assert "ANTHROPIC_API_KEY" in args and "GEMINI_API_KEY" not in args and SECRET_A not in "".join(args) and SECRET_G not in "".join(args)
    assert not any(a.startswith("QC_GT_AGENT_") for a in args)                              # rỗng = mặc định của image
    assert args.index("--user") < args.index(GEN_ENV["IMAGE"]) < args.index("--agent")      # cờ của agent đi SAU image (lệnh gt), mount đi TRƯỚC image
    assert args.index(f"{src}:/src:ro") < args.index(GEN_ENV["IMAGE"])


@needs_bash
def test_agent_overrides_are_forwarded_as_env_only_when_set(generate, src):
    done, args = generate(**BASE, AGENT_SRC=str(src), AGENT_MODEL="claude-sonnet-5-5", AGENT_MAX_TURNS="25", AGENT_MAX_COST_USD="7.5")
    assert done.returncode == 0, done.stderr
    assert {"QC_GT_AGENT_MODEL=claude-sonnet-5-5", "QC_GT_AGENT_MAX_TURNS=25", "QC_GT_AGENT_MAX_COST_USD=7.5"} <= set(args)
    done, args = generate(**BASE, AGENT_SRC=str(src), AGENT_MAX_TURNS="25")
    assert "QC_GT_AGENT_MAX_TURNS=25" in args and not any(a.startswith(("QC_GT_AGENT_MODEL", "QC_GT_AGENT_MAX_COST")) for a in args)


@needs_bash
def test_the_agent_always_uses_the_claude_key_even_if_the_single_shot_model_is_gemini(generate, src):
    done, args = generate(**BASE, GEMINI_API_KEY=SECRET_G, AGENT_SRC=str(src), MODEL="gemini-3.6-flash")
    assert done.returncode == 0, done.stderr
    assert "ANTHROPIC_API_KEY" in args and "GEMINI_API_KEY" not in args


@needs_bash
@pytest.mark.parametrize("env, message", [
    ({"AGENT_MODEL": "gemini-3.6-flash"}, "agent chỉ hỗ trợ model Claude"), ({"AGENT_MODEL": "gemini-x", "GEMINI_API_KEY": SECRET_G}, "agent chỉ hỗ trợ model Claude"),
    ({"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": SECRET_G}, "thiếu secret ANTHROPIC_API_KEY cho model claude-sonnet-5-5"),
    ({"ANTHROPIC_API_KEY": "", "AGENT_MODEL": "claude-sonnet-5-5"}, "thiếu secret ANTHROPIC_API_KEY cho model claude-sonnet-5-5"),
])
def test_agent_stops_before_anything_leaves_the_machine(generate, src, env, message):
    done, args = generate(**{**BASE, "AGENT_SRC": str(src), **env})
    assert done.returncode == 1 and args is None and message in done.stdout + done.stderr
    assert SECRET_A not in done.stdout + done.stderr and SECRET_G not in done.stdout + done.stderr


@needs_bash
@pytest.mark.parametrize("env", [
    {"AGENT_MODEL": "gem ini"}, {"AGENT_MODEL": "claude-x;id"}, {"AGENT_MODEL": "$(id)"}, {"AGENT_MODEL": "-rf"},
    {"AGENT_MAX_TURNS": "0"}, {"AGENT_MAX_TURNS": "abc"}, {"AGENT_MAX_TURNS": "1000"}, {"AGENT_MAX_TURNS": "-3"}, {"AGENT_MAX_TURNS": "5; rm -rf /"}, {"AGENT_MAX_TURNS": "$(id)"},
    {"AGENT_MAX_COST_USD": "abc"}, {"AGENT_MAX_COST_USD": "1e3"}, {"AGENT_MAX_COST_USD": "-1"}, {"AGENT_MAX_COST_USD": "1.234"}, {"AGENT_MAX_COST_USD": "5;id"}, {"AGENT_MAX_COST_USD": "12345"},
])
def test_invalid_agent_inputs_are_rejected_before_docker(generate, src, env):
    done, args = generate(**{**BASE, "AGENT_SRC": str(src), **env})
    assert done.returncode == 1 and args is None and "không hợp lệ" in done.stdout + done.stderr


@needs_bash
@pytest.mark.parametrize("path", ["", "/nonexistent/gt-src"])
def test_agent_needs_the_exported_source(generate, path):
    done, args = generate(**BASE, AGENT_SRC=path)
    assert done.returncode == 1 and args is None and "thiếu mã nguồn nhánh gốc" in done.stdout + done.stderr


@needs_bash
def test_with_the_agent_off_nothing_about_it_reaches_docker(generate, src):
    done, args = generate(ANTHROPIC_API_KEY=SECRET_A, AGENT="false", AGENT_SRC=str(src), AGENT_MODEL="claude-opus-5-5", AGENT_MAX_TURNS="9", AGENT_MAX_COST_USD="3")
    assert done.returncode == 0, done.stderr
    assert "--agent" not in args and "--source-root" not in args and not any(a.endswith(":/src:ro") or a.startswith("QC_GT_AGENT_") for a in args)
    assert "generate" in args and "ANTHROPIC_API_KEY" in args


@needs_bash
def test_the_agent_regenerates_with_regen_and_keeps_the_same_command_shape(generate, src, tmp_path):
    repo = tmp_path / "sut"
    (repo / ".qc-agent" / "ground-truth").mkdir(parents=True)
    (repo / ".qc-agent" / "ground-truth" / "test-cases.yaml").write_text("version: 1\n", encoding="utf-8")
    done, args = generate(**BASE, AGENT_SRC=str(src))
    assert done.returncode == 0 and "regen" in args and "generate" not in args and "--agent" in args
    assert args.index("--prd") < args.index("--sut-root") < args.index("--agent") < args.index("--egress-dir")


def test_the_generated_caller_workflow_only_offers_the_agent_as_comments():
    """`init` sinh workflow gọi: agent PHẢI tắt mặc định (mã nguồn rời máy là quyết định có ý thức), chỉ gợi ý dưới dạng comment."""
    import yaml
    from qc_agent.scaffold import templates as t
    text = t.groundtruth_workflow(project="shop")
    caller = yaml.safe_load(text.replace("{{", "{{"))["jobs"]["groundtruth"]["with"]
    assert not {"agent", "agent_model", "agent_max_turns", "agent_max_cost_usd"} & set(caller)
    for line in ("# agent: true", "# timeout_minutes: 60", "# agent_model: claude-sonnet-5-5", '# agent_max_cost_usd: "3"'):
        assert line in text, line
    assert "MÃ NGUỒN ĐƯỢC GỬI TỚI ANTHROPIC" in text
