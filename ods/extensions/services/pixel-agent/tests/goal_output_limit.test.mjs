// The /goal 'output-limit' stop: a goal round whose own final reply ended at
// the model's output limit on visible text that still announces work, after
// every known call settled. The exact case is the native round redacted in
// ods-length-continuation-20261118/NATIVE_CASE_REDACTED.json: seven calls
// (three exec, the goal plan, three reads), all exec terminal exit 0, a copy
// made by exec rather than a typed edit, no preview, and a final text reply
// cut at 'length' with no tool call in it. The 'unfinished-work' stop refuses
// it (outputLimitStop, no saved edit) and keeps refusing it; 'output-limit' is
// a separate, equally closed eligibility. Like 'unfinished-work' it reports
// host-observed known-call settlement and the missing preview, never that
// commands had no other effects. Tool results are synthetic: no command,
// model or network request runs.
import test from 'node:test';
import assert from 'node:assert/strict';
import {goalGateway, plan, SITE} from './goal-partial-harness.mjs';
import {parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';

const COPY = 'mini-service-copy';
const GOAL = `/goal Repair the appointment form in ${SITE}/index.html and publish the site to the private preview.`;
// The native round's visible final text, verbatim.
const CUT_TEXT = "I've read the source. Now let me see the booking CTA section and check for external resources or contact data.";
const READY = 'The appointment form is repaired and the site is ready.';
const done = (text = 'ok') => ({content: [{type: 'text', text}], details: {status: 'completed', exitCode: 0, durationMs: 157}});
const PAGE = {content: [{type: 'text', text: '<!doctype html><title>Original</title><form></form>'}]};
const FAILED_READ = {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}};

// The native seven-call sequence, mapped to the harness workspace.
const NATIVE = [
  ['exec', {command: `ls -la && ls -la ${SITE} 2>/dev/null; ls -la ${COPY} 2>/dev/null; echo "---done---"`, workdir: '/workspace'}, done('listing')],
  ['plan'],
  ['exec', {command: `mkdir -p ${COPY} && cp ${SITE}/index.html ${COPY}/index.html && ls -la ${COPY} && sha256sum ${SITE}/index.html ${COPY}/index.html`,
    workdir: '/workspace'}, done('copied')],
  ['read', {path: `/workspace/${COPY}/index.html`, limit: 400}, PAGE],
  ['exec', {command: `wc -l ${COPY}/index.html && grep -n "form" ${COPY}/index.html | head -80`, workdir: '/workspace'}, done('42 lines')],
  ['read', {path: `/workspace/${COPY}/index.html`, offset: 297, limit: 180}, PAGE],
  ['read', {path: `/workspace/${COPY}/index.html`, offset: 133, limit: 164}, PAGE],
];

// One native-shaped round through the harness's own finish(): the reply's
// before_message_write, finalize, agent_end, the verification route and the
// real ingress parse of what it serves. Each option changes one fact.
async function nativeRound(t, {prompt = GOAL, calls = NATIVE, before = async () => {}, mutate = async () => {},
  text = CUT_TEXT, stopReason = 'length', content} = {}) {
  const gateway = await goalGateway(t);
  const owner = gateway.run(prompt);
  await owner.start();
  await before(owner);
  for (const [tool, params, result] of calls) {
    if (tool === 'plan') await plan(owner, {id: 'pixel_ods_goal'});
    else await owner.call(tool, params, result);
  }
  await mutate(owner);
  const {served, parsed} = await owner.finish(text, {stopReason, content});
  return {...served, parsedStop: parsed.stop, ownerRunId: owner.ctx.runId};
}

test('the exact native output-limit round gets the closed output-limit stop', async t => {
  const served = await nativeRound(t);
  assert.equal(served.status, 'failed', 'failed stays failed: the stop adds only the closed cause');
  assert.equal(served.stop, 'output-limit');
  assert.equal(served.task.failures, 0);
  // Truthful text: files were read, no new or changed file was verified, no preview.
  assert.match(served.text, /hasn't verified new or changed files/);
  assert.doesNotMatch(served.text, /no other effects|without side effects/i);
  assert.match(served.notice, /output limit/i, 'the cut answer keeps its output-limit notice');
});

test('the ingress accepts exactly the stop it is served (root-owned ingress stop set)', async t => {
  const served = await nativeRound(t);
  assert.equal(served.parsedStop, 'output-limit');
  // Non-vacuous control: the same served body and run parse; only an altered
  // stop is refused by the closed set.
  const {parsedStop, ownerRunId, ...body} = served;
  assert.equal(parseVerificationResponse(structuredClone(body), ownerRunId).stop, 'output-limit');
  assert.throws(() => parseVerificationResponse({...structuredClone(body), stop: 'output-limit-x'}, ownerRunId),
    /verification state unavailable/);
});

test('the unfinished-work boundary is unchanged: no typed edit and an output-limit cut never make it unfinished-work', async t => {
  const served = await nativeRound(t);
  assert.notEqual(served.stop, 'unfinished-work');
  const complete = await nativeRound(t, {stopReason: 'stop'});
  assert.equal(complete.stop, undefined, 'a non-length final with no saved edit gets neither stop');
});

const RUNNING = {content: [{type: 'text', text: 'Command still running (session s1).'}], details: {status: 'running', sessionId: 's1'}};
const replaced = (index, entry) => NATIVE.map((call, i) => i === index ? entry : call);

for (const [name, options] of [
  ['a plain chat without /goal', {prompt: GOAL.replace('/goal ', '')}],
  ['a goal that requests no workspace mutation or preview', {prompt: `/goal Read ${SITE}/index.html and tell me what the form does.`}],
  ['a failed call in the ledger', {calls: replaced(3, ['read', NATIVE[3][1], FAILED_READ])}],
  ['a nonzero exec', {calls: replaced(2, ['exec', NATIVE[2][1], {...done('failed'), details: {status: 'completed', exitCode: 1}}])}],
  ['a still-running exec session', {calls: replaced(4, ['exec', NATIVE[4][1], RUNNING])}],
  ['a refused recursive delete', {mutate: owner => owner.call('exec', {command: `rm -rf /workspace/${SITE}`, workdir: '/workspace'}, done())}],
  ['an unknown tool', {mutate: owner => owner.call('browser_navigate', {url: 'http://127.0.0.1/'}, done())}],
  ['an interrupted tool call at the output limit', {content: [{type: 'text', text: CUT_TEXT},
    {type: 'toolCall', id: 'cut', name: 'read', arguments: {path: `/workspace/${COPY}/index.html`}}]}],
  ['a normal complete final', {stopReason: 'stop'}],
  ['a length cut that announces nothing more', {text: READY}],
  ['a publication attempt', {mutate: owner => owner.call('pixel_ods_workspace_preview', {relativeDirectory: COPY},
    {isError: true, content: [{type: 'text', text: 'Preview failed'}], details: {status: 'error', code: 'preview-failed'}})}],
  ['an owner question', {mutate: async owner => owner.call('pixel_ods_ask_user', {},
    await createAskUserTool().execute('ask', {questions: [{id: 'style', question: 'Which style?', options: ['Clean', 'Bold']}]}))}],
]) test(`no output-limit stop with ${name}`, async t => {
  const served = await nativeRound(t, options);
  assert.notEqual(served.stop, 'output-limit');
  assert.notEqual(served.stop, 'unfinished-work');
});

test('no output-limit stop after the owner cancels', async t => {
  const served = await nativeRound(t, {mutate: async owner => { await owner.abort(); }});
  assert.notEqual(served.stop, 'output-limit');
});

// R2: the installed SDK's terminal path for this case. An incomplete terminal
// reply (stopReason 'length', no terminal output) returns from
// onBeforeTerminalDelivery BEFORE before_agent_finalize (selection-BEwSQKM-.js
// fa2a7170, root-read), so the round gets only before_message_write (whose real
// hook context is agentId + sessionKey) and agent_end, then the verification
// route. No finalize hook fires here.
async function nativeTerminal(t, {text = CUT_TEXT, stopReason = 'length', writeContext, beforeEnd = async () => {}} = {}) {
  const gateway = await goalGateway(t);
  const owner = gateway.run(GOAL);
  await owner.start();
  for (const [tool, params, result] of NATIVE) {
    if (tool === 'plan') await plan(owner, {id: 'pixel_ods_goal'});
    else await owner.call(tool, params, result);
  }
  const message = {role: 'assistant', content: [{type: 'text', text}], stopReason};
  await owner.fire('before_message_write', {message},
    {agentId: 'pixel', sessionKey: owner.ctx.sessionKey, ...writeContext});
  await beforeEnd(owner);
  await owner.fire('agent_end', {runId: owner.ctx.runId, success: true, messages: []}, owner.ctx);
  const served = await owner.verification();
  return {served, parsedStop: parseVerificationResponse(structuredClone(served), owner.ctx.runId).stop};
}

test('R2: the native terminal path without before_agent_finalize gets the output-limit stop', async t => {
  const {served, parsedStop} = await nativeTerminal(t);
  assert.equal(served.status, 'failed');
  assert.equal(served.stop, 'output-limit');
  assert.equal(parsedStop, 'output-limit');
  assert.equal(served.task.failures, 0);
});

for (const [name, options] of [
  ['a later model attempt after the cut', {beforeEnd: owner =>
    owner.fire('model_call_started', {runId: owner.ctx.runId, callId: 'later-attempt'}, owner.ctx)}],
  ['a later non-cut reply after the cut', {beforeEnd: owner => owner.fire('before_message_write',
    {message: {role: 'assistant', content: [{type: 'text', text: 'Next I will publish.'}], stopReason: 'stop'}},
    {agentId: 'pixel', sessionKey: owner.ctx.sessionKey})}],
  ['a cut written with another run id', {writeContext: {runId: 'chatcmpl_00000000-0000-4000-8000-000000000999'}}],
  ['a cut written with another session id', {writeContext: {sessionId: 'session-other'}}],
  ['a native length cut that announces nothing more', {text: READY}],
]) test(`R2: no output-limit stop with ${name}`, async t => {
  const {served} = await nativeTerminal(t, options);
  assert.notEqual(served.stop, 'output-limit');
  assert.notEqual(served.stop, 'unfinished-work');
});

// Guard-level conditions with taskSettlement injected the way plugin/index.js
// passes it: ledger states the real entry cannot easily produce, and a newer
// run taking the session.
const RUN = 'chatcmpl_7a1f4e3b-9c2d-4f0e-8a4b-5c6d5b2c1d0e';
const SESSION_KEY = `agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
const settled = (executions = [{callId: 'copy', tool: 'exec'}]) => ({schemaVersion: 1, ok: true, calls: 2, edits: 0, executions});

function guarded({settlement = settled(), cut = true} = {}) {
  const guard = createToolLoopGuard({goalRun: id => id === RUN,
    taskSettlement: () => typeof settlement === 'function' ? settlement() : settlement});
  const context = {agentId: 'pixel', runId: RUN, sessionId: 'goal-session', sessionKey: SESSION_KEY, trigger: 'user'};
  guard.observeRun(context, 'pixel', {prompt: GOAL});
  const call = (toolName, params, result, toolCallId) => {
    const ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, toolCallId, params}, ctx);
    guard.afterToolCall({toolName, toolCallId, params: {...params, ...before?.params}, result}, ctx);
    guard.toolResultPersist({toolName, toolCallId, message: {role: 'toolResult', toolName, toolCallId, ...result}}, ctx);
  };
  call('read', {path: `${SITE}/index.html`}, PAGE, 'read');
  // No sandbox config at guard level, so no '/workspace' workdir here (an
  // unbound exec receipt correctly settles nothing); the real-entry tests
  // above run the native '/workspace' calls.
  call('exec', {command: `cp ${SITE}/index.html ${COPY}/index.html`}, done('copied'), 'copy');
  const finish = () => {
    const message = {role: 'assistant', content: [{type: 'text', text: CUT_TEXT}], stopReason: cut ? 'length' : 'stop'};
    guard.observeReplyEnd({message}, context, 'pixel');
    guard.beforeAgentFinalize({runId: RUN, lastAssistantMessage: CUT_TEXT, messages: [message]}, context);
    guard.observeAgentEnd({runId: RUN, success: true, messages: []}, context);
    return guard.deliveryVerificationForRun(RUN);
  };
  return {guard, context, finish};
}

test('guard level: the settled cut round stops as output-limit', () => {
  assert.equal(guarded().finish().stop, 'output-limit');
});

for (const [name, settlement] of [
  ['no ledger', null],
  ['a truncated ledger', {schemaVersion: 1, ok: false, reason: 'truncated'}],
  ['a failed ledger', {schemaVersion: 1, ok: false, reason: 'failed'}],
  ['an identity conflict', {schemaVersion: 1, ok: false, reason: 'identity'}],
  ['a ledger without known calls', {schemaVersion: 1, ok: true, calls: 0, edits: 0, executions: []}],
  ['an execution without its own terminal receipt', settled([{callId: 'never-ran', tool: 'exec'}])],
]) test(`guard level: no output-limit stop with ${name}`, () => {
  assert.equal(guarded({settlement}).finish().stop, undefined);
});

test('guard level: a newer run of the same session owns it; the older round gets no stop', () => {
  const round = guarded();
  round.guard.observeRun({...round.context, runId: 'chatcmpl_00000000-9c2d-4f0e-8a4b-5c6d5b2c1d0e'}, 'pixel', {prompt: GOAL});
  assert.equal(round.finish().stop, undefined);
});
