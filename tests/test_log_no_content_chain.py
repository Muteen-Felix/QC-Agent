"""Chống rò rỉ nội dung qua log và file đầu ra, chạy TRỌN CHUỖI bằng tiến trình thật (S4-07).

Mỗi kênh dữ liệu nhạy cảm mang một chuỗi đánh dấu RIÊNG (PRD, diff, mã nguồn SUT, key Claude, key Gemini, token GitHub, token Jira, rationale do LLM viết, body lỗi của Jira/Gemini).
Chuỗi chạy: gt generate (một lời gọi) → gt generate --agent (mã nguồn SUT đi qua agent_loop) → select (Claude) → select (Gemini: 429 → llm.retry → llm.fallback)
→ run --trigger pr → pr_review → jira (tạo ticket, rồi Jira lỗi có marker trong body) → ci (Check Run + comment). LLM/GitHub/Jira đều là server giả ở loopback; không gọi mạng thật.

Khẳng định:
  1. không marker nào xuất hiện trong stderr của BẤT KỲ bước nào;
  2. trong `runs/` và thư mục egress, marker chỉ xuất hiện ở các file được phép (ALLOWED, ghi trong docs/operations.md mục "File nào được chứa gì");
  3. chống xanh giả: mỗi marker CÓ đi tới kênh của nó (có trong request tới LLM/GitHub/Jira giả) và các event đặc trưng (llm.retry, llm.fallback, llm.agent, gt.agent, ...) có xuất hiện;
  4. bộ dò thật sự bắt được rò rỉ (test của test).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import harness_kit as kit
from tests.agentkit import tool_use_msg
from tests.fakes import FakeAnthropic, FakeGemini, FakeGitHub, FakeJira
from tests.test_gt_agent import FINISH_DONE, call, submit_calls

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SUFFIX = "9f3a7c"
MARKS = {
    "PRD": f"LEAKPRD-{SUFFIX}",
    "DIFF": f"LEAKDIFF-{SUFFIX}",
    "SRC": f"LEAKSRC-{SUFFIX}",
    "CLAUDE_KEY": f"sk-ant-LEAKCLAUDEKEY-{SUFFIX}",
    "GEMINI_KEY": f"AIzaLEAKGEMINIKEY-{SUFFIX}",
    "GITHUB_TOKEN": f"ghs_LEAKGHTOKEN-{SUFFIX}",
    "JIRA_TOKEN": f"LEAKJIRATOKEN-{SUFFIX}",
    "RATIONALE": f"LEAKRATIONALE-{SUFFIX}",
    "JIRA_ERR": f"LEAKJIRAERR-{SUFFIX}",
    "GEMINI_ERR": f"LEAKGEMINIERR-{SUFFIX}",
}
# Marker nào được nằm ở ĐƯỜNG DẪN nào (so khớp TOÀN BỘ đường dẫn, không so tên file). Đo ngày 2026-10-07 (spike S4-07): rationale đã làm sạch (`selector.agent._clean`) nằm ở ba
# artifact TOP-LEVEL của một run: selection.json, plan.yaml (selection là một phần của plan text để hash vào plan_id; ngoại lệ có chủ đích, đã chốt ở R3) và report.md, cộng
# bản `runs/selection.json` do `qc-agent select --out` ghi. File cùng tên ở thư mục con (`results/plan.yaml`, `t-001/selection.json`...) KHÔNG được phép. Mọi marker khác KHÔNG
# được có trong file nào.
TOP_LEVEL_ARTIFACTS = ("selection.json", "plan.yaml", "report.md")
ALLOWED = {"RATIONALE": (re.compile(r"runs/selection\.json"), re.compile(r"runs/r-\d+/(?:" + "|".join(re.escape(n) for n in TOP_LEVEL_ARTIFACTS) + ")"))}
GEMINI_SELECT_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODEL = "gemini-3.8-flash"


def leaks(texts: dict[str, str], *, allowed: dict[str, tuple] | None = None) -> list[tuple[str, str]]:
    """[(tên marker, nơi chứa)] cho mọi chỗ marker xuất hiện ngoài danh sách cho phép. `texts` = {nơi: nội dung}; nơi là đường dẫn tương đối (dấu `\\` được chuẩn hoá thành `/`)
    và phải khớp TOÀN BỘ một mẫu của marker đó: chỉ trùng tên file ở chỗ khác không đủ."""
    allowed = allowed or {}
    found = []
    for where, text in texts.items():
        posix = where.replace("\\", "/")
        for name, mark in MARKS.items():
            if mark in text and not any(pattern.fullmatch(posix) for pattern in allowed.get(name, ())):
                found.append((name, where))
    return found


def _read_tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root.parent).as_posix(): p.read_bytes().decode("utf-8", "replace") for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


class Chain:
    def __init__(self, base: Path):
        self.base = base
        self.stderr: dict[str, str] = {}
        self.returncode: dict[str, int] = {}
        self.stdout: dict[str, str] = {}
        self.requests: dict[str, list] = {}
        self.dirs: dict[str, Path] = {}
        self.seconds: dict[str, float] = {}

    def run(self, step: str, args: list[str], *, cwd: Path, env: dict, module: str = "qc_agent.core.cli") -> subprocess.CompletedProcess:
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-m", module, *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        self.seconds[step] = round(time.monotonic() - started, 1)
        self.stderr[step], self.stdout[step], self.returncode[step] = proc.stderr, proc.stdout, proc.returncode
        return proc

    def events(self) -> list[dict]:
        out = []
        for text in self.stderr.values():
            for line in text.splitlines():
                if line.startswith("{"):
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        return out


def _base_env(base: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("QC_", "ANTHROPIC_", "GEMINI_", "JIRA_", "GITHUB_", "OPENAI_", "MIDSCENE_"))}
    env.update(PYTHONUTF8="1", QC_LOG_FORMAT="json", HOME=str(base / "home"), USERPROFILE=str(base / "home"))
    return env


def _make_sut(base: Path) -> tuple[Path, str, str]:
    """Repo noteboard thật có hai commit; PR thêm `scripts/leak_extra.py` mang marker DIFF. Đặt NGOÀI `toyapp/**` (module-map) để Selector không gợi ý thêm worker `pytest`
    (80 giây chạy GT test vào URL không tồn tại): chuỗi chỉ cần có LLM chọn schemathesis."""
    sut, base_sha = kit.make_repo(kit.NOTEBOARD_SUT, base)
    (sut / "scripts").mkdir()
    (sut / "scripts" / "leak_extra.py").write_text(f"# {MARKS['DIFF']}\nVALUE = 1\n", encoding="utf-8", newline="\n")
    return sut, base_sha, kit.commit(sut, "head")


def _gemini_reply(args: dict) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "select_workers", "args": args}}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30}, "modelVersion": GEMINI_FALLBACK_MODEL}


@pytest.fixture(scope="module")
def chain(tmp_path_factory) -> Chain:
    base = tmp_path_factory.mktemp("leakchain")
    (base / "home").mkdir()
    chain = Chain(base)
    env = _base_env(base)
    sut, base_sha, head_sha = _make_sut(base)
    chain.dirs.update(sut=sut)
    gh_env = kit.harness.build_github_context(base, api_url="http://127.0.0.1:1", sha=head_sha, base_sha=base_sha)["env"]

    # ---- 1. gt generate (một lời gọi); PRD mang marker PRD ----
    gt_sut = base / "gt-sut"
    (gt_sut / "docs" / "prd").mkdir(parents=True)
    prd = (FIXTURES / "prd" / "noteboard-prd.md").read_text(encoding="utf-8")
    (gt_sut / "docs" / "prd" / "noteboard-prd.md").write_text(prd + f"\n\n## Ghi chú nội bộ\n\n{MARKS['PRD']}\n", encoding="utf-8")
    shutil.copy(FIXTURES / "openapi" / "noteboard.json", gt_sut / "docs" / "openapi.json")
    gt_args = ["gt", "generate", "--prd", "docs/prd/noteboard-prd.md", "--sut-root", str(gt_sut), "--openapi", "docs/openapi.json", "--no-xlsx"]
    with FakeAnthropic(FIXTURES / "llm" / "gt_noteboard_response.json", key=MARKS["CLAUDE_KEY"]) as llm:
        chain.run("gt-generate", [*gt_args, "--egress-dir", str(base / "egress-gt")], cwd=gt_sut,
                  env={**env, "ANTHROPIC_API_KEY": MARKS["CLAUDE_KEY"], "ANTHROPIC_BASE_URL": llm.url, "QC_GT_CACHE_DIR": str(base / "cache-gt")})
        chain.requests["gt-generate"] = llm.requests

    # ---- 2. gt generate --agent: mã nguồn SUT (marker SRC) đi qua agent_loop ----
    agent_sut = base / "agent-sut"
    (agent_sut / "docs" / "prd").mkdir(parents=True)
    shutil.copy(FIXTURES / "prd" / "noteboard-prd.md", agent_sut / "docs" / "prd" / "noteboard-prd.md")
    shutil.copy(FIXTURES / "openapi" / "noteboard.json", agent_sut / "docs" / "openapi.json")
    source = base / "agent-src"
    (source / "app").mkdir(parents=True)
    (source / "app" / "main.py").write_text(f"# {MARKS['SRC']}\nfrom fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    from qc_agent.groundtruth.prd import parse_prd
    parsed = parse_prd(FIXTURES / "prd" / "noteboard-prd.md", openapi_source=str(FIXTURES / "openapi" / "noteboard.json"))
    emitted = next(block["input"] for block in json.loads((FIXTURES / "llm" / "gt_noteboard_response.json").read_text(encoding="utf-8"))["content"] if block["type"] == "tool_use")
    script = [tool_use_msg(("r", "read_file", {"path": "app/main.py", "start_line": 1, "max_lines": 5}))] + [call(n, d) for n, d in submit_calls(parsed, emitted)] + [call("finish_generation", FINISH_DONE)]
    with FakeAnthropic(*script, key=MARKS["CLAUDE_KEY"]) as llm:
        chain.run("gt-agent", ["gt", "generate", "--agent", "--prd", "docs/prd/noteboard-prd.md", "--sut-root", str(agent_sut), "--source-root", str(source), "--openapi", "docs/openapi.json",
                               "--no-xlsx", "--egress-dir", str(base / "egress-agent")], cwd=agent_sut,
                  env={**env, "ANTHROPIC_API_KEY": MARKS["CLAUDE_KEY"], "ANTHROPIC_BASE_URL": llm.url, "QC_GT_AGENT_MAX_TURNS": str(len(script) + 2)})
        chain.requests["gt-agent"] = llm.requests

    # ---- 3. select (Claude): rationale do LLM viết mang marker RATIONALE; diff mang marker DIFF ----
    (sut / "runs").mkdir(exist_ok=True)
    select_args = ["select", "--project", "noteboard", "--mode", "pr", "--sut-root", str(sut), "--base", base_sha, "--head", head_sha]
    with FakeAnthropic(kit.select_response("schemathesis", reason=MARKS["RATIONALE"]), key=MARKS["CLAUDE_KEY"]) as llm:
        chain.run("select", [*select_args, "--out", str(sut / "runs" / "selection.json")], cwd=sut,
                  env={**env, "ANTHROPIC_API_KEY": MARKS["CLAUDE_KEY"], "ANTHROPIC_BASE_URL": llm.url, "QC_SELECT_CACHE_DIR": str(base / "cache-select")})
        chain.requests["select"] = llm.requests

    # ---- 4. select (Gemini): 429, 429 (hết retry) → model dự phòng 200; marker GEMINI_KEY là khoá, GEMINI_ERR nằm trong body lỗi ----
    good = _gemini_reply({"selections": [{"worker": "schemathesis", "reason": MARKS["RATIONALE"]}]})
    with FakeGemini(429, 429, good, key=MARKS["GEMINI_KEY"], message=f"quota {MARKS['GEMINI_ERR']}") as gem:
        chain.run("select-gemini", [*select_args, "--out", str(base / "selection-gemini.json")], cwd=sut,
                  env={**env, "GEMINI_API_KEY": MARKS["GEMINI_KEY"], "GEMINI_BASE_URL": gem.url, "QC_SELECTOR_MODEL": GEMINI_SELECT_MODEL, "QC_LLM_MAX_RETRIES": "1",
                       "QC_LLM_MIN_INTERVAL_S": "0", "QC_LLM_FALLBACK_MODELS": GEMINI_FALLBACK_MODEL, "QC_SELECT_CACHE_DIR": str(base / "cache-select-gemini")})
        chain.requests["select-gemini"] = gem.requests

    # ---- 5. run --trigger pr --selection (worker thật chưa cài sẽ skipped/error: đủ để có report) ----
    chain.run("run", ["run", "--project", "noteboard", "--mode", "pr", "--sut-root", str(sut), "--runs-dir", str(sut / "runs"), "--trigger", "pr", "--selection",
                      str(sut / "runs" / "selection.json")], cwd=sut, env={**env, "APP_BASE_URL": "http://127.0.0.1:1"})
    run_dir = sorted((sut / "runs").glob("r-*"))[-1]
    chain.dirs["run"] = run_dir

    # ---- 6. pr_review + ci (GitHub giả, token mang marker) ----
    with FakeGitHub() as gh:
        gh.pr_files = kit.pr_files_from_git(sut, base_sha, head_sha)
        gh_run = {**env, **gh_env, "GITHUB_TOKEN": MARKS["GITHUB_TOKEN"], "GITHUB_API_URL": gh.url, "GITHUB_SHA": head_sha}
        # run thật không có finding nào gắn được vào diff (worker chưa cài): dùng report có một finding Critical nằm trong diff để review thật sự được đăng
        review_dir = base / "review-run"
        review_dir.mkdir()
        shown = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        shown["findings"] = [{"fingerprint": "b" * 32, "severity": "critical", "task_id": "t-902", "suite": "sast", "worker": "semgrep", "rule_id": "js-eval-dynamic",
                              "title": "dùng eval trên đầu vào", "path": "scripts/leak_extra.py", "line": 2, "end_line": 2, "lane": "gate", "verdict_source": "worker"}]
        (review_dir / "report.json").write_text(json.dumps(shown), encoding="utf-8")
        chain.run("pr-review", ["--run-dir", str(review_dir)], cwd=sut, env=gh_run, module="qc_agent.integrations.pr_review")
        chain.run("pr-review-again", ["--run-dir", str(review_dir)], cwd=sut, env=gh_run, module="qc_agent.integrations.pr_review")   # cùng digest: review.skipped (duplicate)
        chain.run("ci", ["--run-dir", str(run_dir), "--project", "noteboard", "--mode", "pr", "--exit-code", "1"], cwd=sut, env=gh_run, module="qc_agent.integrations.ci")
        chain.requests["github"] = list(gh.requests)

    # ---- 7. jira: finding Low (title do SUT/diff quyết định: không đưa marker vào) → tạo ticket; rồi Jira lỗi với marker trong body ----
    jira_dir = base / "jira-run"
    jira_dir.mkdir()
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    report["findings"] = [{"fingerprint": "a" * 32, "severity": "low", "task_id": "t-901", "suite": "coverage-debt", "worker": "coverage-debt", "rule_id": "api_endpoint",
                           "title": "endpoint chưa có test", "path": "toyapp/app.py", "line": 3, "end_line": 3, "lane": "debt", "verdict_source": "worker"}]
    (jira_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    jira_env = {**env, **gh_env, "JIRA_EMAIL": "qc@example.invalid", "JIRA_API_TOKEN": MARKS["JIRA_TOKEN"]}
    with FakeJira() as jira:
        chain.run("jira-ok", ["--run-dir", str(jira_dir), "--project", "noteboard"], cwd=sut, env={**jira_env, "JIRA_BASE_URL": jira.url}, module="qc_agent.integrations.jira")
        chain.requests["jira-ok"] = jira.requests
        jira.forced_status, jira.forced_message = 500, f"boom {MARKS['JIRA_ERR']}"
        jira.issues.clear()
        chain.run("jira-error", ["--run-dir", str(jira_dir), "--project", "noteboard"], cwd=sut, env={**jira_env, "JIRA_BASE_URL": jira.url}, module="qc_agent.integrations.jira")
        chain.requests["jira-error"] = jira.requests
    return chain


# ---------------------------------------------------------------- bộ dò

def test_the_detector_catches_a_marker_in_a_log_and_in_a_file_outside_the_allowlist():
    texts = {"stderr:x": f'{{"event": "llm.call", "note": "{MARKS["PRD"]}"}}', "runs/r-0001/selection.json": MARKS["RATIONALE"], "runs/r-0001/results/t-001.json": MARKS["RATIONALE"]}
    assert leaks(texts, allowed=ALLOWED) == [("PRD", "stderr:x"), ("RATIONALE", "runs/r-0001/results/t-001.json")]
    assert leaks({"runs/r-0001/selection.json": MARKS["JIRA_TOKEN"]}, allowed=ALLOWED) == [("JIRA_TOKEN", "runs/r-0001/selection.json")]   # allowlist theo TỪNG marker, không theo file
    assert leaks({"stderr": "sạch"}, allowed=ALLOWED) == []


@pytest.mark.parametrize("where", ["runs/r-0001/selection.json", "runs/r-0001/plan.yaml", "runs/r-0001/report.md", "runs/selection.json", "runs\\r-0001\\plan.yaml"])
def test_the_rationale_is_allowed_only_in_the_top_level_artifacts_of_a_run(where):
    assert leaks({where: MARKS["RATIONALE"]}, allowed=ALLOWED) == []


@pytest.mark.parametrize("where", [
    "runs/r-0001/results/plan.yaml", "runs/r-0001/results/selection.json", "runs/r-0001/t-001/report.md", "runs/r-0001/specs/selection.json", "runs/r-0001/evidence/plan.yaml",
    "runs/r-0001/results/report.md", "runs/r-0001/report.json", "runs/r-0001/selection.json.bak", "runs/r-0001/xplan.yaml", "runs/other/selection.json", "runs/sub/selection.json",
    "runs/r-0001/selection.json/inner.txt", "egress-gt/selection.json", "egress-agent/plan.yaml", "r-0001/plan.yaml", "plan.yaml", "selection.json", "jira-run/report.md",
    "runs\\r-0001\\results\\plan.yaml", "runs\\r-0001\\t-001\\selection.json"])
def test_a_file_with_an_allowed_name_in_any_other_place_is_still_reported_as_a_leak(where):
    """Hồi quy: trước đây allowlist so theo tên file nên `results/plan.yaml` hay `egress-gt/selection.json` lọt qua."""
    assert leaks({where: f"x {MARKS['RATIONALE']} y"}, allowed=ALLOWED) == [("RATIONALE", where)]


def test_the_markers_are_unique_and_none_contains_another():
    values = list(MARKS.values())
    assert len(set(values)) == len(values)
    assert not [(a, b) for a in values for b in values if a != b and a in b]


def test_a_deliberate_leak_through_the_real_logger_is_caught(tmp_path):
    """Test của test: một dòng log thật ghi marker (qua logging_setup.event) phải bị bộ dò báo."""
    code = ("import logging; from qc_agent import logging_setup; logging_setup.configure(); "
            f"logging_setup.event(logging.getLogger('qc_agent.x'), 'llm.call', purpose={MARKS['DIFF']!r})")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=_base_env(tmp_path), timeout=60)
    assert leaks({"stderr": proc.stderr}) == [("DIFF", "stderr")]


# ---------------------------------------------------------------- chuỗi thật

def test_every_step_of_the_chain_ran(chain):
    print("seconds per step:", chain.seconds)
    assert chain.returncode["gt-generate"] == 0, chain.stderr["gt-generate"][-600:]
    assert chain.returncode["gt-agent"] == 0, chain.stderr["gt-agent"][-600:]
    assert chain.returncode["select"] == 0, chain.stderr["select"][-600:]
    assert chain.returncode["select-gemini"] == 0, chain.stderr["select-gemini"][-600:]
    assert chain.returncode["run"] in (0, 1), chain.stderr["run"][-600:]
    assert chain.returncode["pr-review"] == 0 and chain.returncode["pr-review-again"] == 0 and chain.returncode["jira-ok"] == 0 and chain.returncode["jira-error"] == 0, {k: v for k, v in chain.returncode.items()}
    assert chain.returncode["ci"] in (0, 1), chain.stderr["ci"][-600:]


def test_the_characteristic_events_really_appeared_so_the_test_is_not_green_by_skipping_branches(chain):
    names = {e["event"] for e in chain.events()}
    expected = {"llm.call", "llm.agent", "llm.retry", "llm.fallback", "gt.generate", "gt.agent", "gt.cache", "selector.prune", "selector.decision", "selector.cache",
                "run.start", "run.end", "task.end"}
    assert expected <= names, sorted(expected - names)
    reviews = [e for e in chain.events() if e["event"].startswith("review.")]
    assert [e["event"] for e in reviews] == ["review.posted", "review.skipped"] and reviews[1]["reason"] == "duplicate", reviews
    jira_events = [e for e in chain.events() if e["event"] == "jira.sync"]
    assert [e["created"] for e in jira_events] == [1, 0] and jira_events[1]["errors"] == 1, jira_events
    gemini = [e for e in chain.events() if e["event"] == "llm.fallback"]
    assert gemini and gemini[0]["model"] == GEMINI_SELECT_MODEL, gemini


def test_each_marker_really_travelled_to_its_channel(chain):
    """Marker không đi tới đâu thì 'không thấy trong log' chẳng chứng minh gì."""
    body = lambda step: json.dumps(chain.requests[step], ensure_ascii=False)   # noqa: E731
    assert MARKS["PRD"] in body("gt-generate") and MARKS["DIFF"] in body("select") and MARKS["SRC"] in body("gt-agent")
    assert MARKS["CLAUDE_KEY"] in {r["headers"]["x-api-key"] for r in chain.requests["select"]}
    assert MARKS["GEMINI_KEY"] in {r["headers"]["x-goog-api-key"] for r in chain.requests["select-gemini"]}
    assert [r["model"] for r in chain.requests["select-gemini"]] == [GEMINI_SELECT_MODEL, GEMINI_SELECT_MODEL, GEMINI_FALLBACK_MODEL]
    assert any(r["auth"] and MARKS["GITHUB_TOKEN"] in r["auth"] for r in chain.requests["github"]), "token GitHub không tới server giả"
    for step in ("jira-ok", "jira-error"):
        basic = {base64.b64decode(r["auth"].split()[1]).decode() for r in chain.requests[step] if r["auth"]}
        assert f"qc@example.invalid:{MARKS['JIRA_TOKEN']}" in basic, step
    selection = json.loads((chain.dirs["sut"] / "runs" / "selection.json").read_text(encoding="utf-8"))
    assert MARKS["RATIONALE"] in json.dumps(selection), "rationale của LLM không tới selection.json"


@pytest.mark.parametrize("name", sorted(MARKS))
def test_no_marker_appears_in_the_stderr_of_any_step(chain, name):
    where = [step for step, text in chain.stderr.items() if MARKS[name] in text]
    assert not where, f"marker {name} lộ ở stderr của bước: {where}"
    assert all(MARKS[name] not in text for text in chain.stdout.values() if name in ("CLAUDE_KEY", "GEMINI_KEY", "GITHUB_TOKEN", "JIRA_TOKEN")), f"{name} lộ ở stdout"


def test_markers_appear_in_runs_and_egress_files_only_where_allowed(chain):
    base = chain.base
    texts: dict[str, str] = {}
    for root in (chain.dirs["sut"] / "runs", base / "egress-gt", base / "egress-agent", chain.dirs["run"].parent):
        if root.is_dir():
            texts.update(_read_tree(root))
    texts.update({"jira-run/jira-status.json": (base / "jira-run" / "jira-status.json").read_text(encoding="utf-8")})
    assert len(texts) > 10, sorted(texts)
    assert leaks(texts, allowed=ALLOWED) == []


def test_the_allowed_files_really_hold_the_rationale_so_the_allowlist_is_not_stale(chain):
    run = chain.dirs["run"]
    for name in TOP_LEVEL_ARTIFACTS:
        assert MARKS["RATIONALE"] in (run / name).read_text(encoding="utf-8"), f"{name} không còn chứa rationale: bỏ khỏi ALLOWED"
    assert MARKS["RATIONALE"] in (chain.dirs["sut"] / "runs" / "selection.json").read_text(encoding="utf-8")


def test_every_file_that_holds_the_rationale_is_covered_by_the_allowlist_and_nothing_else_holds_it(chain):
    """Hai chiều: file chứa rationale phải nằm trong allowlist (đã có test trên), và allowlist không rộng hơn thực tế (mỗi mẫu có ít nhất một file thật)."""
    holders = {rel for rel, text in _read_tree(chain.dirs["sut"] / "runs").items() if MARKS["RATIONALE"] in text}
    assert holders == {"runs/selection.json", *{f"runs/{chain.dirs['run'].name}/{name}" for name in TOP_LEVEL_ARTIFACTS}}, sorted(holders)
    assert all(any(pattern.fullmatch(rel) for pattern in ALLOWED["RATIONALE"]) for rel in holders)


def test_the_operations_doc_states_the_same_exception_as_the_test():
    """Tài liệu và test phải nói cùng một thứ: ba artifact top-level (kể cả plan.yaml) và việc file cùng tên ở thư mục con không được phép."""
    text = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| Rationale của Select"))
    assert all(f"`runs/<run_id>/{name}`" in row for name in TOP_LEVEL_ARTIFACTS), row
    assert "top-level" in row and "thư mục con" in row
    assert "ngoại lệ" in text.lower() and "`plan.yaml`" in text.split("### File nào được chứa gì", 1)[1].split("\n## ", 1)[0]


def test_the_agent_egress_records_that_source_code_left_without_its_content(chain):
    records = [json.loads(line) for line in (chain.base / "egress-agent" / "egress.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records and any("source_code" in json.dumps(r) for r in records), records
    assert MARKS["SRC"] not in json.dumps(records)
