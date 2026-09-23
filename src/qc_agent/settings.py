"""Cấu hình một nguồn cho qc-agent (env `QC_*`). Đọc lúc gọi (không cache) để test/CLI đổi được bằng env.

  QC_RUNS_DIR      thư mục ghi run (mặc định `runs`)
  QC_WORKERS_PATH  các thư mục manifest worker, ngăn cách bằng os.pathsep (mặc định: `workers/` của repo, hoặc bản đóng gói trong wheel)
  QC_PROJECTS_DIR  thư mục cấu hình project (`<slug>.yaml`; mặc định `configs/projects/`)
  QC_DATABASE_URL  PostgreSQL của service (postgresql://user:pass@host:5432/db); CLI chạy cục bộ không cần
  QC_SCHEMAS_DIR   thư mục chứa task_spec.json / result.json / capabilities.json (mặc định như trên)

Chạy từ source (thư mục có pyproject.toml + schemas/): gốc repo là project root.
Chạy từ wheel: contract được đóng gói vào qc_agent/schemas và qc_agent/workers (hatch force-include), root = cwd.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PKG = Path(__file__).resolve().parent
_SOURCE_ROOT = _PKG.parent.parent  # src/qc_agent/settings.py -> gốc repo (chỉ đúng khi chạy từ source)


def _is_source_checkout() -> bool:
    return (_SOURCE_ROOT / "pyproject.toml").is_file() and (_SOURCE_ROOT / "schemas").is_dir()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QC_", extra="ignore")

    runs_dir: Path = Path("runs")
    workers_path: str = ""
    schemas_dir: Path | None = None
    projects_dir: Path | None = None
    database_url: str = ""

    @property
    def project_root(self) -> Path:
        return _SOURCE_ROOT if _is_source_checkout() else Path.cwd()

    @property
    def resolved_schemas_dir(self) -> Path:
        if self.schemas_dir:
            return self.schemas_dir
        return _SOURCE_ROOT / "schemas" if _is_source_checkout() else _PKG / "schemas"

    @property
    def resolved_projects_dir(self) -> Path:
        if self.projects_dir:
            return self.projects_dir
        return _SOURCE_ROOT / "configs" / "projects" if _is_source_checkout() else _PKG / "configs" / "projects"

    @property
    def workers_dirs(self) -> list[Path]:
        if self.workers_path.strip():
            return [Path(p) for p in self.workers_path.split(os.pathsep) if p.strip()]
        return [_SOURCE_ROOT / "workers" if _is_source_checkout() else _PKG / "workers"]


def get() -> Settings:
    return Settings()
