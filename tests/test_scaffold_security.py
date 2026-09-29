"""Suite Security do `init` sinh ra + manifest của ba worker: hợp lệ với bộ nạp/adapter thật, đúng thỏa thuận (task_id, ngưỡng, base_url)."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from qc_agent.adapters import gitleaks_adapter, semgrep_adapter, trivy_adapter
from qc_agent.core import plan as plan_lib
from qc_agent.core import project as pj
from qc_agent.core import registry
from qc_agent.scaffold import init as init_mod
from qc_agent.scaffold import suites_security as ss
from qc_agent.scaffold import templates as t

ROOT = Path(__file__).resolve().parent.parent
NOTEBOARD = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
SUITES = {"sast": ("t-010", "code.sast", "semgrep"), "secrets": ("t-011", "code.secret", "gitleaks"), "deps": ("t-012", "deps.vuln", "trivy")}
ADAPTERS = {"semgrep": semgrep_adapter.SemgrepAdapter, "gitleaks": gitleaks_adapter.GitleaksAdapter, "trivy": trivy_adapter.TrivyAdapter}


def generated() -> dict[str, str]:
    files = {}
    ss.add_suites(lambda rel, content: files.__setitem__(rel, content), None)
    return files


def task_of(text: str) -> dict:
    return yaml.safe_load(text)["tasks"][0]


# ---------- add_suites ----------

def test_add_suites_writes_exactly_the_three_suite_files():
    files = generated()
    assert sorted(files) == [".qc-agent/suites/deps.yaml", ".qc-agent/suites/sast.yaml", ".qc-agent/suites/secrets.yaml"]


@pytest.mark.parametrize("name", sorted(SUITES))
def test_suite_matches_the_agreed_shape(name):
    task_id, capability, worker = SUITES[name]
    doc = yaml.safe_load(generated()[f".qc-agent/suites/{name}.yaml"])
    task = doc["tasks"][0]
    assert doc["suite"] == name and len(doc["tasks"]) == 1                    # tên suite = tên file (core-rules)
    assert task["task_id"] == task_id and task["capability"] == capability and task["lane"] == "gate" and task["prefer"] == [worker]
    assert task["target"]["base_url"] == "${env.APP_BASE_URL}"                # bắt buộc bởi schema, adapter bỏ qua
    assert task["oracle"]["kind"] == "threshold" and task["expected_result_kind"] == "verdict"
    assert task["retry"] == {"max": 1, "on": ["error"]} and task["budget"]["tokens"] == 0 and task["evidence_required"] == ["raw_output", "stdout"]


def assertions(name):
    return [(a["metric"], a["op"], a["value"]) for a in task_of(generated()[f".qc-agent/suites/{name}.yaml"])["oracle"]["assertions"]]


def test_thresholds_live_in_the_suite_not_in_the_adapter():
    assert ("semgrep.high", "==", 0) in assertions("sast") and ("semgrep.critical", "==", 0) in assertions("sast")   # critical đếm riêng: phải chặn riêng
    assert ("semgrep.files_scanned", ">=", 1) in assertions("sast")
    assert assertions("secrets") == [("gitleaks.count", "==", 0)]
    assert assertions("deps") == [("trivy.critical", "==", 0), ("trivy.high", "==", 0), ("trivy.db_age_days", "<=", 14)]


def test_every_asserted_metric_is_one_the_adapter_emits(tmp_path):
    """Chống lệch tên: assertion nhắc metric mà adapter không đo sẽ là OracleError => error ở mọi lần chạy."""
    emitted = {
        "sast": {f"semgrep.{k}" for k in ("critical", "high", "medium", "low", "total", "files_scanned")},
        "secrets": {"gitleaks.count"},
        "deps": {f"trivy.{k}" for k in ("critical", "high", "medium", "low", "unknown", "total", "targets", "db_age_days")},
    }
    for name in SUITES:
        assert {metric for metric, _, _ in assertions(name)} <= emitted[name]


def test_default_suites_carry_no_todo_but_a_guessed_path_can_ask_for_verification():
    """Bộ cấu hình `init` sinh ra phải sạch dấu TODO khi không có chỗ nào được ĐOÁN (test_scaffold_validate dựa vào điều này)."""
    assert all(t.TODO not in text for text in generated().values())
    text = ss.sast_suite(scan_paths=("src",), verify="thư mục nguồn do scanner đoán")
    assert t.TODO in text and "VERIFY" in text and task_of(text)["inputs"]["paths"] == ["src"]


def test_scan_paths_are_data_not_yaml_structure():
    text = ss.sast_suite(scan_paths=("apps/api-server", "src"))
    assert task_of(text)["inputs"]["paths"] == ["apps/api-server", "src"] and t.TODO not in text
    for bad in ("../x", "a: b\n  evil: 1", "-rf", "a b", ""):
        with pytest.raises(t.TemplateError):
            ss.sast_suite(scan_paths=(bad,))
    with pytest.raises(t.TemplateError):
        ss.sast_suite(scan_paths=())


# ---------- qua bộ nạp thật + adapter thật ----------

@pytest.fixture
def resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "http://sut:8000")
    sut = tmp_path / "sut"
    (sut / ".qc-agent" / "suites").mkdir(parents=True)
    for rel, content in generated().items():
        (sut / rel).write_text(content, encoding="utf-8")
    suites = pj.load_suites(sut / ".qc-agent" / "suites")
    assert sorted(suites) == sorted(SUITES)
    ctx = {"plan_id": "p", "run_id": "r-0001", "runs_dir": str(tmp_path / "runs"), "sut_identity_ref": "s"}
    return {name: plan_lib.resolve(suites[name]["tasks"][0], dict(ctx))[0] for name in suites}   # PlanError nếu vi phạm contract


def test_every_generated_task_is_a_contract_valid_spec(resolved):
    for name, (task_id, capability, _) in SUITES.items():
        assert resolved[name]["task_id"] == task_id and resolved[name]["capability"] == capability
        assert resolved[name]["target"]["base_url"] == "http://sut:8000"


def test_adapters_accept_the_generated_inputs(resolved, tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "r.yaml").write_text("rules: []\n", encoding="utf-8")
    resolved["sast"]["inputs"]["rules_dir"] = str(rules)       # /opt/qc-rules chỉ có trong image
    for name, (_, _, worker) in SUITES.items():
        workdir = tmp_path / name
        workdir.mkdir()
        cmd = ADAPTERS[worker]().build_cmd(resolved[name], workdir)
        assert cmd[0] == worker
    assert semgrep_adapter.SemgrepAdapter().build_cmd(resolved["sast"], tmp_path)[-1] == "."


def test_generated_suites_route_to_the_shipped_workers(resolved):
    workers = registry.load_many([ROOT / "workers"])
    for worker in workers.values():
        worker.probe_ok = True       # định tuyến, không phải môi trường: máy này không có công cụ
    for name, (_, _, expected) in SUITES.items():
        picked, reason = registry.pick(workers, resolved[name], (expected,))
        assert picked is not None and picked.name == expected, reason


# ---------- manifest ----------

@pytest.mark.parametrize("worker, capability, binary, egress", [("semgrep", "code.sast", "semgrep", ["source_code"]), ("gitleaks", "code.secret", "gitleaks", ["source_code"]),
                                                               ("trivy", "deps.vuln", "trivy", ["source_code"])])
def test_manifest(worker, capability, binary, egress):
    loaded = registry.load_many([ROOT / "workers"])[worker]
    assert loaded.lanes == ["gate"] and loaded.requires == {"env": [], "binaries": [binary]} and loaded.data_egress == egress
    assert loaded.capabilities[capability]["oracle_kinds"] == ["threshold"] and loaded.capabilities[capability]["parallel_safe"] is True
    known = json.loads((ROOT / "schemas" / "capabilities.json").read_text(encoding="utf-8"))["capabilities"]
    assert capability in known
    module = __import__(loaded.module, fromlist=["x"])                      # manifest trỏ tới module có thật, có tên worker khớp
    assert next(c for c in vars(module).values() if isinstance(c, type) and getattr(c, "NAME", None) == worker)


def test_missing_binary_makes_the_probe_fail_not_pass(monkeypatch):
    """Thiếu công cụ ⇒ probe hỏng ⇒ engine không chọn được worker ⇒ task skipped (⇒ FAIL với on_skipped_gate_task: fail); không có đường nào thành xanh."""
    monkeypatch.setattr(registry.shutil, "which", lambda name: None)
    for worker in registry.load_many([ROOT / "workers"]).values():
        if worker.name in ADAPTERS:
            assert registry.probe(worker).probe_ok is False and "thiếu binary" in worker.probe_reason


# ---------- init thật ----------

def test_init_generates_the_suites_and_leaves_existing_files_alone(tmp_path):
    sut = tmp_path / "sut"
    shutil.copytree(NOTEBOARD, sut, ignore=shutil.ignore_patterns("__pycache__", ".qc-agent", "runs"))
    plan = init_mod.build(init_mod.Options(sut_root=sut, slug="noteboard"))
    labels = [p.label for p in plan.files]
    assert {".qc-agent/suites/sast.yaml", ".qc-agent/suites/secrets.yaml", ".qc-agent/suites/deps.yaml"} <= set(labels)
    outcomes = init_mod.apply(plan)
    assert all(o.status == "created" for o in outcomes if o.label.startswith(".qc-agent/suites/s") or o.label.endswith("deps.yaml"))
    sast = (sut / ".qc-agent" / "suites" / "sast.yaml").read_text(encoding="utf-8")
    (sut / ".qc-agent" / "suites" / "sast.yaml").write_text(sast + "# do người sửa\n", encoding="utf-8")
    again = {o.label: o.status for o in init_mod.apply(init_mod.build(init_mod.Options(sut_root=sut, slug="noteboard")))}
    assert again[".qc-agent/suites/sast.yaml"] == "kept" and "# do người sửa" in (sut / ".qc-agent" / "suites" / "sast.yaml").read_text(encoding="utf-8")


def test_init_still_generates_security_suites_with_no_api(tmp_path):
    sut = tmp_path / "sut"
    (sut / "web").mkdir(parents=True)
    plan = init_mod.build(init_mod.Options(sut_root=sut, slug="ui-only", no_api=True, ui_dockerfile="Dockerfile.ui"))
    assert ".qc-agent/suites/secrets.yaml" in [p.label for p in plan.files]
