import json

from qc_agent.integrations import pr_review


def finding(n, **extra):
    return {"fingerprint": f"{n:016x}", "severity": "low", "title": "Thiếu test", "worker": "pytest",
            "rule_id": "case", "path": "toyapp/app.py", "line": n, **extra}


def test_inline_only_on_right_side_and_outside_in_body():
    workers = ("semgrep", "gitleaks", "pytest", "coverage-debt")
    findings = [{**finding(n), "worker": workers[(n - 1) % 4]} for n in range(1, 21)] + [{"fingerprint": "f" * 16, "severity": "medium",
        "title": "Không có vị trí", "worker": "trivy", "rule_id": "CVE-1", "path": None, "line": None}]
    comments, outside = pr_review.split_by_diff(findings, {"toyapp/app.py": set(range(1, 11))})
    assert len(comments) == 10
    assert [(c["path"], c["line"], c["side"]) for c in comments] == [
        ("toyapp/app.py", n, "RIGHT") for n in range(1, 11)]
    assert outside == findings[10:]
    body = pr_review.render_body(findings, outside, len(comments), pr_review.digest(findings))
    assert "Không có vị trí" in body and "**Medium**" in body


def test_digest_stable_and_body_sanitized():
    first = finding(1)
    second = {**finding(2), "title": "<img> @user #123 `code`"}
    assert pr_review.digest([first, second]) == pr_review.digest([second, first])
    rendered = pr_review.inline_body(second)
    assert "<img>" not in rendered and "@user" not in rendered and "#123" not in rendered


def test_bot_marker_skips_second_review(tmp_path, monkeypatch):
    findings = [finding(1)]
    (tmp_path / "report.json").write_text(json.dumps({"findings": findings}), encoding="utf-8")
    digest = pr_review.digest(findings)
    posts = []

    class Client:
        def __init__(self, *args):
            pass

        def request(self, method, path, body=None):
            if method == "GET":
                reviews = [{"body": pr_review.MARKER.format(digest=digest), "user": {"type": "Bot"}}] if posts else []
                return 200, reviews
            posts.append(body)
            return 200, {}

    monkeypatch.setattr(pr_review.github, "GitHubClient", Client)
    monkeypatch.setattr(pr_review, "pr_diff_lines", lambda *args: {"toyapp/app.py": {1}})
    ctx = {"repo": "org/repo", "pr_number": 1, "sha": "a" * 40}
    env = {"GITHUB_TOKEN": "fake"}
    assert pr_review.post_pr_review(tmp_path, env=env, ctx=ctx)["review"] == "created"
    assert pr_review.post_pr_review(tmp_path, env=env, ctx=ctx)["review"].startswith("skipped")
    assert len(posts) == 1 and len(posts[0]["comments"]) == 1


def test_human_marker_does_not_suppress_review(tmp_path, monkeypatch):
    findings = [finding(1)]
    (tmp_path / "report.json").write_text(json.dumps({"findings": findings}), encoding="utf-8")
    digest = pr_review.digest(findings)
    posts = []

    class Client:
        def __init__(self, *args):
            pass

        def request(self, method, path, body=None):
            if method == "GET":
                return 200, [{"body": pr_review.MARKER.format(digest=digest), "user": {"type": "User"}}]
            posts.append(body)
            return 200, {}

    monkeypatch.setattr(pr_review.github, "GitHubClient", Client)
    monkeypatch.setattr(pr_review, "pr_diff_lines", lambda *args: {"toyapp/app.py": {1}})
    result = pr_review.post_pr_review(tmp_path, env={"GITHUB_TOKEN": "fake"},
                                      ctx={"repo": "org/repo", "pr_number": 1, "sha": "a" * 40})
    assert result["review"] == "created" and len(posts) == 1


def test_403_is_advisory(tmp_path, monkeypatch):
    (tmp_path / "report.json").write_text(json.dumps({"findings": [finding(1)]}), encoding="utf-8")

    class Client:
        def __init__(self, *args):
            pass

        def request(self, *args):
            raise pr_review.github.GitHubError(403, "forbidden")

    monkeypatch.setattr(pr_review.github, "GitHubClient", Client)
    result = pr_review.post_pr_review(tmp_path, env={"GITHUB_TOKEN": "fake"},
                                      ctx={"repo": "org/repo", "pr_number": 1, "sha": "a" * 40})
    assert result["review"] == "error: GitHubError"
