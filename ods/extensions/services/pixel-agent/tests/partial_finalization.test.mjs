// Tool-limit finalization when the answer turn does not answer tool-free:
// substantive answer text sent together with tool calls is kept as a partial
// answer (the calls are refused and never run), and without any answer text
// the fixed stop text is followed by the host's list of pages read.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {createCompletionAssurance, pageTitle} from '../plugin/completion-assurance.mjs';
import {assistantMessageText, composeProgressFinalization, composeReadPages, createProgressFinalization,
  MAX_READ_PAGES_LISTED, partialFinalizationAnswer, PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE,
  PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE, PROGRESS_READ_PAGES_HEADING} from '../plugin/progress-finalization.mjs';

// tower3, round r8 (integration 7402eb38, Qwen3.5-27B, session 4acd7dc1,
// 10:59:12-11:02:10 UTC). The progress budget stopped the run after retailer
// fetches and extractions failed; the next web_search was refused with the
// instruction (290). The answer turn (291) wrote this summary and then called
// web_search twice, so the owner received only the canned stop text.
const R8_PROMPT = 'Research task as of 2026-09-25T10:59:11Z: Compare NVIDIA GeForce RTX 5070 versus AMD Radeon RX 9070 ' +
  'for a US buyer playing PC games at 2560x1440. Actually search the live web and open sources. No purchase. ' +
  'For each GPU use primary manufacturer documentation for VRAM GB and board power W; find one exact NEW US retailer SKU ' +
  'with price and stock, and one benchmark publisher comparing BOTH GPUs. Return one fenced JSON object then an explanation.';
const R8_ANSWER_TURN_TEXT = "Let me check the search results I already have and compile the final answer with the data I've verified:\n\n" +
  'From my earlier searches, I have:\n\n**RTX 5070:**\n' +
  '- Specs: 12GB GDDR7 VRAM, 250W board power (from ASUS TUF specs via TechPowerUp)\n' +
  '- Retail: Best Buy shows PNY GeForce RTX 5070 12GB OC at $629.99 (SKU: 6620354 for Epic X ARGB variant)\n\n' +
  '**RX 9070:**\n- Specs: 16GB GDDR6 VRAM, 220W typical board power (from AMD official)\n' +
  '- Retail: Newegg shows XFX Mercury RX 9070 XT OC 16GB at $809.99 (Model: RX-97TRGBBB9) - but this is the XT variant\n\n' +
  'I need to find the non-XT RX 9070 and a direct benchmark. Let me search one more time:\n\n';
const R8 = {
  nvidia: 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/',
  amd: 'https://www.amd.com/en/support/downloads/drivers.html/graphics/radeon-rx/radeon-rx-9000-series/amd-radeon-rx-9070.html',
  techspot: 'https://www.techspot.com/review/2962-amd-radeon-9070',
  verge5070: 'https://www.theverge.com/gpu-reviews/623441/nvidia-rtx-5070-review-test-benchmark',
  verge9070: 'https://www.theverge.com/gpu-reviews/624423/amd-radeon-rx-9070-xt-review-benchmarks-price',
  bestbuySite: 'https://www.bestbuy.com/site/pny-geforce-rtx-5070-12gb-oc-gddr7-pci-express-5-0-graphics-card-with-triple-fan-black/6535654.p',
  bestbuy: 'https://www.bestbuy.com/product/pny-nvidia-geforce-rtx-5070-epic-x-argb-overclocked-12gb-gddr7-pci-express-5-0-graphics-card-with-triple-fan-black',
  newegg: 'https://www.newegg.com/xfx-mercury-radeon-rx-9070-xt-oc-16gb-256-bit-gddr6-pci-express-5-0-rdna-4-graphics-card-rx-97trgbbb9/p/N82E16814150194',
};

const wrapped = title => `\n<<<EXTERNAL_UNTRUSTED_CONTENT id="fixture">>>\nSource: Web Fetch\n---\n${title}\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="fixture">>>`;
const searched = query => ({content: [{type: 'text', text: JSON.stringify({query, results: []})}], details: {status: 'ok'}});
const page = (url, {title, finalUrl = url} = {}) => ({content: [{type: 'text', text: `Fetched ${url}`}],
  details: {status: 200, url, finalUrl, text: `Evidence from ${finalUrl}`, ...(title ? {title: wrapped(title)} : {})}});
const extracted = url => ({content: [{type: 'text', text: `Targeted evidence from ${url}: 12 GB, 250 W.`}],
  details: {boundary: 'public-web-read-only', matched: true, source_url: url}});
const failed = error => ({isError: true, content: [{type: 'text', text: JSON.stringify({status: 'error', tool: 'web_fetch', error})}],
  details: {status: 'error'}});

// Drives the real guard through OpenClaw's order for each model round: model
// call, the assistant message written to the transcript (OpenClaw awaits that
// write before it dispatches the message's tool calls), before_tool_call for
// every call, then after_tool_call and tool_result_persist for each result.
function harness(name, prompt) {
  const context = {agentId: 'pixel', runId: `${name}-run`, sessionId: `${name}-session`, sessionKey: `agent:pixel:${name}`};
  const aborts = [];
  const verifications = [];
  const guard = createToolLoopGuard({abortRun: (id, key) => { aborts.push([id, key]); return true; },
    hostCitationVerifier: {allowed: () => true, async verify({urls}) {
      verifications.push(urls);
      return {fetched: urls.length, verified: urls.map(url => ({url})), elapsedMs: 1};
    }}});
  guard.observeRun(context, 'pixel', {prompt});
  const sdk = {runId: context.runId, sessionId: context.sessionId, sessionKey: context.sessionKey};
  let rounds = 0, calls = 0;
  const round = (toolCalls, text = '', {messageAfterCalls = false} = {}) => {
    const callId = `${context.runId}:model:${++rounds}`;
    guard.observeModelCall({callId}, sdk);
    guard.observeModelEnd({callId}, sdk);
    const ids = toolCalls.map(() => `${name}-call-${++calls}`);
    const message = {role: 'assistant', content: [...(text ? [{type: 'text', text}] : []),
      ...toolCalls.map(([toolName, params], index) => ({type: 'toolCall', id: ids[index], name: toolName, arguments: params}))]};
    const write = () => assert.equal(guard.observeAssistantMessage({message}, {agentId: 'pixel', sessionKey: context.sessionKey}), undefined);
    if (!messageAfterCalls) write();
    const decisions = toolCalls.map(([toolName, params], index) =>
      guard.beforeToolCall({toolName, toolCallId: ids[index], params}, {...context, toolName, toolCallId: ids[index]}));
    if (messageAfterCalls) write();
    const results = toolCalls.map(([, , outcome], index) => decisions[index]?.block
      ? {isError: true, content: [{type: 'text', text: decisions[index].blockReason}], details: {status: 'blocked', reason: decisions[index].blockReason}}
      : outcome);
    toolCalls.forEach(([toolName, params], index) => {
      const result = results[index];
      guard.afterToolCall({toolName, toolCallId: ids[index], params, result, ...(result?.isError ? {error: result.content[0].text} : {})},
        {...context, toolName, toolCallId: ids[index]});
    });
    toolCalls.forEach(([toolName], index) => guard.toolResultPersist({toolCallId: ids[index], message: {role: 'toolResult', toolName,
      toolCallId: ids[index], isError: results[index]?.isError === true, ...results[index]}}, {...context, toolName, toolCallId: ids[index]}));
    return decisions;
  };
  return {guard, context, aborts, verifications, round};
}

// Rows 255-290 of the r8 transcript (the two overflow compactions at 267 and
// 281 happened before the stop and are omitted).
function r8Replay({answerText = R8_ANSWER_TURN_TEXT, messageAfterCalls = false} = {}) {
  const run = harness('r8', R8_PROMPT);
  const {round} = run;
  round([['web_search', {query: 'NVIDIA GeForce RTX 5070 specifications VRAM board power official'}, searched('nvidia')],
    ['web_search', {query: 'AMD Radeon RX 9070 specifications VRAM board power official'}, searched('amd')]]);
  round([['web_fetch', {url: R8.nvidia}, page(R8.nvidia)], ['web_fetch', {url: R8.amd}, page(R8.amd)]]);
  round([['pixel_ods_web_extract', {url: R8.nvidia, query: 'Total Graphics Power VRAM 12GB'}, extracted(R8.nvidia)],
    ['pixel_ods_web_extract', {url: R8.amd, query: 'Typical Board Power'}, extracted(R8.amd)]]);
  round([['web_search', {query: 'RTX 5070 12GB 250W board power NVIDIA official specs'}, searched('power')],
    ['web_search', {query: 'RTX 5070 price Newegg Best Buy Amazon USD 2026'}, searched('price')]],
  'Good, I found the AMD RX 9070 specs: 16 GB VRAM, 220W typical board power. Now I need to get the RTX 5070 specs.');
  round([['web_search', {query: 'AMD Radeon RX 9070 price Newegg Amazon Best Buy USD 2026'}, searched('amd price')],
    ['web_search', {query: 'RTX 5070 vs RX 9070 1440p benchmark comparison FPS 2026'}, searched('benchmark')]]);
  round([['web_fetch', {url: R8.techspot, maxChars: 20000}, failed('Web fetch failed (403)')],
    ['web_fetch', {url: R8.verge5070, maxChars: 20000}, page(R8.verge5070, {title: 'Nvidia RTX 5070 review | The Verge'})]]);
  round([['web_fetch', {url: R8.verge9070, maxChars: 20000}, page(R8.verge9070)],
    ['web_fetch', {url: R8.bestbuySite, maxChars: 1000}, failed('request timed out')]]);
  round([['web_search', {query: 'PNY RTX 5070 12GB Best Buy SKU price in stock 2026'}, searched('pny')],
    ['web_search', {query: 'XFX RX 9070 16GB Newegg Best Buy price USD in stock 2026'}, searched('xfx')]]);
  round([['web_fetch', {url: R8.bestbuy}, failed('request timed out')], ['web_fetch', {url: R8.newegg}, failed('Web fetch failed (404)')]]);
  round([['pixel_ods_web_extract', {url: R8.bestbuy, query: 'price SKU'}, failed('Public page read failed: request timed out')],
    ['pixel_ods_web_extract', {url: R8.newegg, query: 'price model'}, failed('Public page read failed (404)')]],
  'Let me try a different approach - use pixel_ods_web_extract for the retailer pages:');
  const [instructed] = round([['web_search', {query: '"RTX 5070" OR "RX 9070" 1440p benchmark FPS comparison 2026'}, searched('x')]],
    'Let me try a simpler search for exact retail SKUs and use the search results directly:');
  const answerTurn = round([['web_search', {query: 'RX 9070 non-XT price USD Newegg Amazon 2026'}, searched('x')],
    ['web_search', {query: "RTX 5070 vs RX 9070 1440p benchmark TechSpot Tom's Hardware"}, searched('x')]], answerText, {messageAfterCalls});
  return {...run, instructed, answerTurn};
}

test('r8 replay: the answer turn\'s summary is delivered as a partial answer; its tool calls are refused', async () => {
  const {guard, context, aborts, instructed, answerTurn} = r8Replay();
  assert.deepEqual(instructed, {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION}, 'row 290: the stop carries the instruction');
  assert.deepEqual(answerTurn, [{block: true, blockReason: RUN_PROGRESS_STOP_REASON}, {block: true, blockReason: RUN_PROGRESS_STOP_REASON}],
    'rows 292-293: both calls are refused and never run');
  assert.deepEqual(aborts, [[context.sessionId, context.sessionKey]], 'the run still ends at that tool boundary, once');
  await guard.settleDelivery(context.runId);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.startsWith(R8_ANSWER_TURN_TEXT.trim()), delivery.text);
  const facts = delivery.text.slice(R8_ANSWER_TURN_TEXT.trim().length);
  assert.ok(facts.startsWith(`\n\n${PROGRESS_FINALIZATION_NOTE}\n\n${PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE}`), facts);
  assert.ok(!delivery.text.includes(RUN_PROGRESS_STOP_REASON));
  assert.ok(!delivery.text.includes(PROGRESS_READ_PAGES_HEADING), 'an answer is delivered, so no page list');
  assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: ''}}).payload.text, delivery.text);
});

test('r8 replay: the same partial answer when the transcript write follows the tool boundary', async () => {
  const {guard, context, aborts, answerTurn} = r8Replay({messageAfterCalls: true});
  assert.deepEqual(answerTurn.map(decision => decision?.blockReason), [RUN_PROGRESS_STOP_REASON, RUN_PROGRESS_STOP_REASON]);
  assert.equal(aborts.length, 1);
  await guard.settleDelivery(context.runId);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith(R8_ANSWER_TURN_TEXT.trim()));
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE));
});

test('r8 replay: narration with tool calls is no answer; the stop text lists the pages read', async () => {
  for (const messageAfterCalls of [false, true]) {
    const {guard, context, aborts} = r8Replay({messageAfterCalls,
      answerText: 'I found several useful sources. Let me now fetch the specific pages to get exact data:\n' +
        '1. TechSpot RX 9070 review\n2. The Verge RTX 5070 review'});
    assert.equal(aborts.length, 1);
    await guard.settleDelivery(context.runId);
    const delivery = guard.deliveryVerificationForRun(context.runId);
    assert.equal(delivery.status, 'failed');
    assert.equal(delivery.text, `${RUN_PROGRESS_STOP_REASON}\n\n${PROGRESS_READ_PAGES_HEADING}\n\n` +
      `- <${R8.nvidia}>\n- <${R8.amd}>\n- Nvidia RTX 5070 review | The Verge — <${R8.verge5070}>\n- <${R8.verge9070}>`, String(messageAfterCalls));
  }
});

test('a partial answer passes the tool-free checks and #6680 host verification before delivery', async () => {
  const unread = 'https://www.theverge.com/gpu-reviews/624423/amd-radeon-rx-9070-xt-review-benchmarks-price-2';
  const text = 'From the pages read before the limit:\n\n- RTX 5070: 12 GB of GDDR7 and 250 W total graphics power (NVIDIA product page).\n' +
    `- RX 9070: 16 GB of GDDR6 and 220 W typical board power; The Verge measured it at 1440p in ${unread}.\n` +
    '- US retail price and stock: not verified, because every retailer page failed to load.\n\nLet me search for a price once more:';
  const {guard, context, verifications, answerTurn} = r8Replay({answerText: text});
  assert.equal(answerTurn[0]?.blockReason, RUN_PROGRESS_STOP_REASON);
  await guard.settleDelivery(context.runId);
  assert.deepEqual(verifications, [[unread]], 'the unread cited page is host-verified once, as for a tool-free answer');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith(text.trim()));
  assert.ok(!delivery.text.includes('were not read successfully'), 'a host-verified page is not labelled unverified');

  // Every tool-free rule still applies to the text: no tool-call syntax, no
  // echo of the stop text, no unverified localhost URL.
  for (const bad of [`${text}\n<tool_call>{"name":"web_search","arguments":{}}</tool_call>`, `${text}\n\n${RUN_PROGRESS_STOP_REASON}`]) {
    const run = r8Replay({answerText: bad});
    assert.ok(run.guard.deliveryVerificationForRun(run.context.runId).text.startsWith(RUN_PROGRESS_STOP_REASON));
  }
  assert.equal(partialFinalizationAnswer(`${text}\nPreview: http://localhost:8080/`, {localUrlsForbidden: true}), undefined);
});

test('substantive means more than narration: at least 160 letters or digits across two or more other lines', () => {
  assert.equal(partialFinalizationAnswer(R8_ANSWER_TURN_TEXT), R8_ANSWER_TURN_TEXT.trim());
  for (const narration of [
    'Let me try a different approach - use pixel_ods_web_extract for the retailer pages:',
    'Good progress. Now I need to find the retail prices for both cards. Let me search Newegg and Best Buy for current listings and stock:',
    'I found several useful sources. Let me now fetch the specific pages to get exact data:\n1. TechSpot RX 9070 review\n2. The Verge RTX 5070 review',
    "I'll check the NVIDIA page next. Then I will open the AMD page. After that, I need to compare both benchmark tables before answering.",
    'Vou pesquisar os preços agora. Preciso abrir as páginas da Newegg e da Best Buy para confirmar o estoque e o preço atual de cada placa.',
  ]) assert.equal(partialFinalizationAnswer(narration), undefined, narration);
  // One long line is not enough on its own.
  assert.equal(partialFinalizationAnswer('RTX 5070: 12 GB GDDR7 VRAM and 250 W board power per the NVIDIA page, with PNY listing the OC card at 629.99 USD on Best Buy, which was in stock at the time.'), undefined);
});

test('state machine: only the answer turn\'s own message can become a partial answer', () => {
  const validate = text => partialFinalizationAnswer(text);
  const turn = () => {
    const finalization = createProgressFinalization();
    finalization.arm(true);
    assert.equal(finalization.toolBoundary(1, 'instructed-1'), 'instruct');
    // A late write of the instructed round's message never becomes the turn.
    finalization.modelCallStarted();
    assert.equal(finalization.assistantMessage(R8_ANSWER_TURN_TEXT, ['instructed-1'], validate), 'turn');
    return finalization;
  };
  const before = turn();
  before.assistantMessage(R8_ANSWER_TURN_TEXT, ['a', 'b'], validate);
  assert.equal(before.abortDeferred, true, 'writing the message changes nothing yet');
  assert.equal(before.toolBoundary(2, 'a'), 'stop');
  assert.equal(before.phase, 'partial');
  assert.equal(before.abortDeferred, false, 'the tool boundary still ends the run');
  assert.equal(before.toolBoundary(2, 'b'), 'stop');
  assert.equal(before.answer, R8_ANSWER_TURN_TEXT.trim(), 'a sibling call of the same message keeps it');
  assert.equal(before.toolBoundary(3, 'c'), 'stop');
  assert.equal(before.phase, 'failed', 'a call from any other message forfeits it');

  const after = turn();
  assert.equal(after.toolBoundary(2, 'a'), 'stop');
  assert.equal(after.phase, 'failed');
  assert.equal(after.assistantMessage(R8_ANSWER_TURN_TEXT, ['x'], validate), 'failed', 'another message cannot claim the forfeit');
  assert.equal(after.assistantMessage(R8_ANSWER_TURN_TEXT, ['a', 'b'], validate), 'partial');
  assert.equal(after.modelCallStarted(), 'failed', 'any further model call still forfeits the answer');

  const late = turn();
  late.toolBoundary(2, 'a');
  late.modelCallStarted();
  assert.equal(late.assistantMessage(R8_ANSWER_TURN_TEXT, ['a'], validate), 'failed', 'no revival once another call started');

  const narration = turn();
  narration.assistantMessage('Let me search once more:', ['a'], validate);
  assert.equal(narration.toolBoundary(2, 'a'), 'stop');
  assert.equal(narration.phase, 'failed');
  assert.equal(narration.answer, undefined);

  // Without observed model rounds there is no identified answer turn.
  const hookless = createProgressFinalization();
  hookless.arm(true);
  hookless.toolBoundary(0, 'first');
  assert.equal(hookless.assistantMessage(R8_ANSWER_TURN_TEXT, ['second'], validate), 'instructed');
  assert.equal(hookless.toolBoundary(0, 'second'), 'stop');
  assert.equal(hookless.phase, 'failed');
});

test('only visible text blocks count as the answer', () => {
  assert.equal(assistantMessageText({content: [{type: 'thinking', thinking: 'Plan: search again.'},
    {type: 'text', text: '<think>internal</think>Visible summary.'}, {type: 'toolCall', id: 'a', name: 'web_search'}]}), 'Visible summary.');
  assert.equal(assistantMessageText({content: [{type: 'text', text: 'hidden reasoning</think>\nAnswer'}]}), 'Answer');
  assert.equal(assistantMessageText({content: 'plain'}), '');
});

test('page titles are reduced to plain words; titles that could link or mark up are dropped', () => {
  assert.equal(pageTitle(wrapped('GeForce RTX 5070 vs Radeon RX 9070 [videocardbenchmark.net] by PassMark Software')),
    'GeForce RTX 5070 vs Radeon RX 9070 videocardbenchmark.net by PassMark Software');
  assert.equal(pageTitle(wrapped('NVIDIA GeForce RTX 5070 Specs | TechPowerUp GPU Database')), 'NVIDIA GeForce RTX 5070 Specs | TechPowerUp GPU Database');
  assert.equal(pageTitle(wrapped('Click [here](https://evil.example) <b>now</b>')), undefined);
  assert.equal(pageTitle(wrapped('Deals at www.example.com')), undefined);
  assert.equal(pageTitle(wrapped('Contact sales@example.com')), undefined);
  assert.equal(pageTitle(wrapped('**Bold** `code` \\escape\u0007')), 'Bold code escape');
  assert.equal(pageTitle('\n<<<EXTERNAL_UNTRUSTED_CONTENT id="x">>>\nSource: Web Fetch\n---\nunterminated'), undefined);
  assert.equal(pageTitle(wrapped('x'.repeat(300))).length, 160);
  assert.equal(pageTitle(undefined), undefined);
});

test('the read list comes from host receipts only: successful reads and host verification, deduplicated', () => {
  const assurance = createCompletionAssurance();
  assurance.begin('Search the live web and open sources for RTX 5070 specs.');
  const observe = (tool, result, params = {}) => assurance.observe(tool, {params, result});
  observe('web_search', {details: {results: [{url: 'https://search-lead.example.com/'}]}});
  observe('web_fetch', page(R8.nvidia.slice(0, -1), {title: 'GeForce RTX 5070 Family | NVIDIA'}));
  observe('pixel_ods_web_extract', extracted(R8.nvidia), {url: R8.nvidia});
  observe('web_fetch', failed('Web fetch failed (403)'));
  observe('web_fetch', {content: [{type: 'text', text: ''}], details: {status: 200, url: R8.techspot, finalUrl: R8.techspot, text: ''}});
  observe('web_fetch', {content: [{type: 'text', text: 'x'}], details: {status: 404, url: R8.newegg, finalUrl: R8.newegg, text: 'Not found'}});
  observe('web_fetch', page('https://videocardbenchmark.net/compare/1', {finalUrl: 'https://www.videocardbenchmark.net/compare/1'}));
  observe('web_fetch', page('https://www.videocardbenchmark.net/compare/1', {title: 'Compare | PassMark'}));
  assurance.observeHostVerification(R8.verge5070);
  assert.deepEqual(assurance.readPages, [
    {url: R8.nvidia.slice(0, -1), title: 'GeForce RTX 5070 Family | NVIDIA'},
    {url: 'https://www.videocardbenchmark.net/compare/1', title: 'Compare | PassMark'},
    {url: R8.verge5070},
  ]);
});

test('the read list is capped and otherwise fixed text', () => {
  assert.equal(composeReadPages([]), '');
  assert.equal(composeReadPages([{url: 'javascript:alert(1)'}]), '');
  const pages = Array.from({length: MAX_READ_PAGES_LISTED + 3}, (_, index) => ({url: `https://example.org/${index}`, ...(index % 2 ? {title: `Page ${index}`} : {})}));
  const list = composeReadPages(pages);
  assert.ok(list.startsWith(`${PROGRESS_READ_PAGES_HEADING}\n\n- <https://example.org/0>\n- Page 1 — <https://example.org/1>\n`));
  assert.equal(list.split('\n').filter(line => line.startsWith('- ')).length, MAX_READ_PAGES_LISTED + 1);
  assert.ok(list.endsWith('\n- and 3 more'));
});

test('an answer next to a published preview carries the host\'s requested-text miss', () => {
  const preview = {url: 'https://preview.ods.test/p/night-garden/'};
  const note = 'The published page does not contain text the owner requested: "Night Garden". The preview is available, but that requirement is not met.';
  const composed = composeProgressFinalization('The page was published with the gallery section.', {preview, requestedTextMissing: note});
  assert.ok(composed.endsWith(`This is the last verified publication, not proof that all requested work completed.\n\n${note}`));
  assert.ok(!composeProgressFinalization('The page was published with the gallery section.', {requestedTextMissing: note}).includes(note),
    'no preview, no requested-text note');
});

test('cancellation and receipt-based runs keep the strict stop text without a page list', async () => {
  const stopAfterRead = (name, prompt) => {
    const run = harness(name, prompt);
    run.round([['web_fetch', {url: R8.nvidia}, page(R8.nvidia)]]);
    for (let index = 0; index < RUN_PROGRESS_LIMITS.consecutiveFailures; index++) {
      run.round([['web_fetch', {url: `${R8.newegg}?${index}`}, failed('Web fetch failed (403)')]]);
    }
    return run;
  };
  // Control: an eligible run with the same reads lists the page.
  const control = stopAfterRead('control', R8_PROMPT);
  control.round([['web_search', {query: 'x'}, searched('x')]]);
  control.round([['web_search', {query: 'y'}, searched('y')]]);
  assert.equal(control.guard.deliveryVerificationForRun(control.context.runId).text,
    `${RUN_PROGRESS_STOP_REASON}

${PROGRESS_READ_PAGES_HEADING}

- <${R8.nvidia}>`);

  for (const prompt of ['Download the exact bytes of the remote file https://example.com/report.pdf.',
    "Inspect this computer's CPU health and explain it."]) {
    const {guard, context, round} = stopAfterRead('receipts', prompt);
    assert.equal(round([['web_search', {query: 'x'}, searched('x')]])[0]?.blockReason, RUN_PROGRESS_STOP_REASON, prompt);
    assert.equal(guard.deliveryVerificationForRun(context.runId).text, RUN_PROGRESS_STOP_REASON, prompt);
  }

  const user = 'ods-' + 'd'.repeat(64);
  const cancelled = stopAfterRead(`openai-user:${user}`, R8_PROMPT);
  cancelled.guard.observeModelCall({}, cancelled.context);
  assert.equal(await cancelled.guard.abortUserRun(user), true);
  assert.equal(cancelled.guard.deliveryVerificationForRun(cancelled.context.runId).text, RUN_PROGRESS_STOP_REASON);
});
