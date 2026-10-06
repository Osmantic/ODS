import test from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {createLayaRuntime, validateLayaConnection, readLayaConnection} from '../plugin/laya-runtime.mjs';

function connection() { return {schemaVersion: 1, installRoot: join(process.cwd(), 'fixture-ods'),
  port: 8017, token: 'a'.repeat(64), composeSha256: 'b'.repeat(64)}; }

test('absent extension neither advertises capability nor makes an inference', async () => {
  let builds = 0;
  const runtime = createLayaRuntime({readConnection: () => undefined, clientFactory: () => { builds++; }});
  assert.equal(runtime.offered(), null);
  assert.equal(runtime.promptHint(), '');
  assert.equal((await runtime.tool.execute('a', {})).details.status, 'disabled');
  assert.equal(builds, 0);
});

test('same session sees enable, disable, reenable and credential rotation', async () => {
  let active, calls = 0, builds = 0;
  const seenTokens = [];
  const runtime = createLayaRuntime({readConnection: () => active, clientFactory: settings => {
    builds++; seenTokens.push(settings.token);
    return {decide: async () => { calls++; return {items: []}; }};
  }});
  assert.equal(runtime.offered(), null);
  active = connection();
  const cachedTool = runtime.offered();
  assert.equal(cachedTool.name, 'pixel_ods_laya');
  assert.match(runtime.promptHint(), /Laya is enabled/);
  await cachedTool.execute('a', {});
  assert.equal(builds, 1);
  active = undefined;
  assert.equal((await cachedTool.execute('b', {})).details.status, 'disabled');
  assert.equal(runtime.promptHint(), '');
  active = connection();
  await cachedTool.execute('c', {});
  active = {...active, token: 'c'.repeat(64)};
  await cachedTool.execute('d', {});
  assert.deepEqual(seenTokens, ['a'.repeat(64), 'a'.repeat(64), 'c'.repeat(64)]);
  assert.equal(calls, 3);
});

test('connection schema refuses destinations, relative roots and arbitrary payloads', () => {
  for (const patch of [{url: 'http://external.invalid'}, {installRoot: '../ods'}, {port: '8017'},
    {token: 'short'}, {composeSha256: ''}, {schemaVersion: 2}]) {
    assert.throws(() => validateLayaConnection({...connection(), ...patch}), /Invalid Laya connection/);
  }
});

test('missing connection is inactive and native Windows does not fake POSIX permissions', () => {
  assert.equal(readLayaConnection(join(process.cwd(), 'missing-laya-record'), {uid: process.getuid?.()}), undefined);
});
