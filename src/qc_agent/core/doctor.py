"""`qc-agent doctor`: image/máy này có đủ công cụ cho MỌI worker không, mà không phải chạy cả gate.

Chỉ nạp manifest (registry.load_many) rồi probe từng worker bằng chính `registry.probe` mà engine dùng: binary, biến môi trường,
`version_probe` — nên "doctor xanh" và "engine chọn được worker" là cùng một câu trả lời. Không gọi mạng, không chạy task.
Exit: 0 = mọi worker probe OK · 1 = có worker hỏng (hoặc không có worker nào) · 3 = manifest lỗi (cli.main bắt ManifestError).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from qc_agent import settings
from qc_agent.core import registry
from qc_agent.core.plan import PlanError

_HEADERS = ("worker", "version", "lanes", "status")
_MAX_VERSION = 40    # `k6 version` in cả commit/go/os: cắt cho bảng gọn


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # cùng quy ước với cli: sai tham số = exit 3, không phải 2
        raise PlanError(f"tham số sai: {message}")


def _row(worker: registry.Worker) -> tuple[str, str, str, str]:
    version = (worker.version or "-")[:_MAX_VERSION]
    status = "✅" if worker.probe_ok else f"❌ {worker.probe_reason or 'probe hỏng'}"
    return worker.name, version, ",".join(worker.lanes), status


def _table(rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(cell) for cell in column) for column in zip(_HEADERS, *rows)]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in (_HEADERS, *rows)]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = _Parser(prog="qc-agent doctor", description="Probe mọi worker: có đủ công cụ để chạy gate không.")
    ap.add_argument("--workers-dir", action="append", metavar="DIR",
                    help="thư mục manifest worker (lặp được); mặc định $QC_WORKERS_PATH hoặc workers/")
    args = ap.parse_args(argv)
    dirs = [Path(d) for d in args.workers_dir] if args.workers_dir else settings.get().workers_dirs
    workers = sorted(registry.load_many(dirs).values(), key=lambda w: w.name)
    if not workers:
        print("LỖI: không tìm thấy manifest worker nào trong " + ", ".join(map(str, dirs)))
        return 1
    for worker in workers:
        registry.probe(worker)
    print(_table([_row(worker) for worker in workers]))
    ok = sum(worker.probe_ok for worker in workers)
    print(f"\n{ok}/{len(workers)} worker OK")
    return 0 if ok == len(workers) else 1
