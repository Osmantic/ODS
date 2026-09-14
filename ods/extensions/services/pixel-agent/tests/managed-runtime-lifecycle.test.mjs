// Deterministic registration/lifecycle tests. These are NOT installed journeys.
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createManagedRuntimeRegistry, DEPLOYMENT_ENV, requiredManagedHooks} from '../plugin/managed-runtime-lifecycle.mjs';
import {createManagedProviderBootstrap} from '../plugin/provider-bootstrap.mjs';
import {createManagedCommandAdmission} from '../plugin/managed-command-admission.mjs';

const context = () => ({agentId: 'pixel', runId: randomUUID(), sessionId: randomUUID(),
  sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64)});
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};
function fixture(options = {}) {
  const deployment = {binding: {schemaVersion: 1, activationId: randomUUID(), revision: 1, allowCloud: false},
    sourceRoot: '/owner/ods', hostPython: '/usr/bin/python3', providerDirectory: '/owner/private/providers',
    ownerScopes: true, leaseTimeoutSeconds: 180, approvalTimeoutSeconds: 60};
  let config = {agents: {defaults: {workspace: '/workspace'}, list: [{id: 'pixel', sandbox: {mode: 'all'},
    model: {primary: 'ods-policy/managed', fallbacks: []}}]},
    models: {providers: {'ods-policy': {baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions',
      apiKey: 'ods-policy-unavailable', models: [{id: 'managed', name: 'ODS managed', contextWindow: 32768,
        maxTokens: 4096, reasoning: false, input: ['text']}]}}},
    plugins: {entries: {'pixel-ods': {enabled: true, hooks: {allowConversationAccess: true},
      config: {managedProvider: structuredClone(deployment.binding)}}}}};
  const environment = {[DEPLOYMENT_ENV]: JSON.stringify(deployment), OPENCLAW_REQUIRED_PLUGINS:
    JSON.stringify({version: 1, plugins: [{id: 'pixel-ods', hooks: requiredManagedHooks}]})};
  const runs = new Set(), calls = [], hooks = new Map(), lifecycle = [];
  let held = false;
  const access = {status: () => ({available: true, phase: held ? 'held' : runs.size ? 'busy' : 'idle',
    active: runs.size, revision: 'synthetic-revision', proof: null}),
    isProbe: ctx => ctx?.runId === 'trusted-probe',
    admit(_event, ctx) {calls.push('access.admit'); if (held) return {outcome: 'block'}; runs.add(ctx.runId); return {outcome: 'pass'};},
    finish(_event, ctx) {calls.push('access.finish'); runs.delete(ctx?.runId);},
    owns: token => held && token === 'a'.repeat(64),
    acquire(token) {if (!held || token !== 'a'.repeat(64)) throw new Error('wrong hold'); return access.status();}};
  const makeApi = (mode = 'full') => ({registrationMode: mode,
    pluginConfig: config.plugins.entries['pixel-ods'].config,
    runtime: {config: {current: () => config}},
    registerProvider(provider) {calls.push('register.provider'); assert.ok(provider);},
    on(name, handler, settings) {calls.push('register.' + name); hooks.set(name, {handler, settings});},
    registerRuntimeLifecycle(value) {lifecycle.push(value);}});
  const routing = {provider: {id: 'ods-policy'}, beforeAgentRunOptions: {timeoutMs: 65000},
    async beforeModelResolve(event, ctx) {calls.push('routing.select'); return options.select ? options.select(event, ctx) :
      ctx.agentId === 'other' ? undefined : {providerOverride: 'ods-policy', modelOverride: 'synthetic-route'};},
    async beforeAgentRun(event, ctx) {calls.push('routing.admit'); return options.admit ? options.admit(event, ctx) : {outcome: 'pass'};},
    async agentEnd(event, ctx) {calls.push('routing.end'); await options.end?.(event, ctx);},
    shutdown() {calls.push('routing.shutdown'); return options.shutdown?.();}};
  let constructions = 0;
  const registry = createManagedRuntimeRegistry({environment, controlTimeoutMs: options.controlTimeoutMs ?? 50,
    createRouting: args => {constructions++; return options.real ? createManagedProviderBootstrap({...args,
      createLease: () => ({durableReplayGuard: true,
        async acquireLease() {calls.push('lease.acquire'); return {baseUrl: 'http://127.0.0.1:12345/v1', token: 'synthetic-key',
          contextTokens: 32768, maxOutputTokens: 4096, reasoning: false, supportsVision: false};},
        async releaseLease() {calls.push('lease.release'); await options.end?.();}}),
      createHandoff: () => async () => null}) : routing;},
    createCommands: args => createManagedCommandAdmission({...args, cleanupTimeoutMs: 25, pollIntervalMs: 1})});
  const api = makeApi();
  return {registry, api, access, calls, hooks, lifecycle, environment, deployment, runs,
    register: () => registry.register(api, access), makeApi,
    hold: () => {held = true;}, config: () => config,
    replaceConfig: value => {config = value;}, constructions: () => constructions};
}

test('legacy mode does not construct or register any managed surface', () => {
  const f = fixture(); delete f.environment[DEPLOYMENT_ENV]; delete f.api.pluginConfig.managedProvider;
  assert.equal(f.register(), null); assert.equal(f.constructions(), 0); assert.deepEqual(f.calls, []);
});

test('managed activation requires qualified access accounting, including native-platform refusal', () => {
  const f = fixture(); f.access.status = () => ({available: false, phase: 'unavailable', active: 0});
  assert.throws(f.register); assert.equal(f.constructions(), 0);
});

test('a held access owner blocks model selection before any provider work', async () => {
  const f = fixture(), owner = f.register(); f.hold();
  assert.equal((await owner.select({}, context())).modelOverride, 'unavailable');
  assert.equal(f.calls.includes('routing.select'), false); await owner.shutdown();
});

for (const missing of ['deployment', 'binding', 'required-policy', 'command-hook', 'current-api']) {
  test('incomplete activation refuses before worker construction: ' + missing, () => {
    const f = fixture();
    if (missing === 'deployment') delete f.environment[DEPLOYMENT_ENV];
    if (missing === 'binding') delete f.api.pluginConfig.managedProvider;
    if (missing === 'required-policy') delete f.environment.OPENCLAW_REQUIRED_PLUGINS;
    if (missing === 'command-hook') f.environment.OPENCLAW_REQUIRED_PLUGINS = JSON.stringify({version: 1,
      plugins: [{id: 'pixel-ods', hooks: requiredManagedHooks.filter(hook => hook !== 'before_command_run')}]});
    if (missing === 'current-api') delete f.api.runtime.config.current;
    assert.throws(f.register, /managed runtime unavailable/); assert.equal(f.constructions(), 0);
  });
}

test('metadata passes neither instantiate nor replace or close the active owner', () => {
  const f = fixture(); assert.equal(f.registry.register(f.makeApi('discovery'), f.access), null);
  const owner = f.register();
  assert.equal(f.registry.register(f.makeApi('cli-metadata'), {}), null);
  assert.equal(f.registry.register(f.makeApi(), f.access), owner);
  assert.equal(f.constructions(), 1); assert.equal(owner.valid(), true);
});

test('one owner per module generation; same registry API is registered once', () => {
  const f = fixture(), owner = f.register(), count = f.calls.length;
  assert.equal(f.register(), owner); assert.equal(f.calls.length, count);
  assert.deepEqual([...f.hooks.keys()], ['before_model_resolve', 'before_agent_run', 'agent_end', 'before_command_run', 'gateway_stop']);
  assert.equal(f.lifecycle.length, 1);
  assert.equal(f.hooks.get('before_command_run').settings.timeoutMs, 6000);
});

test('registration readback reports the registered binding without mutable aliases or private deployment', async () => {
  const f = fixture(), owner = f.register(); f.hold();
  const result = owner.readRegistration();
  assert.deepEqual(result, {status: 'active', binding: f.deployment.binding});
  assert.deepEqual(Object.keys(result).sort(), ['binding', 'status']);
  result.binding.revision = 999;
  assert.equal(owner.readRegistration().binding.revision, 1);
  assert.equal(JSON.stringify(result).includes('/owner/'), false);
  await owner.shutdown();
  assert.throws(() => owner.readRegistration());
});

test('registration readback refuses current config drift rather than echoing a new binding', async () => {
  const f = fixture(), owner = f.register();
  const changed = structuredClone(f.config());
  changed.plugins.entries['pixel-ods'].config.managedProvider.revision = 2;
  f.replaceConfig(changed);
  assert.throws(() => owner.readRegistration());
  await owner.shutdown();
});

test('registration readback refuses active provider ownership', async () => {
  const f = fixture(), owner = f.register(), ctx = context();
  await owner.select({}, ctx);
  assert.throws(() => owner.readRegistration());
  await owner.finish({}, ctx);
  assert.equal(owner.readRegistration().status, 'active');
  await owner.shutdown();
});

test('current runtime snapshot drift poisons the owner; restoration cannot revive it', async () => {
  const f = fixture(), owner = f.register(), old = f.config();
  const changed = structuredClone(old); changed.agents.list[0].sandbox.mode = 'off'; f.replaceConfig(changed);
  assert.equal(owner.valid(), false); f.replaceConfig(old);
  assert.equal(owner.valid(), false); assert.throws(f.register); await owner.shutdown();
});

function changeBinding(f) {
  const changed = structuredClone(f.config());
  changed.plugins.entries['pixel-ods'].config.managedProvider.revision++;
  f.replaceConfig(changed);
}

test('existing held management channel survives drained config invalidation, not provider admission', async () => {
  const f = fixture(), owner = f.register(); f.hold(); changeBinding(f);
  assert.equal(owner.status().available, false);
  const status = await owner.readControlStatus();
  assert.equal(status.available, true); assert.equal(status.phase, 'held'); assert.equal(status.active, 0);
  assert.equal((await owner.acquireTransition('a'.repeat(64), status.revision)).phase, 'held');
  await assert.rejects(owner.acquireTransition('b'.repeat(64), status.revision));
  assert.throws(owner.assertTransition); assert.throws(() => owner.readRegistration());
  assert.equal((await owner.select({}, context())).modelOverride, 'unavailable');
  assert.equal((await owner.admit({}, context())).outcome, 'block');
  assert.equal(owner.beforeCommandRun({commandId: 'after-drift'}, {}).action, 'block');
});

test('invalid idle owner cannot gain a new management hold', async () => {
  const f = fixture(), owner = f.register(); changeBinding(f);
  assert.equal((await owner.readControlStatus()).available, false);
  await assert.rejects(owner.acquireTransition('a'.repeat(64), 'b'.repeat(64)));
  assert.equal(f.access.status().phase, 'idle'); await owner.shutdown();
});

test('held management recovery waits for the same routing shutdown to settle', async () => {
  const gate = deferred(), f = fixture({shutdown: () => gate.promise}), owner = f.register();
  f.hold(); changeBinding(f); let done = false;
  const pending = owner.readControlStatus().then(value => {done = true; return value;});
  await new Promise(resolve => setImmediate(resolve)); assert.equal(done, false);
  gate.resolve(); assert.equal((await pending).phase, 'held');
  assert.equal(f.calls.filter(call => call === 'routing.shutdown').length, 1);
});

test('rejected and bounded hanging cleanup never claim held recovery available', async () => {
  for (const failure of ['reject', 'hang']) {
    const gate = deferred(), f = fixture({shutdown: () => gate.promise, controlTimeoutMs: 5}), owner = f.register();
    f.hold(); changeBinding(f);
    const pending = owner.readControlStatus();
    if (failure === 'reject') gate.reject(new Error('cleanup unknown'));
    assert.equal((await pending).available, false);
    if (failure === 'reject') await assert.rejects(owner.acquireTransition('a'.repeat(64), 'b'.repeat(64)));
    else {gate.resolve(); await owner.shutdown();}
  }
});

for (const mutation of [{revision: 'another-revision'}, {pid: 98765}, {phase: 'idle'}, {available: false}, {active: 1}]) {
  test('changed hold during cleanup cannot be accepted: ' + JSON.stringify(mutation), async () => {
    const gate = deferred(), f = fixture({shutdown: () => gate.promise}), owner = f.register();
    f.hold(); changeBinding(f); const pending = owner.readControlStatus();
    const previous = f.access.status;
    f.access.status = () => ({...previous(), ...mutation});
    gate.resolve(); assert.equal((await pending).available, false);
  });
}

test('valid managed acquire still delegates token validation to the existing access owner', async () => {
  const f = fixture(), owner = f.register(); f.hold();
  await assert.rejects(owner.acquireTransition('b'.repeat(64), 'b'.repeat(64)));
  assert.equal((await owner.acquireTransition('a'.repeat(64), 'b'.repeat(64))).phase, 'held');
  assert.equal(owner.valid(), true); await owner.shutdown();
});

test('token ownership lost during cleanup refuses reacquire despite an unchanged status projection', async () => {
  const gate = deferred(), f = fixture({shutdown: () => gate.promise}), owner = f.register();
  f.hold(); changeBinding(f);
  const pending = owner.acquireTransition('a'.repeat(64), 'b'.repeat(64));
  f.access.owns = () => false;
  gate.resolve(); await assert.rejects(pending);
});

test('unknown command cleanup remains unavailable even if external accounting drops its slot', async () => {
  const f = fixture(), owner = f.register();
  const command = owner.beforeCommandRun({commandId: 'unknown'}, {});
  await assert.rejects(command.finish({commandId: 'unknown', inspectProcess: () => ({state: 'unknown'}), cancelProcess() {}}));
  f.runs.clear(); f.hold(); changeBinding(f);
  assert.equal((await owner.readControlStatus()).available, false);
  await assert.rejects(owner.acquireTransition('a'.repeat(64), 'b'.repeat(64)));
  await assert.rejects(owner.shutdown());
});

test('active provider reservations cannot be hidden by an externally asserted hold', async () => {
  const gate = deferred(), f = fixture({shutdown: () => gate.promise}), owner = f.register();
  await owner.select({}, context()); f.hold(); changeBinding(f);
  assert.equal((await owner.readControlStatus()).available, false);
  gate.resolve(); await owner.shutdown();
});

test('held recovery status never revives the old provider when its config is restored', async () => {
  const f = fixture(), owner = f.register(), before = structuredClone(f.config());
  f.hold(); changeBinding(f); await owner.readControlStatus(); f.replaceConfig(before);
  assert.equal(owner.valid(), false); assert.throws(() => owner.readRegistration());
  assert.throws(f.register);
});

test('unrelated logging and other-agent configuration do not invalidate the policy', async () => {
  const f = fixture(), owner = f.register(); f.config().logging = {level: 'debug'};
  f.config().agents.list.push({id: 'other', model: 'another/model'});
  assert.equal(owner.valid(), true); await owner.shutdown();
});

test('deployment removal, required-hook drift and replacement access owner are refused', async () => {
  for (const drift of ['environment', 'hooks', 'owner']) {
    const f = fixture(), owner = f.register();
    if (drift === 'environment') delete f.environment[DEPLOYMENT_ENV];
    if (drift === 'hooks') f.environment.OPENCLAW_REQUIRED_PLUGINS = '{}';
    assert.throws(() => f.registry.register(f.api, drift === 'owner' ? {...f.access} : f.access));
    assert.equal(owner.valid(), false); await owner.shutdown();
  }
});

test('selection blocks access transition before and after asynchronous lease acquisition', async () => {
  const gate = deferred(), f = fixture({select: () => gate.promise}), owner = f.register(), ctx = context();
  const selection = owner.select({}, ctx); assert.throws(owner.assertTransition);
  assert.equal(owner.status().phase, 'busy'); assert.equal(f.access.status().phase, 'idle');
  gate.resolve({providerOverride: 'ods-policy', modelOverride: 'route'}); await selection;
  assert.throws(owner.assertTransition); await owner.finish({runId: ctx.runId}, ctx);
  assert.doesNotThrow(owner.assertTransition); await owner.shutdown();
});

test('failed selection cleans its reservation even without a subsequent admission/end event', async () => {
  const f = fixture({select: () => {throw new Error('synthetic lease failure');}}), owner = f.register(), ctx = context();
  assert.equal((await owner.select({}, ctx)).modelOverride, 'unavailable');
  assert.doesNotThrow(owner.assertTransition); assert.equal(f.calls.filter(x => x === 'routing.end').length, 1);
  await owner.shutdown();
});

test('provider refusal closes lease before clearing access; later end remains idempotent', async () => {
  const f = fixture({admit: () => ({outcome: 'block'})}), owner = f.register(), ctx = context();
  await owner.select({}, ctx); assert.equal((await owner.admit({}, ctx)).outcome, 'block');
  assert.equal(f.runs.size, 0); assert.deepEqual(f.calls.slice(-2), ['routing.end', 'access.finish']);
  await owner.finish({runId: ctx.runId}, ctx); assert.doesNotThrow(owner.assertTransition);
  await owner.shutdown();
});

test('unknown lease cleanup is sticky; a retry cannot incorrectly clear access', async () => {
  let ends = 0;
  const f = fixture({admit: () => ({outcome: 'block'}), end: () => {if (++ends === 1) throw new Error('unknown exit');}});
  const owner = f.register(), ctx = context(); await owner.select({}, ctx);
  assert.equal((await owner.admit({}, ctx)).outcome, 'block');
  assert.equal(ends, 1); assert.equal(f.runs.size, 1);
  await assert.rejects(owner.finish({}, ctx)); assert.equal(f.runs.size, 1);
  assert.equal(owner.status().available, false); assert.throws(owner.assertTransition);
  await owner.shutdown();
});

test('held access denial cleans selected lease without clearing the owner transition', async () => {
  const f = fixture(), owner = f.register(), ctx = context(); await owner.select({}, ctx); f.hold();
  assert.equal((await owner.admit({}, ctx)).outcome, 'block');
  assert.equal(f.access.status().phase, 'held'); assert.equal(f.runs.size, 0); await owner.shutdown();
});

test('only the existing access owner can identify a probe; untrusted hints cannot bypass routing', async () => {
  const f = fixture(), owner = f.register(), trusted = {...context(), runId: 'trusted-probe'};
  assert.equal(await owner.select({}, trusted), undefined); await owner.admit({}, trusted); await owner.finish({}, trusted);
  assert.equal(f.calls.some(call => call.startsWith('routing.') && call !== 'routing.shutdown'), false);
  const forged = {...context(), isProbe: true, probe: true}; await owner.select({}, forged); await owner.admit({}, forged);
  assert.ok(f.calls.includes('routing.select')); assert.ok(f.calls.includes('routing.admit'));
  await owner.finish({}, forged); await owner.shutdown();
});

test('foreign session or mutated original context cannot release a selected run', async () => {
  const f = fixture(), owner = f.register(), ctx = context(); await owner.select({}, ctx); await owner.admit({}, ctx);
  ctx.sessionId = randomUUID(); await assert.rejects(owner.finish({}, ctx));
  assert.equal(f.runs.size, 1); assert.equal(owner.status().available, false); await owner.shutdown();
});

test('session cleanup refuses live matching work without ending unrelated runs', async () => {
  const f = fixture(), owner = f.register(), ctx = context(); await owner.select({}, ctx); await owner.admit({}, ctx);
  assert.throws(() => owner.cleanup({reason: 'reset', sessionKey: ctx.sessionKey}));
  owner.cleanup({reason: 'delete', sessionKey: 'unrelated'});
  assert.equal(owner.valid(), true); assert.equal(f.runs.size, 1);
  await owner.finish({}, ctx); owner.cleanup({reason: 'reset', sessionKey: ctx.sessionKey}); await owner.shutdown();
});

test('real command adapter shares the access owner and retains unknown process activity', async () => {
  const f = fixture(), owner = f.register();
  const admitted = owner.beforeCommandRun({commandId: 'owned-command'}, {});
  assert.equal(admitted.action, 'allow'); assert.equal(f.runs.size, 1); assert.throws(owner.assertTransition);
  const pending = admitted.finish({commandId: 'owned-command', inspectProcess: () => ({state: 'unknown'}), cancelProcess() {}});
  await assert.rejects(pending); assert.equal(f.runs.size, 1); assert.equal(owner.status().available, false);
  await assert.rejects(owner.shutdown());
});

test('clean command process proof releases exactly its reservation and permits transition', async () => {
  const f = fixture(), owner = f.register();
  const admission = owner.beforeCommandRun({commandId: 'command'}, {});
  await admission.finish({commandId: 'command', inspectProcess: () => ({state: 'exited', scope: 'process-group', pid: 123}), cancelProcess() {}});
  assert.equal(f.runs.size, 0); assert.doesNotThrow(owner.assertTransition);
  const first = owner.shutdown(); assert.equal(owner.shutdown(), first); await first;
  assert.equal(owner.beforeCommandRun({commandId: 'late'}, {}).action, 'block'); assert.throws(f.register);
});

test('production bootstrap and production command adapter compose through registered hooks', async () => {
  const f = fixture({real: true}), owner = f.register(), ctx = context();
  const route = await f.hooks.get('before_model_resolve').handler({}, ctx);
  const resolved = {...ctx, modelProviderId: route.providerOverride, modelId: route.modelOverride};
  assert.equal((await f.hooks.get('before_agent_run').handler({prompt: 'Synthetic'}, resolved)).outcome, 'pass');
  await f.hooks.get('agent_end').handler({runId: ctx.runId}, resolved);
  assert.equal(f.calls.filter(x => x === 'lease.acquire').length, 1);
  assert.equal(f.calls.filter(x => x === 'lease.release').length, 1);
  assert.equal(f.runs.size, 0); assert.doesNotThrow(owner.assertTransition); await owner.shutdown();
});
