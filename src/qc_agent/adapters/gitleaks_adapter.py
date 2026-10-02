"""gitleaks (capability code.secret): quét secret trong working tree (mặc định) hoặc cả lịch sử git (`inputs.history: true`).
Secret không có "mức": một cái cũng là quá nhiều => metric duy nhất `gitleaks.count`; ngưỡng (`== 0`) nằm trong file suite.

🚩 `--redact` là BẮT BUỘC: báo cáo mặc định chứa chính giá trị secret, mà file đó trở thành evidence `raw_output`, đi vào artifact CI và có thể lên API
qua ingest — gate quét secret sẽ TỰ TẠO ra chỗ rò. Nên `parse_output` không tin cờ: nó kiểm từng finding, thấy `Secret`/`Match` còn giá trị thật
thì XOÁ file báo cáo (để không lọt vào artifact) rồi ném AdapterParseError. Thông báo lỗi không bao giờ chứa nội dung của báo cáo.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from qc_agent.adapters import _security as sec
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
OUT_NAME = "gitleaks.json"
STDOUT_NAME = "stdout.log"
REDACTED = "REDACTED"
_NO_LEAKS = re.compile(r"no leaks found", re.I)


def _assert_redacted(entries: list) -> None:
    """`Secret` phải rỗng/REDACTED; `Match` (dòng chứa secret) phải đã bị thay phần secret bằng REDACTED. Không nêu giá trị trong lỗi."""
    unsafe = 0
    for entry in entries:
        secret, match = entry.get("Secret"), entry.get("Match")
        if secret not in (None, "", REDACTED) or (match not in (None, "") and (not isinstance(match, str) or REDACTED not in match)):
            unsafe += 1
    if unsafe:
        raise AdapterParseError(f"gitleaks trả {unsafe} finding mà Secret/Match chưa được REDACTED: từ chối dùng và đã xoá báo cáo (chống rò secret)")


class GitleaksAdapter(Adapter):
    NAME = "gitleaks"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs") or {}
        source = sec.safe_relpath(inputs.get("source", "."), "inputs.source")
        history = inputs.get("history", False)
        if not isinstance(history, bool):
            raise AdapterParseError("inputs.history phải là true/false")
        out = (workdir / OUT_NAME).resolve()
        out.unlink(missing_ok=True)
        cmd = ["gitleaks", "detect", "--source", source, "--report-format", "json", "--report-path", str(out), "--exit-code", "0", "--redact", "--no-banner"]
        if not history:
            cmd.append("--no-git")   # chỉ working tree: bắt secret MỚI đưa vào PR này; lịch sử cần checkout fetch-depth: 0
        return cmd

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        out = workdir / OUT_NAME
        notes = [f"PARSER_VERSION={PARSER_VERSION}", f"gitleaks exit_code={proc.returncode}"]
        if proc.returncode != 0:
            out.unlink(missing_ok=True)   # báo cáo dở dang không đáng tin, và không được nằm lại trong runs/ (sẽ được upload)
            raise AdapterParseError(f"gitleaks kết thúc với exit code {proc.returncode}")
        if not out.is_file():
            # gitleaks (một số phiên bản) không ghi file khi sạch; chỉ tin khi chính nó nói "no leaks found", còn lại là công cụ không chạy tới nơi
            if not _NO_LEAKS.search(proc.stderr or ""):
                raise AdapterParseError("gitleaks không ghi báo cáo và không xác nhận 'no leaks found'")
            out.write_text("[]\n", encoding="utf-8")
            notes.append("gitleaks không ghi file khi sạch: adapter ghi báo cáo rỗng theo xác nhận 'no leaks found' của công cụ")
        try:
            data = sec.read_json(out, "gitleaks.json")
        except AdapterParseError:
            out.unlink(missing_ok=True)   # không parse được thì không kiểm được có secret hay không: không để lại evidence
            raise
        entries = [] if data is None else data      # Go marshal slice rỗng có thể ra `null`
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            out.unlink(missing_ok=True)
            raise AdapterParseError("gitleaks.json không phải danh sách finding")
        try:
            _assert_redacted(entries)
        except AdapterParseError:
            out.unlink(missing_ok=True)
            raise

        rows = []
        for entry in entries:
            try:
                rule, file, line = entry["RuleID"], entry["File"], sec.require_int(entry["StartLine"], "StartLine")
            except (KeyError, AdapterParseError):
                raise AdapterParseError("một finding của gitleaks thiếu RuleID/File/StartLine") from None
            if not isinstance(rule, str) or not isinstance(file, str):
                raise AdapterParseError("một finding của gitleaks có RuleID/File sai kiểu")
            rows.append((file, line, rule))
        rows.sort()
        findings, seen = [], {}
        for file, line, rule in rows:
            where = f"{file}:{line}"
            # Không đưa Secret/Match/Description vào finding: title chỉ có luật + vị trí
            item = sec.finding(sec.finding_id("gitleaks", f"{rule}{where}", seen), f"{rule} @ {where}", f"gitleaks:{rule}", "high")
            item["location"] = {"path": file, "line": line}
            findings.append(item)

        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")   # chỉ ghi SAU khi đã xác nhận redact; không ghi stderr
        return ParsedOutput(
            metrics={"gitleaks.count": len(rows)}, findings=sec.cap_findings(findings, notes),
            evidence_paths=[("raw_output", out), ("stdout", stdout_path)], tokens=0, usd=0.0, exit_code=proc.returncode,
            replay_cmd=shlex.join(str(a) for a in (proc.args if isinstance(proc.args, (list, tuple)) else [proc.args])), adapter_notes=notes)


if __name__ == "__main__":
    raise SystemExit(GitleaksAdapter().main())
