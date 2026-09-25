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
    inputs = DATA[True]["workflow_call"]["inputs"]
    assert not [name for name in inputs if "policy" in name or name.endswith("_ref")]      # Q1: SUT không chọn được ref của policy
    assert "qc_read_token" in DATA[True]["workflow_call"]["secrets"] and DATA[True]["workflow_call"]["secrets"]["qc_read_token"]["required"] is False


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
