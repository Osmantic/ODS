import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

import {createExampleProjectRoute, presentExampleProjects, SHIPPED_EXAMPLE_PROJECTS} from '../plugin/example-projects.mjs';
import {exampleProjectRefusal, exampleProjectTesting, requestsNewPlaygroundProject} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createWorkspacePreviewPublisher, testing as previewTesting} from '../plugin/workspace-preview.mjs';

const SHIPPED = new URL('../example-projects/', import.meta.url);

function workspace({index = true} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-workspace-'));
  const folder = path.join(root, 'Playground', 'elf-of-doom');
  fs.mkdirSync(folder, {recursive: true, mode: 0o700});
  if (index) fs.writeFileSync(path.join(folder, 'index.html'), '<!doctype html><title>Elf of Doom</title>\n');
  return root;
}

function receipt(overrides = {}) {
  const sha256 = 'a'.repeat(64);
  const siteId = `site-${sha256.slice(0, 24)}`;
  return {
    schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded',
    relativeDirectory: 'Playground/elf-of-doom', siteId, port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`, files: 9, bytes: 243500, sha256,
    entryFile: 'index.html', entrySha256: 'b'.repeat(64), httpStatus: 200, readbackVerified: true,
    executable: false, overwritten: false, boundary: previewTesting.BOUNDARY,
    publishedPaths: ['README.md', 'index.html'], publishedPathsOmitted: 7, ...overrides,
  };
}

function sendJson(res, status, body) { Object.assign(res, {status, body}); }

async function request(route, {method = 'GET', url = '/pixel-ods/example-projects', body, type = 'application/json'} = {}) {
  const res = {};
  const raw = body === undefined ? [] : [Buffer.from(typeof body === 'string' ? body : JSON.stringify(body))];
  const req = {method, url, headers: {'content-type': type}, async *[Symbol.asyncIterator]() { yield* raw; }};
  assert.equal(await route(req, res), true);
  return res;
}

// ---------------------------------------------------------------- listing --

test('the plugin names exactly the example folders ODS ships', () => {
  const shipped = fs.readdirSync(SHIPPED, {withFileTypes: true}).filter(entry => entry.isDirectory()).map(entry => entry.name);
  assert.deepEqual([...SHIPPED_EXAMPLE_PROJECTS].sort(), shipped.sort());
  const helper = fs.readFileSync(new URL('../../../../installers/lib/pixel-example-projects.py', import.meta.url), 'utf8');
  for (const name of SHIPPED_EXAMPLE_PROJECTS) assert.match(helper, new RegExp(`^    '${name}': \\{$`, 'm'));
});

test('a present example is listed, and a missing, renamed or emptied one quietly is not', () => {
  const root = workspace();
  assert.deepEqual(presentExampleProjects(root), ['elf-of-doom']);
  fs.renameSync(path.join(root, 'Playground', 'elf-of-doom'), path.join(root, 'Playground', 'my-elf-game'));
  assert.deepEqual(presentExampleProjects(root), []);
  assert.deepEqual(presentExampleProjects(workspace({index: false})), []);
  assert.deepEqual(presentExampleProjects(path.join(root, 'missing')), []);
  assert.deepEqual(presentExampleProjects('relative/workspace'), []);
  assert.deepEqual(presentExampleProjects(undefined), []);
  fs.rmSync(path.join(root, 'Playground'), {recursive: true});
  fs.writeFileSync(path.join(root, 'Playground'), 'not a folder\n');
  assert.deepEqual(presentExampleProjects(root), []);
});

test('a linked example folder is not the shipped example', {skip: process.platform === 'win32'}, () => {
  const root = workspace();
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-outside-'));
  fs.writeFileSync(path.join(outside, 'index.html'), '<!doctype html>\n');
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  fs.symlinkSync(outside, path.join(root, 'Playground', 'elf-of-doom'));
  assert.deepEqual(presentExampleProjects(root), []);
});

// ------------------------------------------------------------------ route --

test('GET lists present examples and never publishes', async () => {
  const root = workspace();
  const published = [];
  const route = createExampleProjectRoute({workspaceRoot: () => root, publish: async value => { published.push(value); }, sendJson});
  assert.deepEqual(await request(route), {status: 200, body: {schemaVersion: 1, projects: ['elf-of-doom']}});
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  assert.deepEqual(await request(route), {status: 200, body: {schemaVersion: 1, projects: []}});
  assert.deepEqual(published, []);
});

test('POST publishes the named present example and returns only the dashboard receipt', async () => {
  const root = workspace();
  const published = [];
  const route = createExampleProjectRoute({workspaceRoot: () => root, publish: async value => { published.push(value); return receipt(); }, sendJson});
  const res = await request(route, {method: 'POST', body: {name: 'elf-of-doom'}});
  assert.equal(res.status, 200);
  const {siteId, sha256, entrySha256, url, port, files, bytes} = receipt();
  assert.deepEqual(res.body, {schemaVersion: 1, preview: {schemaVersion: 1, kind: 'ods-pixel-workspace-preview',
    relativeDirectory: 'Playground/elf-of-doom', siteId, port, url, files, bytes, sha256, entrySha256}});
  assert.deepEqual(published, ['Playground/elf-of-doom']);
});

test('POST refuses any other name, shape or folder before publishing', async () => {
  const root = workspace();
  fs.mkdirSync(path.join(root, 'Playground', 'owner-project'));
  fs.writeFileSync(path.join(root, 'Playground', 'owner-project', 'index.html'), '<!doctype html>\n');
  const published = [];
  const route = createExampleProjectRoute({workspaceRoot: () => root, publish: async value => { published.push(value); return receipt(); }, sendJson});
  for (const body of [{name: 'owner-project'}, {name: '../elf-of-doom'}, {name: 'elf-of-doom', path: '/etc'},
    ['elf-of-doom'], 'not json', {name: 'x'.repeat(300)}]) {
    assert.equal((await request(route, {method: 'POST', body})).status, 400, JSON.stringify(body));
  }
  assert.equal((await request(route, {method: 'POST', body: {name: 'elf-of-doom'}, type: 'text/plain'})).status, 400);
  assert.equal((await request(route, {method: 'PUT', body: {name: 'elf-of-doom'}})).status, 405);
  assert.equal((await request(route, {url: '/pixel-ods/example-projects?x=1'})).status, 400);
  assert.deepEqual(published, []);
});

test('a deleted example is unavailable and a failed publication reports no receipt', async () => {
  const root = workspace();
  const route = createExampleProjectRoute({workspaceRoot: () => root, publish: async () => { throw new Error('host said no'); }, sendJson});
  assert.deepEqual(await request(route, {method: 'POST', body: {name: 'elf-of-doom'}}),
    {status: 503, body: {error: 'example-project-preview-unavailable'}});
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  assert.deepEqual(await request(route, {method: 'POST', body: {name: 'elf-of-doom'}}),
    {status: 404, body: {error: 'example-project-unavailable'}});
});

test('the gateway route is registered with gateway authentication and the configured workspace', async () => {
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const start = source.indexOf("    // Portal's sidebar lists the shipped example project");
  const end = source.indexOf('    registerTool(', start);
  assert.ok(start > 0 && end > start);
  let route, options;
  vm.runInNewContext(source.slice(start, end), {
    api: {registerHttpRoute(value) { route = value; }, config: {agents: {list: [{id: 'pixel', workspace: '/owner/workspace'}]}},
      pluginConfig: {workspacePreviewTransport: 'docker-desktop'}},
    AGENT_ID: 'pixel', sendJson,
    createWorkspacePreviewPublisher: value => { options = value; return 'publisher'; },
    createExampleProjectRoute: value => value,
  });
  assert.equal(route.path, '/pixel-ods/example-projects');
  assert.equal(route.auth, 'gateway');
  assert.equal(route.match, 'exact');
  assert.equal(route.handler.workspaceRoot(), '/owner/workspace');
  assert.equal(route.handler.publish, 'publisher');
  assert.equal(JSON.stringify(options), JSON.stringify({transport: 'docker-desktop'}));
});

// -------------------------------------------------------------- publisher --

test('the owner-initiated publisher returns only a verified receipt for that folder', async () => {
  const requests = [];
  const publish = createWorkspacePreviewPublisher({request: async value => { requests.push(value); return receipt(); }});
  assert.equal((await publish('Playground/elf-of-doom')).siteId, receipt().siteId);
  assert.deepEqual(requests, [{schemaVersion: 1, action: 'publish', relativeDirectory: 'Playground/elf-of-doom'}]);
  for (const reply of [receipt({relativeDirectory: 'Playground/other'}), receipt({readbackVerified: false}),
    {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'failed', boundary: previewTesting.BOUNDARY,
      error: 'ODS workspace preview publication failed', errorCode: 'missing_entry'}]) {
    await assert.rejects(createWorkspacePreviewPublisher({request: async () => reply})('Playground/elf-of-doom'));
  }
  await assert.rejects(publish('../outside'));
});

// ------------------------------------------------------------- protection --

const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', sessionKey: `agent:pixel:openai-user:ods-${'a'.repeat(64)}`};
function guardFor(prompt, root) {
  const guard = createToolLoopGuard();
  guard.observeRun(context, 'pixel', {prompt}, {executionHost: 'sandbox', workspaceRoot: root});
  return guard;
}
function before(guard, toolName, params, id = 'call-1') {
  return guard.beforeToolCall({toolName, runId: 'run-1', params}, {...context, toolName, toolCallId: id}, 'pixel');
}
function readFirst(guard, file) {
  const params = {path: file};
  assert.notEqual(before(guard, 'read', params)?.block, true, 'reading the example stays allowed');
  guard.afterToolCall({toolName: 'read', runId: 'run-1', params, result: {details: {status: 'completed'}}}, {...context, toolName: 'read'}, 'pixel');
}
const NEW_GAME = ['Make me a dungeon crawler game.', 'Make me a game',
  'Build a new browser game where an elf shoots monsters and show me a preview.'];
const OPEN = 'Open the existing Playground/elf-of-doom/index.html preview.';
const CHANGES = [
  ['write', {path: 'Playground/elf-of-doom/index.html', content: '<!doctype html><title>new</title>\n'}],
  ['write', {path: '/workspace/Playground/elf-of-doom/engine.js', content: 'void 0;\n'}],
  ['write', {path: 'playground/Elf-Of-Doom/ui.js', content: 'void 0;\n'}],
  ['edit', {path: 'Playground/elf-of-doom/ui.js', oldText: 'a', newText: 'b'}],
  ['apply_patch', {input: '*** Begin Patch\n*** Update File: Playground/elf-of-doom/ui.js\n@@\n-a\n+b\n*** End Patch'}],
  ['exec', {command: "sed -i 's/3/7/' Playground/elf-of-doom/engine.js"}],
  ['exec', {command: 'cd Playground/elf-of-doom && cat > notes.txt'}],
  ['exec', {command: 'rm index.html', workdir: '/workspace/Playground/elf-of-doom'}],
  ['tool_call', {id: 'write', args: {path: 'Playground/elf-of-doom/index.html', content: 'x'}}],
];

for (const prompt of NEW_GAME) {
  test(`a new-game request never changes or publishes the shipped example: ${prompt}`, () => {
    assert.equal(requestsNewPlaygroundProject(prompt), true);
    const root = workspace();
    for (const [tool, params] of CHANGES) {
      const guard = guardFor(prompt, root);
      readFirst(guard, 'Playground/elf-of-doom/index.html');
      const result = before(guard, tool, params);
      assert.equal(result?.block, true, `${tool} ${JSON.stringify(params)}`);
      assert.match(result.blockReason, /Playground\/elf-of-doom is the finished example project ODS ships, and the owner asked for a new project/);
      assert.match(result.blockReason, /its own descriptive folder/);
    }
    const guard = guardFor(prompt, root);
    readFirst(guard, 'Playground/elf-of-doom/index.html');
    const published = before(guard, 'pixel_ods_workspace_preview', {relativeDirectory: 'Playground/elf-of-doom'});
    assert.equal(published?.block, true);
    assert.match(published.blockReason, /not the new project the owner asked for/);
    // Its own new folder is still the ordinary path.
    const fresh = guardFor(prompt, root);
    assert.notEqual(before(fresh, 'write', {path: 'Playground/dungeon-crawler/index.html', content: '<!doctype html>\n'})?.block, true);
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground', 'elf-of-doom')), ['index.html']);
  });
}

test('opening the existing example reads and publishes it but never changes it', () => {
  assert.equal(requestsNewPlaygroundProject(OPEN), false);
  const root = workspace();
  for (const [tool, params] of CHANGES) {
    const guard = guardFor(OPEN, root);
    readFirst(guard, 'Playground/elf-of-doom/index.html');
    const result = before(guard, tool, params);
    assert.equal(result?.block, true, `${tool} ${JSON.stringify(params)}`);
    assert.match(result.blockReason, /changes it only when the owner's request names it as what to change, for example "In elf-of-doom, make the arrows give 7"/);
  }
  const guard = guardFor(OPEN, root);
  readFirst(guard, 'Playground/elf-of-doom/index.html');
  for (const command of ['ls Playground/elf-of-doom', 'cat Playground/elf-of-doom/index.html | head -n 40',
    'grep -n arrow Playground/elf-of-doom/engine.js 2>/dev/null', 'cd Playground/elf-of-doom && wc -c index.html']) {
    assert.notEqual(before(guard, 'exec', {command})?.block, true, command);
  }
  const published = before(guard, 'pixel_ods_workspace_preview', {relativeDirectory: 'Playground/elf-of-doom'});
  assert.notEqual(published?.block, true);
});

for (const prompt of ['In elf-of-doom, make the arrows give 7', 'In Elf of Doom, make the arrows give 7.',
  'Fix the bugs in the game in Playground/elf-of-doom.', "Make elf-of-doom's arrows faster",
  'Open elf-of-doom and make the arrows give 7', 'In elf-of-doom, create a new boss level.']) {
  test(`an owner who names the example may change it: ${prompt}`, () => {
    const root = workspace();
    for (const [tool, params] of CHANGES) {
      const guard = guardFor(prompt, root);
      readFirst(guard, 'Playground/elf-of-doom/index.html');
      assert.equal(exampleProjectRefusal({tool, params, root, intent: prompt}), undefined, `${tool} ${JSON.stringify(params)}`);
      const result = before(guard, tool, params);
      assert.ok(!(result?.block && /example project ODS ships/.test(result.blockReason)), `${tool} ${JSON.stringify(params)}`);
    }
  });
}

test('a mention that only compares, forbids or opens is not a request to change the example', () => {
  for (const intent of ['Make me a dungeon crawler like elf-of-doom.', 'Make a new game inspired by Elf of Doom',
    'Show me elf-of-doom but do not change anything', 'Open Elf of Doom', 'Play the elf-of-doom game',
    'Make a new game from elf-of-doom', 'Make me a copy of elf-of-doom as a new game', undefined, '',
    // ODS's own appended routing contract is not the owner asking for a change.
    `${OPEN}\n\n[ODS Portal workspace task route: Perform the requested workspace mutation; write or edit the files.]`]) {
    assert.equal(exampleProjectTesting.ownerChangesExample(intent, 'elf-of-doom'), false, String(intent));
  }
  assert.equal(exampleProjectTesting.exampleMention('make a game like elf-of-doom-2', 'elf-of-doom'), null);
  assert.equal(exampleProjectTesting.exampleMention('fix Playground/elf-of-doom/ui.js', 'elf-of-doom'), 'target');
});

test('only plain reading commands count as read-only inside the example', () => {
  for (const command of ['ls -la Playground/elf-of-doom', 'cat a | head', 'grep -rn x . 2>&1', 'find . -name "*.js"', 'wc -l *.js > /dev/null']) {
    assert.equal(exampleProjectTesting.readOnlyCommand(command), true, command);
  }
  for (const command of ['cat a > b', 'sed -i s/a/b/ x', 'find . -delete', 'cp a b', 'echo $(rm x)', 'tee x', 'python3 x.py',
    'sort -o x y', 'git checkout .', 'mv a b', 'node build.js', 'cat <<EOF > x\nhi\nEOF']) {
    assert.equal(exampleProjectTesting.readOnlyCommand(command), false, command);
  }
});

test('paths outside the example, and other workspaces, are untouched by the protection', () => {
  const root = workspace();
  for (const [tool, params] of [['write', {path: 'Playground/elf-of-doom-2/index.html', content: 'x'}],
    ['write', {path: 'elf-of-doom/index.html', content: 'x'}], ['write', {path: '/tmp/Playground/elf-of-doom/x', content: 'x'}],
    ['exec', {command: 'cp -r Playground/elf-of-doom/README.md /tmp/x', workdir: '/tmp'}], ['read', {path: 'Playground/elf-of-doom/index.html'}]]) {
    assert.equal(exampleProjectRefusal({tool, params, root, intent: 'Make me a game'}), undefined, `${tool} ${JSON.stringify(params)}`);
  }
  assert.equal(exampleProjectRefusal({tool: 'write', params: {path: 'Playground/elf-of-doom/x', content: 'x'}, root: undefined, intent: 'Make me a game'}), undefined);
});
