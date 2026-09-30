// Per-item sources: listing pages, their entries' own-page links, and the one
// finalization continuation when the owner asked for a source per item.
// tower1 round 091 (2026-09-25) is replayed through the real guard hooks, the
// real guarded page reader and completion assurance, with synthetic page
// bodies (fixtures/search-read-listings.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createSearchReadTool, SEARCH_READ_TOOL, searchReadOutputChars} from '../plugin/search-read.mjs';
import {createPublicPageReader} from '../plugin/web-extract.mjs';
import {createHostCitationVerifier} from '../plugin/citation-verification.mjs';
import {itemSourcesRequested} from '../plugin/completion-assurance.mjs';
import {entryOwnLink, listingProfile, siteOf, urlNamesEntry} from '../plugin/source-kind.mjs';
import {itemSourceFindings} from '../plugin/item-sources.mjs';
import {t as extractFixture} from './fixtures/html-extraction/extractor.mjs';
import {PER_ITEM_PROMPT, PLAIN_PROMPT, GUIDE, AGGREGATOR, DESIGN, HALLOWEEN, RODEO, R091_SEARCH, R091_ANSWER,
  GUIDE_PAGE, AGGREGATOR_PAGE, OWN_PAGES, MONTH_GUIDE, RIVER_FEST, OKTOBERFEST, R092_T2_ANSWER,
  MONTH_GUIDE_FETCH_TEXT} from './fixtures/search-read-listings.mjs';

const KIMMEL = 'https://www.ensembleartsphilly.org/rent-our-spaces/special-events-and-performances/the-first-at-250';

const PAGES = {[GUIDE]: GUIDE_PAGE, [GUIDE.replace(/\/$/, '')]: GUIDE_PAGE, [AGGREGATOR]: AGGREGATOR_PAGE, ...OWN_PAGES};

function harness(prompt, run, {limits} = {}) {
  const fetched = [];
  const readPage = createPublicPageReader({
    guardedFetch: async ({url}) => {
      fetched.push(url);
      const body = PAGES[url];
      return {response: new Response(body ?? '', {status: body ? 200 : 404,
        headers: {'Content-Type': 'text/html; charset=utf-8'}}), finalUrl: url, release() {}};
    },
    readResponseText: async response => ({text: await response.text(), truncated: false}),
    extractBasicHtmlContent: extractFixture,
  });
  const context = {agentId: 'pixel', runId: run, sessionId: `${run}-session`, sessionKey: `agent:pixel:${run}`};
  const guard = createToolLoopGuard({hostCitationVerifier: createHostCitationVerifier({readPage}), limits});
  guard.observeRun(context, 'pixel', {prompt});
  const config = {agents: {list: [{id: 'pixel', contextLimits: {toolResultMaxChars: 8000}}]}};
  const tool = createSearchReadTool({
    search: async () => ({provider: 'parallel-free', result: {results: R091_SEARCH.results}}),
    readPage, outputChars: () => searchReadOutputChars(config), now: () => new Date('2026-09-25T18:43:15Z'),
  });
  let id = 0, calls = 0;
  async function searchRead(params, callId = `sr-${++id}`) {
    const ctx = {...context, toolName: SEARCH_READ_TOOL, toolCallId: callId};
    const before = guard.beforeToolCall({toolName: SEARCH_READ_TOOL, params}, ctx);
    assert.notEqual(before?.block, true, before?.blockReason);
    const executed = {...params, ...(before?.params ?? {})};
    const result = await tool.execute(callId, executed);
    guard.afterToolCall({toolName: SEARCH_READ_TOOL, params: executed, result}, ctx);
    calls++;
    return {result, text: result.content[0].text};
  }
  async function finalize(answer) {
    const event = {lastAssistantMessage: answer};
    await guard.verifyCitedPages(event, context);
    return guard.beforeAgentFinalize(event, context);
  }
  return {guard, context, fetched, searchRead, finalize, calls: () => calls, tool};
}

const FIRST = {query: R091_SEARCH.query, focus: R091_SEARCH.focus, maxPages: 5};
const linkOf = (text, url) => text.match(new RegExp(`\\[(L\\d+)\\] ${url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`))?.[1];

test('a listing is many distinct dated entries; one event\'s schedule or a detail page is not', () => {
  const guide = ['Top events', 'DesignPhiladelphia Festival', 'October 1-11, 2026', 'Halloween Nights', 'Photo courtesy X',
    'October 2 – November 7, 2026', 'Chinatown Night Market', 'Thursday, October 8, 2026', '8 Second Rodeo',
    'Saturday, October 10, 2026 | 7 p.m.', 'Open Studio Tours', 'October 10-18, 2026'].join('\n');
  assert.deepEqual(listingProfile(guide), {items: 5, listing: true});
  // An aggregator's FAQ repeating its own list counts each show once.
  const shows = ['Phoebe Bridgers is happening on Sun 27 Sep 2026 from 7:00 PM onwards at Xfinity Mobile Arena',
    'Jane Remover is happening on Fri 25 Sep 2026 from 8:00 PM onwards at Franklin Music Hall',
    'Jonas Brothers is happening on Fri 02 Oct 2026 from 7:30 PM onwards at Xfinity Mobile Arena'];
  assert.equal(listingProfile([...shows, ...shows].join('\n')).items, 3);
  // One event's dates: every date line takes the same heading.
  const schedule = ['Halloween Nights', 'Dates', 'Friday, October 2', 'Saturday, October 3', 'Friday, October 9',
    'Saturday, October 10', 'Friday, October 16', 'Saturday, October 17 7:00 PM'].join('\n');
  assert.equal(listingProfile(schedule).listing, false);
  // A detail page with a short sidebar of other events.
  const detail = ['Teddy Swims: The Ugly Tour', 'Sat, Oct 10, 2026 7:00 PM', 'You may also like', 'Gritty 5K Sep 26, 2026',
    'Motionless In White Oct 31, 2026'].join('\n');
  assert.equal(listingProfile(detail).listing, false);
  // Calendar text run together by an extractor ("Oct012026") still dates it.
  const runTogether = ['Mamma Mia', 'Logic and Eazy', 'Santiago Cruz', 'Rachel Reid', 'Bebe Stockwell', 'Geoffrey Asmus']
    .map((name, i) => `- Oct0${i + 1}20267:30 PMThuCONCERT ${name} Keswick Theatre`);
  assert.equal(listingProfile(runTogether.join('\n')).listing, true);
  assert.equal(listingProfile('Open 24/7, call 1/2/3 for help').items, 0);
});

test('an entry\'s own-page link: its title naming the site, or an "official site" link, by a dated entry', () => {
  const around = 'DesignPhiladelphia Festival\nOctober 1-11, 2026';
  const own = (label, url, line = label, near = around) => entryOwnLink({label, line, url, around: near});
  assert.equal(own('DesignPhiladelphia Festival', DESIGN), true);
  assert.equal(own('Halloween Nights at Eastern State Penitentiary', HALLOWEEN), true);
  assert.equal(own('Philadelphia Phillies at Citizens Bank Park', 'https://www.mlb.com/phillies/schedule/2026-09'), true);
  assert.equal(own('official site', RODEO, 'Check the official site for the schedule.'), true);
  // Not an entry's own page: a map, a ticket button, a sentence's inline
  // link, a social profile, a footer partner away from any date.
  assert.equal(own('Philadelphia, PA', 'https://maps.google.com/?cid=1'), false);
  assert.equal(own('Buy tickets', 'https://www.ticketmaster.com/8-seconds-rodeo-tickets/artist/1'), false);
  assert.equal(own('a national readers poll', 'https://10best.usatoday.com/awards/haunted-attraction/',
    'It was named a top attraction by a national readers poll this year.'), false);
  assert.equal(own('Eastern State Penitentiary', 'https://www.facebook.com/easternstate'), false);
  assert.equal(own('Visit Pennsylvania', 'https://www.visitpa.com/', 'Visit Pennsylvania', 'Plan your trip. © 2026'), false);
  assert.deepEqual(urlNamesEntry(HALLOWEEN, ['halloween', 'eastern', 'penitentiary']), ['halloween', 'eastern']);
  assert.equal(siteOf('support.allevents.in'), 'allevents.in');
  assert.equal(siteOf('www.bbc.co.uk'), 'bbc.co.uk');
});

test('the owner asks for a source per item, or only for sources', () => {
  for (const text of [PER_ITEM_PROMPT, 'List 3 concerts and for each one give the official link.',
    'Give me the direct source URL for every product.', 'Liste 3 eventos e para cada um informe o link oficial.']) {
    assert.equal(itemSourcesRequested(text), true, text);
  }
  for (const text of [PLAIN_PROMPT, 'Search the web for concerts this week and cite your sources.',
    'Summarize the official announcement.', 'Find the latest news about the festival.']) {
    assert.equal(itemSourcesRequested(text), false, text);
  }
});

test('search_read marks listings and prints the link an entry\'s title gives its own site', async () => {
  const {searchRead} = harness(PER_ITEM_PROMPT, 'listing-marks');
  const {result, text} = await searchRead({urls: [GUIDE, AGGREGATOR], focus: 'DesignPhiladelphia Festival Halloween Nights date'});
  assert.match(text, /^\[R1\] https:\/\/www\.visitphilly\.com\/\S+ \| HTTP 200 \| listing: 10 dated entries$/m);
  assert.match(text, /^\[R2\] https:\/\/allevents\.in\/philadelphia\/live-music \| HTTP 200 \| listing: 8 dated entries$/m);
  assert.match(text, /A page marked listing names many dated entries: for each of them it is a lead/);
  // The entries' titles carry their own sites' links; a map, a ticket seller,
  // a press award, a social profile and a footer partner are never listed.
  assert.match(text, new RegExp(`DesignPhiladelphia Festival \\[${linkOf(text, DESIGN)}\\]`));
  assert.match(text, new RegExp(`Halloween Nights at Eastern State Penitentiary \\[${linkOf(text, HALLOWEEN)}\\]`));
  assert.doesNotMatch(text, /maps\.google|ticketmaster|usatoday|facebook|visitpa|support\.allevents/);
  const [guide, aggregator] = result.details.pages;
  assert.deepEqual(guide.listing, {items: 10});
  for (const url of [DESIGN, HALLOWEEN, RODEO]) assert.ok(guide.ownLinks.includes(url), url);
  assert.ok(!guide.ownLinks.some(url => /maps\.google|ticketmaster|usatoday|facebook|visitpa/.test(url)));
  assert.deepEqual(aggregator.listing, {items: 8});
  assert.deepEqual(aggregator.ownLinks, [], 'an aggregator links only to itself');
  // Through Tool Search the model sees details: only the printed own links.
  const viaSearch = await harness(PER_ITEM_PROMPT, 'listing-tool-search').tool
    .execute('tool_search_code:1', {urls: [GUIDE], focus: 'DesignPhiladelphia Festival date'});
  const printed = [...viaSearch.content[0].text.matchAll(/\[L\d+\] (\S+)/g)].map(match => match[1]);
  assert.ok(viaSearch.details.pages[0].ownLinks.includes(DESIGN));
  assert.ok(viaSearch.details.pages[0].ownLinks.every(url => printed.includes(url)));
  assert.ok(viaSearch.details.pages[0].ownLinks.length < guide.ownLinks.length);
  // A single event's own page is not a listing.
  const own = await searchRead({urls: [DESIGN], focus: 'date'});
  assert.equal(own.result.details.pages[0].listing, undefined);
  assert.doesNotMatch(own.text, /listing/);
});

test('round 091 replay: items cited only from listings get one continuation with their own pages', async () => {
  const {finalize, searchRead, fetched, calls, guard, context} = harness(PER_ITEM_PROMPT, 'r091');
  await searchRead(FIRST);
  const decision = await finalize(R091_ANSWER);
  assert.equal(decision?.action, 'revise');
  assert.equal(decision.retry.idempotencyKey, 'ods-item-own-sources');
  assert.equal(decision.retry.maxAttempts, 1);
  const instruction = decision.retry.instruction;
  assert.match(instruction, /"Phoebe Bridgers" cites https:\/\/allevents\.in\/philadelphia\/live-music, a listing of 8 dated entries\. No own page for it was seen yet\./);
  assert.match(instruction, new RegExp(`"DesignPhiladelphia Festival" cites \\S+ a listing of 10 dated entries\\. Its own page, linked from a listing you read: ${DESIGN}`));
  assert.match(instruction, new RegExp(`"Halloween Nights at Eastern State Penitentiary" cites \\S+ a listing of 10 dated entries\\. Its own page, linked from a listing you read: ${HALLOWEEN}`));
  const urls = JSON.parse(instruction.match(/urls (\[[^\]]*\])/)[1]);
  assert.deepEqual(urls, [DESIGN, HALLOWEEN]);
  assert.equal(fetched.filter(url => Object.keys(OWN_PAGES).includes(url)).length, 0, 'the check reads nothing itself');

  // The continuation: one urls call reads both own pages with receipts.
  const own = await searchRead({urls, focus: 'dates venue'});
  assert.equal(own.result.details.receipts, 2);
  assert.equal(calls(), 2);
  const revised = R091_ANSWER
    .replace(`${GUIDE}\n\n---\n\n**3.`, `${DESIGN}\n\n---\n\n**3.`)
    .replace(/(\*\*Source:\*\* )https:\/\/www\.visitphilly\.com\S+(\n\n---\n\nAll)/, `$1${HALLOWEEN}$2`)
    .replace('- **Source:** https://allevents.in/philadelphia/live-music',
      '- **Source:** https://allevents.in/philadelphia/live-music (an aggregator listing; the arena\'s own event page could not be found)');
  assert.equal(new Set(revised.match(/https:\/\/\S+(?=\s|$)/g).map(url => url.replace(/[).,]+$/, ''))).size, 3);
  assert.equal(await finalize(revised), undefined, 'own pages, and an honest limitation for the third item');
  assert.notEqual(guard.deliveryVerificationForRun(context.runId).status, 'failed');
});

test('one continuation at most: an unchanged answer after it is delivered as it is', async () => {
  const {finalize, searchRead} = harness(PER_ITEM_PROMPT, 'once');
  await searchRead(FIRST);
  assert.equal((await finalize(R091_ANSWER))?.action, 'revise');
  assert.equal(await finalize(R091_ANSWER), undefined);
});

test('honest unavailability passes: next to the item, or in a closing note that names it', async () => {
  const {finalize, searchRead} = harness(PER_ITEM_PROMPT, 'honest');
  await searchRead(FIRST);
  const beside = R091_ANSWER
    .replace('- **Source:** https://allevents.in/philadelphia/live-music',
      '- **Source:** https://allevents.in/philadelphia/live-music (the arena\'s official page returned HTTP 403)')
    .replace(/(- \*\*Source:\*\* https:\/\/www\.visitphilly\.com\S+)/g, '$1 (no official page could be found; this is a listing)');
  assert.equal(await finalize(beside), undefined);

  const {finalize: finalize2, searchRead: searchRead2} = harness(PER_ITEM_PROMPT, 'honest-note');
  await searchRead2(FIRST);
  const note = `${R091_ANSWER}\n\nLimitations: the official sites for Phoebe Bridgers, DesignPhiladelphia and the ` +
    'Halloween Nights event could not be opened (HTTP 403), so the listings above are the sources.';
  assert.equal(await finalize2(note), undefined);
  // A generic note that names no item does not excuse them.
  const {finalize: finalize3, searchRead: searchRead3} = harness(PER_ITEM_PROMPT, 'generic-note');
  await searchRead3(FIRST);
  assert.equal((await finalize3(`${R091_ANSWER}\n\nSome official pages could not be opened.`))?.action, 'revise');
});

test('no regression: without a per-item request, or with own pages cited, nothing changes', async () => {
  // The same answer when the owner only asked for sources.
  const plain = harness(PLAIN_PROMPT, 'plain');
  await plain.searchRead(FIRST);
  assert.equal(await plain.finalize(R091_ANSWER), undefined);

  // Own pages cited (read in the run), and an entry's own site that is itself
  // a listing of its program (the festival's own events page).
  const own = harness(PER_ITEM_PROMPT, 'own-pages');
  await own.searchRead(FIRST);
  await own.searchRead({urls: [DESIGN, HALLOWEEN], focus: 'dates venue'});
  const answer = [
    `1. **DesignPhiladelphia Festival** - Date: October 1-11, 2026 - various venues - ${DESIGN}`,
    `2. **Halloween Nights at Eastern State Penitentiary** - Date: October 2, 2026 - Eastern State Penitentiary - ${HALLOWEEN}`,
  ].join('\n');
  assert.equal(await own.finalize(answer), undefined);
  const findings = itemSourceFindings(`1. **DesignPhiladelphia Festival** - Date: October 1-11, 2026 - ${DESIGN}`, {
    listings: new Map([[DESIGN, {url: DESIGN, items: 6, ownLinks: []}]]), requestText: PER_ITEM_PROMPT});
  assert.deepEqual(findings, [], 'a listing on the item\'s own site is its own page');

  // A bare reference list after the items names no item.
  const refs = harness(PER_ITEM_PROMPT, 'refs');
  await refs.searchRead(FIRST);
  await refs.searchRead({urls: [DESIGN, HALLOWEEN], focus: 'dates venue'});
  assert.equal(await refs.finalize(`${answer}\n\nSources consulted:\n- ${GUIDE}\n- ${AGGREGATOR}`), undefined);
});

test('main without search_read (tower2 round 092): a guide read with web_fetch is a listing, and its own links count', async () => {
  const run = 'r092-web-fetch';
  const context = {agentId: 'pixel', runId: run, sessionId: `${run}-s`, sessionKey: `agent:pixel:${run}`};
  const guard = createToolLoopGuard();
  guard.observeRun(context, 'pixel', {prompt: PER_ITEM_PROMPT});
  let id = 0;
  const fetch = (url, text) => {
    const ctx = {...context, toolName: 'web_fetch', toolCallId: `f${++id}`};
    const details = {url, finalUrl: url, status: 200, contentType: 'text/html', extractMode: 'markdown', text};
    guard.beforeToolCall({toolName: 'web_fetch', params: {url}}, ctx);
    guard.afterToolCall({toolName: 'web_fetch', params: {url},
      result: {content: [{type: 'text', text: JSON.stringify(details)}], details}}, ctx);
  };
  fetch(MONTH_GUIDE, MONTH_GUIDE_FETCH_TEXT);
  fetch(KIMMEL, '<<<EXTERNAL_UNTRUSTED_CONTENT id="k">>>\nSource: Web Fetch\n---\n# The First at 250\n\nWednesday, ' +
    'September 9, 2026, 6:30 p.m. at the Kimmel Center.\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="k">>>');
  const decision = guard.beforeAgentFinalize({lastAssistantMessage: R092_T2_ANSWER}, context);
  assert.equal(decision?.retry?.idempotencyKey, 'ods-item-own-sources');
  const instruction = decision.retry.instruction;
  assert.match(instruction, new RegExp(`"Delaware River Festival" cites \\S+ a listing of 6 dated entries\\. Its own page, linked from a listing you read: ${RIVER_FEST}`));
  assert.match(instruction, new RegExp(`"18th Annual South Street Oktoberfest" cites \\S+ a listing of 6 dated entries\\. Its own page, linked from a listing you read: ${OKTOBERFEST}`));
  assert.doesNotMatch(instruction, /delawareriverwaterfront|The First at 250/, 'the neighbouring festival and the own page are not named');
  assert.deepEqual(JSON.parse(instruction.match(/urls (\[[^\]]*\])/)[1]), [RIVER_FEST, OKTOBERFEST]);

  // A listing read only as targeted text (pixel_ods_web_extract, no links):
  // flagged, with no own page to offer.
  const run2 = 'r092-extract';
  const context2 = {agentId: 'pixel', runId: run2, sessionId: `${run2}-s`, sessionKey: `agent:pixel:${run2}`};
  const guard2 = createToolLoopGuard();
  guard2.observeRun(context2, 'pixel', {prompt: PER_ITEM_PROMPT});
  const text = MONTH_GUIDE_FETCH_TEXT.replace(/\]\([^)]*\)/g, '').replace(/[[#]/g, '');
  const ctx = {...context2, toolName: 'pixel_ods_web_extract', toolCallId: 'x1'};
  const params = {url: MONTH_GUIDE, query: 'Delaware River Festival'};
  guard2.beforeToolCall({toolName: 'pixel_ods_web_extract', params}, ctx);
  guard2.afterToolCall({toolName: 'pixel_ods_web_extract', params, result: {content: [{type: 'text', text}],
    details: {boundary: 'public-web-read-only', matched: true, source_url: MONTH_GUIDE}}}, ctx);
  guard2.afterToolCall({toolName: 'web_fetch', params: {url: KIMMEL}, result: {content: [{type: 'text', text: 'x'}],
    details: {url: KIMMEL, finalUrl: KIMMEL, status: 200, text: 'The First at 250, September 9, 2026'}}},
  {...context2, toolName: 'web_fetch', toolCallId: 'x2'});
  const extracted = guard2.beforeAgentFinalize({lastAssistantMessage: R092_T2_ANSWER}, context2);
  assert.match(extracted?.retry?.instruction ?? '', /"Delaware River Festival" cites \S+ a listing of 6 dated entries\. No own page for it was seen yet\./);
});

test('no continuation when no page read is left to fix it', async () => {
  const {finalize, searchRead} = harness(PER_ITEM_PROMPT, 'no-reads', {limits: {search: 8, fetch: 5, total: 32}});
  await searchRead({...FIRST, maxPages: 5});
  assert.equal(await finalize(R091_ANSWER), undefined);
});
