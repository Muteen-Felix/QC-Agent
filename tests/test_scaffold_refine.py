"""Bước 34: `init --refine` (Pha 2). Điền vùng REFINE từ OpenAPI sống, chỉ-đọc repo, đầu ra tất định."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from qc_agent.core import project as pj
from qc_agent.core.cli import main as cli_main
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import refine, suggest
from qc_agent.scaffold import templates as t
from qc_agent.scaffold import validate as v

ROOT = Path(__file__).resolve().parent.parent
VAHAN = ROOT / "tests" / "fixtures" / "openapi" / "vahan-rpa.json"
SCAN = ROOT / "tests" / "fixtures" / "scan" / "vahan-rpa"
NOTEBOARD = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
UPLOAD = "/api/jobs/{job_id}/upload-excel"


@pytest.fixture
def phase1(tmp_path):
    """Đầu ra của Pha 1 (init không --openapi) trên bản cắt cấu trúc vahan-rpa."""
    sut = tmp_path / "sut"
    shutil.copytree(SCAN, sut)
    init_mod.apply(init_mod.build(init_mod.Options(sut_root=sut, slug="vahan-rpa", qc_ref="a" * 40, image="ghcr.io/muteen-felix/qc-agent@sha256:" + "d" * 64,
                                                   sut_env=["VAHAN_API_CORS_ORIGINS=http://ui:8080"])))
    return sut


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + path.read_bytes())
    return digest.hexdigest()


def git_apply(sut: Path, patch: str) -> None:
    (sut / "refine.patch").write_text(patch, encoding="utf-8", newline="")
    subprocess.run(["git", "apply", "--unsafe-paths", "refine.patch"], cwd=sut, check=True, capture_output=True)
    (sut / "refine.patch").unlink()


def test_refine_fills_exclude_k6_and_health_from_the_real_vahan_openapi(phase1):
    result = refine.refine(phase1, str(VAHAN))
    assert sorted(result.changed) == [".github/workflows/qc.yml", ".qc-agent/perf/smoke.js", ".qc-agent/suites/api-contract.yaml"]
    assert f'- "{UPLOAD}"' in result.patch and '+const PATHS = ["/", "/api/health", "/api/runners"];' in result.patch
    assert '-      sut_health_path: "/health"' in result.patch and '+      sut_health_path: "/api/health"' in result.patch
    assert any("đề xuất /api/health" in n for n in result.notes)


def test_applying_the_patch_gives_valid_files_that_validate_and_a_second_refine_is_empty(phase1):
    result = refine.refine(phase1, str(VAHAN))
    git_apply(phase1, result.patch)
    contract = yaml.safe_load((phase1 / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8"))["tasks"][0]
    assert contract["inputs"]["exclude_path"] == [UPLOAD]
    k6 = (phase1 / ".qc-agent" / "perf" / "smoke.js").read_text(encoding="utf-8")
    assert "todo REFINE" not in k6 and "qc-agent:begin refine k6_paths" in k6 and "qc-agent:end" in k6   # marker còn: chạy lại là idempotent
    again = refine.refine(phase1, str(VAHAN))
    assert again.patch == "" and again.suggestions == [] and again.changed == []
    # sau khi áp: không còn TODO REFINE => validate không chặn vì REFINE
    projects = ROOT / "configs" / "projects"
    text = "\n".join(f.message for f in v.validate("vahan-rpa", phase1, projects_dir=projects, workers_dirs=[ROOT / "workers"]).findings if f.level == v.ERROR)
    assert "REFINE" not in text


def test_the_output_is_deterministic_for_the_same_openapi(phase1):
    first, second = refine.refine(phase1, str(VAHAN)), refine.refine(phase1, str(VAHAN))
    assert first.patch == second.patch and first.suggestions == second.suggestions and first.patch
    spec = json.loads(VAHAN.read_text(encoding="utf-8"))
    spec["paths"] = dict(reversed(list(spec["paths"].items())))     # cùng nội dung, thứ tự khác
    reordered = phase1.parent / "reordered.json"
    reordered.write_text(json.dumps(spec), encoding="utf-8")
    assert refine.refine(phase1, str(reordered)).patch == first.patch


def test_suggestions_point_at_the_marker_lines_and_stay_small(phase1):
    result = refine.refine(phase1, str(VAHAN))
    by_path = {s["path"]: s for s in result.suggestions}
    contract_lines = (phase1 / ".qc-agent" / "suites" / "api-contract.yaml").read_text(encoding="utf-8").splitlines()
    sug = by_path[".qc-agent/suites/api-contract.yaml"]
    assert "qc-agent:begin refine" in contract_lines[sug["start_line"] - 1] and "qc-agent:end" in contract_lines[sug["end_line"] - 1]
    assert f'"{UPLOAD}"' in sug["replacement"] and sug["replacement"].splitlines()[0].strip().startswith("# qc-agent:begin refine exclude_path")
    health = by_path[".github/workflows/qc.yml"]
    assert health["start_line"] == health["end_line"] and health["replacement"].strip() == 'sut_health_path: "/api/health"'
    assert all(s["end_line"] - s["start_line"] + 1 <= refine.MAX_SUGGEST_LINES for s in result.suggestions)


def test_a_region_whose_marker_was_deleted_belongs_to_the_human(phase1):
    path = phase1 / ".qc-agent" / "suites" / "api-contract.yaml"
    path.write_text("".join(line for line in path.read_text(encoding="utf-8").splitlines(keepends=True) if "qc-agent:begin refine" not in line), encoding="utf-8")
    result = refine.refine(phase1, str(VAHAN))
    assert ".qc-agent/suites/api-contract.yaml" not in result.changed and UPLOAD not in result.patch
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    no_end = phase1 / ".qc-agent" / "perf" / "smoke.js"
    no_end.write_text("".join(line for line in no_end.read_text(encoding="utf-8").splitlines(keepends=True) if "qc-agent:end" not in line), encoding="utf-8")
    assert ".qc-agent/perf/smoke.js" not in refine.refine(phase1, str(VAHAN)).changed and lines


def test_refine_never_writes_into_the_repo(phase1, tmp_path):
    before = tree_hash(phase1)
    refine.refine(phase1, str(VAHAN), suggest_ui=False, out_dir=tmp_path / "out")
    assert tree_hash(phase1) == before


def test_health_already_correct_or_ambiguous_gets_no_proposal(phase1):
    workflow = phase1 / ".github" / "workflows" / "qc.yml"
    text = workflow.read_text(encoding="utf-8").replace('sut_health_path: "/health"', 'sut_health_path: "/api/health"')
    workflow.write_text(text, encoding="utf-8")
    assert ".github/workflows/qc.yml" not in refine.refine(phase1, str(VAHAN)).changed
    spec = json.loads(VAHAN.read_text(encoding="utf-8"))
    spec["paths"]["/v2/health"] = spec["paths"]["/api/health"]
    both = phase1.parent / "both.json"
    both.write_text(json.dumps(spec), encoding="utf-8")
    workflow.write_text(text.replace('sut_health_path: "/api/health"', 'sut_health_path: "/health"'), encoding="utf-8")
    assert ".github/workflows/qc.yml" not in refine.refine(phase1, str(both)).changed      # hai ứng viên: không đoán


def test_no_get_paths_keeps_the_temporary_k6_list(tmp_path, phase1):
    spec = {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}, "paths": {"/x/{id}": {"get": {"responses": {}}}}}
    path = tmp_path / "nogets.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    result = refine.refine(phase1, str(path))
    assert ".qc-agent/perf/smoke.js" not in result.changed and any("bỏ perf-smoke" in n for n in result.notes)
    contract = next(s for s in result.suggestions if s["path"].endswith("api-contract.yaml"))
    assert "không có endpoint nào cần loại" in contract["replacement"]


def _noteboard_openapi(tmp_path, monkeypatch):
    import importlib
    import sys
    monkeypatch.setenv("QC_BUGS", "none")
    monkeypatch.syspath_prepend(str(NOTEBOARD))
    for name in [m for m in sys.modules if m.startswith("toyapp")]:
        del sys.modules[name]
    path = tmp_path / "noteboard-openapi.json"
    path.write_text(json.dumps(importlib.import_module("toyapp.app").app.openapi()), encoding="utf-8")
    return path


def test_noteboard_fixture_after_phase1_gets_a_patch_and_suggestions(tmp_path, monkeypatch):
    sut = tmp_path / "nb"
    shutil.copytree(NOTEBOARD, sut, ignore=shutil.ignore_patterns(".qc-agent", "__pycache__", "runs"))
    init_mod.apply(init_mod.build(init_mod.Options(sut_root=sut, slug="nb")))
    result = refine.refine(sut, str(_noteboard_openapi(tmp_path, monkeypatch)))
    assert result.patch.startswith("--- a/") and result.suggestions
    git_apply(sut, result.patch)
    assert refine.refine(sut, str(_noteboard_openapi(tmp_path, monkeypatch))).patch == ""
    suites = pj.load_suites(sut / ".qc-agent" / "suites")      # vẫn nạp được bằng bộ nạp thật
    assert "api-contract" in suites and "perf-smoke" in suites


# ---------- --suggest-ui ----------

FLOWS = [("open-settings", [("aiTap", "tab Settings")])]


def test_suggest_ui_replaces_only_the_untouched_skeleton_and_logs_into_out(phase1, tmp_path, monkeypatch):
    seen = {}

    def fake(urls, sut_root, **kwargs):
        seen.update(urls=urls, root=Path(sut_root))
        return FLOWS, "test-model"
    monkeypatch.setattr(suggest, "suggest_flows", fake)
    out = tmp_path / "out"
    result = refine.refine(phase1, str(VAHAN), ui_urls=["http://ui:8080"], suggest_ui=True, out_dir=out)
    assert ".qc-agent/midscene/explore.yaml" in result.changed and seen == {"urls": ["http://ui:8080"], "root": out}   # egress log ghi vào --out, không vào repo
    assert "GỢI Ý bởi LLM (test-model)" in result.patch and "tab Settings" in result.patch
    explore = phase1 / ".qc-agent" / "midscene" / "explore.yaml"
    explore.write_text(t.midscene_explore_flow(steps=[("aiTap", "của người")]), encoding="utf-8")     # người đã sửa
    seen.clear()
    again = refine.refine(phase1, str(VAHAN), ui_urls=["http://ui:8080"], suggest_ui=True, out_dir=out)
    assert ".qc-agent/midscene/explore.yaml" not in again.changed and not seen and any("không còn là khung TODO" in n for n in again.notes)


def test_suggest_ui_failure_and_missing_url_are_notes_not_errors(phase1, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise suggest.SuggestError("thiếu biến môi trường MIDSCENE_MODEL_API_KEY")
    monkeypatch.setattr(suggest, "suggest_flows", boom)
    result = refine.refine(phase1, str(VAHAN), ui_urls=["http://ui:8080"], suggest_ui=True, out_dir=tmp_path / "o")
    assert ".qc-agent/midscene/explore.yaml" not in result.changed and any("MIDSCENE_MODEL_API_KEY" in n for n in result.notes)
    assert any("thiếu --ui-url" in n for n in refine.refine(phase1, str(VAHAN), suggest_ui=True, out_dir=tmp_path / "o").notes)
    with pytest.raises(refine.RefineError, match="--out"):
        refine.refine(phase1, str(VAHAN), ui_urls=["http://ui:8080"], suggest_ui=True)


# ---------- CLI ----------

def test_cli_writes_patch_and_suggestions_and_exit_codes(phase1, tmp_path, capsys):
    out = tmp_path / "out"
    argv = ["init", "--refine", "--sut-root", str(phase1), "--openapi", str(VAHAN), "--out", str(out)]
    assert cli_main(argv) == 0
    assert "refine: 3 file đổi" in capsys.readouterr().out
    assert (out / "refine.patch").read_text(encoding="utf-8").startswith("--- a/")
    assert len(json.loads((out / "suggestions.json").read_text(encoding="utf-8"))) == 3
    assert cli_main(["init", "--refine", "--sut-root", str(phase1), "--openapi", str(tmp_path / "nope.json"), "--out", str(out)]) == 3
    assert "LỖI:" in capsys.readouterr().err
    assert cli_main(["init", "--refine", "--sut-root", str(phase1)]) == 3        # thiếu --openapi
    assert cli_main(["init", "--refine", "--sut-root", str(tmp_path / "none"), "--openapi", str(VAHAN)]) == 3


def test_an_unreadable_file_is_reported_not_treated_as_empty(phase1, monkeypatch):
    real = refine._read_lines
    monkeypatch.setattr(refine, "_read_lines", lambda path: None if path.name == "api-contract.yaml" else real(path))
    result = refine.refine(phase1, str(VAHAN))
    assert any("không đọc được .qc-agent/suites/api-contract.yaml" in n for n in result.notes) and ".qc-agent/suites/api-contract.yaml" not in result.changed
