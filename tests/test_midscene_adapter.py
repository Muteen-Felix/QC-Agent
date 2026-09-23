import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import adapters.midscene_adapter as midscene_module
from adapters._base import AdapterParseError
from adapters.midscene_adapter import MidsceneAdapter


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
    monkeypatch.setattr(midscene_module, "_events", lambda _base_url: events)

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
    monkeypatch.setattr(midscene_module, "_events", lambda _base_url: [])
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
    monkeypatch.setattr(midscene_module, "_events", lambda _base_url: [])

    with pytest.raises(AdapterParseError, match="mâu thuẫn exit code/summary"):
        MidsceneAdapter().parse_output(_completed(1), tmp_path, copy.deepcopy(SPEC))


def test_unavailable_telemetry_is_an_adapter_error(tmp_path, monkeypatch):
    shutil.copyfile(SAMPLES / "midscene_summary.fixture.json", tmp_path / "summary.json")

    def unavailable(_base_url):
        raise AdapterParseError("không đọc được telemetry")

    monkeypatch.setattr(midscene_module, "_events", unavailable)
    with pytest.raises(AdapterParseError, match="telemetry"):
        MidsceneAdapter().parse_output(_completed(0), tmp_path, copy.deepcopy(SPEC))
