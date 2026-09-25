// Run binding across an owner cancel. tower1, main 53b8a9b5, round 067,
// cancel_and_recovery, Qwen3.5-27B, chat qualification-cancel-aae2bf3fa27b4c21
// (session 1005f154, 12:51:32-12:52:03 UTC):
//   A  "Research ten current Philadelphia event calendars..." was cancelled
//      1.6 s in, before any model output or tool call.
//   B  "Reply with exactly RECOVERED-FLEET-fcccdbf3cd" then saw A unanswered
//      in its transcript. The model ran A's research inside B (3 searches,
//      4 page reads), compacted on overflow, and answered B's literal
//      correctly. Completion assurance then revised that literal answer
//      because it cited none of the (withdrawn request's) web evidence, and
//      the revision replaced it with "I need to revise the Philadelphia
//      events research with proper source attribution...".
import test from 'node:test';
import assert from 'node:assert/strict';
import {CLIENT_CANCELLED_REASON, OWNER_CANCELLED_REQUEST_CONTEXT, createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {UNREAD_SOURCES_REVISION_INSTRUCTION, createCompletionAssurance} from '../plugin/completion-assurance.mjs';
import {createHostCitationVerifier} from '../plugin/citation-verification.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const USER = 'ods-8fdca12a729a5900e645a58978df94a34a5bd409a0cd7e613b69110761351d22';
const SESSION_KEY = `agent:pixel:openai-user:${USER}`;
const SESSION_ID = '1005f154-7276-41c0-92d6-3bdae5548fb4';
const RUN_A = 'chatcmpl_54f8a920-481f-4587-b4db-30462b55599c';
const RUN_B = 'chatcmpl_62408c05-1a07-40f5-aaad-5418161fb35d';
const RUN_C = 'chatcmpl_7c1b0e55-0000-4000-8000-000000000067';
const PORTAL = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, " +
  'copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const RESEARCH = 'Research ten current Philadelphia event calendars thoroughly and compare their upcoming events. Do not write files.';
const LITERAL = 'RECOVERED-FLEET-fcccdbf3cd';
const RECOVERY = `Reply with exactly ${LITERAL}`;
const FLEET_REVISION = 'The research answer is missing source attribution.';
const REVISION_PREFIX = 'Before accepting the previous final answer, apply this revision request and produce the revised final answer. ' +
  'Do not repeat completed work or rerun tools unless the request explicitly requires it.';

// Run B's calls and returned leads, from the session transcript and SSE frames.
const SEARCHES = [
  ['Philadelphia event calendars 2026 September October upcoming events', ['https://www.espn.com/soccer/team/fixtures/_/id/10739/league/usa.1/philadelphia-union',
    'https://cafe.hardrock.com/philadelphia/event-calendar.aspx?display=list', 'https://www.metrophiladelphia.com/calendar']],
  ['Philly events calendar September 2026 things to do', ['https://www.nbcphiladelphia.com/250-america-anniversary/budget-friendly-guide-to-philly-fun-this-summer/4415194',
    'https://www.lincolnfinancialfield.com/events/month/2026-09', 'https://www.metrophiladelphia.com/calendar']],
  ['Philadelphia attractions events this weekend September 2026', ['https://www.visitphilly.com/uwishunu/things-to-do-in-philadelphia-this-week-weekend',
    'https://www.metrophiladelphia.com/calendar/day/when/2026-09-27/', 'https://www.visitphilly.com/events']],
];
const READS = [
  ['https://www.visitphilly.com/events', 'https://www.visitphilly.com/events/', 'Events in Philadelphia | Visit Philadelphia'],
  ['https://www.metrophiladelphia.com/calendar', 'https://www.metrophiladelphia.com/calendar/', 'Calendar - Metro Philadelphia'],
  ['https://billypenn.com/2026/09/14/philly-weekly-events-calendar-september-14-to-20/',
    'https://billypenn.com/2026/09/14/philly-weekly-events-calendar-september-14-to-20/', 'Philly weekly events calendar: September 14 to 20'],
  ['https://whyy.org/articles/things-to-do-september-2026-weekend-3/', 'https://whyy.org/articles/things-to-do-september-2026-weekend-3/',
    'Things to do around Philadelphia: Boyz II Men, Porchfest - WHYY'],
];
const searchResult = (query, urls) => ({content: [{type: 'text', text: JSON.stringify({query, results: urls.map(url => ({url, title: url}))})}],
  details: {results: urls.map(url => ({url}))}});
const pageResult = (url, finalUrl, title) => ({content: [{type: 'text', text: `Fetched ${finalUrl}`}],
  details: {status: 200, url, finalUrl, title, text: `Upcoming events listed on ${finalUrl}`}});

function context(runId, extra = {}) {
  return {agentId: 'pixel', runId, sessionId: SESSION_ID, sessionKey: SESSION_KEY, trigger: 'user', ...extra};
}

function fleetGuard({verify} = {}) {
  const drains = [];
  const verifications = [];
  const guard = createToolLoopGuard({
    abortRunAndDrain: async (sessionId, sessionKey) => { drains.push([sessionId, sessionKey]); return {aborted: true, drained: true}; },
    hostCitationVerifier: {allowed: () => true, async verify(request) {
      verifications.push(request);
      return verify ? verify(request) : {fetched: request.urls.length, verified: [], elapsedMs: 1};
    }},
  });
  let calls = 0;
  const call = (runId, toolName, params, result, extra) => {
    const toolCallId = `${runId}:call:${++calls}`;
    const ctx = {...context(runId, extra), toolName, toolCallId};
    const decision = guard.beforeToolCall({toolName, toolCallId, params}, ctx);
    if (!decision?.block) guard.afterToolCall({toolName, toolCallId, params: decision?.params ?? params, result}, ctx);
    return decision;
  };
  // OpenClaw's before_agent_finalize order in index.js.
  const finalize = async (runId, lastAssistantMessage, extra) => {
    const ctx = context(runId, extra);
    await guard.verifyCitedPages({lastAssistantMessage}, ctx);
    return guard.beforeAgentFinalize({lastAssistantMessage}, ctx);
  };
  const research = (runId, extra) => {
    const decisions = [];
    for (const [query, urls] of SEARCHES) decisions.push(call(runId, 'web_search', {query}, searchResult(query, urls), extra));
    for (const [url, finalUrl, title] of READS) decisions.push(call(runId, 'web_fetch', {url}, pageResult(url, finalUrl, title), extra));
    return decisions;
  };
  return {guard, drains, verifications, call, finalize, research};
}

const userMessage = text => ({role: 'user', content: [{type: 'text', text}]});

test('round 067 replay: after the cancel, the literal reply is delivered unrevised even though the model ran the withdrawn research', async () => {
  const {guard, drains, finalize, research} = fleetGuard();
  // 12:51:32.3 A starts; 12:51:34.0 the owner cancels before any output.
  guard.observeRun(context(RUN_A), 'pixel', {prompt: RESEARCH + PORTAL, messages: []});
  assert.equal(guard.promptContextForRun(RUN_A), undefined);
  assert.equal(await guard.abortUserRun(USER), true);
  assert.deepEqual(drains, [[SESSION_ID, SESSION_KEY]]);

  // 12:51:35.3 B starts. OpenClaw drops A's empty aborted reply, so the
  // model sees A's request unanswered right before B's.
  guard.observeRun(context(RUN_B), 'pixel', {prompt: RECOVERY + PORTAL, messages: [userMessage(RESEARCH + PORTAL)]});
  assert.equal(guard.promptContextForRun(RUN_B), OWNER_CANCELLED_REQUEST_CONTEXT, 'B is told A is withdrawn');
  assert.equal(guard.promptContextForRun(RUN_A), CLIENT_CANCELLED_REASON, 'a retry attempt of A would be told to stop');

  // The fleet model ignored that and researched A inside B anyway. B is not
  // cancelled: none of its calls is refused as a cancelled run's call.
  assert.deepEqual(research(RUN_B).filter(decision => decision?.block), []);
  guard.observeCompaction(context(RUN_B), 'start');
  guard.observeCompaction(context(RUN_B), 'end');

  // 12:51:59.5 the literal answer. Before this fix: revise, "The research
  // answer is missing source attribution.", and the revision replaced it.
  const decision = await finalize(RUN_B, LITERAL);
  assert.equal(decision, undefined, JSON.stringify(decision));
  const delivery = guard.deliveryVerificationForRun(RUN_B);
  assert.equal(delivery.status, 'none');
  assert.equal(delivery.text, undefined);
  assert.equal(guard.replyPayloadSending({runId: RUN_B, kind: 'final', payload: {text: LITERAL}}), undefined);

  // The notice belongs to B alone: the next owner message starts clean.
  guard.observeRun(context(RUN_C), 'pixel', {prompt: 'Thanks' + PORTAL, messages: []});
  assert.equal(guard.promptContextForRun(RUN_C), undefined);
});

test('round 067 replay: a model that follows the notice answers the conversational request with no tools and no revision', async () => {
  const {guard, finalize, call} = fleetGuard();
  guard.observeRun(context(RUN_A), 'pixel', {prompt: RESEARCH + PORTAL, messages: []});
  assert.equal(await guard.abortUserRun(USER), true);
  guard.observeRun(context(RUN_B), 'pixel', {prompt: RECOVERY + PORTAL, messages: [userMessage(RESEARCH + PORTAL)]});
  assert.equal(guard.promptContextForRun(RUN_B), OWNER_CANCELLED_REQUEST_CONTEXT);
  assert.equal(await finalize(RUN_B, LITERAL), undefined);
  assert.deepEqual(guard.deliveryVerificationForRun(RUN_B), {status: 'none'});
  // Tools stay available to B; only the cancelled run is fenced.
  assert.equal(call(RUN_B, 'read', {path: 'notes.md'}, {content: [{type: 'text', text: 'ok'}]})?.blockReason, undefined);
  assert.deepEqual(call(RUN_A, 'read', {path: 'notes.md'}, {}), {block: true, blockReason: CLIENT_CANCELLED_REASON});
});

// Race: the cancel lands before, during and after run A arms its revision.
// A requested source reads, so its finalization first waits on the #6680 host
// read of an unread cited page; the revision is armed only after that.
const READS_REQUEST = 'Research ten current Philadelphia event calendars, open and read their pages, and compare their upcoming events. Do not write files.';
const UNREAD = 'https://www.phillyfunguide.com/events/2026/09/porchfest';
const A_ANSWER = `Porchfest runs on September 27, 2026 across West Philadelphia (${UNREAD}). ` +
  `Boyz II Men play the Mann Center on September 26, 2026 (${READS[3][0]}).`;

for (const phase of ['before', 'during', 'after']) {
  test(`cancel ${phase} the revision is armed: nothing of run A reaches the next request`, async () => {
    let signal, readStarted;
    const started = new Promise(resolve => { readStarted = resolve; });
    const {guard, verifications, finalize, research, call} = fleetGuard({verify: request => {
      signal = request.signal;
      readStarted();
      if (phase !== 'during') return {fetched: request.urls.length, verified: [], elapsedMs: 1};
      // A slow read that ends only when the cancel aborts it.
      return new Promise(resolve => request.signal.addEventListener('abort',
        () => resolve({fetched: request.urls.length, verified: [], skipped: 'cancelled', elapsedMs: 1}), {once: true}));
    }});
    guard.observeRun(context(RUN_A), 'pixel', {prompt: READS_REQUEST + PORTAL, messages: []});
    research(RUN_A);

    let decisionA;
    if (phase === 'before') {
      assert.equal(await guard.abortUserRun(USER), true);
      decisionA = await finalize(RUN_A, A_ANSWER);
      assert.equal(verifications.length, 0, 'a cancelled run starts no host read');
    } else if (phase === 'during') {
      const pending = finalize(RUN_A, A_ANSWER);
      await started;
      assert.equal(signal.aborted, false);
      assert.equal(await guard.abortUserRun(USER), true);
      assert.equal(signal.aborted, true, 'the cancel aborts the in-flight host read');
      decisionA = await pending;
    } else {
      const armed = await finalize(RUN_A, A_ANSWER);
      assert.equal(armed?.action, 'revise');
      assert.equal(armed.retry.idempotencyKey, 'ods-opened-source-attribution');
      assert.ok(guard.deliveryVerificationForRun(RUN_A).text, 'armed: a replacement text waits for a refused revision');
      assert.equal(await guard.abortUserRun(USER), true);
      assert.equal(signal.aborted, false, 'a finished host read is not touched');
      // OpenClaw had already accepted the revision: its retry attempt of A.
      const retry = `${REVISION_PREFIX}\n\n${armed.reason}\n\n${armed.retry.instruction}`;
      guard.observeRun(context(RUN_A), 'pixel', {prompt: retry, messages: [userMessage(READS_REQUEST + PORTAL)]});
      assert.equal(guard.promptContextForRun(RUN_A), CLIENT_CANCELLED_REASON);
      assert.deepEqual(call(RUN_A, 'web_fetch', {url: UNREAD}, {}), {block: true, blockReason: CLIENT_CANCELLED_REASON});
      decisionA = await finalize(RUN_A, 'Revised answer.');
    }
    assert.equal(decisionA, undefined, `${phase}: no revision after the cancel`);
    assert.equal(guard.deliveryVerificationForRun(RUN_A).text, undefined, `${phase}: no armed replacement text survives`);

    guard.observeRun(context(RUN_B), 'pixel', {prompt: RECOVERY + PORTAL, messages: [userMessage(READS_REQUEST + PORTAL)]});
    assert.equal(guard.promptContextForRun(RUN_B), OWNER_CANCELLED_REQUEST_CONTEXT, `${phase}: the retry of A did not take B's notice`);
    const decisionB = await finalize(RUN_B, LITERAL);
    assert.equal(decisionB, undefined, `${phase}: ${JSON.stringify(decisionB)}`);
    assert.deepEqual(guard.deliveryVerificationForRun(RUN_B), {status: 'none'});
    assert.ok(!JSON.stringify(decisionB ?? {}).includes(UNREAD_SOURCES_REVISION_INSTRUCTION[1]));
    assert.equal(call(RUN_B, 'read', {path: 'notes.md'}, {content: [{type: 'text', text: 'ok'}]})?.block, undefined);
    assert.equal(verifications.filter(request => request.answer === LITERAL).length, 0);
  });
}

test('a resend of the cancelled request, or a new research request, still gets the attribution revision', async () => {
  for (const [name, prompt] of [['resend', RESEARCH], ['new research', 'Search the web for Philadelphia events this weekend and cite sources.']]) {
    const {guard, finalize, research} = fleetGuard();
    guard.observeRun(context(RUN_A), 'pixel', {prompt: RESEARCH + PORTAL, messages: []});
    assert.equal(await guard.abortUserRun(USER), true);
    guard.observeRun(context(RUN_B), 'pixel', {prompt: prompt + PORTAL, messages: [userMessage(RESEARCH + PORTAL)]});
    assert.equal(guard.promptContextForRun(RUN_B), OWNER_CANCELLED_REQUEST_CONTEXT, name);
    research(RUN_B);
    const decision = await finalize(RUN_B, 'Porchfest and Boyz II Men headline this weekend in Philadelphia.');
    assert.equal(decision?.action, 'revise', name);
    assert.equal(decision.reason, FLEET_REVISION, name);
  }
});

test('without a cancel, research evidence still binds a later answer in the same run', async () => {
  const {guard, finalize, research} = fleetGuard();
  guard.observeRun(context(RUN_B), 'pixel', {prompt: RECOVERY + PORTAL, messages: [userMessage(RESEARCH + PORTAL)]});
  assert.equal(guard.promptContextForRun(RUN_B), undefined);
  research(RUN_B);
  // Unchanged behavior for an orphan that was not cancelled by the owner.
  assert.equal((await finalize(RUN_B, LITERAL))?.reason, FLEET_REVISION);
});

test('only an acknowledged cancel, and only an owner turn, takes the withdrawal notice', async () => {
  const refused = createToolLoopGuard({abortRunAndDrain: async () => ({aborted: false})});
  refused.observeRun(context(RUN_A), 'pixel', {prompt: RESEARCH + PORTAL});
  assert.equal(await refused.abortUserRun(USER), false);
  refused.observeRun(context(RUN_B), 'pixel', {prompt: RECOVERY + PORTAL});
  assert.equal(refused.promptContextForRun(RUN_B), undefined, 'an unacknowledged cancel withdraws nothing');

  const {guard} = fleetGuard();
  guard.observeRun(context(RUN_A), 'pixel', {prompt: RESEARCH + PORTAL});
  assert.equal(await guard.abortUserRun(USER), true);
  guard.observeRun(context(RUN_B, {trigger: 'heartbeat'}), 'pixel', {prompt: 'heartbeat'});
  assert.equal(guard.promptContextForRun(RUN_B), undefined, 'a non-owner run neither sees nor takes it');
  guard.observeRun(context(RUN_C), 'pixel', {prompt: RECOVERY + PORTAL});
  assert.equal(guard.promptContextForRun(RUN_C), OWNER_CANCELLED_REQUEST_CONTEXT);
});

test('completion assurance: a cancel voids an armed revision and replacement; a withdrawn request does not bind evidence', () => {
  const research = searchResult(...SEARCHES[0]);
  const cancelled = createCompletionAssurance();
  cancelled.begin('Search the web for Philadelphia events and cite sources.');
  cancelled.observe('web_search', {result: research});
  assert.equal(cancelled.finalize('Porchfest is on Sunday.')?.action, 'revise');
  assert.ok(cancelled.terminal);
  cancelled.cancel();
  assert.equal(cancelled.terminal, undefined);
  assert.equal(cancelled.finalize('Porchfest is on Sunday.'), undefined);
  assert.equal(cancelled.hostVerificationCandidates(`See ${UNREAD}`), undefined);

  const following = createCompletionAssurance();
  following.begin(RECOVERY);
  following.followWithdrawnRequest(RESEARCH);
  following.observe('web_search', {result: research});
  assert.equal(following.finalize(LITERAL), undefined);
  assert.equal(following.terminal, undefined);
  // A promise without the work is still the current request's own obligation.
  assert.equal(following.finalize('I will search for the events now.')?.action, 'revise');
});

test('#6680: an aborted signal ends the host reads at once and verifies nothing', async () => {
  const reads = [];
  const verifier = createHostCitationVerifier({limits: {budgetMs: 60000},
    readPage: (url, {signal}) => new Promise(resolve => { reads.push(signal); signal.addEventListener('abort', () => resolve({ok: false, reason: 'aborted'})); })});
  const controller = new AbortController();
  const answer = 'Boyz II Men play the Mann Center on September 26, 2026 (https://www.manncenter.org/events/boyz-ii-men).';
  const pending = verifier.verify({answer, urls: ['https://www.manncenter.org/events/boyz-ii-men'], signal: controller.signal});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reads.length, 1);
  controller.abort();
  const outcome = await pending;
  assert.equal(outcome.skipped, 'cancelled');
  assert.deepEqual(outcome.verified, []);
  assert.equal(reads[0].aborted, true);
  assert.equal((await verifier.verify({answer, urls: ['https://www.manncenter.org/events/boyz-ii-men'], signal: controller.signal})).fetched, 0,
    'an already cancelled run reads nothing');
});

// #6683: the tool-limit partial answer is bound through the session key. With
// a rotated session ID, the cancelled older run must not take the new run's
// transcript messages.
test('#6683: a partial answer binds to the newest run of the chat, never to the cancelled one', async () => {
  const guard = createToolLoopGuard({abortRun: () => true, abortRunAndDrain: async () => ({aborted: true})});
  guard.observeRun(context(RUN_A, {sessionId: 'session-before-rotation'}), 'pixel', {prompt: RESEARCH + PORTAL});
  assert.equal(await guard.abortUserRun(USER), true);
  const ctx = context(RUN_B, {sessionId: 'session-after-rotation'});
  guard.observeRun(ctx, 'pixel', {prompt: RESEARCH + PORTAL});
  let calls = 0;
  const round = (toolCalls, text = '') => {
    guard.observeModelCall({callId: `m${++calls}`}, ctx);
    guard.observeModelEnd({callId: `m${calls}`}, ctx);
    const ids = toolCalls.map((_, index) => `b-${calls}-${index}`);
    guard.observeAssistantMessage({message: {role: 'assistant', content: [...(text ? [{type: 'text', text}] : []),
      ...toolCalls.map(([name, params], index) => ({type: 'toolCall', id: ids[index], name, arguments: params}))]}},
    {agentId: 'pixel', sessionKey: SESSION_KEY});
    return toolCalls.map(([toolName, params, result], index) => {
      const toolCallId = ids[index];
      const decision = guard.beforeToolCall({toolName, toolCallId, params}, {...ctx, toolName, toolCallId});
      const outcome = decision?.block ? {isError: true, content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', reason: decision.blockReason}} : result;
      guard.afterToolCall({toolName, toolCallId, params, result: outcome, ...(outcome.isError ? {error: 'failed'} : {})},
        {...ctx, toolName, toolCallId});
      return decision;
    });
  };
  const [url, finalUrl, title] = READS[0];
  round([['web_fetch', {url}, pageResult(url, finalUrl, title)]]);
  for (let index = 0; index < RUN_PROGRESS_LIMITS.consecutiveFailures; index++) {
    round([['web_fetch', {url: `${READS[1][0]}?${index}`}, {isError: true, details: {status: 'error', error: 'Web fetch failed (403)'}}]]);
  }
  round([['web_search', {query: 'x'}, searchResult('x', [])]]);
  const answer = 'From the pages read before the limit:\n\n- Visit Philadelphia lists Porchfest on September 27, 2026 in West Philadelphia.\n' +
    '- Metro Philadelphia could not be read (403), so its calendar is not compared here.\n\nLet me search once more:';
  round([['web_search', {query: 'y'}, searchResult('y', [])]], answer);
  await guard.settleDelivery(RUN_B);
  assert.ok(guard.deliveryVerificationForRun(RUN_B).text?.startsWith(answer.trim()), guard.deliveryVerificationForRun(RUN_B).text);
});
