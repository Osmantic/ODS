// pixel_ods_search_read through the real tool-loop guard hooks, direct and as
// a Tool Search child: charge, grant, refund, reserve, pacing and receipts.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, DEFAULT_WEB_TOOL_LIMITS, SEARCH_READ_RESERVE_REASON, WEB_SEARCH_BUDGET_EXHAUSTED_REASON,
  WEB_FETCH_PUBLIC_ONLY_REASON, WEB_BUDGET_EXHAUSTED_REASON} from '../plugin/tool-loop-guard.mjs';
import {createSearchReadTool, SEARCH_READ_SCHEMA_HINT, SEARCH_READ_TOOL} from '../plugin/search-read.mjs';
import {SEARCH_PACING_STREAK} from '../plugin/research-pacing.mjs';
import {HOST_CITATION_LIMITS} from '../plugin/citation-verification.mjs';

const base = {agentId: 'pixel', runId: 'sr-run', sessionId: 'sr-session', sessionKey: 'agent:pixel:openai-user:ods-sr-test'};
const identity = {id: `openclaw:pixel-ods:${SEARCH_READ_TOOL}`, source: 'openclaw', sourceName: 'pixel-ods', name: SEARCH_READ_TOOL};
const text = (message, prefix) => message?.content?.find(block => block.text?.startsWith(prefix))?.text;
const budget = message => text(message, 'ODS research budget');
const remaining = message => {
  const match = budget(message)?.match(/Remaining this response: (\d+) search calls, (\d+) page-reading calls, (\d+) web calls total/);
  return match && {search: +match[1], read: +match[2], total: +match[3]};
};

function guardFor(prompt = 'Research public sources about the RTX 5070.', limits) {
  const guard = createToolLoopGuard(limits ? {limits} : {});
  guard.observeRun(base, 'pixel', {prompt});
  return guard;
}

// A real search_read tool over fixture pages.
function fixtureTool(rows, pages = {}) {
  return createSearchReadTool({
    search: async () => ({provider: 'fixture', result: {results: rows.map(url => ({url, title: 'Result'}))}}),
    readPage: async url => pages[url] ?? {ok: true, status: 200, finalUrl: url, contentType: 'text/html',
      text: `RTX 5070 board power 250 W. Memory 12 GB GDDR7. Page ${url}`, truncated: false},
  });
}

// One call through the hooks, as the pinned runtime runs it. Returns the
// refusal, or the executed params, the tool result and the persisted message.
async function call(guard, transport, id, params, {rows = [], pages, tamper} = {}) {
  const context = {...base, toolName: transport, toolCallId: id};
  const tool = fixtureTool(rows, pages);
  if (transport === 'tool_call') {
    const outerParams = {id: identity.id, args: params};
    const outer = guard.beforeToolCall({toolName: 'tool_call', params: outerParams}, context);
    if (outer?.block) return {refusal: outer.blockReason};
    const childId = `tool_search_code:${id}:${SEARCH_READ_TOOL}:1`;
    const child = {...base, toolName: SEARCH_READ_TOOL, toolCallId: childId};
    const inner = guard.beforeToolCall({toolName: SEARCH_READ_TOOL, params}, child);
    if (inner?.block) return {refusal: inner.blockReason};
    const executed = {...params, ...(inner?.params ?? {})};
    const result = await tool.execute(childId, executed);
    const reported = tamper ? {...executed, ...tamper} : executed;
    guard.afterToolCall({toolName: SEARCH_READ_TOOL, params: reported, result}, child);
    const envelope = {tool: identity, result};
    const outerResult = {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope};
    guard.afterToolCall({toolName: 'tool_call', params: tamper ? {id: identity.id, args: reported} : outerParams,
      result: outerResult}, context);
    const persisted = guard.toolResultPersist({message: {role: 'toolResult', toolName: 'tool_call', toolCallId: id,
      content: outerResult.content}}, context);
    return {executed, result, message: persisted?.message};
  }
  const before = guard.beforeToolCall({toolName: SEARCH_READ_TOOL, params}, context);
  if (before?.block) return {refusal: before.blockReason};
  const executed = {...params, ...(before?.params ?? {})};
  const result = await tool.execute(id, executed);
  guard.afterToolCall({toolName: SEARCH_READ_TOOL, params: tamper ? {...executed, ...tamper} : executed, result}, context);
  const persisted = guard.toolResultPersist({message: {role: 'toolResult', toolName: SEARCH_READ_TOOL, toolCallId: id,
    content: result.content}}, context);
  return {executed, result, message: persisted?.message};
}

function webSearch(guard, id, query, urls = []) {
  const context = {...base, toolName: 'web_search', toolCallId: id};
  const before = guard.beforeToolCall({toolName: 'web_search', params: {query}}, context);
  if (before?.block) return {refusal: before.blockReason};
  const details = {provider: 'test', results: urls.map(url => ({title: 'Source', url, description: 'Excerpt'})),
    externalContent: {untrusted: true}};
  const inner = {content: [{type: 'text', text: JSON.stringify(details)}], details};
  guard.afterToolCall({toolName: 'web_search', params: {query}, result: inner}, context);
  return {message: guard.toolResultPersist({message: {role: 'toolResult', toolName: 'web_search', toolCallId: id}}, context)?.message};
}
const fetchPage = (guard, id, url) => guard.beforeToolCall({toolName: 'web_fetch', params: {url}}, {...base, toolName: 'web_fetch', toolCallId: id});

const ROWS = ['https://a.example.org/specs', 'https://b.example.org/specs', 'https://c.example.org/specs',
  'https://d.example.org/specs', 'https://e.example.org/specs'];
const L = DEFAULT_WEB_TOOL_LIMITS;

for (const transport of [SEARCH_READ_TOOL, 'tool_call']) {
  test(`${transport}: charges one search plus the granted reads and refunds reads it never attempted`, async () => {
    const guard = guardFor();
    const first = await call(guard, transport, 'one', {query: 'RTX 5070 board power'}, {rows: ROWS});
    assert.equal(first.executed.maxPages, 3);
    assert.equal(first.result.details.readsAttempted, 3);
    assert.deepEqual(remaining(first.message), {search: L.search - 1, read: L.fetch - 3, total: L.total - 4});
    // Only one readable result: the two unattempted reads come back.
    const second = await call(guard, transport, 'two', {query: 'RX 9070 board power', maxPages: 3}, {rows: [ROWS[0]]});
    assert.equal(second.result.details.readsAttempted, 1);
    assert.deepEqual(remaining(second.message), {search: L.search - 2, read: L.fetch - 4, total: L.total - 6});
    // A urls call costs no search.
    const third = await call(guard, transport, 'three', {urls: ROWS.slice(0, 2), focus: 'board power'});
    assert.equal(third.result.details.mode, 'urls');
    assert.deepEqual(remaining(third.message), {search: L.search - 2, read: L.fetch - 6, total: L.total - 8});
    assert.match(third.result.content[0].text, /\[R1\] https:\/\/a\.example\.org\/specs/);
  });

  test(`${transport}: maxPages is lowered to the reads left after the host-check reserve`, async () => {
    const guard = guardFor(undefined, {search: 8, fetch: 10, total: 20});
    // fetch 10 keeps a reserve of 2 (a quarter, at most four).
    for (let i = 0; i < 5; i++) assert.notEqual(fetchPage(guard, `f${i}`, `https://docs.example.org/${i}`)?.block, true);
    const clamped = await call(guard, transport, 'clamped', {query: 'RTX 5070 board power', maxPages: 5}, {rows: ROWS});
    assert.equal(clamped.executed.maxPages, 3);
    assert.equal(clamped.result.details.grantedPages, 3);
    assert.match(budget(clamped.message), /opened at most 3 of the 5 requested pages because of the page-reading allowance/);
    assert.deepEqual(remaining(clamped.message), {search: 7, read: 2, total: 11});
    // The reserve is left for the host citation check; search_read refuses,
    // for free, while single-page reads remain available.
    const refused = await call(guard, transport, 'reserve', {query: 'RX 9070 board power'}, {rows: ROWS});
    assert.equal(refused.refusal, SEARCH_READ_RESERVE_REASON);
    assert.notEqual(fetchPage(guard, 'single', 'https://docs.example.org/single')?.block, true);
  });

  test(`${transport}: reserve refusals are free corrections, never a step toward the web-loop stop`, async () => {
    const guard = guardFor(undefined, {search: 8, fetch: 10, total: 20});
    for (let i = 0; i < 5; i++) assert.notEqual(fetchPage(guard, `f${i}`, `https://docs.example.org/${i}`)?.block, true);
    assert.ok((await call(guard, transport, 'clamped', {query: 'RTX 5070 board power', maxPages: 5}, {rows: ROWS})).result);
    // Four refusals in a row, each persisted as the runtime persists a
    // refused call. Before, the third ended the response with 2 page reads,
    // 7 searches and 11 web calls still available.
    for (let i = 0; i < 4; i++) {
      const refused = await call(guard, transport, `reserve-${i}`, {query: `RX 9070 board power ${'abcd'[i]}`}, {rows: ROWS});
      assert.equal(refused.refusal, SEARCH_READ_RESERVE_REASON, `refusal ${i + 1}`);
      guard.toolResultPersist({message: {role: 'toolResult', toolName: transport, toolCallId: `reserve-${i}`, isError: true,
        content: [{type: 'text', text: refused.refusal}]}}, {...base, toolName: transport, toolCallId: `reserve-${i}`});
    }
    // Single-page reads and searches still run: nothing was exhausted.
    assert.notEqual(fetchPage(guard, 'single', 'https://docs.example.org/single')?.block, true);
    assert.ok(webSearch(guard, 'search-after', 'RX 9070 board power review', [ROWS[1]]).message);
  });

  test(`${transport}: refused without charge when searches, reads or the total are spent`, async () => {
    const searchSpent = guardFor(undefined, {search: 1, fetch: 20, total: 30});
    assert.ok(webSearch(searchSpent, 's1', 'RTX 5070 price').message);
    assert.equal((await call(searchSpent, transport, 'q', {query: 'RX 9070 price'}, {rows: ROWS})).refusal,
      WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
    assert.ok((await call(searchSpent, transport, 'u', {urls: [ROWS[0]], focus: 'price'})).result, 'urls needs no search');
    const totalSpent = guardFor(undefined, {search: 8, fetch: 20, total: 2});
    assert.ok(webSearch(totalSpent, 's1', 'RTX 5070 price').message);
    assert.equal((await call(totalSpent, transport, 'q', {query: 'RX 9070 price'}, {rows: ROWS})).refusal,
      WEB_BUDGET_EXHAUSTED_REASON);
  });

  test(`${transport}: invalid arguments and private URLs run nothing and charge nothing`, async () => {
    const guard = guardFor();
    assert.equal((await call(guard, transport, 'bad', {query: 'RTX 5070', urls: [ROWS[0]]})).refusal, SEARCH_READ_SCHEMA_HINT);
    assert.equal((await call(guard, transport, 'bad-2', {query: 'x'})).refusal, SEARCH_READ_SCHEMA_HINT);
    assert.equal((await call(guard, transport, 'private', {urls: ['http://192.168.1.10/admin']})).refusal, WEB_FETCH_PUBLIC_ONLY_REASON);
    const guard2 = guardFor();
    const ok = await call(guard2, transport, 'ok', {query: 'RTX 5070 board power'}, {rows: ROWS});
    assert.deepEqual(remaining(ok.message), {search: L.search - 1, read: L.fetch - 3, total: L.total - 4});
  });

  test(`${transport}: a result bound to other params keeps the whole charge`, async () => {
    const guard = guardFor();
    const tampered = await call(guard, transport, 'tampered', {query: 'RTX 5070 board power', maxPages: 3},
      {rows: [ROWS[0]], tamper: {focus: 'something else'}});
    assert.equal(tampered.result.details.readsAttempted, 1);
    assert.equal(budget(tampered.message), undefined, 'no bound receipt, no budget line');
    const next = await call(guard, transport, 'next', {query: 'RX 9070 board power'}, {rows: ROWS});
    assert.deepEqual(remaining(next.message), {search: L.search - 2, read: L.fetch - 6, total: L.total - 8},
      'the unbound call was charged its full grant of 3');
  });

  test(`${transport}: receipts reach completion assurance; leads do not`, async () => {
    const prompt = 'Today is 2026-09-25. Compare the RTX 5070 and RX 9070. Open the source pages and cite them.';
    const guard = guardFor(prompt);
    const done = await call(guard, transport, 'read', {query: 'RTX 5070 board power', maxPages: 2}, {rows: ROWS});
    assert.equal(done.result.details.receipts, 2);
    const context = {...base};
    const good = {lastAssistantMessage: `RTX 5070 board power is 250 W (${ROWS[0]}); memory 12 GB (${ROWS[1]}).`};
    assert.equal(await guard.verifyCitedPages(good, context), undefined, 'nothing unread to verify');
    assert.equal(guard.beforeAgentFinalize(good, context), undefined);
    const lead = {lastAssistantMessage: `RTX 5070 board power is 250 W (${ROWS[0]}), see also ${ROWS[4]}.`};
    const revision = guard.beforeAgentFinalize(lead, context);
    assert.equal(revision?.action, 'revise');
    assert.ok(revision.retry.instruction.includes(ROWS[4]));
    assert.ok(!revision.retry.instruction.includes(ROWS[0]));
  });
}

test('pacing: search_read is never paused for unread leads and resets the streak', async () => {
  const guard = guardFor(undefined, {search: 20, fetch: 30, total: 60});
  for (let i = 1; i <= SEARCH_PACING_STREAK; i++) assert.ok(webSearch(guard, `s${i}`, `distinct topic ${'abcdefgh'[i]} lookup`, [`https://lead.example.org/${i}`]).message);
  const read = await call(guard, SEARCH_READ_TOOL, 'sr', {query: 'another distinct subject query'}, {rows: ROWS});
  assert.ok(read.result, 'not paused');
  for (let i = 1; i < SEARCH_PACING_STREAK; i++) assert.ok(webSearch(guard, `t${i}`, `second topic ${'pqrstuvw'[i]} lookup`, [`https://lead.example.org/t${i}`]).message);
  assert.ok(webSearch(guard, 't-last', 'second topic last lookup', ['https://lead.example.org/last']).message,
    'the search_read reset the unread streak');
});

test('pacing: a repeated search_read is recalled once with the pages it already read; a repeated web_search query is not', async () => {
  const guard = guardFor();
  assert.ok(webSearch(guard, 'ws', 'RTX 5070 official specs board power', ['https://www.nvidia.com/specs']).message);
  const first = await call(guard, SEARCH_READ_TOOL, 'first', {query: 'RTX 5070 official specs board power'}, {rows: ROWS});
  assert.ok(first.result, 'repeating a plain web_search with search_read reads the pages');
  const repeat = await call(guard, SEARCH_READ_TOOL, 'repeat', {query: 'official RTX 5070 board power specs'}, {rows: ROWS});
  assert.match(repeat.refusal, /^Pixel did not repeat this search/);
  assert.match(repeat.refusal, /Pages it already read in this response: https:\/\/a\.example\.org\/specs/);
  assert.match(repeat.refusal, /did not use the search allowance/);
  const again = await call(guard, SEARCH_READ_TOOL, 'again', {query: 'official RTX 5070 board power specs'}, {rows: ROWS});
  assert.deepEqual(remaining(again.message), {search: L.search - 3, read: L.fetch - 6, total: L.total - 9},
    'a deliberate second repeat proceeds; the recall cost nothing');
  // A different model number is a new search; a web_search repeat is
  // recalled once per earlier entry (here the first web_search), then runs.
  assert.equal(webSearch(guard, 'ws-2', 'RX 9070 official specs board power').refusal, undefined);
  assert.match(webSearch(guard, 'ws-3', 'official RTX 5070 specs board power').refusal, /https:\/\/www\.nvidia\.com\/specs/);
  assert.equal(webSearch(guard, 'ws-4', 'official RTX 5070 specs board power').refusal, undefined,
    'each ledger entry is recalled once');
});

test('the stale-date note follows a search_read query that names an older month', async () => {
  const guard = guardFor('Today is 2026-09-25. Find RTX 5070 prices at US retailers and open the pages.');
  const done = await call(guard, SEARCH_READ_TOOL, 'stale', {query: 'RTX 5070 price January 2026'}, {rows: ROWS});
  assert.match(text(done.message, 'ODS date check') ?? '', /this search named January 2026/);
});

test('a search_read that read no page counts toward the unread-leads streak', async () => {
  const guard = guardFor(undefined, {search: 20, fetch: 30, total: 60});
  const refused = Object.fromEntries(ROWS.map(url => [url, {ok: false, reason: 'http-status', status: 403}]));
  for (let i = 1; i <= SEARCH_PACING_STREAK; i++) {
    assert.ok((await call(guard, SEARCH_READ_TOOL, `sr${i}`, {query: `blocked subject ${'abcdefgh'[i]} lookup`}, {rows: ROWS, pages: refused})).result);
  }
  assert.match(webSearch(guard, 'paused', 'another distinct subject query').refusal ?? '', /Pixel paused this search/);
});

test('the host-check reserve never exceeds the host verifier maximum', () => {
  assert.equal(HOST_CITATION_LIMITS.maxUrls, 4);
});
