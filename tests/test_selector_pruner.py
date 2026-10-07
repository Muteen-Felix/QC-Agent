import subprocess

import pytest

from qc_agent.selector.pruner import prune


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_pruner_keeps_all_file_entries_and_stable_digest(tmp_path):
    if not subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
        pytest.skip("git unavailable")
    git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "comment.py").write_text("# only comment\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    first = prune(tmp_path, base, head, per_file_tokens=10)
    second = prune(tmp_path, base, head, per_file_tokens=10)
    assert first.sha256 == second.sha256
    assert {item.path for item in first.files} == {"app.py", "package-lock.json", "comment.py"}
    assert next(item for item in first.files if item.path == "package-lock.json").hunks is None
    assert next(item for item in first.files if item.path == "comment.py").hunks is None
    with pytest.raises(ValueError):
        prune(tmp_path, "--output=x", head)


def test_merge_base_excludes_new_target_branch_commits(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "base.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "base")
    git(tmp_path, "branch", "feature")
    (tmp_path / "target_only.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "target")
    target = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-q", "feature")
    (tmp_path / "feature.py").write_text("y = 2\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "feature")
    head = git(tmp_path, "rev-parse", "HEAD")
    assert [item.path for item in prune(tmp_path, target, head).files] == ["feature.py"]


def test_binary_generated_vendor_rename_delete_and_total_cap(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "old.py").write_text("original = 1\n", encoding="utf-8")
    (tmp_path / "delete.py").write_text("deleted = True\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "mv", "old.py", "new.py")
    git(tmp_path, "rm", "delete.py")
    (tmp_path / "binary.bin").write_bytes(b"a\x00b")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor/a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "generated.py").write_text("# DO NOT EDIT\nx = 1\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("\n".join(f"x_{n} = {n}" for n in range(100)) + "\n", encoding="utf-8")
    (tmp_path / "comments.js").write_text("// only comment\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    result = prune(tmp_path, base, head, per_file_tokens=10, total_tokens=10)
    by_path = {item.path: item for item in result.files}
    assert by_path["binary.bin"].kind == "binary"
    assert by_path["vendor/a.py"].kind == "vendor"
    assert by_path["dist/x.py"].kind == "generated"
    assert by_path["generated.py"].kind == "generated"
    assert by_path["comments.js"].hunks is None
    assert by_path["delete.py"].status == "D"
    assert by_path["new.py"].status == "R" and by_path["new.py"].old_path == "old.py"
    assert by_path["large.py"].truncated
    assert len(result.files) == 8


def test_git_reads_ignore_directory_ownership(tmp_path, monkeypatch):
    """Container gate chạy `--user` lệch chủ thư mục (Docker Desktop): thiếu safe.directory thì git từ chối và Select lùi về FULL SET (S4-05b)."""
    from qc_agent.selector import pruner
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pruner.subprocess, "run", fake_run)
    pruner._git(tmp_path, "rev-parse", "HEAD")
    assert seen[0][:5] == ["git", "-c", "core.quotepath=off", "-c", "safe.directory=*"]


def _commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def _repo_with_changes(tmp_path):
    git(tmp_path, "init", "-q")
    body = "\n".join(f"line_{n} = {n}" for n in range(60)) + "\n"
    def text(name):
        return f"# {name}\n" + body   # mỗi file một dòng đầu riêng: nội dung giống hệt nhau thì git ghép nhầm cặp đổi tên
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(text(name), encoding="utf-8")
    base = _commit(tmp_path, "base")
    (tmp_path / "a.py").write_text(text("a.py").replace("line_5 = 5", "line_5 = 50").replace("line_40 = 40", "line_40 = 400"), encoding="utf-8")
    (tmp_path / "n.py").write_text("def new():\n    return 'mới 🚀'\n", encoding="utf-8")
    git(tmp_path, "mv", "b.py", "b2.py")
    (tmp_path / "b2.py").write_text(text("b.py").replace("line_20 = 20", "line_20 = 21"), encoding="utf-8")
    git(tmp_path, "mv", "c.py", "c2.py")
    return base, _commit(tmp_path, "head")


def _assert_hunks_are_headerless_and_complete(tmp_path, base, head, item):
    hunks = item.hunks
    assert hunks.startswith("@@ ") and not any(line.startswith(("diff --git", "index ", "--- ", "+++ ", "rename ", "similarity", "new file")) for line in hunks.splitlines())
    raw = subprocess.run(["git", "diff", "--no-color", "-w", "-U1", "-M", base, head, "--", *([item.old_path] if item.old_path else []), item.path], cwd=tmp_path, check=True,
                         capture_output=True).stdout.decode("utf-8").splitlines()   # UTF-8 tường minh: text=True giải mã bằng cp1252 trên Windows
    changed = [line for line in raw if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert changed and all(line in hunks.splitlines() for line in changed), item.path   # không mất dòng thay đổi nào
    assert not item.truncated and item.dropped_hunks == 0


def test_hunks_carry_no_git_header_but_every_changed_line_and_the_file_identity_survive(tmp_path):
    base, head = _repo_with_changes(tmp_path)
    by_path = {item.path: item for item in prune(tmp_path, base, head).files}
    assert sorted(by_path) == ["a.py", "b2.py", "c2.py", "n.py"]
    for path in ("a.py", "n.py"):
        _assert_hunks_are_headerless_and_complete(tmp_path, base, head, by_path[path])
    assert (by_path["b2.py"].status, by_path["b2.py"].old_path) == ("R", "b.py") and (by_path["c2.py"].status, by_path["c2.py"].old_path) == ("R", "c.py")   # danh tính file nằm ở entry, không ở header
    assert (by_path["n.py"].status, by_path["n.py"].old_path) == ("A", None) and "mới 🚀" in by_path["n.py"].hunks
