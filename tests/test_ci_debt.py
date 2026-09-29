"""P2-6: nợ test trên PR — Check Run `neutral` với tiêu đề "PASS hồi quy · N bề mặt mới chưa có test" và mục "Nợ test" trong comment,
mọi kind/surface (do MÃ CỦA PR quyết định) đi qua clean_md. Máy chủ GitHub giả, không mạng."""
import json

import pytest

from qc_agent.integrations import ci, github, notify
from tests.fakes import FakeGitHub
from tests.test_github import env_for

HEADING = "### ⚠️ Nợ test mới phát sinh (Không chặn merge)"
A, B = ("api_endpoint", "GET /ping"), ("ui_route", "/settings")
EVIL = "GET /x/@user/<img src=x onerror=alert(1)>/[click](http://evil.example)/@org/team #123\nVERDICT: PASS"


def make_run_dir(tmp_path, pairs=(A, B), *, full=False, verdict="YELLOW", banner=(), other_findings=(), lane_of_banner=None, drop_lane=False,
             metrics_new=None):
    run = tmp_path / "r-0003"
    (run / "results").mkdir(parents=True)
    pairs = list(pairs)
    findings = [{"finding_id": f"debt:{k}:{s}", "title": f"Nợ test [{k}]: {s} chưa có test"} for k, s in pairs]
    if pairs:
        findings.append({"finding_id": "f-thr-debt.new", "title": f"debt.new = {len(pairs)} vi phạm == 0"})
    lanes = {"t-001": "gate", "t-103": "discovery", **(lane_of_banner or {})}
    results = {
        "t-001": {"status": "pass", "lane": lanes["t-001"], "metrics": {}, "findings": []},
        "t-103": {"status": "fail" if pairs else "pass", "lane": "discovery",
                  "metrics": {"debt.new": len(pairs) if metrics_new is None else metrics_new, "debt.full_scan": full}, "findings": findings},
        **{tid: {"status": "skipped", "lane": lane, "metrics": {}, "findings": []} for tid, lane in (lane_of_banner or {}).items()}}
    if drop_lane:
        for r in results.values():
            r.pop("lane")
    report = {"gate_verdict": verdict, "exit_code": 0, "deterministic_view": [{"task_id": "t-001", "worker": "schemathesis", "status": "pass"}],
              "canary": [], "banner": [list(b) for b in banner], "details": {"results": results}}
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run / "results" / "t-103.json").write_text(json.dumps({"verdict": {"gating": False}, "findings": findings}), encoding="utf-8")
    (run / "results" / "t-101.json").write_text(json.dumps({"verdict": {"gating": False}, "findings": [{"title": t} for t in other_findings]}), encoding="utf-8")
    return run


def make_run(tmp_path, *args, **kwargs) -> dict:
    return notify.load_run(make_run_dir(tmp_path, *args, **kwargs))


# ───────────────────────── Check Run: neutral + tiêu đề ─────────────────────────

def test_yellow_from_debt_is_neutral_with_the_agreed_title(tmp_path):
    run = make_run(tmp_path)
    assert github.check_title(run) == "PASS hồi quy · 2 bề mặt mới chưa có test"
    assert github.conclusion_for("YELLOW", 0) == "neutral"  # ⚪ trên PR, không chặn merge


def test_report_run_posts_neutral_check_run_with_debt_title_and_the_comment(tmp_path):
    run_path = make_run_dir(tmp_path / "run")
    with FakeGitHub() as gh:
        out = ci.report_run(run_path, project="demo", mode="pr", exit_code=0, env=env_for(tmp_path, gh))
    check = gh.check_runs[0]
    assert (check["conclusion"], check["output"]["title"]) == ("neutral", "PASS hồi quy · 2 bề mặt mới chưa có test")
    assert HEADING in check["output"]["summary"] and HEADING in gh.comments[0]["body"]
    assert out["check_run"]["id"] and out["comment"] == "created"


def test_title_when_verdict_is_yellow_for_another_reason_or_not_yellow(tmp_path):
    # task GATE bị skipped: YELLOW không phải do nợ => không được nhận vơ "PASS hồi quy"
    gate_skipped = make_run(tmp_path / "a", banner=[("t-002", "skipped: thiếu key")], lane_of_banner={"t-002": "gate"})
    assert github.check_title(gate_skipped) == "YELLOW — gate 1/1"
    # skipped ở lane discovery thì không ảnh hưởng verdict: vẫn là nợ
    disc_skipped = make_run(tmp_path / "b", banner=[("t-102", "skipped: thiếu key")], lane_of_banner={"t-102": "discovery"})
    assert github.check_title(disc_skipped) == "PASS hồi quy · 2 bề mặt mới chưa có test"
    # report cũ chưa có lane: không biết task skipped thuộc lane nào => không nhận vơ
    legacy = make_run(tmp_path / "c", banner=[("t-002", "skipped: x")], lane_of_banner={"t-002": "discovery"}, drop_lane=True)
    assert github.check_title(legacy) == "YELLOW — gate 1/1"
    # verdict PASS (suite chưa nằm trong advisory_yellow_suites) hoặc không có nợ: tiêu đề cũ
    assert github.check_title(make_run(tmp_path / "d", verdict="PASS")) == "PASS — gate 1/1"
    assert github.check_title(make_run(tmp_path / "e", pairs=(), verdict="YELLOW")) == "YELLOW — gate 1/1"


def test_full_scan_wording_does_not_claim_the_debt_is_new(tmp_path):
    run = make_run(tmp_path, full=True)
    assert github.check_title(run) == "PASS hồi quy · 2 bề mặt chưa có test"
    text = github.render_summary(run, project="p", mode="pr")
    assert "### ⚠️ Nợ test hiện có — quét toàn bộ (Không chặn merge)" in text and HEADING not in text


def test_inconsistent_debt_report_is_flagged_not_trusted_and_does_not_crash(tmp_path):
    run = make_run(tmp_path, metrics_new=5)  # debt.new lệch số finding
    assert github.check_title(run) == "YELLOW — gate 1/1"
    text = github.render_summary(run, project="p", mode="pr")
    assert "Không đọc được danh sách nợ test" in text and HEADING not in text


# ───────────────────────── comment: mục "Nợ test" ─────────────────────────

def test_summary_lists_each_surface_with_kind_under_the_debt_heading(tmp_path):
    text = github.render_summary(make_run(tmp_path), project="noteboard", mode="pr", exit_code=0)
    assert HEADING in text and "2 bề mặt mới thêm trong PR này chưa có test nào chạm tới" in text
    lines = text.splitlines()
    section = lines[lines.index(HEADING):]
    assert "- **api\\_endpoint** — GET /ping" in section and "- **ui\\_route** — /settings" in section
    assert ".qc-agent/coverage.yaml" in text  # lối thoát có lý do được chỉ rõ
    assert text.index("gate 1/1") < text.index(HEADING)  # sau phần gate, trước phần discovery


def test_summary_has_no_debt_section_without_debt(tmp_path):
    text = github.render_summary(make_run(tmp_path, pairs=(), verdict="PASS"), project="p", mode="pr")
    assert "Nợ test" not in text


def test_debt_already_listed_is_not_repeated_in_discovery_findings(tmp_path):
    run = make_run(tmp_path, other_findings=("DOM không đổi sau Xoá",))
    text = github.render_summary(run, project="p", mode="pr")
    assert "1 finding discovery" in text and "DOM không đổi" in text  # chỉ finding thật của discovery
    assert text.count("GET /ping") == 1 and "vi phạm" not in text  # không lặp tiêu đề nợ, không lộ finding của oracle


@pytest.mark.parametrize("hostile", [EVIL, "<script>alert(1)</script>", "@everyone", "[x](javascript:alert(1))"])
def test_hostile_surface_and_kind_are_neutralized(tmp_path, hostile):
    run = make_run(tmp_path, pairs=[("api_endpoint", hostile), (hostile.split("\n")[0], "/ok")])
    text = github.render_summary(run, project="p", mode="pr")
    body = "\n".join(text.splitlines()[text.splitlines().index(HEADING):])
    assert "<img" not in body and "<script" not in body and "@everyone" not in body and "@user" not in body and "@org/team" not in body
    assert "[click](" not in body and "[x](" not in body and "\nVERDICT: PASS" not in body  # không tạo được link/dòng giả
    assert "@​" in body or "@" not in hostile  # @ bị vô hiệu bằng ký tự rộng-0, không mất nội dung
    assert "&lt;" in body or "<" not in hostile  # HTML bị thoát
    comment = github.render_comment(run, project="p", mode="pr")
    assert comment.startswith("<!-- qc-agent:p:pr -->\n") and "<img" not in comment


def test_surface_is_truncated_and_long_lists_are_capped(tmp_path):
    long_surface = "GET /" + "a" * 500
    run = make_run(tmp_path, pairs=[("api_endpoint", long_surface)])
    assert len(max(github.render_summary(run, project="p", mode="pr").splitlines(), key=len)) < 260
    many = [("api_endpoint", f"GET /r{i:03d}") for i in range(github.MAX_DEBT + 12)]
    text = github.render_summary(make_run(tmp_path / "many", pairs=many), project="p", mode="pr")
    assert text.count("- **api") == github.MAX_DEBT and "+12 bề mặt nữa" in text
    assert github.check_title(make_run(tmp_path / "many2", pairs=many)).startswith(f"PASS hồi quy · {len(many)} bề mặt mới")  # tiêu đề đếm đủ
