"""Body của request (Pydantic). Giới hạn độ dài ở biên để chặn input rác/khổng lồ."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(_Strict):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class PasswordIn(_Strict):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class JobIn(_Strict):
    mode: str = Field(min_length=1, max_length=64)
    suites: list[str] | None = Field(default=None, max_length=50)
    task_ids: list[str] | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=64)
    timeout_s: float | None = Field(default=None, gt=0)


class RunIn(_Strict):
    external_id: str = Field(min_length=1, max_length=128, pattern=_NAME)
    mode: str = Field(min_length=1, max_length=64)
    pr_number: int | None = Field(default=None, ge=0)
    sha: str | None = Field(default=None, max_length=64)
    branch: str | None = Field(default=None, max_length=255)
    report: dict
    report_md: str | None = Field(default=None, max_length=2_000_000)
