"""Cache kết quả LLM của Diff Agent (S4-02): chạy lại cùng một thay đổi thì KHÔNG gọi LLM.

Chỉ lưu phần của LLM: `selections` đã qua enum allowlist + `usage` lúc tạo. Floor, rules và FULL SET KHÔNG được lưu: chúng luôn được tính lại tất định mỗi lần
chạy (`merge_floor` chạy cả khi hit), nên entry bị sửa tay cũng không bỏ được floor.
Không bao giờ ghi: fallback (mọi `fallback_reason` khác null), rules `full_set`, `floor_only`. Nếu cache chúng thì một lần 529 thoáng qua bị "đóng băng" thành FULL SET.

Khoá = sha256(JSON[diff đã prune, module-map, policy, model, prompt_version, allowlist, prompt_sha]):
  - `allowlist_sha` băm cả `{worker: suites}`: đổi tập worker hoặc suite của chúng thì bỏ cache.
  - `prompt_sha` băm nội dung `diff_select.md` + `capabilities.json` (cả hai nằm trong system prompt), vì `prompt_version` có thể quên tăng.

Mô hình rủi ro (CI): cache của Actions tách theo ref; PR đọc được cache của nhánh gốc nhưng KHÔNG ghi được vào đó. Kịch bản xấu nhất là cache của chính PR đó bị
đầu độc để làm THIẾU worker ngoài floor trong đúng PR đó: tương đương injection vào prompt, và floor vẫn do `core` ép. Vì vậy entry được validate lại theo schema
và theo allowlist HIỆN TẠI khi đọc; entry hỏng/lạ là miss, không phải lỗi.
"""
from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from qc_agent.llm import filecache

ENTRY_VERSION = 1
KEEP = 100
_LLM = {"type": "object", "additionalProperties": False,
        "required": ["model", "prompt_version", "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"],
        "properties": {"model": {"type": "string"}, "prompt_version": {"type": "string"},
                       "input_tokens": {"type": "integer", "minimum": 0}, "output_tokens": {"type": "integer", "minimum": 0},
                       "cache_creation_input_tokens": {"type": "integer", "minimum": 0}, "cache_read_input_tokens": {"type": "integer", "minimum": 0},
                       "est_usd": {"type": ["number", "null"], "minimum": 0}, "unknown_calls": {"type": "integer", "minimum": 1}}}


def make_key(*, diff_sha: str, module_map: dict | None, policy_sha: str, model: str, prompt_version: str,
             suite_map: dict[str, list[str]], prompt_sha: str) -> str:
    return filecache.make_key("select/1", diff_sha, filecache.digest(module_map), policy_sha, model, prompt_version, filecache.digest(suite_map), prompt_sha)


def lookup(directory: Path, key: str, allowlist: list[str]) -> dict | None:
    """`{"selections": [...], "llm": {...}}` đã validate, hoặc None (miss). Worker phải nằm trong allowlist HIỆN TẠI."""
    entry = filecache.read(directory, key)
    if entry is None:
        return None
    schema = {"type": "object", "additionalProperties": False, "required": ["version", "selections", "llm"],
              "properties": {"version": {"const": ENTRY_VERSION}, "llm": _LLM,
                             "selections": {"type": "array", "maxItems": 200, "items": {"type": "object", "additionalProperties": False, "required": ["worker", "reason"],
                                            "properties": {"worker": {"enum": list(allowlist)}, "reason": {"type": "string", "maxLength": 200}}}}}}
    if not Draft202012Validator(schema).is_valid(entry):
        return None
    return {"selections": entry["selections"], "llm": entry["llm"]}


def store(directory: Path, key: str, selections: list[dict], llm: dict) -> bool:
    ok = filecache.write(directory, key, {"version": ENTRY_VERSION, "selections": selections, "llm": llm})
    if ok:
        filecache.prune(directory, KEEP)
    return ok
