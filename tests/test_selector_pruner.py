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
