"""Ràng buộc tĩnh của Dockerfile image qc-agent (không cần Docker)."""
from pathlib import Path

TEXT = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")


def test_puppeteer_finds_the_playwright_chromium_through_a_stable_symlink():
    """Midscene CLI (Puppeteer) báo 'Could not find Chrome' nếu thiếu: mọi task Midscene trong image lỗi, canary fail vì lý do sai."""
    assert "PUPPETEER_EXECUTABLE_PATH=/opt/ms-playwright/chrome" in TEXT and "ln -s" in TEXT and "test -x /opt/ms-playwright/chrome" in TEXT


def test_the_image_records_its_git_sha_late_so_it_does_not_bust_the_cache_of_heavy_layers():
    assert "ARG QC_AGENT_GIT_SHA=unknown" in TEXT and "ENV QC_AGENT_GIT_SHA=${QC_AGENT_GIT_SHA}" in TEXT
    assert TEXT.index("ARG QC_AGENT_GIT_SHA") > TEXT.index("playwright install") and TEXT.index("ARG QC_AGENT_GIT_SHA") < TEXT.index("USER qc")


def test_the_image_still_runs_as_a_non_root_user():
    assert TEXT.rindex("USER qc") > TEXT.index("useradd")
