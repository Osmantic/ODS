// Portal's one-step undo for Playground projects (plugin/project-snapshots.mjs):
// the copy a response keeps before it first touches a project, what that copy
// leaves out, links, the owner's restore and its undo, refusals, retention,
// the gateway route, and the plugin entry's before_tool_call wiring.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, {after} from 'node:test';
import vm from 'node:vm';

import {captureProject, createProjectRestoreRoute, createProjectSnapshots, planRestore, projectSnapshotTesting, projectsNamed,
  SNAPSHOT_DIRECTORY, SNAPSHOT_LIMITS} from '../plugin/project-snapshots.mjs';

const temporary = [];
const scratch = prefix => { const folder = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix))); temporary.push(folder); return folder; };
after(() => { for (const folder of temporary) fs.rmSync(folder, {recursive: true, force: true}); });

// File links need a privilege Windows grants only in developer mode.
const FILE_LINKS = (() => {
  const folder = scratch('ods-snapshot-link-');
  try { fs.symlinkSync('target', path.join(folder, 'link'), 'file'); return true; }
  catch (error) { if (error.code === 'EPERM') return false; throw error; }
})();
const DIRECTORY_LINK = process.platform === 'win32' ? 'junction' : 'dir';

const USER = `ods-${'a'.repeat(64)}`;
const SESSION = `agent:pixel:openai-user:${USER}`;
const OTHER = `agent:pixel:openai-user:ods-${'b'.repeat(64)}`;
const run = n => `chatcmpl_00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const ORIGINAL = 'export const chars = ["elf", "orc"];\n';

function writeTree(folder, files) {
  for (const [name, body] of Object.entries(files)) {
    const target = path.join(folder, ...name.split('/'));
    fs.mkdirSync(path.dirname(target), {recursive: true});
    if (body !== null) fs.writeFileSync(target, body);
    else fs.mkdirSync(target, {recursive: true});
  }
}

// Every entry below folder: a file's text, '<dir>' or '<link>'. Links are
// listed, never entered.
function tree(folder, prefix = '') {
  const found = {};
  if (!fs.existsSync(folder)) return found;
  for (const name of fs.readdirSync(folder).sort()) {
    const target = path.join(folder, name), stat = fs.lstatSync(target), key = prefix + name;
    if (stat.isSymbolicLink()) found[key] = '<link>';
    else if (stat.isDirectory()) { found[key] = '<dir>'; Object.assign(found, tree(target, `${key}/`)); }
    else found[key] = fs.readFileSync(target, 'utf8');
  }
  return found;
}

function fixture({files = {'chars.js': ORIGINAL, 'index.html': '<script src="chars.js"></script>\n'}, busy = () => false,
  active = () => false, limits = SNAPSHOT_LIMITS, now} = {}) {
  const root = scratch('ods-snapshot-workspace-'), state = scratch('ods-snapshot-state-');
  const project = path.join(root, 'Playground', 'game');
  fs.mkdirSync(project, {recursive: true});
  writeTree(project, files);
  const warnings = [], changes = [], stateDir = path.join(state, SNAPSHOT_DIRECTORY);
  const snapshots = createProjectSnapshots({workspaceRoot: () => root, busy, active, limits, stateDir,
    changed: (...args) => changes.push(args), warn: message => warnings.push(message), ...(now ? {now} : {})});
  // One tool call reaching before_tool_call with its final decision.
  const call = (toolName, params, {runId = run(1), sessionKey = SESSION, decision, agentId = 'pixel'} = {}) =>
    snapshots.before({toolName, params, toolCallId: 'call'}, {agentId, runId, sessionKey, toolName, toolCallId: 'call'}, decision);
  const restore = (snapshot, project_ = 'Playground/game', user = USER) => snapshots.restore({user, project: project_, snapshot});
  // The kept copies of one project: their folder names, newest first.
  const copies = () => {
    if (!fs.existsSync(stateDir)) return [];
    const stores = fs.readdirSync(stateDir);
    return stores.flatMap(store => fs.readdirSync(path.join(stateDir, store)).map(name => ({store, name})))
      .sort((a, b) => (a.name < b.name ? 1 : -1));
  };
  const manifest = copy => JSON.parse(fs.readFileSync(path.join(stateDir, copy.store, copy.name, 'manifest.json'), 'utf8'));
  return {root, project, stateDir, snapshots, warnings, changes, call, restore, copies, manifest};
}

// ------------------------------------------------------- the copy's moment --

test('a response keeps one copy of a project before it first touches it, and an exec that doubles a file is put back', () => {
  const f = fixture();
  // The 2026-10-30 case: a one-off Python patch run through exec in the project.
  const patch = {command: "python3 - <<'EOF'\nsource = open('chars.js').read()\nopen('chars.js', 'w').write(source + source)\nEOF",
    workdir: '/workspace/Playground/game'};
  f.call('exec', patch);
  assert.equal(f.copies().length, 1, 'kept before the exec ran');
  fs.writeFileSync(path.join(f.project, 'chars.js'), ORIGINAL + ORIGINAL);
  // Later calls of the same response, including a retried attempt, keep no second copy.
  f.call('read', {path: 'Playground/game/chars.js'});
  f.call('exec', patch);
  f.call('write', {path: '/workspace/Playground/game/extra.js', content: '1'});
  assert.equal(f.copies().length, 1);
  const copy = f.manifest(f.copies()[0]);
  assert.equal(copy.id, run(1));
  assert.equal(copy.kind, 'turn');
  assert.equal(copy.project, 'Playground/game');
  assert.deepEqual(copy.files.map(file => file.path), ['chars.js', 'index.html']);

  const restored = f.restore(run(1));
  assert.equal(restored.status, 200, JSON.stringify(restored.body));
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL);
  assert.match(restored.body.undo, /^restore_[0-9a-f-]{36}$/);
  assert.deepEqual({...restored.body, undo: null}, {schemaVersion: 1, project: 'Playground/game', snapshot: run(1), written: 1,
    removed: 0, kept: [], keptCount: 0, failed: [], failedCount: 0, complete: true, undo: null});
  // The undo puts back what the turn left.
  assert.equal(f.restore(restored.body.undo).status, 200);
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL + ORIGINAL);
  assert.deepEqual(f.warnings, []);
  // Each restore that changed the project is announced (the example's guard keeps it).
  assert.deepEqual(f.changes, [[f.root, 'game'], [f.root, 'game']]);
});

test('the copy is taken only for an admitted call of a Portal response, once per response and project', () => {
  const f = fixture({files: {'chars.js': ORIGINAL}});
  fs.mkdirSync(path.join(f.root, 'Playground', 'notes'));
  f.call('exec', {command: 'rm chars.js', workdir: '/workspace/Playground/game'}, {decision: {block: true, blockReason: 'no'}});
  f.call('read', {path: 'Playground/game/chars.js'}, {agentId: 'other'});
  f.call('read', {path: 'Playground/game/chars.js'}, {runId: 'cron:nightly'});
  f.call('read', {path: 'Playground/game/chars.js'}, {decision: {block: true}});
  assert.deepEqual(f.copies(), []);
  // The command ODS wraps for execution still names the project in the call the model wrote.
  f.call('exec', {command: 'sed -i s/elf/orc/ Playground/game/chars.js'},
    {decision: {params: {command: `'/run/pixel-ods-control/cancellable-exec.sh' id ${Buffer.from('x').toString('base64')}`, workdir: '/workspace'}}});
  assert.equal(f.copies().length, 1);
  // A routed path in the decision counts too: write path game.js routed into the project.
  f.call('write', {path: 'game.js', content: 'x'}, {runId: run(2), decision: {params: {path: 'Playground/game/game.js', content: 'x'}}});
  assert.equal(f.copies().length, 2);
  // Two projects in one call: one copy each.
  f.call('exec', {command: 'cp Playground/game/chars.js Playground/notes/'}, {runId: run(3)});
  assert.equal(f.copies().length, 4);
  assert.deepEqual(new Set(f.copies().map(copy => f.manifest(copy).project)), new Set(['Playground/game', 'Playground/notes']));
  // After a gateway restart the same response still keeps its first copy.
  fs.writeFileSync(path.join(f.project, 'chars.js'), 'changed by the turn');
  const restarted = createProjectSnapshots({workspaceRoot: () => f.root, stateDir: f.stateDir});
  restarted.before({toolName: 'read', params: {path: 'Playground/game/chars.js'}}, {agentId: 'pixel', runId: run(3), toolName: 'read'});
  assert.equal(f.copies().length, 4);
});

test('a delegated run keeps its copy under the owner run it works for', () => {
  const root = scratch('ods-snapshot-owner-'), stateDir = path.join(scratch('ods-snapshot-owner-state-'), SNAPSHOT_DIRECTORY);
  writeTree(path.join(root, 'Playground', 'game'), {'chars.js': ORIGINAL});
  const snapshots = createProjectSnapshots({workspaceRoot: () => root, stateDir, busy: () => false, active: () => false,
    ownerRun: context => context.runId === 'child-run' ? run(7) : undefined});
  const child = {agentId: 'pixel', runId: 'child-run', sessionKey: 'agent:pixel:subagent:child', toolName: 'edit'};
  snapshots.before({toolName: 'edit', params: {path: 'Playground/game/chars.js'}}, child);
  fs.writeFileSync(path.join(root, 'Playground', 'game', 'chars.js'), 'broken');
  snapshots.before({toolName: 'read', params: {path: 'Playground/game/chars.js'}}, {...child, runId: run(7), sessionKey: SESSION});
  const restored = snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(7)});
  assert.equal(restored.status, 200);
  assert.equal(fs.readFileSync(path.join(root, 'Playground', 'game', 'chars.js'), 'utf8'), ORIGINAL);
});

test('every way a call names a project is found, and nothing outside Playground', () => {
  const root = process.platform === 'win32' ? 'C:\\Users\\owner\\workspace' : '/home/owner/workspace';
  const names = (tool, params, folders) => projectsNamed(tool, params, root, folders).sort();
  assert.deepEqual(names('read', {path: 'Playground/game/chars.js'}), ['game']);
  assert.deepEqual(names('edit', {file_path: './Playground/game/a.js'}), ['game']);
  assert.deepEqual(names('write', {filePath: `${root}${path.sep}Playground${path.sep}game${path.sep}a.js`}), ['game']);
  assert.deepEqual(names('read', {path: '/workspace/playground/game/a.js'}), ['game']);
  assert.deepEqual(names('read', {path: 'Playground/other/../game/a.js'}), ['game']);
  assert.deepEqual(names('exec', {command: 'ls', workdir: '/workspace/Playground/game/src'}), ['game']);
  assert.deepEqual(names('exec', {command: 'node -e "require(\'fs\').writeFileSync(\'/workspace/Playground/game/a.js\', 1)"'}), ['game']);
  assert.deepEqual(names('exec', {command: 'cd Playground\\game && python fix.py; echo see Playground/notes.'}), ['game', 'notes']);
  // Run in the Playground folder itself, a command word that names a folder there counts.
  assert.deepEqual(names('exec', {command: 'cd game && python3 fix.py', workdir: '/workspace/Playground'}, () => ['game', 'notes']), ['game']);
  assert.deepEqual(names('apply_patch', {input: '*** Begin Patch\n*** Update File: Playground/game/a.js\n*** Move to: Playground/next/a.js\n*** Delete File: Playground/old/b.js\n*** End Patch'}),
    ['game', 'next', 'old']);
  assert.deepEqual(names('tool_call', {id: 'openclaw:core:exec', args: {command: 'true', workdir: 'Playground/game'}}), ['game']);
  assert.deepEqual(names('pixel_ods_project_build', {action: 'submit', project: 'Playground/game'}), ['game']);
  assert.deepEqual(names('pixel_ods_workspace_bundle', {outputRoot: 'Playground/game/out', mappingPath: 'm.json',
    files: [{source: 'Playground/notes/a.md', key: 'a', copyTo: 'a.md'}]}), ['game', 'notes']);
  assert.deepEqual(names('pixel_ods_workspace_preview', {relativeDirectory: 'Playground/game'}), ['game']);
  for (const [tool, params] of [['read', {path: '/etc/passwd'}], ['read', {path: '../Playground/game/a.js'}],
    ['read', {path: 'Playground/../../x/Playground/game'}], ['read', {path: 'Playground'}], ['read', {path: 'Playground/CON/a'}],
    ['read', {path: 'Playground/name./a'}], ['write', {path: 'notes/Playground/game/a.js'}], ['exec', {command: 'echo myplayground/game'}],
    ['exec', {command: 'ls', workdir: '/tmp/Playground/game'}], ['web_fetch', {url: 'https://x/Playground/game'}],
    ['tool_call', {id: 'x:constructor', args: {path: 'Playground/game/a'}}], ['constructor', {path: 'Playground/game/a'}],
    ['read', null], ['exec', {command: 42}]]) {
    assert.deepEqual(names(tool, params), [], JSON.stringify([tool, params]));
  }
});

// ------------------------------------------------- what a copy leaves out --

test('node_modules, dot-folders, large files and anything past the caps are left out, listed and left alone', () => {
  const limits = {...SNAPSHOT_LIMITS, fileBytes: 64, totalBytes: 90, files: 4, depth: 3};
  const f = fixture({limits, files: {
    'a.js': 'a'.repeat(40), 'b.js': 'b'.repeat(40), 'c.js': 'c'.repeat(10), 'd.js': 'd', 'e.js': 'e',
    '.env': 'KEY=1', 'big.bin': 'x'.repeat(65),
    'node_modules/lib/index.js': 'lib', '.git/HEAD': 'ref', '.cache/tmp': 'tmp', 'one/two/three/deep.js': 'deep',
  }});
  f.call('read', {path: 'Playground/game/a.js'});
  const copy = f.manifest(f.copies()[0]);
  // Sorted walk: .env, a.js and b.js fit (85 bytes); c.js would pass 90 bytes;
  // d.js fits as the fourth file; e.js would be a fifth.
  assert.deepEqual(copy.files.map(file => file.path), ['.env', 'a.js', 'b.js', 'd.js']);
  assert.deepEqual(copy.skipped.map(item => `${item.path}:${item.reason}`).sort(), ['.cache:folder', '.git:folder', 'big.bin:large',
    'c.js:limit', 'e.js:limit', 'node_modules:folder', 'one/two/three:folder'].sort());
  assert.equal(copy.complete, true);
  // The turn changes everything; the restore puts back only what it copied and removes nothing it left out.
  for (const name of ['a.js', 'c.js', 'e.js', 'big.bin', 'node_modules/lib/index.js', '.git/HEAD', 'one/two/three/deep.js']) {
    fs.writeFileSync(path.join(f.project, ...name.split('/')), 'changed');
  }
  fs.rmSync(path.join(f.project, 'b.js'));
  const restored = f.restore(run(1));
  assert.equal(restored.status, 200);
  const files = tree(f.project);
  assert.equal(files['a.js'], 'a'.repeat(40));
  assert.equal(files['b.js'], 'b'.repeat(40));
  for (const name of ['c.js', 'e.js', 'big.bin', 'node_modules/lib/index.js', '.git/HEAD', 'one/two/three/deep.js']) assert.equal(files[name], 'changed', name);
  assert.equal(restored.body.written, 2);
  assert.equal(restored.body.removed, 0);
  // Both copies count: the one taken before the restore, under the same caps,
  // reached four files at c.js, so it left d.js out too.
  assert.deepEqual(restored.body.kept, ['.cache', '.git', 'big.bin', 'c.js', 'd.js', 'e.js', 'node_modules', 'one/two/three']);
  assert.equal(restored.body.keptCount, 8);
  assert.equal(files['d.js'], 'd');
});

test('a project with more entries than a copy visits is copied in part and never loses files to a restore', () => {
  const f = fixture({limits: {...SNAPSHOT_LIMITS, entries: 3}, files: {'a.js': 'a', 'b.js': 'b', 'c.js': 'c', 'd.js': 'd'}});
  f.call('read', {path: 'Playground/game/a.js'});
  const copy = f.manifest(f.copies()[0]);
  assert.equal(copy.complete, false);
  assert.deepEqual(copy.files.map(file => file.path), ['a.js', 'b.js', 'c.js']);
  fs.writeFileSync(path.join(f.project, 'a.js'), 'changed');
  fs.writeFileSync(path.join(f.project, 'new.js'), 'new');
  const restored = f.restore(run(1));
  assert.equal(restored.body.complete, false);
  assert.equal(fs.readFileSync(path.join(f.project, 'a.js'), 'utf8'), 'a');
  assert.equal(fs.readFileSync(path.join(f.project, 'new.js'), 'utf8'), 'new', 'an incomplete copy removes nothing');
});

// ---------------------------------------------------------------- links --

test('links, hard links and a linked project are never followed, copied or written through', () => {
  const outside = scratch('ods-snapshot-outside-');
  fs.writeFileSync(path.join(outside, 'secret.txt'), 'SECRET-OUTSIDE');
  const f = fixture({files: {'chars.js': ORIGINAL, 'src/a.js': 'a'}});
  fs.symlinkSync(outside, path.join(f.project, 'assets'), DIRECTORY_LINK);
  fs.linkSync(path.join(outside, 'secret.txt'), path.join(f.project, 'hard.txt'));
  if (FILE_LINKS) fs.symlinkSync(path.join(outside, 'secret.txt'), path.join(f.project, 'link.txt'), 'file');
  f.call('read', {path: 'Playground/game/chars.js'});
  const copy = f.copies()[0], saved = f.manifest(copy);
  assert.deepEqual(saved.files.map(file => file.path), ['chars.js', 'src/a.js']);
  assert.deepEqual(saved.skipped.map(item => `${item.path}:${item.reason}`).sort(),
    ['assets:link', 'hard.txt:other', ...(FILE_LINKS ? ['link.txt:link'] : [])].sort());
  for (const blob of fs.readdirSync(path.join(f.stateDir, copy.store, copy.name))) {
    assert.doesNotMatch(fs.readFileSync(path.join(f.stateDir, copy.store, copy.name, blob), 'utf8'), /SECRET-OUTSIDE/);
  }
  // The turn swaps src for a link to the outside folder: the restore never writes through it.
  fs.rmSync(path.join(f.project, 'src'), {recursive: true});
  fs.symlinkSync(outside, path.join(f.project, 'src'), DIRECTORY_LINK);
  fs.writeFileSync(path.join(f.project, 'chars.js'), 'broken');
  const restored = f.restore(run(1));
  assert.equal(restored.status, 200);
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL);
  assert.deepEqual(tree(outside), {'secret.txt': 'SECRET-OUTSIDE'});
  assert.ok(fs.lstatSync(path.join(f.project, 'src')).isSymbolicLink());
  assert.ok(restored.body.kept.includes('src'), JSON.stringify(restored.body));
  assert.deepEqual(restored.body.failed, [], 'nothing below a link is even tried');
  assert.equal(fs.lstatSync(path.join(f.project, 'hard.txt')).nlink, 2);

  // A project folder that is itself a link is neither copied nor restored.
  fs.symlinkSync(outside, path.join(f.root, 'Playground', 'linked'), DIRECTORY_LINK);
  const before = f.copies().length;
  f.call('read', {path: 'Playground/linked/secret.txt'}, {runId: run(2)});
  assert.equal(f.copies().length, before);
  assert.deepEqual(f.restore(run(2), 'Playground/linked'), {status: 503, body: {error: 'project-restore-unavailable'}});
  assert.deepEqual(tree(outside), {'secret.txt': 'SECRET-OUTSIDE'});
});

test('a link that appears after the plan is never written or removed through', () => {
  const outside = scratch('ods-snapshot-outside-'), folder = path.join(scratch('ods-snapshot-race-'), 'game');
  writeTree(outside, {'a.js': 'outside a', 'b.js': 'outside b'});
  fs.mkdirSync(folder);
  fs.symlinkSync(outside, path.join(folder, 'src'), DIRECTORY_LINK);
  const file = {path: 'src/a.js', size: 5, sha256: 'a'.repeat(64), mode: 0o644};
  const result = projectSnapshotTesting.applyPlan(folder,
    {write: [file], remove: ['src/b.js'], removeDirs: ['src'], createDirs: ['src']}, new Map([[file.sha256, Buffer.from('saved')]]));
  assert.deepEqual(result, {written: 0, removed: 0, failed: ['src/b.js', 'src', 'src/a.js']});
  assert.deepEqual(tree(outside), {'a.js': 'outside a', 'b.js': 'outside b'});
  assert.ok(fs.lstatSync(path.join(folder, 'src')).isSymbolicLink());
});

test('a linked Playground or state folder keeps no copy', () => {
  const outside = scratch('ods-snapshot-outside-');
  const f = fixture();
  const moved = path.join(outside, 'Playground');
  fs.renameSync(path.join(f.root, 'Playground'), moved);
  fs.symlinkSync(moved, path.join(f.root, 'Playground'), DIRECTORY_LINK);
  f.call('read', {path: 'Playground/game/chars.js'});
  assert.deepEqual(f.copies(), []);
  const g = fixture();
  fs.mkdirSync(path.dirname(g.stateDir), {recursive: true});
  fs.symlinkSync(outside, g.stateDir, DIRECTORY_LINK);
  g.call('read', {path: 'Playground/game/chars.js'});
  // One attempt per response and project, not one per call.
  g.call('edit', {path: 'Playground/game/chars.js'});
  assert.equal(g.warnings.length, 1);
  assert.match(g.warnings[0], /could not keep a copy of Playground\/game/);
  assert.deepEqual(fs.readdirSync(outside), ['Playground']);
});

// --------------------------------------------------- restore and its undo --

test('a restore puts back changed, deleted and created files and folders, and its undo reverses it', () => {
  const f = fixture({files: {'chars.js': ORIGINAL, 'src/a.js': 'a', 'src/b.js': 'b', 'assets': null}});
  f.call('read', {path: 'Playground/game/chars.js'});
  const before = tree(f.project);
  // The turn: one file changed, one deleted, files and a folder created, an empty folder removed, a file turned into a folder.
  fs.writeFileSync(path.join(f.project, 'chars.js'), ORIGINAL + ORIGINAL);
  fs.rmSync(path.join(f.project, 'src', 'b.js'));
  writeTree(f.project, {'src/new.js': 'new', 'tmp/x/y.txt': 'y'});
  fs.rmdirSync(path.join(f.project, 'assets'));
  fs.rmSync(path.join(f.project, 'src', 'a.js'));
  writeTree(f.project, {'src/a.js/inner.txt': 'inner'});
  const turned = tree(f.project);

  const restored = f.restore(run(1));
  assert.equal(restored.status, 200, JSON.stringify(restored.body));
  assert.deepEqual(tree(f.project), before);
  assert.equal(restored.body.written, 3);
  assert.equal(restored.body.removed, 3);
  // Undo, then put back again: each is one step and exact.
  const undone = f.restore(restored.body.undo);
  assert.equal(undone.status, 200);
  assert.deepEqual(tree(f.project), turned);
  assert.equal(f.restore(run(1)).status, 200);
  assert.deepEqual(tree(f.project), before);
  // Nothing left to change: no undo copy is kept and no change is announced.
  const announced = f.changes.length;
  const unchanged = f.restore(run(1));
  assert.equal(unchanged.body.undo, null);
  assert.equal(unchanged.body.written + unchanged.body.removed, 0);
  assert.equal(f.changes.length, announced);
  // A deleted project comes back.
  fs.rmSync(f.project, {recursive: true});
  assert.equal(f.restore(run(1)).status, 200);
  assert.deepEqual(tree(f.project), before);
  // Nothing outside the project folder changed.
  assert.deepEqual(fs.readdirSync(f.root), ['Playground']);
  assert.deepEqual(fs.readdirSync(path.join(f.root, 'Playground')), ['game']);
});

test('a restore keeps executable modes', {skip: process.platform === 'win32' && 'POSIX modes'}, () => {
  const f = fixture({files: {'run.sh': '#!/bin/sh\n'}});
  fs.chmodSync(path.join(f.project, 'run.sh'), 0o755);
  f.call('read', {path: 'Playground/game/run.sh'});
  fs.chmodSync(path.join(f.project, 'run.sh'), 0o644);
  assert.equal(f.restore(run(1)).body.written, 1);
  assert.equal(fs.statSync(path.join(f.project, 'run.sh')).mode & 0o777, 0o755);
});

test('a project the turn created goes back to an empty folder', () => {
  const f = fixture();
  // Its folder did not exist when the turn first named it: the copy is empty.
  f.call('write', {path: 'Playground/fresh/index.html', content: 'x'}, {runId: run(2)});
  writeTree(path.join(f.root, 'Playground', 'fresh'), {'index.html': 'x', 'js/app.js': 'y'});
  const restored = f.restore(run(2), 'Playground/fresh');
  assert.equal(restored.status, 200);
  assert.equal(restored.body.removed, 2);
  assert.deepEqual(tree(path.join(f.root, 'Playground', 'fresh')), {});
  assert.deepEqual(fs.readdirSync(path.join(f.root, 'Playground')).sort(), ['fresh', 'game']);
});

test('the plan writes and removes only what both copies cover', () => {
  const file = (name, sha = 'a') => ({path: name, size: 1, sha256: sha.repeat(64), mode: 0o644});
  const saved = {complete: true, dirs: ['src'], files: [file('a.js'), file('src/b.js'), file('k.js')],
    skipped: [{path: 'node_modules', reason: 'folder'}]};
  const current = {complete: true, dirs: ['src', 'node_modules2'], files: [file('a.js', 'b'), file('node_modules/x.js'), file('src/c.js'),
    file('node_modules2/y.js')], skipped: [{path: 'k.js', reason: 'large'}]};
  assert.deepEqual(planRestore(saved, current), {write: [file('a.js'), file('src/b.js')], remove: ['src/c.js', 'node_modules2/y.js'],
    removeDirs: ['node_modules2'], createDirs: []});
  assert.deepEqual(planRestore({...saved, complete: false}, current).remove, []);
  assert.deepEqual(planRestore(saved, {...current, files: [file('a.js'), file('src/b.js')], dirs: []}).createDirs, ['src']);
});

// --------------------------------------------------------------- refusals --

test('a restore is refused while a response runs in the chat or touched the project, and changes nothing', () => {
  let chatBusy = true, otherActive = false;
  const f = fixture({busy: user => user === USER && chatBusy, active: key => key === OTHER && otherActive});
  f.call('read', {path: 'Playground/game/chars.js'});
  fs.writeFileSync(path.join(f.project, 'chars.js'), 'broken');
  assert.deepEqual(f.restore(run(1)), {status: 409, body: {error: 'project-restore-busy'}});
  chatBusy = false;
  // A response in another chat touched this project and still runs.
  f.call('edit', {path: 'Playground/game/chars.js'}, {runId: run(2), sessionKey: OTHER});
  otherActive = true;
  assert.deepEqual(f.restore(run(1)), {status: 409, body: {error: 'project-restore-busy'}});
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), 'broken');
  otherActive = false;
  assert.equal(f.restore(run(1)).status, 200);
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), ORIGINAL);
});

test('a missing, damaged or malformed request changes nothing', () => {
  const f = fixture();
  f.call('read', {path: 'Playground/game/chars.js'});
  fs.writeFileSync(path.join(f.project, 'chars.js'), 'broken');
  assert.deepEqual(f.restore(run(9)), {status: 404, body: {error: 'project-snapshot-unavailable'}});
  assert.deepEqual(f.restore(run(1), 'Playground/other'), {status: 404, body: {error: 'project-snapshot-unavailable'}});
  for (const project of ['Playground/../game', 'Playground/game/src', '/etc', 'Other/game', 'Playground/CON', 'Playground/game.', 'Playground/']) {
    assert.deepEqual(f.restore(run(1), project), {status: 400, body: {error: 'invalid-project-restore-request'}}, project);
  }
  for (const snapshot of ['restore_x', '../chatcmpl', `${run(1)}/..`, '']) {
    assert.deepEqual(f.restore(snapshot), {status: 400, body: {error: 'invalid-project-restore-request'}}, snapshot);
  }
  assert.equal(f.snapshots.restore({user: 'ods-x', project: 'Playground/game', snapshot: run(1)}).status, 400);
  assert.equal(f.snapshots.restore({user: USER, project: 'Playground/game', snapshot: run(1), extra: 1}).status, 400);
  // A stored copy whose bytes changed is refused before anything is written.
  const copy = f.copies()[0], folder = path.join(f.stateDir, copy.store, copy.name);
  const blob = fs.readdirSync(folder).find(name => name !== 'manifest.json' && fs.readFileSync(path.join(folder, name), 'utf8') === ORIGINAL);
  // Same size, other bytes: only the checksum tells.
  fs.writeFileSync(path.join(folder, blob), 'x'.repeat(ORIGINAL.length));
  assert.deepEqual(f.restore(run(1)), {status: 422, body: {error: 'project-snapshot-damaged'}});
  fs.writeFileSync(path.join(folder, blob), 'tampered');
  assert.deepEqual(f.restore(run(1)), {status: 422, body: {error: 'project-snapshot-damaged'}});
  fs.writeFileSync(path.join(folder, 'manifest.json'), '{"schemaVersion":1}');
  assert.deepEqual(f.restore(run(1)), {status: 422, body: {error: 'project-snapshot-damaged'}});
  assert.equal(fs.readFileSync(path.join(f.project, 'chars.js'), 'utf8'), 'broken');
  assert.equal(f.copies().length, 1, 'no undo copy for a refused restore');
  assert.deepEqual(f.changes, []);
  // A workspace that is gone.
  fs.rmSync(f.root, {recursive: true});
  assert.deepEqual(f.restore(run(1)), {status: 503, body: {error: 'project-restore-unavailable'}});
});

// -------------------------------------------------------------- retention --

test('the last five turn copies and three restore copies of a project are kept', () => {
  let clock = Date.parse('2026-10-30T10:00:00.000Z');
  const f = fixture({now: () => (clock += 1000)});
  for (let n = 1; n <= 7; n++) {
    f.call('read', {path: 'Playground/game/chars.js'}, {runId: run(n)});
    fs.writeFileSync(path.join(f.project, 'chars.js'), `turn ${n}`);
  }
  const kept = () => f.copies().map(copy => copy.name.slice(16));
  assert.deepEqual(kept(), [7, 6, 5, 4, 3].map(run));
  assert.deepEqual(f.restore(run(2)), {status: 404, body: {error: 'project-snapshot-unavailable'}});
  for (let n = 0; n < 4; n++) {
    fs.writeFileSync(path.join(f.project, 'chars.js'), `owner edit ${n}`);
    assert.equal(f.restore(run(3)).status, 200);
  }
  assert.equal(kept().filter(id => id.startsWith('restore_')).length, 3);
  assert.equal(kept().filter(id => id.startsWith('chatcmpl_')).length, 5);
  assert.deepEqual(fs.readdirSync(path.join(f.stateDir, f.copies()[0].store)).filter(name => name.startsWith('.tmp-')), []);
});

// ------------------------------------------------------------ the route --

function exchange(route, {method = 'POST', url = '/pixel-ods/project-restore', type = 'application/json', body = ''} = {}) {
  const reply = {};
  const req = {method, url, headers: {'content-type': type}, async *[Symbol.asyncIterator]() { if (body) yield Buffer.from(body); }};
  const sendJson = (_res, status, payload) => Object.assign(reply, {status, payload});
  return createProjectRestoreRoute({snapshots: route, sendJson})(req, {}).then(() => reply);
}

test('the gateway route accepts one exact restore request', async () => {
  const requests = [];
  const route = {restore: request => { requests.push(request); return {status: 200, body: {ok: true}}; }};
  const body = JSON.stringify({user: USER, project: 'Playground/game', snapshot: run(1)});
  assert.deepEqual(await exchange(route, {body}), {status: 200, payload: {ok: true}});
  assert.deepEqual(requests, [{user: USER, project: 'Playground/game', snapshot: run(1)}]);
  assert.deepEqual(await exchange(route, {method: 'GET'}), {status: 405, payload: {error: 'method not allowed'}});
  assert.deepEqual(await exchange(route, {url: '/pixel-ods/project-restore?x=1', body}), {status: 400, payload: {error: 'invalid request'}});
  for (const options of [{type: 'text/plain', body}, {body: body.replace('"}', '","x":"' + 'y'.repeat(600) + '"}')},
    {body: 'not json'}, {body: JSON.stringify({user: USER, project: 'Playground/game/x', snapshot: run(1)})}]) {
    assert.deepEqual(await exchange(route, options), {status: 400, payload: {error: 'invalid-project-restore-request'}});
  }
  assert.equal(requests.length, 1);
});

// ------------------------------------------------- the plugin entry wiring --

test('the plugin entry keeps the copy in before_tool_call after the final decision, never for a refused call', async () => {
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const start = source.indexOf('    api.on("before_tool_call",'), end = source.indexOf('    api.on("after_tool_call",', start);
  assert.ok(start >= 0 && end > start);
  const f = fixture();
  let guardDecision;
  const callbacks = {};
  vm.runInNewContext(source.slice(start, end), {
    api: {config: {agents: {list: [{id: 'pixel', workspace: f.root}]}}, on: (name, callback) => { callbacks[name] = callback; }},
    AGENT_ID: 'pixel', toolLoopGuard: {beforeToolCall: () => guardDecision},
    accessRuntime: {isProbe: context => context.runId === 'private-proof', beforeTool: () => undefined},
    goalProgress: {before() {}}, bundleAdmission: {before() {}}, artifactAdmission: {before() {}}, taskActivity: {before() {}},
    projectRunControl: {before() {}}, delegationDelivery: {blocked() {}, before() {}, hasPendingChildren() { return false; }},
    withPixelCronDeliveryDefault: decision => decision, withPixelSubagentWorkspace: decision => decision,
    withCronCommandPayloadBlock: decision => decision, getSessionEntry: () => undefined, resolveStorePath: () => undefined,
    resolveUserPath: value => value, resolveAgentWorkspaceDir: () => f.root, projectSnapshots: f.snapshots,
  });
  const context = runId => ({agentId: 'pixel', runId, sessionKey: SESSION, toolName: 'exec', toolCallId: `call-${runId}`});
  const event = {toolName: 'exec', toolCallId: 'call', params: {command: 'python3 fix.py Playground/game/chars.js'}};
  guardDecision = {block: true, blockReason: 'refused'};
  assert.equal((await callbacks.before_tool_call(event, context(run(1))))?.block, true);
  await callbacks.before_tool_call(event, context('private-proof'));
  assert.deepEqual(f.copies(), []);
  // The guard wraps the command for cancellation; the copy still follows the call.
  guardDecision = {params: {command: `'/run/pixel-ods-control/cancellable-exec.sh' id ${Buffer.from('x').toString('base64')}`}};
  assert.deepEqual(await callbacks.before_tool_call(event, context(run(2))), guardDecision);
  assert.equal(f.copies().length, 1);
  assert.equal(f.manifest(f.copies()[0]).id, run(2));
});

test('the plugin entry builds the copies with the chat, delegation and example wiring, and registers the gateway route', () => {
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const slice = (start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, start);
    return source.slice(from, to);
  };
  const calls = [];
  let options, route;
  const context = {
    api: {config: {agents: {list: [{id: 'pixel', workspace: '/owner/workspace'}]}}, logger: {warn: message => calls.push(['warn', message])},
      registerHttpRoute: value => { route = value; }},
    AGENT_ID: 'pixel', projectSnapshots: undefined, sendJson: 'sendJson',
    createProjectSnapshots: value => { options = value; return {restore() {}}; },
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
  assert.equal(options.active('agent:pixel:subagent:busy'), true);
  options.changed('/owner/workspace', 'elf-of-doom');
  options.warn('note');
  assert.deepEqual(calls.splice(1), [['example', '/owner/workspace', 'elf-of-doom'], ['warn', 'note']]);
  vm.runInNewContext(slice('    // Portal\'s "Put back files": the owner restores', "    // Portal's sidebar lists the shipped example project"), context);
  assert.deepEqual({path: route.path, auth: route.auth, match: route.match}, {path: '/pixel-ods/project-restore', auth: 'gateway', match: 'exact'});
  assert.equal(route.handler.route.sendJson, 'sendJson');
  assert.equal(typeof route.handler.route.snapshots.restore, 'function');
});

test('a capture of a missing folder is an empty project', () => {
  const blobs = scratch('ods-snapshot-blobs-');
  assert.deepEqual(captureProject(path.join(blobs, 'missing'), blobs), {complete: true, dirs: [], files: [], skipped: []});
});
