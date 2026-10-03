import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompletionAssurance, UNREAD_SOURCE_MARKER, UNREAD_SOURCE_MARKER_PT, UNREAD_SOURCE_NOTE,
  UNREAD_SOURCE_NOTE_PT, UNREAD_SOURCES_REPLACEMENT, UNREAD_SOURCES_REPLACEMENT_PT,
  UNREAD_SOURCES_REVISION_INSTRUCTION} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE} from '../plugin/progress-finalization.mjs';
import {PHILLY_EVENTS_FIRST_ANSWER, PHILLY_EVENTS_REVISED_ANSWER} from './fixtures/philly-events-citations.mjs';

// tower1 fleet run (2026-09-25, Qwen3.5-27B, build 64ff3d2a): the research was
// sound, but one cited page (ESPN) failed to fetch, and the whole answer was
// replaced with the "cited source reads were not confirmed" text.
const FLEET_PROMPT = 'Today is 2026-09-25. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL. ' +
  'Exclude undated listings and past events. Explain any unavailable result honestly. Do not create files.\n\n' +
  '[ODS Portal delivery requirement: Answer the owner\'s complete message above. If it asks for exact text, copy that full exact text. ' +
  'Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const LFF = 'https://www.lincolnfinancialfield.com/events/';
const ESPN = 'https://www.espn.com/soccer/team/fixtures/_/id/10739/philadelphia-union';
// Successful web_fetch receipts in that turn before the answer ([url, finalUrl]).
const FLEET_READS = [
  ['https://www.visitphilly.com/articles/philadelphia/upcoming-concerts', 'https://www.visitphilly.com/articles/philadelphia/upcoming-concerts/'],
  ['https://whyy.org/articles/philly-music-fest-2026', 'https://whyy.org/articles/philly-music-fest-2026/'],
  ['https://www.phillymusicfest.com/', 'https://www.phillymusicfest.com/'],
  ['https://www.visitphilly.com/articles/philadelphia/top-things-to-do-in-philadelphia-in-october', 'https://www.visitphilly.com/articles/philadelphia/top-things-to-do-in-philadelphia-in-october/'],
  ['https://www.philadelphiaeagles.com/news/eagles-schedule-release-2026', 'https://www.philadelphiaeagles.com/news/eagles-schedule-release-2026'],
  ['https://www.phillymusicfest.com/schedule', 'https://www.phillymusicfest.com/schedule'],
  ['https://www.philadelphiaunion.com/schedule/matches', 'https://www.philadelphiaunion.com/schedule/matches'],
  ['https://en.wikipedia.org/wiki/2026_Philadelphia_Union_season', 'https://en.wikipedia.org/wiki/2026_Philadelphia_Union_season'],
  ['https://www.philadelphiaunion.com/schedule', 'https://www.philadelphiaunion.com/schedule/'],
  ['https://www.visitphilly.com/articles/philadelphia/events-festivals-2026', 'https://www.visitphilly.com/articles/philadelphia/events-festivals-2026/'],
];

const context = {agentId: 'pixel', runId: 'philly-events-run', sessionId: 'philly-events-session', sessionKey: 'agent:pixel:philly'};
let callId = 0;
function tool(guard, toolName, params, result) {
  const id = `call-${++callId}`;
  guard.afterToolCall({toolName, toolCallId: id, params, result}, {...context, toolName, toolCallId: id});
}
const fetched = (url, finalUrl = url) => ({content: [{type: 'text', text: `Fetched ${url}`}],
  details: {status: 200, url, finalUrl, text: `Evidence from ${finalUrl}`}});
const pageReceipt = (url, finalUrl = url) => ({result: {details: {url, finalUrl, status: 200, text: 'Returned page evidence.'}}});
const neutralised = (text, url, marker = UNREAD_SOURCE_MARKER) => text.split(url).join(marker);
const urls = text => [...text.matchAll(/https?:\/\/[^\s<>"`\\\]|)]+/g)].map(match => match[0].replace(/[.,;:!?]+$/, ''));

function sourceReadGuard(request = 'Search the web and open sources before citing findings.') {
  const guard = createCompletionAssurance();
  guard.begin(request);
  return guard;
}

test('fleet replay: one revision naming the unread URLs, then delivery with only ESPN neutralised', () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  tool(guard, 'web_search', {query: 'Philadelphia Union schedule October 2026'},
    {details: {results: [{url: ESPN}, {url: 'https://www.philadelphiaunion.com/schedule'}]}});
  for (const [url, finalUrl] of FLEET_READS) tool(guard, 'web_fetch', {url}, fetched(url, finalUrl));
  tool(guard, 'web_fetch', {url: ESPN}, {isError: true, details: {status: 'error', tool: 'web_fetch',
    error: 'Web fetch extraction failed: Readability, provider fallback, and basic HTML cleanup returned no content.'}});

  // First answer: LFF was cited before it was read, ESPN failed to fetch.
  const revision = guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_FIRST_ANSWER}, context);
  assert.equal(revision?.action, 'revise');
  assert.equal(revision.retry.idempotencyKey, 'ods-opened-source-attribution');
  assert.equal(revision.retry.maxAttempts, 1);
  assert.equal(revision.retry.instruction, UNREAD_SOURCES_REVISION_INSTRUCTION.join(JSON.stringify([LFF, ESPN])),
    'exactly the unread URLs are named; everything else is fixed text');
  // Armed in case the harness refuses the revision: the answer survives, and
  // both unread links are neutralised while the read pages stay cited.
  const armed = guard.deliveryVerificationForRun(context.runId);
  assert.equal(armed.status, 'failed');
  assert.equal(armed.text, `${neutralised(neutralised(PHILLY_EVENTS_FIRST_ANSWER, LFF), ESPN)}\n\n${UNREAD_SOURCE_NOTE}`);
  assert.ok(armed.text.includes('https://whyy.org/articles/philly-music-fest-2026'));

  // The model reads LFF during the revision; ESPN is still cited, unread.
  tool(guard, 'web_fetch', {url: LFF}, fetched(LFF));
  const final = guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_REVISED_ANSWER}, context);
  assert.equal(final?.action, 'finalize', 'no second revision');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed', 'pixel_outcome still reports incomplete verification');
  assert.equal(delivery.text, `${neutralised(PHILLY_EVENTS_REVISED_ANSWER, ESPN)}\n\n${UNREAD_SOURCE_NOTE}`,
    'byte-stable apart from the unread citation and the fixed note');
  assert.ok(!delivery.text.includes('espn.com'));
  assert.equal(delivery.text.split(LFF).length - 1, 4, 'every LFF citation is kept');
  assert.match(delivery.text, /Carolina Panthers vs\. Philadelphia Eagles/);
  assert.ok(!delivery.text.includes(UNREAD_SOURCES_REPLACEMENT));
  assert.ok(delivery.text.length <= 32 * 1024);
  assert.ok(!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(delivery.text));
  const sent = guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: PHILLY_EVENTS_REVISED_ANSWER}});
  assert.equal(sent.payload.text, delivery.text, 'in-process delivery and ingress agree');
  assert.equal(guard.continuationAllowed(context.runId), false);
});

test('all cited pages unread: the full replacement stays', () => {
  const guard = sourceReadGuard();
  guard.observe('web_search', {result: {details: {results: [{url: 'https://example.org/a'}, {url: 'https://example.org/b'}]}}});
  guard.observe('web_fetch', {result: {isError: true, details: {status: 'error'}}});
  const answer = 'Event A is on 2026-10-01 at Hall A (https://example.org/a). Event B is on 2026-10-02 (https://example.org/b).';
  assert.equal(guard.finalize(answer)?.action, 'revise');
  assert.equal(guard.terminal, UNREAD_SOURCES_REPLACEMENT);
  assert.equal(guard.finalize(answer)?.action, 'finalize');
  assert.equal(guard.terminal, UNREAD_SOURCES_REPLACEMENT);
  assert.equal(guard.terminalStatus, 'failed');

  const pt = sourceReadGuard('Pesquise na internet e abra as fontes antes de citar.');
  assert.equal(pt.finalize('O evento é em 2026-10-01: https://example.org/a')?.action, 'revise');
  assert.equal(pt.terminal, UNREAD_SOURCES_REPLACEMENT_PT);
});

test('an answer without verifiable content keeps the full replacement', () => {
  for (const answer of [
    'https://example.org/read https://example.org/unread',
    'Sources: https://example.org/read, https://example.org/unread.',
    `Read: https://example.org/read\n${'https://example.org/unread '.repeat(5)}`,
  ]) {
    const guard = sourceReadGuard();
    guard.observe('web_fetch', pageReceipt('https://example.org/read'));
    guard.finalize(answer);
    assert.equal(guard.finalize(answer)?.action, 'finalize', answer);
    assert.equal(guard.terminal, UNREAD_SOURCES_REPLACEMENT, answer);
  }
  // A single read citation with real content is enough to keep the answer.
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/read'));
  guard.finalize('The festival runs October 3-5 at Penn\'s Landing (https://example.org/read); the parade date comes from https://example.org/unread.');
  assert.equal(guard.terminal, 'The festival runs October 3-5 at Penn\'s Landing (https://example.org/read); the parade date comes from ' +
    `${UNREAD_SOURCE_MARKER}.\n\n${UNREAD_SOURCE_NOTE}`);
});

test('a revision that cites only read pages delivers the model answer unchanged', () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  for (const [url, finalUrl] of FLEET_READS) tool(guard, 'web_fetch', {url}, fetched(url, finalUrl));
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_FIRST_ANSWER}, context)?.action, 'revise');
  tool(guard, 'web_fetch', {url: LFF}, fetched(LFF));
  const corrected = PHILLY_EVENTS_REVISED_ANSWER.replace(` (${ESPN})`, '');
  assert.ok(!corrected.includes('espn.com'));
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: corrected}, context), undefined);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.text, undefined, 'no host text replaces the answer');
  assert.notEqual(delivery.status, 'failed');
  assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: corrected}}), undefined);
});

test('no change when every citation was read', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/a'));
  guard.observe('web_fetch', pageReceipt('https://example.org/old', 'https://example.org/b/'));
  for (const answer of [
    'Event A: https://example.org/a. Event B: [B](https://example.org/b/).',
    '```json\n{"sources":["https://example.org/a","https://example.org/b"]}\n```',
    'Unread items are marked. Event A: https://example.org/a. Unverified lead, not opened: https://example.org/c',
  ]) {
    assert.equal(guard.finalize(answer), undefined, answer);
    assert.equal(guard.terminal, undefined, answer);
  }
});

test('citation matching normalizes scheme/host case, trailing slash, default port and fragment, not the query', () => {
  const read = [
    ['https://example.org/events/'], ['https://Example.org/page'], ['http://example.net/a'],
    ['https://example.org/old', 'https://example.org/new/'], ['https://example.org/search?q=philly'],
  ];
  const cases = [
    ['https://example.org/events', true],
    ['https://example.org/events/', true],
    ['HTTPS://EXAMPLE.ORG/events', true],
    ['https://example.org/page/', true],
    ['https://example.org:443/page#tickets', true],
    ['http://example.net:80/a/', true],
    ['https://example.org/new', true],
    ['https://example.org/old/', true],
    ['https://example.org/search?q=philly', true],
    ['https://example.org/search/?q=philly#top', true],
    ['https://example.org/search?q=boston', false],
    ['https://example.org/search', false],
    ['https://example.org/page?id=2', false],
    ['https://example.org/Page', false],
    ['https://example.net/a', false],
    ['https://example.org:8443/page', false],
    ['https://www.example.org/page', false],
    ['https://example.org/events/2026', false],
  ];
  for (const [cited, accepted] of cases) {
    const guard = sourceReadGuard();
    for (const [url, finalUrl] of read) guard.observe('web_fetch', pageReceipt(url, finalUrl));
    const answer = `The event date is confirmed by ${cited} and the venue listing.`;
    assert.equal(guard.finalize(answer)?.action, accepted ? undefined : 'revise', cited);
    assert.deepEqual(guard.unverifiedCitations(answer).length, accepted ? 0 : 1, cited);
  }
  // A cited variant of a read page also satisfies the ordinary attribution check.
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/events/'));
  assert.equal(guard.finalize('The listing is at https://example.org/events.'), undefined);
});

test('Portuguese answers get the Portuguese marker and note', () => {
  const guard = sourceReadGuard('Pesquise na internet eventos em Lisboa nos próximos 30 dias e abra as fontes antes de citar.');
  guard.observe('web_fetch', pageReceipt('https://www.visitlisboa.com/pt-pt/eventos'));
  guard.observe('web_fetch', {result: {details: {status: 'error'}}});
  const answer = 'Encontrei dois eventos:\n\n1. **Festa de Outono** — 10/10/2026, Praça do Comércio. Fonte: https://www.visitlisboa.com/pt-pt/eventos/\n' +
    '2. **Concerto no Coliseu** — 17/10/2026. Fonte: [Coliseu](https://www.coliseulisboa.com/agenda)';
  const revision = guard.finalize(answer);
  assert.equal(revision?.action, 'revise');
  assert.equal(revision.retry.instruction, UNREAD_SOURCES_REVISION_INSTRUCTION.join('["https://www.coliseulisboa.com/agenda"]'));
  assert.equal(guard.finalize(answer)?.action, 'finalize');
  assert.equal(guard.terminalStatus, 'failed');
  assert.equal(guard.terminal, answer.replace('[Coliseu](https://www.coliseulisboa.com/agenda)', `Coliseu ${UNREAD_SOURCE_MARKER_PT}`) +
    `\n\n${UNREAD_SOURCE_NOTE_PT}`);
  assert.ok(guard.terminal.includes('https://www.visitlisboa.com/pt-pt/eventos/'), 'trailing-slash variant of a read page is kept');
  assert.ok(!guard.terminal.includes(UNREAD_SOURCE_NOTE));
});

test('only unlabelled unread citations change; Markdown links, autolinks, JSON and fences stay well formed', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/read'));
  guard.observe('web_search', {result: {details: {results: [{url: 'https://example.org/lead'}]}}});
  const answer = [
    'Event A is on October 3 ([official page](https://example.org/read)).',
    'Event B is on October 4 ([tickets](https://example.org/lead "Tickets")).',
    'Event C is on October 5 <https://example.org/c#dates>.',
    '![poster](https://example.org/poster.png)',
    'Unverified lead, not opened: https://example.org/d',
    '```json',
    JSON.stringify({events: [{name: 'A', sourceUrl: 'https://example.org/read'}, {name: 'B', sourceUrl: 'https://example.org/lead'}]}),
  ].join('\n');
  guard.finalize(answer);
  const delivered = guard.terminal;
  assert.equal(delivered, [
    'Event A is on October 3 ([official page](https://example.org/read)).',
    `Event B is on October 4 (tickets ${UNREAD_SOURCE_MARKER}).`,
    `Event C is on October 5 ${UNREAD_SOURCE_MARKER}.`,
    `poster ${UNREAD_SOURCE_MARKER}`,
    'Unverified lead, not opened: https://example.org/d',
    '```json',
    JSON.stringify({events: [{name: 'A', sourceUrl: 'https://example.org/read'}, {name: 'B', sourceUrl: UNREAD_SOURCE_MARKER}]}),
    '```',
    '',
    UNREAD_SOURCE_NOTE,
  ].join('\n'), 'a search lead is not a read page; the explicitly labelled link is left as written');
  const json = delivered.split('```json\n')[1].split('\n```')[0];
  assert.equal(JSON.parse(json).events[1].sourceUrl, UNREAD_SOURCE_MARKER);
  // Sources come only from the answer itself: nothing is added.
  for (const url of urls(delivered)) assert.ok(answer.includes(url), url);
  assert.deepEqual(guard.unverifiedCitations(delivered), [], 'the delivered text passes the same check');
});

test('control characters are stripped and an oversized answer keeps the full replacement', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/read'));
  guard.finalize('Event A\u0007 is confirmed by https://example.org/read; Event B by https://example.org/unread.');
  assert.equal(guard.terminal, `Event A is confirmed by https://example.org/read; Event B by ${UNREAD_SOURCE_MARKER}.\n\n${UNREAD_SOURCE_NOTE}`);

  const long = sourceReadGuard();
  long.observe('web_fetch', pageReceipt('https://example.org/read'));
  long.finalize(`${'Detailed finding. '.repeat(1200)} https://example.org/read https://example.org/unread`);
  assert.equal(long.terminal, UNREAD_SOURCES_REPLACEMENT);
});

test('a corrected answer after the revision clears the armed partial delivery', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/read'));
  assert.equal(guard.finalize('Event A is on October 3: https://example.org/read. Event B is on October 4: https://example.org/unread.')?.action, 'revise');
  assert.ok(guard.terminal.endsWith(UNREAD_SOURCE_NOTE));
  assert.equal(guard.finalize('Event A is on October 3: https://example.org/read. Event B could not be verified.'), undefined);
  assert.equal(guard.terminal, undefined);
});

test('tool-limit finalization lists only citations that are unread after normalization', () => {
  const guard = sourceReadGuard();
  guard.observe('web_fetch', pageReceipt('https://example.org/events/'));
  assert.deepEqual(guard.unverifiedCitations('See https://example.org/events and https://example.org/other/.'),
    ['https://example.org/other/']);
});

test('a tool-limit answer after the source-read revision supersedes the armed delivery', () => {
  const aborts = [];
  const guard = createToolLoopGuard({abortRun: (id, key) => { aborts.push([id, key]); return true; }});
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  for (const [url, finalUrl] of FLEET_READS) tool(guard, 'web_fetch', {url}, fetched(url, finalUrl));
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_FIRST_ANSWER}, context)?.action, 'revise');
  // The revision pass only fails, until the progress budget stops the run.
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    const id = `failed-${i}`;
    guard.toolResultPersist({toolCallId: id, message: {role: 'toolResult', toolName: 'web_fetch', toolCallId: id,
      isError: true, content: [{type: 'text', text: 'Web fetch failed (403)'}]}}, {...context, toolName: 'web_fetch', toolCallId: id});
  }
  guard.observeModelEnd({}, context);
  guard.observeModelCall({}, context);
  assert.deepEqual(guard.beforeToolCall({toolName: 'web_search', toolCallId: 'late', params: {query: 'Philadelphia events'}},
    {...context, toolName: 'web_search', toolCallId: 'late'}), {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION});
  guard.observeModelEnd({}, context);
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_REVISED_ANSWER}, context), undefined);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.startsWith(PHILLY_EVENTS_REVISED_ANSWER), 'the newer answer is delivered, not the armed first answer');
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.ok(delivery.text.includes(`<${LFF}>`) && delivery.text.includes(`<${ESPN}>`), 'its unread links are labelled by the host');
  assert.deepEqual(aborts, []);
});

test('the superseding tool-limit answer also survives a threshold compaction after it', () => {
  // #6671's precedence (the tool-limit answer over the armed first answer)
  // composes with compaction survival: OpenClaw's post-answer summarization
  // call uses the run's model stream and must not forfeit that answer.
  const aborts = [];
  const guard = createToolLoopGuard({abortRun: (id, key) => { aborts.push([id, key]); return true; }});
  const sdk = {runId: context.runId, sessionId: context.sessionId, sessionKey: context.sessionKey};
  let calls = 0;
  const turn = () => { const callId = `${context.runId}:model:${++calls}`; guard.observeModelCall({callId}, sdk); guard.observeModelEnd({callId}, sdk); };
  guard.observeRun(context, 'pixel', {prompt: FLEET_PROMPT});
  for (const [url, finalUrl] of FLEET_READS) tool(guard, 'web_fetch', {url}, fetched(url, finalUrl));
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_FIRST_ANSWER}, context)?.action, 'revise');
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    const id = `failed-${i}`;
    guard.toolResultPersist({toolCallId: id, message: {role: 'toolResult', toolName: 'web_fetch', toolCallId: id,
      isError: true, content: [{type: 'text', text: 'Web fetch failed (403)'}]}}, {...context, toolName: 'web_fetch', toolCallId: id});
  }
  guard.observeModelCall({callId: 'unaware'}, sdk);
  assert.equal(guard.beforeToolCall({toolName: 'web_search', toolCallId: 'late', params: {query: 'Philadelphia events'}},
    {...context, toolName: 'web_search', toolCallId: 'late'})?.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  guard.observeModelEnd({callId: 'unaware'}, sdk);
  turn();
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: PHILLY_EVENTS_REVISED_ANSWER}, context), undefined);
  guard.observeCompaction({sessionKey: context.sessionKey}, 'start');
  turn();
  guard.observeCompaction({sessionKey: context.sessionKey}, 'end');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith(PHILLY_EVENTS_REVISED_ANSWER), 'the tool-limit answer is still delivered after compaction');
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.deepEqual(aborts, [], 'the summarization call is not aborted');
});
