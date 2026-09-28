"""A-7: các ca âm tính — chỗ phân biệt gate thật với gate trang trí.

Mỗi ca chạy qua ENGINE + RUNNER THẬT (spawn adapter bằng `python -m`, oracle, kiểm contract, verdict gate, exit code), với suite Security do `init` sinh ra
(ngưỡng thật nằm trong file suite). Chỉ bước "chạy công cụ" được thay bằng công cụ giả (tests/fixtures/workers/fake_security.py) — nên không cần mạng, không cần
cài semgrep/gitleaks/trivy. Bất biến kiểm ở mọi ca hỏng: KHÔNG có đường nào dẫn tới pass."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from qc_agent.core import engine
from qc_agent.scaffold import suites_security as ss
from tests.fixtures.workers.fake_security import LEAKED

ROOT = Path(__file__).resolve().parent.parent
SUITE = {"semgrep": (ss.sast_suite, "t-010"), "gitleaks": (ss.secrets_suite, "t-011"), "trivy": (ss.deps_suite, "t-012")}
TOOLS = sorted(SUITE)
BAD_BINARY = "qc-no-such-binary-xyz"
COUNTERS = ("critical", "high", "medium", "low", "unknown", "total", "count")


def real_task(tool: str, tmp_path: Path, **inputs) -> dict:
    """Task đúng như suite mà `init` sinh (capability, oracle, ngưỡng, budget, retry), chỉ điền đường dẫn của môi trường test."""
    make, _ = SUITE[tool]
    task = yaml.safe_load(make())["tasks"][0]
    task["target"]["base_url"] = "http://sut:8000"
    if tool == "semgrep":
        rules = tmp_path / "rules"
        rules.mkdir(parents=True, exist_ok=True)
        (rules / "r.yaml").write_text("rules: []\n", encoding="utf-8")
        task["inputs"]["rules_dir"] = str(rules)
    if tool == "trivy":
        cache = tmp_path / "trivy-cache"
        (cache / "db").mkdir(parents=True, exist_ok=True)
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (cache / "db" / "metadata.json").write_text(json.dumps({"Version": 2, "UpdatedAt": fresh}), encoding="utf-8")
        task["inputs"]["cache_dir"] = str(cache)
    task["inputs"].update(inputs)
    return task


def write_workers(tmp_path: Path, tool: str, *, binary: str | None = None) -> Path:
    """Manifest = bản sao manifest THẬT của worker (capability, lane, oracle_kinds giữ nguyên), chỉ đổi adapter -> công cụ giả và probe."""
    manifest = yaml.safe_load((ROOT / "workers" / f"{tool}.yaml").read_text(encoding="utf-8"))
    manifest["adapter"] = f"tests/fixtures/workers/fake_{tool}.py"
    manifest["version_probe"] = [sys.executable, "--version"]
    manifest["requires"] = {"env": [], "binaries": [binary] if binary else []}
    directory = tmp_path / "workers"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{tool}.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return directory


def run_gate(tmp_path, monkeypatch, tool: str, mode: str = "sample", *, on_skipped: str = "fail", binary: str | None = None, also: str | None = None, **inputs):
    """(RunResult, result.json của task, số lần công cụ giả được gọi). `also` = công cụ thứ hai (binary bình thường) cùng plan, để gate có thêm một task gating."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FAKE_TOOL_MODE", mode)
    log = tmp_path / "fake-tool.log"
    monkeypatch.setenv("FAKE_TOOL_LOG", str(log))
    task = real_task(tool, tmp_path, **inputs)
    tasks = [task] + ([real_task(also, tmp_path / "also")] if also else [])
    plan = {"plan_version": 1, "name": f"negative-{tool}-{mode}", "sut": {"files": ["tests/fixtures/sut/noteboard/toyapp"], "attrs": {}}, "tasks": tasks}
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
    write_workers(tmp_path, tool, binary=binary)
    if also:
        write_workers(tmp_path, also)
    result = engine.run_plan(plan_path, tmp_path / "runs", workers_dirs=[tmp_path / "workers"], on_skipped_gate_task=on_skipped)
    task_json = json.loads((result.run_dir / "results" / f"{task['task_id']}.json").read_text(encoding="utf-8"))
    calls = len(log.read_text(encoding="utf-8").split()) if log.exists() else 0
    return result, task_json, calls


def whole_run_text(run_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in run_dir.rglob("*") if p.is_file())


# ---------- 1. thiếu binary ----------

@pytest.mark.parametrize("tool", TOOLS)
def test_missing_binary_skips_the_task_and_the_gate_fails(tmp_path, monkeypatch, tool):
    """Gỡ công cụ khỏi image không được là cách "tắt gate" mà không ai biết."""
    result, task, calls = run_gate(tmp_path, monkeypatch, tool, binary=BAD_BINARY, on_skipped="fail")
    assert task["status"] == "skipped" and BAD_BINARY in task["verdict"]["rationale"] and task["verdict"]["gating"] is False
    assert result.exit_code == 1 and result.gate.value == "FAIL" and calls == 0
    assert any("skipped" in reason for _, reason in result.gate.reasons)


@pytest.mark.parametrize("tool", TOOLS)
def test_alongside_a_passing_gate_task_only_the_fail_policy_makes_a_skipped_one_red(tmp_path, monkeypatch, tool):
    """Đối chứng: khi còn một task gate khác đạt, chính chính sách `on_skipped_gate_task: fail` mới làm gate đỏ — nên policy mặc định của mode pr phải có nó."""
    other = "semgrep" if tool != "semgrep" else "gitleaks"
    result, task, _ = run_gate(tmp_path / "yellow", monkeypatch, tool, "empty", binary=BAD_BINARY, on_skipped="yellow", also=other)
    assert task["status"] == "skipped" and result.gate.value == "YELLOW" and result.exit_code == 0        # không có policy: task chặn biến mất mà gate vẫn xanh (vàng)
    result, task, _ = run_gate(tmp_path / "red", monkeypatch, tool, "empty", binary=BAD_BINARY, on_skipped="fail", also=other)
    assert task["status"] == "skipped" and result.gate.value == "FAIL" and result.exit_code == 1
    default = yaml.safe_load((ROOT / "configs" / "projects" / "_default.yaml").read_text(encoding="utf-8"))
    assert default["modes"]["pr"]["on_skipped_gate_task"] == "fail"


@pytest.mark.parametrize("tool", TOOLS)
def test_a_gate_whose_only_task_is_skipped_fails_even_without_the_policy(tmp_path, monkeypatch, tool):
    """Chốt chặn thứ hai của gate: không còn result gating nào thì không có gate, không phải "xanh vì rỗng"."""
    result, task, _ = run_gate(tmp_path, monkeypatch, tool, binary=BAD_BINARY, on_skipped="yellow")
    assert task["status"] == "skipped" and result.gate.value == "FAIL" and result.exit_code == 1


# ---------- 2. công cụ chết ----------

@pytest.mark.parametrize("tool", TOOLS)
def test_a_crashed_tool_is_error_never_pass(tmp_path, monkeypatch, tool):
    result, task, calls = run_gate(tmp_path, monkeypatch, tool, "crash")     # exit 137, không ghi file
    assert task["status"] == "error" and task["verdict"]["gating"] is False and task["verdict"]["value"] != "pass"
    assert task["verdict"]["rationale"].startswith("parse:") and "137" in task["verdict"]["rationale"]
    assert result.exit_code == 1 and result.gate.value == "FAIL" and any("error" in reason for _, reason in result.gate.reasons)
    assert calls == 2                                                       # retry đúng MỘT lần cho error (hạ tầng), không hơn


@pytest.mark.parametrize("tool", TOOLS)
def test_a_tool_that_exits_zero_but_writes_nothing_is_error(tmp_path, monkeypatch, tool):
    result, task, _ = run_gate(tmp_path, monkeypatch, tool, "no-output")
    assert task["status"] == "error" and result.exit_code == 1


# ---------- 3. output hỏng ----------

@pytest.mark.parametrize("mode", ["truncated", "syntax-error"])
@pytest.mark.parametrize("tool", TOOLS)
def test_broken_json_is_error_labelled_infrastructure(tmp_path, monkeypatch, tool, mode):
    result, task, _ = run_gate(tmp_path, monkeypatch, tool, mode)
    assert task["status"] == "error" and "không đọc được" in task["verdict"]["rationale"] and task["verdict"]["rationale"].startswith("parse:")
    assert result.exit_code == 1 and result.gate.value == "FAIL"
    assert task["metrics"] == {} and task["findings"] == []                 # không có số đo nào bị "bịa" từ file hỏng


def test_semgrep_errors_array_is_never_green(tmp_path, monkeypatch):
    """Rule hỏng/file không parse được mà semgrep vẫn exit 0 và `results: []`: không quét đủ, không được xanh."""
    result, task, _ = run_gate(tmp_path, monkeypatch, "semgrep", "semgrep-errors")
    assert task["status"] == "error" and "1 lỗi" in task["verdict"]["rationale"] and result.exit_code == 1


def test_trivy_without_a_baked_db_is_error_even_if_the_report_says_zero_cve(tmp_path, monkeypatch):
    empty_cache = tmp_path / "no-db-cache"
    (empty_cache / "db").mkdir(parents=True)                                 # có thư mục nhưng không có DB (metadata.json)
    result, task, _ = run_gate(tmp_path, monkeypatch, "trivy", "empty", cache_dir=str(empty_cache))
    assert task["status"] == "error" and "thiếu DB CVE nướng sẵn" in task["verdict"]["rationale"] and result.exit_code == 1


def test_trivy_with_no_lockfile_is_error_not_zero_cve(tmp_path, monkeypatch):
    result, task, _ = run_gate(tmp_path, monkeypatch, "trivy", "no-targets")    # Results rỗng: repo không có manifest nào để quét
    assert task["status"] == "error" and "không tìm thấy manifest/lockfile" in task["verdict"]["rationale"] and result.exit_code == 1


def test_trivy_stale_db_is_a_red_gate_but_not_an_infrastructure_error(tmp_path, monkeypatch):
    stale = tmp_path / "stale-cache"
    (stale / "db").mkdir(parents=True)
    old = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (stale / "db" / "metadata.json").write_text(json.dumps({"UpdatedAt": old}), encoding="utf-8")
    result, task, _ = run_gate(tmp_path, monkeypatch, "trivy", "empty", cache_dir=str(stale))
    assert task["status"] == "fail" and task["metrics"]["trivy.db_age_days"] >= 45 and result.exit_code == 1      # đỏ vì dữ liệu cũ: đo được, do suite chặn
    assert any(f["title"].startswith("trivy.db_age_days") for f in task["findings"])


# ---------- 4. rỗng nhưng hợp lệ ----------

@pytest.mark.parametrize("tool", TOOLS)
def test_a_valid_empty_report_passes_with_every_counter_at_zero(tmp_path, monkeypatch, tool):
    result, task, _ = run_gate(tmp_path, monkeypatch, tool, "empty")
    assert task["status"] == "pass" and task["verdict"]["gating"] is True and task["findings"] == []
    assert result.exit_code == 0 and result.gate.value == "PASS"
    counters = {name: value for name, value in task["metrics"].items() if name.rsplit(".", 1)[1] in COUNTERS}
    assert counters and all(value == 0 for value in counters.values())      # "sạch" = có đủ số đo và bằng 0, không phải "không đo được"


# ---------- 5. vượt ngưỡng ----------

def test_semgrep_high_finding_fails_with_the_exact_location(tmp_path, monkeypatch):
    result, task, _ = run_gate(tmp_path, monkeypatch, "semgrep", "sample")
    titles = [f["title"] for f in task["findings"]]
    assert task["status"] == "fail" and task["verdict"]["gating"] is True and result.exit_code == 1 and result.gate.value == "FAIL"
    assert "qc-rules.python-subprocess-shell-true @ apps/api-server/app/api/jobs.py:42" in titles      # finding của công cụ: đúng path:dòng
    assert "semgrep.high = 1 vi phạm == 0" in titles                                                     # finding của oracle: đúng ngưỡng của suite
    assert task["metrics"]["semgrep.high"] == 1 and task["verdict"]["value"] == "fail"


def test_gitleaks_finding_fails_with_the_exact_location(tmp_path, monkeypatch):
    result, task, _ = run_gate(tmp_path, monkeypatch, "gitleaks", "sample")
    titles = [f["title"] for f in task["findings"]]
    assert task["status"] == "fail" and result.exit_code == 1
    assert "generic-api-key @ apps/api-server/app/config.py:12" in titles and "gitleaks.count = 2 vi phạm == 0" in titles


def test_trivy_critical_finding_fails_and_names_the_lockfile(tmp_path, monkeypatch):
    result, task, _ = run_gate(tmp_path, monkeypatch, "trivy", "sample")
    titles = [f["title"] for f in task["findings"]]
    assert task["status"] == "fail" and result.exit_code == 1
    assert "CVE-2022-0000 example-lib@1.0.0 @ package-lock.json" in titles
    assert "trivy.critical = 1 vi phạm == 0" in titles and "trivy.high = 2 vi phạm == 0" in titles


# ---------- 6. gitleaks rò secret ----------

def test_a_leaky_gitleaks_report_is_refused_and_the_secret_is_nowhere(tmp_path, monkeypatch):
    result, task, calls = run_gate(tmp_path, monkeypatch, "gitleaks", "leak")
    assert task["status"] == "error" and "chưa được REDACTED" in task["verdict"]["rationale"] and result.exit_code == 1 and result.gate.value == "FAIL"
    assert calls == 2 and not list(result.run_dir.rglob("gitleaks.json"))     # báo cáo bị xoá ở CẢ hai lần thử: không nằm lại để bị upload làm artifact
    assert LEAKED not in whole_run_text(result.run_dir) and LEAKED not in json.dumps(task) and LEAKED not in result.report_md


# ---------- 7. đường dẫn hiểm ----------

HOSTILE = {"semgrep": ("paths", ["../../etc"]), "gitleaks": ("source", "../.."), "trivy": ("path", "../../etc")}


@pytest.mark.parametrize("tool", TOOLS)
def test_path_traversal_is_rejected_before_the_tool_ever_runs(tmp_path, monkeypatch, tool):
    key, value = HOSTILE[tool]
    result, task, calls = run_gate(tmp_path, monkeypatch, tool, "sample", **{key: value})
    assert task["status"] == "error" and "'..'" in task["verdict"]["rationale"] and result.exit_code == 1
    assert calls == 0 and task["findings"] == [] and task["metrics"] == {}    # công cụ không được gọi, kết quả không phải pass


@pytest.mark.parametrize("bad", ["/etc/passwd", "C:\\Windows", "--config=evil", "-x", "a/../../b"])
@pytest.mark.parametrize("tool", TOOLS)
def test_absolute_paths_and_flag_injection_are_rejected(tmp_path, monkeypatch, tool, bad):
    key, value = HOSTILE[tool]
    _, task, calls = run_gate(tmp_path, monkeypatch, tool, "sample", **{key: [bad] if isinstance(value, list) else bad})
    assert task["status"] == "error" and task["verdict"]["rationale"].startswith("parse:") and calls == 0


# ---------- bất biến chung ----------

@pytest.mark.parametrize("mode", ["crash", "no-output", "truncated", "syntax-error"])
@pytest.mark.parametrize("tool", TOOLS)
def test_no_broken_scan_ever_yields_a_passing_gate(tmp_path, monkeypatch, tool, mode):
    result, task, _ = run_gate(tmp_path, monkeypatch, tool, mode)
    assert task["status"] != "pass" and result.exit_code != 0 and result.gate.value != "PASS"
