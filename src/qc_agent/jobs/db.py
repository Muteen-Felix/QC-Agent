"""Kết nối PostgreSQL (SQLAlchemy 2 + psycopg 3)."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from qc_agent import settings


def normalize_url(url: str) -> str:
    """`postgresql://` (dạng phổ biến) -> driver psycopg 3 tường minh."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str | None = None, **kw) -> Engine:
    url = url or settings.get().database_url
    if not url:
        raise RuntimeError("chưa cấu hình QC_DATABASE_URL")
    return create_engine(normalize_url(url), pool_pre_ping=True, **kw)


@contextmanager
def session_scope(engine: Engine):
    """Một transaction: commit khi thoát êm, rollback khi có ngoại lệ."""
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
