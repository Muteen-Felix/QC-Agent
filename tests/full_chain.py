"""Kịch bản A–E trên noteboard (S4-05): PRD → gt generate (LLM giả) → thân PR → (QA duyệt giả lập) → PR của dev → Select → Gate → Review → Jira, chạy bằng workflow THẬT trên
Docker cục bộ với GitHub/Anthropic/Jira GIẢ. Dùng chung cho pytest (tests/test_full_chain_local.py) và CLI (`tools/run_reusable_locally.py --scenario full-chain`).

Mỗi kịch bản trả một `Recorder`: bảng (bước, kỳ vọng, kết quả, lý do). Trạng thái PASS / FAIL / CHƯA KIỂM CHỨNG: dòng CHƯA KIỂM CHỨNG nêu thứ plan kỳ vọng nhưng harness
KHÔNG kiểm được (kèm lý do thật), nó không làm test đỏ nhưng luôn hiện ra trong bảng.

PHẠM VI KẾT LUẬN: workflow đúng cho project ĐÃ ĐĂNG KÝ (noteboard: policy riêng có gt-functional, ai-eval, Jira), stack Python; luồng shell/biến/mạng docker/Check Run/comment/review
ở GitHub GIẢ. KHÔNG nói gì về repo dùng policy mặc định, GitHub Actions thật, Anthropic/Jira thật, tạo PR thật (S4-06), chất lượng Selector (LLM là fake).
Kịch bản A theo phương án (b): chỉ chạy `gt generate` và `pr_body` trong container (lệnh giống job `generate`), KHÔNG phải bằng chứng đã chạy nguyên job tạo PR."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tests import harness_kit as kit

ROOT = kit.ROOT
GT = ".qc-agent/ground-truth"
FIXTURES = ROOT / "tests" / "fixtures"
PRD_FILE = FIXTURES / "prd" / "noteboard-prd.md"
OPENAPI = FIXTURES / "openapi" / "noteboard.json"
GT_RESPONSE = FIXTURES / "llm" / "gt_noteboard_response.json"
GT_EXPECTED = FIXTURES / "gt" / "noteboard" / "expected"
GT_WORKFLOW = ROOT / ".github" / "workflows" / "qc-groundtruth.reusable.yml"
GT_KEY = "sk-ant-FAKE-KEY-0123456789"

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "CHƯA KIỂM CHỨNG"


@dataclass
class Recorder:
    name: str
    title: str
    rows: list = field(default_factory=list)

    def check(self, step: str, expected: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((step, expected, PASS if ok else FAIL, detail if not ok else ""))
        return ok

    def unverified(self, step: str, expected: str, reason: str) -> None:
        self.rows.append((step, expected, UNVERIFIED, reason))

    def failures(self) -> list:
        return [row for row in self.rows if row[2] == FAIL]

    def unverified_rows(self) -> list:
        return [row for row in self.rows if row[2] == UNVERIFIED]

    def table(self) -> str:
        lines = [f"### {self.name} · {self.title}", f"{'bước':34} {'kết quả':16} kỳ vọng"]
        for step, expected, status, detail in self.rows:
            lines.append(f"{step:34} {status:16} {expected}" + (f"   <- {detail}" if detail else ""))
        return "\n".join(lines)


def safe_print(text: str) -> None:
    """Windows: stdout cp1252 không in được tiếng Việt (pytest -s, console); thay ký tự không in được thay vì làm test/CLI đỏ."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "ascii", "replace").decode(sys.stdout.encoding or "ascii"))


def guarded(rec: Recorder, body) -> Recorder:
    """Ngoại lệ không lường trước thành một dòng FAIL (bảng vẫn in ra đủ)."""
    try:
        body(rec)
    except (AssertionError, KeyError, IndexError, OSError, ValueError, subprocess.SubprocessError) as error:
        rec.check("kịch bản", "chạy không lỗi", False, f"{type(error).__name__}: {error}")
    return rec


# ───────────────────────── tiện ích ─────────────────────────

def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def _uid() -> str:
    return f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"


def _docker_run(*args: str, env: dict | None = None, timeout: int = 600):
    return subprocess.run(["docker", "run", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
                          env={**os.environ, **(env or {})})


def added_lines(patch: str) -> set[int]:
    """Số dòng (phía mới) được thêm trong một patch kiểu GitHub: dòng đó nằm TRONG diff nên inline comment gắn được."""
    lines, new = set(), 0
    for row in patch.splitlines():
        if row.startswith("@@"):
            new = int(re.search(r"\+(\d+)", row).group(1)) - 1
        elif row.startswith("+"):
            new += 1
            lines.add(new)
        elif not row.startswith("-"):
            new += 1
    return lines


def _patch_of(stack: kit.Stack, filename: str) -> str:
    return next(f["patch"] for f in stack.gh.pr_files if f["filename"] == filename)


REPORT_STEP = "Report (Check Run, PR comment, history, webhook)"


def step_code(res: kit.Result, name: str):
    """Mã thoát của một bước; bước không chạy => None (khác 0, nên bị tính là lỗi thay vì KeyError làm kịch bản dừng giữa chừng)."""
    return res.steps.get(name, {}).get("returncode")


STEPS_PR = ["Start SUT", "Select (PR)", "Run qc-agent gate", "PR review", "Jira (Low)", REPORT_STEP, "Clean up SUT", "Enforce gate result"]


def _pipeline(rec: Recorder, res: kit.Result, *, gate_exit: str, steps=STEPS_PR) -> None:
    rec.check("thứ tự các bước", " → ".join(s.split(" (")[0] for s in steps), list(res.steps) == steps, str(list(res.steps)))
    rec.check("Start SUT", "sẵn sàng, base_url=http://sut:8000", res.outputs("Start SUT") == {"base_url": "http://sut:8000"}, str(res.steps.get("Start SUT")))
    if "Select (PR)" in steps:
        rec.check("Select (PR)", "ok=true, không 'Select failed'", res.outputs("Select (PR)") == {"ok": "true"} and "Select failed" not in res.log, res.log[-400:])
    rec.check("Run qc-agent gate", f"exit_code={gate_exit}", res.outputs("Run qc-agent gate") == {"exit_code": gate_exit}, str(res.outputs("Run qc-agent gate")))
    # Mã thoát của TỪNG bước bắt buộc. Report phải tách riêng: Enforce chỉ nhìn exit_code của gate nên Report hỏng mà gate xanh thì Enforce vẫn 0, và `run_workflow`
    # không đổi mã trả về của CLI; không có dòng này thì Report lỗi đi qua kịch bản xanh.
    others = {name: step_code(res, name) for name in steps if name not in (REPORT_STEP, "Enforce gate result", "Run qc-agent gate")}
    rec.check("mã thoát các bước (trừ Report, gate, Enforce)", "mọi bước exit 0", all(code == 0 for code in others.values()), str(others))
    rec.check("mã thoát Report", "exit 0 (Check Run, comment PR, lịch sử, webhook)", step_code(res, REPORT_STEP) == 0, f"exit {step_code(res, REPORT_STEP)}: {res.log[-400:]}")
    rec.check("Run qc-agent gate (mã thoát bước)", "exit 0 (mã của gate nằm ở output exit_code)", step_code(res, "Run qc-agent gate") == 0, f"exit {step_code(res, 'Run qc-agent gate')}")
    rec.check("Enforce gate result", "job " + ("xanh" if gate_exit == "0" else "đỏ"), step_code(res, "Enforce gate result") == (0 if gate_exit == "0" else 1), str(step_code(res, "Enforce gate result")))
    rec.check("deps không chạy (noteboard không có suite deps)", "expect_deps=False", "deps.vuln" not in kit.capabilities(res), str(kit.capabilities(res)))


@dataclass
class Noteboard:
    """Một PR của dev trên noteboard: repo git thật (base/head), policy THẬT `configs/projects`, fake stack."""
    image: str
    base_dir: Path
    repo: Path
    base: str
    head: str
    stack: kit.Stack
    runs: int = 0

    def run(self, *, sut_env: str, event_name="pull_request", dispatch_inputs=None, extra_inputs=None) -> kit.Result:
        self.runs += 1
        inputs = {"sut_env": sut_env, **(extra_inputs or {})}
        return kit.run_pr(self.stack, self.repo, project="noteboard", policy_dir=kit.POLICY_DIR, image=self.image, base=self.base, head=self.head,
                          run_id=str(8000 + self.runs), home=self.base_dir / "home", inputs=inputs, event_name=event_name, dispatch_inputs=dispatch_inputs)


def _bump_version(repo: Path) -> None:
    """PR của dev: đổi một dòng trong toyapp/app.py (không thêm bề mặt API, không thêm nợ test)."""
    path = repo / "toyapp" / "app.py"
    old = 'app = FastAPI(title="noteboard", version="0.1.0")'
    assert path.read_text(encoding="utf-8").count(old) == 1
    path.write_text(path.read_text(encoding="utf-8").replace(old, old.replace("0.1.0", "0.1.1")), encoding="utf-8", newline="\n")


NEW_ENDPOINT = ('@app.get("/notes/{note_id}/word-count", responses={404: {"description": "not found"}})\n'
                'def word_count(note_id: str):\n    return {"words": len(_get(note_id)["body"].split())}\n\n\n')


def _add_untested_endpoint(repo: Path) -> None:
    """PR của dev: endpoint mới KHÔNG có test nào chạm tới => nợ test (finding Low, không chặn)."""
    path = repo / "toyapp" / "app.py"
    marker = '@app.delete("/notes/{note_id}"'
    text = path.read_text(encoding="utf-8")
    assert text.count(marker) == 1
    path.write_text(text.replace(marker, NEW_ENDPOINT + marker), encoding="utf-8", newline="\n")


class _NoteboardPR:
    def __init__(self, image: str, apply, *llm: str):
        self.image, self.apply, self.llm = image, apply, llm

    def __enter__(self) -> Noteboard:
        self.workspace = kit.workspace_dir()
        base_dir = self.workspace.__enter__()
        repo, base = kit.make_repo(kit.NOTEBOARD_SUT, base_dir)
        self.apply(repo)
        head = kit.commit(repo, "head")
        self.stack_cm = kit.fake_stack(kit.select_response(*self.llm))
        stack = self.stack_cm.__enter__()
        stack.gh.pr_files = kit.pr_files_from_git(repo, base, head)
        return Noteboard(self.image, base_dir, repo, base, head, stack)

    def __exit__(self, *exc):
        self.stack_cm.__exit__(*exc)
        self.workspace.__exit__(*exc)


# ───────────────────────── A: sinh GT (phương án b) ─────────────────────────

def scenario_a(image: str, base_dir: Path) -> Recorder:
    """`gt generate` + `pr_body` trong container (cùng lệnh docker run như job `generate`, cộng `-e ANTHROPIC_BASE_URL` để trỏ vào fake), assert file GT và thân PR.
    Workspace nằm ở `base_dir/sut` (người gọi giữ vòng đời để B dùng tiếp)."""
    def body(rec: Recorder) -> None:
        from tests.fakes import FakeAnthropic
        sut = base_dir / "sut"
        (sut / "docs" / "prd").mkdir(parents=True)
        shutil.copy(PRD_FILE, sut / "docs" / "prd" / "noteboard-prd.md")
        shutil.copy(OPENAPI, sut / "docs" / "openapi.json")
        egress = base_dir / "egress"
        egress.mkdir()
        with FakeAnthropic(GT_RESPONSE, key=GT_KEY, host="0.0.0.0") as llm:
            url = f"http://{kit.HOST}:{llm.server.server_port}"
            done = _docker_run("--rm", "--user", _uid(), "-e", "HOME=/tmp", "-v", f"{sut.as_posix()}:/work", "-v", f"{egress.as_posix()}:/egress", "-w", "/work",
                               "-e", "ANTHROPIC_API_KEY", "-e", "ANTHROPIC_BASE_URL", image, "gt", "generate", "--prd", "docs/prd/noteboard-prd.md", "--sut-root", "/work",
                               "--openapi", "docs/openapi.json", "--egress-dir", "/egress", "--summary-json", "/egress/summary.json",
                               env={"ANTHROPIC_API_KEY": GT_KEY, "ANTHROPIC_BASE_URL": url})
            rec.check("gt generate", "exit 0 (FakeAnthropic phát gt_noteboard_response.json)", done.returncode == 0, (done.stdout + done.stderr)[-500:])
            rec.check("lời gọi LLM", "đúng 1, khoá đúng, không bị từ chối", llm.count == 1 and not llm.requests[0]["rejected"] and llm.requests[0]["headers"]["x-api-key"] == GT_KEY,
                      f"count={llm.count}")
        produced = _tree(sut / ".qc-agent")
        produced.pop("ground-truth/openapi.snapshot.json", None)     # CLI ghi thêm; golden của render() không có
        produced.pop("ground-truth/test-cases.xlsx", None)           # nhị phân, không hứa ổn định byte
        golden = _tree(GT_EXPECTED / ".qc-agent")
        rec.check("file GT", "cây .qc-agent trùng golden tests/fixtures/gt/noteboard/expected", produced == golden,
                  f"khác: {sorted(set(produced) ^ set(golden)) or [k for k in golden if produced.get(k) != golden[k]][:5]}")
        summary = json.loads((egress / "summary.json").read_text(encoding="utf-8"))
        rec.check("summary.json", "generate · 4 story · 25 AC · 33 TC, toàn bộ draft",
                  (summary["command"], summary["stories"], summary["acs"], summary["test_cases"]) == ("generate", 4, 25, 33) and summary["by_status"]["draft"] == 33,
                  str({k: summary.get(k) for k in ("command", "stories", "acs", "test_cases", "by_status")}))
        body_run = _docker_run("--rm", "--user", _uid(), "-e", "HOME=/tmp", "-v", f"{egress.as_posix()}:/egress:ro", "--entrypoint", "python", image,
                               "-m", "qc_agent.groundtruth.pr_body", "/egress/summary.json")
        pr_body = body_run.stdout
        rec.check("thân PR (pr_body)", "exit 0, có số lượng và AC mồ côi/chưa phủ", body_run.returncode == 0 and "33" in pr_body and "AC-3.5" in pr_body and "AC-1.8" in pr_body,
                  (body_run.stderr or pr_body)[-300:])
        clean = "Noteboard là dịch vụ" not in pr_body and "<script" not in pr_body.lower() and not re.search(r"(?<![\w`])@\w", pr_body)
        rec.check("thân PR đã làm sạch (clean_md)", "không chứa văn bản PRD, không HTML thô, không @mention", clean, pr_body[:300])
        rec.unverified("nhánh/PR thật", "không assert gì về nhánh qc-agent/gt/<prd-id> hay PR", "phương án (b): chỉ S4-06 kiểm job `generate` nguyên vẹn (checkout, gh, push, mở PR)")

    return guarded(Recorder("A", "sinh GT từ PRD mẫu (gt generate + pr_body trong container)"), body)


# ───────────────────────── B: duyệt GT ─────────────────────────

def _validate(image: str, ws: Path) -> int:
    github = {"token": "t", "sha": "x", "actor": "tester", "run_attempt": "1", "event_name": "pull_request", "event": {}, "env": {}}
    inputs = {"project": "noteboard", "image": image, "prd_path": "docs/prd/**", "allow_unpinned_image": "true"}
    steps = kit.harness.run_workflow(GT_WORKFLOW, ws, inputs, {}, github, job="validate", echo=lambda *_: None)
    return steps["Run gt validate"]["returncode"]


def scenario_b(image: str, generated: Path) -> Recorder:
    """Job `validate` (qc-groundtruth.reusable.yml) trên GT do A sinh: còn draft → exit 1; QA duyệt hết (giả lập) → exit 0. Rồi GT đã duyệt có sẵn của noteboard (S1-08) → exit 0."""
    def body(rec: Recorder) -> None:
        from tests.test_gt_cli import approve_everything
        rec.check("gt validate (còn draft)", "exit 1", _validate(image, generated) == 1)
        approve_everything(generated)                                         # thao tác của QA: draft → approved, điền module-map
        rec.check("gt validate (QA đã duyệt)", "exit 0", _validate(image, generated) == 0)
        with kit.workspace_dir() as base_dir:
            fixture = base_dir / "approved"
            (fixture / ".qc-agent").mkdir(parents=True)
            shutil.copytree(kit.NOTEBOARD_SUT / ".qc-agent" / "ground-truth", fixture / GT)
            shutil.copytree(kit.NOTEBOARD_SUT / ".qc-agent" / "suites", fixture / ".qc-agent" / "suites")
            rec.check("gt validate (GT đã duyệt của S1-08)", "exit 0", _validate(image, fixture) == 0)
            tests_file = fixture / GT / "tests_gt" / "test_us_1.py"
            tests_file.write_text(tests_file.read_text(encoding="utf-8") + "# sửa tay\n", encoding="utf-8")
            rec.check("gt validate (sửa tay file sinh ra)", "exit 1 (drift)", _validate(image, fixture) == 1)

    return guarded(Recorder("B", "áp GT đã duyệt: validate exit 0, còn draft/drift thì exit 1"), body)


# ───────────────────────── C: lỗi Critical ─────────────────────────

def scenario_c(image: str) -> Recorder:
    def body(rec: Recorder) -> None:
        with _NoteboardPR(image, _bump_version, "schemathesis") as nb:
            res = nb.run(sut_env="QC_BUGS=1")
            _pipeline(rec, res, gate_exit="1")
            report = res.report
            rec.check("verdict", "BLOCKED, exit 1, có finding Critical", report["gate_verdict"] == "BLOCKED" and report["exit_code"] == 1 and report["severity_counts"]["critical"] >= 1,
                      str(report["severity_counts"]))
            rec.check("Check Run", "failure, tiêu đề BLOCKED", [c[:2] for c in kit.check_runs(nb.stack)] == [("qc-agent / noteboard", "failure")] and "BLOCKED" in kit.check_runs(nb.stack)[0][2],
                      str(kit.check_runs(nb.stack)))
            rec.check("comment PR dính", "1 comment, ghi BLOCKED", len(nb.stack.gh.comments) == 1 and "BLOCKED" in nb.stack.gh.comments[0]["body"])
            located = [f for f in report["findings"] if f["severity"] in ("critical", "medium") and f.get("path")]
            reviews = nb.stack.gh.reviews
            rec.check("PR review", "1 review liệt kê finding (ngoài diff nếu không có vị trí)", len(reviews) == 1 and "Critical" in reviews[0]["body"], str(reviews)[:300])
            if located:
                rec.check("inline comment", "có inline cho finding có vị trí", bool(reviews and reviews[0]["comments"]))
            else:
                rec.unverified("inline comment (plan: 'có inline comment')", "inline comment cho lỗi Critical của QC_BUGS=1",
                               "finding của schemathesis/pytest không có path/line nên review chỉ liệt kê chúng là 'ngoài diff' (inline comment Critical được kiểm ở N2 của node-api-harness)")
            rec.check("LLM", "đúng 1 lời gọi Select", nb.stack.llm.count == 1)
    return guarded(Recorder("C", "PR của dev có lỗi Critical (QC_BUGS=1)"), body)


# ───────────────────────── D: chỉ lỗi Low ─────────────────────────

def scenario_d(image: str) -> Recorder:
    def body(rec: Recorder) -> None:
        with _NoteboardPR(image, _add_untested_endpoint, "coverage-debt") as nb:
            res = nb.run(sut_env="QC_BUGS=none")
            _pipeline(rec, res, gate_exit="0")
            report = res.report
            debt = [f for f in report["findings"] if f["rule_id"] == "api_endpoint"]
            rec.check("verdict", "PASSED_WITH_WARNINGS, exit 0, chỉ Low", report["gate_verdict"] == "PASSED_WITH_WARNINGS" and report["severity_counts"]["critical"] == 0
                      and report["severity_counts"]["medium"] == 0 and report["severity_counts"]["low"] >= 1, str(report["severity_counts"]))
            rec.check("finding nợ test", "đúng 1: GET /notes/{note_id}/word-count (toyapp/app.py)", len(debt) == 1 and "GET /notes/{note_id}/word-count" in debt[0]["title"] and debt[0]["path"] == "toyapp/app.py",
                      str(debt))
            name_ok = kit.check_runs(nb.stack)
            low = report["severity_counts"]["low"]
            rec.check("Check Run", f"success, tiêu đề có số cảnh báo ({low})", len(name_ok) == 1 and name_ok[0][1] == "success" and f"{low} cảnh báo Low" in name_ok[0][2], str(name_ok))
            inline = [(c["path"], c["line"]) for r in nb.stack.gh.reviews for c in r.get("comments", [])]
            in_diff = bool(debt) and debt[0]["line"] in added_lines(_patch_of(nb.stack, "toyapp/app.py"))
            rec.check("inline comment", "đúng file và dòng của endpoint mới (dòng nằm trong diff)", bool(debt) and inline == [(debt[0]["path"], debt[0]["line"])] and in_diff, f"{inline} vs {debt}")
            jira_status = json.loads((res.run_dir / "jira-status.json").read_text(encoding="utf-8"))["jira"]
            rec.check("Jira (Low) không lỗi", "bước chạy, finding Low được xét, không làm hỏng gate", res.code("Jira (Low)") == 0 and jira_status.startswith("skipped") and nb.stack.jira.requests == [], jira_status)
            rec.unverified("ticket Jira (plan: 'đúng 1 ticket trong FakeJira')", "1 ticket tạo trong FakeJira",
                           "jira.py chỉ nhận JIRA_BASE_URL https hoặc http tới loopback; từ container, FakeJira ở máy chủ chỉ tới được qua http://host.docker.internal nên bị bỏ qua "
                           f"('{jira_status}'). Dedup/tạo ticket vẫn được kiểm ở tests/test_jira_sync.py; cần quyết định riêng để kiểm trong harness")
            llm_before = nb.stack.llm.count
            again = nb.run(sut_env="QC_BUGS=none")
            rec.check("chạy lại D: Select", "source=cache, 0 lời gọi LLM mới", again.selection["source"] == "cache" and nb.stack.llm.count == llm_before == 1,
                      f"{again.selection['source']} llm={nb.stack.llm.count}")
            rec.check("chạy lại D: comment PR", "0 comment mới (cập nhật tại chỗ)", len(nb.stack.gh.comments) == 1, str(len(nb.stack.gh.comments)))
            rec.check("chạy lại D: PR review", "0 review mới (cùng digest)", len(nb.stack.gh.reviews) == 1, str(len(nb.stack.gh.reviews)))
            rec.check("chạy lại D: Check Run", "thêm 1 Check Run cho lần chạy mới, vẫn success", len(nb.stack.gh.check_runs) == 2 and all(c["conclusion"] == "success" for c in nb.stack.gh.check_runs))
            rec.check("chạy lại D: ticket", "0 ticket mới", len(nb.stack.jira.issues) == 0 and nb.stack.jira.requests == [], "đúng nhưng không có ý nghĩa khi Jira bị bỏ qua (xem dòng CHƯA KIỂM CHỨNG)")
            rec.check("không cần QC_DATABASE_URL", "biến không đặt; Report bỏ qua ingest lịch sử mà luồng PR vẫn chạy tới Enforce",
                      "QC_DATABASE_URL" not in os.environ and '"ingest": "skipped' in res.log and res.code(REPORT_STEP) == 0)
    return guarded(Recorder("D", "PR của dev chỉ có lỗi Low, rồi chạy lại cùng SHA"), body)


# ───────────────────────── E: workflow_dispatch ─────────────────────────

def scenario_e(image: str) -> Recorder:
    def body(rec: Recorder) -> None:
        with _NoteboardPR(image, _bump_version) as nb:
            res = nb.run(sut_env="QC_BUGS=none", event_name="workflow_dispatch", dispatch_inputs={"workers": "semgrep"}, extra_inputs={"workers": "semgrep"})
            steps = ["Start SUT", "Run qc-agent gate", REPORT_STEP, "Clean up SUT", "Enforce gate result"]
            _pipeline(rec, res, gate_exit="0", steps=steps)
            rec.check("bước chỉ-PR bị bỏ qua", "không có Select (PR), PR review, Jira (Low)", not {"Select (PR)", "PR review", "Jira (Low)"} & set(res.steps))
            rec.check("suite chạy", "chỉ sast (worker semgrep)", kit.capabilities(res) == {"code.sast"} and res.selection["source"] == "manual" and res.selection["suites"] == ["sast"],
                      f"{kit.capabilities(res)} {res.selection}")
            rec.check("FakeAnthropic", "0 lời gọi", nb.stack.llm.count == 0, str(nb.stack.llm.count))
            rec.check("verdict", "PASSED, exit 0", res.report["gate_verdict"] == "PASSED" and res.report["exit_code"] == 0)
    return guarded(Recorder("E", "workflow_dispatch với workers=semgrep"), body)


# ───────────────────────── CLI ─────────────────────────

def run_all(image: str) -> list[Recorder]:
    recorders = []
    with kit.workspace_dir() as base_dir:                      # A sinh GT, B duyệt đúng GT đó
        recorders.append(scenario_a(image, base_dir))
        recorders.append(scenario_b(image, base_dir / "sut"))
    for scenario in (scenario_c, scenario_d, scenario_e):
        recorders.append(scenario(image))
    return recorders


def main() -> int:
    """`tools/run_reusable_locally.py --scenario full-chain`: in bảng A–E. Exit 0 nếu không có dòng FAIL (dòng CHƯA KIỂM CHỨNG vẫn hiện, không làm đỏ)."""
    try:
        verdict = kit.image_check.ensure_image(build=False)
    except kit.image_check.ImageCheckError as error:
        print(f"image: {error}", file=sys.stderr)
        return 1
    safe_print(verdict.label)
    try:
        recorders = run_all(verdict.image)
    except kit.harness.DockerNamesCheckError as error:    # tên sut/ui/db/qc-net bị việc khác giữ (hoặc Docker không trả lời): dừng, không xoá gì
        print(f"docker: {error}", file=sys.stderr)
        return 1
    for rec in recorders:
        safe_print(rec.table() + "\n")
    failed = sum(len(r.failures()) for r in recorders)
    open_items = sum(len(r.unverified_rows()) for r in recorders)
    safe_print(f"TỔNG: {sum(len(r.rows) for r in recorders)} dòng, {failed} FAIL, {open_items} CHƯA KIỂM CHỨNG")
    return 1 if failed else 0
