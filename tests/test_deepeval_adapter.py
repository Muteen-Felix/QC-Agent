from __future__ import annotations

import copy
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from adapters import deepeval_runtime
from adapters._base import AdapterParseError
from adapters.deepeval_adapter import (
    GEVAL_NAME,
    JUNIT_NAME,
    METRICS,
    DeepEvalAdapter,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "tests" / "fixtures" / "outputs_collect.json"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "task_de.json").read_text(encoding="utf-8"))
RECORDS = json.loads(OUTPUTS.read_text(encoding="utf-8"))


def _spec() -> dict:
    spec = copy.deepcopy(SPEC)
    spec["inputs"]["outputs_path"] = str(OUTPUTS)
    return spec


def _completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["python", "-m", "pytest", "tests/eval/test_summarize.py"],
        returncode=returncode,
        stdout="pytest summary\n",
        stderr="",
    )


def _write_junit(workdir: Path, failed: set[tuple[str, str]] | None = None) -> None:
    failed = failed or set()
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name="test_summarize")
    for metric in METRICS:
        for record in RECORDS:
            case_id = record["id"]
            case = ET.SubElement(suite, "testcase", name=f"test_metric[{metric}-{case_id}]")
            if (metric, case_id) in failed:
                failure = ET.SubElement(case, "failure", type="AssertionError")
                failure.text = f"{metric} failed for {case_id}"
    ET.SubElement(suite, "testcase", name="test_geval_advisory")
    ET.ElementTree(root).write(workdir / JUNIT_NAME, encoding="utf-8", xml_declaration=True)


def _parse(workdir: Path, returncode: int = 0, spec: dict | None = None):
    return DeepEvalAdapter().parse_output(_completed(returncode), workdir, spec or _spec())


def test_clean_junit_and_geval_score_produce_both_verdict_sources(tmp_path):
    _write_junit(tmp_path)
    (tmp_path / GEVAL_NAME).write_text(
        json.dumps(
            {
                "score": 0.71,
                "cases": {record["id"]: 0.71 for record in RECORDS},
                "judge_provider": "gemini",
                "judge_model": "gemini-3.5-flash-lite",
            }
        ),
        encoding="utf-8",
    )

    parsed = _parse(tmp_path)

    assert parsed.signals == {"checks": {metric: True for metric in METRICS}}
    llm_findings = [finding for finding in parsed.findings if finding["verdict_source"] == "llm_judgment"]
    assert len(llm_findings) == 1
    assert llm_findings[0]["confidence"] == 0.71
    assert parsed.metrics["GEval.score"] == 0.71
    assert any("G-Eval judge=gemini/gemini-3.5-flash-lite" == note for note in parsed.adapter_notes)


def test_one_deterministic_failure_maps_to_its_metric_and_case(tmp_path):
    _write_junit(tmp_path, {("summary_shorter_than_body", "g3")})

    parsed = _parse(tmp_path, returncode=1)

    assert parsed.signals["checks"]["summary_shorter_than_body"] is False
    assert parsed.signals["checks"]["summary_not_empty"] is True
    assert any(
        finding["detected_by"] == "metric:summary_shorter_than_body"
        and "case g3" in finding["title"]
        and finding["verdict_source"] == "deterministic_assert"
        for finding in parsed.findings
    )


def test_judge_model_matching_sut_is_rejected_before_execution(tmp_path):
    spec = _spec()
    spec["inputs"]["judge_config"]["model"] = spec["inputs"]["sut_model"]
    spec["inputs"]["outputs_path"] = "runs/missing-outputs.json"

    with pytest.raises(AdapterParseError, match="judge trùng model của SUT"):
        DeepEvalAdapter().build_cmd(spec, tmp_path)


def test_missing_or_incomplete_junit_is_an_adapter_error(tmp_path):
    with pytest.raises(AdapterParseError, match="không có JUnit report"):
        _parse(tmp_path)

    _write_junit(tmp_path)
    root = ET.parse(tmp_path / JUNIT_NAME).getroot()
    suite = next(element for element in root.iter() if element.tag == "testsuite")
    suite.set("tests", "99")
    ET.ElementTree(root).write(tmp_path / JUNIT_NAME, encoding="utf-8", xml_declaration=True)
    with pytest.raises(AdapterParseError, match="JUnit tests=99"):
        _parse(tmp_path)


def test_exit_code_and_junit_must_agree(tmp_path):
    _write_junit(tmp_path)

    with pytest.raises(AdapterParseError, match="mâu thuẫn exit code/báo cáo JUnit"):
        _parse(tmp_path, returncode=1)


def test_pytest_infrastructure_exit_code_is_not_classified_as_metric_failure(tmp_path):
    _write_junit(tmp_path, {("summary_shorter_than_body", "g3")})

    with pytest.raises(AdapterParseError, match="pytest exit code infrastructure error: 2"):
        _parse(tmp_path, returncode=2)


def test_geval_error_report_does_not_change_deterministic_gate(tmp_path):
    _write_junit(tmp_path)
    (tmp_path / GEVAL_NAME).write_text(
        json.dumps({"error": "judge evaluation failed", "error_type": "NotFoundError"}),
        encoding="utf-8",
    )

    parsed = _parse(tmp_path)

    assert all(parsed.signals["checks"].values())
    assert not any(finding["verdict_source"] == "llm_judgment" for finding in parsed.findings)
    assert parsed.metrics == {}
    assert any("G-Eval advisory report không hợp lệ" in note for note in parsed.adapter_notes)


def test_malformed_geval_case_scores_are_ignored_as_advisory(tmp_path):
    _write_junit(tmp_path)
    valid_cases = {record["id"]: 0.5 for record in RECORDS}
    invalid_reports = [
        {"score": 0.5, "cases": {key: value for key, value in valid_cases.items() if key != "g5"}},
        {"score": 0.5, "cases": {**valid_cases, "g3": 1.5}},
        {"score": 0.4, "cases": valid_cases},
    ]

    for partial in invalid_reports:
        (tmp_path / GEVAL_NAME).write_text(
            json.dumps(
                {
                    **partial,
                    "judge_provider": "gemini",
                    "judge_model": "gemini-3.5-flash-lite",
                }
            ),
            encoding="utf-8",
        )
        parsed = _parse(tmp_path)

        assert all(parsed.signals["checks"].values())
        assert not any(finding["verdict_source"] == "llm_judgment" for finding in parsed.findings)
        assert parsed.metrics == {}


def test_primary_provider_failure_uses_gemini_fallback_once(tmp_path, monkeypatch):
    attempts: list[tuple[str, str]] = []

    def fake_score(records, provider, model):
        attempts.append((provider, model))
        if provider == "openai":
            raise RuntimeError("secret response body and key must not be copied")
        return {record["id"]: 0.71 for record in records}

    monkeypatch.setattr(deepeval_runtime, "_score_cases", fake_score)
    output = deepeval_runtime.run_geval_advisory(
        RECORDS,
        tmp_path,
        {"provider": "openai", "model": "bad-openai-model"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    )

    assert attempts == [
        ("openai", "bad-openai-model"),
        ("gemini", "gemini-3.5-flash-lite"),
    ]
    assert output["judge_provider"] == "gemini"
    assert output["judge_model"] == "gemini-3.5-flash-lite"
    assert json.loads((tmp_path / GEVAL_NAME).read_text(encoding="utf-8"))["score"] == 0.71


def test_all_judges_failing_remains_advisory_and_does_not_leak_errors(tmp_path, monkeypatch):
    attempts: list[str] = []

    def fail_score(records, provider, model):
        attempts.append(provider)
        raise RuntimeError("provider response contained secret-key-value")

    monkeypatch.setattr(deepeval_runtime, "_score_cases", fail_score)
    output = deepeval_runtime.run_geval_advisory(
        RECORDS,
        tmp_path,
        {"provider": "openai", "model": "model-a"},
        {"provider": "gemini", "model": "model-b"},
    )
    _write_junit(tmp_path)
    # The worker parser sees an advisory error while all deterministic samples pass.
    parsed = _parse(tmp_path)
    serialized = json.dumps(output) + (tmp_path / GEVAL_NAME).read_text(encoding="utf-8")

    assert attempts == ["openai", "gemini"]
    assert "secret-key-value" not in serialized
    assert "error" in output
    assert all(parsed.signals["checks"].values())
    assert not any(finding["verdict_source"] == "llm_judgment" for finding in parsed.findings)


def test_invalid_primary_case_scores_use_the_single_fallback(tmp_path, monkeypatch):
    attempts: list[str] = []

    def fake_score(records, provider, model):
        attempts.append(provider)
        if provider == "openai":
            return {record["id"]: 2.0 for record in records}
        return {record["id"]: 0.6 for record in records}

    monkeypatch.setattr(deepeval_runtime, "_score_cases", fake_score)
    result = deepeval_runtime.run_geval_advisory(
        RECORDS,
        tmp_path,
        {"provider": "openai", "model": "model-a"},
        {"provider": "gemini", "model": "model-b"},
    )

    assert attempts == ["openai", "gemini"]
    assert result["judge_provider"] == "gemini"
    assert result["score"] == 0.6
