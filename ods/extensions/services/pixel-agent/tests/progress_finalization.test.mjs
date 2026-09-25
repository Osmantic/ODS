import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, DEFAULT_WEB_TOOL_LIMITS, FREE_CORRECTIONS_PER_KIND, PHANTOM_PROCESS_REASON,
  WEB_LOOP_ABORT_REASON, WEB_LOOP_DELIVERY_REASON, WEB_SEARCH_BUDGET_EXHAUSTED_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {composeProgressFinalization, createProgressFinalization, progressFinalizationAnswer,
  PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE, PROGRESS_READ_PAGES_HEADING} from '../plugin/progress-finalization.mjs';

// Without an answer, the stop text is followed by the host's list of pages read.
const readList = urls => `\n\n${PROGRESS_READ_PAGES_HEADING}\n\n${urls.map(url => `- <${url}>`).join('\n')}`;

// Replays the tower3 fleet stop (2026-09-25, Qwen3.5-27B): four pages were
// read successfully, then 403/404/ENOTFOUND fetches and search-allowance
// refusals exhausted the budget and the owner received only the stop text.
const RESEARCH_PROMPT = 'Compare NVIDIA GeForce RTX 5070 versus AMD Radeon RX 9070 for a US buyer playing PC games at 2560x1440. ' +
  'Actually search the live web and open sources. For each GPU use primary manufacturer documentation for VRAM GB and board power W; ' +
  'find one exact NEW US retailer SKU with price and stock, and one benchmark publisher comparing BOTH GPUs. ' +
  'If evidence is unavailable state it honestly. Return one fenced JSON object then a concise practical explanation.';
const PAGES = {
  nvidia: 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/',
  amd: 'https://www.amd.com/en/products/graphics/desktops/radeon/9000-series/amd-radeon-rx-9070.html',
  gamersnexus: 'https://gamersnexus.net/gpus/incredibly-efficient-amd-rx-9070-gpu-review-benchmarks-vs-9070-xt-rtx-5070',
  newegg: 'https://www.newegg.com/pny-technologies-inc-oc-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814133902',
};
const UNREAD_RETAILER = 'https://www.bestbuy.com/site/amd-radeon-rx-9070-16gb/6614150.p';
const RESEARCH_ANSWER = '```json\n' + JSON.stringify({
  observedAt: '2026-09-25T07:27:00Z',
  components: [
    {name: 'RTX 5070', primarySpecs: {vramGB: 12, boardPowerW: 250, sourceUrls: [PAGES.nvidia]},
      retail: {retailer: 'Newegg', sku: 'PNY GeForce RTX 5070 OC 12GB', condition: 'new', price: 549.99,
        currency: 'USD', inStock: true, observedAt: '2026-09-25T07:26:00Z', locationShippingCaveat: 'US; tax not included', sourceUrl: PAGES.newegg}},
    {name: 'RX 9070', primarySpecs: {vramGB: 16, boardPowerW: 220, sourceUrls: [PAGES.amd]}, retail: null},
  ],
  benchmark: {publisher: 'GamersNexus', sourceUrl: PAGES.gamersnexus, resolution: '2560x1440', results: {'RTX 5070': null, 'RX 9070': null}},
}, null, 2) + '\n```\n\nThe RX 9070 has more VRAM (16 GB vs 12 GB). I found an RX 9070 listing at ' + UNREAD_RETAILER +
  ' but its price was not in the evidence, so RX 9070 retail is null. Benchmark numbers were not extracted before the tool limit.';

const context = {agentId: 'pixel', runId: 'finalize-run', sessionId: 'finalize-session', sessionKey: 'agent:pixel:finalize'};

function guardFixture(prompt = RESEARCH_PROMPT) {
  const aborts = [];
  const guard = createToolLoopGuard({abortRun: (id, key) => { aborts.push([id, key]); return true; }});
  guard.observeRun(context, 'pixel', {prompt});
  return {guard, aborts};
}

function readPage(guard, url, id) {
  guard.afterToolCall({toolName: 'web_fetch', toolCallId: id, params: {url}, result: {
    content: [{type: 'text', text: `Fetched ${url}`}],
    details: {status: 200, url, finalUrl: url, text: `Evidence from ${url}`}}}, {...context, toolName: 'web_fetch', toolCallId: id});
}

function persistFailure(guard, id, toolName = 'web_fetch', text = 'Web fetch failed (403)') {
  return guard.toolResultPersist({toolCallId: id, message: {role: 'toolResult', toolName, toolCallId: id,
    isError: true, content: [{type: 'text', text}]}}, {...context, toolName, toolCallId: id});
}

function exhaustByFailures(guard) {
  let persisted;
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) persisted = persistFailure(guard, `failed-${i}`);
  return persisted;
}

function callTool(guard, toolName = 'web_search', params = {query: 'RTX 5070 vs RX 9070 1440p benchmark'}, id = `call-${toolName}`) {
  return guard.beforeToolCall({toolName, toolCallId: id, params}, {...context, toolName, toolCallId: id});
}

// The model call after the exhausting result still sees that original result
// (the persisted rewrite is transcript-only). Its next tool call is refused
// with the instruction, and the following call is the single answer turn.
function enterAnswerTurn(guard) {
  guard.observeModelCall({}, context);
  assert.deepEqual(callTool(guard), {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION});
  guard.observeModelEnd({}, context);
  guard.observeModelCall({}, context);
}

const text = result => result?.message?.content?.map(block => block.text).join('\n');

test('state machine grants exactly one answer turn after the instruction', () => {
  const finalization = createProgressFinalization();
  assert.equal(finalization.abortDeferred, false, 'no deferral before the budget is exhausted');
  assert.equal(finalization.arm(true), 'pending');
  assert.equal(finalization.modelCallStarted(), 'pending', 'the call that is still unaware of the stop');
  assert.equal(finalization.toolBoundary(3), 'instruct');
  assert.equal(finalization.toolBoundary(3), 'instruct', 'parallel sibling calls carry the same instruction');
  assert.equal(finalization.modelCallStarted(), 'turn');
  assert.equal(finalization.abortDeferred, true);
  const answer = 'The RTX 5070 has 12 GB of VRAM according to the NVIDIA page.';
  assert.equal(finalization.accept(answer), answer);
  assert.equal(finalization.phase, 'answered');
  assert.equal(finalization.modelCallStarted(), 'failed', 'any further model call forfeits the answer');
  assert.equal(finalization.answer, undefined);
  assert.equal(finalization.accept('A second answer is never accepted after the turn was spent.'), undefined);

  const toolInTurn = createProgressFinalization();
  toolInTurn.arm(true); toolInTurn.toolBoundary(1); toolInTurn.modelCallStarted();
  assert.equal(toolInTurn.toolBoundary(2), 'stop');
  assert.equal(toolInTurn.phase, 'failed');
  assert.equal(toolInTurn.abortDeferred, false);

  const ineligible = createProgressFinalization();
  assert.equal(ineligible.arm(false), 'unavailable');
  assert.equal(ineligible.toolBoundary(), 'stop');
  assert.equal(ineligible.abortDeferred, false);

  // Without observed model rounds only the first refusal carries the
  // instruction; the next call ends the run (the hookless call-count bound).
  const hookless = createProgressFinalization();
  hookless.arm(true);
  assert.equal(hookless.toolBoundary(0), 'instruct');
  assert.equal(hookless.toolBoundary(0), 'stop');
  assert.equal(hookless.abortDeferred, false);
  // Siblings are bounded even when a stalled hook leaves the round unchanged.
  const stalled = createProgressFinalization();
  stalled.arm(true);
  const refusals = Array.from({length: 10}, () => stalled.toolBoundary(5));
  assert.deepEqual(refusals, [...Array(8).fill('instruct'), 'stop', 'stop']);

  const unaware = createProgressFinalization();
  unaware.arm(true);
  assert.equal(unaware.modelCallStarted(), 'pending');
  assert.equal(unaware.modelCallStarted(), 'failed', 'a second uninstructed call is not waited for');
});

test('answer validation rejects empty, degenerate, tool-like and echoed output', () => {
  const valid = 'The NVIDIA page lists 12 GB of GDDR7 memory for the RTX 5070; the RX 9070 price is unverified.';
  assert.equal(progressFinalizationAnswer(`  ${valid}\u0007 `), valid, 'control characters are stripped');
  for (const bad of [undefined, '', '   ', 'OK.', '...', 'NO_REPLY', 'No response from OpenClaw.',
    '<tool_call>{"name":"web_search","arguments":{"query":"RTX 5070"}}</tool_call>',
    '{"name": "web_fetch", "arguments": {"url": "https://example.com"}}',
    'I will search again for the benchmark numbers and report back.',
    `${RUN_PROGRESS_STOP_REASON}`, `${PROGRESS_FINALIZATION_INSTRUCTION} Understood.`,
    'x'.repeat(20001)]) assert.equal(progressFinalizationAnswer(bad), undefined, String(bad).slice(0, 60));
});

test('composition keeps the requested format first and closes an unterminated fence', () => {
  const composed = composeProgressFinalization('```json\n{"vramGB": 12}', {verificationStatus: 'failed',
    unverifiedLinks: [UNREAD_RETAILER], previewExpected: true});
  assert.ok(composed.startsWith('```json\n{"vramGB": 12}\n```\n\n'));
  assert.ok(composed.includes(PROGRESS_FINALIZATION_NOTE));
  assert.match(composed, /latest recognized test or verification command failed/);
  assert.ok(composed.includes(`<${UNREAD_RETAILER}>`));
  assert.match(composed, /ODS did not verify a preview in this response\. No localhost URL is live or claimed\./);
  assert.doesNotMatch(composed, /Open last published preview/);
});

test('research stop (tower3 replay): one tool-free turn yields the evidence-based answer plus an honest note', () => {
  const {guard, aborts} = guardFixture();
  Object.values(PAGES).forEach((url, index) => readPage(guard, url, `read-${index}`));
  assert.equal(text(exhaustByFailures(guard)), RUN_PROGRESS_STOP_REASON, 'the saved transcript copy is unchanged');
  // The model call that issued the exhausting tool calls may end late.
  guard.observeModelEnd({}, context);
  enterAnswerTurn(guard);
  guard.observeModelEnd({}, context);
  assert.deepEqual(aborts, [], 'neither the unaware call nor the answer turn is aborted');
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context), undefined, 'no revision is requested');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed', 'the request remains incomplete');
  assert.ok(delivery.text.startsWith(RESEARCH_ANSWER), 'the fenced JSON stays first');
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.ok(!delivery.text.includes(RUN_PROGRESS_STOP_REASON));
  assert.ok(delivery.text.includes(`<${UNREAD_RETAILER}>`), 'unread citations are labelled by the host');
  for (const url of Object.values(PAGES)) assert.ok(!delivery.text.includes(`<${url}>`), `${url} was read`);
  assert.equal(delivery.preview, undefined);
  assert.doesNotMatch(delivery.text, /Open last published preview|preview is ready/i);
  assert.ok(delivery.text.length <= 32 * 1024, 'fits the ingress verification text limit');
  const sent = guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: RESEARCH_ANSWER}});
  assert.equal(sent.payload.text, delivery.text, 'in-process delivery and ingress agree');
  assert.equal(guard.continuationAllowed(context.runId), false);
});

test('tools stay blocked during the answer turn and a tool call falls back to the stop text', () => {
  for (const [toolName, params] of [['web_search', {query: 'again'}], ['write', {path: 'report.md', content: 'x'}],
    ['exec', {command: 'python -m unittest'}], ['tool_call', {id: 'openclaw:core:web_fetch', args: {url: PAGES.amd}}]]) {
    const {guard, aborts} = guardFixture();
    exhaustByFailures(guard);
    enterAnswerTurn(guard);
    assert.deepEqual(callTool(guard, toolName, params, 'late'), {block: true, blockReason: RUN_PROGRESS_STOP_REASON}, toolName);
    assert.deepEqual(aborts, [[context.sessionId, context.sessionKey]], `${toolName}: aborted once at the tool boundary`);
    assert.equal(callTool(guard, 'read', {path: 'x'}, 'sibling')?.blockReason, RUN_PROGRESS_STOP_REASON);
    guard.observeModelEnd({}, context);
    assert.equal(aborts.length, 1, 'the acknowledged abort is not repeated');
    guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
    const delivery = guard.deliveryVerificationForRun(context.runId);
    assert.equal(delivery.status, 'failed');
    assert.equal(delivery.text, RUN_PROGRESS_STOP_REASON);
  }
});

test('a no-progress stop at model start refuses the next call with the instruction, then allows one answer', () => {
  const {guard, aborts} = guardFixture();
  for (let i = 0; i <= RUN_PROGRESS_LIMITS.roundsWithoutProgress; i++) guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  assert.deepEqual(aborts, []);
  assert.deepEqual(callTool(guard), {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION});
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  assert.deepEqual(aborts, []);
  guard.beforeAgentFinalize({lastAssistantMessage: 'The collected evidence shows 12 GB for the RTX 5070; the remaining fields are unverified.'}, context);
  assert.match(guard.deliveryVerificationForRun(context.runId).text, /^The collected evidence shows 12 GB/);
  assert.deepEqual(aborts, []);

  // A model that never reaches a tool boundary is not waited for indefinitely.
  const stuck = guardFixture();
  for (let i = 0; i <= RUN_PROGRESS_LIMITS.roundsWithoutProgress + 1; i++) stuck.guard.observeModelCall({}, context);
  stuck.guard.observeModelEnd({}, context);
  assert.equal(stuck.aborts.length, 1);
  assert.equal(stuck.guard.deliveryVerificationForRun(context.runId).text, RUN_PROGRESS_STOP_REASON);
});

test('a final answer from the call that was unaware of the stop is delivered with the same note', () => {
  const {guard, aborts} = guardFixture();
  exhaustByFailures(guard);
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  guard.beforeAgentFinalize({lastAssistantMessage: 'NVIDIA lists 12 GB for the RTX 5070 and AMD lists 16 GB for the RX 9070.'}, context);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith('NVIDIA lists 12 GB'));
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.deepEqual(aborts, []);
});

test('empty, degenerate, tool-like or overrun finalization falls back to the canned stop text', () => {
  for (const answer of [undefined, '', 'OK', 'I will search again for the benchmark numbers.',
    '<tool_call>{"name":"web_search","arguments":{"query":"RTX"}}</tool_call>', RUN_PROGRESS_STOP_REASON]) {
    const {guard} = guardFixture();
    exhaustByFailures(guard);
    enterAnswerTurn(guard);
    guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
    const delivery = guard.deliveryVerificationForRun(context.runId);
    assert.equal(delivery.status, 'failed', String(answer));
    assert.equal(delivery.text, RUN_PROGRESS_STOP_REASON, String(answer));
    assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: answer ?? ''}}).payload.text,
      RUN_PROGRESS_STOP_REASON);
  }
  // A host-rejected call skips before_tool_call; the next model call forfeits
  // the turn and restores the model-end abort.
  const {guard, aborts} = guardFixture();
  exhaustByFailures(guard);
  enterAnswerTurn(guard);
  assert.equal(text(persistFailure(guard, 'native', 'tool_call', 'Validation failed')), RUN_PROGRESS_STOP_REASON);
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  assert.equal(aborts.length, 1);
  guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, RUN_PROGRESS_STOP_REASON);
});

function verifiedPreview(guard) {
  const write = {path: 'night-garden/index.html', content: '<!doctype html><title>Night Garden</title><h1>Night Garden</h1>'};
  callTool(guard, 'write', write, 'write');
  guard.afterToolCall({toolName: 'write', toolCallId: 'write', params: write, result: {details: {status: 'completed'}}},
    {...context, toolName: 'write', toolCallId: 'write'});
  const params = {relativeDirectory: 'night-garden'};
  assert.notEqual(callTool(guard, 'pixel_ods_workspace_preview', params, 'publish')?.block, true);
  const entry = Buffer.from('index.html'), bytes = Buffer.from(write.content);
  const pathLength = Buffer.alloc(4), contentLength = Buffer.alloc(8);
  pathLength.writeUInt32BE(entry.length); contentLength.writeBigUInt64BE(BigInt(bytes.length));
  const sha256 = createHash('sha256').update(pathLength).update(entry).update(contentLength).update(bytes).digest('hex');
  const siteId = `site-${sha256.slice(0, 24)}`;
  const details = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: 'night-garden',
    files: 1, bytes: bytes.length, sha256, siteId, entryFile: 'index.html', entrySha256: createHash('sha256').update(bytes).digest('hex'),
    port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
  guard.afterToolCall({toolName: 'pixel_ods_workspace_preview', toolCallId: 'publish', params, result: {details}},
    {...context, toolName: 'pixel_ods_workspace_preview', toolCallId: 'publish'});
  return details;
}

test('visual stops never gain an unverified preview or localhost claim', () => {
  const prompt = 'Create a polished static event website in night-garden and publish a verified Pixel workspace preview.';
  const claims = {
    fabricated: 'I built the site in night-garden. Open http://localhost:8080/ to view the finished event page.',
    honest: 'I wrote night-garden/index.html with the three event cards. The preview was not published before the tool limit.',
  };
  for (const [kind, answer] of Object.entries(claims)) {
    const {guard} = guardFixture(prompt);
    exhaustByFailures(guard);
    enterAnswerTurn(guard);
    guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
    const delivery = guard.deliveryVerificationForRun(context.runId);
    assert.equal(delivery.status, 'failed', kind);
    assert.equal(delivery.preview, undefined, kind);
    assert.doesNotMatch(delivery.text, /Open last published preview/, kind);
    if (kind === 'fabricated') assert.equal(delivery.text, RUN_PROGRESS_STOP_REASON, 'an unverified local URL forfeits the answer');
    else {
      assert.ok(delivery.text.startsWith(answer));
      assert.match(delivery.text, /ODS did not verify a preview in this response\. No localhost URL is live or claimed\./);
    }
  }
  // Only the host-verified publication may be linked, and only as a snapshot.
  const {guard} = guardFixture(prompt);
  const details = verifiedPreview(guard);
  exhaustByFailures(guard);
  enterAnswerTurn(guard);
  guard.beforeAgentFinalize({lastAssistantMessage: `The page is published at ${details.url} but the sold-out toggle is unverified.`}, context);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith('The page is published at'));
  assert.ok(delivery.text.includes(`[Open last published preview](${details.url})`));
  assert.match(delivery.text, /not proof that all requested work completed/);
  assert.equal(delivery.preview.sha256, details.sha256);
});

test('coding stops report a failed verification command alongside the answer', () => {
  const {guard} = guardFixture('Fix /workspace/calc/app.py and run python -m unittest in /workspace/calc.');
  const params = {command: 'python -m unittest', workdir: '/workspace/calc'};
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    callTool(guard, 'exec', {...params, command: `${params.command} -v${' '.repeat(i)}`}, `test-${i}`);
    guard.afterToolCall({toolName: 'exec', toolCallId: `test-${i}`, params, result: {isError: true,
      details: {status: 'completed', exitCode: 1}, content: [{type: 'text', text: 'FAILED (failures=1)'}]}},
    {...context, toolName: 'exec', toolCallId: `test-${i}`});
  }
  guard.observeModelCall({}, context);
  assert.equal(callTool(guard, 'exec', params, 'retry')?.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  guard.observeModelCall({}, context);
  guard.beforeAgentFinalize({lastAssistantMessage: 'app.py now validates input, but test_divide still fails with ZeroDivisionError; the fix is incomplete.'}, context);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith('app.py now validates input'));
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.equal(guard.verificationStatus(context.runId), 'failed');
  assert.match(delivery.text, /latest recognized test or verification command failed/);
});

test('receipt-based runs keep the strict stop without an answer turn', () => {
  for (const prompt of ['/extensions install https://github.com/example/project',
    'Download the exact bytes of the remote file https://example.com/report.pdf.',
    "Inspect this computer's CPU health and explain it."]) {
    const {guard, aborts} = guardFixture(prompt);
    exhaustByFailures(guard);
    assert.equal(callTool(guard, 'read', {path: 'x'})?.blockReason, RUN_PROGRESS_STOP_REASON, prompt);
    guard.observeModelEnd({}, context);
    assert.equal(aborts.length, 1, prompt);
    guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
    assert.ok(!guard.deliveryVerificationForRun(context.runId).text.includes(RESEARCH_ANSWER), prompt);
  }
});

test('owner cancellation during the answer turn discards the answer', async () => {
  const user = 'ods-' + 'c'.repeat(64);
  const cancelled = {...context, sessionKey: `agent:pixel:openai-user:${user}`};
  const aborts = [];
  const guard = createToolLoopGuard({abortRun: id => { aborts.push(id); return true; }});
  guard.observeRun(cancelled, 'pixel', {prompt: RESEARCH_PROMPT});
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) guard.toolResultPersist({toolCallId: `c-${i}`,
    message: {role: 'toolResult', toolName: 'web_fetch', toolCallId: `c-${i}`, isError: true, content: []}}, {...cancelled, toolCallId: `c-${i}`});
  guard.observeModelCall({}, cancelled);
  assert.equal(await guard.abortUserRun(user), true);
  guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, cancelled);
  assert.equal(guard.deliveryVerificationForRun(cancelled.runId).text, RUN_PROGRESS_STOP_REASON);
});

test('no change when the budget is not hit', () => {
  const {guard, aborts} = guardFixture();
  Object.values(PAGES).forEach((url, index) => readPage(guard, url, `read-${index}`));
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
    assert.doesNotMatch(JSON.stringify(persistFailure(guard, `failed-${i}`) ?? {}), /tool limit|stopped after repeated/);
  }
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  assert.equal(callTool(guard, 'web_fetch', {url: 'https://www.techpowerup.com/review/'}, 'next')?.block, undefined);
  guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  // The answer's own wording mentions a tool limit; the host note must not appear.
  assert.doesNotMatch(delivery.text ?? '', /reached its tool limit|stopped after repeated/);
  assert.deepEqual(aborts, []);
  const reply = guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: RESEARCH_ANSWER}});
  assert.doesNotMatch(reply?.payload?.text ?? RESEARCH_ANSWER, /reached its tool limit|stopped after repeated/);
});

test('free corrections apply before exhaustion; after it the single answer turn applies', () => {
  const {guard, aborts} = guardFixture('Run the existing Python unit tests in /workspace/project and report the result.');
  const tests = {command: 'python3 -m unittest', workdir: '/workspace/project'};
  assert.notEqual(callTool(guard, 'exec', tests, 'tests')?.block, true);
  guard.afterToolCall({toolName: 'exec', toolCallId: 'tests', params: tests, result: {content: [{type: 'text', text: 'Ran 3 tests. OK'}],
    details: {status: 'completed', exitCode: 0}}}, {...context, toolName: 'exec', toolCallId: 'tests'});
  const phantom = id => {
    const decision = callTool(guard, 'process', {action: 'poll'}, id);
    persistFailure(guard, id, 'process', decision?.blockReason);
    return decision?.blockReason;
  };
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) persistFailure(guard, `failed-${i}`);
  // Before exhaustion: the free phantom answers are neither charged nor progress.
  for (let i = 0; i < FREE_CORRECTIONS_PER_KIND; i++) assert.equal(phantom(`free-${i}`), PHANTOM_PROCESS_REASON);
  assert.equal(callTool(guard, 'read', {path: 'README.md'}, 'probe')?.block, undefined, 'free answers were not charged');
  // The unchanged fuse: the next real failure exhausts the budget.
  persistFailure(guard, 'failed-last');
  guard.observeModelCall({}, context);
  assert.equal(phantom('after-stop'), PROGRESS_FINALIZATION_INSTRUCTION, 'no free correction once the budget is exhausted');
  guard.observeModelCall({}, context);
  assert.equal(phantom('answer-turn'), RUN_PROGRESS_STOP_REASON);
  assert.deepEqual(aborts, [[context.sessionId, context.sessionKey]], 'a tool call in the answer turn ends the run');
});

// Replays tower1 session 5e600138 (integration build e482ce65, 2026-09-25
// 09:08-09:12 UTC): the search allowance ran out, a context-overflow error
// round was compacted, then ENOTFOUND, a second search refusal and a 404 left
// three consecutive failures. The next web_search hit the research web-loop
// terminal, which aborted at once (WEB_LOOP_ABORT_REASON; its receipt then
// exhausted the progress budget) and the owner got only the canned stop text.
function tower1Replay({finalTurn}) {
  const {guard, aborts} = guardFixture();
  let n = 0;
  const call = (toolName, params, outcome) => {
    const id = `t1-${++n}`, ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, toolCallId: id, params}, ctx);
    const result = decision?.block
      ? {content: [{type: 'text', text: decision.blockReason}], details: {status: 'blocked', reason: decision.blockReason}}
      : outcome;
    const isError = decision?.block === true || result?.isError === true;
    guard.afterToolCall({toolName, toolCallId: id, params, result, ...(isError ? {error: result.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolCallId: id, message: {role: 'toolResult', toolName, toolCallId: id, isError, ...result}}, ctx);
    return decision;
  };
  const round = () => guard.observeModelCall({}, context);
  const page = url => ({content: [{type: 'text', text: `Fetched ${url}`}], details: {status: 200, url, finalUrl: url, text: `Evidence from ${url}`}});
  const failure = error => ({isError: true, content: [{type: 'text', text: JSON.stringify({status: 'error', tool: 'web_fetch', error})}],
    details: {status: 'error'}});
  const search = query => ({content: [{type: 'text', text: JSON.stringify({query, results: []})}], details: {status: 'ok'}});
  Object.values(PAGES).forEach(url => { round(); call('web_fetch', {url}, page(url)); });
  for (let i = 0; i < DEFAULT_WEB_TOOL_LIMITS.search; i++) { round(); call('web_search', {query: `gpu ${i}`}, search(`gpu ${i}`)); }
  round();
  assert.equal(call('web_search', {query: 'RX 9070 Newegg price'}, search('x'))?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  const toms = 'https://www.tomshardware.com/pc-components/rx-9070-price';
  round(); call('web_fetch', {url: toms}, page(toms));
  // Context-overflow error round, then compaction and the resumed attempt.
  round(); guard.observeModelEnd({}, context);
  guard.observeRun(context, 'pixel', {prompt: RESEARCH_PROMPT});
  round(); call('web_fetch', {url: 'https://www.gamersnexusguide.com/amd-radeon-rx-9070-review'}, failure('getaddrinfo ENOTFOUND www.gamersnexusguide.com'));
  round();
  assert.equal(call('web_search', {query: 'RTX 5070 RX 9070 1440p GamersNexus'}, search('x'))?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  round(); call('web_fetch', {url: 'https://www.gamersnexus.net/reviews/amd-radeon-rx-9070-review-benchmarks'}, failure('Web fetch failed (404)'));
  round();
  const refused = call('web_search', {query: 'site:gamersnexus.net RTX 5070 RX 9070'}, search('x'));
  guard.observeModelEnd({}, context);
  round();
  const late = finalTurn === 'tool' ? call('web_search', {query: 'one more'}, search('x')) : undefined;
  guard.observeModelEnd({}, context);
  guard.beforeAgentFinalize({lastAssistantMessage: finalTurn === 'tool' ? 'Let me search again.' : RESEARCH_ANSWER}, context);
  return {guard, aborts, refused, late};
}

test('tower1 replay: a research-loop stop after compaction gets the one answer turn, not an immediate abort', () => {
  const {guard, aborts, refused} = tower1Replay({finalTurn: 'answer'});
  assert.deepEqual(refused, {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION},
    'the refused call carries the instruction instead of WEB_LOOP_ABORT_REASON');
  assert.deepEqual(aborts, [], 'no abort before or during the answer turn');
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.startsWith(RESEARCH_ANSWER));
  assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.match(delivery.text, /web research allowance was used up/);
  assert.ok(!delivery.text.includes(RUN_PROGRESS_STOP_REASON) && !delivery.text.includes(WEB_LOOP_DELIVERY_REASON));
  assert.equal(guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: RESEARCH_ANSWER}}).payload.text,
    delivery.text);
  assert.equal(guard.continuationAllowed(context.runId), false);
});

test('tower1 replay: a tool call in the research-stop answer turn still aborts once, with the research stop text', () => {
  const {guard, aborts, refused, late} = tower1Replay({finalTurn: 'tool'});
  assert.equal(refused?.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  assert.deepEqual(late, {block: true, blockReason: RUN_PROGRESS_STOP_REASON});
  assert.deepEqual(aborts, [[context.sessionId, context.sessionKey]]);
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, WEB_LOOP_DELIVERY_REASON +
    readList([...Object.values(PAGES), 'https://www.tomshardware.com/pc-components/rx-9070-price']));
});

test('an ineligible run keeps the immediate research web-loop abort', () => {
  const {guard, aborts} = guardFixture("Inspect this computer's CPU health and search the web for its current driver version.");
  let n = 0;
  const search = () => {
    guard.observeModelCall({}, context);
    const id = `op-${++n}`, params = {query: `driver ${n}`}, ctx = {...context, toolName: 'web_search', toolCallId: id};
    const decision = guard.beforeToolCall({toolName: 'web_search', toolCallId: id, params}, ctx);
    if (!decision?.block) guard.afterToolCall({toolName: 'web_search', toolCallId: id, params,
      result: {content: [{type: 'text', text: `results ${n}`}], details: {status: 'ok'}}}, ctx);
    return decision;
  };
  for (let i = 0; i < DEFAULT_WEB_TOOL_LIMITS.search; i++) search();
  assert.equal(search()?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  assert.equal(search()?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  assert.equal(search()?.blockReason, WEB_LOOP_ABORT_REASON);
  assert.equal(aborts.length, 1);
});

test('without model hooks, an answer after the research-stop refusal is still delivered', () => {
  const aborts = [];
  const guard = createToolLoopGuard({abortRun: id => { aborts.push(id); return true; }, limits: {search: 1, fetch: 4, total: 5}});
  guard.observeRun(context, 'pixel', {prompt: RESEARCH_PROMPT});
  const search = q => guard.beforeToolCall({toolName: 'web_search', toolCallId: q, params: {query: q}}, {...context, toolName: 'web_search', toolCallId: q});
  assert.equal(search('a'), undefined);
  assert.equal(search('b')?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  assert.equal(search('c')?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  assert.equal(search('d')?.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith(RESEARCH_ANSWER));
  assert.match(delivery.text, /web research allowance was used up/);
  assert.deepEqual(aborts, []);
});

// Replays tower3 session 105e4915 (integration 64ff3d2a, 2026-09-25
// 10:29-10:31 UTC, Qwen3.5-27B). After the search allowance ran out, two
// parallel search refusals and a 404, a parallel [web_search, web_fetch] hit
// the research web-loop stop: both refusals carried the instruction and the
// model wrote its JSON answer. OpenClaw then ran a threshold auto-compaction
// for the same run; its summarization call (same runId, through the run's
// model stream) was counted as a further turn, forfeited the answer and was
// aborted, so the owner received only the research stop text.
function tower3Replay({compaction = 'after-finalize', compactionCalls = 1, hooks = true} = {}) {
  const {guard, aborts} = guardFixture();
  const sdk = {runId: context.runId, sessionId: context.sessionId, sessionKey: context.sessionKey};
  let n = 0, m = 0;
  const modelCall = () => { const callId = `${context.runId}:model:${++m}`; guard.observeModelCall({callId}, sdk); return callId; };
  const modelEnd = callId => guard.observeModelEnd({callId}, sdk);
  const turn = () => modelEnd(modelCall());
  const before = (toolName, params) => {
    const id = `t3-${++n}`, ctx = {...context, toolName, toolCallId: id};
    return {id, ctx, toolName, params, decision: guard.beforeToolCall({toolName, toolCallId: id, params}, ctx)};
  };
  const finish = ({id, ctx, toolName, params, decision}, outcome) => {
    const result = decision?.block
      ? {content: [{type: 'text', text: decision.blockReason}], details: {status: 'blocked', reason: decision.blockReason}}
      : outcome;
    const isError = decision?.block === true || result?.isError === true;
    guard.afterToolCall({toolName, toolCallId: id, params, result, ...(isError ? {error: result.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolCallId: id, message: {role: 'toolResult', toolName, toolCallId: id, isError, ...result}}, ctx);
    return decision;
  };
  const batch = calls => { const pending = calls.map(([name, params]) => before(name, params)); return pending.map((p, i) => finish(p, calls[i][2])); };
  const page = url => ({content: [{type: 'text', text: `Fetched ${url}`}], details: {status: 200, url, finalUrl: url, text: `Evidence from ${url}`}});
  const fail = code => ({isError: true, content: [{type: 'text', text: JSON.stringify({status: 'error', tool: 'web_fetch', error: `Web fetch failed (${code})`})}],
    details: {status: 'error'}});
  const found = q => ({content: [{type: 'text', text: JSON.stringify({query: q, results: []})}], details: {status: 'ok'}});
  const compact = () => {
    if (hooks) guard.observeCompaction({sessionKey: context.sessionKey}, 'start');
    const ids = Array.from({length: compactionCalls}, () => modelCall());
    ids.forEach(modelEnd);
    if (hooks) guard.observeCompaction({sessionKey: context.sessionKey}, 'end');
  };
  // Earlier rounds: pages read, duplicate/403 failures, and the search allowance used up.
  Object.values(PAGES).forEach((url, i) => { turn(); batch([['web_fetch', {url}, page(url)], ['web_fetch', {url: `${url}?dup=${i}`}, fail(403)]]); });
  for (let i = 0; i < DEFAULT_WEB_TOOL_LIMITS.search - 1; i++) { turn(); batch([['web_search', {query: `gpu ${i}`}, found(`gpu ${i}`)]]); }
  turn();
  const [, firstRefusal] = batch([['web_search', {query: 'RX 9070 price'}, found('x')], ['web_search', {query: 'RX 9070 specs'}, found('x')],
    ['web_fetch', {url: 'https://bottleneckpc.com/gpu/rtx-5070'}, page('https://bottleneckpc.com/gpu/rtx-5070')]]);
  assert.equal(firstRefusal?.blockReason, WEB_SEARCH_BUDGET_EXHAUSTED_REASON);
  turn();
  const second = batch([['web_fetch', {url: 'https://www.newegg.com/rx-9070-gre'}, page('https://www.newegg.com/rx-9070-gre')],
    ['web_search', {query: 'RX 9070 stock'}, found('x')], ['web_search', {query: 'RTX 5070 stock'}, found('x')]]);
  assert.deepEqual(second.slice(1).map(d => d?.blockReason), [WEB_SEARCH_BUDGET_EXHAUSTED_REASON, WEB_SEARCH_BUDGET_EXHAUSTED_REASON]);
  // "I have substantial data now. Let me compile the final JSON..." but another fetch: 404.
  turn(); batch([['web_fetch', {url: 'https://www.tech4gamers.com/reviews/gpus/rx-9070-vs-rtx-5070'}, fail(404)]]);
  // 10:29:37: parallel web_search + web_fetch after the allowance is gone.
  turn();
  const stop = batch([['web_search', {query: 'RTX 5070 vs RX 9070 1440p FPS'}, found('x')],
    ['web_fetch', {url: 'https://www.notebookcheck.net/RTX-5070-vs-RX-9070.html'}, page('x')]]);
  // 10:29:57: the single tool-free answer turn writes the JSON answer.
  const answerCall = modelCall();
  modelEnd(answerCall);
  if (compaction === 'before-finalize') compact();
  guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
  if (compaction === 'after-finalize') compact();
  return {guard, aborts, stop, turn};
}

test('tower3 replay: parallel refusals carry the instruction and a post-answer compaction keeps the answer', () => {
  for (const compaction of ['after-finalize', 'before-finalize']) {
    for (const compactionCalls of [1, 2]) {
      const label = `${compaction}/${compactionCalls} summarization call(s)`;
      const {guard, aborts, stop} = tower3Replay({compaction, compactionCalls});
      assert.deepEqual(stop.map(d => d?.blockReason), [PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_INSTRUCTION],
        `${label}: both parallel refusals carry the instruction`);
      assert.deepEqual(aborts, [], `${label}: the compaction is not aborted`);
      const delivery = guard.deliveryVerificationForRun(context.runId);
      assert.equal(delivery.status, 'failed', label);
      assert.ok(delivery.text.startsWith(RESEARCH_ANSWER), `${label}: the model's JSON answer is delivered`);
      assert.ok(delivery.text.includes(PROGRESS_FINALIZATION_NOTE), label);
      assert.match(delivery.text, /web research allowance was used up/, label);
      assert.ok(!delivery.text.includes(WEB_LOOP_DELIVERY_REASON), label);
    }
  }
});

test('tower3 replay without compaction hooks reproduces the lost answer', () => {
  const {guard, aborts} = tower3Replay({hooks: false});
  assert.equal(aborts.length, 1, 'the summarization call was treated as a further turn and aborted');
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, WEB_LOOP_DELIVERY_REASON +
    readList([...Object.values(PAGES), 'https://bottleneckpc.com/gpu/rtx-5070', 'https://www.newegg.com/rx-9070-gre']),
    'the answer is lost; only the page list remains');
});

test('the compaction window stays bounded: a further non-summarization call still forfeits the answer turn', () => {
  const {guard, aborts} = guardFixture();
  const sdk = {runId: context.runId, sessionId: context.sessionId, sessionKey: context.sessionKey};
  exhaustByFailures(guard);
  enterAnswerTurn(guard);
  guard.observeCompaction({sessionKey: context.sessionKey}, 'start');
  for (const callId of ['c1', 'c2', 'c3']) guard.observeModelCall({callId}, sdk);
  guard.observeModelEnd({callId: 'c3'}, sdk);
  assert.equal(aborts.length, 1, 'only two summarization calls are exempt');
  guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, RUN_PROGRESS_STOP_REASON);
  // A compaction of another session never opens a window on this run.
  const other = guardFixture();
  exhaustByFailures(other.guard);
  enterAnswerTurn(other.guard);
  other.guard.observeCompaction({sessionKey: 'agent:pixel:someone-else'}, 'start');
  other.guard.observeModelCall({callId: 'x1'}, sdk);
  other.guard.observeModelEnd({callId: 'x1'}, sdk);
  assert.equal(other.aborts.length, 1);
});

// Replays tower2 round 061 (main 17dfce8a, qwen3-coder-next, session
// b63ee849, 11:23 UTC), rows 244-298 in order. The model read the official
// NVIDIA and AMD pages, TechPowerUp, IGN and PassMark; the search allowance ran
// out (282), then a refusal (286), a duplicate-search recall (290, a free
// correction, not replayed) and a second refusal (294). With only six failures (one
// consecutive) the progress budget was not exhausted: the web-loop stop at 296
// refused with the finalization instruction. The model spent its single answer
// turn on another web_search (298), with no text, so the research stop text
// applies. Titles are those the transcript kept (some details were truncated
// when persisted); the overflow compaction at 265-266 is omitted.
const R061 = {
  vcb2: 'https://www.videocardbenchmark.net/compare/5940vs5958/GeForce-RTX-5070-vs-Radeon-RX-9070',
  nvidia: 'https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family',
  amd: 'https://www.amd.com/en/products/graphics/desktops/radeon/9000-series/amd-radeon-rx-9070.html',
  vcb4: 'https://www.videocardbenchmark.net/compare/5940vs5957vs5956vs5958/GeForce-RTX-5070-vs-Radeon-RX-9060-XT-16GB-vs-Radeon-RX-9070-XT-vs-Radeon-RX-9070',
  tpuSpecs: 'https://www.techpowerup.com/gpu-specs/geforce-rtx-5070.c4218',
  nvidiaGuide: 'https://www.nvidia.com/content/geforce-gtx/geforce-rtx-5070-user-guide-r1.pdf',
  ign: 'https://www.ign.com/articles/amd-radeon-rx-9070-benchmark',
  tpuDriver: 'https://www.techpowerup.com/338591/amd-radeon-rx-9070-xt-gains-9-performance-at-1440p-with-latest-driver-beats-rtx-5070-ti',
  techspot: 'https://www.techspot.com/specs/gpu/305059-amd-radeon-rx-9070.html',
};
const R061_TITLES = {
  vcb2: 'GeForce RTX 5070 vs Radeon RX 9070 [videocardbenchmark.net] by PassMark Software',
  vcb4: 'GeForce RTX 5070 vs Radeon RX 9060 XT 16GB vs Radeon RX 9070 XT vs Radeon RX 9070 [videocardbenchmark.net] by PassMark Software',
  tpuSpecs: 'NVIDIA GeForce RTX 5070 Specs | TechPowerUp GPU Database',
  tpuDriver: 'AMD Radeon RX 9070 XT Gains 9% Performance at 1440p with Latest Driver, Beats RTX 5070 Ti | TechPowerUp',
};
function round061Replay({finalTurn}) {
  const {guard, aborts} = guardFixture();
  const sdk = {runId: context.runId, sessionId: context.sessionId, sessionKey: context.sessionKey};
  let n = 0, m = 0;
  const call = (toolName, params, outcome) => {
    const callId = `${context.runId}:model:${++m}`;
    guard.observeModelCall({callId}, sdk);
    guard.observeModelEnd({callId}, sdk);
    const id = `r61-${++n}`, ctx = {...context, toolName, toolCallId: id};
    guard.observeAssistantMessage({message: {role: 'assistant', content: [{type: 'toolCall', id, name: toolName, arguments: params}]}},
      {agentId: 'pixel', sessionKey: context.sessionKey});
    const decision = guard.beforeToolCall({toolName, toolCallId: id, params}, ctx);
    const result = decision?.block
      ? {content: [{type: 'text', text: decision.blockReason}], details: {status: 'blocked', reason: decision.blockReason}}
      : outcome;
    const isError = decision?.block === true || result?.isError === true;
    guard.afterToolCall({toolName, toolCallId: id, params, result, ...(isError ? {error: result.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolCallId: id, message: {role: 'toolResult', toolName, toolCallId: id, isError, ...result}}, ctx);
    return decision;
  };
  const wrapped = title => `\n<<<EXTERNAL_UNTRUSTED_CONTENT id="r61">>>\nSource: Web Fetch\n---\n${title}\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="r61">>>`;
  const page = (url, {title, finalUrl = url} = {}) => ({content: [{type: 'text', text: `Fetched ${url}`}],
    details: {status: 200, url, finalUrl, text: `Evidence from ${finalUrl}`, ...(title ? {title: wrapped(title)} : {})}});
  const extracted = url => ({content: [{type: 'text', text: `Targeted evidence from ${url}`}],
    details: {boundary: 'public-web-read-only', matched: true, source_url: url}});
  const fail = error => ({isError: true, content: [{type: 'text', text: JSON.stringify({status: 'error', tool: 'web_fetch', error})}],
    details: {status: 'error'}});
  const found = query => ({content: [{type: 'text', text: JSON.stringify({query, results: []})}], details: {status: 'ok'}});
  const search = query => call('web_search', {query}, found(query));
  search('NVIDIA GeForce RTX 5070 vs AMD Radeon RX 9070 1440p benchmark review');                  // 244
  call('web_fetch', {url: R061.techspot}, fail('Web fetch failed (403)'));                          // 246
  call('web_fetch', {url: R061.vcb2}, page(R061.vcb2, {title: R061_TITLES.vcb2}));                  // 248
  search('NVIDIA GeForce RTX 5070 official specifications NVIDIA website');                        // 250
  call('web_fetch', {url: R061.nvidia}, page(R061.nvidia));                                         // 252
  call('pixel_ods_web_extract', {url: `${R061.nvidia}/`, query: 'RTX 5070 specifications VRAM power TDP'}, extracted(`${R061.nvidia}/`)); // 254
  search('AMD Radeon RX 9070 official specifications AMD website');                                 // 256
  call('web_fetch', {url: R061.amd}, page(R061.amd));                                               // 258
  call('web_fetch', {url: R061.vcb2}, page(R061.vcb2));                                             // 260 repeated page: refused
  call('pixel_ods_web_extract', {url: R061.vcb2, query: 'RTX 5070 RX 9070 1440p benchmark FPS'}, extracted(R061.vcb2)); // 262
  search('NVIDIA RTX 5070 VS AMD RX 9070 benchmark 1440p comparison site:videocardbenchmark.net');  // 264
  const vcb4Bare = R061.vcb4.replace('://www.', '://');
  call('web_fetch', {url: vcb4Bare}, page(vcb4Bare, {title: R061_TITLES.vcb4, finalUrl: R061.vcb4})); // 268
  search('NVIDIA RTX 5070 vs AMD RX 9070 1440p benchmark comparison site:techpowerup.com');         // 270
  call('web_fetch', {url: R061.tpuSpecs}, page(R061.tpuSpecs, {title: R061_TITLES.tpuSpecs}));     // 272
  search('"GeForce RTX 5070" board power TDP watts official NVIDIA documentation');                 // 274
  call('web_fetch', {url: R061.nvidiaGuide}, page(R061.nvidiaGuide));                               // 276
  search('AMD Radeon RX 9070 official specifications TDP power consumption AMD website');           // 278
  call('web_fetch', {url: R061.techspot}, fail('Web fetch failed (403)'));                          // 280 repeated page: refused
  search('NVIDIA RTX 5070 vs AMD RX 9070 1440p benchmark comparison site:ign.com');                 // 282, the last search
  call('web_fetch', {url: R061.ign}, page(R061.ign));                                               // 284
  assert.equal(search('"RTX 5070" "RX 9070" 1440p benchmark comparison side-by-side site:videocardbenchmark.net')?.blockReason,
    WEB_SEARCH_BUDGET_EXHAUSTED_REASON);                                                            // 286
  call('web_fetch', {url: R061.vcb4}, page(R061.vcb4, {title: R061_TITLES.vcb4}));                  // 288
  // 290, a duplicate-search recall, is a free correction that does not advance
  // the allowance terminal; it needs a bound native search receipt, so it is
  // omitted here.
  call('web_fetch', {url: R061.tpuDriver}, page(R061.tpuDriver, {title: R061_TITLES.tpuDriver}));  // 292
  assert.equal(search('RTX 5070 RX 9070 1440p benchmark comparison site:gpuuser.com')?.blockReason,
    WEB_SEARCH_BUDGET_EXHAUSTED_REASON);                                                            // 294
  const stop = search('RTX 5070 vs RX 9070 1440p gaming benchmark 2026 site:game-debate.com');      // 296
  let late;
  if (finalTurn === 'tool') late = search('RTX 5070 vs RX 9070 1440p benchmark site:videocardbenchmark.net'); // 298
  else {
    const callId = `${context.runId}:model:${++m}`;
    guard.observeModelCall({callId}, sdk);
    guard.observeModelEnd({callId}, sdk);
    guard.beforeAgentFinalize({lastAssistantMessage: RESEARCH_ANSWER}, context);
  }
  return {guard, aborts, stop, late};
}

test('round 061 replay: the research stop grants the answer turn, and a tool call in it falls back', () => {
  const {guard, aborts, stop, late} = round061Replay({finalTurn: 'tool'});
  assert.deepEqual(stop, {block: true, blockReason: PROGRESS_FINALIZATION_INSTRUCTION}, 'the stop carries the instruction');
  assert.deepEqual(late, {block: true, blockReason: RUN_PROGRESS_STOP_REASON});
  assert.deepEqual(aborts, [[context.sessionId, context.sessionKey]]);
  // What round 061 delivered, now followed by the host's list of the eight
  // distinct pages read (repeated reads of one page count once).
  const plain = title => title.replace(/[[\]]/g, ' ').replace(/\s+/g, ' ');
  assert.equal(guard.deliveryVerificationForRun(context.runId).text, WEB_LOOP_DELIVERY_REASON +
    `\n\n${PROGRESS_READ_PAGES_HEADING}\n\n` + [
      `- ${plain(R061_TITLES.vcb2)} — <${R061.vcb2}>`,
      `- <${R061.nvidia}>`,
      `- <${R061.amd}>`,
      `- ${plain(R061_TITLES.vcb4)} — <${R061.vcb4}>`,
      `- ${R061_TITLES.tpuSpecs} — <${R061.tpuSpecs}>`,
      `- <${R061.nvidiaGuide}>`,
      `- <${R061.ign}>`,
      `- ${R061_TITLES.tpuDriver} — <${R061.tpuDriver}>`,
    ].join('\n'));
});

test('round 061 replay: an answer in that turn is delivered from the evidence already read', () => {
  const {guard, aborts, stop} = round061Replay({finalTurn: 'answer'});
  assert.equal(stop?.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  const delivery = guard.deliveryVerificationForRun(context.runId);
  assert.ok(delivery.text.startsWith(RESEARCH_ANSWER));
  assert.match(delivery.text, /web research allowance was used up/);
  assert.deepEqual(aborts, []);
});

test('the instruction is fixed text, identical for every run and tool', () => {
  const seen = new Set();
  for (const [runId, toolName] of [['a', 'web_fetch'], ['b', 'exec'], ['c', 'write']]) {
    const guard = createToolLoopGuard({abortRun: () => true});
    const ctx = {...context, runId, sessionId: `s-${runId}`};
    guard.observeRun(ctx, 'pixel', {prompt: `Task ${runId} at ${new Date().toISOString()}`});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) guard.toolResultPersist({toolCallId: `${runId}-${i}`,
      message: {role: 'toolResult', toolName, toolCallId: `${runId}-${i}`, isError: true, content: [{type: 'text', text: `error ${i}`}]}},
    {...ctx, toolName, toolCallId: `${runId}-${i}`});
    guard.observeModelCall({}, ctx);
    seen.add(guard.beforeToolCall({toolName, toolCallId: `${runId}-next`, params: {}}, {...ctx, toolName})?.blockReason);
  }
  assert.deepEqual([...seen], [PROGRESS_FINALIZATION_INSTRUCTION]);
  assert.doesNotMatch(PROGRESS_FINALIZATION_INSTRUCTION, /\d/);
  assert.ok(PROGRESS_FINALIZATION_INSTRUCTION.length < 700, 'small prompt addition');
});
