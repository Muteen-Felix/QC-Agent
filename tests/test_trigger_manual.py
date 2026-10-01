import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUT = ROOT / "tests/fixtures/sut/noteboard"


def test_manual_runs_only_requested_workers_without_llm(tmp_path):
    script = """
import socket, sys
def blocked(*args, **kwargs):
    raise OSError('network blocked by test')
socket.socket.connect = blocked
from qc_agent.core.cli import main
code = main(sys.argv[1:])
assert 'qc_agent.llm' not in sys.modules
assert 'qc_agent.selector.agent' not in sys.modules
raise SystemExit(code)
"""
    command = [sys.executable, "-c", script, "run", "--project", "noteboard", "--mode", "pr",
               "--trigger", "manual", "--workers", "semgrep,schemathesis", "--sut-root", str(SUT),
               "--runs-dir", str(tmp_path / "runs")]
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env={**os.environ, "APP_BASE_URL": "http://127.0.0.1:1"}, timeout=60)
    assert process.returncode in (0, 1), process.stderr
    run_dir = next((tmp_path / "runs").glob("r-*/"))
    assert {p.stem for p in (run_dir / "results").glob("*.json")} == {"t-001", "t-010"}
    selected = json.loads((run_dir / "selection.json").read_text(encoding="utf-8"))
    assert selected["floor"] == [] and selected["suites"] == ["api-contract", "sast"]
