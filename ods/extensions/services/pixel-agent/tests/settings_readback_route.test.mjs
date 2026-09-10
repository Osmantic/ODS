import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createManagedRuntimeRegistry, DEPLOYMENT_ENV, requiredManagedHooks} from '../plugin/managed-runtime-lifecycle.mjs';
import {readRuntimeSettings} from '../plugin/settings-runtime-readback.mjs';
import {createManagedProviderBootstrap} from '../plugin/provider-bootstrap.mjs';

// Execute the real registration block with controlled owners. Gateway auth and
// installed restart/readback still require real integration qualification.
const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
const start = source.indexOf('    api.registerHttpRoute({path: "/pixel-ods/access-runtime"');
const end = source.indexOf('    api.on("tool_result_persist"', start);
assert.ok(start >= 0 && end > start);
function setup(deny = false) {
  let route;
  const calls = [], result = {source: 'current-runtime-config'};
  vm.runInNewContext(source.slice(start, end), {
    api: {registerHttpRoute(value) { route = value; }},
    managedRuntime: {assertTransition() { calls.push('owner'); if (deny) throw new Error(); }, readControlStatus: () => ({available: true})},
    accessRuntime: {readSettings(token, revision) { calls.push(['read', token, revision]); return result; }},
    sendJson(res, status, body) { Object.assign(res, {status, body}); },
  });
  return {route, calls, result};
}
async function request(route, body, method = 'POST') {
  const res = {};
  const req = {url: '/pixel-ods/access-runtime', method, async *[Symbol.asyncIterator]() { yield Buffer.from(JSON.stringify(body)); }};
  await route.handler(req, res);
  return res;
}
const body = {operation: 'settings-readback', token: 'a'.repeat(64), revision: 'b'.repeat(64)};

test('readback composes existing authenticated route and managed owner check', async () => {
  const {route, calls, result} = setup();
  assert.equal(route.auth, 'gateway');
  assert.equal(route.match, 'exact');
  const res = await request(route, body);
  assert.equal(res.status, 200);
  assert.equal(res.body, result);
  assert.deepEqual(calls, ['owner', ['read', body.token, body.revision]]);
});
test('busy managed owner prevents readback without reaching config', async () => {
  const {route, calls} = setup(true);
  assert.equal((await request(route, body)).status, 409);
  assert.deepEqual(calls, ['owner']);
});
test('unknown fields and malformed identities fail before owner or readback', async () => {
  for (const changed of [{...body, path: '/private'}, {...body, token: 'bad'}, {...body, revision: null}]) {
    const {route, calls} = setup();
    assert.equal((await request(route, changed)).status, 409);
    assert.deepEqual(calls, []);
  }
});
test('GET remains status-only and does not expose settings or enter transition', async () => {
  const {route, calls} = setup();
  const res = await request(route, null, 'GET');
  assert.equal(res.status, 200);
  assert.deepEqual(calls, []);
  assert.equal(res.body.source, undefined);
});

function providerSetup({managed = true, stale = false, busy = false} = {}) {
  let route;
  const calls = [];
  const binding = {schemaVersion: 1, activationId: '00000000-0000-4000-8000-000000000001', revision: 4, allowCloud: false};
  const settings = {pid: 1234, runtimeVersion: '2026.6.33', revision: 'b'.repeat(64),
    observedAt: '2026-09-09T13:00:00.000Z', fields: {neverExpose: 'private-setting'}};
  vm.runInNewContext(source.slice(start, end), {
    api: {registerHttpRoute(value) { route = value; }},
    managedRuntime: managed ? {
      assertTransition() { calls.push('owner'); if (busy) throw new Error(); },
      readRegistration() { calls.push('registration'); return {status: 'active', binding}; },
    } : null,
    accessRuntime: {readSettings(token, revision) {
      calls.push(['snapshot', token, revision]); if (stale) throw new Error(); return settings;
    }},
    sendJson(res, status, body) { Object.assign(res, {status, body}); },
  });
  return {route, calls, binding, settings};
}

test('provider readback uses owned current-process snapshot and distinguishes registration from inference', async () => {
  const f = providerSetup();
  const res = await request(f.route, {...body, operation: 'provider-readback'});
  assert.equal(f.route.auth, 'gateway');
  assert.equal(res.status, 200);
  assert.deepEqual(JSON.parse(JSON.stringify(res.body)), {schemaVersion: 1, source: 'current-provider-registration',
    pid: f.settings.pid, runtimeVersion: f.settings.runtimeVersion, revision: f.settings.revision,
    observedAt: f.settings.observedAt, registration: {status: 'active', binding: f.binding}, transportVerified: false});
  assert.deepEqual(f.calls, ['owner', ['snapshot', body.token, body.revision], 'registration']);
  assert.equal(JSON.stringify(res.body).includes('private-setting'), false);
});

test('legacy provider readback explicitly reports no managed registration', async () => {
  const f = providerSetup({managed: false});
  const res = await request(f.route, {...body, operation: 'provider-readback'});
  assert.equal(res.status, 200);
  assert.deepEqual(JSON.parse(JSON.stringify(res.body.registration)), {status: 'inactive', binding: null});
  assert.equal(res.body.transportVerified, false);
});

test('provider readback refuses busy or stale authority without reading registration', async () => {
  for (const options of [{busy: true}, {stale: true}, {managed: false, stale: true}]) {
    const f = providerSetup(options);
    assert.equal((await request(f.route, {...body, operation: 'provider-readback'})).status, 409);
    assert.equal(f.calls.includes('registration'), false);
  }
});

test('provider readback admits no private paths or additional caller fields', async () => {
  const f = providerSetup();
  assert.equal((await request(f.route, {...body, operation: 'provider-readback', path: '/private'})).status, 409);
  assert.deepEqual(f.calls, []);
});

test('registration supplies the SDK current snapshot lazily, separate from startup probe config', () => {
  const begin = source.indexOf('    accessRuntime ??= createAccessRuntime(');
  const finish = source.indexOf('    const managedRuntime =', begin);
  assert.ok(begin >= 0 && finish > begin);
  const startup = {generation: 'startup'};
  let current = {generation: 'current'}, reads = 0;
  const context = {api: {config: startup, runtime: {config: {current() { reads++; return current; }}}},
    accessRuntime: undefined, createAccessRuntime: options => options,
    createOpenClawCodingTools() {}, resolveSandboxContext() {}, execCancellationControl: {},
    OPENCLAW_VERSION: '2026.6.33'};
  vm.runInNewContext(source.slice(begin, finish), context);
  const options = context.accessRuntime;
  assert.equal(reads, 0);
  assert.equal(options.config(), startup);
  assert.equal(options.settingsConfig(), current);
  current = {generation: 'reloaded'};
  assert.equal(options.settingsConfig(), current);
  assert.equal(reads, 2);
});

function registeredRoute(managed) {
  const binding = {schemaVersion: 1, activationId: '00000000-0000-4000-8000-000000000001', revision: 4, allowCloud: false};
  const config = {agents: {list: [{id: 'pixel', model: managed ? {primary: 'ods-policy/managed', fallbacks: []} : 'local/model'}]},
    plugins: {entries: {'pixel-ods': {enabled: true, hooks: {allowConversationAccess: true},
      config: managed ? {managedProvider: structuredClone(binding)} : {}}}},
    models: {providers: managed ? {'ods-policy': {baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions',
      apiKey: 'ods-policy-unavailable', models: [{id: 'managed', name: 'ODS managed', contextWindow: 32768,
        maxTokens: 4096, reasoning: false, input: ['text']}]}} : {}}};
  const environment = managed ? {[DEPLOYMENT_ENV]: JSON.stringify({binding, sourceRoot: '/owner/ods',
    hostPython: '/usr/bin/python3', providerDirectory: '/owner/providers', ownerScopes: true,
    leaseTimeoutSeconds: 180, approvalTimeoutSeconds: 60}), OPENCLAW_REQUIRED_PLUGINS:
    JSON.stringify({version: 1, plugins: [{id: 'pixel-ods', hooks: requiredManagedHooks}]})} : {};
  let route, reads = 0, providers = 0;
  const access = {status: () => ({available: true, phase: 'held', active: 0}),
    isProbe: () => false, admit: () => ({outcome: 'block'}), finish() {},
    readSettings(token, revision) {
      assert.equal(token, body.token); assert.equal(revision, body.revision); reads++;
      return readRuntimeSettings(config, {pid: 1234, runtimeVersion: '2026.6.33', revision,
        observedAt: '2026-09-09T13:00:00.000Z'});
    }};
  const api = {registrationMode: 'full', config, pluginConfig: config.plugins.entries['pixel-ods'].config,
    runtime: {config: {current: () => config}}, registerProvider() {providers++;}, on() {}, registerRuntimeLifecycle() {},
    registerHttpRoute(value) {route = value;}};
  const registry = createManagedRuntimeRegistry({environment,
    // Registration composition only. Do not launch POSIX transports on Windows
    // or claim a model request from these controlled transport adapters.
    createRouting: args => createManagedProviderBootstrap({...args,
      createLease: () => ({durableReplayGuard: true, acquireLease() {throw new Error('no transport allowed');}, releaseLease() {}}),
      createHandoff: () => async () => null})});
  const context = vm.createContext({api, managedRuntimeRegistry: registry,
    accessRuntime: undefined, createAccessRuntime: () => access, createOpenClawCodingTools() {},
    resolveSandboxContext() {}, execCancellationControl: {}, OPENCLAW_VERSION: '2026.6.33',
    sendJson(res, status, value) {Object.assign(res, {status, body: value});}});
  const begin = source.indexOf('    accessRuntime ??= createAccessRuntime(');
  const finish = source.indexOf('    const statusFile =', begin);
  assert.ok(begin >= 0 && finish > begin);
  // Include the actual production assignment from registry to route owner.
  vm.runInContext(source.slice(begin, finish) + source.slice(start, end), context);
  return {route, config, binding, reads: () => reads, providers: () => providers};
}

test('production owner assignment and native route compose with real managed registration', async () => {
  const f = registeredRoute(true);
  assert.equal(f.providers(), 1);
  const response = await request(f.route, {...body, operation: 'provider-readback'});
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(JSON.stringify(response.body.registration)), {status: 'active', binding: f.binding});
  assert.equal(response.body.transportVerified, false);
  f.config.plugins.entries['pixel-ods'].config.managedProvider.revision++;
  assert.equal((await request(f.route, {...body, operation: 'provider-readback'})).status, 409);
  assert.equal(f.reads(), 1); // Drifted registration is never downgraded to inactive.
});

test('production legacy owner assignment reports inactive without constructing a provider', async () => {
  const f = registeredRoute(false);
  assert.equal(f.providers(), 0);
  const response = await request(f.route, {...body, operation: 'provider-readback'});
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(JSON.stringify(response.body.registration)), {status: 'inactive', binding: null});
});
