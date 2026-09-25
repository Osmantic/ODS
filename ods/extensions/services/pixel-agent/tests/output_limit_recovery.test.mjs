import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {OUTPUT_LIMIT_INSTRUCTION, OUTPUT_LIMIT_REASON, OUTPUT_LIMIT_UNRECOVERED_TEXT,
  outputLimitReply} from '../plugin/output-limit-recovery.mjs';

// strixy 2026-09-25 (Qwen3.6-35B-A3B, 8192 output tokens): the owner asked for
// "a cool looking webpage with a forest theme ... Best you can do". The model
// wrote the whole page in one call, the reply stopped at the output limit
// (stopReason "length") after 220 s, the unfinished write never ran, and the
// owner saw only "Let me build something impressive".
const OWNER_KEY = 'agent:pixel:openai-user:ods-' + 'f'.repeat(64);
const PROMPT = 'as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest ' +
  "type effects.  Best you can do.\n\n[ODS Portal delivery requirement: Answer the owner's complete message above.]";
// A text answer keeps the workspace continuations out of these checks.
const TEXT_PROMPT = 'Write me the longest, most detailed guide you can about how old-growth forests store carbon.';
const RECOVERY_KEY = 'ods-output-limit-recovery';
const CUT = {role: 'assistant', stopReason: 'length', content: [
  {type: 'text', text: 'Let me build something impressive — a full forest-themed interactive experience.'}]};
const DONE = {role: 'assistant', stopReason: 'stop', content: [{type: 'text', text: 'Your forest page is published.'}]};

function turn({trigger = 'user', sessionKey = OWNER_KEY, prompt = TEXT_PROMPT, runId = 'owner-run'} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true});
  const context = {agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey, trigger};
  guard.observeRun(context, 'pixel', {prompt});
  return {guard, context,
    reply: message => guard.observeAssistantMessage({message}, context),
    finalize: message => guard.beforeAgentFinalize({lastAssistantMessage: message.content[0].text}, context)};
}

test('only an assistant reply that stopped at the output limit counts', () => {
  assert.equal(outputLimitReply(CUT), true);
  for (const message of [DONE, {...CUT, role: 'user'}, {...CUT, stopReason: 'toolUse'}, {...CUT, stopReason: 'error'}, undefined])
    assert.equal(outputLimitReply(message), false, JSON.stringify(message));
});

test('a reply cut at the output limit gets one bounded, byte-stable revision pass', () => {
  const {guard, context, reply, finalize} = turn();
  reply(CUT);
  assert.deepEqual(finalize(CUT), {action: 'revise', reason: OUTPUT_LIMIT_REASON,
    retry: {instruction: OUTPUT_LIMIT_INSTRUCTION, idempotencyKey: 'ods-output-limit-recovery', maxAttempts: 1}});
  reply(DONE);
  assert.equal(finalize(DONE), undefined, 'a complete revised reply is accepted');
  assert.notEqual(guard.deliveryVerificationForRun(context.runId).status, 'failed');
  assert.doesNotMatch(OUTPUT_LIMIT_REASON + OUTPUT_LIMIT_INSTRUCTION, /\d/, 'no per-turn variable text');
});

test('the strixy website request is recovered before any other continuation', () => {
  const {reply, finalize} = turn({prompt: PROMPT});
  reply(CUT);
  assert.equal(finalize(CUT)?.retry?.idempotencyKey, RECOVERY_KEY);
});

test('a truncated tool call reply is recovered the same way', () => {
  const {reply, finalize} = turn({prompt: PROMPT});
  const cutCall = {...CUT, content: [...CUT.content, {type: 'toolCall', id: 'call-1', name: 'write',
    arguments: {path: 'forest/index.html', content: '<!doctype html><html><head><style>'}}]};
  reply(cutCall);
  assert.equal(finalize(cutCall)?.action, 'revise');
});

test('cut twice reports the output limit instead of another pass', () => {
  const {guard, context, reply, finalize} = turn();
  reply(CUT);
  assert.equal(finalize(CUT)?.action, 'revise');
  reply(CUT);
  assert.notEqual(finalize(CUT)?.retry?.idempotencyKey, RECOVERY_KEY, 'never a second output-limit pass');
  const verification = guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status, 'failed');
  assert.match(verification.text, /output limit/);
  assert.ok(verification.text.startsWith(OUTPUT_LIMIT_UNRECOVERED_TEXT));
});

test('delivery reports the cut reply even when OpenClaw skips before_agent_finalize', () => {
  // OpenClaw 2026.6.33 treats a length-stopped reply with no other output as an
  // incomplete terminal turn and does not run before_agent_finalize for it.
  const {guard, context, reply} = turn({prompt: PROMPT});
  reply(CUT);
  const verification = guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status, 'failed');
  assert.ok(verification.text.startsWith(OUTPUT_LIMIT_UNRECOVERED_TEXT));
});

test('a later complete reply clears the output-limit report', () => {
  const {guard, context, reply, finalize} = turn();
  reply(CUT);
  finalize(CUT);
  reply(CUT);
  finalize(CUT);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status, 'failed');
  reply(DONE);
  assert.notEqual(guard.deliveryVerificationForRun(context.runId).status, 'failed');
});

test('complete replies, and heartbeat, cron and non-owner turns, are unchanged', () => {
  const complete = turn();
  complete.reply(DONE);
  assert.equal(complete.finalize(DONE), undefined);
  assert.notEqual(complete.guard.deliveryVerificationForRun(complete.context.runId).status, 'failed');
  for (const options of [{trigger: 'heartbeat'}, {trigger: 'cron'}, {sessionKey: 'agent:pixel:main'},
    {prompt: "You are the Builder in the owner's Portal team. Implement the plan."}]) {
    const {reply, finalize} = turn(options);
    reply(CUT);
    assert.notEqual(finalize(CUT)?.retry?.idempotencyKey, RECOVERY_KEY, JSON.stringify(options));
  }
});
