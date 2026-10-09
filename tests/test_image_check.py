"""tools/image_check.py: image cũ phải làm harness thất bại (S4-05). Không cần Docker: `_run` được thay bằng bảng trả lời giả; thuật toán băm nội dung được chạy thật."""
import json
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import image_check as ic  # noqa: E402

HEAD = "a6225d2f6c48fc197ff4501417dcddd82b6e04f3"
OLD = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "qc-agent:harness-a6225d2"
META = {"Version": 2, "UpdatedAt": "2026-09-28T13:05:44.359328237Z", "DownloadedAt": "2026-09-28T16:55:19.526617436Z"}


def proc(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class FakeEnv:
    """Bảng trả lời cho `image_check._run`: ghi lại mọi lệnh để test kiểm tra."""

    def __init__(self, monkeypatch, *, head=HEAD, image_sha=HEAD, dirty="", content_hash="h", meta=META, exists=True, build=None):
        self.calls = []
        self.head, self.image_sha, self.dirty, self.content_hash, self.meta, self.exists, self.build = head, image_sha, dirty, content_hash, meta, exists, build
        monkeypatch.setattr(ic, "_run", self)

    def __call__(self, args, *, cwd=None, timeout=300):
        self.calls.append(list(args))
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return proc(self.head + "\n")
        if args[:2] == ["git", "status"]:
            return proc(self.dirty)
        if args[:2] == ["docker", "image"]:
            return proc(returncode=0 if self.exists else 1)
        if args[:2] == ["docker", "build"]:
            return self.build or proc()
        if "printenv" in args:
            if self.image_sha is None:
                return proc(returncode=1)
            return proc(self.image_sha + "\n")
        if "cat" in args:
            return proc(json.dumps(self.meta)) if self.meta is not None else proc(returncode=1, stderr="No such file")
        if "python" in args:
            return proc(self.content_hash + "\n")
        raise AssertionError(f"lệnh không mong đợi: {args}")


@pytest.fixture
def clean_env():
    return {}   # không có biến QC_HARNESS_*: kiểm đúng mặc định


def test_image_built_from_head_with_clean_inputs_is_verified(monkeypatch, clean_env):
    FakeEnv(monkeypatch)
    verdict = ic.verify(IMAGE, env=clean_env)
    assert verdict.verified and verdict.mode == "commit" and verdict.image_commit == HEAD
    assert "ĐÃ XÁC MINH" in verdict.label


def test_old_commit_is_stale_and_the_message_carries_the_exact_build_command(monkeypatch, clean_env):
    FakeEnv(monkeypatch, image_sha=OLD)
    with pytest.raises(ic.StaleImage) as caught:
        ic.verify(IMAGE, env=clean_env)
    text = str(caught.value)
    assert "0123456" in text and "a6225d2" in text
    assert "docker build --build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD) -t qc-agent:harness-a6225d2 ." in text


@pytest.mark.parametrize("sha", ["unknown", "", None])
def test_image_without_a_commit_stamp_is_stale(monkeypatch, clean_env, sha):
    """`unknown` = build thiếu --build-arg; chuỗi rỗng/biến vắng (printenv thoát 1) = image không phải qc-agent (vd. node)."""
    FakeEnv(monkeypatch, image_sha=sha)
    with pytest.raises(ic.StaleImage, match="không có dấu commit"):
        ic.verify(IMAGE, env=clean_env)


def test_matching_sha_is_not_enough_when_the_working_tree_is_dirty(monkeypatch, clean_env):
    FakeEnv(monkeypatch, dirty=" M src/qc_agent/core/engine.py\n?? src/qc_agent/new.py\n")
    with pytest.raises(ic.StaleImage, match="2 thay đổi chưa commit"):
        ic.verify(IMAGE, env=clean_env)


def test_dirty_check_only_looks_at_the_image_inputs(monkeypatch, clean_env):
    env = FakeEnv(monkeypatch)
    ic.verify(IMAGE, env=clean_env)
    status = next(call for call in env.calls if call[:2] == ["git", "status"])
    tail = status[status.index("--") + 1:]
    assert set(tail) == set(ic.INPUT_PATHS) and "tests" not in tail and "docs" not in tail


def test_explicit_escape_hatch_runs_but_marks_the_result_unverified_and_warns_loudly(monkeypatch):
    FakeEnv(monkeypatch, image_sha=OLD)
    with pytest.warns(ic.ImageWarning, match="KHÔNG DÙNG ĐỂ NGHIỆM THU"):
        verdict = ic.verify(IMAGE, env={ic.ENV_ALLOW: "1"})
    assert not verdict.verified and verdict.label.startswith("IMAGE CHƯA XÁC MINH")


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_the_literal_1_enables_the_escape_hatch(monkeypatch, value):
    FakeEnv(monkeypatch, image_sha=OLD)
    with pytest.raises(ic.StaleImage):
        ic.verify(IMAGE, env={ic.ENV_ALLOW: value})


def test_missing_or_broken_image_is_not_reported_as_stale(monkeypatch, clean_env):
    class Broken(FakeEnv):
        def __call__(self, args, *, cwd=None, timeout=300):
            if "printenv" in args:
                return proc(returncode=125, stderr="Unable to find image 'qc-agent:nope' locally")
            return super().__call__(args, cwd=cwd, timeout=timeout)
    Broken(monkeypatch)
    with pytest.raises(ic.ImageCheckError) as caught:
        ic.verify("qc-agent:nope", env=clean_env)
    assert not isinstance(caught.value, ic.StaleImage) and "Unable to find image" in str(caught.value)


def test_unknown_check_mode_is_a_configuration_error(monkeypatch):
    FakeEnv(monkeypatch)
    with pytest.raises(ic.ImageCheckError, match="commit\\|content"):
        ic.verify(IMAGE, env={ic.ENV_MODE: "fast"})


# ---- lớp 2: băm nội dung ----

def _package(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


FILES = {"__init__.py": b"", "core/engine.py": b"x = 1\r\ny = 2\r\n", "core/__init__.py": b"", "z_last.py": "# tiếng Việt\n".encode()}


def test_content_hash_algorithm_inside_the_image_matches_the_local_one(tmp_path):
    """IMAGE_HASH_SCRIPT chạy thật trên một gói giả trông như site-packages; bản local tính trên src/qc_agent cùng nội dung (một bên CRLF, một bên LF)."""
    site = tmp_path / "site"
    _package(site / "qc_agent", {rel: data.replace(b"\r\n", b"\n") for rel, data in FILES.items()})
    (site / "qc_agent" / "__pycache__").mkdir()
    (site / "qc_agent" / "__pycache__" / "junk.py").write_bytes(b"ignored")
    repo = tmp_path / "repo"
    _package(repo / "src" / "qc_agent", FILES)   # giữ CRLF ở máy Windows
    done = subprocess.run([sys.executable, "-c", ic.IMAGE_HASH_SCRIPT], capture_output=True, text=True, env={"PYTHONPATH": str(site), "PATH": ""}, check=True)
    assert done.stdout.strip() == ic.local_content_hash(repo)


def test_content_hash_changes_when_any_package_file_changes(tmp_path):
    repo = tmp_path / "repo"
    _package(repo / "src" / "qc_agent", FILES)
    before = ic.local_content_hash(repo)
    (repo / "src" / "qc_agent" / "core" / "engine.py").write_bytes(b"x = 1\ny = 3\n")
    assert ic.local_content_hash(repo) != before


def test_content_mode_accepts_matching_hash_even_with_dirty_tree_and_no_stamp(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _package(repo / "src" / "qc_agent", FILES)
    FakeEnv(monkeypatch, image_sha="unknown", dirty=" M src/qc_agent/a.py\n", content_hash=ic.local_content_hash(repo))
    verdict = ic.verify(IMAGE, repo=repo, env={ic.ENV_MODE: "content"})
    assert verdict.verified and verdict.mode == "content"


def test_content_mode_rejects_a_different_hash(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _package(repo / "src" / "qc_agent", FILES)
    FakeEnv(monkeypatch, content_hash="0" * 64)
    with pytest.raises(ic.StaleImage, match="băm qc_agent"):
        ic.verify(IMAGE, repo=repo, env={ic.ENV_MODE: "content"})


# ---- tuổi DB Trivy ----

def _freeze(monkeypatch, iso: str):
    from qc_agent.adapters import trivy_adapter
    monkeypatch.setattr(trivy_adapter, "_now", lambda: datetime.fromisoformat(iso).replace(tzinfo=timezone.utc))


def test_db_age_uses_the_adapters_own_rounding(monkeypatch):
    """UpdatedAt 2026-09-28T13:05 và bây giờ 2026-10-07T10:00 = 8,87 ngày => làm tròn LÊN thành 9, đúng như `trivy.db_age_days` của suite."""
    FakeEnv(monkeypatch)
    _freeze(monkeypatch, "2026-10-07T10:00:00")
    assert ic.trivy_db_age_days(IMAGE) == 9


@pytest.mark.parametrize(("now", "age"), [("2026-10-12T13:05:44", 14), ("2026-10-12T13:05:45", 15)])
def test_the_14_day_boundary_matches_the_deps_suite(monkeypatch, now, age):
    """Suite chặn khi `trivy.db_age_days > 14`: đúng 14 ngày còn đạt, vượt một giây thì làm tròn lên 15 và đỏ."""
    FakeEnv(monkeypatch)
    _freeze(monkeypatch, now)
    assert ic.trivy_db_age_days(IMAGE) == age
    if age == 14:
        with pytest.warns(ic.ImageWarning, match="sắp quá"):   # 14 ngày vẫn đạt nhưng đã sát ngưỡng
            ic.check_trivy_db(IMAGE, deps_expected=True)
    else:
        with pytest.raises(ic.StaleTrivyDb):
            ic.check_trivy_db(IMAGE, deps_expected=True)


def test_stale_db_is_a_hard_error_when_deps_is_expected_and_names_the_rebuild(monkeypatch):
    FakeEnv(monkeypatch)
    _freeze(monkeypatch, "2026-10-20T00:00:00")
    with pytest.raises(ic.StaleTrivyDb) as caught:
        ic.check_trivy_db(IMAGE, deps_expected=True)
    text = str(caught.value)
    assert "22 ngày > 14" in text and "docker build --no-cache --build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD)" in text and "--no-cache-filter trivy` đã đo là không làm mới" in text


def test_stale_db_only_warns_when_deps_is_not_expected(monkeypatch):
    FakeEnv(monkeypatch)
    _freeze(monkeypatch, "2026-10-20T00:00:00")
    with pytest.warns(ic.ImageWarning, match="chỉ cảnh báo"):
        assert ic.check_trivy_db(IMAGE, deps_expected=False) == 22


def test_db_close_to_the_limit_warns_and_a_fresh_one_is_silent(monkeypatch):
    FakeEnv(monkeypatch)
    _freeze(monkeypatch, "2026-10-10T00:00:00")   # 11,4 ngày => 12
    with pytest.warns(ic.ImageWarning, match="sắp quá"):
        assert ic.check_trivy_db(IMAGE, deps_expected=True) == 12
    _freeze(monkeypatch, "2026-10-01T00:00:00")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ic.check_trivy_db(IMAGE, deps_expected=True) == 3


def test_image_without_a_trivy_db_is_a_check_error(monkeypatch):
    FakeEnv(monkeypatch, meta=None)
    with pytest.raises(ic.ImageCheckError, match="không có DB Trivy"):
        ic.trivy_db_age_days(IMAGE)


def test_unreadable_db_metadata_is_a_check_error(monkeypatch):
    FakeEnv(monkeypatch, meta={"Version": 2})
    with pytest.raises(ic.ImageCheckError, match="UpdatedAt"):
        ic.trivy_db_age_days(IMAGE)


# ---- ensure_image ----

def test_ensure_image_prefers_the_named_image_and_still_verifies_it(monkeypatch):
    env = FakeEnv(monkeypatch, image_sha=OLD)
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    with pytest.raises(ic.StaleImage):
        ic.ensure_image(env={ic.ENV_IMAGE: "prebuilt:1"})
    assert not any(call[:2] == ["docker", "build"] for call in env.calls)


def test_ensure_image_uses_the_tag_of_head_when_it_exists(monkeypatch):
    env = FakeEnv(monkeypatch)
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    verdict = ic.ensure_image(env={})
    assert verdict.image == IMAGE and verdict.verified
    assert not any(call[:2] == ["docker", "build"] for call in env.calls)


def test_ensure_image_builds_from_head_with_the_commit_stamp(monkeypatch):
    env = FakeEnv(monkeypatch, exists=False)
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    ic.ensure_image(env={})
    build = next(call for call in env.calls if call[:2] == ["docker", "build"])
    assert build[:6] == ["docker", "build", "--build-arg", f"QC_AGENT_GIT_SHA={HEAD}", "-t", IMAGE]


def test_ensure_image_without_build_explains_how_to_get_one(monkeypatch):
    FakeEnv(monkeypatch, exists=False)
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    with pytest.raises(ic.ImageCheckError, match="docker build --build-arg"):
        ic.ensure_image(build=False, env={})


def test_a_tls_build_failure_stops_with_instructions_instead_of_skipping(monkeypatch):
    FakeEnv(monkeypatch, exists=False, build=proc(returncode=1, stderr="Error: getaddrinfo ENOTFOUND cdn.playwright.dev"))
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    with pytest.raises(ic.ImageCheckError) as caught:
        ic.ensure_image(env={})
    assert "Thử lại MỘT lần" in str(caught.value) and ic.ENV_IMAGE in str(caught.value) and "ENOTFOUND" in str(caught.value)


def test_ensure_image_without_docker_is_a_check_error(monkeypatch):
    monkeypatch.setattr(ic, "docker_available", lambda: False)
    with pytest.raises(ic.ImageCheckError, match="không có docker"):
        ic.ensure_image(env={})


# ---- CLI ----

def test_cli_exit_codes(monkeypatch, capsys):
    FakeEnv(monkeypatch, image_sha=OLD)
    assert ic.main(["--image", IMAGE]) == 1 and "LỖI" in capsys.readouterr().err
    FakeEnv(monkeypatch)
    assert ic.main(["--image", IMAGE]) == 0 and "ĐÃ XÁC MINH" in capsys.readouterr().out
    monkeypatch.setattr(ic, "docker_available", lambda: False)
    monkeypatch.delenv(ic.ENV_IMAGE, raising=False)
    assert ic.main([]) == 3


# ---- ngữ nghĩa skip/lỗi của fixture image trong tests/harness_kit.py (không cần Docker) ----

def _kit(monkeypatch):
    from tests import harness_kit as kit
    monkeypatch.setattr(kit, "_VERDICT", {})
    return kit


def test_missing_docker_skips_with_a_reason_that_says_unverified(monkeypatch):
    kit = _kit(monkeypatch)
    monkeypatch.delenv(kit.ENV_REQUIRE, raising=False)
    monkeypatch.setattr(ic, "docker_available", lambda: False)
    with pytest.raises(pytest.skip.Exception, match="CHƯA KIỂM CHỨNG"):
        kit.verified_image()


def test_missing_docker_is_an_error_when_docker_is_required(monkeypatch):
    kit = _kit(monkeypatch)
    monkeypatch.setenv(kit.ENV_REQUIRE, "1")
    monkeypatch.setattr(ic, "docker_available", lambda: False)
    with pytest.raises(pytest.fail.Exception, match="QC_HARNESS_REQUIRE_DOCKER=1"):
        kit.verified_image()


def test_a_stale_image_fails_every_docker_test_and_is_never_a_skip(monkeypatch):
    """Image cũ không được làm test 'xanh vì skip': `verified_image` lỗi ngay cả khi KHÔNG đặt QC_HARNESS_REQUIRE_DOCKER."""
    kit = _kit(monkeypatch)
    monkeypatch.delenv(kit.ENV_REQUIRE, raising=False)
    FakeEnv(monkeypatch, image_sha=OLD)
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    monkeypatch.setenv(ic.ENV_IMAGE, "qc-agent:old")
    monkeypatch.delenv(ic.ENV_ALLOW, raising=False)
    monkeypatch.delenv(ic.ENV_MODE, raising=False)
    with pytest.raises(pytest.fail.Exception, match="không chứng minh được là build từ commit đang thử"):
        kit.verified_image()


def test_an_image_that_cannot_be_built_or_found_skips_unless_docker_is_required(monkeypatch):
    kit = _kit(monkeypatch)
    FakeEnv(monkeypatch, exists=False)
    monkeypatch.setattr(ic, "docker_available", lambda: True)
    monkeypatch.delenv(ic.ENV_IMAGE, raising=False)
    monkeypatch.delenv(kit.ENV_BUILD, raising=False)
    monkeypatch.delenv(kit.ENV_REQUIRE, raising=False)
    with pytest.raises(pytest.skip.Exception, match="không có image dùng được"):
        kit.verified_image()
    monkeypatch.setenv(kit.ENV_REQUIRE, "1")
    with pytest.raises(pytest.fail.Exception, match="QC_HARNESS_REQUIRE_DOCKER=1"):
        kit.verified_image()


def test_preflight_is_a_hard_error_only_when_deps_is_expected(monkeypatch):
    kit = _kit(monkeypatch)
    FakeEnv(monkeypatch)
    _freeze(monkeypatch, "2026-10-20T00:00:00")
    with pytest.raises(pytest.fail.Exception, match="22 ngày > 14"):
        kit.preflight(IMAGE, "N4", expect_deps=True)
    with pytest.warns(ic.ImageWarning):
        kit.preflight(IMAGE, "N1", expect_deps=False)


def test_expect_deps_is_reconciled_with_the_suites_that_really_ran(monkeypatch, tmp_path):
    kit = _kit(monkeypatch)
    run_dir = tmp_path / "r-0001"
    run_dir.mkdir()
    (run_dir / "plan.yaml").write_text("tasks:\n- {task_id: t-001, capability: api.property}\n- {task_id: t-012, capability: deps.vuln}\n", encoding="utf-8")
    res = kit.Result({}, "", tmp_path, run_dir)
    kit.check_expect_deps(res, "N4", expect_deps=True)
    with pytest.raises(pytest.fail.Exception, match="N-x khai expect_deps=False nhưng deps ĐÃ chạy"):
        kit.check_expect_deps(res, "N-x", expect_deps=False)
    (run_dir / "plan.yaml").write_text("tasks:\n- {task_id: t-001, capability: api.property}\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="N-y khai expect_deps=True nhưng deps KHÔNG chạy"):
        kit.check_expect_deps(res, "N-y", expect_deps=True)
