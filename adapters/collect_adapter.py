"""Collect note summarizer inputs/outputs through the public HTTP API."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from adapters._base import Adapter, AdapterParseError, ParsedOutput
from adapters.collect_runtime import OUTPUT_NAME, REPORT_NAME


STDOUT_NAME = "stdout.log"
REQUIRED_CHECKS = {"all_http_2xx", "count_matches_golden"}
RECORD_FIELDS = {
    "id", "note_id", "input", "actual_output", "model", "prompt_hash", "http_status"
}


class CollectAdapter(Adapter):
    NAME = "http-collect"
    ADAPTER_VERSION = "0.2.0"

    def __init__(self):
        super().__init__()
        self.env = {}

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs", {})
        golden_path = inputs.get("golden")
        if not isinstance(golden_path, str) or not golden_path.strip():
            raise AdapterParseError("inputs.golden phải là đường dẫn file")
        path = Path(golden_path).resolve()
        if not path.is_file():
            raise AdapterParseError(f"golden file không tồn tại: {path}")
        base_url = spec.get("target", {}).get("base_url")
        parsed = urlsplit(base_url) if isinstance(base_url, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AdapterParseError("target.base_url phải là URL HTTP(S) tuyệt đối")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AdapterParseError(
                "target.base_url không được chứa thông tin đăng nhập, query hoặc fragment"
            )
        # Keep the base URL out of argv; argv is persisted in determinism.replay_cmd and can be
        # visible in process listings even though credentials/query parameters are rejected above.
        self.env["QC_COLLECT_BASE_URL"] = base_url
        required = spec.get("oracle", {}).get("required", [])
        if not isinstance(required, list) or set(required) != REQUIRED_CHECKS:
            raise AdapterParseError(
                "oracle.required phải chứa all_http_2xx và count_matches_golden"
            )

        return [
            sys.executable,
            "-m",
            "adapters.collect_worker",
            "--golden",
            str(path),
            "--out",
            str((workdir / OUTPUT_NAME).resolve()),
            "--report",
            str((workdir / REPORT_NAME).resolve()),
        ]

    def parse_output(
        self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict
    ) -> ParsedOutput:
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        if proc.returncode != 0:
            # The worker may have been killed at the task deadline or hit a transport/protocol error.
            raise AdapterParseError(f"collector worker exit code={proc.returncode}")

        output_path = workdir / OUTPUT_NAME
        report_path = workdir / REPORT_NAME
        try:
            records = json.loads(output_path.read_text(encoding="utf-8-sig"))
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise AdapterParseError("collector output/report không đọc được") from error
        if not isinstance(records, list) or not records:
            raise AdapterParseError("outputs.json phải là danh sách record không rỗng")
        ids: list[str] = []
        for record in records:
            if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
                raise AdapterParseError("outputs.json có record sai schema")
            case_id = record["id"]
            if not isinstance(case_id, str) or not case_id.strip():
                raise AdapterParseError("outputs.json có id không hợp lệ")
            ids.append(case_id)
            if (
                isinstance(record["note_id"], bool)
                or record["note_id"] is not None
                and (not isinstance(record["note_id"], int) or record["note_id"] < 1)
                or not isinstance(record["input"], str)
                or not isinstance(record["actual_output"], str)
                or not isinstance(record["model"], str)
                or not isinstance(record["prompt_hash"], str)
                or isinstance(record["http_status"], bool)
                or record["http_status"] is not None
                and (
                    not isinstance(record["http_status"], int)
                    or not 100 <= record["http_status"] <= 599
                )
            ):
                raise AdapterParseError("outputs.json có trường sai kiểu")
        if len(set(ids)) != len(ids):
            raise AdapterParseError("outputs.json có id trùng")

        required = spec.get("oracle", {}).get("required", [])
        if not isinstance(required, list) or set(required) != REQUIRED_CHECKS:
            raise AdapterParseError(
                "oracle.required phải chứa all_http_2xx và count_matches_golden"
            )
        checks = report.get("checks") if isinstance(report, dict) else None
        if (
            not isinstance(report, dict)
            or set(report) != {"count", "expected_count", "checks"}
            or isinstance(report.get("count"), bool)
            or not isinstance(report.get("count"), int)
            or isinstance(report.get("expected_count"), bool)
            or not isinstance(report.get("expected_count"), int)
            or not isinstance(checks, dict)
            or set(checks) != REQUIRED_CHECKS
            or any(not isinstance(value, bool) for value in checks.values())
        ):
            raise AdapterParseError("collection.json sai schema")
        count_matches = report["count"] == report["expected_count"]
        if (
            report["count"] != len(records)
            or report["expected_count"] < 1
            or checks["count_matches_golden"] != count_matches
        ):
            raise AdapterParseError("collection.json mâu thuẫn với outputs.json")

        args = proc.args if isinstance(proc.args, (list, tuple)) else [str(proc.args)]
        return ParsedOutput(
            signals={"checks": checks},
            evidence_paths=[
                ("raw_output", output_path),
                ("raw_output", report_path),
                ("stdout", stdout_path),
            ],
            tokens=0,
            usd=0.0,
            exit_code=proc.returncode,
            replay_cmd=shlex.join([str(argument) for argument in args]),
            adapter_notes=[
                "http_status mô tả phản hồi summarize cuối; khi POST /notes lỗi, lưu status create",
                "HTTP collection chạy trong worker process và bị giới hạn bởi budget.wallclock_s",
            ],
        )


if __name__ == "__main__":
    raise SystemExit(CollectAdapter().main())
