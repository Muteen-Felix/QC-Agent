"""Manifest của worker giả (mock, mock2) nằm trong tests/fixtures/workers, không nằm trong workers/ của sản phẩm."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
sys.path.insert(0, str(SUT))  # `import toyapp` (SUT tham chiếu) trong test


@pytest.fixture(autouse=True)
def _fixture_workers(monkeypatch):
    monkeypatch.setenv("QC_WORKERS_PATH", os.pathsep.join([str(ROOT / "workers"), str(ROOT / "tests" / "fixtures" / "workers")]))


@pytest.fixture(autouse=True)
def _repo_on_pythonpath(monkeypatch):
    """Worker giả (tests.fixtures.workers.*) được spawn bằng `python -m`: cần gốc repo trên PYTHONPATH kể cả khi cwd là SUT root."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in [str(ROOT), os.environ.get("PYTHONPATH", "")] if p))
