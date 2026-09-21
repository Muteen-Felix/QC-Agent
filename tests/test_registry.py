import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from core import registry


def manifest(name="worker", **overrides):
    data = {
        "name": name,
        "version_probe": [sys.executable, "--version"],
        "adapter": "adapters/example_adapter.py",
        "lanes": ["gate"],
        "capabilities": [{"id": "demo.echo", "oracle_kinds": ["trivial", "checks"]}],
        "requires": {"env": [], "binaries": []},
        "data_egress": [],
    }
    data.update(overrides)
    return data


def write_manifest(folder: Path, data: dict, filename: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (filename or f"{data.get('name', 'worker')}.yaml")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def task(**overrides):
    result = {"capability": "demo.echo", "lane": "gate", "oracle": {"kind": "trivial"}}
    result.update(overrides)
    return result


def ready_worker(name="worker", **overrides):
    data = manifest(name, **overrides)
    return registry.Worker(
        name=data["name"],
        adapter=data["adapter"],
        module="adapters.example_adapter",
        lanes=data["lanes"],
        capabilities={entry["id"]: entry for entry in data["capabilities"]},
        requires=data["requires"],
        version_probe=data["version_probe"],
        data_egress=data["data_egress"],
        probe_ok=True,
        probe_reason=None,
        version="Python 3.x",
    )


def test_load_skips_underscore_template_and_derives_module(tmp_path):
    write_manifest(tmp_path, {}, filename="_template.yaml")
    write_manifest(tmp_path, manifest("mock", adapter=r"adapters\mock_adapter.py"))

    workers = registry.load(tmp_path)

    assert list(workers) == ["mock"]
    assert workers["mock"].module == "adapters.mock_adapter"
    assert workers["mock"].capabilities["demo.echo"]["oracle_kinds"] == ["trivial", "checks"]


def test_missing_binary_marks_probe_failed_with_specific_reason(tmp_path):
    write_manifest(tmp_path, manifest(requires={"env": [], "binaries": ["qc-missing-test-binary"]}))
    worker = registry.load(tmp_path)["worker"]

    result = registry.probe(worker)

    assert result is worker
    assert not worker.probe_ok
    assert worker.probe_reason == "thiếu binary qc-missing-test-binary"


def test_empty_required_environment_variable_marks_probe_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("QC_REGISTRY_EMPTY_TEST", "  ")
    write_manifest(tmp_path, manifest(requires={"env": ["QC_REGISTRY_EMPTY_TEST"], "binaries": []}))
    worker = registry.load(tmp_path)["worker"]

    registry.probe(worker)

    assert not worker.probe_ok
    assert worker.probe_reason == "thiếu biến môi trường QC_REGISTRY_EMPTY_TEST"


def test_probe_records_first_stdout_line(tmp_path):
    worker = ready_worker(version_probe=[sys.executable, "-c", "print('version 1'); print('extra')"])
    worker.probe_ok = False

    registry.probe(worker)

    assert worker.probe_ok
    assert worker.version == "version 1"
    assert worker.probe_reason is None


def test_probe_timeout_is_reported(tmp_path, monkeypatch):
    worker = ready_worker()

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(registry.subprocess, "run", timeout)
    registry.probe(worker)

    assert not worker.probe_ok
    assert worker.probe_reason == "version_probe timeout"


def test_pick_rejects_wrong_lane_and_oracle_kind():
    worker = ready_worker()

    picked, reason = registry.pick({worker.name: worker}, task(lane="discovery"))
    assert picked is None and "lane/oracle" in reason

    picked, reason = registry.pick({worker.name: worker}, task(oracle={"kind": "threshold"}))
    assert picked is None and "lane/oracle" in reason


def test_pick_applies_preference_then_stable_name_order():
    workers = {worker.name: worker for worker in (ready_worker("zeta"), ready_worker("alpha"), ready_worker("beta"))}

    preferred, _ = registry.pick(workers, task(), prefer=(" zeta ", "beta", "zeta"))
    stable, _ = registry.pick(workers, task())

    assert preferred.name == "zeta"
    assert stable.name == "alpha"


def test_pick_distinguishes_absent_capability_from_probe_failure():
    worker = ready_worker("broken")
    worker.probe_ok = False
    worker.probe_reason = "thiếu biến môi trường API_KEY"
    workers = {worker.name: worker}

    missing, missing_reason = registry.pick(workers, task(capability="api.property"))
    broken, broken_reason = registry.pick(workers, task())

    assert missing is None and "không worker nào có capability này" in missing_reason
    assert broken is None and "có worker nhưng probe hỏng" in broken_reason
    assert "thiếu biến môi trường API_KEY" in broken_reason


def test_load_reports_manifest_filename_when_required_field_is_missing(tmp_path):
    path = write_manifest(tmp_path, {"name": "broken"}, filename="broken.yaml")

    with pytest.raises(registry.ManifestError, match=path.name):
        registry.load(tmp_path)
