import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND, PHANTOM_PROCESS_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {composeProgressFinalization, createProgressFinalization, progressFinalizationAnswer,
  PROGRESS_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_NOTE} from '../plugin/progress-finalization.mjs';

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
  assert.equal(finalization.toolBoundary(), 'instruct');
  assert.equal(finalization.toolBoundary(), 'instruct', 'parallel sibling calls carry the same instruction');
  assert.equal(finalization.modelCallStarted(), 'turn');
  assert.equal(finalization.abortDeferred, true);
  const answer = 'The RTX 5070 has 12 GB of VRAM according to the NVIDIA page.';
  assert.equal(finalization.accept(answer), answer);
  assert.equal(finalization.phase, 'answered');
  assert.equal(finalization.modelCallStarted(), 'failed', 'any further model call forfeits the answer');
  assert.equal(finalization.answer, undefined);
  assert.equal(finalization.accept('A second answer is never accepted after the turn was spent.'), undefined);

  const toolInTurn = createProgressFinalization();
  toolInTurn.arm(true); toolInTurn.toolBoundary(); toolInTurn.modelCallStarted();
  assert.equal(toolInTurn.toolBoundary(), 'stop');
  assert.equal(toolInTurn.phase, 'failed');
  assert.equal(toolInTurn.abortDeferred, false);

  const ineligible = createProgressFinalization();
  assert.equal(ineligible.arm(false), 'unavailable');
  assert.equal(ineligible.toolBoundary(), 'stop');
  assert.equal(ineligible.abortDeferred, false);

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
  assert.doesNotMatch(delivery.text ?? '', /tool limit|stopped after repeated/);
  assert.deepEqual(aborts, []);
  const reply = guard.replyPayloadSending({runId: context.runId, kind: 'final', payload: {text: RESEARCH_ANSWER}});
  assert.doesNotMatch(reply?.payload?.text ?? RESEARCH_ANSWER, /tool limit|stopped after repeated/);
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
