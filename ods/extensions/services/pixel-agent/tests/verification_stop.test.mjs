import test from 'node:test';
import assert from 'node:assert/strict';
import {parseVerificationResponse} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {createGoalProgress} from '../plugin/goal-progress.mjs';
import {readFileSync} from 'node:fs';

// A failed goal round says which Portal limit stopped it, so goal mode's
// controller (dashboard-api portal_goal.py) can continue the goal from the
// saved work instead of ending it (laptop, 2026-10-30: long changes stop at
// the tool limit). The ingress passes only known stops on failed outcomes.
const RUN_ID = 'chatcmpl_5b2c1d0e-7a1f-4e3b-9c2d-1f0e3a4b5c6d';
const CONTINUED = '\n\nContinue this goal from the saved work. Do only unfinished work.';

test('the ingress accepts a known stop on a failed outcome and nothing else', () => {
  for (const stop of ['tool-limit', 'run-deadline']) {
    assert.deepEqual(parseVerificationResponse({status: 'failed', text: 'Stopped.', stop}, RUN_ID),
      {status: 'failed', text: 'Stopped.', stop});
  }
  for (const value of [
    {status: 'failed', text: 'Stopped.', stop: 'output-limit'},
    {status: 'failed', text: 'Stopped.', stop: ['tool-limit']},
    {status: 'failed', text: 'Stopped.', stop: ''},
    {status: 'passed', text: 'Done.', stop: 'tool-limit'},
    {status: 'none', stop: 'run-deadline'},
    {status: 'pending', text: 'Which one?', stop: 'tool-limit'},
  ]) assert.throws(() => parseVerificationResponse(value, RUN_ID), /verification state unavailable/, JSON.stringify(value));
});

// A round that fails `failures` reads in a row; the guard knows it as a /goal
// round when `goal` is set (index.js passes goal-progress.mjs active).
function stoppedRound({goal = true, prompt = `/goal Fix the enemy drawing in Playground/game.${CONTINUED}`,
  failures = RUN_PROGRESS_LIMITS.consecutiveFailures} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true, goalRun: runId => goal && runId === 'run'});
  const context = {agentId: 'pixel', runId: 'run', sessionId: 'owner-session', sessionKey: 'owner-key'};
  guard.observeRun(context, 'pixel', {prompt});
  for (let i = 0; i < failures; i++) {
    const toolCallId = `read-${i}`, ctx = {...context, toolName: 'read', toolCallId};
    guard.observeModelCall({}, context);
    guard.beforeToolCall({toolName: 'read', params: {path: `Playground/game/missing-${i}.js`}, toolCallId}, ctx);
    const result = {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}};
    guard.afterToolCall({toolName: 'read', params: {path: `Playground/game/missing-${i}.js`}, toolCallId, result, error: 'ENOENT'}, ctx);
    guard.toolResultPersist({toolName: 'read', toolCallId, message: {role: 'toolResult', toolName: 'read', toolCallId, ...result}}, ctx);
  }
  return guard.deliveryVerificationForRun('run');
}

test('a goal round the tool limit stopped says so; an unfinished round does not', () => {
  const stopped = stoppedRound();
  assert.equal(stopped.status, 'failed');
  assert.equal(stopped.stop, 'tool-limit');
  assert.deepEqual(parseVerificationResponse(stopped, RUN_ID).stop, 'tool-limit');
  assert.equal(stoppedRound({failures: 1}).stop, undefined);
});

test('other chats never carry the stop: an older ingress refuses keys it does not know', () => {
  const chat = stoppedRound({goal: false, prompt: 'Fix the enemy drawing in Playground/game.'});
  assert.equal(chat.status, 'failed');
  assert.equal(Object.hasOwn(chat, 'stop'), false);
});

test('receipt-based goal rounds are not continued after a stop', () => {
  // Resumed, an exact download would download again and Operations would
  // propose its plan again (the review's probes).
  for (const prompt of [
    '/goal Download the exact bytes of https://huggingface.co/acme/tool/resolve/main/tool.bin to workspace file named Playground/tool.bin, then write a loader.',
    '/goal Use Pixel Operations to restart the comfyui service on this ODS host and verify it is healthy.',
  ]) {
    const round = stoppedRound({prompt: `${prompt}${CONTINUED}`});
    assert.equal(round.status, 'failed', prompt);
    assert.equal(Object.hasOwn(round, 'stop'), false, prompt);
  }
});

test('the plugin entry tells the guard which runs are goal rounds', () => {
  const source = readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const begin = source.indexOf('toolLoopGuardRegistry.get({');
  const options = source.slice(begin, source.indexOf('\n    });', begin));
  assert.ok(begin >= 0 && options.length > 0);
  assert.match(options, /\n\s+goalRun: runId => goalProgress\.active\(runId\),\n/);
  const goals = createGoalProgress({agentId: 'pixel'});
  goals.begin({prompt: '/goal Make enemies draw from a sprite cache.'}, {agentId: 'pixel', runId: 'goal-run', sessionId: 'a', sessionKey: 'a'});
  goals.begin({prompt: 'Make enemies draw from a sprite cache.'}, {agentId: 'pixel', runId: 'chat-run', sessionId: 'b', sessionKey: 'b'});
  assert.equal(goals.active('goal-run'), true);
  assert.equal(goals.active('chat-run'), false);
});
