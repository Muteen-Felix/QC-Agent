"""noteboard — toy app cho QC Agent PoC.  Chạy từ repo root:
    python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000
Lỗi CÀI SẴN (bật/tắt bằng env QC_BUGS, mặc định "1,2,3"; "none" = tắt hết — KHÔNG dùng chuỗi rỗng, PowerShell coi env rỗng là xoá):
  BUG-1  GET /notes/{id} với id dài hơn LONG_ID_LEN -> 500 (đáng lẽ 4xx)        -> Schemathesis bắt
  BUG-2  frontend: bấm Xoá xong không vẽ lại danh sách (xem static/index.html)   -> Midscene + telemetry
  BUG-3  summarize: body chứa [[long]] -> summary dài hơn body (summarizer.py)   -> DeepEval bắt
Mutant harness (KHÔNG phải bug thứ 4): QC_LATENCY_MS=400 làm GET /notes chậm -> k6 threshold fail.
"""
import asyncio, json, os, pathlib, sqlite3, threading, time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from toyapp import summarizer

BUGS = {b.strip() for b in (os.environ.get("QC_BUGS", "").strip() or "1,2,3").split(",") if b.strip()}
LATENCY_MS = int(os.environ.get("QC_LATENCY_MS", "").strip() or "0")
LONG_ID_LEN = int(os.environ.get("QC_LONG_ID_LEN", "").strip() or "16")
INDEX = pathlib.Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="noteboard", version="0.1.0")
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL)")
_lock = threading.Lock()
EVENTS: list = []                      # telemetry cho implicit signals (KHÔNG nằm trong OpenAPI)


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    id: int
    title: str
    body: str


class Summary(BaseModel):
    summary: str
    model: str
    prompt_hash: str


def _row(r):
    return {"id": r[0], "title": r[1], "body": r[2]}


def _get(note_id: str):
    if "1" in BUGS and len(note_id) > LONG_ID_LEN:
        raise RuntimeError("BUG-1: id quá dài")
    if not note_id.isdigit():
        raise HTTPException(404, "not found")
    with _lock:
        r = _db.execute("SELECT id,title,body FROM notes WHERE id=?", (int(note_id),)).fetchone()
    if not r:
        raise HTTPException(404, "not found")
    return _row(r)


@app.middleware("http")
async def track(request: Request, call_next):
    if LATENCY_MS and request.method == "GET" and request.url.path == "/notes":
        await asyncio.sleep(LATENCY_MS / 1000)
    try:
        resp = await call_next(request)
    except Exception:
        resp = JSONResponse({"detail": "internal error"}, status_code=500)
    if resp.status_code >= 500 and not request.url.path.startswith("/__qc"):
        EVENTS.append({"type": "server_5xx", "path": request.url.path, "status": resp.status_code, "t": time.time()})
    return resp


@app.post("/notes", status_code=201, response_model=NoteOut)
def create_note(n: NoteIn):
    with _lock:
        cur = _db.execute("INSERT INTO notes(title,body) VALUES (?,?)", (n.title, n.body))
        _db.commit()
        return {"id": cur.lastrowid, "title": n.title, "body": n.body}


@app.get("/notes", response_model=list[NoteOut])
def list_notes():
    with _lock:
        return [_row(r) for r in _db.execute("SELECT id,title,body FROM notes ORDER BY id")]


@app.get("/notes/{note_id}", response_model=NoteOut, responses={404: {"description": "not found"}})
def get_note(note_id: str):
    return _get(note_id)


@app.delete("/notes/{note_id}", status_code=204, responses={404: {"description": "not found"}})
def delete_note(note_id: str):
    _get(note_id)
    with _lock:
        _db.execute("DELETE FROM notes WHERE id=?", (int(note_id),))
        _db.commit()
    return Response(status_code=204)


@app.post("/notes/{note_id}/summarize", response_model=Summary, responses={404: {"description": "not found"}})
def summarize(note_id: str):
    n = _get(note_id)
    return {"summary": summarizer.summarize(n["body"], bug3="3" in BUGS),
            "model": summarizer.MODEL, "prompt_hash": summarizer.PROMPT_HASH}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return INDEX.read_text(encoding="utf-8").replace("__QC_BUGS__", json.dumps(sorted(BUGS)))


@app.post("/__qc/events", include_in_schema=False)
async def add_event(request: Request):
    EVENTS.append({**(await request.json()), "t": time.time()})
    return {"ok": True}


@app.get("/__qc/events", include_in_schema=False)
def get_events():
    return EVENTS


@app.get("/__qc/config", include_in_schema=False)
def get_config():
    """Cấu hình runtime của SUT — orchestrator đưa vào SUT identity (plan.sut.probe_url)."""
    return {"bugs": sorted(BUGS), "latency_ms": LATENCY_MS, "long_id_len": LONG_ID_LEN}


@app.delete("/__qc/events", include_in_schema=False)
def reset_events():
    EVENTS.clear()
    return {"ok": True}
