import copy
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from qc_agent import oracle
from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.k6_adapter import PARSER_VERSION, SUMMARY_NAME, K6Adapter


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tests" / "samples"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "task_k6.json").read_text(encoding="utf-8"))


def _completed(
    returncode: int = 0,
    stdout: str = "k6 run complete",
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=args or ["k6", "run"], returncode=returncode, stdout=stdout, stderr="")


def _copy_summary(sample_name: str, workdir: Path) -> None:
    shutil.copyfile(SAMPLES / sample_name, workdir / SUMMARY_NAME)


def test_pass_summary_maps_metrics_and_builds_the_k6_command(tmp_path):
    spec = copy.deepcopy(SPEC)
    adapter = K6Adapter()
    command = adapter.build_cmd(spec, tmp_path)
    summary_argument = next(arg for arg in command if arg.startswith("--summary-export="))

    assert command[:2] == ["k6", "run"]
    assert command[command.index("--vus") + 1] == "10"
    assert command[command.index("--duration") + 1] == "30s"
    assert summary_argument.startswith("--summary-export=")
    assert Path(summary_argument.split("=", 1)[1]) == (tmp_path / SUMMARY_NAME).resolve()
    assert adapter.env["APP_BASE_URL"] == spec["target"]["base_url"]

    _copy_summary("k6_summary.pass.json", tmp_path)
    parsed = adapter.parse_output(_completed(args=command), tmp_path, spec)

    assert parsed.metrics["http_req_duration.p95"] == 5.56 and parsed.metrics["http_req_failed.rate"] == 0
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "pass"
    assert parsed.tokens == 0 and parsed.usd == 0.0
    assert parsed.replay_cmd == shlex.join(command)
    assert parsed.adapter_notes == [f"PARSER_VERSION={PARSER_VERSION}", "k6 exit_code=0"]
    assert [kind for kind, _ in parsed.evidence_paths] == ["raw_output", "stdout"]


def test_threshold_and_server_down_summaries_fail_through_the_oracle(tmp_path):
    spec = copy.deepcopy(SPEC)
    # STEP 30 ran the exploratory threshold script, whose native k6 exit was nonzero.
    # The adapter script has no thresholds, so these summary fixtures are parsed as a completed run.
    for sample_name in ("k6_summary.threshold_fail.json", "k6_summary.server_down.json"):
        _copy_summary(sample_name, tmp_path)
        parsed = K6Adapter().parse_output(_completed(), tmp_path, spec)
        outcome = oracle.evaluate(spec["oracle"], parsed.metrics, {})
        assert outcome.value == "fail"
        assert len(outcome.findings) == 1
        (tmp_path / SUMMARY_NAME).unlink()


def test_missing_summary_is_an_adapter_error(tmp_path):
    with pytest.raises(AdapterParseError, match="không có k6 summary"):
        K6Adapter().parse_output(_completed(returncode=107), tmp_path, copy.deepcopy(SPEC))


def test_nonzero_exit_with_summary_is_an_adapter_error(tmp_path):
    _copy_summary("k6_summary.pass.json", tmp_path)
    with pytest.raises(AdapterParseError, match="exit code 99"):
        K6Adapter().parse_output(_completed(returncode=99), tmp_path, copy.deepcopy(SPEC))


def test_summary_missing_required_metric_is_an_adapter_error(tmp_path):
    summary = json.loads((SAMPLES / "k6_summary.pass.json").read_text(encoding="utf-8"))
    del summary["metrics"]["http_req_failed"]
    (tmp_path / SUMMARY_NAME).write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(AdapterParseError, match="thiếu metric http_req_duration hoặc http_req_failed"):
        K6Adapter().parse_output(_completed(), tmp_path, copy.deepcopy(SPEC))


def test_every_summary_statistic_becomes_a_metric(tmp_path):
    _copy_summary("k6_summary.pass.json", tmp_path)
    m = K6Adapter().parse_output(_completed(), tmp_path, copy.deepcopy(SPEC)).metrics

    assert m["http_req_duration.p90"] == json.loads((SAMPLES / "k6_summary.pass.json").read_text(encoding="utf-8"))["metrics"]["http_req_duration"]["p(90)"]
    assert {"http_req_duration.avg", "http_req_duration.med", "http_req_duration.max", "http_reqs.count", "http_reqs.rate",
            "data_received.count", "vus.max", "iterations.count", "checks.passes", "checks.fails"} <= set(m)
    assert m["checks.rate"] == 1 and m["http_req_failed.rate"] == m["http_req_failed.value"] == 0  # Rate: value giữ nguyên + alias .rate
    assert "http_reqs.rate" in m and "http_reqs.value" not in m  # Counter không có value: không bịa
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in m.values())


def test_custom_metrics_and_unusual_percentiles_are_extracted_and_usable_by_the_oracle(tmp_path):
    summary = json.loads((SAMPLES / "k6_summary.pass.json").read_text(encoding="utf-8"))
    summary["metrics"]["notes_latency"] = {"avg": 3.0, "p(99)": 40.5, "p(99.9)": 80.0, "med": 2.0}
    (tmp_path / SUMMARY_NAME).write_text(json.dumps(summary), encoding="utf-8")
    m = K6Adapter().parse_output(_completed(), tmp_path, copy.deepcopy(SPEC)).metrics

    assert m["notes_latency.p99"] == 40.5 and m["notes_latency.p99.9"] == 80.0
    spec = copy.deepcopy(SPEC)
    spec["oracle"] = {"kind": "threshold", "assertions": [{"metric": "notes_latency.p99", "op": "<", "value": 30, "unit": "ms"}]}
    assert oracle.evaluate(spec["oracle"], m, {}).value == "fail"


def test_non_numeric_statistics_are_dropped_not_invented(tmp_path):
    summary = json.loads((SAMPLES / "k6_summary.pass.json").read_text(encoding="utf-8"))
    summary["metrics"]["weird"] = {"avg": "fast", "max": None, "min": True, "med": float("nan"), "p(95)": 7}
    summary["metrics"]["not_a_dict"] = 5
    (tmp_path / SUMMARY_NAME).write_text(json.dumps(summary), encoding="utf-8")
    m = K6Adapter().parse_output(_completed(), tmp_path, copy.deepcopy(SPEC)).metrics

    assert {k for k in m if k.startswith("weird")} == {"weird.p95"} and not any(k.startswith("not_a_dict") for k in m)


def test_assertion_on_a_metric_k6_did_not_report_is_an_error_not_a_pass(tmp_path):
    _copy_summary("k6_summary.pass.json", tmp_path)
    m = K6Adapter().parse_output(_completed(), tmp_path, copy.deepcopy(SPEC)).metrics
    with pytest.raises(oracle.OracleError, match="metric bắt buộc"):
        oracle.evaluate({"kind": "threshold", "assertions": [{"metric": "nope.p99", "op": "<", "value": 1}]}, m, {})


def test_rate_metric_with_native_thresholds_block_still_gets_rate_alias(tmp_path):
    _copy_summary("k6_summary.server_down.json", tmp_path)  # k6 thật thêm khoá `thresholds` vào metric có ngưỡng
    m = K6Adapter().parse_output(_completed(), tmp_path, copy.deepcopy(SPEC)).metrics
    assert m["http_req_failed.rate"] == 1 and not any(k.startswith("http_req_failed.thresholds") for k in m)
