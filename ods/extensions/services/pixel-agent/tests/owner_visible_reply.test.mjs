import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {OWNER_VISIBLE_REPLY_INSTRUCTION, OWNER_VISIBLE_REPLY_REASON, ownerInteractiveTurn,
  silentReplyText} from '../plugin/owner-visible-reply.mjs';

// strixy round 055 (Qwen3.6-35B-A3B): after a Portal reload the owner's direct
// question got a final reply of exactly NO_REPLY (0 tool calls), and the owner
// saw only "Pixel ended without a visible answer".
const OWNER_KEY = 'agent:pixel:openai-user:ods-' + 'e'.repeat(64);
const PORTAL_MESSAGE = "What did we decide about the dashboard reload?\n\n[ODS Portal delivery requirement: Answer the owner's " +
  "complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]";

function turn({trigger = 'user', sessionKey = OWNER_KEY, prompt = PORTAL_MESSAGE, runId = 'owner-run'} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true});
  const context = {agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey, trigger};
  guard.observeRun(context, 'pixel', {prompt});
  return {guard, context, finalize: text => guard.beforeAgentFinalize({lastAssistantMessage: text}, context)};
}

test('silent sentinel detection mirrors OpenClaw silent forms only', () => {
  for (const text of ['NO_REPLY', '  no_reply \n', 'NO_REPLY NO_REPLY', 'HEARTBEAT_OK', '"NO_REPLY"', '{"action":"NO_REPLY"}', '', '   '])
    assert.equal(silentReplyText(text), true, JSON.stringify(text));
  for (const text of [undefined, null, 'Hello!', 'The token NO_REPLY is reserved.', 'Done. NO_REPLY', '{"action":"reply"}', '"hello"'])
    assert.equal(silentReplyText(text), false, JSON.stringify(text));
});

test('only user-triggered turns in the ODS owner chat session are owner-authored interactive turns', () => {
  assert.equal(ownerInteractiveTurn({trigger: 'user', sessionKey: OWNER_KEY}), true);
  for (const context of [{trigger: 'heartbeat', sessionKey: OWNER_KEY}, {trigger: 'cron', sessionKey: OWNER_KEY},
    {trigger: 'memory', sessionKey: OWNER_KEY}, {sessionKey: OWNER_KEY}, {trigger: 'user', sessionKey: 'agent:pixel:main'},
    {trigger: 'user', sessionKey: 'agent:pixel:openai-user:not-an-ods-user'}, {trigger: 'user', sessionKey: OWNER_KEY.replace('pixel', 'other')}])
    assert.equal(ownerInteractiveTurn(context), false, JSON.stringify(context));
});

test('silent owner reply gets one bounded, byte-stable revision pass', () => {
  const {finalize} = turn();
  assert.deepEqual(finalize('NO_REPLY'), {action: 'revise', reason: OWNER_VISIBLE_REPLY_REASON,
    retry: {instruction: OWNER_VISIBLE_REPLY_INSTRUCTION, idempotencyKey: 'ods-owner-visible-reply', maxAttempts: 1}});
  assert.equal(finalize('We decided to keep the session on reload and restore the last answer.'), undefined,
    'the revised visible answer is accepted');
  assert.doesNotMatch(OWNER_VISIBLE_REPLY_REASON + OWNER_VISIBLE_REPLY_INSTRUCTION, /\d/, 'no per-turn variable text');
});

test('silent twice keeps the existing honest fallback instead of another pass', () => {
  const {guard, context, finalize} = turn();
  assert.equal(finalize('NO_REPLY')?.action, 'revise');
  assert.notEqual(finalize('NO_REPLY')?.action, 'revise', 'never a second silent-reply pass');
  // Ingress (pixel_ingress.mjs) replaces a still-silent reply with its fallback
  // when the guard has no authoritative text: verification stays textless here.
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, undefined);
});

test('heartbeat, cron, non-owner and team turns keep NO_REPLY semantics', () => {
  for (const options of [{trigger: 'heartbeat'}, {trigger: 'cron'}, {trigger: 'memory'},
    {sessionKey: 'agent:pixel:main'}, {sessionKey: 'agent:pixel:cron:nightly'},
    {prompt: "You are the Builder in the owner's Portal team. Implement the plan."}]) {
    const {finalize} = turn(options);
    assert.notEqual(finalize('NO_REPLY')?.action, 'revise', JSON.stringify(options));
  }
});

test('visible replies and stopped runs are unchanged', () => {
  const visible = turn();
  assert.equal(visible.finalize('Here is the plan: keep the session and restore the last answer.'), undefined);
  const stopped = turn({prompt: 'Compare the RTX 5070 and RX 9070. Actually search the live web and open sources.'});
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) stopped.guard.toolResultPersist({toolCallId: `f-${i}`,
    message: {role: 'toolResult', toolName: 'web_fetch', toolCallId: `f-${i}`, isError: true, content: []}},
  {...stopped.context, toolName: 'web_fetch', toolCallId: `f-${i}`});
  assert.equal(stopped.finalize('NO_REPLY'), undefined, 'no model pass after the budget stopped the response');
});
