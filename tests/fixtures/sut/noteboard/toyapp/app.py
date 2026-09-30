"""noteboard — toy app cho QC Agent PoC.  Chạy từ repo root:
    python -m uvicorn toyapp.app:app --host 127.0.0.1 --port 8000
Lỗi CÀI SẴN (bật/tắt bằng env QC_BUGS, mặc định "1,2,3"; "none" = tắt hết — KHÔNG dùng chuỗi rỗng, PowerShell coi env rỗng là xoá):
  BUG-1  GET /notes/{id} với id dài hơn LONG_ID_LEN -> 500 (đáng lẽ 4xx)        -> Schemathesis bắt
  BUG-2  frontend: bấm Xoá xong không vẽ lại danh sách (xem static/index.html)   -> Midscene + telemetry
  BUG-3  summarize: body chứa [[long]] -> summary dài hơn body (summarizer.py)   -> DeepEval bắt
Mutant harness: QC_LATENCY_MS=400 làm GET /notes chậm -> k6 threshold fail.

MUTANT NGHIỆP VỤ BUG-4…BUG-13 (S1-08): đo "bộ Ground-Truth đã duyệt có bắt được lỗi nghiệp vụ không" (tools/eval_groundtruth.py). MẶC ĐỊNH TẮT (chỉ bật khi
QC_BUGS liệt kê tường minh, vd. QC_BUGS=8). Mỗi mutant vi phạm ít nhất một AC của tests/fixtures/prd/noteboard-prd.md và trả về response ĐÚNG SCHEMA, không 5xx:
suite api-contract (Schemathesis: không 5xx + response_schema_conformance) không bắt được, chỉ so với PRD mới thấy sai. Bảng đầy đủ: tests/fixtures/prd/noteboard-golden.yaml.

  BUG-4   AC-1.3       POST /notes nhận title rỗng (201) thay vì 422        [OpenAPI của SUT lỏng theo: title minLength 0, nên request rỗng "hợp lệ theo schema"]
  BUG-5   AC-1.5       title dài đúng 200 ký tự bị 422 (giới hạn lệch 1: tối đa 199) [OpenAPI khai maxLength 199 theo mã; 422 là mã đã khai]
  BUG-6   AC-1.1       POST /notes trả 200 thay vì 201                        [mã 200 không khai trong OpenAPI: check response_schema_conformance chỉ kiểm mã đã khai]
  BUG-7   AC-3.4       DELETE id chưa từng có trả 204 thay vì 404             [204 là mã đã khai]
  BUG-8   AC-3.2/3.3   DELETE trả 204 nhưng KHÔNG xoá (GET sau đó vẫn 200)    [từng request riêng lẻ đều hợp lệ; sai nằm ở trạng thái]
  BUG-9   AC-2.1       GET /notes trả mảng rỗng dù đã có ghi chú              [mảng rỗng vẫn hợp lệ theo schema]
  BUG-10  AC-1.2       lưu title/body sau khi cắt khoảng trắng đầu/cuối       [chuỗi vẫn hợp lệ]
  BUG-11  AC-4.5       summarize id không tồn tại trả 200 + summary rỗng      [200 kèm Summary hợp lệ; endpoint này còn bị exclude_path ở suite api-contract]
  BUG-12  AC-4.6       summarize ghi đè body của ghi chú bằng bản tóm tắt     [response đúng, sai nằm ở tác dụng phụ]
  BUG-13  AC-2.2       GET /notes/{id} trả nội dung của ghi chú TRƯỚC nó (nếu có) [ghi chú trả về vẫn hợp lệ theo schema]
"""
import asyncio, json, os, pathlib, sqlite3, threading, time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from toyapp import summarizer

BUGS = {b.strip() for b in (os.environ.get("QC_BUGS", "").strip() or "1,2,3").split(",") if b.strip()}
TITLE_MIN = 0 if "4" in BUGS else 1
TITLE_MAX = 199 if "5" in BUGS else 200
LATENCY_MS = int(os.environ.get("QC_LATENCY_MS", "").strip() or "0")
LONG_ID_LEN = int(os.environ.get("QC_LONG_ID_LEN", "").strip() or "16")
INDEX = pathlib.Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="noteboard", version="0.1.0")
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL)")
_lock = threading.Lock()
EVENTS: list = []                      # telemetry cho implicit signals (KHÔNG nằm trong OpenAPI)


class NoteIn(BaseModel):
    title: str = Field(min_length=TITLE_MIN, max_length=TITLE_MAX)
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
def create_note(n: NoteIn, response: Response):
    title, body = (n.title.strip(), n.body.strip()) if "10" in BUGS else (n.title, n.body)   # BUG-10
    if "6" in BUGS:
        response.status_code = 200                                                            # BUG-6
    with _lock:
        cur = _db.execute("INSERT INTO notes(title,body) VALUES (?,?)", (title, body))
        _db.commit()
        return {"id": cur.lastrowid, "title": title, "body": body}


@app.get("/notes", response_model=list[NoteOut])
def list_notes():
    with _lock:
        rows = [_row(r) for r in _db.execute("SELECT id,title,body FROM notes ORDER BY id")]
    return [] if "9" in BUGS else rows                                                        # BUG-9


@app.get("/notes/{note_id}", response_model=NoteOut, responses={404: {"description": "not found"}})
def get_note(note_id: str):
    found = _get(note_id)
    if "13" in BUGS:                                                                          # BUG-13: nội dung của ghi chú đứng trước (nếu có)
        with _lock:
            before = _db.execute("SELECT id,title,body FROM notes WHERE id < ? ORDER BY id DESC LIMIT 1", (found["id"],)).fetchone()
        if before:
            return {**_row(before), "id": found["id"]}
    return found


@app.delete("/notes/{note_id}", status_code=204, responses={404: {"description": "not found"}})
def delete_note(note_id: str):
    try:
        _get(note_id)
    except HTTPException:
        if "7" in BUGS:                                                                       # BUG-7: id không tồn tại vẫn "xoá được"
            return Response(status_code=204)
        raise
    if "8" not in BUGS:                                                                       # BUG-8: trả 204 nhưng không xoá
        with _lock:
            _db.execute("DELETE FROM notes WHERE id=?", (int(note_id),))
            _db.commit()
    return Response(status_code=204)


@app.post("/notes/{note_id}/summarize", response_model=Summary, responses={404: {"description": "not found"}})
def summarize(note_id: str):
    try:
        n = _get(note_id)
    except HTTPException:
        if "11" in BUGS:                                                                      # BUG-11: id không tồn tại vẫn 200
            return {"summary": "", "model": summarizer.MODEL, "prompt_hash": summarizer.PROMPT_HASH}
        raise
    summary = summarizer.summarize(n["body"], bug3="3" in BUGS)
    if "12" in BUGS:                                                                          # BUG-12: tóm tắt ghi đè nội dung ghi chú
        with _lock:
            _db.execute("UPDATE notes SET body=? WHERE id=?", (summary or n["body"], n["id"]))
            _db.commit()
    return {"summary": summary, "model": summarizer.MODEL, "prompt_hash": summarizer.PROMPT_HASH}


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
