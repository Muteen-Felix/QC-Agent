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
def _repo_on_pythonpath(monkeypatch):
    """Worker giả (tests.fixtures.workers.*) được spawn bằng `python -m`: cần gốc repo trên PYTHONPATH kể cả khi cwd là SUT root."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in [str(ROOT), os.environ.get("PYTHONPATH", "")] if p))
