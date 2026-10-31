// Portal's one-step undo for Playground projects (plugin/project-snapshots.mjs):
// which calls touch which projects, the bookkeeping of a response (one
// fingerprint, one copy per project, retries, the turn's end), the owner's
// restore and options and their refusals, the gateway routes, the plugin
// entry's wiring, and, where POSIX descriptors exist, the real helper end to
// end. The helper itself is tested in test_project_snapshots_helper.py.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, {after} from 'node:test';
import vm from 'node:vm';

import {createProjectRestoreRoute, createProjectSnapshots, helperRunner, projectsNamed, SNAPSHOT_DIRECTORY}
  from '../plugin/project-snapshots.mjs';

const temporary = [];
const scratch = prefix => { const folder = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix))); temporary.push(folder); return folder; };
after(() => { for (const folder of temporary) fs.rmSync(folder, {recursive: true, force: true}); });

const POSIX = process.platform !== 'win32' && fs.existsSync('/usr/bin/python3');
const USER = `ods-${'a'.repeat(64)}`;
const SESSION = `agent:pixel:openai-user:${USER}`;
const run = n => `chatcmpl_00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const UNDO = 'restore_00000000-0000-4000-8000-00000000000a';
// A run ID as a client may spell it, and as the helper keeps it.
const MIXED = 'chatcmpl_ABCDEF00-0000-4000-8000-00000000000A', LOWER = MIXED.toLowerCase();
const ORIGINAL = 'export const chars = ["elf", "orc"];\n';
const FP = letter => letter.repeat(64);

function writeTree(folder, files) {
  for (const [name, body] of Object.entries(files)) {
    const target = path.join(folder, ...name.split('/'));
    fs.mkdirSync(path.dirname(target), {recursive: true});
    if (body !== null) fs.writeFileSync(target, body);
    else fs.mkdirSync(target, {recursive: true});
  }
}

const receipt = fields => ({schemaVersion: 2, status: 'restored', project: 'Playground/game', snapshot: run(1), written: 1,
  removed: 0, left: [], leftCount: 0, failed: [], failedCount: 0, failure: null, late: false, undo: UNDO, confirm: null, ...fields});
const DEFAULTS = {
  fingerprint: () => ({projects: {game: FP('a')}, complete: true}),
  copy: request => ({projects: Object.fromEntries(request.projects.map(item => [item.name, {status: 'kept', late: false}]))}),
  after: request => ({projects: Object.fromEntries(request.projects.map(item => [item.name, {status: 'changed'}]))}),
  restore: request => receipt({snapshot: request.snapshot}),
  options: () => ({projects: []}),
};

// A workspace with Playground projects, and the snapshots of one gateway
// talking to a scripted helper that records every request.
function fixture({projects = {game: {'chars.js': ORIGINAL}}, script = {}, busy = () => false, portalActive = () => false,
  ownerRun, now} = {}) {
  const root = scratch('ods-snapshot-workspace-');
  for (const [name, files] of Object.entries(projects)) {
    fs.mkdirSync(path.join(root, 'Playground', name), {recursive: true});
    writeTree(path.join(root, 'Playground', name), files);
  }
  const requests = [], warnings = [], changes = [];
  const runHelper = async request => {
    requests.push(request);
    const answer = script[request.operation] ?? DEFAULTS[request.operation];
    return typeof answer === 'function' ? answer(request) : answer;
  };
  const snapshots = createProjectSnapshots({workspaceRoot: () => root, busy, portalActive, runHelper, stateDir: '/state/copies',
    changed: (...args) => changes.push(args), warn: message => warnings.push(message), waitMs: 50,
    ...(ownerRun ? {ownerRun} : {}), ...(now ? {now} : {})});
  const call = (toolName, params, {runId = run(1), decision, agentId = 'pixel'} = {}) =>
    snapshots.before({toolName, params}, {agentId, runId, sessionKey: SESSION, toolName}, decision);
  const restore = (fields = {}) => snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(1), ...fields});
  const ops = () => requests.map(request => request.operation);
  return {root, snapshots, requests, warnings, changes, call, restore, ops};
}

// ------------------------------------------------------- what a call touches --

test('every way a call names a project is found, and a Playground-wide mention touches them all', () => {
  const root = '/home/owner/.openclaw/workspace-pixel', folders = () => ['game', 'notes', 'elf-of-doom'];
  const named = (toolName, params) => projectsNamed(toolName, params, root, folders);
  const exec = (command, workdir) => named('exec', {command, ...(workdir ? {workdir} : {})});
  const all = names => ({names, all: true}), only = (...names) => ({names, all: false});
  // The reviewer's misses: each now touches every project (or names it).
  assert.deepEqual(exec('cd Playground && cd game && sed -i s/a/b/ chars.js'), all([]));
  assert.deepEqual(exec('cd Playground; python3 game/fix.py'), all([]));
  assert.deepEqual(exec(`python3 -c "import os; p=os.path.join('Playground','game','chars.js'); open(p,'a').write('x')"`), all([]));
  assert.deepEqual(exec("python3 - <<'PY'\nfrom pathlib import Path\np = Path('Playground') / 'game' / 'chars.js'\nPY"), all([]));
  assert.deepEqual(exec('rm -rf Playground/*'), all([]));
  assert.deepEqual(exec('sed -i s/a/b/ Playground/*/chars.js'), all([]));
  assert.deepEqual(exec('rm -rf Playground/{game,notes}'), all([]));
  assert.deepEqual(exec('find Playground -name "*.js" -exec sed -i s/a/b/ {} +'), all([]));
  assert.deepEqual(exec('cd ../notes && rm -f *.md', 'Playground/game'), all(['game', 'notes']));
  assert.deepEqual(exec('cd game && python3 fix.py', 'Playground'), all(['game']));
  assert.deepEqual(exec('x'.repeat(1024 * 1024 + 1)), all([]), 'a command too long to read touches them all');
  assert.deepEqual(exec('sed -i s/a/b/ Playground/game*/chars.js'), all([]));
  // A named project left with .. may reach any other.
  assert.deepEqual(exec('cd Playground/game && cd ../notes && rm -f *'), all(['game']));
  assert.deepEqual(exec('cat Playground/game/../notes/b.md > /dev/null && rm Playground/game/../notes/b.md'), all(['game']));
  assert.deepEqual(exec('cd /workspace/Playground/game; rm -rf ../notes'), all(['game']));
  assert.deepEqual(exec("rm -rf '..'", 'Playground/game'), all(['game']));
  assert.deepEqual(named('process', {action: 'write', sessionId: 's1', data: 'cd Playground/game\ncd ..\n'}), all(['game']));
  // Named projects.
  assert.deepEqual(exec("cd /workspace/Playground/elf-of-doom; python3 - <<'PY'\nsrc=open('chars.js').read()\nPY"), only('elf-of-doom'));
  assert.deepEqual(exec('node -e "const a=[...b]; f(...a)" Playground/game/x.js'), only('game'), 'the ... of code is no parent folder');
  assert.deepEqual(exec('node -e "g(...args)"', 'Playground/game'), only('game'));
  assert.deepEqual(exec('python3 fix.py', 'Playground/game/src'), only('game'));
  assert.deepEqual(exec(`cat ${root}/Playground/notes/a.md && cat "Playground\\game\\x"`), only('notes', 'game'));
  assert.deepEqual(named('process', {action: 'write', sessionId: 's1', data: "open('Playground/game/chars.js','w').write('')\n"}), only('game'));
  assert.deepEqual(named('tool_call', {id: 'openclaw:core:exec', args: {command: 'cat Playground/game/x'}}), only('game'));
  assert.deepEqual(named('tool_call', {id: 'openclaw:core:process', args: {action: 'write', sessionId: 's1',
    data: "open('Playground/notes/a.md','w')"}}), only('notes'));
  assert.deepEqual(named('read', {path: 'Playground/game/chars.js'}), only('game'));
  assert.deepEqual(named('write', {file_path: `${root}/Playground/Game/a.js`}), only('Game'));
  assert.deepEqual(named('edit', {filePath: '/workspace/Playground/notes/a.md'}), only('notes'));
  assert.deepEqual(named('apply_patch', {input: '*** Begin Patch\n*** Update File: Playground/game/a.js\n*** Add File: /workspace/Playground/notes/b.md\n*** End Patch'}),
    only('game', 'notes'));
  assert.deepEqual(named('pixel_ods_project_build', {project: 'Playground/site'}), only('site'));
  assert.deepEqual(named('pixel_ods_workspace_bundle', {outputRoot: 'Playground/out-site', files: [{source: 'Playground/game/a.js'}]}),
    only('out-site', 'game'));
  // Nothing outside Playground, and no look-alike.
  for (const [toolName, params] of [['read', {path: '/etc/passwd'}], ['write', {path: '../Playground/game/x'}],
    ['write', {path: 'Playgrounds/game/x'}], ['exec', {command: 'python3 tools/fix_all_projects.py'}],
    ['exec', {command: 'cat MyPlayground/x playground_test/y'}], ['read', {path: 'Playground/../secret'}],
    ['write', {path: 'Playground/con/x'}], ['exec', {command: 'ls', workdir: '/tmp/Playground/game'}]]) {
    assert.deepEqual(named(toolName, params), only(), JSON.stringify(params));
  }
});

// ------------------------------------------------------ a response's copies --

test("a response's first call that may change files takes one fingerprint, before its first copy", async () => {
  const f = fixture({projects: {game: {'chars.js': ORIGINAL}, notes: {'a.md': 'a'}}});
  await f.call('read', {path: 'Playground/game/chars.js'});
  assert.deepEqual(f.ops(), ['copy'], 'a read copies, but takes no fingerprint');
  assert.deepEqual(f.requests[0].projects, [{name: 'game', start: null}]);
  await f.call('exec', {command: 'python3 fix.py', workdir: 'Playground/notes'});
  await f.call('write', {path: 'Playground/notes/b.md', content: 'b'});
  await f.call('exec', {command: 'ls'});
  assert.deepEqual(f.ops(), ['copy', 'fingerprint', 'copy'], 'one fingerprint and one copy per project per response');
  assert.deepEqual(f.requests[2], {operation: 'copy', workspace: f.root, id: run(1), root: '/state/copies', live: [run(1)],
    projects: [{name: 'notes', start: 'absent'}]}, 'a project a complete fingerprint did not see is new');
  assert.equal('live' in f.requests[1], false, 'a fingerprint ends no copy');
  await f.call('write', {path: 'Playground/game/chars.js', content: 'x'}, {runId: run(2)});
  assert.deepEqual(f.ops().slice(3), ['fingerprint', 'copy']);
  assert.deepEqual(f.requests.at(-1).projects, [{name: 'game', start: FP('a')}]);
  assert.deepEqual(f.warnings, []);
});

test('a project absent from a complete fingerprint is new; an incomplete or failed fingerprint marks nothing', async () => {
  for (const [fingerprint, start] of [[{projects: {}, complete: true}, 'absent'], [{projects: {}, complete: false}, null],
    [{error: 'unavailable'}, null]]) {
    const f = fixture({script: {fingerprint}});
    await f.call('write', {path: 'Playground/game/chars.js', content: 'x'});
    assert.deepEqual(f.requests.at(-1).projects, [{name: 'game', start}]);
    assert.equal(f.warnings.length, fingerprint.error ? 1 : 0);
  }
});

test('a Playground-wide mention copies the sixteen most recently changed projects', async () => {
  const projects = Object.fromEntries(Array.from({length: 20}, (_, n) => [`p${String(n).padStart(2, '0')}`, {'a.js': 'a'}]));
  const f = fixture({projects});
  for (const [n, name] of Object.keys(projects).entries()) {
    const time = new Date(Date.UTC(2026, 0, 1, 0, n));
    fs.utimesSync(path.join(f.root, 'Playground', name), time, time);
  }
  await f.call('exec', {command: 'rm -rf Playground/*'});
  assert.deepEqual(f.requests.at(-1).projects.map(item => item.name),
    Array.from({length: 16}, (_, n) => `p${String(19 - n).padStart(2, '0')}`));
  // A file edited in place leaves its folder's time: the fingerprint's newest change inside counts.
  const g = fixture({projects, script: {fingerprint: () => ({projects: {}, complete: false,
    newest: {p00: Date.UTC(2026, 1, 1), p01: Date.UTC(2026, 1, 2)}})}});
  for (const [n, name] of Object.keys(projects).entries()) {
    const time = new Date(Date.UTC(2026, 0, 1, 0, n));
    fs.utimesSync(path.join(g.root, 'Playground', name), time, time);
  }
  await g.call('exec', {command: 'sed -i s/a/b/ Playground/*/a.js'});
  assert.deepEqual(g.requests.at(-1).projects.map(item => item.name).slice(0, 3), ['p01', 'p00', 'p19']);
  assert.equal(g.requests.at(-1).projects.length, 16);
});

test('nothing is copied for a refused call, another agent, a run that is no Portal response, or a linked project', async () => {
  const f = fixture({projects: {game: {'chars.js': ORIGINAL}, real: {}}});
  fs.symlinkSync(path.join(f.root, 'Playground', 'real'), path.join(f.root, 'Playground', 'linked'),
    process.platform === 'win32' ? 'junction' : 'dir');
  await f.call('write', {path: 'Playground/game/a.js'}, {decision: {block: true, blockReason: 'refused'}});
  await f.call('write', {path: 'Playground/game/a.js'}, {agentId: 'other'});
  await f.call('write', {path: 'Playground/game/a.js'}, {runId: 'cron-run-1'});
  assert.deepEqual(f.ops(), []);
  await f.call('read', {path: 'Playground/linked/a.js'});
  assert.deepEqual(f.ops(), [], 'a project folder that is a link is no project');
});

test('a system without what the helper needs is asked once, quietly, and never again', async () => {
  for (const operation of ['fingerprint', 'copy']) {
    const f = fixture({script: {[operation]: {error: 'unsupported'}}});
    await f.call(operation === 'copy' ? 'read' : 'write', {path: 'Playground/game/a.js'});
    await f.call('write', {path: 'Playground/game/b.js'}, {runId: run(2)});
    await f.snapshots.end({runId: run(1)}, {agentId: 'pixel', runId: run(1)});
    assert.deepEqual(f.ops(), [operation]);
    assert.deepEqual(f.warnings, []);
  }
});

test('a delegated run keeps its copy under the owner run it works for', async () => {
  const f = fixture({ownerRun: context => context.runId === run(9) ? run(1) : undefined});
  await f.call('write', {path: 'Playground/game/a.js'}, {runId: run(9)});
  await f.call('write', {path: 'Playground/game/b.js'}, {runId: run(1)});
  assert.deepEqual(f.ops(), ['fingerprint', 'copy']);
  assert.equal(f.requests[1].id, run(1));
});

test('a copy that fails is retried by the next call that touches the project, up to three times, and never throws', async () => {
  let failures = 0;
  const f = fixture({script: {copy: request => {
    failures += 1;
    if (failures === 1) throw new Error('helper crashed');
    if (failures === 2) return {error: 'copies-unusable'};
    return {projects: {[request.projects[0].name]: {error: 'unavailable'}}};
  }}});
  for (let n = 0; n < 5; n += 1) await f.call('read', {path: 'Playground/game/chars.js'});
  assert.deepEqual(f.ops(), ['copy', 'copy', 'copy']);
  assert.deepEqual(f.warnings, [
    'Portal could not keep a copy of Playground/game before this turn (helper crashed).',
    'Portal could not keep a copy of Playground/game before this turn (copies-unusable).',
    'Portal could not keep a copy of Playground/game before this turn (unavailable).',
  ]);
  const g = fixture({script: {copy: () => ({projects: {}})}});
  g.snapshots.before(null, null, null);
  await g.snapshots.before({toolName: 'read', params: {path: 'Playground/game/a.js'}},
    {agentId: 'pixel', runId: run(1), get toolName() { throw new Error('broken context'); }});
  assert.deepEqual(g.warnings, ['Portal could not keep a project copy before this turn (broken context).']);
  await g.call('read', {path: 'Playground/game/a.js'});
  await g.call('read', {path: 'Playground/game/a.js'});
  assert.equal(g.ops().length, 2, 'a copy the helper did not confirm is tried again');
});

test("the turn's end records how it left the projects it kept; options and restores wait for it", async () => {
  let finish;
  const f = fixture({projects: {game: {}, notes: {}}, script: {after: request => new Promise(resolve => {
    finish = () => resolve({projects: Object.fromEntries(request.projects.map(item => [item.name, {status: 'changed'}]))});
  })}});
  await f.call('exec', {command: 'touch Playground/game/a Playground/notes/b'});
  const ending = f.snapshots.end({runId: run(1)}, {agentId: 'pixel', runId: run(1)});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(f.requests.at(-1), {operation: 'after', workspace: f.root, id: run(1), root: '/state/copies', live: [],
    projects: [{name: 'game'}, {name: 'notes'}]});
  const options = f.snapshots.options({runs: [run(1).toUpperCase()]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.ops().includes('options'), false, 'options waits for the end');
  finish();
  await ending;
  assert.deepEqual(await options, {status: 200, body: {schemaVersion: 1, projects: []}});
  assert.deepEqual(f.requests.at(-1), {operation: 'options', workspace: f.root, root: '/state/copies', live: [], runs: [run(1)]});
  await f.snapshots.end({runId: run(2)}, {agentId: 'pixel', runId: run(2)});
  await f.snapshots.end({runId: run(1)}, {agentId: 'other', runId: run(1)});
  assert.equal(f.ops().filter(item => item === 'after').length, 1, 'a run that kept nothing, or another agent, records nothing');
});

test('the helper hears which runs are live: a run until its chat session ends it, then while its chat is busy', async () => {
  let chatBusy = false;
  const f = fixture({busy: () => chatBusy});
  await f.call('read', {path: 'Playground/game/chars.js'});
  await f.call('read', {path: 'Playground/game/chars.js'}, {runId: run(2)});
  assert.deepEqual(f.requests.at(-1).live, [run(1), run(2)]);
  await f.snapshots.end({runId: run(1)}, {agentId: 'pixel', runId: run(1), sessionKey: SESSION});
  assert.deepEqual(f.requests.at(-1), {operation: 'after', workspace: f.root, id: run(1), root: '/state/copies',
    live: [run(2)], projects: [{name: 'game'}]});
  chatBusy = true; // its delegated work still runs
  await f.snapshots.options({runs: [run(1)]});
  assert.deepEqual(f.requests.at(-1).live, [run(1), run(2)]);
  chatBusy = false;
  await f.call('read', {path: 'Playground/notes/a.md'}, {runId: run(1)}); // a continuation of run 1
  await f.snapshots.restore();
  assert.deepEqual(f.requests.at(-1).live, [run(1), run(2)]);
});

test("a delegated child's end records nothing while its owner's session works, and the owner's end records it all", async () => {
  const child = 'f00dcafe-1111-4222-8333-944455556666', childSession = 'agent:pixel:subagent:f00dcafe-1111-4222-8333-944455556666';
  const f = fixture({ownerRun: context => [child, 'announce:v1:x:y'].includes(context.runId) ? run(1) : undefined});
  const childCall = () => f.snapshots.before({toolName: 'write', params: {path: 'Playground/game/a.js'}},
    {agentId: 'pixel', runId: child, sessionKey: childSession, toolName: 'write'}, undefined);
  const childEnd = () => f.snapshots.end({runId: child}, {agentId: 'pixel', runId: child, sessionKey: childSession});
  await f.call('exec', {command: 'echo spawn'});
  await childCall();
  await childEnd();
  assert.equal(f.ops().includes('after'), false, 'the owner still works');
  await f.snapshots.end({runId: run(1)}, {agentId: 'pixel', runId: run(1), sessionKey: SESSION});
  assert.deepEqual(f.ops().filter(item => item === 'after'), ['after']);
  // A child that outlives its owner's session records the response's end.
  await childCall();
  await childEnd();
  assert.deepEqual(f.ops().filter(item => item === 'after'), ['after', 'after']);
  // So does a continuation announced into the chat, also without a session key.
  await f.snapshots.end({runId: 'announce:v1:x:y'}, {agentId: 'pixel', runId: 'announce:v1:x:y'});
  assert.deepEqual(f.ops().filter(item => item === 'after'), ['after', 'after', 'after']);
});

// ------------------------------------------------------------ the restore --

test('a restore passes the receipt through in the spelling asked, and tells the example guard', async () => {
  const f = fixture();
  const answer = await f.restore({snapshot: MIXED});
  assert.deepEqual(answer, {status: 200, body: receipt({snapshot: MIXED})});
  assert.deepEqual(f.requests.at(-1), {operation: 'restore', workspace: f.root, root: '/state/copies', live: [], project: 'game', snapshot: LOWER});
  assert.deepEqual(f.changes, [[f.root, 'game']]);
  const confirm = FP('c');
  const g = fixture({script: {restore: receipt({status: 'confirm', written: 0, undo: null,
    confirm: {token: confirm, overwrite: ['chars.js'], overwriteCount: 1, remove: [], removeCount: 0}})}});
  assert.equal((await g.restore()).body.confirm.token, confirm);
  assert.deepEqual(g.changes, [], 'a restore that waits for a yes changed nothing');
  await g.restore({confirm});
  assert.equal(g.requests.at(-1).confirm, confirm);
});

test('a restore is refused while any Portal run, tool or background command is alive, also when one starts meanwhile', async () => {
  const user = [];
  let active = false;
  const f = fixture({busy: value => { user.push(value); return false; }, portalActive: () => active});
  active = true;
  assert.deepEqual(await f.restore(), {status: 409, body: {error: 'project-restore-busy'}});
  active = false;
  let release;
  const g = fixture({portalActive: () => active, script: {copy: () => new Promise(resolve => { release = resolve; })}});
  const copying = g.call('read', {path: 'Playground/game/chars.js'});
  await new Promise(resolve => setImmediate(resolve));
  const restoring = g.restore();
  active = true; // a run starts while the restore waits for the helper
  release({projects: {game: {status: 'kept', late: false}}});
  await copying;
  assert.deepEqual(await restoring, {status: 409, body: {error: 'project-restore-busy'}});
  assert.deepEqual(g.ops(), ['copy'], 'the helper never ran the refused restore');
  const h = fixture({busy: () => true});
  assert.equal((await h.restore()).status, 409);
  assert.deepEqual([user, f.ops(), h.ops()], [[USER], [], []]);
});

test("a call that may change files waits for a running restore; a read does not", async () => {
  let release;
  const f = fixture({projects: {game: {}, notes: {}}, script: {restore: request => new Promise(resolve => {
    release = () => resolve(receipt({snapshot: request.snapshot}));
  })}});
  const restoring = f.restore();
  await new Promise(resolve => setImmediate(resolve));
  let wrote = false, read = false;
  const writing = f.call('exec', {command: 'python3 tools/x.py'}).then(() => { wrote = true; });
  const reading = f.call('read', {path: 'Playground/notes/a.md'}, {runId: run(2)}).then(() => { read = true; });
  // A cron job's, webhook's or channel's run, and a child whose spawn receipt is unknown, wait the same way.
  let others = 0;
  const unowned = ['cron:job-1', '6f1c2b7e-1111-4222-8333-944455556666'].map(runId =>
    f.call('exec', {command: 'echo x >> Playground/game/chars.js'}, {runId}).then(() => { others += 1; }));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual([wrote, read, others], [false, false, 0], 'the read waits only for the helper queue');
  release();
  await Promise.all([restoring, writing, reading, ...unowned]);
  assert.equal(others, 2);
  assert.deepEqual(f.ops(), ['restore', 'copy', 'fingerprint']);
  const g = fixture();
  await g.call('read', {path: 'Playground/game/chars.js'}, {runId: 'cron:job-1'});
  assert.deepEqual(g.ops(), [], 'such a run keeps no copy: it is no Portal response');
});

test("the helper's refusals become fixed codes, and anything unexpected an unknown outcome", async () => {
  const cases = [
    [{error: 'unavailable-copy'}, 404, 'project-snapshot-unavailable'], [{error: 'pruned'}, 404, 'project-snapshot-pruned'],
    [{error: 'damaged'}, 422, 'project-snapshot-damaged'], [{error: 'folder-unusable'}, 422, 'project-folder-unusable'],
    [{error: 'copies-unusable'}, 503, 'project-copies-unusable'], [{error: 'unsupported'}, 503, 'project-restore-unsupported'],
    [{error: 'invalid'}, 503, 'project-restore-unavailable'], [{error: 'unavailable'}, 503, 'project-restore-unavailable'],
    [receipt({schemaVersion: 1}), 503, 'project-restore-unavailable'], [receipt({project: 'Playground/other'}), 503, 'project-restore-unavailable'],
    [receipt({snapshot: run(2)}), 503, 'project-restore-unavailable'], [receipt({undo: null}), 503, 'project-restore-unavailable'],
    [receipt({status: 'unchanged'}), 503, 'project-restore-unavailable'], [receipt({left: ['a'], leftCount: 0}), 503, 'project-restore-unavailable'],
    [receipt({failed: ['a\nb'], failedCount: 1}), 503, 'project-restore-unavailable'], [receipt({failure: 'disk'}), 503, 'project-restore-unavailable'],
    [receipt({left: ['notes\u0085.md'], leftCount: 1}), 503, 'project-restore-unavailable'],
    [{...receipt(), extra: 1}, 503, 'project-restore-unavailable'], [() => null, 503, 'project-restore-unavailable'],
    [() => { throw new Error('helper crashed'); }, 503, 'project-restore-unavailable'],
  ];
  for (const [answer, status, error] of cases) {
    const f = fixture({script: {restore: answer}});
    assert.deepEqual(await f.restore(), {status, body: {error}}, JSON.stringify(answer));
    assert.deepEqual(f.changes, []);
  }
});

test('a malformed request, a project that is not a usable folder or an unusable workspace changes nothing', async () => {
  const f = fixture({projects: {game: {}, real: {}}});
  for (const request of [{user: USER, project: 'Playground/game/x', snapshot: run(1)}, {user: USER, project: 'game', snapshot: run(1)},
    {user: 'ods-x', project: 'Playground/game', snapshot: run(1)}, {user: USER, project: 'Playground/game', snapshot: 'chatcmpl_1'},
    {user: USER, project: 'Playground/game', snapshot: run(1), confirm: 'yes'}, {user: USER, project: 'Playground/con', snapshot: run(1)},
    {user: USER, project: 'Playground/game', snapshot: run(1), extra: true}, null]) {
    assert.deepEqual(await f.snapshots.restore(request), {status: 400, body: {error: 'invalid-project-restore-request'}});
  }
  fs.symlinkSync(path.join(f.root, 'Playground', 'real'), path.join(f.root, 'Playground', 'linked'),
    process.platform === 'win32' ? 'junction' : 'dir');
  fs.writeFileSync(path.join(f.root, 'Playground', 'file'), 'x');
  for (const project of ['Playground/linked', 'Playground/file']) {
    assert.deepEqual(await f.restore({project}), {status: 422, body: {error: 'project-folder-unusable'}});
  }
  const g = createProjectSnapshots({workspaceRoot: () => 'relative', busy: () => false, portalActive: () => false,
    runHelper: () => assert.fail('no helper')});
  assert.deepEqual(await g.restore({user: USER, project: 'Playground/game', snapshot: run(1)}),
    {status: 503, body: {error: 'project-restore-unavailable'}});
  assert.deepEqual(f.ops(), []);
});

test('a missing project or Playground is put back by the helper; the restore asks for it by its folder spelling', async () => {
  const f = fixture({projects: {Game: {}}});
  const caseless = fs.existsSync(path.join(f.root, 'Playground', 'game'));
  await f.restore({project: 'Playground/game'});
  assert.equal(f.requests.at(-1).project, caseless ? 'Game' : 'game', 'on a case-sensitive disk game is another, missing project');
  const g = fixture({projects: {}});
  fs.rmSync(path.join(g.root, 'Playground'), {recursive: true, force: true});
  assert.equal((await g.restore()).status, 200);
  assert.equal(g.requests.at(-1).project, 'game');
});

// -------------------------------------------------------------- the options --

test('options name the latest turn per project in the spelling asked, with its undo; malformed answers are an unknown', async () => {
  const entry = {project: 'Playground/game', snapshot: LOWER, late: true, restore: {undo: UNDO, side: 'before'}};
  const f = fixture({script: {options: {projects: [entry]}}});
  assert.deepEqual(await f.snapshots.options({runs: [MIXED, run(2)]}), {status: 200, body: {schemaVersion: 1, projects: [{...entry, snapshot: MIXED}]}});
  assert.deepEqual(f.requests.at(-1).runs, [LOWER, run(2)]);
  for (const projects of [[{...entry, snapshot: run(2)}], [{...entry, restore: {undo: UNDO, side: 'later'}}],
    [{...entry, restore: {undo: run(1), side: 'after'}}], [{...entry, project: 'Playground/../x'}], [{...entry, extra: 1}],
    Array.from({length: 33}, () => entry)]) {
    const g = fixture({script: {options: {projects}}});
    assert.deepEqual(await g.snapshots.options({runs: [MIXED]}), {status: 503, body: {error: 'project-restore-unavailable'}});
  }
  const h = fixture({script: {options: {error: 'unsupported'}}});
  assert.deepEqual(await h.snapshots.options({runs: [run(1)]}), {status: 200, body: {schemaVersion: 1, projects: []}});
  const i = fixture({script: {options: {error: 'copies-unusable'}}});
  assert.deepEqual(await i.snapshots.options({runs: [run(1)]}), {status: 503, body: {error: 'project-copies-unusable'}});
  for (const request of [{runs: ['restore_x']}, {runs: Array.from({length: 65}, (_, n) => run(n))}, {runs: [run(1)], user: USER}, {}]) {
    assert.deepEqual(await f.snapshots.options(request), {status: 400, body: {error: 'invalid-project-restore-request'}});
  }
});

// ------------------------------------------------------------ the routes --

function exchange(snapshots, kind, {method = 'POST', url, type = 'application/json', body = ''} = {}) {
  const reply = {};
  const req = {method, url, headers: {'content-type': type}, async *[Symbol.asyncIterator]() { if (body) yield Buffer.from(body); }};
  const sendJson = (_res, status, payload) => Object.assign(reply, {status, payload});
  return createProjectRestoreRoute({snapshots, sendJson, kind})(req, {}).then(() => reply);
}

test('the gateway routes accept one exact request each', async () => {
  const requests = [];
  const snapshots = {restore: request => { requests.push(['restore', request]); return {status: 200, body: {ok: 1}}; },
    options: request => { requests.push(['options', request]); return {status: 200, body: {ok: 2}}; }};
  const restore = JSON.stringify({user: USER, project: 'Playground/game', snapshot: run(1), confirm: FP('c')});
  const options = JSON.stringify({runs: [run(1)]});
  assert.deepEqual(await exchange(snapshots, 'restore', {url: '/pixel-ods/project-restore', body: restore}), {status: 200, payload: {ok: 1}});
  assert.deepEqual(await exchange(snapshots, 'options', {url: '/pixel-ods/project-restore-options', body: options}), {status: 200, payload: {ok: 2}});
  assert.deepEqual(requests, [['restore', JSON.parse(restore)], ['options', JSON.parse(options)]]);
  assert.deepEqual(await exchange(snapshots, 'options', {url: '/pixel-ods/project-restore-options', method: 'GET'}),
    {status: 405, payload: {error: 'method not allowed'}});
  assert.deepEqual(await exchange(snapshots, 'restore', {url: '/pixel-ods/project-restore?x=1', body: restore}),
    {status: 400, payload: {error: 'invalid request'}});
  for (const fields of [{type: 'text/plain', body: restore}, {body: `{"runs":["${'x'.repeat(5000)}"]}`}, {body: 'not json'}]) {
    assert.deepEqual(await exchange(snapshots, 'restore', {url: '/pixel-ods/project-restore', ...fields}),
      {status: 400, payload: {error: 'invalid-project-restore-request'}});
  }
  assert.equal(requests.length, 2);
});

test('the helper is unsupported off POSIX, and its runner refuses an answer it cannot read', {skip: !POSIX && process.platform !== 'win32'}, async () => {
  if (process.platform === 'win32') {
    assert.deepEqual(await helperRunner()({operation: 'options'}), {error: 'unsupported'});
    return;
  }
  const folder = scratch('ods-snapshot-runner-');
  const script = (name, body) => { const file = path.join(folder, name); fs.writeFileSync(file, body); return file; };
  await assert.rejects(helperRunner({helper: script('exit.py', 'import sys; sys.exit(3)')})({}), /exited with 3/);
  await assert.rejects(helperRunner({helper: script('text.py', 'print("not json")')})({}), SyntaxError);
  await assert.rejects(helperRunner({helper: script('slow.py', 'import time; time.sleep(5)'), timeoutMs: 100})({}), /exited with null/);
  assert.deepEqual(await helperRunner({helper: script('echo.py', 'import json, sys; print(json.dumps(json.load(sys.stdin)))')})({a: 1}), {a: 1});
});

// --------------------------------------------------- the real helper, POSIX --

function realFixture({busy = () => false, portalActive = () => false} = {}) {
  const root = scratch('ods-snapshot-real-'), state = scratch('ods-snapshot-real-state-');
  const project = path.join(root, 'Playground', 'game');
  writeTree(project, {'chars.js': ORIGINAL, 'index.html': '<script src="chars.js"></script>\n', 'node_modules/x/y.js': 'dep'});
  const changes = [], warnings = [];
  const snapshots = createProjectSnapshots({workspaceRoot: () => root, busy, portalActive, stateDir: path.join(state, SNAPSHOT_DIRECTORY),
    changed: (...args) => changes.push(args), warn: message => warnings.push(message)});
  const call = (toolName, params, runId = run(1)) =>
    snapshots.before({toolName, params}, {agentId: 'pixel', runId, sessionKey: SESSION, toolName}, undefined);
  const end = runId => snapshots.end({runId}, {agentId: 'pixel', runId});
  return {root, project, snapshots, changes, warnings, call, end, state};
}

test('end to end: an exec that doubles a file is put back, the undo reverses it, and both survive a reload', {skip: !POSIX && 'POSIX descriptors'}, async () => {
  const f = realFixture();
  await f.call('exec', {command: "python3 - <<'PY'\nfrom pathlib import Path\np = Path('Playground') / 'game' / 'chars.js'\np.write_text(p.read_text() * 2)\nPY"});
  fs.writeFileSync(path.join(f.project, 'chars.js'), ORIGINAL + ORIGINAL);
  fs.writeFileSync(path.join(f.project, 'new.js'), 'new');
  await f.end(run(1));
  const listed = await f.snapshots.options({runs: [run(1)]});
  assert.deepEqual(listed, {status: 200, body: {schemaVersion: 1, projects: [{project: 'Playground/game', snapshot: run(1), late: false, restore: null}]}});
  const restored = await f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(1)});
  assert.equal(restored.status, 200, JSON.stringify(restored));
  assert.deepEqual([restored.body.status, restored.body.written, restored.body.removed, restored.body.left], ['restored', 1, 1, []]);
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL);
  assert.equal(fs.existsSync(path.join(f.project, 'new.js')), false);
  assert.equal(fs.readFileSync(path.join(f.project, 'node_modules/x/y.js'), 'utf8'), 'dep', 'bulk folders are left alone');
  assert.deepEqual((await f.snapshots.options({runs: [run(1)]})).body.projects[0].restore, {undo: restored.body.undo, side: 'before'});
  const again = await f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(1)});
  assert.deepEqual(again, restored, 'a retry gets the same receipt');
  const undone = await f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: restored.body.undo});
  assert.equal(undone.body.status, 'restored');
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL + ORIGINAL);
  assert.equal(fs.readFileSync(path.join(f.project, 'new.js'), 'utf8'), 'new');
  assert.deepEqual((await f.snapshots.options({runs: [run(1)]})).body.projects[0].restore, {undo: undone.body.undo, side: 'after'});
  assert.deepEqual(f.changes, [[f.root, 'game'], [f.root, 'game']]);
  assert.deepEqual(f.warnings, []);
  const mode = fs.statSync(path.join(f.state, SNAPSHOT_DIRECTORY)).mode & 0o777;
  assert.equal(mode & 0o077, 0, 'the copies are private');
});

test('end to end: later changes need a yes that names them, and a project folder swapped for a link is refused', {skip: !POSIX && 'POSIX descriptors'}, async () => {
  const f = realFixture();
  await f.call('write', {path: 'Playground/game/chars.js', content: 'x'});
  fs.writeFileSync(path.join(f.project, 'chars.js'), 'turn');
  await f.end(run(1));
  fs.writeFileSync(path.join(f.project, 'chars.js'), 'owner edit after the turn');
  const asked = await f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(1)});
  assert.deepEqual([asked.body.status, asked.body.confirm.overwrite, asked.body.confirm.removeCount], ['confirm', ['chars.js'], 0]);
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), 'owner edit after the turn');
  const yes = await f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(1), confirm: asked.body.confirm.token});
  assert.equal(yes.body.status, 'restored');
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL);
  const outside = scratch('ods-snapshot-outside-');
  fs.renameSync(f.project, path.join(f.root, 'Playground', 'moved'));
  fs.symlinkSync(outside, f.project);
  assert.deepEqual(await f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: yes.body.undo}),
    {status: 422, body: {error: 'project-folder-unusable'}});
  assert.deepEqual(fs.readdirSync(outside), []);
});

// ------------------------------------------------- the plugin entry wiring --

const ENTRY = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
function slice(start, end) {
  const from = ENTRY.indexOf(start), to = ENTRY.indexOf(end, from);
  assert.ok(from >= 0 && to > from, start);
  return ENTRY.slice(from, to);
}

test('the plugin entry waits for the copy in before_tool_call, after the final decision, never for a probe', async () => {
  const seen = [];
  let guardDecision, release;
  const callbacks = {};
  vm.runInNewContext(slice('    api.on("before_tool_call",', '    api.on("after_tool_call",'), {
    api: {config: {}, on: (name, callback) => { callbacks[name] = callback; }},
    AGENT_ID: 'pixel', toolLoopGuard: {beforeToolCall: () => guardDecision},
    accessRuntime: {isProbe: context => context.runId === 'private-proof', beforeTool: () => undefined},
    goalProgress: {before() {}}, bundleAdmission: {before() {}}, artifactAdmission: {before() {}}, taskActivity: {before() {}},
    projectRunControl: {before() {}}, delegationDelivery: {blocked() {}, before() {}, hasPendingChildren() { return false; }},
    withPixelCronDeliveryDefault: decision => decision, withPixelSubagentWorkspace: decision => decision,
    withCronCommandPayloadBlock: decision => decision, getSessionEntry: () => undefined, resolveStorePath: () => undefined,
    resolveUserPath: value => value, resolveAgentWorkspaceDir: () => '/w',
    projectSnapshots: {before: (...args) => { seen.push(args); return new Promise(resolve => { release = resolve; }); }},
  });
  const context = runId => ({agentId: 'pixel', runId, sessionKey: SESSION, toolName: 'exec', toolCallId: `call-${runId}`});
  const event = {toolName: 'exec', toolCallId: 'call', params: {command: 'python3 fix.py Playground/game/chars.js'}};
  await callbacks.before_tool_call(event, context('private-proof'));
  assert.deepEqual(seen, []);
  guardDecision = {params: {command: 'wrapped'}};
  let returned;
  const pending = callbacks.before_tool_call(event, context(run(2))).then(value => { returned = value; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(returned, undefined, 'the call waits until its copy is kept');
  assert.deepEqual(seen, [[event, context(run(2)), guardDecision]]);
  release();
  await pending;
  assert.deepEqual(returned, guardDecision);
});

test("the plugin entry records each Portal run's end, never a probe's", () => {
  const ended = [];
  const callbacks = {};
  const noop = () => {};
  vm.runInNewContext(slice('    api.on("agent_end",', '    api.on("before_tool_call",'), {
    api: {on: (name, callback) => { callbacks[name] = callback; }, logger: {warn: noop}},
    delegationDelivery: {end: noop}, conversationImageLifecycle: {observe: noop},
    toolLoopGuard: {endPreviewRevalidation: noop, observeAgentEnd: noop},
    accessRuntime: {isProbe: context => context.runId === 'private-proof', finish: noop},
    goalProgress: {finish: noop}, taskActivity: {finish: noop}, managedRuntime: undefined,
    projectSnapshots: {end: (...args) => { ended.push(args); return Promise.resolve(); }},
  });
  callbacks.agent_end({runId: run(1)}, {agentId: 'pixel', runId: run(1)});
  callbacks.agent_end({runId: 'private-proof'}, {agentId: 'pixel', runId: 'private-proof'});
  assert.deepEqual(ended, [[{runId: run(1)}, {agentId: 'pixel', runId: run(1)}]]);
});

test('the plugin entry builds the copies with the chat, runtime, delegation and example wiring, and registers both routes', () => {
  const calls = [], routes = [];
  let options;
  const context = {
    api: {config: {agents: {list: [{id: 'pixel', workspace: '/owner/workspace'}]}}, logger: {warn: message => calls.push(['warn', message])},
      registerHttpRoute: value => { routes.push(value); }},
    AGENT_ID: 'pixel', projectSnapshots: undefined, sendJson: 'sendJson', currentManagedRuntime: undefined,
    accessRuntime: {status: () => ({active: 0})},
    createProjectSnapshots: value => { options = value; return {restore() {}, options() {}}; },
    createProjectRestoreRoute: value => ({route: value}),
    resolveActiveEmbeddedRunSessionId: key => { calls.push(['active', key]); return key.endsWith('busy') ? 'session' : undefined; },
    delegationDelivery: {ownerRunOf: value => `owner-of-${value.runId}`, pending: (user, isActive) => { calls.push(['pending', user, isActive('child-busy')]); return false; }},
    toolLoopGuard: {exampleOwnerChanged: (...args) => calls.push(['example', ...args])},
  };
  vm.runInNewContext(slice('    projectSnapshots ??= createProjectSnapshots({', "    api.on('subagent_spawned'"), context);
  assert.equal(options.workspaceRoot(), '/owner/workspace');
  assert.equal(options.ownerRun({runId: 'child'}), 'owner-of-child');
  assert.equal(options.busy(USER), false);
  assert.deepEqual(calls.splice(0), [['active', SESSION], ['active', 'child-busy'], ['pending', USER, true]]);
  // Any run, tool or background command counts, from the managed runtime when there is one.
  assert.equal(options.portalActive(), false);
  context.accessRuntime.status = () => ({active: 1, activity: {runs: 0, tools: 0, detached: 1}});
  assert.equal(options.portalActive(), true);
  context.accessRuntime.status = () => undefined;
  assert.equal(options.portalActive(), true, 'an unknown state counts as busy');
  context.currentManagedRuntime = {status: () => ({active: 0})};
  assert.equal(options.portalActive(), false);
  context.currentManagedRuntime = {status: () => ({active: 2})};
  assert.equal(options.portalActive(), true);
  options.changed('/owner/workspace', 'elf-of-doom');
  options.warn('note');
  assert.deepEqual(calls, [['example', '/owner/workspace', 'elf-of-doom'], ['warn', 'note']]);
  vm.runInNewContext(slice('    // Portal\'s "Put back files": the owner restores', "    // Portal's sidebar lists the shipped example project"), context);
  assert.deepEqual(routes.map(route => [route.path, route.auth, route.match, route.handler.route.kind ?? 'restore']), [
    ['/pixel-ods/project-restore', 'gateway', 'exact', 'restore'],
    ['/pixel-ods/project-restore-options', 'gateway', 'exact', 'options'],
  ]);
  assert.ok(routes.every(route => route.handler.route.sendJson === 'sendJson' && route.handler.route.snapshots.restore));
});
