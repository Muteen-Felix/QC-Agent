"""Bước 27: `qc-agent validate` — bắt lỗi cấu hình OFFLINE, và không báo oan cấu hình đúng."""
import re
from pathlib import Path

import pytest
import yaml

from qc_agent.core.cli import main as cli_main
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import templates as t
from qc_agent.scaffold import validate as v

ROOT = Path(__file__).resolve().parent.parent
VAHAN = ROOT / "tests" / "fixtures" / "openapi" / "vahan-rpa.json"
SHA = "9947edff8146903a5848d75cacc12c3b94b18547"
IMAGE = "ghcr.io/muteen-felix/qc-agent@sha256:" + "d" * 64
REUSABLE = ROOT / ".github" / "workflows" / "qc-gate.reusable.yml"


def generate(tmp_path, *, ui=True, pins=True, finish_flow=True, **over):
    """init như người dùng thật rồi (tuỳ chọn) hoàn tất phần TODO. Trả (sut, projects)."""
    sut, projects = tmp_path / "sut", tmp_path / "projects"
    sut.mkdir(exist_ok=True)
    opts = dict(sut_root=sut, slug="vahan-rpa", repo="o/v", openapi_source=str(VAHAN), projects_dir=projects,
                qc_ref=SHA if pins else None, image=IMAGE if pins else None, ui_dockerfile="apps/web-ui/Dockerfile" if ui else None)
    opts.update(over)
    init_mod.apply(init_mod.build(init_mod.Options(**opts)))
    if ui and finish_flow:
        (sut / ".qc-agent" / "midscene" / "explore.yaml").write_text(t.midscene_explore_flow(steps=[("aiTap", "tab Settings")]), encoding="utf-8")
    return sut, projects


def check(tmp_path, sut, projects, **kwargs):
    return v.validate("vahan-rpa", sut, projects_dir=projects, workers_dirs=[ROOT / "workers"], **kwargs)


def by_level(report, level):
    return [f for f in report.findings if f.level == level]


def messages(report, level=v.ERROR):
    return "\n".join(f"{f.where}: {f.message}" for f in by_level(report, level))


# ---------- cấu hình đúng thì không báo lỗi ----------

def test_a_completed_generated_setup_has_no_errors_or_warnings(tmp_path):
    sut, projects = generate(tmp_path)
    report = check(tmp_path, sut, projects)
    assert not by_level(report, v.ERROR) and not by_level(report, v.WARN), messages(report) + messages(report, v.WARN)
    notes = messages(report, v.NOTE)
    assert "MIDSCENE_MODEL_API_KEY" in notes and "t-101" in notes and len(by_level(report, v.NOTE)) == 1  # gộp thành MỘT ghi chú


def test_the_real_noteboard_reference_project_validates_clean(tmp_path):
    report = v.validate("noteboard", ROOT / "tests" / "fixtures" / "sut" / "noteboard", projects_dir=ROOT / "configs" / "projects",
                        workers_dirs=[ROOT / "workers"])
    assert not by_level(report, v.ERROR), messages(report)
    assert any("qc-gate" in f.message for f in by_level(report, v.WARN))  # noteboard không có qc.yml gọi workflow: chỉ cảnh báo


def test_every_mode_is_checked_by_default_and_can_be_narrowed(tmp_path):
    sut, projects = generate(tmp_path)
    assert not by_level(check(tmp_path, sut, projects, modes=["pr"]), v.ERROR)
    assert "không có mode 'nope'" in messages(check(tmp_path, sut, projects, modes=["nope"]))


# ---------- dấu qc-agent:todo và chưa ghim ----------

def test_fresh_init_output_is_rejected_with_every_todo_located(tmp_path):
    sut, projects = generate(tmp_path, pins=False, finish_flow=False)
    report = check(tmp_path, sut, projects)
    text = messages(report)
    assert ".qc-agent/midscene/explore.yaml:2" in text and ".github/workflows/qc.yml:16" in text and ".github/workflows/qc.yml:19" in text
    assert "ghim commit SHA 40 ký tự" in text and "ghim theo digest" in text


def test_a_todo_in_the_project_file_or_a_suite_is_an_error(tmp_path):
    sut, projects = generate(tmp_path, ui=False)
    with (projects / "vahan-rpa.yaml").open("a", encoding="utf-8") as handle:
        handle.write(f"# {t.TODO} xem lại\n")
    suite = sut / ".qc-agent" / "suites" / "api-contract.yaml"
    suite.write_text(suite.read_text(encoding="utf-8") + f"# {t.TODO} chưa xong\n", encoding="utf-8")
    text = messages(check(tmp_path, sut, projects))
    assert "<projects>/vahan-rpa.yaml:" in text and ".qc-agent/suites/api-contract.yaml:" in text


def test_no_api_project_is_flagged_as_having_no_blocking_suite(tmp_path):
    sut, projects = generate(tmp_path, openapi_source=None, no_api=True)
    report = check(tmp_path, sut, projects)
    assert "blocking_suites: []" in messages(report) or "qc-agent:todo" in messages(report)
    assert any("không có task nào ở lane gate" in f.message for f in by_level(report, v.WARN))


# ---------- suite/policy/worker ----------

def test_suite_with_a_lane_that_conflicts_with_policy_is_reported(tmp_path):
    sut, projects = generate(tmp_path, ui=False)
    path = sut / ".qc-agent" / "suites" / "api-contract.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("lane: gate", "lane: discovery"), encoding="utf-8")
    assert "mode pr" in messages(check(tmp_path, sut, projects))


def test_missing_suite_missing_dir_and_invalid_yaml(tmp_path):
    sut, projects = generate(tmp_path, ui=False)
    (sut / ".qc-agent" / "suites" / "perf-smoke.yaml").unlink()
    assert "cần suite không có" in messages(check(tmp_path, sut, projects))
    (sut / ".qc-agent" / "suites" / "api-contract.yaml").write_text("suite: api-contract\ntasks: []\n", encoding="utf-8")
    assert "api-contract.yaml" in messages(check(tmp_path, sut, projects))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ".qc-agent/suites" in messages(check(tmp_path, empty, projects))


def test_unknown_project_is_an_error(tmp_path):
    sut, projects = generate(tmp_path)
    report = v.validate("nope", sut, projects_dir=projects, workers_dirs=[ROOT / "workers"])
    assert "không có project" in messages(report) and len(report.findings) == 1


def test_task_without_any_capable_worker_is_reported(tmp_path):
    sut, projects = generate(tmp_path, ui=False)
    path = sut / ".qc-agent" / "suites" / "api-contract.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("api.property", "demo.echo"), encoding="utf-8")
    text = messages(check(tmp_path, sut, projects))
    assert "không có worker chạy được task này" in text and "demo.echo" in text


def test_a_task_that_violates_the_contract_is_reported_with_its_id(tmp_path):
    sut, projects = generate(tmp_path, ui=False)
    path = sut / ".qc-agent" / "suites" / "api-contract.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("wallclock_s: 120", "wallclock_s: -5"), encoding="utf-8")
    assert "t-001" in messages(check(tmp_path, sut, projects))


def test_validation_never_probes_workers_or_needs_env(tmp_path, monkeypatch):
    for name in ("APP_BASE_URL", "APP_UI_URL", "MIDSCENE_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    sut, projects = generate(tmp_path)
    monkeypatch.setattr(v.registry, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("không được probe")), raising=False)
    assert not by_level(check(tmp_path, sut, projects), v.ERROR)


# ---------- file tham chiếu ----------

def test_missing_referenced_flow_and_script_are_reported_with_the_task(tmp_path):
    sut, projects = generate(tmp_path)
    (sut / ".qc-agent" / "midscene" / "canary.yaml").unlink()
    (sut / ".qc-agent" / "perf" / "smoke.js").unlink()
    text = messages(check(tmp_path, sut, projects))
    assert "file tham chiếu không tồn tại: .qc-agent/midscene/canary.yaml" in text and "t-canary-01 inputs.flow" in text
    assert "file tham chiếu không tồn tại: .qc-agent/perf/smoke.js" in text and "t-102 inputs.script" in text


@pytest.mark.parametrize("bad", ["../outside.js", "/etc/passwd", "a/../../x.js"])
def test_referenced_files_may_not_escape_the_sut_root(tmp_path, bad):
    sut, projects = generate(tmp_path, ui=False)
    path = sut / ".qc-agent" / "suites" / "perf-smoke.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace(".qc-agent/perf/smoke.js", bad), encoding="utf-8")
    assert "phải nằm trong repo SUT" in messages(check(tmp_path, sut, projects))


# ---------- qc.yml ----------

def edit_workflow(sut, fn):
    path = sut / ".github" / "workflows" / "qc.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data["jobs"]["qc"])
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_workflow_must_pin_sha_and_digest(tmp_path):
    sut, projects = generate(tmp_path)
    edit_workflow(sut, lambda job: job.update(uses=job["uses"].rpartition("@")[0] + "@main"))
    edit_workflow(sut, lambda job: job["with"].update(image="ghcr.io/muteen-felix/qc-agent:latest"))
    text = messages(check(tmp_path, sut, projects))
    assert "@main" in text and "ghcr.io/muteen-felix/qc-agent:latest" in text


def test_workflow_input_typos_are_caught_against_the_real_workflow(tmp_path):
    sut, projects = generate(tmp_path)
    edit_workflow(sut, lambda job: job["with"].update(sut_prot="8000"))
    assert "input `sut_prot` không có trong workflow tái sử dụng" in messages(check(tmp_path, sut, projects))


def test_workflow_suites_must_stay_inside_the_policy(tmp_path):
    sut, projects = generate(tmp_path)
    edit_workflow(sut, lambda job: job["with"].update(suites="api-contract,perf-full"))
    assert "suite 'perf-full' nằm ngoài policy" in messages(check(tmp_path, sut, projects))
    edit_workflow(sut, lambda job: job["with"].update(suites="api-contract", mode="staging"))
    assert "mode 'staging' không có trong project" in messages(check(tmp_path, sut, projects))


def test_wrong_project_slug_in_the_workflow_is_a_warning_that_no_job_matches(tmp_path):
    sut, projects = generate(tmp_path)
    edit_workflow(sut, lambda job: job["with"].update(project="other"))
    report = check(tmp_path, sut, projects)
    assert any("project: vahan-rpa" in f.message and "project khác" in f.message for f in by_level(report, v.WARN))


def test_ui_suite_without_ui_container_in_the_workflow_is_an_error(tmp_path):
    sut, projects = generate(tmp_path)
    edit_workflow(sut, lambda job: [job["with"].pop(k, None) for k in ("sut_ui_dockerfile",)])
    text = messages(check(tmp_path, sut, projects))
    assert "${env.APP_UI_URL}" in text and "sut_ui_dockerfile" in text


def test_missing_workflow_is_only_a_warning(tmp_path):
    sut, projects = generate(tmp_path)
    (sut / ".github" / "workflows" / "qc.yml").unlink()
    report = check(tmp_path, sut, projects)
    assert not by_level(report, v.ERROR) and any("không thấy job nào" in f.message for f in by_level(report, v.WARN))


def test_custom_env_var_not_provided_by_the_workflow_is_an_error(tmp_path):
    sut, projects = generate(tmp_path, ui=False)
    path = sut / ".qc-agent" / "suites" / "api-contract.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("${env.APP_BASE_URL}/openapi.json", "${env.MY_TOKEN}/openapi.json", 1), encoding="utf-8")
    assert "${env.MY_TOKEN}" in messages(check(tmp_path, sut, projects))


# ---------- chống lệch với workflow thật ----------

def test_passthrough_constant_matches_the_env_the_workflow_passes_to_the_gate():
    text = REUSABLE.read_text(encoding="utf-8")
    gate_run = yaml.safe_load(text)["jobs"]["gate"]["steps"]
    run = next(s["run"] for s in gate_run if s.get("id") == "gate")
    passed = set(re.findall(r"-e ([A-Z][A-Z0-9_]*)(?=[ \\\n]|$)", run)) - {"HOME", "APP_BASE_URL", "APP_UI_URL"}
    assert passed == set(v.PASSTHROUGH)


# ---------- CLI ----------

def test_cli_validate_exit_codes_and_strict(tmp_path, capsys):
    sut, projects = generate(tmp_path)
    argv = ["validate", "--project", "vahan-rpa", "--sut-root", str(sut), "--projects-dir", str(projects), "--workers-dir", str(ROOT / "workers")]
    assert cli_main(argv) == 0 and "OK: 0 lỗi, 0 cảnh báo, 1 ghi chú" in capsys.readouterr().out
    (sut / ".github" / "workflows" / "qc.yml").unlink()
    assert cli_main(argv) == 0 and "1 cảnh báo" in capsys.readouterr().out
    assert cli_main(argv + ["--strict"]) == 3 and "FAIL" in capsys.readouterr().out  # --strict: cảnh báo thành lỗi
    fresh, fprojects = tmp_path / "f" / "sut", tmp_path / "f" / "projects"
    fresh.mkdir(parents=True)
    init_mod.apply(init_mod.build(init_mod.Options(sut_root=fresh, slug="vahan-rpa", repo="o/v", openapi_source=str(VAHAN), projects_dir=fprojects)))
    assert cli_main(["validate", "--project", "vahan-rpa", "--sut-root", str(fresh), "--projects-dir", str(fprojects)]) == 3
    assert "qc-agent:todo" in capsys.readouterr().out
    assert cli_main(["validate", "--project", "x"]) == 3  # thiếu --sut-root
    assert cli_main(["validate", "--project", "x", "--sut-root", str(tmp_path / "nope")]) == 3
