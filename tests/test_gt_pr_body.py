"""groundtruth/pr_body.py và `gt info` (S1-07): thân PR từ summary, và định danh PRD offline để workflow đặt tên nhánh."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from qc_agent.core.cli import main as cli_main
from qc_agent.groundtruth import pr_body
from qc_agent.integrations.github import ZWSP
from tests.fakes import FakeAnthropic

ROOT = Path(__file__).resolve().parent.parent
PRD_FILE = ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md"
OPENAPI = ROOT / "tests" / "fixtures" / "openapi" / "noteboard.json"
RESPONSE = ROOT / "tests" / "fixtures" / "llm" / "gt_noteboard_response.json"
KEY = "sk-ant-FAKE-KEY-0123456789"


def summary(**over):
    base = {"command": "generate", "prd_id": "noteboard", "prd_sha256": "b" * 64, "model": "claude-sonnet-5", "prompt_version": "gt-generate/1",
            "stories": 4, "acs": 25, "test_cases": 33, "by_status": {"draft": 33, "approved": 0, "rejected": 0}, "dropped_test_cases": 5,
            "uncovered_acs": ["AC-1.8"], "orphans": ["AC-3.5"], "warnings": ["TC #34 bị bỏ: ac_refs không có trong PRD: AC-9.9"]}
    return {**base, **over}


def test_a_generate_body_has_the_counts_the_orphans_and_the_qa_checklist():
    body = pr_body.render(summary())
    assert body.startswith("## Ground-Truth cho `noteboard`: bản nháp do LLM đề xuất")
    assert "| Story / AC | 4 / 25 |" in body and "draft 33, approved 0, rejected 0" in body and "| TC bị bỏ do vi phạm | 5 |" in body
    assert "AC mồ côi cần thêm TC hoặc ghi vào `uncovered_acs`: AC-3.5" in body and "không kiểm được bằng HTTP: AC-1.8" in body
    for item in ("`draft` → `approved`", "`origin: qa`", "`module-map.yaml`", "`status` của catalog", "`gt validate`"):
        assert f"- [ ] " in body and item in body
    assert body.count("- [ ]") >= 6 and "tests_gt/" in body and "<details>" in body and "34 bị bỏ" in body and body.endswith("\n")
    assert "PRD sha256 `" + "b" * 64 + "` · model `claude-sonnet-5` · prompt `gt-generate/1`" in body


def test_orphan_and_uncovered_lines_only_appear_when_there_are_any():
    body = pr_body.render(summary(orphans=[], uncovered_acs=[], warnings=[]))
    assert "AC mồ côi cần" not in body and "không kiểm được bằng HTTP:" not in body and "<details>" not in body


def test_a_regen_body_reports_the_merge_and_the_ac_that_disappeared():
    merge = {"kept": 7, "added": ["TC-a", "TC-b"], "removed_drafts": ["TC-c"], "lost_acs": {"TC-AC-2.6-abc123": ["AC-2.6"]}}
    body = pr_body.render(summary(command="regen", merge=merge, by_status={"draft": 2, "approved": 5, "rejected": 2}))
    assert "cập nhật theo PRD mới" in body and "Giữ nguyên **7** TC" in body and "thêm **2** TC draft mới" in body and "thay **1** TC draft cũ" in body
    assert "`TC-AC-2.6-abc123` trỏ tới AC không còn trong PRD (AC-2.6)" in body and "không sửa TC" in body and "gate sẽ báo lỗi" in body


def test_untrusted_strings_cannot_mention_people_link_issues_or_inject_html():
    evil = summary(prd_id="x`@org/team", warnings=["<script>alert(1)</script> @admin fixes #123 [click](http://evil) `code` **bold**"],
                   orphans=["AC-1<img src=x onerror=1>", "@everyone"], model="m\nINJECT", prompt_version="v|w")
    body = pr_body.render(evil)
    assert "<script>" not in body and "<img" not in body and "@admin" not in body and "@everyone" not in body and "@org/team" not in body
    assert f"@{ZWSP}admin" in body and "&lt;script&gt;" in body
    assert "\nINJECT" not in body and "http://evil" not in body.replace("\\(http://evil\\)", "")


def test_long_lists_are_truncated_and_the_body_stays_under_the_github_limit():
    many = summary(orphans=[f"AC-{n}" for n in range(500)], warnings=[f"w{n}" * 40 for n in range(500)], uncovered_acs=[f"AC-U{n}" for n in range(500)])
    body = pr_body.render(many)
    assert "… (+475)" in body and "và 475 cảnh báo nữa" in body and len(body) <= pr_body.MAX_BODY


@pytest.mark.parametrize("bad", [None, [], {}, {"command": "validate"}, {"command": "generate"}, summary(by_status=None)])
def test_a_summary_that_is_not_from_generate_or_regen_is_refused(bad):
    with pytest.raises((ValueError, KeyError, TypeError)):
        pr_body.render(bad)


def test_the_module_prints_the_body_and_fails_cleanly_on_bad_input(tmp_path):
    good = tmp_path / "s.json"
    good.write_text(json.dumps(summary()), encoding="utf-8")
    ok = subprocess.run([sys.executable, "-m", "qc_agent.groundtruth.pr_body", str(good)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert ok.returncode == 0 and ok.stdout.startswith("## Ground-Truth cho `noteboard`") and "\r" not in ok.stdout
    bad = tmp_path / "bad.json"
    bad.write_text('{"command": "generate"', encoding="utf-8")
    for argv in ([str(bad)], [str(tmp_path / "missing.json")], [], [str(good), "extra"]):
        done = subprocess.run([sys.executable, "-m", "qc_agent.groundtruth.pr_body", *argv], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert done.returncode == 3 and done.stdout == "" and done.stderr.strip()


def test_the_body_of_a_real_generate_and_regen_summary(tmp_path, capsys, monkeypatch):
    sut = tmp_path / "sut"
    (sut / "docs" / "prd").mkdir(parents=True)
    (sut / "docs" / "prd" / "noteboard-prd.md").write_text(PRD_FILE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.chdir(tmp_path)
    with FakeAnthropic(RESPONSE, key=KEY) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        for command, path in (("generate", "one.json"), ("regen", "two.json")):
            args = ["gt", command, "--prd", str(sut / "docs/prd/noteboard-prd.md"), "--sut-root", str(sut), "--openapi", str(OPENAPI),
                    "--egress-dir", str(tmp_path / "eg"), "--summary-json", str(tmp_path / path)]
            assert cli_main(args) == 0
            body = pr_body.render(json.loads((tmp_path / path).read_text(encoding="utf-8")))
            assert "AC-3.5" in body and "Story / AC | 4 / 25" in body and "Noteboard là dịch vụ" not in body
            assert ("Merge với bản cũ" in body) == (command == "regen")
    capsys.readouterr()


# ---------------- gt info ----------------

def test_gt_info_prints_the_prd_identity_offline_without_a_key_or_any_request(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with FakeAnthropic(RESPONSE) as server:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
        capsys.readouterr()
        assert cli_main(["gt", "info", "--prd", str(PRD_FILE), "--openapi", str(OPENAPI)]) == 0
        out = capsys.readouterr().out
        assert server.count == 0
    info = json.loads(out)
    assert info == {"prd_id": "noteboard", "prd_sha256": info["prd_sha256"], "format": "markdown", "stories": 4, "acs": 25, "endpoints": 5, "warnings": []}
    assert len(info["prd_sha256"]) == 64


def test_gt_info_gives_an_id_that_is_safe_for_a_branch_name(tmp_path, capsys):
    prd = tmp_path / "PRD của Nhóm; rm -rf.md"
    prd.write_text("# Sổ ghi chú\n\n## US-1: A\n\n### Acceptance Criteria\n\n- AC-1.1: x\n", encoding="utf-8")
    capsys.readouterr()
    assert cli_main(["gt", "info", "--prd", str(prd)]) == 0
    prd_id = json.loads(capsys.readouterr().out)["prd_id"]
    import re
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", prd_id), prd_id


def test_gt_info_fails_with_exit_3_on_a_missing_prd(tmp_path, capsys):
    capsys.readouterr()
    assert cli_main(["gt", "info", "--prd", str(tmp_path / "khong-co.md")]) == 3
    assert capsys.readouterr().err.strip()
