from pathlib import Path
import copy

import pytest

from qc_agent.core import project, registry
from qc_agent.core.plan import PlanError
from qc_agent.selector.payload import TriggerPayload, merge_floor

ROOT = Path(__file__).resolve().parent.parent


def test_manual_maps_worker_to_suite_without_floor():
    cfg = project.load_project("noteboard", ROOT / "configs/projects")
    suites = project.load_suites(ROOT / "tests/fixtures/sut/noteboard/.qc-agent/suites")
    workers = registry.load(ROOT / "workers")
    result = TriggerPayload.manual(["schemathesis"], cfg, "pr", suites, workers)
    assert result["suites"] == ["api-contract"]
    assert result["floor"] == []
    assert result["source"] == "manual"
    with pytest.raises(PlanError, match="hop le"):
        TriggerPayload.manual(["not-a-worker"], cfg, "pr", suites, workers)


def test_floor_only_adds():
    out = merge_floor({"workers": ["pytest"], "suites": ["gt-functional"], "rationale": {"pytest": "notes"}},
                      {"floor_workers": ["semgrep"]}, {"semgrep": ["sast"]})
    assert out["workers"] == ["pytest", "semgrep"]
    assert out["suites"] == ["gt-functional", "sast"]


def test_floor_suite_must_be_blocking():
    cfg = project.load_project("noteboard", ROOT / "configs/projects")
    cfg = copy.deepcopy(cfg)
    cfg["modes"]["pr"]["blocking_suites"].remove("sast")
    cfg["modes"]["pr"]["advisory_suites"].append("sast")
    suites = project.load_suites(ROOT / "tests/fixtures/sut/noteboard/.qc-agent/suites")
    with pytest.raises(PlanError, match="floor_workers"):
        project.build_plan(cfg, "pr", suites)
