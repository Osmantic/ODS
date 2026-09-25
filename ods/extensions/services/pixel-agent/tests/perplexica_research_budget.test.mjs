// pixel_ods_research in the loop guard and completion assurance: its web
// allowance cost, its one-call-per-response limit, and the rule that nothing
// Perplexica returns counts as a page Pixel read.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, PERPLEXICA_REPEAT_REASON, WEB_BUDGET_EXHAUSTED_REASON,
  WEB_FETCH_BUDGET_EXHAUSTED_REASON, WEB_SEARCH_BUDGET_EXHAUSTED_REASON} from '../plugin/tool-loop-guard.mjs';
import {createPerplexicaResearchTool, RESEARCH_ADDRESS_ONLY_HINT, RESEARCH_REQUEST_HINT,
  UNSOURCED_LINK_MARKER} from '../plugin/perplexica-research.mjs';
import {UNREAD_SOURCE_MARKER, UNREAD_SOURCE_NOTE, UNREAD_SOURCES_REVISION_INSTRUCTION} from '../plugin/completion-assurance.mjs';
import {SEARCH_PACING_REASON, SEARCH_PACING_STREAK} from '../plugin/research-pacing.mjs';
import {PERPLEXICA_MEASURED_RUNS} from './fixtures/perplexica-answers.mjs';

const base = {agentId: 'pixel', runId: 'research-run', sessionId: 'research-session', sessionKey: 'agent:pixel:research'};
const envelopeTool = name => ({id: `openclaw:${name === 'pixel_ods_research' ? 'pixel-ods' : 'core'}:${name}`,
  source: 'openclaw', sourceName: name === 'pixel_ods_research' ? 'pixel-ods' : 'core', name});
let sequence = 0;

function guardFor(prompt = 'Research public sources for this question.', limits) {
  const guard = createToolLoopGuard(limits ? {limits} : {});
  guard.observeRun(base, 'pixel', {prompt});
  return guard;
}

// One web tool call through the real hooks, in the direct or Tool Search
// form. Returns the refusal text, or undefined when the call was admitted.
function before(guard, transport, name, args) {
  const id = `call-${++sequence}`;
  const context = {...base, toolName: transport === 'direct' ? name : 'tool_call', toolCallId: id};
  const params = transport === 'direct' ? args : {id: envelopeTool(name).id, args};
  const outer = guard.beforeToolCall({toolName: context.toolName, params}, context);
  if (outer?.block) return {id, refusal: outer.blockReason};
  if (transport !== 'direct') {
    const nested = guard.beforeToolCall({toolName: name, params: args},
      {...base, toolName: name, toolCallId: `tool_search_code:${id}`});
    if (nested?.block) return {id, refusal: nested.blockReason};
  }
  return {id, params};
}
function after(guard, transport, name, call, result) {
  const context = {...base, toolName: transport === 'direct' ? name : 'tool_call', toolCallId: call.id};
  const event = transport === 'direct' ? {toolName: name, params: call.params, result}
    : {toolName: 'tool_call', params: call.params, result: {content: [{type: 'text', text: JSON.stringify({tool: envelopeTool(name), result})}],
      details: {tool: envelopeTool(name), result}}};
  guard.afterToolCall(event, context);
}
const research = (guard, transport, query = 'Philadelphia public events in October 2026') =>
  before(guard, transport, 'pixel_ods_research', {query}).refusal;
const search = (guard, transport, query) => before(guard, transport, 'web_search', {query}).refusal;
const fetch = (guard, transport, url) => before(guard, transport, 'web_fetch', {url}).refusal;

for (const transport of ['direct', 'tool_call']) {
  test(`${transport}: a research call uses one search and one page-reading unit`, () => {
    let guard = guardFor(undefined, {search: 3, fetch: 10, total: 20});
    assert.equal(research(guard, transport), undefined);
    assert.equal(search(guard, transport, 'first distinct topic'), undefined);
    assert.equal(search(guard, transport, 'second distinct subject'), undefined);
    assert.equal(search(guard, transport, 'third unrelated item'), WEB_SEARCH_BUDGET_EXHAUSTED_REASON);

    guard = guardFor(undefined, {search: 10, fetch: 3, total: 20});
    assert.equal(research(guard, transport), undefined);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/a'), undefined);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/b'), undefined);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/c'), WEB_FETCH_BUDGET_EXHAUSTED_REASON);

    // Two total units are needed; with one left, a single-unit read still runs.
    guard = guardFor(undefined, {search: 10, fetch: 10, total: 3});
    assert.equal(fetch(guard, transport, 'https://docs.example.org/a'), undefined);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/b'), undefined);
    assert.equal(research(guard, transport), WEB_BUDGET_EXHAUSTED_REASON);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/c'), undefined);
  });

  test(`${transport}: research is refused when either allowance is spent`, () => {
    let guard = guardFor(undefined, {search: 1, fetch: 10, total: 20});
    assert.equal(search(guard, transport, 'only search'), undefined);
    assert.equal(research(guard, transport), WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
    assert.match(WEB_SEARCH_BUDGET_EXHAUSTED_REASON, /Do not repeat web_search or pixel_ods_research/);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/a'), undefined, 'page reads remain');

    guard = guardFor(undefined, {search: 10, fetch: 1, total: 20});
    assert.equal(fetch(guard, transport, 'https://docs.example.org/a'), undefined);
    assert.equal(research(guard, transport), WEB_FETCH_BUDGET_EXHAUSTED_REASON);
    assert.match(WEB_FETCH_BUDGET_EXHAUSTED_REASON, /pixel_ods_research/);
    assert.equal(search(guard, transport, 'searches remain'), undefined);
  });

  test(`${transport}: one research call per response; the repeat runs nothing and costs nothing`, () => {
    const guard = guardFor(undefined, {search: 2, fetch: 2, total: 4});
    assert.equal(research(guard, transport), undefined);
    assert.equal(research(guard, transport, 'A different research brief'), PERPLEXICA_REPEAT_REASON);
    // Compaction re-observes the run; the limit stays.
    guard.observeRun(base, 'pixel', {prompt: 'Research public sources for this question.'});
    assert.equal(research(guard, transport, 'Yet another brief'), PERPLEXICA_REPEAT_REASON);
    // The refusals used no allowance: one search and one read remain, for a total of exactly 4.
    assert.equal(search(guard, transport, 'remaining search'), undefined);
    assert.equal(search(guard, transport, 'over the limit'), WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/a'), undefined);
    assert.equal(fetch(guard, transport, 'https://docs.example.org/b'), WEB_BUDGET_EXHAUSTED_REASON);
    // A new response starts with its own research call.
    const next = {...base, runId: 'next-run'};
    guard.observeRun(next, 'pixel', {prompt: 'Research public sources again.'});
    assert.notEqual(guard.beforeToolCall({toolName: 'pixel_ods_research', params: {query: 'Next response brief'}},
      {...next, toolName: 'pixel_ods_research', toolCallId: 'next-1'})?.block, true);
  });

  test(`${transport}: an unusable brief is refused before it is charged`, () => {
    const guard = guardFor(undefined, {search: 1, fetch: 1, total: 2});
    assert.equal(research(guard, transport, 'a'.repeat(1001)), RESEARCH_REQUEST_HINT);
    assert.equal(research(guard, transport, 'http://host.docker.internal:8080/admin'), RESEARCH_ADDRESS_ONLY_HINT);
    assert.equal(before(guard, transport, 'pixel_ods_research', {query: 'Valid words', mode: 'quality'}).refusal, RESEARCH_REQUEST_HINT);
    assert.equal(research(guard, transport), undefined, 'the allowance and the one call are still available');
  });
}

test('parallel Tool Search siblings: only the first nested execution runs', () => {
  const guard = guardFor();
  const outer = id => guard.beforeToolCall({toolName: 'tool_call', params: {id: 'pixel_ods_research', args: {query: `Brief ${id}`}}},
    {...base, toolName: 'tool_call', toolCallId: id});
  const nested = id => guard.beforeToolCall({toolName: 'pixel_ods_research', params: {query: `Brief ${id}`}},
    {...base, toolName: 'pixel_ods_research', toolCallId: `tool_search_code:${id}`});
  assert.notEqual(outer('a')?.block, true);
  assert.notEqual(outer('b')?.block, true);
  assert.notEqual(nested('a')?.block, true);
  assert.equal(nested('b')?.blockReason, PERPLEXICA_REPEAT_REASON);
});

// A web_search with its persisted receipt, as research_pacing.test.mjs drives it.
function pacedSearch(guard, id, query) {
  const context = {...base, toolName: 'web_search', toolCallId: id};
  const decision = guard.beforeToolCall({toolName: 'web_search', params: {query}}, context);
  if (decision?.block) return decision.blockReason;
  const details = {provider: 'test', results: [{title: 'Source', url: `https://source.example/${id}`, description: 'Lead'}], externalContent: {untrusted: true}};
  guard.afterToolCall({toolName: 'web_search', params: {query}, result: {content: [{type: 'text', text: JSON.stringify(details)}], details}}, context);
  guard.toolResultPersist({message: {role: 'toolResult', toolName: 'web_search', toolCallId: id}}, context);
  return undefined;
}

test('research reads no page, so it does not end an unread-search streak', () => {
  for (const [between, paused] of [['pixel_ods_research', true], ['web_fetch', false]]) {
    const guard = guardFor(undefined, {search: 20, fetch: 10, total: 40});
    for (let i = 1; i <= SEARCH_PACING_STREAK; i++) assert.equal(pacedSearch(guard, `s${i}`, `distinct topic ${'abcdefgh'[i]} lookup`), undefined);
    const params = between === 'web_fetch' ? {url: 'https://docs.example.org/page'} : {query: 'Philadelphia public events'};
    assert.notEqual(guard.beforeToolCall({toolName: between, params}, {...base, toolName: between, toolCallId: `between-${between}`})?.block, true);
    assert.equal(pacedSearch(guard, 'next', 'another distinct subject query'), paused ? SEARCH_PACING_REASON : undefined, between);
  }
});

// The tower1/tower2 event-search journey prompt: sources must be opened.
const EVENT_PROMPT = 'Today is 2026-09-25. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL.';
const MEASURED = PERPLEXICA_MEASURED_RUNS.find(run => run.run === 'new-speed-event-2');
const SOURCED = 'https://www.visitphilly.com/events/';
const INVENTED = 'https://www.constitutioncenter.org/events/philly-korean-festival-2026';

async function measuredResearchResult() {
  let calls = 0;
  const tool = createPerplexicaResearchTool({env: {}, fetch: async () => ++calls === 1
    ? Response.json({values: {preferences: {defaultChatProvider: 'c', defaultChatModel: 'm', defaultEmbeddingProvider: 'e', defaultEmbeddingModel: 'x'}}})
    : new Response([{type: 'sources', data: MEASURED.sources.map(url => ({content: 'Search snippet.', metadata: {title: 'Listing', url}}))},
      {type: 'response', data: MEASURED.answer}, {type: 'done'}].map(event => JSON.stringify(event)).join('\n'))});
  return tool.execute('research', {query: 'Philadelphia public events in the next 45 days'}, new AbortController().signal);
}

for (const transport of ['direct', 'tool_call']) {
  test(`${transport} replay: Perplexica links are flagged and never count as pages read`, async () => {
    const result = await measuredResearchResult();
    const text = result.content[0].text;
    // Invented links are replaced before the model sees them; its source link stays, marked unread.
    assert.equal(result.details.unsourcedLinkCount, 3);
    assert.ok(!text.includes(INVENTED) && text.includes(UNSOURCED_LINK_MARKER) && text.includes(SOURCED));
    assert.match(text, /Its sources are unread search results, not pages Pixel read/);

    const guard = guardFor(EVENT_PROMPT);
    const call = before(guard, transport, 'pixel_ods_research', {query: 'Philadelphia public events in the next 45 days'});
    assert.equal(call.refusal, undefined);
    after(guard, transport, 'pixel_ods_research', call, result);

    // The model cites Perplexica's listing source and reconstructs an invented detail link.
    const answer = `1. Philly Korean Festival, October 10, 2026, Constitution Center: ${INVENTED}\n` +
      `2. More events are listed at ${SOURCED}`;
    const revision = guard.beforeAgentFinalize({lastAssistantMessage: answer}, {...base});
    assert.equal(revision?.action, 'revise');
    assert.equal(revision.retry.instruction, UNREAD_SOURCES_REVISION_INSTRUCTION.join(JSON.stringify([INVENTED, SOURCED])),
      'neither the research answer nor its returned sources is a page read');

    // Reading the listing is a real receipt; the invented detail link stays unverified.
    const read = before(guard, transport, 'web_fetch', {url: SOURCED});
    assert.equal(read.refusal, undefined);
    after(guard, transport, 'web_fetch', read, {content: [{type: 'text', text: 'Events listing'}],
      details: {status: 200, url: SOURCED, finalUrl: SOURCED, text: 'Philly Korean Festival October 10, 2026'}});
    const final = guard.beforeAgentFinalize({lastAssistantMessage: answer}, {...base});
    assert.equal(final?.action, 'finalize');
    const delivery = guard.deliveryVerificationForRun(base.runId);
    assert.equal(delivery.status, 'failed');
    assert.equal(delivery.text, `${answer.replace(INVENTED, UNREAD_SOURCE_MARKER)}\n\n${UNREAD_SOURCE_NOTE}`);
  });

  test(`${transport}: returned research sources satisfy the weaker research-source check`, async () => {
    // Research without an explicit page-read request: a cited returned source
    // is attribution, as for web_search results. Tool Search results are bound too.
    const guard = guardFor('Search the web for public events in Philadelphia this October.');
    const call = before(guard, transport, 'pixel_ods_research', {query: 'Philadelphia public events October 2026'});
    after(guard, transport, 'pixel_ods_research', call, await measuredResearchResult());
    const decision = guard.beforeAgentFinalize({lastAssistantMessage: `Several festivals are listed at ${SOURCED} (not yet opened).`}, {...base});
    assert.equal(decision, undefined);
  });
}
