"""Trường catalog dành cho bộ sinh dạng agent (PR 1 của kế hoạch GT agent): priority/technique/preconditions/rationale/evidence trên TC,
và `coverage_plan` / `waivers` / `spec_conflicts` ở gốc catalog. Tất cả TUỲ CHỌN: catalog cũ phải hợp lệ và render ra đúng từng byte như trước.
"""
import copy
import json
from pathlib import Path

import pytest
import yaml

from qc_agent.groundtruth import generate as gen
from qc_agent.groundtruth import render as r
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.merge import merge
from qc_agent.groundtruth.prd import parse_prd

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHA = "a" * 64


def tc(tc_id="TC-AC-1.1-aaaaaa", ac="AC-1.1", status="draft", origin="llm", **extra):
    base = {"tc_id": tc_id, "title": "tạo ghi chú", "ac_refs": [ac], "kind": "api_functional", "status": status, "origin": origin,
            "steps": [{"request": {"method": "POST", "path": "/notes", "json": {"title": "x"}}, "expect": {"status": [201]}}]}
    base.update(extra)
    return base


def catalog(*cases, **extra):
    out = {"version": 1, "prd": {"id": "demo", "sha256": SHA, "source": "docs/prd/demo.md"},
           "generated_by": {"model": "claude-opus-5-5", "prompt_version": "gt-agent/1"}, "status": "draft",
           "stories": [{"story_id": "US-1", "title": "Ghi chú", "acs": [{"ac_id": "AC-1.1", "text": "tạo"}, {"ac_id": "AC-1.2", "text": "xoá"}]}],
           "test_cases": list(cases) or [tc()], "uncovered_acs": []}
    out.update(extra)
    return out


AGENT_TC = dict(priority="high", technique="boundary", preconditions="không cần dữ liệu có sẵn", rationale="title tối đa 200 ký tự",
                evidence=[{"path": "app/models.py", "line": 12}, {"path": "app/routes.py"}])
PLAN = [{"ac_id": "AC-1.1", "technique": "boundary", "scenario": "title dài 200 và 201", "decision": "planned", "tc_ids": ["TC-AC-1.1-aaaaaa"]}]
WAIVER = {"kind": "api", "target": "POST /notes 500", "reason_code": "needs_infra_fault", "reason": "không tái hiện được qua HTTP", "status": "approved"}
CONFLICT = {"ac_id": "AC-1.2", "summary": "PRD nói 204 nhưng mã trả 200", "evidence": [{"path": "app/routes.py", "line": 40}], "status": "open"}


# ---------------- schema ----------------

def test_old_catalog_without_new_fields_is_valid():
    assert gt_schema.validate_catalog(catalog()) == []


def test_new_fields_are_accepted():
    data = catalog(tc(**AGENT_TC), coverage_plan=PLAN, waivers=[WAIVER], spec_conflicts=[CONFLICT])
    assert gt_schema.validate_catalog(data) == []


@pytest.mark.parametrize("name,mutate", [
    ("technique lạ", lambda d: d["test_cases"][0].__setitem__("technique", "fuzz")),
    ("priority lạ", lambda d: d["test_cases"][0].__setitem__("priority", "urgent")),
    ("rationale quá dài", lambda d: d["test_cases"][0].__setitem__("rationale", "x" * 301)),
    ("quá 5 evidence", lambda d: d["test_cases"][0].__setitem__("evidence", [{"path": f"f{i}.py"} for i in range(6)])),
    ("evidence path có ..", lambda d: d["test_cases"][0].__setitem__("evidence", [{"path": "../etc/passwd"}])),
    ("evidence path tuyệt đối", lambda d: d["test_cases"][0].__setitem__("evidence", [{"path": "/etc/passwd"}])),
    ("evidence khoá lạ", lambda d: d["test_cases"][0].__setitem__("evidence", [{"path": "a.py", "snippet": "secret"}])),
    ("plan decision lạ", lambda d: d.__setitem__("coverage_plan", [{**PLAN[0], "decision": "maybe"}])),
    ("waiver reason_code lạ", lambda d: d.__setitem__("waivers", [{**WAIVER, "reason_code": "lazy"}])),
    ("waiver thiếu lý do", lambda d: d.__setitem__("waivers", [{k: v for k, v in WAIVER.items() if k != "reason"}])),
    ("conflict status lạ", lambda d: d.__setitem__("spec_conflicts", [{**CONFLICT, "status": "ignored"}])),
    ("khoá gốc lạ", lambda d: d.__setitem__("extra", [])),
])
def test_new_fields_reject_bad_values(name, mutate):
    data = copy.deepcopy(catalog(tc(**AGENT_TC), coverage_plan=PLAN, waivers=[WAIVER], spec_conflicts=[CONFLICT]))
    mutate(data)
    assert gt_schema.validate_catalog(data), name


def test_emit_tool_schema_is_unchanged():
    """Single-shot không được nhận thêm trường nào: fixture response cũ vẫn phải hợp lệ."""
    response = json.loads((FIXTURES / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))
    emitted = next(block["input"] for block in response["content"] if block["type"] == "tool_use")
    assert gt_schema.errors(emitted, gen.tool_schema()) == []   # tool_schema() nới khoảng mã HTTP: fixture cố ý có TC mã sai để `_convert` bỏ
    props = gen.tool_schema()["properties"]["test_cases"]["items"]["properties"]
    assert not {"priority", "technique", "preconditions", "rationale", "evidence"} & set(props)


# ---------------- render ----------------

def test_old_catalog_renders_without_new_keys():
    text = r._catalog_file(catalog())
    for key in ("coverage_plan", "waivers", "spec_conflicts", "priority", "technique", "evidence"):
        assert key not in text


def test_render_orders_new_keys_and_is_deterministic():
    data = catalog(tc(**AGENT_TC), coverage_plan=PLAN, waivers=[WAIVER], spec_conflicts=[CONFLICT])
    first, second = r._catalog_file(data), r._catalog_file(copy.deepcopy(data))
    assert first == second
    parsed = yaml.safe_load(first)
    assert list(parsed["test_cases"][0])[:8] == ["tc_id", "title", "ac_refs", "kind", "priority", "technique", "status", "origin"]
    assert list(parsed["test_cases"][0])[-3:] == ["rationale", "evidence", "steps"][-3:]
    assert list(parsed["test_cases"][0]["evidence"][0]) == ["path", "line"]
    assert list(parsed) == ["version", "prd", "generated_by", "status", "stories", "test_cases", "uncovered_acs", "coverage_plan", "waivers", "spec_conflicts"]
    assert list(parsed["spec_conflicts"][0]) == ["ac_id", "summary", "evidence", "status"]
    assert gt_schema.validate_catalog(parsed) == []


def test_new_fields_do_not_change_generated_tests():
    plain = {f.path: f.content for f in r.render(catalog(), sut_root=Path("."))}
    rich = {f.path: f.content for f in r.render(catalog(tc(**AGENT_TC), coverage_plan=PLAN), sut_root=Path("."))}
    assert {p: c for p, c in plain.items() if p.startswith(r.TESTS_DIR)} == {p: c for p, c in rich.items() if p.startswith(r.TESTS_DIR)}


# ---------------- generate._metadata ----------------

def test_assemble_copies_agent_metadata_and_keeps_tc_id():
    prd = parse_prd(FIXTURES / "prd" / "noteboard-prd.md", openapi_source=str(FIXTURES / "openapi" / "noteboard.json"))
    response = json.loads((FIXTURES / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(next(block["input"] for block in response["content"] if block["type"] == "tool_use"))
    base_ids = [x["tc_id"] for x in gen._assemble(prd, copy.deepcopy(data), model="m", version="gt-agent/1", source="s")[0]["test_cases"]]
    data["test_cases"][0].update({**AGENT_TC, "rationale": "  nhiều   khoảng   trắng  ", "preconditions": None})
    catalog_out = gen._assemble(prd, data, model="m", version="gt-agent/1", source="s")[0]
    assert [x["tc_id"] for x in catalog_out["test_cases"]] == base_ids
    first = next(x for x in catalog_out["test_cases"] if x.get("technique") == "boundary")
    assert first["priority"] == "high" and first["rationale"] == "nhiều khoảng trắng"
    assert "preconditions" not in first
    assert first["evidence"] == [{"path": "app/models.py", "line": 12}, {"path": "app/routes.py"}]
    assert gt_schema.validate_catalog(catalog_out) == []


def test_assemble_drops_tc_with_invalid_metadata():
    prd = parse_prd(FIXTURES / "prd" / "noteboard-prd.md", openapi_source=str(FIXTURES / "openapi" / "noteboard.json"))
    response = json.loads((FIXTURES / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(next(block["input"] for block in response["content"] if block["type"] == "tool_use"))
    baseline = gen._assemble(prd, copy.deepcopy(data), model="m", version="gt-agent/1", source="s")   # fixture đã có sẵn vài TC cố ý sai
    data["test_cases"][0]["technique"] = "fuzz"
    _, warnings, _, dropped, _ = gen._assemble(prd, data, model="m", version="gt-agent/1", source="s")
    assert dropped == baseline[3] + 1 and any(w.startswith("TC #1 bị bỏ") for w in warnings)


@pytest.mark.parametrize("path", ["../etc/passwd", "a/../b", "a//b", "a/", "/abs.py", "a\nb.py", "a b.py"])
def test_assemble_ignores_unsafe_evidence_path(path):
    prd = parse_prd(FIXTURES / "prd" / "noteboard-prd.md", openapi_source=str(FIXTURES / "openapi" / "noteboard.json"))
    response = json.loads((FIXTURES / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(next(block["input"] for block in response["content"] if block["type"] == "tool_use"))
    data["test_cases"][0]["technique"] = "boundary"
    data["test_cases"][0]["evidence"] = [{"path": path, "line": 1}, {"path": ".github/workflows/ci.yml", "line": None}]
    out = gen._assemble(prd, data, model="m", version="gt-agent/1", source="s")[0]
    marked = next(x for x in out["test_cases"] if x.get("technique") == "boundary")
    assert marked["evidence"] == [{"path": ".github/workflows/ci.yml"}]
    assert gt_schema.validate_catalog(out) == []


# ---------------- merge ----------------

def test_merge_without_new_fields_adds_no_keys():
    old = catalog(tc(status="approved"))
    out = merge(old, catalog()).catalog
    assert not {"coverage_plan", "waivers", "spec_conflicts"} & set(out)


def test_merge_carries_plan_prunes_gone_tc_and_lost_ac():
    old = catalog(tc(status="approved"))
    plan = [{**PLAN[0], "tc_ids": ["TC-AC-1.1-aaaaaa", "TC-AC-1.1-gone00"]}, {**PLAN[0], "ac_id": "AC-9.9", "tc_ids": []}]
    out = merge(old, catalog(tc(), coverage_plan=plan)).catalog
    assert [p["ac_id"] for p in out["coverage_plan"]] == ["AC-1.1"]
    assert out["coverage_plan"][0]["tc_ids"] == ["TC-AC-1.1-aaaaaa"]


def test_merge_keeps_qa_waiver_and_conflict_over_candidate():
    qa_waiver = {**WAIVER, "reason": "QA đã duyệt lý do này"}
    qa_conflict = {**CONFLICT, "status": "resolved"}
    old = catalog(tc(status="approved"), waivers=[qa_waiver], spec_conflicts=[qa_conflict])
    candidate = catalog(tc(), waivers=[{**WAIVER, "status": "draft", "reason": "agent đề xuất"}], spec_conflicts=[CONFLICT])
    out = merge(old, candidate).catalog
    assert out["waivers"] == [qa_waiver]
    assert out["spec_conflicts"] == [qa_conflict]


def test_merge_drops_conflict_of_removed_ac_but_keeps_waiver():
    old = catalog(tc(status="approved"), waivers=[WAIVER], spec_conflicts=[{**CONFLICT, "ac_id": "AC-9.9"}])
    out = merge(old, catalog()).catalog
    assert "spec_conflicts" not in out
    assert out["waivers"] == [WAIVER]
