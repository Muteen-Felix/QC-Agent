"""Phần chung của tools/e2e: nhãn loại bước, in bước, chạy lệnh cục bộ có danh sách trắng, nhãn nguồn bằng chứng."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OFFLINE, LLM, EXTERNAL = "offline", "llm", "external"
KINDS = {OFFLINE: "OFFLINE (không tốn tiền, không chạm hệ thống ngoài)",
         LLM: "LLM THẬT (tốn tiền, gửi dữ liệu ra nhà cung cấp LLM; cần duyệt)",
         EXTERNAL: "TÁC ĐỘNG GITHUB/JIRA (cần xác nhận từng hành động)"}

PROVENANCE = ("github", "local", "fake")
NOT_DOD = "KHÔNG PHẢI BẰNG CHỨNG GITHUB THẬT: không dùng để tick DoD"

# Công cụ chỉ được chạy git và Python của chính repo. `gh`, `curl`, `docker`... bị từ chối, nên không có đường vô tình gọi ra ngoài.
ALLOWED_EXECUTABLES = ("git", sys.executable)


class ExternalCallRefused(RuntimeError):
    """Cố chạy tiến trình ngoài danh sách trắng: công cụ này chỉ IN lệnh ra ngoài, không bao giờ chạy."""


@dataclass(frozen=True)
class Step:
    kind: str
    title: str
    command: str | None = None
    needs: tuple[str, ...] = ()   # điều kiện phải có trước khi chạy (xác nhận, secret, ngân sách)
    note: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind sai: {self.kind!r}")


def render_steps(steps: list[Step]) -> str:
    """Nhóm theo loại, giữ thứ tự trong từng nhóm. Lệnh ngoài in ra để NGƯỜI chạy; công cụ không chạy chúng."""
    lines: list[str] = []
    for kind, label in KINDS.items():
        group = [step for step in steps if step.kind == kind]
        if not group:
            continue
        lines += [f"== {label} ==", ""]
        for index, step in enumerate(group, 1):
            lines.append(f"{index}. {step.title}")
            if step.needs:
                lines.append("   cần: " + "; ".join(step.needs))
            if step.note:
                lines.append(f"   ghi chú: {step.note}")
            if step.command:
                lines += ["   " + row for row in step.command.splitlines()]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_local(argv: list[str], *, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Chạy một lệnh cục bộ bằng argv (không shell). Chỉ git và Python hiện tại."""
    if not argv or argv[0] not in ALLOWED_EXECUTABLES:
        raise ExternalCallRefused(f"không được chạy {argv[:1]}: chỉ {ALLOWED_EXECUTABLES} (lệnh ra ngoài chỉ in cho người chạy)")
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def git(repo: Path, *args: str, timeout: int = 60) -> str:
    done = run_local(["git", "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "-c", "core.autocrlf=false", *args], cwd=repo, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def say(text: str) -> None:
    """In an toàn trên console Windows cp1252."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, "replace").decode(enc))
