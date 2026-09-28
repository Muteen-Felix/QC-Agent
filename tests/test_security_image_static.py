"""Ràng buộc tĩnh (không cần Docker) của phần Security trong image: vùng của Làn A trong Dockerfile, ghim digest/hash, rule Semgrep vendored, tài liệu."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
RULES = sorted((ROOT / "rules" / "semgrep").glob("*.y*ml"))


def region(name: str) -> str:
    match = re.search(rf"# ==== qc-agent:region {re.escape(name)} ====\r?\n(.*?)# ==== qc-agent:end ====", DOCKERFILE, re.S)
    assert match, f"thiếu vùng {name}"
    return match.group(1)


def test_all_four_regions_exist_and_lane_a_regions_are_filled():
    for name in ("security-stages (A)", "integration-stages (B)", "security-runtime (A)", "integration-runtime (B)"):
        region(name)
    assert DOCKERFILE.index("security-stages (A)") < DOCKERFILE.index("integration-stages (B)") < DOCKERFILE.index("AS build")      # vùng stage nằm trước stage build
    assert DOCKERFILE.index("security-runtime (A)") < DOCKERFILE.index("integration-runtime (B)") < DOCKERFILE.index("useradd")   # vùng runtime chạy bằng root, trước USER qc
    assert "FROM " in region("security-stages (A)") and "COPY --from=" in region("security-runtime (A)")


def test_lane_b_regions_are_untouched_by_lane_a():
    for name in ("integration-stages (B)", "integration-runtime (B)"):
        assert not re.search(r"^(FROM|COPY|RUN|ENV|ARG) ", region(name), re.M)     # còn nguyên là comment khung


def test_third_party_images_are_pinned_by_digest():
    for line in region("security-stages (A)").splitlines():
        if line.startswith("FROM ") and "${" not in line:
            assert re.search(r"@sha256:[0-9a-f]{64} AS \w+$", line.strip()), f"image Security phải ghim theo digest: {line}"


def test_the_image_installs_all_three_tools_and_smoke_tests_them_at_build_time():
    stages, runtime = region("security-stages (A)"), region("security-runtime (A)")
    assert "AS gitleaks" in stages and "AS trivy" in stages and "AS semgrep" in stages
    assert "--download-db-only" in stages and "/opt/trivy-cache" in stages                        # DB CVE nướng lúc build, cùng đường dẫn adapter dùng
    assert "--require-hashes" in stages and "docker/semgrep.lock" in stages
    for needle in ("COPY --from=gitleaks", "COPY --from=trivy /usr/local/bin/trivy", "COPY --from=trivy /opt/trivy-cache", "COPY --from=semgrep /opt/semgrep-venv",
                   "COPY rules/semgrep /opt/qc-rules/semgrep", "gitleaks version", "trivy --version", "semgrep --version", "SEMGREP_SEND_METRICS=off"):
        assert needle in runtime, needle
    assert "USER qc" in DOCKERFILE and DOCKERFILE.rindex("USER qc") > DOCKERFILE.index("security-runtime (A)")      # vẫn chạy bằng non-root


def test_paths_match_what_the_adapters_and_the_suites_use():
    from qc_agent.adapters.trivy_adapter import DEFAULT_CACHE_DIR
    from qc_agent.scaffold import suites_security as ss
    assert DEFAULT_CACHE_DIR == "/opt/trivy-cache"
    assert yaml.safe_load(ss.sast_suite())["tasks"][0]["inputs"]["rules_dir"] == "/opt/qc-rules/semgrep"


def test_semgrep_lock_is_hash_pinned_and_covers_every_requirement():
    lock = (ROOT / "docker" / "semgrep.lock").read_text(encoding="utf-8")
    assert (ROOT / "docker" / "semgrep.in").read_text(encoding="utf-8").strip() == "semgrep==1.178.0" and "\nsemgrep==1.178.0 \\\n" in "\n" + lock
    requirements = re.findall(r"^([A-Za-z0-9_.-]+)==\S+ \\$", lock, re.M)
    hashes = re.findall(r"^\s+--hash=sha256:[0-9a-f]{64}", lock, re.M)
    assert len(requirements) > 30 and len(hashes) >= len(requirements)                             # mỗi gói có ít nhất một hash: --require-hashes cần vậy
    body = [line.strip() for line in lock.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not any(line.startswith(("-e", "git+", "--index-url", "--extra-index-url", "--find-links")) or "://" in line or " @ " in line for line in body)   # chỉ gói PyPI theo tên==phiên bản + hash


def test_semgrep_is_not_added_to_the_main_lock():
    """Semgrep ghim jsonschema/pydantic... lệch với uv.lock của qc-agent: nên nằm ở venv riêng (docker/semgrep.lock), không lẫn vào phụ thuộc chính."""
    assert "semgrep" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()


# ---------- rule Semgrep vendored ----------

def test_vendored_rules_are_well_formed_and_ids_unique():
    assert RULES, "rules/semgrep/ phải có rule (Dockerfile COPY thư mục này; rỗng ⇒ 0 finding giả)"
    ids = []
    for path in RULES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict) and isinstance(doc.get("rules"), list) and doc["rules"], path.name
        for rule in doc["rules"]:
            ids.append(rule["id"])
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", rule["id"]), rule["id"]
            assert isinstance(rule["languages"], list) and rule["languages"] and rule["severity"] in ("ERROR", "WARNING", "INFO"), rule["id"]
            assert isinstance(rule["message"], str) and 20 < len(rule["message"]) < 400, rule["id"]
            assert any(key in rule for key in ("pattern", "patterns", "pattern-either", "pattern-regex")), rule["id"]
            assert "metadata" in rule and rule["metadata"].get("category") == "security", rule["id"]
    assert len(ids) == len(set(ids))


def test_vendored_rules_map_onto_the_severities_the_adapter_understands():
    from qc_agent.adapters.semgrep_adapter import SEVERITY
    for path in RULES:
        for rule in yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]:
            assert rule["severity"] in SEVERITY            # mức lạ ⇒ adapter báo error; rule của ta không được rơi vào đó


def test_rules_are_copied_into_the_build_context():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "rules" not in [line.strip().rstrip("/") for line in ignore.splitlines()] and (ROOT / "docker" / "semgrep.lock").is_file()
    assert not any(line.strip().startswith("docker") and not line.startswith("docker-compose") and not line.startswith("!") for line in ignore.splitlines())
