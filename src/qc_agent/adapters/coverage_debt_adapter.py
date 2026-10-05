"""Coverage-debt (capability repo.coverage_debt): vỏ mỏng quanh worker tất định `coverage_debt_worker` (P2-2).
Adapter chỉ dịch `debt.json` -> findings/metrics; KHÔNG phán: ngưỡng (`debt.new == 0`) nằm trong file suite, oracle `threshold` so.

Chế độ quét do môi trường quyết định: có `QC_DIFF_BASE` thì diff-scan, không có thì full-scan.

Ba trường hợp đều kết thúc bằng AdapterParseError (status=error, chỉ lên banner vì lane discovery), KHÔNG BAO GIỜ bằng "0 nợ":
  - worker báo `status: error` (thiếu base/checkout nông/cấu hình sai/file không parse được);
  - không có debt.json, hoặc exit code mâu thuẫn với status;
  - findings và metrics tự mâu thuẫn (số nợ khác độ dài danh sách, finding_id lệch kind/surface).
Không cắt danh sách findings để report và Jira thấy đầy đủ phạm vi.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from qc_agent.adapters import _security as sec
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
OUT_NAME = "debt.json"
STDOUT_NAME = "stdout.log"
KINDS = ("api_contract", "api_endpoint", "ui_route")
TITLE_MAX = 200


def _title(kind: str, surface: str) -> str:
    """`surface` đến từ mã của người gửi PR và sẽ vào comment: bỏ ký tự điều khiển, cắt độ dài (P2-6 còn làm sạch markdown)."""
    clean = "".join(ch if ch.isprintable() else " " for ch in surface)
    return f"Nợ test [{kind}]: {clean[:TITLE_MAX]} chưa có test"


class CoverageDebtAdapter(Adapter):
    NAME = "coverage-debt"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs") or {}
        out = (workdir / OUT_NAME).resolve()   # tuyệt đối: worker chạy với cwd = SUT root
        out.unlink(missing_ok=True)            # không đọc nhầm file của lần chạy trước
        cmd = [sys.executable, "-m", "qc_agent.adapters.coverage_debt_worker", "--out", str(out)]
        if "suites_dir" in inputs:
            cmd += ["--suites-dir", sec.safe_relpath(inputs["suites_dir"], "inputs.suites_dir")]
        return cmd

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        out = workdir / OUT_NAME
        if not out.is_file():
            raise AdapterParseError(f"worker không ghi ra {OUT_NAME} (exit code {proc.returncode})")
        data = sec.read_json(out, OUT_NAME)
        if not isinstance(data, dict):
            raise AdapterParseError(f"{OUT_NAME} phải là object")
        status = data.get("status")
        if status == "error":
            raise AdapterParseError(f"coverage-debt: {str(data.get('error') or 'không rõ lý do')[:300]}")
        if status != "ok":
            raise AdapterParseError(f"{OUT_NAME}: status lạ {status!r}")
        if proc.returncode != 0:
            raise AdapterParseError(f"mâu thuẫn: status=ok nhưng worker thoát với exit code {proc.returncode}")

        metrics, raw = data.get("metrics"), data.get("findings")
        if not isinstance(metrics, dict) or not isinstance(raw, list):
            raise AdapterParseError(f"{OUT_NAME} thiếu metrics/findings")
        new, full = metrics.get("debt.new"), metrics.get("debt.full_scan")
        if isinstance(new, bool) or not isinstance(new, int) or new < 0 or not isinstance(full, bool):
            raise AdapterParseError("metrics phải có debt.new (số nguyên ≥ 0) và debt.full_scan (bool)")
        if data.get("mode") != ("full" if full else "diff"):
            raise AdapterParseError(f"mâu thuẫn: mode={data.get('mode')!r} nhưng debt.full_scan={full}")

        findings, seen = [], set()
        for item in raw:
            kind, surface = (item.get("kind"), item.get("surface")) if isinstance(item, dict) else (None, None)
            if kind not in KINDS or not isinstance(surface, str) or not surface:
                raise AdapterParseError(f"một khoản nợ có kind/surface không hợp lệ (kind={kind!r}): không đoán")
            if item.get("finding_id") != f"debt:{kind}:{surface}" or item["finding_id"] in seen:
                raise AdapterParseError("finding_id không khớp debt:<kind>:<surface> hoặc bị trùng")
            seen.add(item["finding_id"])
            finding = {"finding_id": item["finding_id"], "title": _title(kind, surface), "detected_by": f"coverage-debt:{kind}",
                       "verdict_source": "heuristic", "confidence": None, "severity_hint": "low"}
            if item.get("path") is not None or item.get("line") is not None:
                if not isinstance(item.get("path"), str) or not isinstance(item.get("line"), int) or item["line"] < 1:
                    raise AdapterParseError("location của finding coverage-debt không hợp lệ")
                finding["location"] = {"path": sec.safe_relpath(item["path"], "finding.path"), "line": item["line"]}
            findings.append(finding)
        if new != len(findings):
            raise AdapterParseError(f"mâu thuẫn: debt.new={new} nhưng có {len(findings)} findings")

        ignored = data.get("ignored") if isinstance(data.get("ignored"), list) else []
        notes = [f"PARSER_VERSION={PARSER_VERSION}", f"mode={data['mode']}", f"base={data.get('base')}", f"ignored={len(ignored)}"]
        return ParsedOutput(
            metrics={"debt.new": new, "debt.full_scan": full}, findings=findings,
            evidence_paths=[("raw_output", out), ("stdout", stdout_path)], tokens=0, usd=0.0, exit_code=proc.returncode,
            replay_cmd=shlex.join(str(a) for a in (proc.args if isinstance(proc.args, (list, tuple)) else [proc.args])), adapter_notes=notes)


if __name__ == "__main__":
    raise SystemExit(CoverageDebtAdapter().main())
