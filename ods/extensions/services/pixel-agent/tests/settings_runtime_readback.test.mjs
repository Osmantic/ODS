import test from 'node:test';
import assert from 'node:assert/strict';
import {readRuntimeSettings} from '../plugin/settings-runtime-readback.mjs';

// These fixtures prove projection and rejection only, not applied settings.
const metadata = {pid: 123, runtimeVersion: '2026.6.33', revision: 'a'.repeat(64),
  observedAt: '2026-09-08T15:00:00.000Z'};
const cases = [
  ['contextTokens', ['contextTokens'], 32768, 4095],
  ['maxOutputTokens', ['params', 'maxTokens'], 4096, 0],
  ['thinking', ['thinkingDefault'], 'high', 'secret-value'],
  ['verbosity', ['verboseDefault'], 'full', true],
  ['reasoningVisibility', ['reasoningDefault'], 'stream', 'high'],
  ['toolProgress', ['toolProgressDetail'], 'explain', 'on'],
  ['temperature', ['params', 'temperature'], 0.7, 2.1],
  ['topP', ['params', 'topP'], 0.95, 0],
  ['toolResultMaxChars', ['contextLimits', 'toolResultMaxChars'], 15000, 2000001],
  ['bootstrapMaxChars', ['bootstrapMaxChars'], 10000, 0],
  ['bootstrapTotalMaxChars', ['bootstrapTotalMaxChars'], 30000, 0],
  ['compactionMode', ['compaction', 'mode'], 'safeguard', 'unknown'],
  ['compactionReserveTokens', ['compaction', 'reserveTokens'], 10000, -1],
  ['compactionReserveFloorTokens', ['compaction', 'reserveTokensFloor'], 0, -1],
  ['compactionKeepRecentTokens', ['compaction', 'keepRecentTokens'], 2000, 0],
  ['compactionHistoryShare', ['compaction', 'maxHistoryShare'], 0.4, 0.91],
  ['compactionRecentTurns', ['compaction', 'recentTurnsPreserve'], 3, 13],
  ['compactionTimeoutSeconds', ['compaction', 'timeoutSeconds'], 120, 3601],
  ['compactionNotify', ['compaction', 'notifyUser'], false, 0],
  ['compactionMemoryFlush', ['compaction', 'memoryFlush', 'enabled'], true, 'true'],
];
function fixture() {
  return {agents: {list: [{id: 'pixel'}]}, plugins: {entries: {'pixel-ods': {enabled: true}}}};
}
function put(config, name, path, value) {
  let target = name.startsWith('compaction')
    ? (config.agents.defaults ??= {}) : config.agents.list[0];
  for (const key of path.slice(0, -1)) target = target[key] ??= {};
  target[path.at(-1)] = value;
}
function rejected(config, meta = metadata) {
  assert.throws(() => readRuntimeSettings(config, meta), error =>
    error instanceof Error && !error.message.includes('secret-value'));
}

test('missing leaves remain absent, without invented defaults or applied claims', () => {
  const config = fixture(), before = structuredClone(config);
  const result = readRuntimeSettings(config, metadata);
  assert.deepEqual(Object.keys(result).sort(), ['schemaVersion', 'source', 'pid', 'runtimeVersion',
    'revision', 'observedAt', 'pixelOnlyRuntime', 'fields', 'pluginContext'].sort());
  assert.equal(result.schemaVersion, 1);
  assert.equal(result.source, 'current-runtime-config');
  assert.equal(result.pixelOnlyRuntime, true);
  for (const [key, value] of Object.entries(metadata)) assert.equal(result[key], value);
  assert.deepEqual(Object.keys(result.fields).sort(), cases.map(([name]) => name).sort());
  for (const field of Object.values(result.fields)) assert.deepEqual(field, {present: false, value: null});
  assert.deepEqual(result.pluginContext, {present: false, value: null});
  assert.deepEqual(config, before);
});

test('all fixed leaves project exactly and do not expose unrelated owner data', () => {
  const config = fixture();
  for (const [name, path, good] of cases) put(config, name, path, good);
  config.plugins.entries['pixel-ods'].config = {modelContextWindow: 32768};
  Object.defineProperty(config, 'models', {enumerable: true, get() { throw new Error('secret-value'); }});
  const result = readRuntimeSettings(config, metadata);
  for (const [name, , good] of cases) assert.deepEqual(result.fields[name], {present: true, value: good});
  assert.deepEqual(result.pluginContext, {present: true, value: 32768});
  result.fields.contextTokens.value = 16384;
  assert.equal(config.agents.list[0].contextTokens, 32768);
  assert.equal(readRuntimeSettings(config, metadata).fields.contextTokens.value, 32768);
});

for (const [name, path, good, bad] of cases) {
  test(`${name} rejects malformed, null, and out-of-range runtime values`, () => {
    for (const value of [bad, null, [], {}, NaN, Infinity, ...(typeof good === 'number' ? [true] : [])]) {
      const config = fixture(); put(config, name, path, value); rejected(config);
    }
    if (Number.isInteger(good)) {
      const config = fixture(); put(config, name, path, good + 0.5); rejected(config);
    }
  });
}

test('agent topology and known containers must be unambiguous plain data', () => {
  for (const list of [[], [{id: 'other'}], [{id: 'pixel'}, {id: 'pixel'}],
    [{id: 'pixel'}, {id: ''}], [{id: 'pixel'}, {id: 1}], [{id: 'pixel'}, null]]) {
    const config = fixture(); config.agents.list = list; rejected(config);
  }
  for (const key of ['params', 'contextLimits']) {
    const config = fixture(); config.agents.list[0][key] = []; rejected(config);
  }
  for (const defaults of [null, [], 0]) {
    const config = fixture(); config.agents.defaults = defaults; rejected(config);
  }
  const config = fixture(); config.agents.list.push({id: 'other'});
  assert.equal(readRuntimeSettings(config, metadata).pixelOnlyRuntime, false);
});

test('own-property access never invokes getters or inherits runtime values', () => {
  const config = fixture();
  Object.defineProperty(config.agents.list[0], 'contextTokens', {get() { throw new Error('secret-value'); }});
  rejected(config);
  const inherited = fixture(); inherited.agents.list[0] = Object.create({id: 'pixel'}); rejected(inherited);
  const nullPrototype = Object.assign(Object.create(null), fixture());
  assert.equal(readRuntimeSettings(nullPrototype, metadata).pixelOnlyRuntime, true);
  const ignored = fixture();
  Object.defineProperty(ignored, '__proto__', {value: {contextTokens: 9999}, enumerable: true});
  assert.deepEqual(readRuntimeSettings(ignored, metadata).fields.contextTokens, {present: false, value: null});
});

test('plugin context and response metadata reject invalid identity without leaking values', () => {
  for (const value of [null, 4095, 10000001, '32768', true]) {
    const config = fixture(); config.plugins.entries['pixel-ods'].config = {modelContextWindow: value}; rejected(config);
  }
  for (const [name, value] of [['pid', 0], ['pid', 1.5], ['pid', true], ['runtimeVersion', ''],
    ['runtimeVersion', 'secret-value\n'], ['runtimeVersion', 'x'.repeat(129)], ['revision', 'A'.repeat(64)],
    ['observedAt', '2026-02-30T15:00:00.000Z'], ['observedAt', '2026-09-08T15:00:00+00:00']]) {
    rejected(fixture(), {...metadata, [name]: value});
  }
});
