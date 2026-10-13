"""Kiểm grouping và hướng xử lý, không làm đổi report hoặc che lỗi chỉ có oracle."""
import copy

from qc_agent.integrations import finding_view, github, pr_review


def f(worker, rule, title, path=None, severity='critical', **extra):
    return {'worker': worker, 'task_id': worker, 'rule_id': rule, 'title': title,
            'path': path, 'severity': severity, **extra}


def test_aggregate_is_only_hidden_with_related_detail_and_severity_preserved():
    findings = [f('semgrep', 'python-eval-exec-dynamic', 'eval', 'app.py', 'medium', line=4),
                f('semgrep', 'semgrep.high', 'semgrep.high = 1'),
                f('semgrep', 'semgrep.files_scanned', 'files_scanned = 0'),
                f('trivy', 'trivy.high', 'trivy.high = 1')]
    before = copy.deepcopy(findings)
    issues = finding_view.group(findings)
    assert len(issues) == 3
    assert issues[0].severity == 'critical'
    assert len(issues[0].members) == 2
    assert {x.finding['rule_id'] for x in issues} == {'python-eval-exec-dynamic', 'semgrep.files_scanned', 'trivy.high'}
    assert findings == before


def test_threshold_in_another_task_cannot_be_suppressed():
    issues = finding_view.group([f('gitleaks', 'github-pat', 'Token', 'app.py'),
                                 f('gitleaks', 'gitleaks.count', 'count', task_id='different')])
    assert len(issues) == 2


def test_cves_group_by_dependency_and_path_not_worker_alone():
    findings = [f('trivy', 'CVE-2026-1', 'CVE-2026-1 lib@1 @ a.lock', 'a.lock'),
                f('trivy', 'CVE-2026-2', 'CVE-2026-2 lib@1 @ a.lock', 'a.lock'),
                f('trivy', 'CVE-2026-3', 'CVE-2026-3 other@2 @ a.lock', 'a.lock'),
                f('trivy', 'CVE-2026-4', 'CVE-2026-4 lib@1 @ b.lock', 'b.lock')]
    issues = finding_view.group(findings)
    assert len(issues) == 3 and len(issues[0].members) == 2
    detail = finding_view.technical(issues, github.clean_md)
    for rule in ['CVE-2026-1', 'CVE-2026-2', 'CVE-2026-3', 'CVE-2026-4']:
        assert github.clean_md(rule, 100) in detail


def test_api_endpoint_is_location_and_summary_severity_is_retained():
    issues = finding_view.group([f('schemathesis', 'not_a_server_error', 'POST /login: not_a_server_error không đạt', severity='medium'),
                                 f('schemathesis', 'not_a_server_error', 'check not_a_server_error không đạt')])
    assert len(issues) == 1 and issues[0].severity == 'critical'
    body = finding_view.render(issues[0], github.clean_md)
    assert '`POST /login`' in body and 'HTTP 5xx' in body and '**Cách sửa:**' in body
    assert 'không có vị trí' not in body


def test_api_checks_with_different_rules_are_not_merged():
    assert len(finding_view.group([f('schemathesis', 'not_a_server_error', 'GET /a: error'),
                                  f('schemathesis', 'response_schema_conformance', 'check schema error')])) == 2


def test_ignore_guidance_is_secondary_to_fix_and_no_secret_is_assumed_valid():
    body = pr_review.inline_body(f('gitleaks', 'github-pat', 'token', 'app.py', line=1))
    assert body.index('**Cách sửa:**') < body.index('<details>') < body.index('.gitleaksignore')
    assert 'cần xác minh' in body and 'Nếu credential thật' in body


def test_grouped_inline_comment_is_not_duplicated_by_oracle():
    findings = [f('semgrep', 'python-eval-exec-dynamic', 'eval', 'app.py', 'medium', line=4),
                f('semgrep', 'semgrep.high', 'semgrep.high = 1')]
    comments, outside = pr_review.split_by_diff(findings, {'app.py': {4}})
    assert len(comments) == 1 and not outside
    assert '**Mức độ:** Critical' in comments[0]['body']


def test_review_keeps_marker_when_content_is_truncated():
    findings = [f('pytest', 'rule', 'x' * 1000, 'file.py', line=n) for n in range(600)]
    body = pr_review.render_body(findings, findings, 0, 'a'*64)
    assert len(body) <= github.MAX_COMMENT
    assert body.endswith(pr_review.MARKER.format(digest='a'*64))


def test_numbered_sections_and_bullet_hierarchy():
    item = f('semgrep', 'python-eval-exec-dynamic', 'eval', 'some_path.py', line=3)
    body = pr_review.render_body([item], [item], 0, 'a'*64)
    assert '\n---\n\n#### 1.' in body
    assert '- **Vị trí:** `some_path.py:3`' in body
    assert '- **Vì sao:**' in body and '- **Cách sửa:**' in body


def test_code_location_cannot_break_table_or_start_html():
    rendered = finding_view.code_location('x` | <img>\n@user #123')
    assert rendered.count('`') == 2 and '|' not in rendered and '<img>' not in rendered
    assert '@user' not in rendered and '#123' not in rendered
