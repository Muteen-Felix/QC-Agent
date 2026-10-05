import re
from pathlib import Path

import pytest
import yaml

from qc_agent.adapters import playwright_adapter
from qc_agent.core import project
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import suites_integration as integration
from qc_agent.scaffold import templates as t

ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD = ROOT / "src" / "qc_agent" / "scaffold"
EXAMPLES = {".qc-agent/suites/integration.yaml.example", ".qc-agent/suites/integration-live.yaml.example", ".qc-agent/integration/support.mjs.example",
            ".qc-agent/integration/tier1.spec.mjs.example", ".qc-agent/integration/tier2.spec.mjs.example"}


def test_integration_suites_have_separate_gate_and_live_lanes(tmp_path):
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "integration.yaml").write_text(integration.integration_suite(b_host="partner.example.org"), encoding="utf-8")
    (suites / "integration-live.yaml").write_text(integration.integration_live_suite(b_host="partner.example.org"), encoding="utf-8")
    loaded = project.load_suites(suites)
    assert [task["task_id"] for task in loaded["integration"]["tasks"]] == ["t-020", "t-021"]
    assert all(task["lane"] == "gate" for task in loaded["integration"]["tasks"])
    assert loaded["integration-live"]["tasks"][0]["lane"] == "discovery"
    assert loaded["integration-live"]["tasks"][0]["retry"] == {"max": 0, "on": []}


def test_generated_specs_require_one_time_human_verification():
    assert t.TODO in integration.tier1_spec() and t.TODO in integration.tier2_spec()
    assert t.TODO in integration.integration_suite() and t.TODO in integration.integration_live_suite()
    assert "routeFromHAR(" not in integration.tier2_spec()
    support = integration.support()
    assert "notFound: 'abort'" in support and "update: false" in support


@pytest.mark.parametrize("host", ["bad host", "https://example.org", "x\ny", "-bad.example", "bad.example:"])
def test_b_host_rejects_yaml_or_url_injection(host):
    with pytest.raises(t.TemplateError, match="b_host"):
        integration.integration_suite(b_host=host)


def test_b_host_is_quoted_as_data():
    suite = yaml.safe_load(integration.integration_suite(b_host="B.Example"))
    assert suite["tasks"][1]["inputs"]["b_host"] == "b.example"


# ---------- khung trung tính: không bias vào SUT nào ----------

def test_scaffold_templates_and_lane_modules_name_no_specific_sut():
    banned = re.compile(r"vahan|parivahan|noteboard|change-me", re.IGNORECASE)
    files = [*sorted((SCAFFOLD / "tmpl").glob("*.tmpl")), *sorted(SCAFFOLD.glob("suites_*.py"))]
    assert len(files) > 10
    offenders = {path.name: match[0] for path in files if (match := banned.search(path.read_text(encoding="utf-8")))}
    assert not offenders, offenders


def sut(tmp_path, *dirs):
    root = tmp_path / "sut"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM python:3.11-slim\nEXPOSE 8000\n", encoding="utf-8")
    for rel in dirs:
        (root / rel).mkdir(parents=True)
    return root


@pytest.mark.parametrize("dirs", [(), ("apps/api-server", "apps/web-ui"), ("vahan-chrome-extension",)], ids=["plain", "apps-layout", "extension-dir"])
def test_init_offers_the_same_inert_skeleton_for_any_sut_layout(tmp_path, dirs):
    plan = init_mod.build(init_mod.Options(sut_root=sut(tmp_path, *dirs), slug="demo"))
    labels = {planned.label for planned in plan.files}
    assert EXAMPLES <= labels
    assert not [label for label in labels if "integration" in label and not label.endswith(".example")]   # không có file integration đang hoạt động
    assert integration.EXAMPLE_NOTE in plan.notes


def test_the_skeleton_is_ignored_by_suite_loading_and_not_reported_as_todo(tmp_path):
    root = sut(tmp_path)
    plan = init_mod.build(init_mod.Options(sut_root=root, slug="demo"))
    outcomes = init_mod.apply(plan)
    assert not [o.label for o in outcomes if o.label.endswith(".example") and o.todos]
    assert (root / ".qc-agent" / "suites" / "integration.yaml.example").is_file()
    assert "integration" not in project.load_suites(root / ".qc-agent" / "suites")


def test_an_activated_file_is_not_regenerated_next_to_itself(tmp_path):
    root = sut(tmp_path)
    (root / ".qc-agent" / "integration").mkdir(parents=True)
    (root / ".qc-agent" / "integration" / "tier1.spec.mjs").write_text("// của người dùng\n", encoding="utf-8")
    labels = {planned.label for planned in init_mod.build(init_mod.Options(sut_root=root, slug="demo")).files}
    assert ".qc-agent/integration/tier1.spec.mjs.example" not in labels and ".qc-agent/integration/tier2.spec.mjs.example" in labels


@pytest.mark.parametrize("spec", [integration.tier1_spec, integration.tier2_spec])
def test_the_skeleton_spec_passes_the_adapter_lint_but_has_only_the_reserved_check(tmp_path, spec):
    path = tmp_path / "x.spec.mjs"
    path.write_text(spec(), encoding="utf-8")
    assert playwright_adapter._lint_spec(path) == ["todo_replace_me"]
