"""CI đẩy kết quả một run (chế độ tự động trên PR) lên để có lịch sử tập trung. Xác thực bằng token API của ĐÚNG project.
Ingest chỉ GHI NHẬN kết quả; verdict chặn merge vẫn do exit code / Check Run của CLI trong CI quyết định, không phụ thuộc service này."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from qc_agent.api.deps import project_or_404
from qc_agent.api.schemas import RunIn
from qc_agent.auth import service
from qc_agent.core.evidence import sha256_file
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope

router = APIRouter(prefix="/api/v1", tags=["ingest"])
_VERDICTS = ("PASS", "YELLOW", "FAIL")


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _tasks_from_report(report: dict) -> list[dict]:
    """job_tasks từ report.json: details.results (status/cost/metrics), làm giàu bằng deterministic_view (worker/capability/gating)."""
    gating = {row.get("task_id"): row for row in report.get("deterministic_view") or [] if isinstance(row, dict)}
    rows = []
    for task_id, result in sorted(((report.get("details") or {}).get("results") or {}).items()):
        if not isinstance(result, dict):
            continue
        cost, view = result.get("cost") or {}, gating.get(task_id) or {}
        rows.append({
            "task_id": str(task_id)[:128], "worker": view.get("worker"), "capability": view.get("capability"),
            "lane": "gate" if view else None, "status": str(result.get("status", "error"))[:16], "gating": bool(view),
            "duration_s": cost.get("wallclock_s"), "tokens": cost.get("tokens"), "usd": cost.get("usd"),
            "summary": {"metrics": result.get("metrics") or {}}})
    return rows


@router.post("/projects/{slug}/runs", status_code=201)
async def ingest_run(slug: str, request: Request, response: Response):
    state = request.app.state.qc
    project_or_404(state, slug)
    raw_token = _bearer(request)
    if raw_token is None:
        raise HTTPException(status_code=401, detail="cần Authorization: Bearer <token>")
    limit = state.cfg.max_ingest_bytes
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(status_code=413, detail=f"report vượt {limit} byte")
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status_code=413, detail=f"report vượt {limit} byte")

    # scope riêng cho xác thực: ghi last_used_at ngay cả khi các bước sau từ chối
    with session_scope(state.engine) as session:
        token = service.resolve_api_token(session, raw_token)
        allowed = token is not None and service.token_can_write(session, token, slug)
    if token is None:
        raise HTTPException(status_code=401, detail="token không hợp lệ hoặc đã thu hồi")
    if not allowed:
        raise HTTPException(status_code=403, detail="token này không có quyền ghi vào project này")

    try:
        run = RunIn.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=json.loads(error.json(include_url=False, include_input=False))) from None
    report = run.report
    verdict, exit_code = report.get("gate_verdict"), report.get("exit_code")
    if verdict not in _VERDICTS or isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise HTTPException(status_code=422, detail="report.json thiếu gate_verdict (PASS|YELLOW|FAIL) hoặc exit_code (số nguyên)")
    if not isinstance((report.get("details") or {}).get("results"), dict):
        raise HTTPException(status_code=422, detail="report.json thiếu details.results")

    with session_scope(state.engine) as session:
        job, created = repo.create_finished_job(
            session, slug, external_id=run.external_id, mode=run.mode, status="failed" if verdict == "FAIL" else "succeeded",
            gate_verdict=verdict, exit_code=exit_code, pr_number=run.pr_number, sha=run.sha, branch=run.branch)
        job_id = job.id
        if created:
            run_dir = state.runs_root / slug / str(job_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            artifacts = []
            for name, content in (("report.json", json.dumps(report, ensure_ascii=False, indent=2)), ("report.md", run.report_md)):
                if content is None:
                    continue
                path = run_dir / name
                path.write_bytes(content.encode("utf-8"))
                artifacts.append({"path": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path),
                                  "storage_uri": path.resolve().as_uri()})
            repo.replace_job_results(session, job_id, _tasks_from_report(report), artifacts)
    if not created:
        response.status_code = 200  # gửi lại cùng external_id: trả job cũ, không tạo trùng
    return {"id": str(job_id), "created": created, "project": slug, "status": job.status,
            "ingested_at": datetime.now(timezone.utc).isoformat()}
