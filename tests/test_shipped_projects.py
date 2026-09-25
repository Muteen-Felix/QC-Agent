"""Các config project ĐÓNG GÓI trong image (configs/projects/*.yaml) phải luôn hợp lệ và không còn dấu chưa hoàn tất."""
from pathlib import Path

import pytest
import yaml

from qc_agent.core import project as pj
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import templates as t

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "configs" / "projects"
SLUGS = sorted(p.stem for p in PROJECTS.glob("*.yaml"))


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
    assert yaml.safe_load((PROJECTS / "vahan-rpa.yaml").read_text(encoding="utf-8")) == generated
