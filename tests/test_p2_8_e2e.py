"""P2-8: nghiệm thu E2E của khâu dò nợ. KHÔNG có quyền push vào repo GitHub thật (Muteen-Felix/vahan-rpa) hay image qc-agent publish
lên ghcr.io ghim digest để workflow thật dùng, nên nghiệm thu bằng đúng hạ tầng LOCAL đã có trong repo: Docker thật chạy image
qc-agent, PostgreSQL thật, máy chủ GitHub GIẢ (FakeGitHub, tests/fakes.py) nhận Check Run/comment — cùng cơ sở hạ tầng
tests/test_reusable_workflow.py đã dùng để kiểm workflow. Policy dùng ĐÚNG configs/projects/ đã bật coverage-debt ở bước này
(không sao chép/giả lập). "PR" dựng trên một bản sao git-hoá của SUT noteboard, vì repo vahan-rpa thật ngoài tầm với.

Pha A (Mode 1 — PR, diff-scan, qua reusable workflow + CI ingest P2-5/P2-6): PR thêm 1 endpoint API + 1 UI route, không viết
test => Check Run neutral, tiêu đề đúng D2, comment liệt kê đúng 2 bề mặt, 2 dòng test_debt MỞ.
Pha B (Mode 2 — thủ công, full-scan, qua Executor thật — đúng nhánh "executor (Mode 2)" trong sơ đồ luồng dữ liệu của
plan-debt.md §1, không qua GitHub): sau khi thêm test cho cả hai, 2 dòng nợ ĐÓNG với closed_reason=covered.

Cần docker + QC_TEST_DOCKER_IMAGE (image qc-agent đã build, vd `docker build -t qc-agent:dev .`) + QC_TEST_DATABASE_URL + git.
Chậm (build SUT + một lượt gate thật + một lượt executor thật): không chạy mặc định."""
import shutil

import pytest

from debtkit import Repo
from qc_agent.jobs import repository as repo
from qc_agent.jobs.db import session_scope
from qc_agent.jobs.executor import Executor, ExecutorConfig
from tests.dbfix import db_url, engine, make_db, requires_pg  # noqa: F401  (fixtures của `stack`)
from tests.test_reusable_workflow import IMAGE, POLICY, ROOT, run, stack  # noqa: F401  (hạ tầng đã có)

pytestmark = [requires_pg, pytest.mark.skipif(not IMAGE or shutil.which("docker") is None, reason="needs docker + QC_TEST_DOCKER_IMAGE"),
              pytest.mark.skipif(shutil.which("git") is None, reason="cần binary git")]

NOTEBOARD = ROOT / "tests" / "fixtures" / "sut" / "noteboard"
NEW_ROUTE_FILE = "toyapp/static/routes.js"
NEW_ENDPOINT, NEW_ROUTE = ("api_endpoint", "GET /stats"), ("ui_route", "/settings")


def sut_repo(tmp_path) -> Repo:
    """Bản sao git-hoá của SUT noteboard (đã có .qc-agent/suites/coverage-debt.yaml từ P2-3): vahan-rpa thật ngoài tầm với,
    nhưng cùng cơ chế — image, Dockerfile, suite mẫu, policy đều là bản THẬT của repo."""
    sandbox = Repo(tmp_path / "sut")
    for src in NOTEBOARD.rglob("*"):
        if src.is_file() and "__pycache__" not in src.parts:
            dst = sandbox.root / src.relative_to(NOTEBOARD)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
    sandbox.commit("base: SUT noteboard nguyên trạng (coverage-debt đã bật ở modes.pr của policy)")
    return sandbox


def add_pr_surfaces(sandbox: Repo) -> None:
    """PR thêm 1 endpoint API (GET /stats) + 1 UI route (/settings), KHÔNG viết test — đúng kịch bản DoD của P2-8."""
    app = sandbox.root / "toyapp" / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + '''

@app.get("/stats", include_in_schema=True)
def stats():
    """Endpoint MỚI của PR (P2-8 E2E), chưa có test nào chạm tới."""
    return {"notes": 0}
''', encoding="utf-8")
    sandbox.write(NEW_ROUTE_FILE, """
        export const routes = [
          { path: '/settings', component: SettingsPage },
        ];
    """)
    sandbox.commit("pr: thêm GET /stats + UI route /settings, chưa có test")


def add_tests_for_the_surfaces(sandbox: Repo) -> None:
    sandbox.write("tests/test_stats.py", 'def test_stats(client):\n    client.get("/stats")\n')
    sandbox.write("e2e/settings.spec.js", "await page.goto('/settings');\n")
    sandbox.commit("thêm test cho GET /stats và UI route /settings")


def open_debt(engine, slug="noteboard") -> list:
    with session_scope(engine) as s:
        return sorted((r.kind, r.surface) for r in repo.list_debt(s, slug))


def closed_reasons(engine, slug="noteboard") -> dict:
    with session_scope(engine) as s:
        return {(r.kind, r.surface): r.closed_reason for r in repo.list_debt(s, slug, open_only=False) if r.closed_at}


def test_pr_opens_two_debts_with_neutral_check_then_manual_full_scan_closes_them(tmp_path, stack, monkeypatch):
    sandbox = sut_repo(tmp_path)
    add_pr_surfaces(sandbox)

    # ---- Pha A: Mode 1 (PR, diff-scan) — reusable workflow thật + policy THẬT đã bật coverage-debt ở P2-8 ----
    outcome, log = run(stack, bugs="none", run_id="8001", workspace=sandbox.root, policy_dir=POLICY)
    assert outcome["Run qc-agent gate"]["outputs"] == {"exit_code": "0"}, log  # YELLOW => exit 0, không chặn merge
    assert outcome["Enforce gate result"]["returncode"] == 0, log

    check = stack.gh.check_runs[-1]
    assert check["conclusion"] == "neutral"  # ⚪
    assert check["output"]["title"] == "PASS hồi quy · 2 bề mặt mới chưa có test"

    body = stack.gh.comments[-1]["body"]
    assert "### ⚠️ Nợ test mới phát sinh (Không chặn merge)" in body
    assert "**api\\_endpoint** — GET /stats" in body and "**ui\\_route** — /settings" in body
    assert body.index("**api\\_endpoint**") < body.index("**ui\\_route**")  # sắp xếp tất định (kind, surface)

    assert open_debt(stack.engine) == sorted([NEW_ENDPOINT, NEW_ROUTE])  # đúng 2 dòng MỞ trong test_debt, không hơn không kém

    # ---- Pha B: Mode 2 (thủ công, full-scan) — Executor thật, KHÔNG qua GitHub (docs/phase2/plan-debt.md §1) ----
    add_tests_for_the_surfaces(sandbox)
    monkeypatch.setenv("APP_BASE_URL", "http://sut.invalid")  # bắt buộc bởi schema (target.base_url); adapter bỏ qua giá trị này
    monkeypatch.delenv("QC_DIFF_BASE", raising=False)  # D4: không đặt => full-scan
    executor = Executor(stack.engine, ExecutorConfig(runs_root=tmp_path / "runs", projects_dir=ROOT / "configs" / "projects",
                                                     tick_s=0.1, heartbeat_interval_s=0.3, default_timeout_s=120, cancel_grace_s=5,
                                                     sut_root_for=lambda job, slug: sandbox.root))
    with session_scope(stack.engine) as s:
        job_id = repo.create_job(s, "noteboard", mode="manual", source="web", suites=["coverage-debt"]).id
    assert executor.run_once() is True
    with session_scope(stack.engine) as s:
        job = repo.get_job(s, job_id)
        lanes = {t.task_id: t.lane for t in job.tasks}
    assert job.status == "succeeded" and job.gate_verdict == "PASS" and lanes == {"t-103": "discovery"}  # cả 2 bề mặt đã có test

    assert open_debt(stack.engine) == []  # 2 dòng nợ đã ĐÓNG, sổ hết nợ
    assert closed_reasons(stack.engine) == {NEW_ENDPOINT: "covered", NEW_ROUTE: "covered"}
