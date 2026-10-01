"""Nạp và kiểm schema Ground-Truth (`schemas/ground_truth.json`, `schemas/module_map.json`): dùng chung cho generate (S1-04), render (S1-05) và CLI (S1-06).

  - `catalog_schema()`     catalog `test-cases.yaml` mà QA đọc/sửa (map tự do, if/then cho luật `rejected` và `kind`).
  - `emit_schema()`        input của tool `emit_test_cases`: `$defs/emit_test_cases` đã inline mọi `$ref`, strict-tương thích (xem client.wire_schema).
  - `validate_catalog / validate_module_map`  trả danh sách lỗi dạng "đường/dẫn (từ-khoá)"; KHÔNG bao giờ kèm giá trị vi phạm
    (giá trị có thể là nội dung PRD/LLM, không được lọt vào log hay thông điệp lỗi).
Schema là dữ liệu, không thuộc contract đóng băng (không nằm trong CONTRACT.lock).
"""
from __future__ import annotations

import copy
import json

import jsonschema
from jsonschema import exceptions as jse

from qc_agent import settings

CATALOG = "ground_truth.json"
MODULE_MAP = "module_map.json"
MAX_ERRORS = 50
_DEFS = "#/$defs/"


def _load(name: str) -> dict:
    return json.loads((settings.get().resolved_schemas_dir / name).read_text(encoding="utf-8"))


def catalog_schema() -> dict:
    return _load(CATALOG)


def module_map_schema() -> dict:
    return _load(MODULE_MAP)


def _inline(node, defs: dict, stack: tuple[str, ...]):
    if isinstance(node, list):
        return [_inline(item, defs, stack) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if not (isinstance(ref, str) and ref.startswith(_DEFS) and ref[len(_DEFS):] in defs):
            raise ValueError(f"$ref không giải được: {ref!r}")
        name = ref[len(_DEFS):]
        if name in stack:
            raise ValueError(f"schema đệ quy ({' -> '.join((*stack, name))}): strict mode không hỗ trợ")
        target = _inline(copy.deepcopy(defs[name]), defs, (*stack, name))
        siblings = {key: _inline(value, defs, stack) for key, value in node.items() if key != "$ref"}
        return {**target, **siblings}   # khoá đứng cạnh $ref (vd description) ghi đè bản trong $defs
    return {key: _inline(value, defs, stack) for key, value in node.items()}


def emit_schema() -> dict:
    """Input schema của tool `emit_test_cases`, tự đủ (không còn `$defs`/`$ref`) để gửi qua `client.wire_schema` và validate bằng jsonschema."""
    defs = catalog_schema()["$defs"]
    return _inline(defs["emit_test_cases"], defs, ("emit_test_cases",))


AGENT_TOOLS = ("submit_test_cases", "record_coverage_plan", "report_spec_conflict", "finish_generation")


def tool_input_schema(name: str) -> dict:
    """Input schema của một tool của agent (`AGENT_TOOLS`), đã inline mọi `$ref`: strict-tương thích, và là schema ĐẦY ĐỦ để `agent_loop` validate lại."""
    if name not in AGENT_TOOLS:
        raise ValueError(f"không có tool {name!r}; có: {', '.join(AGENT_TOOLS)}")
    defs = catalog_schema()["$defs"]
    return _inline(defs[name], defs, (name,))


def _path(error: jse.ValidationError) -> str:
    return "/".join(str(part) for part in error.absolute_path) or "<gốc>"


def _describe(error: jse.ValidationError) -> str:
    if error.context:  # anyOf/oneOf: chỉ ra nhánh gần đúng nhất thay vì chỉ "anyOf"
        best = jse.best_match(error.context)
        if best is not None:
            return f"{_path(best)} ({error.validator}: {best.validator})"
    return f"{_path(error)} ({error.validator})"


def errors(instance, schema: dict) -> list[str]:
    """Mọi lỗi của `instance` theo `schema`, đã khử trùng và xếp; chỉ có vị trí và từ khoá, không có giá trị."""
    validator = jsonschema.Draft202012Validator(schema)
    found = sorted({_describe(error) for error in validator.iter_errors(instance)})
    if len(found) > MAX_ERRORS:
        found = [*found[:MAX_ERRORS], f"… và {len(found) - MAX_ERRORS} lỗi nữa"]
    return found


def validate_catalog(data) -> list[str]:
    return errors(data, catalog_schema())


def validate_module_map(data) -> list[str]:
    return errors(data, module_map_schema())
