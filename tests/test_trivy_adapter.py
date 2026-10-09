"""Adapter Trivy: đếm CVE theo mức + đo tuổi DB; ba bẫy xanh giả (DB hỏng, DB cũ, không có lockfile) đều có test."""
import copy
import hashlib
import json
import os
import random
import shutil
import subprocess
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
    assert hints["CVE-2021-44906 minimist@0.0.8 @ package-lock.json"] == "critical"
    assert hints["CVE-2020-14343 PyYAML@5.3 @ requirements.txt"] == "critical"
    assert hints["CVE-2021-23337 lodash@4.17.20 @ package-lock.json"] == "critical"
    assert hints["CVE-2020-28500 lodash@4.17.20 @ package-lock.json"] == "medium"
    assert all(f["detected_by"].startswith("trivy:") and f["location"]["path"] and f["verdict_source"] == "deterministic_assert" for f in parsed.findings)
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


# ---------- bẫy 3, ngoại lệ có chủ đích: SUT không có dependency (opt-in gắn với manifest đã khai báo) ----------

# Đúng như Trivy 0.74 in cho lockfile hợp lệ 0 dependency VÀ cho repo không có lockfile (không phân biệt được hai ca này).
NO_RESULTS = {"SchemaVersion": 2, "ArtifactName": ".", "ArtifactType": "filesystem"}


@pytest.fixture
def sut(tmp_path, monkeypatch):
    """Gốc SUT giả: adapter chạy với cwd = gốc SUT."""
    root = tmp_path / "sut"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def opt_in_spec(cache, manifests=("package-lock.json",), **inputs):
    return make_spec("deps.vuln", {"path": ".", "cache_dir": str(cache), "allow_no_dependencies": True, "expected_manifests": list(manifests), **inputs},
                     ASSERTIONS, task_id="t-012")


def test_declared_manifest_without_dependencies_passes_with_zero_targets_and_a_hash(tmp_path, sut, cache):
    (sut / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {"": {}}}', encoding="utf-8")
    parsed = parse(tmp_path, opt_in_spec(cache), NO_RESULTS)
    assert parsed.metrics["trivy.targets"] == 0 and parsed.metrics["trivy.total"] == 0 and parsed.metrics["trivy.db_age_days"] == 3
    digest = hashlib.sha256((sut / "package-lock.json").read_bytes()).hexdigest()
    assert any("declared_manifests" in n and f"package-lock.json sha256={digest}" in n for n in parsed.adapter_notes)
    assert parsed.findings == []


def test_declared_manifest_run_is_green_end_to_end_and_hash_follows_the_content(tmp_path, sut, cache, monkeypatch):
    spec = opt_in_spec(cache)
    (sut / "package-lock.json").write_text("{}", encoding="utf-8")
    first = parse(tmp_path, spec, NO_RESULTS).adapter_notes
    (sut / "package-lock.json").write_text('{"changed": true}', encoding="utf-8")
    assert parse(tmp_path, spec, NO_RESULTS).adapter_notes != first      # đổi nội dung lockfile hiện trong report
    result = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "run", report=json.dumps(NO_RESULTS))
    assert result["status"] == "pass" and result["metrics"]["trivy.targets"] == 0


def test_declared_manifest_that_was_deleted_is_an_error_even_with_the_opt_in_still_present(tmp_path, sut, cache, monkeypatch):
    spec = opt_in_spec(cache)                     # PR xoá package-lock.json nhưng không đụng file suite
    with pytest.raises(AdapterParseError, match="package-lock.json"):
        parse(tmp_path, spec, NO_RESULTS)
    result = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "run", report=json.dumps(NO_RESULTS))
    assert result["status"] == "error" and "package-lock.json" in result["verdict"]["rationale"]


def test_one_missing_manifest_among_several_is_an_error(tmp_path, sut, cache):
    (sut / "package-lock.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AdapterParseError, match="uv.lock"):
        parse(tmp_path, opt_in_spec(cache, ["package-lock.json", "uv.lock"]), NO_RESULTS)


def test_without_the_opt_in_empty_results_stay_an_error_even_when_the_manifest_exists(tmp_path, sut, spec, monkeypatch):
    (sut / "package-lock.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AdapterParseError, match="không tìm thấy manifest/lockfile"):
        parse(tmp_path, spec, NO_RESULTS)
    result = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "run", report=json.dumps(NO_RESULTS))
    assert result["status"] == "error"


def test_the_opt_in_does_not_swallow_real_findings(tmp_path, sut, cache):
    (sut / "package-lock.json").write_text("{}", encoding="utf-8")
    parsed = parse(tmp_path, opt_in_spec(cache), fixture_json("trivy-sample.json"))      # Trivy thấy dependency khác: đếm bình thường
    assert parsed.metrics["trivy.critical"] == 3 and parsed.metrics["trivy.targets"] >= 1


def test_a_missing_declared_manifest_is_an_error_even_when_trivy_returns_results_from_another_manifest(tmp_path, sut, cache, monkeypatch):
    (sut / "uv.lock").write_text("version = 1", encoding="utf-8")          # manifest thứ hai còn, Trivy vẫn báo CVE từ file khác
    spec = opt_in_spec(cache, ["uv.lock", "package-lock.json"])               # package-lock.json đã bị xoá
    with pytest.raises(AdapterParseError, match="package-lock.json"):
        parse(tmp_path, spec, fixture_json("trivy-sample.json"))
    result = run_with_fake_tool(TrivyAdapter(), spec, monkeypatch, tmp_path / "run", report=fixture_text("trivy-sample.json"))
    assert result["status"] == "error" and "package-lock.json" in result["verdict"]["rationale"]


@pytest.mark.parametrize("make_bad", [lambda sut: (sut / "package-lock.json").mkdir(), lambda sut: None])
def test_declared_manifest_state_is_checked_with_non_empty_results_too(tmp_path, sut, cache, make_bad):
    make_bad(sut)                                                             # thư mục hoặc không tồn tại
    with pytest.raises(AdapterParseError, match="package-lock.json"):
        parse(tmp_path, opt_in_spec(cache), fixture_json("trivy-sample.json"))


@pytest.mark.parametrize("results", ["x", {}, 0, False])
def test_the_opt_in_only_covers_a_missing_or_empty_list_not_a_malformed_report(tmp_path, sut, cache, results):
    (sut / "package-lock.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AdapterParseError, match="không tìm thấy manifest/lockfile"):
        parse(tmp_path, opt_in_spec(cache), {**NO_RESULTS, "Results": results})


def test_the_opt_in_with_a_broken_db_is_still_an_error(tmp_path, sut, cache):
    (sut / "package-lock.json").write_text("{}", encoding="utf-8")
    (cache / "db" / "metadata.json").unlink()
    with pytest.raises(AdapterParseError, match="thiếu DB CVE nướng sẵn"):
        parse(tmp_path, opt_in_spec(cache), NO_RESULTS)


MANIFEST = ["package-lock.json"]
BAD_DECLARATIONS = [
    {"allow_no_dependencies": "yes", "expected_manifests": MANIFEST},        # không phải bool
    {"allow_no_dependencies": 1, "expected_manifests": MANIFEST},
    {"allow_no_dependencies": True},                                         # thiếu danh sách
    {"allow_no_dependencies": True, "expected_manifests": []},
    {"allow_no_dependencies": True, "expected_manifests": "package-lock.json"},
    {"allow_no_dependencies": True, "expected_manifests": MANIFEST * 2},     # trùng
    {"allow_no_dependencies": True, "expected_manifests": [f"d{i}/package-lock.json" for i in range(21)]},     # quá 20
    {"expected_manifests": MANIFEST},                                        # có danh sách nhưng không opt-in
    {"allow_no_dependencies": False, "expected_manifests": MANIFEST},
    {"allow_no_dependencies": True, "expected_manifests": ["/etc/package-lock.json"]},
    {"allow_no_dependencies": True, "expected_manifests": ["../package-lock.json"]},
    {"allow_no_dependencies": True, "expected_manifests": ["-x/package-lock.json"]},
    {"allow_no_dependencies": True, "expected_manifests": [""]},
    {"allow_no_dependencies": True, "expected_manifests": [5]},
    {"allow_no_dependencies": True, "expected_manifests": ["."]},
    {"allow_no_dependencies": True, "expected_manifests": ["README.md"]},    # không phải manifest
    {"allow_no_dependencies": True, "expected_manifests": ["requirements-dev.txt"]},      # Trivy 0.74 không nhận tên này
    {"allow_no_dependencies": True, "expected_manifests": ["Package-Lock.json"]},         # đúng chữ hoa/thường
    {"allow_no_dependencies": True, "path": "apps/api", "expected_manifests": MANIFEST},                  # ngoài inputs.path
    {"allow_no_dependencies": True, "path": "apps/api", "expected_manifests": ["apps/other/uv.lock"]},
    {"allow_no_dependencies": True, "path": "apps/api", "expected_manifests": ["apps/api"]},               # chính thư mục path
]


@pytest.mark.parametrize("inputs", BAD_DECLARATIONS)
def test_a_bad_declaration_is_an_error_before_and_after_running_trivy(tmp_path, sut, cache, inputs):
    spec = make_spec("deps.vuln", {"cache_dir": str(cache), **inputs}, ASSERTIONS, task_id="t-012")
    with pytest.raises(AdapterParseError):
        TrivyAdapter().build_cmd(spec, tmp_path)
    with pytest.raises(AdapterParseError):
        parse(tmp_path, spec, NO_RESULTS)


def test_a_declaration_inside_inputs_path_is_accepted(tmp_path, sut, cache):
    (sut / "apps" / "api").mkdir(parents=True)
    (sut / "apps" / "api" / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    spec = opt_in_spec(cache, ["apps/api/uv.lock"], path="apps/api")
    assert TrivyAdapter().build_cmd(spec, tmp_path)[-1] == "apps/api"
    assert parse(tmp_path, spec, NO_RESULTS).metrics["trivy.targets"] == 0


def test_a_declared_manifest_that_is_a_directory_is_an_error(tmp_path, sut, cache):
    (sut / "package-lock.json").mkdir()
    with pytest.raises(AdapterParseError, match="package-lock.json"):
        parse(tmp_path, opt_in_spec(cache), NO_RESULTS)


def link_dir(link, target):
    """Symlink thư mục; Windows không có quyền thì dùng junction (mklink /J, không cần quyền). Không tạo được thì skip."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        done = os.name == "nt" and subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0
        if not done:
            pytest.skip("không tạo được symlink/junction trên máy này")


def test_a_declared_manifest_reached_through_a_link_out_of_the_repo_is_an_error(tmp_path, sut, cache):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "package-lock.json").write_text("{}", encoding="utf-8")
    link_dir(sut / "linked", outside)          # tên khai báo nằm trong repo, nhưng file thật nằm NGOÀI gốc SUT
    with pytest.raises(AdapterParseError, match="linked/package-lock.json"):
        parse(tmp_path, opt_in_spec(cache, ["linked/package-lock.json"]), NO_RESULTS)


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
