// The guard's half of the /goal 'unfinished-work' stop, with its two
// collaborators injected the way plugin/index.js passes them (goalRun and
// taskSettlement). goal_partial_wiring.test.mjs proves the composition through
// the real entry; these isolate the guard's own conditions: the exact
// missing-current-preview outcome, every listed execution matched to a
// bound terminal receipt, fresh run ownership and unchanged stop precedence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const RUN = 'chatcmpl_7a1f4e3b-9c2d-4f0e-8a4b-5c6d5b2c1d0e';
const SESSION_KEY = `agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
const GOAL = '/goal Repair the appointment form in site/index.html and publish the site to the private preview.';
const UNFINISHED = 'I added the error styles. Next I will replace the submit handler with validation, then publish.';
const settled = (executions = []) => ({schemaVersion: 1, ok: true, calls: 3 + executions.length, edits: 1, executions});

function guarded({settlement = settled(), goal = true, prompt = GOAL, edit = true} = {}) {
  const seen = [];
  const guard = createToolLoopGuard({goalRun: id => goal && id === RUN,
    taskSettlement: (id, identity) => { seen.push({id, identity}); return typeof settlement === 'function' ? settlement() : settlement; }});
  const context = {agentId: 'pixel', runId: RUN, sessionId: 'goal-session', sessionKey: SESSION_KEY, trigger: 'user'};
  guard.observeRun(context, 'pixel', {prompt});
  const call = (toolName, params, result, toolCallId) => {
    const ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, toolCallId, params}, ctx);
    if (before?.block) return before;
    const actual = toolName === 'tool_call' ? (before?.params ?? params) : {...params, ...before?.params};
    guard.afterToolCall({toolName, toolCallId, params: actual, result}, ctx);
    guard.toolResultPersist({toolName, toolCallId, message: {role: 'toolResult', toolName, toolCallId, ...result}}, ctx);
    return before;
  };
  call('read', {path: 'site/index.html'}, {content: [{type: 'text', text: '<!doctype html><form></form>'}]}, 'read');
  if (edit) call('edit', {path: 'site/index.html', edits: [{oldText: '<form>', newText: '<form novalidate>'}]},
    {content: [{type: 'text', text: 'ok'}]}, 'edit');
  const finish = (text = UNFINISHED) => {
    guard.beforeAgentFinalize({runId: RUN, lastAssistantMessage: text,
      messages: [{role: 'assistant', content: [{type: 'text', text}], stopReason: 'stop'}]}, context);
    return guard.deliveryVerificationForRun(RUN);
  };
  return {guard, context, call, finish, seen};
}

test('the stop is the guard decision for the exact settled missing-preview round', () => {
  const round = guarded();
  const served = round.finish();
  assert.equal(served.status, 'failed');
  assert.equal(served.stop, 'unfinished-work');
  assert.deepEqual(round.seen.at(-1), {id: RUN, identity: {sessionId: 'goal-session', sessionKey: SESSION_KEY}},
    'settlement is asked for this run and its own session');
});

for (const [name, options] of [
  ['without goal mode', {goal: false}],
  ['without a settled ledger', {settlement: {schemaVersion: 1, ok: false, reason: 'unsettled'}}],
  ['without any ledger', {settlement: null}],
  ['when the ledger saw no edit', {settlement: {...settled(), edits: 0}}],
  ['when no file was saved', {edit: false, settlement: settled()}],
]) test(`no stop ${name}`, () => {
  assert.equal(guarded(options).finish().stop, undefined);
});

test('every listed execution needs a bound terminal receipt of this run', () => {
  assert.equal(guarded({settlement: settled([{callId: 'never-ran', tool: 'exec'}])}).finish().stop, undefined);
  const round = guarded({settlement: () => settled([{callId: 'build', tool: 'exec'}, {callId: 'wrapped', tool: 'exec'}])});
  round.call('exec', {command: 'npm run build'}, {content: [{type: 'text', text: 'built'}], details: {status: 'completed', exitCode: 0}}, 'build');
  // A Tool Search child's own receipt settles its parent call.
  round.call('exec', {command: 'npm test'}, {content: [{type: 'text', text: 'ok'}], details: {status: 'completed', exitCode: 0}},
    'tool_search_code:wrapped:exec:1');
  assert.equal(round.finish().stop, 'unfinished-work');
  const failing = guarded({settlement: () => settled([{callId: 'build', tool: 'exec'}])});
  failing.call('exec', {command: 'npm run build'}, {content: [{type: 'text', text: 'boom'}], details: {status: 'completed', exitCode: 1}}, 'build');
  assert.equal(failing.finish().stop, undefined);
});

test('an owned terminal poll settles both the poll and the build it finished; a pending session does not', () => {
  const executions = [{callId: 'build', tool: 'exec'}, {callId: 'poll', tool: 'process'}];
  const round = guarded({settlement: () => settled(executions)});
  round.call('exec', {command: 'npm run build'}, {content: [{type: 'text', text: 'running'}], details: {status: 'running', sessionId: 'b1'}}, 'build');
  const pending = guarded({settlement: () => settled([{callId: 'build', tool: 'exec'}])});
  pending.call('exec', {command: 'npm run build'}, {content: [{type: 'text', text: 'running'}], details: {status: 'running', sessionId: 'b1'}}, 'build');
  assert.equal(pending.finish().stop, undefined, 'still running');
  round.call('process', {action: 'poll', sessionId: 'b1'}, {content: [{type: 'text', text: 'built'}],
    details: {status: 'completed', sessionId: 'b1', exitCode: 0}}, 'poll');
  assert.equal(round.finish().stop, 'unfinished-work');
});

test('a child receipt cannot settle listed calls whose IDs share its sanitized prefix', () => {
  const ok = 'completed exit 0';
  const done = {content: [{type: 'text', text: ok}], details: {status: 'completed', exitCode: 0}};
  const single = guarded({settlement: () => settled([{callId: 'pair z', tool: 'exec'}])});
  single.call('exec', {command: 'npm run build'}, done, 'tool_search_code:pair_z:exec:1');
  assert.equal(single.finish().stop, 'unfinished-work', 'one listed parent owns its child receipt');
  const pair = guarded({settlement: () => settled([{callId: 'pair z', tool: 'exec'}, {callId: 'pair_z', tool: 'exec'}])});
  pair.call('exec', {command: 'npm run build'}, done, 'tool_search_code:pair_z:exec:1');
  assert.equal(pair.finish().stop, undefined, 'two listed parents share the prefix');
});

test('a process receipt must carry the params it was admitted with', () => {
  const executions = [{callId: 'build', tool: 'exec'}, {callId: 'poll', tool: 'process'}];
  const running = {content: [{type: 'text', text: 'running'}], details: {status: 'running', sessionId: 'b1'}};
  const finished = {content: [{type: 'text', text: 'built'}], details: {status: 'completed', sessionId: 'b1', exitCode: 0}};
  const mismatch = guarded({settlement: () => settled(executions)});
  mismatch.call('exec', {command: 'npm run build'}, running, 'build');
  const ctx = {...mismatch.context, toolName: 'process', toolCallId: 'poll'};
  mismatch.guard.beforeToolCall({toolName: 'process', toolCallId: 'poll', params: {action: 'log', sessionId: 'b1'}}, ctx);
  mismatch.guard.afterToolCall({toolName: 'process', toolCallId: 'poll', params: {action: 'poll', sessionId: 'b1'}, result: finished}, ctx);
  assert.equal(mismatch.finish().stop, undefined);
  const matched = guarded({settlement: () => settled(executions)});
  matched.call('exec', {command: 'npm run build'}, running, 'build');
  matched.call('process', {action: 'log', sessionId: 'b1'}, finished, 'poll');
  assert.equal(matched.finish().stop, 'unfinished-work');
});

test('a newer run of the same session owns the session; the older round gets no stop', () => {
  const round = guarded();
  round.guard.observeRun({...round.context, runId: 'chatcmpl_00000000-9c2d-4f0e-8a4b-5c6d5b2c1d0e'}, 'pixel', {prompt: GOAL});
  assert.equal(round.finish().stop, undefined);
});

test('the earlier stops keep precedence and their own text', () => {
  const round = guarded();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    round.guard.observeModelCall({}, round.context);
    round.call('read', {path: `site/missing-${i}.js`}, {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}},
      `missing-${i}`);
  }
  const served = round.finish();
  assert.equal(served.status, 'failed');
  assert.equal(served.stop, 'tool-limit');
});
