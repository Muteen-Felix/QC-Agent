"""So thời gian job gate có Select với baseline (cùng SUT, chạy FULL SET không Select). DoD: chậm thêm ≤ 60 giây (median). Chỉ đọc job.json đã lưu.

Baseline cùng commit: `gh workflow run qc-gate.yml --ref <nhánh D> ` (workflow_dispatch, không workers => FULL SET, không Select). Đo là thời gian của CHÍNH job gate
(`startedAt` → `completedAt` của job tên kết thúc bằng `qc-agent / <project>`), không phải thời gian xếp hàng. Số mẫu nhỏ => median có độ bất định lớn; báo kèm số mẫu.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

LIMIT_S = 60.0


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def job_seconds(job_json: Path, project: str) -> float:
    data = json.loads(Path(job_json).read_text(encoding="utf-8-sig"))
    jobs = [j for j in data.get("jobs", []) if str(j.get("name", "")).endswith(f"qc-agent / {project}")]
    if len(jobs) != 1:
        raise ValueError(f"{job_json}: cần đúng 1 job 'qc-agent / {project}', thấy {len(jobs)}")
    job = jobs[0]
    if not job.get("startedAt") or not job.get("completedAt"):
        raise ValueError(f"{job_json}: job chưa có startedAt/completedAt")
    return (_parse(job["completedAt"]) - _parse(job["startedAt"])).total_seconds()


def compare(with_select: list[float], baseline: list[float], *, limit_s: float = LIMIT_S) -> dict:
    if not with_select or not baseline:
        return {"verdict": "PENDING", "reason": "cần ≥ 1 mẫu cho mỗi bên", "with_select_n": len(with_select), "baseline_n": len(baseline)}
    delta = statistics.median(with_select) - statistics.median(baseline)
    return {"verdict": "PASS" if delta <= limit_s else "FAIL", "delta_s": round(delta, 1), "limit_s": limit_s,
            "with_select_median_s": statistics.median(with_select), "baseline_median_s": statistics.median(baseline),
            "with_select_n": len(with_select), "baseline_n": len(baseline),
            "note": "median; số mẫu nhỏ thì kết luận yếu. Lượt có cache hit không đại diện cho đường gọi LLM."}


def collect(job_files: list[Path], project: str) -> list[float]:
    return [job_seconds(path, project) for path in job_files]
