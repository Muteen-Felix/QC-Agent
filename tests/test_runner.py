"""core/runner: retry đúng 1 lần cho error, không retry fail, timeout, skipped (phụ thuộc / không worker), budget, contract.
Worker giả là tests/fake_worker.py chạy qua `python -m fake_worker` — đi đúng đường subprocess thật của runner."""
import copy
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from qc_agent.core import runner, schema

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
_K6 = json.loads((ROOT / "tests" / "fixtures" / "contract" / "task.k6.json").read_text(encoding="utf-8-sig"))

WORKER = SimpleNamespace(name="fake", module="fake_worker", probe_ok=True, probe_reason=None, version=None)


class Registry:
    """registry giả: pick luôn trả cùng một đáp án và ghi lại lời gọi."""

    def __init__(self, worker=WORKER, reason=""):
        self.worker, self.reason, self.calls = worker, reason, []

    def pick(self, spec, prefer=()):
        self.calls.append((spec["task_id"], prefer))
        return self.worker, self.reason


def make_spec(tid, mode, retry_max=1, **over):
    s = copy.deepcopy(_K6)
    s.update(task_id=tid, capability="demo.echo", oracle={"kind": "trivial"}, evidence_required=[],
             budget={"wallclock_s": 20, "tokens": 0, "usd": 0},
             retry={"max": retry_max, "on": ["error"] if retry_max else []})
    s["inputs"] = {"mode": mode}
    s.update(over)
    assert schema.validate_task(s) == []
    return s


@pytest.fixture(autouse=True)
def fake_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(TESTS), str(ROOT)]))  # cho `python -m fake_worker` + `import core`
    monkeypatch.setenv("FAKE_COUNT_DIR", str(tmp_path / "spawns"))
    (tmp_path / "spawns").mkdir()


def run(tmp_path, specs, registry=None, plan_only=None):
    return runner.run_all({s["task_id"]: s for s in specs}, plan_only or {}, registry or Registry(),
                          tmp_path / "runs" / "r-0001")


def spawns(tmp_path, tid):
    f = tmp_path / "spawns" / tid
    return f.stat().st_size if f.exists() else 0


def test_01_error_is_retried_exactly_once(tmp_path):
    res = run(tmp_path, [make_spec("t-001", "error"), make_spec("t-002", "flaky")])
    assert res["t-001"]["status"] == "error"
    assert spawns(tmp_path, "t-001") == 2  # 1 lần đầu + ĐÚNG 1 lần retry, không có lần 3
    assert res["t-002"]["status"] == "pass" and spawns(tmp_path, "t-002") == 2
    assert any("retry" in n for n in res["t-002"]["adapter_notes"])  # retry không được che mất lần lỗi đầu


def test_02_error_is_not_retried_when_retry_max_is_zero(tmp_path):
    res = run(tmp_path, [make_spec("t-001", "crash", retry_max=0)])
    assert spawns(tmp_path, "t-001") == 1
    assert res["t-001"]["status"] == "error" and "exit=3" in res["t-001"]["verdict"]["rationale"]


def test_03_fail_is_never_retried(tmp_path):
    reg = Registry()
    res = run(tmp_path, [make_spec("t-001", "fail", retry_max=1)], reg, {"t-001": {"prefer": ["fake"]}})
    assert res["t-001"]["status"] == "fail"
    assert spawns(tmp_path, "t-001") == 1  # retry.max=1 vẫn không được chạy lại một assert fail
    assert reg.calls == [("t-001", ("fake",))]  # `prefer` của plan tới được registry


def test_04_timeout_is_error_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SLACK_S", 0)  # nếu không, test phải chờ +30s thật
    hang = make_spec("t-001", "hang", retry_max=0, budget={"wallclock_s": 1, "tokens": 0, "usd": 0})
    t0 = time.monotonic()
    res = run(tmp_path, [hang, make_spec("t-002", "pass")])
    assert time.monotonic() - t0 < 15  # không chờ worker ngủ hết 60s, cũng không treo ở communicate()
    assert res["t-001"]["status"] == "error" and "timeout" in res["t-001"]["verdict"]["rationale"]
    assert res["t-001"]["cost"]["wallclock_s"] >= 1
    assert res["t-002"]["status"] == "pass"  # timeout của một task không dừng cả run


def test_05_skipped_when_dependency_not_pass_and_results_written(tmp_path):
    specs = [make_spec("t-000", "fail"), make_spec("t-001", "pass"), make_spec("t-002", "pass"),
             make_spec("t-003", "pass"), make_spec("t-004", "pass"), make_spec("t-005", "pass")]
    plan_only = {"t-001": {"depends_on": ["t-000"]},  # t-000 fail -> skipped
                 "t-002": {"depends_on": ["t-001"]},  # t-001 skipped (không phải pass) -> skipped lan truyền
                 "t-003": {"depends_on": ["t-999"]},  # chưa từng chạy -> skipped
                 "t-005": {"depends_on": ["t-004"]}}  # t-004 pass -> phải chạy bình thường
    res = run(tmp_path, specs, plan_only=plan_only)
    assert [res[t]["status"] for t in sorted(res)] == ["fail", "skipped", "skipped", "skipped", "pass", "pass"]
    assert "phụ thuộc t-000 không đạt" in res["t-001"]["verdict"]["rationale"]
    assert [spawns(tmp_path, t) for t in ("t-001", "t-002", "t-003")] == [0, 0, 0]  # skipped thì không spawn
    assert spawns(tmp_path, "t-005") == 1
    for s in specs:  # file ghi ra khớp giá trị trả về, UTF-8, xuống dòng \n
        d = tmp_path / "runs" / "r-0001"
        raw = (d / "results" / f"{s['task_id']}.json").read_bytes()
        assert b"\r\n" not in raw and json.loads(raw.decode("utf-8")) == res[s["task_id"]]
        assert json.loads((d / "specs" / f"{s['task_id']}.json").read_bytes().decode("utf-8")) == s


def test_06_over_budget_becomes_error_and_is_not_retried(tmp_path):
    res = run(tmp_path, [make_spec("t-001", "tokens"), make_spec("t-002", "usd"), make_spec("t-003", "pass")])
    for tid, key in (("t-001", "tokens"), ("t-002", "usd")):
        r = res[tid]
        assert r["status"] == "error" and "vượt budget" in r["verdict"]["rationale"] and key in r["verdict"]["rationale"]
        assert r["cost"][key] > 0  # giữ chi phí thật đã tiêu, không xoá về 0
        assert schema.validate_result(r) == []
        assert spawns(tmp_path, tid) == 1  # retry.max=1 nhưng chạy lại chỉ tiêu thêm tiền
    assert res["t-003"]["status"] == "pass"  # cost == budget là biên: không bị cắt nhầm


def test_07_invalid_worker_output_becomes_error(tmp_path):
    res = run(tmp_path, [make_spec("t-001", "bad_gate", retry_max=0), make_spec("t-002", "garbage", retry_max=0)])
    assert res["t-001"]["status"] == "error"  # worker báo pass nhưng gating=false trên task gate
    assert res["t-001"]["verdict"]["rationale"].startswith("contract") and "gating" in res["t-001"]["verdict"]["rationale"]
    assert res["t-002"]["status"] == "error" and res["t-002"]["verdict"]["rationale"].startswith("parse")
    assert all(schema.validate_result(r) == [] for r in res.values())


def test_08_skipped_when_no_worker_or_probe_broken(tmp_path):
    none = Registry(None, "không worker nào có capability demo.echo")
    res = run(tmp_path, [make_spec("t-001", "pass")], none)
    assert res["t-001"]["status"] == "skipped"
    assert res["t-001"]["verdict"]["rationale"] == "không worker nào có capability demo.echo"

    broken = SimpleNamespace(name="fake", module="fake_worker", probe_ok=False,
                             probe_reason="thiếu biến môi trường OPENAI_API_KEY")
    res = run(tmp_path, [make_spec("t-002", "pass")], Registry(broken))
    assert res["t-002"]["status"] == "skipped"
    assert "thiếu biến môi trường OPENAI_API_KEY" in res["t-002"]["verdict"]["rationale"]
    assert spawns(tmp_path, "t-001") == spawns(tmp_path, "t-002") == 0  # không worker thì không spawn
