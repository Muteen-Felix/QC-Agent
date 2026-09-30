import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from qc_agent.core import egress, runner, signature


def test_safe_tasks_overlap_and_exclusive_runs_alone(monkeypatch, tmp_path):
    times = {}
    class Registry:
        def pick(self, spec, prefer=()):
            return SimpleNamespace(capabilities={"demo.echo": {"parallel_safe": spec["task_id"] != "c"}}), ""
    def fake(spec, extra, registry, done, *args):
        tid = spec["task_id"]
        start = time.perf_counter()
        time.sleep(.1)
        times[tid] = (start, time.perf_counter())
        return {"task_id": tid, "status": "pass", "worker": {"name": "fake"}, "cost": {}, "verdict": {}}
    monkeypatch.setattr(runner, "_run_task", fake)
    specs = {tid: {"task_id": tid, "capability": "demo.echo"} for tid in "abc"}
    results = runner.run_all(specs, {}, Registry(), tmp_path, layers=[["a", "b", "c"]], max_parallel=2)
    assert list(results) == ["a", "b", "c"]
    assert max(times["a"][0], times["b"][0]) < min(times["a"][1], times["b"][1])
    assert times["c"][0] >= max(times["a"][1], times["b"][1])
    assert len(list((tmp_path / "results").glob("*.json"))) == 3


def test_parallel_result_order_and_signature_match_serial(monkeypatch, tmp_path):
    class Registry:
        def pick(self, spec, prefer=()):
            return SimpleNamespace(capabilities={"demo.echo": {"parallel_safe": True}}), ""
    def fake(spec, extra, registry, done, *args):
        return {"task_id": spec["task_id"], "status": "pass", "worker": {"name": "fake", "version": "1"},
                "cost": {}, "verdict": {}}
    monkeypatch.setattr(runner, "_run_task", fake)
    specs = {tid: {"task_id": tid, "capability": "demo.echo", "lane": "gate", "determinism": {"seed": 0}} for tid in "abc"}
    serial = runner.run_all(specs, {}, Registry(), tmp_path / "serial", layers=[["a", "b"], ["c"]], max_parallel=1)
    parallel = runner.run_all(specs, {}, Registry(), tmp_path / "parallel", layers=[["a", "b"], ["c"]], max_parallel=2)
    assert serial == parallel
    assert list(parallel) == ["a", "b", "c"]
    assert signature.run_signature("plan", "sut", serial, specs) == signature.run_signature("plan", "sut", parallel, specs)


def test_egress_lines_are_complete_under_concurrency(tmp_path):
    worker = SimpleNamespace(name="fake", data_egress=[])
    spec = {"task_id": "t", "capability": "demo.echo", "target": {}}
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: egress.record(egress.LogOnlyPolicy(), tmp_path, spec, worker, 1), range(30)))
    lines = (tmp_path / "egress.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 30
    assert all(json.loads(line)["task_id"] == "t" for line in lines)
