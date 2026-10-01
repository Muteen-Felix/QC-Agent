"""schemas/ground_truth.json + module_map.json + groundtruth/schema.py (S1-02).

Catalog = thứ QA đọc/sửa; emit = thứ LLM phát ra (strict-tương thích). Hai dạng phải cùng hình dạng, chỉ khác cách biểu diễn map và json body.
"""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from qc_agent.groundtruth import schema
from qc_agent.llm.client import wire_schema

ROOT = Path(__file__).resolve().parent.parent
MARK = "MARK-8d41f0"   # giá trị vi phạm chứa chuỗi này: không được xuất hiện trong thông điệp lỗi


def sample_catalog() -> dict:
    create = {"request": {"method": "POST", "path": "/notes", "json": {"title": "a", "body": "b"}},
              "expect": {"status": [201], "json": [{"path": "$.title", "op": "eq", "value": "a"}, {"path": "$.id", "op": "type", "value": "integer"}]}}
    get = {"request": {"method": "GET", "path": "/notes/{note_id}", "path_params": {"note_id": "{{note_id}}"}},
           "expect": {"status": [200], "json": [{"path": "$.title", "op": "eq", "value": "a"}, {"path": "$.body", "op": "contains", "value": "b"},
                                                {"path": "$.missing", "op": "absent"}, {"path": "$.id", "op": "exists"}]}}
    return {
        "version": 1,
        "prd": {"id": "noteboard", "sha256": "0" * 64, "source": "docs/prd/noteboard.md"},
        "generated_by": {"model": "claude-sonnet-5", "prompt_version": "gt-generate/1"},
        "status": "draft",
        "stories": [{"story_id": "US-1", "title": "Tạo ghi chú", "acs": [{"ac_id": "AC-1.1", "text": "POST /notes trả 201"}, {"ac_id": "AC-1.2", "text": "lưu nguyên văn"}]},
                    {"story_id": "US-2", "title": "Xem ghi chú", "acs": [{"ac_id": "AC-2.1", "text": "GET /notes trả mảng"}]}],
        "test_cases": [
            {"tc_id": "TC-AC-1.1-3f2a1c", "title": "Tạo ghi chú hợp lệ", "ac_refs": ["AC-1.1"], "kind": "api_functional", "status": "draft", "origin": "llm",
             "rejected_reason": None, "notes": None, "steps": [copy.deepcopy(create)]},
            {"tc_id": "TC-AC-1.2-9b8c7d", "title": "Tạo rồi đọc lại", "ac_refs": ["AC-1.2", "AC-1.1"], "kind": "flow", "status": "draft", "origin": "llm",
             "steps": [{**copy.deepcopy(create), "capture": {"note_id": "$.id"}}, copy.deepcopy(get)]},
            {"tc_id": "TC-AC-2.1-aaaaaa", "title": "Danh sách trả mảng", "ac_refs": ["AC-2.1"], "kind": "api_contract", "status": "approved", "origin": "qa",
             "notes": "QA thêm", "steps": [{"request": {"method": "GET", "path": "/notes"}, "expect": {"status": [200]}}]},
            {"tc_id": "TC-AC-2.1-bbbbbb", "title": "Đã loại", "ac_refs": ["AC-2.1"], "kind": "api_functional", "status": "rejected", "origin": "llm",
             "rejected_reason": "trùng với TC khác", "steps": [{"request": {"method": "GET", "path": "/notes"}, "expect": {"status": [200]}}]},
        ],
        "uncovered_acs": [{"ac_id": "AC-3.5", "reason": "chỉ kiểm được trên UI"}],
    }


def sample_module_map() -> dict:
    return {"version": 1, "status": "draft",
            "modules": [{"name": "notes", "paths": ["toyapp/app.py", "toyapp/**/*.py"], "suites": ["api-contract", "gt-functional"], "source": "openapi"}]}


def changed(mutate, base=None) -> dict:
    data = copy.deepcopy(sample_catalog() if base is None else base)
    mutate(data)
    return data


def tc(data, index=0) -> dict:
    return data["test_cases"][index]


# ---------------- hai file schema ----------------

@pytest.mark.parametrize("name", ["ground_truth.json", "module_map.json"])
def test_schemas_are_valid_draft_2020_12_and_say_they_are_not_contract(name):
    doc = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(doc)
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema" and "CONTRACT.lock" in doc["$comment"]
    lock = json.loads((ROOT / "schemas" / "CONTRACT.lock").read_text(encoding="utf-8"))
    assert f"schemas/{name}" not in lock["files"]   # schema dữ liệu: đổi không qua quy trình SemVer của contract


def _all_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _all_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _all_keys(value)


def _object_nodes(node, path="#"):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield path, node
        for key, value in node.items():
            yield from _object_nodes(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _object_nodes(value, f"{path}/{index}")


@pytest.mark.parametrize("name", ["ground_truth.json", "module_map.json"])
def test_every_object_is_closed_or_a_declared_map(name):
    doc = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    for path, node in _object_nodes(doc):
        is_map = "propertyNames" in node and isinstance(node.get("additionalProperties"), dict) and "properties" not in node
        assert node.get("additionalProperties") is False or is_map, f"{path} là object mở không khai báo"


def test_loaders_return_the_shipped_files():
    assert schema.catalog_schema() == json.loads((ROOT / "schemas" / "ground_truth.json").read_text(encoding="utf-8"))
    assert schema.module_map_schema() == json.loads((ROOT / "schemas" / "module_map.json").read_text(encoding="utf-8"))


# ---------------- catalog hợp lệ ----------------

def test_sample_catalog_and_module_map_are_valid():
    assert schema.validate_catalog(sample_catalog()) == []
    assert schema.validate_module_map(sample_module_map()) == []


def test_optional_keys_may_be_omitted_and_json_body_is_free_form():
    def edit(data):
        step = tc(data)["steps"][0]
        step["request"]["json"] = {"nested": {"list": [1, "x", None, True, {"k": 1.5}]}}
    assert "notes" not in tc(sample_catalog(), 1) and "rejected_reason" not in tc(sample_catalog(), 1)   # khoá tuỳ chọn thật sự vắng
    assert schema.validate_catalog(changed(edit)) == []
    for body in (None, [], "text", 7):
        data = changed(lambda d, b=body: tc(d)["steps"][0]["request"].__setitem__("json", b))
        assert schema.validate_catalog(data) == []


def test_flow_may_have_more_than_two_steps_and_qa_test_cases_are_allowed():
    def edit(data):
        flow = tc(data, 1)
        flow["steps"].append(copy.deepcopy(flow["steps"][1]))
        flow["origin"], flow["tc_id"] = "qa", "TC-EDGE-unicode-1"
    assert schema.validate_catalog(changed(edit)) == []


def test_approved_catalog_needs_no_draft_test_cases():
    assert schema.validate_catalog(changed(lambda d: d.__setitem__("status", "approved"))) != []   # còn TC draft
    def approve_all(data):
        data["status"] = "approved"
        for case in data["test_cases"]:
            if case["status"] == "draft":
                case["status"] = "approved"
    assert schema.validate_catalog(changed(approve_all)) == []


# ---------------- bắt từng lỗi cấu trúc ----------------

BAD = {
    "rejected thiếu rejected_reason": lambda d: tc(d, 3).pop("rejected_reason"),
    "rejected_reason rỗng": lambda d: tc(d, 3).__setitem__("rejected_reason", ""),
    "rejected_reason null": lambda d: tc(d, 3).__setitem__("rejected_reason", None),
    "op lạ": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("op", "matches"),
    "op regex": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("op", "regex"),
    "path JSONPath có ..": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("path", "$..title"),
    "path JSONPath có [*]": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("path", "$.a[*]"),
    "path không bắt đầu bằng $": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("path", "title"),
    "path chỉ số âm": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("path", "$[-1]"),
    "path có biểu thức lọc": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("path", "$.a[?(@.b==1)]"),
    "path có khoảng trắng": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("path", "$.a b"),
    "flow chỉ 1 bước": lambda d: tc(d, 1).__setitem__("steps", tc(d, 1)["steps"][:1]),
    "api_functional 2 bước": lambda d: tc(d, 0)["steps"].append(copy.deepcopy(tc(d, 0)["steps"][0])),
    "api_functional 0 bước": lambda d: tc(d, 0).__setitem__("steps", []),
    "api_contract có json body": lambda d: tc(d, 2)["steps"][0]["request"].__setitem__("json", {"a": 1}),
    "api_contract có query": lambda d: tc(d, 2)["steps"][0]["request"].__setitem__("query", {"q": "x"}),
    "api_contract có headers": lambda d: tc(d, 2)["steps"][0]["request"].__setitem__("headers", {"X-A": "b"}),
    "api_contract có assertion json": lambda d: tc(d, 2)["steps"][0]["expect"].__setitem__("json", [{"path": "$", "op": "exists"}]),
    "api_contract có capture": lambda d: tc(d, 2)["steps"][0].__setitem__("capture", {"x": "$.id"}),
    "eq thiếu value": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].pop("value"),
    "value là object": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("value", {"a": 1}),
    "value là mảng": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("value", [1]),
    "exists có value": lambda d: tc(d, 1)["steps"][1]["expect"]["json"][3].__setitem__("value", 1),
    "absent có value": lambda d: tc(d, 1)["steps"][1]["expect"]["json"][2].__setitem__("value", 1),
    "type sai tên": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][1].__setitem__("value", "str"),
    "type value không phải chuỗi": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][1].__setitem__("value", 5),
    "len_eq âm": lambda d: tc(d, 0)["steps"][0]["expect"]["json"].append({"path": "$", "op": "len_eq", "value": -1}),
    "len_gte là chuỗi": lambda d: tc(d, 0)["steps"][0]["expect"]["json"].append({"path": "$", "op": "len_gte", "value": "3"}),
    "len_eq là số thực": lambda d: tc(d, 0)["steps"][0]["expect"]["json"].append({"path": "$", "op": "len_eq", "value": 1.5}),
    "assertion thừa khoá": lambda d: tc(d, 0)["steps"][0]["expect"]["json"][0].__setitem__("code", "x == 1"),
    "status 99": lambda d: tc(d, 0)["steps"][0]["expect"].__setitem__("status", [99]),
    "status 600": lambda d: tc(d, 0)["steps"][0]["expect"].__setitem__("status", [600]),
    "status rỗng": lambda d: tc(d, 0)["steps"][0]["expect"].__setitem__("status", []),
    "status là chuỗi": lambda d: tc(d, 0)["steps"][0]["expect"].__setitem__("status", ["201"]),
    "method TRACE": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("method", "TRACE"),
    "method chữ thường": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("method", "post"),
    "path thiếu /": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("path", "notes"),
    "path có query": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("path", "/notes?x=1"),
    "path có khoảng trắng URL": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("path", "/no tes"),
    "header tên có khoảng trắng": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("headers", {"X A": "b"}),
    "header value không phải chuỗi": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("headers", {"X-A": 1}),
    "query value là object": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("query", {"q": {"a": 1}}),
    "path_params tên sai": lambda d: tc(d, 1)["steps"][1]["request"].__setitem__("path_params", {"1x": "a"}),
    "capture tên chữ hoa": lambda d: tc(d, 1)["steps"][0].__setitem__("capture", {"NoteId": "$.id"}),
    "capture path sai": lambda d: tc(d, 1)["steps"][0].__setitem__("capture", {"note_id": "$..id"}),
    "tc_id thiếu tiền tố": lambda d: tc(d, 0).__setitem__("tc_id", "AC-1.1-3f2a1c"),
    "ac_refs rỗng": lambda d: tc(d, 0).__setitem__("ac_refs", []),
    "ac_refs trùng": lambda d: tc(d, 0).__setitem__("ac_refs", ["AC-1.1", "AC-1.1"]),
    "kind lạ": lambda d: tc(d, 0).__setitem__("kind", "ui"),
    "status TC lạ": lambda d: tc(d, 0).__setitem__("status", "pending"),
    "origin lạ": lambda d: tc(d, 0).__setitem__("origin", "human"),
    "thiếu steps": lambda d: tc(d, 0).pop("steps"),
    "TC thừa khoá": lambda d: tc(d, 0).__setitem__("code", "print(1)"),
    "step thừa khoá": lambda d: tc(d, 0)["steps"][0].__setitem__("script", "x"),
    "request thừa khoá": lambda d: tc(d, 0)["steps"][0]["request"].__setitem__("timeout", 5),
    "expect thừa khoá": lambda d: tc(d, 0)["steps"][0]["expect"].__setitem__("eval", "1"),
    "gốc thừa khoá": lambda d: d.__setitem__("extra", 1),
    "version 2": lambda d: d.__setitem__("version", 2),
    "sha256 ngắn": lambda d: d["prd"].__setitem__("sha256", "abc"),
    "sha256 chữ hoa": lambda d: d["prd"].__setitem__("sha256", "A" * 64),
    "prompt_version sai": lambda d: d["generated_by"].__setitem__("prompt_version", "v1"),
    "catalog status lạ": lambda d: d.__setitem__("status", "final"),
    "thiếu uncovered_acs": lambda d: d.pop("uncovered_acs"),
    "uncovered thiếu reason": lambda d: d["uncovered_acs"][0].pop("reason"),
    "AC id sai": lambda d: d["stories"][0]["acs"][0].__setitem__("ac_id", "AC 1.1"),
    "story thiếu acs": lambda d: d["stories"][0].pop("acs"),
}


@pytest.mark.parametrize("name", list(BAD))
def test_catalog_rejects_each_structural_error(name):
    found = schema.validate_catalog(changed(BAD[name]))
    assert found, f"schema đã nhận nhầm: {name}"


CLOSED_OPS = {
    "eq": [{"value": "a"}, {"value": 1}, {"value": None}, {"value": True}], "ne": [{"value": "a"}], "contains": [{"value": "a"}],
    "exists": [{}], "absent": [{}],
    "type": [{"value": t} for t in ("string", "number", "integer", "boolean", "null", "object", "array")],
    "len_eq": [{"value": 0}, {"value": 3}], "len_gte": [{"value": 1}],
}


def _with_assertion(assertion) -> dict:
    return changed(lambda d: tc(d)["steps"][0]["expect"]["json"].append(assertion))


@pytest.mark.parametrize("op,extra", [(op, extra) for op, variants in CLOSED_OPS.items() for extra in variants])
def test_every_closed_op_is_accepted_in_its_own_shape(op, extra):
    assert schema.validate_catalog(_with_assertion({"path": "$.a[0].b-c", "op": op, **extra})) == []


@pytest.mark.parametrize("op", ["regex", "matches", "gt", "lt", "gte", "in", "startswith", "eval", "EQ", "", "len"])
@pytest.mark.parametrize("extra", [{}, {"value": "a"}, {"value": 1}])
def test_any_op_outside_the_closed_set_is_rejected_with_or_without_a_value(op, extra):
    assert schema.validate_catalog(_with_assertion({"path": "$.a", "op": op, **extra}))


def test_ops_that_take_no_value_reject_one_and_ops_that_need_one_reject_its_absence():
    for op in ("exists", "absent"):
        assert schema.validate_catalog(_with_assertion({"path": "$", "op": op, "value": None}))
    for op in ("eq", "ne", "contains", "type", "len_eq", "len_gte"):
        assert schema.validate_catalog(_with_assertion({"path": "$", "op": op}))


def test_error_messages_point_at_the_location_and_keyword():
    found = schema.validate_catalog(changed(BAD["rejected thiếu rejected_reason"]))
    assert any(e.startswith("test_cases/3") for e in found)
    found = schema.validate_catalog(changed(BAD["op lạ"]))
    assert any("test_cases/0/steps/0/expect/json/0" in e for e in found)
    assert any("<gốc>" in e for e in schema.validate_catalog([]))


@pytest.mark.parametrize("bad_value", [MARK, {"k": MARK}, [MARK], 5])
def test_error_messages_never_echo_the_offending_value(bad_value):
    def edit(data):
        assertion = tc(data)["steps"][0]["expect"]["json"][0]
        assertion["op"], assertion["value"] = bad_value if isinstance(bad_value, str) else "eq", bad_value
        tc(data)["title"] = MARK * 40                 # title quá dài (vi phạm maxLength)
        tc(data)["steps"][0]["request"]["headers"] = {f"Bad {MARK}": MARK}
    found = schema.validate_catalog(changed(edit))
    assert found and all(MARK not in e for e in found)


def test_error_list_is_capped():
    data = changed(lambda d: d.__setitem__("stories", [{"story_id": "bad id"}] * 80))
    found = schema.validate_catalog(data)
    assert len(found) == schema.MAX_ERRORS + 1 and found[-1].startswith("… và ")


# ---------------- emit (dạng LLM phát ra) ----------------

def sample_emit() -> dict:
    return {
        "test_cases": [{
            "title": "Tạo rồi đọc lại", "ac_refs": ["AC-1.1", "AC-1.2"], "kind": "flow",
            "steps": [
                {"request": {"method": "POST", "path": "/notes", "path_params": [], "query": [], "headers": [{"name": "X-Trace", "value": "t"}],
                             "json": "{\"title\": \"a\", \"body\": \"b\"}"},
                 "expect": {"status": [201], "json": [{"path": "$.id", "op": "type", "value": "integer"}]},
                 "capture": [{"name": "note_id", "path": "$.id"}]},
                {"request": {"method": "GET", "path": "/notes/{note_id}", "path_params": [{"name": "note_id", "value": "{{note_id}}"}],
                             "query": [{"name": "verbose", "value": True}], "headers": [], "json": None},
                 "expect": {"status": [200], "json": [{"path": "$.title", "op": "eq", "value": "a"}, {"path": "$.x", "op": "absent"}]},
                 "capture": []},
            ],
        }],
        "uncovered_acs": [{"ac_id": "AC-3.5", "reason": "chỉ kiểm được trên UI"}],
    }


def emit_errors(data) -> list[str]:
    return schema.errors(data, schema.emit_schema())


def test_emit_schema_is_self_contained_and_valid():
    emit = schema.emit_schema()
    assert not [k for k in _all_keys(emit) if k in ("$ref", "$defs", "$comment")]   # khoá thật; chữ "$ref" trong description thì không tính
    jsonschema.Draft202012Validator.check_schema(emit)
    assert schema.emit_schema() == emit   # gọi lại không đổi, không sửa dữ liệu nạp


def test_emit_schema_survives_wire_schema_and_is_strict_compatible():
    wire = wire_schema(schema.emit_schema())   # ValueError nếu còn object mở/tự do
    jsonschema.Draft202012Validator.check_schema(wire)
    text = json.dumps(wire)
    for keyword in ("minLength", "maxLength", "minimum", "maximum", "maxItems", "uniqueItems", "$ref", "\"if\"", "\"then\"", "patternProperties"):
        assert keyword not in text, keyword
    for path, node in _object_nodes(wire):
        assert node["additionalProperties"] is False, path
    assert "propertyNames" not in text   # không còn map tự do


def test_catalog_schema_itself_is_not_strict_compatible_which_is_why_emit_exists():
    with pytest.raises(ValueError):
        wire_schema(schema.catalog_schema())


def test_sample_emit_is_valid_and_lifts_the_same_ops_as_the_catalog():
    assert emit_errors(sample_emit()) == []


@pytest.mark.parametrize("name,mutate", [
    ("thừa status", lambda d: d["test_cases"][0].__setitem__("status", "approved")),
    ("thừa tc_id", lambda d: d["test_cases"][0].__setitem__("tc_id", "TC-X-1")),
    ("thiếu capture", lambda d: d["test_cases"][0]["steps"][0].pop("capture")),
    ("query là map", lambda d: d["test_cases"][0]["steps"][0]["request"].__setitem__("query", {"a": 1})),
    ("json là object", lambda d: d["test_cases"][0]["steps"][0]["request"].__setitem__("json", {"a": 1})),
    ("headers là map", lambda d: d["test_cases"][0]["steps"][0]["request"].__setitem__("headers", {"X-A": "b"})),
    ("op lạ", lambda d: d["test_cases"][0]["steps"][1]["expect"]["json"][0].__setitem__("op", "regex")),
    ("kind lạ", lambda d: d["test_cases"][0].__setitem__("kind", "ui")),
    ("thiếu uncovered_acs", lambda d: d.pop("uncovered_acs")),
    ("pair thiếu value", lambda d: d["test_cases"][0]["steps"][0]["request"]["headers"][0].pop("value")),
    ("path có query", lambda d: d["test_cases"][0]["steps"][0]["request"].__setitem__("path", "/notes?x=1")),
])
def test_emit_schema_rejects(name, mutate):
    data = copy.deepcopy(sample_emit())
    mutate(data)
    assert emit_errors(data), name


def test_emit_and_catalog_shapes_stay_in_sync():
    defs = schema.catalog_schema()["$defs"]
    system_owned = {"tc_id", "status", "origin", "rejected_reason", "notes"}
    agent_only = {"priority", "technique", "preconditions", "rationale", "evidence"}   # chỉ bộ sinh dạng agent điền (tool riêng); single-shot không có
    assert set(defs["emitTestCase"]["properties"]) == set(defs["testCase"]["properties"]) - system_owned - agent_only
    assert set(defs["emitTestCase"]["required"]) == set(defs["testCase"]["required"]) - system_owned
    assert set(defs["emitStep"]["properties"]) == set(defs["step"]["properties"])
    assert set(defs["emitRequest"]["properties"]) == set(defs["request"]["properties"])
    assert set(defs["emitExpect"]["properties"]) == set(defs["expect"]["properties"])
    assert defs["emitTestCase"]["properties"]["kind"] == defs["testCase"]["properties"]["kind"]
    assert defs["emitRequest"]["properties"]["method"] == defs["request"]["properties"]["method"]
    assert defs["emit_test_cases"]["properties"]["uncovered_acs"] == schema.catalog_schema()["properties"]["uncovered_acs"]


def test_inline_rejects_cycles_and_dangling_refs():
    with pytest.raises(ValueError, match="đệ quy"):
        schema._inline({"$ref": "#/$defs/a"}, {"a": {"type": "array", "items": {"$ref": "#/$defs/a"}}}, ())
    with pytest.raises(ValueError, match="giải được"):
        schema._inline({"$ref": "#/$defs/missing"}, {}, ())
    with pytest.raises(ValueError, match="giải được"):
        schema._inline({"$ref": "https://example.test/x.json"}, {}, ())


def test_inline_keeps_sibling_keywords_next_to_a_ref():
    inlined = schema._inline({"$ref": "#/$defs/s", "description": "mô tả riêng"}, {"s": {"type": "string", "description": "gốc"}}, ())
    assert inlined == {"type": "string", "description": "mô tả riêng"}


# ---------------- module map ----------------

@pytest.mark.parametrize("name,mutate", [
    ("thừa khoá", lambda d: d.__setitem__("owner", "qa")),
    ("status lạ", lambda d: d.__setitem__("status", "final")),
    ("thiếu modules", lambda d: d.pop("modules")),
    ("module thừa khoá", lambda d: d["modules"][0].__setitem__("tags", [])),
    ("source lạ", lambda d: d["modules"][0].__setitem__("source", "llm")),
    ("paths rỗng", lambda d: d["modules"][0].__setitem__("paths", [])),
    ("path tuyệt đối", lambda d: d["modules"][0].__setitem__("paths", ["/etc/passwd"])),
    ("path có ..", lambda d: d["modules"][0].__setitem__("paths", ["../secret.py"])),
    ("path có .. giữa", lambda d: d["modules"][0].__setitem__("paths", ["a/../b.py"])),
    ("path có khoảng trắng", lambda d: d["modules"][0].__setitem__("paths", ["a b.py"])),
    ("suites rỗng", lambda d: d["modules"][0].__setitem__("suites", [])),
    ("suites trùng", lambda d: d["modules"][0].__setitem__("suites", ["sast", "sast"])),
    ("suite tên sai", lambda d: d["modules"][0].__setitem__("suites", ["API Contract"])),
    ("tên module sai", lambda d: d["modules"][0].__setitem__("name", "Notes!")),
    ("version 2", lambda d: d.__setitem__("version", 2)),
])
def test_module_map_rejects(name, mutate):
    data = copy.deepcopy(sample_module_map())
    mutate(data)
    assert schema.validate_module_map(data), name


def test_module_map_accepts_dotdot_inside_a_name_and_double_star():
    data = sample_module_map()
    data["modules"][0]["paths"] = ["src/**", "a..b.py", "**/*.py", ".hidden.py"]
    data["status"] = "approved"
    assert schema.validate_module_map(data) == []
