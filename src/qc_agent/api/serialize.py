"""Chuyển bản ghi DB thành JSON trả về. Không bao giờ đưa ra: password_hash, token_hash, params.env, đường dẫn máy chủ."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from qc_agent.jobs.models import Job, JobTask, User


def iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def user_json(user: User) -> dict:
    return {"email": user.email, "display_name": user.display_name}


def task_json(task: JobTask) -> dict:
    return {"task_id": task.task_id, "worker": task.worker, "capability": task.capability, "lane": task.lane,
            "status": task.status, "gating": task.gating, "duration_s": task.duration_s, "tokens": task.tokens,
            "usd": task.usd, "summary": task.summary}


def progress(runs_root: Path, slug: str, job: Job) -> dict | None:
    """Tiến độ của job đang chạy, đọc từ thư mục run (results đã ghi / số task trong plan đã lưu)."""
    if job.status != "running":
        return None
    run_dir = runs_root / slug / str(job.id)
    done = len(list((run_dir / "results").glob("*.json")))
    total = 0
    try:
        total = len(yaml.safe_load((run_dir / "plan.yaml").read_text(encoding="utf-8")).get("tasks") or [])
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        pass
    return {"done": done, "total": max(total, done)}


def job_json(job: Job, slug: str, creators: dict[int, str], *, runs_root: Path | None = None, detail: bool = False) -> dict:
    params = job.params or {}
    out = {
        "id": str(job.id), "project": slug, "mode": job.mode, "source": job.source, "status": job.status,
        "suites": job.suites, "task_ids": job.task_ids, "environment": params.get("environment"),
        "pr_number": job.pr_number, "sha": job.sha, "branch": job.branch, "external_id": job.external_id,
        "created_by": creators.get(job.created_by) if job.created_by else None,
        "created_at": iso(job.created_at), "started_at": iso(job.started_at), "finished_at": iso(job.finished_at),
        "cancel_requested": job.cancel_requested, "gate_verdict": job.gate_verdict, "exit_code": job.exit_code,
        "error": job.error, "attempts": job.attempts,
    }
    if detail:
        out["tasks"] = [task_json(t) for t in job.tasks]
        out["artifacts"] = [{"path": a.path, "size_bytes": a.size_bytes, "sha256": a.sha256} for a in job.artifacts]
        out["progress"] = progress(runs_root, slug, job) if runs_root else None
    return out


def loads_json(data: bytes):
    return json.loads(data.decode("utf-8-sig"))
