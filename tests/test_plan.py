import copy
import json
from pathlib import Path

import pytest
import yaml

from core import plan, schema


ROOT = Path(__file__).resolve().parent.parent
BASE_TASK = json.loads((ROOT / "tests" / "fixtures" / "contract" / "task.k6.json").read_text(encoding="utf-8-sig"))
for _field in ("plan_id", "run_id", "sut_identity_ref"):
    BASE_TASK.pop(_field)


def write_plan(path: Path, **overrides) -> Path:
    value = {
        "plan_version": 1,
        "name": "plan-test",
        "sut": {"files": ["toyapp"], "attrs": {"model": "stub"}},
        "tasks": [copy.deepcopy(BASE_TASK)],
    }
    value.update(overrides)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def context(**overrides):
    result = {"plan_id": "plan-test", "run_id": "r-0001", "sut_identity_ref": "sut-test"}
    result.update(overrides)
    return result


def test_load_plan_preserves_text_and_optional_selection(tmp_path):
    path = write_plan(tmp_path / "plan.yaml", selection={"floor": [BASE_TASK["task_id"]]})

    loaded = plan.load_plan(path)

    assert loaded["name"] == "plan-test"
    assert loaded["sut"] == {"files": ["toyapp"], "attrs": {"model": "stub"}}
    assert loaded["selection"] == {"floor": [BASE_TASK["task_id"]]}
    assert loaded["text"] == path.read_bytes().decode("utf-8")


def test_load_plan_rejects_duplicate_task_ids(tmp_path):
    task = copy.deepcopy(BASE_TASK)
    path = write_plan(tmp_path / "plan.yaml", tasks=[task, task])

    with pytest.raises(plan.PlanError, match="task_id bị trùng"):
        plan.load_plan(path)


def test_resolve_substitutes_task_and_sut_splits_plan_keys_and_validates(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    task = copy.deepcopy(BASE_TASK)
    task["target"]["base_url"] = "${env.APP_BASE_URL}/run/${run_id}"
    task["depends_on"] = ["t-000"]
    task["prefer"] = [" k6 ", "beta", "k6"]
    task["expect_status"] = "pass"
    run_context = context(sut={"probe_url": "${env.APP_BASE_URL}/__qc/config/${run_id}"})

    spec, plan_only = plan.resolve(task, run_context)

    assert spec["target"]["base_url"] == "http://127.0.0.1:8000/run/r-0001"
    assert run_context["sut"]["probe_url"] == "http://127.0.0.1:8000/__qc/config/r-0001"
    assert {key: spec[key] for key in ("plan_id", "run_id", "sut_identity_ref")} == {
        "plan_id": "plan-test", "run_id": "r-0001", "sut_identity_ref": "sut-test"
    }
    assert plan_only == {"depends_on": ["t-000"], "prefer": ["k6", "beta"], "expect_status": "pass"}
    assert schema.validate_task(spec) == []


def test_resolve_missing_env_names_variable(monkeypatch):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    task = copy.deepcopy(BASE_TASK)
    task["target"]["base_url"] = "${env.APP_BASE_URL}"

    with pytest.raises(plan.PlanError, match="APP_BASE_URL"):
        plan.resolve(task, context())


def test_resolve_missing_context_ids_is_plan_error():
    with pytest.raises(plan.PlanError, match="sut_identity_ref"):
        plan.resolve(copy.deepcopy(BASE_TASK), {"plan_id": "plan-test", "run_id": "r-0001"})


def test_resolve_rejects_schema_invalid_task():
    task = copy.deepcopy(BASE_TASK)
    task.pop("target")

    with pytest.raises(plan.PlanError, match="target"):
        plan.resolve(task, context())


def test_resolve_rejects_unregistered_capability():
    task = copy.deepcopy(BASE_TASK)
    task["capability"] = "unknown.capability"

    with pytest.raises(plan.PlanError, match="capability không được khai báo"):
        plan.resolve(task, context())


@pytest.mark.parametrize(
    "field,value",
    [("prefer", False), ("prefer", ["k6", {"bad": "worker"}]), ("depends_on", "t-001"),
     ("expect_status", "skipped")],
)
def test_resolve_rejects_malformed_plan_only_fields(field, value):
    task = copy.deepcopy(BASE_TASK)
    task[field] = value

    with pytest.raises(plan.PlanError, match=field):
        plan.resolve(task, context())


def test_toposort_returns_stable_layers():
    tasks = [
        {"task_id": "t-002", "depends_on": ["t-001"]},
        {"task_id": "t-001"},
        {"task_id": "t-003"},
        {"task_id": "t-004", "depends_on": ["t-002", "t-003"]},
    ]

    assert plan.toposort(tasks) == [["t-001", "t-003"], ["t-002"], ["t-004"]]


def test_toposort_reports_cycles_and_missing_dependencies():
    with pytest.raises(plan.PlanError, match="chu trình"):
        plan.toposort([{"task_id": "t-001", "depends_on": ["t-002"]},
                       {"task_id": "t-002", "depends_on": ["t-001"]}])
    with pytest.raises(plan.PlanError, match="không tồn tại"):
        plan.toposort([{"task_id": "t-001", "depends_on": ["missing"]}])
