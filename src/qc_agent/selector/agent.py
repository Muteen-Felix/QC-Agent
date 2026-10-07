"""Diff Analysis Agent chi bo sung worker; loi se chay full set."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from qc_agent import settings
from qc_agent.core import project as project_lib
from qc_agent.llm import filecache, prices
from qc_agent.llm.client import LLMError, call_tool, estimate_input_tokens
from qc_agent.logging_setup import event
from qc_agent.selector import cache as select_cache
from qc_agent.selector.payload import merge_floor

log = logging.getLogger("qc_agent.selector.agent")
PROMPT = Path(__file__).parent / "prompts" / "diff_select.md"
TOOL_NAME = "select_workers"
TOOL_DESCRIPTION = "Select workers needed for changed files"
PROMPT_VERSION = "diff-select/1"   # khớp front-matter diff_select.md; khoá cache còn băm cả nội dung prompt nên quên tăng số cũng không trả kết quả cũ


@dataclass(frozen=True)
class DiffRequest:
    """Phần không phụ thuộc diff của request Diff Agent: `system` (prompt + MODULE MAP + WORKERS + CAPABILITIES) và `schema` của tool `select_workers`.
    `prompt` và `capabilities` giữ lại để băm khoá cache. Dùng chung cho `select` và `tools/eval_cost.py` để số đo không lệch khung với request thật."""
    system: str
    schema: dict
    allowlist: list[str]
    prompt: str
    capabilities: dict


def build_request(suite_map: dict[str, list[str]], module_map: dict | None) -> DiffRequest:
    allowlist = sorted(suite_map)
    schema = {"type": "object", "additionalProperties": False, "required": ["selections"],
              "properties": {"selections": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                  "required": ["worker", "reason"], "properties": {"worker": {"enum": allowlist}, "reason": {"type": "string"}}}}}}
    prompt = PROMPT.read_text(encoding="utf-8")
    capabilities_path = settings.get().resolved_schemas_dir / "capabilities.json"
    capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
    catalog = {name: {"suites": suite_map[name]} for name in allowlist}
    system = prompt + "\nMODULE MAP\n" + yaml.safe_dump(module_map or {}, sort_keys=True, allow_unicode=True)
    system += "\nWORKERS\n" + json.dumps(catalog, ensure_ascii=False, sort_keys=True)
    system += "\nCAPABILITIES\n" + json.dumps(capabilities, ensure_ascii=False, sort_keys=True)
    return DiffRequest(system, schema, allowlist, prompt, capabilities)


def payload_json(files) -> str:
    """Biểu diễn JSON của danh sách `PrunedFile` trong `user`. Một chỗ duy nhất: `tools/eval_cost.py` dùng cùng hàm này để dựng cận dưới B_min."""
    return json.dumps([asdict(item) for item in files], ensure_ascii=False, sort_keys=True)


def pruned_payload(pruned) -> str:
    """Danh sách file đã prune dạng JSON: phần thay đổi theo diff trong `user`."""
    return payload_json(pruned.files)


def user_message(payload: str) -> str:
    """Khung `user`: payload (không tin cậy) đặt trong vùng phân cách; chuỗi đóng khung bị vô hiệu để diff không thoát được khỏi vùng."""
    return "<untrusted_diff>\n" + payload.replace("</untrusted_diff", "&lt;/untrusted_diff") + "\n</untrusted_diff>"


def estimate_request(request: DiffRequest, user: str) -> int:
    """Ước lượng đầu vào của một lời gọi (cùng công thức với trần `token_cap`: system + user + schema; `estimate_input_tokens` là ước lượng gần đúng)."""
    return estimate_input_tokens(request.system, user, request.schema)


def _clean(value: str) -> str:
    return " ".join("".join(c if c.isprintable() else " " for c in str(value)).split())[:200]


def _base(pruned, policy, suite_map) -> dict:
    return {"version": 1, "trigger_type": "pr", "source": "rules", "full_set": False,
            "diff_sha256": pruned.sha256, "floor": [], "workers": [], "suites": [],
            "rationale": {}, "fallback_reason": None, "llm": None}


def _validated(result: dict) -> dict:
    schema = json.loads((settings.get().resolved_schemas_dir / "selection.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result)
    return result


def _full(pruned, policy, suite_map, reason: str, llm: dict | None = None) -> dict:
    """FULL SET. `llm` mang chi phí của lời gọi ĐÃ tốn (response bị từ chối) hoặc CHƯA XÁC ĐỊNH (đã gửi, không có response); `None` khi chưa gửi gì."""
    result = _base(pruned, policy, suite_map)
    result.update(source="fallback", full_set=True, fallback_reason=reason, llm=llm,
                  workers=sorted(suite_map), suites=sorted(set(policy.get("blocking_suites", [])) | set(policy.get("advisory_suites", []))))
    return _validated(merge_floor(result, policy, suite_map))


def _failed_llm(error: LLMError, model: str) -> dict | None:
    """Khối `llm` của một lời gọi hỏng, theo những gì client biết (xem LLMError):
      - có `usage`: response bị từ chối (sai schema, refused...) đã bị tính phí: token thật + `est_usd`;
      - đã gửi mà không có response (timeout, đứt kết nối): `usage_known: false`; 4 trường token là 0 chỉ vì schema đòi số, consumer phải coi là CHƯA BIẾT;
      - chưa gửi gì, hoặc API trả lỗi HTTP ([Assumption, chưa kiểm chứng] không tính phí): `None`.
    Thời lượng không được ghi (`selection` được băm vào `plan_id`): xem `llm.call` trong log."""
    base = {"model": model, "prompt_version": PROMPT_VERSION, "status": error.kind}
    if error.unknown_calls > 0:
        base["unknown_calls"] = error.unknown_calls   # kể cả khi lần cuối có usage: các lần gửi trước đó (retry sau lỗi mạng) vẫn chưa rõ chi phí
    if error.usage is not None:
        usage = error.usage
        return {**base, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens, "est_usd": prices.estimate_cost(model, usage)}
    if error.unknown_calls > 0:
        return {**base, "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "usage_known": False}
    return None


def select(pruned, rule_decision, policy: dict, suite_map: dict[str, list[str]], module_map: dict | None,
           *, transport=None, egress_dir=None, policy_sha: str | None = None) -> dict:
    result = _base(pruned, policy, suite_map)
    if rule_decision.full_set:
        result.update(full_set=True, suites=sorted(set(policy.get("blocking_suites", [])) | set(policy.get("advisory_suites", []))))
        return _validated(merge_floor(result, policy, suite_map))
    for worker, reasons in rule_decision.hint_workers.items():
        if worker in suite_map:
            result["workers"].append(worker)
            result["suites"].extend(suite_map[worker])
            result["rationale"][worker] = _clean(", ".join(reasons))
    if rule_decision.floor_only:
        return _validated(merge_floor(result, policy, suite_map))
    request = build_request(suite_map, module_map)
    allowlist, schema, prompt, capabilities = request.allowlist, request.schema, request.prompt, request.capabilities
    system = request.system
    user = user_message(pruned_payload(pruned))
    model = settings.get().selector_model
    cache_dir = settings.get().resolved_select_cache_dir
    cache_key = cache_info = None
    if cache_dir is not None:
        cache_key = select_cache.make_key(diff_sha=pruned.sha256, module_map=module_map, policy_sha=policy_sha or filecache.digest(policy), model=model,
                                          prompt_version=PROMPT_VERSION, suite_map=suite_map, prompt_sha=filecache.digest([prompt, capabilities]))
        cache_info = select_cache.lookup(cache_dir, cache_key, allowlist)
        event(log, "selector.cache", outcome="hit" if cache_info else "miss", key=cache_key[:8])
    if cache_info is None:   # hit thì không tốn gì nên không bị chặn bởi trần; ước lượng gần đúng, không phải trần cứng (xem client.estimate_input_tokens)
        estimate = estimate_request(request, user)
        if estimate > settings.get().llm_max_input_tokens:
            event(log, "selector.token_cap", estimate=estimate, cap=settings.get().llm_max_input_tokens, files=len(pruned.files))
            return _full(pruned, policy, suite_map, "token_cap")   # 0 lời gọi, không ghi cache; gate không đỏ vì chi phí
    try:
        if cache_info is not None:
            selections, llm_info = cache_info["selections"], {**cache_info["llm"], "cache_hit": True}
        else:
            call = call_tool(purpose="diff-select", model=model, system=system, user=user,
                             tool_name=TOOL_NAME, tool_description=TOOL_DESCRIPTION,
                             input_schema=schema, egress_dir=Path(egress_dir or "."), data_categories=["source_code_diff"],
                             max_tokens=1024, timeout_s=15, transport=transport)
            selections = [{"worker": item["worker"], "reason": _clean(item["reason"])} for item in call.data["selections"]]
            llm_info = {"model": call.model, "prompt_version": PROMPT_VERSION, "input_tokens": call.usage.input_tokens,
                        "output_tokens": call.usage.output_tokens, "cache_creation_input_tokens": call.usage.cache_creation_input_tokens,
                        "cache_read_input_tokens": call.usage.cache_read_input_tokens, "est_usd": prices.estimate_cost(call.model, call.usage)}
            if call.unknown_calls:
                llm_info["unknown_calls"] = call.unknown_calls   # thành công sau retry/model dự phòng: các lần gửi trước chưa rõ chi phí
        for item in selections:
            worker = item["worker"]
            if worker not in suite_map:
                return _full(pruned, policy, suite_map, "unknown_worker", None if cache_info is not None else llm_info)   # lời gọi đã tốn dù kết quả bị bỏ
            result["workers"].append(worker)
            result["suites"].extend(suite_map[worker])
            result["rationale"][worker] = _clean(item["reason"])
        result["source"] = "cache" if cache_info is not None else "llm"
        result["llm"] = llm_info
        result["workers"] = sorted(set(result["workers"]))
        result["suites"] = sorted(set(result["suites"]))
        if cache_key is not None and cache_info is None:   # chỉ lời gọi THÀNH CÔNG và hợp lệ mới được ghi (mọi fallback return/except ở trên/dưới)
            stored = select_cache.store(cache_dir, cache_key, selections, llm_info)
            event(log, "selector.cache", outcome="store" if stored else "store_failed", key=cache_key[:8])
    except LLMError as error:
        reason = {"missing_key": "missing_api_key", "refused": "bad_output", "bad_request": "bad_output"}.get(error.kind, error.kind)
        result = _full(pruned, policy, suite_map, reason, _failed_llm(error, model))
    result = merge_floor(result, policy, suite_map)
    event(log, "selector.decision", source=result["source"], full_set=result["full_set"],
          fallback_reason=result["fallback_reason"], workers=len(result["workers"]), suites=len(result["suites"]), files=len(pruned.files))
    return _validated(result)
