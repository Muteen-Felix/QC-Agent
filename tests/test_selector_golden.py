from pathlib import Path
import subprocess

import yaml

from qc_agent.llm.client import ToolCall, Usage
from qc_agent.selector import agent, rules
from qc_agent.selector.pruner import PrunedDiff, PrunedFile
from tools.eval_selector import _fake_gate_verdict, _ratio

ROOT = Path(__file__).resolve().parent / "fixtures/diffs"
SUT = Path(__file__).resolve().parent / "fixtures/sut/noteboard"


def test_golden_patch_inventory_and_rule_categories():
    labels = yaml.safe_load((ROOT / "labels.yaml").read_text(encoding="utf-8"))
    normal = sorted(ROOT.glob("diff-*.patch"))
    injection = sorted((ROOT / "injection").glob("*.patch"))
    assert len(normal) >= 30 and len(injection) >= 10
    assert {p.stem for p in normal + injection} == set(labels) - {"labeled_by"}
    for patch in normal + injection:
        check = subprocess.run(["git", "apply", "--check", str(patch)], cwd=SUT, capture_output=True, text=True)
        assert check.returncode == 0, (patch.name, check.stderr)
    assert "CẦN QA DUYỆT" in labels["labeled_by"]
    policy = {"full_set_paths": ["Dockerfile", "**/Dockerfile*", "package-lock.json"], "docs_paths": ["docs/**", "**/*.md"]}
    assert rules.decide([rules.ChangedFile("docs/note.md", "A")], policy, None, {}).floor_only
    assert rules.decide([rules.ChangedFile("services/api/Dockerfile", "A")], policy, None, {}).full_set


def test_all_injections_keep_floor_even_if_fake_llm_selects_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "call_tool", lambda **kwargs: ToolCall({"selections": []}, Usage(), "fake", "tool_use", 0))
    policy = {"floor_workers": ["semgrep", "gitleaks"], "blocking_suites": ["sast", "secrets", "api-contract", "gt-functional"]}
    suite_map = {"semgrep": ["sast"], "gitleaks": ["secrets"], "schemathesis": ["api-contract"], "pytest": ["gt-functional"]}
    module_map = {"status": "approved", "modules": [{"name": "notes", "paths": ["toyapp/**"], "suites": ["api-contract", "gt-functional"]}]}
    decision = rules.decide([rules.ChangedFile("toyapp/selector_api_1.py", "A")], policy, module_map, suite_map)
    for patch in sorted((ROOT / "injection").glob("*.patch")):
        diff = PrunedDiff("b", "h", "b", (PrunedFile("toyapp/selector_api_1.py", "A", None, "code",
                           patch.read_text(encoding="utf-8"), False, 0),), 10, patch.stem)
        selected = agent.select(diff, decision, policy, suite_map, module_map, egress_dir=tmp_path)
        assert {"semgrep", "gitleaks", "schemathesis", "pytest"} <= set(selected["workers"])


def test_eval_formula_and_fake_verdict_are_sensitive_to_omission():
    assert _ratio(9, 10) == .9
    assert _ratio(0, 0) == 1.0
    assert _fake_gate_verdict({"semgrep", "schemathesis"}) == "BLOCKED"
    assert _fake_gate_verdict({"semgrep"}) == "PASSED"
