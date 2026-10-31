import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {FILE_CHECK_INSTRUCTION, FILE_CHECK_REMOVAL} from '../plugin/changed-file-checks.mjs';
import {exampleNotice} from '../plugin/example-guard.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE} from '../plugin/progress-finalization.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

// Portal's checks of the changed files, replayed through the guard's hooks in
// the gateway's order: before_tool_call (the first touch fingerprints the
// project before the call runs), the call's effect on disk, after_tool_call,
// tool_result_persist (the model's one notice), before_agent_finalize, and the
// verification the ingress delivers after settleDelivery. On 2026-10-30/31 an
// exec's one-off Python patch doubled chars.js, debug globals stayed in the
// game code and .crop-return.txt stayed in the project folder, all unreported.

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
const WORKDIR = '/workspace/Playground/sprite-game';
const FOUND = '`Playground/sprite-game`: `chars.js` does not parse (line 9); ' +
  '`SPRITE_pose`, `SPRITE_blit`, `SPRITE_CACHE` and 1 more name are each defined twice in `chars.js`; ' +
  'new debug global `globalThis.__SF` in `chars.js`; new file `.crop-return.txt` in the project folder.';
const NOTICE = `[ODS Pixel check] Portal checked the files this response changed in ${FOUND} ${FILE_CHECK_INSTRUCTION} ${FILE_CHECK_REMOVAL}`;
const FACTS = `Portal's checks of the changed files in ${FOUND}`;
const USER = `ods-${'5c'.repeat(32)}`;
const RUN = 'chatcmpl_6e2b9f80-4c1d-4a3e-9b7f-0d5c3a2e1f40';
const NEXT_RUN = 'chatcmpl_7a1c0e92-5d2f-4b8a-8c6e-1f3d5b7a9c20';
const ok = {content: [{type: 'text', text: '(no output)'}], details: {status: 'completed', exitCode: 0, durationMs: 4}};
const failed = text => ({isError: true, content: [{type: 'text', text}], details: {status: 'completed', exitCode: 2, durationMs: 4}});
const quietExample = {begin: () => undefined, guarding: () => false, check: () => undefined, end: () => undefined};

function harness(t, {files = {'Playground/sprite-game/index.html': '<script src="chars.js"></script>\n',
  'Playground/sprite-game/chars.js': CHARS}, prompt = PROMPT, wrapped = false, runId = RUN, shared, ...options} = {}) {
  let root = shared?.root;
  if (!root) {
    root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ods-file-checks-guard-')));
    t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  }
  const at = relative => path.join(root, ...relative.split('/'));
  const write = (relative, content) => { fs.mkdirSync(path.dirname(at(relative)), {recursive: true}); fs.writeFileSync(at(relative), content); };
  if (!shared) for (const [relative, content] of Object.entries(files)) write(relative, content);
  let clock = 0;
  const guard = shared?.guard ?? createToolLoopGuard({now: () => clock, abortRun: () => true, exampleGuard: quietExample,
    abortRunAndDrain: async () => ({aborted: true, drained: true}), ...options});
  const context = {agentId: 'pixel', runId, sessionId: 'sprite-session', sessionKey: `agent:pixel:openai-user:${USER}`, trigger: 'user'};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root, executionHost: 'sandbox', runTimeoutSeconds: 1800});
  guard.observeExampleRun(context, 'pixel');
  let sequence = 0;
  const texts = message => message.content.filter(block => block?.type === 'text').map(block => block.text);
  // One call's hooks; `during` is what the call does on disk.
  function call(name, args, inner = ok) {
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const id = `${name}-${runId.slice(-2)}-${++sequence}`, ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    return {decision, finish(during) {
      during?.();
      const executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
      const envelope = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result: inner};
      const result = wrapped ? {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope} : inner;
      guard.afterToolCall({toolName, params: executed, toolCallId: id, result, ...(inner.isError ? {error: inner.content[0].text} : {})}, ctx);
      const message = {role: 'toolResult', toolName, toolCallId: id, isError: inner.isError === true, ...structuredClone(result)};
      return texts(guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message);
    }};
  }
  // One model round with one call.
  function step(name, args, inner = ok, during) {
    guard.observeModelCall({}, context);
    const started = call(name, args, inner);
    if (started.decision?.block === true) {
      guard.observeModelEnd({}, context);
      return [started.decision.blockReason];
    }
    const result = started.finish(during);
    guard.observeModelEnd({}, context);
    return result;
  }
  // The model's final answer, and the verification the ingress delivers.
  async function finish(answer) {
    guard.observeModelCall({}, context);
    guard.observeModelEnd({}, context);
    const decision = guard.beforeAgentFinalize({lastAssistantMessage: answer, messages: [
      {role: 'user', content: [{type: 'text', text: prompt}]}, {role: 'assistant', content: [{type: 'text', text: answer}]}]}, context);
    await guard.settleDelivery(runId);
    return {decision, delivery: guard.deliveryVerificationForRun(runId)};
  }
  // The owner's question card: pixel_ods_ask_user and its pending result.
  async function ask(question) {
    const questions = [{id: 'q', question, options: ['Yes', 'No']}];
    const result = await createAskUserTool().execute('ask', {questions});
    const ctx = {...context, toolName: 'pixel_ods_ask_user', toolCallId: `ask-${runId.slice(-2)}`};
    guard.beforeToolCall({toolName: 'pixel_ods_ask_user', params: {questions}, toolCallId: ctx.toolCallId}, ctx);
    guard.afterToolCall({toolName: 'pixel_ods_ask_user', toolCallId: ctx.toolCallId, params: {questions}, result}, ctx);
  }
  const doubling = () => {
    write('Playground/sprite-game/chars.js', DOUBLED);
    write('Playground/sprite-game/.crop-return.txt', '{"x": 12, "y": 40}\n');
  };
  return {guard, root, at, write, step, call, finish, ask, doubling, context, setClock: value => { clock = value; }};
}
const checkNotes = texts => texts.filter(text => text.startsWith('[ODS Pixel check]'));

for (const wrapped of [false, true]) {
  test(`${wrapped ? 'Tool Search' : 'direct'}: the exec that doubled chars.js gets one notice, and what remains is listed under the answer`, async t => {
    const {step, finish, doubling} = harness(t, {wrapped});
    assert.deepEqual(checkNotes(step('read', {path: 'Playground/sprite-game/chars.js'})), []);
    const patched = step('exec', {command: PATCH, workdir: WORKDIR}, ok, doubling);
    assert.equal(patched.at(-1), NOTICE);
    // Told once: a later call finds the same and says nothing more.
    assert.deepEqual(checkNotes(step('exec', {command: 'node --check chars.js', workdir: WORKDIR},
      failed('chars.js:9\nSyntaxError: Unexpected identifier'))), []);
    const {decision, delivery} = await finish('Done: the enemy sprites now have eight frames.');
    // No finalize revision: the pinned harness refuses one after side effects.
    assert.notEqual(decision?.action, 'revise');
    assert.equal(delivery.status, 'none');
    assert.equal(delivery.notice, FACTS);
  });
}

test('a model that fixes them after the notice leaves nothing to list', async t => {
  const {step, finish, doubling, write, at} = harness(t);
  assert.equal(step('exec', {command: PATCH, workdir: WORKDIR}, ok, doubling).at(-1), NOTICE);
  assert.deepEqual(checkNotes(step('write', {path: 'Playground/sprite-game/chars.js', content: FIXED}, ok,
    () => write('Playground/sprite-game/chars.js', FIXED))), []);
  assert.deepEqual(checkNotes(step('exec', {command: 'rm .crop-return.txt', workdir: WORKDIR}, ok,
    () => fs.rmSync(at('Playground/sprite-game/.crop-return.txt')))), []);
  assert.deepEqual((await finish('Done: eight frames, and the scratch file is removed.')).delivery, {status: 'none'});
});

test('a finding that appears later gets its own notice, and a file that breaks again is told again', async t => {
  const {step, finish, doubling, write} = harness(t);
  step('exec', {command: PATCH, workdir: WORKDIR}, ok, doubling);
  const later = step('edit', {path: 'Playground/sprite-game/chars.js', edits: [{oldText: 'size: 32', newText: 'size: 48'}]}, ok,
    () => write('Playground/sprite-game/chars.js', `${DOUBLED}window.__REPLAY = [];\n`));
  assert.deepEqual(checkNotes(later), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/sprite-game`: ' +
    `new debug global \`window.__REPLAY\` in \`chars.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  assert.deepEqual(checkNotes(step('write', {path: 'Playground/sprite-game/chars.js', content: FIXED}, ok,
    () => write('Playground/sprite-game/chars.js', FIXED))), []);
  assert.deepEqual(checkNotes(step('edit', {path: 'Playground/sprite-game/chars.js', edits: [{oldText: '4', newText: '4 +'}]}, ok,
    () => write('Playground/sprite-game/chars.js', `${FIXED}const = 1;\n`))), ['[ODS Pixel check] Portal checked the files this ' +
    `response changed in \`Playground/sprite-game\`: \`chars.js\` does not parse (line ${FIXED.split('\n').length}). ${FILE_CHECK_INSTRUCTION}`]);
  assert.match((await finish('Done.')).delivery.notice, /`chars\.js` does not parse/);
});

test('a new folder is the owner\'s to hear about: the model is never asked to delete it', async t => {
  // A folder goes with rm -rf, which Portal refuses; every later call would be refused too.
  const {step, finish, write} = harness(t);
  const cropped = step('exec', {command: 'python3 crop.py --out tmp-crops', workdir: WORKDIR}, ok,
    () => write('Playground/sprite-game/tmp-crops/elf-0.png', 'png'));
  assert.deepEqual(checkNotes(cropped), []);
  assert.deepEqual(checkNotes(step('exec', {command: 'python3 crop.py --check', workdir: WORKDIR})), []);
  assert.equal((await finish('Cropped the sprites.')).delivery.notice,
    "Portal's checks of the changed files in `Playground/sprite-game`: new folder `tmp-crops` in the project folder.");
});

test('findings a question card could not show are reported by the chat\'s next answer as still found', async t => {
  const first = harness(t);
  const patch = first.call('exec', {command: PATCH, workdir: WORKDIR});
  first.doubling();
  // The question went out while the patch was finishing: no notice to the model then.
  await first.ask('Should the eight frames also play faster?');
  assert.deepEqual(checkNotes(patch.finish()), []);
  const asked = await first.finish('Should they also play faster?');
  assert.equal(asked.delivery.status, 'pending');
  assert.equal(asked.delivery.notice, undefined, 'the ingress refuses a notice on a question card');
  // The owner answers: the chat's next turn, in the same project.
  const next = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'Yes, faster too.'});
  next.step('exec', {command: 'python3 speed.py', workdir: WORKDIR}, ok,
    () => next.write('Playground/sprite-game/speed.js', 'const SPEED = 2;\n'));
  const answered = await next.finish('Done: they play faster.');
  assert.equal(answered.delivery.notice, `Still found from earlier changes in ${FOUND}`);
  // Said once: a third turn has nothing carried.
  const third = harness(t, {shared: first, runId: 'chatcmpl_8b2d1fa3-6e30-4c9b-9d7f-2a4e6c8b0d31', prompt: 'Thanks.'});
  assert.equal((await third.finish('You are welcome.')).delivery.notice, undefined);
});

test('a delegated continuation reports what the owner run it continues changed', async t => {
  const owner = harness(t, {exampleOwnerRun: context => context.runId === NEXT_RUN ? RUN : undefined});
  owner.step('exec', {command: PATCH, workdir: WORKDIR}, ok, owner.doubling);
  // The continuation that delivers the chain's answer (subagent-delivery.mjs).
  const continuation = harness(t, {shared: owner, runId: NEXT_RUN, prompt: 'Report the result.'});
  assert.equal((await continuation.finish('The frames are done.')).delivery.notice, FACTS);
});

test('a new game is checked where the guard routed it, in its Playground folder', async t => {
  const {step, write, finish} = harness(t, {files: {}, prompt: 'Make me a snake game.'});
  const game = 'function tick() {}\nfunction tick() {}\nwindow.__SNAKE = state;\n';
  const written = step('write', {path: 'snake-game/game.js', content: game}, ok,
    () => write('Playground/snake-game/game.js', game));
  assert.deepEqual(checkNotes(written), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/snake-game`: ' +
    `\`tick\` is defined twice in \`game.js\`; new debug global \`window.__SNAKE\` in \`game.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  assert.equal((await finish('Your snake game is ready.')).delivery.notice, "Portal's checks of the changed files in `Playground/snake-game`: " +
    '`tick` is defined twice in `game.js`; new debug global `window.__SNAKE` in `game.js`.');
});

test('an ES module parse finishes in the background and reaches the model on a later result, and the owner at delivery', async t => {
  const {guard, step, write, finish, context} = harness(t, {files: {'Playground/mod-game/main.mjs': 'export const a = 1;\n'},
    prompt: 'In Playground/mod-game, add a score module.'});
  const written = step('write', {path: 'Playground/mod-game/score.mjs', content: 'export const = 0;\n'}, ok,
    () => write('Playground/mod-game/score.mjs', 'export const = 0;\n'));
  assert.deepEqual(checkNotes(written), [], 'not known yet');
  await guard.settleDelivery(context.runId);
  // A later result, even a read, carries it.
  assert.deepEqual(checkNotes(step('read', {path: 'Playground/mod-game/main.mjs'})), ['[ODS Pixel check] Portal checked the files ' +
    `this response changed in \`Playground/mod-game\`: \`score.mjs\` does not parse (line 1). ${FILE_CHECK_INSTRUCTION}`]);
  assert.equal((await finish('Added the score module.')).delivery.notice,
    "Portal's checks of the changed files in `Playground/mod-game`: `score.mjs` does not parse (line 1).");
});

test('the checks share the one notice line with the example\'s line, within its 1024 characters', async t => {
  // ODS put the shipped example back after a call (example-guard.mjs).
  const exampleGuard = {begin: () => undefined, guarding: () => true, check: () => ({outcome: 'restored'}), end: () => undefined};
  const {step, finish, write} = harness(t, {exampleGuard});
  step('exec', {command: 'python3 split.py', workdir: WORKDIR}, ok, () => {
    for (let i = 0; i < 40; i++) write(`Playground/sprite-game/parts/part-${String(i).padStart(2, '0')}.js`, `const part${i} = ;\n`);
  });
  const {notice} = (await finish('Done: chars.js is split into parts.')).delivery;
  assert.ok(notice.startsWith(`${exampleNotice('restored')} Portal's checks of the changed files in \`Playground/sprite-game\`: ` +
    '`parts/part-00.js` does not parse (line 1); '), notice);
  assert.match(notice, /; and \d+ more\.$/);
  assert.ok(notice.length <= 1024, `${notice.length}`);
});

test('a response that touches no project checks nothing, and an untouched project is never reported', async t => {
  const first = harness(t, {files: {'Playground/sprite-game/chars.js': CHARS, 'Playground/other/main.js': 'const a = 1;\n'},
    prompt: 'Write my todo list to notes/todo.md.'});
  const texts = [
    ...first.step('write', {path: 'notes/todo.md', content: '- milk\n'}, ok, () => first.write('notes/todo.md', '- milk\n')),
    // A command in the workspace root that changes a project without naming it.
    ...first.step('exec', {command: 'sh tidy.sh', workdir: '/workspace'}, ok, () => first.write('Playground/sprite-game/chars.js', DOUBLED)),
  ];
  assert.deepEqual(checkNotes(texts), []);
  assert.equal((await first.finish('Your list is saved.')).delivery.notice, undefined);
  // The next response touches Playground/other only.
  const next = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'In Playground/other, rename a to b.'});
  const renamed = next.step('edit', {path: 'Playground/other/main.js', edits: [{oldText: 'a', newText: 'b'}]}, ok, () => {
    next.write('Playground/other/main.js', 'const b = 1;\n');
    next.write('Playground/other/.rename-notes.txt', 'a -> b');
    next.write('Playground/sprite-game/.crop-return.txt', 'x');
  });
  assert.deepEqual(checkNotes(renamed), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/other`: ' +
    `new file \`.rename-notes.txt\` in the project folder. ${FILE_CHECK_INSTRUCTION} ${FILE_CHECK_REMOVAL}`]);
  assert.equal((await next.finish('Renamed.')).delivery.notice,
    "Portal's checks of the changed files in `Playground/other`: new file `.rename-notes.txt` in the project folder.");
});

test('an exec that only reads files is not a first touch, and nothing is walked after it', async t => {
  const {step, write, finish} = harness(t);
  const broken = `${CHARS}const = 1;\n`;
  // The owner's own edit lands while a read-only command runs: that command
  // touches nothing, so the next call's first print already holds it.
  assert.deepEqual(checkNotes(step('exec', {command: 'grep -n frames chars.js', workdir: WORKDIR}, ok,
    () => write('Playground/sprite-game/chars.js', broken))), []);
  assert.deepEqual(checkNotes(step('exec', {command: 'python3 bump.py', workdir: WORKDIR})), []);
  // A change during a read is not looked for after the read...
  assert.deepEqual(checkNotes(step('exec', {command: 'cat chars.js', workdir: WORKDIR}, ok,
    () => write('Playground/sprite-game/chars.js', `${broken}window.__CAT = 1;\n`))), []);
  // ...but after the next call that may change files, and at delivery.
  assert.deepEqual(checkNotes(step('exec', {command: 'python3 tidy.py', workdir: WORKDIR})), ['[ODS Pixel check] Portal checked the ' +
    `files this response changed in \`Playground/sprite-game\`: new debug global \`window.__CAT\` in \`chars.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  assert.equal((await finish('Done.')).delivery.notice,
    "Portal's checks of the changed files in `Playground/sprite-game`: new debug global `window.__CAT` in `chars.js`.");
});

test('a refused call is not a first touch: what changes before Portal\'s first change is not Portal\'s', async t => {
  const {step, write, finish} = harness(t);
  // Refused (edit cannot create a file): it runs nothing, and fingerprints nothing.
  assert.match(step('edit', {path: 'Playground/sprite-game/new.js', edits: [{oldText: '', newText: 'x'}]})[0], /edit cannot create a new file/);
  // The owner's own edit lands before Portal's first change.
  write('Playground/sprite-game/chars.js', `${CHARS}window.__OWNER = 1;\n`);
  assert.deepEqual(checkNotes(step('exec', {command: 'python3 bump.py', workdir: WORKDIR})), []);
  assert.equal((await finish('Done.')).delivery.notice, undefined);
});

test('a background exec that ends through process is checked on the process result', async t => {
  const {step, doubling} = harness(t);
  const running = {content: [{type: 'text', text: 'Command still running (session s1).'}], details: {status: 'running', sessionId: 's1', pid: 42}};
  assert.deepEqual(checkNotes(step('exec', {command: 'python3 patch.py', workdir: WORKDIR, background: true}, running)), []);
  const polled = step('process', {action: 'poll', sessionId: 's1'},
    {content: [{type: 'text', text: 'done'}], details: {status: 'completed', exitCode: 0, sessionId: 's1'}}, doubling);
  assert.deepEqual(checkNotes(polled), [NOTICE]);
});

test('no notice while a question waits, after coding tools ended, a refused delete or an ODS abort; none in Operations', async t => {
  // A call in flight when the run's state changes: its result carries no notice.
  for (const [label, interrupt] of [
    ['question', h => h.ask('Faster too?')],
    // Two no-op edits end coding tools for the response.
    ['coding ended', h => { for (let i = 0; i < 2; i++) h.call('edit', {path: 'Playground/sprite-game/chars.js', edits: [{oldText: 'a', newText: 'a'}]}); }],
    // A refused recursive delete ends tool use for the response.
    ['refused delete', h => assert.equal(h.call('exec', {command: 'rm -rf tmp-crops', workdir: WORKDIR}).decision?.block, true)],
    // A third edit that tries to create a file aborts the run.
    ['ods abort', h => { for (let i = 0; i < 3; i++) h.call('edit', {path: 'Playground/sprite-game/new.js', edits: [{oldText: '', newText: 'x'}]}); }],
  ]) {
    const h = harness(t);
    const patch = h.call('exec', {command: PATCH, workdir: WORKDIR});
    assert.notEqual(patch.decision?.block, true, label);
    await interrupt(h);
    assert.deepEqual(checkNotes(patch.finish(h.doubling)), [], label);
  }
  // Operations: the host-receipt work keeps its own delivery; its files are not checked.
  const operations = harness(t, {prompt: 'Check the ODS host hostname.'});
  const patch = operations.call('exec', {command: PATCH, workdir: WORKDIR});
  assert.notEqual(patch.decision?.block, true, 'the command runs');
  assert.deepEqual(checkNotes(patch.finish(operations.doubling)), []);
  assert.equal((await operations.finish('The hostname is tower.')).delivery.notice, undefined);
});

test('a defect in the checks never blocks a call or the delivery: they switch off with a warning', async t => {
  const warnings = [];
  for (const broken of ['touch', 'take', 'findings']) {
    const changedFileChecks = () => ({
      touch() { if (broken === 'touch') throw new TypeError('touch defect'); },
      take() { if (broken === 'take') throw new TypeError('take defect'); return []; },
      findings() { if (broken === 'findings') throw new TypeError('findings defect'); return []; },
      settle: async () => {},
    });
    const h = harness(t, {changedFileChecks, warn: message => warnings.push(message)});
    const patch = h.call('exec', {command: PATCH, workdir: WORKDIR});
    assert.notEqual(patch.decision?.block, true, broken);
    assert.deepEqual(checkNotes(patch.finish(h.doubling)), [], broken);
    const {delivery} = await h.finish('Done.');
    assert.equal(delivery.notice, undefined, broken);
    assert.ok(warnings.some(warning => warning.includes(`${broken} defect`)), broken);
    // Off for the rest of the response: no further call reaches them.
    assert.deepEqual(checkNotes(h.step('exec', {command: 'python3 again.py', workdir: WORKDIR})), [], broken);
  }
});

test('after a tool-limit stop the model is told nothing more, and the stop lists the checks after its changed files', async t => {
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
  const stopped = step('exec', {command: 'sh crop.sh', workdir: WORKDIR}, failed('crop failed'),
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
  const {guard, doubling, context, call} = harness(t);
  // The patch was running when the owner pressed Stop.
  const patch = call('exec', {command: PATCH, workdir: WORKDIR});
  assert.notEqual(patch.decision?.block, true);
  doubling();
  assert.equal(await guard.abortUserRun(USER), true);
  assert.deepEqual(checkNotes(patch.finish()), []);
  assert.equal(guard.deliveryVerificationForRun(context.runId).notice, undefined);
});
