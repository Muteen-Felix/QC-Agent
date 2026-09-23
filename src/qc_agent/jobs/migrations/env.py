from alembic import context
from sqlalchemy import create_engine, pool

from qc_agent.jobs.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url")
    if not url:
        from qc_agent import settings
        from qc_agent.jobs.db import normalize_url
        url = normalize_url(settings.get().database_url)
    if not url:
        raise RuntimeError("chưa cấu hình QC_DATABASE_URL")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
