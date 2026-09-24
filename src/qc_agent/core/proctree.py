"""Giết CẢ CÂY tiến trình của một worker (worker -> npx -> node -> chrome ...).

Windows: `taskkill /T /F` giết cả cây.
POSIX: `killpg` chỉ giết NHÓM của tiến trình gốc. Nhưng adapter chạy công cụ của nó trong một session riêng (start_new_session),
nên hậu duệ nằm NGOÀI nhóm đó và sẽ thành tiến trình mồ côi (bị reparent về init) nếu chỉ dùng killpg. Vì vậy chụp danh sách
hậu duệ theo quan hệ cha-con TRƯỚC khi giết (sau khi giết cha thì không còn truy ra được), rồi giết từng cái.
Lỗi này chỉ lộ ra trên Linux (test executor chạy trong container); Windows che khuất vì taskkill /T."""
from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress

import psutil


def kill_tree(proc: subprocess.Popen, *, wait_s: float = 5) -> None:
    if os.name == "nt":
        with suppress(OSError):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        victims: list[psutil.Process] = []
        with suppress(psutil.Error):
            victims = psutil.Process(proc.pid).children(recursive=True)
        with suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)  # nhóm của tiến trình gốc (đường nhanh)
        for victim in victims:  # hậu duệ ở session/nhóm khác
            with suppress(psutil.Error):
                victim.kill()
        with suppress(psutil.Error):
            psutil.wait_procs(victims, timeout=wait_s)
    with suppress(OSError):
        proc.kill()  # dự phòng cho tiến trình gốc
    with suppress(subprocess.TimeoutExpired):
        proc.communicate(timeout=wait_s)  # gom nốt output, tránh zombie; nếu cháu còn giữ pipe thì bỏ qua (không đóng pipe: sẽ treo)
