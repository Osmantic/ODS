import test from 'node:test';
import assert from 'node:assert/strict';
import {createIngressServer, projectExamplePreview, projectExampleProjects} from '../host/pixel_ingress.mjs';

const sha256 = 'a'.repeat(64);
const siteId = `site-${sha256.slice(0, 24)}`;
const preview = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', relativeDirectory: 'Playground/elf-of-doom',
  siteId, port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, files: 9, bytes: 243500, sha256, entrySha256: 'b'.repeat(64)};

async function ingress(t, reply) {
  const calls = [];
  const server = createIngressServer({token: 'private-gateway-key', gatewayPort: 18789, deps: {setTimeout, clearTimeout,
    fetch: async (url, options) => {
      calls.push({url, options});
      const [status, body] = reply(options);
      return new Response(typeof body === 'string' ? body : JSON.stringify(body), {status, headers: {'content-type': 'application/json'}});
    }}});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  return {url: `http://127.0.0.1:${server.address().port}/v1/example-projects`, calls};
}

const open = (url, body, type = 'application/json') => fetch(url, {method: 'POST', headers: {'content-type': type}, body: JSON.stringify(body)});

test('the relay lists present examples with gateway auth and no upstream extras', async t => {
  const {url, calls} = await ingress(t, () => [200, {schemaVersion: 1, projects: ['elf-of-doom']}]);
  const result = await fetch(url);
  assert.equal(result.status, 200);
  assert.equal(result.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await result.json(), {schemaVersion: 1, projects: ['elf-of-doom']});
  assert.equal(calls[0].url, 'http://127.0.0.1:18789/pixel-ods/example-projects');
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].options.headers.authorization, 'Bearer private-gateway-key');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.body, undefined);
  assert.equal((await fetch(url + '?all=1')).status, 400);
  assert.equal((await fetch(url, {method: 'DELETE'})).status, 405);
  assert.equal(calls.length, 1);
});

test('the relay opens one named example and returns only the validated receipt', async t => {
  const {url, calls} = await ingress(t, () => [200, {schemaVersion: 1, preview: {...preview}}]);
  const result = await open(url, {name: 'elf-of-doom'});
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), {schemaVersion: 1, preview});
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.body, '{"name":"elf-of-doom"}');
  for (const body of [{name: '../x'}, {name: 'elf-of-doom', path: '/etc'}, {}, ['elf-of-doom']]) {
    assert.equal((await open(url, body)).status, 400, JSON.stringify(body));
  }
  assert.equal((await open(url, {name: 'elf-of-doom'}, 'text/plain')).status, 415);
  assert.equal(calls.length, 1);
});

test('a missing example stays 404, refused files stay 409, and every other failure is a fixed 503', async t => {
  let reply = [404, {error: 'example-project-unavailable', secret: '/private'}];
  const {url} = await ingress(t, () => reply);
  const missing = await open(url, {name: 'elf-of-doom'});
  assert.equal(missing.status, 404);
  assert.equal(await missing.text(), '{"error":"example-project-unavailable"}');
  reply = [409, {error: 'example-project-not-previewable', secret: '/private'}];
  const refused = await open(url, {name: 'elf-of-doom'});
  assert.equal(refused.status, 409);
  assert.equal(await refused.text(), '{"error":"example-project-not-previewable"}');
  // Only opening has those answers; a listing never does.
  reply = [409, {error: 'example-project-not-previewable'}];
  assert.equal((await fetch(url)).status, 503);
  for (const next of [[503, {error: 'private detail'}], [200, {schemaVersion: 1, preview: {...preview, relativeDirectory: 'Playground/other'}}],
    [200, {schemaVersion: 1, preview: {...preview, extra: '/private'}}], [200, 'x'.repeat(9000)], [200, 'not json']]) {
    reply = next;
    const failed = await open(url, {name: 'elf-of-doom'});
    assert.equal(failed.status, 503);
    assert.equal(await failed.text(), '{"error":"example-project-preview-unavailable"}');
  }
  reply = [200, {schemaVersion: 1, projects: ['../x']}];
  const listed = await fetch(url);
  assert.equal(listed.status, 503);
  assert.equal(await listed.text(), '{"error":"example-projects-unavailable"}');
});

test('the projections accept exactly the plugin shapes', () => {
  assert.deepEqual(projectExampleProjects({schemaVersion: 1, projects: []}), {schemaVersion: 1, projects: []});
  for (const bad of [{schemaVersion: 1, projects: ['a', 'a']}, {schemaVersion: 2, projects: []}, {projects: []},
    {schemaVersion: 1, projects: ['a'], extra: 1}, {schemaVersion: 1, projects: Array.from({length: 9}, (_, i) => `p${i}`)}]) {
    assert.throws(() => projectExampleProjects(bad), JSON.stringify(bad));
  }
  assert.deepEqual(projectExamplePreview({schemaVersion: 1, preview}, 'elf-of-doom'), {schemaVersion: 1, preview});
  for (const bad of [{...preview, siteId: 'site-' + 'c'.repeat(24)}, {...preview, url: 'http://evil.example/'},
    {...preview, files: 0}, {...preview, kind: 'other'}]) {
    assert.throws(() => projectExamplePreview({schemaVersion: 1, preview: bad}, 'elf-of-doom'));
  }
});
