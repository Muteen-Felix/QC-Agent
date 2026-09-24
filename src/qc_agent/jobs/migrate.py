"""Chạy Alembic bằng code (dùng được từ service, test, CLI):
    python -m qc_agent.jobs.migrate upgrade|downgrade [revision]   (URL từ $QC_DATABASE_URL)"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from qc_agent import settings
from qc_agent.jobs.db import normalize_url

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def _config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.attributes["url"] = normalize_url(url)
    return cfg


def head_revision() -> str:
    """Revision mới nhất trong thư mục migration (so với alembic_version của DB để biết service đã migrate chưa)."""
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(_config("postgresql://x/x")).get_current_head()


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("upgrade", "downgrade"):
        print(__doc__, file=sys.stderr)
        return 2
    url = settings.get().database_url
    if not url:
        print("chưa cấu hình QC_DATABASE_URL", file=sys.stderr)
        return 2
    (upgrade if argv[0] == "upgrade" else downgrade)(url, *argv[1:2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
