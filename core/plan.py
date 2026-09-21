"""Load and resolve committed plans into contract-valid task specs."""
from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from core import schema


ROOT = Path(__file__).resolve().parent.parent
CAPABILITIES_PATH = ROOT / "schemas" / "capabilities.json"
_PLAN_ONLY_KEYS = ("depends_on", "prefer", "expect_status")
_ENV_REFERENCE = re.compile(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}")
_RUN_ID_REFERENCE = re.compile(r"\$\{run_id\}")


class PlanError(ValueError):
    """A plan cannot be safely resolved into contract-valid task specs."""


def load_plan(path: str | Path) -> dict[str, Any]:
    """Load a YAML plan, retaining its original text for later plan identity hashing."""
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise PlanError(f"không đọc được plan {path}: {error}") from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise PlanError(f"plan {path}: YAML không hợp lệ: {error}") from None
    if not isinstance(raw, dict):
        raise PlanError(f"plan {path}: nội dung gốc phải là object")
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise PlanError(f"plan {path}: thiếu name hợp lệ")
    if not isinstance(raw.get("sut"), dict):
        raise PlanError(f"plan {path}: sut phải là object")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise PlanError(f"plan {path}: tasks phải là danh sách object")
    task_ids = [task.get("task_id") for task in tasks]
    if any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise PlanError(f"plan {path}: mỗi task cần task_id là chuỗi không rỗng")
    if len(set(task_ids)) != len(task_ids):
        raise PlanError(f"plan {path}: task_id bị trùng")
    if "selection" in raw and not isinstance(raw["selection"], dict):
        raise PlanError(f"plan {path}: selection phải là object")

    plan: dict[str, Any] = {
        "name": raw["name"],
        "sut": raw["sut"],
        "tasks": tasks,
        "text": text,
    }
    if "selection" in raw:
        plan["selection"] = raw["selection"]
    return plan


def _replace_string(value: str, run_ctx: Mapping[str, Any]) -> str:
    run_id = run_ctx.get("run_id")

    def replace_env(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise PlanError(f"thiếu biến môi trường {name}")
        return os.environ[name]

    value = _ENV_REFERENCE.sub(replace_env, value)
    if _RUN_ID_REFERENCE.search(value):
        if not isinstance(run_id, str) or not run_id:
            raise PlanError("thiếu run_id trong run_ctx")
        value = _RUN_ID_REFERENCE.sub(lambda _match: run_id, value)
    return value


def _replace_tree(value: Any, run_ctx: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return _replace_string(value, run_ctx)
    if isinstance(value, dict):
        return {key: _replace_tree(item, run_ctx) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_tree(item, run_ctx) for item in value]
    return value


def _context_value(run_ctx: Mapping[str, Any], name: str) -> str:
    value = run_ctx.get(name)
    if not isinstance(value, str) or not value:
        raise PlanError(f"thiếu {name} trong run_ctx")
    return value


def _capability_ids() -> set[str]:
    try:
        data = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8-sig"))
        capabilities = data["capabilities"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PlanError(f"không đọc được {CAPABILITIES_PATH}: {error}") from None
    if not isinstance(capabilities, dict):
        raise PlanError(f"{CAPABILITIES_PATH}: capabilities phải là object")
    return set(capabilities)


def resolve(task: dict, run_ctx: MutableMapping[str, Any]) -> tuple[dict, dict]:
    """Substitute task/SUT variables, split plan-only fields, and validate the task spec.

    `run_ctx` supplies `plan_id`, `run_id`, and `sut_identity_ref`. If it contains the
    plan's `sut` object, that object is substituted in place so the caller can pass the
    resolved configuration to the SUT identity step.
    """
    if not isinstance(task, dict):
        raise PlanError("task phải là object")
    if not isinstance(run_ctx, MutableMapping):
        raise PlanError("run_ctx phải là mapping có thể cập nhật")

    if "sut" in run_ctx:
        if not isinstance(run_ctx["sut"], dict):
            raise PlanError("run_ctx.sut phải là object")
        run_ctx["sut"] = _replace_tree(copy.deepcopy(run_ctx["sut"]), run_ctx)

    resolved = _replace_tree(copy.deepcopy(task), run_ctx)
    plan_only = {key: resolved.pop(key) for key in _PLAN_ONLY_KEYS if key in resolved}
    for key in ("depends_on", "prefer"):
        value = plan_only.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise PlanError(f"task {task.get('task_id', '<unknown>')}: {key} phải là danh sách chuỗi không rỗng")
        if key in plan_only:
            plan_only[key] = list(dict.fromkeys(item.strip() for item in value))
    if "expect_status" in plan_only:
        expected = plan_only["expect_status"]
        if not isinstance(expected, str) or expected not in {"pass", "fail"}:
            raise PlanError(f"task {task.get('task_id', '<unknown>')}: expect_status phải là pass hoặc fail")
    resolved["plan_id"] = _context_value(run_ctx, "plan_id")
    resolved["run_id"] = _context_value(run_ctx, "run_id")
    resolved["sut_identity_ref"] = _context_value(run_ctx, "sut_identity_ref")

    violations = schema.validate_task(resolved)
    if violations:
        raise PlanError("task " + str(resolved.get("task_id", "<unknown>")) + " không hợp lệ: " + "; ".join(violations))
    capability_id = resolved["capability"]
    if capability_id not in _capability_ids():
        raise PlanError(f"task {resolved['task_id']}: capability không được khai báo: {capability_id}")
    return resolved, plan_only


def _task_list(tasks: Sequence[dict] | Mapping[str, dict]) -> list[dict]:
    if isinstance(tasks, Mapping):
        result = []
        for task_id, task in tasks.items():
            if not isinstance(task, dict):
                raise PlanError(f"task {task_id} phải là object")
            item = dict(task)
            item.setdefault("task_id", task_id)
            result.append(item)
        return result
    if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Sequence):
        raise PlanError("tasks phải là danh sách hoặc mapping")
    return list(tasks)


def toposort(tasks: Sequence[dict] | Mapping[str, dict]) -> list[list[str]]:
    """Return stable DAG layers of task IDs; reject missing dependencies and cycles."""
    task_items = _task_list(tasks)
    order: list[str] = []
    dependencies: dict[str, set[str]] = {}
    for task in task_items:
        if not isinstance(task, dict):
            raise PlanError("mỗi task trong DAG phải là object")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise PlanError("mỗi task trong DAG cần task_id là chuỗi không rỗng")
        if task_id in dependencies:
            raise PlanError(f"task_id bị trùng: {task_id}")
        raw_deps = task.get("depends_on", [])
        if not isinstance(raw_deps, list) or any(not isinstance(dep, str) or not dep for dep in raw_deps):
            raise PlanError(f"task {task_id}: depends_on phải là danh sách task_id")
        order.append(task_id)
        dependencies[task_id] = set(raw_deps)

    all_ids = set(dependencies)
    for task_id, deps in dependencies.items():
        missing = sorted(deps - all_ids)
        if missing:
            raise PlanError(f"task {task_id}: phụ thuộc không tồn tại: {', '.join(missing)}")

    completed: set[str] = set()
    layers: list[list[str]] = []
    while len(completed) < len(order):
        layer = [task_id for task_id in order if task_id not in completed and dependencies[task_id] <= completed]
        if not layer:
            cycle_nodes = [task_id for task_id in order if task_id not in completed]
            raise PlanError("chu trình depends_on: " + ", ".join(cycle_nodes))
        layers.append(layer)
        completed.update(layer)
    return layers
