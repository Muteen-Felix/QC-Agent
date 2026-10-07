"""Tài liệu vận hành khớp hệ thống thật (S4-07): lệnh, cờ, module, biến môi trường và đường dẫn được nhắc trong docs phải tồn tại trong code.
Không chạy lệnh nào có tác dụng phụ: chỉ `--help`, `importlib.util.find_spec` và đọc file. Giới hạn: kiểm TÊN (lệnh con, cờ, module, biến, đường dẫn), không kiểm ngữ nghĩa
của ví dụ; các ví dụ cần Docker/GitHub/Jira/LLM thật kiểm tay (xem báo cáo S4-07)."""
import importlib.util
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/operations.md", "docs/e2e-runbook.md", "docs/usage-ci.md", "docs/onboarding.md", "docs/groundtruth.md", "docs/user-guide-sprint-1.md",
        "docs/groundtruth-real-sut.md"]
# Đường dẫn trong docs là của REPO SUT (hoặc thư mục chạy sinh ra), không phải của repo qc-agent: không đòi tồn tại ở đây.
SUT_SIDE = (".qc-agent/", ".github/workflows/qc-gate.yml", ".github/workflows/qc-groundtruth.yml", ".github/workflows/qc.yml", "runs/", "docs/prd", "docs/openapi", "openapi",
            "tests_gt/", "selection.json", "report.", "llm_usage", "egress", "plan.yaml", "jira-status", "src/routes", "src/", "test-cases", "module-map", "app/", "toyapp/",
            "ui/", "docs/example", "eval/my-sut", "eval/mutants", "Dockerfile", "dist/", "~/", "/", "$", "<", "{", "http", "docs/runbook", "scripts/")
# Biến môi trường chỉ có ở GitHub Actions / máy người dùng / dịch vụ ngoài, không nằm trong code qc-agent.
EXTERNAL_ENV = {"QC_READ_TOKEN"}


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _repo_text() -> str:
    parts = []
    for base in ("src", "tools", ".github", "tests", "docker", "configs", "workers"):
        for path in (ROOT / base).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".yml", ".yaml", ".tmpl", ".sh", ".md", ".mjs", ".json", "") and "fixtures" not in path.parts[-3:]:
                try:
                    parts.append(path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, OSError):
                    pass
    parts.append(_text(".env.example"))
    return "\n".join(parts)


def _code_spans(text: str) -> list[str]:
    spans = re.findall(r"`([^`\n]+)`", text)
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S):
        spans += block.splitlines()
    return spans


# ───────────────────────── lệnh `qc-agent <sub> ...` ─────────────────────────

@lru_cache(maxsize=None)
def _help(*args: str) -> tuple[int, str]:
    done = subprocess.run([sys.executable, "-m", "qc_agent.core.cli", *args, "--help"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return done.returncode, done.stdout + done.stderr


def _commands(name: str) -> list[tuple[str, tuple[str, ...], list[str]]]:
    """(dòng gốc, đường lệnh con, các cờ dài) cho mọi DÒNG LỆNH bắt đầu bằng `qc-agent <con> ...` (có thể sau `$ `). Chữ `qc-agent` giữa câu văn không tính."""
    found = []
    for line in _code_spans(_text(name)):
        match = re.match(r"^(?:\$\s+)?qc-agent\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?(.*)$", line.strip())
        if not match:
            continue
        words = tuple(word for word in match.groups()[:2] if word)
        flags = re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", match.group(3))
        found.append((line.strip(), words, flags))
    return found


def _known_subcommands() -> set[str]:
    """Các lệnh con mà `core/cli.py: main` điều phối (không phải argparse subparsers nên đọc từ mã nguồn)."""
    source = _text("src/qc_agent/core/cli.py")
    names = set()
    for group in re.findall(r'argv\[0\] (?:==|in) \(?((?:"[a-z]+",?\s*)+)\)?', source):
        names |= set(re.findall(r'"([a-z]+)"', group))
    return names


def test_the_cli_exposes_the_documented_top_level_commands():
    assert {"run", "select", "gt", "init", "validate", "doctor", "user", "token"} <= _known_subcommands(), _known_subcommands()


@pytest.mark.parametrize("name", DOCS)
def test_every_documented_qc_agent_command_and_flag_exists(name):
    known = _known_subcommands()
    problems = []
    for line, words, flags in _commands(name):
        if words[0] not in known:
            problems.append(f"{name}: lệnh con `{words[0]}` không có: {line[:100]}")
            continue
        path = words if words[0] in ("gt", "token", "user") and len(words) > 1 else words[:1]
        if words[0] == "init" and "--refine" in flags:
            path = ("init", "--refine")        # `init --refine` có bộ tham số riêng (xem `qc-agent init --help`)
        code, helptext = _help(*path)
        if code != 0:
            problems.append(f"{name}: `qc-agent {' '.join(path)} --help` lỗi (exit {code}): {line[:100]}")
            continue
        for flag in flags:
            if flag not in helptext:
                problems.append(f"{name}: cờ {flag} không có ở `qc-agent {' '.join(path)}`: {line[:100]}")
    assert not problems, chr(10).join(problems)


# ───────────────────────── python -m / tools/ ─────────────────────────

@pytest.mark.parametrize("name", DOCS)
def test_every_documented_python_module_and_tool_script_exists(name):
    problems = []
    for span in _code_spans(_text(name)):
        for module in re.findall(r"python3?\s+-m\s+([a-z_][\w.]*)", span):
            if module in {"pytest", "uvicorn", "pip", "venv", "http.server"} or module.startswith(("uvicorn", "playwright")):
                continue
            try:
                found = importlib.util.find_spec(module)
            except (ImportError, ValueError):
                found = None
            if found is None:
                problems.append(f"{name}: module `{module}` không import được: {span[:100]}")
        for script in re.findall(r"python3?\s+((?:tools|tests)/[\w./-]+\.py)", span):
            if not (ROOT / script).is_file():
                problems.append(f"{name}: script `{script}` không tồn tại: {span[:100]}")
    assert not problems, "\n".join(problems)


# ───────────────────────── biến môi trường ─────────────────────────

@pytest.mark.parametrize("name", DOCS)
def test_every_qc_environment_variable_named_in_the_docs_exists_in_the_code(name):
    repo = _repo_text()
    missing = sorted({var for var in re.findall(r"\bQC_[A-Z][A-Z0-9_]*[A-Z0-9]\b", _text(name)) if var not in EXTERNAL_ENV and var not in repo})
    assert not missing, f"{name} nhắc biến không có trong code/workflow/.env.example: {missing}"


def test_settings_fields_documented_in_the_runbook_have_the_documented_defaults():
    """Mặc định nêu trong operations.md phải bằng giá trị thật trong `Settings` (không chép lại từ trí nhớ)."""
    from qc_agent import settings
    cfg = settings.Settings()
    text = _text("docs/operations.md")
    expected = {"QC_GT_AGENT_MAX_COST_USD": cfg.gt_agent_max_cost_usd, "QC_GT_AGENT_MAX_TURNS": cfg.gt_agent_max_turns, "QC_GT_AGENT_MAX_WALL_S": cfg.gt_agent_max_wall_s,
                "QC_GT_AGENT_MAX_READ_BYTES": cfg.gt_agent_max_read_bytes, "QC_GT_AGENT_TIMEOUT_S": cfg.gt_agent_timeout_s, "QC_LLM_MAX_RETRIES": cfg.llm_max_retries,
                "QC_LLM_MIN_INTERVAL_S": cfg.llm_min_interval_s, "QC_LLM_MAX_INPUT_TOKENS": cfg.llm_max_input_tokens, "QC_LLM_TIMEOUT_S": cfg.llm_timeout_s}
    for var, value in expected.items():
        match = re.search(rf"`{var}`[^()\n]*?\(\s*(?:mặc định\s*)?([0-9][0-9.]*)\b", text)
        assert match, f"operations.md không nêu mặc định của {var} dạng `{var}` (<số>)"
        assert float(match.group(1)) == float(value), (var, match.group(1), value)
    assert cfg.gt_model == "claude-sonnet-5" and cfg.selector_model == "claude-haiku-4-5-20251001" and cfg.gt_agent_model == "claude-sonnet-5-5"
    assert "`QC_GT_MODEL=claude-sonnet-5`" in text and "`QC_SELECTOR_MODEL=claude-haiku-4-5-20251001`" in text and "`QC_GT_AGENT_MODEL` (`claude-sonnet-5-5`)" in text


# ───────────────────────── đường dẫn trong repo ─────────────────────────

@pytest.mark.parametrize("name", DOCS)
def test_every_repo_path_in_backticks_exists(name):
    problems = []
    for span in re.findall(r"`([^`\n]+)`", _text(name)):
        for path in re.findall(r"(?<![\w./$<{~-])((?:docs|tests|tools|src|schemas|workers|configs|rules|web|docker|eval)/[\w./*-]+\.[A-Za-z0-9]+)(?![\w/])", span):
            if "*" in path or path.startswith(SUT_SIDE) or span.startswith(SUT_SIDE[:2]):
                continue
            if not (ROOT / path).exists():
                problems.append(f"{name}: `{path}` không tồn tại (trong: {span[:80]})")
    assert not problems, "\n".join(problems)
