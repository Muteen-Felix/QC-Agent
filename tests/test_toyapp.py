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


# ---------- BUG-4…BUG-13 (S1-08): mutant nghiệp vụ, mặc định TẮT, mỗi cờ chỉ làm sai đúng một hành vi ----------

def _post(c, **over):
    return c.post("/notes", json={"title": "a", "body": "b", **over})


def _b4(c):
    return _post(c, title="").status_code                                     # AC-1.3


def _b5(c):
    return _post(c, title="a" * 200).status_code                              # AC-1.5


def _b6(c):
    return _post(c).status_code                                               # AC-1.1


def _b7(c):
    return c.delete("/notes/999999").status_code                              # AC-3.4


def _b8(c):
    note = _post(c).json()
    c.delete(f"/notes/{note['id']}")
    return c.get(f"/notes/{note['id']}").status_code                          # AC-3.2


def _b9(c):
    _post(c)
    return len(c.get("/notes").json())                                        # AC-2.1


def _b10(c):
    return _post(c, title="  a  ").json()["title"]                            # AC-1.2


def _b11(c):
    return c.post("/notes/999999/summarize").status_code                      # AC-4.5


def _b12(c):
    note = _post(c, body="Câu một. Câu hai.").json()
    c.post(f"/notes/{note['id']}/summarize")
    return c.get(f"/notes/{note['id']}").json()["body"]                       # AC-4.6


def _b13(c):
    _post(c, title="đầu tiên")
    second = _post(c, title="thứ hai").json()
    return c.get(f"/notes/{second['id']}").json()["title"]                    # AC-2.2


MUTANTS = {  # bug -> (kịch bản, kết quả ĐÚNG theo PRD, kết quả khi bật mutant)
    "4": (_b4, 422, 201), "5": (_b5, 201, 422), "6": (_b6, 201, 200), "7": (_b7, 404, 204), "8": (_b8, 404, 200), "9": (_b9, 1, 0),
    "10": (_b10, "  a  ", "a"), "11": (_b11, 404, 200), "12": (_b12, "Câu một. Câu hai.", "Câu một"), "13": (_b13, "thứ hai", "đầu tiên"),
}


@pytest.mark.parametrize("bug", MUTANTS)
def test_each_mutant_misbehaves_only_when_its_own_flag_is_on(fresh, bug):
    scenario, good, bad = MUTANTS[bug]
    assert scenario(fresh("none")) == good                                    # sạch: đúng PRD
    assert scenario(fresh(bug)) == bad                                        # bật đúng cờ: sai
    for other in MUTANTS:                                                     # bật từng cờ khác: kịch bản này vẫn đúng (mỗi cờ độc lập)
        if other != bug and not (bug == "5" and other == "6"):                # (cờ 6 đổi chính mã trạng thái mà kịch bản 5 đọc)
            assert scenario(fresh(other)) == good, other


def test_the_mutants_are_off_by_default_and_the_default_set_is_unchanged(monkeypatch):
    monkeypatch.delenv("QC_BUGS", raising=False)
    for name in [m for m in sys.modules if m.startswith("toyapp")]:
        del sys.modules[name]
    assert importlib.import_module("toyapp.app").BUGS == {"1", "2", "3"}      # CI hiện có không đổi hành vi
    assert not set(MUTANTS) & importlib.import_module("toyapp.app").BUGS


@pytest.mark.parametrize("bug", MUTANTS)
def test_a_mutant_flag_does_not_switch_on_the_old_bugs(fresh, bug):
    c = fresh(bug)
    assert c.get("/notes/" + "x" * 65).status_code == 404                     # BUG-1 tắt
    c.post("/notes", json={"title": "a", "body": "Ghi chú [[long]]. Chi tiết."})
    if bug not in ("12", "10"):
        assert c.post("/notes/1/summarize").json()["summary"] == "Ghi chú [[long]]"   # BUG-3 tắt
    assert c.get("/__qc/config").json()["bugs"] == [bug]


@pytest.mark.parametrize("bug", MUTANTS)
def test_a_mutant_response_still_conforms_to_the_documented_schema(fresh, bug):
    """Lỗi nghiệp vụ nhưng đúng hình dạng: đây là lý do api-contract không bắt được chúng."""
    c = fresh(bug)
    created = _post(c, title="a", body="Một. Hai.")
    assert created.status_code in (200, 201) and set(created.json()) == {"id", "title", "body"} and isinstance(created.json()["id"], int)
    listed = c.get("/notes")
    assert listed.status_code == 200 and isinstance(listed.json(), list) and all(set(n) == {"id", "title", "body"} for n in listed.json())
    summary = c.post(f"/notes/{created.json()['id']}/summarize")
    assert summary.status_code == 200 and set(summary.json()) == {"summary", "model", "prompt_hash"}
