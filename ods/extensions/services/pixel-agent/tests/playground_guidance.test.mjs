import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {routePlaygroundTool} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';
import {nativeExecWorkdir} from '../plugin/workspace-path-contract.mjs';

// Fleet evidence, claude-open-prompts sweep1 on ODS main d4a61f33, prompt 07
// "write a python script that renames all photos in a folder by date taken,
// and test it". mac-mini (Qwen3.5-9B) was graded NOTHING: the generic
// Playground correction named no usable path, the exec-before-write refusal
// did not say mkdir was unnecessary, and the fourth failure stopped the run.
const MACMINI = JSON.parse(fs.readFileSync(new URL('./fixtures/photo-renamer-macmini-d4a61f33.json', import.meta.url), 'utf8'));
const PROMPT = MACMINI.prompt;
const CORRECTION_START = 'For a new project, use a workspace-relative path such as Playground/snake-game/index.html';
const CORRECTION_END = 'Do not use a bare filename or a generic src/public/project folder as the project name.';
// A refused write without a suggested path: it says nothing was written, then
// gives only the correction.
const NOT_WRITTEN = `Not written. ${CORRECTION_START}`;
const NOT_RUN = 'Not run: write the first project file before running commands; write creates its folder, so mkdir is not needed.';
const FIRST_FILE = 'Create the first project file with write in a descriptive Playground folder before running commands or patches.';

const roots = [];
after(() => { for (const root of roots) fs.rmSync(root, {recursive: true, force: true}); });
function workspace() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(tmpdir(), 'pixel-playground-guidance-')));
  roots.push(root);
  return root;
}
// The sandbox shows the owner workspace at /workspace.
function hostPath(root, value) {
  if (value === undefined || value === '.' || value === '/workspace') return root;
  if (path.isAbsolute(value) && !value.startsWith('/workspace/')) return value;
  return path.join(root, ...value.replace(/^\/workspace\//, '').split('/'));
}
const synthesized = chars => '#!/usr/bin/env python3\n' + 'x'.repeat(chars - 23);

// Replays OpenClaw's hook sequence for each recorded call against a real
// workspace: an allowed write lands on disk, a blocked call gets the SDK's
// standard veto receipt, and every receipt reaches after_tool_call and
// tool_result_persist, which own the unchanged progress accounting.
function harness({root = workspace(), native = false, runId = 'photo-renamer'} = {}) {
  const guard = createToolLoopGuard({execControl: {
    resolveWorkdir: native ? nativeExecWorkdir : () => undefined,
    prepare: (_run, command) => command,
  }});
  const context = {agentId:'pixel', runId, sessionId:`session-${runId}`, sessionKey:`agent:pixel:openai-user:ods-${'b'.repeat(64)}`};
  guard.observeRun(context, 'pixel', {prompt: PROMPT}, {workspaceRoot: root, executionHost: native ? 'gateway' : 'sandbox'});
  let sequence = 0;
  function call(tool, args, perform, id = `${tool}-${++sequence}`) {
    guard.observeModelCall({}, context);
    const ctx = {...context, toolName: tool, toolCallId: id};
    const original = structuredClone(args);
    const decision = guard.beforeToolCall({toolName: tool, params: args, toolCallId: id}, ctx);
    assert.deepEqual(args, original, 'the caller params are never mutated');
    let result, executed = args;
    if (decision?.block) {
      result = {content:[{type:'text', text:decision.blockReason}],
        details:{status:'blocked', deniedReason:'plugin-before-tool-call', reason:decision.blockReason}};
    } else {
      // The pinned SDK merges direct before-hook params into the arguments.
      executed = {...args, ...decision?.params};
      result = perform(executed);
    }
    const isError = decision?.block === true || result?.isError === true;
    guard.afterToolCall({toolName: tool, params: executed, toolCallId: id, result,
      ...(isError ? {error: result.content[0].text} : {})}, ctx);
    const message = {role:'toolResult', toolName: tool, toolCallId: id, isError, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName: tool, toolCallId: id, message}, ctx)?.message ?? message;
    return {decision, executed, text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  }
  const write = (args, id) => call('write', args, executed => {
    const target = hostPath(root, executed.path);
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, executed.content);
    return {content:[{type:'text', text:`Successfully wrote ${Buffer.byteLength(executed.content)} bytes to ${executed.path}`}], details:{}};
  }, id);
  // Exec receipts are simulated from the real workspace state: a command
  // succeeds only when the directory it needs exists under its cwd.
  const exec = (args, needs, id) => call('exec', args, executed => {
    const cwd = native ? executed.workdir : hostPath(root, executed.workdir);
    const ok = fs.existsSync(path.join(cwd, ...needs.split('/')));
    const text = ok ? 'ok' : `ls: cannot access '${needs}': No such file or directory\n\n(Command exited with code 2)`;
    return {...(ok ? {} : {isError:true}), content:[{type:'text', text}],
      details:{status:'completed', exitCode: ok ? 0 : 2, aggregated: text, cwd}};
  }, id);
  return {guard, context, root, call, write, exec};
}
// Allowed execs keep the guard's appended execution receipt after 'ok'.
const ran = text => text.split('\n')[0] === 'ok';
const stopped = text => [RUN_PROGRESS_STOP_REASON, PROGRESS_FINALIZATION_INSTRUCTION].includes(text);

test('mac-mini d4a61f33 replay: refusals name the exact next path and the unchanged fuse still stops at call 4', () => {
  assert.deepEqual({...RUN_PROGRESS_LIMITS}, {consecutiveFailures:4, totalFailures:12, roundsWithoutProgress:8, identicalSuccesses:2});
  assert.equal(FREE_CORRECTIONS_PER_KIND, 2);
  assert.equal(MACMINI.sessionFileSha256.slice(0, 8), '9e3eef73');
  const [recordedWrite, mkdirTouch, touch, list, listLong] = MACMINI.calls;
  assert.equal(recordedWrite.args.contentChars, 5936);
  const h = harness();
  const content = synthesized(recordedWrite.args.contentChars);
  assert.equal(content.length, 5936);

  const first = h.write({path: recordedWrite.args.path, content}, recordedWrite.id);
  assert.equal(first.decision?.block, true);
  assert.ok(first.text.startsWith('Not written: project files go in a descriptive Playground folder. Call write again now with path Playground/photo-renamer/PhotoRenamer.py and the same content'), first.text);
  assert.match(first.text, /then use that folder for every project file and as exec workdir\. For a new project/);
  assert.ok(first.text.endsWith(CORRECTION_END), 'the existing correction follows unchanged');
  assert.ok(first.text.includes(recordedWrite.recorded.resultHead), 'the recorded refusal text is kept after the new prefix');

  for (const recorded of [mkdirTouch, touch]) {
    const refused = h.exec(recorded.args, '.', recorded.id);
    assert.equal(refused.decision?.block, true, recorded.args.command);
    // The earlier suggestion is repeated; the model's test-data folder is not a project.
    assert.ok(refused.text.startsWith(`${NOT_RUN} Call write now with path Playground/photo-renamer/PhotoRenamer.py and its content. ${FIRST_FILE} ${CORRECTION_START}`), refused.text);
    assert.equal(stopped(refused.text), false);
    assert.ok(refused.text.includes(recorded.recorded.resultHead));
  }

  // Inspection before the first write stays allowed. The folder was never
  // created, so the listing fails: the fourth consecutive failure.
  const listing = h.exec(list.args, 'Playground/photo-renamer-test', list.id);
  assert.notEqual(listing.decision?.block, true, listing.decision?.blockReason);
  assert.equal(listing.text, RUN_PROGRESS_STOP_REASON, 'charged exactly as recorded: the fuse trips at call 4');
  assert.ok(listing.text.startsWith(list.recorded.resultHead));
  const next = h.exec(listLong.args, 'Playground/photo-renamer-test', listLong.id);
  assert.equal(next.decision?.block, true);
  assert.equal(stopped(next.decision.blockReason), true);
  assert.equal(fs.existsSync(path.join(h.root, 'Playground')), false, 'refusals reserve and write nothing');
});

test('mac-mini d4a61f33 counterfactual: the suggested write is accepted, reserves the folder and later execs get its workdir', () => {
  for (const native of [false, true]) {
    const h = harness({native, runId:`counterfactual-${native}`});
    const [recordedWrite, mkdirTouch] = MACMINI.calls;
    const content = synthesized(recordedWrite.args.contentChars);
    const first = h.write({path: recordedWrite.args.path, content});
    const suggested = /with path (\S+) and the same content/.exec(first.text)?.[1];
    assert.equal(suggested, 'Playground/photo-renamer/PhotoRenamer.py');
    const accepted = h.write({path: suggested, content});
    assert.notEqual(accepted.decision?.block, true, accepted.decision?.blockReason);
    assert.equal(accepted.executed.path, suggested, 'accepted exactly as suggested');
    assert.equal(fs.readFileSync(path.join(h.root, 'Playground', 'photo-renamer', 'PhotoRenamer.py'), 'utf8'), content);
    const records = fs.readdirSync(path.join(h.root, '.ods-projects'));
    assert.equal(records.length, 1);
    assert.equal(JSON.parse(fs.readFileSync(path.join(h.root, '.ods-projects', records[0]), 'utf8')).directory, 'Playground/photo-renamer');

    const run = h.exec({command:'python3 PhotoRenamer.py --help', workdir:'/workspace'}, 'PhotoRenamer.py');
    assert.notEqual(run.decision?.block, true, run.decision?.blockReason);
    assert.equal(run.executed.workdir, native ? path.join(h.root, 'Playground', 'photo-renamer') : '/workspace/Playground/photo-renamer');
    assert.equal(run.executed.command, 'python3 PhotoRenamer.py --help', 'shell text is never rewritten');
    assert.equal(ran(run.text), true);
    // The recorded follow-up names explicit /workspace/ paths: no longer refused and not rerouted.
    const explicit = h.exec(mkdirTouch.args, '.');
    assert.notEqual(explicit.decision?.block, true, explicit.decision?.blockReason);
    assert.equal(explicit.executed.workdir, native ? h.root : '/workspace');
    assert.equal(h.guard.beforeToolCall({toolName:'read', params:{path:suggested}, toolCallId:'probe'},
      {...h.context, toolName:'read', toolCallId:'probe'})?.block, undefined, 'no fuse was reached');
  }
});

test('tower2 d4a61f33 replay: a bare filename gets its own suggestion and the recorded corrected path is still accepted', () => {
  // tower2 (Qwen3-Coder-Next), session fe9ee859-c533-483c-b978-5c4a672dc126
  // (session file sha256 8d21ebc1...), PARTIAL at 25 s; call 1 cost one step.
  const h = harness();
  const first = h.write({path:'rename_photos.py', content:synthesized(3968)});
  assert.equal(first.decision?.block, true);
  assert.match(first.text, /^Not written: .* with path Playground\/rename-photos\/rename_photos\.py and the same content/);
  const second = h.write({path:'Playground/photo-renamer/rename_photos.py', content:synthesized(3968)});
  assert.notEqual(second.decision?.block, true, second.decision?.blockReason);
  assert.equal(second.executed.path, 'Playground/photo-renamer/rename_photos.py');
  assert.equal(h.write({path:'Playground/photo-renamer/test_rename.py', content:synthesized(2265)}).executed.path, 'Playground/photo-renamer/test_rename.py');
  assert.equal(fs.existsSync(path.join(h.root, 'Playground', 'rename-photos')), false, 'a suggestion reserves nothing');
  const check = h.exec({command:'cd /workspace/Playground/photo-renamer && python3 --version && pip3 show Pillow 2>/dev/null | head -2 || pip3 install Pillow --quiet', workdir:'/workspace'}, 'Playground/photo-renamer');
  assert.equal(check.executed.workdir, '/workspace');
  assert.equal(ran(check.text), true);
});

test('tower1 d4a61f33 replay: a leading cd into the bound project keeps the workspace cwd; the same command without it gets the project', () => {
  // tower1 (Qwen3.5-27B), session 4da3c1ca-0f6e-4ce0-9e9e-ef727061f499
  // (session file sha256 0bdab0e3...). Recorded call 7 failed with "sh: 1:
  // cd: can't cd to Playground/photo-renamer" (exit 2) because the router
  // injected the project as cwd under the command's own cd.
  const recorded = 'cd Playground/photo-renamer && python3 photo_renamer.py test_photos --dry-run';
  for (const native of [false, true]) for (const workdir of ['/workspace', '.', undefined]) {
    const h = harness({native, runId:`tower1-${native}-${workdir}`});
    const project = native ? path.join(h.root, 'Playground', 'photo-renamer') : '/workspace/Playground/photo-renamer';
    const root = native ? h.root : '/workspace';
    assert.equal(h.write({path:'Playground/photo-renamer/photo_renamer.py', content:synthesized(3767)}).executed.path, 'Playground/photo-renamer/photo_renamer.py');
    assert.equal(h.exec({command:'mkdir -p Playground/photo-renamer/test_photos', workdir:'/workspace'}, 'Playground').executed.workdir, root);
    fs.mkdirSync(path.join(h.root, 'Playground', 'photo-renamer', 'test_photos'), {recursive: true});
    h.write({path:'Playground/photo-renamer/test_photos/create_test_images.py', content:synthesized(857)});
    assert.equal(ran(h.exec({command:'cd Playground/photo-renamer/test_photos && python3 create_test_images.py', workdir:'/workspace'}, 'Playground/photo-renamer/test_photos').text), true);
    h.write({path:'Playground/photo-renamer/test_photos/add_exif_dates.py', content:synthesized(976)});
    assert.equal(ran(h.exec({command:'cd Playground/photo-renamer/test_photos && python3 add_exif_dates.py', workdir:'/workspace'}, 'Playground/photo-renamer/test_photos').text), true);

    const cd = h.exec({command:recorded, ...(workdir === undefined ? {} : {workdir})}, 'Playground/photo-renamer');
    assert.notEqual(cd.decision?.block, true, cd.decision?.blockReason);
    assert.equal(cd.executed.command, recorded, 'shell text is never rewritten');
    assert.notEqual(cd.executed.workdir, project, `left unrouted (workdir ${workdir})`);
    assert.equal(native ? cd.executed.workdir : cd.executed.workdir ?? '/workspace', native ? h.root : workdir ?? '/workspace');
    assert.equal(ran(cd.text), true, 'the cd target exists under the cwd the command runs in');
    const plain = h.exec({command:'python3 photo_renamer.py test_photos --dry-run', ...(workdir === undefined ? {} : {workdir})}, 'photo_renamer.py');
    assert.equal(plain.executed.workdir, project);
    assert.equal(ran(plain.text), true);
  }
});

function fresh(overrides = {}) {
  const root = workspace();
  const state = {};
  const call = (tool, params, extra = {}) => routePlaygroundTool({state, tool, params, root,
    session:'guidance-session', intent:PROMPT, ...overrides, ...extra});
  return {root, state, call};
}

test('single file names yield a derived descriptive folder; every suggestion is accepted as sent and never auto-applied', () => {
  const cases = {
    '/workspace/PhotoRenamer.py': 'Playground/photo-renamer/PhotoRenamer.py',
    'rename_photos.py': 'Playground/rename-photos/rename_photos.py',
    './HTTPServer.py': 'Playground/http-server/HTTPServer.py',
    '/photo_sorter.py': 'Playground/photo-sorter/photo_sorter.py',
    'weather-tool.html': 'Playground/weather-tool/weather-tool.html',
  };
  for (const [target, expected] of Object.entries(cases)) for (const wrapped of [false, true]) {
    const {root, state, call} = fresh();
    const args = {path:target, content:'print(1)'};
    const decision = wrapped ? call('tool_call', {id:'openclaw:core:write', args}) : call('write', args);
    assert.equal(decision.block, true, target);
    assert.equal(decision.params, undefined, 'a suggested path is never auto-applied');
    assert.equal(args.path, target);
    assert.ok(decision.blockReason.includes(`Call write again now with path ${expected} and the same content`), `${target}: ${decision.blockReason}`);
    assert.ok(decision.blockReason.endsWith(CORRECTION_END));
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
    assert.equal(state.binding ?? null, null, 'no project is reserved');
    assert.equal(call('write', {path:expected, content:'print(1)'}), undefined, `${expected} is accepted unchanged`);
    assert.ok(fs.statSync(path.join(root, ...expected.split('/').slice(0, 2))).isDirectory());
  }
  // The configured host root is stripped too, as the router already does.
  const {root, call} = fresh();
  assert.ok(call('write', {path:`${root.replaceAll('\\', '/')}/PhotoRenamer.py`}).blockReason.includes('with path Playground/photo-renamer/PhotoRenamer.py'));
});

test('role, generic and multi-segment names keep only the generic correction', () => {
  for (const target of ['main.py', 'index.html', 'app.py', 'test_x.py', 'TestRenamer.py', 'README.md', 'utils.py', 'setup.py',
    // Manifests, configuration, tests, samples, data, documents and too-short names.
    'package.json', 'requirements.txt', 'Makefile', 'Dockerfile', 'pyproject.toml', 'conftest.py', 'config.py', 'settings.py',
    'photo_renamer_test.py', 'PhotoRenamerTest.py', 'renamer.test.js', 'renamer_spec.rb', 'tests.py', 'demo.py', 'sample.py',
    'IMG_2024.jpg', 'photo.png', 'story.md', 'notes.txt', 'data.csv', 'x.py', 'ab.py', 'x1.py',
    'game.py', 'website.html', 'src/main.py', 'Playground/photo_renamer.py', '/home/other/photo_renamer.py', '/tmp/photo_renamer.py',
    '../photo_renamer.py', 'CON.py']) {
    const {root, call} = fresh();
    const decision = call('write', {path:target, content:'x'});
    assert.equal(decision.block, true, target);
    assert.ok(decision.blockReason.startsWith(NOT_WRITTEN), `${target}: ${decision.blockReason}`);
    // No path is suggested: neither the suggestion prefix nor a next write.
    assert.doesNotMatch(decision.blockReason, /Not written:|Call write|with path/, target);
    assert.equal(fs.existsSync(path.join(root, 'Playground')), false, target);
  }
});

test('exec and patches before the first write say mkdir is unnecessary and name a valid Playground folder only when the command does', () => {
  const named = {
    'mkdir -p Playground/snake-game': 'snake-game',
    'mkdir -p /workspace/Playground/todo-app': 'todo-app',
    'mkdir -p "./Playground/bean-there" && touch Playground/bean-there/index.html': 'bean-there',
    'python3 Playground/photo-renamer/photo_renamer.py': 'photo-renamer',
  };
  for (const [command, name] of Object.entries(named)) {
    const decision = fresh().call('exec', {command, workdir:'/workspace'});
    assert.equal(decision.block, true, command);
    assert.ok(decision.blockReason.startsWith(`${NOT_RUN} Call write now with path Playground/${name}/<file name> and its content. ${FIRST_FILE} ${CORRECTION_START}`), `${command}: ${decision.blockReason}`);
    assert.ok(decision.blockReason.endsWith(CORRECTION_END));
  }
  for (const command of ['mkdir Playground/src', 'mkdir -p Playground/CON', 'mkdir -p myPlayground/tool-x', 'ls -la /workspace/.openclaw/tmp/ 2>/dev/null || mkdir -p x', 'mkdir snake-game && echo hi > snake-game/index.html',
    // Test data, samples and too-short names are not project folders.
    'mkdir -p /workspace/Playground/photo-renamer-test && touch /workspace/Playground/photo-renamer-test/a.jpg',
    'mkdir -p Playground/test_photos', 'mkdir -p Playground/sample', 'mkdir -p Playground/x1', 'mkdir -p Playground/New_Project']) {
    const decision = fresh().call('exec', {command});
    assert.equal(decision.block, true, command);
    assert.ok(decision.blockReason.startsWith(`${NOT_RUN} ${FIRST_FILE} ${CORRECTION_START}`), `${command}: ${decision.blockReason}`);
  }
  const patch = fresh().call('apply_patch', {input:'*** Begin Patch\n*** Add File: snake-game/index.html\n+x\n*** End Patch'});
  assert.ok(patch.blockReason.startsWith(`${NOT_RUN} ${FIRST_FILE}`));
  // The inspection allowlist before the first write is unchanged.
  for (const command of ['pwd', 'ls -la', 'ls Playground/', 'git status', 'python3 --version']) assert.equal(fresh().call('exec', {command}), undefined, command);
});

test('only a leading cd into the bound directory keeps an unspecified cwd; other projects, suffixes and later cds keep today\'s routing', () => {
  const {call} = fresh();
  call('write', {path:'Playground/photo-renamer/photo_renamer.py', content:'x'});
  for (const command of ['cd Playground/photo-renamer', 'cd Playground/photo-renamer/', 'cd ./Playground/photo-renamer && ls',
    'cd ./Playground/photo-renamer/ && ls', '  cd Playground/photo-renamer&&ls', 'cd Playground/photo-renamer;ls',
    'cd Playground/photo-renamer||true', 'cd Playground/photo-renamer \t&& ls', 'cd Playground/photo-renamer ; ls',
    'cd Playground/photo-renamer || exit 1', 'cd Playground/photo-renamer\nls', 'cd Playground/photo-renamer/ \nls', 'cd Playground/photo-renamer  ']) {
    for (const workdir of [undefined, '.', '/workspace']) assert.equal(call('exec', {command, ...(workdir === undefined ? {} : {workdir})}), undefined, `${command} ${workdir}`);
  }
  for (const command of ['cd Playground/other-tool && ls', 'cd Playground/photo-renamer-2 && ls', 'cd Playground/photo-renamerx && ls',
    'cd Playground/photo-renamer|ls', 'echo x && cd Playground/photo-renamer && ls', 'python3 photo_renamer.py', 'cd test_photos && ls',
    // A single & or | runs the cd in a subshell: the rest must still start in the project.
    'cd Playground/photo-renamer & python3 photo_renamer.py .', 'cd Playground/photo-renamer | python3 photo_renamer.py .',
    'cd Playground/photo-renamer &ls', 'cd Playground/photo-renamer &',
    // Anything else after the directory is an operand or redirection of cd itself.
    'cd Playground/photo-renamer\tls', 'cd Playground/photo-renamer 2>/dev/null && ls', 'cd Playground/photo-renamer # x']) {
    const decision = call('exec', {command});
    assert.deepEqual(decision?.params, {command, workdir:'/workspace/Playground/photo-renamer'}, command);
  }
  // An explicit cwd stays a core decision.
  assert.equal(call('exec', {command:'cd Playground/photo-renamer && ls', workdir:'/workspace/Playground/photo-renamer'}), undefined);

  // Collision-suffixed project: the unsuffixed spelling names another folder.
  const collided = fresh();
  fs.mkdirSync(path.join(collided.root, 'Playground', 'photo-renamer'), {recursive: true});
  assert.equal(collided.call('write', {path:'Playground/photo-renamer/photo_renamer.py'}).params.path, 'Playground/photo-renamer-2/photo_renamer.py');
  assert.deepEqual(collided.call('exec', {command:'cd Playground/photo-renamer && ls'})?.params,
    {command:'cd Playground/photo-renamer && ls', workdir:'/workspace/Playground/photo-renamer-2'});
  assert.equal(collided.call('exec', {command:'cd Playground/photo-renamer-2 && ls'}), undefined);
  assert.equal(collided.call('exec', {command:'python3 photo-renamer/photo_renamer.py'}).block, true, 'the old prefix refusal is unchanged');
});

test('a suggestion never names an existing folder: an owner project named by a command is not offered, and a taken derived name gets the collision suffix', () => {
  // An owner project the model only inspected is never suggested as the place to write.
  const owner = fresh();
  fs.mkdirSync(path.join(owner.root, 'Playground', 'photo-tools'), {recursive: true});
  fs.writeFileSync(path.join(owner.root, 'Playground', 'photo-tools', 'rename.py'), 'OWNER');
  assert.equal(owner.call('read', {path:'Playground/photo-tools/rename.py'}), undefined);
  for (const command of ['cat Playground/photo-tools/rename.py && python3 --version', 'mkdir -p Playground/photo-tools && touch Playground/photo-tools/x.py']) {
    const refused = owner.call('exec', {command, workdir:'/workspace'});
    assert.ok(refused.blockReason.startsWith(`${NOT_RUN} ${FIRST_FILE}`), `${command}: ${refused.blockReason}`);
    assert.doesNotMatch(refused.blockReason, /photo-tools/);
  }
  // A later unused name in the same command is still offered.
  assert.ok(owner.call('exec', {command:'cp Playground/photo-tools/rename.py Playground/photo-sorter/'}).blockReason
    .includes('Call write now with path Playground/photo-sorter/<file name> and its content.'));
  assert.equal(fs.readFileSync(path.join(owner.root, 'Playground', 'photo-tools', 'rename.py'), 'utf8'), 'OWNER');

  // The same prompt again: the derived folder exists, so the suggestion is the
  // folder reserveProject will really use, and the model's cd then matches it.
  for (const native of [false, true]) {
    const root = workspace();
    fs.mkdirSync(path.join(root, 'Playground', 'photo-renamer'), {recursive: true});
    fs.writeFileSync(path.join(root, 'Playground', 'photo-renamer', 'PhotoRenamer.py'), 'EARLIER');
    const h = harness({root, native, runId:`repeat-${native}`});
    const first = h.write({path:'/workspace/PhotoRenamer.py', content:'NEW'});
    const suggested = /with path (\S+) and the same content/.exec(first.text)?.[1];
    assert.equal(suggested, 'Playground/photo-renamer-2/PhotoRenamer.py');
    assert.equal(fs.existsSync(path.join(root, 'Playground', 'photo-renamer-2')), false, 'a suggestion reserves nothing');
    const accepted = h.write({path:suggested, content:'NEW'});
    assert.equal(accepted.executed.path, suggested, 'accepted exactly as suggested');
    assert.equal(fs.readFileSync(path.join(root, 'Playground', 'photo-renamer', 'PhotoRenamer.py'), 'utf8'), 'EARLIER');
    assert.equal(fs.readFileSync(path.join(root, 'Playground', 'photo-renamer-2', 'PhotoRenamer.py'), 'utf8'), 'NEW');
    const cd = h.exec({command:'cd Playground/photo-renamer-2 && python3 PhotoRenamer.py .', workdir:'/workspace'}, 'Playground/photo-renamer-2');
    assert.equal(cd.executed.workdir, native ? root : '/workspace');
    assert.equal(ran(cd.text), true);
  }
  const both = fresh();
  for (const name of ['photo-renamer', 'photo-renamer-2']) fs.mkdirSync(path.join(both.root, 'Playground', name), {recursive: true});
  fs.writeFileSync(path.join(both.root, 'Playground', 'photo-renamer-3'), 'a file also takes the name');
  assert.ok(both.call('write', {path:'PhotoRenamer.py'}).blockReason.includes('with path Playground/photo-renamer-4/PhotoRenamer.py and'));
});

test('one run gets one suggested folder: later refusals repeat it, including for test and role files', () => {
  const {root, state, call} = fresh();
  const first = call('write', {path:'/workspace/PhotoRenamer.py', content:'x'});
  assert.ok(first.blockReason.includes('with path Playground/photo-renamer/PhotoRenamer.py and the same content'));
  for (const command of ['mkdir -p /workspace/Playground/photo-renamer-test && cd /workspace/Playground/photo-renamer-test && touch a.jpg',
    'mkdir -p Playground/other-tool', 'python3 PhotoRenamer.py']) {
    assert.ok(call('exec', {command, workdir:'/workspace'}).blockReason.startsWith(
      `${NOT_RUN} Call write now with path Playground/photo-renamer/PhotoRenamer.py and its content. ${FIRST_FILE}`), command);
  }
  assert.ok(call('write', {path:'test_photo_renamer.py', content:'x'}).blockReason
    .includes('with path Playground/photo-renamer/test_photo_renamer.py and the same content'));
  assert.ok(call('apply_patch', {input:'*** Begin Patch\n*** Add File: x.py\n+x\n*** End Patch'}).blockReason
    .startsWith(`${NOT_RUN} Call write now with path Playground/photo-renamer/test_photo_renamer.py and its content.`));
  // Multi-segment paths still get only the correction.
  assert.ok(call('write', {path:'src/main.py', content:'x'}).blockReason.startsWith(NOT_WRITTEN));
  assert.equal(fs.existsSync(path.join(root, 'Playground')), false);
  assert.equal(state.binding ?? null, null);
  // If the folder is taken meanwhile, the next suggestion derives an unused one.
  fs.mkdirSync(path.join(root, 'Playground', 'photo-renamer'), {recursive: true});
  assert.ok(call('write', {path:'PhotoRenamer.py', content:'x'}).blockReason.includes('with path Playground/photo-renamer-2/PhotoRenamer.py and'));

  // A folder first named by a command is reused for a role file name.
  const named = fresh();
  assert.ok(named.call('exec', {command:'mkdir -p Playground/snake-game'}).blockReason.includes('with path Playground/snake-game/<file name> and its content.'));
  assert.ok(named.call('exec', {command:'mkdir -p Playground/pong-game'}).blockReason.includes('with path Playground/snake-game/<file name> and its content.'));
  assert.ok(named.call('write', {path:'index.html', content:'x'}).blockReason.includes('with path Playground/snake-game/index.html and the same content'));
  assert.equal(named.call('write', {path:'Playground/snake-game/index.html', content:'x'}), undefined);
  assert.equal(named.state.binding.directory, 'Playground/snake-game');
});

test('advice needs a checkable workspace and never changes routing state', () => {
  const missing = path.join(workspace(), 'missing');
  const state = {};
  const call = (tool, params) => routePlaygroundTool({state, tool, params, root:missing, session:'guidance-session', intent:PROMPT});
  assert.ok(call('write', {path:'/workspace/PhotoRenamer.py', content:'x'}).blockReason.startsWith(NOT_WRITTEN));
  assert.ok(call('exec', {command:'mkdir -p Playground/snake-game'}).blockReason.startsWith(`${NOT_RUN} ${FIRST_FILE}`));
  assert.equal(state.failed, undefined);
  assert.equal(state.suggestedFolder, undefined);
  // A Playground that is not a real directory gets no suggestion either.
  const file = fresh();
  fs.writeFileSync(path.join(file.root, 'Playground'), 'not a folder');
  assert.ok(file.call('write', {path:'PhotoRenamer.py', content:'x'}).blockReason.startsWith(NOT_WRITTEN));
  assert.ok(file.call('exec', {command:'mkdir -p Playground/snake-game'}).blockReason.startsWith(`${NOT_RUN} ${FIRST_FILE}`));
  assert.equal(file.state.failed, undefined);
});
