"""Danh mục: project, suite (đọc từ checkout của SUT), worker (từ registry). Chỉ đọc; cần đăng nhập."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from qc_agent.api.deps import StateDep, UserDep, project_or_404
from qc_agent.core import project as project_lib
from qc_agent.core import registry
from qc_agent.core.plan import PlanError

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def _mode_kind(policy: dict) -> str:
    return "suites" if "suites" in policy else "blocking"


@router.get("/projects")
def list_projects(state: StateDep, user: UserDep):
    out = []
    for slug, cfg in sorted(state.projects.items()):
        checkout = state.resolver.sut_checkout(slug)
        out.append({
            "slug": slug, "name": cfg.get("name") or slug, "repo": cfg.get("repo"),
            "modes": {name: {"kind": _mode_kind(p), "on_skipped_gate_task": p.get("on_skipped_gate_task")}
                      for name, p in cfg["modes"].items()},
            "environments": {name: {"description": e.get("description"), "exclusive": bool(e.get("concurrency_key")),
                                    "timeout_s": e.get("timeout_s")} for name, e in (cfg.get("environments") or {}).items()},
            "runnable": checkout is not None and checkout.is_dir(),  # có checkout để đọc suite / chạy worker
        })
    return out


def load_project_suites(state, slug: str) -> dict:
    """Suite của project từ checkout trên máy service. Thiếu cấu hình/thư mục => 409 (lỗi cấu hình phía vận hành)."""
    project_or_404(state, slug)
    checkout = state.resolver.sut_checkout(slug)
    if checkout is None:
        raise HTTPException(status_code=409, detail="project chưa cấu hình sut_checkout nên không chạy thủ công được")
    if not checkout.is_dir():
        raise HTTPException(status_code=409, detail="thư mục checkout của project không tồn tại trên máy chủ")
    try:
        return project_lib.load_suites(Path(checkout) / state.projects[slug]["suites_dir"])
    except PlanError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.get("/projects/{slug}/suites")
def list_suites(slug: str, state: StateDep, user: UserDep):
    suites = load_project_suites(state, slug)
    policies = state.projects[slug]["modes"]
    out = []
    for name, suite in suites.items():
        modes = {}
        for mode, policy in policies.items():
            if "suites" in policy:
                if policy["suites"] == "*" or name in policy["suites"]:
                    modes[mode] = "any"
            elif name in policy.get("blocking_suites", []):
                modes[mode] = "blocking"
            elif name in policy.get("advisory_suites", []):
                modes[mode] = "advisory"
        out.append({"name": name, "sha256": suite["sha256"], "modes": modes,
                    "tasks": [{"task_id": t.get("task_id"), "capability": t.get("capability"), "lane": t.get("lane"),
                               "intent": t.get("intent")} for t in suite["tasks"]]})
    return out


@router.get("/workers")
def list_workers(state: StateDep, user: UserDep):
    """Metadata worker từ registry (chưa probe: probe chạy lệnh ngoài và chỉ xảy ra lúc thực thi job)."""
    workers = registry.load_many(state.cfg.workers_dirs)
    return [{"name": w.name, "lanes": w.lanes, "adapter": w.adapter, "data_egress": w.data_egress,
             "requires": w.requires,
             "capabilities": [{"id": cid, "oracle_kinds": cap.get("oracle_kinds", []),
                               "parallel_safe": cap.get("parallel_safe")} for cid, cap in w.capabilities.items()]}
            for w in sorted(workers.values(), key=lambda w: w.name)]
