import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

import {createExampleProjectRoute, presentExampleProjects, SHIPPED_EXAMPLE_PROJECTS} from '../plugin/example-projects.mjs';
import {exampleProjectRefusal, exampleProjectTesting} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createWorkspacePreviewPublisher, PREVIEW_FOLDER_FAILURES, testing as previewTesting} from '../plugin/workspace-preview.mjs';

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

// ------------------------------------------------------------- protection --

let runs = 0;
function chatKey(digit) { return `agent:pixel:openai-user:ods-${digit.repeat(64)}`; }
// One owner turn. Passing the same guard and chat key continues that chat.
function guardFor(prompt, root, {guard = createToolLoopGuard(), sessionKey} = {}) {
  const run = ++runs;
  const context = {agentId: 'pixel', runId: `run-${run}`, sessionId: `session-${run}`, trigger: 'user',
    sessionKey: sessionKey ?? `agent:pixel:openai-user:ods-${String(run).padStart(64, 'a')}`};
  guard.observeRun(context, 'pixel', {prompt}, {executionHost: 'sandbox', workspaceRoot: root});
  let call = 0;
  const before = (toolName, params) => guard.beforeToolCall({toolName, runId: context.runId, params},
    {...context, toolName, toolCallId: `call-${++call}`}, 'pixel');
  const read = file => {
    const params = {path: file};
    assert.notEqual(before('read', params)?.block, true, 'reading the example stays allowed');
    guard.afterToolCall({toolName: 'read', runId: context.runId, params, result: {details: {status: 'completed'}}}, {...context, toolName: 'read'}, 'pixel');
  };
  return {guard, before, read};
}
const SHIPS = /Playground\/elf-of-doom is the finished example project ODS ships/;
function refused(result, label) {
  assert.equal(result?.block, true, label);
  assert.match(result.blockReason, SHIPS, label);
}
function notRefused(result, label) {
  assert.ok(!(result?.block && SHIPS.test(result.blockReason)), `${label}: ${result?.blockReason}`);
}
const unchanged = root => assert.deepEqual(fs.readdirSync(path.join(root, 'Playground', 'elf-of-doom')).sort(), ['engine.js', 'index.html', 'ui.js']);
function fullWorkspace() {
  const root = workspace();
  for (const name of ['engine.js', 'ui.js']) fs.writeFileSync(path.join(root, 'Playground', 'elf-of-doom', name), 'orig\n');
  return root;
}

const NEW_GAME = ['Make me a dungeon crawler game.', 'Make me a game',
  'Build a new browser game where an elf shoots monsters and show me a preview.',
  // Prompts from an owner who has just played the example.
  'Make a sequel to Elf of Doom', 'Make me a new game, a prequel to elf-of-doom',
  'Make me a dungeon crawler like the one in elf-of-doom', 'Make me a new game with a boss like the dragon in elf of doom',
  'Build a new game for elf-of-doom fans', 'Create a new game with the same controls as in Elf of Doom',
  "Make me a new game on top of elf-of-doom's engine", 'Make me a dungeon crawler in elf-of-doom'];
const OPEN = 'Open the existing Playground/elf-of-doom/index.html preview.';
const OPEN_ONLY = [OPEN, 'Show me elf of doom and explain how the arrows work', 'Open elf-of-doom. Does it work on my phone?',
  'Open elf of doom, I want to see what it can do before I make my own game.', 'Open elf-of-doom and tell me what you would change'];
const NAMED = ['In elf-of-doom, make the arrows give 7', 'In Elf of Doom, make the arrows give 7.',
  'Fix the bugs in the game in Playground/elf-of-doom.', "Make elf-of-doom's arrows faster",
  'Open elf-of-doom and make the arrows give 7', 'Open elf-of-doom. Make the arrows give 7.',
  'In elf-of-doom, create a new boss level.', 'In elf-of-doom, build a new game mode with a boss',
  'Can you add a pause button to elf of doom?', 'I want the arrows in elf-of-doom to give 7'];
const CHANGES = [
  ['write', {path: 'Playground/elf-of-doom/index.html', content: '<!doctype html><title>new</title>\n'}],
  ['write', {path: 'Playground/elf-of-doom/level2.html', content: '<!doctype html>\n'}],
  ['write', {path: '/workspace/Playground/elf-of-doom/engine.js', content: 'void 0;\n'}],
  ['write', {path: 'playground/Elf-Of-Doom/ui.js', content: 'void 0;\n'}],
  ['edit', {path: 'Playground/elf-of-doom/ui.js', oldText: 'orig', newText: 'new'}],
  ['apply_patch', {input: '*** Begin Patch\n*** Update File: Playground/elf-of-doom/ui.js\n@@\n-orig\n+new\n*** End Patch'}],
  ['tool_call', {id: 'write', args: {path: 'Playground/elf-of-doom/index.html', content: 'x'}}],
];
// Commands that change the example, each from the workspace root unless a
// workdir is given; none of them may run for a request that does not name it.
const COMMANDS = [
  ["sed -i 's/3/7/' Playground/elf-of-doom/engine.js"],
  ['cd Playground/elf-of-doom && cat > notes.txt'],
  ['rm index.html', '/workspace/Playground/elf-of-doom'],
  ['cd Playground && mv elf-of-doom old-elf'],
  ['pushd Playground; rm elf-of-doom/engine.js; popd'],
  ['cd /workspace/Playground; mv elf-of-doom old'],
  ['cd Playground && rm -rf elf-of-doom'],
  ['rm -rf Playground/elf*'],
  ['cd /workspace && rm -rf Playground/elf*'],
  ["python3 - <<'EOF'\nopen('Playground/elf-of-doom/engine.js','w').write('x')\nEOF"],
  ["python3 -c \"open('Playground/elf-of-doom/index.html','w')\""],
  ["python3 -c \"open('../elf-of-doom/index.html','w').write('x')\"", '/workspace/Playground/dungeon-crawler'],
  ["node -e \"require('fs').writeFileSync('../elf-of-doom/index.html','x')\"", '/workspace/Playground/dungeon-crawler'],
  ["sh -c 'echo x > ../elf-of-doom/index.html'", '/workspace/Playground/dungeon-crawler'],
  ['cp index.html ../elf-of-doom/', '/workspace/Playground/dungeon-crawler'],
  ['cd .. && rm -r elf-of-doom', '/workspace/Playground/dungeon-crawler'],
  ['sed -i s/a/b/ elf-of-doom/engine.js', '/workspace/Playground'],
  ['sed -i s/a/b/ elf-of-doom/engine.js', 'Playground'],
  ["find /workspace/Playground -name '*.js' -exec sed -i s/a/b/ {} \\;"],
  ['mv /workspace/Playground /workspace/Playground-old'],
  ['chmod -R g+w /workspace/Playground'],
  ["grep -rl dragon /workspace/Playground | xargs sed -i 's/dragon/wyrm/'"],
];
// Commands that copy the example's files as a starting point.
const COPIES = [
  ['cd /workspace/Playground && cp -r elf-of-doom dungeon-crawler'],
  ['cp ../elf-of-doom/engine.js .', '/workspace/Playground/dungeon-crawler'],
  ['cat /workspace/Playground/elf-of-doom/engine.js > engine.js', '/workspace/Playground/dungeon-crawler'],
];

for (const prompt of NEW_GAME) {
  test(`a new-game request never changes, copies or publishes the shipped example: ${prompt}`, () => {
    const root = fullWorkspace();
    for (const [tool, params] of CHANGES) {
      const turn = guardFor(prompt, root);
      turn.read('Playground/elf-of-doom/index.html');
      const result = turn.before(tool, params);
      refused(result, `${tool} ${JSON.stringify(params)}`);
      assert.match(result.blockReason, /the owner asked for something new\. Leave Playground\/elf-of-doom as it is and do not copy its files/);
      assert.doesNotMatch(result.blockReason, /dungeon-crawler/);
    }
    for (const [command, workdir] of [...COMMANDS, ...COPIES]) {
      const turn = guardFor(prompt, root);
      // The run's own project exists first, so routing lets commands run.
      notRefused(turn.before('write', {path: 'Playground/dungeon-crawler/index.html', content: '<!doctype html>\n'}), 'new folder');
      refused(turn.before('exec', {command, ...(workdir ? {workdir} : {})}), command);
    }
    const turn = guardFor(prompt, root);
    turn.read('Playground/elf-of-doom/index.html');
    const published = turn.before('pixel_ods_workspace_preview', {relativeDirectory: 'Playground/elf-of-doom'});
    assert.equal(published?.block, true);
    assert.match(published.blockReason, /not the new project the owner asked for/);
    // Its own new folder is still the ordinary path.
    notRefused(guardFor(prompt, root).before('write', {path: 'Playground/dungeon-crawler/index.html', content: '<!doctype html>\n'}), 'new folder');
    unchanged(root);
  });
}

test('a refused copy names the run\'s own project folder', () => {
  const root = fullWorkspace();
  const turn = guardFor('Make me a dungeon crawler game.', root);
  notRefused(turn.before('write', {path: 'Playground/dungeon-crawler/index.html', content: '<!doctype html>\n'}), 'new folder');
  const result = turn.before('exec', {command: 'cp ../elf-of-doom/engine.js .', workdir: '/workspace/Playground/dungeon-crawler'});
  refused(result, 'copy');
  assert.match(result.blockReason, /^Not copied: .* never a starting point for new work unless the owner asks for a copy of it\. .* in Playground\/dungeon-crawler\.$/);
});

for (const prompt of OPEN_ONLY) {
  test(`opening or asking about the example reads and publishes it but never changes it: ${prompt}`, () => {
    const root = fullWorkspace();
    for (const [tool, params] of CHANGES) {
      const turn = guardFor(prompt, root);
      turn.read('Playground/elf-of-doom/index.html');
      const result = turn.before(tool, params);
      refused(result, `${tool} ${JSON.stringify(params)}`);
      assert.match(result.blockReason, /changes it only when the owner's request names it as what to change, for example "In elf-of-doom, make the arrows give 7"/);
    }
    for (const [command, workdir] of COMMANDS) {
      refused(guardFor(prompt, root).before('exec', {command, ...(workdir ? {workdir} : {})}), command);
    }
    const turn = guardFor(prompt, root);
    turn.read('Playground/elf-of-doom/index.html');
    for (const command of ['ls Playground/elf-of-doom', 'cat Playground/elf-of-doom/index.html | head -n 40',
      'grep -n arrow Playground/elf-of-doom/engine.js 2>/dev/null', 'cd Playground/elf-of-doom && wc -c index.html']) {
      assert.notEqual(turn.before('exec', {command})?.block, true, command);
    }
    assert.notEqual(turn.before('pixel_ods_workspace_preview', {relativeDirectory: 'Playground/elf-of-doom'})?.block, true);
    unchanged(root);
  });
}

for (const prompt of NAMED) {
  test(`an owner who names the example may change it in place: ${prompt}`, () => {
    const root = fullWorkspace();
    for (const [tool, params] of CHANGES) {
      const turn = guardFor(prompt, root);
      turn.read('Playground/elf-of-doom/index.html');
      turn.read('Playground/elf-of-doom/engine.js');
      assert.equal(exampleProjectRefusal({tool, params, root, intent: prompt}), undefined, `${tool} ${JSON.stringify(params)}`);
      const result = turn.before(tool, params);
      notRefused(result, `${tool} ${JSON.stringify(params)}`);
      // Never moved into a fresh copy such as Playground/elf-of-doom-2.
      assert.doesNotMatch(JSON.stringify(result?.params ?? {}), /elf-of-doom-2/i, `${tool} ${JSON.stringify(params)}`);
    }
    const turn = guardFor(prompt, root);
    notRefused(turn.before('exec', {command: "sed -i 's/3/7/' Playground/elf-of-doom/engine.js"}), 'sed');
    notRefused(turn.before('pixel_ods_workspace_preview', {relativeDirectory: 'Playground/elf-of-doom'}), 'publish');
    assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom']);
  });
}

test('a request to copy the example may copy it but changes only the copy', () => {
  const root = fullWorkspace();
  const prompt = 'Copy elf-of-doom into a new folder and make it harder';
  const turn = guardFor(prompt, root);
  turn.read('Playground/elf-of-doom/engine.js');
  const edit = turn.before('edit', {path: 'Playground/elf-of-doom/engine.js', oldText: 'orig', newText: 'new'});
  refused(edit, 'edit in place');
  assert.match(edit.blockReason, /the owner asked for a copy of it\. Leave Playground\/elf-of-doom as it is: copy its files into a new descriptive Playground folder and change only that copy/);
  notRefused(guardFor(prompt, root).before('exec', {command: 'cp -r Playground/elf-of-doom Playground/elf-of-doom-hard'}), 'copy');
  notRefused(guardFor(prompt, root).before('write', {path: 'Playground/elf-of-doom-hard/engine.js', content: 'x'}), 'write the copy');
  unchanged(root);
});

test('later turns of a chat that named the example keep changing it until a turn asks for something new', () => {
  const root = fullWorkspace();
  const guard = createToolLoopGuard();
  const edit = turn => turn.before('edit', {path: 'Playground/elf-of-doom/engine.js', oldText: 'orig', newText: 'new'});
  const chat = chatKey('e');
  notRefused(edit(guardFor('In elf-of-doom, make the arrows give 7', root, {guard, sessionKey: chat})), 'turn 1');
  notRefused(edit(guardFor('Now make them give 9', root, {guard, sessionKey: chat})), 'turn 2');
  notRefused(edit(guardFor('make it faster', root, {guard, sessionKey: chat})), 'turn 3');
  refused(edit(guardFor('Now make them give 9', root, {guard, sessionKey: chatKey('f')})), 'another chat');
  refused(edit(guardFor('Now make them give 9', root, {sessionKey: chat})), 'after a restart');
  refused(edit(guardFor('Make me a snake game', root, {guard, sessionKey: chat})), 'new game');
  refused(edit(guardFor('make it faster', root, {guard, sessionKey: chat})), 'after the new game');
  const opened = chatKey('c');
  notRefused(guardFor('Open elf-of-doom', root, {guard, sessionKey: opened}).before('pixel_ods_workspace_preview', {relativeDirectory: 'Playground/elf-of-doom'}), 'open');
  notRefused(edit(guardFor('Make the arrows give 7', root, {guard, sessionKey: opened})), 'change after opening it');
  refused(edit(guardFor('Make the arrows give 7 but do not change elf-of-doom', root, {guard, sessionKey: opened})), 'forbidden');
});

test('a call that routing moves into the example is refused after routing', () => {
  const root = fullWorkspace();
  const session = chatKey('b');
  // This chat's saved project binding already names the example folder.
  const records = path.join(root, '.ods-projects');
  fs.mkdirSync(records, {mode: 0o700});
  fs.writeFileSync(path.join(records, `${createHash('sha256').update(session).digest('hex')}.json`),
    JSON.stringify({schemaVersion: 1, source: 'elf-of-doom', directory: 'Playground/elf-of-doom'}), {mode: 0o600});
  const params = {path: 'ui.js', content: 'void 0;\n'};
  // Before routing, a bare ui.js names no example folder.
  assert.equal(exampleProjectRefusal({tool: 'write', params, root, intent: 'add a pause menu'}), undefined);
  refused(guardFor('add a pause menu', root, {sessionKey: session}).before('write', params), 'routed write');
  refused(guardFor('add a pause menu', root, {sessionKey: session}).before('exec', {command: 'rm ui.js'}), 'routed exec');
  unchanged(root);
});

test('with the example deleted, a new project never takes its name and the chat keeps working', () => {
  const root = workspace();
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  const session = chatKey('d');
  const turn = guardFor('Make me an elf of doom game', root, {sessionKey: session});
  assert.deepEqual(turn.before('write', {path: 'elf-of-doom/index.html', content: '<!doctype html>\n'})?.params?.path, 'Playground/elf-of-doom-2/index.html');
  assert.deepEqual(fs.readdirSync(path.join(root, 'Playground')), ['elf-of-doom-2']);
  const later = turn.before('write', {path: 'style.css', content: 'body{}\n'});
  assert.equal(later?.params?.path, 'Playground/elf-of-doom-2/style.css');
  const next = guardFor('make it faster', root, {sessionKey: session}).before('write', {path: 'game.js', content: 'void 0;\n'});
  assert.equal(next?.params?.path, 'Playground/elf-of-doom-2/game.js');
  assert.equal(fs.existsSync(path.join(root, 'Playground', 'elf-of-doom')), false);
});

test('workspace bundles, promoted downloads and process input cannot write into the example', () => {
  const root = fullWorkspace();
  const turn = guardFor('Make me a game', root);
  refused(turn.before('pixel_ods_workspace_bundle', {outputRoot: 'Playground/elf-of-doom', mappingPath: 'map.json',
    files: [{source: 'notes.txt', key: 'a', copyTo: 'a.txt'}]}), 'bundle output');
  refused(turn.before('pixel_ods_workspace_bundle', {outputRoot: 'Playground/new-game', mappingPath: 'map.json',
    files: [{source: 'Playground/elf-of-doom/engine.js', key: 'a', copyTo: 'a.txt'}]}), 'bundle source');
  refused(turn.before('pixel_ods_download_promote', {jobId: 'job', filename: 'x.js', relativePath: 'Playground/elf-of-doom/x.js',
    sha256: 'a'.repeat(64), sourceUrl: 'https://example.com/x.js'}), 'promote');
  refused(turn.before('process', {action: 'write', sessionId: 'proc-1', data: "open('Playground/elf-of-doom/x','w')\n"}), 'process input');
  assert.equal(exampleProjectRefusal({tool: 'process', params: {action: 'poll', sessionId: 'proc-1'}, root, intent: 'Make me a game'}), undefined);
});

test('what the owner asks of the example: change, copy, open or only a mention', () => {
  const level = intent => exampleProjectTesting.exampleRequest(intent, 'elf-of-doom').level;
  for (const intent of NAMED) assert.equal(level(intent), 'change', intent);
  for (const intent of [...NEW_GAME, ...OPEN_ONLY, 'Make me a dungeon crawler like elf-of-doom.', 'Make a new game inspired by Elf of Doom',
    'Show me elf-of-doom but do not change anything', 'Open Elf of Doom', 'Play the elf-of-doom game',
    'Make a new game from elf-of-doom', 'Make me a copy of elf-of-doom as a new game', 'Make the snake game look like elf-of-doom',
    'Copy elf-of-doom into a new folder and make it harder', undefined, '',
    // ODS's own appended routing contract is not the owner asking for a change.
    `${OPEN}\n\n[ODS Portal workspace task route: Perform the requested workspace mutation; write or edit the files.]`]) {
    assert.notEqual(level(intent), 'change', String(intent));
  }
  assert.equal(level('Copy elf-of-doom into a new folder and make it harder'), 'reuse');
  for (const intent of OPEN_ONLY) assert.equal(level(intent), 'open', intent);
  assert.equal(level('make a game like elf-of-doom-2'), null);
  assert.equal(exampleProjectTesting.exampleRequest('Make a sequel to Elf of Doom', 'elf-of-doom').creation, true);
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

test('commands in a project folder of their own are not mistaken for the example', () => {
  const root = fullWorkspace();
  const workdir = '/workspace/Playground/dungeon-crawler';
  for (const command of ['find . -name "*.tmp" -delete', 'rm -rf ./dist', 'git init && git add . && git stash', 'python3 -m http.server -d .',
    'cp -r assets/* .', 'ls *.js | xargs wc -l', "sed -i 's/a/b/' *.js", 'mv game.js main.js']) {
    assert.equal(exampleProjectTesting.execExampleUse(command, workdir, root, 'elf-of-doom', false), null, command);
  }
  // With no workdir, a command that routing will move into the run's project
  // is not yet a command on the whole workspace.
  assert.equal(exampleProjectTesting.execExampleUse('find . -name "*.tmp" -delete', undefined, root, 'elf-of-doom', true), null);
  assert.equal(exampleProjectTesting.execExampleUse('find . -name "*.tmp" -delete', undefined, root, 'elf-of-doom', false), 'change');
});

test('paths outside the example, and other workspaces, are untouched by the protection', () => {
  const root = workspace();
  for (const [tool, params] of [['write', {path: 'Playground/elf-of-doom-2/index.html', content: 'x'}],
    ['write', {path: 'elf-of-doom/index.html', content: 'x'}], ['write', {path: '/tmp/Playground/elf-of-doom/x', content: 'x'}],
    ['exec', {command: 'cp -r Playground/elf-of-doom/README.md /tmp/x', workdir: '/tmp'}], ['read', {path: 'Playground/elf-of-doom/index.html'}]]) {
    assert.equal(exampleProjectRefusal({tool, params, root, intent: 'Make me a game'}), undefined, `${tool} ${JSON.stringify(params)}`);
  }
  assert.equal(exampleProjectRefusal({tool: 'write', params: {path: 'Playground/elf-of-doom/x', content: 'x'}, root: undefined, intent: 'Make me a game'}), undefined);
  // A deleted example is ordinary history, not a protected name.
  fs.rmSync(path.join(root, 'Playground', 'elf-of-doom'), {recursive: true});
  assert.equal(exampleProjectRefusal({tool: 'write', params: {path: 'Playground/elf-of-doom/index.html', content: 'x'}, root, intent: 'Open it'}), undefined);
});
