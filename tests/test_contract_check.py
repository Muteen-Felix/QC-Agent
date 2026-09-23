"""freeze_contract --write và contract_check: bump đúng mức, đếm approval, fail-closed."""
import json
import subprocess
import sys

import pytest

sys.path.insert(0, "tools")
import contract_check as cc  # noqa: E402
import freeze_contract as fc  # noqa: E402

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
TEMPLATE = "name: X\nlanes: [gate]\n"


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "workers").mkdir()
    for f in ("schemas/task_spec.json", "schemas/result.json"):
        (tmp_path / f).write_text(json.dumps(SCHEMA, indent=2) + "\n", encoding="utf-8", newline="\n")
    (tmp_path / "workers/_template.yaml").write_text(TEMPLATE, encoding="utf-8", newline="\n")
    fc.write_lock(tmp_path, "1.0.0", fc.current_hashes(tmp_path))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _edit(root, fn):
    p = root / "schemas/task_spec.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    fn(data)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def add_optional(d):
    d["properties"]["b"] = {"type": "string"}


def drop_required(d):
    d["required"] = []


def test_check_passes_when_intact(repo):
    assert fc.main(["--check"], root=repo) == 0


def test_check_fails_when_file_edited_without_lock(repo):
    _edit(repo, add_optional)
    assert fc.main(["--check"], root=repo) == 1


def test_write_refuses_without_change(repo):
    assert fc.main(["--write", "--version", "1.1.0"], root=repo) == 1


def test_write_refuses_non_increasing_version(repo):
    _edit(repo, add_optional)
    assert fc.main(["--write", "--version", "1.0.0"], root=repo) == 1
    assert fc.main(["--write", "--version", "0.9.0"], root=repo) == 1


def test_write_refuses_minor_bump_for_major_change(repo):
    _edit(repo, drop_required)
    assert fc.main(["--write", "--version", "1.1.0"], root=repo) == 1
    assert fc.read_lock(repo)["version"] == "1.0.0"


def test_write_accepts_minor_for_additive_and_major_for_breaking(repo):
    _edit(repo, add_optional)
    assert fc.main(["--write", "--version", "1.1.0"], root=repo) == 0
    assert fc.main(["--check"], root=repo) == 0
    _edit(repo, drop_required)
    assert fc.main(["--write", "--version", "2.0.0", "--base-ref", "HEAD"], root=repo) == 0


def test_write_rejects_bad_semver(repo):
    _edit(repo, add_optional)
    assert fc.main(["--write", "--version", "v1"], root=repo) == 1


# ---- contract_check ----

CORE, ELIG = {"lead"}, {"lead", "a", "b", "c"}


def _bump(repo, fn, version):
    _edit(repo, fn)
    assert fc.main(["--write", "--version", version], root=repo) == 0


def test_no_contract_change_passes_without_approvals(repo):
    assert cc.evaluate(repo, "HEAD", set(), CORE, ELIG)[0] is True


def test_minor_needs_one_valid_approval(repo):
    _bump(repo, add_optional, "1.1.0")
    assert cc.evaluate(repo, "HEAD", set(), CORE, ELIG)[0] is False
    assert cc.evaluate(repo, "HEAD", {"a"}, CORE, ELIG)[0] is True
    assert cc.evaluate(repo, "HEAD", {"stranger"}, CORE, ELIG)[0] is False


def test_major_needs_three_and_a_lead(repo):
    _bump(repo, drop_required, "2.0.0")
    assert cc.evaluate(repo, "HEAD", {"a", "b"}, CORE, ELIG)[0] is False
    ok, msgs = cc.evaluate(repo, "HEAD", {"a", "b", "c"}, CORE, ELIG)
    assert ok is False and any("Lead/Core" in m for m in msgs)
    assert cc.evaluate(repo, "HEAD", {"a", "b", "lead"}, CORE, ELIG)[0] is True


def test_empty_reviewer_list_fails_closed(repo):
    _bump(repo, add_optional, "1.1.0")
    assert cc.evaluate(repo, "HEAD", {"a"}, set(), set())[0] is False


def test_insufficient_bump_fails_even_with_approvals(repo):
    _edit(repo, drop_required)
    fc.write_lock(repo, "1.0.1", fc.current_hashes(repo))  # cố tình bump patch cho thay đổi major
    assert cc.evaluate(repo, "HEAD", {"a", "b", "lead"}, CORE, ELIG)[0] is False


def test_lock_out_of_sync_fails(repo):
    _edit(repo, add_optional)
    assert cc.evaluate(repo, "HEAD", {"a"}, CORE, ELIG)[0] is False


def test_latest_review_state_wins_and_author_excluded():
    reviews = [
        {"user": {"login": "A"}, "state": "APPROVED", "submitted_at": "1"},
        {"user": {"login": "b"}, "state": "APPROVED", "submitted_at": "1"},
        {"user": {"login": "b"}, "state": "CHANGES_REQUESTED", "submitted_at": "2"},
        {"user": {"login": "c"}, "state": "APPROVED", "submitted_at": "1"},
        {"user": {"login": "c"}, "state": "COMMENTED", "submitted_at": "2"},
        {"user": {"login": "me"}, "state": "APPROVED", "submitted_at": "1"},
    ]
    assert cc.latest_approvals(reviews, "me") == {"a", "c"}


def test_load_reviews_handles_paginated_concatenated_arrays():
    assert cc.load_reviews('[{"a":1}]\n[{"a":2}]') == [{"a": 1}, {"a": 2}]
