"""Bước 35: đăng kết quả refine thành review ```suggestion``` — chỉ dòng trong diff, không đăng lặp, fork chỉ cảnh báo."""
import json

import pytest

from qc_agent.integrations import ci, refine_review as rr
from tests.fakes import FakeGitHub

CTX = {"repo": "o/r", "pr_number": 7, "sha": "abc1234def5678"}
PATCH = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
SUGGESTIONS = [
    {"path": ".qc-agent/suites/api-contract.yaml", "start_line": 20, "end_line": 22, "replacement": "    exclude_path:\n    - \"/up\""},
    {"path": ".github/workflows/qc.yml", "start_line": 19, "end_line": 19, "replacement": '      sut_health_path: "/api/health"'},
    {"path": ".qc-agent/perf/smoke.js", "start_line": 5, "end_line": 7, "replacement": 'const PATHS = ["/"];'},
]
DIFF_PATCH = "@@ -18,3 +18,6 @@ jobs\n   with:\n     project: x\n+      sut_health_path: \"/health\"\n+      a: 1\n+      b: 2\n   z: 1\n"


def write_dir(tmp_path, patch=PATCH, suggestions=SUGGESTIONS):
    (tmp_path / "refine.patch").write_text(patch, encoding="utf-8")
    (tmp_path / "suggestions.json").write_text(json.dumps(suggestions), encoding="utf-8")
    return tmp_path


def test_right_lines_are_added_and_context_lines_of_each_hunk():
    lines = rr.right_lines("@@ -1,2 +1,3 @@\n a\n+b\n-c\n d\n@@ -10 +11,2 @@\n+x\n y\n\\ No newline at end of file\n")
    assert lines == {1, 2, 3, 11, 12}
    assert rr.right_lines(None) == set() and rr.right_lines("") == set()


def test_fence_is_longer_than_any_backtick_run_in_the_untrusted_replacement():
    assert rr.suggestion_body("x") == "```suggestion\nx\n```"
    body = rr.suggestion_body('path: "```` evil ```"')
    assert body.startswith("`````suggestion\n") and body.endswith("\n`````")


def test_only_ranges_fully_inside_the_diff_become_comments():
    diff = {".github/workflows/qc.yml": {18, 19, 20}, ".qc-agent/suites/api-contract.yaml": {20, 21}}      # api-contract chỉ phủ 20-21, cần 20-22
    comments, skipped = rr.build_comments(SUGGESTIONS, diff)
    assert [c["path"] for c in comments] == [".github/workflows/qc.yml"] and comments[0]["line"] == 19 and "start_line" not in comments[0]
    assert set(skipped) == {".qc-agent/suites/api-contract.yaml:20-22", ".qc-agent/perf/smoke.js:5-7"}
    multi, _ = rr.build_comments(SUGGESTIONS[:1], {".qc-agent/suites/api-contract.yaml": {20, 21, 22}})
    assert multi[0]["start_line"] == 20 and multi[0]["line"] == 22 and multi[0]["start_side"] == "RIGHT" and multi[0]["side"] == "RIGHT"


def test_malformed_suggestions_are_ignored():
    assert rr.build_comments([{"path": "a"}, "x", {"path": "a", "start_line": "n", "end_line": 1, "replacement": ""}], {"a": {1}}) == ([], [])


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        fake.pr_files = [{"filename": ".github/workflows/qc.yml", "patch": DIFF_PATCH}, {"filename": "README.md", "patch": None}]
        yield fake


def env_for(gh, token="tok"):
    return {"GITHUB_TOKEN": token, "GITHUB_API_URL": gh.url}


def test_a_review_with_suggestions_is_created_once_and_not_reposted(tmp_path, gh):
    out = write_dir(tmp_path)
    first = rr.post_refine(out, env=env_for(gh), ctx=CTX)
    assert first["review"] == "created" and first["comments"] == 1 and first["outside_diff"] == 2 and len(first["sha256"]) == 64
    review = gh.reviews[0]
    assert review["commit_id"] == CTX["sha"] and review["event"] == "COMMENT" and review["comments"][0]["body"].startswith("```suggestion")
    assert f"<!-- qc-agent:refine sha256={first['sha256']} -->" in review["body"] and "refine.patch" in review["body"]
    again = rr.post_refine(out, env=env_for(gh), ctx=CTX)
    assert again["review"].startswith("skipped: đã đăng") and len(gh.reviews) == 1
    write_dir(tmp_path, patch=PATCH + "+more\n")                                       # patch đổi => review mới
    assert rr.post_refine(out, env=env_for(gh), ctx=CTX)["review"] == "created" and len(gh.reviews) == 2


def test_someone_elses_review_with_the_marker_does_not_suppress_ours(tmp_path, gh):
    out = write_dir(tmp_path)
    digest = rr.patch_digest(PATCH)
    gh.reviews.append({"id": 1, "body": rr.MARKER.format(digest=digest), "user": {"type": "User"}})
    assert rr.post_refine(out, env=env_for(gh), ctx=CTX)["review"] == "created"


def test_fork_pr_with_a_read_only_token_is_only_a_warning(tmp_path, gh):
    gh.forced[("POST", "/repos/o/r/pulls/7/reviews")] = 403
    result = rr.post_refine(write_dir(tmp_path), env=env_for(gh), ctx=CTX)
    assert result["review"].startswith("error: 403") and not gh.reviews and "tok" not in json.dumps(result)


@pytest.mark.parametrize("ctx, env", [({**CTX, "pr_number": None}, {"GITHUB_TOKEN": "t"}), ({**CTX, "repo": None}, {"GITHUB_TOKEN": "t"}), (CTX, {})])
def test_missing_context_or_token_skips(tmp_path, gh, ctx, env):
    assert rr.post_refine(write_dir(tmp_path), env={**env, "GITHUB_API_URL": gh.url}, ctx=ctx)["review"].startswith("skipped: cần")


def test_empty_patch_or_missing_files_skip(tmp_path, gh):
    assert rr.post_refine(write_dir(tmp_path, patch="", suggestions=[]), env=env_for(gh), ctx=CTX)["review"].startswith("skipped: không có gì")
    assert rr.post_refine(tmp_path / "nope", env=env_for(gh), ctx=CTX)["review"].startswith("skipped: không đọc được")
    assert not gh.reviews


def test_ci_cli_refine_dir_mode_never_fails_the_job(tmp_path, gh, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_API_URL", gh.url)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": "abc1234def5678"}}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert ci.main(["--refine-dir", str(write_dir(tmp_path))]) == 0
    assert json.loads(capsys.readouterr().out)["review"] == "created" and len(gh.reviews) == 1
    assert ci.main(["--refine-dir", str(tmp_path / "missing")]) == 0
    with pytest.raises(SystemExit):        # thiếu cả --run-dir lẫn --refine-dir: lỗi cú pháp của argparse, không phải lỗi báo cáo
        ci.main([])
