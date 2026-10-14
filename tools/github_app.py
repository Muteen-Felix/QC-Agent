"""GitHub App authentication helpers for the onboarding service."""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
BRANCH = "qc-agent/onboarding"


def repository(value):
    if not REPO.fullmatch(value) or any(p in {'.', '..'} for p in value.split('/')):
        raise ValueError('Invalid repository')
    return value


def api(method, path, token, body=None):
    request = urllib.request.Request('https://api.github.com' + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'User-Agent': 'qc-agent-app'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response) if response.status != 204 else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'GitHub API HTTP {error.code}') from None


def app_jwt(app_id, key):
    if not str(app_id).isdigit():
        raise ValueError('Invalid App ID')
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=')
    now = int(time.time())
    message = encode(b'{"alg":"RS256","typ":"JWT"}') + b'.' + encode(
        json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': str(app_id)}).encode())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'key.pem'
        path.write_text(key, encoding='utf-8')
        path.chmod(0o600)
        signed = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(path)],
                                input=message, capture_output=True)
        if signed.returncode:
            raise RuntimeError('Unable to sign App JWT')
    return (message + b'.' + encode(signed.stdout)).decode()


def installation_token(repo, app_id, key, request=api, expected_installation=None):
    repository(repo)
    jwt = app_jwt(app_id, key)
    installation = request('GET', '/repos/' + repo + '/installation', jwt)
    if expected_installation is not None and installation['id'] != expected_installation:
        raise ValueError('Installation changed since webhook delivery')
    result = request('POST', f"/app/installations/{installation['id']}/access_tokens", jwt,
        {'repositories': [repo.split('/')[1]],
         'permissions': {'contents': 'write', 'pull_requests': 'write', 'workflows': 'write'}})
    token = result['token']
    try:
        metadata = request('GET', '/repos/' + repo, token)
        if metadata.get('archived') or metadata.get('disabled'):
            raise ValueError('Repository is archived or disabled')
        return token, metadata['default_branch']
    except Exception:
        request('DELETE', '/installation/token', token)
        raise
