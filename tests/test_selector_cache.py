"""Cache kết quả Select (S4-02): chạy lại cùng thay đổi thì 0 lời gọi LLM, mà floor, fallback và rules không bao giờ bị cache.

LLM là `FakeAnthropic` (HTTP server giả, `ANTHROPIC_BASE_URL`) có đếm lời gọi; không có mạng thật. Thư mục cache là thư mục tạm của test.
"""
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fakes import FakeAnthropic
from qc_agent import logging_setup
from qc_agent.selector import agent
from qc_agent.selector.cli import main
from qc_agent.selector.pruner import PrunedDiff, PrunedFile
from qc_agent.selector.rules import RuleDecision

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-ant-FAKE-KEY-0123456789"
MARKER = "PRIVATE_REASON_MARKER_7c2"


def tool_response(*workers, reason="toyapp/selector_new.py"):
    return {"id": "msg_01", "type": "message", "role": "assistant", "model": "claude-haiku-4-5-20251001", "stop_reason": "tool_use", "stop_sequence": None,
            "content": [{"type": "tool_use", "id": "toolu_01", "name": "select_workers", "input": {"selections": [{"worker": w, "reason": reason} for w in workers]}}],
            "usage": {"input_tokens": 120, "output_tokens": 15, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    for name in ("GEMINI_API_KEY", "QC_SELECTOR_MODEL", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QC_SELECT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


def entries(directory):
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


_STARTED: list = []


@pytest.fixture(autouse=True)
def _stop_servers():
    yield
    while _STARTED:
        _STARTED.pop().__exit__(None, None, None)


def fake_server(monkeypatch, *script):
    server = FakeAnthropic(*(script or [tool_response("pytest")]), key=KEY)
    server.__enter__()
    _STARTED.append(server)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    return server


# ---------------- qua CLI, trên git repo thật ----------------

def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def sut_change(tmp_path_factory):
    """(sut, base, head): một commit sửa mã nguồn (không khớp docs/full_set) nên phải hỏi LLM."""
    sut = tmp_path_factory.mktemp("sut") / "sut"
    shutil.copytree(ROOT / "tests/fixtures/sut/noteboard", sut)
    commit = ["-c", "user.name=QC", "-c", "user.email=qc@example.invalid", "commit", "-qm"]
    git(sut, "init", "-q")
    git(sut, "add", "-A")
    git(sut, *commit, "base")
    base = git(sut, "rev-parse", "HEAD")
    (sut / "toyapp/selector_new.py").write_text("x = 1\n", encoding="utf-8")
    git(sut, "add", "-A")
    git(sut, *commit, "code")
    return sut, base, git(sut, "rev-parse", "HEAD")


def run_cli(sut_change, out: Path) -> dict:
    sut, base, head = sut_change
    code = main(["--project", "noteboard", "--mode", "pr", "--sut-root", str(sut), "--base", base, "--head", head, "--out", str(out)])
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_cli_second_run_hits_the_cache_with_zero_calls(monkeypatch, tmp_path, cache_dir, sut_change):
    server = fake_server(monkeypatch)
    first = run_cli(sut_change, tmp_path / "one" / "selection.json")
    assert server.count == 1 and first["source"] == "llm" and len(entries(cache_dir)) == 1
    second = run_cli(sut_change, tmp_path / "two" / "selection.json")
    assert server.count == 1                                   # lần 2: không có request nào
    assert second["source"] == "cache" and second["llm"]["cache_hit"] is True
    assert "cache_hit" not in first["llm"]
    for name in ("source", "llm"):                             # còn lại giống hệt lần 1
        first.pop(name), second.pop(name)
    assert first == second and "pytest" in second["workers"] and second["floor"]


def test_cli_hit_keeps_usage_from_creation(monkeypatch, tmp_path, sut_change):
    fake_server(monkeypatch)
    first = run_cli(sut_change, tmp_path / "one" / "selection.json")
    second = run_cli(sut_change, tmp_path / "two" / "selection.json")
    assert {k: v for k, v in second["llm"].items() if k != "cache_hit"} == first["llm"]
    assert first["llm"]["input_tokens"] == 120


def test_cli_changed_policy_is_a_miss(monkeypatch, tmp_path, sut_change):
    server = fake_server(monkeypatch)
    projects = tmp_path / "projects"
    shutil.copytree(ROOT / "configs" / "projects", projects)

    def run():
        sut, base, head = sut_change
        assert main(["--project", "noteboard", "--mode", "pr", "--sut-root", str(sut), "--base", base, "--head", head,
                     "--projects-dir", str(projects), "--out", str(tmp_path / "selection.json")]) == 0
    run()
    run()
    assert server.count == 1
    path = projects / "noteboard.yaml"
    old = "advisory_suites: [ui-explore, perf-smoke, coverage-debt]"
    assert old in path.read_text(encoding="utf-8")
    path.write_text(path.read_text(encoding="utf-8").replace(old, "advisory_suites: [ui-explore, perf-smoke]"), encoding="utf-8")
    run()
    assert server.count == 2                                   # cấu hình hiệu lực đổi: miss


# ---------------- qua agent.select (đổi từng thành phần của khoá) ----------------

DIFF = PrunedDiff("base", "head", "base", (PrunedFile("toyapp/app.py", "M", None, "code", "x", False, 0),), 1, "abc")
POLICY = {"floor_workers": ["semgrep"], "blocking_suites": ["sast", "api-contract", "gt-functional"]}
SUITES = {"semgrep": ["sast"], "schemathesis": ["api-contract"], "pytest": ["gt-functional"]}
DECISION = RuleDecision(False, False, "analysis", {"schemathesis": ("module-map: notes",)}, (), "approved")
MODULE_MAP = {"version": 1, "status": "approved", "modules": [{"name": "notes", "paths": ["toyapp/**"], "suites": ["api-contract"]}]}


def select(tmp_path, **over):
    args = {"diff": DIFF, "decision": DECISION, "policy": POLICY, "suites": SUITES, "module_map": MODULE_MAP, "policy_sha": "p1"}
    args.update(over)
    return agent.select(args["diff"], args["decision"], args["policy"], args["suites"], args["module_map"], egress_dir=tmp_path, policy_sha=args["policy_sha"])


def test_select_hit_gives_identical_selection_except_source_and_flag(monkeypatch, tmp_path):
    server = fake_server(monkeypatch)
    first = select(tmp_path)
    second = select(tmp_path)
    assert server.count == 1 and first["source"] == "llm" and second["source"] == "cache"
    assert second["llm"] == {**first["llm"], "cache_hit": True}
    assert {k: v for k, v in first.items() if k not in ("source", "llm")} == {k: v for k, v in second.items() if k not in ("source", "llm")}
    assert second["workers"] == ["pytest", "schemathesis", "semgrep"] and second["floor"] == ["semgrep"]


def _other_prompt(monkeypatch, tmp_path):
    other = tmp_path / "diff_select.md"
    other.write_text(agent.PROMPT.read_text(encoding="utf-8") + "\nExtra rule.\n", encoding="utf-8")
    monkeypatch.setattr(agent, "PROMPT", other)


@pytest.mark.parametrize("change", ["policy_sha", "module_map", "prompt_version", "model", "allowlist", "suites_of_worker", "prompt_text", "diff"])
def test_changing_any_part_of_the_key_is_a_miss(monkeypatch, tmp_path, change):
    server = fake_server(monkeypatch, tool_response("pytest"))
    select(tmp_path)
    select(tmp_path)
    assert server.count == 1
    over = {}
    if change == "policy_sha":
        over["policy_sha"] = "p2"
    elif change == "module_map":
        over["module_map"] = {**MODULE_MAP, "status": "draft"}
    elif change == "prompt_version":
        monkeypatch.setattr(agent, "PROMPT_VERSION", "diff-select/2")
    elif change == "model":
        monkeypatch.setenv("QC_SELECTOR_MODEL", "claude-other-model")
    elif change == "allowlist":
        over["suites"] = {**SUITES, "extra": ["perf"]}
    elif change == "suites_of_worker":
        over["suites"] = {**SUITES, "pytest": ["gt-functional", "other"]}
    elif change == "prompt_text":
        _other_prompt(monkeypatch, tmp_path)
    elif change == "diff":
        over["diff"] = PrunedDiff("base", "head", "base", DIFF.files, 1, "different")
    select(tmp_path, **over)
    assert server.count == 2


def test_fallback_is_never_cached(monkeypatch, tmp_path, cache_dir):
    server = fake_server(monkeypatch, 529)
    result = select(tmp_path)
    assert server.count == 1 and result["source"] == "fallback" and result["full_set"] and result["fallback_reason"] == "unavailable"
    assert entries(cache_dir) == []
    server.script = [tool_response("pytest")]                 # LLM hồi phục: lần sau phải gọi lại và dùng kết quả thật, không bị "đóng băng"
    again = select(tmp_path)
    assert server.count == 2 and again["source"] == "llm" and not again["full_set"]


def test_invalid_llm_output_fallback_is_never_cached(monkeypatch, tmp_path, cache_dir):
    server = fake_server(monkeypatch, tool_response("outside"))   # worker ngoài allowlist: client từ chối (bad_output) nên Select chạy FULL SET
    result = select(tmp_path)
    assert server.count >= 1 and result["full_set"] and result["fallback_reason"] == "bad_output"
    assert entries(cache_dir) == []


def test_full_set_and_floor_only_rules_write_nothing(monkeypatch, tmp_path, cache_dir):
    server = fake_server(monkeypatch)
    assert select(tmp_path, decision=RuleDecision(True, False, "Dockerfile", {}, (), "approved"))["full_set"]
    assert select(tmp_path, decision=RuleDecision(False, True, "docs_only", {}, (), "approved"))["workers"] == ["semgrep"]
    assert server.count == 0 and entries(cache_dir) == []


def test_corrupt_entry_is_a_miss_not_an_error(monkeypatch, tmp_path, cache_dir):
    server = fake_server(monkeypatch)
    select(tmp_path)
    (path,) = entries(cache_dir)
    path.write_text("{khong phai json", encoding="utf-8")
    result = select(tmp_path)
    assert server.count == 2 and result["source"] == "llm"
    assert select(tmp_path)["source"] == "cache" and server.count == 2     # entry đã được ghi lại hợp lệ


@pytest.mark.parametrize("entry", [
    {"version": 1, "selections": [{"worker": "outside", "reason": "x"}], "llm": {"model": "m", "prompt_version": "p", "input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}},
    {"version": 1, "selections": [{"worker": "pytest", "reason": "x", "extra": 1}], "llm": {"model": "m"}},
    {"version": 2, "selections": [], "llm": {}},
    ["not", "a", "dict"],
])
def test_entry_with_unknown_worker_or_bad_shape_is_a_miss(monkeypatch, tmp_path, cache_dir, entry):
    server = fake_server(monkeypatch)
    select(tmp_path)
    (path,) = entries(cache_dir)
    path.write_text(json.dumps(entry), encoding="utf-8")
    result = select(tmp_path)
    assert server.count == 2 and result["source"] == "llm" and not result["full_set"]


def test_hand_edited_entry_cannot_drop_the_floor(monkeypatch, tmp_path, cache_dir):
    server = fake_server(monkeypatch)
    select(tmp_path)
    (path,) = entries(cache_dir)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["selections"] = []                                   # sửa tay: bỏ hết lựa chọn của LLM; entry vẫn hợp lệ nên là HIT
    path.write_text(json.dumps(entry), encoding="utf-8")
    result = select(tmp_path)
    assert server.count == 1 and result["source"] == "cache"
    assert result["floor"] == ["semgrep"] and "semgrep" in result["workers"] and "sast" in result["suites"]


def test_cache_can_be_disabled(monkeypatch, tmp_path, cache_dir):
    server = fake_server(monkeypatch)
    monkeypatch.setenv("QC_SELECT_CACHE_DIR", "none")
    select(tmp_path)
    select(tmp_path)
    assert server.count == 2 and entries(cache_dir) == []


def test_unwritable_cache_dir_does_not_break_selection(monkeypatch, tmp_path):
    fake_server(monkeypatch)
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("QC_SELECT_CACHE_DIR", str(blocker / "sub"))   # mkdir dưới một file: lỗi
    assert select(tmp_path)["source"] == "llm"


def test_cache_log_has_key_prefix_and_no_content(monkeypatch, tmp_path, cache_dir):
    fake_server(monkeypatch, tool_response("pytest", reason=MARKER))
    stream = io.StringIO()
    logging_setup.configure(stream)
    select(tmp_path)
    select(tmp_path)
    (path,) = entries(cache_dir)
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    cache_events = [(e["outcome"], e["key"]) for e in lines if e.get("event") == "selector.cache"]
    assert cache_events == [("miss", path.stem[:8]), ("store", path.stem[:8]), ("hit", path.stem[:8])]
    assert MARKER not in stream.getvalue() and path.stem not in stream.getvalue()


# ---------------- S4-03: est_usd trong entry, và cache_read_input_tokens từ lần gọi 2 hiện trong report ----------------

def test_the_cache_entry_keeps_est_usd_of_the_creation_and_a_hit_reports_it_with_the_flag(monkeypatch, tmp_path, cache_dir):
    fake_server(monkeypatch)
    first = select(tmp_path)
    second = select(tmp_path)
    assert isinstance(first["llm"]["est_usd"], float) and second["llm"] == {**first["llm"], "cache_hit": True}


def test_cache_read_tokens_from_the_second_call_show_up_in_the_report_line(monkeypatch, tmp_path):
    from qc_agent.core.report import render
    from tests.test_report import with_selection
    monkeypatch.setenv("QC_SELECT_CACHE_DIR", "none")                      # hai lời gọi thật (không hit cache của S4-02): prompt cache của API là chuyện khác
    one, two = tool_response("pytest"), tool_response("pytest")
    two["usage"] = {"input_tokens": 100, "output_tokens": 15, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4096}
    server = fake_server(monkeypatch, one, two)
    select(tmp_path)
    selected = select(tmp_path)
    assert server.count == 2 and selected["llm"]["cache_read_input_tokens"] == 4096
    md, data = render(with_selection(selected["llm"]))
    assert "LLM: 4 196 in (4 096 từ cache) / 15 out" in md and data["llm_usage"]["summary"]["cache_read_tokens"] == 4096
