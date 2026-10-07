"""Kịch bản N1–N4 của fixture `node-api-harness` (S4-05): workflow qc-gate.reusable.yml chạy trên Docker cục bộ bằng tools/run_reusable_locally.py, repo Node, project CHƯA đăng ký
(policy `_default.yaml`), GitHub/Anthropic/Jira GIẢ. Mỗi kịch bản assert TỪNG BƯỚC (Start SUT, Select, Gate, PR review, Jira, Report, Enforce) và các fake.

PHẠM VI KẾT LUẬN: các test này chứng minh luồng Select → Gate → Report chạy được cho repo Node dùng policy mặc định với LLM/GitHub/Jira GIẢ. Chúng KHÔNG chứng minh chất lượng
Selector (LLM là fake: recall không được đo), KHÔNG chứng minh hành vi của GitHub Actions thật, và không nói gì về pytest/GT/Jira (policy mặc định không có).
Image phải build từ commit đang thử (tools/image_check.py); thiếu Docker/image thì SKIP = CHƯA KIỂM CHỨNG (QC_HARNESS_REQUIRE_DOCKER=1 biến skip thành lỗi)."""
import json

import pytest

from tests import harness_kit as kit

pytestmark = pytest.mark.docker
INPUTS = {"sut_port": "3000", "sut_health_path": "/health"}


@pytest.fixture(scope="module")
def image():
    return kit.verified_image().image


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    """Luồng PR không được phụ thuộc QC_DATABASE_URL (CLAUDE.md): harness không truyền `qc_api_url`, và biến này không được đặt."""
    monkeypatch.delenv("QC_DATABASE_URL", raising=False)
    monkeypatch.delenv("QC_TEST_DATABASE_URL", raising=False)


class Scenario:
    """Một kịch bản: workspace + fake, chạy được nhiều lần trên cùng SHA (mô phỏng push lại / re-run)."""

    def __init__(self, image, name, apply, *llm, control=False, expect_deps=False):
        self.image, self.name, self.expect_deps = image, name, expect_deps
        self.workspace = kit.workspace_dir()
        self.base_dir = self.workspace.__enter__()
        self.repo, self.base = kit.make_repo(kit.NODE_SUT, self.base_dir)
        if control:
            kit.strip_exclude_path(self.repo)
            self.base = kit.commit(self.repo, "base (đối chứng: không exclude_path)")
        apply(self.repo)
        self.head = kit.commit(self.repo, "head")
        self.policy = kit.default_only_policy(self.base_dir)
        self.stack_cm = kit.fake_stack(kit.select_response(*llm))
        self.stack = self.stack_cm.__enter__()
        self.stack.gh.pr_files = kit.pr_files_from_git(self.repo, self.base, self.head)
        self.runs = 0

    def run(self) -> "kit.Result":
        kit.preflight(self.image, self.name, expect_deps=self.expect_deps)          # TRƯỚC Gate
        self.runs += 1
        res = kit.run_pr(self.stack, self.repo, project=kit.NODE_PROJECT, policy_dir=self.policy, image=self.image, base=self.base, head=self.head,
                         run_id=str(7000 + self.runs), home=self.base_dir / "home", inputs=INPUTS)
        kit.check_expect_deps(res, self.name, expect_deps=self.expect_deps, image=self.image)   # SAU Gate
        return res

    def close(self):
        self.stack_cm.__exit__(None, None, None)
        self.workspace.__exit__(None, None, None)


@pytest.fixture
def scenario(image):
    made = []

    def make(name, apply, *llm, **kwargs):
        item = Scenario(image, name, apply, *llm, **kwargs)
        made.append(item)
        return item

    yield make
    for item in made:
        item.close()


def _assert_pipeline_ran(res, *, gate_exit: str):
    """Mọi bước shell của workflow đã chạy đúng thứ tự và trả kết quả mong đợi (PR review/Jira/Report là continue-on-error nên luôn 0)."""
    assert list(res.steps) == ["Start SUT", "Select (PR)", "Run qc-agent gate", "PR review", "Jira (Low)", "Report (Check Run, PR comment, history, webhook)",
                               "Clean up SUT", "Enforce gate result"], res.log
    assert res.outputs("Start SUT") == {"base_url": "http://sut:3000"}, res.log
    assert res.outputs("Select (PR)") == {"ok": "true"} and "Select failed" not in res.log, res.log
    assert res.outputs("Run qc-agent gate") == {"exit_code": gate_exit}, res.log
    for step in ("PR review", "Jira (Low)", "Report (Check Run, PR comment, history, webhook)", "Clean up SUT"):
        assert res.code(step) == 0, (step, res.log)
    assert res.code("Enforce gate result") == (0 if gate_exit == "0" else 1), res.log


# ───────────────────────── N1 ─────────────────────────

def test_n1_route_change_goes_through_llm_select_gate_and_report_then_reruns_from_the_select_cache(scenario):
    s = scenario("N1", kit.apply_n1, "schemathesis", expect_deps=False)
    res = s.run()
    _assert_pipeline_ran(res, gate_exit="0")
    sel = res.selection
    assert sel["source"] == "llm" and sel["full_set"] is False and sel["fallback_reason"] is None, sel
    assert sel["suites"] == ["api-contract", "sast", "secrets"] and sel["floor"] == ["gitleaks", "semgrep"], sel
    assert kit.capabilities(res) == {"api.property", "code.sast", "code.secret"}      # đúng ba suite, không deps/coverage-debt
    # đúng 1 lời gọi LLM; request chứa đường dẫn file đã đổi, nằm trong vùng phân cách, và KHÔNG chứa bí mật
    assert s.stack.llm.count == 1 and not s.stack.llm.requests[0]["rejected"]
    body = json.dumps(s.stack.llm.requests[0]["body"], ensure_ascii=False)
    assert "src/routes/users.ts" in body and "openapi.json" in body and "<untrusted_diff>" in body
    assert kit.FAKE_KEY not in body and "ghs_local_test_token" not in body
    # báo cáo lên GitHub giả
    assert kit.check_runs(s.stack) == [("qc-agent / node-api-harness", "success", "✅ PASS")]
    assert s.stack.gh.check_runs[0]["head_sha"] == s.head
    assert len(s.stack.gh.comments) == 1 and "<!-- qc-agent:node-api-harness:pr -->" in s.stack.gh.comments[0]["body"]
    assert s.stack.gh.reviews == [] and s.stack.jira.requests == []
    assert res.report["gate_verdict"] == "PASSED" and res.report["severity_counts"] == {"critical": 0, "medium": 0, "low": 0}

    again = s.run()                                                                   # push lại / re-run cùng SHA
    _assert_pipeline_ran(again, gate_exit="0")
    assert again.selection["source"] == "cache" and s.stack.llm.count == 1, "Select cache hit: không thêm lời gọi LLM"
    assert again.selection["suites"] == sel["suites"]
    assert len(s.stack.gh.comments) == 1, "comment dính được cập nhật tại chỗ, không tạo thêm"
    assert len(s.stack.gh.check_runs) == 2 and s.stack.gh.reviews == []


# ───────────────────────── N2 ─────────────────────────

def test_n2_eval_is_blocked_by_the_floor_even_when_the_llm_selects_nothing(scenario):
    s = scenario("N2", kit.apply_n2, expect_deps=False)                                 # fake LLM trả selections RỖNG
    res = s.run()
    _assert_pipeline_ran(res, gate_exit="1")
    sel = res.selection
    assert sel["source"] == "llm" and s.stack.llm.count == 1
    assert sel["floor"] == ["gitleaks", "semgrep"] and {"semgrep", "gitleaks"} <= set(sel["workers"])
    assert sel["rationale"]["semgrep"] == "floor" and sel["rationale"]["gitleaks"] == "floor"
    assert {"code.sast", "code.secret"} <= kit.capabilities(res), "floor phải chạy dù LLM chọn rỗng"
    assert res.report["gate_verdict"] == "BLOCKED" and res.report["exit_code"] == 1
    critical = [f for f in res.report["findings"] if f["severity"] == "critical" and f["worker"] == "semgrep"]
    assert critical and critical[0]["path"] == "src/routes/debug.ts" and critical[0]["line"] == 2
    assert "js-eval-dynamic" in critical[0]["rule_id"]
    assert [c[:2] for c in kit.check_runs(s.stack)] == [("qc-agent / node-api-harness", "failure")] and "BLOCKED" in kit.check_runs(s.stack)[0][2]
    assert len(s.stack.gh.reviews) == 1
    inline = s.stack.gh.reviews[0]["comments"]
    assert [(c["path"], c["line"], c["side"]) for c in inline] == [("src/routes/debug.ts", 2, "RIGHT")], "inline comment đúng file và dòng"
    assert "BLOCKED" in s.stack.gh.comments[0]["body"] and s.stack.jira.requests == []


# ───────────────────────── N3 ─────────────────────────

def test_n3_new_operation_with_a_reserved_exclude_path_is_a_low_warning_only_and_does_not_pull_in_deps(scenario):
    """PR chỉ đổi openapi.json + src/** (không đụng `.qc-agent/**`): không FULL SET nên `deps` không chạy. `exclude_path` có sẵn ở fixture gốc là lý do phát sinh nợ Low.
    Test KHÔNG kiểm schemathesis (nó không gọi operation này) và KHÔNG kiểm handler (xem tests/test_node_api_harness_serve.py: 200 đúng schema và 404 khai báo)."""
    s = scenario("N3", kit.apply_n3, "schemathesis", "coverage-debt", expect_deps=False)
    res = s.run()
    _assert_pipeline_ran(res, gate_exit="0")
    sel = res.selection
    assert sel["full_set"] is False and sel["source"] == "llm"
    assert sel["suites"] == ["api-contract", "coverage-debt", "sast", "secrets"], "không có deps (không FULL SET)"
    assert "deps.vuln" not in kit.capabilities(res) and "repo.coverage_debt" in kit.capabilities(res)
    debt = [f for f in res.report["findings"] if f["rule_id"] == "api_contract"]
    assert len(debt) == 1 and debt[0]["severity"] == "low" and debt[0]["title"] == "Nợ test [api_contract]: GET /users/{id}/profile chưa có test"
    assert debt[0]["path"] == "openapi.json" and isinstance(debt[0]["line"], int)
    assert {f["rule_id"] for f in res.report["findings"]} == {"api_contract", "debt.new"}      # 1 nợ + dòng oracle `debt.new = 1`
    assert res.report["severity_counts"] == {"critical": 0, "medium": 0, "low": 2}
    assert res.report["gate_verdict"] == "PASSED_WITH_WARNINGS" and res.report["exit_code"] == 0
    (name, conclusion, title), = kit.check_runs(s.stack)
    assert (name, conclusion) == ("qc-agent / node-api-harness", "success") and "cảnh báo Low" in title, title
    comment_body = s.stack.gh.comments[0]["body"]
    assert "PASSED\\_WITH\\_WARNINGS" in comment_body and "GET /users/\\{id\\}/profile" in comment_body
    inline = s.stack.gh.reviews[0]["comments"]
    assert [(c["path"], c["line"]) for c in inline] == [(debt[0]["path"], debt[0]["line"])], "inline comment đúng dòng của operation mới"
    # Jira: policy mặc định không có jira.project_key => bỏ qua, không request nào tới Jira
    assert s.stack.jira.requests == [] and s.stack.jira.issues == []
    assert json.loads((res.run_dir / "jira-status.json").read_text(encoding="utf-8"))["jira"].startswith("skipped")


def test_n3_control_without_the_reserved_exclude_path_is_not_debt_and_schemathesis_covers_the_operation(scenario):
    """Đối chứng khoá cơ chế `CoverageIndex.covered`: cùng PR nhưng suite gốc KHÔNG có exclude_path => operation đã có test (schemathesis gọi nó): không nợ."""
    s = scenario("N3-control", kit.apply_n3, "schemathesis", "coverage-debt", control=True, expect_deps=False)
    res = s.run()
    _assert_pipeline_ran(res, gate_exit="0")
    assert res.report["gate_verdict"] == "PASSED" and res.report["findings"] == [] and res.report["severity_counts"]["low"] == 0
    debt_result = next(json.loads(p.read_text(encoding="utf-8")) for p in (res.run_dir / "t-103").glob("debt.json"))
    assert debt_result["metrics"]["debt.new"] == 0
    stdout = (res.run_dir / "t-001" / "stdout.log").read_text(encoding="utf-8")
    assert "Operations:       3 selected / 3 total" in stdout, "schemathesis phải gọi cả operation mới (nên handler của PR phải phục vụ nó)"
    assert kit.check_runs(s.stack) == [("qc-agent / node-api-harness", "success", "✅ PASS")]


# ───────────────────────── N4 ─────────────────────────

def test_n4_root_lockfile_is_a_full_set_with_zero_llm_calls_and_runs_every_fixture_suite_including_deps(scenario, image):
    s = scenario("N4", kit.apply_n4, "schemathesis", expect_deps=True)                 # preflight: DB Trivy quá 14 ngày => LỖI CỨNG trước khi chạy
    res = s.run()
    _assert_pipeline_ran(res, gate_exit="0")
    sel = res.selection
    assert sel["full_set"] is True and sel["source"] == "rules" and sel["llm"] is None
    assert s.stack.llm.count == 0, "FULL SET theo rules: không gọi LLM"
    assert kit.capabilities(res) == {"api.property", "code.sast", "code.secret", "deps.vuln", "repo.coverage_debt"}, "mọi suite của fixture, gồm deps"
    deps_task = next(t for t in kit.plan_tasks(res) if t["capability"] == "deps.vuln")
    assert deps_task["oracle"]["assertions"][-1] == {"metric": "trivy.db_age_days", "op": "<=", "value": 14, "unit": "days"}
    assert res.report["gate_verdict"] == "PASSED" and kit.check_runs(s.stack) == [("qc-agent / node-api-harness", "success", "✅ PASS")]
    assert s.stack.jira.requests == []


def test_a_scenario_that_declares_the_wrong_expect_deps_fails_by_name(scenario, image):
    """Khai báo `expect_deps` không thể trôi âm thầm: N4 khai False mà deps chạy => test fail có tên kịch bản."""
    s = scenario("N4-declared-wrong", kit.apply_n4, "schemathesis", expect_deps=False)
    with pytest.raises(pytest.fail.Exception, match=r"N4-declared-wrong khai expect_deps=False nhưng deps ĐÃ chạy"):
        s.run()
