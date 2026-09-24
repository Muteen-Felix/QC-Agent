"""Sức khoẻ: /healthz (tiến trình sống), /readyz (DB nối được và đã migrate tới head). Không cần đăng nhập, không lộ chi tiết."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from qc_agent.jobs import migrate

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request):
    state = request.app.state.qc
    try:
        with state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 — trả 503 gọn, không lộ chuỗi kết nối/lỗi nội bộ
        return JSONResponse({"status": "unavailable", "reason": "database"}, status_code=503)
    if version != migrate.head_revision():
        return JSONResponse({"status": "unavailable", "reason": "migrations"}, status_code=503)
    return {"status": "ready"}
