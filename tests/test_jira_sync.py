import json

import httpx

from qc_agent.core.egress import Decision, EgressPolicy
from qc_agent.integrations.jira import sync_low_findings
from tests.fakes import FakeJira


FINDING = {"fingerprint": "0123456789abcdef", "severity": "low", "title": "Thiếu test API", "rule_id": "route",
           "path": "toyapp/app.py", "line": 12}
CFG = {"jira": {"project_key": "QCSB", "issue_type": "Task", "user_map": {"dev": "account-123"}}}
ENV = {"JIRA_BASE_URL": "https://jira.example.invalid", "JIRA_EMAIL": "qc@example.invalid", "JIRA_API_TOKEN": "fake-secret"}
CTX = {"author": "dev", "pr_url": "https://github.example/pr/1", "run_url": "https://github.example/run/1"}


def test_create_low_then_skip_existing(tmp_path):
    created = []

    def respond(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/search/jql"):
            assert "qcagent-0123456789abcdef" in body["jql"]
            labels = ["qcagent-0123456789abcdef"] if created else []
            return httpx.Response(200, json={"issues": [{"fields": {"labels": labels}}] if labels else []})
        assert request.url.path.endswith("/issue")
        fields = body["fields"]
        assert fields["assignee"] == {"accountId": "account-123"}
        assert fields["description"]["type"] == "doc"
        assert "source-secret-marker" not in json.dumps(fields)
        created.append(fields)
        return httpx.Response(201, json={"key": "QCSB-1"})

    transport = httpx.MockTransport(respond)
    first = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=ENV, egress_dir=tmp_path, transport=transport)
    second = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=ENV, egress_dir=tmp_path, transport=transport)
    assert first["created"] == 1 and second["duplicates"] == 1 and len(created) == 1


def test_only_low_and_failure_is_open(tmp_path):
    def fail(request):
        return httpx.Response(503)

    transport = httpx.MockTransport(fail)
    assert sync_low_findings([{**FINDING, "severity": "medium"}], cfg=CFG, ctx=CTX, env=ENV,
                             egress_dir=tmp_path, transport=transport)["created"] == 0
    result = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=ENV, egress_dir=tmp_path, transport=transport)
    assert result["jira"] == "error: HTTP 503" and result["created"] == 0


class Deny(EgressPolicy):
    def decide(self, event):
        return Decision("deny", "test")


def test_egress_deny_makes_no_request(tmp_path):
    def unexpected(request):
        raise AssertionError("network request after deny")

    result = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=ENV, egress_dir=tmp_path,
                               transport=httpx.MockTransport(unexpected), egress_policy=Deny())
    assert result["jira"] == "error: egress_denied"


def test_unmapped_author_and_creation_limit(tmp_path):
    created = []

    def respond(request):
        if request.url.path.endswith("/search/jql"):
            return httpx.Response(200, json={"issues": []})
        fields = json.loads(request.content)["fields"]
        assert "assignee" not in fields
        assert "Không map được tác giả PR" in json.dumps(fields, ensure_ascii=False)
        created.append(fields)
        return httpx.Response(201, json={"key": "QCSB-1"})

    cfg = {"jira": {**CFG["jira"], "max_new_per_run": 1}}
    ctx = {**CTX, "author": "unknown"}
    findings = [FINDING, {**FINDING, "fingerprint": "f" * 16}]
    result = sync_low_findings(findings, cfg=cfg, ctx=ctx, env=ENV, egress_dir=tmp_path,
                               transport=httpx.MockTransport(respond))
    assert result["created"] == 1 and result["remaining"] == 1 and len(created) == 1


def test_auth_failure_does_not_expose_key(tmp_path):
    result = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=ENV, egress_dir=tmp_path,
                               transport=httpx.MockTransport(lambda _: httpx.Response(401)))
    assert result["jira"] == "error: HTTP 401"
    assert ENV["JIRA_API_TOKEN"] not in json.dumps(result)


def test_fake_jira_http_server_reuses_label(tmp_path):
    with FakeJira() as server:
        env = {**ENV, "JIRA_BASE_URL": server.url}
        first = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=env, egress_dir=tmp_path)
        again = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=env, egress_dir=tmp_path)
        assert first["created"] == 1 and again["duplicates"] == 1
        assert len(server.issues) == 1


def test_search_paginates_before_creation(tmp_path):
    seen = []

    def respond(request):
        if request.url.path.endswith("/search/jql"):
            body = json.loads(request.content)
            seen.append(body.get("nextPageToken"))
            if "nextPageToken" not in body:
                return httpx.Response(200, json={"issues": [], "nextPageToken": "page-2"})
            return httpx.Response(200, json={"issues": [{"fields": {"labels": ["qcagent-" + FINDING["fingerprint"]]}}]})
        raise AssertionError("ticket existed on second page")

    result = sync_low_findings([FINDING], cfg=CFG, ctx=CTX, env=ENV, egress_dir=tmp_path,
                               transport=httpx.MockTransport(respond))
    assert seen == [None, "page-2"] and result["duplicates"] == 1 and result["created"] == 0
