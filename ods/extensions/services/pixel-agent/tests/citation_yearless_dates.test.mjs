// Host citation check: event pages that show a date without its year
// (citation-verification.mjs, "Page dates without a year"), the schema.org
// Event dates the host reader takes from JSON-LD (web-extract.mjs), and the
// tower1 round 092 fleet replay.
import test from 'node:test';
import assert from 'node:assert/strict';
import {citationClaims, citationDateReference, createHostCitationVerifier, pageSupportsClaims,
  YEARLESS_DATE_LIMITS} from '../plugin/citation-verification.mjs';
import {createPublicPageReader, STRUCTURED_EVENT_LIMITS, structuredEventDates} from '../plugin/web-extract.mjs';
import {UNREAD_SOURCES_REVISION_INSTRUCTION} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {LFF_COWBOYS_EVENT_JSON_LD, LFF_COWBOYS_TEXT, LFF_COWBOYS_URL, LFF_PANTHERS_EVENT_JSON_LD, LFF_PANTHERS_TEXT,
  LFF_PANTHERS_URL, TOWER1_R092_FIRST_ANSWER} from './fixtures/philly-events-yearless-dates.mjs';

// The fleet journey prompt (live event search), as tower1 round 092 sent it.
const FLEET_PROMPT = 'Today is 2026-09-25. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL. ' +
  'Exclude undated listings and past events. Explain any unavailable result honestly. Do not create files.';
const REFERENCE = citationDateReference(FLEET_PROMPT);
const OURPHILLY = 'https://www.ourphilly.org/date/2026-10-23';

const panthers = (date, url = LFF_PANTHERS_URL) =>
  `## 1. **Carolina Panthers vs. Philadelphia Eagles**\n- **Date:** ${date}\n- **Source:** [Lincoln Financial Field official event page](${url})`;
const check = (page, {answer = panthers('October 18, 2026 at 1:00 PM'), url = LFF_PANTHERS_URL, reference = REFERENCE,
  context = {}} = {}) => pageSupportsClaims(page, citationClaims(answer, url), {...reference, urls: [url], ...context});
const withJsonLd = (json, text) => `<html><head><script type="application/ld+json">${JSON.stringify(json)}</script></head>` +
  `<body>${text}</body></html>`;
const staleJsonLd = json => JSON.parse(JSON.stringify(json).replaceAll('"2026-10', '"2025-10'));

// ---------------------------------------------------------------------------
// The date reference: owner's stated date and window, else the host's date
// ---------------------------------------------------------------------------

test('the owner\'s stated date and requested window set the reference; the host date otherwise', () => {
  assert.deepEqual(REFERENCE, {today: Date.UTC(2026, 8, 25) / 86_400_000, days: 45, requested: true});
  const day = (y, m, d) => Date.UTC(y, m - 1, d) / 86_400_000;
  for (const [owner, days] of [
    ['Find events in the next two weeks', 14],
    ['upcoming 3 months of shows', 93],
    ['concerts within 30 days', 30],
    ['what is on this weekend?', 14],
    ['anything next month?', 62],
    ['Pesquise eventos em Lisboa nos próximos 30 dias', 30],
    ['eventos no próximo mês', 62],
    ['shows over the next 12 months', YEARLESS_DATE_LIMITS.maxDays],
  ]) {
    assert.equal(citationDateReference(owner, Date.UTC(2026, 8, 25, 12)).days, days, owner);
    assert.equal(citationDateReference(owner, Date.UTC(2026, 8, 25, 12)).requested, true, owner);
  }
  const plain = citationDateReference('When do the Eagles play the Cowboys?', new Date(2026, 8, 25, 12).getTime());
  assert.deepEqual(plain, {today: day(2026, 9, 25), days: YEARLESS_DATE_LIMITS.defaultDays}, 'host date, default window, none requested');
  assert.equal(citationDateReference(undefined, new Date(2027, 0, 2, 9).getTime()).today, day(2027, 1, 2));
  assert.equal(citationDateReference('Today is 2026-02-31. Next 10 days.', new Date(2026, 8, 25, 12).getTime()).today,
    day(2026, 9, 25), 'an impossible stated date falls back to the host date');
});

// ---------------------------------------------------------------------------
// The rule, table-driven
// ---------------------------------------------------------------------------

test('a year-less page date inside the owner\'s window supports the dated claim', () => {
  assert.deepEqual(check(LFF_PANTHERS_TEXT), {supported: true, yearless: 'window'}, 'the recorded tower1 page text');
  assert.deepEqual(check(LFF_COWBOYS_TEXT, {answer: `## 2. **Dallas Cowboys vs. Philadelphia Eagles (Monday Night Football)**\n` +
    `- **Date:** October 26, 2026 at 8:15 PM\n- **Source:** [page](${LFF_COWBOYS_URL})`, url: LFF_COWBOYS_URL}),
  {supported: true, yearless: 'window'});
  for (const [page, why] of [
    ['Carolina Panthers vs. Philadelphia Eagles\nSunday, Oct. 18 · 1:00 PM · Lincoln Financial Field', 'abbreviated month, weekday'],
    ['Carolina Panthers vs. Philadelphia Eagles — 2026-27 NFL season — October 18', 'a season that includes the year'],
    ['Carolina Panthers vs. Philadelphia Eagles, October 18. Box office: 2001 Market St, Philadelphia', 'a street number'],
    ['Carolina Panthers vs. Philadelphia Eagles October 18 @ 1:00 pm\nNew York Giants vs. Philadelphia Eagles January 3, 2027',
      'a later-year row is how a listing crosses into the next year'],
    [`Carolina Panthers vs. Philadelphia Eagles October 18${' Stadium info.'.repeat(12)} Copyright 2003-2025 Philadelphia Eagles.`,
      'a footer far from the event does not date it'],
  ]) assert.equal(check(page).supported, true, why);
});

test('a stale or contradicted year-less page date does not count', () => {
  const lff = LFF_PANTHERS_TEXT;
  for (const [page, context, why] of [
    [lff.replace('October 18 @ 1:00 pm', 'October 18, 2025 @ 1:00 pm'), {}, 'explicit wrong year on the page'],
    [lff.replace('Date:\n\n October 18', 'Date:\n\n October 18, 2025').replace('October 18 @', 'October 18th @'), {},
      'the only year on the page is last year\'s'],
    [lff, {urls: ['https://www.lincolnfinancialfield.com/events/2025/carolina-panthers-vs-philadelphia-eagles/']}, 'year in the URL path'],
    [lff, {urls: [LFF_PANTHERS_URL, 'https://www.lincolnfinancialfield.com/events/carolina-panthers-vs-philadelphia-eagles-2025/']},
      'year in the final URL\'s slug'],
    [lff, {urls: ['https://www.lincolnfinancialfield.com/events/?season=2025']}, 'year in the query'],
    [lff.replaceAll('Carolina Panthers vs. Philadelphia Eagles\n', 'Carolina Panthers vs. Philadelphia Eagles 2025\n'), {},
      'the heading carries last year'],
    ['2025 Season\nCarolina Panthers vs. Philadelphia Eagles\nOctober 18 @ 1:00 pm', {}, 'a season label next to the event'],
    ['Carolina Panthers vs. Philadelphia Eagles October 18 · 2025', {}, 'a year the date parser does not join'],
    ['Philadelphia Eagles vs. Dallas Cowboys October 11, 2025\nCarolina Panthers vs. Philadelphia Eagles October 18', {},
      'an earlier-year row next to it'],
    [`This event has passed.\n${lff}`, {}, 'past-event notice (The Events Calendar)'],
    [`${lff}\nThis event has ended`, {}, 'past-event notice anywhere on the page'],
    ['Past Event\nCarolina Panthers vs. Philadelphia Eagles\nOctober 18 @ 1:00 pm', {}, 'past-event badge'],
    ['Archive: Carolina Panthers vs. Philadelphia Eagles, October 18 @ 1:00 pm', {}, 'archive label next to the event'],
    ['Carolina Panthers vs. Philadelphia Eagles was held October 18 at 1:00 pm', {}, 'past tense next to the event'],
    [lff, {eventDates: ['2025-10-18']}, 'schema.org Event startDate in another year'],
    [lff, {eventDates: ['2026-10-18', '2025-10-18']}, 'schema.org Event dates that disagree'],
  ]) assert.equal(check(page, {context}).supported, false, why);
});

test('the window bounds how far a year-less page date may be read as the claimed year', () => {
  const page = 'Carolina Panthers vs. Philadelphia Eagles\nDecember 20 @ 1:00 pm';
  const december = panthers('December 20, 2026 at 1:00 PM');
  assert.equal(check(page, {answer: december}).supported, false, 'beyond the owner\'s 45 days');
  const plain = citationDateReference('When do the Panthers play in Philadelphia?', new Date(2026, 8, 25, 12).getTime());
  assert.equal(check(page, {answer: december, reference: plain}).supported, true, 'inside the default window without a stated one');
  assert.equal(check(page.replace('December 20', 'March 20'), {answer: panthers('March 20, 2027'), reference: plain}).supported, false,
    'six months ahead is as likely last season\'s page');
  assert.equal(check(page.replace('December 20', 'September 20'), {answer: panthers('September 20, 2026')}).supported, false,
    'a year-less date already past is not read as this year\'s');
  assert.equal(check(page.replace('December 20', 'October 18'), {answer: panthers('October 18, 2027')}).supported, false,
    'next year\'s date is outside the window');
  assert.equal(pageSupportsClaims(page, citationClaims(december, LFF_PANTHERS_URL)).supported, false,
    'without a reference day a year-less page date never supports a dated claim');
});

test('schema.org Event data confirms or refutes the year of a year-less page date', () => {
  assert.deepEqual(check(LFF_PANTHERS_TEXT, {context: {eventDates: ['2026-10-18']}}), {supported: true, yearless: 'event-data'});
  assert.deepEqual(check('Carolina Panthers vs. Philadelphia Eagles\nMarch 20 @ 1:00 pm',
    {answer: panthers('March 20, 2027'), context: {eventDates: ['2027-03-20']}}), {supported: true, yearless: 'event-data'},
  'the page\'s own Event date carries the year beyond the window');
  assert.deepEqual(check(LFF_PANTHERS_TEXT, {context: {eventDates: ['2026-10-26']}}), {supported: true, yearless: 'window'},
    'another day\'s Event says nothing about this one');
  assert.equal(check(LFF_PANTHERS_TEXT, {context: {eventDates: ['2025-10-18']}}).supported, false);
  assert.equal(check(`This event has passed. ${LFF_PANTHERS_TEXT}`, {context: {eventDates: ['2026-10-18']}}).supported, false,
    'Event data does not outweigh a past-event notice');
  assert.equal(check(LFF_PANTHERS_TEXT.replace('October 18 @', 'October 18, 2025 @'), {context: {eventDates: ['2026-10-18']}}).supported,
    false, 'the same day shown in another year outweighs Event data');
  assert.deepEqual(check('Carolina Panthers vs. Philadelphia Eagles, October 18, 2026', {context: {eventDates: ['2025-10-18']}}),
    {supported: true}, 'an explicit visible year is judged as before');
});

test('a year-less claim is read inside the requested window; otherwise it stays year-less', () => {
  const yearless = panthers('Sunday, October 18 at 1:00 PM');
  assert.deepEqual(citationClaims(yearless, LFF_PANTHERS_URL)[0].date, {y: undefined, m: 10, d: 18});
  assert.equal(check('Carolina Panthers vs. Philadelphia Eagles, October 18, 2026', {answer: yearless}).supported, true);
  assert.equal(check('Carolina Panthers vs. Philadelphia Eagles, October 18, 2025', {answer: yearless}).supported, false,
    'the owner asked for the next 45 days: last year\'s page does not support it');
  assert.deepEqual(check(LFF_PANTHERS_TEXT, {answer: yearless}), {supported: true, yearless: 'window'});
  assert.equal(check(`This event has passed. ${LFF_PANTHERS_TEXT}`, {answer: yearless}).supported, false);
  // Without a requested window (a question about a past edition, say) a
  // year-less claim matches any year, as before.
  const history = citationDateReference('When was the Carolina game last season?', new Date(2026, 8, 25, 12).getTime());
  assert.equal(check('Carolina Panthers vs. Philadelphia Eagles, October 18, 2025', {answer: yearless, reference: history}).supported, true);
  assert.equal(check('Carolina Panthers vs. Philadelphia Eagles, August 2, 2026', {answer: panthers('August 2'), reference: history}).supported,
    true);
});

test('Portuguese: "nos próximos 30 dias", a day-first date without a year, and Portuguese past markers', () => {
  const owner = 'Pesquise eventos em Lisboa nos próximos 30 dias e abra as fontes.';
  const url = 'https://www.coliseulisboa.com/agenda/concerto-de-outono';
  const answer = `2. **Concerto de Outono** — 17 de outubro de 2026, às 21h30. Fonte: [Coliseu](${url})`;
  const context = {...citationDateReference(owner, new Date(2026, 8, 25, 12).getTime()), urls: [url]};
  assert.equal(context.days, 30);
  const ok = page => pageSupportsClaims(page, citationClaims(answer, url, {portuguese: true}), context).supported;
  assert.equal(ok('Concerto de Outono\nsábado, 17 de outubro · 21:30 · Coliseu dos Recreios'), true);
  assert.equal(ok('Evento encerrado\nConcerto de Outono\nsábado, 17 de outubro · 21:30'), false);
  assert.equal(ok('Concerto de Outono (edição anterior) — 17 de outubro · 21:30'), false);
  assert.equal(ok('Concerto de Outono — Avenida da Liberdade, 1950 — 17 de outubro · 21:30'), true, 'a street number');
});

// ---------------------------------------------------------------------------
// schema.org Event dates from the page's JSON-LD (the host reader)
// ---------------------------------------------------------------------------

test('JSON-LD Event start dates: Event types only, the event\'s own calendar date, bounded', () => {
  assert.deepEqual(structuredEventDates(withJsonLd(LFF_PANTHERS_EVENT_JSON_LD, LFF_PANTHERS_TEXT)), ['2026-10-18']);
  const html = [
    {'@type': 'MusicEvent', startDate: '2026-11-01T23:30:00-05:00'},
    [{'@type': ['Thing', 'https://schema.org/SportsEvent'], startDate: '2026-10-26'}],
    {'@type': 'EventSeries', startDate: '2019-10-18'},
    {'@type': 'Organization', startDate: '2001-01-01', subEvent: {'@type': 'Festival', startDate: '2026-10-03T10:00'}},
    {'@type': 'Event', startDate: 'October 18, 2026'},
  ].map(json => `<script type="application/ld+json">${JSON.stringify(json)}</script>`).join('\n') +
    '<script type=application/ld+json>{"@type": "Event", "startDate": "2026-12-01",}</script>' +
    '<script>{"@type": "Event", "startDate": "2026-12-02"}</script>' +
    '<script type="application/ld+json" src="x.json"/><p>{"@type": "Event", "startDate": "2026-12-03"}</p>' +
    `<script type="application/ld+json">${JSON.stringify({'@type': 'TheaterEvent', startDate: '2026-12-04'})}</script>`;
  assert.deepEqual(structuredEventDates(html).sort(), ['2026-10-03', '2026-10-26', '2026-11-01', '2026-12-04'],
    'no UTC shift; no series, non-event, prose date, invalid JSON, plain script, self-closing tag or visible text');
  assert.deepEqual(structuredEventDates('<p>no structured data</p>'), []);
  assert.deepEqual(structuredEventDates('<script type="application/ld+json">{"@type": "Event", "startDate": "2026-10-18"'), [],
    'an unclosed block is not read');
  const many = Array.from({length: 200}, (_, i) => ({'@type': 'Event', startDate: `2026-10-${String(1 + (i % 28)).padStart(2, '0')}`,
    location: {name: `Hall ${i}`}}));
  assert.ok(structuredEventDates(`<script type="application/ld+json">${JSON.stringify(many)}</script>`).length <= STRUCTURED_EVENT_LIMITS.dates);
  const huge = `<script type="application/ld+json">${JSON.stringify({'@type': 'Event', startDate: '2026-10-18',
    description: 'x'.repeat(STRUCTURED_EVENT_LIMITS.chars)})}</script>`;
  assert.deepEqual(structuredEventDates(huge), [], 'beyond the parse budget nothing is read');
  // Linear on hostile markup: many unterminated script tags.
  const started = performance.now();
  structuredEventDates(`application/ld+json ${'<script '.repeat(100_000)}`);
  structuredEventDates(`${'<script type="application/ld+json">{}</script>'.repeat(20_000)}`);
  assert.ok(performance.now() - started < 1000);
});

// A stubbed transport behind the real guarded page reader; the extractor
// returns the recorded OpenClaw text for each page, so only the reader's own
// JSON-LD scan sees the markup.
function recordedTransport(pages) {
  const calls = [];
  const guardedFetch = async options => {
    calls.push(options.url);
    const page = pages[options.url];
    if (!page) throw new Error(`unexpected fetch ${options.url}`);
    return {response: new Response(page.html, {status: page.status ?? 200, headers: {'Content-Type': 'text/html; charset=UTF-8'}}),
      finalUrl: page.finalUrl ?? options.url, release() {}};
  };
  const textFor = new Map(Object.entries(pages).map(([url, page]) => [page.finalUrl ?? url, page.text]));
  const readPage = createPublicPageReader({guardedFetch,
    readResponseText: async response => ({text: await response.text(), truncated: false}),
    extractBasicHtmlContent: async ({url}) => ({text: textFor.get(url) ?? ''})});
  return {calls, readPage};
}
const LFF_PAGES = {
  [LFF_PANTHERS_URL]: {html: withJsonLd(LFF_PANTHERS_EVENT_JSON_LD, 'Carolina Panthers vs. Philadelphia Eagles'), text: LFF_PANTHERS_TEXT},
  [LFF_COWBOYS_URL]: {html: withJsonLd(LFF_COWBOYS_EVENT_JSON_LD, 'Dallas Cowboys vs. Philadelphia Eagles'), text: LFF_COWBOYS_TEXT},
};

test('the host reader returns the page\'s Event dates with its text', async () => {
  const {readPage} = recordedTransport(LFF_PAGES);
  const page = await readPage(LFF_PANTHERS_URL, {timeoutSeconds: 4});
  assert.equal(page.ok, true);
  assert.equal(page.text, LFF_PANTHERS_TEXT);
  assert.deepEqual(page.eventDates, ['2026-10-18']);
  const plain = recordedTransport({[OURPHILLY]: {html: '<p>Carmina Burana</p>', text: 'Carmina Burana'}});
  assert.equal('eventDates' in await plain.readPage(OURPHILLY, {timeoutSeconds: 4}), false);
});

// ---------------------------------------------------------------------------
// Fleet replay: tower1, ODS main 04f0a835, round 092, live event search
// ---------------------------------------------------------------------------

let callId = 0;
function tool(guard, context, toolName, params, result) {
  const id = `r092-${++callId}`;
  guard.afterToolCall({toolName, toolCallId: id, params, result}, {...context, toolName, toolCallId: id});
}
const fetched = url => ({content: [{type: 'text', text: `Fetched ${url}`}], details: {status: 200, url, finalUrl: url, text: `Evidence from ${url}`}});
const failedFetch = status => ({isError: true, details: {status: 'error', tool: 'web_fetch', error: `Web fetch failed (${status})`}});
const searched = urls => ({details: {results: urls.map(url => ({url}))}});

// The recorded tool calls before the first answer (session transcript): three
// searches, five successful page reads and two refused ones. Both Lincoln
// Financial Field detail pages were cited without being opened.
function replayRound092(guard, context) {
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  tool(guard, context, 'web_search', {query: 'Philadelphia events October 2026 concerts shows'},
    searched(['https://www.visitphilly.com/articles/philadelphia/upcoming-concerts']));
  tool(guard, context, 'web_fetch', {url: 'https://www.visitphilly.com/articles/philadelphia/upcoming-concerts'},
    fetched('https://www.visitphilly.com/articles/philadelphia/upcoming-concerts'));
  tool(guard, context, 'web_fetch', {url: 'https://www.shazam.com/event/fd3f0070-aa12-4279-a1f5-ee02d8d0f31a'}, failedFetch(405));
  tool(guard, context, 'web_search', {query: '"Philadelphia" concert October 2026 "The Met" OR "Kimmel Center" OR "Lincoln Theatre"'},
    searched(['https://www.themetphilly.com/shows']));
  tool(guard, context, 'web_fetch', {url: 'https://www.themetphilly.com/shows'}, fetched('https://www.themetphilly.com/shows'));
  tool(guard, context, 'web_fetch', {url: 'https://www.classicalcalendar.com/philadelphia'}, failedFetch(403));
  tool(guard, context, 'web_search', {query: 'Philadelphia events October November 2026 specific dates venue'},
    searched(['https://www.lincolnfinancialfield.com/events/', OURPHILLY]));
  for (const url of ['https://www.lincolnfinancialfield.com/events/', OURPHILLY,
    'https://www.metrophiladelphia.com/calendar/day/when/2026-10-11/']) tool(guard, context, 'web_fetch', {url}, fetched(url));
}

function guardFor(pages) {
  const stub = recordedTransport(pages);
  const guard = createToolLoopGuard({hostCitationVerifier: createHostCitationVerifier({readPage: stub.readPage,
    clock: () => Date.UTC(2026, 8, 25, 19, 3)})});
  return {guard, ...stub};
}

test('fleet replay (tower1 r092): the year-less Lincoln Financial Field pages verify; the answer is delivered as is', async () => {
  const context = {agentId: 'pixel', runId: 'tower1-r092', sessionId: 'tower1-r092', sessionKey: 'agent:pixel:tower1-r092'};
  const {guard, calls} = guardFor(LFF_PAGES);
  replayRound092(guard, context);
  const event = {lastAssistantMessage: TOWER1_R092_FIRST_ANSWER};
  const outcome = await guard.verifyCitedPages(event, context);
  assert.deepEqual(outcome.results.map(({url, verified, yearless}) => ({url, verified, yearless})), [
    {url: LFF_PANTHERS_URL, verified: true, yearless: 'event-data'},
    {url: LFF_COWBOYS_URL, verified: true, yearless: 'event-data'},
  ]);
  assert.deepEqual(calls, [LFF_PANTHERS_URL, LFF_COWBOYS_URL], 'the Our Philly page was read by the model, not again');
  assert.equal(guard.beforeAgentFinalize(event, context), undefined, 'no revision: the links stay');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.text, undefined);
  assert.notEqual(delivery.status, 'failed');
});

test('fleet replay (tower1 r092): the same pages without JSON-LD verify by the window alone', async () => {
  const context = {agentId: 'pixel', runId: 'tower1-r092-text', sessionId: 'tower1-r092-text', sessionKey: 'agent:pixel:tower1-r092-text'};
  const {guard} = guardFor(Object.fromEntries(Object.entries(LFF_PAGES).map(([url, page]) =>
    [url, {...page, html: '<p>Lincoln Financial Field Events</p>'}])));
  replayRound092(guard, context);
  const outcome = await guard.verifyCitedPages({lastAssistantMessage: TOWER1_R092_FIRST_ANSWER}, context);
  assert.deepEqual(outcome.results.map(({verified, yearless}) => ({verified, yearless})),
    [{verified: true, yearless: 'window'}, {verified: true, yearless: 'window'}]);
});

test('fleet replay (tower1 r092): last season\'s pages still go to the source revision', async () => {
  const context = {agentId: 'pixel', runId: 'tower1-r092-stale', sessionId: 'tower1-r092-stale', sessionKey: 'agent:pixel:tower1-r092-stale'};
  const {guard} = guardFor({
    [LFF_PANTHERS_URL]: {...LFF_PAGES[LFF_PANTHERS_URL], html: withJsonLd(staleJsonLd(LFF_PANTHERS_EVENT_JSON_LD), '')},
    [LFF_COWBOYS_URL]: {...LFF_PAGES[LFF_COWBOYS_URL], text: `This event has passed.\n${LFF_COWBOYS_TEXT}`},
  });
  replayRound092(guard, context);
  const event = {lastAssistantMessage: TOWER1_R092_FIRST_ANSWER};
  const outcome = await guard.verifyCitedPages(event, context);
  assert.deepEqual(outcome.verified, []);
  assert.deepEqual(outcome.results.map(result => result.reason), ['anchors-not-found', 'anchors-not-found']);
  const revision = guard.beforeAgentFinalize(event, context);
  assert.equal(revision?.action, 'revise');
  assert.equal(revision.retry.instruction, UNREAD_SOURCES_REVISION_INSTRUCTION.join(JSON.stringify([LFF_PANTHERS_URL, LFF_COWBOYS_URL])));
});
