// Webhook bridge only. QC-Agent and the SUT run on GitHub Actions.
const repoPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

export async function verify(body, signature, secret) {
  if (!secret || !/^sha256=[0-9a-f]{64}$/.test(signature || '')) return false;
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret),
    {name: 'HMAC', hash: 'SHA-256'}, false, ['verify']);
  const bytes = Uint8Array.from(signature.slice(7).match(/../g), x => parseInt(x, 16));
  return crypto.subtle.verify('HMAC', key, bytes, body);
}

export function repositories(event, payload, controlRepo) {
  const valid = (repo) => repoPattern.test(repo) && !repo.split('/').some(p => ['.', '..'].includes(p));
  const source = event === 'installation' && payload.action === 'created' ? payload.repositories :
    event === 'installation_repositories' && payload.action === 'added' ? payload.repositories_added : [];
  if (!Array.isArray(source)) throw new Error('Invalid repositories');
  return [...new Set(source.map(r => r.full_name).filter(r => typeof r === 'string' && valid(r)
    && r.toLowerCase() !== controlRepo.toLowerCase()))];
}

function b64(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

async function jwt(env) {
  const now = Math.floor(Date.now() / 1000);
  const enc = value => b64(new TextEncoder().encode(JSON.stringify(value)));
  const message = enc({alg: 'RS256', typ: 'JWT'}) + '.' + enc({iat: now - 60, exp: now + 540, iss: env.QC_APP_ID});
  const pem = env.QC_APP_PRIVATE_KEY_PKCS8.replace(/-----[^-]+-----|\s/g, '');
  const key = await crypto.subtle.importKey('pkcs8', Uint8Array.from(atob(pem), c => c.charCodeAt(0)),
    {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['sign']);
  const signature = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(message));
  return message + '.' + b64(new Uint8Array(signature));
}

async function api(path, token, body) {
  const response = await fetch('https://api.github.com' + path, {
    method: 'POST', redirect: 'error',
    headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json', 'User-Agent': 'qc-agent-app'},
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error('GitHub HTTP ' + response.status);
  return response.status === 204 ? null : response.json();
}

export class OnboardingQueue {
  constructor(state, env) { this.state = state; this.env = env; }

  async fetch(request) {
    const {delivery, repos} = await request.json();
    const key = 'delivery:' + delivery;
    let duplicate = false;
    await this.state.storage.transaction(async storage => {
      if (await storage.get(key)) { duplicate = true; return; }
      await storage.put(key, {repos, next: 0, attempts: 0, created: Date.now(), done: repos.length === 0});
      // Alarm and queue entry persist together; failed dispatches retry without needing webhook redelivery.
      await storage.setAlarm(Date.now() + 1000);
    });
    return Response.json({status: duplicate ? 'duplicate' : 'queued'}, {status: 202});
  }

  async alarm() {
    const entries = await this.state.storage.list({prefix: 'delivery:'});
    for (const [key, job] of entries) {
      if (job.done) {
        if (Date.now() - job.created > 7 * 86400000) await this.state.storage.delete(key);
        continue;
      }
      try {
        const appJwt = await jwt(this.env);
        const auth = await api('/app/installations/' + this.env.QC_CONTROL_INSTALLATION_ID + '/access_tokens', appJwt,
          {repositories: [this.env.QC_CONTROL_REPOSITORY.split('/')[1]], permissions: {actions: 'write'}});
        // Process at most five dispatches per alarm to bound execution time.
        for (let count = 0; job.next < job.repos.length && count < 5; count++) {
          await api('/repos/' + this.env.QC_CONTROL_REPOSITORY + '/actions/workflows/qc-onboard-app.yml/dispatches', auth.token,
            {ref: this.env.QC_CONTROL_REF, inputs: {repository: job.repos[job.next], qa_owner: this.env.QC_QA_OWNER || ''}});
          job.next++;
          await this.state.storage.put(key, job);
        }
        job.done = job.next === job.repos.length;
        job.attempts = 0;
      } catch {
        job.attempts++;
        // No payload, key, token or provider response is logged.
        console.error('Onboarding dispatch failed; attempt=' + job.attempts);
      }
      await this.state.storage.put(key, job);
    }
    // Also wakes periodically to expire dedup records. Delivery is at-least-once;
    // workflow concurrency + existing PR lookup handle dispatch duplicates.
    const pending = [...(await this.state.storage.list({prefix: 'delivery:'})).values()].some(j => !j.done);
    await this.state.storage.setAlarm(Date.now() + (pending ? 60000 : 86400000));
  }
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST' || new URL(request.url).pathname !== '/webhook')
      return new Response('Not found', {status: 404});
    if (Number(request.headers.get('content-length')) > 1_000_000)
      return new Response('Too large', {status: 413});
    const body = await request.arrayBuffer();
    if (body.byteLength > 1_000_000) return new Response('Too large', {status: 413});
    if (!await verify(body, request.headers.get('x-hub-signature-256'), env.QC_WEBHOOK_SECRET))
      return new Response('Invalid signature', {status: 401});
    const delivery = request.headers.get('x-github-delivery');
    if (!/^[a-f0-9-]{36}$/i.test(delivery || '')) return new Response('Invalid delivery', {status: 400});
    try {
      const payload = JSON.parse(new TextDecoder().decode(body));
      const repos = repositories(request.headers.get('x-github-event'), payload, env.QC_CONTROL_REPOSITORY);
      const queue = env.ONBOARDING.get(env.ONBOARDING.idFromName('onboarding'));
      return queue.fetch('https://internal/queue', {method: 'POST', body: JSON.stringify({delivery, repos})});
    } catch { return new Response('Invalid webhook or queue unavailable', {status: 503}); }
  },
};
