import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID, randomBytes } from 'node:crypto';
import { createAccessRuntime } from '../plugin/access-runtime.mjs';
import { createManagedProviderBootstrap } from '../plugin/provider-bootstrap.mjs';
import { composeManagedAdmission } from './fixtures/managed-admission.mjs';

const options = {skip: typeof process.getuid !== 'function'};
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};
const identity = () => ({agentId: 'pixel', runId: randomUUID(), sessionId: randomUUID(),
  sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64), workspaceDir: '/workspace'});
const lease = () => ({baseUrl: 'http://127.0.0.1:12345/v1', token: 'synthetic-private-lease',
  contextTokens: 32768, maxOutputTokens: 4096, reasoning: false, supportsVision: false});
function fixture(t, {acquire, release, authorize} = {}) {
  const root = mkdtempSync(join(tmpdir(), 'ods-access-provider-'));
  t.after(() => rmSync(root, {recursive: true, force: true})); // this test's fresh owned directory only
  const deployment = {binding: {schemaVersion: 1, activationId: randomUUID(), revision: 1, allowCloud: false},
    sourceRoot: '/owner/ods', hostPython: '/usr/bin/python3', providerDirectory: '/owner/private/providers',
    ownerScopes: true, leaseTimeoutSeconds: 180, approvalTimeoutSeconds: 60};
  const config = {agents: {defaults: {workspace: '/workspace'}, list: [{id: 'pixel', sandbox: {mode: 'all'},
    model: {primary: 'ods-policy/managed', fallbacks: []}}]},
  models: {providers: {'ods-policy': {baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions',
    apiKey: 'ods-policy-unavailable', models: [{id: 'managed', name: 'ODS managed', contextWindow: 32768,
      maxTokens: 4096, reasoning: false, input: ['text']}]}}},
  plugins: {entries: {'pixel-ods': {enabled: true, hooks: {allowConversationAccess: true},
    config: {managedProvider: clone(deployment.binding)}}}}};
  const access = createAccessRuntime({directory: join(root, 'admission'), config: () => config,
    runtimeVersion: '2026.6.33', hooksAllowed: true});
  assert.equal(access.status().available, true);
  const acquired = [], released = [];
  const routing = createManagedProviderBootstrap({deployment, readConfig: () => config,
    createLease: () => ({durableReplayGuard: true,
      async acquireLease(ctx) {acquired.push(ctx); return acquire ? await acquire(ctx) : lease();},
      async releaseLease(ctx) {released.push(ctx); await release?.(ctx);},
    }), createHandoff: () => authorize ?? (async () => null)});
  const composition = composeManagedAdmission(access, routing);
  const event = {prompt: 'Continue existing task', systemPrompt: 'Preserve permissions', messages: []};
  async function select(context = identity()) {
    const selected = await routing.beforeModelResolve({}, context);
    return {...context, modelProviderId: selected.providerOverride, modelId: selected.modelOverride};
  }
  return {access, routing, composition, config, acquired, released, event, select};
}

test('access hold denies a selected route without inference and preserves its transition token', options, async t => {
  const f = fixture(t), ctx = await f.select(), token = randomBytes(32).toString('hex');
  f.access.acquire(token, f.access.status().revision);
  assert.equal((await f.composition.admit(f.event, ctx)).outcome, 'block');
  assert.equal(f.released.length, 1); assert.equal(f.access.status().active, 0);
  assert.equal(f.access.status().phase, 'held'); assert.equal(f.access.owns(token), true);
  await f.composition.finish(f.event, ctx); // core may or may not emit this later
  assert.equal(f.released.length, 1); assert.equal(f.access.owns(token), true);
  f.access.release(token); assert.equal(f.access.status().phase, 'idle');
  await f.routing.shutdown();
});

test('provider denial after access admission finishes even when core never emits agent_end', options, async t => {
  const f = fixture(t), ctx = await f.select();
  assert.equal((await f.composition.admit(f.event, {...ctx, modelId: 'foreign'})).outcome, 'block');
  assert.equal(f.access.status().phase, 'idle'); assert.equal(f.access.status().active, 0);
  assert.equal(f.released.length, 1);
  await f.composition.finish(f.event, ctx); // duplicate event stays idempotent
  assert.equal(f.released.length, 1); assert.equal(f.access.status().active, 0);
  await f.routing.shutdown();
});

test('held access waits for a late pending lease to close before denied admission settles', options, async t => {
  const gate = deferred(), f = fixture(t, {acquire: () => gate.promise}), ctx = identity();
  const selection = f.routing.beforeModelResolve({}, ctx);
  for (let i = 0; i < 5; i++) await Promise.resolve();
  const token = randomBytes(32).toString('hex'); f.access.acquire(token, f.access.status().revision);
  let ended = false;
  const denied = f.composition.admit(f.event, ctx).then(value => {ended = true; return value;});
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(ended, false); assert.equal(f.access.status().phase, 'held');
  gate.resolve(lease());
  assert.equal((await selection).modelOverride, 'unavailable');
  assert.equal((await denied).outcome, 'block'); assert.equal(f.released.length, 1);
  f.access.release(token); await f.routing.shutdown();
});

test('slow lease cleanup keeps access busy until exit and prevents a transition', options, async t => {
  const gate = deferred(), f = fixture(t, {release: () => gate.promise}), ctx = await f.select();
  assert.equal((await f.composition.admit(f.event, ctx)).outcome, 'pass');
  let ended = false;
  const finishing = f.composition.finish(f.event, ctx).then(() => {ended = true;});
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(f.access.status().phase, 'busy'); assert.equal(ended, false);
  assert.throws(() => f.access.acquire(randomBytes(32).toString('hex'), f.access.status().revision), /busy/);
  gate.resolve(); await finishing;
  assert.equal(f.access.status().phase, 'idle'); assert.equal(f.access.status().active, 0);
  await f.routing.shutdown();
});

test('unknown provider cleanup cannot clear shared activity or allow access transition', options, async t => {
  const f = fixture(t, {release: () => {throw new Error('unknown worker exit');}}), ctx = await f.select();
  assert.equal((await f.composition.admit(f.event, ctx)).outcome, 'pass');
  await assert.rejects(f.composition.finish(f.event, ctx), /cleanup incomplete/);
  assert.equal(f.access.status().phase, 'busy'); assert.equal(f.access.status().active, 1);
  assert.throws(() => f.access.acquire(randomBytes(32).toString('hex'), f.access.status().revision), /busy/);
  await assert.rejects(f.routing.shutdown(), /cleanup incomplete/);
});

for (const mismatch of [{sessionId: randomUUID()}, {sessionId: null},
  {sessionKey: 'agent:pixel:openai-user:ods-' + 'b'.repeat(64)}, {agentId: 'other'}]) {
test('foreign session identity cannot mark an existing run idle: ' + JSON.stringify(mismatch), options, async t => {
  const f = fixture(t), ctx = await f.select();
  assert.equal((await f.composition.admit(f.event, ctx)).outcome, 'pass');
  await assert.rejects(f.composition.finish(f.event, {...ctx, ...mismatch}), /cleanup incomplete/);
  assert.equal(f.access.status().phase, 'busy'); assert.equal(f.released.length, 0);
  await f.composition.finish(f.event, ctx); assert.equal(f.access.status().phase, 'idle');
  await f.routing.shutdown();
});
}

test('per-run cancellation waits for owner-checkpoint worker exit before clearing access', options, async t => {
  const gate = deferred(); let approvalSignal;
  const f = fixture(t, {acquire: () => ({...lease(), handoff: {id: 'stronger', previousProviderId: 'leader', kind: 'local',
    label: 'Stronger', model: 'stronger', baseUrl: 'http://127.0.0.1:8000/v1', revision: 1, scope: 'run'}}),
  authorize: ({signal}) => {approvalSignal = signal; return gate.promise;}});
  const ctx = await f.select(), admission = f.composition.admit(f.event, ctx);
  for (let i = 0; i < 20 && !approvalSignal; i++) await Promise.resolve();
  assert.ok(approvalSignal);
  let ended = false;
  const finishing = f.composition.finish(f.event, ctx).then(() => {ended = true;});
  for (let i = 0; i < 20; i++) await Promise.resolve();
  assert.equal(approvalSignal.aborted, true);
  assert.equal(ended, false, 'checkpoint process has not exited');
  assert.equal(f.access.status().phase, 'busy');
  gate.resolve(null); await finishing;
  assert.equal((await admission).outcome, 'block');
  assert.equal(f.access.status().phase, 'idle'); assert.equal(f.released.length, 1);
  await f.routing.shutdown();
});

test('other agents and detached tool activity remain accounted across Pixel cleanup', options, async t => {
  const f = fixture(t), ctx = await f.select(), other = {...identity(), agentId: 'other'};
  assert.equal((await f.composition.admit(f.event, ctx)).outcome, 'pass');
  assert.equal((await f.composition.admit(f.event, other)).outcome, 'pass');
  f.access.beforeTool({toolCallId: 'background'}, ctx);
  f.access.afterTool({toolCallId: 'background', toolName: 'exec', result: {details: {status: 'running', sessionId: 'process-1'}}}, ctx);
  await f.composition.finish(f.event, ctx); assert.equal(f.access.status().active, 2);
  await f.composition.finish(f.event, other); assert.equal(f.access.status().active, 1);
  assert.equal(f.access.status().phase, 'busy');
  f.access.afterTool({toolCallId: 'poll', toolName: 'process', params: {sessionId: 'process-1'}, result: {details: {status: 'exited'}}}, ctx);
  assert.equal(f.access.status().phase, 'busy', 'status alone is not a terminal process receipt');
  f.access.afterTool({toolCallId: 'poll', toolName: 'process', params: {sessionId: 'process-1'}, result: {details: {status: 'exited', exitCode: 0}}}, ctx);
  assert.equal(f.access.status().phase, 'idle'); await f.routing.shutdown();
});

test('per-run checkpoint cleanup does not wait for or cancel a different live run', options, async t => {
  const waiting = new Map();
  const f = fixture(t, {acquire: () => ({...lease(), handoff: {id: 'stronger', previousProviderId: 'leader', kind: 'local',
    label: 'Stronger', model: 'stronger', baseUrl: 'http://127.0.0.1:8000/v1', revision: 1, scope: 'run'}}),
  authorize: ({checkpoint, signal}) => {const gate = deferred(); waiting.set(checkpoint.runId, {...gate, signal}); return gate.promise;}});
  const first = await f.select(), second = await f.select();
  const admissions = [f.composition.admit(f.event, first), f.composition.admit(f.event, second)];
  for (let i = 0; i < 20 && waiting.size < 2; i++) await Promise.resolve();
  assert.equal(waiting.size, 2);
  const finished = f.composition.finish(f.event, first);
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(waiting.get(first.runId).signal.aborted, true);
  assert.equal(waiting.get(second.runId).signal.aborted, false);
  waiting.get(first.runId).resolve(null); await finished; await admissions[0];
  assert.equal(f.access.status().active, 1); assert.equal(f.access.status().phase, 'busy');
  const last = f.composition.finish(f.event, second);
  waiting.get(second.runId).resolve(null); await last; await admissions[1];
  assert.equal(f.access.status().phase, 'idle'); assert.equal(f.released.length, 2);
  await f.routing.shutdown();
});

for (const cleanupStarted of [false, true]) {
test('rejected owner transport retains unknown cleanup before/after cleanup snapshot: ' + cleanupStarted, options, async t => {
  const gate = deferred(); let started = false;
  const f = fixture(t, {acquire: () => ({...lease(), handoff: {id: 'stronger', previousProviderId: 'leader', kind: 'local',
    label: 'Stronger', model: 'stronger', baseUrl: 'http://127.0.0.1:8000/v1', revision: 1, scope: 'run'}}),
  authorize: () => {started = true; return gate.promise;}});
  const ctx = await f.select(), admission = f.composition.admit(f.event, ctx);
  for (let i = 0; i < 20 && !started; i++) await Promise.resolve();
  assert.equal(started, true);
  const finishing = cleanupStarted ? assert.rejects(f.composition.finish(f.event, ctx), /cleanup incomplete/) : null;
  for (let i = 0; i < 10; i++) await Promise.resolve();
  gate.reject(new Error('private owner transport failure'));
  assert.equal((await admission).outcome, 'block');
  if (finishing) await finishing;
  await assert.rejects(f.composition.finish(f.event, ctx), /cleanup incomplete/);
  assert.equal(f.access.status().phase, 'busy'); assert.equal(f.access.status().active, 1);
  await assert.rejects(f.routing.shutdown());
  assert.equal((await f.routing.beforeModelResolve({}, identity())).modelOverride, 'unavailable');
});
}
