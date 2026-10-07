"""Fixture `node-api-harness` dựng và chạy được trong Docker (S4-05, bước "Docker nhẹ"): không npm, không gate. Khẳng định /health, /openapi.json, route người dùng,
và handler THẬT của N3 (200 đúng schema, 404 khai báo) độc lập với gate: N3 chạy qua gate với `exclude_path` nên gate không chứng minh được handler.
Cần docker + image qc-agent đúng commit (xem tools/image_check.py); thiếu thì SKIP (= chưa kiểm chứng) hoặc LỖI nếu QC_HARNESS_REQUIRE_DOCKER=1."""
import json

import pytest

from tests import harness_kit as kit

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def image():
    return kit.verified_image().image


@pytest.fixture
def repo(tmp_path_factory):
    with kit.workspace_dir() as base:
        yield base


def test_base_fixture_serves_health_openapi_and_users(image, repo):
    sut, _ = kit.make_repo(kit.NODE_SUT, repo)
    got = kit.probe_sut(sut, image, ["/health", "/openapi.json", "/users/1", "/users/2000", "/nope"])
    assert got["/health"] == (200, {"status": "ok"})
    assert got["/openapi.json"] == (200, json.loads((kit.NODE_SUT / "openapi.json").read_text(encoding="utf-8")))
    assert got["/users/1"] == (200, {"id": 1, "active": True})
    assert got["/users/2000"][0] == 404 and got["/nope"][0] == 404


def test_n3_handler_really_serves_the_new_operation(image, repo):
    sut, _ = kit.make_repo(kit.NODE_SUT, repo)
    kit.apply_n3(sut)
    got = kit.probe_sut(sut, image, ["/users/1/profile", "/users/2000/profile", "/openapi.json"])
    assert got["/users/1/profile"] == (200, {"id": 1, "bio": "hi"})          # đúng schema của operation (id, bio)
    assert got["/users/2000/profile"][0] == 404                               # 404 đã khai báo trong openapi.json
    assert kit.PROFILE_PATH in got["/openapi.json"][1]["paths"]


def test_base_fixture_does_not_serve_the_profile_operation_before_n3(image, repo):
    sut, _ = kit.make_repo(kit.NODE_SUT, repo)
    assert kit.probe_sut(sut, image, ["/users/1/profile"])["/users/1/profile"][0] == 404


def test_n1_changes_the_route_behaviour_and_n2_adds_no_route(image, repo):
    sut, _ = kit.make_repo(kit.NODE_SUT, repo)
    kit.apply_n1(sut)
    assert kit.probe_sut(sut, image, ["/users/1", "/users/2"]) == {"/users/1": (200, {"id": 1, "active": False}), "/users/2": (200, {"id": 2, "active": True})}
