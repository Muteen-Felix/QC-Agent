"""Semgrep (capability code.sast): chạy OFFLINE với rule vendored, ĐẾM finding theo mức thành metric phẳng `semgrep.<mức>`.
Adapter không phán: ngưỡng (`semgrep.high == 0`...) nằm trong file suite và do oracle `threshold` so.

Ba bẫy đều kết thúc bằng AdapterParseError (status=error, gate đỏ nhãn hạ tầng), KHÔNG BAO GIỜ bằng "0 finding":
  - `errors[]` khác rỗng (rule hỏng / file không parse được) mà semgrep vẫn exit 0 => không quét đủ;
  - thư mục rule rỗng/không tồn tại => 0 finding vì không có luật nào;
  - `paths.scanned` không phải danh sách => không biết đã quét gì. (Quét được 0 file thì vẫn là số đo hợp lệ `semgrep.files_scanned = 0`:
    suite chặn bằng `semgrep.files_scanned >= 1`, để "không quét gì" là một dòng YAML đọc được.)
KHÔNG truyền `--error`: mặc định semgrep exit 0 dù có finding (đã kiểm bằng semgrep thật, xem ADR-002); truyền `--error` (hoặc `--error=false` — dạng
`=false` không hợp lệ với flag boolean của semgrep, `option '--error' is a flag, it cannot take the argument 'false'`) sẽ làm exit code lẫn nghĩa
"có finding" vào nghĩa "công cụ hỏng" mà adapter đang cần tách riêng ở `parse_output`.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from qc_agent.adapters import _security as sec
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
OUT_NAME = "semgrep.json"
STDOUT_NAME = "stdout.log"
SEVERITY = {"CRITICAL": "critical", "ERROR": "high", "HIGH": "high", "WARNING": "medium", "MEDIUM": "medium", "INFO": "low", "LOW": "low"}


def _rules_dir(value) -> str:
    if not isinstance(value, str) or not value.strip() or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise AdapterParseError("inputs.rules_dir phải là đường dẫn không rỗng, không bắt đầu bằng '-'")
    directory = Path(value)
    if not directory.is_dir() or not any(p.suffix in (".yaml", ".yml") for p in directory.rglob("*") if p.is_file()):
        raise AdapterParseError(f"inputs.rules_dir không có file rule (*.yaml|*.yml): {value!r} — rule rỗng sẽ cho 0 finding giả")
    return value


class SemgrepAdapter(Adapter):
    NAME = "semgrep"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs") or {}
        rules = _rules_dir(inputs.get("rules_dir"))
        paths = sec.safe_relpaths(inputs.get("paths"), "inputs.paths", default=["."]) or ["."]
        excludes = sec.safe_patterns(inputs.get("exclude"), "inputs.exclude")
        out = (workdir / OUT_NAME).resolve()
        out.unlink(missing_ok=True)   # không đọc nhầm file của lần chạy trước
        cmd = ["semgrep", "scan", "--json", f"--output={out}", "--metrics=off", "--disable-version-check", "--config", rules]
        cmd += [f"--exclude={pattern}" for pattern in excludes]
        return cmd + paths

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        out = workdir / OUT_NAME
        if proc.returncode != 0:
            raise AdapterParseError(f"semgrep kết thúc với exit code {proc.returncode}")
        if not out.is_file():
            raise AdapterParseError("semgrep không ghi ra file JSON")
        data = sec.read_json(out, "semgrep.json")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise AdapterParseError("semgrep.json thiếu danh sách results")
        errors = data.get("errors")
        if errors is not None and not isinstance(errors, list):
            raise AdapterParseError("semgrep.json: errors không phải danh sách")
        if errors:
            raise AdapterParseError(f"semgrep báo {len(errors)} lỗi khi quét (rule hỏng/file không parse được): không được coi là sạch")
        scanned = (data.get("paths") or {}).get("scanned") if isinstance(data.get("paths"), dict) else None
        if not isinstance(scanned, list):
            raise AdapterParseError("semgrep.json thiếu paths.scanned: không biết đã quét những file nào")

        rows = []
        for result in data["results"]:
            try:
                check_id, path, line = result["check_id"], result["path"], sec.require_int(result["start"]["line"], "start.line")
                raw_severity = result["extra"]["severity"]
            except (KeyError, TypeError, AdapterParseError):
                raise AdapterParseError("một finding của semgrep thiếu check_id/path/start.line/extra.severity") from None
            if not isinstance(check_id, str) or not isinstance(path, str) or raw_severity not in SEVERITY:
                raise AdapterParseError(f"một finding của semgrep có kiểu/mức nghiêm trọng lạ (severity={raw_severity!r}): không đoán mức")
            rows.append((path, line, result["start"].get("col") if isinstance(result["start"].get("col"), int) else 0, check_id, SEVERITY[raw_severity]))
        rows.sort()   # thứ tự tất định => hậu tố chống trùng của finding_id ổn định

        counts = {f"semgrep.{level}": 0 for level in sec.LEVELS}
        findings, seen, notes = [], {}, [f"PARSER_VERSION={PARSER_VERSION}", f"semgrep exit_code={proc.returncode}"]
        for path, line, _col, check_id, level in rows:
            counts[f"semgrep.{level}"] += 1
            where = f"{path}:{line}"
            # title CHỈ có id luật + vị trí: đoạn mã (`extra.lines`) do người gửi PR kiểm soát, sẽ đi vào comment PR
            findings.append(sec.finding(sec.finding_id("semgrep", f"{check_id}{where}", seen), f"{check_id} @ {where}", "semgrep", level))
        metrics = {**counts, "semgrep.total": len(rows), "semgrep.files_scanned": len(scanned)}
        return ParsedOutput(
            metrics=metrics, findings=sec.cap_findings(findings, notes), evidence_paths=[("raw_output", out), ("stdout", stdout_path)],
            tokens=0, usd=0.0, exit_code=proc.returncode,
            replay_cmd=shlex.join(str(a) for a in (proc.args if isinstance(proc.args, (list, tuple)) else [proc.args])), adapter_notes=notes)


if __name__ == "__main__":
    raise SystemExit(SemgrepAdapter().main())
