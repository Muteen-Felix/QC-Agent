"""P2-3: worker coverage-debt được đăng ký đủ (manifest, capability, suite mẫu), adapter dịch debt.json đúng contract,
và chuỗi worker -> adapter -> threshold -> policy advisory_yellow_suites ra đúng YELLOW/PASS. Oracle `threshold` có sẵn, không class mới."""
import importlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from coveragekit import MAIN_PY, PING, Repo
from qc_agent.adapters._base import Adapter, AdapterParseError
from qc_agent.adapters.coverage_debt_adapter import OUT_NAME, PARSER_VERSION, CoverageDebtAdapter
from qc_agent.core import engine, registry, schema
from qc_agent.core import plan as plan_lib
from qc_agent.scaffold import templates as t
from qc_agent.scaffold import validate as v

ROOT = Path(__file__).resolve().parent.parent
NOTEBOARD = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="cần binary git")


def suite_task() -> dict:
    return yaml.safe_load(t.coverage_debt_suite())["tasks"][0]


def make_spec(**inputs) -> dict:
    """Spec resolve từ chính task của suite mẫu (không tự dựng tay), qua validate_task."""
    task = json.loads(json.dumps(suite_task()).replace("${env.APP_BASE_URL}", "http://sut.invalid"))
    task["inputs"] = {**task["inputs"], **inputs}
    spec, _ = plan_lib.resolve(task, {"plan_id": "plan-debt", "run_id": "r-0001", "runs_dir": "runs", "sut_identity_ref": "sut-debt"})
    assert schema.validate_task(spec) == []
    return spec


def debt(findings=(), *, full=False, status="ok", **over) -> dict:
    data = {"status": status, "error": None, "mode": "full" if full else "diff", "base": None if full else "HEAD^1",
            "findings": list(findings), "ignored": [], "metrics": {"debt.new": len(findings), "debt.full_scan": full}}
    data.update(over)
    return data


def item(kind="api_endpoint", surface="GET /ping") -> dict:
    return {"finding_id": f"debt:{kind}:{surface}", "kind": kind, "surface": surface}


class Proc:
    def __init__(self, returncode=0):
        self.returncode, self.stdout, self.args = returncode, "coverage-debt: ok\n", ["python", "-m", "worker"]


def parse(tmp_path, data, *, returncode=0, spec=None):
    (tmp_path / OUT_NAME).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return CoverageDebtAdapter().parse_output(Proc(returncode), tmp_path, spec or make_spec())


# ───────────────────────── đăng ký: manifest + capability ─────────────────────────

def test_manifest_declares_discovery_only_git_and_no_egress():
    worker = registry.load(ROOT / "workers")["coverage-debt"]
    assert worker.lanes == ["discovery"] and worker.requires == {"env": [], "binaries": ["git"]} and worker.data_egress == []
    assert set(worker.capabilities) == {"repo.coverage_debt"}
    assert worker.capabilities["repo.coverage_debt"]["oracle_kinds"] == ["threshold"]  # D3: oracle có sẵn, không viết mới
    assert worker.module == "qc_agent.adapters.coverage_debt_adapter"
    assert issubclass(importlib.import_module(worker.module).CoverageDebtAdapter, Adapter)


def test_capability_is_in_the_shared_vocabulary_and_no_new_oracle_was_added():
    vocab = json.loads((ROOT / "schemas" / "capabilities.json").read_text(encoding="utf-8"))
    assert "repo.coverage_debt" in vocab["capabilities"]
    assert vocab["oracle_kinds"] == ["trivial", "checks", "threshold", "implicit_signals"]  # danh sách oracle không đổi
    assert sorted(p.stem for p in (ROOT / "src" / "qc_agent" / "oracle").glob("*.py") if p.stem != "__init__") == \
        ["checks", "signals", "threshold", "trivial"]  # và không có file oracle mới (D3)


def test_registry_routes_a_coverage_debt_task_to_the_worker():
    workers = {n: w for n, w in registry.load(ROOT / "workers").items()}
    for worker in workers.values():
        worker.probe_ok, worker.probe_reason = True, None
    picked, _ = registry.pick(workers, make_spec(), ("coverage-debt",))
    assert picked is not None and picked.name == "coverage-debt"


# ───────────────────────── suite mẫu ─────────────────────────

def test_sample_suite_shape_and_threshold_lives_in_yaml():
    doc = yaml.safe_load(t.coverage_debt_suite())
    task = doc["tasks"][0]
    assert doc["suite"] == "coverage-debt" and len(doc["tasks"]) == 1
    assert task["capability"] == "repo.coverage_debt" and task["lane"] == "discovery" and task["prefer"] == ["coverage-debt"]
    assert task["expected_result_kind"] == "candidate_finding" and task["target"]["base_url"] == "${env.APP_BASE_URL}"
    assert task["oracle"] == {"kind": "threshold", "assertions": [{"metric": "debt.new", "op": "==", "value": 0}]}
    assert task["retry"] == {"max": 0, "on": []} and task["evidence_required"] == ["raw_output", "stdout"]


def test_noteboard_fixture_suite_is_exactly_the_template():
    fixture = NOTEBOARD / ".qc-agent" / "suites" / "coverage-debt.yaml"
    assert fixture.read_text(encoding="utf-8") == t.coverage_debt_suite()


def test_every_asserted_metric_is_one_the_adapter_emits(tmp_path):
    asserted = {a["metric"] for a in suite_task()["oracle"]["assertions"]}
    assert asserted <= set(parse(tmp_path, debt()).metrics)


# ───────────────────────── build_cmd ─────────────────────────

def test_build_cmd_calls_the_worker_module_with_absolute_out_and_removes_stale(tmp_path):
    (tmp_path / OUT_NAME).write_text("cũ", encoding="utf-8")
    cmd = CoverageDebtAdapter().build_cmd(make_spec(), tmp_path)
    assert cmd[1:3] == ["-m", "qc_agent.adapters.coverage_debt_worker"]
    assert Path(cmd[cmd.index("--out") + 1]) == (tmp_path / OUT_NAME).resolve() and "--suites-dir" not in cmd and "--base" not in cmd
    assert not (tmp_path / OUT_NAME).exists()


def test_build_cmd_passes_safe_suites_dir_and_rejects_unsafe(tmp_path):
    cmd = CoverageDebtAdapter().build_cmd(make_spec(suites_dir="ci/suites"), tmp_path)
    assert cmd[cmd.index("--suites-dir") + 1] == "ci/suites"
    for bad in ("../x", "/abs", "-flag"):
        with pytest.raises(AdapterParseError):
            CoverageDebtAdapter().build_cmd(make_spec(suites_dir=bad), tmp_path)


# ───────────────────────── parse_output ─────────────────────────

def test_parse_maps_findings_and_flat_metrics(tmp_path):
    out = parse(tmp_path, debt([item(), item("ui_route", "/settings")]))
    assert out.metrics == {"debt.new": 2, "debt.full_scan": False}
    assert [f["finding_id"] for f in out.findings] == ["debt:api_endpoint:GET /ping", "debt:ui_route:/settings"]  # D2: khoá của sổ nợ
    first = out.findings[0]
    assert first["detected_by"] == "coverage-debt:api_endpoint" and first["verdict_source"] == "heuristic" and first["severity_hint"] == "low"
    assert (out.tokens, out.usd, out.exit_code) == (0, 0.0, 0) and f"PARSER_VERSION={PARSER_VERSION}" in out.adapter_notes
    assert {k for k, _ in out.evidence_paths} == {"raw_output", "stdout"}


def test_parse_carries_source_location(tmp_path):
    raw = item()
    raw.update(path="toyapp/app.py", line=7)
    out = parse(tmp_path, debt([raw]))
    assert out.findings[0]["location"] == {"path": "toyapp/app.py", "line": 7}


def test_parse_full_scan_and_ignored_count(tmp_path):
    out = parse(tmp_path, debt([item()], full=True, ignored=[{"finding_id": "x", "reason": "r"}]))
    assert out.metrics["debt.full_scan"] is True and "mode=full" in out.adapter_notes and "ignored=1" in out.adapter_notes


def test_parse_does_not_truncate_findings(tmp_path):
    many = [item("api_endpoint", f"GET /r{i}") for i in range(500)]
    assert len(parse(tmp_path, debt(many, full=True)).findings) == 500  # full-scan đóng nợ theo phần vắng mặt: cắt bớt sẽ đóng oan


def test_parse_title_is_printable_and_bounded(tmp_path):
    out = parse(tmp_path, debt([item("api_endpoint", "GET /x\n@evil " + "a" * 500)]))
    title = out.findings[0]["title"]
    assert "\n" not in title and len(title) < 300


@pytest.mark.parametrize("data", [
    debt(status="error", error="không tìm thấy base 'HEAD^1' (checkout nông? workflow cần fetch-depth: 2)"),
    debt(status="lạ"),
    debt([item()], metrics={"debt.new": 2, "debt.full_scan": False}),  # số nợ lệch độ dài danh sách
    debt([item()], metrics={"debt.new": True, "debt.full_scan": False}),
    debt([item()], metrics={"debt.new": 1}),  # thiếu debt.full_scan
    debt([item()], mode="full"),  # mode lệch cờ full_scan
    debt([{"finding_id": "debt:api_endpoint:GET /a", "kind": "api_endpoint", "surface": "GET /b"}]),  # id lệch surface
    debt([item("secret_kind", "x")]),
    debt([item(), item()], metrics={"debt.new": 2, "debt.full_scan": False}),  # trùng
    {**debt(), "findings": "x"},
    "[1, 2]",
    "không phải json",
])
def test_parse_rejects_what_it_cannot_trust_as_error_never_zero_debt(tmp_path, data):
    with pytest.raises(AdapterParseError):
        parse(tmp_path, data)


def test_parse_error_message_carries_the_worker_reason(tmp_path):
    with pytest.raises(AdapterParseError, match="fetch-depth: 2"):
        parse(tmp_path, debt(status="error", error="base thiếu: workflow cần fetch-depth: 2"), returncode=3)


def test_parse_missing_file_and_exit_status_contradiction(tmp_path):
    with pytest.raises(AdapterParseError, match="không ghi ra"):
        CoverageDebtAdapter().parse_output(Proc(3), tmp_path, make_spec())
    with pytest.raises(AdapterParseError, match="mâu thuẫn"):
        parse(tmp_path, debt(), returncode=3)


# ───────────────────────── vòng đời thật: worker thật, git thật ─────────────────────────

def pr_repo(tmp_path, *, tested=False) -> Repo:
    repo = Repo(tmp_path / "sut")
    repo.write("app/main.py", MAIN_PY)
    repo.commit("base")
    repo.write("app/main.py", MAIN_PY + PING)
    if tested:
        repo.write("tests/test_ping.py", 'def test_ping(client):\n    client.get("/ping")\n')
    repo.commit("pr")
    return repo


def run_adapter(tmp_path, monkeypatch, repo, base):
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.chdir(repo.root)  # worker chạy với cwd = SUT root
    if base:
        monkeypatch.setenv("QC_DIFF_BASE", base)
    else:
        monkeypatch.delenv("QC_DIFF_BASE", raising=False)
    return CoverageDebtAdapter().run(make_spec())


@needs_git
def test_lifecycle_new_untested_endpoint_is_a_non_gating_fail_with_debt_findings(tmp_path, monkeypatch):
    result = run_adapter(tmp_path, monkeypatch, pr_repo(tmp_path), "HEAD^1")
    assert schema.validate_result(result) == []
    assert result["status"] == "fail" and result["verdict"]["gating"] is False  # discovery: không bao giờ chặn
    assert result["metrics"] == {"debt.new": 1, "debt.full_scan": False}
    ids = [f["finding_id"] for f in result["findings"]]
    assert "debt:api_endpoint:GET /ping" in ids and "f-thr-debt.new" in ids  # finding của oracle threshold có sẵn


@needs_git
def test_lifecycle_covered_endpoint_passes(tmp_path, monkeypatch):
    result = run_adapter(tmp_path, monkeypatch, pr_repo(tmp_path, tested=True), "HEAD^1")
    assert result["status"] == "pass" and result["metrics"]["debt.new"] == 0 and result["findings"] == []


@needs_git
def test_lifecycle_without_qc_diff_base_is_a_full_scan(tmp_path, monkeypatch):
    result = run_adapter(tmp_path, monkeypatch, pr_repo(tmp_path), None)
    assert result["metrics"]["debt.full_scan"] is True and result["metrics"]["debt.new"] == 2  # /health + /ping
    assert result["status"] == "fail"


@needs_git
def test_lifecycle_missing_base_is_error_not_pass(tmp_path, monkeypatch):
    result = run_adapter(tmp_path, monkeypatch, pr_repo(tmp_path), "deadbeef")
    assert result["status"] == "error" and "fetch-depth: 2" in result["verdict"]["rationale"]


# ───────────────────────── chuỗi đầy đủ với policy (P2-1 + P2-3) ─────────────────────────

def project_dir(tmp_path, *, yellow: bool) -> Path:
    modes = {"pr": {"blocking_suites": ["core"], "advisory_suites": ["coverage-debt"]}}
    folder = tmp_path / "projects"
    folder.mkdir()
    (folder / "debt.yaml").write_text(yaml.safe_dump({"slug": "debt", "modes": modes}), encoding="utf-8")
    return folder


def run_policy(tmp_path, monkeypatch, *, yellow, tested):
    repo = pr_repo(tmp_path, tested=tested)
    (repo.root / ".qc-agent" / "suites").mkdir(parents=True)
    (repo.root / ".qc-agent" / "suites" / "coverage-debt.yaml").write_text(t.coverage_debt_suite(), encoding="utf-8")
    monkeypatch.setenv("APP_BASE_URL", "http://sut.invalid")
    monkeypatch.setenv("QC_DIFF_BASE", "HEAD^1")
    monkeypatch.delenv("QC_WORKERS_PATH", raising=False)
    return engine.run_project("debt", "pr", tmp_path / "runs", projects_dir=project_dir(tmp_path, yellow=yellow), sut_root=repo.root,
                              only_suites=["coverage-debt"], workers_dirs=[ROOT / "workers"])


@needs_git
def test_policy_warns_when_untested_endpoint(tmp_path, monkeypatch):
    result = run_policy(tmp_path, monkeypatch, yellow=True, tested=False)
    assert (result.gate.value, result.exit_code) == ("PASSED_WITH_WARNINGS", 0)


@needs_git
def test_policy_warns_without_old_yellow_setting_and_passes_when_covered(tmp_path, monkeypatch):
    assert run_policy(tmp_path / "a", monkeypatch, yellow=False, tested=False).gate.value == "PASSED_WITH_WARNINGS"
    assert run_policy(tmp_path / "b", monkeypatch, yellow=True, tested=True).gate.value == "PASSED"


# ───────────────────────── validate: cảnh báo suite yellow vắng mặt ─────────────────────────

def validate_with(tmp_path, *, yellow, with_suite):
    sut = tmp_path / "sut"
    suites = sut / ".qc-agent" / "suites"
    suites.mkdir(parents=True)
    shutil.copy(NOTEBOARD / ".qc-agent" / "suites" / "api-contract.yaml", suites / "api-contract.yaml")
    if with_suite:
        (suites / "coverage-debt.yaml").write_text(t.coverage_debt_suite(), encoding="utf-8")
    mode = {"blocking_suites": ["api-contract"], "advisory_suites": ["coverage-debt", "ui-explore"]}
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "_default.yaml").write_text(yaml.safe_dump({"modes": {"pr": mode}}), encoding="utf-8")
    (projects / "x.yaml").write_text("slug: x\n", encoding="utf-8")
    return v.validate("x", sut, projects_dir=projects)


def levels(report, level):
    return [f.message for f in report.findings if f.level == level]


def test_validate_notes_missing_advisory_suite(tmp_path):
    report = validate_with(tmp_path, yellow=True, with_suite=False)
    notes = " ".join(levels(report, v.NOTE))
    assert "ui-explore" in notes and "coverage-debt" in notes


def test_validate_is_quiet_when_suite_present_or_not_yellow(tmp_path):
    assert not [w for w in levels(validate_with(tmp_path / "a", yellow=True, with_suite=True), v.WARN) if "advisory_yellow_suites" in w]
    plain = validate_with(tmp_path / "b", yellow=False, with_suite=False)
    assert not [w for w in levels(plain, v.WARN) if "advisory_yellow_suites" in w]
    assert "coverage-debt" in " ".join(levels(plain, v.NOTE))  # không yellow: giữ hành vi cũ (ghi chú)
