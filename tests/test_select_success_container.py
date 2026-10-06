"""Đường Select (PR) THÀNH CÔNG trong container (S4-05b): SUT là repo git thật, diff chỉ có docs => selector đi đường `rules` (không LLM), gate chỉ chạy floor.
Các test container khác dùng SHA giả nên Select luôn lùi về FULL SET; test này cấp `base.sha`/`head.sha` thật. Không cần PostgreSQL: bỏ bước Report.
CHỈ chạy khi có docker + QC_TEST_DOCKER_IMAGE (tên image qc-agent đã build, vd. qc-agent:dev)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.fakes import FakeAnthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_reusable_locally as harness  # noqa: E402

IMAGE = os.environ.get("QC_TEST_DOCKER_IMAGE", "")
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
POLICY = ROOT / "configs" / "projects"
pytestmark = pytest.mark.skipif(not IMAGE or shutil.which("docker") is None, reason="needs docker + QC_TEST_DOCKER_IMAGE")
SKIP = ("Pull qc-agent image", "PR review", "Jira (Low)", "Report (Check Run, PR comment, history, webhook)", "Upload run artifacts")


def _rmtree(path: Path) -> None:
    """Windows: object trong .git là read-only nên rmtree thường bỏ sót; gỡ cờ rồi xoá lại."""
    shutil.rmtree(path, onerror=lambda func, target, _exc: (os.chmod(target, stat.S_IWRITE), func(target)))


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", *args], cwd=repo, capture_output=True, text=True,
                          timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def git_sut():
    """Bản sao noteboard là repo git có 2 commit (base, rồi head chỉ sửa docs). Đặt trong repo (Docker Desktop không mount được thư mục tạm trên C:), xoá sau test."""
    base_dir = ROOT / "tests" / ".docker-workspaces" / uuid.uuid4().hex
    sut = base_dir / "noteboard"
    shutil.copytree(SUT, sut, ignore=shutil.ignore_patterns("runs", "__pycache__", ".git"))
    _git(sut, "init", "-q")
    _git(sut, "add", "-A")
    _git(sut, "commit", "-qm", "base")
    base = _git(sut, "rev-parse", "HEAD")
    (sut / "docs").mkdir(exist_ok=True)
    (sut / "docs" / "note.md").write_text("# ghi chú\n", encoding="utf-8")   # khớp docs_paths của policy
    _git(sut, "add", "-A")
    _git(sut, "commit", "-qm", "docs")
    yield sut, base, _git(sut, "rev-parse", "HEAD")
    _rmtree(base_dir)


def test_docs_only_pr_selects_floor_via_rules_inside_the_container(git_sut):
    sut, base, head = git_sut
    event = sut.parent / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": base}, "head": {"sha": head, "ref": "feat/x"}}}), encoding="utf-8")
    github = {"token": "ghs_local_test_token", "sha": "mergecommit0000", "actor": "tester", "run_attempt": "1", "event_name": "pull_request",
              "event": {"pull_request": {"number": 7, "base": {"sha": base}, "head": {"sha": head}}},
              "env": {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "mergecommit0000", "GITHUB_RUN_ID": "5001",
                      "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_API_URL": "http://127.0.0.1:9", "GITHUB_REF_NAME": "feat/x"}}
    logs = []
    with FakeAnthropic() as llm:
        # ANTHROPIC_API_KEY không được cấp (secrets rỗng); workflow cũng không chuyển ANTHROPIC_BASE_URL vào container, nên FakeAnthropic chỉ là lớp canh phụ
        step_env = {"Select (PR)": {"ANTHROPIC_BASE_URL": llm.url, "ANTHROPIC_API_KEY": None}}
        try:
            results = harness.run_workflow(harness.DEFAULT_WORKFLOW, sut, {"project": "noteboard", "image": IMAGE, "allow_unpinned_image": "true",
                                                                           "sut_env": "QC_BUGS=none", "refine": "off"}, {}, github, skip=SKIP,
                                           echo=logs.append, policy_dir=POLICY, step_env=step_env)
            log = "\n".join(logs)
            assert results["Select (PR)"]["outputs"] == {"ok": "true"}, log
            assert "Select failed" not in log, log
            assert results["Run qc-agent gate"]["outputs"].get("exit_code") is not None, log
            run_dirs = sorted((sut / "runs").glob("r-*"))
            assert len(run_dirs) == 1, log
            selection = json.loads((run_dirs[0] / "selection.json").read_text(encoding="utf-8"))
            assert selection["source"] == "rules" and selection["full_set"] is False, selection
            assert selection["suites"] == ["sast", "secrets"], selection   # chỉ floor
            report = (run_dirs[0] / "report.md").read_text(encoding="utf-8")
            assert "## Phạm vi chạy" in report and "source: rules" in report, report
        finally:
            subprocess.run(["docker", "rm", "-f", "sut", "ui"], capture_output=True)
            subprocess.run(["docker", "network", "rm", "qc-net"], capture_output=True)
        assert llm.count == 0
