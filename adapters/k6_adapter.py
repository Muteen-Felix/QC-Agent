"""Translate a k6 summary export into metrics for the shared threshold oracle."""
from __future__ import annotations

import json
import math
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from adapters._base import Adapter, AdapterParseError, ParsedOutput


PARSER_VERSION = "1"
SUMMARY_NAME = "k6-summary.json"
STDOUT_NAME = "stdout.log"


def _is_measurement(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class K6Adapter(Adapter):
    NAME = "k6"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs", {})
        script = inputs.get("script")
        vus = inputs.get("vus")
        duration = inputs.get("duration")
        base_url = spec.get("target", {}).get("base_url")
        parsed_url = urlsplit(base_url) if isinstance(base_url, str) else None
        if not isinstance(script, str) or not script.strip():
            raise AdapterParseError("inputs.script phải là đường dẫn script không rỗng")
        if isinstance(vus, bool) or not isinstance(vus, int):
            raise AdapterParseError("inputs.vus phải là số nguyên")
        if not isinstance(duration, str) or not duration.strip():
            raise AdapterParseError("inputs.duration phải là chuỗi không rỗng")
        if not parsed_url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise AdapterParseError("target.base_url phải là URL HTTP(S) tuyệt đối")

        self.env = {**self.env, "APP_BASE_URL": base_url}
        summary_path = (workdir / SUMMARY_NAME).resolve()
        summary_path.unlink(missing_ok=True)
        return [
            "k6", "run",
            f"--summary-export={summary_path}",
            "--vus", str(vus),
            "--duration", duration,
            script,
        ]

    def parse_output(
        self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict
    ) -> ParsedOutput:
        stdout = proc.stdout or ""
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(stdout, encoding="utf-8")

        summary_path = workdir / SUMMARY_NAME
        if not summary_path.is_file():
            raise AdapterParseError(f"không có k6 summary: {summary_path.name}")
        if proc.returncode != 0:
            raise AdapterParseError(f"k6 kết thúc với exit code {proc.returncode}")
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AdapterParseError(f"k6 summary không đọc được: {error}") from error
        if not isinstance(summary, dict) or not isinstance(summary.get("metrics"), dict):
            raise AdapterParseError("k6 summary thiếu object metrics")

        metrics = summary["metrics"]
        duration = metrics.get("http_req_duration")
        failed = metrics.get("http_req_failed")
        if not isinstance(duration, dict) or not isinstance(failed, dict):
            raise AdapterParseError("k6 summary thiếu metric http_req_duration hoặc http_req_failed")
        p95 = duration.get("p(95)")
        failed_rate = failed.get("value")
        if not _is_measurement(p95):
            raise AdapterParseError("k6 summary thiếu số đo http_req_duration['p(95)']")
        if not _is_measurement(failed_rate):
            raise AdapterParseError("k6 summary thiếu số đo http_req_failed.value")

        args = proc.args if isinstance(proc.args, (list, tuple)) else [str(proc.args)]
        return ParsedOutput(
            metrics={
                "http_req_duration.p95": p95,
                "http_req_failed.rate": failed_rate,
            },
            evidence_paths=[("raw_output", summary_path), ("stdout", stdout_path)],
            tokens=0,
            usd=0.0,
            exit_code=proc.returncode,
            replay_cmd=shlex.join([str(argument) for argument in args]),
            adapter_notes=[f"PARSER_VERSION={PARSER_VERSION}", f"k6 exit_code={proc.returncode}"],
        )


if __name__ == "__main__":
    raise SystemExit(K6Adapter().main())
