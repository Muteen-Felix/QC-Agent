"""QC-Agent dashboard.  Chạy:  python -m uvicorn dashboard.app:app --port 8080"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard import runs_reader
from dashboard.job import Job, sut_alive

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("QC_RUNS_DIR") or ROOT / "runs")
REPORT_DIR = ROOT / "midscene_run" / "report"
STATE_DIR = Path(__file__).resolve().parent / "_state"
STATIC = Path(__file__).resolve().parent / "static"
FIRST_TICKET = 101

app = FastAPI(title="QC-Agent Dashboard")
job = Job(ROOT, RUNS_DIR, STATE_DIR)
_ticket_lock = threading.Lock()

REPORT_DIR.mkdir(parents=True, exist_ok=True)  # StaticFiles đòi thư mục tồn tại lúc mount
app.mount("/midscene", StaticFiles(directory=REPORT_DIR, html=True), name="midscene")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/summary")
def summary():
    return {**runs_reader.summary(RUNS_DIR), "sut_alive": sut_alive(), "job": job.state}


@app.get("/api/runs")
def runs():
    rows = runs_reader.list_runs(RUNS_DIR)
    running = job.new_run_id() if job.running() else None
    for row in rows:
        if row["run_id"] == running and not row["complete"]:
            row["gate_verdict"] = "RUNNING"
    return rows


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    detail = runs_reader.run_detail(RUNS_DIR, run_id, REPORT_DIR)
    if detail is None:
        raise HTTPException(404, f"không có run {run_id}")
    return detail


@app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
def run_report_md(run_id: str):
    if not runs_reader.RUN_ID.match(run_id) or not (RUNS_DIR / run_id / "report.md").is_file():
        raise HTTPException(404, "không có report.md")
    return (RUNS_DIR / run_id / "report.md").read_text(encoding="utf-8")


@app.post("/api/run", status_code=202)
def trigger():
    if job.running():
        raise HTTPException(409, "Pipeline đang chạy — đợi run hiện tại xong.")
    if not sut_alive():
        raise HTTPException(412, "Toy app (SUT) chưa chạy ở 127.0.0.1:8000. Chạy: .\\scripts\\toyapp.ps1 start")
    try:
        job.start()
    except RuntimeError:
        raise HTTPException(409, "Pipeline đang chạy — đợi run hiện tại xong.") from None
    return job.status()


@app.get("/api/job")
def job_status():
    return job.status()


class TicketIn(BaseModel):
    run_id: str
    finding_id: str


@app.post("/api/tickets", status_code=201)
def create_ticket(body: TicketIn):
    """MOCK: không gọi Jira. Ghi ticket nháp cục bộ kèm sha256 THẬT của evidence để minh hoạ truy vết."""
    finding = runs_reader.find_finding(RUNS_DIR, body.run_id, body.finding_id, REPORT_DIR)
    if finding is None:
        raise HTTPException(404, "không tìm thấy finding")
    with _ticket_lock:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / "tickets.json"
        tickets = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        ticket = {
            "key": f"JIRA-{FIRST_TICKET + len(tickets)}",
            "mock": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": body.run_id,
            "task_id": finding["task_id"],
            "finding_id": finding["finding_id"],
            "summary": finding["title"],
            "severity": finding["severity"],
            "detected_by": finding["detected_by"],
            "repro_steps": finding["repro_steps"],
            "evidence": finding["evidence"],
        }
        tickets.append(ticket)
        path.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")
    return ticket
