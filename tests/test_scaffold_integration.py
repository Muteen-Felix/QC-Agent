from pathlib import Path

import pytest
import yaml

from qc_agent.core import project
from qc_agent.scaffold import suites_integration as integration
from qc_agent.scaffold import templates as t


def test_integration_suites_have_separate_gate_and_live_lanes(tmp_path):
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "integration.yaml").write_text(integration.integration_suite(b_host="analytics.parivahan.gov.in"), encoding="utf-8")
    (suites / "integration-live.yaml").write_text(integration.integration_live_suite(b_host="analytics.parivahan.gov.in"), encoding="utf-8")
    loaded = project.load_suites(suites)
    assert [task["task_id"] for task in loaded["integration"]["tasks"]] == ["t-020", "t-021"]
    assert all(task["lane"] == "gate" for task in loaded["integration"]["tasks"])
    assert loaded["integration-live"]["tasks"][0]["lane"] == "discovery"
    assert loaded["integration-live"]["tasks"][0]["retry"] == {"max": 0, "on": []}


def test_generated_specs_require_one_time_human_verification():
    assert t.TODO in integration.tier1_spec() and t.TODO in integration.tier2_spec()
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
