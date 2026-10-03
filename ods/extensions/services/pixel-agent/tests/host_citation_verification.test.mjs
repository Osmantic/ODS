import test from 'node:test';
import assert from 'node:assert/strict';
import {citationClaims, citationPageReadsAllowed, createHostCitationVerifier, HOST_CITATION_LIMITS,
  pageSupportsClaims} from '../plugin/citation-verification.mjs';
import {createPublicPageReader, createPublicWebExtractTool} from '../plugin/web-extract.mjs';
import {createCompletionAssurance, UNREAD_SOURCE_MARKER, UNREAD_SOURCE_MARKER_PT, UNREAD_SOURCE_NOTE,
  UNREAD_SOURCE_NOTE_PT, UNREAD_SOURCES_REVISION_INSTRUCTION} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE} from '../plugin/progress-finalization.mjs';
import {TOWER1_R060_FINAL_ANSWER, TOWER1_R060_FIRST_ANSWER, TOWER3_FIRST_ANSWER,
  TOWER3_REVISED_ANSWER} from './fixtures/philly-events-detail-links.mjs';

// The fleet journey prompt (live event search), identical on every host.
const FLEET_PROMPT = 'Today is 2026-09-25. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL. ' +
  'Exclude undated listings and past events. Explain any unavailable result honestly. Do not create files.\n\n' +
  '[ODS Portal delivery requirement: Answer the owner\'s complete message above. If it asks for exact text, copy that full exact text. ' +
  'Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const TT = 'https://taylor-tomlinson.ticketsphiladelphia.net/helium-comedy-club-philadelphia-nov-01-2026-08-45-pm.php';
const SA = 'https://stayin-alive-one-night-of-the-bee-gees.ticketsphiladelphia.net/lansdowne-theater-oct-15-2026-07-30-pm.php';
const GRITTY = 'https://www.xfinitymobilearena.com/events/detail/gritty-5k-presented-by-penn-medicine';
const CAPITALS = 'https://www.xfinitymobilearena.com/events/detail/flyers-capitals-9-26-26';
const TEDDY = 'https://www.xfinitymobilearena.com/events/detail/teddy-swims';
const MOTIONLESS = 'https://www.xfinitymobilearena.com/events/detail/motionless-in-white';
const LFF_MONTH = 'https://www.lincolnfinancialfield.com/events/month/2026-09/';

// Page text shaped like the real pages behind those links (their extracted
// text starts with the page title; the ticket widget adds no time).
const NAV = '\nTickets Philadelphia\n\n Concerts\n\n New\n\n Alestorm\nBanshee\nBarbara Kingsolver\nBlack Tie Optional\nBob Dylan\n';
const PAGES = {
  [TT]: `<title>Buy Taylor Tomlinson Philadelphia tickets - Nov 01, 2026 Taylor Tomlinson Helium Comedy Club - Philadelphia seating chart</title>${NAV}`,
  [SA]: `<title>Buy Stayin' Alive - One Night of the Bee Gees Lansdowne tickets - Oct 15, 2026 Stayin' Alive - One Night of the Bee Gees Lansdowne Theater seating chart</title>${NAV}`,
  [GRITTY]: '<title>Gritty 5K Presented by Penn Medicine | Xfinity Mobile Arena</title>\nEvents &amp; Tickets\nAll Events\nPhiladelphia Flyers\n' +
    '<h1>Gritty 5K Presented by Penn Medicine</h1>\nSIGN UP\nDate\n Sep 26, 2026\nEvent Starts\n 7:30 AM\nOn Sale\n On Sale Now\n Saturday, Sep 26\n',
  [CAPITALS]: '<title>Capitals vs. Flyers (Preseason) | Xfinity Mobile Arena</title>\nEvents &amp; Tickets\nAll Events\nPhiladelphia Flyers\n' +
    '<h1>Capitals vs. Flyers (Preseason)</h1>\nBuy Tickets\nDate\n Sep 26, 2026\nEvent Starts\n 5:00 PM\nOn Sale\n On Sale Now\n',
  [TEDDY]: '<title>Teddy Swims | Xfinity Mobile Arena</title>\n<h1>Teddy Swims: The Ugly Tour</h1>\nDate\n Oct 10, 2026\nEvent Starts\n 7:00 PM\n',
  [MOTIONLESS]: '<title>Motionless In White | Xfinity Mobile Arena</title>\n<h1>Motionless In White - The Sweat and Blood Tour</h1>\nDate\n Oct 31, 2026\nEvent Starts\n 6:30 PM\n',
};

// A stubbed transport behind the real guarded page reader: each entry may set
// status, contentType, body, finalUrl, error (the guard throws) or hang (never
// answers until the reader's signal aborts).
function transport(entries) {
  const calls = [];
  const released = [];
  const guardedFetch = async options => {
    calls.push(options);
    const entry = typeof entries[options.url] === 'string' ? {body: entries[options.url]} : entries[options.url];
    if (!entry) throw new Error(`unexpected fetch ${options.url}`);
    if (entry.hang) {
      await new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true}));
    }
    if (entry.error) throw entry.error;
    return {
      response: new Response(entry.body ?? '', {status: entry.status ?? 200,
        headers: {'Content-Type': entry.contentType ?? 'text/html; charset=UTF-8'}}),
      finalUrl: entry.finalUrl ?? options.url,
      release: () => released.push(options.url),
    };
  };
  const deps = {
    guardedFetch,
    readResponseText: async (response, {maxBytes}) => ({text: await response.text(), truncated: false, maxBytes}),
    extractBasicHtmlContent: async ({html}) => ({text: html.replace(/<[^>]+>/g, '\n').replace(/&amp;/g, '&')}),
  };
  return {calls, released, deps, readPage: createPublicPageReader(deps)};
}

const verifierFor = (entries, options = {}) => {
  const stub = transport(entries);
  return {...stub, verifier: createHostCitationVerifier({readPage: stub.readPage, ...options})};
};
const claimsOk = (answer, url, page, options) => pageSupportsClaims(page, citationClaims(answer, url, options));

// ---------------------------------------------------------------------------
// The anchor rule
// ---------------------------------------------------------------------------

test('claim anchors come from the answer next to each citation, never from the URL', () => {
  assert.deepEqual(citationClaims(TOWER3_FIRST_ANSWER, TT),
    [{tokens: ['taylor', 'tomlinson'], date: {y: 2026, m: 11, d: 1}, time: 20 * 60 + 45, full: true}],
    'the "Event 1" heading and the labelled Date line; the Notes date is not the attributed one');
  assert.deepEqual(citationClaims(TOWER3_FIRST_ANSWER, SA),
    [{tokens: ['stayin', 'alive', 'one', 'night', 'bee', 'gees'], date: {y: 2026, m: 10, d: 15}, time: 19 * 60 + 30, full: true}]);
  // tower1 round 060: slug URLs, display titles in the answer.
  assert.deepEqual(citationClaims(TOWER1_R060_FINAL_ANSWER, GRITTY),
    [{tokens: ['gritty', '5k', 'penn', 'medicine'], date: {y: 2026, m: 9, d: 26}, full: true}]);
  assert.deepEqual(citationClaims(TOWER1_R060_FINAL_ANSWER, CAPITALS),
    [{tokens: ['capitals', 'flyers'], date: {y: 2026, m: 9, d: 26}, full: true}],
    'the parenthetical qualifier is dropped; the slug order flyers-capitals does not matter');
  // A citation with nothing but the link attributes no claim, even when its
  // slug spells a title and a date.
  for (const answer of [`Source: ${TT}`, `- ${CAPITALS}\n- ${GRITTY}`, `Sources:\n\n1. <${TT}>`]) {
    assert.deepEqual(citationClaims(answer, TT).filter(claim => claim.full), [], answer);
  }
});

test('anchor match: the page shows the answer\'s title and date in its own formats', () => {
  const answer = `## **Event 1: Taylor Tomlinson (Comedy)**\n- **Date:** November 1, 2026 at 8:45 PM\n- **Source URL:** ${TT}`;
  for (const page of [
    PAGES[TT],
    'Buy Taylor Tomlinson Philadelphia tickets - Nov 01st, 2026 Taylor Tomlinson Helium Comedy Club - Philadelphia seating chart',
    'Taylor Tomlinson\nSaturday, November 1, 2026 · 8:45 PM · Helium Comedy Club',
    'TAYLOR TOMLINSON — 2026-11-01 20:45',
    'Taylor Tomlinson: Sat 1 Nov 2026, doors 7:30 pm, show 8:45 pm',
    'Taylor Tomlinson live on 11/01/2026? No: see Sat, Nov. 1, 2026 at 8:45 p.m.',
  ]) assert.deepEqual(claimsOk(answer, TT, page), {supported: true}, page);
});

test('anchor miss: a wrong date, year, title, distance, time, echo or error page does not count', () => {
  const answer = `## **Event 1: Taylor Tomlinson (Comedy)**\n- **Date:** November 1, 2026 at 8:45 PM\n- **Source URL:** ${TT}`;
  const filler = ' Seating chart and venue information.'.repeat(20);
  for (const [page, why] of [
    ['Buy Taylor Tomlinson Philadelphia tickets - Nov 02, 2026 Helium Comedy Club', 'another day'],
    ['Buy Taylor Tomlinson Philadelphia tickets - Nov 01, 2025 Helium Comedy Club', 'another year'],
    ['Taylor Tomlinson at Helium Comedy Club - Sat, Nov 01 - 8:45 PM', 'no year on the page for a dated claim'],
    ['Buy Taylor Swift Philadelphia tickets - Nov 01, 2026 Helium Comedy Club', 'another performer'],
    [`Taylor Tomlinson Tickets.${filler} Upcoming: Nov 01, 2026`, 'title and date too far apart'],
    ['Taylor Tomlinson - Nov 01, 2026 - 6:30 PM', 'a different time next to the date'],
    ['Taylor Tomlinson - 11/01/2026', 'an all-numeric date that could be January 11'],
    ['Oops! /helium-comedy-club-philadelphia-nov-01-2026-08-45-pm.php on taylor-tomlinson.ticketsphiladelphia.net ' +
      'https://taylor-tomlinson.ticketsphiladelphia.net/ taylor-tomlinson', 'only the URL, host and slug echoed'],
    ['Page not found. Taylor Tomlinson - Nov 01, 2026', 'an error page'],
    ['Taylor Tomlinson - Nov 01, 2026 - POSTPONED', 'cancelled or postponed next to the date'],
    ['', 'an empty page'],
  ]) assert.equal(claimsOk(answer, TT, page).supported, false, why);
  // On a listing, a neighbouring row cannot lend its date: another date sits
  // between the claimed title and the matching date.
  for (const listing of [
    'Taylor Tomlinson - Nov 02, 2026 - 6:30 PM\nIan Fidance - Nov 01, 2026 - 8:45 PM',
    'Nov 01, 2026 · 8:45 PM: Ian Fidance\nNov 02, 2026 · 6:30 PM: Taylor Tomlinson',
  ]) assert.equal(claimsOk(answer, TT, listing).supported, false, listing);
  assert.equal(claimsOk(answer, TT, 'Ian Fidance - Oct 30, 2026 - 7:30 PM\nTaylor Tomlinson - Nov 01, 2026 - 8:45 PM\n' +
    'Ian Fidance - Nov 05, 2026 - 7:30 PM').supported, true, 'its own row on a listing does count');
  // The title's words must stand together, not scattered across the page.
  assert.equal(claimsOk(answer, TT, `Taylor Swift.${' Seats.'.repeat(20)} Nov 01, 2026: Tomlinson Hall`).supported, false);
});

test('each citation owns only its own sentence, list item, table row or section', () => {
  const prose = `Taylor Tomlinson performs at Helium Comedy Club on November 1, 2026 (${TT}). ` +
    `The Bee Gees tribute Stayin' Alive plays Lansdowne on Oct. 15, 2026 (${SA}).`;
  assert.deepEqual(citationClaims(prose, TT)[0].date, {y: 2026, m: 11, d: 1});
  assert.deepEqual(citationClaims(prose, SA)[0].date, {y: 2026, m: 10, d: 15}, 'the "Oct." abbreviation does not end the sentence');
  assert.equal(claimsOk(prose, SA, PAGES[TT]).supported, false);
  assert.equal(claimsOk(prose, TT, PAGES[TT]).supported, true);

  const list = `1. **Taylor Tomlinson** — Nov 1, 2026 — ${TT}\n2. **Stayin' Alive - One Night of the Bee Gees** — Oct 15, 2026 — ${SA}`;
  assert.deepEqual(citationClaims(list, TT)[0].tokens, ['taylor', 'tomlinson']);
  assert.deepEqual(citationClaims(list, SA)[0].date, {y: 2026, m: 10, d: 15});

  const table = `| Event | Date | Source |\n|---|---|---|\n| Taylor Tomlinson | Nov 1, 2026 | [tickets](${TT}) |\n` +
    `| Gritty 5K Presented by Penn Medicine | Sep 26, 2026 | ${GRITTY} |`;
  assert.deepEqual(citationClaims(table, TT), [{tokens: ['taylor', 'tomlinson'], date: {y: 2026, m: 11, d: 1}, full: true}]);
  assert.deepEqual(citationClaims(table, GRITTY)[0].tokens, ['gritty', '5k', 'penn', 'medicine']);

  // A bare link line never borrows another citation's claim from its block.
  const shared = `### Tickets\n- **Taylor Tomlinson**, Nov 1, 2026: ${SA}\n- Also: ${TT}`;
  assert.deepEqual(citationClaims(shared, TT).filter(claim => claim.full), []);
  // A nested item takes its parent's title and date.
  const nested = `- **Capitals vs. Flyers (Preseason)**, September 26, 2026\n  - Official page: ${CAPITALS}`;
  assert.deepEqual(citationClaims(nested, CAPITALS), [{tokens: ['capitals', 'flyers'], date: {y: 2026, m: 9, d: 26}, full: true}]);
  // Every anchored occurrence of one URL must hold on its page.
  const twice = `- **Gritty 5K Presented by Penn Medicine**, Sep 26, 2026: ${GRITTY}\n\n- **Gritty 5K Presented by Penn Medicine**, Sep 27, 2026: ${GRITTY}`;
  assert.equal(claimsOk(twice, GRITTY, PAGES[GRITTY]).supported, false);
});

test('Portuguese answers: day-first dates, Portuguese months and 24-hour times', () => {
  const answer = 'Encontrei dois eventos:\n\n1. **Festa de Outono** — 05/11/2026, Praça do Comércio. Fonte: https://www.visitlisboa.com/pt-pt/eventos/festa\n' +
    '2. **Concerto no Coliseu** — 17 de outubro de 2026, às 21h30. Fonte: [Coliseu](https://www.coliseulisboa.com/agenda/concerto)';
  const festa = 'https://www.visitlisboa.com/pt-pt/eventos/festa';
  const coliseu = 'https://www.coliseulisboa.com/agenda/concerto';
  assert.deepEqual(citationClaims(answer, festa, {portuguese: true}),
    [{tokens: ['festa', 'outono'], date: {y: 2026, m: 11, d: 5}, full: true}], '05/11 is 5 November in Portuguese');
  assert.deepEqual(citationClaims(answer, festa)[0].date, {y: 2026, m: 5, d: 11}, 'and 11 May in English');
  assert.deepEqual(citationClaims(answer, coliseu, {portuguese: true}),
    [{tokens: ['coliseu'], date: {y: 2026, m: 10, d: 17}, time: 21 * 60 + 30, full: true}]);
  for (const page of ['Festa de Outono · 5 de novembro de 2026', 'Festa de Outono — 5 nov. 2026', 'FESTA DE OUTONO 2026-11-05']) {
    assert.equal(claimsOk(answer, festa, page, {portuguese: true}).supported, true, page);
  }
  assert.equal(claimsOk(answer, coliseu, 'Coliseu dos Recreios: sábado, 17 de outubro de 2026 · 21:30', {portuguese: true}).supported, true);
  assert.equal(claimsOk(answer, coliseu, 'Coliseu dos Recreios: 17 de outubro de 2026 · 18h00', {portuguese: true}).supported, false);
  assert.equal(claimsOk(answer, festa, 'Festa de Outono · 11 de maio de 2026', {portuguese: true}).supported, false);
  assert.equal(claimsOk(answer, festa, 'Festa de Outono · Página não encontrada · 5 de novembro de 2026', {portuguese: true}).supported, false);
  // "out" (outubro) is not read from ordinary English text.
  assert.equal(claimsOk('**Coliseu** — 17 out 2026: https://c.example.org/x', 'https://c.example.org/x',
    'Coliseu: 17 out of 20 seats', {portuguese: true}).supported, false);
});

test('a claim without a date is anchored by its numbers', () => {
  const answer = 'The **Radeon RX 9070** has 16 GB of memory and a 220 W board power (https://www.amd.com/en/products/rx-9070).';
  const url = 'https://www.amd.com/en/products/rx-9070';
  assert.deepEqual(citationClaims(answer, url), [{tokens: ['radeon', 'rx'], numbers: ['9070', '16', '220'], full: true}]);
  assert.equal(claimsOk(answer, url, 'AMD Radeon RX 9070 · Memory: 16GB GDDR6 · Typical Board Power: 220W').supported, true);
  assert.equal(claimsOk(answer, url, 'AMD Radeon RX 9070 · Memory: 16GB GDDR6 · Typical Board Power: 304W').supported, false);
});

// ---------------------------------------------------------------------------
// The bounded host read (real reader, stubbed transport)
// ---------------------------------------------------------------------------

test('host reads use the extract tool\'s guarded path and options, in parallel', async () => {
  const {verifier, calls, released} = verifierFor({[TT]: PAGES[TT], [SA]: PAGES[SA]});
  const outcome = await verifier.verify({answer: TOWER3_FIRST_ANSWER, urls: [TT, SA]});
  assert.deepEqual(outcome.verified, [{url: TT, finalUrl: TT}, {url: SA, finalUrl: SA}]);
  assert.equal(outcome.fetched, 2);
  assert.deepEqual(released.sort(), [SA, TT].sort(), 'every guarded response is released');
  const extract = transport({'https://docs.example.org/x': 'Path.exists'});
  await createPublicWebExtractTool(extract.deps).execute('x', {url: 'https://docs.example.org/x', query: 'Path.exists'});
  for (const call of calls) {
    assert.equal(call.useEnvProxy, false);
    assert.equal(call.maxRedirects, 3);
    assert.equal(call.timeoutSeconds, Math.ceil(HOST_CITATION_LIMITS.budgetMs / 1000));
    assert.ok(call.signal instanceof AbortSignal);
    assert.deepEqual(call.init, extract.calls[0].init, 'same Accept headers as pixel_ods_web_extract');
  }
});

test('a 404, a private or foreign redirect, a guard denial and a non-HTML document are not reads', async () => {
  const answer = TOWER1_R060_FINAL_ANSWER;
  for (const [entry, reason] of [
    [{status: 404, body: PAGES[GRITTY]}, 'http-status'],
    [{finalUrl: 'http://127.0.0.1/events/detail/gritty', body: PAGES[GRITTY]}, 'blocked'],
    [{finalUrl: 'http://[::1]/gritty', body: PAGES[GRITTY]}, 'blocked'],
    [{error: new Error('Blocked: resolves to private/internal/special-use IP address')}, 'blocked'],
    [{finalUrl: 'https://www.ticketmaster.com/gritty-5k', body: PAGES[GRITTY]}, 'redirected-elsewhere'],
    [{finalUrl: 'https://www.xfinitymobilearena.com/', body: PAGES[GRITTY]}, 'redirected-elsewhere'],
    [{contentType: 'application/pdf', body: PAGES[GRITTY]}, 'content-type'],
    [{contentType: 'application/json', body: JSON.stringify({title: 'Gritty 5K Presented by Penn Medicine', date: 'Sep 26, 2026'})}, 'content-type'],
    [{body: '<title>Xfinity Mobile Arena</title> Sep 26, 2026'}, 'anchors-not-found'],
  ]) {
    const {verifier, released} = verifierFor({[GRITTY]: entry, [CAPITALS]: PAGES[CAPITALS]});
    const outcome = await verifier.verify({answer, urls: [GRITTY, CAPITALS]});
    const gritty = outcome.results.find(result => result.url === GRITTY);
    assert.equal(gritty.verified, false, reason);
    assert.equal(gritty.reason, reason);
    assert.deepEqual(outcome.verified.map(entry_ => entry_.url), [CAPITALS], `${reason}: the other page still verifies`);
    assert.equal(released.length, entry.error ? 1 : 2);
  }
  // A same-site canonical redirect is the cited page.
  const {verifier} = verifierFor({[GRITTY]: {finalUrl: `${GRITTY.replace('https://www.', 'https://')}/`, body: PAGES[GRITTY]}});
  assert.equal((await verifier.verify({answer, urls: [GRITTY]})).verified.length, 1);
});

test('the total time budget bounds the reads; finished pages keep their result', async () => {
  const {verifier, released} = verifierFor({[GRITTY]: {hang: true}, [CAPITALS]: PAGES[CAPITALS]}, {limits: {budgetMs: 150}});
  const started = performance.now();
  const outcome = await verifier.verify({answer: TOWER1_R060_FINAL_ANSWER, urls: [GRITTY, CAPITALS]});
  const elapsed = performance.now() - started;
  assert.ok(elapsed < 1000, `returned after ${elapsed} ms`);
  assert.equal(outcome.results.find(result => result.url === GRITTY).reason, 'timeout');
  assert.deepEqual(outcome.verified.map(entry => entry.url), [CAPITALS]);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(released, [CAPITALS], 'the aborted read never produced a response to release');
});

test('no fetch for more than four URLs, missing anchors or anchors that a query string could echo', async () => {
  const urls = [TT, SA, GRITTY, CAPITALS, TEDDY];
  const answer = urls.map((url, i) => `- **Event ${i}**, Oct ${i + 1}, 2026: ${url}`).join('\n');
  const many = verifierFor({});
  assert.equal((await many.verifier.verify({answer, urls})).skipped, 'too-many-citations');
  assert.equal(HOST_CITATION_LIMITS.maxUrls, 4);
  const bare = verifierFor({});
  assert.equal((await bare.verifier.verify({answer: `Sources: ${TT} and ${SA}`, urls: [TT, SA]})).skipped, 'insufficient-anchors');
  const search = 'https://tickets.example.com/search?q=taylor+tomlinson';
  const echo = verifierFor({});
  assert.equal((await echo.verifier.verify({answer: `- **Taylor Tomlinson**, Nov 1, 2026: ${search}`, urls: [search]})).skipped, 'query-reflection');
  for (const stub of [many, bare, echo]) assert.equal(stub.calls.length, 0);
});

test('operator configuration that disables or denies page reads disables host reads', () => {
  const ods = {tools: {web: {fetch: {enabled: true}}, profile: 'coding',
    sandbox: {tools: {allow: ['read', 'web_search', 'web_fetch', 'pixel_ods_web_extract']}}},
    agents: {list: [{id: 'pixel', tools: {deny: ['discord', 'message', 'pixel_web_browse']}}]}};
  assert.equal(citationPageReadsAllowed(ods), true);
  assert.equal(citationPageReadsAllowed(undefined), true);
  assert.equal(citationPageReadsAllowed({tools: {web: {fetch: {enabled: false}}}}), false);
  assert.equal(citationPageReadsAllowed({tools: {deny: ['web_fetch']}}), false);
  assert.equal(citationPageReadsAllowed({agents: {list: [{id: 'pixel', tools: {deny: ['group:web']}}]}}), false);
  assert.equal(citationPageReadsAllowed({agents: {list: [{id: 'other', tools: {deny: ['*']}}]}}), true);
  assert.equal(citationPageReadsAllowed({tools: {sandbox: {tools: {allow: ['read', 'web_fetch']}}}}), false);
  assert.equal(citationPageReadsAllowed({tools: {allow: ['group:web']}}), true);
});

// ---------------------------------------------------------------------------
// Finalization (tool-loop guard) and fleet replays
// ---------------------------------------------------------------------------

let callId = 0;
function tool(guard, context, toolName, params, result) {
  const id = `call-${++callId}`;
  guard.afterToolCall({toolName, toolCallId: id, params, result}, {...context, toolName, toolCallId: id});
}
const fetched = (url, finalUrl = url) => ({content: [{type: 'text', text: `Fetched ${url}`}],
  details: {status: 200, url, finalUrl, text: `Evidence from ${finalUrl}`}});
const failedFetch = status => ({isError: true, details: {status: 'error', tool: 'web_fetch', error: `Web fetch failed (${status}): Not Acceptable`}});
const searched = urls => ({details: {results: urls.map(url => ({url}))}});

function guardWith(entries, {allowed = () => true, limits, verifierLimits} = {}) {
  const stub = transport(entries);
  const logs = [];
  const guard = createToolLoopGuard({limits, info: message => logs.push(message),
    hostCitationVerifier: createHostCitationVerifier({readPage: stub.readPage, allowed, limits: verifierLimits})});
  return {guard, logs, ...stub};
}

// tower3, build 7402eb38: successful page reads in the turn before the answer.
const TOWER3_READS = [
  ['https://www.visitphilly.com/articles/philadelphia/upcoming-concerts', 'https://www.visitphilly.com/articles/philadelphia/upcoming-concerts/'],
  ['https://ma.to/event/harry-potter-concert-18-oct-2026'],
  ['https://philadelphiaevents.guide/comedy/ian-fidance/'],
  ['https://www.philadelphia-theater.com/'],
  ['https://americanarenas.com/city/philadelphia-events/november/'],
  ['https://lansdowne-theater.ticketsphiladelphia.net/'],
  ['https://helium-comedy-club-philadelphia.ticketsphiladelphia.net/'],
];
function replayTower3(guard, context) {
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  tool(guard, context, 'web_search', {query: 'Philadelphia events October 2026 concerts shows'}, searched([TT, SA]));
  for (const [url, finalUrl] of TOWER3_READS) tool(guard, context, 'web_fetch', {url}, fetched(url, finalUrl));
  tool(guard, context, 'web_fetch', {url: 'https://www.shazam.com/event/fd3f0070-aa12-4279-a1f5-ee02d8d0f31a'}, failedFetch(404));
}

test('fleet replay (tower3): both unread detail links are host-verified; the answer is delivered as is', async () => {
  const context = {agentId: 'pixel', runId: 'tower3-events', sessionId: 'tower3-session', sessionKey: 'agent:pixel:tower3'};
  const {guard, calls, logs} = guardWith({[TT]: PAGES[TT], [SA]: PAGES[SA]});
  replayTower3(guard, context);
  const event = {lastAssistantMessage: TOWER3_FIRST_ANSWER};
  const outcome = await guard.verifyCitedPages(event, context);
  assert.deepEqual(outcome.verified.map(entry => entry.url), [TT, SA]);
  assert.ok(outcome.elapsedMs < HOST_CITATION_LIMITS.budgetMs);
  assert.equal(guard.beforeAgentFinalize(event, context), undefined, 'no revision: no extra model turn');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.text, undefined, 'no host text replaces or amends the answer');
  assert.notEqual(delivery.status, 'failed');
  assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: TOWER3_FIRST_ANSWER}}), undefined);
  assert.equal(calls.length, 2);
  // Recorded as host verification, not as model reads.
  const [record] = guard.citationVerificationForRun(context.runId);
  assert.deepEqual(record.urls, [TT, SA]);
  assert.equal(record.fetched, 2);
  assert.match(logs[0], /^Pixel host-verified 2\/2 cited page\(s\) for run tower3-events in \d+ ms$/);
  // A later finalization of the same answer neither refetches nor changes it.
  assert.equal(await guard.verifyCitedPages(event, context), undefined);
  assert.equal(calls.length, 2);
});

test('fleet replay (tower3): pages without the anchors fall through to the revision and partial delivery', async () => {
  const context = {agentId: 'pixel', runId: 'tower3-miss', sessionId: 'tower3-miss-session', sessionKey: 'agent:pixel:tower3-miss'};
  const {guard, calls} = guardWith({[TT]: PAGES[TT].replaceAll('Nov 01', 'Nov 02'), [SA]: {status: 404}});
  replayTower3(guard, context);
  const first = {lastAssistantMessage: TOWER3_FIRST_ANSWER};
  assert.deepEqual((await guard.verifyCitedPages(first, context)).verified, []);
  const revision = guard.beforeAgentFinalize(first, context);
  assert.equal(revision?.action, 'revise');
  assert.equal(revision.retry.instruction, UNREAD_SOURCES_REVISION_INSTRUCTION.join(JSON.stringify([TT, SA])));
  assert.match(revision.retry.instruction, /prefer that item's own page over a shared listing page/);
  // The revision reads nothing and repeats the links (the fleet outcome).
  const revised = {lastAssistantMessage: TOWER3_REVISED_ANSWER};
  assert.equal(await guard.verifyCitedPages(revised, context), guard.citationVerificationForRun(context.runId).at(-1));
  assert.equal(guard.citationVerificationForRun(context.runId).at(-1).skipped, 'already-attempted');
  assert.equal(calls.length, 2, 'no second host read of the same URLs');
  assert.equal(guard.beforeAgentFinalize(revised, context)?.action, 'finalize');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.text, `${TOWER3_REVISED_ANSWER.split(TT).join(UNREAD_SOURCE_MARKER).split(SA).join(UNREAD_SOURCE_MARKER)}\n\n${UNREAD_SOURCE_NOTE}`);
});

// tower1, build 7402eb38, round 060: the pages read before the final answer.
function replayTower1(guard, context, {stop = false} = {}) {
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  tool(guard, context, 'web_search', {query: 'Philadelphia events September October November 2026 concerts sports'},
    searched(['https://www.xfinitymobilearena.com/events', LFF_MONTH]));
  tool(guard, context, 'web_fetch', {url: LFF_MONTH.slice(0, -1)}, fetched(LFF_MONTH.slice(0, -1), LFF_MONTH));
  tool(guard, context, 'web_fetch', {url: 'https://www.xfinitymobilearena.com/events'}, fetched('https://www.xfinitymobilearena.com/events'));
  tool(guard, context, 'web_fetch', {url: 'https://www.citizensbankpark.com/events'}, fetched('https://www.citizensbankpark.com/events',
    'https://www.citizensbank.com/about-us/our-company/corporate-sponsorships/citizens-bank-park.aspx'));
  if (stop) {
    // The search allowance was spent; both searches were refused.
    for (const query of ['Philadelphia Eagles schedule 2026 October home games', 'Xfinity Mobile Arena Philadelphia concerts October 2026']) {
      tool(guard, context, 'web_search', {query}, {isError: true, details: {status: 'blocked', deniedReason: 'plugin-before-tool-call'}});
    }
  }
  // web_fetch's own request was refused by the site (HTTP 406) for both
  // detail pages; the guarded extraction path reads them.
  tool(guard, context, 'web_fetch', {url: CAPITALS, maxChars: 3000}, failedFetch(406));
  tool(guard, context, 'web_fetch', {url: GRITTY, maxChars: 3000}, failedFetch(406));
  if (stop) tool(guard, context, 'web_fetch', {url: 'https://www.philadelphiaeagles.com/tickets/single-game'}, failedFetch(404));
}

test('fleet replay (tower1 r060): display titles and dates verify the slug-named detail pages', async () => {
  const context = {agentId: 'pixel', runId: 'tower1-r060', sessionId: 'tower1-session', sessionKey: 'agent:pixel:tower1'};
  const {guard, calls} = guardWith({[GRITTY]: PAGES[GRITTY], [CAPITALS]: PAGES[CAPITALS]});
  replayTower1(guard, context);
  const event = {lastAssistantMessage: TOWER1_R060_FINAL_ANSWER};
  assert.deepEqual((await guard.verifyCitedPages(event, context)).verified.map(entry => entry.url), [GRITTY, CAPITALS]);
  assert.equal(guard.beforeAgentFinalize(event, context), undefined);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.text, undefined);
  assert.notEqual(delivery.status, 'failed');
  assert.deepEqual(calls.map(call => call.url), [GRITTY, CAPITALS]);
});

test('fleet replay (tower1 r060, tool limit): the answer turn no longer lists host-verified pages as unread', async () => {
  const context = {agentId: 'pixel', runId: 'tower1-r060-stop', sessionId: 'tower1-stop-session', sessionKey: 'agent:pixel:tower1-stop'};
  const {guard} = guardWith({[GRITTY]: PAGES[GRITTY], [CAPITALS]: PAGES[CAPITALS]});
  replayTower1(guard, context, {stop: true});
  guard.observeModelEnd({}, context);
  guard.observeModelCall({}, context);
  assert.deepEqual(guard.beforeToolCall({toolName: 'web_fetch', toolCallId: 'late', params: {url: GRITTY}},
    {...context, toolName: 'web_fetch', toolCallId: 'late'}), {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION});
  guard.observeModelEnd({}, context);
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  const event = {lastAssistantMessage: TOWER1_R060_FINAL_ANSWER};
  assert.equal((await guard.verifyCitedPages(event, context)).verified.length, 2);
  assert.equal(guard.beforeAgentFinalize(event, context), undefined);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed', 'the tool limit still stopped this response');
  assert.equal(delivery.text, `${TOWER1_R060_FINAL_ANSWER}\n\n${PROGRESS_FINALIZATION_NOTE}`,
    'without "These linked pages were not read successfully..." for the verified detail pages');
});

test('fleet replay (tower1 r060, first answer): host verification would have ended the turn before the revision', async () => {
  const context = {agentId: 'pixel', runId: 'tower1-r060-first', sessionId: 'tower1-first-session', sessionKey: 'agent:pixel:tower1-first'};
  const {guard} = guardWith({[TEDDY]: PAGES[TEDDY], [MOTIONLESS]: PAGES[MOTIONLESS]});
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  const rams = 'https://www.lincolnfinancialfield.com/events/los-angeles-rams-vs-philadelphia-eagles-2';
  tool(guard, context, 'web_search', {query: 'Philadelphia Eagles vs Rams October 4 2026 Lincoln Financial Field'}, searched([rams, TEDDY]));
  tool(guard, context, 'web_fetch', {url: TEDDY, maxChars: 4000}, failedFetch(406));
  tool(guard, context, 'web_fetch', {url: rams, maxChars: 4000}, fetched(rams, `${rams}/`));
  const event = {lastAssistantMessage: TOWER1_R060_FIRST_ANSWER};
  assert.deepEqual((await guard.verifyCitedPages(event, context)).verified.map(entry => entry.url), [TEDDY, MOTIONLESS]);
  assert.equal(guard.beforeAgentFinalize(event, context), undefined);
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, undefined);
});

test('no host read when web access is disabled, the allowance is short, or there are too many citations', async () => {
  for (const [label, options, answer, reason] of [
    ['web disabled', {allowed: () => false}, TOWER3_FIRST_ANSWER, 'web-disabled'],
    ['allowance', {limits: {fetch: 1, total: 1}}, TOWER3_FIRST_ANSWER, 'web-allowance'],
    ['too many', {}, `${TOWER3_FIRST_ANSWER}\n\n${[GRITTY, CAPITALS, TEDDY].map((url, i) =>
      `- **Event ${i}**, Oct ${i + 1}, 2026: ${url}`).join('\n')}`, 'too-many-citations'],
  ]) {
    const context = {agentId: 'pixel', runId: `skip-${label}`, sessionId: `skip-${label}`, sessionKey: `agent:pixel:skip-${label}`};
    const {guard, calls} = guardWith({[TT]: PAGES[TT], [SA]: PAGES[SA]}, options);
    replayTower3(guard, context);
    const event = {lastAssistantMessage: answer};
    assert.equal((await guard.verifyCitedPages(event, context)).skipped, reason, label);
    assert.equal(calls.length, 0, label);
    assert.equal(guard.beforeAgentFinalize(event, context)?.action, 'revise', `${label}: the existing path applies unchanged`);
  }
  // Nothing is attempted for a request that did not ask for source reads, or
  // in a run whose web tools returned nothing.
  for (const [prompt, withWeb] of [['Summarize these events for me.', true], [FLEET_PROMPT, false]]) {
    const context = {agentId: 'pixel', runId: `none-${withWeb}`, sessionId: `none-${withWeb}`, sessionKey: `agent:pixel:none-${withWeb}`};
    const {guard, calls} = guardWith({[TT]: PAGES[TT], [SA]: PAGES[SA]});
    guard.observeRun(context, 'pixel', {prompt});
    if (withWeb) tool(guard, context, 'web_fetch', {url: TOWER3_READS[5][0]}, fetched(TOWER3_READS[5][0]));
    assert.equal(await guard.verifyCitedPages({lastAssistantMessage: TOWER3_FIRST_ANSWER}, context), undefined);
    assert.equal(calls.length, 0);
  }
  // A guard without the verifier (tests, other agents) is unchanged.
  const plain = createToolLoopGuard();
  assert.equal(await plain.verifyCitedPages({lastAssistantMessage: TOWER3_FIRST_ANSWER},
    {agentId: 'pixel', runId: 'plain', sessionId: 'plain'}), undefined);
});

test('Portuguese request: verified pages keep the answer; unverified ones get the Portuguese marker', async () => {
  const prompt = 'Pesquise na internet eventos em Lisboa nos próximos 30 dias e abra as fontes antes de citar.';
  const agenda = 'https://www.visitlisboa.com/pt-pt/eventos';
  const festa = 'https://www.visitlisboa.com/pt-pt/eventos/festa-de-outono';
  const coliseu = 'https://www.coliseulisboa.com/agenda/concerto-de-outono';
  const answer = 'Encontrei dois eventos:\n\n1. **Festa de Outono** — 10/10/2026, Praça do Comércio. Fonte: ' + festa + '\n' +
    '2. **Concerto no Coliseu** — 17/10/2026, às 21h30. Fonte: [Coliseu](' + coliseu + ')\n\nAgenda consultada: ' + agenda;
  const pages = {
    [festa]: '<h1>Festa de Outono</h1> Praça do Comércio · sábado, 10 de outubro de 2026',
    [coliseu]: '<h1>Concerto no Coliseu</h1> 17 out. 2026 · 21:30 · Coliseu dos Recreios',
  };
  for (const [label, entries] of [['verified', pages], ['unverified', {...pages, [coliseu]: {status: 404}}]]) {
    const context = {agentId: 'pixel', runId: `pt-${label}`, sessionId: `pt-${label}`, sessionKey: `agent:pixel:pt-${label}`};
    const {guard} = guardWith(entries);
    guard.observeRun(context, 'pixel', {prompt});
    tool(guard, context, 'web_fetch', {url: agenda}, fetched(agenda));
    const event = {lastAssistantMessage: answer};
    await guard.verifyCitedPages(event, context);
    const decision = guard.beforeAgentFinalize(event, context);
    if (label === 'verified') {
      assert.equal(decision, undefined);
      assert.equal(guard.deliveryVerificationForRun(context.runId).text, undefined);
      continue;
    }
    assert.equal(decision?.action, 'revise');
    assert.equal(decision.retry.instruction, UNREAD_SOURCES_REVISION_INSTRUCTION.join(JSON.stringify([coliseu])),
      'only the page that did not verify is named');
    assert.equal(guard.beforeAgentFinalize(event, context)?.action, 'finalize');
    const delivery = guard.deliveryVerificationForRun(context.runId);
    assert.equal(delivery.status, 'failed');
    assert.equal(delivery.text, answer.replace(`[Coliseu](${coliseu})`, `Coliseu ${UNREAD_SOURCE_MARKER_PT}`) + `\n\n${UNREAD_SOURCE_NOTE_PT}`,
      'the host-verified Festa page stays cited');
  }
});

test('host verification is a separate receipt kind in completion assurance', () => {
  const assurance = createCompletionAssurance();
  assurance.begin('Search the web and open sources before citing findings.');
  assert.equal(assurance.hostVerificationCandidates(`A (${TT})`), undefined, 'no web result observed yet');
  assurance.observe('web_fetch', {result: fetched('https://example.org/read')});
  const answer = `Read page https://example.org/read; Taylor Tomlinson, Nov 1, 2026: ${TT}`;
  assert.deepEqual(assurance.hostVerificationCandidates(answer), {urls: [TT], portuguese: false});
  assurance.observeHostVerification(TT);
  assert.deepEqual(assurance.hostVerifiedSources, [TT]);
  assert.equal(assurance.hostVerificationCandidates(answer), undefined);
  assert.deepEqual(assurance.unverifiedCitations(answer), []);
  assert.equal(assurance.finalize(answer), undefined);
  assert.equal(assurance.terminal, undefined);
  const conversational = createCompletionAssurance();
  conversational.begin('Translate this list of events.');
  conversational.observe('web_fetch', {result: fetched('https://example.org/read')});
  assert.equal(conversational.hostVerificationCandidates(answer), undefined);
});
