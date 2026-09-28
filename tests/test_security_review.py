"""A-9: review Security gắn file:dòng — chỉ dòng trong diff mới inline, nội dung không tin cậy được làm sạch, không lộ mã/secret, không đăng lặp, lỗi API chỉ là cảnh báo."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from qc_agent.integrations import github, security_review as sr
from securitykit import fixture_json
from tests.fakes import FakeGitHub

ROOT = Path(__file__).resolve().parent.parent
CTX = {"repo": "o/r", "pr_number": 7, "sha": "abc1234def5678"}
FILES = {"semgrep": "semgrep.json", "gitleaks": "gitleaks.json", "trivy": "trivy.json"}
SNIPPET = "subprocess.run(cmd, shell=True)  # SNIPPET-MARKER"
# `jobs.py`: dòng 40-43 nằm trong diff (dòng 42 bị semgrep báo); `config.py` có dòng 12 trong diff (gitleaks); lockfile cũng có dòng trong diff
PATCH_JOBS = "@@ -40,3 +40,4 @@\n a\n b\n+c\n d\n"
PATCH_CONFIG = "@@ -10,2 +10,4 @@\n x\n y\n+z\n+w\n"
PATCH_LOCK = "@@ -1,2 +1,3 @@\n {\n+ new\n }\n"


def make_run(tmp_path, reports: dict, *, status="fail", worker_names=None) -> Path:
    """run_dir như engine ghi: results/<tid>.json + <tid>/<tool>.json, sha256 của evidence khớp file."""
    run = tmp_path / "runs" / "r-0001"
    (run / "results").mkdir(parents=True)
    for index, (tool, data) in enumerate(reports.items()):
        task_id = f"t-01{index}"
        (run / task_id).mkdir()
        raw = (data if isinstance(data, str) else json.dumps(data)).encode("utf-8")
        (run / task_id / FILES[tool]).write_bytes(raw)
        result = {"task_id": task_id, "worker": {"name": (worker_names or {}).get(tool, tool)}, "status": status,
                  "evidence": [{"kind": "raw_output", "uri": f"runs/r-0001/{task_id}/{FILES[tool]}", "sha256": hashlib.sha256(raw).hexdigest()}]}
        (run / "results" / f"{task_id}.json").write_text(json.dumps(result), encoding="utf-8")
    return run


def full_run(tmp_path, **overrides) -> Path:
    reports = {"semgrep": fixture_json("semgrep-sample.json"), "gitleaks": fixture_json("gitleaks-sample.json"), "trivy": fixture_json("trivy-sample.json")}
    reports.update(overrides)
    return make_run(tmp_path, reports)


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        fake.pr_files = [{"filename": "apps/api-server/app/api/jobs.py", "patch": PATCH_JOBS},
                         {"filename": "apps/api-server/app/config.py", "patch": PATCH_CONFIG},
                         {"filename": "package-lock.json", "patch": PATCH_LOCK}, {"filename": "README.md", "patch": None}]
        yield fake


def env_for(gh, token="tok"):
    return {"GITHUB_TOKEN": token, "GITHUB_API_URL": gh.url}


def md(text: str) -> str:
    """Dạng đã qua clean_md mà review hiển thị (clean_md escape dấu `-`, chèn ZWSP sau `@`...)."""
    return github.clean_md(text, 200)


def sample_gitleaks_in_diff():
    data = fixture_json("gitleaks-sample.json")
    data[0]["File"] = "apps/api-server/app/config.py"
    return data


# ---------- đọc evidence ----------

def test_collect_reads_all_three_tools_sorted_by_severity(tmp_path):
    findings, notes = sr.collect(full_run(tmp_path))
    assert notes == [] and [f.tool for f in findings].count("semgrep") == 3 and [f.tool for f in findings].count("gitleaks") == 2 and [f.tool for f in findings].count("trivy") == 6
    ranks = [sr._RANK[f.level] for f in findings]
    assert ranks == sorted(ranks)
    high = next(f for f in findings if f.rule == "qc-rules.python-subprocess-shell-true")
    assert (high.path, high.line, high.level, high.tool) == ("apps/api-server/app/api/jobs.py", 42, "high", "semgrep")
    assert next(f for f in findings if f.rule == "CVE-2022-0000").level == "critical"        # review giữ mức thật (finding của result chỉ có high)
    lodash = [f for f in findings if f.rule == "CVE-2021-23337"]
    assert {f.path for f in lodash} == {"package-lock.json", "requirements.txt"} and lodash[0].fixed == "4.17.21" and lodash[0].line is None


def test_only_finished_security_tasks_are_read(tmp_path):
    assert sr.collect(make_run(tmp_path / "a", {"semgrep": fixture_json("semgrep-sample.json")}, status="error")) == ([], [])       # error: không có evidence đáng tin
    assert sr.collect(make_run(tmp_path / "b", {"semgrep": fixture_json("semgrep-sample.json")}, worker_names={"semgrep": "k6"})) == ([], [])
    assert sr.collect(tmp_path / "không-có-run") == ([], [])


def test_evidence_whose_sha256_does_not_match_is_not_trusted(tmp_path):
    run = full_run(tmp_path)
    (run / "t-010" / "semgrep.json").write_text(json.dumps(fixture_json("semgrep-empty.json")), encoding="utf-8")      # file bị thay sau khi result ghi sha
    findings, notes = sr.collect(run)
    assert not any(f.tool == "semgrep" for f in findings) and any("sha256" in n and "semgrep" in n for n in notes)


def test_task_id_cannot_point_outside_the_run_dir(tmp_path):
    run = full_run(tmp_path)
    result_file = run / "results" / "t-010.json"
    result = json.loads(result_file.read_text(encoding="utf-8"))
    result["task_id"] = "../../etc"
    result_file.write_text(json.dumps(result), encoding="utf-8")
    findings, notes = sr.collect(run)
    assert not any(f.tool == "semgrep" for f in findings) and notes


def test_unredacted_gitleaks_report_is_dropped_whole_and_never_quoted(tmp_path):
    leaky = fixture_json("gitleaks-sample.json")
    leaky[0].update(Secret="sk_live_FAKE-LEAKED-VALUE", Match='API_KEY = "sk_live_FAKE-LEAKED-VALUE"')
    run = full_run(tmp_path, gitleaks=leaky)
    findings, notes = sr.collect(run)
    assert not any(f.tool == "gitleaks" for f in findings) and any("gitleaks" in n for n in notes)
    assert "FAKE-LEAKED" not in json.dumps([[f.__dict__ for f in findings], notes])


def test_malformed_reports_become_notes_not_crashes(tmp_path):
    run = make_run(tmp_path, {"semgrep": '{"results": [{"check_id": 1}]}', "gitleaks": "không phải json", "trivy": '{"Results": [{"Target": "x", "Vulnerabilities": [{"Severity": "X"}]}]}'})
    findings, notes = sr.collect(run)
    assert findings == [] and len(notes) == 3


# ---------- ánh xạ diff -> comment ----------

def test_only_lines_inside_the_diff_become_inline_comments(tmp_path):
    findings, _ = sr.collect(full_run(tmp_path, gitleaks=sample_gitleaks_in_diff()))
    diff = {"apps/api-server/app/api/jobs.py": {40, 41, 42, 43}, "apps/api-server/app/config.py": {10, 11, 12, 13}, "package-lock.json": {1, 2, 3}}
    comments, outside = sr.split_by_diff(findings, diff)
    assert {(c["path"], c["line"]) for c in comments} == {("apps/api-server/app/api/jobs.py", 42), ("apps/api-server/app/config.py", 12)}
    assert all(c["side"] == "RIGHT" for c in comments)
    assert {(f.tool, f.rule) for f in outside if f.tool == "semgrep"} == {("semgrep", "qc-rules.python-requests-no-timeout"), ("semgrep", "qc-rules.python-assert-used")}
    assert sum(1 for f in outside if f.tool == "trivy") == 6 and not any(c["path"] == "package-lock.json" for c in comments)     # Trivy không bao giờ inline, kể cả lockfile có dòng trong diff
    assert any(f.tool == "gitleaks" and f.path == "scripts/deploy.sh" for f in outside)                                                  # ngoài diff: chỉ vào thân


def test_leading_dot_slash_is_normalised_and_unknown_files_go_to_the_body():
    f = sr.Finding("semgrep", "r", "high", "./src/a.py", 3)
    comments, outside = sr.split_by_diff([f], {"src/a.py": {3}})
    assert comments[0]["path"] == "src/a.py" and outside == []
    assert sr.split_by_diff([f], {}) == ([], [f])
    assert sr.split_by_diff([sr.Finding("semgrep", "r", "high", "/abs/src/a.py", 3)], {"src/a.py": {3}})[0] == []


def test_inline_comments_are_capped_and_the_rest_go_to_the_body():
    findings = [sr.Finding("semgrep", f"r{i}", "high", "a.py", i) for i in range(1, sr.MAX_COMMENTS + 6)]
    comments, outside = sr.split_by_diff(findings, {"a.py": set(range(1, 100))})
    assert len(comments) == sr.MAX_COMMENTS and len(outside) == 5


# ---------- làm sạch nội dung không tin cậy ----------

HOSTILE = sr.Finding("semgrep", "rule@evil #123 <script>alert(1)</script> `x`", "high", "src/@octocat/#9 `a`.py", 5,
                     message="xem @org/team #456 <img src=x onerror=alert(1)> [link](http://evil) ```fence")


def test_untrusted_text_is_neutralised_in_comment_and_body():
    comment = sr.inline_body(HOSTILE)
    body = sr.render_body("d" * 64, [HOSTILE], inline=0, outside=[HOSTILE], notes=["ghi chú @user <b>x</b>"])
    for text in (comment, body):
        assert "<script>" not in text and "<img" not in text and "<b>" not in text
        assert "@octocat" not in text and "@org" not in text and "@user" not in text and "@evil" not in text      # không ping người dùng
        assert "#123" not in text and "#456" not in text and "#9" not in text                                        # không tạo tham chiếu issue/PR
        assert "[link](http" not in text and "```fence" not in text
    assert github.ZWSP in comment and github.ZWSP in body


def test_no_code_snippet_and_no_secret_ever_reaches_the_review(tmp_path):
    semgrep = fixture_json("semgrep-sample.json")
    for item in semgrep["results"]:
        item["extra"]["lines"] = SNIPPET
        item["extra"]["fingerprint"] = "FINGERPRINT-MARKER"
    gitleaks = sample_gitleaks_in_diff()
    for entry in gitleaks:
        entry.update(Description="DESC-MARKER", Message="MSG-MARKER", Author="AUTHOR-MARKER", Match="k = REDACTED SURROUNDING-CONTEXT-MARKER")
    findings, _ = sr.collect(full_run(tmp_path, semgrep=semgrep, gitleaks=gitleaks))
    comments, outside = sr.split_by_diff(findings, {"apps/api-server/app/api/jobs.py": {42}, "apps/api-server/app/config.py": {12}})
    blob = json.dumps([comments, sr.render_body("d" * 64, findings, inline=len(comments), outside=outside, notes=[])], ensure_ascii=False)
    for marker in ("SNIPPET-MARKER", "subprocess.run", "FINGERPRINT-MARKER", "DESC-MARKER", "MSG-MARKER", "AUTHOR-MARKER", "SURROUNDING-CONTEXT-MARKER", "API_KEY", "AWS_ACCESS_KEY_ID"):
        assert marker not in blob, marker


def test_body_lists_out_of_diff_findings_and_trivy_with_fix_versions(tmp_path):
    findings, _ = sr.collect(full_run(tmp_path))
    comments, outside = sr.split_by_diff(findings, {"apps/api-server/app/api/jobs.py": {42}})
    body = sr.render_body("a" * 64, findings, inline=len(comments), outside=outside, notes=["một ghi chú"])
    assert "1 finding gắn vào dòng của PR; 10 nằm ngoài diff" in body
    assert "Semgrep: 3 (1 high, 1 medium, 1 low)" in body and "gitleaks: 2" in body and "Trivy: 6" in body
    assert "Thư viện có lỗ hổng (Trivy)" in body and md("lodash@4.17.20") in body and md("4.17.21") in body and "chưa có bản vá" in body
    assert md("qc-rules.python-requests-no-timeout") in body and md("apps/api-server/app/services/runner.py:17") in body
    dynamic_code_spans = [span for span in body.split("`")[1::2] if span not in ("# nosemgrep: <rule>", ".gitleaksignore", ".trivyignore", "qc-runs-*")]
    assert dynamic_code_spans == []                    # nội dung động không nằm trong code span (dấu gạch chéo ngược của clean_md sẽ lộ ra)
    assert "qc-agent:security sha256=" + "a" * 64 in body and "một ghi chú" in body and len(body) < github.MAX_COMMENT


def test_a_huge_report_is_truncated_in_the_body():
    many = [sr.Finding("trivy", f"CVE-2020-{i:04d}", "high", "package-lock.json", None, package=f"p{i}@1") for i in range(200)]
    body = sr.render_body("b" * 64, many, inline=0, outside=many, notes=[])
    assert "và 170 lỗ hổng nữa" in body and len(body) < github.MAX_COMMENT


# ---------- hash chống đăng lặp ----------

def test_digest_ignores_order_and_wording_but_tracks_findings():
    a = sr.Finding("semgrep", "r1", "high", "a.py", 1, message="thông điệp cũ")
    b = sr.Finding("gitleaks", "r2", "high", "b.py", 2)
    base = sr.digest([a, b])
    assert len(base) == 64 and sr.digest([b, a]) == base
    assert sr.digest([sr.Finding("semgrep", "r1", "high", "a.py", 1, message="thông điệp mới"), b]) == base            # đổi câu chữ không đăng lại
    for changed in (sr.Finding("semgrep", "r1", "high", "a.py", 2), sr.Finding("semgrep", "r1", "medium", "a.py", 1), sr.Finding("semgrep", "r9", "high", "a.py", 1)):
        assert sr.digest([changed, b]) != base
    assert sr.digest([a]) != base and sr.digest([]) != base


# ---------- đăng lên GitHub (FakeGitHub) ----------

def test_review_is_created_once_with_inline_comments_and_not_reposted(tmp_path, gh):
    run = full_run(tmp_path, gitleaks=sample_gitleaks_in_diff())
    first = sr.post_security_review(run, env=env_for(gh), ctx=CTX)
    assert first["review"] == "created" and first["comments"] == 2 and first["outside_diff"] == 9 and len(first["sha256"]) == 64
    review = gh.reviews[0]
    assert review["commit_id"] == CTX["sha"] and review["event"] == "COMMENT"
    assert {(c["path"], c["line"], c["side"]) for c in review["comments"]} == {("apps/api-server/app/api/jobs.py", 42, "RIGHT"), ("apps/api-server/app/config.py", 12, "RIGHT")}
    assert f"<!-- qc-agent:security sha256={first['sha256']} -->" in review["body"]
    again = sr.post_security_review(run, env=env_for(gh), ctx=CTX)                    # push thêm commit mà finding không đổi
    assert again["review"].startswith("skipped: đã đăng") and len(gh.reviews) == 1
    changed = fixture_json("semgrep-sample.json")
    changed["results"][0]["start"]["line"] = 43                                          # finding đổi => review mới
    assert sr.post_security_review(full_run(tmp_path / "again", semgrep=changed), env=env_for(gh), ctx=CTX)["review"] == "created" and len(gh.reviews) == 2


def test_someone_elses_review_with_our_marker_does_not_suppress_ours(tmp_path, gh):
    run = full_run(tmp_path)
    findings, _ = sr.collect(run)
    gh.reviews.append({"id": 1, "body": sr.MARKER.format(digest=sr.digest(findings)), "user": {"type": "User"}})
    assert sr.post_security_review(run, env=env_for(gh), ctx=CTX)["review"] == "created"


def test_a_refine_review_does_not_suppress_a_security_review(tmp_path, gh):
    gh.reviews.append({"id": 1, "body": "<!-- qc-agent:refine sha256=" + "0" * 64 + " -->", "user": {"type": "Bot"}})
    assert sr.post_security_review(full_run(tmp_path), env=env_for(gh), ctx=CTX)["review"] == "created"


def test_findings_outside_the_diff_still_appear_in_the_body(tmp_path, gh):
    gh.pr_files = [{"filename": "README.md", "patch": None}]
    result = sr.post_security_review(full_run(tmp_path), env=env_for(gh), ctx=CTX)
    review = gh.reviews[0]
    assert result["comments"] == 0 and review["comments"] == [] and md("apps/api-server/app/api/jobs.py:42") in review["body"] and md("CVE-2022-0000") in review["body"]


def test_fork_pr_with_a_read_only_token_is_only_a_warning(tmp_path, gh):
    gh.forced[("POST", "/repos/o/r/pulls/7/reviews")] = 403
    result = sr.post_security_review(full_run(tmp_path), env=env_for(gh), ctx=CTX)
    assert result["review"].startswith("error: 403") and not gh.reviews and "tok" not in json.dumps(result)


def test_network_failure_is_only_a_warning(tmp_path):
    result = sr.post_security_review(full_run(tmp_path), env={"GITHUB_TOKEN": "tok", "GITHUB_API_URL": "http://127.0.0.1:9"}, ctx=CTX)
    assert result["review"].startswith("error:") and "tok" not in json.dumps(result)


@pytest.mark.parametrize("ctx, env", [({**CTX, "pr_number": None}, {"GITHUB_TOKEN": "t"}), ({**CTX, "repo": None}, {"GITHUB_TOKEN": "t"}), ({**CTX, "sha": None}, {"GITHUB_TOKEN": "t"}), (CTX, {})])
def test_missing_context_or_token_skips_without_any_request(tmp_path, gh, ctx, env):
    assert sr.post_security_review(full_run(tmp_path), env={**env, "GITHUB_API_URL": gh.url}, ctx=ctx)["review"].startswith("skipped: cần")
    assert gh.requests == []


def test_no_findings_means_no_review_and_no_request(tmp_path, gh):
    clean = make_run(tmp_path, {"semgrep": fixture_json("semgrep-empty.json"), "gitleaks": [], "trivy": fixture_json("trivy-empty.json")}, status="pass")
    assert sr.post_security_review(clean, env=env_for(gh), ctx=CTX)["review"].startswith("skipped: không có finding") and gh.requests == []


def test_invalid_repo_or_sha_is_rejected_before_any_request(tmp_path, gh):
    result = sr.post_security_review(full_run(tmp_path), env=env_for(gh), ctx={**CTX, "repo": "../evil", "sha": "zzz"})
    assert result["review"].startswith("error:") and gh.requests == []


# ---------- CLI + workflow ----------

def test_cli_never_changes_the_job_result(tmp_path, gh, monkeypatch, capsys):
    run = full_run(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_API_URL", gh.url)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": "abc1234def5678"}}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert sr.main(["--run-dir", str(run)]) == 0 and json.loads(capsys.readouterr().out)["review"] == "created"
    gh.forced[("GET", "/repos/o/r/pulls/7/reviews")] = 500                           # GitHub hỏng: vẫn exit 0
    assert sr.main(["--run-dir", str(run)]) == 0 and json.loads(capsys.readouterr().out)["review"].startswith("error")
    capsys.readouterr()
    assert sr.main(["--run-dir", str(tmp_path / "missing")]) == 0
    capsys.readouterr()
    monkeypatch.setattr(sr, "post_security_review", lambda *a, **k: 1 / 0)         # lỗi bất ngờ trong module cũng không đổi exit code
    assert sr.main(["--run-dir", str(run)]) == 0 and json.loads(capsys.readouterr().out) == {"error": "ZeroDivisionError"}


def test_workflow_step_is_report_only_readonly_and_inside_region_a():
    text = (ROOT / ".github" / "workflows" / "qc-gate.reusable.yml").read_text(encoding="utf-8")
    region = text.split("# ==== qc-agent:region security-review (A) ====")[1].split("# ==== qc-agent:end ====")[0]
    assert "qc_agent.integrations.security_review" in region and "if: always()" in region and "continue-on-error: true" in region
    assert '"$PWD:/work:ro"' in region and "${{ inputs" not in region.split("run: |")[1]         # đầu vào đi qua env, không nội suy vào script
    assert text.index("Run qc-agent gate") < text.index("security-review (A)") < text.index("name: Report (Check Run")     # sau gate, trước Report
