"""Unit tests for STEP 48 result classification; live workers run via the script itself."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("run_mutants", ROOT / "tools" / "run_mutants.py")
run_mutants = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = run_mutants
spec.loader.exec_module(run_mutants)


def _mutant(key: str):
    return next(item for item in run_mutants.MUTANTS if item.key == key)


def test_assessment_distinguishes_clean_failure_and_worker_error():
    assert run_mutants._assessment(_mutant("M0"), {"gate_verdict": "PASS"}, {"t-001": {"status": "pass"}}, True) == "XANH"
    assert run_mutants._assessment(_mutant("M0"), {"gate_verdict": "FAIL"}, {"t-001": {"status": "pass"}}, True) == "ĐỎ"
    assert run_mutants._assessment(_mutant("M1"), {"gate_verdict": "FAIL"}, {"t-001": {"status": "fail"}}, True) == "BẮT"
    assert run_mutants._assessment(_mutant("M3"), {"gate_verdict": "FAIL"}, {"t-003": {"status": "pass"}}, True) == "LỌT"
    assert run_mutants._assessment(_mutant("M5"), {"gate_verdict": "FAIL"}, {"t-002": {"status": "error"}}, True) == "BẮT"


def test_rendered_document_has_exactly_five_columns(tmp_path):
    text = run_mutants._render_document([["M0", "—", "clean", "gate", "XANH"]], tmp_path)
    header = next(line for line in text.splitlines() if line.startswith("| Mutant |"))
    assert header.count("|") == 6
    assert "M0" in text and "XANH" in text


def test_venv_bin_is_kept_before_path_for_worker_probes():
    env = {"PATH": "/usr/bin"}
    run_mutants._prepend_python_to_path(env)
    assert env["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).parent)
