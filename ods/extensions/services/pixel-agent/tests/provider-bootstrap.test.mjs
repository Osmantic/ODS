import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createManagedProviderBootstrap, managedProviderRequiredHooks } from '../plugin/provider-bootstrap.mjs';

const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; };
const ctx = () => ({agentId: 'pixel', runId: randomUUID(), sessionId: randomUUID(),
  sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64)});
const lease = () => ({baseUrl: 'http://127.0.0.1:12345/v1', token: 'private-test-token',
  contextTokens: 32768, maxOutputTokens: 4096, reasoning: false, supportsVision: false});
function fixture({changeConfig, changeDeployment, acquire, release, authorize} = {}) {
  const deployment = {binding: {schemaVersion: 1, activationId: randomUUID(), revision: 7, allowCloud: false},
    sourceRoot: '/owner/ods', hostPython: '/usr/bin/python3', providerDirectory: '/owner/private/providers',
    ownerScopes: true, leaseTimeoutSeconds: 180, approvalTimeoutSeconds: 60};
  let config = {agents: {defaults: {workspace: '/workspace'}, list: [{id: 'pixel',
    model: {primary: 'ods-policy/managed', fallbacks: []}, sandbox: {mode: 'all'}}]},
  models: {providers: {'ods-policy': {baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions',
    apiKey: 'ods-policy-unavailable', models: [{id: 'managed', name: 'ODS managed', contextWindow: 32768,
      maxTokens: 4096, reasoning: false, input: ['text']}]}}},
  plugins: {entries: {'pixel-ods': {enabled: true, hooks: {allowConversationAccess: true},
    config: {managedProvider: clone(deployment.binding)}}}}};
  changeConfig?.(config); changeDeployment?.(deployment);
  const acquisitions = [], releases = [], calls = [], policies = [];
  let leaseOptions, handoffOptions;
  const bootstrap = createManagedProviderBootstrap({deployment, readConfig: () => config,
    createLease: options => {
      leaseOptions = options;
      return {durableReplayGuard: true,
        acquireLease: async value => {acquisitions.push(value); policies.push(options.request(value)); return acquire ? await acquire(value) : lease();},
        releaseLease: async value => {releases.push(value); await release?.(value);}};
    },
    createHandoff: options => {handoffOptions = options; return authorize ?? (async () => null);},
  });
  async function prepare(context = ctx()) {
    const selected = await bootstrap.beforeModelResolve({}, context);
    const resolved = {...context, modelProviderId: selected.providerOverride, modelId: selected.modelOverride};
    const model = bootstrap.provider.resolveDynamicModel(resolved);
    const stream = bootstrap.provider.wrapStreamFn({...resolved, streamFn: (...args) => {calls.push(args); return 'stream';}});
    return {context, selected, resolved, send: () => stream(model, {messages: [], tools: []}, {})};
  }
  return {bootstrap, deployment, get config() {return config;}, set config(value) {config = value;},
    leaseOptions, handoffOptions, acquisitions, releases, calls, policies, prepare};
}

test('owner bootstrap composes fixed private commands and frozen policy without writes or activation', async () => {
  const f = fixture(), initial = clone(f.config), run = await f.prepare();
  assert.deepEqual(f.leaseOptions.command, ['/usr/bin/python3', '-I', '-B', '/owner/ods/bin/ods-pixel-route-lease']);
  assert.deepEqual(f.handoffOptions.command, ['/usr/bin/python3', '-I', '-B', '/owner/ods/bin/pixel_provider/handoff_worker.py']);
  assert.equal(f.leaseOptions.directory, '/owner/private/providers');
  assert.equal(f.leaseOptions.ownerScopes, true);
  assert.equal(f.handoffOptions.timeoutSeconds, 60);
  assert.deepEqual(f.policies, [{expectedRevision: 7, allowCloud: false, confirmed: true, timeoutSeconds: 180}]);
  assert.equal(Object.isFrozen(f.policies[0]), true);
  assert.deepEqual(f.acquisitions[0], {runId: run.context.runId, sessionId: run.context.sessionId, sessionKey: run.context.sessionKey});
  assert.deepEqual(f.bootstrap.requiredHooks, managedProviderRequiredHooks);
  assert.deepEqual(f.bootstrap.beforeAgentRunOptions, {timeoutMs: 65000});
  assert.equal((await f.bootstrap.beforeAgentRun({}, run.resolved)).outcome, 'pass');
  assert.equal(run.send(), 'stream');
  assert.deepEqual(f.config, initial);
  await f.bootstrap.agentEnd({}, run.context);
  assert.equal(f.releases.length, 1);
  assert.throws(run.send, /unavailable/);
  await f.bootstrap.shutdown();
});

const normalizedPlaceholder = config => Object.assign(config.models.providers['ods-policy'].models[0],
  {cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}, api: 'openai-completions'});

test('exact runtime-normalized placeholder admits and cleans up without editing config', async () => {
  const f = fixture({changeConfig: normalizedPlaceholder}), initial = clone(f.config);
  const run = await f.prepare();
  assert.equal((await f.bootstrap.beforeAgentRun({}, run.resolved)).outcome, 'pass');
  assert.equal(run.send(), 'stream');
  await f.bootstrap.agentEnd({}, run.context);
  assert.equal(f.releases.length, 1); assert.deepEqual(f.config, initial);
  await f.bootstrap.shutdown();
});

for (const [name, change] of [
  ['endpoint', p => p.baseUrl = 'http://127.0.0.1:8000/v1'],
  ['credential', p => p.apiKey = 'unexpected-key'],
  ['provider API', p => p.api = 'openai-responses'],
  ['provider headers', p => p.headers = {Authorization: 'unexpected'}],
  ['model ID', p => p.models[0].id = 'other'],
  ['model name', p => p.models[0].name = 'other'],
  ['additional model', p => p.models.push(clone(p.models[0]))],
  ['model API', p => p.models[0].api = 'openai-responses'],
  ['model headers', p => p.models[0].headers = {Authorization: 'unexpected'}],
  ['model context', p => p.models[0].contextWindow++],
  ['model reasoning', p => p.models[0].reasoning = true],
  ['nonzero cost', p => p.models[0].cost.input = 1],
  ['cost type', p => p.models[0].cost.input = '0'],
  ['extra cost', p => p.models[0].cost.extra = 0],
  ['missing cost', p => delete p.models[0].cost.cacheWrite],
  ['only cost', p => delete p.models[0].api],
  ['only API', p => delete p.models[0].cost],
]) test('normalized placeholder still rejects unexpected ' + name, () => {
  assert.throws(() => fixture({changeConfig: config => {
    normalizedPlaceholder(config); change(config.models.providers['ods-policy']);
  }}), /unavailable/);
});

test('normalized placeholder authority drift revokes a prepared route and cannot revive it', async () => {
  const f = fixture({changeConfig: normalizedPlaceholder}), initial = clone(f.config), run = await f.prepare();
  f.config.models.providers['ods-policy'].models[0].api = 'openai-responses';
  assert.equal((await f.bootstrap.beforeAgentRun({}, run.resolved)).outcome, 'block');
  f.config = initial;
  assert.equal((await f.bootstrap.beforeModelResolve({}, ctx())).modelOverride, 'unavailable');
  assert.throws(run.send, /unavailable/); await f.bootstrap.shutdown();
});

for (const [name, change] of [
  ['revision', c => c.plugins.entries['pixel-ods'].config.managedProvider.revision++],
  ['activation identity', c => c.plugins.entries['pixel-ods'].config.managedProvider.activationId = randomUUID()],
  ['cloud consent', c => c.plugins.entries['pixel-ods'].config.managedProvider.allowCloud = true],
  ['extra binding field', c => c.plugins.entries['pixel-ods'].config.managedProvider.command = '/bin/sh'],
  ['missing agent', c => c.agents.list = []],
  ['duplicate agent', c => c.agents.list.push(clone(c.agents.list[0]))],
  ['disabled plugin', c => c.plugins.entries['pixel-ods'].enabled = false],
  ['conversation hooks', c => c.plugins.entries['pixel-ods'].hooks.allowConversationAccess = false],
  ['primary model', c => c.agents.list[0].model.primary = 'legacy/model'],
  ['native fallback', c => c.agents.list[0].model.fallbacks.push('legacy/model')],
  ['reachable route', c => c.models.providers['ods-policy'].baseUrl = 'http://127.0.0.1:8000/v1'],
  ['placeholder credentials', c => c.models.providers['ods-policy'].apiKey = 'real-key'],
  ['provider headers', c => c.models.providers['ods-policy'].headers = {Authorization: 'unsafe'}],
  ['global provider tools', c => c.tools = {byProvider: {legacy: {allow: ['exec']}}}],
  ['pixel provider tools', c => c.agents.list[0].tools = {byProvider: {legacy: {allow: ['exec']}}}],
]) test('reject invalid activation before acquiring a worker: ' + name, () => {
  assert.throws(() => fixture({changeConfig: change}), /unavailable/);
});

for (const [name, change] of [
  ['invalid UUID', d => d.binding.activationId = 'not-a-uuid'],
  ['unsafe revision', d => d.binding.revision = 2 ** 53],
  ['boolean revision', d => d.binding.revision = true],
  ['cloud type', d => d.binding.allowCloud = 'true'],
  ['extra command', d => d.command = ['/bin/sh']],
  ['root source', d => d.sourceRoot = '/'],
  ['relative Python', d => d.hostPython = 'python3'],
  ['path traversal', d => d.sourceRoot = '/owner/../untrusted'],
  ['NUL directory', d => d.providerDirectory += '\0'],
  ['scope type', d => d.ownerScopes = 1],
  ['approval too long', d => d.approvalTimeoutSeconds = 121],
  ['zero approval', d => d.approvalTimeoutSeconds = 0],
  ['lease too long', d => d.leaseTimeoutSeconds = 3601],
  ['lease shorter than review', d => d.leaseTimeoutSeconds = d.approvalTimeoutSeconds],
]) test('reject malformed owner deployment: ' + name, () => {
  assert.throws(() => fixture({changeDeployment: change}), /unavailable/);
});

test('mutation of caller deployment cannot repoint executable or widen a lease', async () => {
  const f = fixture();
  f.deployment.hostPython = '/bin/sh'; f.deployment.binding.allowCloud = true; f.deployment.binding.revision = 99;
  const run = await f.prepare();
  assert.equal(f.leaseOptions.command[0], '/usr/bin/python3');
  assert.equal(f.policies[0].allowCloud, false); assert.equal(f.policies[0].expectedRevision, 7);
  await f.bootstrap.agentEnd({}, run.context);
});

test('unrelated owner edits and other agent policies are preserved, not globally rejected', async () => {
  const f = fixture();
  f.config.logging = {level: 'debug'};
  f.config.agents.list.push({id: 'other', tools: {byProvider: {local: {allow: ['exec']}}}});
  const run = await f.prepare();
  assert.equal((await f.bootstrap.beforeAgentRun({}, run.resolved)).outcome, 'pass');
  assert.equal(await f.bootstrap.beforeModelResolve({}, {agentId: 'other'}), undefined);
  assert.equal(await f.bootstrap.beforeAgentRun({}, {agentId: 'other'}), undefined);
  assert.throws(() => f.bootstrap.provider.resolveDynamicModel({...run.resolved, agentId: 'other'}), /unavailable/);
  await f.bootstrap.shutdown();
});

for (const change of [
  c => c.plugins.entries['pixel-ods'].config.managedProvider.revision++,
  c => c.agents.list[0].sandbox.mode = 'off',
  c => c.agents.defaults.workspace = '/new-workspace',
  c => c.tools = {exec: {host: 'gateway'}},
]) test('drift between admission and actual stream revokes all routes and cannot revive', async () => {
  const f = fixture(), initial = clone(f.config), run = await f.prepare();
  assert.equal((await f.bootstrap.beforeAgentRun({}, run.resolved)).outcome, 'pass');
  change(f.config);
  assert.throws(run.send, /unavailable/);
  f.config = initial;
  assert.throws(run.send, /unavailable/);
  assert.equal((await f.bootstrap.beforeModelResolve({}, ctx())).modelOverride, 'unavailable');
  assert.equal((await f.bootstrap.beforeAgentRun({}, run.resolved)).outcome, 'block');
  await assert.rejects(f.bootstrap.provider.prepareRuntimeAuth(run.resolved), /unavailable/);
  assert.throws(() => f.bootstrap.provider.resolveDynamicModel(run.resolved), /unavailable/);
  await f.bootstrap.agentEnd({}, run.context); await f.bootstrap.shutdown();
  assert.equal(f.releases.length, 1); assert.equal(f.calls.length, 0);
});

test('drift while lease acquisition is pending releases the late lease', async () => {
  const gate = deferred(), f = fixture({acquire: () => gate.promise}), context = ctx();
  const selection = f.bootstrap.beforeModelResolve({}, context);
  await Promise.resolve(); await Promise.resolve();
  f.config.plugins.entries['pixel-ods'].config.managedProvider.revision++;
  assert.equal((await f.bootstrap.beforeAgentRun({}, context)).outcome, 'block');
  gate.resolve(lease());
  assert.equal((await selection).modelOverride, 'unavailable');
  await f.bootstrap.shutdown();
  assert.equal(f.acquisitions.length, 1); assert.equal(f.releases.length, 1);
});

test('parent access denial cleanup releases selected route without calling provider admission', async () => {
  const f = fixture(), run = await f.prepare();
  await f.bootstrap.agentEnd({}, run.context);
  assert.throws(run.send, /unavailable/);
  assert.equal(f.releases.length, 1); assert.equal(f.calls.length, 0);
  await f.bootstrap.shutdown();
});

test('shutdown immediately revokes all routes, awaits cleanup, and is idempotent', async () => {
  const gate = deferred(), f = fixture({release: () => gate.promise});
  const one = await f.prepare(), two = await f.prepare();
  const stopped = f.bootstrap.shutdown();
  assert.equal(stopped, f.bootstrap.shutdown());
  assert.throws(one.send, /unavailable/); assert.throws(two.send, /unavailable/);
  assert.equal((await f.bootstrap.beforeModelResolve({}, ctx())).modelOverride, 'unavailable');
  let finished = false; void stopped.then(() => {finished = true;});
  await Promise.resolve(); assert.equal(finished, false);
  gate.resolve(); await stopped;
  assert.equal(f.releases.length, 2);
});

test('failed cleanup is not reported as successful rollback and stays closed', async () => {
  const f = fixture({release: () => {throw new Error('cleanup unknown');}}), run = await f.prepare();
  await assert.rejects(f.bootstrap.agentEnd({}, run.context), /cleanup incomplete/);
  await assert.rejects(f.bootstrap.shutdown(), /cleanup incomplete/);
  assert.throws(run.send, /unavailable/);
  await assert.rejects(f.bootstrap.shutdown(), /cleanup incomplete/);
});

test('shutdown waits for owner transport exit, not only the faster aborted approval decision', async () => {
  const gate = deferred(); let approvalSignal;
  const f = fixture({acquire: () => ({...lease(), handoff: {id: 'stronger', previousProviderId: 'leader',
    kind: 'local', label: 'Stronger', model: 'stronger', baseUrl: 'http://127.0.0.1:8000/v1', revision: 7, scope: 'run'}}),
  authorize: ({signal}) => {approvalSignal = signal; return gate.promise;}});
  const run = await f.prepare();
  const admission = f.bootstrap.beforeAgentRun({prompt: 'Continue', systemPrompt: 'Existing rules', messages: []},
    {...run.resolved, workspaceDir: '/workspace'});
  for (let i = 0; i < 10 && !approvalSignal; i++) await Promise.resolve();
  assert.ok(approvalSignal);
  const stopped = f.bootstrap.shutdown();
  assert.equal(approvalSignal.aborted, true);
  assert.equal((await admission).outcome, 'block');
  let finished = false; void stopped.then(() => {finished = true;});
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(finished, false, 'owner process still closing');
  gate.resolve(null); await stopped;
  assert.throws(run.send, /unavailable/);
});

test('configuration read failure fails closed without forwarding private error text', async () => {
  const f = fixture(), run = await f.prepare();
  f.config = null;
  const result = await f.bootstrap.beforeAgentRun({}, run.resolved);
  assert.equal(result.outcome, 'block');
  assert.doesNotMatch(JSON.stringify(result), /private-test-token/);
  await f.bootstrap.shutdown();
});
