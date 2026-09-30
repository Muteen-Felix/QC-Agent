"""Job `validate` của qc-groundtruth.reusable.yml chạy trên Docker cục bộ bằng harness (tools/run_reusable_locally.py --job validate) với image qc-agent THẬT.

CHỈ chạy khi có docker + QC_TEST_DOCKER_IMAGE (image qc-agent đã build từ mã có `gt`, vd. `docker build -t qc-agent:gt .`). Không cần PostgreSQL hay mạng.
Các job `select`/`generate` không chạy ở đây: chúng cần GitHub (checkout, gh, push); phần shell của chúng được kiểm bằng git thật trong tests/test_workflow_static.py.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes import FakeAnthropic
from tests.test_gt_cli import GT, KEY, OPENAPI, PRD_FILE, RESPONSE, approve_everything
from qc_agent.core.cli import main as cli_main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_reusable_locally as harness  # noqa: E402

IMAGE = os.environ.get("QC_TEST_DOCKER_IMAGE", "")
WORKFLOW = ROOT / ".github" / "workflows" / "qc-groundtruth.reusable.yml"


def _image_has_gt() -> bool:
    if not IMAGE or shutil.which("docker") is None:
        return False
    done = subprocess.run(["docker", "run", "--rm", IMAGE, "gt", "--help"], capture_output=True, text=True, timeout=120)
    return done.returncode == 0


pytestmark = pytest.mark.skipif(not _image_has_gt(), reason="needs docker + QC_TEST_DOCKER_IMAGE có lệnh `gt`")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    sut = tmp_path / "sut"
    (sut / "docs" / "prd").mkdir(parents=True)
    shutil.copy(PRD_FILE, sut / "docs" / "prd" / "noteboard-prd.md")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.chdir(tmp_path)
    with FakeAnthropic(RESPONSE, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        code = cli_main(["gt", "generate", "--prd", str(sut / "docs/prd/noteboard-prd.md"), "--sut-root", str(sut), "--openapi", str(OPENAPI),
                         "--egress-dir", str(tmp_path / "eg")])
    assert code == 0
    return sut


def validate_job(workspace) -> dict:
    github = {"token": "t", "sha": "x", "actor": "tester", "run_attempt": "1", "event_name": "pull_request", "event": {}, "env": {}}
    inputs = {"project": "noteboard", "image": IMAGE, "prd_path": "docs/prd/**", "allow_unpinned_image": "true"}
    return harness.run_workflow(WORKFLOW, workspace, inputs, {}, github, job="validate", echo=lambda *_: None)


def test_validate_job_fails_while_drafts_remain_and_passes_once_qa_has_reviewed(workspace):
    assert validate_job(workspace)["Run gt validate"]["returncode"] == 1
    approve_everything(workspace)
    assert validate_job(workspace)["Run gt validate"]["returncode"] == 0


def test_validate_job_catches_a_hand_edited_generated_file(workspace):
    approve_everything(workspace)
    target = workspace / GT / "tests_gt" / "test_us_1.py"
    target.write_text(target.read_text(encoding="utf-8") + "# sửa tay\n", encoding="utf-8")
    assert validate_job(workspace)["Run gt validate"]["returncode"] == 1


def test_validate_job_skips_a_pr_that_does_not_touch_ground_truth(tmp_path):
    (tmp_path / ".qc-agent" / "suites").mkdir(parents=True)
    assert validate_job(tmp_path)["Run gt validate"]["returncode"] == 0
