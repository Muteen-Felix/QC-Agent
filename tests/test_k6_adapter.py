import copy
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import oracle
from adapters._base import AdapterParseError
from adapters.k6_adapter import PARSER_VERSION, SUMMARY_NAME, K6Adapter


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

    assert parsed.metrics == {"http_req_duration.p95": 5.56, "http_req_failed.rate": 0}
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
