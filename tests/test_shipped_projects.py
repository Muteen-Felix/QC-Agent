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


def test_at_least_the_reference_project_ships():
    assert {"noteboard"} <= set(SLUGS)


@pytest.mark.parametrize("slug", SLUGS)
def test_each_shipped_project_loads_and_has_no_unfinished_marker(slug):
    cfg = pj.load_project(slug, PROJECTS)
    assert cfg["slug"] == slug and t.TODO not in (PROJECTS / f"{slug}.yaml").read_text(encoding="utf-8")
    assert cfg["modes"]["pr"].get("blocking_suites"), "mode pr phải có ít nhất một suite chặn merge, nếu không gate luôn PASS"


def test_all_shipped_projects_load_together_like_service_startup_does():
    assert set(pj.list_projects(PROJECTS)) == set(SLUGS)


def test_default_policy_ships_and_is_valid_on_its_own():
    default = PROJECTS / "_default.yaml"
    assert default.is_file() and t.TODO not in default.read_text(encoding="utf-8")
    assert pj.load_project("some-new-repo", PROJECTS)["modes"]["pr"]["blocking_suites"] == ["api-contract", "sast", "secrets", "deps"]
    assert "_default" not in pj.list_projects(PROJECTS)


def test_vahan_rpa_is_unregistered_and_falls_back_to_the_default_policy():
    """vahan-rpa.yaml đã bị xoá chủ đích (không còn đăng ký riêng): vahan-rpa giờ là repo CHƯA ĐĂNG KÝ như mọi repo mới,
    dùng nguyên _default.yaml (không còn khai coverage-debt/advisory_yellow_suites riêng cho nó nữa)."""
    assert not (PROJECTS / "vahan-rpa.yaml").exists()
    cfg, info = pj.resolve_project("vahan-rpa", PROJECTS)
    default_cfg, default_info = pj.resolve_project("some-other-unregistered-repo", PROJECTS)
    assert info["source"] == "default"
    assert {k: v for k, v in cfg.items() if k != "slug"} == {k: v for k, v in default_cfg.items() if k != "slug"}
