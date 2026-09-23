"""Manifest của worker giả (mock, mock2) nằm trong tests/fixtures/workers, không nằm trong workers/ của sản phẩm."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fixture_workers(monkeypatch):
    monkeypatch.setenv("QC_WORKERS_PATH", os.pathsep.join([str(ROOT / "workers"), str(ROOT / "tests" / "fixtures" / "workers")]))
