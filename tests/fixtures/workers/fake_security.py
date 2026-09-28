"""Công cụ Security GIẢ cho test A-7 (tests/test_security_negative_cases.py): adapter THẬT (build_cmd -> parse_output -> oracle -> kiểm contract), chỉ thay bước
chạy công cụ (`_exec`) bằng kịch bản chọn qua $FAKE_TOOL_MODE, để dựng được các ca "công cụ chết/JSON hỏng/rò secret" mà không cần cài công cụ.
Mỗi lần `_exec` được gọi sẽ ghi một dòng vào $FAKE_TOOL_LOG (nếu có): test dùng để chứng minh công cụ KHÔNG bị chạy (đường dẫn hiểm bị chặn trước đó) hoặc chỉ bị retry đúng 1 lần."""
import json
import os
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "security"
LEAKED = "sk_live_FAKE-A7-LEAKED-SECRET-VALUE"       # chỉ tồn tại trong kịch bản `leak`; test khẳng định nó không nằm ở bất cứ đâu sau khi chạy


def _output_path(cmd: list[str]) -> Path:
    for i, arg in enumerate(cmd):
        if arg.startswith("--output="):
            return Path(arg.split("=", 1)[1])
        if arg in ("--output", "--report-path"):
            return Path(cmd[i + 1])
    raise SystemExit(f"lệnh không có tham số báo cáo: {cmd}")


def _report(tool: str, mode: str) -> str | None:
    sample = (FIXTURES / f"{tool}-sample.json").read_text(encoding="utf-8")
    if mode == "sample":
        return sample
    if mode == "empty":
        return (FIXTURES / f"{tool}-empty.json").read_text(encoding="utf-8")
    if mode == "truncated":
        return sample[: len(sample) // 2]
    if mode == "syntax-error":
        return '{"results": [oops, }'
    if mode == "no-targets":     # trivy: repo không có lockfile/manifest => Results rỗng
        return json.dumps({"SchemaVersion": 2, "ArtifactName": ".", "ArtifactType": "filesystem", "Results": []})
    if mode == "semgrep-errors":
        data = json.loads((FIXTURES / "semgrep-empty.json").read_text(encoding="utf-8"))
        data["errors"] = [{"code": 3, "level": "warn", "type": "PartialParsing", "message": "không parse được"}]
        return json.dumps(data)
    if mode == "leak":       # báo cáo như khi --redact không có hiệu lực
        data = json.loads(sample)
        data[0].update(Secret=LEAKED, Match=f'API_KEY = "{LEAKED}"')
        return json.dumps(data)
    return None              # crash / no-output: công cụ không ghi file


def fake_adapter(base, tool: str):
    class Fake(base):
        def _exec(self, cmd, timeout):
            log = os.environ.get("FAKE_TOOL_LOG")
            if log:
                with open(log, "a", encoding="utf-8") as handle:
                    handle.write(tool + "\n")
            mode = os.environ.get("FAKE_TOOL_MODE", "sample")
            report = _report(tool, mode)
            if report is not None:
                _output_path(cmd).write_text(report, encoding="utf-8")
            crashed = mode == "crash"
            return subprocess.CompletedProcess(cmd, 137 if crashed else 0, "", "Killed" if crashed else "")

    Fake.__name__ = f"Fake{base.__name__}"
    return Fake
