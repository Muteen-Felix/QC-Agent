import json
import shutil
import subprocess
from pathlib import Path

from qc_agent.selector.cli import main

ROOT = Path(__file__).resolve().parent.parent


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_select_docs_without_llm_and_code_without_key(tmp_path, monkeypatch):
    sut = tmp_path / "sut"
    shutil.copytree(ROOT / "tests/fixtures/sut/noteboard", sut)
    git(sut, "init", "-q")
    git(sut, "add", "-A")
    git(sut, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "base")
    base = git(sut, "rev-parse", "HEAD")
    (sut / "docs").mkdir()
    (sut / "docs/note.md").write_text("# docs\n", encoding="utf-8")
    git(sut, "add", "-A")
    git(sut, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "docs")
    head = git(sut, "rev-parse", "HEAD")
    base_args = ["--project", "noteboard", "--mode", "pr", "--sut-root", str(sut),
                 "--base", base, "--head", head, "--out", str(tmp_path / "selection.json")]
    assert main(base_args) == 0
    selected = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert selected["source"] == "rules" and selected["suites"] == ["sast", "secrets"]
    (sut / "toyapp/selector_new.py").write_text("x = 1\n", encoding="utf-8")
    git(sut, "add", "-A")
    git(sut, "-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm", "code")
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "QC_SELECTOR_MODEL"):   # kín với .env: không được gọi LLM thật
        monkeypatch.delenv(name, raising=False)
    code_args = base_args.copy()
    code_args[code_args.index("--base") + 1] = head
    code_args[code_args.index("--head") + 1] = git(sut, "rev-parse", "HEAD")
    assert main(code_args) == 0
    selected = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert selected["full_set"] and selected["fallback_reason"] == "missing_api_key"
    code_args[code_args.index("--base") + 1] = "-x"
    assert main(code_args) == 3
