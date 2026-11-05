import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {FILE_CHECK_INSTRUCTION, FILE_CHECK_LIMITS, FILE_CHECK_REMOVAL, FILE_CHECKS_OFF} from '../plugin/changed-file-checks.mjs';
import {exampleNotice} from '../plugin/example-guard.mjs';
import {createPendingClarifications} from '../plugin/pending-clarification.mjs';
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
// Delegated children of RUN, each in its own subagent session.
const CHILDREN = ['chatcmpl_1c4e7a20-0b3d-4f6a-9c8e-2d5b7f9a1c31', 'chatcmpl_2d5f8b31-1c4e-4a7b-8d9f-3e6c8a0b2d42',
  'chatcmpl_3e6a9c42-2d5f-4b8c-9e0a-4f7d9b1c3e53'];
const child = (index, shared, options = {}) => harness(null, {shared, runId: CHILDREN[index], sessionId: `child-${index}`,
  sessionKey: `agent:pixel:subagent:${index}${'0'.repeat(7)}-1111-4222-8333-944455556666`, ...options});
const delegatedFrom = context => context.runId === RUN ? undefined : RUN;
const ok = {content: [{type: 'text', text: '(no output)'}], details: {status: 'completed', exitCode: 0, durationMs: 4}};
const failed = text => ({isError: true, content: [{type: 'text', text}], details: {status: 'completed', exitCode: 2, durationMs: 4}});
const quietExample = {begin: () => undefined, guarding: () => false, check: () => undefined, end: () => undefined};

function harness(t, {files = {'Playground/sprite-game/index.html': '<script src="chars.js"></script>\n',
  'Playground/sprite-game/chars.js': CHARS}, prompt = PROMPT, wrapped = false, runId = RUN, shared,
  sessionKey = `agent:pixel:openai-user:${USER}`, sessionId = 'sprite-session', trigger = 'user', clarifications = false,
  ...options} = {}) {
  let root = shared?.root;
  if (!root) {
    root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'ods-file-checks-guard-')));
    t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  }
  const at = relative => path.join(root, ...relative.split('/'));
  const write = (relative, content) => { fs.mkdirSync(path.dirname(at(relative)), {recursive: true}); fs.writeFileSync(at(relative), content); };
  if (!shared) for (const [relative, content] of Object.entries(files)) write(relative, content);
  let clock = 0;
  // clarifications: the chat keeps a question card's record, in the test's folder.
  const guard = shared?.guard ?? createToolLoopGuard({now: () => clock, abortRun: () => true, exampleGuard: quietExample,
    abortRunAndDrain: async () => ({aborted: true, drained: true}),
    ...(clarifications ? {pendingClarifications: createPendingClarifications({stateDir: path.join(root, 'clarifications')})} : {}),
    ...options});
  const context = {agentId: 'pixel', runId, sessionId, sessionKey, trigger};
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

test('children working side by side each hear only of what changed while their own calls ran; the turn lists each finding once', async t => {
  // The owner run only delegates; two children work in the project.
  const owner = harness(t, {exampleOwnerRun: delegatedFrom});
  const a = child(0, owner, {prompt: 'Give the sprites eight frames.'}), b = child(1, owner, {prompt: 'Tune the speed.'});
  assert.deepEqual(checkNotes(b.step('exec', {command: 'python3 speed.py', workdir: WORKDIR})), []);
  assert.deepEqual(checkNotes(a.step('exec', {command: PATCH, workdir: WORKDIR}, ok, a.doubling)), [NOTICE]);
  // b found the same files changed since its own first touch, by a's call.
  assert.deepEqual(checkNotes(b.step('exec', {command: 'python3 speed.py --check', workdir: WORKDIR})), []);
  // b's own change later is b's to hear about.
  assert.deepEqual(checkNotes(b.step('exec', {command: 'python3 speed.py --write', workdir: WORKDIR}, ok,
    () => b.write('Playground/sprite-game/speed.js', 'const SPEED = 2;\nwindow.__SPEED = SPEED;\n'))),
  ['[ODS Pixel check] Portal checked the files this response changed in `Playground/sprite-game`: ' +
    `new debug global \`window.__SPEED\` in \`speed.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  // The continuation delivers the turn: both children's findings, each once.
  const continuation = harness(t, {shared: owner, runId: NEXT_RUN, prompt: 'Report the result.'});
  assert.equal((await continuation.finish('The frames are done.')).delivery.notice,
    FACTS.replace('in `chars.js`; new file', 'in `chars.js`; new debug global `window.__SPEED` in `speed.js`; new file'));
});

test('names in the owner\'s own words are not debug globals in the children they delegate to; a delegated task\'s are', async t => {
  const owner = harness(t, {exampleOwnerRun: delegatedFrom, prompt: 'In Playground/sprite-game, expose the game as window.__GAME.'});
  const worker = child(0, owner, {prompt: 'Add window.__GAME, and a window.__TRACE log.'});
  const added = worker.step('exec', {command: 'python3 hooks.py', workdir: WORKDIR}, ok,
    () => worker.write('Playground/sprite-game/chars.js', `${CHARS}window.__GAME = SPRITE_CACHE;\nwindow.__TRACE = [];\n`));
  assert.deepEqual(checkNotes(added), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/sprite-game`: ' +
    `new debug global \`window.__TRACE\` in \`chars.js\`. ${FILE_CHECK_INSTRUCTION}`]);
  // A scheduled run's prompt is not the owner's words either.
  const cron = harness(t, {trigger: 'cron', prompt: 'Nightly: set window.__NIGHTLY in Playground/sprite-game.'});
  assert.deepEqual(checkNotes(cron.step('exec', {command: 'python3 nightly.py', workdir: WORKDIR}, ok,
    () => cron.write('Playground/sprite-game/chars.js', `${CHARS}window.__NIGHTLY = 1;\n`))),
  ['[ODS Pixel check] Portal checked the files this response changed in `Playground/sprite-game`: ' +
    `new debug global \`window.__NIGHTLY\` in \`chars.js\`. ${FILE_CHECK_INSTRUCTION}`]);
});

test('names in the request a question card recorded are the owner\'s words when the owner answers it', async t => {
  const first = harness(t, {clarifications: true, prompt: 'In Playground/sprite-game, expose the game as window.__GAME.'});
  await first.ask('Should the hook also hold the level?');
  assert.equal((await first.finish('Should the hook also hold the level?')).delivery.status, 'pending');
  const answer = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'Should the hook also hold the level?\nYes'});
  const hooked = answer.step('exec', {command: 'python3 hook.py', workdir: WORKDIR}, ok,
    () => answer.write('Playground/sprite-game/chars.js', `${CHARS}window.__GAME = SPRITE_CACHE;\nwindow.__LEVEL = 1;\n`));
  assert.deepEqual(checkNotes(hooked), ['[ODS Pixel check] Portal checked the files this response changed in `Playground/sprite-game`: ' +
    `new debug global \`window.__LEVEL\` in \`chars.js\`. ${FILE_CHECK_INSTRUCTION}`]);
});

test('what a question card could not show waits for an owner turn: a cron run or a delegated child leaves it for the next answer', async t => {
  const LATER = 'chatcmpl_5a8c1e64-4f71-4dae-9a2c-6b9f1d3e5a75';
  const first = harness(t, {exampleOwnerRun: context => context.runId === CHILDREN[0] ? LATER : undefined});
  const patch = first.call('exec', {command: PATCH, workdir: WORKDIR});
  first.doubling();
  await first.ask('Should the eight frames also play faster?');
  patch.finish();
  assert.equal((await first.finish('Should they also play faster?')).delivery.status, 'pending');
  // A scheduled run in the same chat, and a child that a later owner turn,
  // still running, delegated to.
  const cron = harness(t, {shared: first, runId: 'chatcmpl_4f7b0d53-3e60-4c9d-8f1b-5a8e0c2d4f64', trigger: 'cron', prompt: 'Daily summary.'});
  assert.equal((await cron.finish('Summary sent.')).delivery.notice, undefined);
  harness(t, {shared: first, runId: LATER, prompt: 'Draw the sprites with a helper.'});
  const worker = child(0, first, {prompt: 'Draw the sprites.'});
  assert.equal((await worker.finish('Drawn.')).delivery.notice, undefined);
  const next = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'Yes, faster too.'});
  assert.equal((await next.finish('Done: they play faster.')).delivery.notice, `Still found from earlier changes in ${FOUND}`);
});

test('a pending run that is no owner turn keeps what an earlier pending turn left, beside its own', async t => {
  const first = harness(t);
  const patch = first.call('exec', {command: PATCH, workdir: WORKDIR});
  first.doubling();
  await first.ask('Should the eight frames also play faster?');
  patch.finish();
  assert.equal((await first.finish('Should they also play faster?')).delivery.status, 'pending');
  // A scheduled run in the same chat changes a file and asks too.
  const cron = harness(t, {shared: first, runId: 'chatcmpl_4f7b0d53-3e60-4c9d-8f1b-5a8e0c2d4f64', trigger: 'cron', prompt: 'Nightly tidy.'});
  cron.step('exec', {command: 'python3 tidy.py', workdir: WORKDIR}, ok,
    () => cron.write('Playground/sprite-game/tidy.js', 'window.__TIDY = 1;\n'));
  await cron.ask('Keep the nightly log?');
  assert.equal((await cron.finish('Keep the nightly log?')).delivery.status, 'pending');
  const next = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'Yes, faster too.'});
  assert.equal((await next.finish('Done: they play faster.')).delivery.notice, `Still found from earlier changes in ${
    FOUND.replace('in `chars.js`; new file', 'in `chars.js`; new debug global `window.__TIDY` in `tidy.js`; new file')}`);
});

test('a chat carries the checks of its last eight pending responses', async t => {
  const first = harness(t);
  let turn = first;
  for (let i = 0; i < 9; i++) {
    if (i) turn = harness(t, {shared: first, runId: `chatcmpl_9${i}0000a0-1a2b-4c3d-8e4f-5a6b7c8d9e0${i}`, prompt: `Answer ${i}.`});
    const hook = turn;
    hook.step('exec', {command: `python3 hook.py ${i}`, workdir: WORKDIR}, ok,
      () => hook.write(`Playground/sprite-game/hook-${i}.js`, `window.__HOOK${i} = ${i};\n`));
    await hook.ask(`Question ${i}?`);
    assert.equal((await hook.finish(`Question ${i}?`)).delivery.status, 'pending');
  }
  // Only the first response found the first hook: it is no longer carried.
  const last = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'Thanks.'});
  const {notice} = (await last.finish('Done.')).delivery;
  assert.match(notice, /^Still found from earlier changes in `Playground\/sprite-game`: .*`window\.__HOOK8`/);
  assert.doesNotMatch(notice, /window\.__HOOK0`/);
});

test('a defect that switched off a child\'s checks is said in the turn\'s line', async t => {
  const changedFileChecks = () => ({touch() { throw new TypeError('child defect'); }, take: () => [], findings: () => [], settle: async () => {}});
  const owner = harness(t, {exampleOwnerRun: delegatedFrom, changedFileChecks});
  const worker = child(0, owner, {prompt: 'Give the sprites eight frames.'});
  assert.deepEqual(checkNotes(worker.step('exec', {command: PATCH, workdir: WORKDIR}, ok, worker.doubling)), []);
  const continuation = harness(t, {shared: owner, runId: NEXT_RUN, prompt: 'Report the result.'});
  assert.equal((await continuation.finish('The frames are done.')).delivery.notice, FILE_CHECKS_OFF);
});

test('a finding that both an earlier pending response and this one made is listed once, as this one\'s', async t => {
  const first = harness(t);
  const hook = first.call('exec', {command: 'python3 hook.py', workdir: WORKDIR});
  first.write('Playground/sprite-game/chars.js', `${CHARS}window.__A = 1;\n`);
  await first.ask('Keep the debug hook?');
  hook.finish();
  assert.equal((await first.finish('Keep it?')).delivery.status, 'pending');
  const next = harness(t, {shared: first, runId: NEXT_RUN, prompt: 'Yes, and set it twice.'});
  next.step('exec', {command: 'python3 hook.py --twice', workdir: WORKDIR}, ok,
    () => next.write('Playground/sprite-game/chars.js', `${CHARS}window.__A = 1;\nwindow.__A = 2;\n`));
  assert.equal((await next.finish('Done.')).delivery.notice,
    "Portal's checks of the changed files in `Playground/sprite-game`: new debug global `window.__A` in `chars.js`.");
});

test('one module queue for the gateway: two parses at a time, each version once whichever response asks', async t => {
  let calls = 0, live = 0, peak = 0;
  const checkModule = async () => {
    calls += 1; live += 1; peak = Math.max(peak, live);
    await new Promise(resolve => setTimeout(resolve, 5));
    live -= 1;
    return {ok: true};
  };
  const name = i => `Playground/esm/m${String(i).padStart(2, '0')}.js`;
  const files = {'Playground/esm/index.html': '<script type="module" src="m00.js"></script>\n'};
  for (let i = 0; i < 6; i++) files[name(i)] = `export const m${i} = ${i};\n`;
  const owner = harness(t, {files, exampleOwnerRun: delegatedFrom, checkModule, prompt: 'In Playground/esm, double every module.'});
  owner.step('exec', {command: 'python3 plan.py', workdir: '/workspace/Playground/esm'});
  for (let k = 0; k < 3; k++) {
    const worker = child(k, owner, {prompt: `Double modules ${2 * k} and ${2 * k + 1}.`});
    worker.step('exec', {command: `python3 double.py ${k}`, workdir: '/workspace/Playground/esm'}, ok, () => {
      for (const i of [2 * k, 2 * k + 1]) worker.write(name(i), `export const m${i} = ${i} * 2;\n`);
    });
  }
  const continuation = harness(t, {shared: owner, runId: NEXT_RUN, prompt: 'Report the result.'});
  assert.equal((await continuation.finish('Doubled.')).delivery.notice, undefined);
  assert.equal(calls, 6, 'each changed version once, none at first touch');
  assert.ok(peak <= 2, `${peak} at once`);
});

test('the delegated-delivery route waits a bounded time for module parses, then says what could not be checked', async t => {
  const {guard, step, write, context} = harness(t, {files: {'Playground/mod-game/main.mjs': 'export const a = 1;\n'},
    prompt: 'In Playground/mod-game, add a score module.', checkModule: () => new Promise(() => {})});
  step('write', {path: 'Playground/mod-game/score.mjs', content: 'export const score = 0;\n'}, ok,
    () => write('Playground/mod-game/score.mjs', 'export const score = 0;\n'));
  const started = Date.now();
  await guard.settleDelivery(context.runId, {delegated: true});
  const waited = Date.now() - started;
  // Its caller gives up after 5 s (host/pixel_ingress.mjs).
  assert.ok(waited >= FILE_CHECK_LIMITS.delegatedSettleMs - 100 && waited < FILE_CHECK_LIMITS.settleMs, `${waited} ms`);
  assert.equal(guard.deliveryVerificationForRun(context.runId).notice,
    "Portal's checks of the changed files in `Playground/mod-game`: could not check whether `score.mjs` parses.");
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
  for (const broken of ['touch', 'take', 'findings', 'settle']) {
    const changedFileChecks = () => ({
      touch() { if (broken === 'touch') throw new TypeError('touch defect'); },
      take() { if (broken === 'take') throw new TypeError('take defect'); return []; },
      findings() { if (broken === 'findings') throw new TypeError('findings defect'); return []; },
      async settle() { if (broken === 'settle') throw new TypeError('settle defect'); },
    });
    const h = harness(t, {changedFileChecks, warn: message => warnings.push(message)});
    const patch = h.call('exec', {command: PATCH, workdir: WORKDIR});
    assert.notEqual(patch.decision?.block, true, broken);
    assert.deepEqual(checkNotes(patch.finish(h.doubling)), [], broken);
    const {delivery} = await h.finish('Done.');
    // The owner reads that they could not finish.
    assert.equal(delivery.notice, FILE_CHECKS_OFF, broken);
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
  // The call that reaches the limit also leaves a scratch file: the stop adds
  // no check notice, and its result ends with the answer instruction (the
  // tool limit may keep the call's own output ahead of it).
  const stopped = step('exec', {command: 'sh crop.sh', workdir: WORKDIR}, failed('crop failed'),
    () => write('Playground/sprite-game/.crop-return.txt', 'x'));
  assert.equal(stopped.at(-1), PROGRESS_FINALIZATION_INSTRUCTION);
  assert.equal(checkNotes(stopped).length, 0, stopped.join('\n'));
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
