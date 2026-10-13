"""Gom finding để trình bày; không thay report gốc, severity policy hay verdict."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVEL = {"low": 0, "medium": 1, "critical": 2}
AGGREGATES = {"semgrep.high", "semgrep.critical", "semgrep.medium", "semgrep.low", "gitleaks.count",
              "trivy.high", "trivy.critical", "trivy.medium", "trivy.low", "debt.new"}


@dataclass
class Issue:
    finding: dict
    members: list[dict] = field(default_factory=list)

    @property
    def severity(self):
        return max((f.get("severity", "low") for f in self.members), key=lambda s: LEVEL.get(s, -1), default="low")


def _aggregate(f):
    rule, title = str(f.get("rule_id") or ""), str(f.get("title") or "")
    return (not f.get("path") and (rule in AGGREGATES or
            (f.get("worker") == "schemathesis" and title.startswith("check "))))


def _scope(f):
    return f.get("task_id"), f.get("worker")


def dependency(f):
    match = re.search(r"\bCVE-\d{4}-\d+\s+(\S+@\S+)\s+@", str(f.get("title") or ""))
    return match.group(1) if match else None


def group(findings):
    """Chỉ gộp ngưỡng có finding chi tiết cùng task/worker; giữ ngưỡng đứng riêng và lỗi hạ tầng."""
    issues, packages, aggregates = [], {}, []
    for f in findings:
        if _aggregate(f):
            aggregates.append(f)
            continue
        package = dependency(f) if f.get("worker") == "trivy" else None
        key = (_scope(f), f.get("path"), package) if package else None
        if key and key in packages:
            packages[key].members.append(f)
        else:
            issue = Issue(f, [f])
            issues.append(issue)
            if key:
                packages[key] = issue
    for f in aggregates:
        related = [i for i in issues if _scope(i.finding) == _scope(f) and not _aggregate(i.finding)]
        if f.get("worker") == "schemathesis":
            related = [i for i in related if i.finding.get("rule_id") == f.get("rule_id")]
        if related:
            related[0].members.append(f)
        else:
            issues.append(Issue(f, [f]))
    return sorted(issues, key=lambda i: -LEVEL.get(i.severity, -1))


def describe(f):
    """Thông điệp có quy tắc; trường không biết dùng lại title đã có, không đoán nguyên nhân."""
    worker, rule = f.get("worker"), str(f.get("rule_id") or "")
    location = str(f.get("path") or "")
    if location and isinstance(f.get("line"), int) and not isinstance(f.get("line"), bool):
        location += ':' + str(f['line'])
    title = str(f.get("title") or "Kiểm tra không đạt")
    why, fix = "Kiểm tra báo kết quả không đạt.", "Xem evidence của worker để xác định và sửa nguyên nhân, rồi chạy lại QC."
    if worker == "semgrep" and rule.endswith("python-eval-exec-dynamic"):
        title = "Sử dụng eval/exec với dữ liệu động"
        why = "Chuỗi đầu vào có thể được thực thi thành mã Python."
        fix = "Bỏ eval/exec; dùng parser phù hợp với dữ liệu cần xử lý."
    elif worker == "semgrep":
        why = "Mã nguồn khớp rule SAST đang được bật."
        fix = "Kiểm tra đoạn code được đánh dấu và sửa cách xử lý không an toàn theo evidence của rule."
    elif worker == "gitleaks" and rule != "gitleaks.count":
        title = "Phát hiện mẫu credential/token trong code"
        why = "Detector nhận diện chuỗi có định dạng credential; cần xác minh có phải secret thật."
        fix = "Xóa khỏi code và đọc từ secret/env. Nếu credential thật đã lộ, thu hồi hoặc thay key; nếu là fixture thử nghiệm, xóa sau khi test."
    elif worker == "trivy" and dependency(f):
        title = "Dependency có lỗ hổng: " + dependency(f)
        why = "DB của Trivy ghi nhận CVE cho phiên bản dependency này."
        fix = "Nâng lên phiên bản đã vá theo evidence Trivy, cập nhật lockfile và chạy lại kiểm tra."
    elif worker == "schemathesis":
        match = re.match(r"^((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+?):\s", title)
        if match:
            location = match.group(1)
        if rule == "not_a_server_error":
            title = "API trả lỗi server (HTTP 5xx)"
            why = "Request do bài kiểm tra tạo ra nhận lỗi server thay vì response hợp lệ."
            fix = "Kiểm tra exception và server log của endpoint, khôi phục xử lý lỗi phù hợp rồi chạy lại."
        elif rule == "response_schema_conformance":
            title = "Response API không khớp OpenAPI"
            why = "Status hoặc dữ liệu response không đáp ứng schema đã khai báo."
            fix = "Đối chiếu response trong evidence với OpenAPI, sửa implementation hoặc schema theo yêu cầu đã duyệt."
    elif worker == "coverage-debt" and rule != "debt.new":
        title = "Chức năng mới chưa tìm thấy test tương ứng"
        why = "Scanner chưa nhận diện được test bảo vệ thay đổi; đây là heuristic, cần kiểm tra lại."
        fix = "Bổ sung test cho hành vi mới hoặc xác nhận test hiện có đã bao phủ và cấu hình để scanner nhận diện."
    return title, location or "Kết quả kiểm tra " + str(worker or "worker"), why, fix


def render(issue, clean, *, detailed=True):
    title, where, why, fix = describe(issue.finding)
    lines = [f"**{clean(title, 200)}** ({clean(issue.severity, 20)})", f"Vị trí: {clean(where, 180)}."]
    if detailed:
        lines.append("Vì sao: " + clean(why, 350))
    lines.append("Cách xử lý: " + clean(fix, 500))
    return '\n\n'.join(lines)


def technical(issues, clean):
    lines = ['<details><summary>Chi tiết kỹ thuật và finding gốc</summary>', '']
    for issue in issues:
        for f in issue.members:
            lines.append('- ' + clean(f.get('worker') or 'worker', 40) + ': ' + clean(f.get('rule_id') or 'rule', 100)
                         + ' — ' + clean(f.get('title') or 'Kiểm tra không đạt', 220))
    lines += ['', 'Các finding gốc vẫn được giữ trong artifact; số finding không phải số lỗi độc lập.', '', '</details>']
    return '\n'.join(lines)
