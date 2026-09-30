"""tools/eval_gt_sut.py (S1-09): đo Ground-Truth trên SUT cấu hình bằng YAML. Phần thuần + CLI + một lượt chạy thật với LLM giả.

Toyapp/noteboard chỉ đóng vai "một SUT HTTP bất kỳ" cho test; công cụ không biết gì về nó ngoài cấu hình YAML.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("eval_gt_sut", ROOT / "tools" / "eval_gt_sut.py")
ev = importlib.util.module_from_spec(SPEC)
sys.modules["eval_gt_sut"] = ev
SPEC.loader.exec_module(ev)

SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
PRD = ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md"
OPENAPI = ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"
UVICORN = f'"{sys.executable}" -m uvicorn --app-dir . toyapp.app:app --host 127.0.0.1 --port {{port}}'


def config(tmp_path, **over):
    cfg = {
        "name": "noteboard-as-generic-sut", "sut_root": str(SUT), "prd": str(PRD), "openapi": str(OPENAPI),
        "non_testable": ["AC-1.8", "AC-3.5"], "labeled_by": "QA test",
        "sut": {"start": {"cmd": UVICORN, "health_path": "/__qc/config", "env": {"QC_BUGS": "none"}, "timeout_s": 60}},
        "mutants": [
            {"id": "M-env", "acs": ["AC-1.3"], "required": True, "env": {"QC_BUGS": "4"}},
            {"id": "M-edit", "acs": ["AC-1.1"], "edits": [{"file": "toyapp/app.py", "find": 'if "6" in BUGS:', "replace": "if True:"}]},
            {"id": "M-alive", "acs": ["AC-2.1"], "edits": [{"file": "toyapp/app.py", "find": "MUTANT NGHIỆP VỤ", "replace": "MUTANT  NGHIỆP VỤ"}]},   # không đổi hành vi
        ],
    }
    cfg.update(over)
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


# ---------------- cấu hình ----------------

def test_a_valid_config_has_no_problems(tmp_path):
    cfg = yaml.safe_load(config(tmp_path).read_text(encoding="utf-8"))
    assert ev.validate_config(cfg) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda c: c.pop("prd"), "thiếu `prd`"),
    (lambda c: c.pop("sut_root"), "thiếu `sut_root`"),
    (lambda c: c.__setitem__("sut", {}), "`sut` cần"),
    (lambda c: c["sut"]["start"].__setitem__("cmd", "run-it"), "{port}"),
    (lambda c: c.__setitem__("non_testable", "AC-1"), "danh sách"),
    (lambda c: c["mutants"][0].pop("env"), "đúng MỘT"),
    (lambda c: c["mutants"][0].__setitem__("patch", "x.patch"), "đúng MỘT"),
    (lambda c: c["mutants"][0].pop("acs"), "cần `acs`"),
    (lambda c: c["mutants"][1].__setitem__("id", "M-env"), "trùng id"),
    (lambda c: c["mutants"][1]["edits"][0].__setitem__("find", ""), "cần `file`"),
    (lambda c: (c["sut"].pop("start"), c["sut"].__setitem__("base_url", "http://x")), "cần `sut.start`"),
])
def test_config_problems_are_reported(tmp_path, mutate, needle):
    cfg = yaml.safe_load(config(tmp_path).read_text(encoding="utf-8"))
    mutate(cfg)
    assert any(needle in problem for problem in ev.validate_config(cfg)), ev.validate_config(cfg)


def test_a_user_managed_sut_needs_base_urls_for_its_mutants():
    cfg = {"sut_root": ".", "prd": "p.md", "sut": {"base_url": "http://x"},
           "mutants": [{"id": "M1", "acs": ["AC-1"], "base_url": "http://y"}, {"id": "M2", "acs": ["AC-1"], "env": {"A": "1"}}]}
    problems = ev.validate_config(cfg)
    assert len(problems) == 1 and "M2" in problems[0]


def test_paths_in_the_config_are_resolved_relative_to_the_config_file(tmp_path):
    (tmp_path / "cfg").mkdir()
    path = tmp_path / "cfg" / "eval.yaml"
    path.write_text(yaml.safe_dump({"sut_root": "../sut", "prd": "prd.md", "openapi": "api.json", "sut": {"base_url": "http://x"}}), encoding="utf-8")
    cfg = ev.load_config(path)
    assert cfg["sut_root"] == (tmp_path / "sut").resolve() and cfg["prd"] == (tmp_path / "cfg" / "prd.md").resolve()
    assert cfg["openapi"] == str((tmp_path / "cfg" / "api.json").resolve())
    path.write_text(yaml.safe_dump({"sut_root": ".", "prd": "p", "openapi": "https://x.test/openapi.json", "sut": {"base_url": "http://x"}}), encoding="utf-8")
    assert ev.load_config(path)["openapi"] == "https://x.test/openapi.json"


def test_golden_is_built_from_the_prd_and_unknown_acs_are_rejected(tmp_path):
    from qc_agent.groundtruth.prd import parse_prd
    prd = parse_prd(PRD)
    cfg = {"non_testable": ["AC-1.8"], "mutants": [{"id": "M1", "acs": ["AC-1.3"]}]}
    golden = ev.build_golden(cfg, prd)
    assert golden["acs"]["AC-1.8"] == {"testable": False} and golden["acs"]["AC-1.3"] == {"testable": True} and golden["mutants"] == {"M1": {"acs": ["AC-1.3"]}}
    with pytest.raises(ev.EvalError, match="non_testable"):
        ev.build_golden({"non_testable": ["AC-99"]}, prd)
    with pytest.raises(ev.EvalError, match="M1"):
        ev.build_golden({"mutants": [{"id": "M1", "acs": ["AC-99"]}]}, prd)


# ---------------- chèn lỗi ----------------

def test_edits_must_match_exactly_once_and_never_touch_the_original(tmp_path):
    (tmp_path / "app").mkdir()
    target = tmp_path / "app" / "r.py"
    target.write_text("a = 201\nb = 201\nc = 5\n", encoding="utf-8", newline="")
    ev.apply_edits(tmp_path, [{"file": "app/r.py", "find": "c = 5", "replace": "c = 6"}], "M")
    assert target.read_text(encoding="utf-8") == "a = 201\nb = 201\nc = 6\n"
    with pytest.raises(ev.EvalError, match="khớp 2 lần"):
        ev.apply_edits(tmp_path, [{"file": "app/r.py", "find": "201", "replace": "200"}], "M")
    with pytest.raises(ev.EvalError, match="khớp 0 lần"):
        ev.apply_edits(tmp_path, [{"file": "app/r.py", "find": "nope", "replace": "x"}], "M")
    ev.apply_edits(tmp_path, [{"file": "app/r.py", "find": "201", "replace": "200", "count": 2}], "M")
    assert target.read_text(encoding="utf-8").startswith("a = 200\nb = 200")


def test_edits_cannot_escape_the_sut_copy_and_keep_line_endings(tmp_path):
    root = tmp_path / "sut"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(ev.EvalError, match="ngoài SUT"):
        ev.apply_edits(root, [{"file": "../outside.txt", "find": "secret", "replace": "x"}], "M")
    with pytest.raises(ev.EvalError, match="không có file"):
        ev.apply_edits(root, [{"file": "missing.py", "find": "a", "replace": "b"}], "M")
    crlf = root / "w.py"
    crlf.write_bytes(b"x = 1\r\ny = 2\r\n")
    ev.apply_edits(root, [{"file": "w.py", "find": "y = 2", "replace": "y = 3"}], "M")
    assert crlf.read_bytes() == b"x = 1\r\ny = 3\r\n"


def test_a_git_patch_is_applied_to_the_copy_and_a_stale_patch_is_an_error(tmp_path):
    root = tmp_path / "sut"
    root.mkdir()
    (root / "f.txt").write_text("one\ntwo\nthree\n", encoding="utf-8", newline="\n")
    patch = tmp_path / "m.patch"
    patch.write_text("--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n", encoding="utf-8", newline="\n")
    ev.apply_patch(root, str(patch), "M")
    assert (root / "f.txt").read_text(encoding="utf-8") == "one\nTWO\nthree\n"
    with pytest.raises(ev.EvalError, match="git apply"):
        ev.apply_patch(root, str(patch), "M")   # đã áp rồi: không còn khớp


def test_a_sut_that_does_not_boot_reports_the_tail_of_its_own_log(tmp_path):
    sut = {"start": {"cmd": f'"{sys.executable}" -c "import sys; print(\'boom-in-sut {{port}}\'); sys.exit(3)"', "timeout_s": 20}}
    with pytest.raises(ev.EvalError, match="boom-in-sut"):
        with ev.SutProcess(sut, None, SUT):
            pass


# ---------------- ngưỡng ----------------

THR = {"ac_coverage": 0.9, "green_rate": 0.9, "mutant_kill_rate": 0.9}


def mutants(killed, total, missed=()):
    return {"killed": [], "survived": [], "count": killed, "total": total, "value": killed / total if total else 0.0, "required_missed": list(missed)}


def test_summarize_mutants_counts_kills_and_required_misses():
    results = {"A": {"killed": True}, "B": {"killed": False}, "C": {"killed": True}}
    summary = ev.summarize_mutants(results, {"B", "C", "Z"})
    assert (summary["killed"], summary["survived"], summary["count"], summary["total"]) == (["A", "C"], ["B"], 2, 3)
    assert summary["required_missed"] == ["B"] and summary["value"] == pytest.approx(2 / 3)
    assert ev.summarize_mutants({}, set())["value"] == 0.0


@pytest.mark.parametrize("kw, passed", [
    (dict(coverage=0.9, green=0.9, mutants=mutants(9, 10), baseline_green=True), True),
    (dict(coverage=0.899, green=1, mutants=mutants(10, 10), baseline_green=True), False),
    (dict(coverage=1, green=0.899, mutants=mutants(10, 10), baseline_green=True), False),
    (dict(coverage=1, green=1, mutants=mutants(8, 10), baseline_green=True), False),
    (dict(coverage=1, green=1, mutants=mutants(10, 10, ["M1"]), baseline_green=True), False),   # mutant bắt buộc bị sót
    (dict(coverage=1, green=1, mutants=mutants(10, 10), baseline_green=False), False),
    (dict(coverage=1, green=1, mutants=mutants(0, 0), baseline_green=True), False),              # không có mutant nào thì không được "đạt"
    (dict(coverage=1, green=1, mutants=None, baseline_green=None), True),                        # chỉ đo (a),(b)
    (dict(coverage=None, green=None, mutants=None, baseline_green=True), True),                  # chỉ baseline
    (dict(coverage=None, green=None, mutants=None, baseline_green=None), False),                 # không đo gì thì không đạt
])
def test_verdict(kw, passed):
    assert ev.verdict(THR, **kw)["passed"] is passed


def test_unmeasured_parts_are_absent_from_the_checks():
    assert set(ev.verdict(THR, coverage=1, green=1, mutants=None, baseline_green=None)["checks"]) == {"ac_coverage", "green_rate"}


# ---------------- bảng Markdown ----------------

def test_the_markdown_report_names_survivors_and_uncovered_acs():
    report = {
        "name": "sut-x", "prd_id": "p", "llm": "real", "model": "gemini-3.6-flash", "labeled_by": "QA An", "thresholds": THR,
        "generation": {"runs": [{}, {}, {}], "ac_coverage": {"median": 0.95, "covered": 19, "testable": 20, "missing": ["AC-2.1"]}, "green_rate": {"median": 0.93}},
        "approved": {"test_cases": 30, "baseline_green": True, "ac_coverage": {"value": 1.0}, "mutants": {"count": 1, "total": 2, "survived": ["M2"], "required_missed": []},
                     "mutant_results": {"M1": {"killed": True, "failing_tcs": ["a", "b", "c", "d"], "caught_by_expected_ac": True},
                                        "M2": {"killed": False, "failing_tcs": [], "measurement_error": True}}},
        "mutant_acs": {"M1": ["AC-1.3"], "M2": ["AC-2.1"]},
        "verdict": {"passed": False, "checks": {"ac_coverage": True, "green_rate": True, "baseline_green": True, "mutant_kill_rate": False, "required_mutants": True}},
    }
    text = ev.render_markdown(report)
    for needle in ("`sut-x`", "gemini-3.6-flash", "Nhãn", "QA An", "95.0%", "93.0%", "1/2", "| M1 | ✅ | AC-1.3 | a, b, c … |", "| M2 | ❌ (đo lỗi) |",
                   "Mutant sống sót (bộ test bỏ sót): M2", "AC-2.1", "**Kết luận: KHÔNG ĐẠT**"):
        assert needle in text, needle


# ---------------- CLI ----------------

def test_real_mode_prints_a_free_tier_estimate_and_needs_yes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("QC_GT_MODEL", "gemini-3.6-flash")
    out = tmp_path / "out.json"
    assert ev.main(["--config", str(config(tmp_path)), "--runs", "3", "--out-json", str(out)]) == 3
    err = capsys.readouterr().err
    assert "gemini-3.6-flash" in err and "free tier" in err and "3 lượt" in err and "--yes" in err and not out.exists()


def test_a_paid_model_needs_explicit_prices(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("QC_GT_MODEL", "some-other-model")
    assert ev.main(["--config", str(config(tmp_path)), "--yes"]) == 3
    assert "--price-in" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [["--runs", "0"], ["--runs", "11"], ["--llm", "nope"], ["--skip-generate", "--skip-mutants"]])
def test_bad_usage_is_exit_3(tmp_path, capsys, extra):
    assert ev.main(["--config", str(config(tmp_path)), *extra]) == 3
    capsys.readouterr()


def test_a_broken_config_or_missing_approved_suite_is_exit_3(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    assert ev.main(["--config", str(bad), "--llm", "fake"]) == 3
    empty = tmp_path / "empty-sut"
    empty.mkdir()
    assert ev.main(["--config", str(config(tmp_path, sut_root=str(empty))), "--skip-generate"]) == 3
    assert "không có bộ Ground-Truth" in capsys.readouterr().err


def test_a_mutant_naming_an_ac_outside_the_prd_is_exit_3_before_anything_runs(tmp_path, capsys):
    cfg = config(tmp_path, mutants=[{"id": "M", "acs": ["AC-99"], "env": {"QC_BUGS": "4"}}])
    assert ev.main(["--config", str(cfg), "--llm", "fake"]) == 3
    assert "AC-99" in capsys.readouterr().err


def test_the_real_repo_is_never_modified_by_measuring(tmp_path):
    before = subprocess.run(["git", "status", "--porcelain", "--", str(SUT)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    with ev.sut_instance(ev.load_config(config(tmp_path)), {"id": "M", "acs": ["AC-1.1"], "edits": [{"file": "toyapp/app.py", "find": 'if "6" in BUGS:', "replace": "if True:"}]}) as url:
        assert url.startswith("http://127.0.0.1:")
    after = subprocess.run(["git", "status", "--porcelain", "--", str(SUT)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    assert before == after and 'if "6" in BUGS:' in (SUT / "toyapp" / "app.py").read_text(encoding="utf-8")


def test_end_to_end_with_the_fake_llm_measures_generation_and_kills_or_spares_each_mutant(tmp_path, capsys):
    """Chạy thật: sinh bằng LLM giả, xanh trên SUT sạch, mutant env/edit bị bắt, mutant vô hại sống sót => kill rate 2/3 < 90% => exit 1."""
    out = tmp_path / "out.json"
    code = ev.main(["--config", str(config(tmp_path)), "--llm", "fake", "--out-json", str(out)])
    text, err = capsys.readouterr()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1, text + err
    gen, appr = data["generation"], data["approved"]
    assert gen["ac_coverage"]["median"] >= 0.9 and gen["green_rate"]["median"] >= 0.9
    assert appr["baseline_green"] is True
    assert appr["mutant_results"]["M-env"]["killed"] is True and appr["mutant_results"]["M-edit"]["killed"] is True
    assert appr["mutant_results"]["M-alive"]["killed"] is False and appr["mutants"]["survived"] == ["M-alive"]
    assert data["verdict"]["checks"]["ac_coverage"] and data["verdict"]["checks"]["required_mutants"] and data["verdict"]["checks"]["mutant_kill_rate"] is False
    assert "Mutant sống sót" in text and "M-alive" in text and data["labeled_by"] == "QA test"
    status = subprocess.run(["git", "status", "--porcelain", "--", str(SUT)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    assert status == ""   # sut_root thật không bị sửa


def test_without_an_approved_suite_generation_is_still_measured_and_the_missing_part_is_only_a_note(tmp_path, capsys):
    import shutil
    fresh = tmp_path / "fresh-sut"
    shutil.copytree(SUT, fresh, ignore=shutil.ignore_patterns(".qc-agent", "__pycache__"))
    out = tmp_path / "out.json"
    code = ev.main(["--config", str(config(tmp_path, sut_root=str(fresh))), "--llm", "fake", "--out-json", str(out)])
    text, err = capsys.readouterr()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0, text + err
    assert "generation" in data and "approved" not in data and set(data["verdict"]["checks"]) == {"ac_coverage", "green_rate"}
    assert "bỏ qua (c)" in err and "--skip-generate" in err
