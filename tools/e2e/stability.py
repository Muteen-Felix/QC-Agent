"""10 lượt C+D liên tiếp: sinh script cho NGƯỜI chạy (mỗi lượt hỏi xác nhận, kiểm ngân sách giữa các lượt), ghi kết quả từng lượt, tổng hợp tỉ lệ xanh.

Định nghĩa "một lượt xanh" (GIẢ ĐỊNH cần bạn xác nhận, plan chỉ nói "trọn chuỗi xanh"): C đúng kỳ vọng (PR đỏ vì Critical) VÀ D đúng kỳ vọng (PR xanh, inline comment, ticket)
trên bằng chứng nguồn `github`. Lượt có dòng PENDING hoặc nguồn không phải github không được tính là xanh. DoD cần 10 lượt liên tiếp (1..10, không thiếu số) và ≥ 9 xanh.
Phân loại lượt đỏ theo `cause` của dòng FAIL: product > llm > infra (chỉ để chẩn đoán; lượt đỏ nào cũng tính vào tỉ lệ).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.e2e import collect, scenarios

SCRIPT = r'''#!/usr/bin/env bash
# Sinh bởi `python -m tools.e2e stability script`. DO NGƯỜI CHẠY. Chưa được chạy lần nào trên GitHub thật.
# Cần: bash, git, gh đăng nhập bằng tài khoản DEV (tác giả PR), Python của repo qc-agent (đã `uv sync`), biến JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN (chỉ để ĐỌC ticket).
# Mỗi lượt: tạo nhánh C và D, đẩy, mở PR, chờ check, thu bằng chứng (chỉ đọc), đóng PR, rồi kiểm ngân sách. Dừng ngay khi ngân sách báo STOP.
set -uo pipefail
REPO="@@REPO@@"; SANDBOX="@@SANDBOX@@"; EVID="@@EVID@@"; PROJECT="@@PROJECT@@"; JIRA_KEY="@@JIRA_KEY@@"
CAP="@@CAP@@"; ITERATIONS=@@ITERATIONS@@; DEV="@@DEV@@"; RESULTS="$EVID/stability-results.json"
cd "@@ROOT@@" || exit 3
confirm() { if [ "${E2E_ASSUME_YES:-}" = 1 ]; then return 0; fi; read -r -p "$1 [y/N] " a; [ "$a" = y ]; }

open_pr() {   # $1 = C|D, $2 = tag, $3 = thư mục bằng chứng của lượt
  local sc="$1" tag="$2" iter="$3"
  local low; low=$(printf '%s' "$sc" | tr 'A-Z' 'a-z')
  local branch="qc-e2e/${low}-${tag}" out="$iter/$sc"
  mkdir -p "$out"
  python -m tools.e2e prepare "$sc" --repo "$SANDBOX" --tag "$tag" || return 1
  git -C "$SANDBOX" push -u origin "$branch" || return 1
  local url; url=$(gh pr create --repo "$REPO" --base main --head "$branch" --title "test(e2e): $sc $tag" --body "E2E S4-06, lượt $tag") || return 1
  local pr="${url##*/}"
  gh pr checks "$pr" --repo "$REPO" --watch || true   # C đỏ là KỲ VỌNG: không dừng theo mã thoát
  local sha; sha=$(git -C "$SANDBOX" rev-parse "$branch")
  local run; run=$(gh run list --repo "$REPO" --branch "$branch" --limit 1 --json databaseId --jq '.[0].databaseId')
@@READS@@
  gh pr close "$pr" --repo "$REPO" --delete-branch || true
}

for i in $(seq 1 "$ITERATIONS"); do
  n=$(printf '%02d' "$i"); tag="s$n"; iter="$EVID/iter-$n"
  confirm "Lượt $i/$ITERATIONS: mở PR C và D trên $REPO (gọi LLM thật qua Select, tác động GitHub/Jira)?" || { echo "dừng theo yêu cầu"; exit 2; }
  mkdir -p "$iter"
  python -m tools.e2e meta "$iter" --repo "$REPO" --source github || exit 3
  open_pr C "$tag" "$iter" || { echo "lượt $i: lỗi thao tác ở C (xem output trên)"; exit 3; }
  open_pr D "$tag" "$iter" || { echo "lượt $i: lỗi thao tác ở D (xem output trên)"; exit 3; }
  python -m tools.e2e collect "$iter" --scenario C --scenario D --project "$PROJECT" --dev "$DEV" --record "$RESULTS" --iteration "$i" || true
  python -m tools.e2e ledger "$EVID" --cap "$CAP" || { echo "NGÂN SÁCH: DỪNG sau lượt $i"; exit 4; }
done
python -m tools.e2e stability summary "$RESULTS"
'''


def emit_script(*, repo: str, sandbox: str, evidence: str, project: str, jira_key: str, cap: float, iterations: int, dev: str) -> str:
    """Thân script bash. Lệnh đọc dùng đúng `scenarios.read_commands` (một nguồn duy nhất với collect.py)."""
    reads = []
    for sc in ("C", "D"):
        rows = scenarios.read_commands(sc, repo=repo, project=project, key=jira_key, evidence="$iter", branch="$branch", pr="$pr", sha="$sha", run="$run")
        reads.append(f'  if [ "$sc" = {sc} ]; then')
        reads += [f"    {cmd}" for _, cmd in rows]
        reads.append("  fi")
    text = SCRIPT
    for key, value in {"REPO": repo, "SANDBOX": sandbox, "EVID": evidence, "PROJECT": project, "JIRA_KEY": jira_key, "CAP": f"{cap:g}", "ITERATIONS": str(iterations),
                       "DEV": dev, "ROOT": str(Path(__file__).resolve().parents[2]).replace("\\", "/"), "READS": "\n".join(reads)}.items():
        text = text.replace(f"@@{key}@@", value)
    return text


def record(results: Path, iteration: int, report: collect.Report, *, force: bool = False) -> dict:
    """Ghi kết quả một lượt (C+D). Không ghi đè lượt đã có (chống sửa kết quả sau khi thấy đỏ) trừ khi `force`."""
    data = json.loads(results.read_text(encoding="utf-8")) if results.is_file() else {"iterations": {}}
    key = str(iteration)
    if key in data["iterations"] and not force:
        raise ValueError(f"lượt {iteration} đã được ghi; dùng --force nếu thật sự muốn ghi đè (và nói rõ lý do trong báo cáo)")
    rows = [r for r in report.rows if r.scenario in ("C", "D")]
    fails, pend = [r for r in rows if r.status == collect.FAIL], [r for r in rows if r.status == collect.PENDING]
    if fails:
        status = collect.FAIL
    elif pend or not rows or not report.github_evidence:
        status = collect.PENDING
    else:
        status = collect.PASS
    causes = sorted({r.cause for r in fails if r.cause})
    data["iterations"][key] = {"status": status, "causes": causes, "failed_checks": [f"{r.scenario}.{r.check}" for r in fails],
                               "pending_checks": [f"{r.scenario}.{r.check}" for r in pend], "source": report.source,
                               "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return data["iterations"][key]


def _dominant(causes: list[str]) -> str:
    for cause in ("product", "llm", "infra"):
        if cause in causes:
            return cause
    return "không rõ"


def summarize(results: Path, *, required: int = 10, min_pass: int = 9) -> dict:
    data = json.loads(results.read_text(encoding="utf-8")) if results.is_file() else {"iterations": {}}
    items = {int(k): v for k, v in data["iterations"].items()}
    numbers = sorted(items)
    contiguous = numbers == list(range(1, len(numbers) + 1))
    count = {key: sum(1 for v in items.values() if v["status"] == key) for key in (collect.PASS, collect.FAIL, collect.PENDING)}
    failures = {n: {"cause": _dominant(v["causes"]), "checks": v["failed_checks"]} for n, v in sorted(items.items()) if v["status"] == collect.FAIL}
    by_cause: dict[str, int] = {}
    for item in failures.values():
        by_cause[item["cause"]] = by_cause.get(item["cause"], 0) + 1
    reasons = []
    if len(items) < required:
        reasons.append(f"mới có {len(items)}/{required} lượt")
    if not contiguous:
        reasons.append("các lượt không liên tục từ 1 (thiếu số)")
    if count[collect.PENDING]:
        reasons.append(f"{count[collect.PENDING]} lượt PENDING (thiếu bằng chứng hoặc nguồn không phải github)")
    if len(items) >= required and contiguous and not count[collect.PENDING]:
        verdict = "ĐỦ ĐIỀU KIỆN để phiên dod-verify xem xét" if count[collect.PASS] >= min_pass else "CHƯA ĐẠT"
    elif count[collect.FAIL] > required - min_pass:
        verdict = "CHƯA ĐẠT"   # đã đỏ quá ngưỡng, các lượt sau không cứu được
        reasons.append(f"đã {count[collect.FAIL]} lượt đỏ > {required - min_pass} cho phép")
    else:
        verdict = "CHƯA KẾT LUẬN"
    return {"recorded": len(items), "required": required, "min_pass": min_pass, "contiguous": contiguous, "counts": count,
            "green_rate": (count[collect.PASS] / len(items)) if items else None, "failures": failures, "failures_by_cause": by_cause, "verdict": verdict, "reasons": reasons,
            "note": "Công cụ không tick DoD."}
