"""Image cũ làm test fail, kiểm bằng Docker THẬT (S4-05): image đúng commit qua; image không có dấu commit (node) và image dẫn xuất mang SHA cũ bị từ chối.
Thiếu Docker/image: SKIP = CHƯA KIỂM CHỨNG (QC_HARNESS_REQUIRE_DOCKER=1 biến skip thành lỗi)."""
import re
import subprocess
import sys
import uuid

import pytest

from tests import harness_kit as kit

import image_check as ic  # noqa: E402  (tools/ đã nằm trên sys.path nhờ harness_kit)

pytestmark = pytest.mark.docker
ROOT_DOCKERFILE = (kit.ROOT / "Dockerfile").read_text(encoding="utf-8")
NODE_IMAGE = re.search(r"ARG NODE_IMAGE=(\S+)", ROOT_DOCKERFILE).group(1)


@pytest.fixture(scope="module")
def image():
    return kit.verified_image().image


def test_the_image_built_from_head_is_verified(image):
    verdict = ic.verify(image)
    assert verdict.verified and verdict.image_commit == verdict.head and verdict.mode == "commit"
    assert ic.image_commit(image) == ic.source_commit()


def test_an_image_without_a_commit_stamp_is_stale(image):
    """`node:22-bookworm-slim` (không có QC_AGENT_GIT_SHA) hoặc bản dựng thiếu --build-arg (`unknown`) đều bị từ chối."""
    with pytest.raises(ic.StaleImage, match="không có dấu commit"):
        ic.verify(NODE_IMAGE)


def test_a_derived_image_carrying_an_old_sha_is_stale(image):
    tag = f"qc-agent:stale-{uuid.uuid4().hex[:8]}"
    built = subprocess.run(["docker", "build", "-q", "-t", tag, "-"], input=f"FROM {image}\nENV QC_AGENT_GIT_SHA={'0' * 40}\n", capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr
    try:
        with pytest.raises(ic.StaleImage, match="0000000"):
            ic.verify(tag)
        with pytest.warns(ic.ImageWarning, match="KHÔNG DÙNG ĐỂ NGHIỆM THU"):
            assert not ic.verify(tag, env={ic.ENV_ALLOW: "1"}).verified
        done = subprocess.run([sys.executable, str(kit.ROOT / "tools" / "image_check.py"), "--image", tag], capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert done.returncode == 1 and "docker build --build-arg QC_AGENT_GIT_SHA" in done.stderr
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True)


def test_content_mode_matches_the_real_image_against_the_working_tree(image, monkeypatch):
    monkeypatch.setenv(ic.ENV_MODE, "content")
    assert ic.verify(image).verified


def test_the_trivy_db_age_is_readable_from_the_real_image(image):
    age = ic.trivy_db_age_days(image)
    assert 0 <= age < 400
