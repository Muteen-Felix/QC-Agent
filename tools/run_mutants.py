"""Run the reduced STEP 48 seeded-fault mutation suite.

The suite intentionally runs only M0/M1/M3/M5.  It starts an isolated toy
SUT for every case, creates a small derived plan, and never edits plan.yaml.
Exit 0 means the clean control is green and every seeded fault was caught.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import yaml


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAN = ROOT / "plan.yaml"
DEFAULT_RUNS_ROOT = ROOT / "runs" / "mutants"
DOCUMENT = ROOT / "docs" / "mutants.md"


@dataclass(frozen=True)
class Mutant:
    key: str
    task_ids: tuple[str, ...]
    bugs: str
    latency_ms: int
    description: str
    detector: str
    marker_file: str | None = None
    marker: str | None = None
    broken_k6: bool = False


MUTANTS = (
    Mutant(
        "M0",
        ("t-000", "t-001", "t-002", "t-003"),
        "none",
        0,
        "đối chứng sạch: không bật lỗi cài sẵn",
        "gate phải PASS (không false positive)",
    ),
    Mutant(
        "M1",
        ("t-001",),
        "1",
        0,
        "BUG-1: id dài trả HTTP 500 thay vì 4xx",
        "t-001 Schemathesis",
        "toyapp/app.py",
        "len(note_id) > LONG_ID_LEN",
    ),
    Mutant(
        "M3",
        ("t-000", "t-003"),
        "3",
        0,
        "BUG-3: summary dài hơn input khi body có [[long]]",
        "t-003 summary_shorter_than_body",
        "toyapp/summarizer.py",
        "[[long]]",
    ),
    Mutant(
        "M5",
        ("t-002",),
        "none",
        0,
        "worker k6 crash vì script JavaScript không hợp lệ",
        "t-002 phải là status=error, không phải fail",
        "tests/perf/broken.js",
        "this is not javascript",
        broken_k6=True,
    ),
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _line_label(path: Path, marker: str) -> str:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if marker in line:
            return f"{path.relative_to(ROOT).as_posix()}:{number}"
    raise ValueError(f"không tìm thấy marker {marker!r} trong {path}")


def _display_path(path: Path) -> str:
    """Return a project-relative path when possible, otherwise keep it absolute."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _source_label(mutant: Mutant) -> str:
    if not mutant.marker_file or not mutant.marker:
        return "—"
    return _line_label(ROOT / mutant.marker_file, mutant.marker)


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError(f"{path}: plan phải có tasks[]")
    return data


def _derived_plan(source: Path, mutant: Mutant, base_url: str, run_root: Path) -> dict:
    plan = copy.deepcopy(_load_yaml(source))
    selected = {task_id for task_id in mutant.task_ids}
    tasks = [task for task in plan["tasks"] if task.get("task_id") in selected]
    if {task.get("task_id") for task in tasks} != selected:
        raise ValueError(f"{mutant.key}: plan thiếu task cần chạy")

    plan["name"] = f"{plan.get('name', 'poc')}-mutant-{mutant.key.lower()}"
    plan["tasks"] = tasks
    plan.pop("selection", None)
    plan["sut"] = copy.deepcopy(plan["sut"])
    plan["sut"]["probe_url"] = base_url + "/__qc/config"

    for task in tasks:
        target = task.get("target", {})
        if isinstance(target, dict):
            target["base_url"] = base_url
            if "spec_ref" in target:
                target["spec_ref"] = base_url + "/openapi.json"
        inputs = task.get("inputs", {})
        if isinstance(inputs, dict) and "schema_url" in inputs:
            inputs["schema_url"] = base_url + "/openapi.json"
        if task.get("task_id") == "t-003":
            inputs["outputs_path"] = str(run_root / "${run_id}" / "t-000" / "outputs.json")
        if mutant.broken_k6 and task.get("task_id") == "t-002":
            inputs["script"] = "tests/perf/broken.js"
    return plan


def _write_yaml(path: Path, plan: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _wait_for_sut(base_url: str, log_path: Path, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            with urlopen(base_url + "/__qc/config", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            time.sleep(0.2)
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-1000:] if log_path.exists() else ""
    raise RuntimeError(f"toy SUT không khởi động được: {detail}")


def _stop_sut(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _latest_run(run_root: Path) -> Path:
    candidates = sorted(path for path in run_root.glob("r-*") if path.is_dir())
    if not candidates:
        raise RuntimeError(f"{run_root}: orchestrator không tạo run")
    return candidates[-1]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _assessment(mutant: Mutant, report: dict, results: dict[str, dict], valid: bool) -> str:
    gate = report.get("gate_verdict")
    if mutant.key == "M0":
        green = gate == "PASS" and valid and all(item.get("status") == "pass" for item in results.values())
        return "XANH" if green else "ĐỎ"
    task_id = {"M1": "t-001", "M3": "t-003", "M5": "t-002"}[mutant.key]
    status = results.get(task_id, {}).get("status")
    expected_status = "error" if mutant.key == "M5" else "fail"
    return "BẮT" if gate == "FAIL" and status == expected_status and valid else "LỌT"


def _result_row(mutant: Mutant, outcome: str) -> list[str]:
    return [mutant.key, _source_label(mutant), mutant.description, mutant.detector, outcome]


def _render_document(rows: list[list[str]], session_root: Path) -> str:
    lines = [
        "# Seeded-fault mutation results",
        "",
        "STEP 48 reduced scope: M0/M1/M3/M5. M2 needs Midscene and M4/M6 were cut from this sprint scope.",
        f"Run root: `{_display_path(session_root)}`",
        "",
        "| Mutant | file/dòng | loại lỗi mô phỏng | test nào phải bắt | kết quả thực tế |",
        "|---|---|---|---|---|",
    ]
    lines.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _prepend_python_to_path(env: dict[str, str]) -> None:
    # Do not resolve the venv's ``python`` symlink: resolving it points to the
    # system interpreter directory and makes manifest probes miss ``st`` and
    # ``deepeval`` installed beside the venv executable.
    bin_dir = str(Path(sys.executable).parent)
    current = env.get("PATH", "")
    env["PATH"] = bin_dir + (os.pathsep + current if current else "")


def run_mutant(mutant: Mutant, source_plan: Path, session_root: Path) -> tuple[list[str], Path]:
    mutant_root = session_root / mutant.key
    run_root = mutant_root / "runs"
    base_url = f"http://127.0.0.1:{_free_port()}"
    plan_path = mutant_root / "plan.yaml"
    _write_yaml(plan_path, _derived_plan(source_plan, mutant, base_url, run_root))

    env = dict(os.environ)
    _prepend_python_to_path(env)
    env.update(
        APP_BASE_URL=base_url,
        QC_BUGS=mutant.bugs,
        QC_LATENCY_MS=str(mutant.latency_ms),
        QC_LONG_ID_LEN="16",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    mutant_root.mkdir(parents=True, exist_ok=True)
    server_log = mutant_root / "toyapp.log"
    with server_log.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "toyapp.app:app", "--host", "127.0.0.1", "--port", base_url.rsplit(":", 1)[1]],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_for_sut(base_url, server_log, server)
        completed = subprocess.run(
            [sys.executable, "orchestrator.py", "--plan", str(plan_path), "--runs-dir", str(run_root)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=600,
        )
        (mutant_root / "orchestrator.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (mutant_root / "orchestrator.stderr.log").write_text(completed.stderr, encoding="utf-8")
    finally:
        _stop_sut(server)

    run_dir = _latest_run(run_root)
    result_paths = sorted((run_dir / "results").glob("*.json"))
    if not result_paths:
        raise RuntimeError(f"{mutant.key}: không có results/*.json")
    validation = subprocess.run(
        [sys.executable, "tools/validate.py", "result", *(str(path) for path in result_paths)],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    (mutant_root / "validate.stdout.log").write_text(validation.stdout, encoding="utf-8")
    (mutant_root / "validate.stderr.log").write_text(validation.stderr, encoding="utf-8")
    report = _read_json(run_dir / "report.json")
    results = {path.stem: _read_json(path) for path in result_paths}
    return _result_row(mutant, _assessment(mutant, report, results, validation.returncode == 0)), run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="STEP 48 reduced seeded-fault mutation suite (M0/M1/M3/M5).")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="plan nguồn, mặc định plan.yaml")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_ROOT, help="gốc evidence, mặc định runs/mutants")
    args = parser.parse_args(argv)
    source_plan = args.plan.resolve()
    if not source_plan.is_file():
        parser.error(f"không có plan: {source_plan}")

    session_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_root = args.runs_dir.resolve() / session_name
    rows: list[list[str]] = []
    failed = False
    for mutant in MUTANTS:
        try:
            row, run_dir = run_mutant(mutant, source_plan, session_root)
            print(f"{mutant.key}: {row[-1]} ({run_dir.relative_to(ROOT)})")
        except Exception as error:  # preserve partial evidence/table when infrastructure itself breaks
            row = _result_row(mutant, "ĐỎ" if mutant.key == "M0" else "LỌT")
            row[-1] += f" — lỗi script: {type(error).__name__}"
            print(f"{mutant.key}: {row[-1]}", file=sys.stderr)
        rows.append(row)
        failed = failed or row[-1].split(" ", 1)[0] not in ({"XANH"} if mutant.key == "M0" else {"BẮT"})

    document = _render_document(rows, session_root)
    DOCUMENT.write_text(document, encoding="utf-8")
    print(document, end="")
    print(f"wrote {DOCUMENT.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
