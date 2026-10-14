"""Trusted runner helpers; no SUT code is executed during onboarding."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
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


def installation_token(repo, app_id, key, request=api):
    repository(repo)
    jwt = app_jwt(app_id, key)
    installation = request('GET', '/repos/' + repo + '/installation', jwt)
    result = request('POST', f"/app/installations/{installation['id']}/access_tokens", jwt,
        {'repositories': [repo.split('/')[1]],
         'permissions': {'contents': 'write', 'pull_requests': 'write', 'workflows': 'write'}})
    token = result['token']
    metadata = request('GET', '/repos/' + repo, token)
    if metadata.get('archived') or metadata.get('disabled'):
        request('DELETE', '/installation/token', token)
        raise ValueError('Repository is archived or disabled')
    return token, metadata['default_branch']


def git(root, *args):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def publish(root, repo, base, token, request=api):
    repository(repo)
    query = urllib.parse.urlencode({'state': 'open', 'head': repo.split('/')[0] + ':' + BRANCH, 'base': base})
    existing = request('GET', '/repos/' + repo + '/pulls?' + query, token)
    if existing:
        return {'status': 'existing', 'url': existing[0]['html_url']}
    # Never overwrite any existing bot branch, including QA edits or a closed PR.
    if git(root, 'ls-remote', '--heads', 'origin', 'refs/heads/' + BRANCH):
        raise ValueError('Onboarding branch already exists; review it before retrying')
    paths = git(root, 'ls-files', '--modified', '--others', '--exclude-standard', '-z').split('\0')
    paths = [p for p in paths if p]
    if not paths:
        return {'status': 'unchanged'}
    if len(paths) > 200:
        raise ValueError('Too many generated files')
    for name in paths:
        if not (name.startswith(('.qc-agent/', '.github/')) or name == 'CODEOWNERS'):
            raise ValueError('Generated path outside onboarding scope')
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Unsafe generated path')
        if path.stat().st_size > 2_000_000:
            raise ValueError('Generated file exceeds limit')
    git(root, 'switch', '-c', BRANCH)
    git(root, 'config', 'user.name', 'QC-Agent App')
    git(root, 'config', 'user.email', 'qc-agent@users.noreply.github.com')
    git(root, 'add', '--', *paths)
    git(root, 'commit', '-m', 'chore(qc): onboard repository to QC-Agent')
    git(root, 'push', 'origin', BRANCH)
    body = ('QC-Agent scanned this repository and generated a configuration proposal.\n\n'
            '- Review every `qc-agent:todo` marker; detection is a best-effort proposal.\n'
            '- Provide test-only SUT/DB secrets in Actions secrets; never commit credentials.\n'
            '- Review database startup, health URL, suites and QA CODEOWNERS.\n'
            '- Configure branch protection after confirming a green baseline.\n'
            '- LLM calls are not enabled by this onboarding job.\n\n'
            'Existing files were preserved. This PR does not prove that the SUT starts or tests pass.')
    result = request('POST', '/repos/' + repo + '/pulls', token,
                     {'title': 'chore(qc): onboard QC-Agent', 'head': BRANCH, 'base': base, 'body': body, 'draft': True})
    return {'status': 'created', 'url': result['html_url']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['token', 'publish', 'revoke'])
    args = parser.parse_args()
    if args.command == 'revoke':
        api('DELETE', '/installation/token', os.environ['QC_APP_TOKEN'])
    elif args.command == 'token':
        token, base = installation_token(os.environ['SUT_REPOSITORY'], os.environ['QC_APP_ID'], os.environ['QC_APP_PRIVATE_KEY'])
        print('::add-mask::' + token)
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            # JSON encodes branch safely; checkout/init consume the raw single-line value.
            if '\n' in base or '\r' in base:
                raise ValueError('Invalid default branch')
            output.write(f'token={token}\nbase={base}\n')
    else:
        print(json.dumps(publish(Path(os.environ['SUT_ROOT']), os.environ['SUT_REPOSITORY'],
                                  os.environ['SUT_BASE_BRANCH'], os.environ['QC_APP_TOKEN'])))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Git/provider exceptions may contain tokens; never print their raw messages.
        print('GitHub App operation failed: ' + type(error).__name__)
        raise SystemExit(1)
