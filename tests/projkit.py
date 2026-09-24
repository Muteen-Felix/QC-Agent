"""Helper dựng project/suite/SUT tạm cho test (dùng chung test_project và test_executor)."""
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def task(task_id, lane="gate", capability="demo.echo", fixture="data/mock_ok.json", **over):
    t = {
        "task_id": task_id, "capability": capability, "lane": lane, "intent": "x",
        "target": {"kind": "none", "base_url": "http://127.0.0.1:1"},
        "inputs": {"fixture": fixture}, "oracle": {"kind": "trivial"},
        "expected_result_kind": "verdict" if lane == "gate" else "candidate_finding",
        "budget": {"wallclock_s": 30, "tokens": 0, "usd": 0},
        "determinism": {"seed": None, "replayable": lane == "gate"},
        "evidence_required": ["raw_output"], "retry": {"max": 0, "on": []},
    }
    t.update(over)
    return t


def write_suite(sut, name, tasks):
    d = sut / ".qc-agent" / "suites"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(yaml.safe_dump({"suite": name, "tasks": tasks}), encoding="utf-8")


def write_project(tmp_path, modes=None, slug="demo"):
    d = tmp_path / "projects"
    d.mkdir(exist_ok=True)
    modes = modes or {"pr": {"blocking_suites": ["core"], "advisory_suites": ["extra"], "on_skipped_gate_task": "fail"},
                      "manual": {"suites": "*"}}
    (d / f"{slug}.yaml").write_text(yaml.safe_dump({"slug": slug, "sut": {"files": []}, "modes": modes}), encoding="utf-8")
    return d


def make_sut(tmp_path):
    """SUT tạm: fixture chỉ tồn tại dưới SUT root (chứng minh worker chạy với cwd = SUT root) + 2 suite."""
    s = tmp_path / "sut"
    (s / "data").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "fixtures" / "mock_ok.json", s / "data" / "mock_ok.json")
    write_suite(s, "core", [task("t-1"), task("t-2")])
    write_suite(s, "extra", [task("t-9", lane="discovery")])
    return s
