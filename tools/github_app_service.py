"""Install-only onboarding service. Never executes SUT code or dispatches Actions."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse

from fastapi import FastAPI, HTTPException, Request
from qc_agent.scaffold import init, templates
from tools.github_app import api, installation_token, repository, BRANCH

log = logging.getLogger('qc-agent.onboarding')


def verify(body, signature, secret):
    return bool(secret and isinstance(signature, str) and hmac.compare_digest(
        ('sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()).encode(), signature.encode()))


def targets(event, payload):
    source = (payload.get('repositories', []) if event == 'installation' and payload.get('action') == 'created'
              else payload.get('repositories_added', []) if event == 'installation_repositories' and payload.get('action') == 'added'
              else [])
    return sorted({repository(item['full_name']) for item in source})


class Queue:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (delivery TEXT, repo TEXT, installation INTEGER, '
                       'status TEXT, attempts INTEGER DEFAULT 0, due REAL DEFAULT 0, '
                       'PRIMARY KEY(delivery,repo))')
            # One process/worker per DB; recover jobs interrupted by process restart.
            db.execute("UPDATE jobs SET status='queued' WHERE status='running'")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def enqueue(self, delivery, repos, installation):
        with self.connect() as db:
            for repo in repos:
                db.execute("INSERT OR IGNORE INTO jobs(delivery,repo,installation,status) VALUES(?,?,?,'queued')",
                           (delivery, repo, installation))

    def run_one(self, handler):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute("SELECT delivery,repo,installation,attempts FROM jobs WHERE status='queued' AND due<=? LIMIT 1",
                             (time.time(),)).fetchone()
            if not job:
                return False
            delivery, repo, installation, attempts = job
            db.execute("UPDATE jobs SET status='running' WHERE delivery=? AND repo=?", (delivery, repo))
        try:
            handler(repo, installation)
            status, due = 'done', 0
        except Exception as error:
            status, due = ('failed' if attempts >= 9 else 'queued'), time.time() + min(3600, 30 * 2 ** attempts)
            # Git errors/provider responses may contain auth headers. Log only type + repo.
            log.warning('onboard failed repo=%s error=%s', repo, type(error).__name__)
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,attempts=?,due=? WHERE delivery=? AND repo=?',
                       (status, attempts + 1, due, delivery, repo))
        return True


def snapshot(repo, token, root):
    """Fetch Git data without checkout filters/hooks, then extract only regular files."""
    env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
           'GIT_CONFIG_VALUE_0': 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode()}
    gitdir = root.parent / 'git-data'
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--depth', '1', '--no-checkout',
                    'https://github.com/' + repo + '.git', str(gitdir)], env=env, capture_output=True, check=True, timeout=120)
    sha = subprocess.run(['git', '-C', str(gitdir), 'rev-parse', 'HEAD'], capture_output=True,
                         text=True, check=True).stdout.strip()
    archive = root.parent / 'snapshot.tar'
    subprocess.run(['git', '-C', str(gitdir), 'archive', '--format=tar', '--output=' + str(archive), sha],
                   capture_output=True, check=True, timeout=30)
    extract_snapshot(archive, root)
    config = root / '.git/config'
    config.parent.mkdir()
    config.write_text('[remote "origin"]\nurl = https://github.com/' + repo + '.git\n')
    return sha


def extract_snapshot(archive, root):
    if archive.stat().st_size > 100_000_000:
        raise ValueError('Repository snapshot too large')
    total = 0
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if len(members) > 20000:
            raise ValueError('Too many repository files')
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
                raise ValueError('Unsafe repository path')
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Links/special files need manual onboarding')
            total += member.size
            if total > 100_000_000 or member.size > 20_000_000:
                raise ValueError('Repository content too large')
            dest = root / member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source:
                dest.write_bytes(source.read())


def onboard(repo, installation, cfg, request=api, download=snapshot):
    token, base = installation_token(repo, cfg['QC_APP_ID'], cfg['QC_APP_PRIVATE_KEY'], request,
                                    expected_installation=installation)
    try:
        query = urllib.parse.urlencode({'state': 'open', 'head': repo.split('/')[0] + ':' + BRANCH, 'base': base})
        existing = request('GET', '/repos/' + repo + '/pulls?' + query, token)
        if existing:
            return {'status': 'existing', 'url': existing[0]['html_url']}
        refs = request('GET', '/repos/' + repo + '/git/matching-refs/heads/' + BRANCH, token)
        if any(r['ref'] == 'refs/heads/' + BRANCH for r in refs):
            raise ValueError('Existing onboarding branch requires manual review')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'sut'
            root.mkdir()
            sha = download(repo, token, root)
            parent = request('GET', '/repos/' + repo + '/git/commits/' + sha, token)
            plan = init.build(init.Options(sut_root=root, base_branch=base,
                qc_repo=cfg['QC_WORKFLOW_REPOSITORY'], qc_ref=cfg['QC_WORKFLOW_REF'], image=cfg['QC_IMAGE'],
                qa_team=cfg.get('QC_QA_OWNER') or None, ghcr_username=cfg.get('GHCR_USERNAME') or None))
            before = {p.label: p.path.read_bytes() if p.path.is_file() else None for p in plan.files}
            init.apply(plan)
            changes = [p for p in plan.files if p.path.read_bytes() != before[p.label]]
            if not changes:
                return {'status': 'unchanged'}
            tree = []
            for file in changes:
                if not file.label.startswith(('.qc-agent/', '.github/')) and file.label not in ('CODEOWNERS', '.typos.toml'):
                    raise ValueError('Unexpected generated path')
                blob = request('POST', '/repos/' + repo + '/git/blobs', token,
                               {'content': base64.b64encode(file.path.read_bytes()).decode(), 'encoding': 'base64'})
                tree.append({'path': file.label, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
            newtree = request('POST', '/repos/' + repo + '/git/trees', token, {'base_tree': parent['tree']['sha'], 'tree': tree})
            commit = request('POST', '/repos/' + repo + '/git/commits', token,
                             {'message': 'chore(qc): onboard QC-Agent', 'tree': newtree['sha'], 'parents': [sha]})
            request('POST', '/repos/' + repo + '/git/refs', token, {'ref': 'refs/heads/' + BRANCH, 'sha': commit['sha']})
            body = ('Generated by offline init; no SUT code or LLM calls were executed.\n\n'
                    'Review TODOs, test-only secrets, DB startup, health route and QA owners before merging. '
                    'Actions in this SUT will pull QC-Agent from GHCR and run QC after merge.\n\n'
                    'Detection notes:\n' + '\n'.join('- ' + note for note in plan.warnings))
            pr = request('POST', '/repos/' + repo + '/pulls', token,
                         {'title': 'chore(qc): onboard QC-Agent', 'head': BRANCH, 'base': base, 'body': body[:12000], 'draft': True})
            return {'status': 'created', 'url': pr['html_url']}
    finally:
        request('DELETE', '/installation/token', token)


def create_app(config=None, handler=None):
    cfg = dict(os.environ if config is None else config)
    if not cfg.get('QC_APP_PRIVATE_KEY') and cfg.get('QC_APP_PRIVATE_KEY_FILE'):
        cfg['QC_APP_PRIVATE_KEY'] = Path(cfg['QC_APP_PRIVATE_KEY_FILE']).read_text(encoding='utf-8')
    for name in ('QC_WEBHOOK_SECRET', 'QC_APP_ID', 'QC_APP_PRIVATE_KEY', 'QC_WORKFLOW_REPOSITORY', 'QC_WORKFLOW_REF', 'QC_IMAGE'):
        if not cfg.get(name):
            raise ValueError('Missing service config: ' + name)
    repository(cfg['QC_WORKFLOW_REPOSITORY'])
    templates.qc_workflow(project='preflight', qc_ref=cfg['QC_WORKFLOW_REF'], image=cfg['QC_IMAGE'])
    queue = Queue(cfg.get('QC_ONBOARD_DB', '/data/onboarding.sqlite'))
    stop = threading.Event()
    run = handler or (lambda repo, installation: onboard(repo, installation, cfg))
    def consume():
        while not stop.is_set():
            try:
                queue.run_one(run)
            except Exception as error:
                log.error('queue operation failed error=%s', type(error).__name__)
            stop.wait(1)
    @asynccontextmanager
    async def lifespan(app):
        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        yield
        stop.set()
        await asyncio.to_thread(thread.join, 180)
    app = FastAPI(lifespan=lifespan)
    app.state.queue = queue
    @app.get('/healthz')
    def health():
        return {'ok': True}
    @app.post('/webhook')
    async def webhook(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1_000_000:
                raise HTTPException(413, 'Webhook too large')
        if not verify(body, request.headers.get('x-hub-signature-256'), cfg['QC_WEBHOOK_SECRET']):
            raise HTTPException(401, 'Invalid signature')
        delivery = request.headers.get('x-github-delivery', '')
        import re
        if not re.fullmatch(r'[a-fA-F0-9-]{36}', delivery):
            raise HTTPException(400, 'Invalid delivery')
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError('Payload must be an object')
            repos = targets(request.headers.get('x-github-event'), payload)
            if repos:
                installation = payload['installation']['id']
                if type(installation) is not int or installation <= 0:
                    raise ValueError('Invalid installation')
                queue.enqueue(delivery, repos, installation)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Invalid webhook payload') from None
        return {'status': 'queued' if repos else 'ignored', 'repositories': len(repos)}
    return app
