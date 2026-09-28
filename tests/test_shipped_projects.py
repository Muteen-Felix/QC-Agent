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


def test_default_policy_ships_and_is_valid_on_its_own():
    default = PROJECTS / "_default.yaml"
    assert default.is_file() and t.TODO not in default.read_text(encoding="utf-8")
    assert pj.load_project("some-new-repo", PROJECTS)["modes"]["pr"]["blocking_suites"] == ["api-contract"]
    assert "_default" not in pj.list_projects(PROJECTS)


_VAHAN_BEFORE_STEP_30 = {   # nội dung configs/projects/vahan-rpa.yaml ngay trước khi rút gọn thành đăng ký mỏng (P1)
    "slug": "vahan-rpa", "name": "VAHAN Report Automation", "repo": "Muteen-Felix/vahan-rpa",
    "modes": {"pr": {"blocking_suites": ["api-contract"], "advisory_suites": ["perf-smoke", "ui-explore"], "on_skipped_gate_task": "fail"},
              "manual": {"suites": "*"}}}
# nội dung ngay trước P2-8 (coverage-debt chưa bật): dùng để chứng minh P2-8 CHỈ thêm coverage-debt/advisory_yellow_suites,
# không đụng gì khác của đăng ký mỏng (blocking_suites, on_skipped_gate_task, mode manual vẫn kế thừa nguyên từ _default.yaml)
_VAHAN_BEFORE_P2_8 = _VAHAN_BEFORE_STEP_30


def test_vahan_rpa_thin_registration_gives_the_identical_plan_as_before_reduction():
    from tests.projkit import task
    suites = {name: {"name": name, "sha256": "0" * 64, "tasks": [task(f"t-{name}", lane=lane)]}
              for name, lane in (("api-contract", "gate"), ("perf-smoke", "discovery"), ("ui-explore", "discovery"), ("extra", "gate"))}
    before = {**_VAHAN_BEFORE_STEP_30, "suites_dir": ".qc-agent/suites"}
    after = pj.load_project("vahan-rpa", PROJECTS)
    assert after != before  # P2-8 đã bật coverage-debt: KHÔNG còn giống đăng ký mỏng thuần từ P1
    assert pj.build_plan(after, "pr", suites) != pj.build_plan(before, "pr", suites)  # advisory_yellow_suites: meta["yellow_task_ids"] khác
    # mode manual: suites: "*" chọn theo suites TRUYỀN VÀO (không có coverage-debt ở đây), không đọc advisory_suites => plan vẫn giống hệt
    assert pj.build_plan(after, "manual", suites) == pj.build_plan(before, "manual", suites)


def test_vahan_rpa_p2_8_only_adds_coverage_debt_advisory_yellow_and_keeps_the_rest_inherited():
    """P2-8: chỉ modes.pr.advisory_suites (+ coverage-debt) và advisory_yellow_suites là mới; blocking_suites/on_skipped_gate_task/manual
    vẫn kế thừa nguyên từ _default.yaml (không bị khai lại ở vahan-rpa.yaml, deep_merge giữ nguyên)."""
    after = pj.load_project("vahan-rpa", PROJECTS)
    before = _VAHAN_BEFORE_P2_8
    assert after["modes"]["pr"]["advisory_suites"] == [*before["modes"]["pr"]["advisory_suites"], "coverage-debt"]
    assert after["modes"]["pr"]["advisory_yellow_suites"] == ["coverage-debt"]
    for key in ("blocking_suites", "on_skipped_gate_task"):
        assert after["modes"]["pr"][key] == before["modes"]["pr"][key]
    assert after["modes"]["manual"] == before["modes"]["manual"]
    before_full = {**before, "suites_dir": ".qc-agent/suites"}  # resolve_project tự điền mặc định này (không có trong _VAHAN_BEFORE_P2_8)
    assert {k: v for k, v in after.items() if k != "modes"} == {k: v for k, v in before_full.items() if k != "modes"}
    raw = (PROJECTS / "_default.yaml").read_text(encoding="utf-8")
    assert "coverage-debt" not in raw and "advisory_yellow_suites" not in raw  # P2-8: KHÔNG đụng _default.yaml (yêu cầu đề bài)
