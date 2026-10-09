"""kill_tree phải giết cả hậu duệ ở SESSION KHÁC (đúng kiểu adapter chạy công cụ trong session riêng), không để mồ côi.
Trên Linux killpg chỉ giết nhóm của tiến trình gốc: đây là lỗi đã lộ ra khi chạy test executor trong container."""
import os
import subprocess
import sys
import textwrap
import time
import types

import psutil
import pytest
from qc_agent.core import proctree
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


@pytest.mark.skipif(os.name != "nt", reason="nhánh psutil + taskkill dự phòng chỉ có trên Windows")
def test_windows_kills_the_tree_with_psutil_without_calling_taskkill(monkeypatch):
    def no_taskkill(*args, **kwargs):
        raise AssertionError("taskkill không được gọi khi psutil đã giết hết cây")
    monkeypatch.setattr(proctree.subprocess, "run", no_taskkill)
    proc, grandchild = spawn(91.222)
    try:
        t0 = time.monotonic()
        kill_tree(proc)
        elapsed = time.monotonic() - t0
        assert not alive(grandchild) and proc.poll() is not None
        assert elapsed < 4, f"kill_tree mất {elapsed:.1f}s: đã quay lại đường chậm kiểu taskkill (~7s)?"
    finally:
        for pid in (proc.pid, grandchild):
            _kill_quietly(pid)


@pytest.mark.skipif(os.name != "nt", reason="nhánh psutil + taskkill dự phòng chỉ có trên Windows")
def test_windows_falls_back_to_taskkill_when_psutil_leaves_survivors(monkeypatch):
    calls = []
    real_run, real_wait = proctree.subprocess.run, proctree.psutil.wait_procs
    monkeypatch.setattr(proctree.psutil, "wait_procs", lambda procs, timeout=None: (real_wait(procs, timeout=timeout)[0], list(procs)[:1]))
    monkeypatch.setattr(proctree.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or real_run(cmd, **kw))
    proc, grandchild = spawn(91.333)
    try:
        kill_tree(proc)
        assert calls and calls[0][0] == "taskkill" and "/T" in calls[0] and "/F" in calls[0]
        assert not alive(grandchild) and proc.poll() is not None
    finally:
        for pid in (proc.pid, grandchild):
            _kill_quietly(pid)


@pytest.mark.skipif(os.name != "nt", reason="nhánh psutil + taskkill dự phòng chỉ có trên Windows")
def test_windows_falls_back_to_taskkill_when_psutil_cannot_read_the_tree(monkeypatch):
    def denied(pid):
        raise psutil.AccessDenied(pid)
    calls = []
    real_run = proctree.subprocess.run
    # chỉ thay tên `psutil` TRONG proctree: `alive()` của test vẫn dùng psutil thật
    monkeypatch.setattr(proctree, "psutil", types.SimpleNamespace(Process=denied, Error=psutil.Error, wait_procs=psutil.wait_procs))
    monkeypatch.setattr(proctree.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or real_run(cmd, **kw))
    proc, grandchild = spawn(91.444)
    try:
        kill_tree(proc)
        assert calls and calls[0][0] == "taskkill" and "/T" in calls[0] and "/F" in calls[0]
        assert not alive(grandchild) and proc.poll() is not None
    finally:
        for pid in (proc.pid, grandchild):
            _kill_quietly(pid)


def _kill_quietly(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass
