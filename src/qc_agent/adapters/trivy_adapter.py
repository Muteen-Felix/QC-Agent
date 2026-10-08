"""Trivy (capability deps.vuln): CVE của dependency, chạy OFFLINE với DB nướng sẵn trong image (`--skip-db-update`).
Metric: `trivy.critical|high|medium|low|unknown|total`, `trivy.targets` (số manifest/lockfile đã quét), `trivy.db_age_days`.
Adapter chỉ đếm; ngưỡng (kể cả tuổi DB) nằm trong file suite để độ cũ của dữ liệu là một dòng YAML ai cũng đọc được.

Ba bẫy xanh giả xử lý ở ĐÂY (mỗi cái là AdapterParseError, không bao giờ là "0 CVE sạch sẽ"):
  1. DB thiếu/hỏng: Trivy vẫn có thể in 0 vuln. Adapter đọc `<cache>/db/metadata.json`; không xác định được thời điểm DB => error.
  2. DB cũ: KHÔNG phải lỗi — đó là một phép đo hợp lệ, `trivy.db_age_days`, do suite chặn (`<= 14`) => oracle fail chứ không phải error.
  3. Không có manifest/lockfile nào (Results rỗng): Trivy in rỗng => "0 CVE". Ở đây là error rõ ràng: không có gì để quét ≠ đã quét và sạch.
     Trivy in Y HỆT nhau cho "lockfile hợp lệ nhưng 0 dependency" và "không có lockfile" (đo trên Trivy 0.74), nên SUT thật sự không có
     dependency phải opt-in TƯỜNG MINH trong suite: `allow_no_dependencies: true` kèm `expected_manifests: [<file>, ...]`. Results rỗng chỉ được
     qua khi MỌI file khai báo vẫn là file thường nằm trong gốc SUT (kiểm cả khi Results không rỗng). Xoá/đổi tên lockfile (PR không đụng file suite) => error, không xanh giả.
     Giới hạn: chỉ chứng minh file còn tồn tại (và ghi sha256), KHÔNG chứng minh Trivy đã parse nó hay dependency không bị giấu.
"""
from __future__ import annotations

import hashlib
import math
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from qc_agent.adapters import _security as sec
from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

PARSER_VERSION = "1"
OUT_NAME = "trivy.json"
STDOUT_NAME = "stdout.log"
DEFAULT_CACHE_DIR = "/opt/trivy-cache"
SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "UNKNOWN": "unknown"}
_FRACTION = re.compile(r"(\.\d{6})\d+")
# Tên file Trivy 0.74 `fs` NHẬN (thử từng loại trong image): khai tên ngoài danh sách thì vô nghĩa nên là error. `requirements-dev.txt` KHÔNG được nhận.
MANIFEST_NAMES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "Pipfile.lock", "poetry.lock", "uv.lock",
                            "go.mod", "Cargo.lock", "composer.lock", "Gemfile.lock", "pom.xml", "packages.lock.json"})
MAX_MANIFESTS = 20


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


def _parts(rel: str) -> list[str]:
    return [part for part in PurePosixPath(rel.replace("\\", "/")).parts if part != "."]


def declared_manifests(spec: dict) -> list[str] | None:
    """None = không opt-in. Có opt-in thì trả danh sách manifest đã chuẩn hoá; MỌI khai báo sai là AdapterParseError (không bao giờ là pass)."""
    inputs = spec.get("inputs") or {}
    flag, manifests = inputs.get("allow_no_dependencies"), inputs.get("expected_manifests")
    if flag is not None and not isinstance(flag, bool):
        raise AdapterParseError("inputs.allow_no_dependencies phải là true/false")
    if not flag:
        if manifests is not None:
            raise AdapterParseError("inputs.expected_manifests có mặt nhưng inputs.allow_no_dependencies không bật: khai báo mâu thuẫn")
        return None
    if not isinstance(manifests, list) or not manifests:
        raise AdapterParseError("allow_no_dependencies=true cần inputs.expected_manifests là danh sách không rỗng các manifest/lockfile dự kiến")
    if len(manifests) > MAX_MANIFESTS:
        raise AdapterParseError(f"inputs.expected_manifests tối đa {MAX_MANIFESTS} phần tử")
    base = _parts(sec.safe_relpath(inputs.get("path", "."), "inputs.path"))
    seen, out = set(), []
    for index, item in enumerate(manifests):
        parts = _parts(sec.safe_relpath(item, f"inputs.expected_manifests[{index}]"))
        if len(parts) <= len(base) or parts[:len(base)] != base:
            raise AdapterParseError(f"inputs.expected_manifests[{index}] phải nằm trong inputs.path: {item!r}")
        if parts[-1] not in MANIFEST_NAMES:
            raise AdapterParseError(f"inputs.expected_manifests[{index}] không phải tên manifest/lockfile Trivy nhận: {item!r}")
        if tuple(parts) in seen:
            raise AdapterParseError(f"inputs.expected_manifests[{index}] bị trùng: {item!r}")
        seen.add(tuple(parts))
        out.append("/".join(parts))
    return out


def declared_manifest_digests(manifests: list[str]) -> list[str]:
    """Mỗi manifest khai báo phải là FILE THƯỜNG trong gốc SUT (cwd). Thiếu một cái => error. Trả `<tên> sha256=<hex>` để đổi nội dung hiện trong report."""
    root = Path.cwd().resolve()
    digests, bad = [], []
    for name in manifests:
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file():
            bad.append(name)
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        digests.append(f"{name} sha256={digest.hexdigest()}")
    if bad:
        raise AdapterParseError("manifest đã khai báo trong inputs.expected_manifests không còn là file trong repo SUT: " + ", ".join(bad)
                                + " (Trivy không có gì để quét: không được coi là 0 CVE)")
    return digests


class TrivyAdapter(Adapter):
    NAME = "trivy"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        inputs = spec.get("inputs") or {}
        path = sec.safe_relpath(inputs.get("path", "."), "inputs.path")
        declared_manifests(spec)    # khai báo sai thì dừng NGAY, trước khi chạy Trivy
        out = (workdir / OUT_NAME).resolve()
        out.unlink(missing_ok=True)
        return ["trivy", "fs", "--scanners", "vuln", "--skip-db-update", "--offline-scan", "--cache-dir", _cache_dir(spec),
                "--format", "json", "--output", str(out), "--exit-code", "0", path]

    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        declared = declared_manifests(spec)
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
        results, extra_notes = data.get("Results"), []
        if declared is not None:    # khai báo là lời hứa về repo: kiểm BẤT KỂ Results rỗng hay không (manifest thiếu mà Trivy vẫn thấy file khác vẫn là error)
            extra_notes = ["declared_manifests (allow_no_dependencies=true): " + "; ".join(declared_manifest_digests(declared))]
        if not isinstance(results, list) or not results:
            if declared is None or results not in (None, []):
                raise AdapterParseError("trivy không tìm thấy manifest/lockfile nào để quét (Results rỗng): không được coi là 0 CVE")
            results = []

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
        findings, seen, notes = [], {}, [f"PARSER_VERSION={PARSER_VERSION}", f"trivy exit_code={proc.returncode}", *extra_notes]
        for target, vid, pkg, installed, level in rows:
            counts[f"trivy.{level}"] += 1
            # Trivy không có số dòng đáng tin: vị trí là file lockfile.
            item = sec.finding(sec.finding_id("trivy", f"{vid}|{pkg}|{installed}|{target}", seen),
                               f"{vid} {pkg}@{installed} @ {target}", f"trivy:{vid}", level)
            item["location"] = {"path": target}
            findings.append(item)
        metrics = {**counts, "trivy.total": len(rows), "trivy.targets": len(results), "trivy.db_age_days": age}
        return ParsedOutput(
            metrics=metrics, findings=sec.cap_findings(findings, notes), evidence_paths=[("raw_output", out), ("stdout", stdout_path)],
            tokens=0, usd=0.0, exit_code=proc.returncode,
            replay_cmd=shlex.join(str(a) for a in (proc.args if isinstance(proc.args, (list, tuple)) else [proc.args])), adapter_notes=notes)


if __name__ == "__main__":
    raise SystemExit(TrivyAdapter().main())
