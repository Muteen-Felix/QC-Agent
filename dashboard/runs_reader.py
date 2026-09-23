"""Đọc runs/r-NNNN/ thành dict thuần cho dashboard. Read-only, không cache, không import core/."""
from __future__ import annotations

import json
import re
from pathlib import Path

RUN_ID = re.compile(r"^r-\d{4}$")  # bỏ thư mục rác như r-step25, r-collect: không phải run của orchestrator
RECENT = 10  # số run hoàn chỉnh gần nhất để tính tỷ lệ pass/fail


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_dirs(runs_dir: Path) -> list[Path]:
    if not runs_dir.is_dir():
        return []
    dirs = [p for p in runs_dir.iterdir() if p.is_dir() and RUN_ID.match(p.name)]
    return sorted(dirs, key=lambda p: int(p.name[2:]), reverse=True)


def _cost(report: dict) -> dict:
    """Cộng các giá trị cost KHÔNG null; null nghĩa là worker không báo cost, không phải $0."""
    usd, tokens, missing = 0.0, 0, 0
    for result in (report.get("details") or {}).get("results", {}).values():
        cost = result.get("cost") or {}
        if cost.get("usd") is None:
            missing += 1
        else:
            usd += cost["usd"]
        tokens += cost.get("tokens") or 0
    return {"usd": round(usd, 4), "tokens": tokens, "unreported": missing}


def _row(run_dir: Path) -> dict:
    report = _read(run_dir / "report.json")
    if not isinstance(report, dict):
        return {"run_id": run_dir.name, "gate_verdict": "INCOMPLETE", "complete": False}
    gating = report.get("deterministic_view") or []
    details = report.get("details") or {}
    results = details.get("results") or {}
    return {
        "run_id": report.get("run_id", run_dir.name),
        "complete": True,
        "gate_verdict": report.get("gate_verdict", "UNKNOWN"),
        "exit_code": report.get("exit_code"),
        "generated_at": details.get("generated_at"),
        "wallclock_s": details.get("wallclock_s"),
        "gating_pass": sum(1 for g in gating if g.get("status") == "pass"),
        "gating_total": len(gating),
        "tasks_pass": sum(1 for r in results.values() if r.get("status") == "pass"),
        "tasks_total": len(results),
        "cost": _cost(report),
    }


def list_runs(runs_dir: Path) -> list[dict]:
    return [_row(d) for d in _run_dirs(runs_dir)]


def summary(runs_dir: Path) -> dict:
    rows = list_runs(runs_dir)
    done = [r for r in rows if r["complete"]]
    recent = done[:RECENT]
    return {
        "total_runs": len(rows),
        "latest": done[0] if done else None,
        "recent_window": len(recent),
        "recent_pass": sum(1 for r in recent if r["gate_verdict"] == "PASS"),
        "recent_fail": sum(1 for r in recent if r["gate_verdict"] == "FAIL"),
        "usd_total": round(sum(r["cost"]["usd"] for r in done), 4),
        "tokens_total": sum(r["cost"]["tokens"] for r in done),
        "unreported_tasks": sum(r["cost"]["unreported"] for r in done),
    }


def midscene_reports(task_dir: Path, report_dir: Path) -> list[dict]:
    """Report HTML của Midscene mà summary.json trỏ tới; chỉ nhận file nằm TRONG report_dir (chống path traversal)."""
    data = _read(task_dir / "summary.json")
    if not isinstance(data, dict):
        return []
    root = report_dir.resolve()
    out = []
    for item in data.get("results") or []:
        rel = item.get("report")
        if not rel:
            continue
        target = (task_dir / rel.replace("\\", "/")).resolve()
        inside = target.parent == root
        out.append({
            "script": Path(str(item.get("script", "")).replace("\\", "/")).name,
            "success": item.get("success"),
            "error": item.get("error"),
            "file": target.name if inside else None,
            "exists": inside and target.is_file(),
        })
    return out


def run_detail(runs_dir: Path, run_id: str, report_dir: Path) -> dict | None:
    if not RUN_ID.match(run_id):
        return None
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        return None
    report = _read(run_dir / "report.json") or {}
    results = {}
    for path in sorted((run_dir / "results").glob("*.json")):
        result = _read(path)
        if isinstance(result, dict):
            results[result.get("task_id", path.stem)] = result

    workers, findings = [], []
    for task_id, r in results.items():
        verdict = r.get("verdict") or {}
        gating = bool(verdict.get("gating"))
        evidence = [{"uri": e.get("uri"), "sha256": e.get("sha256")} for e in r.get("evidence") or []]
        workers.append({
            "task_id": task_id,
            "worker": (r.get("worker") or {}).get("name"),
            "status": r.get("status"),
            "gating": gating,
            "verdict_value": verdict.get("value"),
            "verdict_source": verdict.get("verdict_source"),
            "wallclock_s": (r.get("cost") or {}).get("wallclock_s"),
            "metrics": r.get("metrics") or {},
            "findings": [f.get("title") for f in r.get("findings") or []],
            "midscene": midscene_reports(run_dir / task_id, report_dir),
        })
        if not gating:  # discovery lane: heuristic, không chặn gate
            for f in r.get("findings") or []:
                findings.append({
                    "task_id": task_id,
                    "finding_id": f.get("finding_id"),
                    "title": f.get("title"),
                    "detected_by": f.get("detected_by"),
                    "severity": f.get("severity_hint"),
                    "repro_steps": (f.get("promote_candidate") or {}).get("repro_steps") or [],
                    "evidence": evidence,
                })
    return {
        "run": _row(run_dir),
        "workers": workers,
        "findings": findings,
        "canary": report.get("canary") or [],
        "banner": report.get("banner") or [],
        "has_report_md": (run_dir / "report.md").is_file(),
    }


def find_finding(runs_dir: Path, run_id: str, finding_id: str, report_dir: Path) -> dict | None:
    detail = run_detail(runs_dir, run_id, report_dir)
    if not detail:
        return None
    return next((f for f in detail["findings"] if f["finding_id"] == finding_id), None)
