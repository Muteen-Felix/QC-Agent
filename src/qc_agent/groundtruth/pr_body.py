"""Thân PR "Ground-Truth" từ `--summary-json` của `gt generate|regen` (S1-07): số lượng, orphan, warning, và checklist cho QA.

    python -m qc_agent.groundtruth.pr_body SUMMARY.json > body.md      # workflow qc-groundtruth chạy bằng image qc-agent

Tất cả chuỗi lấy từ summary (ID, đường dẫn, warning) đều đi qua `clean_md`: summary suy ra từ PRD/catalog nên coi là dữ liệu không tin cậy.
Không import LLM/mạng: đây chỉ là bước định dạng.
"""
from __future__ import annotations

import json
import sys
import re
from pathlib import Path

from qc_agent.integrations.github import clean_md

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}")   # ID, tên model, phiên bản prompt: không có ký tự nào của Markdown/mention

MAX_LIST = 25
MAX_BODY = 60000


def _id(value) -> str:
    """ID đã qua regex thì giữ nguyên cho dễ đọc (clean_md sẽ thoát `-` và `_`); chuỗi lạ thì làm sạch."""
    return value if isinstance(value, str) and _ID.fullmatch(value) else clean_md(value, 80)


def _ids(values, limit: int = MAX_LIST) -> str:
    values = [_id(v) for v in values]
    return ", ".join(values[:limit]) + (f" … (+{len(values) - limit})" if len(values) > limit else "")


_DIM_LABEL = {"ac": "AC", "technique": "Technique (ràng buộc OpenAPI)", "api": "API (operation × mã trạng thái)"}
_SHORT_LABEL = {"ac": "AC", "technique": "technique", "api": "API"}


def _coverage_lines(coverage) -> list[str]:
    """Bảng điểm của bộ chấm tất định (coverage.py). Summary cũ không có khoá này nên bỏ qua. Chỉ số và id; gap suy ra từ OpenAPI nên đi qua `clean_md`."""
    if not isinstance(coverage, dict) or not coverage:
        return []
    lines = ["### Coverage (bộ chấm tất định, tính cả TC draft)", "", "| Chiều | Phủ | Miễn | Tổng | Tỉ lệ |", "|---|---|---|---|---|"]
    for name in ("ac", "technique", "api"):
        dim = coverage.get(name)
        if dim:
            lines.append(f"| {_DIM_LABEL[name]} | {int(dim['covered'])} | {int(dim['waived'])} | {int(dim['total'])} | {float(dim['ratio']):.0%} |")
    for name in ("ac", "technique", "api"):
        dim = coverage.get(name)
        if dim and dim.get("gaps"):
            shown = dim["gaps"][:MAX_LIST]
            lines += ["", f"Còn thiếu về {_SHORT_LABEL[name]}: {int(dim['gaps_total'])} mục"]
            lines += [f"- ⚠️ {clean_md(gap, 200)}" for gap in shown]
            if int(dim["gaps_total"]) > len(shown):
                lines.append(f"- … và {int(dim['gaps_total']) - len(shown)} mục nữa")
    if "technique" not in coverage:
        lines += ["", "_Chưa chấm technique/API: lần sinh này không có `--openapi`._"]
    return lines + [""]


def _agent_lines(agent) -> list[str]:
    """Dòng 'Bộ sinh: agent …'. Chỉ số và cờ (int/float/bool): không có đường dẫn hay nội dung."""
    if not isinstance(agent, dict):
        return []
    cost = f" · ~${float(agent['cost_usd_est']):.2f}" if agent.get("cost_usd_est") is not None else ""
    lines = [f"Bộ sinh: **agent** · {int(agent['turns'])} lượt · đọc {int(agent['files_read'])} file · nộp {int(agent['submissions'])} lần · "
             f"{int(agent['dropped_in_loop'])} TC bị bỏ khi nộp{cost}", ""]
    if not agent.get("completed"):
        lines += [f"⚠️ Agent **chưa hoàn tất** (dừng: `{_id(agent.get('error_kind') or agent.get('stop') or '?')}`): bộ này còn gap coverage, xem bảng dưới.", ""]
    return lines


def render(summary: dict) -> str:
    if not isinstance(summary, dict) or summary.get("command") not in ("generate", "regen"):
        raise ValueError("summary không phải kết quả của `gt generate|regen`")
    status = summary["by_status"]
    regen = summary["command"] == "regen"
    lines = [
        f"## Ground-Truth cho `{_id(summary['prd_id'])}`: {'cập nhật theo PRD mới' if regen else 'bản nháp do LLM đề xuất'}",
        "",
        f"PRD sha256 `{_id(summary['prd_sha256'])}` · model `{_id(summary['model'])}` · prompt `{_id(summary['prompt_version'])}`",
        "",
        *_agent_lines(summary.get("agent")),
        "| | |",
        "|---|---|",
        f"| Story / AC | {int(summary['stories'])} / {int(summary['acs'])} |",
        f"| Test case | {int(summary['test_cases'])} (draft {int(status['draft'])}, approved {int(status['approved'])}, rejected {int(status['rejected'])}) |",
        f"| TC bị bỏ do vi phạm | {int(summary['dropped_test_cases'])} |",
        f"| AC không kiểm được bằng HTTP (`uncovered_acs`) | {len(summary['uncovered_acs'])} |",
        f"| AC mồ côi (không có TC, không nằm trong `uncovered_acs`) | {len(summary['orphans'])} |",
        "",
    ]
    lines += _coverage_lines(summary.get("coverage"))
    if regen:
        merge = summary.get("merge") or {}
        lines += ["### Merge với bản cũ", "",
                  f"Giữ nguyên **{int(merge.get('kept', 0))}** TC (approved / rejected / `origin: qa`), thêm **{len(merge.get('added', []))}** TC draft mới, "
                  f"thay **{len(merge.get('removed_drafts', []))}** TC draft cũ.", ""]
        for tc_id, refs in sorted((merge.get("lost_acs") or {}).items()):
            lines.append(f"- ⚠️ `{_id(tc_id)}` trỏ tới AC không còn trong PRD ({_ids(refs)}): **không sửa TC**; gate sẽ báo lỗi cho tới khi QA sửa `ac_refs` hoặc chuyển `rejected`.")
        if merge.get("lost_acs"):
            lines.append("")
    lines += [
        "### Việc của QA (bắt buộc trước khi merge)",
        "",
        "- [ ] Duyệt từng test case trong `.qc-agent/ground-truth/test-cases.yaml`: `draft` → `approved`, hoặc `rejected` kèm `rejected_reason`.",
        "- [ ] Thêm edge case còn thiếu (`origin: qa`, `status: approved`).",
    ]
    if summary["orphans"]:
        lines.append(f"- [ ] AC mồ côi cần thêm TC hoặc ghi vào `uncovered_acs`: {_ids(summary['orphans'])}")
    if summary["uncovered_acs"]:
        lines.append(f"- [ ] Xác nhận các AC này thật sự không kiểm được bằng HTTP: {_ids(summary['uncovered_acs'])}")
    agent = summary.get("agent") if isinstance(summary.get("agent"), dict) else {}
    if int(agent.get("waivers") or 0):
        lines.append(f"- [ ] Duyệt **{int(agent['waivers'])}** waiver agent đề xuất trong `waivers:` (đồng ý lý do thì đổi `status: approved`; chưa approved thì KHÔNG được tính vào coverage).")
    if int(agent.get("spec_conflicts") or 0):
        lines.append(f"- [ ] Quyết **{int(agent['spec_conflicts'])}** xung đột spec trong `spec_conflicts:` (mã nguồn khác PRD/OpenAPI): PRD sai hay SUT sai? Xong thì đổi `status: resolved`.")
    lines += [
        "- [ ] `module-map.yaml`: điền `paths` của từng module, xoá dấu `qc-agent:todo`, đổi `status: approved`.",
        "- [ ] Đổi `status` của catalog thành `approved` (khi đó mọi TC phải là `approved` hoặc `rejected`).",
        "- [ ] Check `gt validate` xanh (chạy khi có commit mới trên PR; xem `docs/groundtruth.md`).",
        "",
        "Không sửa tay các file trong `tests_gt/`: chúng được render lại từ `test-cases.yaml` và `gt validate` báo drift nếu khác.",
    ]
    if summary["warnings"]:
        lines += ["", "<details><summary>Cảnh báo khi sinh</summary>", ""]
        lines += [f"- {clean_md(w, 300)}" for w in summary["warnings"][:MAX_LIST]]
        if len(summary["warnings"]) > MAX_LIST:
            lines.append(f"- … và {len(summary['warnings']) - MAX_LIST} cảnh báo nữa")
        lines += ["", "</details>"]
    text = "\n".join(lines) + "\n"
    return text if len(text) <= MAX_BODY else text[: MAX_BODY - 20] + "\n… (cắt bớt)\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("cách dùng: python -m qc_agent.groundtruth.pr_body SUMMARY.json", file=sys.stderr)
        return 3
    try:
        summary = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        text = render(summary)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"LỖI: không dựng được thân PR ({type(error).__name__})", file=sys.stderr)
        return 3
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
