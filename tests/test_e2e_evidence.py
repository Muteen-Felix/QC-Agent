"""tools/e2e (S4-06, phần cục bộ): đối chiếu bằng chứng với bảng kỳ vọng, tổng hợp 10 lượt, đo thời gian, và script bash chạy với `gh`/`curl` GIẢ.

Dữ liệu ở đây do test tự dựng theo HÌNH DẠNG kỳ vọng của GitHub/Jira; nó chứng minh logic đối chiếu, KHÔNG chứng minh GitHub thật hành xử như vậy.
Mọi 'bằng chứng' tạo ở đây mang nguồn `fake`/`local` hoặc là dữ liệu dựng: không được dùng để tick DoD.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_e2e_tools import DIGEST, SHA, full_intake
from tools.e2e import collect, common, sandbox, scenarios, stability, timing

ROOT = Path(__file__).resolve().parent.parent
FP = "ab12cd34ef56ab12"
USER_MAP = {"dev1": "acc-1"}
KW = dict(project="noteboard", dev="dev1", user_map=USER_MAP, non_qa="outsider", workers="semgrep")


def put(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")


def edit(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    put(path, data)


def check_run(conclusion, title, summary="wallclock 40s · LLM: 1 650 in (0 từ cache) / 160 out · ~$0.0025", *, real=True):
    run = {"name": "qc-agent / noteboard", "conclusion": conclusion, "output": {"title": title, "summary": summary}}
    if real:
        run.update(app={"slug": "github-actions"}, html_url="https://github.com/acme/sandbox/runs/1")
    return {"check_runs": [run, {"name": "qc / qc-agent / noteboard", "conclusion": conclusion}]}


def artifact(root: Path, *, verdict, exit_code, counts, findings=(), source="llm", usage=(), workers=("gitleaks", "semgrep"), trigger="pr", fallback=None, suites=("sast",), floor=("gitleaks", "semgrep")):
    put(root / "artifact" / "selection.json", {"version": 1, "trigger_type": trigger, "source": source, "full_set": False, "floor": list(floor), "workers": list(workers),
                                              "suites": list(suites), "fallback_reason": fallback})
    put(root / "artifact" / "r-0001" / "report.json", {"gate_verdict": verdict, "exit_code": exit_code, "severity_counts": counts, "findings": list(findings)})
    if usage:
        put(root / "artifact" / "r-0001" / "llm_usage.json", list(usage))


MARK = "<!-- qc-agent:noteboard:pr -->\n"
SELECT_ROW = {"purpose": "diff-select", "est_usd": 0.0025, "cache_hit": False}
CACHED_ROW = {"purpose": "diff-select", "est_usd": 0.0025, "cache_hit": True}
LOW = {"severity": "low", "path": "toyapp/app.py", "line": 12, "fingerprint": FP, "rule_id": "api_endpoint"}


def write_evidence(root: Path, source: str = "github", *, real_checks: bool = True) -> Path:
    collect.write_meta(root, repo="acme/sandbox", source=source, image=DIGEST, qc_ref=SHA)
    a = root / "A"
    put(a / "pr.json", {"number": 1, "state": "OPEN", "headRefName": "qc-agent/gt/noteboard-prd", "files": [{"path": ".qc-agent/ground-truth/test-cases.yaml"}]})
    put(a / "runs.json", [{"conclusion": "success", "event": "push"}])
    put(a / "artifact" / "qc-groundtruth-x-1" / "llm_usage.json", [{"purpose": "gt-generate", "est_usd": 0.1, "cache_hit": False}])
    b = root / "B"
    put(b / "protection.json", {"required_pull_request_reviews": {"require_code_owner_reviews": True, "required_approving_review_count": 1}})
    put(b / "push-attempt.txt", "Thu Oct  8 10:00:00 UTC 2026\nremote: error: GH006: Protected branch update failed for refs/heads/main.\n ! [remote rejected] HEAD -> main (protected branch hook declined)\n")
    put(b / "merge-attempt.json", {"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED", "mergeStateStatus": "BLOCKED", "author": {"login": "outsider"}, "files": [{"path": ".qc-agent/NOTE.md"}]})
    put(b / "qa-merge.json", {"state": "MERGED", "mergedBy": {"login": "qa1"}, "reviews": [{"state": "APPROVED"}]})
    c = root / "C"
    put(c / "checks.json", check_run("failure", "❌ BLOCKED · 2 critical, 0 medium", real=real_checks))
    put(c / "comments.json", [{"body": MARK + "VERDICT BLOCKED"}])
    artifact(c, verdict="BLOCKED", exit_code=1, counts={"critical": 2, "medium": 0, "low": 0}, usage=[SELECT_ROW])
    d = root / "D"
    put(d / "checks.json", check_run("success", "✅ PASS · 1 cảnh báo Low", real=real_checks))
    put(d / "comments.json", [{"body": MARK + "PASSED_WITH_WARNINGS"}])
    put(d / "reviews.json", [{"id": 7}])
    put(d / "review-comments.json", [{"path": "toyapp/app.py", "line": 12}])
    put(d / "jira.json", {"issues": [{"fields": {"labels": [f"qcagent-{FP}", "x"], "assignee": {"accountId": "acc-1"}}}]})
    artifact(d, verdict="PASSED_WITH_WARNINGS", exit_code=0, counts={"critical": 0, "medium": 0, "low": 1}, findings=[LOW], usage=[SELECT_ROW])
    rr = d / "rerun"
    put(rr / "comments.json", [{"body": MARK + "PASSED_WITH_WARNINGS"}])
    put(rr / "reviews.json", [{"id": 7}])
    put(rr / "jira.json", json.loads((d / "jira.json").read_text(encoding="utf-8")))
    artifact(rr, verdict="PASSED_WITH_WARNINGS", exit_code=0, counts={"critical": 0, "medium": 0, "low": 1}, findings=[LOW], source="cache", usage=[CACHED_ROW])
    e = root / "E"
    put(e / "job.json", {"event": "workflow_dispatch", "conclusion": "success"})
    artifact(e, verdict="PASSED", exit_code=0, counts={"critical": 0, "medium": 0, "low": 0}, source="manual", trigger="manual", workers=("semgrep",), floor=())
    return root


def rows(report, scenario=None):
    return {(r.scenario, r.check): r for r in report.rows if scenario in (None, r.scenario)}


def status(report, scenario, check):
    return rows(report)[(scenario, check)].status


# ───────────────────────── đối chiếu: đường xanh và nguồn bằng chứng ─────────────────────────

def test_complete_github_evidence_passes_every_row(tmp_path):
    report = collect.evaluate(write_evidence(tmp_path), **KW)
    assert [r for r in report.rows if r.status != collect.PASS] == []
    assert report.github_evidence and "KHÔNG PHẢI BẰNG CHỨNG" not in report.render()
    assert "KHÔNG tick DoD" in report.render()


def test_expected_checks_are_exactly_the_scenarios_table(tmp_path):
    report = collect.evaluate(write_evidence(tmp_path), **KW)
    expected = {(sc, e.check) for sc, items in scenarios.EXPECT.items() for e in items}
    assert {(r.scenario, r.check) for r in report.rows if r.scenario != "*"} == expected


@pytest.mark.parametrize("source", ["fake", "local"])
def test_non_github_source_is_never_dod_evidence(tmp_path, source):
    report = collect.evaluate(write_evidence(tmp_path, source), **KW)
    assert not report.github_evidence
    prov = rows(report)[("*", "provenance")]
    assert prov.status == collect.PENDING and collect.NOT_DOD in prov.detail
    assert collect.NOT_DOD in report.render()


def test_declared_github_but_fake_shaped_checks_is_a_failure(tmp_path):
    report = collect.evaluate(write_evidence(tmp_path, "github", real_checks=False), **KW)
    prov = rows(report)[("*", "provenance")]
    assert prov.status == collect.FAIL and "app.slug" in prov.detail


def test_missing_meta_is_pending(tmp_path):
    write_evidence(tmp_path)
    (tmp_path / "meta.json").unlink()
    report = collect.evaluate(tmp_path, **KW)
    assert rows(report)[("*", "provenance")].status == collect.PENDING and not report.github_evidence


def test_empty_directory_has_no_pass_at_all(tmp_path):
    report = collect.evaluate(tmp_path, **KW)
    assert report.counts()[collect.PASS] == 0 and report.counts()[collect.FAIL] == 0
    assert report.counts()[collect.PENDING] >= 15


def test_write_meta_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError):
        collect.write_meta(tmp_path, repo="o/r", source="staging")


# Xoá từng file bằng chứng: dòng tương ứng phải là PENDING, không bao giờ PASS (không có "xanh vì thiếu").
MISSING = [("A", "pr.json", "pr_opened"), ("A", "runs.json", "workflow_ok"), ("A", "artifact", "one_llm_call"), ("B", "protection.json", "protection_on"),
           ("B", "push-attempt.txt", "push_rejected"), ("B", "merge-attempt.json", "merge_blocked"), ("B", "qa-merge.json", "qa_merge_ok"), ("C", "checks.json", "check_failure"),
           ("C", "artifact", "report_blocked"), ("C", "artifact", "selection_llm"), ("C", "comments.json", "comment_sticky"), ("C", "checks.json", "cost_line"),
           ("D", "checks.json", "check_success"), ("D", "artifact", "report_warn"), ("D", "review-comments.json", "inline_comment"), ("D", "jira.json", "jira_ticket"),
           ("D", "rerun", "rerun_cache"), ("E", "job.json", "dispatch"), ("E", "artifact", "exact_workers")]


@pytest.mark.parametrize("scenario,name,check", MISSING)
def test_missing_evidence_file_makes_its_row_pending_never_pass(tmp_path, scenario, name, check):
    write_evidence(tmp_path)
    target = tmp_path / scenario / name
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    assert status(collect.evaluate(tmp_path, **KW), scenario, check) == collect.PENDING


# ───────────────────────── đối chiếu: các cách sai phải thành FAIL ─────────────────────────

FAILS = {
    "A.pr_wrong_branch": ("A", "pr_opened", lambda r: edit(r / "A" / "pr.json", lambda d: d.update(headRefName="feature/x")), None),
    "A.no_gt_files": ("A", "pr_opened", lambda r: edit(r / "A" / "pr.json", lambda d: d.update(files=[{"path": "README.md"}])), None),
    "A.workflow_failed": ("A", "workflow_ok", lambda r: put(r / "A" / "runs.json", [{"conclusion": "failure"}]), "infra"),
    "A.two_llm_calls": ("A", "one_llm_call", lambda r: put(r / "A" / "artifact" / "qc-groundtruth-x-1" / "llm_usage.json", [{"purpose": "gt-generate", "est_usd": 0.1}] * 2), "llm"),
    "B.no_codeowner": ("B", "protection_on", lambda r: edit(r / "B" / "protection.json", lambda d: d["required_pull_request_reviews"].update(require_code_owner_reviews=False)), None),
    "B.push_accepted": ("B", "push_rejected", lambda r: put(r / "B" / "push-attempt.txt", "Thu Oct  8 10:00:00 UTC 2026\nTo github.com:o/r.git\n   abc..def  HEAD -> main\n"), None),
    "B.merge_not_blocked": ("B", "merge_blocked", lambda r: edit(r / "B" / "merge-attempt.json", lambda d: d.update(reviewDecision="APPROVED", mergeStateStatus="CLEAN")), None),
    "B.pr_not_touching_qc": ("B", "merge_blocked", lambda r: edit(r / "B" / "merge-attempt.json", lambda d: d.update(files=[{"path": "README.md"}])), None),
    "B.wrong_author": ("B", "merge_blocked", lambda r: edit(r / "B" / "merge-attempt.json", lambda d: d.update(author={"login": "qa1"})), None),
    "B.qa_not_merged": ("B", "qa_merge_ok", lambda r: edit(r / "B" / "qa-merge.json", lambda d: d.update(state="OPEN")), None),
    "C.check_green": ("C", "check_failure", lambda r: put(r / "C" / "checks.json", check_run("success", "✅ PASS")), None),
    "C.no_critical_in_title": ("C", "check_failure", lambda r: put(r / "C" / "checks.json", check_run("failure", "❌ BLOCKED · 0 critical, 1 medium")), None),
    "C.report_passed": ("C", "report_blocked", lambda r: edit(r / "C" / "artifact" / "r-0001" / "report.json", lambda d: d.update(gate_verdict="PASSED", exit_code=0)), None),
    "C.select_fell_back_to_full_set": ("C", "selection_llm", lambda r: edit(r / "C" / "artifact" / "selection.json", lambda d: d.update(source="fallback", fallback_reason="timeout")), "llm"),
    "C.select_has_no_floor": ("C", "selection_llm", lambda r: edit(r / "C" / "artifact" / "selection.json", lambda d: d.update(floor=[])), "product"),
    "C.no_usage_row": ("C", "selection_llm", lambda r: (r / "C" / "artifact" / "r-0001" / "llm_usage.json").unlink(), "product"),
    "C.unpriced_usage": ("C", "selection_llm", lambda r: put(r / "C" / "artifact" / "r-0001" / "llm_usage.json", [{"purpose": "diff-select", "est_usd": None, "cache_hit": False}]), "llm"),
    "C.two_sticky_comments": ("C", "comment_sticky", lambda r: put(r / "C" / "comments.json", [{"body": MARK + "BLOCKED"}, {"body": MARK + "BLOCKED"}]), None),
    "C.no_cost_line": ("C", "cost_line", lambda r: put(r / "C" / "checks.json", check_run("failure", "❌ BLOCKED · 2 critical, 0 medium", "không có chi phí")), None),
    "D.check_red": ("D", "check_success", lambda r: put(r / "D" / "checks.json", check_run("failure", "❌ BLOCKED · 1 critical, 0 medium")), None),
    "D.has_medium": ("D", "report_warn", lambda r: edit(r / "D" / "artifact" / "r-0001" / "report.json", lambda d: d.update(severity_counts={"critical": 0, "medium": 1, "low": 1})), None),
    "D.inline_elsewhere": ("D", "inline_comment", lambda r: put(r / "D" / "review-comments.json", [{"path": "toyapp/app.py", "line": 99}]), None),
    "D.ticket_wrong_assignee": ("D", "jira_ticket", lambda r: edit(r / "D" / "jira.json", lambda d: d["issues"][0]["fields"].update(assignee={"accountId": "someone-else"})), None),
    "D.ticket_unassigned": ("D", "jira_ticket", lambda r: edit(r / "D" / "jira.json", lambda d: d["issues"][0]["fields"].update(assignee=None)), None),
    "D.no_ticket": ("D", "jira_ticket", lambda r: put(r / "D" / "jira.json", {"issues": []}), None),
    "D.duplicate_ticket": ("D", "jira_ticket", lambda r: edit(r / "D" / "jira.json", lambda d: d["issues"].append(json.loads(json.dumps(d["issues"][0])))), None),
    "D.rerun_missed_cache": ("D", "rerun_cache", lambda r: edit(r / "D" / "rerun" / "artifact" / "selection.json", lambda d: d.update(source="llm")), None),
    "D.rerun_charged_again": ("D", "rerun_cache", lambda r: put(r / "D" / "rerun" / "artifact" / "r-0001" / "llm_usage.json", [SELECT_ROW]), None),
    "D.rerun_second_comment": ("D", "rerun_cache", lambda r: put(r / "D" / "rerun" / "comments.json", [{"body": MARK + "x"}, {"body": MARK + "y"}]), None),
    "D.rerun_extra_review": ("D", "rerun_cache", lambda r: put(r / "D" / "rerun" / "reviews.json", [{"id": 7}, {"id": 8}]), None),
    "D.rerun_extra_ticket": ("D", "rerun_cache", lambda r: edit(r / "D" / "rerun" / "jira.json", lambda d: d["issues"].append(json.loads(json.dumps(d["issues"][0])))), None),
    "E.not_dispatch": ("E", "dispatch", lambda r: put(r / "E" / "job.json", {"event": "pull_request", "conclusion": "success"}), "infra"),
    "E.wrong_workers": ("E", "exact_workers", lambda r: edit(r / "E" / "artifact" / "selection.json", lambda d: d.update(workers=["semgrep", "gitleaks"])), None),
    "E.llm_was_called": ("E", "exact_workers", lambda r: put(r / "E" / "artifact" / "r-0001" / "llm_usage.json", [SELECT_ROW]), None),
    "E.not_manual": ("E", "exact_workers", lambda r: edit(r / "E" / "artifact" / "selection.json", lambda d: d.update(source="llm", trigger_type="pr")), None),
}


@pytest.mark.parametrize("name", sorted(FAILS))
def test_wrong_evidence_is_a_failure_on_exactly_the_expected_row(tmp_path, name):
    scenario, check, change, cause = FAILS[name]
    write_evidence(tmp_path)
    change(tmp_path)
    report = collect.evaluate(tmp_path, **KW)
    row = rows(report)[(scenario, check)]
    assert row.status == collect.FAIL, (row.detail, name)
    if cause:
        assert row.cause == cause
    others = [k for k, r in rows(report).items() if r.status == collect.FAIL and k != (scenario, check)]
    assert len(others) <= 1, f"một thay đổi không được làm đỏ lan rộng: {others}"


def test_two_check_runs_with_the_same_name_is_ambiguous_so_pending(tmp_path):
    write_evidence(tmp_path)
    edit(tmp_path / "C" / "checks.json", lambda d: d["check_runs"].append(dict(d["check_runs"][0])))
    row = rows(collect.evaluate(tmp_path, **KW))[("C", "check_failure")]
    assert row.status == collect.PENDING and "đúng 1 Check Run" in row.detail


def test_malformed_json_is_pending_with_a_reason_not_a_crash(tmp_path):
    write_evidence(tmp_path)
    (tmp_path / "C" / "checks.json").write_text("{không phải json", encoding="utf-8")
    row = rows(collect.evaluate(tmp_path, **KW))[("C", "check_failure")]
    assert row.status == collect.PENDING and "JSON" in row.detail


def test_wrong_shape_is_failure_not_crash(tmp_path):
    write_evidence(tmp_path)
    put(tmp_path / "A" / "runs.json", {"không": "phải mảng"})
    assert status(collect.evaluate(tmp_path, **KW), "A", "workflow_ok") == collect.FAIL


def test_push_rejection_without_timestamp_is_pending(tmp_path):
    write_evidence(tmp_path)
    put(tmp_path / "B" / "push-attempt.txt", " ! [remote rejected] HEAD -> main (protected branch hook declined)\n")
    row = rows(collect.evaluate(tmp_path, **KW))[("B", "push_rejected")]
    assert row.status == collect.PENDING and "thời điểm" in row.detail


def test_jira_assignee_cannot_be_checked_without_dev_and_user_map(tmp_path):
    write_evidence(tmp_path)
    row = rows(collect.evaluate(tmp_path, project="noteboard"))[("D", "jira_ticket")]
    assert row.status == collect.PENDING and "--dev" in row.detail


def test_workers_argument_changes_the_expectation_for_e(tmp_path):
    write_evidence(tmp_path)
    assert status(collect.evaluate(tmp_path, **{**KW, "workers": "semgrep,gitleaks"}), "E", "exact_workers") == collect.FAIL


def test_scenario_filter_only_reports_those_scenarios(tmp_path):
    write_evidence(tmp_path)
    report = collect.evaluate(tmp_path, ["C", "D"], **KW)
    assert {r.scenario for r in report.rows} == {"C", "D", "*"}


# ───────────────────────── 10 lượt ─────────────────────────

def make_report(*, source="github", fail=None, pending=None, cause="product"):
    rows_ = [collect.Row("C", "check_failure", "x", collect.PASS), collect.Row("D", "check_success", "x", collect.PASS), collect.Row("*", "provenance", "x", collect.PASS)]
    if fail:
        rows_[0] = collect.Row("C", "check_failure", "x", collect.FAIL, "d", cause)
    if pending:
        rows_[1] = collect.Row("D", "check_success", "x", collect.PENDING, "thiếu")
    return collect.Report(rows=rows_, source=source, meta={})


def record_all(path, pattern):
    for i, kind in enumerate(pattern, 1):
        stability.record(path, i, make_report(fail=kind == "F", pending=kind == "P", cause={"F": "infra"}.get(kind, "product")))


def test_ten_green_iterations_are_eligible_but_the_tool_still_ticks_nothing(tmp_path):
    path = tmp_path / "r.json"
    record_all(path, "G" * 10)
    result = stability.summarize(path)
    assert result["verdict"].startswith("ĐỦ ĐIỀU KIỆN") and result["green_rate"] == 1.0 and "không tick DoD" in result["note"]


def test_nine_of_ten_is_still_eligible_and_failure_cause_is_reported(tmp_path):
    path = tmp_path / "r.json"
    record_all(path, "GGGGGFGGGG")
    result = stability.summarize(path)
    assert result["verdict"].startswith("ĐỦ") and result["counts"]["FAIL"] == 1 and result["failures_by_cause"] == {"infra": 1}


def test_eight_of_ten_is_not_met(tmp_path):
    path = tmp_path / "r.json"
    record_all(path, "GGFGGFGGGG")
    assert stability.summarize(path)["verdict"] == "CHƯA ĐẠT"


def test_failing_past_the_threshold_early_is_decided_before_ten(tmp_path):
    path = tmp_path / "r.json"
    record_all(path, "FF")
    result = stability.summarize(path)
    assert result["verdict"] == "CHƯA ĐẠT" and any("đã 2 lượt đỏ" in r for r in result["reasons"])


def test_partial_run_is_inconclusive_and_pending_blocks_a_verdict(tmp_path):
    path = tmp_path / "r.json"
    record_all(path, "GGGGG")
    assert stability.summarize(path)["verdict"] == "CHƯA KẾT LUẬN"
    record_all(tmp_path / "p.json", "GGGGGGGGGP")
    result = stability.summarize(tmp_path / "p.json")
    assert result["verdict"] == "CHƯA KẾT LUẬN" and any("PENDING" in r for r in result["reasons"])


def test_gaps_in_iteration_numbers_are_not_consecutive(tmp_path):
    path = tmp_path / "r.json"
    for i in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11):
        stability.record(path, i, make_report())
    result = stability.summarize(path)
    assert result["contiguous"] is False and result["verdict"] == "CHƯA KẾT LUẬN"


def test_non_github_iteration_is_pending_even_if_every_row_passes(tmp_path):
    path = tmp_path / "r.json"
    assert stability.record(path, 1, make_report(source="fake"))["status"] == collect.PENDING


def test_iteration_cannot_be_rewritten_silently(tmp_path):
    path = tmp_path / "r.json"
    stability.record(path, 1, make_report(fail=True))
    with pytest.raises(ValueError, match="đã được ghi"):
        stability.record(path, 1, make_report())
    assert stability.record(path, 1, make_report(), force=True)["status"] == collect.PASS


def test_dominant_cause_prefers_product_over_llm_over_infra(tmp_path):
    path = tmp_path / "r.json"
    report = collect.Report(rows=[collect.Row("C", "a", "x", collect.FAIL, "", "infra"), collect.Row("D", "b", "x", collect.FAIL, "", "llm"),
                                  collect.Row("D", "c", "x", collect.FAIL, "", "product")], source="github")
    stability.record(path, 1, report)
    assert stability.summarize(path)["failures"][1]["cause"] == "product"


def test_record_ignores_other_scenarios_rows(tmp_path):
    report = collect.Report(rows=[collect.Row("C", "a", "x", collect.PASS), collect.Row("D", "b", "x", collect.PASS), collect.Row("A", "z", "x", collect.FAIL, "", "infra")], source="github")
    assert stability.record(tmp_path / "r.json", 1, report)["status"] == collect.PASS


def test_summary_of_missing_file_is_inconclusive(tmp_path):
    assert stability.summarize(tmp_path / "none.json")["verdict"] == "CHƯA KẾT LUẬN"


# ───────────────────────── đo thời gian ─────────────────────────

def job_file(path, start, end, project="noteboard"):
    put(path, {"jobs": [{"name": f"qc / qc-agent / {project}", "startedAt": start, "completedAt": end}, {"name": "khác", "startedAt": "2026-10-08T00:00:00Z", "completedAt": "2026-10-08T09:00:00Z"}]})
    return path


def test_job_seconds_uses_only_the_gate_job(tmp_path):
    assert timing.job_seconds(job_file(tmp_path / "a.json", "2026-10-08T10:00:00Z", "2026-10-08T10:03:30Z"), "noteboard") == 210


def test_job_seconds_rejects_missing_or_ambiguous_job(tmp_path):
    put(tmp_path / "x.json", {"jobs": []})
    with pytest.raises(ValueError):
        timing.job_seconds(tmp_path / "x.json", "noteboard")
    put(tmp_path / "y.json", {"jobs": [{"name": "qc / qc-agent / noteboard", "startedAt": None, "completedAt": None}]})
    with pytest.raises(ValueError, match="startedAt"):
        timing.job_seconds(tmp_path / "y.json", "noteboard")


def test_timing_limit_is_60_seconds_on_the_median():
    assert timing.compare([230, 240, 250], [150, 160, 170])["verdict"] == "FAIL"
    assert timing.compare([200, 210, 220], [150, 160, 170])["verdict"] == "PASS"
    assert timing.compare([210], [150])["delta_s"] == 60 and timing.compare([210], [150])["verdict"] == "PASS"
    assert timing.compare([211], [150])["verdict"] == "FAIL"


def test_timing_without_samples_is_pending_not_pass():
    assert timing.compare([], [100])["verdict"] == "PENDING" and timing.compare([100], [])["verdict"] == "PENDING"


# ───────────────────────── script 10 lượt (bash + gh/curl GIẢ + remote git cục bộ) ─────────────────────────

def bash_available() -> bool:
    exe = shutil.which("bash")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-c", "exit 0"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_bash = pytest.mark.skipif(not bash_available(), reason="không có bash chạy được: script 10 lượt chưa được kiểm chứng ở môi trường này")

GH_STUB = r'''#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
case "$1" in
  pr)
    case "$2" in
      create) n=$(cat "$STUB_DIR/n" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$STUB_DIR/n"; echo "https://github.com/acme/sandbox/pull/$n";;
      view) echo '{}';;
      *) exit 0;;
    esac;;
  run)
    case "$2" in
      list) echo 4242;;
      view) echo '{}';;
      download) dir=""; while [ $# -gt 0 ]; do if [ "$1" = --dir ]; then dir="$2"; fi; shift; done; mkdir -p "$dir";;
    esac;;
  api)
    case "$2" in
      *check-runs*) echo '{"check_runs": []}';;
      *) echo '[]';;
    esac;;
  *) exit 0;;
esac
'''
CURL_STUB = '#!/usr/bin/env bash\necho "curl $*" >> "$STUB_LOG"\necho \'{"issues": []}\'\n'


@pytest.fixture
def harness(tmp_path):
    """Sandbox thật (dựng cục bộ) + remote git bare cục bộ + `gh`/`curl` giả. Không có gì rời máy."""
    data = full_intake()
    sb = tmp_path / "sb"
    sandbox.build_local(sb, data)
    origin = tmp_path / "origin.git"
    common.git(tmp_path, "init", "-q", "--bare", str(origin))
    common.git(sb, "remote", "add", "origin", origin.as_posix())
    common.git(sb, "push", "-q", "origin", "main")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gh", GH_STUB), ("curl", CURL_STUB)):
        (bin_dir / name).write_text(body, encoding="utf-8", newline="\n")
        (bin_dir / name).chmod(0o755)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    env = {**os.environ, "PATH": os.pathsep.join([bin_dir.as_posix(), str(Path(sys.executable).parent), os.environ["PATH"]]), "STUB_LOG": (tmp_path / "calls.log").as_posix(),
           "STUB_DIR": stub_dir.as_posix(), "E2E_ASSUME_YES": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "JIRA_BASE_URL": "https://x.invalid", "JIRA_EMAIL": "a@b.invalid", "JIRA_API_TOKEN": "x"}
    evid = tmp_path / "evid"

    def run(iterations=2, cap=5.0, *, assume_yes=True, stdin=None):
        script = tmp_path / "stability.sh"
        script.write_text(stability.emit_script(repo="acme/sandbox", sandbox=sb.as_posix(), evidence=evid.as_posix(), project="noteboard", jira_key="SBX", cap=cap, iterations=iterations,
                                                dev="dev1"), encoding="utf-8", newline="\n")
        run_env = {k: v for k, v in env.items() if assume_yes or k != "E2E_ASSUME_YES"}
        done = subprocess.run([shutil.which("bash"), script.as_posix()], cwd=ROOT, env=run_env, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        calls = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines() if (tmp_path / "calls.log").exists() else []
        return done, calls

    return run, evid, sb, origin


def test_emitted_script_has_no_merge_and_gates_every_iteration_on_confirm_and_budget():
    text = stability.emit_script(repo="o/r", sandbox="/tmp/sb", evidence="/tmp/ev", project="noteboard", jira_key="SBX", cap=2, iterations=10, dev="dev1")
    assert "gh pr merge" not in text and "--force" not in text and "push -f" not in text
    assert text.index("confirm \"Lượt") < text.index("open_pr C") and text.index("ledger") > text.index("collect")
    assert "ITERATIONS=10" in text and 'CAP="2"' in text and "@@" not in text


def test_script_reads_use_the_same_commands_as_the_scenario_contract():
    text = stability.emit_script(repo="o/r", sandbox="/tmp/sb", evidence="/tmp/ev", project="noteboard", jira_key="SBX", cap=2, iterations=1, dev="d")
    for name, cmd in scenarios.read_commands("D", repo="o/r", project="noteboard", key="SBX", evidence="$iter", branch="$branch", pr="$pr", sha="$sha", run="$run"):
        assert cmd in text, name


@needs_bash
def test_emitted_script_is_valid_bash(tmp_path):
    script = tmp_path / "s.sh"
    script.write_text(stability.emit_script(repo="o/r", sandbox="/tmp/sb", evidence="/tmp/ev", project="noteboard", jira_key="SBX", cap=2, iterations=10, dev="d"), encoding="utf-8", newline="\n")
    done = subprocess.run([shutil.which("bash"), "-n", script.as_posix()], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@needs_bash
def test_script_two_iterations_with_stubbed_gh_never_yields_a_false_green(harness):
    run, evid, sb, origin = harness
    done, calls = run(iterations=2)
    assert done.returncode == 1, (done.returncode, done.stdout[-600:], done.stderr[-600:])   # summary: CHƯA KẾT LUẬN => exit 1, và KHÔNG phải 2/3/4
    assert sum(1 for c in calls if c.startswith("gh pr create")) == 4 and sum(1 for c in calls if c.startswith("gh pr close")) == 4
    assert not [c for c in calls if " merge" in c or "secret" in c or "protect" in c]
    assert all("--base main" in c for c in calls if c.startswith("gh pr create"))
    refs = common.git(sb, "ls-remote", "--heads", origin.as_posix())
    for name in ("c-s01", "d-s01", "c-s02", "d-s02"):
        assert f"refs/heads/qc-e2e/{name}" in refs
    results = json.loads((evid / "stability-results.json").read_text(encoding="utf-8"))["iterations"]
    assert set(results) == {"1", "2"} and all(v["status"] in (collect.PENDING, collect.FAIL) for v in results.values())
    assert (evid / "iter-01" / "C" / "checks.json").is_file() and (evid / "iter-02" / "D" / "jira.json").is_file()
    assert common.git(sb, "rev-parse", "--abbrev-ref", "HEAD") == "main"


@needs_bash
def test_script_stops_when_budget_ledger_says_stop(harness):
    run, evid, sb, origin = harness
    put(evid / "earlier" / "llm_usage.json", [{"purpose": "diff-select", "est_usd": 9.0, "cache_hit": False}])
    done, calls = run(iterations=3, cap=1.0)
    assert done.returncode == 4 and "NGÂN SÁCH" in done.stdout
    assert sum(1 for c in calls if c.startswith("gh pr create")) == 2, "dừng sau lượt 1, không mở lượt 2"


@needs_bash
def test_script_declined_confirmation_creates_nothing(harness):
    run, evid, sb, origin = harness
    done, calls = run(iterations=2, assume_yes=False, stdin="n\n")
    assert done.returncode == 2 and calls == []
    assert common.git(sb, "branch", "--list", "qc-e2e/*") == ""
