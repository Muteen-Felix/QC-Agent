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
  QC_GT_MODEL / QC_SELECTOR_MODEL  model cho GT generator (mặc định claude-sonnet-5) và Diff Agent (claude-haiku-4-5-20251001); `gemini-*` chọn provider Gemini
  QC_LLM_TIMEOUT_S timeout một lời gọi LLM, giây (mặc định 120)
  QC_LLM_MAX_RETRIES / QC_LLM_MIN_INTERVAL_S / QC_LLM_FALLBACK_MODELS  CHỈ Gemini: số lần thử lại khi 429/5xx (5), giãn cách tối thiểu giữa hai request (0 = tắt),
                   model dự phòng cách nhau dấu phẩy. QC_GEMINI_THINKING_LEVEL (tuỳ chọn: minimal|low|medium|high) đặt mức thinking của Gemini 3.
  ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / GEMINI_API_KEY / GEMINI_BASE_URL  KHÔNG nằm ở đây: llm/client.py đọc bằng os.environ lúc gọi (repr của pydantic có thể lộ khoá)

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
    gt_model: str = "claude-sonnet-5"
    selector_model: str = "claude-haiku-4-5-20251001"
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 5
    llm_min_interval_s: float = 0.0
    llm_fallback_models: str = ""
    gemini_thinking_level: str = ""
    # Ground-Truth agent (đọc repo SUT, nhiều lượt, chỉ Claude). `single` = bộ sinh một lời gọi như cũ. Xem llm/agent_loop.py và groundtruth/agent.py.
    gt_generator: str = "single"
    gt_agent_model: str = "claude-sonnet-5-5"
    gt_agent_effort: str = "high"
    gt_agent_max_turns: int = 40
    gt_agent_max_cost_usd: float = 3.0
    gt_agent_max_wall_s: float = 1800.0
    gt_agent_max_read_bytes: int = 3_000_000
    gt_agent_timeout_s: float = 600.0
    gt_agent_fallbacks: bool = True
    gt_xlsx: bool = True   # `gt generate|regen` ghi thêm test-cases.xlsx cho QA (xlsx.py); QC_GT_XLSX=false hoặc --no-xlsx để tắt

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
