"""Phân loại SemVer của thay đổi contract: 4 cặp MINOR, 8 cặp MAJOR, cộng biên (mặc định MAJOR khi không chắc)."""
import copy
import sys

import pytest

sys.path.insert(0, "tools")
import contract_diff as cd  # noqa: E402

BASE = {
    "type": "object", "required": ["a"], "additionalProperties": False,
    "properties": {
        "a": {"type": "string", "description": "cũ"},
        "o": {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0}}},
        "e": {"enum": ["p", "q"]},
    },
    "$defs": {"d1": {"type": "string"}},
}


def mut(fn):
    o = copy.deepcopy(BASE)
    fn(o)
    return o


MINOR_CASES = {
    "M1_add_optional_top_property": lambda o: o["properties"].update(n={"type": "string"}),
    "M2_add_nested_property": lambda o: o["properties"]["o"]["properties"].update(y={"type": "number"}),
    "M3_add_def": lambda o: o["$defs"].update(d2={"type": "integer"}),
    "M4_add_property_with_description": lambda o: o["properties"].update(n={"type": "string", "description": "mới"}),
}
MAJOR_CASES = {
    "J1_remove_property": lambda o: o["properties"].pop("e"),
    "J2_add_required": lambda o: o["required"].append("o"),
    "J3_enum_add_value": lambda o: o["properties"]["e"].update(enum=["p", "q", "r"]),
    "J4_type_change": lambda o: o["properties"]["a"].update(type="integer"),
    "J5_tighten_minimum": lambda o: o["properties"]["o"]["properties"]["x"].update(minimum=1),
    "J6_remove_required": lambda o: o.update(required=[]),
    "J7_additional_properties_flip": lambda o: o.update(additionalProperties=True),
    "J8_remove_def": lambda o: o["$defs"].pop("d1"),
}


@pytest.mark.parametrize("name", MINOR_CASES)
def test_minor(name):
    level, _ = cd.classify_schema(BASE, mut(MINOR_CASES[name]))
    assert level == cd.MINOR


@pytest.mark.parametrize("name", MAJOR_CASES)
def test_major(name):
    level, reasons = cd.classify_schema(BASE, mut(MAJOR_CASES[name]))
    assert level == cd.MAJOR and reasons


def test_description_only_is_none():
    level, _ = cd.classify_schema(BASE, mut(lambda o: o["properties"]["a"].update(description="mới")))
    assert level == cd.NONE


def test_minor_plus_major_is_major():
    def both(o):
        o["properties"].update(n={"type": "string"})
        o["properties"].pop("e")
    assert cd.classify_schema(BASE, mut(both))[0] == cd.MAJOR


def test_unknown_keyword_change_defaults_to_major():
    assert cd.classify_schema(BASE, mut(lambda o: o.update(foo=1)))[0] == cd.MAJOR


def test_yaml_added_key_minor_removed_or_changed_major_comment_none():
    old = "# c\nname: X\nlanes: [gate]\n"
    assert cd.classify_yaml(old, old + "modes: [pr]\n")[0] == cd.MINOR
    assert cd.classify_yaml(old, "name: X\n")[0] == cd.MAJOR
    assert cd.classify_yaml(old, "name: X\nlanes: [discovery]\n")[0] == cd.MAJOR
    assert cd.classify_yaml(old, "# khác\nname: X\nlanes: [gate]\n")[0] == cd.NONE


def test_file_added_minor_deleted_major_unparseable_major():
    assert cd.classify_file("schemas/x.json", None, "{}")[0] == cd.MINOR
    assert cd.classify_file("schemas/x.json", "{}", None)[0] == cd.MAJOR
    assert cd.classify_file("schemas/x.json", "{}", "{oops")[0] == cd.MAJOR
