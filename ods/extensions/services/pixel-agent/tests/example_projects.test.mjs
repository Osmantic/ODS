import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

import {addedExampleProjects, createExampleProjectRoute, EXAMPLE_RECORD_DIRECTORY, exampleProjectState, exampleRecordDirectory,
  exampleRecordFile, presentExampleProjects, SHIPPED_EXAMPLE_PROJECTS} from '../plugin/example-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createWorkspacePreviewPublisher, PREVIEW_FOLDER_FAILURES, testing as previewTesting} from '../plugin/workspace-preview.mjs';

const SHIPPED = new URL('../example-projects/', import.meta.url);

// Portal's state folder, where the installers keep their records: outside
// every test workspace, as it is outside the sandbox's workspace mount.
process.env.OPENCLAW_STATE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-state-'));
fs.mkdirSync(exampleRecordDirectory(), {mode: 0o700});

// The record installers/lib/pixel-example-projects.py writes for a workspace:
// 'added' when ODS added Playground/elf-of-doom, 'present' when the owner
// already had one.
function recordFile(root) { return exampleRecordFile(fs.realpathSync(root)); }
function recordBody(root, kind = 'added', extra = {}) {
  const added = kind === 'added' ? ['elf-of-doom'] : [];
  return `${JSON.stringify({schemaVersion: 1, workspace: fs.realpathSync(root), received: ['elf-of-doom'], added, ...extra})}\n`;
}
function writeRecord(root, kind = 'added') {
  fs.writeFileSync(recordFile(root), recordBody(root, kind), {mode: 0o600});
}

function workspace({index = true, record = 'added'} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-workspace-'));
  const folder = path.join(root, 'Playground', 'elf-of-doom');
  fs.mkdirSync(folder, {recursive: true, mode: 0o700});
  if (index) fs.writeFileSync(path.join(folder, 'index.html'), '<!doctype html><title>Elf of Doom</title>\n');
  if (record) writeRecord(root, record);
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
  // The installers and the plugin read one record, in the same place.
  assert.match(helper, new RegExp(`^RECORD_DIRECTORY = '${EXAMPLE_RECORD_DIRECTORY}'$`, 'm'));
});

test('the record is in Portal\'s state folder, named for the workspace, never in the workspace', () => {
  const root = workspace();
  assert.equal(path.dirname(recordFile(root)), path.join(process.env.OPENCLAW_STATE_DIR, '.ods-example-projects'));
  assert.equal(path.basename(recordFile(root)), `${createHash('sha256').update(fs.realpathSync(root)).digest('hex')}.json`);
  assert.deepEqual(fs.readdirSync(root), ['Playground']);
  // A record written into the workspace itself is not the installers' record.
  const inside = workspace({record: null});
  fs.writeFileSync(path.join(inside, '.ods-examples.json'), recordBody(inside));
  assert.deepEqual(presentExampleProjects(inside), []);
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

test('a folder of that name the owner already had is not listed as the shipped example', () => {
  // Portal may have built an "Elf of Doom" of the owner's own before ODS
  // shipped one: the installer then records it as present, never as added.
  for (const record of ['present', null]) {
    const root = workspace({record});
    assert.deepEqual(presentExampleProjects(root), [], String(record));
    assert.deepEqual(addedExampleProjects(fs.realpathSync(root)), [], String(record));
  }
  const root = workspace();
  const file = recordFile(root);
  const valid = JSON.parse(recordBody(root));
  const other = workspace();
  for (const body of ['not json', JSON.stringify({...valid, added: undefined}), JSON.stringify({...valid, received: []}),
    JSON.stringify({...valid, schemaVersion: 2}), JSON.stringify({...valid, workspace: undefined}),
    // Another workspace's record, and a small record with one key too many.
    JSON.stringify({...valid, workspace: fs.realpathSync(other)}), JSON.stringify({...valid, note: 1}),
    JSON.stringify({...valid, pad: 'x'.repeat(5000)})]) {
    fs.writeFileSync(file, body);
    assert.deepEqual(presentExampleProjects(root), [], body.slice(0, 60));
  }
  fs.rmSync(file);
  fs.mkdirSync(file);
  assert.deepEqual(presentExampleProjects(root), [], 'a folder where the record belongs');
  fs.rmdirSync(file);
  writeRecord(root);
  assert.deepEqual(presentExampleProjects(root), ['elf-of-doom'], 'the record restored');
});

test('a linked record, or a record folder that is a link or shared, is not the installers\' record', {skip: process.platform === 'win32'}, () => {
  const root = workspace({record: null});
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-outside-'));
  fs.writeFileSync(path.join(outside, 'record.json'), recordBody(root), {mode: 0o600});
  fs.symlinkSync(path.join(outside, 'record.json'), recordFile(root));
  assert.deepEqual(presentExampleProjects(root), []);
  fs.rmSync(recordFile(root));
  fs.linkSync(path.join(outside, 'record.json'), recordFile(root));
  assert.deepEqual(presentExampleProjects(root), [], 'a hard link');
  fs.rmSync(recordFile(root));
  // The record folder itself must be the owner's private real folder.
  const state = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-example-state-'));
  const records = path.join(state, EXAMPLE_RECORD_DIRECTORY);
  fs.mkdirSync(records, {mode: 0o700});
  fs.writeFileSync(exampleRecordFile(fs.realpathSync(root), records), recordBody(root), {mode: 0o600});
  assert.deepEqual(addedExampleProjects(fs.realpathSync(root), {recordDirectory: records}), ['elf-of-doom']);
  fs.chmodSync(records, 0o755);
  assert.deepEqual(addedExampleProjects(fs.realpathSync(root), {recordDirectory: records}), [], 'a shared record folder');
  fs.chmodSync(records, 0o700);
  const linked = path.join(state, 'linked');
  fs.symlinkSync(records, linked);
  assert.deepEqual(addedExampleProjects(fs.realpathSync(root), {recordDirectory: linked}), [], 'a linked record folder');
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
  // Files in the folder the host refuses (a group-writable file after an owner
  // edit, say) are the owner's to fix, never a reason to click again.
  for (const code of PREVIEW_FOLDER_FAILURES) {
    const refusing = createExampleProjectRoute({workspaceRoot: () => root, sendJson,
      publish: async () => { throw Object.assign(new Error('refused'), {code}); }});
    assert.deepEqual(await request(refusing, {method: 'POST', body: {name: 'elf-of-doom'}}),
      {status: 409, body: {error: 'example-project-not-previewable'}}, code);
  }
  for (const code of ['source_capture_limit', 'unavailable', undefined]) {
    const failing = createExampleProjectRoute({workspaceRoot: () => root, sendJson,
      publish: async () => { throw Object.assign(new Error('failed'), {code}); }});
    assert.equal((await request(failing, {method: 'POST', body: {name: 'elf-of-doom'}})).status, 503, String(code));
  }
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  assert.deepEqual(await request(route, {method: 'POST', body: {name: 'elf-of-doom'}}),
    {status: 404, body: {error: 'example-project-unavailable'}});
});

test('a folder that is still there but cannot open as the example is told apart from a deleted one', async () => {
  const published = [];
  const open = async root => request(createExampleProjectRoute({workspaceRoot: () => root, sendJson,
    publish: async value => { published.push(value); return receipt(); }}), {method: 'POST', body: {name: 'elf-of-doom'}});
  const notPlayable = {status: 422, body: {error: 'example-project-not-playable'}};
  // index.html moved away, or the record no longer says ODS added it.
  const noIndex = workspace();
  fs.rmSync(path.join(noIndex, 'Playground', 'elf-of-doom', 'index.html'));
  assert.equal(exampleProjectState(noIndex, 'elf-of-doom'), 'not-playable');
  assert.deepEqual(await open(noIndex), notPlayable);
  const unrecorded = workspace();
  fs.writeFileSync(recordFile(unrecorded), 'not json');
  assert.deepEqual(await open(unrecorded), notPlayable);
  const ownerOwn = workspace({record: 'present'});
  assert.deepEqual(await open(ownerOwn), notPlayable);
  // Gone: deleted, renamed, or the whole Playground removed.
  for (const change of ['deleted', 'renamed', 'no-playground']) {
    const root = workspace();
    if (change === 'deleted') fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
    else if (change === 'renamed') fs.renameSync(path.join(root, 'Playground', 'elf-of-doom'), path.join(root, 'Playground', 'mine'));
    else fs.rmSync(path.join(root, 'Playground'), {recursive: true});
    assert.equal(exampleProjectState(root, 'elf-of-doom'), 'missing', change);
    assert.deepEqual(await open(root), {status: 404, body: {error: 'example-project-unavailable'}}, change);
  }
  assert.equal(exampleProjectState(workspace(), 'elf-of-doom'), 'present');
  assert.deepEqual(published, []);
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
  for (const reply of [receipt({relativeDirectory: 'Playground/other'}), receipt({readbackVerified: false})]) {
    await assert.rejects(createWorkspacePreviewPublisher({request: async () => reply})('Playground/elf-of-doom'),
      error => error.code === undefined);
  }
  // A validated host refusal keeps its code; the folder codes are every
  // refusal that is not about a source capture.
  for (const code of previewTesting.FAILURE_CODES) {
    const reply = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'failed', boundary: previewTesting.BOUNDARY,
      error: 'ODS workspace preview publication failed', errorCode: code};
    await assert.rejects(createWorkspacePreviewPublisher({request: async () => reply})('Playground/elf-of-doom'), {code});
  }
  assert.deepEqual([...PREVIEW_FOLDER_FAILURES].sort(),
    previewTesting.FAILURE_CODES.filter(code => !code.startsWith('source_') && code !== 'no_eligible_source').sort());
  await assert.rejects(publish('../outside'));
});

// ---------------------------------------------------------- new projects --

// The shipped example's guard has its own tests (example_guard.test.mjs).
test('with the example deleted, a new project never takes its name and the chat keeps working', () => {
  const root = workspace();
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  const guard = createToolLoopGuard();
  const turn = (prompt, run) => {
    const context = {agentId: 'pixel', runId: `run-deleted-${run}`, sessionId: `session-deleted-${run}`, trigger: 'user',
      sessionKey: `agent:pixel:openai-user:ods-${'d'.repeat(64)}`};
    guard.observeRun(context, 'pixel', {prompt}, {executionHost: 'sandbox', workspaceRoot: root});
    return (toolName, params) => guard.beforeToolCall({toolName, runId: context.runId, params}, {...context, toolName, toolCallId: `call-${run}`}, 'pixel');
  };
  const first = turn('Make me an elf of doom game', 1);
  assert.equal(first('write', {path: 'elf-of-doom/index.html', content: '<!doctype html>\n'})?.params?.path, 'Playground/elf-of-doom-2/index.html');
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom-2']);
  assert.equal(first('write', {path: 'style.css', content: 'body{}\n'})?.params?.path, 'Playground/elf-of-doom-2/style.css');
  assert.equal(turn('make it faster', 2)('write', {path: 'game.js', content: 'void 0;\n'})?.params?.path, 'Playground/elf-of-doom-2/game.js');
  assert.equal(fs.existsSync(path.join(root, 'Playground', 'elf-of-doom')), false);
});
