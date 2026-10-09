"""`pytest -q` ở gốc repo phải thu thập được mọi test mà không lỗi (DoD điều kiện chung "pytest -q xanh"). Dataset của pruner (tests/fixtures/selector-datasets/*) chứa file
`test_*.py` của SUT giả; chúng không được bị nhặt (S4-07: `routes` không import được làm cả lượt chạy dừng ở collection)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_whole_suite_collects_without_errors_and_skips_the_dataset_fixtures():
    done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    assert done.returncode == 0, (done.stdout + done.stderr)[-1500:]
    assert "selector-datasets" not in done.stdout and "error" not in done.stdout.splitlines()[-1].lower()
    assert "tests/test_collection.py" in done.stdout.replace("\\", "/") and "tests/test_runner.py" in done.stdout.replace("\\", "/")
