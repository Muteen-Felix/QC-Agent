"""Cấu hình đa dự án: project (TẬP TRUNG ở qc-agent: configs/projects/<slug>.yaml) + suite (PHÂN TÁN ở repo SUT: <suites_dir>/<name>.yaml).

  project: slug, suites_dir, sut{files,attrs,probe_url}, modes{<mode>: policy}
  mode `pr`-kiểu : blocking_suites (mọi task phải lane=gate), advisory_suites (mọi task phải lane=discovery), on_skipped_gate_task
  mode `manual`-kiểu: suites: "*" | [tên...] (lane giữ nguyên như suite khai báo)
  suite  : {suite: <tên == tên file>, tasks: [task như trong plan]}

Policy quyết định suite nào CHẶN merge; PR sửa suite của mình không đổi được điều đó. Lane xung đột với policy là LỖI (PlanError, exit 3),
không đoán/ép: đổi lane còn kéo theo expected_result_kind (schema: gate->verdict, discovery->candidate_finding).
Không LLM, không tên worker cụ thể."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from qc_agent.core.plan import PlanError

_NAME = r"^[a-z0-9][a-z0-9_-]*$"
_NAME_LIST = {"type": "array", "items": {"type": "string", "pattern": _NAME}, "uniqueItems": True}

PROJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["slug", "modes"],
    "properties": {
        "slug": {"type": "string", "pattern": _NAME},
        "name": {"type": "string"},
        "repo": {"type": "string"},
        "suites_dir": {"type": "string", "minLength": 1},
        "sut": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "files": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "attrs": {"type": "object"},
                "probe_url": {"type": "string"},
            },
        },
        "environments": {"type": "object"},  # chỗ cho staging/perf (bước sau): chưa có ngữ nghĩa
        "modes": {
            "type": "object",
            "minProperties": 1,
            "propertyNames": {"pattern": _NAME},
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "blocking_suites": _NAME_LIST,
                    "advisory_suites": _NAME_LIST,
                    "suites": {"oneOf": [{"const": "*"}, _NAME_LIST]},
                    "on_skipped_gate_task": {"enum": ["yellow", "fail"]},
                },
                "oneOf": [
                    {"required": ["suites"], "not": {"anyOf": [{"required": ["blocking_suites"]}, {"required": ["advisory_suites"]}]}},
                    {"anyOf": [{"required": ["blocking_suites"]}, {"required": ["advisory_suites"]}], "not": {"required": ["suites"]}},
                ],
            },
        },
    },
}

SUITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suite", "tasks"],
    "properties": {
        "suite": {"type": "string", "pattern": _NAME},
        "description": {"type": "string"},
        "tasks": {"type": "array", "minItems": 1, "items": {"type": "object"}},
    },
}

_PROJECT_V = Draft202012Validator(PROJECT_SCHEMA)
_SUITE_V = Draft202012Validator(SUITE_SCHEMA)


def _errors(validator, data) -> list[str]:
    return [("/".join(map(str, e.path)) or "<root>") + ": " + e.message[:160]
            for e in sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.path)))]


def _read_yaml(path: Path, what: str) -> Any:
    try:
        return yaml.safe_load(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PlanError(f"không đọc được {what} {path}: {error}") from None


def load_project(slug: str, projects_dir) -> dict:
    path = Path(projects_dir) / f"{slug}.yaml"
    if not path.is_file():
        raise PlanError(f"không có project {slug!r}: thiếu {path}")
    data = _read_yaml(path, "project")
    errors = _errors(_PROJECT_V, data)
    if errors:
        raise PlanError(f"project {path.name} không hợp lệ: " + "; ".join(errors))
    if data["slug"] != slug:
        raise PlanError(f"project {path.name}: slug {data['slug']!r} phải trùng tên file {slug!r}")
    data.setdefault("suites_dir", ".qc-agent/suites")
    return data


def load_suites(suites_dir) -> dict[str, dict]:
    """{tên: {"name", "tasks", "sha256", "path"}}; sha256 tính trên nội dung đã chuẩn hoá CRLF->LF."""
    root = Path(suites_dir)
    if not root.is_dir():
        raise PlanError(f"thư mục suite không tồn tại: {root}")
    suites: dict[str, dict] = {}
    for path in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
        data = _read_yaml(path, "suite")
        errors = _errors(_SUITE_V, data)
        if errors:
            raise PlanError(f"suite {path.name} không hợp lệ: " + "; ".join(errors))
        if data["suite"] != path.stem:
            raise PlanError(f"suite {path.name}: tên {data['suite']!r} phải trùng tên file {path.stem!r}")
        if data["suite"] in suites:
            raise PlanError(f"suite bị trùng: {data['suite']}")
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        suites[data["suite"]] = {"name": data["suite"], "tasks": data["tasks"],
                                 "sha256": hashlib.sha256(raw).hexdigest(), "path": path}
    return suites


def _check_lanes(suite: dict, want: str, mode: str, role: str) -> None:
    for task in suite["tasks"]:
        lane = task.get("lane")
        if lane != want:
            raise PlanError(
                f"suite {suite['name']} task {task.get('task_id', '?')}: lane={lane!r} nhưng suite nằm trong "
                f"{role} của mode {mode!r} (cần lane={want}). Sửa suite cho khớp policy.")


def build_plan(project: dict, mode: str, suites: dict[str, dict], only_suites: list[str] | None = None) -> tuple[dict, dict]:
    """Ghép các suite được policy chọn thành một plan (cùng hình dạng `load_plan`) + meta để ghi vào report."""
    policies = project["modes"]
    if mode not in policies:
        raise PlanError(f"project {project['slug']}: không có mode {mode!r} (có: {', '.join(sorted(policies))})")
    policy = policies[mode]

    if "suites" in policy:
        names = sorted(suites) if policy["suites"] == "*" else list(policy["suites"])
        roles = {name: None for name in names}  # không ép lane
    else:
        blocking, advisory = list(policy.get("blocking_suites", [])), list(policy.get("advisory_suites", []))
        both = sorted(set(blocking) & set(advisory))
        if both:
            raise PlanError(f"mode {mode!r}: suite vừa blocking vừa advisory: {', '.join(both)}")
        names = blocking + advisory
        roles = {**{n: "blocking" for n in blocking}, **{n: "advisory" for n in advisory}}

    if only_suites is not None:
        wanted = list(dict.fromkeys(only_suites))
        outside = [n for n in wanted if n not in roles]
        if outside:
            raise PlanError(f"--suites chứa suite ngoài policy của mode {mode!r}: {', '.join(outside)}")
        names = [n for n in names if n in wanted]
        if not names:
            raise PlanError("--suites rỗng")

    missing = [n for n in names if n not in suites]
    if missing:
        raise PlanError(f"mode {mode!r} cần suite không có trong thư mục suite: {', '.join(missing)}")

    tasks, seen = [], {}
    for name in names:
        suite = suites[name]
        if roles[name] == "blocking":
            _check_lanes(suite, "gate", mode, "blocking_suites")
        elif roles[name] == "advisory":
            _check_lanes(suite, "discovery", mode, "advisory_suites")
        for task in suite["tasks"]:
            task_id = task.get("task_id")
            if task_id in seen:
                raise PlanError(f"task_id {task_id!r} trùng giữa suite {seen[task_id]} và {name}")
            seen[task_id] = name
            tasks.append(copy.deepcopy(task))

    sut = copy.deepcopy(project.get("sut", {}))
    plan = {"plan_version": 1, "name": f"{project['slug']}:{mode}", "sut": sut, "tasks": tasks}
    text = yaml.safe_dump(plan, allow_unicode=True, sort_keys=False)
    meta = {
        "project": project["slug"], "mode": mode,
        "suite_sha256": {n: suites[n]["sha256"] for n in names},
        "on_skipped_gate_task": policy.get("on_skipped_gate_task"),
    }
    return {"name": plan["name"], "sut": sut, "tasks": tasks, "text": text}, meta
