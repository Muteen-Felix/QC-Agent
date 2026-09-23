import importlib, sys

import pytest
from fastapi.testclient import TestClient

BODY = "Mua sữa. Nhớ túi. [[long]]"


@pytest.fixture
def fresh(monkeypatch):
    """fresh(bugs) -> TestClient với DB rỗng và bộ bug chỉ định ("none" = tắt hết)."""
    def make(bugs):
        monkeypatch.setenv("QC_BUGS", bugs)
        for name in [m for m in sys.modules if m.startswith("toyapp")]:
            del sys.modules[name]
        app = importlib.import_module("toyapp.app").app
        return TestClient(app, raise_server_exceptions=False)
    return make


def test_crud_roundtrip(fresh):
    c = fresh("none")
    r = c.post("/notes", json={"title": "a", "body": "b"})
    assert r.status_code == 201 and r.json()["id"] == 1
    assert c.get("/notes").json() == [{"id": 1, "title": "a", "body": "b"}]
    assert c.get("/notes/1").status_code == 200
    assert c.delete("/notes/1").status_code == 204
    assert c.get("/notes/1").status_code == 404


def test_unknown_id_404(fresh):
    assert fresh("none").get("/notes/abc").status_code == 404


def test_bug1_on_long_id_500(fresh):
    assert fresh("1").get("/notes/" + "x" * 65).status_code == 500


def test_bug1_off_long_id_404(fresh):
    assert fresh("none").get("/notes/" + "x" * 65).status_code == 404


def test_bug3_on_summary_longer(fresh):
    c = fresh("3")
    c.post("/notes", json={"title": "a", "body": BODY})
    assert len(c.post("/notes/1/summarize").json()["summary"]) > len(BODY)


def test_openapi_hides_telemetry(fresh):
    paths = fresh("none").get("/openapi.json").json()["paths"]
    assert not any(p.startswith("/__qc") for p in paths) and "/notes" in paths
