"""Các config project ĐÓNG GÓI trong image (configs/projects/*.yaml) phải luôn hợp lệ và không còn dấu chưa hoàn tất."""
from pathlib import Path

import pytest
import yaml

from qc_agent.core import project as pj
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import templates as t

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "configs" / "projects"
SLUGS = sorted(p.stem for p in PROJECTS.glob("*.yaml") if not p.stem.startswith("_"))


def test_at_least_the_reference_and_vahan_projects_ship():
    assert {"noteboard", "vahan-rpa"} <= set(SLUGS)


@pytest.mark.parametrize("slug", SLUGS)
def test_each_shipped_project_loads_and_has_no_unfinished_marker(slug):
    cfg = pj.load_project(slug, PROJECTS)
    assert cfg["slug"] == slug and t.TODO not in (PROJECTS / f"{slug}.yaml").read_text(encoding="utf-8")
    assert cfg["modes"]["pr"].get("blocking_suites"), "mode pr phải có ít nhất một suite chặn merge, nếu không gate luôn PASS"


def test_all_shipped_projects_load_together_like_service_startup_does():
    assert set(pj.list_projects(PROJECTS)) == set(SLUGS)


def test_vahan_rpa_policy_is_exactly_what_init_generates_from_its_openapi(tmp_path):
    """Chống lệch: config đóng gói của vahan-rpa = kết quả `qc-agent init` từ OpenAPI của nó (chỉ khác lời chú thích)."""
    sut = tmp_path / "sut"
    sut.mkdir()
    plan = init_mod.build(init_mod.Options(sut_root=sut, slug="vahan-rpa", repo="Muteen-Felix/vahan-rpa", name="VAHAN Report Automation",
                                           openapi_source=str(ROOT / "tests" / "fixtures" / "openapi" / "vahan-rpa.json"),
                                           projects_dir=tmp_path / "p", ui_dockerfile="apps/web-ui/Dockerfile"))
    generated = yaml.safe_load(next(f.content for f in plan.files if f.label.startswith("<projects>")))
    resolved = pj.load_project("vahan-rpa", PROJECTS)   # đăng ký mỏng + _default
    assert {k: resolved[k] for k in generated} == generated


def test_default_policy_ships_and_is_valid_on_its_own():
    default = PROJECTS / "_default.yaml"
    assert default.is_file() and t.TODO not in default.read_text(encoding="utf-8")
    assert pj.load_project("some-new-repo", PROJECTS)["modes"]["pr"]["blocking_suites"] == ["api-contract"]
    assert "_default" not in pj.list_projects(PROJECTS)


_VAHAN_BEFORE_STEP_30 = {   # nội dung configs/projects/vahan-rpa.yaml ngay trước khi rút gọn thành đăng ký mỏng (P1)
    "slug": "vahan-rpa", "name": "VAHAN Report Automation", "repo": "Muteen-Felix/vahan-rpa",
    "modes": {"pr": {"blocking_suites": ["api-contract"], "advisory_suites": ["perf-smoke", "ui-explore"], "on_skipped_gate_task": "fail"},
              "manual": {"suites": "*"}}}


def test_vahan_rpa_thin_registration_gives_the_identical_plan_as_before_reduction():
    from tests.projkit import task
    suites = {name: {"name": name, "sha256": "0" * 64, "tasks": [task(f"t-{name}", lane=lane)]}
              for name, lane in (("api-contract", "gate"), ("perf-smoke", "discovery"), ("ui-explore", "discovery"), ("extra", "gate"))}
    before = {**_VAHAN_BEFORE_STEP_30, "suites_dir": ".qc-agent/suites"}
    after = pj.load_project("vahan-rpa", PROJECTS)
    assert after == before
    for mode in ("pr", "manual"):
        assert pj.build_plan(after, mode, suites) == pj.build_plan(before, mode, suites)
