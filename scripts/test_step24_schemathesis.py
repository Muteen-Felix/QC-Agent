#!/usr/bin/env python3
"""Run the STEP 24 Schemathesis calibration on an isolated local toyapp port."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
DEFAULT_OUTPUT_DIR = ROOT / "runs" / "step24"
DEFAULT_SAMPLES_DIR = ROOT / "tests" / "samples"
CHECKS = "not_a_server_error,response_schema_conformance"
EXCLUDED_PATH = "/notes/{note_id}/summarize"
TARGET_TESTCASE = "GET /notes/{note_id}"


def _free_port(requested: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((HOST, requested))
        except OSError as error:
            raise RuntimeError(f"port {requested} đang được sử dụng; không dừng tiến trình khác") from error
        return int(listener.getsockname()[1])


def _st_command(configured: str | None) -> str:
    candidates = [configured] if configured else []
    if not configured:
        candidates.extend(["st", str(ROOT / ".venv" / "bin" / "st")])
        if os.name == "nt":
            candidates.append(str(ROOT / ".venv" / "Scripts" / "st.exe"))
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
    raise RuntimeError("không tìm thấy Schemathesis CLI `st`; hãy kích hoạt .venv hoặc truyền --st-bin")


def _wait_for_server(process: subprocess.Popen, schema_url: str, log_path: Path) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"toyapp dừng khi khởi động (exit={process.returncode}):\n{detail[-3000:]}")
        try:
            with urlopen(schema_url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as error:
            last_error = error
        time.sleep(0.2)
    raise RuntimeError(f"toyapp không sẵn sàng sau 20 giây: {last_error}")


def _start_server(bugs: str, port: int, log_path: Path, long_id_len: int | None) -> subprocess.Popen:
    env = dict(os.environ)
    env["QC_BUGS"] = bugs
    env["QC_LATENCY_MS"] = "0"
    if long_id_len is None:
        env.pop("QC_LONG_ID_LEN", None)
    else:
        env["QC_LONG_ID_LEN"] = str(long_id_len)
    log_file = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "toyapp.app:app",
                "--host", HOST, "--port", str(port), "--log-level", "warning",
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except BaseException:
        log_file.close()
        raise
    process._step24_log_file = log_file  # type: ignore[attr-defined]
    try:
        _wait_for_server(process, f"http://{HOST}:{port}/openapi.json", log_path)
    except BaseException:
        _stop_server(process)
        raise
    return process


def _stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log_file = getattr(process, "_step24_log_file", None)
    if log_file:
        log_file.close()


def _run_st(command: list[str], timeout: int, cwd: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        detail = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
        error_output = error.stderr or ""
        error_detail = error_output.decode("utf-8", errors="replace") if isinstance(error_output, bytes) else error_output
        return 124, detail + error_detail + f"\nSchemathesis timeout sau {timeout} giây.\n"


def _junit_report(
    report_path: Path,
) -> tuple[bool, list[str], list[tuple[str, str]], list[tuple[str, str]]]:
    try:
        root = ET.parse(report_path).getroot()
    except (OSError, ET.ParseError):
        return False, [], [], []
    testcases = [testcase.attrib.get("name", "") for testcase in root.iter("testcase")]
    failures: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    for testcase in root.iter("testcase"):
        for failure in testcase.findall("failure"):
            failures.append((testcase.attrib.get("name", ""), "".join(failure.itertext())))
        for error in testcase.findall("error"):
            errors.append((testcase.attrib.get("name", ""), "".join(error.itertext())))
    return True, testcases, failures, errors


def _sample_text(command: list[str], output: str, exit_code: int) -> str:
    normalized_output = "\n".join(line.rstrip() for line in output.rstrip().splitlines())
    return (
        "Command argv:\n"
        + json.dumps(command, ensure_ascii=False, indent=2)
        + f"\n\nExit code: {exit_code}\n\n"
        + normalized_output
        + "\n"
    )


def _run_case(
    st_bin: str,
    label: str,
    index: int,
    bugs: str,
    args: argparse.Namespace,
) -> dict:
    port = _free_port(args.port)
    case_dir = args.output_dir / label
    case_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{label}_{index:02d}"
    log_path = case_dir / f"{stem}.server.log"
    report_path = case_dir / f"{stem}.junit.xml"
    report_path.unlink(missing_ok=True)
    process = _start_server(bugs, port, log_path, args.long_id_len)
    base_url = f"http://{HOST}:{port}"
    url = f"{base_url}/openapi.json"
    report_argument = str(report_path.resolve())
    command = [
        st_bin, "run", url,
        "--checks", CHECKS,
        "--max-examples", str(args.max_examples),
        "--seed", str(args.seed),
        "--exclude-path", EXCLUDED_PATH,
        "--generation-database", "none",
        "--report", "junit",
        "--report-junit-path", report_argument,
        "--no-color",
    ]
    try:
        with urlopen(f"{base_url}/__qc/config", timeout=3) as response:
            runtime_config = json.loads(response.read().decode("utf-8"))
        exit_code, output = _run_st(command, args.timeout, args.output_dir)
    finally:
        _stop_server(process)

    report_valid, testcases, report_failures, report_errors = _junit_report(report_path)
    target_failed_report = any(
        name == TARGET_TESTCASE and "500" in detail and "Server error" in detail
        for name, detail in [*report_failures, *report_errors]
    )
    target_failed_output = TARGET_TESTCASE in output and "[500] Internal Server Error" in output
    if label == "bug_on":
        passed = exit_code != 0 and report_valid and target_failed_report and target_failed_output
    else:
        passed = (
            exit_code == 0 and report_valid and TARGET_TESTCASE in testcases
            and not report_failures and not report_errors
        )
    return {
        "label": label,
        "index": index,
        "port": port,
        "runtime_config": runtime_config,
        "command": command,
        "exit_code": exit_code,
        "output": output,
        "report": str(report_path),
        "report_exists": report_valid,
        "junit_testcases": len(testcases),
        "junit_failures": len(report_failures),
        "junit_errors": len(report_errors),
        "target_get_500_report": target_failed_report,
        "target_get_500_output": target_failed_output,
        "passed": passed,
    }


def _run_server_down(st_bin: str, args: argparse.Namespace) -> dict:
    port = _free_port(args.port)
    report_path = args.output_dir / "server_down" / "st_server_down.junit.xml"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)
    report_argument = str(report_path.resolve())
    command = [
        st_bin, "run", f"http://{HOST}:{port}/openapi.json",
        "--checks", CHECKS,
        "--max-examples", str(args.max_examples),
        "--seed", str(args.seed),
        "--exclude-path", EXCLUDED_PATH,
        "--generation-database", "none",
        "--report", "junit",
        "--report-junit-path", report_argument,
        "--no-color",
    ]
    exit_code, output = _run_st(command, args.timeout, args.output_dir)
    lower_output = output.lower()
    expected_connection_error = (
        "failed to load specification" in lower_output
        and any(text in lower_output for text in ("connection refused", "actively refused", "failed to establish a new connection"))
    )
    report_valid, testcases, report_failures, report_errors = _junit_report(report_path)
    return {
        "label": "server_down",
        "port": port,
        "command": command,
        "exit_code": exit_code,
        "output": output,
        "report": str(report_path),
        "report_exists": report_valid,
        "junit_testcases": len(testcases),
        "junit_failures": len(report_failures),
        "junit_errors": len(report_errors),
        "connection_refused": expected_connection_error,
        "passed": (
            exit_code != 0 and expected_connection_error and report_valid
            and not testcases and not report_failures and not report_errors
        ),
    }


def _args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="runs cho mỗi cấu hình bug_on/bug_off (mặc định: 5)")
    parser.add_argument("--port", type=int, default=0, help="port cố định; mặc định chọn port trống riêng cho từng run")
    parser.add_argument("--long-id-len", type=int, default=None, help="ghi đè QC_LONG_ID_LEN; mặc định dùng giá trị trong toyapp")
    parser.add_argument("--max-examples", type=int, default=25)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--timeout", type=int, default=120, help="timeout mỗi lệnh Schemathesis, tính bằng giây")
    parser.add_argument("--st-bin", help="đường dẫn CLI st; mặc định tìm trong PATH rồi .venv/bin")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    args = parser.parse_args(argv)
    if args.runs < 1 or args.port < 0 or args.max_examples < 1 or args.timeout < 1:
        parser.error("runs, max-examples và timeout phải > 0; port phải >= 0")
    if args.long_id_len is not None and args.long_id_len < 1:
        parser.error("long-id-len phải > 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    try:
        st_bin = _st_command(args.st_bin)
        version = subprocess.run(
            [st_bin, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
        )
        if version.returncode != 0:
            raise RuntimeError(f"`st --version` thất bại: {version.stderr.strip()}")
        args.output_dir = args.output_dir.resolve()
        args.samples_dir = args.samples_dir.resolve()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        args.samples_dir.mkdir(parents=True, exist_ok=True)

        results = {"bug_on": [], "bug_off": []}
        for label, bugs in (("bug_on", "1"), ("bug_off", "none")):
            for index in range(1, args.runs + 1):
                result = _run_case(st_bin, label, index, bugs, args)
                results[label].append(result)
                print(
                    f"{label} run {index}/{args.runs}: exit={result['exit_code']}, "
                    f"GET 500={result['target_get_500_output']}, report={result['report_exists']}"
                )
        server_down = _run_server_down(st_bin, args)
        print(f"server_down: exit={server_down['exit_code']}, report={server_down['report_exists']}")

        first_bug = results["bug_on"][0]
        first_clean = results["bug_off"][0]
        (args.samples_dir / "st_bug_on.txt").write_text(
            _sample_text(first_bug["command"], first_bug["output"], first_bug["exit_code"]), encoding="utf-8"
        )
        (args.samples_dir / "st_bug_off.txt").write_text(
            _sample_text(first_clean["command"], first_clean["output"], first_clean["exit_code"]), encoding="utf-8"
        )
        (args.samples_dir / "st_server_down.txt").write_text(
            _sample_text(server_down["command"], server_down["output"], server_down["exit_code"]), encoding="utf-8"
        )
        for sample_name, result in (("st_bug_on.junit.xml", first_bug), ("st_bug_off.junit.xml", first_clean)):
            source = Path(result["report"])
            if source.is_file():
                shutil.copy2(source, args.samples_dir / sample_name)

        summary = {
            "schemathesis_version": version.stdout.strip(),
            "checks": CHECKS.split(","),
            "max_examples_per_operation": args.max_examples,
            "seed": args.seed,
            "excluded_path": EXCLUDED_PATH,
            "runtime_config": results["bug_on"][0]["runtime_config"],
            "long_id_len": results["bug_on"][0]["runtime_config"]["long_id_len"],
            "bug_on": [{key: value for key, value in run.items() if key not in {"output", "command"}} for run in results["bug_on"]],
            "bug_off": [{key: value for key, value in run.items() if key not in {"output", "command"}} for run in results["bug_off"]],
            "server_down": {key: value for key, value in server_down.items() if key not in {"output", "command"}},
            "all_passed": args.runs >= 5 and all(
                run["passed"] for group in results.values() for run in group
            ) and server_down["passed"],
            "samples_dir": str(args.samples_dir),
            "full_reports_dir": str(args.output_dir),
        }
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Samples: {args.samples_dir}")
        print(f"Full reports: {args.output_dir}")
        if summary["all_passed"]:
            print("STEP 24 DoD: PASS")
        elif args.runs < 5:
            print(f"STEP 24 DoD: INCOMPLETE (chạy {args.runs}/5 lượt cho mỗi cấu hình)")
        else:
            print("STEP 24 DoD: FAIL")
        return 0 if summary["all_passed"] else 1
    except (OSError, RuntimeError, ValueError, URLError, ET.ParseError) as error:
        print(f"STEP 24 runner error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
