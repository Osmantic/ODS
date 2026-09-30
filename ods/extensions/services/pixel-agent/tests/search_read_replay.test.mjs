// Fleet research journeys replayed through pixel_ods_search_read with a
// stubbed network: the real guarded page reader, the real guard hooks, the
// real completion assurance and host citation check. Page bodies are
// synthetic (fixtures/search-read-fleet.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, DEFAULT_WEB_TOOL_LIMITS} from '../plugin/tool-loop-guard.mjs';
import {createSearchReadTool, SEARCH_READ_TOOL, searchReadOutputChars} from '../plugin/search-read.mjs';
import {createPublicPageReader} from '../plugin/web-extract.mjs';
import {createHostCitationVerifier} from '../plugin/citation-verification.mjs';
import {t as extractFixture} from './fixtures/html-extraction/extractor.mjs';
import {TOWER1_R060_FIRST_ANSWER} from './fixtures/philly-events-detail-links.mjs';
import {EVENT_PROMPT, COMPONENT_PROMPT, EVENT_SEARCH, VENUE_SEARCH, COMPONENT_SEARCH, EVENT_PAGES, DETAIL_PAGES,
  COMPONENT_PAGES, GRITTY, CAPITALS, TEDDY, MOTIONLESS, RAMS, RX9070_URL} from './fixtures/search-read-fleet.mjs';

const PAGES = {...EVENT_PAGES, ...DETAIL_PAGES, ...COMPONENT_PAGES};

// The real reader behind a stubbed guarded transport. Unknown pages are 404.
function network() {
  const fetched = [];
  const readPage = createPublicPageReader({
    guardedFetch: async ({url}) => {
      fetched.push(url);
      const entry = PAGES[url];
      const {status = 200, body = ''} = typeof entry === 'string' ? {body: entry} : entry ?? {status: 404};
      return {response: new Response(body, {status, headers: {'Content-Type': 'text/html; charset=utf-8'}}), finalUrl: url, release() {}};
    },
    readResponseText: async response => ({text: await response.text(), truncated: false}),
    extractBasicHtmlContent: extractFixture,
  });
  return {fetched, readPage};
}

function harness(prompt, run) {
  const net = network();
  const context = {agentId: 'pixel', runId: run, sessionId: `${run}-session`, sessionKey: `agent:pixel:${run}`};
  const guard = createToolLoopGuard({hostCitationVerifier: createHostCitationVerifier({readPage: net.readPage})});
  guard.observeRun(context, 'pixel', {prompt});
  const config = {agents: {list: [{id: 'pixel', contextLimits: {toolResultMaxChars: 16000}}]}};
  let searches = 0;
  const lists = new Map([EVENT_SEARCH, VENUE_SEARCH, COMPONENT_SEARCH].map(entry => [entry.query, entry.results]));
  const tool = createSearchReadTool({
    search: async ({query}) => {
      searches++;
      return {provider: 'parallel-free', result: {results: lists.get(query.replace(/ site:\S+$/, '')) ?? []}};
    },
    readPage: net.readPage,
    outputChars: () => searchReadOutputChars(config),
    now: () => new Date('2026-09-25T14:07:00Z'),
  });
  let id = 0;
  // One search_read call through the guard hooks, as the runtime runs it.
  async function searchRead(params) {
    const callId = `sr-${++id}`;
    const ctx = {...context, toolName: SEARCH_READ_TOOL, toolCallId: callId};
    const before = guard.beforeToolCall({toolName: SEARCH_READ_TOOL, params}, ctx);
    assert.notEqual(before?.block, true, before?.blockReason);
    const executed = {...params, ...(before?.params ?? {})};
    const result = await tool.execute(callId, executed);
    guard.afterToolCall({toolName: SEARCH_READ_TOOL, params: executed, result}, ctx);
    const message = guard.toolResultPersist({message: {role: 'toolResult', toolName: SEARCH_READ_TOOL,
      toolCallId: callId, content: result.content}}, ctx)?.message;
    return {result, text: result.content[0].text, message};
  }
  return {guard, context, net, searchRead, searches: () => searches};
}

const linkUrls = text => [...text.matchAll(/\[L\d+\] (https:\/\/\S+)/g)].map(match => match[1]);

test('event journey: one call reads the venue listings and exposes their per-event detail links', async () => {
  const {guard, context, net, searchRead} = harness(EVENT_PROMPT, 'events-listing');
  const {result, text} = await searchRead({query: VENUE_SEARCH.query, focus: 'date venue'});
  assert.equal(result.details.receipts, 2);
  assert.ok(text.length <= 5000, `${text.length} chars`);
  // In-window dates survive in the excerpt, next to their detail links.
  assert.match(text, /Teddy Swims: The Ugly Tour \[L\d+\] Sat, Oct 10, 2026 7:00 PM/);
  assert.match(text, /Los Angeles Rams vs\. Philadelphia Eagles \[L\d+\] October 4, 2026 1:00 PM/);
  const links = linkUrls(text);
  for (const url of [GRITTY, CAPITALS, TEDDY, MOTIONLESS, RAMS]) assert.ok(links.includes(url), url);
  assert.ok(!links.some(url => /ticketmaster|facebook/.test(url)), 'only same-site links');
  assert.doesNotMatch(text, /dataLayer/, 'script text is not page evidence');
  assert.equal(net.fetched.length, 2);

  // The fleet answer cites three detail pages it saw as [L#] links. They are
  // unread, so the host citation check reads them (the listing receipts do
  // not cover them) and, finding each claim, lets the answer through as is.
  const event = {lastAssistantMessage: TOWER1_R060_FIRST_ANSWER};
  const outcome = await guard.verifyCitedPages(event, context);
  assert.deepEqual(outcome.verified.map(entry => entry.url).sort(), [RAMS, TEDDY, MOTIONLESS].sort());
  assert.equal(guard.beforeAgentFinalize(event, context), undefined, 'no revision');
  assert.notEqual(guard.deliveryVerificationForRun(context.runId).status, 'failed');
});

test('event journey: reading chosen detail links is one parallel call with receipts', async () => {
  const {guard, context, searchRead, searches} = harness(EVENT_PROMPT, 'events-details');
  await searchRead({query: VENUE_SEARCH.query, focus: 'date venue'});
  const details = await searchRead({urls: [GRITTY, CAPITALS, TEDDY], focus: 'date time'});
  assert.equal(details.result.details.receipts, 3);
  assert.equal(searches(), 1, 'a urls call does not search');
  for (const date of ['Sep 26, 2026', 'Oct 10, 2026']) assert.ok(details.text.includes(date));
  const budget = details.message.content.find(block => block.text?.startsWith('ODS research budget')).text;
  const L = DEFAULT_WEB_TOOL_LIMITS;
  assert.match(budget, new RegExp(`Remaining this response: ${L.search - 1} search calls, ${L.fetch - 5} page-reading calls, ${L.total - 6} web calls total`));
  const answer = [
    'Three public events in Philadelphia within 45 days:',
    `1. **Gritty 5K Presented by Penn Medicine** - Date: Sep 26, 2026 - Xfinity Mobile Arena - ${GRITTY}`,
    `2. **Capitals vs. Flyers (Preseason)** - Date: Sep 26, 2026 - Xfinity Mobile Arena - ${CAPITALS}`,
    `3. **Teddy Swims: The Ugly Tour** - Date: Oct 10, 2026 - Xfinity Mobile Arena - ${TEDDY}`,
  ].join('\n');
  const event = {lastAssistantMessage: answer};
  assert.equal(await guard.verifyCitedPages(event, context), undefined, 'every cited page was read by search_read');
  assert.equal(guard.beforeAgentFinalize(event, context), undefined);
});

test('event journey: the recorded first search reads dated listings and keeps the rest as leads', async () => {
  const {searchRead} = harness(EVENT_PROMPT, 'events-first');
  const {result, text} = await searchRead({query: EVENT_SEARCH.query, focus: 'date venue'});
  // None of these synthetic pages exist: every read fails, honestly labelled.
  assert.equal(result.details.readsAttempted, 3);
  assert.equal(result.details.receipts, 0);
  assert.equal(result.details.status, 'partial');
  assert.equal((text.match(/^\[not read\] /gm) ?? []).length, 3);
  assert.match(text, /Leads \(search results, not opened\):/);
  // Host diversity: two Visit Philadelphia articles are not both opened first.
  const opened = result.details.pages.map(page => new URL(page.url).hostname);
  assert.equal(new Set(opened).size, 3);
});

test('component journey: official specifications survive in the excerpts with their sources', async () => {
  const {guard, context, searchRead} = harness(COMPONENT_PROMPT, 'components');
  // Without a site preference the official page is the fifth result: a lead.
  const general = await searchRead({query: COMPONENT_SEARCH.query, focus: 'memory board power'});
  assert.match(general.text, /\[not read\] https:\/\/www\.techspot\.com\/\S+ \| HTTP 403: the site refused this client/);
  assert.match(general.text, /- https:\/\/www\.pny\.com\/\S+\.pdf/);
  assert.ok(general.result.details.results.some(row => row.url.includes('nvidia.com')));
  // With a site preference (a refinement, not a repeat) the official page is
  // read first; its facts are in the excerpt.
  const official = await searchRead({query: COMPONENT_SEARCH.query, site: 'nvidia.com', maxPages: 1,
    focus: 'memory board power'});
  assert.equal(official.result.details.pages[0].url, COMPONENT_SEARCH.results[4].url);
  assert.equal(official.result.details.receipts, 1);
  for (const fact of ['12 GB', 'GDDR7', '250 W']) assert.ok(official.text.includes(fact), fact);
  const amd = await searchRead({urls: [RX9070_URL], focus: 'memory board power'});
  for (const fact of ['16 GB', 'GDDR6', '220 W']) assert.ok(amd.text.includes(fact), fact);
  const answer = JSON.stringify({gpus: [
    {model: 'RTX 5070', memory: '12 GB GDDR7', boardPowerW: 250, source: COMPONENT_SEARCH.results[4].url},
    {model: 'RX 9070', memory: '16 GB GDDR6', boardPowerW: 220, source: RX9070_URL},
  ]}, null, 2);
  const event = {lastAssistantMessage: answer};
  assert.equal(await guard.verifyCitedPages(event, context), undefined);
  assert.equal(guard.beforeAgentFinalize(event, context), undefined);
});
