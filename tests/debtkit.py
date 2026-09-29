"""Đồ dùng chung cho test của khâu dò nợ test (worker + adapter): repo git tạm dựng bằng git thật, cách ly khỏi ~/.gitconfig."""
import os
import subprocess
import textwrap
from pathlib import Path

from qc_agent.adapters import coverage_debt_worker as cd

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


class Repo:
    def __init__(self, path: Path):
        self.root = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, cwd=None):
        done = subprocess.run(["git", *args], cwd=cwd or self.root, capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, **GIT_ENV})
        assert done.returncode == 0, f"git {' '.join(args)}\n{done.stderr}"
        return done.stdout.strip()

    def write(self, rel: str, text: str):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8", newline="\n")

    def commit(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def ids(self, base="HEAD^1", root=None) -> list[str]:
        return [f["finding_id"] for f in cd.scan(root or self.root, base)["findings"]]


FILLER = "".join(f"# dòng đệm số {i} để git nhận ra đổi tên (similarity)\n" for i in range(40))

MAIN_PY = textwrap.dedent("""
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/health")
    def health():
        return "ok"
""").lstrip("\n")
PING = '\n@app.get("/ping")\ndef ping():\n    return "pong"\n'
OLD = '\n@app.get("/old")\ndef old(): ...\n'
