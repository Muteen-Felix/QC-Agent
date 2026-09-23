"""Cấu hình một nguồn cho qc-agent (env `QC_*`). Đọc lúc gọi (không cache) để test/CLI đổi được bằng env.

  QC_RUNS_DIR      thư mục ghi run (mặc định `runs`)
  QC_WORKERS_PATH  các thư mục manifest worker, ngăn cách bằng os.pathsep (mặc định: `workers/` của repo, hoặc bản đóng gói trong wheel)
  QC_PROJECTS_DIR  thư mục cấu hình project (`<slug>.yaml`; mặc định `configs/projects/`)
  QC_DATABASE_URL  PostgreSQL của service (postgresql://user:pass@host:5432/db); CLI chạy cục bộ không cần
  QC_ALLOWED_EMAIL_DOMAINS  domain email được phép có tài khoản, cách nhau dấu phẩy (để trống = KHÔNG AI được thêm: fail-closed)
  QC_SESSION_TTL_HOURS      thời hạn phiên đăng nhập (mặc định 168)
  QC_LOGIN_MAX_FAILURES / QC_LOGIN_LOCKOUT_MINUTES  khoá tài khoản sau N lần sai liên tiếp (5 lần / 15 phút)
  QC_COOKIE_SECURE          cookie phiên chỉ gửi qua HTTPS (mặc định true; đặt false khi dev bằng http)
  QC_MAX_OPEN_JOBS_PER_PROJECT  giới hạn job queued+running của một project (mặc định 20)
  QC_MAX_JOB_TIMEOUT_S      trần timeout người dùng được xin (mặc định 86400)
  QC_MAX_INGEST_BYTES       kích thước tối đa một report CI đẩy lên (mặc định 5 MB)
  QC_WEB_DIR                thư mục giao diện tĩnh (mặc định `web/`, hoặc bản đóng gói trong wheel)
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
    web_dir: Path | None = None
    allowed_email_domains: str = ""
    session_ttl_hours: int = 168
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    cookie_secure: bool = True
    max_open_jobs_per_project: int = 20
    max_job_timeout_s: float = 86400
    max_ingest_bytes: int = 5 * 1024 * 1024

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
    def resolved_web_dir(self) -> Path:
        if self.web_dir:
            return self.web_dir
        return _SOURCE_ROOT / "web" if _is_source_checkout() else _PKG / "web"

    @property
    def workers_dirs(self) -> list[Path]:
        if self.workers_path.strip():
            return [Path(p) for p in self.workers_path.split(os.pathsep) if p.strip()]
        return [_SOURCE_ROOT / "workers" if _is_source_checkout() else _PKG / "workers"]


def get() -> Settings:
    return Settings()
