"""Đối chiếu bằng chứng đã LƯU (JSON/text do người chạy các lệnh `gh`/`curl` đọc-chỉ ở scenarios.py) với bảng kỳ vọng. Chỉ đọc file cục bộ.

Quy tắc trạng thái (không có đường nào cho "xanh giả"):
  PASS     file bằng chứng có, đọc được, và khớp kỳ vọng
  FAIL     file có nhưng KHÔNG khớp (kèm `cause`: infra | llm | product, dùng cho phân loại ở stability)
  PENDING  thiếu file/thiếu thông tin để kết luận: không bao giờ được tính là PASS
Nguồn bằng chứng nằm ở `<evidence>/meta.json` (`source`: github | local | fake). Chỉ `github` mới có thể là bằng chứng DoD, và công cụ KHÔNG tự tick DoD.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tools.e2e import scenarios
from tools.e2e.common import NOT_DOD, PROVENANCE

PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"
LLM_FALLBACKS = {"timeout", "unavailable", "bad_output", "missing_api_key", "egress_denied", "token_cap"}
_PUSH_REJECTED = re.compile(r"GH006|protected branch|remote rejected|Protected branch update failed", re.I)


@dataclass
class Row:
    scenario: str
    check: str
    expected: str
    status: str
    detail: str = ""
    cause: str | None = None   # chỉ khi FAIL: infra | llm | product


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    source: str | None = None
    meta: dict | None = None

    def counts(self) -> dict:
        return {key: sum(1 for row in self.rows if row.status == key) for key in (PASS, FAIL, PENDING)}

    @property
    def github_evidence(self) -> bool:
        return self.source == "github"

    def render(self) -> str:
        head = ["# Kết quả đối chiếu bằng chứng S4-06", ""]
        if not self.github_evidence:
            head += [f"> **{NOT_DOD}** (nguồn khai báo: {self.source or 'không có meta.json'}).", ""]
        head += ["| KB | Kiểm | Kỳ vọng | Kết quả | Chi tiết |", "|---|---|---|---|---|"]
        body = [f"| {r.scenario} | {r.check} | {r.expected} | {r.status}{f' ({r.cause})' if r.cause else ''} | {r.detail} |" for r in self.rows]
        count = self.counts()
        tail = ["", f"Tổng: {count[PASS]} PASS · {count[FAIL]} FAIL · {count[PENDING]} PENDING.",
                "Công cụ này KHÔNG tick DoD và không tuyên bố S4-06 đạt: việc đó thuộc phiên `dod-verify`, và chỉ khi mọi dòng PASS với nguồn `github`."]
        return "\n".join(head + body + tail) + "\n"


# ───────────────────────── đọc file ─────────────────────────

class _Missing(Exception):
    pass


def _json(path: Path):
    if not path.is_file():
        raise _Missing(f"thiếu {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        raise _Missing(f"{path.name} không phải JSON hợp lệ") from None


def _text(path: Path) -> str:
    if not path.is_file():
        raise _Missing(f"thiếu {path.name}")
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _artifact(root: Path) -> dict:
    """Tìm các file artifact `runs/`: selection.json, llm_usage.json, report.json của lần chạy cuối (r-*)."""
    base = root / "artifact"
    if not base.is_dir():
        raise _Missing("thiếu thư mục artifact/")
    runs = sorted({p.parent for p in base.rglob("report.json")}, key=lambda p: p.name)
    if not runs:
        raise _Missing("artifact/ không có report.json")
    run_dir = runs[-1]
    selection = next(iter(sorted(base.rglob("selection.json"), key=lambda p: len(p.parts))), None)
    usage = run_dir / "llm_usage.json"
    return {"run_dir": run_dir, "report": _json(run_dir / "report.json"), "selection": _json(selection) if selection else None,
            "usage": json.loads(usage.read_text(encoding="utf-8")) if usage.is_file() else []}


def _check_run(checks, project: str) -> dict:
    runs = checks.get("check_runs", []) if isinstance(checks, dict) else checks
    found = [c for c in runs if isinstance(c, dict) and c.get("name") == f"qc-agent / {project}"]
    if len(found) != 1:
        raise _Missing(f"cần đúng 1 Check Run 'qc-agent / {project}', thấy {len(found)}")
    return found[0]


def _guard(rows: list[Row], scenario: str, check: str, expected: str, body) -> None:
    """Chạy một kiểm; thiếu dữ liệu => PENDING; kiểm trả (ok, chi tiết, cause)."""
    try:
        ok, detail, cause = body()
        rows.append(Row(scenario, check, expected, PASS if ok else FAIL, detail, None if ok else cause))
    except _Missing as error:
        rows.append(Row(scenario, check, expected, PENDING, str(error)))
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as error:
        rows.append(Row(scenario, check, expected, FAIL, f"dữ liệu sai hình dạng: {type(error).__name__}", "infra"))


def _marker_comments(comments, project: str, mode: str = "pr") -> list:
    mark = f"<!-- qc-agent:{re.sub(r'[^A-Za-z0-9_.-]', '_', project)}:{mode} -->"
    return [c for c in comments if isinstance(c, dict) and mark in str(c.get("body", ""))]


def _select_ok(art: dict) -> tuple[bool, str, str | None]:
    sel = art["selection"]
    if sel is None:
        raise _Missing("thiếu selection.json")
    if not sel.get("floor"):
        return False, "selection.json không có floor", "product"
    if sel.get("fallback_reason") in LLM_FALLBACKS:
        return False, f"Select lùi về FULL SET ({sel['fallback_reason']}): đường LLM không được kiểm", "llm"
    if sel.get("source") not in ("llm", "cache", "rules", "fallback"):
        return False, f"source lạ {sel.get('source')!r}", "product"
    rows = [r for r in art["usage"] if r.get("purpose") == "diff-select"]
    if sel.get("source") in ("llm", "cache") and not rows:
        return False, "source=llm|cache nhưng llm_usage.json không có dòng diff-select", "product"
    priced = [r for r in rows if isinstance(r.get("est_usd"), (int, float)) or r.get("cache_hit")]
    if rows and len(priced) != len(rows):
        return False, "có dòng diff-select chưa rõ chi phí", "llm"
    return True, f"source={sel['source']}, floor={sel['floor']}, {len(rows)} dòng diff-select", None


# ───────────────────────── từng kịch bản ─────────────────────────

def _a(root: Path, rows: list[Row], ctx: dict) -> None:
    exp = {e.check: e.text for e in scenarios.EXPECT["A"]}

    def pr_opened():
        pr = _json(root / "pr.json")
        pr = pr[0] if isinstance(pr, list) and pr else pr
        paths = [f.get("path", "") for f in pr.get("files", [])]
        ok = pr.get("state") in ("OPEN", "MERGED") and str(pr.get("headRefName", "")).startswith("qc-agent/gt/") and any(p.startswith(".qc-agent/ground-truth/") for p in paths)
        return ok, f"nhánh {pr.get('headRefName')!r}, {len(paths)} file", "product"

    def workflow_ok():
        runs = _json(root / "runs.json")
        ok = any(r.get("conclusion") == "success" for r in runs)
        return ok, f"{len(runs)} run, kết luận: {[r.get('conclusion') for r in runs]}", "infra"

    def one_llm_call():
        usage = root / "artifact"
        files = sorted(usage.rglob("llm_usage.json")) if usage.is_dir() else []
        if not files:
            raise _Missing("artifact/ không có llm_usage.json")
        calls = [r for f in files for r in json.loads(f.read_text(encoding="utf-8")) if not r.get("cache_hit")]
        ok = len(calls) == 1 and isinstance(calls[0].get("est_usd"), (int, float))
        return ok, f"{len(calls)} lời gọi trả phí", "llm"
    _guard(rows, "A", "pr_opened", exp["pr_opened"], pr_opened)
    _guard(rows, "A", "workflow_ok", exp["workflow_ok"], workflow_ok)
    _guard(rows, "A", "one_llm_call", exp["one_llm_call"], one_llm_call)


def _b(root: Path, rows: list[Row], ctx: dict) -> None:
    exp = {e.check: e.text for e in scenarios.EXPECT["B"]}

    def protection_on():
        data = _json(root / "protection.json")
        reviews = data.get("required_pull_request_reviews")
        ok = isinstance(reviews, dict) and reviews.get("require_code_owner_reviews") is True and int(reviews.get("required_approving_review_count") or 0) >= 1
        return ok, "require_code_owner_reviews=" + str(reviews.get("require_code_owner_reviews") if isinstance(reviews, dict) else None), "product"

    def push_rejected():
        text = _text(root / "push-attempt.txt")
        if not _PUSH_REJECTED.search(text):
            return False, "output không có dấu hiệu bị từ chối (GH006/protected branch/remote rejected)", "product"
        if not re.search(r"\b20\d\d\b", text.splitlines()[0] if text.splitlines() else ""):
            raise _Missing("bị từ chối nhưng dòng đầu không có thời điểm (chạy lại với `date -u;` trước lệnh push)")
        return True, "bị từ chối; thời điểm: " + text.splitlines()[0].strip()[:40], None

    def merge_blocked():
        data = _json(root / "merge-attempt.json")
        paths = [f.get("path", "") for f in data.get("files", [])]
        if not any(p.startswith(".qc-agent/") for p in paths):
            return False, "PR thử merge không đụng .qc-agent/", "product"
        blocked = data.get("state") == "OPEN" and (data.get("reviewDecision") in ("REVIEW_REQUIRED", "CHANGES_REQUESTED") or data.get("mergeStateStatus") == "BLOCKED")
        who = (data.get("author") or {}).get("login")
        if ctx.get("non_qa") and who != ctx["non_qa"]:
            return False, f"tác giả PR là {who!r}, không phải {ctx['non_qa']!r}", "product"
        return blocked, f"state={data.get('state')} reviewDecision={data.get('reviewDecision')} mergeState={data.get('mergeStateStatus')}", "product"

    def qa_merge_ok():
        data = _json(root / "qa-merge.json")
        approved = any(r.get("state") == "APPROVED" for r in data.get("reviews", []))
        ok = data.get("state") == "MERGED" and approved and bool((data.get("mergedBy") or {}).get("login"))
        return ok, f"state={data.get('state')} approved={approved}", "product"
    _guard(rows, "B", "protection_on", exp["protection_on"], protection_on)
    _guard(rows, "B", "push_rejected", exp["push_rejected"], push_rejected)
    _guard(rows, "B", "merge_blocked", exp["merge_blocked"], merge_blocked)
    _guard(rows, "B", "qa_merge_ok", exp["qa_merge_ok"], qa_merge_ok)


def _gate_pr(scenario: str, root: Path, rows: list[Row], ctx: dict, *, conclusion: str, title_re: str) -> dict | None:
    """Phần chung C/D: Check Run + report.json + selection. Trả artifact (hoặc None nếu thiếu)."""
    project = ctx["project"]
    exp = {e.check: e.text for e in scenarios.EXPECT[scenario]}
    check_name = "check_failure" if scenario == "C" else "check_success"

    def check():
        run = _check_run(_json(root / "checks.json"), project)
        title = str((run.get("output") or {}).get("title", ""))
        m = re.search(title_re, title)
        ok = run.get("conclusion") == conclusion and bool(m) and (scenario == "D" or int(m.group(1)) >= 1)
        return ok, f"conclusion={run.get('conclusion')} title={title!r}", "product"

    def report():
        art = _artifact(root)
        rep = art["report"]
        sc = rep.get("severity_counts") or {}
        if scenario == "C":
            ok = rep.get("gate_verdict") == "BLOCKED" and rep.get("exit_code") == 1 and sc.get("critical", 0) >= 1
        else:
            ok = rep.get("gate_verdict") == "PASSED_WITH_WARNINGS" and rep.get("exit_code") == 0 and sc.get("critical", 0) == 0 and sc.get("medium", 0) == 0 and sc.get("low", 0) >= 1
        return ok, f"{rep.get('gate_verdict')} exit={rep.get('exit_code')} {sc}", "product"
    _guard(rows, scenario, check_name, exp[check_name], check)
    _guard(rows, scenario, "report_blocked" if scenario == "C" else "report_warn", exp["report_blocked" if scenario == "C" else "report_warn"], report)
    try:
        art = _artifact(root)
    except _Missing:
        art = None
    if scenario == "C":
        _guard(rows, "C", "selection_llm", exp["selection_llm"], lambda: _select_ok(_artifact(root)))
    return art


def _c(root: Path, rows: list[Row], ctx: dict) -> None:
    exp = {e.check: e.text for e in scenarios.EXPECT["C"]}
    project = ctx["project"]
    _gate_pr("C", root, rows, ctx, conclusion="failure", title_re=r"BLOCKED · (\d+) critical")

    def comment():
        mine = _marker_comments(_json(root / "comments.json"), project)
        return len(mine) == 1 and "BLOCKED" in mine[0].get("body", ""), f"{len(mine)} comment dính", "product"

    def cost_line():
        run = _check_run(_json(root / "checks.json"), project)
        summary = str((run.get("output") or {}).get("summary", ""))
        return "LLM:" in summary and "~$" in summary, "dòng chi phí " + ("có" if "~$" in summary else "KHÔNG có"), "product"
    _guard(rows, "C", "comment_sticky", exp["comment_sticky"], comment)
    _guard(rows, "C", "cost_line", exp["cost_line"], cost_line)


def _jira_matches(path: Path, fingerprints: set[str]) -> list:
    issues = _json(path).get("issues", [])
    wanted = {f"qcagent-{f}" for f in fingerprints}
    return [i for i in issues if wanted & set((i.get("fields") or {}).get("labels", []))]


def _d(root: Path, rows: list[Row], ctx: dict) -> None:
    exp = {e.check: e.text for e in scenarios.EXPECT["D"]}
    project = ctx["project"]
    art = _gate_pr("D", root, rows, ctx, conclusion="success", title_re=r"(\d+) cảnh báo Low")
    low = [f for f in (art or {}).get("report", {}).get("findings", []) if f.get("severity") == "low"]

    def inline():
        if art is None:
            raise _Missing("thiếu artifact/ để biết finding Low nằm ở đâu")
        comments = _json(root / "review-comments.json")
        got = {(c.get("path"), c.get("line") or c.get("original_line")) for c in comments if isinstance(c, dict)}
        want = {(f.get("path"), f.get("line")) for f in low if f.get("path") and f.get("line")}
        return bool(want & got), f"inline {sorted(got, key=str)[:3]} vs finding {sorted(want, key=str)[:3]}", "product"

    def jira():
        if art is None:
            raise _Missing("thiếu artifact/ để biết fingerprint của finding Low")
        prints = {f["fingerprint"] for f in low if f.get("fingerprint")}
        found = _jira_matches(root / "jira.json", prints)
        if len(found) != len(prints):
            return False, f"{len(found)} ticket cho {len(prints)} finding Low", "product"
        dev, user_map = ctx.get("dev"), ctx.get("user_map") or {}
        if not dev or dev not in user_map:
            raise _Missing(f"có {len(found)} ticket nhưng thiếu --dev/--user-map để kiểm người nhận")
        assignees = {((i.get("fields") or {}).get("assignee") or {}).get("accountId") for i in found}
        return assignees == {user_map[dev]}, f"{len(found)} ticket, người nhận khớp: {assignees == {user_map[dev]}}", "product"

    def rerun():
        rr = root / "rerun"
        if not rr.is_dir():
            raise _Missing("thiếu thư mục rerun/ (chạy lại cùng commit rồi thu lại)")
        second = _artifact(rr)
        sel = second["selection"] or {}
        used = [r for r in second["usage"] if r.get("purpose") == "diff-select"]
        cache_ok = sel.get("source") == "cache" and all(r.get("cache_hit") for r in used)
        before = len(_marker_comments(_json(root / "comments.json"), project))
        after = len(_marker_comments(_json(rr / "comments.json"), project))
        reviews_same = len(_json(root / "reviews.json")) == len(_json(rr / "reviews.json"))
        prints = {f["fingerprint"] for f in low if f.get("fingerprint")}
        tickets_same = len(_jira_matches(root / "jira.json", prints)) == len(_jira_matches(rr / "jira.json", prints))
        ok = cache_ok and before == after == 1 and reviews_same and tickets_same
        return ok, f"source={sel.get('source')} cache_hit={cache_ok} comment {before}→{after} review_giữ_nguyên={reviews_same} ticket_giữ_nguyên={tickets_same}", "product"
    _guard(rows, "D", "inline_comment", exp["inline_comment"], inline)
    _guard(rows, "D", "jira_ticket", exp["jira_ticket"], jira)
    _guard(rows, "D", "rerun_cache", exp["rerun_cache"], rerun)


def _e(root: Path, rows: list[Row], ctx: dict) -> None:
    exp = {e.check: e.text for e in scenarios.EXPECT["E"]}
    wanted = {w for w in str(ctx.get("workers") or "semgrep").split(",") if w}

    def dispatch():
        job = _json(root / "job.json")
        return job.get("event") == "workflow_dispatch" and job.get("conclusion") == "success", f"event={job.get('event')} conclusion={job.get('conclusion')}", "infra"

    def exact():
        art = _artifact(root)
        sel = art["selection"]
        if sel is None:
            raise _Missing("thiếu selection.json")
        no_llm = not [r for r in art["usage"] if r.get("purpose") == "diff-select"]
        ok = sel.get("source") == "manual" and sel.get("trigger_type") == "manual" and set(sel.get("workers", [])) == wanted and not sel.get("full_set") and no_llm
        return ok, f"source={sel.get('source')} workers={sel.get('workers')} suites={sel.get('suites')} diff-select={not no_llm}", "product"
    _guard(rows, "E", "dispatch", exp["dispatch"], dispatch)
    _guard(rows, "E", "exact_workers", exp["exact_workers"], exact)


_EVALUATORS = {"A": _a, "B": _b, "C": _c, "D": _d, "E": _e}


# ───────────────────────── meta và điểm vào ─────────────────────────

def write_meta(evidence: Path, *, repo: str, source: str, image: str | None = None, qc_ref: str | None = None) -> dict:
    if source not in PROVENANCE:
        raise ValueError(f"source phải thuộc {PROVENANCE}")
    meta = {"repo": repo, "source": source, "image": image, "qc_ref": qc_ref, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    Path(evidence).mkdir(parents=True, exist_ok=True)
    (Path(evidence) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return meta


def _provenance_row(evidence: Path, meta: dict | None) -> Row:
    expected = "meta.json khai nguồn và dữ liệu khớp nguồn đó"
    if meta is None:
        return Row("*", "provenance", expected, PENDING, "thiếu meta.json: chạy `python -m tools.e2e meta`")
    if meta.get("source") != "github":
        return Row("*", "provenance", expected, PENDING, f"nguồn {meta.get('source')}: {NOT_DOD}")
    # nguồn khai là github: Check Run thật có app.slug và html_url ở github.com; fake thì không
    for path in sorted(Path(evidence).glob("*/checks.json")):
        try:
            runs = _json(path).get("check_runs", [])
        except _Missing:
            continue
        real = [r for r in runs if (r.get("app") or {}).get("slug") == "github-actions" and str(r.get("html_url", "")).startswith("https://github.com/")]
        if not real:
            return Row("*", "provenance", expected, FAIL, f"{path.parent.name}/checks.json khai github nhưng không có Check Run nào có app.slug=github-actions và html_url github.com", "infra")
    return Row("*", "provenance", expected, PASS, "khai github; dữ liệu Check Run không mâu thuẫn")


def evaluate(evidence: Path, scenario_ids: list[str] | None = None, *, project: str = "noteboard", dev: str | None = None, user_map: dict | None = None,
             non_qa: str | None = None, workers: str = "semgrep") -> Report:
    evidence = Path(evidence)
    meta_path = evidence / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
    report = Report(source=(meta or {}).get("source"), meta=meta)
    ctx = {"project": project, "dev": dev, "user_map": user_map, "non_qa": non_qa, "workers": workers}
    for sid in scenario_ids or list(scenarios.SCENARIOS):
        _EVALUATORS[sid](evidence / sid, report.rows, ctx)
    report.rows.append(_provenance_row(evidence, meta))
    return report
