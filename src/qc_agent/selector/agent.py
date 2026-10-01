"""Diff Analysis Agent chi bo sung worker; loi se chay full set."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from qc_agent import settings
from qc_agent.core import project as project_lib
from qc_agent.llm.client import LLMError, call_tool
from qc_agent.logging_setup import event
from qc_agent.selector.payload import merge_floor

log = logging.getLogger("qc_agent.selector.agent")
PROMPT = Path(__file__).parent / "prompts" / "diff_select.md"


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


def _full(pruned, policy, suite_map, reason: str) -> dict:
    result = _base(pruned, policy, suite_map)
    result.update(source="fallback", full_set=True, fallback_reason=reason,
                  workers=sorted(suite_map), suites=sorted(set(policy.get("blocking_suites", [])) | set(policy.get("advisory_suites", []))))
    return _validated(merge_floor(result, policy, suite_map))


def select(pruned, rule_decision, policy: dict, suite_map: dict[str, list[str]], module_map: dict | None,
           *, transport=None, egress_dir=None) -> dict:
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
    diff = json.dumps([asdict(item) for item in pruned.files], ensure_ascii=False, sort_keys=True)
    diff = diff.replace("</untrusted_diff", "&lt;/untrusted_diff")
    user = "<untrusted_diff>\n" + diff + "\n</untrusted_diff>"
    try:
        call = call_tool(purpose="diff-select", model=settings.get().selector_model, system=system, user=user,
                         tool_name="select_workers", tool_description="Select workers needed for changed files",
                         input_schema=schema, egress_dir=Path(egress_dir or "."), data_categories=["source_code_diff"],
                         max_tokens=1024, timeout_s=15, transport=transport)
        for item in call.data["selections"]:
            worker = item["worker"]
            if worker not in suite_map:
                return _full(pruned, policy, suite_map, "unknown_worker")
            result["workers"].append(worker)
            result["suites"].extend(suite_map[worker])
            result["rationale"][worker] = _clean(item["reason"])
        result["source"] = "llm"
        result["llm"] = {"model": call.model, "prompt_version": "diff-select/1", "input_tokens": call.usage.input_tokens,
                         "output_tokens": call.usage.output_tokens, "cache_creation_input_tokens": call.usage.cache_creation_input_tokens,
                         "cache_read_input_tokens": call.usage.cache_read_input_tokens}
        result["workers"] = sorted(set(result["workers"]))
        result["suites"] = sorted(set(result["suites"]))
    except LLMError as error:
        reason = {"missing_key": "missing_api_key", "refused": "bad_output", "bad_request": "bad_output"}.get(error.kind, error.kind)
        result = _full(pruned, policy, suite_map, reason)
    result = merge_floor(result, policy, suite_map)
    event(log, "selector.decision", source=result["source"], full_set=result["full_set"],
          fallback_reason=result["fallback_reason"], workers=len(result["workers"]), suites=len(result["suites"]), files=len(pruned.files))
    return _validated(result)
