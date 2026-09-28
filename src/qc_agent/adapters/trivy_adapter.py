"""Trivy (capability deps.vuln): CVE của dependency, chạy OFFLINE với DB nướng sẵn trong image (`--skip-db-update`).
Metric: `trivy.critical|high|medium|low|unknown|total`, `trivy.targets` (số manifest/lockfile đã quét), `trivy.db_age_days`.
Adapter chỉ đếm; ngưỡng (kể cả tuổi DB) nằm trong file suite để độ cũ của dữ liệu là một dòng YAML ai cũng đọc được.

Ba bẫy xanh giả xử lý ở ĐÂY (mỗi cái là AdapterParseError, không bao giờ là "0 CVE sạch sẽ"):
  1. DB thiếu/hỏng: Trivy vẫn có thể in 0 vuln. Adapter đọc `<cache>/db/metadata.json`; không xác định được thời điểm DB => error.
  2. DB cũ: KHÔNG phải lỗi — đó là một phép đo hợp lệ, `trivy.db_age_days`, do suite chặn (`<= 14`) => oracle fail chứ không phải error.
  3. Không có manifest/lockfile nào (Results rỗng): Trivy in rỗng => "0 CVE". Ở đây là error rõ ràng: không có gì để quét ≠ đã quét và sạch.
"""
from __future__ import annotations

import math
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from qc_agent.adapters import _security as sec
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
OUT_NAME = "trivy.json"
STDOUT_NAME = "stdout.log"
DEFAULT_CACHE_DIR = "/opt/trivy-cache"
SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "UNKNOWN": "unknown"}
_FRACTION = re.compile(r"(\.\d{6})\d+")


def _now() -> datetime:
    return datetime.now(timezone.utc)   # tách ra để test cố định thời gian


def _cache_dir(spec: dict) -> str:
    value = (spec.get("inputs") or {}).get("cache_dir", DEFAULT_CACHE_DIR)
    if not isinstance(value, str) or not value.strip() or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise AdapterParseError("inputs.cache_dir phải là đường dẫn không rỗng, không bắt đầu bằng '-'")
    return value


def db_age_days(cache_dir: str) -> int:
    """Tuổi DB theo `UpdatedAt` (lúc đội Trivy dựng nội dung DB), làm tròn LÊN: `age <= N ngày` ⇔ `db_age_days <= N`."""
    path = Path(cache_dir) / "db" / "metadata.json"
    if not path.is_file():
        raise AdapterParseError(f"không thấy DB của Trivy ({path.name} trong {cache_dir}/db): image thiếu DB CVE nướng sẵn")
    meta = sec.read_json(path, "metadata.json của DB Trivy")
    stamp = meta.get("UpdatedAt") if isinstance(meta, dict) else None
    if not isinstance(stamp, str):
        raise AdapterParseError("metadata.json của DB Trivy thiếu UpdatedAt: không xác định được phiên bản DB")
    try:
        updated = datetime.fromisoformat(_FRACTION.sub(r"\1", stamp.strip()).replace("Z", "+00:00"))
    except ValueError:
        raise AdapterParseError("UpdatedAt của DB Trivy sai định dạng thời gian") from None
    if updated.tzinfo is None:
        raise AdapterParseError("UpdatedAt của DB Trivy thiếu múi giờ")
    return max(0, math.ceil((_now() - updated).total_seconds() / 86400))


class TrivyAdapter(Adapter):
    NAME = "trivy"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs") or {}
        path = sec.safe_relpath(inputs.get("path", "."), "inputs.path")
        out = (workdir / OUT_NAME).resolve()
        out.unlink(missing_ok=True)
        return ["trivy", "fs", "--scanners", "vuln", "--skip-db-update", "--offline-scan", "--cache-dir", _cache_dir(spec),
                "--format", "json", "--output", str(out), "--exit-code", "0", path]

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        stdout_path = workdir / STDOUT_NAME
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        out = workdir / OUT_NAME
        if proc.returncode != 0:
            raise AdapterParseError(f"trivy kết thúc với exit code {proc.returncode}")
        if not out.is_file():
            raise AdapterParseError("trivy không ghi ra file JSON")
        data = sec.read_json(out, "trivy.json")
        if not isinstance(data, dict):
            raise AdapterParseError("trivy.json không phải object")
        age = db_age_days(_cache_dir(spec))    # sau khi biết báo cáo đọc được, TRƯỚC khi đếm: DB không rõ thì không có quyền nói "sạch"
        results = data.get("Results")
        if not isinstance(results, list) or not results:
            raise AdapterParseError("trivy không tìm thấy manifest/lockfile nào để quét (Results rỗng): không được coi là 0 CVE")

        rows = []
        for result in results:
            if not isinstance(result, dict) or not isinstance(result.get("Target"), str):
                raise AdapterParseError("một mục Results của trivy thiếu Target")
            vulns = result.get("Vulnerabilities")
            if vulns is not None and not isinstance(vulns, list):
                raise AdapterParseError("Vulnerabilities của trivy không phải danh sách")
            for vuln in vulns or []:
                try:
                    vid, severity = vuln["VulnerabilityID"], vuln["Severity"]
                    pkg, installed = vuln.get("PkgName") or "", vuln.get("InstalledVersion") or ""
                except (KeyError, TypeError):
                    raise AdapterParseError("một CVE của trivy thiếu VulnerabilityID/Severity") from None
                if not isinstance(vid, str) or severity not in SEVERITY or not isinstance(pkg, str) or not isinstance(installed, str):
                    raise AdapterParseError(f"một CVE của trivy có kiểu/mức nghiêm trọng lạ (Severity={severity!r}): không đoán mức")
                rows.append((result["Target"], vid, pkg, installed, SEVERITY[severity]))
        rows.sort()

        counts = {f"trivy.{level}": 0 for level in (*sec.LEVELS, "unknown")}
        findings, seen, notes = [], {}, [f"PARSER_VERSION={PARSER_VERSION}", f"trivy exit_code={proc.returncode}"]
        for target, vid, pkg, installed, level in rows:
            counts[f"trivy.{level}"] += 1
            # Trivy không có số dòng đáng tin: vị trí là file lockfile. severity_hint: critical -> high (schema không có critical), unknown -> null
            findings.append(sec.finding(sec.finding_id("trivy", f"{vid}|{pkg}|{installed}|{target}", seen),
                                        f"{vid} {pkg}@{installed} @ {target}", "trivy", level))
        metrics = {**counts, "trivy.total": len(rows), "trivy.targets": len(results), "trivy.db_age_days": age}
        return ParsedOutput(
            metrics=metrics, findings=sec.cap_findings(findings, notes), evidence_paths=[("raw_output", out), ("stdout", stdout_path)],
            tokens=0, usd=0.0, exit_code=proc.returncode,
            replay_cmd=shlex.join(str(a) for a in (proc.args if isinstance(proc.args, (list, tuple)) else [proc.args])), adapter_notes=notes)


if __name__ == "__main__":
    raise SystemExit(TrivyAdapter().main())
