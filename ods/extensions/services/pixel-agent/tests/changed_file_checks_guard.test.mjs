import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {FILE_CHECK_INSTRUCTION} from '../plugin/changed-file-checks.mjs';
import {exampleNotice} from '../plugin/example-guard.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE} from '../plugin/progress-finalization.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

// Portal's checks of the changed files, replayed through the guard's hooks in
// the gateway's order: before_tool_call (the first touch fingerprints the
// project before the call runs), the call's effect on disk, after_tool_call,
// tool_result_persist (the model's one notice), before_agent_finalize and
// the verification the ingress delivers. On 2026-10-30/31 an exec's one-off
// Python patch doubled chars.js, debug globals stayed in the game code and
// .crop-return.txt stayed in the project folder, all unreported.

const PREFIX = 'function SPRITE_pose(e, time) {\n  return Math.floor(time * 8) % 4;\n}\n' +
  'function SPRITE_blit(g, e, time) {\n  g.drawImage(e.sheet, SPRITE_pose(e, time) * 32, 0, 32, 32, e.x, e.y, 32, 32);\n}\n' +
  'const SPRITE_CACHE = {\n  size: 32,\n';
const SUFFIX = '};\nfunction drawEnemy(g, e, time) {\n  SPRITE_blit(g, e, time);\n}\n';
const CHARS = `${PREFIX}  frames: 4,\n${SUFFIX}`;
const DOUBLED = `${PREFIX}${PREFIX}  frames: 8,\n${SUFFIX}${SUFFIX}globalThis.__SF = SPRITE_CACHE;\n`;
const FIXED = `${PREFIX}  frames: 8,\n${SUFFIX}`;
const PROMPT = 'In Playground/sprite-game, give the enemy sprites eight frames instead of four.';
const PATCH = "python3 - <<'EOF'\nsrc = open('chars.js').read()\ni = src.index('  frames: 4,')\n" +
  "open('chars.js', 'w').write(src[:i] + src[:i] + '  frames: 8,\\n' + src[i:] + src[i:])\nEOF";
const FOUND = '`Playground/sprite-game`: `chars.js` does not parse (line 9); ' +
  '`SPRITE_pose`, `SPRITE_blit`, `SPRITE_CACHE` and 1 more name are each defined twice in `chars.js`; ' +
  'new debug global `globalThis.__SF` in `chars.js`; new file `.crop-return.txt` in the project folder.';
const NOTICE = `[ODS Pixel check] Portal checked the files this response changed in ${FOUND} ${FILE_CHECK_INSTRUCTION}`;
const FACTS = `Portal's checks of the changed files in ${FOUND}`;
const USER = `ods-${'5c'.repeat(32)}`;
const ok = {content: [{type: 'text', text: '(no output)'}], details: {status: 'completed', exitCode: 0, durationMs: 4}};
const failed = text => ({isError: true, content: [{type: 'text', text}], details: {status: 'completed', exitCode: 2, durationMs: 4}});

function harness(t, {files = {'Playground/sprite-game/index.html': '<script src="chars.js"></script>\n',
  'Playground/sprite-game/chars.js': CHARS}, prompt = PROMPT, wrapped = false, exampleGuard} = {}) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ods-file-checks-guard-')));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const at = relative => path.join(root, ...relative.split('/'));
  const write = (relative, content) => { fs.mkdirSync(path.dirname(at(relative)), {recursive: true}); fs.writeFileSync(at(relative), content); };
  for (const [relative, content] of Object.entries(files)) write(relative, content);
  let clock = 0;
  const guard = createToolLoopGuard({now: () => clock, abortRun: () => true, exampleGuard,
    abortRunAndDrain: async () => ({aborted: true, drained: true})});
  const context = {agentId: 'pixel', runId: 'chatcmpl_6e2b9f80-4c1d-4a3e-9b7f-0d5c3a2e1f40', sessionId: 'sprite-session',
    sessionKey: `agent:pixel:openai-user:${USER}`, trigger: 'user'};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root, executionHost: 'sandbox', runTimeoutSeconds: 1800});
  let sequence = 0;
  // One model round with one call; `during` is what the call does on disk.
  function step(name, args, inner = ok, during) {
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const id = `${name}-${++sequence}`, ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    if (decision?.block === true) {
      guard.observeModelEnd({}, context);
      return [decision.blockReason];
    }
    during?.();
    const executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
    const envelope = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result: inner};
    const result = wrapped ? {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope} : inner;
    guard.afterToolCall({toolName, params: executed, toolCallId: id, result, ...(inner.isError ? {error: inner.content[0].text} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError: inner.isError === true, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message;
    guard.observeModelEnd({}, context);
    return persisted.content.filter(block => block?.type === 'text').map(block => block.text);
  }
  // The model's final answer, and the verification the ingress delivers.
  function finish(answer) {
    guard.observeModelCall({}, context);
    guard.observeModelEnd({}, context);
    const decision = guard.beforeAgentFinalize({lastAssistantMessage: answer, messages: [
      {role: 'user', content: [{type: 'text', text: prompt}]}, {role: 'assistant', content: [{type: 'text', text: answer}]}]}, context);
    return {decision, delivery: guard.deliveryVerificationForRun(context.runId)};
  }
  const doubling = () => {
    write('Playground/sprite-game/chars.js', DOUBLED);
    write('Playground/sprite-game/.crop-return.txt', '{"x": 12, "y": 40}\n');
  };
  return {guard, root, at, write, step, finish, doubling, context, setClock: value => { clock = value; }};
}
const checkNotes = texts => texts.filter(text => text.startsWith('[ODS Pixel check]'));

for (const wrapped of [false, true]) {
  test(`${wrapped ? 'Tool Search' : 'direct'}: the exec that doubled chars.js gets one notice, and what remains is listed under the answer`, t => {
    const {step, finish, doubling} = harness(t, {wrapped});
    assert.deepEqual(checkNotes(step('read', {path: 'Playground/sprite-game/chars.js'})), []);
    const patched = step('exec', {command: PATCH, workdir: '/workspace/Playground/sprite-game'}, ok, doubling);
    assert.equal(patched.at(-1), NOTICE);
    // Told once: a later call finds the same and says nothing more.
    assert.deepEqual(checkNotes(step('exec', {command: 'node --check chars.js', workdir: '/workspace/Playground/sprite-game'},
      failed('chars.js:9\nSyntaxError: Unexpected identifier'))), []);
    const {decision, delivery} = finish('Done: the enemy sprites now have eight frames.');
    // No finalize revision: the pinned harness refuses one after side effects.
    assert.notEqual(decision?.action, 'revise');
    assert.equal(delivery.status, 'none');
    assert.equal(delivery.notice, FACTS);
  });
}

test('a model that fixes them after the notice leaves nothing to list', t => {
  const {step, finish, doubling, write, at} = harness(t);
  assert.equal(step('exec', {command: PATCH, workdir: '/workspace/Playground/sprite-game'}, ok, doubling).at(-1), NOTICE);
  assert.deepEqual(checkNotes(step('write', {path: 'Playground/sprite-game/chars.js', content: FIXED}, ok,
    () => write('Playground/sprite-game/chars.js', FIXED))), []);
  assert.deepEqual(checkNotes(step('exec', {command: 'rm .crop-return.txt', workdir: '/workspace/Playground/sprite-game'}, ok,
    () => fs.rmSync(at('Playground/sprite-game/.crop-return.txt')))), []);
  const {delivery} = finish('Done: eight frames, and the scratch file is removed.');
  assert.deepEqual(delivery, {status: 'none'});
});

test('a finding that appears later gets its own notice; one told already does not', t => {
  const {step, finish, doubling, write} = harness(t);
  step('exec', {command: PATCH, workdir: '/workspace/Playground/sprite-game'}, ok, doubling);
  const later = step('edit', {path: 'Playground/sprite-game/chars.js', edits: [{oldText: 'size: 32', newText: 'size: 48'}]}, ok,
    () => write('Playground/sprite-game/chars.js', `${DOUBLED}window.__REPLAY = [];\n`));
  assert.deepEqual(checkNotes(later), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/sprite-game`: ' +
    `new debug global \`window.__REPLAY\` in \`chars.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  assert.match(finish('Done.').delivery.notice, /new debug globals `globalThis\.__SF` and `window\.__REPLAY` in `chars\.js`/);
});

test('a new game is checked where the guard routed it, in its Playground folder', t => {
  const {step, write, finish} = harness(t, {files: {}, prompt: 'Make me a snake game.'});
  const game = 'function tick() {}\nfunction tick() {}\nwindow.__SNAKE = state;\n';
  const written = step('write', {path: 'snake-game/game.js', content: game}, ok,
    () => write('Playground/snake-game/game.js', game));
  assert.deepEqual(checkNotes(written), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/snake-game`: ' +
    `\`tick\` is defined twice in \`game.js\`; new debug global \`window.__SNAKE\` in \`game.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  assert.equal(finish('Your snake game is ready.').delivery.notice, "Portal's checks of the changed files in `Playground/snake-game`: " +
    '`tick` is defined twice in `game.js`; new debug global `window.__SNAKE` in `game.js`.');
});

test('the checks share the one notice line with the example\'s line, within its 1024 characters', t => {
  // ODS put the shipped example back after a call (example-guard.mjs).
  const exampleGuard = {begin: () => undefined, guarding: () => true, check: () => ({outcome: 'restored'}), end: () => undefined};
  const {step, finish, write} = harness(t, {exampleGuard});
  step('exec', {command: 'python3 split.py', workdir: '/workspace/Playground/sprite-game'}, ok, () => {
    for (let i = 0; i < 40; i++) write(`Playground/sprite-game/parts/part-${String(i).padStart(2, '0')}.js`, `const part${i} = ;\n`);
  });
  const {notice} = finish('Done: chars.js is split into parts.').delivery;
  const head = exampleNotice('restored');
  assert.ok(notice.startsWith(`${head} Portal's checks of the changed files in \`Playground/sprite-game\`: ` +
    '`parts/part-00.js` does not parse (line 1); '), notice);
  assert.match(notice, /; and \d+ more\.$/);
  assert.ok(notice.length <= 1024, `${notice.length}`);
});

test('a response that touches no project checks nothing, and an untouched project is never reported', t => {
  const {guard, root, step, finish, write, context} = harness(t, {files: {'Playground/sprite-game/chars.js': CHARS,
    'Playground/other/main.js': 'const a = 1;\n'}, prompt: 'Write my todo list to notes/todo.md.'});
  const texts = [
    ...step('write', {path: 'notes/todo.md', content: '- milk\n'}, ok, () => write('notes/todo.md', '- milk\n')),
    // A command in the workspace root that changes a project without naming it.
    ...step('exec', {command: 'sh tidy.sh', workdir: '/workspace'}, ok, () => write('Playground/sprite-game/chars.js', DOUBLED)),
  ];
  assert.deepEqual(checkNotes(texts), []);
  assert.equal(finish('Your list is saved.').delivery.notice, undefined);
  // The next response touches Playground/other only: what changes there is
  // reported, what changes in sprite-game is not.
  const next = {...context, runId: 'chatcmpl_7a1c0e92-5d2f-4b8a-8c6e-1f3d5b7a9c20'};
  guard.observeRun(next, 'pixel', {prompt: 'In Playground/other, rename a to b.'},
    {workspaceRoot: root, executionHost: 'sandbox', runTimeoutSeconds: 1800});
  const params = {path: 'Playground/other/main.js', edits: [{oldText: 'a', newText: 'b'}]};
  const ctx = {...next, toolName: 'edit', toolCallId: 'e1'};
  assert.notEqual(guard.beforeToolCall({toolName: 'edit', toolCallId: 'e1', params}, ctx)?.block, true);
  write('Playground/other/main.js', 'const b = 1;\n');
  write('Playground/other/.rename-notes.txt', 'a -> b');
  write('Playground/sprite-game/.crop-return.txt', 'x');
  guard.afterToolCall({toolName: 'edit', toolCallId: 'e1', params, result: ok}, ctx);
  const message = {role: 'toolResult', toolName: 'edit', toolCallId: 'e1', isError: false, ...structuredClone(ok)};
  const persisted = guard.toolResultPersist({toolName: 'edit', toolCallId: 'e1', message}, ctx)?.message ?? message;
  assert.deepEqual(checkNotes(persisted.content.map(block => block.text)), ['[ODS Pixel check] Portal checked the files this ' +
    `response changed in \`Playground/other\`: new file \`.rename-notes.txt\` in the project folder. ${FILE_CHECK_INSTRUCTION}`]);
  assert.equal(guard.deliveryVerificationForRun(next.runId).notice, "Portal's checks of the changed files in `Playground/other`: " +
    'new file `.rename-notes.txt` in the project folder.');
});

test('after a tool-limit stop the model is told nothing more, and the stop lists the checks after its changed files', t => {
  const {step, guard, context, write} = harness(t);
  // The response's own write leaves chars.js unparseable: its one notice.
  const written = step('write', {path: 'Playground/sprite-game/chars.js', content: `${CHARS}const = 1;\n`}, ok,
    () => write('Playground/sprite-game/chars.js', `${CHARS}const = 1;\n`));
  assert.equal(checkNotes(written).length, 1);
  for (let i = 1; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    step('read', {path: `Playground/sprite-game/missing-${i}.js`}, {isError: true, content: [{type: 'text', text: 'ENOENT'}],
      details: {status: 'error'}});
  }
  // The call that reaches the limit also leaves a scratch file: its result is
  // the stop's answer instruction only.
  const stopped = step('exec', {command: 'sh crop.sh', workdir: '/workspace/Playground/sprite-game'}, failed('crop failed'),
    () => write('Playground/sprite-game/.crop-return.txt', 'x'));
  assert.deepEqual(stopped, [PROGRESS_FINALIZATION_INSTRUCTION]);
  // The one tool-free answer turn.
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  const answer = 'I wrote the eight-frame cache, but chars.js does not parse yet and the crop helper failed.';
  const decision = guard.beforeAgentFinalize({lastAssistantMessage: answer, messages: []}, context);
  assert.notEqual(decision?.action, 'revise');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.text, `${answer}\n\n${PROGRESS_FINALIZATION_NOTE}\n\n` +
    'Files changed in this response: `Playground/sprite-game/chars.js`.\n\n' +
    `Portal's checks of the changed files in \`Playground/sprite-game\`: \`chars.js\` does not parse (line ${CHARS.split('\n').length}); ` +
    'new file `.crop-return.txt` in the project folder.');
  assert.equal(delivery.notice, undefined, 'said once, in the stop');
});

test('a stop without an answer, and a run-deadline stop, list the checks after the changed files', t => {
  const stop = harness(t);
  stop.step('write', {path: 'Playground/sprite-game/chars.js', content: DOUBLED}, ok,
    () => stop.write('Playground/sprite-game/chars.js', DOUBLED));
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    stop.step('read', {path: `Playground/sprite-game/missing-${i}.js`}, {isError: true, content: [{type: 'text', text: 'ENOENT'}],
      details: {status: 'error'}});
  }
  const stopped = stop.guard.deliveryVerificationForRun(stop.context.runId);
  assert.ok(stopped.text.startsWith(`${RUN_PROGRESS_STOP_REASON}\n\nFiles changed in this response: \`Playground/sprite-game/chars.js\`.\n\n` +
    "Portal's checks of the changed files in `Playground/sprite-game`: `chars.js` does not parse (line 9); "), stopped.text);
  assert.equal(stopped.notice, undefined);

  const deadline = harness(t);
  deadline.step('write', {path: 'Playground/sprite-game/chars.js', content: DOUBLED}, ok,
    () => deadline.write('Playground/sprite-game/chars.js', DOUBLED));
  deadline.setClock(30 * 60000);
  deadline.guard.observeAgentEnd({success: false, durationMs: 30 * 60000, messages: [
    {role: 'user', content: [{type: 'text', text: PROMPT}]}, {role: 'assistant', stopReason: 'aborted', content: []}]}, deadline.context);
  const late = deadline.guard.deliveryVerificationForRun(deadline.context.runId);
  assert.equal(late.status, 'failed');
  assert.match(late.text, /\n\nFiles changed in this response: `Playground\/sprite-game\/chars\.js`\.\n\nPortal's checks of the changed files in `Playground\/sprite-game`: `chars\.js` does not parse \(line 9\); /);
  assert.equal(late.notice, undefined);
});

test('an owner cancel ends the checks: no notice and no line', async t => {
  const {guard, doubling, context} = harness(t);
  // The patch was running when the owner pressed Stop.
  const params = {command: PATCH, workdir: '/workspace/Playground/sprite-game'};
  const ctx = {...context, toolName: 'exec', toolCallId: 'exec-1'};
  guard.observeModelCall({}, context);
  assert.notEqual(guard.beforeToolCall({toolName: 'exec', params, toolCallId: 'exec-1'}, ctx)?.block, true);
  doubling();
  assert.equal(await guard.abortUserRun(USER), true);
  guard.afterToolCall({toolName: 'exec', params, toolCallId: 'exec-1', result: ok}, ctx);
  const message = {role: 'toolResult', toolName: 'exec', toolCallId: 'exec-1', isError: false, ...structuredClone(ok)};
  const persisted = guard.toolResultPersist({toolName: 'exec', toolCallId: 'exec-1', message}, ctx)?.message ?? message;
  assert.deepEqual(checkNotes(persisted.content.map(block => block.text)), []);
  assert.equal(guard.deliveryVerificationForRun(context.runId).notice, undefined);
});
