"""Helper của contract: validate theo JSON Schema, kiểm luật liên-trường, dựng result tổng hợp (error/skipped).
KHÔNG được có tên worker cụ thể nào trong file này."""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from qc_agent import settings

SCHEMAS_DIR = settings.get().resolved_schemas_dir
SCHEMA_FILES = {"task": "task_spec.json", "result": "result.json"}


@lru_cache(maxsize=None)
def _validator(kind):
    # utf-8-sig: chịu được file có BOM (Windows), giống tools/validate.py
    schema = json.loads((SCHEMAS_DIR / SCHEMA_FILES[kind]).read_text(encoding="utf-8-sig"))
    return Draft202012Validator(schema)


def _errors(kind, obj):
    errs = sorted(_validator(kind).iter_errors(obj), key=lambda e: list(map(str, e.path)))
    return [("/".join(map(str, e.path)) or "<root>") + ": " + e.message[:200] for e in errs]


def validate_task(spec: dict) -> list[str]:
    """Validate spec theo task_spec.json. Rỗng = hợp lệ."""
    return _errors("task", spec)


def validate_result(result: dict) -> list[str]:
    """Validate result theo result.json. Rỗng = hợp lệ."""
    return _errors("result", result)


def check_result_against_spec(spec: dict, result: dict) -> list[str]:
    """Luật liên-trường mà JSON Schema không diễn đạt được. Trả về danh sách vi phạm (rỗng = ổn).

    `error`/`skipped` là result tổng hợp (worker không cho ra kết quả): chưa có verdict thật và chưa có
    evidence, nên chỉ kiểm định danh + việc không được gating. Nếu áp luật gate/evidence cho chúng thì
    make_result() sẽ tự vi phạm.
    """
    v = []
    if result.get("task_id") != spec["task_id"]:
        v.append(f"task_id không khớp spec: {result.get('task_id')!r} != {spec['task_id']!r}")
    if result.get("run_id") != spec["run_id"]:
        v.append(f"run_id không khớp spec: {result.get('run_id')!r} != {spec['run_id']!r}")

    st = result.get("status")
    vd = result.get("verdict", {})
    if st in ("error", "skipped"):
        if vd.get("gating"):
            v.append(f"status={st} nhưng verdict.gating=true")
        return v

    lane = spec["lane"]
    if lane == "gate":
        if vd.get("gating") is not True:
            v.append("lane=gate nhưng verdict.gating != true (task gate sẽ tụt khỏi gate trong im lặng)")
        if vd.get("value") not in ("pass", "fail"):
            v.append("lane=gate nhưng verdict.value không phải pass/fail")
        elif vd.get("value") != st:
            v.append(f"status={st} lệch verdict.value={vd.get('value')}")
    elif vd.get("gating") is not False:
        v.append("lane=discovery nhưng verdict.gating=true (discovery không được chặn gate)")

    for f in result.get("findings", []):
        if f.get("verdict_source") == "llm_judgment" and f.get("confidence") is None:
            v.append(f"finding {f.get('finding_id')}: llm_judgment thiếu confidence")  # schema không chặn (G4)

    have = {e.get("kind") for e in result.get("evidence", [])}
    miss = [k for k in spec.get("evidence_required", []) if k not in have]
    if miss:
        v.append(f"thiếu evidence bắt buộc: {miss}")
    return v


def make_result(spec: dict, status: str, rationale: str,
                worker_name: str = "unknown", adapter_version: str = "core") -> dict:
    """Result tổng hợp khi worker không cho ra kết quả hợp lệ. status ∈ {error, skipped}.

    error -> verdict.value='fail', skipped -> 'non_gating'; cả hai gating=false (schema chỉ cho gating=true
    khi verdict_source=deterministic_assert, mà ở đây không có phép assert nào chạy). Việc gate đỏ/vàng do
    tầng gộp verdict quyết định theo `status`, không theo `gating`.
    """
    if status not in ("error", "skipped"):
        raise ValueError(f"make_result chỉ nhận status error|skipped, nhận {status!r}")
    return {
        "task_id": spec["task_id"], "run_id": spec["run_id"],
        "worker": {"name": worker_name, "version": None, "adapter_version": adapter_version},
        "status": status,
        "verdict": {"value": "fail" if status == "error" else "non_gating",
                    "verdict_source": "heuristic", "gating": False,
                    "confidence": None, "rationale": rationale},
        "findings": [], "metrics": {}, "evidence": [],
        "cost": {"wallclock_s": 0.0, "tokens": None, "usd": None},
        "sut_identity_ref": spec.get("sut_identity_ref"),
        "determinism": {}, "adapter_notes": [rationale],
    }
