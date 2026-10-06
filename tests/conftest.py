"""Manifest của worker giả (mock, mock2) nằm trong tests/fixtures/workers, không nằm trong workers/ của sản phẩm."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")   # import deepeval không được nạp .env của máy dev
ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
sys.path.insert(0, str(SUT))  # `import toyapp` (SUT tham chiếu) trong test


@pytest.fixture(autouse=True)
def _utf8_children(monkeypatch):
    """Windows: tiến trình con in tiếng Việt theo cp1252 nếu không ép UTF-8 (giống core/runner.py và adapters/_base.py)."""
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")


@pytest.fixture(autouse=True)
def _fixture_workers(monkeypatch):
    monkeypatch.setenv("QC_WORKERS_PATH", os.pathsep.join([str(ROOT / "workers"), str(ROOT / "tests" / "fixtures" / "workers")]))


@pytest.fixture(autouse=True)
def _no_llm_cache(monkeypatch):
    """Cache LLM (S4-02) mặc định ghi vào ~/.cache của máy dev: test lặp cùng đầu vào giả sẽ chép kết quả của test khác hoặc của lần chạy hôm qua. Tắt mặc định;
    test cache tự bật bằng thư mục tạm (monkeypatch.setenv lại QC_SELECT_CACHE_DIR / QC_GT_CACHE_DIR)."""
    monkeypatch.setenv("QC_SELECT_CACHE_DIR", "none")
    monkeypatch.setenv("QC_GT_CACHE_DIR", "none")


@pytest.fixture(autouse=True)
def _repo_on_pythonpath(monkeypatch):
    """Worker giả (tests.fixtures.workers.*) được spawn bằng `python -m`: cần gốc repo trên PYTHONPATH kể cả khi cwd là SUT root."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in [str(ROOT), os.environ.get("PYTHONPATH", "")] if p))


@pytest.fixture(autouse=True)
def _no_stale_log_handler():
    """`logging_setup.configure()` gắn handler vào stderr/StringIO của test; test sau ghi log vào luồng đã đóng ("Logging error: I/O operation on closed file")."""
    yield
    import logging
    from qc_agent import logging_setup
    root = logging.getLogger("qc_agent")
    for handler in list(root.handlers):
        if getattr(handler, logging_setup._MARK, False):
            root.removeHandler(handler)
