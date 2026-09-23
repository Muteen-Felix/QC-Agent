"""kill_tree phải giết cả hậu duệ ở SESSION KHÁC (đúng kiểu adapter chạy công cụ trong session riêng), không để mồ côi.
Trên Linux killpg chỉ giết nhóm của tiến trình gốc: đây là lỗi đã lộ ra khi chạy test executor trong container."""
import subprocess
import sys
import textwrap
import time

import psutil
from qc_agent.core.proctree import kill_tree

GRANDCHILD = "import time; time.sleep({seconds})"
PARENT = textwrap.dedent("""
    import subprocess, sys, time
    # cháu ở session/nhóm RIÊNG (như Adapter._exec): killpg(nhóm của cha) không tới được nó
    kwargs = {{"start_new_session": True}} if sys.platform != "win32" else {{"creationflags": 0x00000200}}
    child = subprocess.Popen([sys.executable, "-c", {child!r}], **kwargs)
    print(child.pid, flush=True)
    time.sleep(120)
""")


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def spawn(seconds: float):
    proc = subprocess.Popen([sys.executable, "-c", PARENT.format(child=GRANDCHILD.format(seconds=seconds))],
                            stdout=subprocess.PIPE, text=True)
    grandchild = int(proc.stdout.readline())
    return proc, grandchild


def test_kills_a_descendant_that_lives_in_another_session():
    proc, grandchild = spawn(91.111)
    try:
        assert alive(proc.pid) and alive(grandchild)
        kill_tree(proc)
        deadline = time.monotonic() + 10
        while alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not alive(grandchild), "hậu duệ ở session khác còn sống: mồ côi"
        assert proc.poll() is not None
    finally:
        for pid in (proc.pid, grandchild):
            _kill_quietly(pid)  # dọn nếu test hỏng giữa chừng


def test_kill_tree_on_an_already_dead_process_is_harmless():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    kill_tree(proc)  # không raise


def _kill_quietly(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass
