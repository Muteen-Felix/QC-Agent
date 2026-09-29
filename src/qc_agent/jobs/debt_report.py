"""Từ report.json của MỘT run -> ghi nợ test vào sổ (`repository.apply_debt`). Dùng chung cho hai đường vào:
CI ingest (mode pr, thường là diff-scan) và executor (Mode 2, full-scan). Service KHÔNG biết tên worker: nhận diện task dò nợ bằng
hợp đồng D2 — metric `debt.full_scan` (bool) + `debt.new` và finding_id dạng `debt:<kind>:<surface>`.

Việc đọc report (thuần) nằm ở qc_agent.debt; file này chỉ nối nó với sổ nợ. Report không tin được => bỏ CẢ lần ghi này, không đoán. Lỗi ở đây không bao giờ
làm hỏng việc ghi nhận job/gate (§1.1); nợ của lần đó mất và full-scan kế tiếp bắt lại.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from qc_agent import logging_setup
from qc_agent.debt import DEBT_PREFIX, DebtReportError, DebtScan, debt_scan_from_report  # noqa: F401  (re-export)
from qc_agent.jobs import repository as repo
from qc_agent.jobs.models import Job

log = logging.getLogger("qc_agent.jobs")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


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
