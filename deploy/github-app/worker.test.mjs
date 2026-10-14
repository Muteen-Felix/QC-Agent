import test from 'node:test';
import assert from 'node:assert/strict';
import {createHmac, generateKeyPairSync} from 'node:crypto';
import worker, {verify, repositories, OnboardingQueue} from './worker.mjs';

test('signature checks exact bytes and rejects missing/tampered signatures', async () => {
  const body = new TextEncoder().encode('Hello, World!');
  const secret = "It's a Secret to Everybody";
  const signature = 'sha256=' + createHmac('sha256', secret).update(body).digest('hex');
  assert.equal(await verify(body, signature, secret), true);
  assert.equal(await verify(new TextEncoder().encode('changed'), signature, secret), false);
  assert.equal(await verify(body, null, secret), false);
});

test('only install/add events onboard unique SUT repositories', () => {
  const repos = [{full_name: 'org/sut'}, {full_name: 'org/sut'}, {full_name: 'org/control'}, {full_name: '../bad'}];
  assert.deepEqual(repositories('installation', {action: 'created', repositories: repos}, 'org/control'), ['org/sut']);
  assert.deepEqual(repositories('installation', {action: 'deleted', repositories: repos}, 'org/control'), []);
  assert.deepEqual(repositories('installation_repositories', {action: 'added', repositories_added: repos}, 'org/control'), ['org/sut']);
});

test('invalid signature never invokes queue', async () => {
  const response = await worker.fetch(new Request('https://app/webhook', {method: 'POST', body: '{}'}),
    {QC_WEBHOOK_SECRET: 'test', ONBOARDING: {get() {throw new Error('must not queue');}}});
  assert.equal(response.status, 401);
});

test('redelivery is deduplicated with a persistent queue record', async () => {
  const entries = new Map();
  const storage = {get: async k => entries.get(k), put: async (k,v) => entries.set(k,v), setAlarm: async () => {},
    transaction: async fn => fn(storage)};
  const queue = new OnboardingQueue({storage}, {});
  const request = () => new Request('https://internal', {method: 'POST', body: JSON.stringify({delivery: 'd1', repos: ['org/sut']})});
  assert.equal((await (await queue.fetch(request())).json()).status, 'queued');
  assert.equal((await (await queue.fetch(request())).json()).status, 'duplicate');
  assert.equal(entries.size, 1);
});

test('dispatch failure is retained and retried with a real signed App JWT', async () => {
  const {privateKey} = generateKeyPairSync('rsa', {modulusLength: 2048});
  const env = {QC_APP_ID: '123', QC_CONTROL_INSTALLATION_ID: '456', QC_CONTROL_REPOSITORY: 'org/control',
    QC_CONTROL_REF: 'app-llm', QC_QA_OWNER: '@qa',
    QC_APP_PRIVATE_KEY_PKCS8: privateKey.export({type: 'pkcs8', format: 'pem'})};
  const entries = new Map([['delivery:d1', {repos: ['org/sut'], next: 0, attempts: 0, created: Date.now(), done: false}]]);
  let alarmAt;
  const storage = {list: async () => entries, put: async (k,v) => entries.set(k, structuredClone(v)),
    delete: async k => entries.delete(k), setAlarm: async t => {alarmAt = t;}};
  const originalFetch = globalThis.fetch;
  let dispatches = 0;
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    if (url.endsWith('/access_tokens')) {
      assert.deepEqual(body.permissions, {actions: 'write'});
      assert.deepEqual(body.repositories, ['control']);
      assert.equal(options.headers.Authorization.split('.').length, 3);
      return Response.json({token: 'fake-token'});
    }
    assert.equal(body.ref, 'app-llm');
    assert.deepEqual(body.inputs, {repository: 'org/sut', qa_owner: '@qa'});
    return ++dispatches === 1 ? new Response('', {status: 503}) : new Response(null, {status: 204});
  };
  try {
    const queue = new OnboardingQueue({storage}, env);
    await queue.alarm();
    assert.equal(entries.get('delivery:d1').done, false);
    assert.equal(entries.get('delivery:d1').next, 0);
    assert.ok(alarmAt > Date.now());
    await queue.alarm();
    assert.equal(entries.get('delivery:d1').done, true);
    assert.equal(dispatches, 2);
  } finally {globalThis.fetch = originalFetch;}
});
