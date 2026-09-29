"""Đọc "nợ test" từ report.json của MỘT run (hợp đồng D2), thuần túy: không DB, không I/O. Dùng chung cho service ghi sổ (jobs/debt_report)
và bước báo cáo trên PR (integrations/github). Không biết tên worker: nhận diện task dò nợ bằng metric `debt.full_scan` (bool) + `debt.new`
và finding_id dạng `debt:<kind>:<surface>`.

Nguyên tắc bảo thủ (§1.1): chỉ tin task có status pass|fail (`error`/`skipped` không có quyền nói "hết nợ"); số nợ lệch danh sách findings
(report bị cắt/hỏng) hoặc finding_id sai dạng => DebtReportError, người gọi bỏ cả lần này thay vì đoán."""
from __future__ import annotations

from dataclasses import dataclass, field

DEBT_PREFIX = "debt:"


class DebtReportError(ValueError):
    """Report có task dò nợ nhưng không tin được nội dung."""


@dataclass(frozen=True)
class DebtScan:
    findings: frozenset  # {(kind, surface)}
    full_scan: bool      # chỉ True khi MỌI task dò nợ trong report là full-scan (lẫn lộn => coi là diff-scan: không đóng gì)
    titles: frozenset = field(default_factory=frozenset)  # tiêu đề MỌI finding của các task dò nợ (kể cả của oracle): để khỏi liệt kê hai lần


def debt_scan_from_report(report: dict) -> DebtScan | None:
    """None = report không có task dò nợ đáng tin nào (không có gì để ghi/hiển thị)."""
    details = report.get("details") if isinstance(report, dict) else None
    results = details.get("results") if isinstance(details, dict) else None
    if not isinstance(results, dict):
        return None
    scans, titles = [], set()
    for task_id, result in sorted(results.items()):
        metrics = result.get("metrics") if isinstance(result, dict) else None
        full = metrics.get("debt.full_scan") if isinstance(metrics, dict) else None
        if not isinstance(full, bool) or result.get("status") not in ("pass", "fail"):
            continue
        pairs = set()
        for finding in result.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            if isinstance(finding.get("title"), str):
                titles.add(finding["title"])
            fid = finding.get("finding_id")
            if not isinstance(fid, str) or not fid.startswith(DEBT_PREFIX):
                continue  # finding khác (vd. f-thr-debt.new của oracle) không phải một khoản nợ
            _, kind, surface = (fid.split(":", 2) + ["", ""])[:3]
            if not kind or not surface:
                raise DebtReportError(f"task {task_id}: finding_id không đúng dạng debt:<kind>:<surface>")
            pairs.add((kind, surface))
        new = metrics.get("debt.new")
        if isinstance(new, bool) or not isinstance(new, int) or new != len(pairs):
            raise DebtReportError(f"task {task_id}: debt.new={new!r} nhưng report có {len(pairs)} khoản nợ")
        scans.append((pairs, full))
    if not scans:
        return None
    return DebtScan(frozenset().union(*(p for p, _ in scans)), all(f for _, f in scans), frozenset(titles))
