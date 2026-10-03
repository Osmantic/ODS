import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createRuntimeIdentity, pluginTreeDigest} from '../plugin/runtime-identity.mjs';
import {createIngressServer, projectRuntimeIdentity} from '../host/pixel_ingress.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-identity-'));
  const pluginRoot = path.join(root, 'plugin');
  const modulePath = path.join(root, 'tool-search.js');
  fs.mkdirSync(pluginRoot);
  fs.mkdirSync(path.join(pluginRoot, 'nested'));
  fs.writeFileSync(path.join(pluginRoot, 'index.js'), 'export const x = 1;');
  fs.writeFileSync(path.join(pluginRoot, 'nested', 'tool.mjs'), 'export const tool = {};');
  fs.writeFileSync(modulePath, 'export const toolSearch = 1;');
  t.after(() => fs.rmSync(root, {recursive:true, force:true}));
  return {root, pluginRoot, modulePath, openclawVersion:'2026.6.33'};
}

test('initialization digest uses canonical Pixel tree framing but cannot attest evaluated code or release', t => {
  const options = fixture(t), read = createRuntimeIdentity(options);
  const expected = execFileSync(process.execPath, [fileURLToPath(new URL('../../../../vendor/pixel/scripts/hash-gateway-extension.mjs', import.meta.url)), options.pluginRoot], {encoding:'utf8'}).trim();
  assert.equal(pluginTreeDigest(options.pluginRoot), expected);
  const value = read();
  assert.equal(value.identities.pluginSha256, expected);
  assert.equal(value.state, 'partial');
  assert.equal(value.diskComparison, 'match');
  assert.equal(value.runtimeMatchesRelease, null);
  assert.equal(value.identities.odsReleaseCommit, null);
  assert.equal(value.identities.pixelSourceRevision, null);
  assert.equal(value.identities.previewImageDigest, null);
  assert.match(value.boundary, /not-evaluated-code-or-release-proof/);
  assert.deepEqual(projectRuntimeIdentity(value), value);
  assert.equal(JSON.stringify(value).includes(options.root), false);
});

test('hot-swapped disk never replaces initialization identity; removed files are unknown rather than green', t => {
  const options = fixture(t), read = createRuntimeIdentity(options), initial = read();
  fs.writeFileSync(path.join(options.pluginRoot, 'index.js'), 'export const x = 2;');
  const changed = read();
  assert.deepEqual(changed.identities, initial.identities);
  assert.equal(changed.runtimeMatchesRelease, false);
  assert.equal(changed.diskComparison, 'mismatch');
  fs.writeFileSync(path.join(options.pluginRoot, 'index.js'), 'export const x = 1;');
  fs.unlinkSync(options.modulePath);
  assert.equal(read().runtimeMatchesRelease, null);
  assert.equal(read().diskComparison, 'unavailable');
});

test('created in-memory tool schemas are measured independently of disk and never called offered surface', t => {
  const options = fixture(t), read = createRuntimeIdentity(options);
  const schema = {name:'pixel_ods_python_library_proposal', parameters:{type:'object', properties:{expression:{type:'string', maxLength:2048}}}};
  read.observeTool(schema);
  const old = read();
  delete schema.parameters.properties.expression.maxLength;
  read.observeTool(schema);
  const changed = read();
  assert.deepEqual(changed.identities, old.identities);
  assert.notEqual(changed.toolSchemas.registeredPluginToolSchemasSha256, old.toolSchemas.registeredPluginToolSchemasSha256);
  assert.equal(changed.toolSchemas.registeredPluginToolCount, 1);
  assert.equal(changed.toolSchemas.offeredToolCount, null);
  assert.equal(changed.toolSchemas.offeredToolSchemasSha256, null);
  assert.equal(changed.runtimeMatchesRelease, null);
});

test('missing and excessive files cannot claim complete identity', t => {
  const options = fixture(t);
  fs.writeFileSync(path.join(options.pluginRoot, 'oversize'), Buffer.alloc(16 * 1024 * 1024 + 1));
  assert.equal(createRuntimeIdentity(options)().identities.pluginSha256, null);
  const unknown = createRuntimeIdentity({...options, modulePath:path.join(options.root, 'missing')})();
  assert.equal(unknown.state, 'unavailable');
  assert.equal(unknown.runtimeMatchesRelease, null);
});

test('linked plugin files are unknown rather than followed', {skip:process.platform === 'win32' ? 'symlink privileges require POSIX CI' : false}, t => {
  const options = fixture(t);
  fs.symlinkSync(options.modulePath, path.join(options.pluginRoot, 'linked.js'));
  assert.equal(createRuntimeIdentity(options)().identities.pluginSha256, null);
});

test('projection strips private extras and refuses asserted green, stale, paths, and wrong schema shape', t => {
  const value = createRuntimeIdentity(fixture(t))();
  assert.deepEqual(projectRuntimeIdentity({...value, credential:'secret', identities:{...value.identities, path:'/private'}}), value);
  for (const bad of [
    {...value, runtimeMatchesRelease:true}, {...value, observedAt:'2001-01-01T00:00:00.000Z'},
    {...value, schemaVersion:true}, {...value, identities:{...value.identities, pluginSha256:'/private/key'}},
    {...value, toolSchemas:{...value.toolSchemas, offeredToolCount:20}},
  ]) assert.throws(() => projectRuntimeIdentity(bad));
});

test('private ingress relays exact diagnostics with gateway auth, bounded bodies and sanitized failures', async t => {
  const read = createRuntimeIdentity(fixture(t));
  const calls = [];
  let body = JSON.stringify({...read(), privatePath:'/must-not-leak'});
  const server = createIngressServer({token:'private-gateway-key', gatewayPort:18789, deps:{setTimeout,clearTimeout,
    fetch:async (url, options) => {calls.push({url,options}); return new Response(body,{status:200, headers:{'content-type':'application/json'}});}}});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));});
  const url = `http://127.0.0.1:${server.address().port}/v1/runtime-identity`;
  const result = await fetch(url);
  assert.equal(result.status, 200);
  assert.equal((await result.text()).includes('must-not-leak'), false);
  assert.equal(calls[0].url, 'http://127.0.0.1:18789/pixel-ods/runtime-identity');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer private-gateway-key');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal((await fetch(url + '?path=/private')).status, 400);
  assert.equal((await fetch(url, {method:'POST'})).status, 400);
  assert.equal(calls.length, 1);
  body = 'secret'.repeat(8192);
  const failed = await fetch(url);
  assert.equal(failed.status, 503);
  assert.equal(await failed.text(), '{"error":"runtime-identity-unavailable"}');
});
