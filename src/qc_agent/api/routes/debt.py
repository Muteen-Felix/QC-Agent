"""Sổ nợ test của project (chỉ đọc; cần đăng nhập). Nợ do khâu dò nợ ghi (CI ingest / executor); API này không sửa được sổ."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from qc_agent.api.deps import DbDep, StateDep, UserDep, project_or_404
from qc_agent.api.serialize import debt_json
from qc_agent.jobs import repository as repo

router = APIRouter(prefix="/api/v1", tags=["debt"])


@router.get("/projects/{slug}/debt")
def list_debt(slug: str, state: StateDep, session: DbDep, user: UserDep, open: Annotated[bool, Query()] = True,
              kind: Annotated[str | None, Query(max_length=32)] = None, limit: Annotated[int, Query(ge=1, le=2000)] = 500):
    """`open=true` (mặc định): chỉ nợ chưa đóng; `open=false`: cả nợ đã đóng (kèm closed_reason). `truncated` = còn nữa sau `limit`."""
    project_or_404(state, slug)
    try:
        rows = repo.list_debt(session, slug, open_only=open, kind=kind, limit=limit + 1)
    except repo.ProjectNotFound:
        raise HTTPException(status_code=404, detail=f"không có project {slug!r}") from None
    return {"items": [debt_json(r) for r in rows[:limit]], "open": open, "limit": limit, "truncated": len(rows) > limit}
