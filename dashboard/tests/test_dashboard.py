"""Chạy:  python -m pytest dashboard/tests -q   (nằm ngoài testpaths=tests nên không lẫn vào suite chính)."""
import json

import pytest
from fastapi.testclient import TestClient

from dashboard import app as app_mod
from dashboard import runs_reader


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    runs, reports = tmp_path / "runs", tmp_path / "midscene_run" / "report"
    reports.mkdir(parents=True)
    (reports / "explore.html").write_text("<html>ok</html>", encoding="utf-8")
    run = runs / "r-0002"
    _write(run / "report.json", {
        "run_id": "r-0002", "gate_verdict": "FAIL", "exit_code": 1,
        "deterministic_view": [{"task_id": "t-001", "status": "fail"}, {"task_id": "t-002", "status": "pass"}],
        "canary": [],
        "details": {"generated_at": "2026-09-22T00:00:00+00:00", "wallclock_s": 12.5, "results": {
            "t-001": {"status": "fail", "cost": {"tokens": 0, "usd": 0.0}},
            "t-002": {"status": "pass", "cost": {"tokens": 10, "usd": 0.25}},
            "t-101": {"status": "pass", "cost": {"tokens": None, "usd": None}}}},
    })
    _write(run / "results" / "t-001.json", {"task_id": "t-001", "status": "fail", "worker": {"name": "schemathesis"},
                                             "verdict": {"gating": True}, "findings": [{"title": "500"}]})
    _write(run / "results" / "t-101.json", {
        "task_id": "t-101", "status": "pass", "worker": {"name": "midscene-cli"}, "verdict": {"gating": False},
        "findings": [{"finding_id": "f-1", "title": "DOM không đổi", "severity_hint": "high",
                      "promote_candidate": {"repro_steps": ["mở /", "bấm Xoá"]}}],
        "evidence": [{"uri": "runs/r-0002/t-101/summary.json", "sha256": "ab" * 32}],
    })
    _write(run / "t-101" / "summary.json", {"results": [
        {"script": "..\\x\\explore.yaml", "success": True, "report": "..\\..\\..\\midscene_run\\report\\explore.html"},
        {"script": "evil.yaml", "success": True, "report": "..\\..\\..\\secret.html"},
    ]})
    (runs / "r-0003").mkdir()          # run chưa có report.json
    (runs / "r-step25").mkdir()        # thư mục rác: phải bị bỏ qua
    return runs, reports


def test_list_runs_filters_and_orders(tree):
    runs, _ = tree
    rows = runs_reader.list_runs(runs)
    assert [r["run_id"] for r in rows] == ["r-0003", "r-0002"]
    assert rows[0]["gate_verdict"] == "INCOMPLETE"
    assert rows[1]["gating_pass"] == 1 and rows[1]["gating_total"] == 2


def test_cost_null_is_not_zero(tree):
    runs, _ = tree
    s = runs_reader.summary(runs)
    assert s["usd_total"] == 0.25 and s["tokens_total"] == 10 and s["unreported_tasks"] == 1
    assert s["recent_fail"] == 1 and s["recent_pass"] == 0


def test_detail_splits_gate_and_discovery(tree):
    runs, reports = tree
    d = runs_reader.run_detail(runs, "r-0002", reports)
    assert {w["task_id"]: w["gating"] for w in d["workers"]} == {"t-001": True, "t-101": False}
    assert [f["finding_id"] for f in d["findings"]] == ["f-1"]  # finding của task gating không vào discovery
    assert d["findings"][0]["evidence"][0]["sha256"] == "ab" * 32


def test_midscene_report_confined_to_report_dir(tree):
    runs, reports = tree
    ms = runs_reader.midscene_reports(runs / "r-0002" / "t-101", reports)
    assert ms[0]["file"] == "explore.html" and ms[0]["exists"]
    assert ms[1]["file"] is None and not ms[1]["exists"]


def test_bad_run_id_rejected(tree):
    runs, reports = tree
    assert runs_reader.run_detail(runs, "../r-0002", reports) is None
    assert runs_reader.run_detail(runs, "r-9999", reports) is None


@pytest.fixture
def client(tree, tmp_path, monkeypatch):
    runs, reports = tree
    monkeypatch.setattr(app_mod, "RUNS_DIR", runs)
    monkeypatch.setattr(app_mod, "REPORT_DIR", reports)
    monkeypatch.setattr(app_mod, "STATE_DIR", tmp_path / "_state")
    monkeypatch.setattr(app_mod, "sut_alive", lambda: False)
    return TestClient(app_mod.app)


def test_api_404_and_ticket(client):
    assert client.get("/api/runs/r-9999").status_code == 404
    t = client.post("/api/tickets", json={"run_id": "r-0002", "finding_id": "f-1"}).json()
    assert t["key"] == "JIRA-101" and t["mock"] is True and t["evidence"][0]["sha256"] == "ab" * 32
    assert client.post("/api/tickets", json={"run_id": "r-0002", "finding_id": "f-1"}).json()["key"] == "JIRA-102"
    assert client.post("/api/tickets", json={"run_id": "r-0002", "finding_id": "nope"}).status_code == 404


def test_trigger_refuses_when_sut_offline(client, tree):
    runs, _ = tree
    before = sorted(p.name for p in runs.iterdir())
    assert client.post("/api/run").status_code == 412
    assert sorted(p.name for p in runs.iterdir()) == before  # không tạo run mới
