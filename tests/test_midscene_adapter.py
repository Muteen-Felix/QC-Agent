import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import qc_agent.adapters.midscene_adapter as midscene_module
from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.midscene_adapter import MidsceneAdapter


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tests" / "samples"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "task_ms.json").read_text(encoding="utf-8"))
CANARY_SPEC = json.loads(
    (ROOT / "tests" / "fixtures" / "task_ms_canary.json").read_text(encoding="utf-8")
)


def _completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["npx", "@midscene/cli"], returncode=returncode, stdout="midscene output\n", stderr=""
    )


def _run_fixture(monkeypatch, tmp_path, spec, sample_name, returncode, events):
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(midscene_module, "_events", lambda _base_url, _endpoint: events)

    def fake_build_cmd(self, _spec, workdir):
        shutil.copyfile(SAMPLES / sample_name, workdir / "summary.json")
        return [sys.executable, "-c", f"raise SystemExit({returncode})"]

    monkeypatch.setattr(MidsceneAdapter, "build_cmd", fake_build_cmd)
    return MidsceneAdapter().run(copy.deepcopy(spec))


def test_mock_pass_is_non_gating_and_keeps_cost_unknown(monkeypatch, tmp_path):
    result = _run_fixture(
        monkeypatch, tmp_path, SPEC, "midscene_summary.fixture.json", returncode=0, events=[]
    )

    assert result["status"] == "pass"
    assert result["verdict"]["value"] == "non_gating"
    assert result["verdict"]["gating"] is False
    assert result["findings"] == []
    assert result["cost"]["tokens"] is None and result["cost"]["usd"] is None
    assert any("MOCK" in note for note in result["adapter_notes"])
    assert len(result["evidence"]) == 3


def test_delete_without_later_render_becomes_promotable_finding(monkeypatch, tmp_path):
    events = [{"type": "render_done"}, {"type": "delete_clicked"}]
    result = _run_fixture(
        monkeypatch, tmp_path, SPEC, "midscene_summary.fixture.json", returncode=0, events=events
    )

    assert result["status"] == "pass"
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["detected_by"] == "implicit_signal:dom_unchanged"
    assert finding["verdict_source"] == "deterministic_assert"
    assert finding["promote_candidate"]["suggested_capability"] == "ui.explore"
    assert finding["promote_candidate"]["suggested_assertion"] == SPEC["inputs"]["promote_hints"]["dom_unchanged"]["suggested_assertion"]


def test_missing_element_canary_is_a_non_gating_failure(monkeypatch, tmp_path):
    result = _run_fixture(
        monkeypatch,
        tmp_path,
        CANARY_SPEC,
        "midscene_summary.fail_missing_element.json",
        returncode=1,
        events=[],
    )

    assert result["status"] == "fail"
    assert result["verdict"]["gating"] is False
    assert result["verdict"]["value"] == "fail"
    assert [finding["detected_by"] for finding in result["findings"]] == [
        "implicit_signal:element_not_found"
    ]


def test_missing_summary_and_missing_model_config_are_adapter_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(midscene_module, "_events", lambda _base_url, _endpoint: [])
    with pytest.raises(AdapterParseError, match="summary.json"):
        MidsceneAdapter().parse_output(_completed(1), tmp_path, copy.deepcopy(SPEC))

    for error in (
        "Model configuration is incomplete: model name is required",
        "XML parse error: Incomplete planning response",
    ):
        (tmp_path / "summary.json").write_text(
            json.dumps({"results": [{"success": False, "error": error}]}), encoding="utf-8"
        )
        with pytest.raises(AdapterParseError, match="lỗi hạ tầng"):
            MidsceneAdapter().parse_output(_completed(1), tmp_path, copy.deepcopy(SPEC))


def test_exit_code_and_summary_contradiction_is_an_adapter_error(tmp_path, monkeypatch):
    shutil.copyfile(SAMPLES / "midscene_summary.fixture.json", tmp_path / "summary.json")
    monkeypatch.setattr(midscene_module, "_events", lambda _base_url, _endpoint: [])

    with pytest.raises(AdapterParseError, match="mâu thuẫn exit code/summary"):
        MidsceneAdapter().parse_output(_completed(1), tmp_path, copy.deepcopy(SPEC))


def test_unavailable_telemetry_is_an_adapter_error(tmp_path, monkeypatch):
    shutil.copyfile(SAMPLES / "midscene_summary.fixture.json", tmp_path / "summary.json")

    def unavailable(_base_url, _endpoint):
        raise AdapterParseError("không đọc được telemetry")

    monkeypatch.setattr(midscene_module, "_events", unavailable)
    with pytest.raises(AdapterParseError, match="telemetry"):
        MidsceneAdapter().parse_output(_completed(0), tmp_path, copy.deepcopy(SPEC))


# ─────────── bước 19: URL từ target, max_steps, telemetry tuỳ chọn ───────────

def _build(tmp_path, monkeypatch, spec, flow=None):
    monkeypatch.setattr(midscene_module, "_reset_events", lambda *_a: None)
    monkeypatch.setattr(midscene_module.shutil, "which", lambda _n: "npx")
    spec = copy.deepcopy(spec)
    flow_path = tmp_path / "flow_in.yaml"
    flow_path.write_text(flow or "web:\n  url: http://placeholder.invalid/\n  chromeArgs: [--no-sandbox]\ntasks:\n  - name: t\n    flow: []\n", encoding="utf-8")
    spec["inputs"]["flow"] = str(flow_path)
    adapter = MidsceneAdapter()
    return adapter, adapter.build_cmd(spec, tmp_path), spec


def test_flow_url_comes_from_target_and_other_web_options_survive(tmp_path, monkeypatch):
    import yaml
    spec = copy.deepcopy(SPEC)
    spec["target"] = {"kind": "web_app", "base_url": "http://sut:8000/", "entry_path": "/notes"}
    _, cmd, _ = _build(tmp_path, monkeypatch, spec)

    rendered = yaml.safe_load((tmp_path / "flow.yaml").read_text(encoding="utf-8"))
    assert rendered["web"] == {"url": "http://sut:8000/notes", "chromeArgs": ["--no-sandbox"]}
    assert cmd[2] == str(tmp_path / "flow.yaml")


def test_flow_without_web_block_gets_one(tmp_path, monkeypatch):
    import yaml
    _build(tmp_path, monkeypatch, SPEC, flow="tasks:\n  - name: t\n    flow: []\n")
    assert yaml.safe_load((tmp_path / "flow.yaml").read_text(encoding="utf-8"))["web"]["url"] == "http://127.0.0.1:8000/"


def test_max_steps_is_passed_to_midscene_and_validated(tmp_path, monkeypatch):
    adapter, _, _ = _build(tmp_path, monkeypatch, SPEC)
    assert adapter.env == {"MIDSCENE_REPLANNING_CYCLE_LIMIT": "8"}
    for bad in (0, -1, True, "8", 1.5):
        spec = copy.deepcopy(SPEC)
        spec["inputs"]["max_steps"] = bad
        with pytest.raises(AdapterParseError, match="max_steps"):
            _build(tmp_path, monkeypatch, spec)
    spec = copy.deepcopy(SPEC)
    del spec["inputs"]["max_steps"]
    assert _build(tmp_path, monkeypatch, spec)[0].env == {}


@pytest.mark.parametrize("target, message", [
    ({"kind": "web_app", "base_url": "http://u:p@h:1"}, "base_url"),
    ({"kind": "web_app", "base_url": "ftp://h"}, "base_url"),
    ({"kind": "web_app", "base_url": "http://h:1", "entry_path": "//evil.test/x"}, "entry_path"),
    ({"kind": "web_app", "base_url": "http://h:1", "entry_path": "http://evil.test/"}, "entry_path"),
    ({"kind": "web_app", "base_url": "http://h:1", "entry_path": "notes"}, "entry_path"),
])
def test_target_cannot_redirect_the_browser_elsewhere(tmp_path, monkeypatch, target, message):
    spec = copy.deepcopy(SPEC)
    spec["target"] = target
    with pytest.raises(AdapterParseError, match=message):
        _build(tmp_path, monkeypatch, spec)


def test_without_telemetry_plugin_no_request_is_made_and_no_events_evidence(tmp_path, monkeypatch):
    spec = copy.deepcopy(SPEC)
    del spec["inputs"]["telemetry"]
    calls = []
    monkeypatch.setattr(midscene_module, "_reset_events", lambda *a: calls.append(a))
    monkeypatch.setattr(midscene_module, "_events", lambda *a: calls.append(a) or [])
    monkeypatch.setattr(midscene_module.shutil, "which", lambda _n: "npx")
    flow = tmp_path / "f.yaml"
    flow.write_text("tasks: []\n", encoding="utf-8")
    spec["inputs"]["flow"] = str(flow)
    MidsceneAdapter().build_cmd(spec, tmp_path)
    shutil.copyfile(SAMPLES / "midscene_summary.fixture.json", tmp_path / "summary.json")
    parsed = MidsceneAdapter().parse_output(_completed(0), tmp_path, spec)

    assert calls == [] and not (tmp_path / "events.json").exists()
    assert parsed.signals == {"detected": []} and [k for k, _ in parsed.evidence_paths] == ["raw_output", "stdout"]
    assert any("telemetry: tắt" in n for n in parsed.adapter_notes)


def test_telemetry_event_names_are_configurable_not_hardcoded(monkeypatch, tmp_path):
    spec = copy.deepcopy(SPEC)
    spec["inputs"]["telemetry"] = {"endpoint": "/x/ev", "dom_unchanged": {"after": "row_removed", "expect": "list_painted"},
                                   "console_error": "js_error"}
    monkeypatch.setattr(midscene_module, "_events", lambda _b, _e: [{"type": "row_removed"}, {"type": "js_error"}, {"type": "delete_clicked"}])
    shutil.copyfile(SAMPLES / "midscene_summary.fixture.json", tmp_path / "summary.json")
    parsed = MidsceneAdapter().parse_output(_completed(0), tmp_path, spec)
    assert [s["name"] for s in parsed.signals["detected"]] == ["dom_unchanged", "console_error"]  # tên sự kiện của toyapp không còn ý nghĩa


@pytest.mark.parametrize("telemetry", [{}, {"endpoint": "http://x/"}, {"endpoint": "/e", "bogus": 1},
                                       {"endpoint": "/e", "dom_unchanged": {"after": "a"}}, {"endpoint": "/e", "http_5xx": ""}, "x"])
def test_invalid_telemetry_config_is_rejected(tmp_path, monkeypatch, telemetry):
    spec = copy.deepcopy(SPEC)
    spec["inputs"]["telemetry"] = telemetry
    with pytest.raises(AdapterParseError, match="telemetry"):
        _build(tmp_path, monkeypatch, spec)
