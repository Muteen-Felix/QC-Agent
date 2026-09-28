"""Adapter gitleaks: chỉ đếm `gitleaks.count`; --redact là bắt buộc và adapter KHÔNG tin cờ mà kiểm lại từng finding (không rò secret ra result/evidence)."""
import copy
import hashlib
import json
import random

import pytest

from qc_agent import oracle
from qc_agent.adapters._base import AdapterParseError
from qc_agent.adapters.gitleaks_adapter import OUT_NAME, GitleaksAdapter
from securitykit import completed, fixture_json, fixture_text, make_spec, run_with_fake_tool

ASSERTIONS = [{"metric": "gitleaks.count", "op": "==", "value": 0}]
SECRET = "sk_live_FAKE0123456789abcdefSECRET"       # giả, dựng ngay trong test; KHÔNG nằm trong fixture nào


@pytest.fixture
def spec():
    return make_spec("code.secret", {"source": ".", "history": False}, ASSERTIONS, task_id="t-011")


def parse(tmp_path, spec, data, *, returncode=0, stderr=""):
    (tmp_path / OUT_NAME).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return GitleaksAdapter().parse_output(completed(returncode, stderr=stderr, args=["gitleaks"]), tmp_path, spec)


def leaky_report(**changes):
    """Báo cáo như khi --redact KHÔNG có hiệu lực: giá trị secret thật nằm trong Secret và Match."""
    entry = fixture_json("gitleaks-sample.json")[0]
    entry.update({"Secret": SECRET, "Match": f'API_KEY = "{SECRET}"', **changes})
    return [entry]


# ---------- build_cmd ----------

def test_build_cmd_is_redacted_working_tree_and_exit_code_zero(tmp_path, spec):
    cmd = GitleaksAdapter().build_cmd(spec, tmp_path)
    assert cmd[:2] == ["gitleaks", "detect"] and cmd[cmd.index("--source") + 1] == "."
    assert cmd[cmd.index("--report-format") + 1] == "json" and cmd[cmd.index("--report-path") + 1] == str((tmp_path / OUT_NAME).resolve())
    assert cmd[cmd.index("--exit-code") + 1] == "0"
    assert "--redact" in cmd and "--no-banner" in cmd and "--no-git" in cmd
    assert "--verbose" not in cmd and "-v" not in cmd          # verbose in finding (kèm secret) ra stdout


def test_history_true_drops_no_git(tmp_path):
    cmd = GitleaksAdapter().build_cmd(make_spec("code.secret", {"history": True}, ASSERTIONS), tmp_path)
    assert "--no-git" not in cmd and "--redact" in cmd          # lịch sử vẫn phải redact


def test_defaults_are_working_tree_only(tmp_path):
    cmd = GitleaksAdapter().build_cmd(make_spec("code.secret", {}, ASSERTIONS), tmp_path)
    assert cmd[cmd.index("--source") + 1] == "." and "--no-git" in cmd


@pytest.mark.parametrize("inputs", [{"source": "../.."}, {"source": "/"}, {"source": "--config=evil"}, {"source": ""}, {"source": 3},
                                    {"history": "false"}, {"history": 0}, {"history": None}])
def test_bad_inputs_are_rejected(tmp_path, inputs):
    with pytest.raises(AdapterParseError):
        GitleaksAdapter().build_cmd(make_spec("code.secret", inputs, ASSERTIONS), tmp_path)


# ---------- parse_output ----------

def test_sample_counts_and_locates_without_any_secret_field(tmp_path, spec):
    parsed = parse(tmp_path, spec, fixture_json("gitleaks-sample.json"))
    assert parsed.metrics == {"gitleaks.count": 2}                       # CHỈ một metric
    assert sorted(f["title"] for f in parsed.findings) == ["aws-access-token @ scripts/deploy.sh:5", "generic-api-key @ apps/api-server/app/config.py:12"]
    assert all(f["severity_hint"] == "high" and f["detected_by"] == "gitleaks" and f["verdict_source"] == "deterministic_assert" for f in parsed.findings)
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "fail"


@pytest.mark.parametrize("report", ["[]", "null"])     # Go marshal slice rỗng có thể ra null
def test_clean_report_counts_zero(tmp_path, spec, report):
    parsed = parse(tmp_path, spec, report)
    assert parsed.metrics == {"gitleaks.count": 0} and parsed.findings == []
    assert oracle.evaluate(spec["oracle"], parsed.metrics, {}).value == "pass"


def test_clean_scan_without_a_report_file_needs_the_tools_own_confirmation(tmp_path, spec):
    parsed = GitleaksAdapter().parse_output(completed(stderr="12:00AM INF no leaks found"), tmp_path, spec)
    assert parsed.metrics == {"gitleaks.count": 0} and (tmp_path / OUT_NAME).read_text(encoding="utf-8").strip() == "[]"     # raw_output vẫn có file
    assert any("no leaks found" in n for n in parsed.adapter_notes)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(AdapterParseError, match="không ghi báo cáo"):     # không có file và công cụ không xác nhận sạch = không chạy tới nơi
        GitleaksAdapter().parse_output(completed(stderr="panic: something broke"), other, spec)


def test_nonzero_exit_is_an_error_and_the_report_is_deleted(tmp_path, spec):
    with pytest.raises(AdapterParseError, match="exit code 1"):
        parse(tmp_path, spec, fixture_json("gitleaks-sample.json"), returncode=1)
    assert not (tmp_path / OUT_NAME).exists()


@pytest.mark.parametrize("data", ['{"a": 1}', '"text"', "[1, 2]", '[{"RuleID": "x"}]', '[{"RuleID": "x", "File": "f", "StartLine": "3"}]', '[{"RuleID": 1, "File": "f", "StartLine": 3}]'])
def test_malformed_report_is_an_error(tmp_path, spec, data):
    with pytest.raises(AdapterParseError):
        parse(tmp_path, spec, data)


def test_truncated_report_is_an_error(tmp_path, spec):
    with pytest.raises(AdapterParseError, match="không đọc được"):
        parse(tmp_path, spec, fixture_text("gitleaks-sample.json")[:80])


# ---------- BẢO VỆ CHỐNG RÒ SECRET ----------

@pytest.mark.parametrize("changes", [
    {},                                             # Secret và Match đều còn giá trị thật
    {"Secret": SECRET, "Match": "API_KEY = REDACTED"},     # chỉ Secret còn
    {"Secret": "REDACTED", "Match": f'API_KEY = "{SECRET}"'},   # chỉ Match còn (dòng chứa secret chưa được thay)
    {"Secret": ["x"], "Match": "REDACTED"},         # sai kiểu: không chứng minh được là đã redact
    {"Secret": "REDACTED", "Match": {"a": 1}},
])
def test_unredacted_secret_or_match_is_refused_and_leaves_no_trace(tmp_path, spec, changes):
    with pytest.raises(AdapterParseError, match="chưa được REDACTED") as caught:
        parse(tmp_path, spec, leaky_report(**changes))
    assert SECRET not in str(caught.value)                     # thông báo lỗi (sẽ vào rationale) không chứa giá trị
    assert not (tmp_path / OUT_NAME).exists()                  # báo cáo bị xoá: không nằm lại trong runs/ để bị upload làm artifact
    assert not any(SECRET in p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*") if p.is_file())


def test_one_unredacted_finding_among_clean_ones_taints_the_whole_report(tmp_path, spec):
    data = fixture_json("gitleaks-sample.json") + leaky_report()
    with pytest.raises(AdapterParseError):
        parse(tmp_path, spec, data)


def test_redacted_values_are_accepted_including_empty_ones(tmp_path, spec):
    data = fixture_json("gitleaks-sample.json")
    data[0].update(Secret="", Match="")
    data[1].pop("Secret"), data[1].pop("Match")
    assert parse(tmp_path, spec, data).metrics == {"gitleaks.count": 2}


def test_full_run_with_a_correctly_redacted_report_leaks_the_secret_nowhere(tmp_path, spec, monkeypatch):
    """Công cụ giả phát hiện SECRET và báo cáo ĐÚNG như --redact: giá trị không được xuất hiện ở result, findings, metrics, evidence, stdout."""
    report = json.dumps([{**fixture_json("gitleaks-sample.json")[0], "Match": f'API_KEY = "REDACTED"', "Secret": "REDACTED"}])
    result = run_with_fake_tool(GitleaksAdapter(), spec, monkeypatch, tmp_path, report=report, stdout="1 leak found\n")
    assert result["status"] == "fail" and result["metrics"] == {"gitleaks.count": 1}
    assert SECRET not in json.dumps(result)
    assert not any(SECRET in p.read_text(encoding="utf-8", errors="ignore") for p in (tmp_path / "runs").rglob("*") if p.is_file())


def test_full_run_with_a_leaky_report_is_an_error_and_the_secret_is_nowhere(tmp_path, spec, monkeypatch):
    """Công cụ (hoặc cờ bị đổi) trả báo cáo chưa redact: gate đỏ nhãn hạ tầng, không rò ra result lẫn đĩa."""
    result = run_with_fake_tool(GitleaksAdapter(), spec, monkeypatch, tmp_path, report=json.dumps(leaky_report()))
    assert result["status"] == "error" and result["verdict"]["rationale"].startswith("parse: gitleaks trả 1 finding")
    assert SECRET not in json.dumps(result)
    assert not any(SECRET in p.read_text(encoding="utf-8", errors="ignore") for p in (tmp_path / "runs").rglob("*") if p.is_file())


def test_secret_never_appears_in_findings_metrics_or_notes(tmp_path, spec):
    data = fixture_json("gitleaks-sample.json")
    for entry in data:
        entry["Description"] = "Detected a Generic API Key"      # mô tả chung của luật, không phải giá trị
    parsed = parse(tmp_path, spec, data)
    blob = json.dumps([parsed.findings, parsed.metrics, parsed.adapter_notes])
    assert "REDACTED" not in blob and "API_KEY" not in blob and "AWS_ACCESS_KEY_ID" not in blob and "Description" not in blob


# ---------- finding_id ----------

def test_finding_id_is_stable_unique_and_hashes_rule_plus_location(tmp_path, spec):
    first = [f["finding_id"] for f in parse(tmp_path, spec, fixture_json("gitleaks-sample.json")).findings]
    second = [f["finding_id"] for f in parse(tmp_path, spec, fixture_json("gitleaks-sample.json")).findings]
    assert first == second and len(set(first)) == 2
    assert "f-gitleaks-" + hashlib.sha1(b"generic-api-keyapps/api-server/app/config.py:12").hexdigest()[:12] in first


def test_finding_id_ignores_order_and_dedupes_same_line(tmp_path, spec):
    data = fixture_json("gitleaks-sample.json")
    data.append(copy.deepcopy(data[0]))                       # cùng luật, cùng dòng
    ordered = lambda d: [(f["finding_id"], f["title"]) for f in parse(tmp_path, spec, d).findings]
    base = ordered(data)
    assert len({i for i, _ in base}) == 3
    for seed in range(5):
        shuffled = copy.deepcopy(data)
        random.Random(seed).shuffle(shuffled)
        assert ordered(shuffled) == base


# ---------- cả vòng đời (công cụ giả) ----------

def test_full_run_fail_and_pass(tmp_path, spec, monkeypatch):
    failed = run_with_fake_tool(GitleaksAdapter(), spec, monkeypatch, tmp_path / "a", report=fixture_text("gitleaks-sample.json"))
    assert failed["status"] == "fail" and failed["verdict"]["gating"] is True
    assert any(f["title"] == "generic-api-key @ apps/api-server/app/config.py:12" for f in failed["findings"])
    passed = run_with_fake_tool(GitleaksAdapter(), spec, monkeypatch, tmp_path / "b", report=fixture_text("gitleaks-empty.json"))
    assert passed["status"] == "pass" and passed["findings"] == []


@pytest.mark.parametrize("kwargs, expected", [
    (dict(report=None, returncode=137), "parse: gitleaks kết thúc với exit code 137"),
    (dict(report=None, stderr="boom"), "parse: gitleaks không ghi báo cáo"),
    (dict(report='[{"RuleID": "x", "Fi'), "parse: gitleaks.json không đọc được"),
])
def test_full_run_never_turns_a_broken_scan_into_pass(tmp_path, spec, monkeypatch, kwargs, expected):
    result = run_with_fake_tool(GitleaksAdapter(), spec, monkeypatch, tmp_path, **kwargs)
    assert result["status"] == "error" and expected in result["verdict"]["rationale"]
