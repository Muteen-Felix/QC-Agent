"""Đồ dùng chung cho test của ba adapter Security: spec hợp lệ theo contract, công cụ giả (thay `Adapter._exec`) và fixture."""
import copy
import json
import subprocess
from pathlib import Path

from qc_agent.core import schema

FIX = Path(__file__).resolve().parent / "fixtures" / "security"


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


def make_spec(capability: str, inputs: dict, assertions: list, *, task_id: str = "t-010", run_id: str = "r-0001") -> dict:
    """Spec đúng dạng mẫu suite Security (kể cả `base_url` bắt buộc mà adapter bỏ qua); qua validate_task ngay tại đây."""
    spec = {
        "task_id": task_id, "plan_id": "plan-sec", "run_id": run_id, "capability": capability, "lane": "gate", "intent": "security",
        "target": {"kind": "source_tree", "base_url": "http://sut:8000"}, "inputs": copy.deepcopy(inputs),
        "oracle": {"kind": "threshold", "assertions": copy.deepcopy(assertions)}, "expected_result_kind": "verdict",
        "budget": {"wallclock_s": 60, "tokens": 0, "usd": 0}, "determinism": {"seed": 0, "replayable": True},
        "sut_identity_ref": "sut-sec", "evidence_required": ["raw_output", "stdout"], "retry": {"max": 1, "on": ["error"]},
    }
    assert schema.validate_task(spec) == []
    return spec


def completed(returncode: int = 0, stdout: str = "", stderr: str = "", args=None) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=args or ["tool"], returncode=returncode, stdout=stdout, stderr=stderr)


def output_path(cmd: list[str]) -> Path:
    """Đường dẫn file báo cáo mà adapter đưa cho công cụ: `--output=F` (semgrep), `--output F` (trivy) hoặc `--report-path F` (gitleaks)."""
    for i, arg in enumerate(cmd):
        if arg.startswith("--output="):
            return Path(arg.split("=", 1)[1])
        if arg in ("--output", "--report-path"):
            return Path(cmd[i + 1])
    raise AssertionError(f"lệnh không có tham số báo cáo: {cmd}")


def run_with_fake_tool(adapter, spec, monkeypatch, tmp_path, *, report: str | None = None, returncode: int = 0, stdout: str = "", stderr: str = "") -> dict:
    """Chạy `adapter.run(spec)` đủ vòng đời (build_cmd -> công cụ -> parse -> oracle -> kiểm contract) với công cụ giả:
    ghi `report` vào đúng file mà adapter chỉ định (None = công cụ không ghi gì) rồi trả exit code/stdout/stderr đã định."""
    monkeypatch.setenv("QC_RUNS_DIR", str(tmp_path / "runs"))

    def fake_exec(cmd, timeout):
        if report is not None:
            output_path(cmd).write_text(report, encoding="utf-8")
        return completed(returncode, stdout, stderr, args=cmd)

    monkeypatch.setattr(adapter, "_exec", fake_exec)
    return adapter.run(spec)
