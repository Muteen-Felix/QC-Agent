"""Adapter Trivy: đếm CVE theo mức + đo tuổi DB; ba bẫy xanh giả (DB hỏng, DB cũ, không có lockfile) đều có test."""
import copy
import hashlib
import json
import random
import shutil
from datetime import datetime, timezone

import pytest

from qc_agent import oracle
from qc_agent.adapters import trivy_adapter
from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.trivy_adapter import OUT_NAME, TrivyAdapter
from securitykit import FIX, completed, fixture_json, fixture_text, make_spec, run_with_fake_tool

ASSERTIONS = [{"metric": "trivy.critical", "op": "==", "value": 0}, {"metric": "trivy.high", "op": "==", "value": 0},
              {"metric": "trivy.db_age_days", "op": "<=", "value": 14, "unit": "days"}]     # như tmpl/deps.yaml.tmpl
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)      # DB fixture thật: UpdatedAt 2026-09-28T13:05:44 => 2 ngày 19h54 => làm tròn lên 3


@pytest.fixture
def cache(tmp_path):
    directory = tmp_path / "trivy-cache"
    (directory / "db").mkdir(parents=True)
    shutil.copy(FIX / "trivy-db-metadata.json", directory / "db" / "metadata.json")
    return directory


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(trivy_adapter, "_now", lambda: NOW)


@pytest.fixture
def spec(cache):
    return make_spec("deps.vuln", {"path": ".", "cache_dir": str(cache)}, ASSERTIONS, task_id="t-012")


def parse(tmp_path, spec, data, *, returncode=0):
    (tmp_path / OUT_NAME).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return TrivyAdapter().parse_output(completed(returncode, args=["trivy"]), tmp_path, spec)


def set_metadata(cache, content):
    (cache / "db" / "metadata.json").write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


# ---------- build_cmd ----------

def test_build_cmd_is_offline_json_and_exit_code_zero(tmp_path, spec, cache):
    cmd = TrivyAdapter().build_cmd(spec, tmp_path)
    assert cmd == ["trivy", "fs", "--scanners", "vuln", "--skip-db-update", "--offline-scan", "--cache-dir", str(cache), "--format", "json",
                   "--output", str((tmp_path / OUT_NAME).resolve()), "--exit-code", "0", "."]


def test_defaults_use_the_baked_cache_dir(tmp_path):
    cmd = TrivyAdapter().build_cmd(make_spec("deps.vuln", {}, ASSERTIONS), tmp_path)
    assert cmd[cmd.index("--cache-dir") + 1] == "/opt/trivy-cache" and cmd[-1] == "."


@pytest.mark.parametrize("inputs", [{"path": "../.."}, {"path": "/etc"}, {"path": "--config=evil"}, {"path": ""}, {"path": 1},
                                    {"cache_dir": "-x"}, {"cache_dir": ""}, {"cache_dir": "a\nb"}, {"cache_dir": 2}])
def test_bad_inputs_are_rejected(tmp_path, inputs):
    with pytest.raises(AdapterParseError):
        TrivyAdapter().build_cmd(make_spec("deps.vuln", inputs, ASSERTIONS), tmp_path)


# ---------- đếm ----------

def test_sample_counts_by_level_and_measures_db_age(tmp_path, spec):
    parsed = parse(tmp_path, spec, fixture_json("trivy-sample.json"))
    assert parsed.metrics == {"trivy.critical": 3, "trivy.high": 2, "trivy.medium": 4, "trivy.low": 0, "trivy.unknown": 0,
                              "trivy.total": 9, "trivy.targets": 2, "trivy.db_age_days": 3}
    hints = {f["title"]: f["severity_hint"] for f in parsed.findings}
    assert hints["CVE-2021-44906 minimist@0.0.8 @ package-lock.json"] == "high"      # CRITICAL -> high (schema không có critical)
    assert hints["CVE-2020-14343 PyYAML@5.3 @ requirements.txt"] == "high"           # CRITICAL ở target thứ hai
    assert hints["CVE-2021-23337 lodash@4.17.20 @ package-lock.json"] == "high"      # HIGH thật
    assert hints["CVE-2020-28500 lodash@4.17.20 @ package-lock.json"] == "medium"
    assert all(f["detected_by"] == "trivy" and f["verdict_source"] == "deterministic_assert" for f in parsed.findings)
    assert parsed.tokens == 0 and [k for k, _ in parsed.evidence_paths] == ["raw_output", "stdout"]
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "fail"


def test_low_and_unknown_severities_are_measured_and_hinted_correctly(tmp_path, spec):
    """Bản ghi thật (lodash/minimist/PyYAML) không tự nhiên có CVE mức LOW/UNKNOWN: kiểm hai mức này bằng cách mutate một finding thật."""
    data = fixture_json("trivy-sample.json")
    data["Results"][0]["Vulnerabilities"][2]["Severity"] = "LOW"       # CVE-2020-28500 (lodash)
    data["Results"][1]["Vulnerabilities"][0]["Severity"] = "UNKNOWN"   # CVE-2020-14343 (PyYAML)
    parsed = parse(tmp_path, spec, data)
    hints = {f["title"]: f["severity_hint"] for f in parsed.findings}
    assert hints["CVE-2020-28500 lodash@4.17.20 @ package-lock.json"] == "low"
    assert hints["CVE-2020-14343 PyYAML@5.3 @ requirements.txt"] is None             # UNKNOWN: không bịa mức
    assert parsed.metrics["trivy.low"] == 1 and parsed.metrics["trivy.unknown"] == 1


def test_clean_scan_of_real_lockfiles_is_zero_cve_and_green(tmp_path, spec):
    parsed = parse(tmp_path, spec, fixture_json("trivy-empty.json"))
    assert parsed.findings == [] and parsed.metrics["trivy.total"] == 0 and parsed.metrics["trivy.targets"] == 1
    assert all(parsed.metrics[f"trivy.{lv}"] == 0 for lv in ("critical", "high", "medium", "low", "unknown"))
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "pass"


def test_null_vulnerabilities_key_is_fine(tmp_path, spec):
    data = fixture_json("trivy-empty.json")
    data["Results"][0]["Vulnerabilities"] = None
    assert parse(tmp_path, spec, data).metrics["trivy.total"] == 0


# ---------- bẫy 1: DB thiếu/hỏng ----------

@pytest.mark.parametrize("content", ['{"Version": 2}', '{"UpdatedAt": 5}', '{"UpdatedAt": "hôm qua"}', '{"UpdatedAt": "2026-09-18T08:30:00"}',     # thiếu/sai/thiếu múi giờ
                                     '{"UpdatedAt": "2026-09-18T08:', "[]", "null"])
def test_unreadable_db_version_is_an_error_not_a_clean_scan(tmp_path, spec, cache, content):
    set_metadata(cache, content)
    with pytest.raises(AdapterParseError):
        parse(tmp_path, spec, fixture_json("trivy-empty.json"))       # báo cáo "0 CVE" nhưng không biết DB nào: không có quyền nói sạch


def test_missing_db_is_an_error(tmp_path, spec, cache):
    (cache / "db" / "metadata.json").unlink()
    with pytest.raises(AdapterParseError, match="thiếu DB CVE nướng sẵn"):
        parse(tmp_path, spec, fixture_json("trivy-empty.json"))


def test_db_timestamp_with_nanoseconds_and_offset_parses(tmp_path, spec, cache):
    set_metadata(cache, {"UpdatedAt": "2026-10-01T02:00:00.123456789+07:00"})       # = 2026-09-30T19:00Z: 14 giờ trước NOW => làm tròn lên 1 ngày
    assert parse(tmp_path, spec, fixture_json("trivy-empty.json")).metrics["trivy.db_age_days"] == 1


# ---------- bẫy 2: DB cũ là phép đo hợp lệ, oracle phán ----------

def test_stale_db_is_a_measurement_that_fails_the_oracle_not_an_error(tmp_path, spec, monkeypatch):
    monkeypatch.setattr(trivy_adapter, "_now", lambda: datetime(2026, 10, 20, tzinfo=timezone.utc))      # DB ~22 ngày tuổi (UpdatedAt thật: 2026-09-28)
    parsed = parse(tmp_path, spec, fixture_json("trivy-empty.json"))
    assert parsed.metrics["trivy.db_age_days"] == 22 and parsed.metrics["trivy.total"] == 0      # không CVE, nhưng dữ liệu cũ
    outcome = oracle.evaluate(spec["oracle"], parsed.metrics, {})
    assert outcome.value == "fail" and any("trivy.db_age_days = 22" in f["title"] for f in outcome.findings)


@pytest.mark.parametrize("now, age, verdict", [(datetime(2026, 10, 2, 8, 30, tzinfo=timezone.utc), 14, "pass"),       # đúng 14 ngày: còn trong ngưỡng
                                               (datetime(2026, 10, 2, 8, 31, tzinfo=timezone.utc), 15, "fail")])      # quá 1 phút: làm tròn lên => vượt
def test_age_threshold_boundary_is_exact(tmp_path, spec, cache, monkeypatch, now, age, verdict):
    set_metadata(cache, {"UpdatedAt": "2026-09-18T08:30:00Z"})
    monkeypatch.setattr(trivy_adapter, "_now", lambda: now)
    metrics = parse(tmp_path, spec, fixture_json("trivy-empty.json")).metrics
    assert metrics["trivy.db_age_days"] == age and oracle.evaluate(spec["oracle"], metrics, {}).value == verdict


def test_db_in_the_future_clamps_to_zero(tmp_path, spec, monkeypatch):
    monkeypatch.setattr(trivy_adapter, "_now", lambda: datetime(2026, 9, 1, tzinfo=timezone.utc))    # đồng hồ máy lệch
    assert parse(tmp_path, spec, fixture_json("trivy-empty.json")).metrics["trivy.db_age_days"] == 0


# ---------- bẫy 3: không có lockfile/manifest ----------

@pytest.mark.parametrize("mutate", [lambda d: d.pop("Results"), lambda d: d.update(Results=[]), lambda d: d.update(Results=None), lambda d: d.update(Results="x")])
def test_no_targets_is_an_error_not_zero_cve(tmp_path, spec, mutate):
    data = {"SchemaVersion": 2, "ArtifactName": ".", "ArtifactType": "filesystem", "Results": [{"Target": "x"}]}
    mutate(data)
    with pytest.raises(AdapterParseError, match="không tìm thấy manifest/lockfile"):
        parse(tmp_path, spec, data)


# ---------- báo cáo hỏng ----------

@pytest.mark.parametrize("mutate, match", [
    (lambda d: d["Results"][0]["Vulnerabilities"][0].update(Severity="BOGUS"), "Severity"),
    (lambda d: d["Results"][0]["Vulnerabilities"][0].update(Severity=None), "Severity"),
    (lambda d: d["Results"][0]["Vulnerabilities"][0].pop("VulnerabilityID"), "thiếu"),
    (lambda d: d["Results"][0].update(Vulnerabilities="x"), "không phải danh sách"),
    (lambda d: d["Results"][0].pop("Target"), "Target"),
    (lambda d: d.update(Results=[5]), "Target"),
])
def test_malformed_report_is_an_error(tmp_path, spec, mutate, match):
    data = copy.deepcopy(fixture_json("trivy-sample.json"))
    mutate(data)
    with pytest.raises(AdapterParseError, match=match):
        parse(tmp_path, spec, data)


def test_missing_truncated_or_nonobject_report_and_bad_exit_are_errors(tmp_path, spec):
    with pytest.raises(AdapterParseError, match="không ghi ra file JSON"):
        TrivyAdapter().parse_output(completed(), tmp_path, spec)
    with pytest.raises(AdapterParseError, match="không đọc được"):
        parse(tmp_path, spec, fixture_text("trivy-sample.json")[:100])
    with pytest.raises(AdapterParseError, match="không phải object"):
        parse(tmp_path, spec, "[]")
    with pytest.raises(AdapterParseError, match="exit code 2"):
        parse(tmp_path, spec, fixture_json("trivy-empty.json"), returncode=2)


# ---------- finding_id ----------

def test_finding_id_is_stable_unique_and_distinguishes_lockfiles(tmp_path, spec):
    first = [f["finding_id"] for f in parse(tmp_path, spec, fixture_json("trivy-sample.json")).findings]
    second = [f["finding_id"] for f in parse(tmp_path, spec, fixture_json("trivy-sample.json")).findings]
    assert first == second and len(set(first)) == 9
    assert "f-trivy-" + hashlib.sha1(b"CVE-2021-23337|lodash|4.17.20|package-lock.json").hexdigest()[:12] in first


def test_finding_id_ignores_report_order(tmp_path, spec):
    data = fixture_json("trivy-sample.json")
    ordered = lambda d: [(f["finding_id"], f["title"]) for f in parse(tmp_path, spec, d).findings]
    base = ordered(data)
    for seed in range(5):
        shuffled = copy.deepcopy(data)
        random.Random(seed).shuffle(shuffled["Results"])
        for result in shuffled["Results"]:
            random.Random(seed).shuffle(result["Vulnerabilities"])
        assert ordered(shuffled) == base


# ---------- cả vòng đời (công cụ giả) ----------

def test_full_run_fail_pass_and_stale_db(tmp_path, spec, monkeypatch):
    failed = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "a", report=fixture_text("trivy-sample.json"))
    assert failed["status"] == "fail" and failed["verdict"]["gating"] is True and failed["metrics"]["trivy.critical"] == 3
    passed = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "b", report=fixture_text("trivy-empty.json"))
    assert passed["status"] == "pass" and passed["metrics"]["trivy.db_age_days"] == 3
    monkeypatch.setattr(trivy_adapter, "_now", lambda: datetime(2027, 1, 1, tzinfo=timezone.utc))
    stale = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "c", report=fixture_text("trivy-empty.json"))
    assert stale["status"] == "fail" and any("trivy.db_age_days" in f["title"] for f in stale["findings"])     # đỏ vì dữ liệu cũ, KHÔNG phải error


@pytest.mark.parametrize("kwargs, expected", [
    (dict(report=None, returncode=137), "parse: trivy kết thúc với exit code 137"),
    (dict(report=None), "parse: trivy không ghi ra file JSON"),
    (dict(report='{"Results": [{"Target": "x"}]', ), "parse: trivy.json không đọc được"),
    (dict(report='{"SchemaVersion": 2}'), "parse: trivy không tìm thấy manifest/lockfile"),
])
def test_full_run_never_turns_a_broken_scan_into_pass(tmp_path, spec, monkeypatch, kwargs, expected):
    result = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path, **kwargs)
    assert result["status"] == "error" and expected in result["verdict"]["rationale"]


def test_full_run_with_a_missing_db_is_an_error(tmp_path, spec, cache, monkeypatch):
    (cache / "db" / "metadata.json").unlink()
    result = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path, report=fixture_text("trivy-empty.json"))
    assert result["status"] == "error" and "thiếu DB CVE nướng sẵn" in result["verdict"]["rationale"]
