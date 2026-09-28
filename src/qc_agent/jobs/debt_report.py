"""Từ report.json của MỘT run -> ghi nợ test vào sổ (`repository.apply_debt`). Dùng chung cho hai đường vào:
CI ingest (mode pr, thường là diff-scan) và executor (Mode 2, full-scan). Service KHÔNG biết tên worker: nhận diện task dò nợ bằng
hợp đồng D2 — metric `debt.full_scan` (bool) + `debt.new` và finding_id dạng `debt:<kind>:<surface>`.

Nguyên tắc bảo thủ (§1.1): chỉ tin task có status pass|fail. `error`/`skipped` không có quyền nói "hết nợ" (full-scan sẽ đóng nợ theo phần vắng mặt);
số nợ lệch danh sách findings (report bị cắt/hỏng) hoặc finding_id sai dạng => bỏ CẢ lần ghi này, không đoán. Lỗi ở đây không bao giờ
làm hỏng việc ghi nhận job/gate (§1.1); nợ của lần đó mất và full-scan kế tiếp bắt lại.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from qc_agent import logging_setup
from qc_agent.jobs import repository as repo
from qc_agent.jobs.models import Job

log = logging.getLogger("qc_agent.jobs")
DEBT_PREFIX = "debt:"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class DebtReportError(ValueError):
    """Report có task dò nợ nhưng không tin được nội dung."""


@dataclass(frozen=True)
class DebtScan:
    findings: frozenset  # {(kind, surface)}
    full_scan: bool      # chỉ True khi MỌI task dò nợ trong report là full-scan (lẫn lộn => coi là diff-scan: không đóng gì)


def debt_scan_from_report(report: dict) -> DebtScan | None:
    """None = report không có task dò nợ đáng tin nào (không có gì để ghi)."""
    details = report.get("details") if isinstance(report, dict) else None
    results = details.get("results") if isinstance(details, dict) else None
    if not isinstance(results, dict):
        return None
    scans = []
    for task_id, result in sorted(results.items()):
        metrics = result.get("metrics") if isinstance(result, dict) else None
        full = metrics.get("debt.full_scan") if isinstance(metrics, dict) else None
        if not isinstance(full, bool) or result.get("status") not in ("pass", "fail"):
            continue
        pairs = set()
        for finding in result.get("findings") or []:
            fid = finding.get("finding_id") if isinstance(finding, dict) else None
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
    return DebtScan(frozenset().union(*(p for p, _ in scans)), all(f for _, f in scans))


def github_pr_url(repo_full_name: str | None, pr_number: int | None) -> str | None:
    if repo_full_name and _REPO.fullmatch(repo_full_name) and isinstance(pr_number, int) and not isinstance(pr_number, bool) and pr_number > 0:
        return f"https://github.com/{repo_full_name}/pull/{pr_number}"
    return None


def apply_report_debt(session: Session, slug: str, job_id, report: dict) -> repo.DebtDelta | None:
    """Trích nợ từ report rồi `apply_debt`. None = không có gì để ghi. Raise DebtReportError nếu report không tin được."""
    scan = debt_scan_from_report(report)
    if scan is None or (not scan.full_scan and not scan.findings):
        return None  # diff-scan không thấy gì: không có khoản nào để mở, và diff-scan không được đóng
    project, job = repo.get_project(session, slug), session.get(Job, job_id)
    return repo.apply_debt(session, slug, job_id=job_id, findings=scan.findings, full_scan=scan.full_scan,
                           pr_url=github_pr_url(project.repo, job.pr_number if job else None))


def try_apply_report_debt(session: Session, slug: str, job_id, report) -> repo.DebtDelta | None:
    """Như `apply_report_debt` nhưng KHÔNG BAO GIỜ ném lỗi ra ngoài: chạy trong savepoint (lỗi chỉ huỷ phần ghi nợ, job vẫn được lưu),
    ghi một dòng log có cấu trúc — chỉ đếm, không có nội dung surface."""
    try:
        with session.begin_nested():
            delta = apply_report_debt(session, slug, job_id, report if isinstance(report, dict) else {})
    except Exception as error:  # noqa: BLE001 — ghi nhận nợ là phụ; gate và lịch sử job không được phụ thuộc vào nó
        logging_setup.event(log, "debt.apply_failed", logging.ERROR, project=slug, job_id=str(job_id), error_type=type(error).__name__)
        return None
    if delta is not None:
        logging_setup.event(log, "debt.applied", project=slug, job_id=str(job_id), opened=len(delta.opened),
                            refreshed=len(delta.refreshed), closed=len(delta.closed))
    return delta
