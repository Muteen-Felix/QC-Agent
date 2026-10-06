"""Rut gon diff tu merge-base. Uoc luong token bang so ky tu / 4."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from qc_agent.logging_setup import event

log = logging.getLogger("qc_agent.selector.pruner")
REF = re.compile(r"^(?!-)[A-Za-z0-9_./-]{1,200}$")
LOCKFILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock"}
COMMENT = {".py": ("#",), ".js": ("//", "/*", "*", "*/"), ".ts": ("//", "/*", "*", "*/"),
           ".jsx": ("//", "/*", "*", "*/"), ".tsx": ("//", "/*", "*", "*/"),
           ".java": ("//", "/*", "*", "*/"), ".go": ("//", "/*", "*", "*/"),
           ".c": ("//", "/*", "*", "*/"), ".cpp": ("//", "/*", "*", "*/"),
           ".cs": ("//", "/*", "*", "*/"), ".sh": ("#",), ".yaml": ("#",),
           ".yml": ("#",), ".sql": ("--",), ".html": ("<!--", "-->")}


@dataclass(frozen=True)
class PrunedFile:
    path: str
    status: str
    old_path: str | None
    kind: str
    hunks: str | None
    truncated: bool
    dropped_hunks: int


@dataclass(frozen=True)
class PrunedDiff:
    base: str
    head: str
    merge_base: str
    files: tuple[PrunedFile, ...]
    approx_tokens: int
    sha256: str


def _git(repo: Path, *args: str) -> bytes:
    # safe.directory=*: gate chạy container với `--user <uid runner>` trên checkout bind-mount; Docker Desktop hoặc runner tự host (chủ thư mục lệch)
    # thì git từ chối ("dubious ownership") và Select lùi về FULL SET. Chỉ áp cho lệnh ĐỌC (diff/show/merge-base) tại SUT root do vận hành chỉ định.
    run = subprocess.run(["git", "-c", "core.quotepath=off", "-c", "safe.directory=*", *args], cwd=repo,
                         capture_output=True, timeout=30, check=False)
    if run.returncode:
        raise ValueError("git diff khong thanh cong")
    return run.stdout


def _kind(repo: Path, path: str, status: str, merge_base: str, head: str) -> str:
    if status == "D":
        return "deleted"
    p = Path(path)
    if p.name in LOCKFILES:
        return "lockfile"
    if any(part in {"vendor", "node_modules"} for part in p.parts):
        return "vendor"
    if any(part in {"dist", "build"} for part in p.parts) or path.endswith((".min.js", ".map")):
        return "generated"
    numstat = _git(repo, "diff", "--numstat", "-z", merge_base, head, "--", path)
    if numstat.startswith(b"-\t-\t"):
        return "binary"
    header = _git(repo, "show", f"{head}:{path}").decode("utf-8", "replace").splitlines()[:5]
    if any("@generated" in line or "DO NOT EDIT" in line for line in header):
        return "generated"
    return "code"


def _trim_comments(diff: str, suffix: str) -> tuple[str | None, int]:
    chunks = re.split(r"(?=^@@ )", diff, flags=re.MULTILINE)
    header = chunks.pop(0) if chunks and not chunks[0].startswith("@@ ") else ""
    kept = []
    dropped = 0
    markers = COMMENT.get(suffix, ())
    if suffix == ".md":
        markers = ("<!--", "-->")
    for chunk in chunks:
        edits = [line[1:].strip() for line in chunk.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        if edits and markers and all(not line or line.startswith(markers) for line in edits):
            dropped += 1
        elif edits:
            kept.append(chunk)
    return (header + "".join(kept)).strip() if kept else None, dropped


def _cap(value: str | None, tokens: int) -> tuple[str | None, bool]:
    if value is None or len(value) <= tokens * 4:
        return value, False
    limited = value[:tokens * 4]
    dropped = value.count("\n") - limited.count("\n")
    return limited + f"\n...[cat {dropped} dong]", True


def prune(repo: Path, base: str, head: str, *, per_file_tokens: int = 1500, total_tokens: int = 12000) -> PrunedDiff:
    if not REF.fullmatch(base or "") or not REF.fullmatch(head or ""):
        raise ValueError("base/head khong hop le")
    repo = Path(repo)
    merge_base = _git(repo, "merge-base", base, head).decode().strip()
    raw = _git(repo, "diff", "--name-status", "-z", "-M", merge_base, head, "--").decode("utf-8", "replace").split("\0")
    files = []
    i = 0
    while i < len(raw) and raw[i]:
        status = raw[i][0]
        if status == "R":
            old_path, path = raw[i + 1], raw[i + 2]
            i += 3
        else:
            path, old_path = raw[i + 1], None
            i += 2
        kind = _kind(repo, path, status, merge_base, head)
        hunks, dropped = None, 0
        if kind == "code":
            diff = _git(repo, "diff", "--no-color", "--no-ext-diff", "-w", "-U1", merge_base, head, "--", path).decode("utf-8", "replace")
            hunks, dropped = _trim_comments(diff, Path(path).suffix.lower())
        hunks, truncated = _cap(hunks, per_file_tokens)
        files.append(PrunedFile(path, status, old_path, kind, hunks, truncated, dropped))
    files.sort(key=lambda item: item.path)
    total = sum(len(item.hunks or "") // 4 for item in files)
    if total > total_tokens:
        order = sorted(range(len(files)), key=lambda n: ("test" not in files[n].path.lower(), -len(files[n].hunks or "")))
        for n in order:
            if total <= total_tokens:
                break
            old = files[n]
            if old.hunks:
                total -= len(old.hunks) // 4
                files[n] = replace(old, hunks=None, truncated=True, dropped_hunks=old.dropped_hunks + 1)
    digest = hashlib.sha256(json.dumps([asdict(item) for item in files], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    approx = sum(len(item.hunks or "") // 4 for item in files)
    event(log, "selector.prune", files=len(files), approx_tokens=approx, truncated=sum(item.truncated for item in files),
          kinds={kind: sum(item.kind == kind for item in files) for kind in sorted({item.kind for item in files})})
    return PrunedDiff(base, head, merge_base, tuple(files), approx, digest)
