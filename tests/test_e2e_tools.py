"""tools/e2e (S4-06, phần cục bộ): guard không-gọi-ra-ngoài, intake, ngân sách offline, kịch bản C/D cục bộ, dựng sandbox, danh sách lệnh, CLI.

Không có mạng, không có LLM, không có GitHub/Jira: lệnh ra ngoài chỉ được IN. Phần đối chiếu bằng chứng/10 lượt/script bash ở tests/test_e2e_evidence.py.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.e2e import budget, cli, common, intake, plan, preflight, sandbox, scenarios
from tools.e2e.common import EXTERNAL, LLM, OFFLINE

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / "tools" / "e2e"
SHA = "a" * 40
DIGEST = "ghcr.io/muteen-felix/qc-agent@sha256:" + "b" * 64


def full_intake() -> dict:
    """Intake điền đủ (dữ liệu giả) để kiểm đường 'không còn mục chặn'."""
    data = intake.template_data()
    data["sut"].update(commit=SHA, test_command="python -m pytest -q")
    data["github"].update(sandbox_repo="acme/sandbox", plan="team", visibility="private", qa_team="@acme/qa", non_qa_account="outsider", dev_account="dev1", qc_ref=SHA, image=DIGEST)
    data["jira"].update(base_url="https://acme.atlassian.net", project_key="SBX", user_map={"dev1": "acc-1"})
    data["llm"].update(egress_question3="confirmed", egress_evidence="ticket SEC-1", budget_usd_max=2.0)
    return data


# ───────────────────────── guard: công cụ không tự gọi ra ngoài ─────────────────────────

NETWORK_MODULES = {"urllib", "http", "httpx", "requests", "socket", "ssl", "aiohttp", "anthropic", "openai", "smtplib", "ftplib", "websockets"}


def test_tools_e2e_imports_no_network_library():
    found = []
    for path in sorted(E2E.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            found += [f"{path.name}:{name}" for name in names if name.split(".")[0] in NETWORK_MODULES]
    assert found == []


def test_subprocess_is_only_used_in_common():
    users = [p.name for p in sorted(E2E.glob("*.py")) if "subprocess" in p.read_text(encoding="utf-8")]
    assert users == ["common.py"]


@pytest.mark.parametrize("argv", [["gh", "pr", "list"], ["curl", "https://example.com"], ["docker", "ps"], ["bash", "-c", "true"], []])
def test_run_local_refuses_outside_whitelist(argv, tmp_path):
    with pytest.raises(common.ExternalCallRefused):
        common.run_local(argv, cwd=tmp_path)


def test_run_local_allows_git_and_python(tmp_path):
    assert common.run_local([sys.executable, "-c", "print(1)"], cwd=tmp_path).stdout.strip() == "1"
    assert common.run_local(["git", "--version"], cwd=tmp_path).returncode == 0


def test_step_rejects_unknown_kind():
    with pytest.raises(ValueError):
        common.Step("remote", "x")


# ───────────────────────── intake ─────────────────────────

def test_template_is_valid_yaml_and_every_null_becomes_a_gap():
    data = intake.template_data()
    gaps = {g.key for g in intake.validate(data)}
    for key in ("sut.commit", "github.sandbox_repo", "github.plan", "github.qa_team", "github.non_qa_account", "github.image", "github.qc_ref", "jira.base_url",
                "jira.project_key", "llm.egress_question3", "llm.budget_usd_max"):
        assert key in gaps, key


def test_filled_intake_has_no_blocker():
    assert [g for g in intake.validate(full_intake()) if g.blocker] == []


def test_github_free_private_blocks_scenario_b():
    data = full_intake()
    data["github"].update(plan="free", visibility="private")
    gap = next(g for g in intake.validate(data) if g.key == "github.visibility")
    assert gap.blocker and gap.blocks == "B" and "branch protection" in gap.reason
    data["github"]["visibility"] = "public"
    assert not [g for g in intake.validate(data) if g.key == "github.visibility"]


@pytest.mark.parametrize("team", ["@org/team", "@my-org/qa-team", "@qc-agent-todo/qa-team", "no-at-sign"])
def test_placeholder_or_malformed_qa_team_is_rejected(team):
    data = full_intake()
    data["github"]["qa_team"] = team
    assert any(g.key == "github.qa_team" for g in intake.validate(data))


def test_unpinned_image_and_short_sha_are_rejected():
    data = full_intake()
    data["github"].update(image="ghcr.io/muteen-felix/qc-agent:latest", qc_ref="abc123")
    keys = {g.key for g in intake.validate(data)}
    assert {"github.image", "github.qc_ref"} <= keys


def test_dev_account_missing_from_user_map_is_flagged_with_policy_pr_hint():
    data = full_intake()
    data["jira"]["user_map"] = {}
    gap = next(g for g in intake.validate(data) if g.key == "jira.user_map")
    assert "qc-agent@main" in gap.reason and gap.blocks == "D"


def test_question3_pending_blocks_llm_steps_only_by_message():
    data = full_intake()
    data["llm"]["egress_question3"] = "pending"
    gap = next(g for g in intake.validate(data) if g.key == "llm.egress_question3")
    assert gap.blocker and "PENDING" in gap.reason and "B và E" in gap.reason


def test_budget_must_be_a_positive_number():
    for bad in (None, 0, -1, True, "2"):
        data = full_intake()
        data["llm"]["budget_usd_max"] = bad
        assert any(g.key == "llm.budget_usd_max" for g in intake.validate(data)), bad


def test_db_sut_requires_pinned_db_image_and_ready_cmd():
    data = full_intake()
    data["sut"]["db"].update(needed=True, image="postgres:16", ready_cmd=None)
    gaps = {g.key: g for g in intake.validate(data)}
    assert "sut.db.ready_cmd" in gaps and "sut.db.image" in gaps


def test_gt_agent_and_extra_judge_keys_are_flagged_as_notes_not_silently_allowed():
    data = full_intake()
    data["llm"].update(enable_gt_agent=True, judge_and_midscene_keys=True)
    notes = {g.key: g for g in intake.validate(data) if not g.blocker}
    assert "llm.enable_gt_agent" in notes and "llm.judge_and_midscene_keys" in notes


def test_template_is_not_overwritten_without_force(tmp_path):
    target = tmp_path / "intake.yaml"
    assert intake.write_template(target) is True
    target.write_text("# của người dùng\n", encoding="utf-8")
    assert intake.write_template(target) is False
    assert target.read_text(encoding="utf-8") == "# của người dùng\n"


def test_unknown_top_level_key_is_rejected(tmp_path):
    path = tmp_path / "i.yaml"
    path.write_text(yaml.safe_dump({"sut": {}, "secrets": {"ANTHROPIC_API_KEY": "sk-..."}}), encoding="utf-8")
    with pytest.raises(ValueError):
        intake.load(path)


# ───────────────────────── ngân sách offline ─────────────────────────

def test_budget_counts_for_ten_iterations():
    rows = {r.phase: r for r in budget.plan(10)}
    assert rows["10× (C+D)"].calls == (0, 20, 20)
    assert rows["A"].calls == (1, 1, 2)
    assert rows["D chạy lại"].calls[1] == 0   # cache hit là điều cần chứng minh, nên dự kiến 0
    assert rows["đo baseline thời gian"].calls == (0, 0, 0)


def test_budget_iterations_scale_linearly():
    assert {r.phase: r.calls for r in budget.plan(3)}["3× (C+D)"] == (0, 6, 6)


def test_budget_totals_use_the_single_price_table_and_cap_is_sensible():
    rows = budget.plan(10)
    t = budget.totals(rows)
    assert 0 < t["exp_usd"] <= t["max_usd"] < 5
    assert budget.recommended_cap(rows) >= max(1.0, t["max_usd"])
    select = next(r for r in rows if r.phase == "10× (C+D)")
    from qc_agent.llm.client import Usage
    from qc_agent.llm.prices import estimate_cost
    assert select.usd(1) == pytest.approx(estimate_cost(select.model, Usage(input_tokens=select.in_tok[0] * 20, output_tokens=select.out_tok[0] * 20)))


def test_count_tokens_row_is_priced_zero_only_as_a_flagged_assumption():
    text = budget.render(budget.plan(10))
    assert "EXTERNAL GAP" in text and "ƯỚC TÍNH" in text
    assert "Điều kiện dừng" in text and "câu hỏi #3" in text


def test_budget_render_lists_every_phase_and_data_sent():
    text = budget.render(budget.plan(10), cap=3.0)
    for needle in ("gt-generate", "diff-select", "count_tokens", "đo recall", "Trần đã chọn: $3.00"):
        assert needle in text


def test_other_channels_are_listed_but_not_priced():
    rows = budget.plan(10, with_other_channels=True)
    other = rows[-1]
    assert other.model is None and "KHÔNG ước tính được" in other.note


def _usage(tmp, name, rows):
    path = tmp / name / "llm_usage.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def _row(usd, **extra):
    return {"purpose": "diff-select", "est_usd": usd, "cache_hit": False, **extra}


def test_ledger_continues_below_80_percent_and_stops_at_it(tmp_path):
    _usage(tmp_path, "a", [_row(0.5)])
    assert budget.ledger(tmp_path, 1.0)["decision"] == "CONTINUE"
    _usage(tmp_path, "b", [_row(0.31)])
    result = budget.ledger(tmp_path, 1.0)
    assert result["decision"] == "STOP" and result["spent_usd"] == pytest.approx(0.81)


def test_ledger_does_not_charge_cache_hits_but_stops_on_unknown_cost(tmp_path):
    _usage(tmp_path, "a", [_row(0.4), _row(9.0, cache_hit=True)])
    assert budget.ledger(tmp_path, 1.0)["spent_usd"] == pytest.approx(0.4)
    _usage(tmp_path, "b", [_row(None, usage_known=False)])
    result = budget.ledger(tmp_path, 1.0)
    assert result["decision"] == "STOP" and result["unknown_calls"] == 1


def test_ledger_on_empty_directory_is_zero_and_continues(tmp_path):
    assert budget.ledger(tmp_path, 1.0) == {"files": 0, "calls": 0, "cache_hits": 0, "unknown_calls": 0, "non_ok_calls": 0, "spent_usd": 0.0, "cap_usd": 1.0,
                                                 "decision": "CONTINUE", "reasons": []}


# ───────────────────────── sandbox + kịch bản cục bộ ─────────────────────────

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    dest = tmp_path_factory.mktemp("sandbox") / "sb"
    data = full_intake()
    return dest, sandbox.build_local(dest, data), data


def test_sandbox_has_no_ground_truth_and_one_base_commit_on_main(built):
    dest, report, _ = built
    assert report["status"] == "đã dựng" and not (dest / ".qc-agent" / "ground-truth").exists()
    assert not (dest / ".qc-agent" / "suites" / "gt-functional.yaml").exists()
    assert (dest / "docs" / "openapi.json").is_file()
    assert not (dest / "docs" / "prd").exists(), "PRD vào commit base thì lần đẩy đầu tiên kích hoạt qc-groundtruth trước khi có secret"
    assert common.git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert len(common.git(dest, "log", "--oneline").splitlines()) == 1


def test_sandbox_workflows_are_pinned_from_intake_and_codeowners_uses_the_real_team(built):
    dest, report, data = built
    gate = (dest / ".github" / "workflows" / "qc-gate.yml").read_text(encoding="utf-8")
    assert SHA in gate and DIGEST in gate and "qc-agent:todo" not in gate
    assert "@acme/qa" in (dest / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert report["notes"] == []


def test_sandbox_is_idempotent_and_refuses_foreign_directories(built, tmp_path):
    dest, _, data = built
    head = common.git(dest, "rev-parse", "HEAD")
    again = sandbox.build_local(dest, data)
    assert again["status"] == "đã có" and common.git(dest, "rev-parse", "HEAD") == head
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "x.txt").write_text("không phải sandbox", encoding="utf-8")
    with pytest.raises(FileExistsError):
        sandbox.build_local(foreign, data)
    assert (foreign / "x.txt").read_text(encoding="utf-8") == "không phải sandbox"


def test_sandbox_does_not_modify_the_noteboard_fixture():
    before = subprocess.run(["git", "status", "--porcelain", "--", "tests/fixtures"], cwd=ROOT, capture_output=True, text=True).stdout
    sandbox.dry_run(intake.template_data())
    after = subprocess.run(["git", "status", "--porcelain", "--", "tests/fixtures"], cwd=ROOT, capture_output=True, text=True).stdout
    assert before == after


def test_dry_run_reports_template_todos_and_leaves_nothing_behind():
    report = sandbox.dry_run(intake.template_data())
    assert report["has_ground_truth"] is False and any("qc-agent:todo" in n for n in report["notes"])
    assert ".github/workflows/qc-gate.yml" in report["generated"]


def test_secret_list_contains_names_only():
    names = [n for n, _, _ in sandbox.secrets(full_intake())]
    assert "ANTHROPIC_API_KEY" in names and "JIRA_API_TOKEN" in names
    assert "OPENAI_API_KEY" not in names and "SUT_SECRET_ENV" not in names
    data = full_intake()
    data["sut"]["db"]["needed"] = True
    data["llm"]["judge_and_midscene_keys"] = True
    names = [n for n, _, _ in sandbox.secrets(data)]
    assert {"SUT_SECRET_ENV", "SUT_DB_SECRET_ENV", "OPENAI_API_KEY", "GEMINI_API_KEY"} <= set(names)


def test_prepare_a_adds_the_prd_as_a_second_local_commit_on_main_and_only_once(tmp_path):
    dest = tmp_path / "sb"
    sandbox.build_local(dest, full_intake())
    base = common.git(dest, "rev-parse", "HEAD")
    branch, sha = scenarios.prepare("A", dest, "prd")
    assert branch == "main" and sha != base and common.git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert common.git(dest, "diff", "--name-only", f"{base}..{sha}").splitlines() == ["docs/prd/noteboard-prd.md"]
    assert (dest / "docs" / "prd" / "noteboard-prd.md").read_bytes() == scenarios.PRD_SRC.read_bytes()
    with pytest.raises(RuntimeError, match="đã có"):
        scenarios.prepare("A", dest, "prd")


def test_prepare_a_refuses_off_main_or_dirty_tree(tmp_path):
    dest = tmp_path / "sb"
    sandbox.build_local(dest, full_intake())
    common.git(dest, "switch", "-c", "other")
    with pytest.raises(RuntimeError, match="main"):
        scenarios.prepare("A", dest, "prd")
    common.git(dest, "switch", "main")
    (dest / "stray.txt").write_text("bẩn", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bẩn"):
        scenarios.prepare("A", dest, "prd")


def _diff_names(repo, branch):
    return common.git(repo, "diff", "--name-only", f"main...{branch}").splitlines()


def test_prepare_c_adds_one_regression_line_only_in_toyapp(built):
    dest, _, _ = built
    branch, sha = scenarios.prepare("C", dest, "t1")
    assert branch == "qc-e2e/c-t1" and common.git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _diff_names(dest, branch) == ["toyapp/app.py"]
    patch = common.git(dest, "diff", f"main...{branch}")
    added = [line for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]
    assert added == ['+BUGS.add("1")  # hồi quy: bật lại BUG-1']
    assert common.git(dest, "rev-parse", branch) == sha


def test_prepare_d_adds_untested_endpoint_only_in_toyapp(built):
    dest, _, _ = built
    branch, _ = scenarios.prepare("D", dest, "t1")
    assert _diff_names(dest, branch) == ["toyapp/app.py"]
    assert "word-count" in common.git(dest, "diff", f"main...{branch}")


@pytest.mark.parametrize("scenario", ["C", "D"])
def test_prepare_never_touches_full_set_paths(built, scenario):
    """`.github/workflows/**` và `.qc-agent/**` là FULL SET (0 lời gọi LLM): PR đo Select không được đụng vào đó."""
    default = yaml.safe_load((ROOT / "configs" / "projects" / "_default.yaml").read_text(encoding="utf-8"))
    full = default["modes"]["pr"]["full_set_paths"]
    assert ".github/workflows/**" in full and ".qc-agent/**" in full
    dest, _, _ = built
    branch, _ = scenarios.prepare(scenario, dest, f"fs-{scenario}")
    assert all(not p.startswith((".github/", ".qc-agent/")) and p not in ("Dockerfile", "package.json") for p in _diff_names(dest, branch))


def test_prepare_rejects_bad_tag_duplicate_branch_dirty_tree_and_unknown_scenario(built):
    dest, _, _ = built
    for bad in ("", "a b", "x/y", "a" * 41):
        with pytest.raises(ValueError):
            scenarios.prepare("C", dest, bad)
    scenarios.prepare("C", dest, "dup")
    with pytest.raises(RuntimeError, match="đã tồn tại"):
        scenarios.prepare("C", dest, "dup")
    with pytest.raises(ValueError):
        scenarios.prepare("E", dest, "x")
    (dest / "stray.txt").write_text("bẩn", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="bẩn"):
            scenarios.prepare("D", dest, "dirty")
    finally:
        (dest / "stray.txt").unlink()


def test_prepare_rolls_back_when_the_toy_app_changed(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "toyapp").mkdir()
    (repo / "toyapp" / "app.py").write_text("print('khác hẳn')\n", encoding="utf-8")
    common.git(repo, "init", "-q")
    common.git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    common.git(repo, "add", "-A")
    common.git(repo, "commit", "-qm", "base")
    with pytest.raises(ValueError):
        scenarios.prepare("C", repo, "x")
    assert common.git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main" and common.git(repo, "branch", "--list", "qc-e2e/*") == ""
    assert common.git(repo, "status", "--porcelain") == ""


def test_prepare_works_from_any_current_branch_and_returns_to_it(built):
    dest, _, _ = built
    common.git(dest, "switch", "-c", "scratch")
    try:
        scenarios.prepare("D", dest, "from-scratch")
        assert common.git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "scratch"
    finally:
        common.git(dest, "switch", "main")


# ───────────────────────── danh sách lệnh ─────────────────────────

def test_plan_groups_never_mix_external_commands_into_offline():
    steps = plan.full_plan(full_intake())
    external_markers = ("gh ", "git push", "curl ", "--yes", "protect_ground_truth")
    offenders = [s.title for s in steps if s.kind == OFFLINE and s.command and any(m in s.command for m in external_markers)]
    assert offenders == []


def test_real_llm_commands_need_question3_and_budget_and_use_yes():
    llm = [s for s in plan.llm_steps() if s.command]
    assert llm and all("--yes" in s.command for s in llm)
    assert all(any("câu hỏi" in n or "egress_question3" in n for n in s.needs) and any("ngân sách" in n for n in s.needs) for s in llm)


def test_every_external_or_llm_step_with_a_command_is_a_gh_git_curl_or_script_command():
    for step in plan.full_plan(full_intake()):
        if step.kind in (EXTERNAL, LLM) and step.command:
            first = [line.strip() for line in step.command.splitlines() if line.strip() and not line.strip().startswith("#")]
            assert all(line.split()[0] in ("gh", "git", "curl", "python", "bash", "echo", "(date", "export") or line.startswith(("(date", "&&", "\\")) for line in first), step.title


def test_plan_orders_prd_push_before_protection_and_secrets_before_prd_push():
    """Đẩy PRD (A) phải sau khi secret có và TRƯỚC khi bật branch protection (protection chặn push thẳng vào main)."""
    steps = plan.full_plan(full_intake())
    def first(text):
        return next(i for i, s in enumerate(steps) if text in (s.command or "") or text in s.title)
    assert first("gh secret set ANTHROPIC_API_KEY") < first("git -C <sandbox-dir> push origin main") < first("protect_ground_truth.py")


def test_prd_push_step_requires_that_protection_is_not_yet_on_and_question3():
    step = next(s for s in scenarios.steps("A", repo="o/r", project="noteboard", key="K", evidence="ev") if s.kind == LLM)
    assert any("branch protection CHƯA bật" in n for n in step.needs) and any("question3" in n for n in step.needs)


def test_scenario_b_nonqa_step_demands_write_but_not_admin_access():
    steps = scenarios.steps("B", repo="o/r", project="noteboard", key="K", evidence="ev", non_qa="outsider")
    push = next(s for s in steps if "push thẳng" in s.title)
    assert any("write" in n and "admin" in n for n in push.needs) and "enforce_admins" in push.note


def test_each_scenario_has_one_grouped_read_only_evidence_step():
    for sc in "ABCDE":
        steps = scenarios.steps(sc, repo="o/r", project="noteboard", key="K", evidence="ev")
        reads = [s for s in steps if s.title.startswith("Thu bằng chứng")]
        assert len(reads) == 1 and reads[0].kind == EXTERNAL and reads[0].command.count("\n") >= 1


def test_protect_script_is_only_ever_printed_with_dry_run_first():
    steps = [s for s in plan.full_plan(full_intake()) if s.command and "protect_ground_truth" in s.command]
    assert steps and all(s.kind == EXTERNAL for s in steps)
    for step in steps:
        lines = [line for line in step.command.splitlines() if "protect_ground_truth" in line]
        assert "--dry-run" in lines[0]


def test_llm_group_does_not_claim_fake_runs_count_as_recall():
    titles = " ".join(s.title for s in plan.offline_steps())
    assert "KHÔNG chứng minh recall" in titles and "KHÔNG phải GitHub thật" in titles


def test_scenario_steps_print_read_only_commands_with_the_collect_contract_names():
    for sc in "ABCDE":
        rows = scenarios.read_commands(sc, repo="o/r", project="noteboard", key="SBX", evidence="ev")
        text = "\n".join(cmd for _, cmd in rows)
        assert "gh pr merge" not in text and "--method" not in text and not re.search(r"-X (PUT|PATCH|DELETE)", text)
        assert all("-X POST" not in cmd for name, cmd in rows if name != "jira.json"), "chỉ truy vấn JQL của Jira (POST /search/jql, chỉ đọc) được dùng POST"
    c_files = [name for name, _ in scenarios.read_commands("C", repo="o/r", project="p", key="K", evidence="ev")]
    assert {"checks.json", "comments.json", "reviews.json", "artifact/"} <= set(c_files)
    assert "jira.json" in [n for n, _ in scenarios.read_commands("D", repo="o/r", project="p", key="K", evidence="ev")]


def test_jira_read_command_has_valid_json_after_formatting():
    cmd = dict(scenarios.read_commands("D", repo="o/r", project="p", key="SBX", evidence="ev"))["jira.json"]
    body = cmd.split("-d '", 1)[1].split("' >", 1)[0]
    assert json.loads(body)["jql"].startswith("project = SBX")
    assert "$JIRA_API_TOKEN" in cmd and "ATATT" not in cmd   # chỉ tham chiếu biến môi trường, không bao giờ nhúng giá trị


# ───────────────────────── preflight ─────────────────────────

def test_preflight_on_noteboard_separates_local_from_external():
    checks = preflight.run(intake.template_data())
    text = preflight.render(checks)
    by = {c.name: c for c in checks}
    assert by["contract còn nguyên"].status == preflight.OK
    assert by["Dockerfile"].status == preflight.OK and by["policy của project"].status == preflight.OK
    assert by["Jira user_map trong policy"].status == preflight.EXT
    assert "CHƯA CÓ BẰNG CHỨNG GITHUB THẬT" in text and "Cần thông tin/quyền bên ngoài" in text
    assert by["lệnh build SUT"].status == preflight.SKIP


def test_preflight_runs_sut_tests_only_on_a_temp_copy(tmp_path):
    sut = tmp_path / "sut"
    sut.mkdir()
    (sut / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (sut / "marker.py").write_text("import pathlib\npathlib.Path('written-by-test.txt').write_text('x')\n", encoding="utf-8")
    data = intake.template_data()
    data["sut"].update(local_path=str(sut), test_command="python marker.py", dockerfile="Dockerfile")
    checks = {c.name: c for c in preflight.run(data, run_sut_tests=True)}
    assert checks["lệnh test SUT (bản sao tạm)"].status == preflight.OK
    assert not (sut / "written-by-test.txt").exists(), "preflight không được ghi vào thư mục SUT gốc"


def test_preflight_reports_a_missing_sut_path_and_unrunnable_test_command(tmp_path):
    data = intake.template_data()
    data["sut"]["local_path"] = str(tmp_path / "nope")
    assert {c.name: c for c in preflight.run(data)}["SUT cục bộ"].status == preflight.FAIL
    sut = tmp_path / "sut"
    sut.mkdir()
    data["sut"].update(local_path=str(sut), test_command="npm test")
    checks = {c.name: c for c in preflight.run(data, run_sut_tests=True)}
    assert checks["lệnh test SUT (bản sao tạm)"].status == preflight.SKIP, "npm không nằm trong danh sách trắng: phải nói thẳng là không chạy được"


# ───────────────────────── CLI ─────────────────────────

def run_cli(*argv):
    return cli.main(list(argv))


def test_cli_intake_check_exit_codes(tmp_path, capsys):
    path = tmp_path / "i.yaml"
    assert run_cli("intake", "template", "--out", str(path)) == 0
    assert run_cli("intake", "check", str(path)) == 1
    out = capsys.readouterr().out
    assert "CHẶN" in out and "KHÔNG có nghĩa S4-06 đạt" in out
    path.write_text(yaml.safe_dump(full_intake(), allow_unicode=True), encoding="utf-8")
    assert run_cli("intake", "check", str(path)) == 0
    assert run_cli("intake", "check") == 3


def test_cli_commands_prints_three_groups(capsys):
    assert run_cli("commands") == 0
    out = capsys.readouterr().out
    assert out.index("== OFFLINE") < out.index("== LLM THẬT") < out.index("== TÁC ĐỘNG GITHUB/JIRA")


def test_cli_budget_and_ledger_exit_codes(tmp_path, capsys):
    assert run_cli("budget", "--cap", "2") == 0
    assert "Trần đã chọn: $2.00" in capsys.readouterr().out
    _usage(tmp_path, "r", [_row(0.9)])
    assert run_cli("ledger", str(tmp_path), "--cap", "1") == 1
    assert run_cli("ledger", str(tmp_path), "--cap", "5") == 0


def test_cli_stability_script_refuses_to_emit_without_a_budget_cap(tmp_path, capsys):
    data = full_intake()
    data["llm"]["budget_usd_max"] = None
    path = tmp_path / "i.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert run_cli("stability", "script", "--intake", str(path)) == 3
    assert "thiếu trần ngân sách" in capsys.readouterr().out


def test_cli_prepare_and_sandbox_plan_never_execute_external_commands(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(common.subprocess, "run", lambda argv, **kw: pytest.fail(f"không được chạy {argv}") if argv[0] not in ("git", sys.executable) else subprocess.CompletedProcess(argv, 0, "", ""))
    assert run_cli("sandbox", "--plan") == 0
    out = capsys.readouterr().out
    assert "gh repo create" in out and "gh secret set ANTHROPIC_API_KEY" in out and "protect_ground_truth" not in out
    assert out.count("== ") >= 2


def test_cli_rejects_unknown_scenario_for_prepare():
    with pytest.raises(SystemExit):
        run_cli("prepare", "E", "--repo", ".", "--tag", "x")


def test_python_dash_m_entrypoint_runs_from_repo_root():
    done = subprocess.run([sys.executable, "-m", "tools.e2e", "budget", "--iterations", "2"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0 and "2× (C+D)" in done.stdout, done.stderr[-300:]


def test_no_secret_value_shaped_strings_in_tools_or_template():
    text = "\n".join(p.read_text(encoding="utf-8") for p in E2E.glob("*.py")) + intake.TEMPLATE
    assert not re.search(r"sk-ant-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|ATATT[A-Za-z0-9]{10,}", text)
