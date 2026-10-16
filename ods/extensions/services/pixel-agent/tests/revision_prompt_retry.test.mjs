// A before_agent_finalize revision that never reached the model. Fleet event
// search, rounds 105-112, OpenClaw 2026.6.33: the first answer cited a page it
// never read, and completion assurance asked for one source revision. OpenClaw
// sends a revision as the next attempt's prompt, but that attempt's pre-prompt
// context check overflowed (the chat already held the website and coding
// journeys). After compaction or tool-result truncation, OpenClaw retried with
// the owner's original message, not the revision. The model answered the same
// request again with the same unread citation, the one source revision was
// spent, and the answer was delivered with the link neutralised as failed.
// Seen in three runs (rounds 105, 108 and 111).
import test from 'node:test';
import assert from 'node:assert/strict';
import {CLIENT_CANCELLED_REASON, OWNER_CANCELLED_REQUEST_CONTEXT, RESUBMITTED_REVISION_CONTEXT,
  createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {UNREAD_SOURCES_REVISION_INSTRUCTION} from '../plugin/completion-assurance.mjs';
import {OWNER_VISIBLE_REPLY_INSTRUCTION, OWNER_VISIBLE_REPLY_REASON} from '../plugin/owner-visible-reply.mjs';

const USER = 'ods-' + '7'.repeat(64);
const SESSION_KEY = `agent:pixel:openai-user:${USER}`;
const PORTAL = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, " +
  'copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const OWNER = 'Today is 2026-09-26. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL. ' +
  'Exclude undated listings and past events. Explain any unavailable result honestly. Do not create files.' + PORTAL;
// OpenClaw's revision prompt is this prefix, a blank line, then the reason and
// the retry instruction (embedded-agent buildBeforeAgentFinalizeRetryPrompt).
const OPENCLAW_REVISION_PREFIX = 'Before accepting the previous final answer, apply this revision request and produce the revised final answer. ' +
  'Do not repeat completed work or rerun tools unless the request explicitly requires it.';
const ACDC = 'https://www.lincolnfinancialfield.com/events/ac-dc-power-up-tour-2026/';
const JOURNEY = 'https://www.xfinitymobilearena.com/events/detail/journey-10-28-26';
const LFF_LIST = 'https://www.lincolnfinancialfield.com/events/list/?tribe-bar-date=2026-10-18';
// Round 111: the cited pages, as their receipts stood when the answer was judged.
const FIRST_ANSWER = 'Three verified public events in Philadelphia within the next 45 days:\n\n' +
  `1. AC/DC Power Up Tour 2026 - Tuesday, September 29, 2026 - Lincoln Financial Field - ${ACDC}\n` +
  `2. Journey - Final Frontier Tour - Wednesday, October 28, 2026 at 7:30 PM - Xfinity Mobile Arena - ${JOURNEY}\n` +
  `3. Philadelphia Eagles vs. Dallas Cowboys - Monday, October 26, 2026 at 8:15 PM - Lincoln Financial Field - ${LFF_LIST}`;
const REVISED_ANSWER = FIRST_ANSWER.replace(ACDC, '(source not verified: the event page was not read)');
const SOURCE_REVISION = `Cited pages lack current-turn read receipts.\n\n${UNREAD_SOURCES_REVISION_INSTRUCTION.join(JSON.stringify([ACDC]))}`;

const context = (runId, extra = {}) => ({agentId: 'pixel', runId, sessionId: 'session-evs', sessionKey: SESSION_KEY, trigger: 'user', ...extra});

function fleetRun(runId = 'chatcmpl_event-search-run') {
  const guard = createToolLoopGuard({abortRun: () => true, abortRunAndDrain: async () => ({aborted: true, drained: true})});
  let calls = 0;
  const call = (toolName, params, result) => {
    const toolCallId = `${runId}:call:${++calls}`;
    const ctx = {...context(runId), toolName, toolCallId};
    guard.beforeToolCall({toolName, toolCallId, params}, ctx);
    guard.afterToolCall({toolName, toolCallId, params, result}, ctx);
  };
  const attempt = prompt => { guard.observeRun(context(runId), 'pixel', {prompt}); return guard.promptContextForRun(runId); };
  const finalize = lastAssistantMessage => guard.beforeAgentFinalize({lastAssistantMessage}, context(runId));
  return {guard, runId, call, attempt, finalize};
}

function researched(run) {
  run.call('web_search', {query: 'Philadelphia events October 2026'}, {content: [{type: 'text', text: '{}'}],
    details: {results: [{url: ACDC}, {url: 'https://www.lincolnfinancialfield.com/'}]}});
  for (const url of [JOURNEY, LFF_LIST]) run.call('web_fetch', {url}, {content: [{type: 'text', text: `Fetched ${url}`}],
    details: {status: 200, url, finalUrl: url, text: `Event listing from ${url}`}});
}

test('fleet replay (round 111): the owner message resent after the revision attempt overflowed carries that revision', () => {
  const run = fleetRun();
  assert.equal(run.attempt(OWNER), undefined, 'the first attempt carries no model-only context');
  researched(run);
  const revision = run.finalize(FIRST_ANSWER);
  assert.equal(revision?.action, 'revise');
  assert.equal(revision.retry.idempotencyKey, 'ods-opened-source-attribution');
  // The revision attempt: OpenClaw's own prompt already carries the revision.
  assert.equal(run.attempt(`${OPENCLAW_REVISION_PREFIX}\n\n${SOURCE_REVISION}`), undefined);
  // It stopped at the pre-prompt context check; after compaction or tool-result
  // truncation OpenClaw resent the owner's message instead. That attempt
  // carries exactly what followed OpenClaw's prefix in the lost revision.
  assert.equal(run.attempt(OWNER), `${RESUBMITTED_REVISION_CONTEXT}\n\n${SOURCE_REVISION}`);
  // A second overflow and resend carries it again: it is still unanswered.
  assert.equal(run.attempt(OWNER), `${RESUBMITTED_REVISION_CONTEXT}\n\n${SOURCE_REVISION}`);

  // The next final answer is judged against the same bounded budget: no second
  // source revision, and nothing is carried after it.
  assert.equal(run.finalize(REVISED_ANSWER), undefined);
  assert.equal(run.guard.deliveryVerificationForRun(run.runId).status, 'none');
  assert.equal(run.attempt(OWNER), undefined);
});

test('a resent owner message with a retry note OpenClaw appends carries the unanswered revision', () => {
  const run = fleetRun();
  run.attempt(OWNER);
  researched(run);
  assert.equal(run.finalize(FIRST_ANSWER)?.action, 'revise');
  // An empty or compacted revision attempt: OpenClaw retries with the owner's
  // message and its own continuation note.
  const note = 'The previous attempt compacted the conversation context before producing a final user-visible answer. ' +
    'Continue from the compacted transcript and produce the final answer now.';
  assert.equal(run.attempt(`${OWNER}\n\n${note}`), `${RESUBMITTED_REVISION_CONTEXT}\n\n${SOURCE_REVISION}`);
});

test('the same unread answer after a carried revision is still judged once: finalized with the link neutralised', () => {
  const run = fleetRun();
  run.attempt(OWNER);
  researched(run);
  assert.equal(run.finalize(FIRST_ANSWER)?.action, 'revise');
  run.attempt(OWNER);
  // A model that ignores the carried revision gets no further pass.
  assert.equal(run.finalize(FIRST_ANSWER)?.action, 'finalize');
  const delivery = run.guard.deliveryVerificationForRun(run.runId);
  assert.equal(delivery.status, 'failed');
  assert.ok(!delivery.text.includes(ACDC) && delivery.text.includes(JOURNEY));
  assert.equal(run.attempt(OWNER), undefined);
});

test('every revision kind is carried the same way: a silent owner reply', () => {
  const prompt = 'What did we decide about the dashboard reload?' + PORTAL;
  const run = fleetRun('silent-run');
  run.attempt(prompt);
  assert.equal(run.finalize('NO_REPLY')?.action, 'revise');
  assert.equal(run.attempt(prompt), `${RESUBMITTED_REVISION_CONTEXT}\n\n${OWNER_VISIBLE_REPLY_REASON}\n\n${OWNER_VISIBLE_REPLY_INSTRUCTION}`);
});

test('no revision, another run, or another message: nothing is carried', () => {
  const run = fleetRun();
  run.attempt(OWNER);
  researched(run);
  assert.equal(run.finalize(FIRST_ANSWER.replace(ACDC, JOURNEY)), undefined, 'every cited page was read');
  assert.equal(run.attempt(OWNER), undefined);

  const armed = fleetRun('armed-run');
  armed.attempt(OWNER);
  researched(armed);
  assert.equal(armed.finalize(FIRST_ANSWER)?.action, 'revise');
  // A different owner message is never the same run's resend.
  assert.equal(armed.attempt('Thanks, that is all.' + PORTAL), undefined);
  // Another run with the same text has its own state.
  armed.guard.observeRun(context('next-run'), 'pixel', {prompt: OWNER});
  assert.equal(armed.guard.promptContextForRun('next-run'), undefined);
});

test('a cancelled run is only told to stop; a withdrawn earlier request is noted before the revision', async () => {
  const run = fleetRun();
  run.attempt(OWNER);
  researched(run);
  assert.equal(run.finalize(FIRST_ANSWER)?.action, 'revise');
  assert.equal(await run.guard.abortUserRun(USER), true);
  assert.equal(run.attempt(OWNER), CLIENT_CANCELLED_REASON);

  // The next run in the chat follows that cancel, then loses its own revision.
  const next = 'chatcmpl_next-after-cancel';
  const attempt = () => { run.guard.observeRun(context(next), 'pixel', {prompt: OWNER}); return run.guard.promptContextForRun(next); };
  assert.equal(attempt(), OWNER_CANCELLED_REQUEST_CONTEXT);
  for (const url of [JOURNEY, LFF_LIST]) {
    const toolCallId = `${next}:${url}`;
    const ctx = {...context(next), toolName: 'web_fetch', toolCallId};
    run.guard.afterToolCall({toolName: 'web_fetch', toolCallId, params: {url},
      result: {content: [{type: 'text', text: 'x'}], details: {status: 200, url, finalUrl: url, text: 'x'}}}, ctx);
  }
  assert.equal(run.guard.beforeAgentFinalize({lastAssistantMessage: FIRST_ANSWER}, context(next))?.action, 'revise');
  assert.equal(attempt(), `${OWNER_CANCELLED_REQUEST_CONTEXT}\n\n${RESUBMITTED_REVISION_CONTEXT}\n\n${SOURCE_REVISION}`);
});
