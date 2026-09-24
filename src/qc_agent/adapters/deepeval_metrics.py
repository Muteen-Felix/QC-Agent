"""Metric tất định cấu hình được cho worker deepeval: `inputs.metrics` là danh sách khai báo, không còn 3 metric cố định của PoC.

    metrics:
      - {name: summary_not_empty, kind: not_empty, field: actual_output}
      - {name: summary_shorter_than_body, kind: shorter_than, field: actual_output, than: input}
      - {name: summary_json_valid, kind: json_schema, schema: {...}}      # kiểm tra cả record
      - {name: no_todo, kind: regex_absent, field: actual_output, pattern: "TODO"}
      - {name: short_enough, kind: max_len, field: actual_output, max: 280}

`name` (chữ thường, số, gạch dưới) trở thành tên check cho oracle và tên test `test_metric[<name>-<case>]`.
"""
from __future__ import annotations

import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

NAME = re.compile(r"[a-z][a-z0-9_]*")
KINDS = {
    "not_empty": {"field"},
    "shorter_than": {"field", "than"},
    "max_len": {"field", "max"},
    "regex_present": {"field", "pattern"},
    "regex_absent": {"field", "pattern"},
    "json_schema": {"schema"},
}


def validate(metrics) -> list[str]:
    if not isinstance(metrics, list) or not metrics:
        return ["inputs.metrics phải là danh sách không rỗng"]
    errors: list[str] = []
    names: list[str] = []
    for index, metric in enumerate(metrics):
        where = f"inputs.metrics[{index}]"
        if not isinstance(metric, dict):
            errors.append(f"{where} phải là object")
            continue
        name, kind = metric.get("name"), metric.get("kind")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            errors.append(f"{where}.name phải khớp [a-z][a-z0-9_]*")
        else:
            names.append(name)
        if kind not in KINDS:
            errors.append(f"{where}.kind phải thuộc {sorted(KINDS)}")
            continue
        wanted = KINDS[kind]
        if set(metric) != wanted | {"name", "kind"}:
            errors.append(f"{where} (kind={kind}) phải có đúng các khoá name, kind, {', '.join(sorted(wanted))}")
            continue
        if kind == "max_len" and (isinstance(metric["max"], bool) or not isinstance(metric["max"], int) or metric["max"] < 0):
            errors.append(f"{where}.max phải là số nguyên >= 0")
        if kind.startswith("regex"):
            try:
                re.compile(metric["pattern"])
            except (re.error, TypeError):
                errors.append(f"{where}.pattern không phải regex hợp lệ")
        if kind == "json_schema":
            try:
                Draft202012Validator.check_schema(metric["schema"])
            except SchemaError:
                errors.append(f"{where}.schema không phải JSON Schema hợp lệ")
    if len(set(names)) != len(names):
        errors.append("inputs.metrics có name trùng")
    return errors


def passes(metric: dict, record: dict) -> bool:
    kind = metric["kind"]
    if kind == "json_schema":
        return not list(Draft202012Validator(metric["schema"]).iter_errors(record))
    value = record.get(metric["field"])
    if not isinstance(value, str):
        return False
    if kind == "not_empty":
        return bool(value.strip())
    if kind == "shorter_than":
        other = record.get(metric["than"])
        return isinstance(other, str) and len(value) < len(other)
    if kind == "max_len":
        return len(value) <= metric["max"]
    found = re.search(metric["pattern"], value) is not None
    return found if kind == "regex_present" else not found
