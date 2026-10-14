import importlib.util
from pathlib import Path

import pytest
import yaml

from qc_agent.scaffold import templates

spec = importlib.util.spec_from_file_location('app_helper', Path(__file__).parents[1] / 'tools/github_app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_token_scoped_to_installed_sut_and_reads_default_branch(monkeypatch):
    monkeypatch.setattr(app, 'app_jwt', lambda *args: 'jwt')
    calls = []
    def request(method, path, token, body=None):
        calls.append((method, path, token, body))
        if path.endswith('/installation'): return {'id': 123}
        if path.endswith('/access_tokens'): return {'token': 'test-token'}
        return {'default_branch': 'master'}
    assert app.installation_token('org/sut', '1', 'fake', request) == ('test-token', 'master')
    assert calls[1][3]['repositories'] == ['sut']
    assert calls[1][3]['permissions'] == {'contents': 'write', 'pull_requests': 'write', 'workflows': 'write'}


@pytest.mark.parametrize('repo', ['../sut', 'org/repo/extra', 'org/repo\n', 'org/..'])
def test_repository_input_rejected(repo):
    with pytest.raises(ValueError): app.repository(repo)


def test_existing_pr_preserves_qa_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'git', lambda *args: pytest.fail('must not change existing PR'))
    assert app.publish(tmp_path, 'org/sut', 'master', 'token',
                       lambda *args: [{'html_url': 'https://github.com/org/sut/pull/1'}])['status'] == 'existing'


def test_generated_workflows_target_master():
    gate = yaml.safe_load(templates.qc_workflow(project='sut', base_branch='master'))
    gt = yaml.safe_load(templates.groundtruth_workflow(project='sut', base_branch='master'))
    assert gate[True]['pull_request']['branches'] == ['master']
    assert gt[True]['push']['branches'] == ['master']
    assert gt['jobs']['groundtruth']['with']['base_branch'] == 'master'
    with pytest.raises(templates.TemplateError): templates.qc_workflow(project='sut', base_branch='../bad')


def test_private_ghcr_uses_pat_owner_instead_of_app_actor():
    for render in (templates.qc_workflow, templates.groundtruth_workflow):
        data = yaml.safe_load(render(project='sut', ghcr_username='MinhDuc-IT'))
        job = next(iter(data['jobs'].values()))
        assert job['with']['ghcr_username'] == 'MinhDuc-IT'
        with pytest.raises(templates.TemplateError): render(project='sut', ghcr_username='bad\nname')


def test_publish_only_generated_files_and_create_draft(tmp_path, monkeypatch):
    commands = []
    path = tmp_path / '.qc-agent/suites/api.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('suite', encoding='utf-8')
    def git(root, *args):
        commands.append(args)
        return '.qc-agent/suites/api.yaml' if args[0] == 'ls-files' else ''
    monkeypatch.setattr(app, 'git', git)
    posted = []
    def request(method, path, token, body=None):
        if method == 'GET': return []
        posted.append(body)
        return {'html_url': 'https://github.com/org/sut/pull/1'}
    assert app.publish(tmp_path, 'org/sut', 'master', 'token', request)['status'] == 'created'
    assert posted[0]['draft'] and posted[0]['base'] == 'master'
    assert ('push', 'origin', app.BRANCH) in commands
    assert not any('--force' in args for args in commands)


def test_unrelated_changes_never_pushed(tmp_path, monkeypatch):
    def git(root, *args): return 'backend/app.py' if args[0] == 'ls-files' else ''
    monkeypatch.setattr(app, 'git', git)
    with pytest.raises(ValueError, match='outside onboarding scope'):
        app.publish(tmp_path, 'org/sut', 'master', 'token', lambda *args: [])
