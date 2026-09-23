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
import os
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from qc_agent import settings
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
        "sut_checkout": {"type": "string", "minLength": 1},  # thư mục checkout repo SUT TRÊN MÁY SERVICE (tương đối => theo project root)
        "environments": {  # môi trường chạy thủ công (staging, perf...): cấu hình TẬP TRUNG, người dùng chỉ chọn theo tên
            "type": "object",
            "propertyNames": {"pattern": _NAME},
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string"},
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},  # giá trị có thể là ${env.TÊN} (bí mật của server)
                    "concurrency_key": {"type": "string", "minLength": 1},  # 1 job / khoá (vd. perf trên staging dùng chung)
                    "timeout_s": {"type": "number", "exclusiveMinimum": 0},
                },
            },
        },
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


_ENV_REF = re.compile(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}")


def list_projects(projects_dir) -> dict[str, dict]:
    """Nạp và validate MỌI project trong thư mục; một file hỏng làm cả danh sách lỗi (fail-fast lúc khởi động service)."""
    root = Path(projects_dir)
    if not root.is_dir():
        raise PlanError(f"thư mục project không tồn tại: {root}")
    return {p.stem: load_project(p.stem, root) for p in sorted(root.glob("*.yaml"))}


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class ProjectResolver:
    """Từ cấu hình project (tập trung) suy ra: checkout của SUT, biến môi trường, khoá đồng thời, timeout của một environment.
    Dùng chung cho API (validate lúc tạo job) và executor (lúc chạy). Bí mật `${env.X}` chỉ được thay ở executor, không bao giờ vào DB/API."""

    def __init__(self, projects_dir, project_root=None):
        self.projects_dir = Path(projects_dir)
        self.project_root = Path(project_root) if project_root else settings.get().project_root

    def project(self, slug: str) -> dict:
        return load_project(slug, self.projects_dir)

    def sut_checkout(self, slug: str) -> Path | None:
        checkout = self.project(slug).get("sut_checkout")
        if not checkout:
            return None
        path = Path(checkout)
        return (path if path.is_absolute() else self.project_root / path).resolve()

    def environment(self, slug: str, name: str | None) -> dict | None:
        if not name:
            return None
        envs = self.project(slug).get("environments") or {}
        if name not in envs:
            raise PlanError(f"project {slug}: không có environment {name!r} (có: {', '.join(sorted(envs)) or 'không có'})")
        return envs[name]

    def env_vars(self, slug: str, name: str | None) -> dict[str, str]:
        """Biến môi trường của environment với `${env.X}` lấy từ môi trường của TIẾN TRÌNH NÀY (secret của server)."""
        environment = self.environment(slug, name)
        out = {}
        for key, value in ((environment or {}).get("env") or {}).items():
            def sub(match):
                if match.group(1) not in os.environ:
                    raise PlanError(f"environment {name!r} cần biến môi trường {match.group(1)} trên máy chủ")
                return os.environ[match.group(1)]
            out[key] = _ENV_REF.sub(sub, value)
        return out
