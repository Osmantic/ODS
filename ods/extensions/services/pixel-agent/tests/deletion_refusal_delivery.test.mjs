// Replays tower2 open-prompt 07 photo-renamer (Qwen3-Coder-Next, ODS main
// d4a61f33, session fe9ee859): "write a python script that renames all photos
// in a folder by date taken, and test it". The model wrote rename_photos.py and
// test_rename.py, and `python3 test_rename.py` passed. It then ran an
// unrequested manual test ending `&& rm -rf /workspace/test_manual`. The guard
// refused that command before it ran, refused the next harmless exec and
// aborted the run, as designed. The owner received only the model-directed
// refusal text ("Do not retry ... wait for a new owner instruction") with
// product outcome failed, although the files and the passing test existed.
// Hermes and OpenCode delivered the same prompt on the same hardware and model.
//
// Every run here goes through the lifecycle callbacks plugin/index.js
// registers (before_tool_call, after_tool_call, agent_end) around the real
// guard. agent_end runs endPreviewRevalidation before the ingress reads
// delivery, as OpenClaw 2026.6 does.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import http from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {createToolLoopGuard, RECURSIVE_DELETE_REQUIRES_OWNER_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {createWorkspaceBundleAdmission} from '../plugin/workspace-bundle.mjs';
import {withPixelCronDeliveryDefault} from '../plugin/cron-delivery-default.mjs';
import {AGENT_SKILLS, DERIVED_FILE_CONTRACT, NAMED_ITEM_CONTRACT, RECURSIVE_DELETE_CONTRACT} from '../plugin/agent-skills.mjs';
import {ODS_SEPTEMBER16_CONVERSATION_CONTRACT, promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const RECORDED = JSON.parse(fs.readFileSync(new URL('./fixtures/photo-renamer-tower2-d4a61f33.json', import.meta.url), 'utf8'));
const TOWER3 = JSON.parse(fs.readFileSync(new URL('./fixtures/photo-renamer-tower3-d4a61f33.json', import.meta.url), 'utf8'));
const CALLS = RECORDED.calls;
const PROJECT = 'Playground/photo-renamer';
const ELLIPSIS = String.fromCharCode(0x2026);
const LEAD = 'Pixel stopped using tools because a command included a recursive deletion that you did not ask for. ' +
  "That command was refused and did not run.\nResults recorded by Pixel's tools before that:\n";
const FILES = '- File written: `/workspace/Playground/photo-renamer/rename_photos.py`.\n' +
  '- File written: `/workspace/Playground/photo-renamer/test_rename.py`.\n';
const NO_FILES = "- No file was written or changed with Pixel's file tools.\n";
const TEST = 'The latest recognized test command, `python3 test_rename.py` in `/workspace/Playground/photo-renamer`,';
const UNITTEST = 'The latest recognized test command, `python3 -m unittest -v` in `/workspace/Playground/photo-renamer`,';
const CURRENT = ' passed, and no tool call that could change the workspace ran after it.\n';
const STALE = ' passed, but a later tool call or command could have changed the workspace, so that result is not current.\n';
const COMPLETE = 'This does not establish complete test coverage or completion of every requested step.';
const INCOMPLETE = 'This request is not complete. Ask Pixel to continue, or say explicitly if you want a folder deleted.';
const RECEIPT = LEAD + FILES + `- ${TEST}${CURRENT}` + COMPLETE;
const UNITTEST_OK = 'test_a (test_rename.T.test_a) ... ok\n\n' +
  '----------------------------------------------------------------------\nRan 1 test in 0.001s\n\nOK';

// The lifecycle block of plugin/index.js, run with the real guard and inert
// access, goal and activity collaborators.
const INDEX = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
const LIFECYCLE_START = INDEX.indexOf('    if (!managedRuntime) {');
const LIFECYCLE = INDEX.slice(LIFECYCLE_START, INDEX.indexOf('    api.registerHttpRoute(', LIFECYCLE_START));
function registeredHooks(guard) {
  const hooks = {};
  vm.runInNewContext(LIFECYCLE, {
    api: {on: (name, callback) => { hooks[name] = callback; }},
    toolLoopGuard: guard, AGENT_ID: 'pixel', managedRuntime: false, withPixelCronDeliveryDefault,
    bundleAdmission: createWorkspaceBundleAdmission(),
    accessRuntime: {isProbe: () => false, admit() {}, finish() {}, beforeTool() {}, afterTool() {}},
    goalProgress: {before() {}, update() {}, finish() {}},
    taskActivity: {before() {}, after() {}, finish() {}},
  });
  return hooks;
}

// OpenClaw's hook order for each round: model call start and end,
// before_tool_call, the executed result (or the SDK's veto result for a
// blocked call), after_tool_call and tool_result_persist.
function session(t, {prompt = RECORDED.submittedUserText, runId = RECORDED.runId, sessionId = RECORDED.sessionId} = {}) {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-deletion-refusal-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const prepared = [], signalled = [], aborted = [], decisions = [];
  const guard = createToolLoopGuard({
    abortRun: (id) => { aborted.push(id); return true; },
    execControl: {
      signal: (id) => { signalled.push(id); return true; },
      prepare: (_runId, command) => {
        prepared.push(command);
        return `/run/pixel-ods-control/cancellable-exec.sh ${'0'.repeat(64)} ${Buffer.from(command).toString('base64')}`;
      },
    },
    warn() {}, info() {},
  });
  const hooks = registeredHooks(guard);
  const context = {agentId: 'pixel', runId, sessionId, sessionKey: 'agent:pixel:openai-user:owner'};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root});
  let round = 0, count = 0;
  async function call(tool, params, result, id = `call-${++count}`) {
    const modelCall = `${context.runId}:model:${++round}`;
    guard.observeModelCall({callId: modelCall}, context);
    guard.observeModelEnd({callId: modelCall, outcome: 'completed'}, context);
    const ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, toolCallId: id, runId: context.runId, params: structuredClone(params)};
    const decision = await hooks.before_tool_call(event, ctx);
    decisions.push(decision);
    const admitted = decision?.block ? event.params : {...event.params, ...decision?.params};
    const outcome = decision?.block
      ? {isError: true, content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: decision.blockReason}}
      : result;
    if (!decision?.block && tool === 'write' && !outcome.isError) {
      fs.mkdirSync(path.dirname(path.join(root, admitted.path)), {recursive: true});
      fs.writeFileSync(path.join(root, admitted.path), admitted.content);
    }
    hooks.after_tool_call({toolName: tool, toolCallId: id, runId: context.runId, params: admitted, result: outcome,
      ...(outcome.isError ? {error: outcome.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolName: tool, toolCallId: id, message: {role: 'toolResult', toolName: tool,
      toolCallId: id, isError: outcome.isError === true, ...structuredClone(outcome)}}, ctx);
    return decision;
  }
  // The final model call, the registered agent_end, then the plugin's
  // verification route: settle, then read.
  async function delivered({outcome = 'error'} = {}) {
    const modelCall = `${context.runId}:model:${++round}`;
    guard.observeModelCall({callId: modelCall}, context);
    guard.observeModelEnd({callId: modelCall, outcome}, context);
    await hooks.agent_end({runId: context.runId}, context);
    await guard.settleDelivery(context.runId);
    return guard.deliveryVerificationForRun(context.runId);
  }
  return {guard, hooks, context, call, delivered, decisions, prepared, signalled, aborted};
}

const text = (value) => ({content: [{type: 'text', text: value}]});
const done = (exitCode, output) => ({content: [{type: 'text', text: output}],
  details: {status: 'completed', exitCode, aggregated: output}, ...(exitCode ? {isError: true} : {})});
const running = (sessionId) => ({content: [{type: 'text', text: `Command still running (session ${sessionId}).`}],
  details: {status: 'running', sessionId}});
const polled = (sessionId, exitCode, output) => ({content: [{type: 'text', text: output}],
  details: {status: 'completed', sessionId, exitCode, aggregated: output}, ...(exitCode ? {isError: true} : {})});
// Executed results are the recorded tool outputs without the ODS suffix that
// persistence appended.
const recordedResult = (call) => ({content: [{type: 'text', text: call.result.text.split('[ODS Pixel ')[0]}],
  ...(call.result.details ? {details: call.result.details} : {}), ...(call.result.isError ? {isError: true} : {})});
async function replay(t, calls, options) {
  const run = session(t, options);
  for (const call of calls) await run.call(call.tool, call.arguments, recordedResult(call), call.id);
  return run;
}
const byIndex = (...numbers) => numbers.map(number => CALLS[number - 1]);
const exec = (id, command, workdir = '/workspace') =>
  ({id, tool: 'exec', arguments: {command, workdir}, result: {isError: false, text: ''}});
async function writeProject(run, directory = PROJECT) {
  for (const [file, content] of [['rename_photos.py', 'def rename(p):\n    return p\n'],
    ['test_rename.py', 'import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n']]) {
    const decision = await run.call('write', {path: `${directory}/${file}`, content}, text(`Successfully wrote ${content.length} bytes`));
    assert.notEqual(decision?.block, true, decision?.blockReason);
  }
}
async function refuse(run, command = 'rm -rf /workspace/test_manual') {
  assert.equal((await run.call('exec', {command}, done(0, ''))).blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
}

test('recorded tower2 run: eleven calls, a passing test, then the refused cleanup and the refusal text', () => {
  assert.equal(RECORDED.sessionId, 'fe9ee859-c533-483c-b978-5c4a672dc126');
  assert.match(RECORDED.evidence, /sha256 8d21ebc1d247f332de279b80e3fd205d5a477a15211dd5bb1f47f158e4ca6f89/);
  assert.equal(RECORDED.prompt, 'write a python script that renames all photos in a folder by date taken, and test it');
  assert.ok(RECORDED.submittedUserText.startsWith(`${RECORDED.prompt}\n\n[ODS Portal delivery requirement:`));
  assert.deepEqual(CALLS.map(call => call.tool),
    ['write', 'write', 'write', 'exec', 'exec', 'write', 'exec', 'write', 'exec', 'exec', 'exec']);
  assert.deepEqual(CALLS.map(call => call.result.isError),
    [true, false, false, false, true, false, true, false, false, true, true]);
  assert.match(CALLS[9].arguments.command, /python3 \/workspace\/Playground\/photo-renamer\/rename_photos\.py \. --dry-run && rm -rf \/workspace\/test_manual$/);
  assert.equal(CALLS[10].arguments.command, 'mkdir -p /workspace/test_manual && cd /workspace/test_manual && ls -la');
  assert.deepEqual(RECORDED.finalAssistant, {stopReason: 'aborted', errorMessage: 'This operation was aborted'});
  // What the owner received on main d4a61f33.
  assert.equal(RECORDED.delivered.text, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.equal(RECORDED.delivered.productOutcome, 'failed');
});

test('tower2 replay through the registered hooks: the refused cleanup keeps the current pass and the owner gets a receipt', async t => {
  // agent_end advances the preview generation before observing the end; the
  // pass must survive that, because the ingress reads delivery after it.
  assert.match(LIFECYCLE, /api\.on\("agent_end", \(event, context\) => \{\n\s+toolLoopGuard\.endPreviewRevalidation\(event, context\);\n\s+toolLoopGuard\.observeAgentEnd\(event, context\);/);
  const run = await replay(t, CALLS);
  const {guard, context, decisions, prepared, signalled, aborted} = run;
  // #1 is the recorded path correction; #10 and #11 are the recorded refusals.
  // The correction is the Playground routing guard's text, which #6751
  // extended after this session was recorded, so only its recorded sentence
  // is pinned here.
  assert.ok(decisions[0]?.blockReason.includes(CALLS[0].result.text), decisions[0]?.blockReason);
  assert.match(decisions[0].blockReason, /For a new project, use a workspace-relative path/);
  assert.deepEqual(decisions.map(decision => decision?.block === true),
    [true, false, false, false, false, false, false, false, false, true, true]);
  assert.equal(decisions[9].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.equal(decisions[10].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  // Only #4, #5, #7 and #9 reached execution control; the deletion never did.
  assert.deepEqual(prepared, byIndex(4, 5, 7, 9).map(call => call.arguments.command));
  assert.deepEqual(signalled, [context.runId]);
  assert.deepEqual(aborted, [context.sessionId]);
  assert.deepEqual(guard.verificationForRun(context.runId), {status: 'passed', text: RECEIPT});
  assert.deepEqual(await run.delivered(), {status: 'passed', text: RECEIPT});
  // The aborted run's empty final, or any model text, is replaced.
  for (const final of ['', 'I wrote the script and tested it; the manual folder was cleaned up.']) {
    assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: final}}).payload.text, RECEIPT);
  }
  assert.doesNotMatch(RECEIPT, /Do not retry|wait for a new owner instruction|nothing was deleted/i);
  // The latch, finalization and continuation behavior are unchanged.
  assert.equal(guard.beforeAgentFinalize({}, context), undefined);
  assert.equal(guard.continuationAllowed(context.runId), false);
  assert.deepEqual(await run.hooks.before_tool_call({toolName: 'read', params: {path: `${PROJECT}/test_rename.py`}},
    {...context, toolName: 'read', toolCallId: 'later-read'}),
  {block: true, blockReason: RECURSIVE_DELETE_REQUIRES_OWNER_REASON});
  assert.deepEqual(signalled, [context.runId]);
  assert.deepEqual(aborted, [context.sessionId]);
});

test('tower2 replay: the refused cleanup alone, before any later call, gives the same receipt', async t => {
  const run = await replay(t, CALLS.slice(0, 10));
  assert.equal(run.decisions[9].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(run.signalled, []);
  assert.deepEqual(run.aborted, []);
  assert.deepEqual(await run.delivered(), {status: 'passed', text: RECEIPT});
  // A text-only answer after the refusal is still not accepted.
  assert.equal(run.guard.replyPayloadSending({runId: run.context.runId, kind: 'final',
    payload: {text: 'Done! I removed the temporary folder.'}}).payload.text, RECEIPT);
});

test('tower2 replay: a later write makes the pass stale, so the refusal stays failed', async t => {
  const write = {id: 'extra-write', tool: 'write', arguments: {path: `${PROJECT}/README.md`, content: '# Photo renamer\n'},
    result: {isError: false, text: `Successfully wrote 16 bytes to ${PROJECT}/README.md`}};
  const run = await replay(t, [...CALLS.slice(0, 9), write, CALLS[9]]);
  assert.deepEqual(await run.delivered(), {status: 'failed', text: LEAD +
    '- File written: `/workspace/Playground/photo-renamer/README.md`.\n' + FILES + `- ${TEST}${STALE}` + INCOMPLETE});
});

test('tower2 replay: a refusal right after the failing test stays failed', async t => {
  const run = await replay(t, [...CALLS.slice(0, 5), CALLS[9]]);
  assert.equal(run.decisions[5].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await run.delivered(), {status: 'failed', text: LEAD + FILES + `- ${TEST} failed.\n` + INCOMPLETE});
});

test('tower2 replay: a refusal before any test ran stays failed with an owner receipt', async t => {
  const run = await replay(t, [...CALLS.slice(0, 4), CALLS[9]]);
  const receipt = LEAD + FILES + '- No recognized test command ran.\n' + INCOMPLETE;
  assert.deepEqual(await run.delivered(), {status: 'failed', text: receipt});
  assert.equal(run.guard.replyPayloadSending({runId: run.context.runId, kind: 'final',
    payload: {text: 'All tests pass.'}}).payload.text, receipt);
});

test('tower2 replay: a refused rerun of the test is not a test result', async t => {
  // Every call after the refusal is refused and runs nothing. Its blocked
  // receipt must not turn the real pass into a failure.
  const rerun = exec('refused-rerun', `cd /workspace/${PROJECT} && python3 test_rename.py`);
  const run = await replay(t, [...CALLS.slice(0, 10), rerun]);
  assert.equal(run.decisions[10].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await run.delivered(), {status: 'passed', text: RECEIPT});
  // The same holds when the refused deletion itself carries a test command.
  const combined = exec('refused-combined', 'rm -rf /workspace/test_manual && python3 -m unittest -v', `/workspace/${PROJECT}`);
  const chained = await replay(t, [...CALLS.slice(0, 9), combined]);
  assert.equal(chained.decisions[9].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await chained.delivered(), {status: 'passed', text: RECEIPT});
});

test('owner-authorized recursive deletion is unchanged', async t => {
  const run = session(t, {prompt: 'Delete the directory /workspace/test_manual recursively.'});
  const decision = await run.call('exec', {command: 'rm -rf /workspace/test_manual', workdir: '/workspace'}, done(0, ''));
  assert.notEqual(decision?.block, true, decision?.blockReason);
  assert.doesNotMatch(JSON.stringify(await run.delivered()), /recursive deletion/);
});

// Review of #6753, probes A and B: a background test is current only as of
// when it started, and a background command that ends after a pass could have
// changed the workspace until then.
test('a background test pass is dated from its start, and a background command ending later makes it stale', async t => {
  const params = {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`};
  // Control: nothing ran while the test ran, then a read-only poll.
  const clean = session(t, {prompt: RECORDED.prompt});
  await writeProject(clean);
  await clean.call('exec', params, running('s1'));
  assert.equal(clean.guard.verificationStatus(clean.context.runId), 'pending');
  await clean.call('process', {action: 'poll', sessionId: 's1'}, polled('s1', 0, UNITTEST_OK));
  await refuse(clean);
  assert.deepEqual(await clean.delivered(), {status: 'passed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + COMPLETE});
  // A: the module is rewritten while the test still runs.
  const rewritten = session(t, {prompt: RECORDED.prompt});
  await writeProject(rewritten);
  await rewritten.call('exec', params, running('s1'));
  await rewritten.call('write', {path: `${PROJECT}/rename_photos.py`, content: 'def rename(p):\n    raise SystemExit(1)\n'},
    text('Successfully wrote 40 bytes'));
  await rewritten.call('process', {action: 'poll', sessionId: 's1'}, polled('s1', 0, UNITTEST_OK));
  assert.equal(rewritten.guard.verificationStatus(rewritten.context.runId), 'passed');
  await refuse(rewritten);
  assert.deepEqual(await rewritten.delivered(), {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${STALE}` + INCOMPLETE});
  // B: a generator started before the test ends after the pass.
  const generator = session(t, {prompt: RECORDED.prompt});
  await writeProject(generator);
  await generator.call('exec', {command: 'python3 make_fixture_photos.py', workdir: `/workspace/${PROJECT}`}, running('s2'));
  await generator.call('exec', params, done(0, UNITTEST_OK));
  await generator.call('process', {action: 'poll', sessionId: 's2'}, polled('s2', 0, 'wrote 40 fixtures'));
  await refuse(generator);
  assert.deepEqual(await generator.delivered(), {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${STALE}` + INCOMPLETE});
});

// Re-review, probes N01-N03: a background command that ran while the test
// ran, or ended after it, could have changed what the test saw.
const OVERLAPPED = ' passed, but another tool call or command ran at the same time, so that result is not current.\n';
test('a background command that ran while the passing test ran keeps the refusal failed', async t => {
  const params = {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`};
  const generator = {command: 'python3 make_fixture_photos.py', workdir: `/workspace/${PROJECT}`};
  const cases = {
    // N01: started before the background test and ended before it.
    'ends first': async run => {
      await run.call('exec', generator, running('c1'));
      await run.call('exec', params, running('t1'));
      await run.call('process', {action: 'poll', sessionId: 'c1'}, polled('c1', 0, 'overwrote rename_photos.py'));
      await run.call('process', {action: 'poll', sessionId: 't1'}, polled('t1', 0, UNITTEST_OK));
    },
    // Started before a foreground test and observed to end after it.
    'foreground test, ends after': async run => {
      await run.call('exec', generator, running('c1'));
      await run.call('exec', params, done(0, UNITTEST_OK));
      await run.call('process', {action: 'poll', sessionId: 'c1'}, polled('c1', 0, 'wrote 40 fixtures'));
    },
    // Started while a background test ran, then both ended.
    'started during a background test': async run => {
      await run.call('exec', params, running('t1'));
      await run.call('exec', generator, running('c1'));
      await run.call('process', {action: 'poll', sessionId: 'c1'}, polled('c1', 0, 'wrote 40 fixtures'));
      await run.call('process', {action: 'poll', sessionId: 't1'}, polled('t1', 0, UNITTEST_OK));
    },
  };
  for (const [name, steps] of Object.entries(cases)) {
    const run = session(t, {prompt: RECORDED.prompt});
    await writeProject(run);
    await steps(run);
    assert.equal(run.guard.verificationStatus(run.context.runId), 'passed', name);
    await refuse(run, 'rm -rf /tmp/x');
    const verification = await run.delivered();
    assert.equal(verification.status, 'failed', name);
    assert.doesNotMatch(verification.text, /still running when tool use stopped/, name);
    assert.ok(verification.text.endsWith(INCOMPLETE), name);
  }
  // The receipt says why for a pass that is otherwise current.
  const first = session(t, {prompt: RECORDED.prompt});
  await writeProject(first);
  await cases['ends first'](first);
  await refuse(first, 'rm -rf /tmp/x');
  assert.deepEqual(await first.delivered(), {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${OVERLAPPED}` + INCOMPLETE});
  // Control: a background command that ended before the test started.
  const before = session(t, {prompt: RECORDED.prompt});
  await writeProject(before);
  await before.call('exec', generator, running('c1'));
  await before.call('process', {action: 'poll', sessionId: 'c1'}, polled('c1', 0, 'wrote 40 fixtures'));
  await before.call('exec', params, done(0, UNITTEST_OK));
  await refuse(before, 'rm -rf /tmp/x');
  assert.deepEqual(await before.delivered(), {status: 'passed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + COMPLETE});
  // N02 and N03: ended or was killed after the pass.
  for (const last of [
    run => run.call('process', {action: 'poll', sessionId: 'c1'}, polled('c1', 0, 'overwrote rename_photos.py')),
    run => run.call('process', {action: 'kill', sessionId: 'c1'}, text('Killed session c1')),
  ]) {
    const run = session(t, {prompt: RECORDED.prompt});
    await writeProject(run);
    await run.call('exec', generator, running('c1'));
    await run.call('exec', params, running('t1'));
    await run.call('process', {action: 'poll', sessionId: 't1'}, polled('t1', 0, UNITTEST_OK));
    await last(run);
    await refuse(run, 'rm -rf /tmp/x');
    assert.equal((await run.delivered()).status, 'failed');
  }
});

// Sibling calls from one model response run at the same time: OpenClaw runs
// every before_tool_call, then the calls, then each after_tool_call and
// tool_result_persist. A test that ran beside a write may not have seen it,
// whichever receipt arrives first.
async function siblings(run, calls) {
  const {guard, hooks, context} = run;
  const modelCall = `${context.runId}:model:siblings-${calls.length}-${calls[0][0]}`;
  guard.observeModelCall({callId: modelCall}, context);
  guard.observeModelEnd({callId: modelCall, outcome: 'completed'}, context);
  const admitted = [];
  for (const [index, [tool, params, result]] of calls.entries()) {
    const id = `${modelCall}:${index}`, ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, toolCallId: id, runId: context.runId, params: structuredClone(params)};
    admitted.push({id, ctx, tool, event, result, decision: await hooks.before_tool_call(event, ctx)});
  }
  for (const {id, ctx, tool, event, result, decision} of admitted) {
    const outcome = decision?.block
      ? {isError: true, content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: decision.blockReason}}
      : result;
    hooks.after_tool_call({toolName: tool, toolCallId: id, runId: context.runId,
      params: decision?.block ? event.params : {...event.params, ...decision?.params}, result: outcome,
      ...(outcome.isError ? {error: outcome.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolName: tool, toolCallId: id, message: {role: 'toolResult', toolName: tool,
      toolCallId: id, isError: outcome.isError === true, ...structuredClone(outcome)}}, ctx);
  }
  return admitted.map(({decision}) => decision);
}

test('a test that ran beside a sibling call that could change the workspace keeps the refusal failed', async t => {
  const params = {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`};
  const rewrite = ['write', {path: `${PROJECT}/rename_photos.py`, content: 'def rename(p):\n    raise SystemExit(1)\n'},
    text('Successfully wrote 40 bytes')];
  const outcome = async (steps) => {
    const run = session(t, {prompt: RECORDED.prompt});
    await writeProject(run);
    await steps(run);
    assert.equal(run.guard.verificationStatus(run.context.runId), 'passed');
    await refuse(run, 'rm -rf /tmp/x');
    return run.delivered();
  };
  // The write's receipt arrives first, so no call was admitted after the pass.
  assert.deepEqual(await outcome(run => siblings(run, [rewrite, ['exec', params, done(0, UNITTEST_OK)]])),
    {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${OVERLAPPED}` + INCOMPLETE});
  // The test's receipt arrives first.
  assert.equal((await outcome(run => siblings(run, [['exec', params, done(0, UNITTEST_OK)], rewrite]))).status, 'failed');
  // A background test started beside the write, then polled.
  assert.deepEqual(await outcome(async run => {
    await siblings(run, [rewrite, ['exec', params, running('t1')]]);
    await run.call('process', {action: 'poll', sessionId: 't1'}, polled('t1', 0, UNITTEST_OK));
  }), {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${OVERLAPPED}` + INCOMPLETE});
  // Controls: a read-only sibling changes nothing, and a sibling this guard
  // refused ran nothing.
  const current = {status: 'passed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + COMPLETE};
  assert.deepEqual(await outcome(run => siblings(run, [['read', {path: `${PROJECT}/rename_photos.py`},
    text('def rename(p):\n    return p\n')], ['exec', params, done(0, UNITTEST_OK)]])), current);
  const refusedSibling = session(t, {prompt: RECORDED.prompt});
  await writeProject(refusedSibling);
  const decisions = await siblings(refusedSibling, [['exec', params, done(0, UNITTEST_OK)], ['exec', {command: 'rm -rf /tmp/x'}, done(0, '')]]);
  assert.equal(decisions[1]?.blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await refusedSibling.delivered(), current);
  // A Tool Search call's nested child runs inside it and is not a sibling.
  assert.deepEqual(await outcome(async run => {
    const {guard, hooks, context} = run;
    const parent = 'wrapped-test', child = `tool_search_code:${parent}:exec:1`;
    const envelope = {tool: {id: 'openclaw:core:exec', name: 'exec', source: 'openclaw', sourceName: 'core'},
      result: done(0, UNITTEST_OK)};
    const wrapped = {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope};
    guard.observeModelCall({callId: 'wrapped-model'}, context);
    guard.observeModelEnd({callId: 'wrapped-model', outcome: 'completed'}, context);
    const outer = {...context, toolName: 'tool_call', toolCallId: parent};
    const outerParams = {id: 'openclaw:core:exec', args: structuredClone(params)};
    const admittedOuter = {...outerParams, ...(await hooks.before_tool_call(
      {toolName: 'tool_call', toolCallId: parent, runId: context.runId, params: outerParams}, outer))?.params};
    const inner = {...context, toolName: 'exec', toolCallId: child};
    const admittedInner = {...admittedOuter.args, ...(await hooks.before_tool_call(
      {toolName: 'exec', toolCallId: child, runId: context.runId, params: structuredClone(admittedOuter.args)}, inner))?.params};
    hooks.after_tool_call({toolName: 'exec', toolCallId: child, runId: context.runId, params: admittedInner,
      result: done(0, UNITTEST_OK)}, inner);
    hooks.after_tool_call({toolName: 'tool_call', toolCallId: parent, runId: context.runId, params: admittedOuter,
      result: wrapped}, outer);
    guard.toolResultPersist({toolName: 'tool_call', toolCallId: parent, message: {role: 'toolResult',
      toolName: 'tool_call', toolCallId: parent, isError: false, ...structuredClone(wrapped)}}, outer);
  }), current);
});

test('a still-running test keeps the refusal failed', async t => {
  const run = session(t, {prompt: RECORDED.prompt});
  await writeProject(run);
  await run.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, running('tests-1'));
  assert.equal(run.guard.verificationStatus(run.context.runId), 'pending');
  await refuse(run, 'rm -rf /workspace/tmp');
  assert.deepEqual(await run.delivered(), {status: 'failed', text: LEAD + FILES +
    `- ${UNITTEST} had not finished, so its result is unknown.\n` +
    '- A command started earlier was still running when tool use stopped.\n' + INCOMPLETE});
});

// Probes C and D: a pass keeps the run only after this run changed files for
// an owner who asked for a change and for no written answer.
test('a pass without a change, or for a question, does not complete the run', async t => {
  const baseline = session(t, {prompt: 'Fix the bug in Playground/photo-renamer/rename_photos.py so PNG files keep their EXIF date, then run the tests.'});
  await baseline.call('read', {path: `${PROJECT}/rename_photos.py`}, text('def rename(p):\n    return p\n'));
  await baseline.call('exec', {command: `cd /workspace/${PROJECT} && python3 -m unittest -v`}, done(0, UNITTEST_OK));
  // A top-level target: open #6722 guides a first deletion inside a project
  // (such as its __pycache__) instead of stopping the turn.
  await refuse(baseline, 'rm -rf /workspace/test_manual');
  assert.deepEqual(await baseline.delivered(), {status: 'failed', text: LEAD + NO_FILES + `- ${UNITTEST}${CURRENT}` + INCOMPLETE});
  for (const prompt of ['What does Playground/photo-renamer/rename_photos.py do? Explain it briefly.',
    'Write a test for Playground/photo-renamer/rename_photos.py and tell me what it covers.']) {
    const question = session(t, {prompt});
    await question.call('read', {path: `${PROJECT}/rename_photos.py`}, text('def rename(p):\n    return p\n'));
    await question.call('write', {path: `${PROJECT}/test_probe.py`, content: 'import unittest\n'}, text('Successfully wrote 16 bytes'));
    await question.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
    await refuse(question, 'rm -rf /tmp/x');
    assert.deepEqual(await question.delivered(), {status: 'failed', text: LEAD +
      '- File written: `/workspace/Playground/photo-renamer/test_probe.py`.\n' + `- ${UNITTEST}${CURRENT}` + INCOMPLETE}, prompt);
  }
  // A polite request is still a request.
  const polite = session(t, {prompt: 'Can you write a python script that renames all photos in a folder by date taken and test it?'});
  await writeProject(polite);
  await polite.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
  await refuse(polite);
  assert.equal((await polite.delivered()).status, 'passed');
});

// Re-review, probes N20-N27: after a refusal no model text reaches the owner,
// so any ask for something to be said in the reply keeps the run failed, with
// the same honest receipt.
test('a request that asks for anything in the reply keeps the refusal failed', async t => {
  const RECEIPT_FAILED = LEAD + FILES + `- ${UNITTEST}${CURRENT}` + INCOMPLETE;
  const outcome = async prompt => {
    const run = session(t, {prompt});
    await writeProject(run);
    await run.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
    await refuse(run, 'rm -rf /tmp/x');
    const verification = await run.delivered();
    // No model text is accepted in place of the receipt.
    assert.equal(run.guard.replyPayloadSending({runId: run.context.runId, kind: 'final',
      payload: {text: 'It renames photos by EXIF date; run it with python3 rename_photos.py <folder>.'}}).payload.text,
    verification.text, prompt);
    return verification;
  };
  // The reviewer's phrasings.
  for (const prompt of [
    'Write a python script that renames all photos in a folder by date taken, test it, and provide a brief explanation of how it works.',
    'Write a python script that renames all photos in a folder by date taken and test it. Give me a short summary of what it does.',
    'Write a python script that renames all photos in a folder by date taken, test it, and report which photos it renamed.',
    'Write a python script that renames all photos in a folder by date taken, test it, and let me know the results.',
    'Write a python script that renames all photos in a folder by date taken, test it, and include usage instructions in your reply.',
    'Write a python script that renames all photos in a folder by date taken, test it, and give an overview of the design.',
    'Write a python script that renames all photos in a folder by date taken, test it, and tell me how to run it.',
    'Build a photo renamer that sorts by date taken. Test it and describe the edge cases you handled.',
  ]) {
    assert.deepEqual(await outcome(prompt), {status: 'failed', text: RECEIPT_FAILED}, prompt);
  }
  // Each reply-content ask on its own, after the same change request.
  const ask = 'Write a python script that renames all photos in a folder by date taken and test it';
  for (const tail of [
    'then explain the approach', 'with an explanation', 'and a summary', 'then summarize it', 'then summarise it',
    'and give an overview', 'then describe it', 'with a report', 'and share the results', 'and let me know',
    'and tell me when done', 'and include the output', 'with usage notes', 'with instructions', 'and say how it works',
    'and say how to run it', 'then document it', 'with a walkthrough', 'with a walk-through', 'and walk me through it',
    'and show me the output', 'and answer in two lines', 'and reply with the file list',
  ]) {
    assert.equal((await outcome(`${ask} ${tail}.`)).status, 'failed', tail);
  }
  // Controls: a change request that asks for nothing in the reply.
  for (const prompt of [RECORDED.prompt, `${ask}.`, 'Please write a python script that renames photos by date taken, then test it.']) {
    assert.equal((await outcome(prompt)).status, 'passed', prompt);
  }
});

// Probe Q: research answers and citations are model text, which a refusal
// never delivers.
test('research requests and runs that used web results stay failed', async t => {
  for (const [prompt, web] of [
    ['Search the web for which EXIF tag stores the date a photo was taken and write a python script that prints it for a file, with a test.', false],
    [RECORDED.prompt, true],
    ['Research which EXIF tag stores the date a photo was taken, cite your sources, and write a python script that prints it for a file, with a test.', false],
  ]) {
    const run = session(t, {prompt});
    if (web) await run.call('web_search', {query: 'EXIF DateTimeOriginal tag'}, text('DateTimeOriginal (0x9003) https://exiftool.org/TagNames/EXIF.html'));
    await writeProject(run);
    await run.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
    await refuse(run, 'rm -rf /tmp/x');
    assert.deepEqual(await run.delivered(), {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + INCOMPLETE}, prompt);
  }
});

// Probes E and F: a run that skipped every test proves nothing.
test('a pass that skipped every test keeps the refusal failed', async t => {
  for (const [command, output] of [
    ['python3 -m unittest -v', "test_a (test_rename.T.test_a) ... skipped 'todo'\n\nRan 1 test in 0.000s\n\nOK (skipped=1)"],
    ['pytest -q', 's                                                                        [100%]\n1 skipped in 0.01s'],
    ['pytest', '============================== 2 skipped, 1 warning in 0.02s ==============================='],
  ]) {
    const run = session(t, {prompt: RECORDED.prompt});
    await writeProject(run);
    await run.call('exec', {command, workdir: `/workspace/${PROJECT}`}, done(0, output));
    assert.equal(run.guard.verificationStatus(run.context.runId), 'passed');
    await refuse(run, 'rm -rf /tmp/x');
    assert.deepEqual(await run.delivered(), {status: 'failed', text: LEAD + FILES +
      `- The latest recognized test command, \`${command}\` in \`/workspace/Playground/photo-renamer\`, exited successfully, ` +
      'but no test it reported passed: each was skipped, not run or an expected failure.\n' + INCOMPLETE}, command);
  }
  // Some tests ran: the pass counts.
  const partial = session(t, {prompt: RECORDED.prompt});
  await writeProject(partial);
  await partial.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`},
    done(0, 'Ran 2 tests in 0.001s\n\nOK (skipped=1)'));
  await refuse(partial, 'rm -rf /tmp/x');
  assert.equal((await partial.delivered()).status, 'passed');
});

// Re-review, probes N04-N06: colored summaries (a pty exec) and runs where
// nothing passed although nothing was skipped.
test('a pass with no passing test keeps the refusal failed, colored or not', async t => {
  const RULE = '-'.repeat(70);
  const outcome = async (command, output) => {
    const run = session(t, {prompt: RECORDED.prompt});
    await writeProject(run);
    await run.call('exec', {command, workdir: `/workspace/${PROJECT}`}, done(0, output));
    assert.equal(run.guard.verificationStatus(run.context.runId), 'passed', output);
    await refuse(run, 'rm -rf /tmp/x');
    return run.delivered();
  };
  for (const [command, output] of [
    // Python 3.14 on a pty.
    ['python3 -m unittest -v', "test_a (test_rename.T.test_a) ... skipped 'todo'\r\n\r\n" + `${RULE}\r\nRan 1 test in 0.000s\r\n\r\n` +
      '\x1b[32mOK\x1b[0m (\x1b[33mskipped=1\x1b[0m)\r\n'],
    // pytest, colored and plain.
    ['pytest -q', 's \x1b[32m[100%]\x1b[0m\n\x1b[33m1 skipped\x1b[0m\x1b[33m in 0.01s\x1b[0m'],
    ['pytest -q', 'xx [100%]\n2 xfailed in 0.02s'],
    ['pytest -q', '\x1b[33m1 skipped\x1b[0m, \x1b[33m1 xfailed\x1b[0m, \x1b[33m1 xpassed\x1b[0m\x1b[33m in 0.03s\x1b[0m'],
    ['pytest', '============================== 3 deselected in 0.02s ==============================='],
    // Jest and node:test.
    ['npm test', 'Tests:       2 todo, 2 total\nTime:        0.5 s'],
    ['npm test', 'ℹ tests 1\nℹ pass 0\nℹ fail 0\nℹ todo 1'],
  ]) {
    const verification = await outcome(command, output);
    assert.equal(verification.status, 'failed', output);
    assert.match(verification.text, /exited successfully, but no test it reported passed: each was skipped, not run or an expected failure\./, output);
  }
  // Something passed.
  for (const [command, output] of [
    ['pytest -q', '\x1b[32m1 passed\x1b[0m, \x1b[33m1 skipped\x1b[0m\x1b[32m in 0.02s\x1b[0m'],
    ['pytest -q', '1 passed, 2 xfailed in 0.02s'],
    ['python3 -m unittest -v', `${RULE}\r\nRan 3 tests in 0.000s\r\n\r\n\x1b[32mOK\x1b[0m (\x1b[33mskipped=1\x1b[0m)\r\n`],
  ]) {
    assert.equal((await outcome(command, output)).status, 'passed', output);
  }
});

// A test run can name another directory than the one it runs in.
test('a pass counts only for a test that stays in a directory with this run\'s changes', async t => {
  const outcome = async (params, file = undefined) => {
    const run = session(t, {prompt: file ? `Fix the bug in ${file} and run the tests.` : RECORDED.prompt});
    if (file) {
      await run.call('read', {path: file}, text('def rename(p):\n    return p\n'));
      const edit = await run.call('edit', {path: file, edits: [{oldText: 'return p', newText: 'return str(p)'}]},
        text('Successfully replaced 1 block(s)'));
      assert.notEqual(edit?.block, true, edit?.blockReason);
    } else {
      await writeProject(run);
    }
    await run.call('exec', params, done(0, 'Ran 4 tests in 0.01s\n\nOK'));
    await refuse(run, 'rm -rf /tmp/x');
    return (await run.delivered()).status;
  };
  const inProject = `/workspace/${PROJECT}`;
  for (const command of [
    'python3 -m unittest discover -s ../old-project',
    'python3 -m unittest discover --start-directory=../old-project',
    'python3 -m unittest discover -s /workspace/Playground/old-project',
    'python3 -m unittest discover -s ~/tests',
    'python3 -m unittest discover -s tests -t ..',
  ]) {
    assert.equal(await outcome({command, workdir: inProject}), 'failed', command);
  }
  // Inside the project, relative or absolute.
  for (const command of ['python3 -m unittest discover -s tests', `python3 -m unittest discover -s ${inProject}/tests`]) {
    assert.equal(await outcome({command, workdir: inProject}), 'passed', command);
  }
  // The workspace root contains only its own top-level files.
  assert.equal(await outcome({command: 'python3 -m unittest -v', workdir: '/workspace'}, 'rename_photos.py'), 'passed');
  assert.equal(await outcome({command: 'python3 -m unittest -v', workdir: '/workspace'}, `${PROJECT}/rename_photos.py`), 'failed');
  // So does the Playground folder, which holds the other projects: the first
  // command runs old-project's tests only. A run from there that names this
  // project is refused the same way (a false failure, never a false pass).
  assert.equal(await outcome({command: 'cd /workspace/Playground && python3 -m pytest old-project'}), 'failed');
  assert.equal(await outcome({command: `cd /workspace/Playground && python3 -m pytest ${PROJECT.split('/')[1]}`}), 'failed');
});

// Probe G: edit and apply_patch changes are listed; probe H: a pass in a
// directory without this run's changes does not cover them.
test('the receipt lists edited and patched files, and the pass must cover a changed file', async t => {
  const MODULE = 'def rename(p):\n    return p\n';
  const edited = session(t, {prompt: 'Fix the bug in Playground/photo-renamer/rename_photos.py and run the tests.'});
  await edited.call('read', {path: `${PROJECT}/rename_photos.py`}, text(MODULE));
  const edit = await edited.call('edit', {path: `${PROJECT}/rename_photos.py`, edits: [{oldText: 'return p', newText: 'return str(p)'}]},
    text('Successfully replaced 1 block(s)'));
  assert.notEqual(edit?.block, true, edit?.blockReason);
  await edited.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
  await refuse(edited, 'rm -rf /tmp/x');
  assert.deepEqual(await edited.delivered(), {status: 'passed', text: LEAD +
    '- File changed: `/workspace/Playground/photo-renamer/rename_photos.py`.\n' + `- ${UNITTEST}${CURRENT}` + COMPLETE});

  const patched = session(t, {prompt: 'Fix the bug in Playground/photo-renamer/rename_photos.py and run the tests.'});
  await patched.call('read', {path: `${PROJECT}/rename_photos.py`}, text(MODULE));
  const patch = await patched.call('apply_patch', {input: `*** Begin Patch\n*** Update File: ${PROJECT}/rename_photos.py\n@@\n-    return p\n+    return str(p)\n*** End Patch`},
    text('Success. Updated the following files:\nM Playground/photo-renamer/rename_photos.py'));
  assert.notEqual(patch?.block, true, patch?.blockReason);
  await patched.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
  await refuse(patched, 'rm -rf /tmp/x');
  assert.deepEqual(await patched.delivered(), {status: 'passed', text: LEAD +
    '- File changed: `/workspace/Playground/photo-renamer/rename_photos.py`.\n' + `- ${UNITTEST}${CURRENT}` + COMPLETE});

  const elsewhere = session(t, {prompt: RECORDED.prompt});
  await writeProject(elsewhere);
  await elsewhere.call('exec', {command: 'cd /workspace/Playground/old-project && python3 -m unittest'}, done(0, 'Ran 4 tests in 0.01s\n\nOK'));
  await refuse(elsewhere, 'rm -rf /tmp/x');
  assert.deepEqual(await elsewhere.delivered(), {status: 'failed', text: LEAD + FILES +
    '- The latest recognized test command, `python3 -m unittest` in `/workspace/Playground/old-project`,' + CURRENT + INCOMPLETE});
});

// Probe S: a leading `cd /workspace/...` is where the test ran, whatever
// workdir the call named.
test('the receipt names the directory a leading cd chose over the workdir', async t => {
  const run = session(t, {prompt: RECORDED.prompt});
  await writeProject(run);
  await run.call('exec', {command: `cd /workspace/${PROJECT} && python3 -m unittest -v`, workdir: '/workspace/Playground/other'},
    done(0, UNITTEST_OK));
  await refuse(run, 'rm -rf /tmp/x');
  assert.deepEqual(await run.delivered(), {status: 'passed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + COMPLETE});
});

test('receipt-based and team work stay failed after a deletion refusal', async t => {
  const operations = session(t, {prompt: 'Check the ODS host hostname.'});
  await operations.call('pixel_ods_host_observe', {actions: ['host.identity']}, {details: {jobId: 'ops-1234567890123-abcdef123456',
    status: 'succeeded', waitTimedOut: false, steps: [{stepId: 'observe-1', target: 'ods-host', action: 'host.identity',
      exitCode: 0, stdout: 'test-host\n', stderr: '', outputTruncated: {stdout: false, stderr: false}, riskSignals: []}]}});
  assert.equal(operations.guard.verificationForRun(operations.context.runId).status, 'passed');
  await operations.call('exec', {command: 'python3 -m unittest -v'}, done(0, 'Ran 1 test in 0.001s\n\nOK'));
  assert.equal(operations.guard.verificationStatus(operations.context.runId), 'passed');
  await refuse(operations, 'rm -rf /workspace/scratch');
  assert.deepEqual(await operations.delivered(), {status: 'failed', text: LEAD + NO_FILES +
    '- The latest recognized test command, `python3 -m unittest -v` in `/workspace`,' + CURRENT + INCOMPLETE});
  // A team builder's outcome drives the team's plan, not the owner's reply.
  const builder = session(t, {prompt: `You are the Builder in the owner's Portal team. ${RECORDED.prompt}`});
  await writeProject(builder);
  await builder.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
  await refuse(builder, 'rm -rf /tmp/x');
  assert.deepEqual(await builder.delivered(), {status: 'failed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + INCOMPLETE});
});

// The latch's refusals count as failures; after four in a row the budget is
// exhausted. The deletion refusal came first and remains the reported cause.
test('a progress stop caused by the latch keeps the deletion receipt', async t => {
  const run = session(t, {prompt: RECORDED.prompt});
  await writeProject(run);
  await run.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
  await refuse(run, 'rm -rf /tmp/x');
  const reasons = [];
  for (let index = 0; index < 4; index++) {
    reasons.push((await run.call('exec', {command: `ls /workspace/${PROJECT}`}, done(0, ''))).blockReason);
  }
  assert.deepEqual(reasons, [RECURSIVE_DELETE_REQUIRES_OWNER_REASON, RECURSIVE_DELETE_REQUIRES_OWNER_REASON,
    RECURSIVE_DELETE_REQUIRES_OWNER_REASON, RUN_PROGRESS_STOP_REASON]);
  // As on main: the latch signals and aborts once, and the progress stop
  // once more at the next model end.
  assert.deepEqual(run.signalled, [run.context.runId, run.context.runId]);
  assert.deepEqual(run.aborted, [run.context.sessionId, run.context.sessionId]);
  assert.deepEqual(await run.delivered(), {status: 'passed', text: LEAD + FILES + `- ${UNITTEST}${CURRENT}` + COMPLETE});
});

// Probe R: path depth is unbounded even though each component is bounded.
test('the receipt stays bounded and never quotes model text as markup', async t => {
  const deep = Array.from({length: 30}, (_, index) => `d${index}`.padEnd(120, 'x')).join('/');
  const run = session(t, {prompt: RECORDED.prompt});
  await writeProject(run);
  for (let index = 0; index < 23; index++) {
    const decision = await run.call('write', {path: `${PROJECT}/${deep}/f${String(index).padStart(2, '0')}.py`, content: 'x\n'},
      text('Successfully wrote 2 bytes'));
    assert.notEqual(decision?.block, true, decision?.blockReason);
  }
  await run.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`}, done(0, UNITTEST_OK));
  await refuse(run, 'rm -rf /tmp/x');
  const verification = await run.delivered();
  assert.equal(verification.status, 'passed');
  const lines = verification.text.match(/^- File written: [^\n]*$/gm);
  assert.equal(lines.length, 20);
  assert.match(verification.text, /\n- 5 additional files were written\.\n/);
  for (const line of lines) {
    assert.ok(line.length <= 220, line);
    assert.ok(line.startsWith('- File written: `/workspace/Playground/photo-renamer/d0') && line.includes(ELLIPSIS), line);
    assert.match(line, /xxx\/f[0-9]{2}\.py`\.$/);
  }
  assert.ok(verification.text.length < 8 * 1024, String(verification.text.length));

  // A model-chosen directory with a backtick, spaces and length is bounded
  // inline code. It holds none of this run's changes, so the run stays failed.
  const hostile = session(t, {prompt: RECORDED.prompt});
  await writeProject(hostile);
  const long = `cd "/workspace/${PROJECT}/a\`b ${'d'.repeat(300)}" && python3 test_rename.py`;
  await hostile.call('exec', {command: long}, done(0, 'ok'));
  await refuse(hostile, 'rm -rf /workspace/tmp');
  const {status, text: receipt} = await hostile.delivered();
  assert.equal(status, 'failed');
  const test = receipt.match(/^- The latest recognized test command, `python3 test_rename\.py` in `([^`\n]*)`, passed/m);
  assert.ok(test, receipt);
  assert.ok(test[1].startsWith(`/workspace/${PROJECT}/a b ddd`));
  assert.ok(test[1].length <= 160 && test[1].endsWith(ELLIPSIS));
  assert.doesNotMatch(receipt, /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/);
});

// tower3 open-prompt 07 (Qwen3.5-27B, main d4a61f33, session 047d3145): the
// unittest runner reported "Ran 11 tests" and OK with exit 0, then the program
// under a negative test printed "Error: Folder ... does not exist.". That
// line was counted as a unittest failure and the owner got the
// failed-verification text instead of the model's answer.
test('tower3 replay: a line the tested program printed after a clean unittest verdict is not a failure', async t => {
  assert.equal(TOWER3.sessionId, '047d3145-2472-4aef-a626-f43b6e712f6b');
  assert.match(TOWER3.evidence, /sha256 e861b9f0263626593ff7e3966f81d95a901976fdc74e3cc3dfa2f07a70d228f3/);
  assert.equal(TOWER3.prompt, RECORDED.prompt);
  assert.deepEqual(TOWER3.calls.map(call => call.tool), ['write', 'write', 'exec', 'edit', 'exec', 'exec', 'exec', 'exec']);
  const passing = TOWER3.calls[4];
  assert.equal(passing.arguments.command, 'cd /workspace/Playground/photo-renamer && python3 -m unittest test_photo_renamer -v');
  assert.equal(passing.result.details.exitCode, 0);
  assert.match(passing.result.details.aggregated, /\nRan 11 tests in [0-9.]+s\n\nOK\n/);
  assert.match(passing.result.details.aggregated, /\nError: Folder '\/nonexistent\/folder\/path' does not exist\.$/);
  assert.equal(TOWER3.delivered.productOutcome, 'failed');
  assert.match(TOWER3.delivered.text, /latest verification check failed/);
  const run = await replay(t, TOWER3.calls, {prompt: TOWER3.submittedUserText, runId: TOWER3.runId, sessionId: TOWER3.sessionId});
  assert.deepEqual(run.decisions.map(decision => decision?.block === true), Array(8).fill(false));
  assert.equal(run.guard.verificationStatus(run.context.runId), 'passed');
  assert.equal(run.guard.beforeAgentFinalize({lastAssistantMessage: TOWER3.finalAssistant.text}, run.context, 'pixel'), undefined);
  const verification = await run.delivered({outcome: 'completed'});
  assert.equal(verification.status, 'passed');
  assert.equal(verification.text, undefined);
  // The model's own answer is delivered unchanged.
  assert.equal(run.guard.replyPayloadSending({runId: run.context.runId, kind: 'final',
    payload: {text: TOWER3.finalAssistant.text}}), undefined);

  // Without unittest's verdict, an ad-hoc test script's Error or FAIL line
  // still fails an exit-zero run, in any case.
  for (const output of ['Error: negative numbers were accepted', 'fail: rename kept the old name\nDone']) {
    const adhoc = session(t, {prompt: RECORDED.prompt});
    await writeProject(adhoc);
    await adhoc.call('exec', {command: 'python3 test_rename.py', workdir: `/workspace/${PROJECT}`}, done(0, output));
    assert.equal(adhoc.guard.verificationStatus(adhoc.context.runId), 'failed', output);
  }
  // With the verdict, unittest's own upper-case failure lines still count.
  const reported = session(t, {prompt: RECORDED.prompt});
  await writeProject(reported);
  await reported.call('exec', {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`},
    done(0, `${UNITTEST_OK}\n${'='.repeat(70)}\nFAIL: test_b (test_rename.T.test_b)\n\nFAILED (failures=1)`));
  assert.equal(reported.guard.verificationStatus(reported.context.runId), 'failed');
});

test('turns that can trip the deletion refusal disclose it once; other turns are unchanged', () => {
  assert.equal(RECURSIVE_DELETE_CONTRACT, 'Do not delete directories recursively (rm -rf or equivalents) unless the owner asked; ' +
    'ODS refuses that and may stop tool use for the turn. Keep temporary test inputs inside the project folder and leave cleanup to the owner.');
  for (const contract of [RECURSIVE_DELETE_CONTRACT, NAMED_ITEM_CONTRACT, DERIVED_FILE_CONTRACT]) {
    assert.equal(AGENT_SKILLS.workspace.split(contract).length, 2, contract);
  }
  for (const other of ['extensions', 'research', 'verification']) assert.ok(!AGENT_SKILLS[other].includes(RECURSIVE_DELETE_CONTRACT));
  assert.ok(!ODS_SEPTEMBER16_CONVERSATION_CONTRACT.includes('rm -rf'));
  assert.equal(ODS_SEPTEMBER16_CONVERSATION_CONTRACT.length, 17751);
  const system = (prompt, contextTokenBudget = 65536) =>
    promptContractForAgent({agentId: 'pixel', contextTokenBudget}, 'pixel', {prompt}).appendSystemContext;
  for (const budget of [65536, 16384]) {
    const photo = system(RECORDED.submittedUserText, budget);
    assert.ok(photo.includes(AGENT_SKILLS.workspace));
    assert.equal(photo.split(RECURSIVE_DELETE_CONTRACT).length, 2);
    // An existing-code change needs no "workspace" wording or new project.
    for (const prompt of [
      'Fix the bug in Playground/photo-renamer/rename_photos.py so PNG files keep their EXIF date, then run the tests.',
      'Run the tests in Playground/photo-renamer and fix any failures.',
      'Add a --dry-run flag to rename_photos.py and update its tests.',
    ]) {
      const contract = system(prompt, budget);
      assert.ok(!contract.includes(AGENT_SKILLS.workspace), prompt);
      assert.equal(contract.split(RECURSIVE_DELETE_CONTRACT).length, 2, prompt);
    }
    for (const prompt of ['What is the capital of France?', 'Check the ODS host hostname.',
      'What does Playground/photo-renamer/rename_photos.py do? Explain it briefly.',
      'Research the latest news about local AI models on the web and cite sources.',
      'Do not edit rename_photos.py; just tell me what it is for.']) {
      assert.ok(!system(prompt, budget).includes(RECURSIVE_DELETE_CONTRACT), prompt);
    }
  }
});

// The trusted ingress over loopback: the gateway's verification route returns
// what the plugin route returns for the replayed run after the registered
// agent_end, and the aborted model run produced no text.
test('ingress delivers the replay receipt with outcome passed', async t => {
  const run = await replay(t, CALLS);
  const verification = await run.delivered();
  const listen = async server => {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
    return server.address().port;
  };
  const gateway = http.createServer(async (req, res) => {
    for await (const _chunk of req) { /* drain */ }
    const routes = {
      '/health': {ok: true},
      '/v1/chat/completions': {id: RECORDED.runId, choices: [{index: 0,
        message: {role: 'assistant', content: ''}, finish_reason: 'stop'}]},
      '/pixel-ods/verification': verification,
      '/pixel-ods/read-only-extension-continuation': {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false},
      '/pixel-ods/unfinished-extension-decision': {schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false},
      '/pixel-ods/activity': {task: null},
    };
    if (!Object.hasOwn(routes, req.url)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify(routes[req.url]));
  });
  const gatewayPort = await listen(gateway);
  const port = await listen(createIngressServer({token: 'test-gateway-token-0123456789abcdef', gatewayPort}));
  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
    headers: {'content-type': 'application/json', connection: 'close'}, signal: AbortSignal.timeout(5000),
    body: JSON.stringify({user: 'open-prompt-d987768b0c194e76ad5b3666', stream: true,
      messages: [{role: 'user', content: RECORDED.prompt}]})});
  const frames = (await response.text()).split(/\r?\n/).filter(line => line.startsWith('data: {'))
    .map(line => JSON.parse(line.slice(6)));
  assert.equal(frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join(''), RECEIPT);
  assert.deepEqual(frames.at(-1).pixel_outcome, {schemaVersion: 1, status: 'passed'});
});
