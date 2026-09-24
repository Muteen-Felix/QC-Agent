"""Stable plan, SUT, and gate-run identities for comparing QC runs."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


PROBE_TIMEOUT_SECONDS = 5
GIT_TIMEOUT_SECONDS = 5


def plan_id(plan_text: str) -> str:
    """Return a content ID, treating CRLF and LF plan files as equivalent."""
    if not isinstance(plan_text, str):
        raise TypeError("plan_text phải là chuỗi")
    normalized = plan_text.replace("\r\n", "\n")
    return "plan-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def _code_commit(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "no-git"
    if completed.returncode != 0:
        return "no-git"
    commit = completed.stdout.strip()
    return commit if commit else "no-git"


def _inside_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"SUT file nằm ngoài root: {path}") from None
    return resolved


def _is_cache_file(path: Path) -> bool:
    return path.parent.name == "__pycache__" and path.suffix == ".pyc"


def _files_sha256(files: Any, root: Path) -> str:
    if not isinstance(files, list) or any(not isinstance(item, str) or not item for item in files):
        raise ValueError("cfg.files phải là danh sách đường dẫn tương đối không rỗng")

    included: dict[str, Path] = {}
    for configured_path in files:
        candidate = _inside_root(root / configured_path, root)
        if not candidate.exists():
            raise FileNotFoundError(f"không tìm thấy SUT file: {configured_path}")
        candidates = [candidate] if candidate.is_file() else candidate.rglob("*")
        for path in candidates:
            if not path.is_file():
                continue
            resolved = _inside_root(path, root)
            if _is_cache_file(resolved):
                continue
            relative = resolved.relative_to(root).as_posix()
            included[relative] = resolved

    digest = hashlib.sha256()
    digest.update(b"qc-sut-files-v1\0")
    for relative, path in sorted(included.items()):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _probe_error(error: Exception) -> dict[str, str]:
    if isinstance(error, HTTPError):
        message = f"HTTP {error.code}"
    elif isinstance(error, URLError):
        reason = error.reason
        message = "network error" if reason is None else str(reason)[:160]
    elif isinstance(error, TimeoutError):
        message = "timeout"
    elif isinstance(error, (ValueError, UnicodeError, json.JSONDecodeError)):
        message = type(error).__name__
    else:
        message = type(error).__name__
    return {"error": message}


def _probe_config(probe_url: Any) -> Any:
    if probe_url is None or probe_url == "":
        return None
    if not isinstance(probe_url, str):
        return {"error": "probe_url must be a string"}
    try:
        if urlsplit(probe_url).scheme not in {"http", "https"}:
            return {"error": "probe_url must use http or https"}
        with urlopen(probe_url, timeout=PROBE_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:  # A SUT config probe is advisory and must never abort identity creation.
        return _probe_error(error)


def sut_identity(cfg: dict, root: str | Path) -> dict[str, Any]:
    """Build a minimal SUT identity from source files, declared attrs, and optional runtime probe.

    The SUT need not live in the qc-agent repo:
      cfg.root  optional directory of the SUT checkout (default: `root`); `files` are resolved and confined under it
      cfg.ref   optional explicit commit/ref (e.g. the PR head SHA); default: `git rev-parse HEAD` of the SUT root
      cfg.files optional (default none): source files/dirs hashed into the identity
    """
    if not isinstance(cfg, dict):
        raise TypeError("cfg phải là object")
    sut_root = cfg.get("root")
    if sut_root is not None and (not isinstance(sut_root, str) or not sut_root.strip()):
        raise ValueError("cfg.root phải là chuỗi không rỗng")
    root_path = Path(sut_root or root).resolve()
    if not root_path.is_dir():
        raise ValueError(f"cfg.root không phải thư mục: {root_path}")
    ref = cfg.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip()):
        raise ValueError("cfg.ref phải là chuỗi không rỗng")
    attrs = cfg.get("attrs", {})
    if not isinstance(attrs, dict):
        raise ValueError("cfg.attrs phải là object")
    return {
        "code_commit": ref.strip() if ref else _code_commit(root_path),
        "files_sha256": _files_sha256(cfg.get("files", []), root_path),
        "attrs": copy.deepcopy(attrs),
        "probe": _probe_config(cfg.get("probe_url")),
    }


def sut_id(identity: dict) -> str:
    """Hash a canonical SUT identity object."""
    canonical = json.dumps(identity, sort_keys=True)
    return "sut-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def write_sut_identity(identity: dict, run_dir: str | Path) -> Path:
    """Write the identity as UTF-8 JSON under the per-run directory."""
    path = Path(run_dir) / "sut_identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_bytes(payload.encode("utf-8"))
    return path


def _sort_value(value: Any) -> tuple[int, str]:
    return (0, "") if value is None else (1, str(value))


def run_signature(plan_id_value: str, sut_id_value: str, results: dict, specs: dict) -> str:
    """Hash plan/SUT IDs and gate-only worker versions/adapters/seeds deterministically."""
    workers: set[tuple[str, str | None, str | None]] = set()
    seeds: list[tuple[str, int | None]] = []
    for task_id, result in results.items():
        spec = specs.get(task_id)
        if not isinstance(spec, dict):
            raise ValueError(f"result {task_id} không có spec tương ứng")
        if spec.get("lane") != "gate":
            continue
        worker = result.get("worker", {}) if isinstance(result, dict) else {}
        if not isinstance(worker, dict):
            worker = {}
        name = worker.get("name") or "unknown"
        version = worker.get("version")
        adapter_version = worker.get("adapter_version")
        workers.add((str(name), None if version is None else str(version),
                     None if adapter_version is None else str(adapter_version)))
        determinism = spec.get("determinism", {})
        seed = determinism.get("seed") if isinstance(determinism, dict) else None
        seeds.append((str(task_id), seed))

    payload = {
        "plan_id": plan_id_value,
        "sut_id": sut_id_value,
        "workers": [list(row) for row in sorted(workers, key=lambda row: tuple(_sort_value(part) for part in row))],
        "seeds": [list(row) for row in sorted(seeds, key=lambda row: (row[0], _sort_value(row[1])))],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
