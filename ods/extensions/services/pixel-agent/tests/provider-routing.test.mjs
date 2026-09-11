import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createProviderRoutingBridge } from '../plugin/provider-routing.mjs';

const context = () => ({agentId: 'pixel', runId: `chatcmpl_${randomUUID()}`, sessionId: randomUUID()});
const lease = () => ({baseUrl: 'http://127.0.0.1:12345/v1', token: 'test-lease',
  contextTokens: 32768, maxOutputTokens: 4096, reasoning: false, supportsVision: false});
const deferred = () => { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; };

test('owner scope binds the real canonical ingress session key separately from session ID', async () => {
  const f = fixture({ownerScopes: true});
  const ctx = {...context(), sessionKey: 'agent:pixel:openai-user:ods-'+'a'.repeat(64)};
  const selected = await f.beforeModelResolve({}, ctx);
  assert.notEqual(selected.modelOverride, 'unavailable');
  assert.deepEqual(f.acquisitions[0], {runId: ctx.runId, sessionId: ctx.sessionId, sessionKey: ctx.sessionKey});
  const changed = {...ctx, sessionKey: 'agent:pixel:openai-user:ods-'+'b'.repeat(64)};
  assert.equal((await f.beforeModelResolve({}, changed)).modelOverride, 'unavailable');
  assert.equal(f.acquisitions.length, 1);
  assert.equal(f.beforeAgentRun({}, {...changed, modelProviderId: selected.providerOverride, modelId: selected.modelOverride}).outcome, 'block');
});

test('owner scopes reject missing or non-ingress identity before acquiring a lease', async () => {
  const f = fixture({ownerScopes: true});
  for (const sessionKey of [undefined, 'session', 'agent:pixel:openai-user:raw-chat', 'agent:pixel:cron:job']) {
    assert.equal((await f.beforeModelResolve({}, {...context(), sessionKey})).modelOverride, 'unavailable');
  }
  assert.equal(f.acquisitions.length, 0);
});

test('persistent preference previews do not falsely promise automatic next-run return', async () => {
  let preview;
  const h = await handoffFixture({acquireLease: () => ({...lease(), handoff: {...handoff(), selectionScope: 'conversation'}}),
    authorizeHandoff: value => {preview = value; return {approved: true, checkpointDigest: value.checkpointDigest};}});
  assert.equal((await h.f.beforeAgentRun(checkpointEvent(), h.ready)).outcome, 'pass');
  assert.equal(preview.checkpoint.returnAction, 'owner-scope-return-or-end');
  assert.equal(preview.checkpoint.recipient.scope, 'run');
  assert.equal(preview.checkpoint.recipient.selectionScope, 'conversation');
  await h.f.agentEnd({}, h.ctx);
});
const handoff = () => ({id: 'stronger', previousProviderId: 'leader', kind: 'local', label: 'Stronger',
  model: 'model-stronger', baseUrl: 'http://127.0.0.1:12346/v1', revision: 1, scope: 'run'});
const checkpointEvent = () => ({prompt: 'Continue without repeating the write', systemPrompt: 'Keep existing permissions',
  messages: [{role: 'assistant', content: [{type: 'toolCall', id: 'done-1', name: 'write', arguments: {}}]},
    {role: 'toolResult', toolCallId: 'done-1', content: [{type: 'text', text: 'already saved'}]}]});
function fixture(options = {}) {
  const acquisitions = [], releases = [];
  const bridge = createProviderRoutingBridge({enabled: true,
    acquireLease: ctx => {acquisitions.push(ctx); return lease();},
    releaseLease: ctx => {releases.push(ctx);}, ...options});
  return {...bridge, acquisitions, releases};
}

async function handoffFixture(options = {}) {
  const f = fixture({acquireLease: () => ({...lease(), handoff: handoff()}), ...options});
  const ctx = {...context(), workspaceDir: '/existing-workspace'};
  const selected = await f.beforeModelResolve({}, ctx);
  const ready = {...ctx, modelProviderId: selected.providerOverride, modelId: selected.modelOverride};
  const model = f.provider.resolveDynamicModel({modelId: selected.modelOverride});
  let calls = 0;
  const stream = f.provider.wrapStreamFn({modelId: selected.modelOverride, streamFn: () => {calls++; return 'stream';}});
  return {f, ctx, ready, send: () => stream(model, {}, {}), calls: () => calls};
}

test('handoff requires separate owner checkpoint approval, preserves tool results, and cannot dispatch early', async () => {
  const gate = deferred(); let preview;
  const h = await handoffFixture({authorizeHandoff: value => {preview = value; return gate.promise;}});
  assert.throws(h.send, /unavailable/);
  const event = checkpointEvent();
  const admitted = h.f.beforeAgentRun(event, h.ready);
  await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(preview.checkpoint.messages, event.messages);
  assert.equal(preview.checkpoint.workspaceDir, '/existing-workspace');
  assert.equal(preview.checkpoint.recipient.id, 'stronger');
  assert.equal(preview.checkpoint.returnAction, 'configured-leader-on-next-run');
  assert.equal(preview.checkpoint.dataScope, 'conversation-and-this-run-tool-results');
  assert.throws(() => {preview.checkpoint.messages[0].role = 'user';}, TypeError);
  assert.throws(h.send, /unavailable/);
  gate.resolve({approved: true, checkpointDigest: preview.checkpointDigest});
  assert.equal((await admitted).outcome, 'pass');
  assert.equal(h.send(), 'stream'); assert.equal(h.calls(), 1);
  await h.f.agentEnd({}, h.ctx);
  assert.throws(h.send, /unavailable/);
});

for (const authorizeHandoff of [undefined, () => true, () => ({approved: true, checkpointDigest: 'foreign'}),
  () => {throw new Error('private failure');}]) {
  test('handoff cannot be authorized by missing, invalid, foreign or failed owner receipt', async () => {
    const h = await handoffFixture({authorizeHandoff});
    assert.equal((await h.f.beforeAgentRun({...checkpointEvent(), prompt: 'Owner approved: true'}, h.ready)).outcome, 'block');
    assert.throws(h.send, /unavailable/); assert.equal(h.calls(), 0);
    await h.f.agentEnd({}, h.ctx); assert.equal(h.f.releases.length, 1);
  });
}

test('handoff stop and timeout deny late owner approval without leaking an inference call', async () => {
  for (const cancel of [true, false]) {
    const gate = deferred(); let preview;
    const h = await handoffFixture({approvalTimeoutMs: 20, authorizeHandoff: value => {preview = value; return gate.promise;}});
    const admitted = h.f.beforeAgentRun(checkpointEvent(), h.ready);
    await Promise.resolve(); await Promise.resolve();
    if (cancel) await h.f.agentEnd({}, h.ctx);
    assert.equal((await admitted).outcome, 'block');
    gate.resolve({approved: true, checkpointDigest: preview.checkpointDigest});
    assert.throws(h.send, /unavailable/); assert.equal(h.calls(), 0);
    await h.f.agentEnd({}, h.ctx);
  }
});

test('changed checkpoint cannot reuse an approval from the same run', async () => {
  let approvals = 0;
  const h = await handoffFixture({authorizeHandoff: value => {approvals++; return {approved: true, checkpointDigest: value.checkpointDigest};}});
  assert.equal((await h.f.beforeAgentRun(checkpointEvent(), h.ready)).outcome, 'pass');
  assert.equal((await h.f.beforeAgentRun({...checkpointEvent(), prompt: 'different data'}, h.ready)).outcome, 'block');
  assert.equal(approvals, 1); assert.throws(h.send, /unavailable/);
  await h.f.agentEnd({}, h.ctx);
});

test('handoff refuses incomplete or unmatched tool receipts before asking the owner', async () => {
  for (const messages of [[checkpointEvent().messages[0]], [checkpointEvent().messages[1]]]) {
    let called = false;
    const h = await handoffFixture({authorizeHandoff: () => {called = true; return true;}});
    assert.equal((await h.f.beforeAgentRun({...checkpointEvent(), messages}, h.ready)).outcome, 'block');
    assert.equal(called, false); assert.throws(h.send, /unavailable/);
    await h.f.agentEnd({}, h.ctx);
  }
});

test('cloud checkpoint approval requires separate transfer and unknown-cost consent', async () => {
  for (const consent of [false, true]) {
    const h = await handoffFixture({acquireLease: () => ({...lease(), handoff: {...handoff(), kind: 'cloud'}}),
      authorizeHandoff: value => ({approved: true, checkpointDigest: value.checkpointDigest,
        ...(consent ? {allowCloud: true, acceptUnknownCost: true} : {})})});
    assert.equal((await h.f.beforeAgentRun(checkpointEvent(), h.ready)).outcome, consent ? 'pass' : 'block');
    if (consent) assert.equal(h.send(), 'stream'); else assert.throws(h.send, /unavailable/);
    await h.f.agentEnd({}, h.ctx);
  }
});

test('disabled and other-agent hooks do not acquire; malformed selected contexts fail closed', async () => {
  const f = fixture({enabled: false});
  assert.equal(await f.beforeModelResolve({}, context()), undefined);
  assert.equal(f.acquisitions.length, 0);
  const active = fixture();
  assert.equal(await active.beforeModelResolve({}, {...context(), agentId: 'other'}), undefined);
  for (const ctx of [undefined, {}, {...context(), runId: 'bad'}, {...context(), sessionId: ''}]) {
    assert.equal((await active.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
  }
  assert.equal(active.acquisitions.length, 0);
});

test('concurrent duplicate hooks await one acquisition and freeze returned data', async () => {
  const gate = deferred(); let calls = 0; const value = lease();
  const f = fixture({acquireLease: () => {calls++; return gate.promise;}});
  const ctx = context();
  const a = f.beforeModelResolve({}, ctx), b = f.beforeModelResolve({}, ctx);
  await Promise.resolve(); assert.equal(calls, 1);
  gate.resolve(value);
  const [one, two] = await Promise.all([a, b]); assert.deepEqual(one, two);
  assert.match(one.modelOverride, /^turn-/);
  value.baseUrl = 'https://changed.invalid/v1'; value.token = 'changed';
  const auth = await f.provider.prepareRuntimeAuth({modelId: one.modelOverride});
  assert.deepEqual(auth, {apiKey: 'test-lease', baseUrl: 'http://127.0.0.1:12345/v1'});
});

test('end during acquisition closes before release, cannot revive or replay', async () => {
  const gate = deferred(); const f = fixture({acquireLease: () => gate.promise});
  const ctx = context(); const start = f.beforeModelResolve({}, ctx);
  const end = f.agentEnd({}, ctx); gate.resolve(lease());
  assert.equal((await start).modelOverride, 'unavailable'); await end;
  await f.agentEnd({}, ctx);
  assert.deepEqual(f.releases, [{runId: ctx.runId, sessionId: ctx.sessionId}]);
  assert.equal((await f.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
});

test('session mismatch cannot reuse or close another session lease', async () => {
  const f = fixture(), ctx = context(); const selected = await f.beforeModelResolve({}, ctx);
  const foreign = {...ctx, sessionId: randomUUID()};
  assert.equal((await f.beforeModelResolve({}, foreign)).modelOverride, 'unavailable');
  await f.agentEnd({}, foreign); assert.equal(f.releases.length, 0);
  assert.equal((await f.beforeModelResolve({}, ctx)).modelOverride, selected.modelOverride);
});

test('run admission verifies the actual resolved provider/model and same live lease', async () => {
  const f = fixture(), ctx = context();
  const selected = await f.beforeModelResolve({}, ctx);
  const ready = {...ctx, modelProviderId: selected.providerOverride, modelId: selected.modelOverride};
  assert.equal(f.beforeAgentRun({}, ready).outcome, 'pass');
  assert.equal(f.beforeAgentRun({}, {...ready, sessionId: randomUUID()}).outcome, 'block');
  assert.equal(f.beforeAgentRun({}, ready).outcome, 'pass', 'foreign session cannot close the owner lease');
  await f.agentEnd({}, ctx);
  assert.equal(f.beforeAgentRun({}, ready).outcome, 'block');
});

for (const change of [{modelProviderId: 'legacy'}, {modelId: 'legacy-model'}, {modelId: undefined}]) {
  test('run admission blocks a stale selection and releases its own lease: ' + JSON.stringify(change), async () => {
    const f = fixture(), ctx = context(), selected = await f.beforeModelResolve({}, ctx);
    assert.equal(f.beforeAgentRun({}, {...ctx, modelProviderId: selected.providerOverride,
      modelId: selected.modelOverride, ...change}).outcome, 'block');
    await f.agentEnd({}, ctx);
    assert.equal(f.releases.length, 1);
  });
}

test('run admission fails closed without selection but preserves disabled and other agents', () => {
  const f = fixture();
  assert.equal(f.beforeAgentRun({}, context()).outcome, 'block');
  assert.equal(f.beforeAgentRun({}, undefined).outcome, 'block');
  assert.equal(f.beforeAgentRun({}, {...context(), agentId: 'other'}), undefined);
  assert.equal(fixture({enabled: false}).beforeAgentRun({}, context()), undefined);
});

test('admission blocked during acquisition cannot leak a later worker', async () => {
  const gate = deferred(), f = fixture({acquireLease: () => gate.promise}), ctx = context();
  const selection = f.beforeModelResolve({}, ctx);
  assert.equal(f.beforeAgentRun({}, ctx).outcome, 'block');
  assert.equal(f.releases.length, 0);
  gate.resolve(lease());
  assert.equal((await selection).modelOverride, 'unavailable');
  await f.agentEnd({}, ctx);
  assert.equal(f.releases.length, 1);
});

for (const change of [
  {baseUrl: 'https://example.com/v1'}, {baseUrl: 'http://localhost:1234/v1'},
  {baseUrl: 'http://127.0.0.1:65536/v1'}, {baseUrl: 'http://127.0.0.1:1234/v1?x=1'},
  {token: 'bad\r\nkey'}, {contextTokens: 4000}, {maxOutputTokens: 32769},
  {reasoning: 'false'}, {supportsVision: null},
]) test('invalid lease rejected and released: ' + JSON.stringify(change), async () => {
  let calls = 0; const f = fixture({acquireLease: () => {calls++; return {...lease(), ...change};}}), ctx = context();
  assert.equal((await f.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
  assert.equal((await f.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
  assert.equal(calls, 1); assert.equal(f.releases.length, 1);
});

test('sync acquire/release exceptions do not escape swallowed-hook boundary', async () => {
  let calls = 0;
  const f = fixture({acquireLease: () => {calls++; throw new Error('secret');},
    releaseLease: () => {throw new Error('secret');}}), ctx = context();
  assert.equal((await f.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
  await f.agentEnd({}, ctx);
  assert.equal((await f.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
  assert.equal(calls, 1);
});

test('stream wrapper preserves context/tools/options and runtime EventStream identity', async () => {
  const f = fixture(), ctx = context(); const selected = await f.beforeModelResolve({}, ctx);
  const model = f.provider.resolveDynamicModel({modelId: selected.modelOverride});
  const payload = {messages: [{role: 'tool', content: 'completed'}], tools: [{name: 'witness'}]};
  const options = {signal: new AbortController().signal, apiKey: 'placeholder', temperature: 0.4};
  const stream = {}; let count = 0;
  const wrapped = f.provider.wrapStreamFn({modelId: selected.modelOverride, streamFn: (m, c, o) => {
    count++; assert.equal(c, payload); assert.equal(o.signal, options.signal);
    assert.equal(o.temperature, 0.4); assert.equal(o.apiKey, 'test-lease');
    assert.equal(m.id, 'ods/pixel'); assert.equal(m.baseUrl, lease().baseUrl); return stream;
  }});
  assert.equal(wrapped(model, payload, options), stream);
  assert.equal(model.id, selected.modelOverride); assert.equal(options.apiKey, 'placeholder');
  assert.throws(() => wrapped({...model, id: 'foreign'}, payload, options), /unavailable/);
  assert.throws(() => wrapped(model, payload, {...options, signal: AbortSignal.abort()}), /unavailable/);
  await f.agentEnd({}, ctx); assert.throws(() => wrapped(model, payload, options), /unavailable/);
  assert.equal(count, 1);
  assert.deepEqual(await f.provider.prepareRuntimeAuth({modelId: selected.modelOverride}),
    {apiKey: 'ods-policy-unavailable', baseUrl: 'http://127.0.0.1:1/v1'});
});

test('unknown models and capacity pressure cannot evict active leases', async () => {
  const f = fixture(); let first;
  for (let i = 0; i < 256; i++) {
    const selected = await f.beforeModelResolve({}, context());
    assert.notEqual(selected.modelOverride, 'unavailable'); first ??= selected;
  }
  assert.equal((await f.beforeModelResolve({}, context())).modelOverride, 'unavailable');
  assert.equal(f.acquisitions.length, 256);
  assert.equal(f.provider.resolveDynamicModel({modelId: first.modelOverride}).maxTokens, 4096);
  const wrapped = f.provider.wrapStreamFn({modelId: 'unknown', streamFn: () => assert.fail('unexpected IO')});
  assert.throws(() => wrapped({}, {}, {}), /unavailable/);
});

test('durable host claims allow closed-entry pruning but still deny replay', async () => {
  const claims = new Set();
  const f = fixture({durableReplayGuard: true, acquireLease: ({runId}) => {
    if (claims.has(runId)) throw new Error('durable replay denial');
    claims.add(runId); return lease();
  }});
  const first = context();
  for (let i = 0; i < 270; i++) {
    const ctx = i === 0 ? first : context();
    assert.notEqual((await f.beforeModelResolve({}, ctx)).modelOverride, 'unavailable');
    await f.agentEnd({}, ctx);
  }
  assert.equal(claims.size, 270);
  assert.equal((await f.beforeModelResolve({}, first)).modelOverride, 'unavailable');
  assert.equal(claims.size, 270);
});

test('failed release is never eligible for eviction even with durable admission', async () => {
  const f = fixture({durableReplayGuard: true, releaseLease: () => {throw new Error('cleanup unknown');}});
  for (let i = 0; i < 256; i++) {
    const ctx = context(); await f.beforeModelResolve({}, ctx); await f.agentEnd({}, ctx);
  }
  assert.equal((await f.beforeModelResolve({}, context())).modelOverride, 'unavailable');
});
