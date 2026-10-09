"""Cache output của bộ sinh GT MỘT lời gọi (S4-02): `gt generate|regen` gặp lại cùng PRD thì KHÔNG gọi LLM, và render ra đúng các file như cũ.

Chỉ cache bộ sinh một lời gọi. Agent đọc mã nguồn SUT qua nhiều lượt nên đầu vào không chỉ là PRD + OpenAPI: cache theo PRD sẽ trả kết quả cũ khi code đổi. Agent không tra, không ghi.

Lưu OUTPUT CỦA TOOL đã validate (`emit_test_cases`, trước `_assemble`/merge/render), không lưu catalog: catalog có `prd.source` (đường dẫn PRD) mà khoá không chứa,
và `_assemble` tất định nên chạy lại trên entry cho ra cùng warnings/orphans/dropped. Entry chỉ được ghi sau khi catalog dựng từ nó đã qua `validate_catalog`.
Không ghi khi Gemini rơi sang model dự phòng (`fallback_from`): kết quả là của model khác với `model` trong khoá.

Khoá = sha256(JSON[generator, prd.sha256, openapi_sha, model, prompt_version, auth_sha]):
  - `openapi_sha` băm `prd.endpoints` (đúng thứ đi vào prompt và vào kiểm endpoint của từng TC).
  - `auth_sha` băm khối `<auth>` (nằm trong prompt nên đổi `auth.yaml` phải bỏ cache).
  - Provider (Claude/Gemini) đã nằm trong `model` (chọn theo tiền tố). Schema của tool không nằm trong khoá: entry được validate lại theo schema HIỆN TẠI khi đọc.

Rủi ro: entry là dữ liệu không tin cậy (cache của Actions tách theo ref). Sản phẩm vẫn đi qua PR và QA duyệt; entry hỏng hoặc sai schema chỉ là miss.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from jsonschema import Draft202012Validator

from qc_agent.groundtruth.prd import ParsedPRD
from qc_agent.llm import filecache
from qc_agent.llm.client import Usage

ENTRY_VERSION = 1
GENERATOR = "single"
_USAGE = {"type": "object", "additionalProperties": False, "required": list(asdict(Usage())),
          "properties": {name: {"type": "integer", "minimum": 0} for name in asdict(Usage())}}


def make_key(*, prd: ParsedPRD, model: str, prompt_version: str, auth: str | None, generator: str = GENERATOR) -> str:
    return filecache.make_key("gt/1", generator, prd.sha256, filecache.digest(list(prd.endpoints)), model, prompt_version, filecache.digest(auth))


def lookup(directory: Path, key: str, tool_schema: dict) -> dict | None:
    """`{"data", "usage", "attempts"}` đã validate theo schema tool hiện tại, hoặc None (miss)."""
    entry = filecache.read(directory, key)
    if entry is None:
        return None
    shape = {"type": "object", "additionalProperties": False, "required": ["version", "data", "usage", "attempts"],
             "properties": {"version": {"const": ENTRY_VERSION}, "data": {"type": "object"}, "usage": _USAGE, "attempts": {"type": "integer", "minimum": 1, "maximum": 2}}}
    if not Draft202012Validator(shape).is_valid(entry) or not Draft202012Validator(tool_schema).is_valid(entry["data"]):
        return None
    return {"data": entry["data"], "usage": Usage(**entry["usage"]), "attempts": entry["attempts"]}


def store(directory: Path, key: str, data: dict, usage: Usage, attempts: int) -> bool:
    return filecache.write(directory, key, {"version": ENTRY_VERSION, "data": data, "usage": asdict(usage), "attempts": attempts})
