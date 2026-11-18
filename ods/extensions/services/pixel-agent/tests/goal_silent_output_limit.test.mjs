// The /goal 'output-limit' stop for a SILENT cut: a goal round whose own last
// reply hit the model's output limit before any visible text and with no
// tool call of any shape (the native case recorded in
// ods-length-continuation-20261118/native-outputlimit/TERMINAL_SHAPES.json:
// stopReason 'length', content types [thinking], 0 visible characters, no
// tool calls, every known call settled, a copy made by exec, no typed edit and
// no preview). The installed runtime skips before_agent_finalize for such an
// incomplete reply, so every round here goes through the real registered
// before_message_write and agent_end hooks only, then the verification route
// and the real ingress parse. The host goal is still unmet, so no narration is
// needed; nothing from the reply reaches the owner. All tool results and
// content blocks are synthetic structure: no model, command or network request
// runs and no reasoning text exists here (the thinking string is a placeholder).
import test from 'node:test';
import assert from 'node:assert/strict';
import {goalGateway, plan, SITE} from './goal-partial-harness.mjs';
import {parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';

const COPY = 'mini-service-copy';
const GOAL = `/goal Repair the appointment form in ${SITE}/index.html and publish the site to the private preview.`;
const PLACEHOLDER = 'synthetic-placeholder-not-model-reasoning';
const THINKING = {type: 'thinking', thinking: PLACEHOLDER};
const READY = 'The appointment form is repaired and the site is ready.';
const done = (text = 'ok') => ({content: [{type: 'text', text}], details: {status: 'completed', exitCode: 0, durationMs: 157}});
const PAGE = {content: [{type: 'text', text: '<!doctype html><title>Original</title><form></form>'}]};
const FAILED_READ = {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}};
const RUNNING = {content: [{type: 'text', text: 'Command still running (session s1).'}], details: {status: 'running', sessionId: 's1'}};

// Native-shaped known calls, mapped to the harness workspace: listings, the
// goal plan, a copy by exec (not a typed edit) and reads of the entry page.
const NATIVE = [
  ['exec', {command: `ls -la && ls -la ${SITE}`, workdir: '/workspace'}, done('listing')],
  ['plan'],
  ['exec', {command: `mkdir -p ${COPY} && cp ${SITE}/index.html ${COPY}/index.html`, workdir: '/workspace'}, done('copied')],
  ['read', {path: `/workspace/${COPY}/index.html`, limit: 400}, PAGE],
  ['exec', {command: `grep -n "form" ${COPY}/index.html`, workdir: '/workspace'}, done('42 lines')],
  ['read', {path: `/workspace/${COPY}/index.html`, offset: 133, limit: 164}, PAGE],
];
const replaced = (index, entry) => NATIVE.map((call, i) => i === index ? entry : call);

// One round: prompt, calls, the reply's before_message_write (real hook
// context: agentId + sessionKey), agent_end, the verification route and the
// real ingress parse. No before_agent_finalize. Each option changes one fact.
async function silentRound(t, {prompt = GOAL, calls = NATIVE, content = [THINKING], absentContent = false, stopReason = 'length',
  writeContext, beforeWrite = async () => {}, beforeEnd = async () => {}, success = true, errorMessage} = {}) {
  const gateway = await goalGateway(t);
  const owner = gateway.run(prompt);
  await owner.start();
  for (const [tool, params, result] of calls) {
    if (tool === 'plan') await plan(owner, {id: 'pixel_ods_goal'});
    else await owner.call(tool, params, result);
  }
  await beforeWrite(owner, gateway);
  const message = {role: 'assistant', stopReason, usage: {input: 25513, output: 8192}, ...(errorMessage===undefined?{}:{errorMessage}),
    ...(absentContent ? {} : {content})};
  await owner.fire('before_message_write', {message}, {agentId: 'pixel', sessionKey: owner.ctx.sessionKey, ...writeContext});
  await beforeEnd(owner);
  await owner.fire('agent_end', {runId: owner.ctx.runId, success, messages: []}, owner.ctx);
  const served = await owner.verification();
  return {served, parsedStop: parseVerificationResponse(structuredClone(served), owner.ctx.runId).stop};
}

for (const [name, content] of [
  ['a thinking-only reply (the native shape)', [THINKING]],
  ['an empty text block', [{type: 'text', text: ''}]],
  ['thinking and a blank text block', [THINKING, {type: 'text', text: ' \n '}]],
]) test(`a silent cut with ${name} gets the output-limit stop with its truthful failure and the missing-preview evidence`, async t => {
  const {served, parsedStop} = await silentRound(t, {content});
  assert.equal(served.status, 'failed', 'failed stays failed: the stop adds only the closed cause');
  assert.equal(served.stop, 'output-limit');
  assert.equal(parsedStop, 'output-limit', 'the real ingress accepts it');
  assert.equal(served.task.failures, 0);
  // The owner reads what happened: the limit hit before any answer, and the
  // preview fact (files only read, nothing new or changed verified, none published).
  assert.match(served.text, /used its 8,192-token output limit before it wrote an answer/);
  assert.match(served.text, /hasn't verified new or changed files/);
  assert.doesNotMatch(served.text, /no other effects|without side effects/i);
  assert.doesNotMatch(served.notice ?? '', /This reply was cut off/, 'no cut-answer notice: there was no answer');
  assert.ok(!JSON.stringify(served).includes(PLACEHOLDER), 'nothing from the thinking block reaches the owner');
});

test('the same silent round without the cut, or with no missing preview, gets no stop (non-vacuous controls)', async t => {
  assert.equal((await silentRound(t, {stopReason: 'stop'})).served.stop, undefined);
  const noEntry = await silentRound(t, {calls: NATIVE.filter(([tool]) => tool !== 'read')});
  assert.equal(noEntry.served.status, 'failed');
  assert.equal(noEntry.served.stop, undefined, 'without entry-page evidence there is no exact missing-current-preview outcome');
});

for (const [name, options] of [
  // Content: visible text, any tool-shaped or unknown block, or absent/malformed content.
  ['a visible unannounced answer cut at the limit', {content: [{type: 'text', text: READY}]}],
  ['an unnamed tool call cut at the limit', {content: [THINKING, {type: 'toolCall', id: 'cut', arguments: {}}]}],
  ['a named tool call cut at the limit', {content: [THINKING,
    {type: 'toolCall', id: 'cut', name: 'write', arguments: {path: `/workspace/${COPY}/index.html`, content: '<!doctype html>'}}]}],
  ['a tool block of another shape', {content: [THINKING, {type: 'tool_use', id: 'cut', name: 'read', input: {}}]}],
  ['an unknown content block', {content: [THINKING, {type: 'unknown'}]}],
  ['a null content block', {content: [THINKING, null]}],
  ['a malformed thinking block', {content: [{type: 'thinking'}]}],
  ['a non-string text block', {content: [{type: 'text', text: 42}]}],
  ['absent content', {absentContent: true}],
  ['string content', {content: ''}],
  ['an empty content list', {content: []}],
  // Other ends of the reply.
  ['a normal stop', {stopReason: 'stop'}],
  ['an error stop', {stopReason: 'error'}],
  ['an aborted stop', {stopReason: 'aborted'}],
  ['an unsuccessful run end', {success: false}],
  ['an error-bearing length reply', {errorMessage: 'provider error'}],
  ['a malformed reply error field', {errorMessage: {code: 'unknown'}}],
  // Identity and supersession.
  ['a cut written with another run id', {writeContext: {runId: 'chatcmpl_00000000-0000-4000-8000-000000000999'}}],
  ['a cut written with another session id', {writeContext: {sessionId: 'session-other'}}],
  ['a newer run in the same chat before the cut', {beforeWrite: async (_owner, gateway) => { await gateway.run(GOAL).start(); }}],
  ['a later model call after the cut', {beforeEnd: owner =>
    owner.fire('model_call_started', {runId: owner.ctx.runId, callId: 'later-attempt'}, owner.ctx)}],
  ['a later visible reply after the cut', {beforeEnd: owner => owner.fire('before_message_write',
    {message: {role: 'assistant', content: [{type: 'text', text: 'Next I will publish.'}], stopReason: 'stop'}},
    {agentId: 'pixel', sessionKey: owner.ctx.sessionKey})}],
  // Ledger and run exclusions.
  ['no known calls', {calls: []}],
  ['a failed call in the ledger', {calls: replaced(3, ['read', NATIVE[3][1], FAILED_READ])}],
  ['a nonzero exec', {calls: replaced(2, ['exec', NATIVE[2][1], {...done('failed'), details: {status: 'completed', exitCode: 1}}])}],
  ['a still-running exec session', {calls: replaced(4, ['exec', NATIVE[4][1], RUNNING])}],
  ['a refused recursive delete', {beforeWrite: owner => owner.call('exec', {command: `rm -rf /workspace/${SITE}`, workdir: '/workspace'}, done())}],
  ['an unknown tool', {beforeWrite: owner => owner.call('browser_navigate', {url: 'http://127.0.0.1/'}, done())}],
  ['a publication attempt', {beforeWrite: owner => owner.call('pixel_ods_workspace_preview', {relativeDirectory: COPY},
    {isError: true, content: [{type: 'text', text: 'Preview failed'}], details: {status: 'error', code: 'preview-failed'}})}],
  ['an owner question', {beforeWrite: async owner => owner.call('pixel_ods_ask_user', {},
    await createAskUserTool().execute('ask', {questions: [{id: 'style', question: 'Which style?', options: ['Clean', 'Bold']}]}))}],
  ['the owner cancelling', {beforeEnd: async owner => { await owner.abort(); }}],
  ['a plain chat without /goal', {prompt: GOAL.replace('/goal ', '')}],
]) test(`no output-limit stop for a silent-shaped round with ${name}`, async t => {
  const {served} = await silentRound(t, options);
  assert.notEqual(served.stop, 'output-limit');
  assert.notEqual(served.stop, 'unfinished-work');
});

// Guard level, the way plugin/index.js injects taskSettlement: ledger states
// the real entry cannot easily produce and a newer run taking the session.
// No before_agent_finalize here either.
const RUN = 'chatcmpl_7a1f4e3b-9c2d-4f0e-8a4b-5c6d5b2c1d0e';
const SESSION_KEY = `agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
const settled = (executions = [{callId: 'copy', tool: 'exec'}]) => ({schemaVersion: 1, ok: true, calls: 2, edits: 0, executions});

function guarded({settlement = settled()} = {}) {
  const guard = createToolLoopGuard({goalRun: id => id === RUN,
    taskSettlement: () => settlement});
  const context = {agentId: 'pixel', runId: RUN, sessionId: 'goal-session', sessionKey: SESSION_KEY, trigger: 'user'};
  guard.observeRun(context, 'pixel', {prompt: GOAL});
  const call = (toolName, params, result, toolCallId) => {
    const ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, toolCallId, params}, ctx);
    guard.afterToolCall({toolName, toolCallId, params: {...params, ...before?.params}, result}, ctx);
    guard.toolResultPersist({toolName, toolCallId, message: {role: 'toolResult', toolName, toolCallId, ...result}}, ctx);
  };
  call('read', {path: `${SITE}/index.html`}, PAGE, 'read');
  call('exec', {command: `cp ${SITE}/index.html ${COPY}/index.html`}, done('copied'), 'copy');
  const finish = () => {
    guard.observeReplyEnd({message: {role: 'assistant', content: [THINKING], stopReason: 'length', usage: {output: 8192}}},
      {agentId: 'pixel', sessionKey: SESSION_KEY}, 'pixel');
    guard.observeAgentEnd({runId: RUN, success: true, messages: []}, context);
    return guard.deliveryVerificationForRun(RUN);
  };
  return {guard, context, finish};
}

test('guard level: the settled silent cut round stops as output-limit', () => {
  assert.equal(guarded().finish().stop, 'output-limit');
});

for (const [name, settlement] of [
  ['no ledger', null],
  ['a truncated ledger', {schemaVersion: 1, ok: false, reason: 'truncated'}],
  ['a failed ledger', {schemaVersion: 1, ok: false, reason: 'failed'}],
  ['an identity conflict', {schemaVersion: 1, ok: false, reason: 'identity'}],
  ['a ledger without known calls', {schemaVersion: 1, ok: true, calls: 0, edits: 0, executions: []}],
  ['an execution without its own terminal receipt', settled([{callId: 'never-ran', tool: 'exec'}])],
]) test(`guard level: no output-limit stop for a silent cut with ${name}`, () => {
  assert.equal(guarded({settlement}).finish().stop, undefined);
});

test('guard level: a newer run of the same session owns it; the older silent round gets no stop', () => {
  const round = guarded();
  round.guard.observeRun({...round.context, runId: 'chatcmpl_00000000-9c2d-4f0e-8a4b-5c6d5b2c1d0e'}, 'pixel', {prompt: GOAL});
  assert.equal(round.finish().stop, undefined);
});
