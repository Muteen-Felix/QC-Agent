"""Năm kịch bản A–E của S4-06: phần cục bộ (tạo nhánh/commit trong bản sao sandbox) và danh sách lệnh để NGƯỜI chạy.

Công cụ không bao giờ chạy `git push`, `gh` hay `curl`: mọi lệnh ra ngoài được IN (loại `external`/`llm`). Tên file bằng chứng ở `READ` là hợp đồng với `collect.py`.

PR của C và D chỉ được đụng `toyapp/**`: `.github/workflows/**` và `.qc-agent/**` là đường FULL SET theo policy (0 lời gọi LLM), nên PR đụng vào đó không đo được Select.
Dockerfile của noteboard đặt `ENV QC_BUGS=none` nên base sạch (D xanh). Lỗi Critical của C là một dòng mã `BUGS.add("1")` thêm vào toyapp/app.py (BUG-1: id dài => 500, Schemathesis bắt),
đúng kiểu "dev đưa lại hồi quy"; không đụng Dockerfile hay workflow. Select (LLM thật) có thể không chọn schemathesis: khi đó C xanh là một phát hiện về recall, không phải lỗi của công cụ này.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from tools.e2e.common import EXTERNAL, LLM, OFFLINE, ROOT, Step, git

APP = Path("toyapp") / "app.py"
PRD_SRC = ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md"
BUGS_LINE = 'BUGS = {b.strip() for b in (os.environ.get("QC_BUGS", "").strip() or "1,2,3").split(",") if b.strip()}\n'
REGRESSION = 'BUGS.add("1")  # hồi quy: bật lại BUG-1\n'
NEW_ENDPOINT = ('@app.get("/notes/{note_id}/word-count", responses={404: {"description": "not found"}})\n'
                'def word_count(note_id: str):\n    return {"words": len(_get(note_id)["body"].split())}\n\n\n')
DELETE_MARKER = '@app.delete("/notes/{note_id}"'

SCENARIOS = {
    "A": ("BA commit PRD → workflow qc-groundtruth mở PR sinh GT", True),
    "B": ("QA duyệt rồi merge; tài khoản không phải QA bị chặn khi đụng .qc-agent/", False),
    "C": ("Dev mở PR có lỗi Critical → PR đỏ", True),
    "D": ("Dev mở PR chỉ có lỗi Low → PR xanh, inline comment, ticket Jira; chạy lại → cache hit, 0 ticket mới", True),
    "E": ("workflow_dispatch với danh sách worker → chạy đúng các worker đó", False),
}

# Tên file bằng chứng đọc-chỉ (relative với <evidence>/<kịch bản>/). `{repo}` `{pr}` `{sha}` `{run}` `{branch}` `{project}` điền khi in.
READ = {
    "pr.json": "gh pr view {pr} --repo {repo} --json number,state,title,author,headRefName,mergeStateStatus,reviewDecision,statusCheckRollup,files",
    "checks.json": "gh api repos/{repo}/commits/{sha}/check-runs",
    "comments.json": "gh api repos/{repo}/issues/{pr}/comments --paginate",
    "reviews.json": "gh api repos/{repo}/pulls/{pr}/reviews --paginate",
    "review-comments.json": "gh api repos/{repo}/pulls/{pr}/comments --paginate",
    "runs.json": "gh run list --repo {repo} --branch {branch} --limit 5 --json databaseId,event,conclusion,headSha,createdAt,updatedAt,workflowName",
    "job.json": "gh run view {run} --repo {repo} --json jobs,conclusion,event,createdAt,updatedAt",
}
ARTIFACT = "gh run download {run} --repo {repo} --name qc-runs-{project}-1 --dir {out}/artifact"
JIRA = ('curl -sS -u "$JIRA_EMAIL:$JIRA_API_TOKEN" -H "Content-Type: application/json" -X POST "$JIRA_BASE_URL/rest/api/3/search/jql" '
        '-d \'{{"jql":"project = {key} AND labels is not EMPTY ORDER BY created DESC","fields":["summary","labels","assignee"],"maxResults":50}}\' > {out}/jira.json')


@dataclass(frozen=True)
class Expect:
    check: str
    text: str
    evidence: str


EXPECT = {
    "A": [Expect("pr_opened", "có PR đang mở từ nhánh qc-agent/gt/<prd-id> vào main, sửa .qc-agent/ground-truth/", "A/pr.json"),
          Expect("workflow_ok", "workflow qc-groundtruth kết luận success", "A/runs.json"),
          Expect("one_llm_call", "đúng 1 lời gọi sinh GT, có dòng chi phí trong llm_usage.json, 0 nội dung PRD trong artifact egress", "A/artifact/")],
    "B": [Expect("protection_on", "branch protection main: require_code_owner_reviews=true và bắt buộc qua PR", "B/protection.json"),
          Expect("push_rejected", "tài khoản không phải QA push thẳng vào main bị từ chối (protected branch)", "B/push-attempt.txt"),
          Expect("merge_blocked", "PR đụng .qc-agent/ của tài khoản không phải QA không merge được khi thiếu QA approve", "B/merge-attempt.json"),
          Expect("qa_merge_ok", "PR sinh GT được QA duyệt rồi merge", "B/qa-merge.json")],
    "C": [Expect("check_failure", "Check Run `qc-agent / <project>` kết luận failure, tiêu đề BLOCKED, ≥1 critical", "C/checks.json"),
          Expect("report_blocked", "report.json: BLOCKED, exit_code 1, critical ≥ 1", "C/artifact/"),
          Expect("selection_llm", "selection.json có floor; nguồn llm|cache|rules|fallback; llm_usage.json có dòng diff-select kèm chi phí", "C/artifact/"),
          Expect("comment_sticky", "đúng 1 comment dính ghi BLOCKED", "C/comments.json"),
          Expect("cost_line", "Check Run/comment có dòng chi phí `~$`", "C/checks.json")],
    "D": [Expect("check_success", "Check Run success, tiêu đề `N cảnh báo Low`", "D/checks.json"),
          Expect("report_warn", "report.json: PASSED_WITH_WARNINGS, exit 0, 0 critical/medium, ≥1 low", "D/artifact/"),
          Expect("inline_comment", "≥1 inline comment đúng path/line của finding Low", "D/review-comments.json"),
          Expect("jira_ticket", "đúng 1 ticket Jira, nhãn qcagent-<fingerprint>, gán cho accountId của tác giả PR", "D/jira.json"),
          Expect("rerun_cache", "chạy lại cùng commit: selection.source=cache, dòng llm_usage cache_hit=true, 0 comment/ticket mới", "D/rerun/")],
    "E": [Expect("dispatch", "run có event workflow_dispatch, kết luận success", "E/job.json"),
          Expect("exact_workers", "selection.json: source=manual và suites khớp đúng worker đã yêu cầu; 0 dòng diff-select trong llm_usage", "E/artifact/")],
}


def _mutate(scenario: str, repo: Path) -> None:
    path = repo / APP
    text = path.read_text(encoding="utf-8")
    if scenario == "C":
        if text.count(BUGS_LINE) != 1:
            raise ValueError(f"{APP}: không thấy đúng một lần dòng khai báo BUGS; toy app đã đổi, sửa tools/e2e/scenarios.py")
        path.write_text(text.replace(BUGS_LINE, BUGS_LINE + REGRESSION), encoding="utf-8", newline="\n")
    elif scenario == "D":
        if text.count(DELETE_MARKER) != 1:
            raise ValueError(f"{APP}: không thấy đúng một lần {DELETE_MARKER!r}")
        path.write_text(text.replace(DELETE_MARKER, NEW_ENDPOINT + DELETE_MARKER), encoding="utf-8", newline="\n")
    else:
        raise ValueError(f"kịch bản {scenario} không có đột biến mã (chỉ C, D)")


def add_prd(repo: Path) -> str:
    """Kịch bản A: chép PRD mẫu vào sandbox và commit trên `main`, cục bộ. PRD KHÔNG nằm trong commit base: workflow qc-groundtruth chạy khi `docs/prd/**` được đẩy lên `main`, nên
    nếu PRD đi cùng lần đẩy đầu tiên thì nó chạy trước khi secret và cấu hình Actions sẵn sàng."""
    target = repo / "docs" / "prd" / PRD_SRC.name
    if target.exists():
        raise RuntimeError("PRD đã có trong sandbox: kịch bản A đã được chuẩn bị")
    if git(repo, "rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise RuntimeError("kịch bản A commit trên main: chuyển sandbox về main trước")
    if git(repo, "status", "--porcelain"):
        raise RuntimeError("cây làm việc của sandbox đang bẩn: commit hoặc bỏ thay đổi trước")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PRD_SRC, target)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs(prd): thêm PRD mẫu noteboard (kịch bản A)")
    return git(repo, "rev-parse", "HEAD")


def prepare(scenario: str, repo: Path, tag: str, *, base: str = "main") -> tuple[str, str]:
    """A: commit PRD trên `main`. C/D: tạo nhánh cục bộ `qc-e2e/<kịch bản>-<tag>` từ `base`, áp đột biến, commit. Trả (nhánh, sha). Không đẩy đi đâu."""
    if scenario == "A":
        return "main", add_prd(repo)
    if scenario not in ("C", "D"):
        raise ValueError("chỉ A, C và D có commit cục bộ; B/E là thao tác trên GitHub")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", tag):
        raise ValueError("tag chỉ gồm chữ, số, . _ - (tối đa 40)")
    if git(repo, "status", "--porcelain"):
        raise RuntimeError("cây làm việc của sandbox đang bẩn: commit hoặc bỏ thay đổi trước")
    branch = f"qc-e2e/{scenario.lower()}-{tag}"
    if git(repo, "branch", "--list", branch):
        raise RuntimeError(f"nhánh {branch} đã tồn tại: dùng --tag khác")
    original = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    git(repo, "switch", "-c", branch, base)
    try:
        _mutate(scenario, repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"test(e2e): kịch bản {scenario} ({tag})")
        sha = git(repo, "rev-parse", "HEAD")
    except Exception:
        git(repo, "reset", "-q", "--hard")   # cây đã được kiểm sạch ở trên nên chỉ bỏ phần đột biến vừa áp
        git(repo, "switch", original)
        git(repo, "branch", "-D", branch)
        raise
    git(repo, "switch", original)   # sandbox luôn quay về nhánh cũ (main) để lượt sau tách từ base sạch
    return branch, sha


def read_commands(scenario: str, *, repo: str, project: str, key: str, evidence: str, branch: str = "<BRANCH>", pr: str = "<PR>", sha: str = "<HEAD_SHA>",
                  run: str = "<RUN_ID>") -> list[tuple[str, str]]:
    """(file, lệnh đọc-chỉ) cho kịch bản, theo hợp đồng `READ`."""
    out_dir = f"{evidence}/{scenario}"
    fields = dict(repo=repo, project=project, branch=branch, pr=pr, sha=sha, run=run, out=out_dir, key=key)
    wanted = {"A": ["pr.json", "runs.json"], "C": list(READ), "D": list(READ), "E": ["runs.json", "job.json"], "B": []}[scenario]
    rows = [(name, f"{READ[name].format(**fields)} > {out_dir}/{name}") for name in wanted]
    if scenario == "A":
        rows.append(("artifact/", f"gh run download {run} --repo {repo} --dir {out_dir}/artifact   # artifact qc-groundtruth-<prd-id>-1"))
    if scenario in ("C", "D", "E"):
        rows.append(("artifact/", ARTIFACT.format(**fields)))
    if scenario == "D":
        rows.append(("jira.json", JIRA.format(**fields)))
    if scenario == "B":
        rows += [("protection.json", f"gh api repos/{repo}/branches/main/protection > {out_dir}/protection.json"),
                 ("qa-merge.json", f"gh pr view <GT_PR> --repo {repo} --json number,state,mergedAt,mergedBy,reviewDecision,reviews > {out_dir}/qa-merge.json")]
    return rows


def steps(scenario: str, *, repo: str, project: str, key: str, evidence: str, branch: str = "<BRANCH>", dev: str = "<dev>", non_qa: str = "<non-qa>",
          workers: str = "semgrep") -> list[Step]:
    """Bước cho một kịch bản, theo đúng thứ tự thao tác. Mọi lệnh `gh`/`git push`/`curl` chỉ được IN."""
    out_dir = f"{evidence}/{scenario}"
    reads = [Step(EXTERNAL, f"Thu bằng chứng kịch bản {scenario} (chỉ đọc; thay <...> bằng giá trị thật)",
                  command="\n".join(cmd for _, cmd in read_commands(scenario, repo=repo, project=project, key=key, evidence=evidence, branch=branch)))]
    meta = Step(OFFLINE, "Ghi meta của bằng chứng (nguồn = github) và đối chiếu", command=f"python -m tools.e2e meta {evidence} --repo {repo} --source github\npython -m tools.e2e collect {evidence} --scenario {scenario}",
                note="thiếu file nào thì dòng tương ứng là PENDING, không bao giờ PASS")
    if scenario == "A":
        return [Step(OFFLINE, "Chép PRD mẫu vào sandbox và commit cục bộ trên main (PRD không nằm trong commit base)", command="python -m tools.e2e prepare A --repo <sandbox-dir> --tag prd"),
                Step(LLM, "Đẩy commit PRD lên main → workflow qc-groundtruth tự chạy (đồng thời tác động GitHub)", needs=("llm.egress_question3 = confirmed", "ngân sách đã duyệt", "secret ANTHROPIC_API_KEY", "branch protection CHƯA bật"),
                     command="git -C <sandbox-dir> push origin main",
                     note="Settings > Actions > General > 'Allow GitHub Actions to create and approve pull requests' phải bật; PR mở bằng GITHUB_TOKEN không kích hoạt validate (cần qc_bot_token hoặc QA push commit)"),
                *reads, meta]
    if scenario == "B":
        return [Step(EXTERNAL, "Bật branch protection (dry-run trước)", needs=("repo public hoặc gói trả phí", "GITHUB_TOKEN quyền admin repo", "xác nhận của bạn cho từng lệnh"),
                     command=f"python tools/protect_ground_truth.py --repo {repo} --dry-run   # vẫn GET lên GitHub (chỉ đọc)\npython tools/protect_ground_truth.py --repo {repo}   # PUT thật"),
                Step(EXTERNAL, "QA (người thật) review PR sinh GT, đổi draft → approved, điền module-map, approve và merge", command=f"gh pr list --repo {repo} --search 'head:qc-agent/gt/' --state open"),
                Step(EXTERNAL, f"Trong một bản clone RIÊNG đã đăng nhập bằng {non_qa} (không thuộc team QA): thử push thẳng vào main khi đụng .qc-agent/, lưu NGUYÊN VĂN output",
                     needs=(f"{non_qa} có quyền write nhưng KHÔNG phải admin của repo",),
                     note="nếu tài khoản không có quyền write thì bị từ chối vì thiếu quyền chứ không phải vì CODEOWNERS; admin thì không bị chặn khi enforce_admins=false: cả hai trường hợp bằng chứng đều vô nghĩa",
                     command=f"git switch -c qc-e2e/b-nonqa main && echo '# thử' >> .qc-agent/NOTE.md && git add -A && git commit -qm 'test(e2e): B non-QA' \\\n  && (date -u; git push origin HEAD:main 2>&1) | tee {out_dir}/push-attempt.txt"),
                Step(EXTERNAL, f"{non_qa} đẩy nhánh, mở PR đụng .qc-agent/ rồi thử merge (kỳ vọng bị từ chối)",
                     command=f"git push -u origin qc-e2e/b-nonqa && gh pr create --repo {repo} --base main --head qc-e2e/b-nonqa --title 'test(e2e): B non-QA' --body 'thử chặn'\n"
                             f"gh pr view <PR> --repo {repo} --json number,state,mergeStateStatus,reviewDecision,mergeable,author,files > {out_dir}/merge-attempt.json\n(date -u; gh pr merge <PR> --repo {repo} --merge 2>&1) | tee {out_dir}/merge-attempt.txt"),
                *reads, meta]
    if scenario in ("C", "D"):
        rerun = [Step(EXTERNAL, "Chạy lại job trên CÙNG commit (đo cache hit) rồi thu lại vào thư mục rerun/",
                      note="lần chạy lại là attempt 2: artifact tên qc-runs-<project>-2 (đổi --name khi tải); comments/reviews/jira thu lại đúng như lần đầu",
                      command=f"gh run rerun <RUN_ID> --repo {repo}   # rồi lặp các lệnh thu bằng chứng, đổi đầu ra sang {out_dir}/rerun/")] if scenario == "D" else []
        return [Step(OFFLINE, f"Tạo nhánh cục bộ kịch bản {scenario} trong bản sao sandbox", command=f"python -m tools.e2e prepare {scenario} --repo <sandbox-dir> --tag <tag>"),
                Step(LLM, f"Đăng nhập {dev}: đẩy nhánh và mở PR (Select gọi LLM thật; đồng thời tác động GitHub/Jira)", needs=("llm.egress_question3 = confirmed", "ngân sách đã duyệt", "secret ANTHROPIC_API_KEY"),
                     command=f"git -C <sandbox-dir> push -u origin {branch}\ngh pr create --repo {repo} --base main --head {branch} --title 'test(e2e): {scenario}' --body 'E2E S4-06'\ngh pr checks <PR> --repo {repo} --watch"),
                *reads, *rerun, meta]
    return [Step(EXTERNAL, "Kích hoạt tay với danh sách worker", needs=("xác nhận của bạn",), command=f"gh workflow run qc-gate.yml --repo {repo} --ref main -f workers={workers}"),
            *reads, meta]
