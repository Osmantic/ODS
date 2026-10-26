// RTX Spark, 2026-10-25, "Make me a simple stopwatch web page with start, stop
// and lap buttons" (seen twice): the model wrote the page and published it, and
// the preview card worked. Its next three preview checks failed on its own plan
// mistakes ("Step 4 matched the element but did not observe its requested
// visibility", "Step 7 (assert-text) matched 2 elements", then a generic
// failure), which used up the tool budget. Every later call was refused, and
// the owner read under the working card: "This response was stopped after
// repeated tool failures or attempts without progress. ... The full request was
// not completed ... This is the last verified publication, not proof that all
// requested work completed."
//
// The model never saw the instruction to answer: on ODS's runtime the result
// persisted for a call is what the next model request carries, and every result
// after the stop, the refusal that carried the instruction included, was
// persisted as the stop text, which says to continue "with a corrected
// approach". Now those results carry the instruction, which tells the model to
// stop checking a published page and say what it built and could not verify.
// The owner reads that answer, if any, one plain line and the preview card.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, PROGRESS_STOP_HELP, PROGRESS_STOPPED_DELIVERY_PREFIX,
  WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, createWorkspacePreviewInspectTool, inspectionPlanHash} from '../plugin/workspace-preview-inspect.mjs';
import {PREVIEW_STOPPED_NOTE, PREVIEW_UNFINISHED_NOTE} from '../plugin/preview-explanation.mjs';
import {PREVIEW_CHECK_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_INSTRUCTION,
  PROGRESS_FINALIZATION_NOTE} from '../plugin/progress-finalization.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

const USER = 'ods-' + '5f0'.repeat(21) + 'a';
const runId = 'chatcmpl_5f0a7c11-2d3e-4f50-8a61-7b8c9d0e1f2a';
const context = {agentId: 'pixel', runId, sessionId: 'stopwatch-session', sessionKey: `agent:pixel:openai-user:${USER}`};
const PROMPT = 'Make me a simple stopwatch web page with start, stop and lap buttons';
const PAGE = '<!doctype html><title>Stopwatch</title><div id="display">00:00.0</div>' +
  '<button id="start">Start</button><button id="stop" disabled>Stop</button><button id="lap" disabled>Lap</button>' +
  '<ol id="laps"></ol><script src="app.js"></script>';
const ANSWER = 'Your stopwatch page is ready: it shows the running time with Start, Stop and Lap buttons, and Lap adds ' +
  'the current time to a numbered list. I could not verify automatically that Stop and Lap respond, because my own ' +
  'checks of those buttons kept failing, so please try them.';
// What an owner must not read after a build that published a working preview.
const ALARM = /stopped after|tool failures|without progress|not completed|not proof|tool limit|verified publication|corrected approach/i;

const shown = {count: 1, visible: true, display: 'block', visibility: 'visible', opacity: '1', hidden: false,
  hiddenUntilFound: false, rectCount: 1};
// A capsule receipt whose steps passed up to `failAt`, where `failure` failed.
function failedReceipt(request, failAt, failure) {
  const steps = request.steps.slice(0, failAt + 1).map((step, index) => {
    const base = {index, action: step.action, locator: step.locator, stable: true,
      ...(step.action === 'assert-text' ? {expectedText: step.expectedText} : {})};
    if (index === failAt) return {...base, ...failure, status: 'failed'};
    return {...base, status: 'passed', ...(step.action === 'click' ? {after: shown} : {}),
      before: step.action === 'assert-text' ? {...shown, text: {actual: step.expectedText, truncated: false}} : shown};
  });
  return {schemaVersion: 1, kind: INSPECTION_KIND, status: 'failed', siteId: request.siteId, sha256: request.sha256,
    planSha256: inspectionPlanHash(request), viewport: request.viewport, steps,
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE};
}
const button = name => ({role: 'button', name, exact: true});
// The three checks of the Spark run, each failing on the model's own plan.
const CHECKS = [
  {steps: [{action: 'assert-visible', locator: {selector: '#display'}}, {action: 'click', locator: button('Start')},
    {action: 'click', locator: button('Stop')}, {action: 'assert-hidden', locator: button('Stop')}],
  failAt: 3, failure: {before: shown, errorCode: 'visibility_mismatch'},
  says: /^Preview inspection failed\. Step 4 matched the element but did not observe its requested visibility\./},
  {steps: [{action: 'assert-visible', locator: {selector: '#display'}}, {action: 'click', locator: button('Start')},
    {action: 'click', locator: button('Lap')}, {action: 'click', locator: button('Lap')}, {action: 'click', locator: button('Stop')},
    {action: 'assert-visible', locator: {selector: '#laps'}}, {action: 'assert-text', locator: {selector: '#laps li'}, expectedText: 'Lap 1'}],
  failAt: 6, failure: {before: {count: 2}, errorCode: 'selector_not_unique'},
  says: /Step 7 \(assert-text\) matched 2 elements; a locator must match exactly one/},
  {steps: [{action: 'click', locator: button('Start')}, {action: 'assert-text', locator: {selector: '#display'}, expectedText: '00:00.1'}],
  failAt: 0, failure: {before: shown, errorCode: 'click_failed'},
  says: /^Preview inspection failed\. Requested behavior remains unverified; a failed inspection does not establish/},
];

// One stopwatch turn through the real guard and inspection tool: write and
// publish the page, then a failed read and the three failed checks, so that the
// third check is the one that uses up the budget, as on the Spark. `persist`
// replays each result as the runtime persists it and returns the text the next
// model request carries.
async function stopwatchTurn({published = true, edited = false, checks = published && !edited} = {}) {
  const aborts = [];
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true,
    abortRun: (sessionId) => { aborts.push(sessionId); return true; }});
  guard.observeRun({...context, trigger: 'user'}, 'pixel', {prompt: PROMPT});
  let sequence = 0;
  const persisted = [];
  const persist = (toolName, toolCallId, result) => {
    const message = {role: 'toolResult', toolName, toolCallId, isError: result?.isError === true, content: result?.content ?? []};
    const decision = guard.toolResultPersist({toolCallId, message}, {...context, toolName, toolCallId});
    persisted.push((decision?.message ?? message).content.map(block => block.text).join('\n'));
    return persisted.at(-1);
  };
  const round = () => { guard.observeModelEnd({}, context); guard.observeModelCall({}, context); };
  const call = (toolName, params) => {
    const ctx = {...context, toolName, toolCallId: `call-${sequence++}`};
    const decision = guard.beforeToolCall({toolName, runId, toolCallId: ctx.toolCallId, params}, ctx);
    return {ctx, decision, params: decision?.params ?? params};
  };
  const run = (toolName, params, result) => {
    const {ctx, decision, params: actual} = call(toolName, params);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    guard.afterToolCall({toolName, runId, toolCallId: ctx.toolCallId, params: actual, result,
      ...(result.isError ? {error: result.content?.[0]?.text ?? 'failed'} : {})}, ctx);
    persist(toolName, ctx.toolCallId, result);
    return actual;
  };
  guard.observeModelCall({}, context);
  const written = run('write', {path: 'stopwatch/index.html', content: PAGE}, {content: [{type: 'text', text: 'Successfully wrote file.'}]});
  const relativeDirectory = written.path.slice(0, -'/index.html'.length);
  let preview;
  if (published) {
    round();
    const sha256 = createHash('sha256').update(relativeDirectory + PAGE).digest('hex'), siteId = `site-${sha256.slice(0, 24)}`;
    preview = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory, siteId, sha256,
      entryFile: 'index.html', entrySha256: createHash('sha256').update(PAGE).digest('hex'), files: 2, bytes: 2048, port: 9437,
      url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
    run('pixel_ods_workspace_preview', {relativeDirectory}, {details: preview});
  }
  if (edited) {
    round();
    run('edit', {path: 'stopwatch/index.html', edits: [{oldText: '00:00.0', newText: '00:00.00'}]},
      {content: [{type: 'text', text: 'Successfully replaced text.'}]});
  }
  round();
  run('read', {path: 'stopwatch/style.css'}, {isError: true, content: [{type: 'text', text: 'ENOENT: no such file or directory'}]});
  const inspections = [];
  for (const check of checks ? CHECKS : []) {
    round();
    const params = {siteId: preview.siteId, sha256: preview.sha256, viewport: {width: 1280, height: 800}, steps: check.steps};
    const tool = createWorkspacePreviewInspectTool({request: async request => failedReceipt(request, check.failAt, check.failure)});
    const {ctx, decision, params: prepared} = call(PREVIEW_INSPECTION_TOOL, params);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    const result = await tool.execute(ctx.toolCallId, prepared);
    assert.equal(result.isError, true);
    assert.match(result.content[0].text, check.says);
    guard.afterToolCall({toolName: PREVIEW_INSPECTION_TOOL, runId, toolCallId: ctx.toolCallId, params: prepared, result,
      error: result.content[0].text}, ctx);
    inspections.push({result, persisted: persist(PREVIEW_INSPECTION_TOOL, ctx.toolCallId, result)});
  }
  return {guard, preview, inspections, persisted, aborts, round, call, persist};
}

const receiptOf = guard => {
  const recorded = guard.verificationForRun(runId).text;
  return recorded.slice(recorded.indexOf('Your preview is ready.'));
};

test('Spark stopwatch: the check that stops the run tells the model to stop checking and wrap up', async () => {
  const {guard, inspections, persisted, call, persist, round, aborts} = await stopwatchTurn();
  // The first two checks reach the model unchanged; they did not stop the run.
  assert.deepEqual(inspections.slice(0, 2).map(item => item.persisted), inspections.slice(0, 2).map(item => item.result.content[0].text));
  // The third one stopped it: its persisted result, which the next model request
  // carries, is the wrap-up instruction instead of the stop text.
  assert.equal(inspections[2].persisted, PREVIEW_CHECK_FINALIZATION_INSTRUCTION);
  assert.ok(!persisted.includes(RUN_PROGRESS_STOP_REASON), 'the model never reads "continue with a corrected approach"');
  assert.match(PREVIEW_CHECK_FINALIZATION_INSTRUCTION, /^\[ODS Pixel tool limit\] Stop checking the preview now: tools are disabled/);
  assert.match(PREVIEW_CHECK_FINALIZATION_INSTRUCTION, /say plainly which requested behavior you could not verify/);
  // The Spark model's next call: refused with the same instruction, persisted as it.
  round();
  const listing = call('exec', {command: 'ls -la stopwatch && head -5 stopwatch/index.html'});
  assert.deepEqual(listing.decision, {block: true, blockReason: PREVIEW_CHECK_FINALIZATION_INSTRUCTION});
  assert.equal(persist('exec', listing.ctx.toolCallId, {isError: true, content: [{type: 'text', text: listing.decision.blockReason}]}),
    PREVIEW_CHECK_FINALIZATION_INSTRUCTION);
  // A tool call in the answer turn still ends the run at that boundary.
  round();
  const again = call('read', {path: 'stopwatch/app.js'});
  assert.deepEqual(again.decision, {block: true, blockReason: RUN_PROGRESS_STOP_REASON});
  assert.equal(persist('read', again.ctx.toolCallId, {isError: true, content: [{type: 'text', text: RUN_PROGRESS_STOP_REASON}]}),
    RUN_PROGRESS_STOP_REASON, 'once the answer turn is spent, results carry the stop text');
  assert.equal(aborts.length, 1);
});

test('Spark stopwatch: without an answer the owner reads one plain line and the preview card', async () => {
  const {guard, preview, round, call} = await stopwatchTurn();
  round();
  call('exec', {command: 'ls -la stopwatch'});
  round();
  call('read', {path: 'stopwatch/app.js'});
  guard.beforeAgentFinalize({lastAssistantMessage: 'Let me check the files again.'}, context);
  const delivery = guard.deliveryVerificationForRun(runId);
  assert.equal(delivery.status, 'failed', 'Portal certifies nothing');
  assert.equal(delivery.text, `${PREVIEW_UNFINISHED_NOTE}\n\n${receiptOf(guard)}`);
  assert.ok(delivery.text.includes(`\n\nYour preview is ready.\n\n[Open preview](${preview.url})\n\n`), 'the receipt becomes the card');
  assert.doesNotMatch(delivery.text, ALARM);
  assert.equal(delivery.preview.siteId, preview.siteId, 'the preview card stays');
  // The record keeps every check fact, as the model read them.
  assert.match(guard.verificationForRun(runId).text, /^The attempted preview interaction checks have not passed browser inspection\./);
  assert.equal(guard.replyPayloadSending({runId, kind: 'final', payload: {text: 'x'}}).payload.text, delivery.text);
});

test('Spark stopwatch: the model\'s own wrap-up comes first, then Portal\'s line and the card', async () => {
  const {guard, preview, round} = await stopwatchTurn();
  round();
  guard.beforeAgentFinalize({lastAssistantMessage: ANSWER, messages: [{role: 'user', content: PROMPT},
    {role: 'assistant', content: [{type: 'text', text: ANSWER}], stopReason: 'stop'}]}, context);
  const delivery = guard.deliveryVerificationForRun(runId);
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.text, `${ANSWER}\n\n${PREVIEW_UNFINISHED_NOTE}\n\n${receiptOf(guard)}`);
  assert.doesNotMatch(delivery.text.slice(ANSWER.length), ALARM);
  assert.ok(!delivery.text.includes(PROGRESS_FINALIZATION_NOTE));
  assert.equal(delivery.preview.sha256, preview.sha256);
});

test('a stopped build whose files changed since publication links the earlier preview with its plain line', async () => {
  const {guard, preview, round, call} = await stopwatchTurn({edited: true});
  // Without a current publication, failed reads alone stop it: the generic instruction applies.
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
    round();
    const {ctx} = call('read', {path: `stopwatch/missing-${i}.js`});
    guard.afterToolCall({toolName: 'read', runId, toolCallId: ctx.toolCallId, params: {path: `stopwatch/missing-${i}.js`},
      result: {isError: true, content: [{type: 'text', text: 'ENOENT'}]}, error: 'ENOENT'}, ctx);
  }
  round();
  assert.equal(call('exec', {command: 'ls'}).decision.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  round();
  call('read', {path: 'stopwatch/app.js'});
  const delivery = guard.deliveryVerificationForRun(runId);
  assert.equal(delivery.text, `${PREVIEW_STOPPED_NOTE}\n\n[Open last published preview](${preview.url})`);
  assert.doesNotMatch(delivery.text, ALARM);
  assert.equal(delivery.preview.sha256, preview.sha256);
});

test('a stopped build with no preview says so plainly, with what there is, the next step and where to get help', async () => {
  const {guard, round, call} = await stopwatchTurn({published: false});
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
    round();
    const {ctx} = call('exec', {command: `npx serve stopwatch --port ${3000 + i}`});
    guard.afterToolCall({toolName: 'exec', runId, toolCallId: ctx.toolCallId, params: {command: 'npx serve'},
      result: {isError: true, details: {status: 'completed', exitCode: 127}, content: [{type: 'text', text: 'npx: not found'}]},
      error: 'npx: not found'}, ctx);
  }
  round();
  call('exec', {command: 'ls'});
  round();
  call('read', {path: 'stopwatch/app.js'});
  const delivery = guard.deliveryVerificationForRun(runId);
  assert.equal(delivery.status, 'failed');
  assert.equal(delivery.preview, undefined);
  assert.equal(delivery.text, `${PROGRESS_STOPPED_DELIVERY_PREFIX}\n\n${WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX}\n\n${PROGRESS_STOP_HELP}`);
  assert.doesNotMatch(delivery.text, /localhost|tool failures|corrected approach/);
});

test('the wrap-up never lets another tool run, and the budget is unchanged', async () => {
  assert.deepEqual(RUN_PROGRESS_LIMITS, {consecutiveFailures: 4, totalFailures: 12, roundsWithoutProgress: 8, identicalSuccesses: 2});
  const {round, call} = await stopwatchTurn();
  const decisions = [];
  for (const [toolName, params] of [['exec', {command: 'ls'}], ['pixel_ods_workspace_preview_inspect', {siteId: 'x'}],
    ['write', {path: 'stopwatch/app.js', content: 'x'}], ['read', {path: 'stopwatch/app.js'}]]) {
    round();
    decisions.push(call(toolName, params).decision);
  }
  assert.ok(decisions.every(decision => decision?.block === true), 'every call after the stop is refused');
  assert.deepEqual(decisions.map(decision => decision.blockReason), [PREVIEW_CHECK_FINALIZATION_INSTRUCTION,
    RUN_PROGRESS_STOP_REASON, RUN_PROGRESS_STOP_REASON, RUN_PROGRESS_STOP_REASON]);
});
