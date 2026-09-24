"""Structured JSON logging: mỗi bản ghi là MỘT dòng JSON trên stderr, có ngữ cảnh job_id / task_id / worker / project.

    2026-09-24T07:50:01.123Z {"ts": "...", "level": "INFO", "logger": "qc_agent.runner", "event": "task.end",
                              "project": "noteboard", "job_id": "…", "run_id": "…", "task_id": "t-001", "worker": "k6", "status": "pass"}

  jq:   qc-agent run … 2>&1 >/dev/null | jq 'select(.event=="task.end") | {task_id, status}'
        executor.log (stdout+stderr trộn với báo cáo Markdown):  jq -R 'fromjson? | select(.event)' executor.log

Cấu hình bằng môi trường: QC_LOG_FORMAT=json (mặc định) | text ; QC_LOG_LEVEL=INFO (mặc định). Ngữ cảnh ngầm định qua contextvars (`bind`);
tiến trình con nhận QC_JOB_ID / QC_PROJECT từ executor nên log của cả cây tiến trình cùng khoá được với một job.
KHÔNG bao giờ log nội dung spec/inputs, secret, hay giá trị biến môi trường: chỉ định danh, trạng thái và lý do ngắn (cắt 200 ký tự).
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sys
import time

_CONTEXT: contextvars.ContextVar[dict] = contextvars.ContextVar("qc_log_context", default={})
_MARK = "_qc_handler"
FIELDS_ATTR = "qc_fields"
DETAIL_MAX = 200
_RESERVED = {"ts", "level", "logger", "event"}


@contextlib.contextmanager
def bind(**fields):
    """Gắn ngữ cảnh cho mọi bản ghi phát ra trong khối (lồng nhau thì gộp; giá trị None bị bỏ)."""
    token = _CONTEXT.set({**_CONTEXT.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def event(logger: logging.Logger, name: str, level: int = logging.INFO, /, **fields) -> None:  # positional-only: tên trường tự do
    logger.log(level, name, extra={FIELDS_ATTR: {k: v for k, v in fields.items() if v is not None}})


def short(value, limit: int = DETAIL_MAX) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for source in (_CONTEXT.get(), getattr(record, FIELDS_ATTR, None) or {}):
            for key, value in source.items():
                entry[key if key not in _RESERVED else f"field_{key}"] = value
        if record.exc_info:
            entry["exc"] = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"  # không kèm traceback: có thể chứa dữ liệu
        return json.dumps(entry, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = {**_CONTEXT.get(), **(getattr(record, FIELDS_ATTR, None) or {})}
        tail = " ".join(f"{k}={v}" for k, v in extra.items())
        return f"{time.strftime('%H:%M:%S', time.gmtime(record.created))} {record.levelname:<5} {record.name} {record.getMessage()} {tail}".rstrip()


def configure(stream=None) -> None:
    """Idempotent. Gọi ở điểm vào của tiến trình (CLI, executor, API). Ngữ cảnh môi trường (QC_JOB_ID, QC_PROJECT) được gắn ngầm định."""
    root = logging.getLogger("qc_agent")
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, _MARK, True)
    handler.setFormatter(TextFormatter() if os.environ.get("QC_LOG_FORMAT", "json").lower() == "text" else JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, os.environ.get("QC_LOG_LEVEL", "INFO").upper(), logging.INFO))
    root.propagate = False  # không để root logger của uvicorn/pytest in trùng
    ambient = {"job_id": os.environ.get("QC_JOB_ID"), "project": os.environ.get("QC_PROJECT")}
    _CONTEXT.set({k: v for k, v in ambient.items() if v})
