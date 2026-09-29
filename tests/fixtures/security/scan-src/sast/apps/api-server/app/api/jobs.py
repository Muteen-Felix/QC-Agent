"""Endpoint nhận job và chạy runner cho một xe (rút gọn từ vahan-rpa để làm fixture Security)."""
import subprocess


def run_job(cmd: str) -> int:
    proc = subprocess.run(cmd, shell=True)
    return proc.returncode
