"""tools/protect_ground_truth.py (S1-07): bật `require_code_owner_reviews` + bắt buộc qua PR bằng cách GỘP vào bảo vệ hiện có; token không bao giờ bị in.

Không gọi GitHub thật: `FakeGitHub` (GET/PUT protection) và `GITHUB_API_URL` trỏ vào nó.
"""
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from tests.fakes import FakeGitHub

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "ghp_FAKEtoken0123456789abcdefghij"
SPEC = importlib.util.spec_from_file_location("protect_ground_truth", ROOT / "tools" / "protect_ground_truth.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

EXISTING = {   # đúng hình dạng GET của GitHub (khác hình dạng PUT)
    "url": "https://api.github.com/repos/o/r/branches/main/protection",
    "required_status_checks": {"url": "u", "strict": True, "contexts": ["qc-agent / noteboard", "lint"], "contexts_url": "u",
                               "checks": [{"context": "qc-agent / noteboard", "app_id": 15368}, {"context": "lint", "app_id": -1}]},
    "enforce_admins": {"url": "u", "enabled": True},
    "required_pull_request_reviews": {
        "url": "u", "dismiss_stale_reviews": True, "require_code_owner_reviews": False, "required_approving_review_count": 2, "require_last_push_approval": True,
        "dismissal_restrictions": {"url": "u", "users": [{"login": "alice"}], "teams": [{"slug": "leads"}], "apps": []},
        "bypass_pull_request_allowances": {"users": [], "teams": [{"slug": "release"}], "apps": [{"slug": "bot"}]}},
    "restrictions": {"url": "u", "users": [{"login": "bob"}], "teams": [{"slug": "core"}], "apps": [{"slug": "deployer"}]},
    "required_linear_history": {"enabled": True}, "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
    "block_creations": {"enabled": False}, "required_conversation_resolution": {"enabled": True}, "lock_branch": {"enabled": False},
    "allow_fork_syncing": {"enabled": True},
}


@pytest.fixture
def fake(monkeypatch):
    with FakeGitHub() as server:
        monkeypatch.setenv("GITHUB_API_URL", server.url)
        monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
        yield server


def run(capsys, *args):
    capsys.readouterr()
    code = tool.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def only_put(fake):
    (payload,) = fake.protection_puts
    return payload


# ---------------- nhánh chưa được bảo vệ ----------------

def test_an_unprotected_branch_gets_code_owner_reviews_and_a_required_pull_request(fake, capsys):
    code, out, err = run(capsys, "--repo", "o/r")
    assert code == 0, err
    payload = only_put(fake)
    assert payload["required_pull_request_reviews"] == {"dismiss_stale_reviews": False, "require_code_owner_reviews": True,
                                                        "required_approving_review_count": 1, "require_last_push_approval": False}
    assert payload["required_status_checks"] is None and payload["enforce_admins"] is False and payload["restrictions"] is None
    assert json.loads(out) == payload                                    # stdout là payload đã gửi
    assert [(r["method"], r["path"]) for r in fake.requests] == [("GET", "/repos/o/r/branches/main/protection"), ("PUT", "/repos/o/r/branches/main/protection")]


def test_every_top_level_key_the_put_endpoint_requires_is_present(fake, capsys):
    run(capsys, "--repo", "o/r")
    assert {"required_status_checks", "enforce_admins", "required_pull_request_reviews", "restrictions"} <= set(only_put(fake))


# ---------------- gộp vào bảo vệ có sẵn ----------------

def test_existing_protection_is_merged_not_overwritten(fake, capsys):
    fake.protection = json.loads(json.dumps(EXISTING))
    code, _, err = run(capsys, "--repo", "o/r", "--branch", "main")
    assert code == 0, err
    payload = only_put(fake)
    assert payload["required_status_checks"] == {"strict": True, "checks": [{"context": "qc-agent / noteboard", "app_id": 15368}, {"context": "lint"}]}   # giữ status check cũ
    assert payload["enforce_admins"] is True                                                    # giữ enforce_admins
    assert payload["restrictions"] == {"users": ["bob"], "teams": ["core"], "apps": ["deployer"]}   # giữ restrictions
    review = payload["required_pull_request_reviews"]
    assert review["require_code_owner_reviews"] is True and review["required_approving_review_count"] == 2      # nâng cờ, không hạ số lượng
    assert review["dismiss_stale_reviews"] is True and review["require_last_push_approval"] is True
    assert review["dismissal_restrictions"] == {"users": ["alice"], "teams": ["leads"], "apps": []}
    assert review["bypass_pull_request_allowances"] == {"users": [], "teams": ["release"], "apps": ["bot"]}
    for key, value in (("required_linear_history", True), ("allow_force_pushes", False), ("allow_deletions", False), ("block_creations", False),
                       ("required_conversation_resolution", True), ("lock_branch", False), ("allow_fork_syncing", True)):
        assert payload[key] is value, key


@pytest.mark.parametrize("count, expected", [(None, 1), (0, 1), (1, 1), (3, 3)])
def test_the_required_approval_count_is_at_least_one_and_never_lowered(fake, capsys, count, expected):
    fake.protection = {"required_pull_request_reviews": {"required_approving_review_count": count, "require_code_owner_reviews": False}}
    assert run(capsys, "--repo", "o/r")[0] == 0
    assert only_put(fake)["required_pull_request_reviews"]["required_approving_review_count"] == expected


def test_a_branch_protected_only_by_status_checks_keeps_them_and_gains_the_review_rule(fake, capsys):
    fake.protection = {"required_status_checks": {"strict": False, "contexts": ["ci"]}, "enforce_admins": {"enabled": False}}
    assert run(capsys, "--repo", "o/r")[0] == 0
    payload = only_put(fake)
    assert payload["required_status_checks"] == {"strict": False, "checks": [{"context": "ci"}]}          # dạng cũ `contexts` được đổi sang `checks`
    assert payload["required_pull_request_reviews"]["require_code_owner_reviews"] is True


def test_applying_twice_is_stable(fake, capsys):
    fake.protection = json.loads(json.dumps(EXISTING))
    run(capsys, "--repo", "o/r")
    first = fake.protection_puts[-1]
    assert tool.build_payload(json.loads(json.dumps(EXISTING))) == first


def test_the_branch_can_be_chosen(fake, capsys):
    assert run(capsys, "--repo", "o/r", "--branch", "release/1.x")[0] == 0
    assert fake.requests[-1]["path"] == "/repos/o/r/branches/release/1.x/protection"


# ---------------- dry-run ----------------

def test_dry_run_prints_the_payload_and_never_puts(fake, capsys):
    fake.protection = json.loads(json.dumps(EXISTING))
    code, out, err = run(capsys, "--repo", "o/r", "--dry-run")
    assert code == 0 and fake.protection_puts == [] and [r["method"] for r in fake.requests] == ["GET"]
    assert json.loads(out)["required_pull_request_reviews"]["require_code_owner_reviews"] is True and "dry-run" in err


# ---------------- lỗi: exit 1, không lộ token ----------------

@pytest.mark.parametrize("method, status", [("GET", 403), ("GET", 500), ("PUT", 403), ("PUT", 404), ("PUT", 422)])
def test_api_errors_are_exit_1_and_never_echo_the_token(fake, capsys, method, status):
    fake.forced[(method, "/repos/o/r/branches/main/protection")] = status
    code, out, err = run(capsys, "--repo", "o/r")
    assert code == 1 and TOKEN not in out + err and "LỖI" in err and out == ""
    assert fake.protection_puts == []


def test_a_403_says_the_token_needs_admin_rights(fake, capsys):
    fake.forced[("PUT", "/repos/o/r/branches/main/protection")] = 403
    _, _, err = run(capsys, "--repo", "o/r")
    assert "quyền admin" in err


def test_the_token_is_only_ever_sent_as_a_bearer_header(fake, capsys):
    run(capsys, "--repo", "o/r")
    assert {r["auth"] for r in fake.requests} == {f"Bearer {TOKEN}"} and all(TOKEN not in json.dumps(r["body"]) for r in fake.requests)


def test_a_missing_token_is_exit_1_without_any_request(fake, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    code, out, err = run(capsys, "--repo", "o/r")
    assert code == 1 and "GITHUB_TOKEN" in err and fake.requests == []


@pytest.mark.parametrize("args", [["--repo", "khong-co-dau-gach"], ["--repo", "o/r", "--branch", "../x"], ["--repo", "o/r", "--branch", "a b"],
                                  ["--repo", "o/r", "--branch", "-x"], ["--repo", "o/r/extra"], ["--branch", "main"]])
def test_bad_arguments_are_exit_1_without_any_request(fake, capsys, args):
    code, out, err = run(capsys, *args)
    assert code == 1 and fake.requests == [] and TOKEN not in out + err


def test_an_unreachable_api_is_exit_1_without_leaking_url_or_token(capsys, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    code, out, err = run(capsys, "--repo", "o/r")
    assert code == 1 and TOKEN not in out + err and str(port) not in err


def test_a_token_that_slips_into_a_message_is_masked(fake, capsys, monkeypatch):
    def leak(*a, **k):
        raise tool.ProtectError(f"boom {TOKEN}")
    monkeypatch.setattr(tool, "protect", leak)
    code, out, err = run(capsys, "--repo", "o/r")
    assert code == 1 and TOKEN not in out + err and "***" in err


def test_help_is_exit_0_and_a_bad_flag_is_exit_1(capsys):
    assert tool.main(["--help"]) == 0
    assert tool.main(["--nope"]) == 1
    capsys.readouterr()


def test_build_payload_of_nothing_is_the_minimal_lock():
    payload = tool.build_payload(None)
    assert payload == {"required_status_checks": None, "enforce_admins": False, "restrictions": None,
                       "required_pull_request_reviews": {"dismiss_stale_reviews": False, "require_code_owner_reviews": True,
                                                         "required_approving_review_count": 1, "require_last_push_approval": False}}
