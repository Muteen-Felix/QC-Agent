"""Giết CẢ CÂY tiến trình của một worker (worker -> npx -> node -> chrome ...).

Windows: chụp hậu duệ bằng psutil rồi giết từng cái (đo trên Windows 11: ~0,6 s; `taskkill /T /F` mất ~7 s mỗi lần, cộng thẳng vào mỗi task timeout).
`taskkill /T /F` chỉ còn là dự phòng khi psutil không đọc được cây hoặc sau đó vẫn có tiến trình sống.
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
        victims: list[psutil.Process] = []
        tree_read = False
        with suppress(psutil.Error):
            root = psutil.Process(proc.pid)
            victims = root.children(recursive=True) + [root]  # chụp TRƯỚC khi giết: giết cha rồi thì không truy ra con cháu
            tree_read = True
        for victim in victims:
            with suppress(psutil.Error):
                victim.kill()
        _, survivors = psutil.wait_procs(victims, timeout=wait_s)
        if survivors or not tree_read:  # không đọc được cây (AccessDenied...) hoặc còn tiến trình sống: để taskkill duyệt lại cả cây
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
