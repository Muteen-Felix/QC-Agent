"""Tao selection.json tu trigger va gop floor tat dinh."""
from __future__ import annotations

from qc_agent.core.plan import PlanError
from qc_agent.core.project import suites_by_worker


class TriggerPayload:
    @staticmethod
    def manual(workers, project_cfg, mode, suites, registry) -> dict:
        requested = list(dict.fromkeys(workers))
        suite_map = suites_by_worker(project_cfg, mode, suites, registry)
        invalid = [name for name in requested if name not in suite_map]
        if not requested or invalid:
            raise PlanError("worker khong hop le: " + ", ".join(invalid or ["<rong>"])
                            + "; hop le: " + ", ".join(sorted(suite_map)))
        names = sorted({suite for worker in requested for suite in suite_map[worker]})
        return {"version": 1, "trigger_type": "manual", "source": "manual", "full_set": False,
                "diff_sha256": None, "floor": [], "workers": requested, "suites": names,
                "rationale": {worker: "manual" for worker in requested}, "fallback_reason": None, "llm": None}


def merge_floor(selection: dict, mode_policy: dict, suite_map: dict[str, list[str]]) -> dict:
    out = dict(selection)
    floor = list(mode_policy.get("floor_workers", []))
    out["floor"] = floor
    out["workers"] = sorted(set(out["workers"]) | set(floor))
    out["suites"] = sorted(set(out["suites"]) | {s for w in floor for s in suite_map.get(w, [])})
    out["rationale"] = {**{w: "floor" for w in floor}, **out["rationale"]}
    return out
