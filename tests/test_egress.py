"""Hook data_egress (bước 22): ghi log dữ liệu rời máy theo từng lần gọi worker, không chặn; deny/mask có chỗ đứng nhưng không im lặng bỏ qua."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from qc_agent.core import egress, runner
from tests.test_runner import Registry, make_spec, fake_env  # noqa: F401  (fake_env: autouse fixture của test_runner)

ROOT = Path(__file__).resolve().parent.parent


def worker(egress_categories=("screenshot", "dom")):
    return SimpleNamespace(name="fake", module="fake_worker", probe_ok=True, probe_reason=None, version=None,
                           data_egress=list(egress_categories))


def run(tmp_path, specs, reg=None, policy=None):
    run_dir = tmp_path / "runs" / "r-0001"
    results = runner.run_all({s["task_id"]: s for s in specs}, {}, reg or Registry(worker()), run_dir, egress_policy=policy)
    return results, run_dir


def lines(run_dir):
    path = run_dir / egress.LOG_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_every_dispatch_is_logged_with_declared_categories_and_only_the_host(tmp_path):
    spec = make_spec("t-1", "pass")
    spec["target"] = {"kind": "web_app", "base_url": "https://user:pw@shop.example:8443/secret/path?token=abc#frag"}
    results, run_dir = run(tmp_path, [spec])

    (event,) = lines(run_dir)
    assert results["t-1"]["status"] == "pass"
    assert event["task_id"] == "t-1" and event["worker"] == "fake" and event["attempt"] == 1 and event["basis"] == "declared"
    assert event["categories"] == ["dom", "screenshot"]
    assert event["target_host"] == "shop.example:8443"
    assert event["decision"] == {"action": "allow", "reason": "log-only"}
    text = (run_dir / egress.LOG_NAME).read_text(encoding="utf-8")
    assert not any(leak in text for leak in ("pw", "secret/path", "token=abc", "frag", "user"))  # không lộ thông tin đăng nhập/đường dẫn/query


def test_retry_is_a_second_egress_and_is_logged_as_attempt_2(tmp_path):
    results, run_dir = run(tmp_path, [make_spec("t-1", "crash", retry_max=1)])
    assert results["t-1"]["status"] == "error"
    assert [e["attempt"] for e in lines(run_dir)] == [1, 2]


def test_tasks_that_never_reach_a_worker_leave_no_egress_record(tmp_path):
    reg = Registry(worker=None, reason="không worker nào có capability này")
    results, run_dir = run(tmp_path, [make_spec("t-1", "pass")], reg=reg)
    assert results["t-1"]["status"] == "skipped" and lines(run_dir) == []


def test_worker_without_declaration_logs_an_empty_category_list(tmp_path):
    _, run_dir = run(tmp_path, [make_spec("t-1", "pass")], reg=Registry(worker(())))
    assert lines(run_dir)[0]["categories"] == []


def test_log_write_failure_does_not_break_the_run(tmp_path):
    run_dir = tmp_path / "runs" / "r-0001"
    run_dir.mkdir(parents=True)
    (run_dir / egress.LOG_NAME).mkdir()  # không thể mở như file để ghi thêm
    results = runner.run_all({"t-1": make_spec("t-1", "pass")}, {}, Registry(worker()), run_dir)
    assert results["t-1"]["status"] == "pass"


# ---- chừa chỗ cho mask/deny ----

class Fixed(egress.EgressPolicy):
    def __init__(self, action, reason=""):
        self.action, self.reason, self.seen = action, reason, []

    def decide(self, event):
        self.seen.append(event)
        return egress.Decision(self.action, self.reason)


def test_policy_receives_the_event_before_anything_runs(tmp_path):
    policy = Fixed("allow")
    run(tmp_path, [make_spec("t-1", "pass")], policy=policy)
    assert policy.seen[0]["categories"] == ["dom", "screenshot"] and "decision" not in policy.seen[0]


def test_deny_prevents_the_worker_from_running_and_is_skipped(tmp_path):
    results, run_dir = run(tmp_path, [make_spec("t-1", "pass")], policy=Fixed("deny", "screenshot bị cấm"))
    assert results["t-1"]["status"] == "skipped" and "bị chính sách từ chối (screenshot bị cấm)" in results["t-1"]["verdict"]["rationale"]
    assert not (tmp_path / "spawns" / "t-1").exists(), "worker không được spawn"
    assert lines(run_dir)[0]["decision"]["action"] == "deny"


def test_mask_is_refused_as_unsupported_never_silently_allowed(tmp_path):
    results, run_dir = run(tmp_path, [make_spec("t-1", "pass", retry_max=0)], policy=Fixed("mask"))
    assert results["t-1"]["status"] == "error" and "'mask' chưa được hỗ trợ" in results["t-1"]["verdict"]["rationale"]
    assert not (tmp_path / "spawns" / "t-1").exists()


def test_invalid_decision_action_is_rejected():
    with pytest.raises(ValueError):
        egress.Decision("maybe")


# ---- qua CLI/engine thật: log nằm trong thư mục run và có thể `jq` ----

def test_cli_run_writes_egress_jsonl_next_to_the_results(tmp_path):
    import subprocess
    import sys
    env = {**os.environ, "PYTHONUTF8": "1", "QC_WORKERS_PATH": str(ROOT / "tests" / "fixtures" / "workers")}
    proc = subprocess.run([sys.executable, "-m", "qc_agent.core.cli", "run", "--plan", str(ROOT / "tests" / "fixtures" / "plans" / "demo.yaml"),
                           "--runs-dir", str(tmp_path / "runs")], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    log = tmp_path / "runs" / "r-0001" / egress.LOG_NAME
    assert log.is_file(), proc.stdout[-500:] + proc.stderr[-500:]
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert events and all(e["basis"] == "declared" and e["decision"]["action"] == "allow" and e["run_id"] == "r-0001" for e in events)
