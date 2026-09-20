import subprocess
import sys


def test_contract_files_match_lock():
    """Ai sửa schemas/task_spec.json, schemas/result.json hoặc workers/_template.yaml sau STEP 07 mà không
    chạy `freeze_contract.py --write` (tức là không có sự đồng ý của cả ba) sẽ làm test này đỏ."""
    r = subprocess.run([sys.executable, "tools/freeze_contract.py", "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout
