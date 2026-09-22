"""Load worker manifests, probe their local requirements, and route tasks deterministically."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


PROBE_TIMEOUT_SECONDS = 20


class ManifestError(ValueError):
    """A worker manifest is malformed or incomplete."""


@dataclass
class Worker:
    name: str
    adapter: str
    module: str
    lanes: list[str]
    capabilities: dict[str, dict[str, Any]]
    requires: dict[str, list[str]] = field(default_factory=dict)
    version_probe: str | list[str] = ""
    data_egress: list[str] = field(default_factory=list)
    probe_ok: bool = False
    probe_reason: str | None = "chưa probe"
    version: str | None = None


def _module_for_adapter(adapter: str) -> str:
    path = PurePosixPath(adapter.replace("\\", "/"))
    if path.suffix == ".py":
        path = path.with_suffix("")
    return ".".join(part for part in path.parts if part not in ("", "."))


def _manifest_error(path: Path, detail: str) -> ManifestError:
    return ManifestError(f"{path.name}: {detail}")


def _string_list(value: Any, field_name: str, path: Path) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise _manifest_error(path, f"{field_name} phải là danh sách chuỗi không rỗng")
    return [item.strip() for item in value]


def _worker_from_manifest(path: Path) -> Worker:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise _manifest_error(path, f"không đọc được YAML: {error}") from None
    if not isinstance(raw, dict):
        raise _manifest_error(path, "manifest phải là object YAML")

    for required in ("name", "adapter", "lanes", "capabilities"):
        if required not in raw or raw[required] is None:
            raise _manifest_error(path, f"thiếu trường bắt buộc {required}")

    name, adapter = raw["name"], raw["adapter"]
    if not isinstance(name, str) or not name.strip():
        raise _manifest_error(path, "name phải là chuỗi không rỗng")
    if not isinstance(adapter, str) or not adapter.strip():
        raise _manifest_error(path, "adapter phải là chuỗi không rỗng")

    lanes = _string_list(raw["lanes"], "lanes", path)
    if not lanes:
        raise _manifest_error(path, "lanes không được rỗng")

    raw_capabilities = raw["capabilities"]
    if not isinstance(raw_capabilities, list):
        raise _manifest_error(path, "capabilities phải là danh sách")
    capabilities: dict[str, dict[str, Any]] = {}
    for capability in raw_capabilities:
        if not isinstance(capability, dict):
            raise _manifest_error(path, "mỗi capability phải là object")
        capability_id = capability.get("id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise _manifest_error(path, "mỗi capability cần id là chuỗi không rỗng")
        capability_id = capability_id.strip()
        if capability_id in capabilities:
            raise _manifest_error(path, f"capability bị lặp: {capability_id}")
        capabilities[capability_id] = dict(capability)

    raw_requires = raw.get("requires", {})
    if not isinstance(raw_requires, dict):
        raise _manifest_error(path, "requires phải là object")
    requires = {
        key: _string_list(raw_requires.get(key, []), f"requires.{key}", path)
        for key in ("env", "binaries")
    }
    version_probe = raw.get("version_probe", "")
    if not isinstance(version_probe, (str, list)) or (
        isinstance(version_probe, list)
        and any(not isinstance(part, str) or not part.strip() for part in version_probe)
    ):
        raise _manifest_error(path, "version_probe phải là chuỗi hoặc danh sách đối số")
    data_egress = _string_list(raw.get("data_egress", []), "data_egress", path)

    return Worker(
        name=name.strip(),
        adapter=adapter.strip(),
        module=_module_for_adapter(adapter.strip()),
        lanes=lanes,
        capabilities=capabilities,
        requires=requires,
        version_probe=version_probe,
        data_egress=data_egress,
    )


def load(dir: str | Path = "workers") -> dict[str, Worker]:
    """Load all YAML worker manifests, skipping underscore-prefixed templates."""
    root = Path(dir)
    if not root.is_dir():
        raise ManifestError(f"{root}: thư mục worker không tồn tại")

    registry: dict[str, Worker] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith("_") or not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        worker = _worker_from_manifest(path)
        if worker.name in registry:
            raise _manifest_error(path, f"name worker bị trùng: {worker.name}")
        registry[worker.name] = worker
    return registry


def probe(worker: Worker) -> Worker:
    """Update and return a worker's preflight status without raising probe failures."""
    worker.probe_ok = False
    worker.probe_reason = None
    worker.version = None

    for binary in worker.requires.get("binaries", []):
        if shutil.which(binary) is None:
            worker.probe_reason = f"thiếu binary {binary}"
            return worker
    for variable in worker.requires.get("env", []):
        if not os.environ.get(variable, "").strip():
            worker.probe_reason = f"thiếu biến môi trường {variable}"
            return worker

    command = worker.version_probe
    if isinstance(command, str):
        try:
            argv = shlex.split(command, posix=(os.name != "nt"))
        except ValueError as error:
            worker.probe_reason = f"version_probe sai cú pháp: {error}"
            return worker
    else:
        argv = list(command)
    if not argv:
        worker.probe_reason = "thiếu version_probe"
        return worker

    resolved = shutil.which(argv[0])
    if resolved is None and not Path(argv[0]).is_file():
        worker.probe_reason = f"thiếu binary {argv[0]}"
        return worker
    if resolved is not None:
        argv = [resolved, *argv[1:]]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        worker.probe_reason = "version_probe timeout"
        return worker
    except OSError as error:
        worker.probe_reason = f"version_probe không chạy được: {error}"
        return worker

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        worker.probe_reason = f"version_probe exit={completed.returncode}{suffix}"
        return worker
    first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    if not first_line:
        worker.probe_reason = "version_probe không in phiên bản ra stdout"
        return worker

    worker.probe_ok = True
    worker.probe_reason = None
    worker.version = first_line
    return worker


def pick(registry: dict[str, Worker], spec: dict, prefer: tuple[str, ...] | list[str] = ()) -> tuple[Worker | None, str]:
    """Pick a probed worker by capability/lane/oracle and explicit deterministic preference."""
    capability_id = spec.get("capability")
    lane = spec.get("lane")
    oracle = spec.get("oracle")
    oracle_kind = oracle.get("kind") if isinstance(oracle, dict) else None

    has_capability = [worker for worker in registry.values() if capability_id in worker.capabilities]
    if not has_capability:
        return None, f"không worker nào có capability này: {capability_id}"

    compatible = [
        worker for worker in has_capability
        if lane in worker.lanes
        and oracle_kind in worker.capabilities[capability_id].get("oracle_kinds", [])
    ]
    if not compatible:
        return None, f"có worker nhưng không hỗ trợ lane/oracle: lane={lane}, oracle.kind={oracle_kind}"

    ready = [worker for worker in compatible if worker.probe_ok]
    if not ready:
        reasons = sorted({worker.probe_reason or "probe chưa hoàn tất" for worker in compatible})
        return None, "có worker nhưng probe hỏng: " + "; ".join(reasons)

    if prefer is None:
        preferences = ()
    elif isinstance(prefer, str):
        preferences = (prefer.strip(),) if prefer.strip() else ()
    elif isinstance(prefer, (tuple, list)) and all(isinstance(name, str) and name.strip() for name in prefer):
        preferences = tuple(dict.fromkeys(name.strip() for name in prefer))
    else:
        return None, "prefer phải là danh sách tên worker"
    rank = {name: index for index, name in enumerate(preferences)}
    ready.sort(key=lambda worker: (rank.get(worker.name, len(rank)), worker.name))
    return ready[0], ""
