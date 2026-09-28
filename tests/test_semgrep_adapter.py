"""Adapter Semgrep: đếm theo mức, không phán; mọi ca "không quét đủ" là error chứ không phải xanh."""
import copy
import hashlib
import json
import random
import shlex

import pytest

from qc_agent import oracle
from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.semgrep_adapter import OUT_NAME, PARSER_VERSION, SemgrepAdapter
from securitykit import completed, fixture_json, fixture_text, make_spec, run_with_fake_tool

ASSERTIONS = [{"metric": "semgrep.high", "op": "==", "value": 0}, {"metric": "semgrep.critical", "op": "==", "value": 0},
              {"metric": "semgrep.files_scanned", "op": ">=", "value": 1}]   # như tmpl/sast.yaml.tmpl
LEVELS = ("critical", "high", "medium", "low")


@pytest.fixture
def rules(tmp_path):
    directory = tmp_path / "rules"
    directory.mkdir()
    (directory / "python.yaml").write_text("rules: []\n", encoding="utf-8")
    return directory


@pytest.fixture
def spec(rules):
    return make_spec("code.sast", {"rules_dir": str(rules), "paths": ["."], "exclude": ["node_modules", "tests"]}, ASSERTIONS)


def parse(tmp_path, spec, data, *, returncode=0):
    """Ghi `data` (dict -> JSON, str -> nguyên văn) làm semgrep.json rồi parse."""
    (tmp_path / OUT_NAME).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return SemgrepAdapter().parse_output(completed(returncode, args=["semgrep"]), tmp_path, spec)


# ---------- build_cmd ----------

def test_build_cmd_flags_and_output_path(tmp_path, spec, rules):
    cmd = SemgrepAdapter().build_cmd(spec, tmp_path)
    assert cmd[:3] == ["semgrep", "scan", "--json"]
    assert f"--output={(tmp_path / OUT_NAME).resolve()}" in cmd
    for flag in ("--metrics=off", "--error=false", "--disable-version-check"):
        assert flag in cmd
    assert cmd[cmd.index("--config") + 1] == str(rules)
    assert "--exclude=node_modules" in cmd and "--exclude=tests" in cmd
    assert cmd[-1] == "."


def test_build_cmd_removes_a_stale_report(tmp_path, spec):
    (tmp_path / OUT_NAME).write_text("cũ", encoding="utf-8")
    SemgrepAdapter().build_cmd(spec, tmp_path)
    assert not (tmp_path / OUT_NAME).exists()


def test_paths_default_to_the_whole_tree(tmp_path, rules):
    cmd = SemgrepAdapter().build_cmd(make_spec("code.sast", {"rules_dir": str(rules)}, ASSERTIONS), tmp_path)
    assert cmd[-1] == "."


@pytest.mark.parametrize("bad", ["../../etc", "a/../../b", "/etc/passwd", "C:\\Windows", "\\\\host\\share", "--config=evil", "-x", "a\nb", "", "  "])
def test_hostile_paths_are_rejected(tmp_path, rules, bad):
    with pytest.raises(AdapterParseError):
        SemgrepAdapter().build_cmd(make_spec("code.sast", {"rules_dir": str(rules), "paths": [bad]}, ASSERTIONS), tmp_path)


def test_odd_but_legal_characters_stay_one_argv_element(tmp_path, rules):
    """Không qua shell: `;`, khoảng trắng, `$()` chỉ là ký tự trong MỘT phần tử argv."""
    odd = "src dir/a; rm -rf $(id)"
    cmd = SemgrepAdapter().build_cmd(make_spec("code.sast", {"rules_dir": str(rules), "paths": [odd], "exclude": ["*.min.js; x"]}, ASSERTIONS), tmp_path)
    assert odd in cmd and "--exclude=*.min.js; x" in cmd
    assert cmd.count(odd) == 1 and cmd[-1] == odd


@pytest.mark.parametrize("inputs", [{"paths": "src"}, {"paths": [1]}, {"exclude": "node_modules"}, {"exclude": [""]}, {"exclude": ["a\x00b"]}])
def test_malformed_lists_are_rejected(tmp_path, rules, inputs):
    with pytest.raises(AdapterParseError):
        SemgrepAdapter().build_cmd(make_spec("code.sast", {"rules_dir": str(rules), **inputs}, ASSERTIONS), tmp_path)


def test_rules_dir_must_hold_rules(tmp_path, rules):
    """Thư mục rule rỗng/không có ⇒ 0 finding vì không có luật nào: phải là error, không phải xanh."""
    empty = tmp_path / "empty"
    empty.mkdir()
    for bad in (str(empty), str(tmp_path / "missing"), "-config", "", None, 5):
        with pytest.raises(AdapterParseError):
            SemgrepAdapter().build_cmd(make_spec("code.sast", {"rules_dir": bad}, ASSERTIONS), tmp_path)


# ---------- parse_output ----------

def test_sample_counts_by_level_and_puts_the_location_in_the_title(tmp_path, spec):
    parsed = parse(tmp_path, spec, fixture_json("semgrep-sample.json"))
    assert parsed.metrics == {"semgrep.critical": 0, "semgrep.high": 1, "semgrep.medium": 1, "semgrep.low": 1, "semgrep.total": 3, "semgrep.files_scanned": 3}
    by_title = {f["title"]: f for f in parsed.findings}
    high = by_title["qc-rules.python-subprocess-shell-true @ apps/api-server/app/api/jobs.py:42"]
    assert high["severity_hint"] == "high" and high["detected_by"] == "semgrep" and high["verdict_source"] == "deterministic_assert" and high["confidence"] is None
    assert {f["severity_hint"] for f in parsed.findings} == {"high", "medium", "low"}
    assert parsed.tokens == 0 and parsed.usd == 0.0 and [k for k, _ in parsed.evidence_paths] == ["raw_output", "stdout"]
    assert f"PARSER_VERSION={PARSER_VERSION}" in parsed.adapter_notes


def test_adapter_counts_and_the_oracle_decides(tmp_path, spec):
    """Adapter không phán: cùng metric, suite khác ngưỡng ⇒ verdict khác."""
    metrics = parse(tmp_path, spec, fixture_json("semgrep-sample.json")).metrics
    assert oracle.evaluate(spec["oracle"], metrics, {}).value == "fail"
    lenient = {"kind": "threshold", "assertions": [{"metric": "semgrep.high", "op": "<=", "value": 1}]}
    assert oracle.evaluate(lenient, metrics, {}).value == "pass"


def test_clean_scan_has_every_metric_present_and_zero(tmp_path, spec):
    parsed = parse(tmp_path, spec, fixture_json("semgrep-empty.json"))
    assert parsed.findings == [] and all(parsed.metrics[f"semgrep.{lv}"] == 0 for lv in LEVELS) and parsed.metrics["semgrep.total"] == 0
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "pass"     # oracle cần đủ key: thiếu key là OracleError


def test_critical_is_counted_separately_and_hinted_high(tmp_path, spec):
    data = fixture_json("semgrep-sample.json")
    data["results"][0]["extra"]["severity"] = "CRITICAL"
    parsed = parse(tmp_path, spec, data)
    assert parsed.metrics["semgrep.critical"] == 1 and parsed.metrics["semgrep.high"] == 0
    assert next(f for f in parsed.findings if "jobs.py:42" in f["title"])["severity_hint"] == "high"     # schema không có `critical`
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "fail"      # suite phải chặn cả critical: high == 0 một mình sẽ bỏ lọt


def test_zero_files_scanned_is_a_measurement_not_a_crash(tmp_path, spec):
    data = fixture_json("semgrep-empty.json")
    data["paths"]["scanned"] = []
    parsed = parse(tmp_path, spec, data)
    assert parsed.metrics["semgrep.files_scanned"] == 0
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "fail"      # "không quét gì" bị suite chặn, không im lặng xanh


def test_errors_array_is_never_green(tmp_path, spec):
    data = fixture_json("semgrep-empty.json")
    data["errors"] = [{"code": 3, "level": "warn", "type": "PartialParsing", "message": "không parse được a.py"}]
    with pytest.raises(AdapterParseError, match="1 lỗi"):
        parse(tmp_path, spec, data)


@pytest.mark.parametrize("mutate, match", [
    (lambda d: d.pop("results"), "results"),
    (lambda d: d.update(results={}), "results"),
    (lambda d: d.update(errors="x"), "errors"),
    (lambda d: d.pop("paths"), "paths.scanned"),
    (lambda d: d.update(paths={"scanned": "a.py"}), "paths.scanned"),
    (lambda d: d["results"][0]["extra"].update(severity="BOGUS"), "severity"),
    (lambda d: d["results"][0]["extra"].pop("severity"), "thiếu"),
    (lambda d: d["results"][0].pop("check_id"), "thiếu"),
    (lambda d: d["results"][0]["start"].update(line="42"), "thiếu"),
])
def test_malformed_report_is_an_adapter_error(tmp_path, spec, mutate, match):
    data = copy.deepcopy(fixture_json("semgrep-sample.json"))
    mutate(data)
    with pytest.raises(AdapterParseError, match=match):
        parse(tmp_path, spec, data)


def test_truncated_or_missing_report_is_an_adapter_error(tmp_path, spec):
    with pytest.raises(AdapterParseError, match="không ghi ra file JSON"):
        SemgrepAdapter().parse_output(completed(), tmp_path, spec)
    with pytest.raises(AdapterParseError, match="không đọc được"):
        parse(tmp_path, spec, fixture_text("semgrep-sample.json")[:120])
    with pytest.raises(AdapterParseError, match="results"):
        parse(tmp_path, spec, "[]")


def test_nonzero_exit_is_an_adapter_error_even_with_a_report(tmp_path, spec):
    with pytest.raises(AdapterParseError, match="exit code 137"):
        parse(tmp_path, spec, fixture_json("semgrep-empty.json"), returncode=137)


def test_code_snippets_never_reach_findings_or_metrics(tmp_path, spec):
    data = fixture_json("semgrep-sample.json")
    marker = "subprocess.run(cmd, shell=True)  # SNIPPET-MARKER"
    data["results"][0]["extra"]["lines"] = marker
    data["results"][0]["extra"]["message"] = marker
    parsed = parse(tmp_path, spec, data)
    assert "SNIPPET-MARKER" not in json.dumps([parsed.findings, parsed.metrics, parsed.adapter_notes])


# ---------- finding_id ----------

def test_finding_id_is_stable_unique_and_hashes_rule_plus_location(tmp_path, spec):
    first = [f["finding_id"] for f in parse(tmp_path, spec, fixture_json("semgrep-sample.json")).findings]
    second = [f["finding_id"] for f in parse(tmp_path, spec, fixture_json("semgrep-sample.json")).findings]
    assert first == second and len(set(first)) == len(first)
    expected = "f-semgrep-" + hashlib.sha1(b"qc-rules.python-subprocess-shell-true" + b"apps/api-server/app/api/jobs.py:42").hexdigest()[:12]
    assert expected in first


def test_finding_id_is_independent_of_report_order_and_dedupes_same_line(tmp_path, spec):
    data = fixture_json("semgrep-sample.json")
    twin = copy.deepcopy(data["results"][0])      # cùng luật, cùng dòng, khác cột
    twin["start"]["col"] = 30
    data["results"].append(twin)
    ordered = lambda d: [(f["finding_id"], f["title"]) for f in parse(tmp_path, spec, d).findings]     # CÓ thứ tự: cắt 200 finding/comment PR phải tất định
    base = ordered(data)
    assert len({i for i, _ in base}) == 4 and any(i.endswith("-2") for i, _ in base)
    for seed in range(5):
        shuffled = copy.deepcopy(data)
        random.Random(seed).shuffle(shuffled["results"])
        assert ordered(shuffled) == base


def test_many_findings_are_capped_but_still_counted(tmp_path, spec):
    data = fixture_json("semgrep-empty.json")
    template = fixture_json("semgrep-sample.json")["results"][0]
    for i in range(250):
        item = copy.deepcopy(template)
        item["start"]["line"] = i + 1
        data["results"].append(item)
    parsed = parse(tmp_path, spec, data)
    assert parsed.metrics["semgrep.high"] == 250 and len(parsed.findings) == 200 and any("cắt" in n for n in parsed.adapter_notes)


def test_replay_cmd_is_the_command_that_ran(tmp_path, spec):
    cmd = SemgrepAdapter().build_cmd(spec, tmp_path)
    (tmp_path / OUT_NAME).write_text(fixture_text("semgrep-empty.json"), encoding="utf-8")
    parsed = SemgrepAdapter().parse_output(completed(args=cmd), tmp_path, spec)
    assert parsed.replay_cmd == shlex.join(cmd)


# ---------- cả vòng đời (công cụ giả) ----------

def test_full_run_fails_with_a_located_finding_and_a_contract_valid_result(tmp_path, spec, monkeypatch):
    result = run_with_fake_tool(SemgrepAdapter(), spec, monkeypatch, tmp_path, report=fixture_text("semgrep-sample.json"))
    assert result["status"] == "fail" and result["verdict"]["gating"] is True and result["verdict"]["value"] == "fail"
    titles = [f["title"] for f in result["findings"]]
    assert "qc-rules.python-subprocess-shell-true @ apps/api-server/app/api/jobs.py:42" in titles
    assert any(t.startswith("semgrep.high = 1") for t in titles)      # finding của oracle
    assert result["metrics"]["semgrep.high"] == 1 and {e["kind"] for e in result["evidence"]} == {"raw_output", "stdout"}


def test_full_run_passes_on_a_clean_tree(tmp_path, spec, monkeypatch):
    result = run_with_fake_tool(SemgrepAdapter(), spec, monkeypatch, tmp_path, report=fixture_text("semgrep-empty.json"))
    assert result["status"] == "pass" and result["findings"] == []


@pytest.mark.parametrize("kwargs, expected", [
    (dict(report=None, returncode=137), "parse: semgrep kết thúc với exit code 137"),     # công cụ chết, không ghi file
    (dict(report=None), "parse: semgrep không ghi ra file JSON"),
    (dict(report='{"results": [], "errors": [{"x": 1}], "paths": {"scanned": ["a.py"]}}'), "parse: semgrep báo 1 lỗi"),
    (dict(report='{"results": [], "erro'), "parse: semgrep.json không đọc được"),          # JSON cụt
])
def test_full_run_never_turns_a_broken_scan_into_pass(tmp_path, spec, monkeypatch, kwargs, expected):
    result = run_with_fake_tool(SemgrepAdapter(), spec, monkeypatch, tmp_path, **kwargs)
    assert result["status"] == "error" and result["verdict"]["gating"] is False
    assert expected in result["verdict"]["rationale"]
