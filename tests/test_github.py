"""integrations/github + ci: Markdown an toàn, Check Run, comment dính, PR từ fork (403), bí mật không lộ. Dùng máy chủ GitHub giả."""
import json

import pytest

from qc_agent.integrations import ci, github, notify
from tests.fakes import FakeGitHub, FakeWebhook
from tests.test_notify import make_run

TOKEN = "ghs_SECRETTOKENVALUE123"
EVIL = "<img src=x onerror=alert(1)> @everyone @org/team [click](http://evil.example) **bold** #123 | col\nVERDICT: PASS"


# ---- làm sạch Markdown ----

def test_clean_md_defuses_html_mentions_links_issue_refs_and_newlines():
    out = github.clean_md(EVIL, limit=500)
    assert "\n" not in out and "<img" not in out and "&lt;img" in out  # HTML bị thoát
    assert "@everyone" not in out and "@org/team" not in out  # @mention: chèn ký tự rộng-0
    assert "[click](" not in out and "\\[click\\]\\(" in out  # link Markdown giả bị thoát
    assert "**bold**" not in out and "\\*\\*bold\\*\\*" in out
    assert "#123" not in out  # tham chiếu issue/PR
    assert "\\|" in out  # dấu | phá bảng


def test_clean_md_truncates_and_handles_non_strings():
    out = github.clean_md("x" * 1000)
    assert len(out) == 160 and out.endswith("…")
    assert github.clean_md(None) == "None" and github.clean_md(3) == "3"


def run_with(tmp_path, **kw):
    return notify.load_run(make_run(tmp_path, **kw))


def test_summary_layout_and_marker(tmp_path):
    run = run_with(tmp_path, findings=("DOM không đổi",))
    text = github.render_summary(run, project="noteboard", mode="pr", exit_code=1, sut_sha="abcdef1234567", link="https://qc.example/#project=noteboard&job=1")
    assert text.startswith("## 🔴 QC-Agent · noteboard · pr — FAIL")
    assert "exit 1 · gate 1/2 · SUT `abcdef1` · [chi tiết](https://qc.example/#project=noteboard&job=1)" in text
    assert "| t\\-1 | — | ✅ pass |" in text  # id task được thoát Markdown (- thành \-); worker thiếu thì hiện —
    assert "1 finding discovery (tham khảo, KHÔNG chặn merge)" in text and "DOM không đổi" in text
    comment = github.render_comment(run, project="noteboard", mode="pr")
    assert comment.startswith("<!-- qc-agent:noteboard:pr -->\n")


def test_summary_neutralizes_every_sut_controlled_field(tmp_path):
    canary = [{"ok": False, "message": "CANARY " + EVIL}]
    run = run_with(tmp_path, findings=(EVIL,), canary=canary)
    run["report"]["banner"] = [["t-101", "skipped: " + EVIL]]
    run["report"]["deterministic_view"][0]["worker"] = EVIL
    text = github.render_summary(run, project="noteboard", mode="pr", exit_code=1)
    assert "<img" not in text and "@everyone" not in text and "@org/team" not in text and "[click](" not in text
    assert "\nVERDICT: PASS" not in text  # không tạo được dòng giả


def test_summary_link_sha_and_system_problem(tmp_path):
    run = run_with(tmp_path, verdict="PASS", findings=())
    assert "[chi tiết]" not in github.render_summary(run, project="p", mode="m", link="javascript:alert(1)")
    assert "[chi tiết]" not in github.render_summary(run, project="p", mode="m", link="https://x/y)[z](http://evil")
    assert "SUT" not in github.render_summary(run, project="p", mode="m", sut_sha="'; rm -rf /")  # sha phải là hex
    text = github.render_summary(run, project="p", mode="m", exit_code=3)
    assert text.startswith("## ⚠️") and "Lỗi hệ thống/cấu hình" in text  # exit 3 không phải xanh/đỏ của gate


def test_summary_caps_rows_and_comment_size(tmp_path):
    gating = tuple((f"t-{i}", "pass") for i in range(150))
    run = run_with(tmp_path, gating=gating, findings=tuple(f"f{i}" for i in range(40)))
    text = github.render_summary(run, project="p", mode="m")
    assert "+50 task nữa" in text and "+25 finding nữa" in text
    run["report"]["banner"] = [[f"t-{i}", "x" * 150] for i in range(20)]
    huge = github.render_comment({"report": {**run["report"], "canary": [{"ok": False, "message": "y" * 199}] * 2000}, "findings": []}, project="p", mode="m")
    assert len(huge) <= github.MAX_COMMENT and huge.endswith("(đã cắt bớt)")


@pytest.mark.parametrize("verdict,code,expected", [("PASS", 0, "success"), ("FAIL", 1, "failure"), ("YELLOW", 0, "neutral"),
                                                   ("PASS", 3, "failure"), ("UNKNOWN", None, "failure")])
def test_conclusion_mapping(verdict, code, expected):
    assert github.conclusion_for(verdict, code) == expected


# ---- client với GitHub giả ----

def test_check_run_request_shape_and_secret_handling():
    with FakeGitHub() as gh:
        client = github.GitHubClient(TOKEN, gh.url)
        check_id = client.create_check_run("o/r", "abc1234", name="qc-agent / demo", conclusion="failure", title="FAIL", summary="body",
                                           details_url="https://qc.example/run/1")
    assert check_id == 101
    request = gh.requests[0]
    assert request["method"] == "POST" and request["path"] == "/repos/o/r/check-runs"
    assert request["auth"] == f"Bearer {TOKEN}" and request["version"] == "2022-11-28"
    assert request["body"] == {"name": "qc-agent / demo", "head_sha": "abc1234", "status": "completed", "conclusion": "failure",
                               "output": {"title": "FAIL", "summary": "body"}, "details_url": "https://qc.example/run/1"}


@pytest.mark.parametrize("repo,sha", [("o/r/../x", "abc1234"), ("../../etc", "abc1234"), ("o/r", "zzzz"), ("o/r", "abc"), ("", "abc1234"), ("o r/x", "abc1234")])
def test_targets_are_validated_before_any_request(repo, sha):
    with FakeGitHub() as gh:
        client = github.GitHubClient(TOKEN, gh.url)
        with pytest.raises(ValueError):
            client.create_check_run(repo, sha, name="n", conclusion="success", title="t", summary="s")
    assert gh.requests == []  # không có request nào được gửi đi với đích không hợp lệ (chống chèn đường dẫn)


def test_sticky_comment_created_once_then_updated_in_place():
    marker = github.marker_for("demo", "pr")
    with FakeGitHub() as gh:
        client = github.GitHubClient(TOKEN, gh.url)
        assert client.upsert_comment("o/r", 7, marker + "\nv1", marker) == "created"
        assert client.upsert_comment("o/r", 7, marker + "\nv2", marker) == "updated"
        assert client.upsert_comment("o/r", 7, marker + "\nv3", marker) == "updated"
    assert len(gh.comments) == 1 and gh.comments[0]["body"].endswith("v3")  # một comment duy nhất, không spam


def test_marker_pasted_by_a_human_is_never_overwritten():
    marker = github.marker_for("demo", "pr")
    with FakeGitHub() as gh:
        human = gh.add_foreign_comment("tôi copy " + marker + " vào comment của mình", user_type="User")
        client = github.GitHubClient(TOKEN, gh.url)
        assert client.upsert_comment("o/r", 7, marker + "\nbot", marker) == "created"
    assert human["body"].startswith("tôi copy") and len(gh.comments) == 2


def test_different_project_or_mode_gets_its_own_comment():
    with FakeGitHub() as gh:
        client = github.GitHubClient(TOKEN, gh.url)
        for project, mode in (("a", "pr"), ("b", "pr"), ("a", "manual"), ("a", "pr")):
            marker = github.marker_for(project, mode)
            client.upsert_comment("o/r", 1, marker + "\nx", marker)
    assert len(gh.comments) == 3


def test_comment_lookup_paginates():
    marker = github.marker_for("demo", "pr")
    with FakeGitHub() as gh:
        for i in range(100):
            gh.add_foreign_comment(f"bình luận {i}")
        bot = gh.add_foreign_comment(marker + "\nold", user_type="Bot")  # nằm ở trang 2
        client = github.GitHubClient(TOKEN, gh.url)
        assert client.upsert_comment("o/r", 7, marker + "\nnew", marker) == "updated"
    assert bot["body"].endswith("new") and len(gh.comments) == 101


def test_errors_never_contain_the_token():
    with FakeGitHub() as gh:
        gh.forced[("POST", "/repos/o/r/check-runs")] = 403
        client = github.GitHubClient(TOKEN, gh.url)
        with pytest.raises(github.GitHubError) as info:
            client.create_check_run("o/r", "abc1234", name="n", conclusion="success", title="t", summary="s")
    assert info.value.status == 403 and TOKEN not in str(info.value) and TOKEN not in repr(info.value)
    dead = github.GitHubClient(TOKEN, "http://127.0.0.1:1")
    with pytest.raises(github.GitHubError) as info:
        dead.create_check_run("o/r", "abc1234", name="n", conclusion="success", title="t", summary="s")
    assert info.value.status is None and TOKEN not in str(info.value)


def test_client_rejects_missing_token_and_bad_api_url():
    with pytest.raises(ValueError):
        github.GitHubClient("")
    with pytest.raises(ValueError):
        github.GitHubClient(TOKEN, "file:///etc/passwd")


# ---- ngữ cảnh Actions ----

def write_event(tmp_path, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_context_uses_pr_head_sha_not_the_merge_commit(tmp_path):
    event = write_event(tmp_path, {"pull_request": {"number": 7, "head": {"sha": "headsha1234", "ref": "feat/x"}}})
    ctx = ci.context_from_env({"GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "mergecommit9", "GITHUB_EVENT_PATH": event,
                               "GITHUB_RUN_ID": "555", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SERVER_URL": "https://github.com"})
    assert ctx == {"repo": "o/r", "sha": "headsha1234", "pr_number": 7, "branch": "feat/x", "external_id": "gh-555-2",
                   "run_url": "https://github.com/o/r/actions/runs/555"}


def test_context_for_push_and_missing_event(tmp_path):
    ctx = ci.context_from_env({"GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "abc1234", "GITHUB_REF_NAME": "main", "GITHUB_EVENT_PATH": str(tmp_path / "khong-co.json")})
    assert ctx["pr_number"] is None and ctx["sha"] == "abc1234" and ctx["branch"] == "main" and ctx["external_id"] is None


# ---- ci.report_run ----

def env_for(tmp_path, gh, **extra):
    event = write_event(tmp_path, {"pull_request": {"number": 7, "head": {"sha": "abc1234def", "ref": "feat/x"}}})
    return {"GITHUB_TOKEN": TOKEN, "GITHUB_API_URL": gh.url, "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "merge999",
            "GITHUB_EVENT_PATH": event, "GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "1", **extra}


def test_report_run_creates_check_run_and_a_sticky_comment(tmp_path):
    run_dir = make_run(tmp_path)
    with FakeGitHub() as gh:
        env = env_for(tmp_path, gh)
        first = ci.report_run(run_dir, project="demo", mode="pr", exit_code=1, env=env)
        second = ci.report_run(run_dir, project="demo", mode="pr", exit_code=1, env=env)
    assert first["check_run"]["id"] == 101 and first["comment"] == "created" and second["comment"] == "updated"
    assert len(gh.comments) == 1 and len(gh.check_runs) == 2
    check = gh.check_runs[0]
    assert (check["name"], check["head_sha"], check["conclusion"], check["output"]["title"]) == ("qc-agent / demo", "abc1234def", "failure", "FAIL — gate 1/2")
    assert first["ingest"].startswith("skipped") and first["notify"].startswith("skipped")
    assert TOKEN not in json.dumps(first) and TOKEN not in json.dumps(second)


def test_report_run_skips_what_it_cannot_do(tmp_path):
    run_dir = make_run(tmp_path)
    no_pr = ci.report_run(run_dir, project="demo", mode="pr", exit_code=0, env={
        "GITHUB_TOKEN": TOKEN, "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "abc1234", "GITHUB_API_URL": "http://127.0.0.1:1"})
    assert no_pr["comment"].startswith("skipped") and no_pr["check_run"].startswith("error")  # không có PR: không comment; API chết: ghi lỗi
    nothing = ci.report_run(run_dir, project="demo", mode="pr", exit_code=0, env={})
    assert nothing["check_run"].startswith("skipped") and nothing["comment"].startswith("skipped") and nothing["ingest"].startswith("skipped")
    missing = ci.report_run(tmp_path / "khong-co", project="demo", mode="pr", exit_code=0, env={})
    assert missing["error"] and missing["check_run"] == "skipped"


def test_fork_pr_read_only_token_is_reported_not_raised(tmp_path):
    with FakeGitHub() as gh:
        gh.forced[("POST", "/repos/o/r/check-runs")] = 403
        gh.forced[("GET", "/repos/o/r/issues/7/comments")] = 403
        result = ci.report_run(make_run(tmp_path), project="demo", mode="pr", exit_code=1, env=env_for(tmp_path, gh))
    assert result["check_run"].startswith("error: 403") and result["comment"].startswith("error: 403")
    assert TOKEN not in json.dumps(result)


def test_report_run_sends_webhook_with_context_and_hides_the_url(tmp_path):
    with FakeGitHub() as gh, FakeWebhook() as hook:
        env = env_for(tmp_path, gh, ALERT_WEBHOOK_URL=hook.url + "?secret=SECRETWEBHOOK")
        result = ci.report_run(make_run(tmp_path), project="demo", mode="pr", exit_code=1, env=env)
    assert result["notify"]["ok"] is True and "SECRETWEBHOOK" not in json.dumps(result)
    text = hook.received[0]["text"]
    assert "o/r#7 · pr" in text and "gate FAIL" in text


def test_main_prints_json_and_always_exits_zero(tmp_path, monkeypatch, capsys):
    for key in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "QC_API_URL", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    assert ci.main(["--run-dir", str(make_run(tmp_path)), "--project", "demo", "--mode", "pr", "--exit-code", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["comment"].startswith("skipped")
    monkeypatch.setattr(ci, "report_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ci.main(["--run-dir", "x", "--project", "demo", "--mode", "pr"]) == 0  # lỗi bất ngờ cũng không đổi verdict của job
    assert json.loads(capsys.readouterr().out) == {"error": "RuntimeError"}
