import hashlib
import hmac
import json
import io
import tarfile

import pytest
import yaml
from fastapi.testclient import TestClient

from qc_agent.scaffold import templates
from tools import github_app as auth, github_app_service as service


def test_token_scoped_to_sut_and_rejects_stale_install(monkeypatch):
    monkeypatch.setattr(auth, 'app_jwt', lambda *args: 'jwt')
    calls = []
    def request(method, path, token, body=None):
        calls.append((method, path, body))
        if path.endswith('/installation'): return {'id': 123}
        if path.endswith('/access_tokens'): return {'token': 'fake-token'}
        return {'default_branch': 'master'}
    assert auth.installation_token('org/sut', '1', 'fake', request, 123) == ('fake-token', 'master')
    assert calls[1][2]['repositories'] == ['sut']
    assert 'actions' not in calls[1][2]['permissions']
    with pytest.raises(ValueError, match='Installation changed'):
        auth.installation_token('org/sut', '1', 'fake', request, 456)


def test_queue_deduplicates_and_retries(tmp_path):
    q = service.Queue(tmp_path / 'queue.sqlite')
    q.enqueue('d1', ['org/sut'], 123)
    q.enqueue('d1', ['org/sut'], 123)
    def fail(*args): raise RuntimeError('fake-token must not be logged')
    assert q.run_one(fail)
    with q.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
        assert db.execute('SELECT status FROM jobs').fetchone()[0] == 'queued'
        db.execute('UPDATE jobs SET due=0')
    seen = []
    assert q.run_one(lambda *args: seen.append(args))
    assert seen == [('org/sut', 123)]
    assert not q.run_one(fail)


def config(tmp_path):
    return {'QC_APP_ID': '1', 'QC_APP_PRIVATE_KEY': 'fake', 'QC_WEBHOOK_SECRET': 'fake-secret',
            'QC_WORKFLOW_REPOSITORY': 'org/qc', 'QC_WORKFLOW_REF': 'a'*40,
            'QC_IMAGE': 'ghcr.io/org/qc@sha256:'+'b'*64, 'QC_ONBOARD_DB': str(tmp_path/'queue.sqlite')}


def test_signed_install_queues_only_sut_and_rejects_tampering(tmp_path):
    app = service.create_app(config(tmp_path), handler=lambda *args: None)
    client = TestClient(app)
    body = json.dumps({'action': 'created', 'installation': {'id':123}, 'repositories':[{'full_name':'org/sut'}]}).encode()
    headers = {'x-github-event':'installation', 'x-github-delivery':'01234567-0123-0123-0123-012345678901',
               'x-hub-signature-256':'sha256='+hmac.new(b'fake-secret',body,hashlib.sha256).hexdigest()}
    assert client.post('/webhook', content=body, headers=headers).status_code == 200
    assert client.post('/webhook', content=body, headers=headers).status_code == 200
    assert client.post('/webhook', content=body+b' ', headers=headers).status_code == 401
    with app.state.queue.connect() as db:
        assert db.execute('SELECT repo FROM jobs').fetchall() == [('org/sut',)]


def test_direct_onboarding_creates_draft_pr_no_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'installation_token', lambda *a, **k: ('fake-token','master'))
    calls = []
    def request(method, path, token, body=None):
        calls.append((method, path, body))
        if '/pulls?' in path or 'matching-refs' in path: return []
        if method == 'GET': return {'tree': {'sha':'base-tree'}}
        if path.endswith('/pulls'): return {'html_url':'https://github.com/org/sut/pull/1'}
        return {'sha':'new-object'}
    def download(repo, token, root):
        (root/'Dockerfile').write_text('FROM python:3.11\nEXPOSE 8000\n')
        (root/'main.py').write_text('from fastapi import FastAPI\napp=FastAPI()\n@app.get("/health")\ndef health(): return {"ok":True}\n')
        return 'c'*40
    result = service.onboard('org/sut',123,config(tmp_path),request,download)
    assert result['status'] == 'created'
    posted = [body for method,path,body in calls if method=='POST' and path.endswith('/pulls')][0]
    assert posted['draft'] and posted['base']=='master'
    assert not any('/actions/' in path for _,path,_ in calls)
    assert calls[-1][:2] == ('DELETE','/installation/token')


def test_existing_pr_preserves_qa_edits_and_revokes_token(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'installation_token',lambda *a, **k: ('fake-token','master'))
    calls=[]
    def request(method,path,token,body=None):
        calls.append(method)
        return [{'html_url':'https://github.com/org/sut/pull/1'}] if method=='GET' else None
    def never(*args): pytest.fail('must not download or modify existing PR')
    assert service.onboard('org/sut',123,config(tmp_path),request,never)['status']=='existing'
    assert calls==['GET','DELETE']


def test_templates_master_and_private_ghcr_owner():
    for render in (templates.qc_workflow, templates.groundtruth_workflow):
        d=yaml.safe_load(render(project='sut',base_branch='master',ghcr_username='MinhDuc-IT'))
        assert next(iter(d['jobs'].values()))['with']['ghcr_username']=='MinhDuc-IT'
    assert yaml.safe_load(templates.qc_workflow(project='sut',base_branch='master'))[True]['pull_request']['branches']==['master']
    assert yaml.safe_load(templates.groundtruth_workflow(project='sut',base_branch='master'))['jobs']['groundtruth']['with']['base_branch']=='master'


@pytest.mark.parametrize('name,link', [('../escape',False),('/absolute',False),('.git/config',False),('link',True)])
def test_snapshot_rejects_traversal_and_links(tmp_path,name,link):
    archive=tmp_path/'snapshot.tar'
    with tarfile.open(archive,'w') as tar:
        entry=tarfile.TarInfo(name)
        if link:
            entry.type=tarfile.SYMTYPE
            entry.linkname='/outside'
            tar.addfile(entry)
        else:
            entry.size=1
            tar.addfile(entry,io.BytesIO(b'x'))
    with pytest.raises(ValueError): service.extract_snapshot(archive,tmp_path/'sut')
