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
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import http from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, RECURSIVE_DELETE_REQUIRES_OWNER_REASON} from '../plugin/tool-loop-guard.mjs';
import {AGENT_SKILLS, DERIVED_FILE_CONTRACT, NAMED_ITEM_CONTRACT, RECURSIVE_DELETE_CONTRACT} from '../plugin/agent-skills.mjs';
import {ODS_SEPTEMBER16_CONVERSATION_CONTRACT, promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const RECORDED = JSON.parse(fs.readFileSync(new URL('./fixtures/photo-renamer-tower2-d4a61f33.json', import.meta.url), 'utf8'));
const CALLS = RECORDED.calls;
const PROJECT = 'Playground/photo-renamer';
const LEAD = 'Pixel stopped using tools because a command included a recursive deletion that you did not ask for. ' +
  "That command was refused and did not run.\nResults recorded by Pixel's tools before that:\n";
const FILES = '- File written: `/workspace/Playground/photo-renamer/rename_photos.py`.\n' +
  '- File written: `/workspace/Playground/photo-renamer/test_rename.py`.\n';
const TEST = 'The latest recognized test command, `python3 test_rename.py` in `/workspace/Playground/photo-renamer`,';
const INCOMPLETE = 'This request is not complete. Ask Pixel to continue, or say explicitly if you want a folder deleted.';
const RECEIPT = LEAD + FILES +
  `- ${TEST} passed, and no tool call that could change the workspace ran after it.\n` +
  'This does not establish complete test coverage or completion of every requested step.';

// OpenClaw's hook order for each recorded round: model call start and end,
// before_tool_call, the executed result (or the SDK's veto result for a
// blocked call), after_tool_call and tool_result_persist. Executed results are
// the recorded tool outputs without the ODS suffix that persistence appended.
function replay(t, calls, {prompt = RECORDED.submittedUserText} = {}) {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'ods-deletion-refusal-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const prepared = [], signalled = [], aborted = [];
  const guard = createToolLoopGuard({
    abortRun: (sessionId) => { aborted.push(sessionId); return true; },
    execControl: {
      signal: (runId) => { signalled.push(runId); return true; },
      prepare: (runId, command) => {
        prepared.push(command);
        return `/run/pixel-ods-control/cancellable-exec.sh ${'0'.repeat(64)} ${Buffer.from(command).toString('base64')}`;
      },
    },
  });
  const context = {agentId: 'pixel', runId: RECORDED.runId, sessionId: RECORDED.sessionId,
    sessionKey: 'agent:pixel:openai-user:owner'};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root});
  const decisions = [];
  let round = 0;
  for (const call of calls) {
    const callId = `${context.runId}:model:${++round}`;
    guard.observeModelCall({callId}, context);
    guard.observeModelEnd({callId, outcome: 'completed'}, context);
    const ctx = {...context, toolName: call.tool, toolCallId: call.id};
    const params = structuredClone(call.arguments);
    const decision = guard.beforeToolCall({toolName: call.tool, toolCallId: call.id, runId: context.runId, params}, ctx);
    decisions.push(decision);
    const admitted = decision?.block ? params : {...params, ...decision?.params};
    let result;
    if (decision?.block) {
      result = {isError: true, content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: decision.blockReason}};
    } else {
      const text = call.result.text.split('[ODS Pixel ')[0];
      result = {content: [{type: 'text', text}], ...(call.result.details ? {details: call.result.details} : {}),
        ...(call.result.isError ? {isError: true} : {})};
      if (call.tool === 'write') {
        fs.mkdirSync(path.dirname(path.join(root, admitted.path)), {recursive: true});
        fs.writeFileSync(path.join(root, admitted.path), admitted.content);
      }
    }
    guard.afterToolCall({toolName: call.tool, toolCallId: call.id, runId: context.runId, params: admitted, result,
      ...(result.isError ? {error: result.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolName: call.tool, toolCallId: call.id, message: {role: 'toolResult',
      toolName: call.tool, toolCallId: call.id, isError: result.isError === true, ...structuredClone(result)}}, ctx);
  }
  // The recorded final model call was aborted without text.
  const callId = `${context.runId}:model:${++round}`;
  guard.observeModelCall({callId}, context);
  guard.observeModelEnd({callId, outcome: 'error'}, context);
  return {guard, context, decisions, prepared, signalled, aborted};
}

// The plugin's verification route: agent_end, then settle, then read.
async function delivered({guard, context}) {
  guard.observeAgentEnd({}, context);
  await guard.settleDelivery(context.runId);
  return guard.deliveryVerificationForRun(context.runId);
}

const byIndex = (...numbers) => numbers.map(number => CALLS[number - 1]);
const exec = (id, command, workdir = '/workspace') =>
  ({id, tool: 'exec', arguments: {command, workdir}, result: {isError: false, text: ''}});

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

test('tower2 replay: the refused cleanup keeps the current passing test and the owner gets a receipt', async t => {
  const run = replay(t, CALLS);
  const {guard, context, decisions, prepared, signalled, aborted} = run;
  // #1 is the recorded path correction; #10 and #11 are the recorded refusals.
  assert.equal(decisions[0]?.blockReason, CALLS[0].result.text);
  assert.match(decisions[0].blockReason, /^For a new project, use a workspace-relative path/);
  assert.deepEqual(decisions.map(decision => decision?.block === true),
    [true, false, false, false, false, false, false, false, false, true, true]);
  assert.equal(decisions[9].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.equal(decisions[10].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  // Only #4, #5, #7 and #9 reached execution control; the deletion never did.
  assert.deepEqual(prepared, byIndex(4, 5, 7, 9).map(call => call.arguments.command));
  assert.deepEqual(signalled, [context.runId]);
  assert.deepEqual(aborted, [context.sessionId]);
  assert.equal(guard.verificationStatus(context.runId), 'passed');
  assert.deepEqual(guard.verificationForRun(context.runId), {status: 'passed', text: RECEIPT});
  assert.deepEqual(await delivered(run), {status: 'passed', text: RECEIPT});
  // The aborted run's empty final, or any model text, is replaced.
  for (const text of ['', 'I wrote the script and tested it; the manual folder was cleaned up.']) {
    assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text}}).payload.text, RECEIPT);
  }
  assert.doesNotMatch(RECEIPT, /Do not retry|wait for a new owner instruction|nothing was deleted/i);
  assert.ok(RECEIPT.length < 32 * 1024);
  // The latch, finalization and continuation behavior are unchanged.
  assert.equal(guard.beforeAgentFinalize({}, context), undefined);
  assert.equal(guard.continuationAllowed(context.runId), false);
  assert.deepEqual(guard.beforeToolCall({toolName: 'read', params: {path: `${PROJECT}/test_rename.py`}},
    {...context, toolName: 'read', toolCallId: 'later-read'}),
  {block: true, blockReason: RECURSIVE_DELETE_REQUIRES_OWNER_REASON});
  assert.deepEqual(signalled, [context.runId]);
  assert.deepEqual(aborted, [context.sessionId]);
});

test('tower2 replay: the refused cleanup alone, before any later call, gives the same receipt', async t => {
  const run = replay(t, CALLS.slice(0, 10));
  assert.equal(run.decisions[9].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(run.signalled, []);
  assert.deepEqual(run.aborted, []);
  assert.deepEqual(await delivered(run), {status: 'passed', text: RECEIPT});
  // A text-only answer after the refusal is still not accepted.
  assert.equal(run.guard.replyPayloadSending({runId: run.context.runId, kind: 'final',
    payload: {text: 'Done! I removed the temporary folder.'}}).payload.text, RECEIPT);
});

test('tower2 replay: a later write makes the pass stale, so the refusal stays failed', async t => {
  const write = {id: 'extra-write', tool: 'write', arguments: {path: `${PROJECT}/README.md`, content: '# Photo renamer\n'},
    result: {isError: false, text: `Successfully wrote 16 bytes to ${PROJECT}/README.md`}};
  const run = replay(t, [...CALLS.slice(0, 9), write, CALLS[9]]);
  assert.deepEqual(await delivered(run), {status: 'failed', text: LEAD +
    '- File written: `/workspace/Playground/photo-renamer/README.md`.\n' + FILES +
    `- ${TEST} passed, but a later tool call could have changed the workspace, so that result is not current.\n` + INCOMPLETE});
});

test('tower2 replay: a refusal right after the failing test stays failed', async t => {
  const run = replay(t, [...CALLS.slice(0, 5), CALLS[9]]);
  assert.equal(run.decisions[5].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await delivered(run), {status: 'failed', text: LEAD + FILES + `- ${TEST} failed.\n` + INCOMPLETE});
});

test('tower2 replay: a refusal before any test ran stays failed with an owner receipt', async t => {
  const run = replay(t, [...CALLS.slice(0, 4), CALLS[9]]);
  const receipt = LEAD + FILES + '- No recognized test command ran.\n' + INCOMPLETE;
  assert.deepEqual(await delivered(run), {status: 'failed', text: receipt});
  assert.equal(run.guard.replyPayloadSending({runId: run.context.runId, kind: 'final',
    payload: {text: 'All tests pass.'}}).payload.text, receipt);
});

test('tower2 replay: a refused rerun of the test is not a test result', async t => {
  // Every call after the refusal is refused and runs nothing. Its blocked
  // receipt must not turn the real pass into a failure.
  const rerun = exec('refused-rerun', `cd /workspace/${PROJECT} && python3 test_rename.py`);
  const run = replay(t, [...CALLS.slice(0, 10), rerun]);
  assert.equal(run.decisions[10].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await delivered(run), {status: 'passed', text: RECEIPT});
  // The same holds when the refused deletion itself carries a test command.
  const combined = exec('refused-combined', 'rm -rf test_photos && python3 -m unittest -v', `/workspace/${PROJECT}`);
  const chained = replay(t, [...CALLS.slice(0, 9), combined]);
  assert.equal(chained.decisions[9].blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(await delivered(chained), {status: 'passed', text: RECEIPT});
});

test('owner-authorized recursive deletion is unchanged', async t => {
  const prompt = 'Delete the directory /workspace/test_manual recursively.';
  const run = replay(t, [exec('authorized', 'rm -rf /workspace/test_manual')], {prompt});
  assert.notEqual(run.decisions[0]?.block, true, run.decisions[0]?.blockReason);
  assert.doesNotMatch(JSON.stringify(await delivered(run)), /recursive deletion/);
});

test('a still-running test keeps the refusal failed', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
  guard.observeRun(context, 'pixel', {prompt: RECORDED.prompt});
  const params = {command: 'python3 -m unittest -v', workdir: `/workspace/${PROJECT}`};
  guard.beforeToolCall({toolName: 'exec', toolCallId: 'test', params}, {...context, toolName: 'exec', toolCallId: 'test'});
  guard.afterToolCall({toolName: 'exec', toolCallId: 'test', params,
    result: {details: {status: 'running', sessionId: 'tests-1'}, content: [{type: 'text', text: 'running'}]}},
  {...context, toolName: 'exec', toolCallId: 'test'});
  assert.equal(guard.verificationStatus('run-1'), 'pending');
  assert.equal(guard.beforeToolCall({toolName: 'exec', toolCallId: 'rm', params: {command: 'rm -rf /workspace/tmp'}},
    {...context, toolName: 'exec', toolCallId: 'rm'}).blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(guard.deliveryVerificationForRun('run-1'), {status: 'failed', text: LEAD + '- No file was written.\n' +
    '- The latest recognized test command, `python3 -m unittest -v` in `/workspace/Playground/photo-renamer`, had not finished, ' +
    'so its result is unknown.\n- A command started earlier was still running when tool use stopped.\n' + INCOMPLETE});
});

test('an Operations receipt and a current test pass cannot complete a run after a deletion refusal', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
  const run = (toolName, params, id, result) => {
    const ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, toolCallId: id, params}, ctx);
    if (result) guard.afterToolCall({toolName, toolCallId: id, params, result}, ctx);
    return decision;
  };
  guard.observeRun(context, 'pixel', {prompt: 'Check the ODS host hostname.'});
  run('pixel_ods_host_observe', {actions: ['host.identity']}, 'observe', {details: {jobId: 'ops-1234567890123-abcdef123456',
    status: 'succeeded', waitTimedOut: false, steps: [{stepId: 'observe-1', target: 'ods-host', action: 'host.identity',
      exitCode: 0, stdout: 'test-host\n', stderr: '', outputTruncated: {stdout: false, stderr: false}, riskSignals: []}]}});
  assert.equal(guard.verificationForRun('run-1').status, 'passed');
  run('exec', {command: 'python3 -m unittest -v'}, 'test',
    {details: {status: 'completed', exitCode: 0}, content: [{type: 'text', text: 'Ran 1 test\n\nOK'}]});
  assert.equal(guard.verificationStatus('run-1'), 'passed');
  assert.equal(run('exec', {command: 'rm -rf /workspace/scratch'}, 'rm').blockReason, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);
  assert.deepEqual(guard.deliveryVerificationForRun('run-1'), {status: 'failed', text: LEAD + '- No file was written.\n' +
    '- The latest recognized test command, `python3 -m unittest -v` in `/workspace`, passed, and no tool call that could ' +
    'change the workspace ran after it.\n' + INCOMPLETE});
});

test('the receipt stays bounded and never quotes model text as markup', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
  guard.observeRun(context, 'pixel', {prompt: RECORDED.prompt});
  const run = (toolName, params, id, result) => {
    const ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, toolCallId: id, params}, ctx);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    guard.afterToolCall({toolName, toolCallId: id, params: {...params, ...decision?.params}, result}, ctx);
  };
  for (let index = 0; index < 25; index++) {
    const content = `fixture ${index}\n`;
    run('write', {path: `${PROJECT}/fixture_${String(index).padStart(2, '0')}.txt`, content}, `write-${index}`,
      {content: [{type: 'text', text: `Successfully wrote ${content.length} bytes`}]});
  }
  const long = `cd "/workspace/${PROJECT}/a\`b ${'d'.repeat(300)}" && python3 test_rename.py`;
  run('exec', {command: long}, 'test', {details: {status: 'completed', exitCode: 0}, content: [{type: 'text', text: 'ok'}]});
  assert.equal(guard.verificationStatus('run-1'), 'passed');
  guard.beforeToolCall({toolName: 'exec', toolCallId: 'rm', params: {command: 'rm -rf /workspace/tmp'}},
    {...context, toolName: 'exec', toolCallId: 'rm'});
  const {status, text} = guard.deliveryVerificationForRun('run-1');
  assert.equal(status, 'passed');
  assert.equal(text.match(/^- File written: /gm).length, 20);
  assert.match(text, /\n- 5 additional files were written\.\n/);
  const test = text.match(/^- The latest recognized test command, `python3 test_rename\.py` in `([^`\n]*)`, passed/m);
  assert.ok(test, text);
  assert.ok(test[1].startsWith(`/workspace/${PROJECT}/a b ddd`));
  assert.ok(test[1].length <= 160 && test[1].endsWith('\u2026'));
  assert.doesNotMatch(text, /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/);
  assert.ok(text.length < 32 * 1024);
});

test('the photo-renamer turn discloses the deletion tripwire in the workspace guide only', () => {
  assert.equal(RECURSIVE_DELETE_CONTRACT, 'Do not delete directories recursively (rm -rf or equivalents) unless the owner asked; ' +
    'ODS refuses that and may stop tool use for the turn. Keep temporary test inputs inside the project folder and leave cleanup to the owner.');
  for (const contract of [RECURSIVE_DELETE_CONTRACT, NAMED_ITEM_CONTRACT, DERIVED_FILE_CONTRACT]) {
    assert.equal(AGENT_SKILLS.workspace.split(contract).length, 2, contract);
  }
  for (const other of ['extensions', 'research', 'verification']) assert.ok(!AGENT_SKILLS[other].includes(RECURSIVE_DELETE_CONTRACT));
  assert.ok(!ODS_SEPTEMBER16_CONVERSATION_CONTRACT.includes('rm -rf'));
  assert.equal(ODS_SEPTEMBER16_CONVERSATION_CONTRACT.length, 17751);
  const system = promptContractForAgent({agentId: 'pixel', contextTokenBudget: 65536}, 'pixel',
    {prompt: RECORDED.submittedUserText}).appendSystemContext;
  assert.ok(system.includes(AGENT_SKILLS.workspace));
  assert.equal(system.split(RECURSIVE_DELETE_CONTRACT).length, 2);
});

// The trusted ingress over loopback: the gateway's verification route returns
// what the plugin route returns for the replayed run, and the aborted model
// run produced no text.
test('ingress delivers the replay receipt with outcome passed', async t => {
  const run = replay(t, CALLS);
  const verification = await delivered(run);
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
