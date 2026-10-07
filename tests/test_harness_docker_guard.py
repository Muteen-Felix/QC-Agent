"""Guard tên Docker của harness (S4-05): workflow tự tạo container `sut`/`ui`/`db` và mạng `qc-net`, rồi dọn bằng `docker rm -f sut ui db` ở bước "Clean up SUT" (không sửa được).
Nếu việc khác đang giữ các tên đó, harness phải DỪNG và báo rõ, KHÔNG xoá. Không cần Docker: `subprocess.run` của harness bị thay bằng một Docker giả ghi lại mọi lệnh."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import image_check  # noqa: E402
import run_reusable_locally as harness  # noqa: E402

from tests import harness_kit as kit  # noqa: E402

REAL_RUN = subprocess.run
READ_ONLY = (["ps", "-a", "--format", "{{.Names}}"], ["network", "ls", "--format", "{{.Name}}"])


class FakeDocker:
    """Thay `subprocess.run` của harness: trả danh sách container/mạng cho trước, GHI LẠI mọi lệnh, và làm test đỏ nếu có lệnh ghi (rm, network rm/create, run, build)."""

    def __init__(self, containers=(), networks=(), fail=None):
        self.containers, self.networks, self.fail, self.calls = list(containers), list(networks), fail, []

    def __call__(self, cmd, **_kwargs):
        if cmd[0] != "docker":      # bash của các bước workflow giả vẫn chạy thật
            return REAL_RUN(cmd, **_kwargs)
        args = cmd[1:]
        self.calls.append(args)
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", self.fail)
        if args == READ_ONLY[0]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.containers) + "\n", "")
        if args == READ_ONLY[1]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.networks) + "\n", "")
        raise AssertionError(f"harness gọi lệnh Docker không-chỉ-đọc khi tên đang bị giữ: docker {' '.join(args)}")

    def wrote_anything(self) -> bool:
        return any(call not in READ_ONLY for call in self.calls)


@pytest.fixture
def docker(monkeypatch):
    def install(**kwargs):
        fake = FakeDocker(**kwargs)
        monkeypatch.setattr(harness.subprocess, "run", fake)
        return fake
    return install


# ---- phát hiện ----

def test_free_names_pass_and_only_read_docker(docker):
    fake = docker(containers=["other-app", "redis"], networks=["bridge", "host", "none"])
    harness.ensure_docker_names_free()
    assert fake.calls == [READ_ONLY[0], READ_ONLY[1]] and not fake.wrote_anything()


@pytest.mark.parametrize(("containers", "networks", "expected"), [
    (["sut"], [], ["container sut"]),
    (["ui", "db"], [], ["container ui", "container db"]),
    ([], ["qc-net"], ["network qc-net"]),
    (["sut", "x"], ["bridge", "qc-net"], ["container sut", "network qc-net"]),
])
def test_each_reserved_name_in_use_is_reported(docker, containers, networks, expected):
    docker(containers=containers, networks=networks)
    assert harness.busy_docker_names() == expected


def test_a_longer_or_prefixed_name_is_not_a_clash(docker):
    docker(containers=["sut-prod", "my-ui", "db2", "qc-net-old"], networks=["qc-network", "qc-net2"])
    assert harness.busy_docker_names() == []


def test_the_message_names_the_resource_and_says_nothing_was_deleted(docker):
    docker(containers=["sut"], networks=["qc-net"])
    with pytest.raises(harness.DockerNamesBusy) as caught:
        harness.ensure_docker_names_free()
    text = str(caught.value)
    assert "container sut" in text and "network qc-net" in text and "KHÔNG xoá" in text and "docker ps -a" in text


def test_a_docker_that_cannot_be_asked_stops_the_harness_instead_of_guessing(docker):
    docker(fail="Cannot connect to the Docker daemon")
    with pytest.raises(harness.DockerNamesCheckError, match="Cannot connect") as caught:
        harness.ensure_docker_names_free()
    assert not isinstance(caught.value, harness.DockerNamesBusy)


# ---- run_workflow: dừng trước bước đầu tiên, không xoá gì ----

MINI = """
name: mini
on:
  workflow_call:
    inputs:
      x: {type: string, default: ""}
jobs:
  gate:
    steps:
      - name: Start SUT
        run: |
          docker network create qc-net
          echo started > "$OUT/started.txt"
      - name: Clean up SUT
        if: always()
        run: docker rm -f sut ui db; docker network rm qc-net
"""
NO_NAMES = """
name: mini
on:
  workflow_call:
    inputs:
      x: {type: string, default: ""}
jobs:
  validate:
    steps:
      - name: Only echo
        run: echo ok > "$OUT/ran.txt"
"""
GITHUB = {"token": "t", "sha": "x", "actor": "a", "run_attempt": "1", "event_name": "pull_request", "event": {}, "env": {}}


def _workflow(tmp_path, text, name="mini.yml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "ws").mkdir(exist_ok=True)
    return path, tmp_path / "ws", {"OUT": str(tmp_path / "out")}


def test_run_workflow_stops_before_any_step_when_a_name_is_taken_and_deletes_nothing(tmp_path, docker):
    """Tái hiện lỗi: trước guard, workflow chạy tiếp và bước 'Clean up SUT' `docker rm -f sut` xoá container của người khác."""
    fake = docker(containers=["sut"], networks=["qc-net"])
    path, ws, env = _workflow(tmp_path, MINI)
    with pytest.raises(harness.DockerNamesBusy, match="container sut"):
        harness.run_workflow(path, ws, {}, {}, GITHUB, skip=(), echo=lambda *_: None, step_env={"Start SUT": env, "Clean up SUT": env})
    assert not (tmp_path / "out" / "started.txt").exists(), "không bước nào được chạy"
    assert not fake.wrote_anything(), fake.calls


def test_run_workflow_checks_the_real_gate_job_too(tmp_path, docker):
    fake = docker(networks=["qc-net"])
    with pytest.raises(harness.DockerNamesBusy, match="network qc-net"):
        harness.run_workflow(harness.DEFAULT_WORKFLOW, tmp_path, {"project": "p", "image": "qc-agent:x"}, {}, GITHUB, echo=lambda *_: None)
    assert not fake.wrote_anything() and len(fake.calls) == 2


def test_a_job_that_never_touches_the_names_does_not_even_ask_docker(tmp_path, docker):
    fake = docker(containers=["sut"], networks=["qc-net"])   # có xung đột, nhưng job này không dùng các tên đó
    path, ws, env = _workflow(tmp_path, NO_NAMES)
    results = harness.run_workflow(path, ws, {}, {}, GITHUB, skip=(), echo=lambda *_: None, step_env={"Only echo": env}, job="validate")
    assert results["Only echo"]["returncode"] == 0 and fake.calls == []


def test_the_workflow_still_contains_the_names_the_guard_looks_for():
    """Canh workflow thật: nếu bước dựng/dọn đổi tên container hay mạng thì RESERVED_* của guard phải đổi theo, không thì guard im lặng bỏ sót."""
    import yaml
    steps = yaml.safe_load(harness.DEFAULT_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["gate"]["steps"]
    assert harness.uses_reserved_docker_names(steps)
    cleanup = next(step["run"] for step in steps if step.get("name") == "Clean up SUT")
    assert f"docker rm -f {' '.join(harness.RESERVED_CONTAINERS)}" in cleanup and f"docker network rm {harness.RESERVED_NETWORK}" in cleanup
    start = next(step["run"] for step in steps if step.get("name") == "Start SUT")
    assert f"docker network create {harness.RESERVED_NETWORK}" in start
    assert all(f"--name {name} " in start for name in harness.RESERVED_CONTAINERS)


# ---- CLI ----

def test_cli_exits_1_with_the_reason_and_deletes_nothing(tmp_path, docker, capsys, monkeypatch):
    fake = docker(containers=["db"])
    monkeypatch.setattr(image_check, "verify", lambda image: image_check.Verdict(image, True, "commit", "a" * 40, "a" * 40))
    code = harness.main(["--workspace", str(tmp_path), "--input", "project=p", "--input", "image=qc-agent:x"])
    err = capsys.readouterr().err
    assert code == 1 and "container db" in err and "KHÔNG xoá" in err and not fake.wrote_anything()


# ---- harness_kit (đường pytest/kịch bản): guard chạy trước `finally` xoá container ----

def _stack_stub():
    from types import SimpleNamespace
    return SimpleNamespace(gh_url="http://x", llm_url="http://y", jira_url="http://z")


def test_run_pr_raises_before_touching_the_repo_or_cleaning_up(tmp_path, docker, monkeypatch):
    fake = docker(containers=["sut"])
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    monkeypatch.setattr(kit, "cleanup_containers", lambda: pytest.fail("cleanup_containers xoá sut/ui/db/qc-net của việc khác"))
    with pytest.raises(harness.DockerNamesBusy, match="container sut"):
        kit.run_pr(_stack_stub(), repo, project="p", policy_dir=tmp_path, image="qc-agent:x", base="b", head="h", run_id="1", home=tmp_path / "home")
    assert (repo / "runs").exists() and not (tmp_path / "runs-before-1").exists(), "repo không được bị đụng tới khi guard dừng"
    assert not fake.wrote_anything()


def test_probe_sut_raises_before_cleaning_up(tmp_path, docker, monkeypatch):
    fake = docker(networks=["qc-net"])
    monkeypatch.setattr(kit, "cleanup_containers", lambda: pytest.fail("cleanup_containers xoá sut/ui/db/qc-net của việc khác"))
    with pytest.raises(harness.DockerNamesBusy, match="network qc-net"):
        kit.probe_sut(tmp_path, "qc-agent:x", ["/health"])
    assert not fake.wrote_anything()


def test_full_chain_cli_reports_the_clash_and_exits_1(monkeypatch, capsys):
    from tests import full_chain
    monkeypatch.setattr(kit.image_check, "ensure_image", lambda **_: kit.image_check.Verdict("qc-agent:x", True, "commit", "a" * 40, "a" * 40))
    monkeypatch.setattr(full_chain, "run_all", lambda image: (_ for _ in ()).throw(harness.DockerNamesBusy("container ui: KHÔNG xoá")))
    assert full_chain.main() == 1 and "container ui" in capsys.readouterr().err
