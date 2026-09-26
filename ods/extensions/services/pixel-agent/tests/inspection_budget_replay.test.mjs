// Replays the recorded website runs that the run-progress budget stopped on
// merged main d4a61f33 (fleet rounds 106/107), through the real guard hooks:
// - strixy round 107 (Tool Search): its first inspection (#8) was rejected
//   before execution with "Call tool_describe ... then retry"; it then called
//   tool_call {id:"tool_describe"} twice (unknown id), and its corrected
//   inspection (#11) was rejected again and became the fourth consecutive
//   failure. The response stopped before the model's next attempt (#12).
// - mac-mini round 106 (direct calls): four inspections whose final locator
//   matched no element or was not valid CSS; the fourth (call 12) stopped it.
// - windows-laptop round 107 (Tool Search): 12 failures in 40 calls with at
//   most 2 in a row; call 40 (transcript entry 86) reached the total cap.
// A failure that measured nothing, whose result already hands the model its
// correction, now waits one result for its corrected attempt; nothing else
// changes. Both after_tool_call/tool_result_persist orders are replayed.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL, correctableInspectionFailure} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, createWorkspacePreviewInspectTool, inspectionPlanHash}
  from '../plugin/workspace-preview-inspect.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

const FIXTURE = JSON.parse(fs.readFileSync(new URL('./inspection-budget-replay-round106-107.json', import.meta.url), 'utf8'));
const session = label => FIXTURE.sessions.find(item => item.label === label);
const STRIXY = session('strixy-r107');
const MAC = session('mac-mini-r106');
const LAPTOP = session('windows-laptop-wsl-beta-r107');

const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});
const recorded = call => ({content: [{type: 'text', text: call.text ?? `(recorded ${call.selected ?? call.tool} result)`}],
  ...(call.result.details ? {details: call.result.details} : {}), ...(call.result.isError ? {isError: true} : {})});

// No broker exists here: arguments the tool rejects never reach one, and any
// valid plan would come back "unavailable" rather than a recorded receipt.
const offline = createWorkspacePreviewInspectTool({request: async () => { throw Error('no broker in replay'); }});

// A passing capsule receipt for a show/hide plan on the recorded strixy page.
const shown = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible',
  opacity: '1', hidden: !visible, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
const passing = createWorkspacePreviewInspectTool({request: async request => ({schemaVersion: 1, kind: INSPECTION_KIND,
  status: 'passed', siteId: request.siteId, sha256: request.sha256, planSha256: inspectionPlanHash(request),
  viewport: request.viewport, steps: request.steps.map((step, index) => ({index, ...step,
    before: shown(step.action === 'click' || step.action === 'assert-visible'),
    ...(step.action === 'click' ? {after: shown(true)} : {}), stable: true, status: 'passed'})),
  diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE})});

// Drives the recorded calls through the guard hooks (before_tool_call only for
// inspections, which bind the pending run). Returns the call number after
// which the budget stopped the run (or undefined), the correctable results,
// and the guard to continue the same run.
async function replay(recording, {persistFirst = false, calls = recording.calls} = {}) {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  const context = {agentId: 'pixel', sessionId: recording.sessionId, sessionKey: recording.sessionKey, runId: recording.runId};
  guard.observeRun(context, 'pixel', {prompt: recording.prompt});
  let round, probes = 0;
  // A successful persisted receipt of no tool is never charged; the guard
  // replaces it with the stop reason once the run's budget has stopped it.
  const exhausted = () => {
    const id = `budget-probe-${++probes}`;
    return guard.toolResultPersist({toolName: 'budget_probe', toolCallId: id, message: {role: 'toolResult',
      toolName: 'budget_probe', toolCallId: id, content: [{type: 'text', text: 'probe'}]}},
    {...context, toolName: 'budget_probe', toolCallId: id})?.message?.content?.[0]?.text === RUN_PROGRESS_STOP_REASON;
  };
  const deliver = (call, result, persistedIsError) => {
    const ctx = {...context, toolName: call.tool, toolCallId: call.id};
    // Other calls keep their argument digest: identical successes stay identical.
    const args = call.args ?? {argsSha256: call.argsSha256};
    const params = call.tool === 'tool_call' ? {id: call.selected, args} : args;
    if (call.selected === PREVIEW_INSPECTION_TOOL || call.tool === PREVIEW_INSPECTION_TOOL) {
      const prepared = guard.beforeToolCall({toolName: call.tool, runId: context.runId, toolCallId: call.id, params}, ctx);
      assert.notEqual(prepared?.block, true, `${recording.label} #${call.n} ${prepared?.blockReason}`);
    }
    const after = () => guard.afterToolCall({toolName: call.tool, runId: context.runId, toolCallId: call.id, params, result}, ctx);
    const persist = () => guard.toolResultPersist({toolName: call.tool, toolCallId: call.id, message: {role: 'toolResult',
      toolName: call.tool, toolCallId: call.id, content: result.content, ...(result.details ? {details: result.details} : {}),
      isError: persistedIsError}}, ctx);
    if (persistFirst) { persist(); after(); } else { after(); persist(); }
  };
  const correctable = [];
  const run = async call => {
    if (call.round !== round) { round = call.round; guard.observeModelCall({callId: `model-${round}`}, context); }
    let result;
    let persistedIsError = call.persistedIsError;
    if (call.stopped) {
      // Never executed on main: only a model's next inspection can be run here.
      assert.ok(call.selected === PREVIEW_INSPECTION_TOOL || call.tool === PREVIEW_INSPECTION_TOOL, `${recording.label} #${call.n}`);
      const inner = await offline.execute(call.id, call.args);
      assert.equal(inner.details.errorCode, 'invalid_request', 'deterministic without a broker');
      result = call.tool === 'tool_call' ? envelope(PREVIEW_INSPECTION_TOOL, inner) : inner;
      persistedIsError = call.tool !== 'tool_call';
    } else result = call.envelope ? envelope(call.selected, recorded(call)) : recorded(call);
    const inner = call.envelope || (call.stopped && call.tool === 'tool_call') ? result.details.result : result;
    if ((call.selected ?? call.tool) === PREVIEW_INSPECTION_TOOL && correctableInspectionFailure(inner)) correctable.push(call.n);
    deliver(call, result, persistedIsError);
  };
  for (const call of calls) {
    await run(call);
    if (exhausted()) return {terminalAt: call.n, correctable, guard, context, exhausted};
  }
  return {terminalAt: undefined, correctable, guard, context, exhausted};
}

for (const persistFirst of [false, true]) {
  const order = persistFirst ? 'tool_result_persist before after_tool_call' : 'after_tool_call before tool_result_persist';

  test(`strixy round 107: the corrected attempt after the rejected inspection gets exactly one more call (${order})`, async () => {
    const replayed = await replay(STRIXY, {persistFirst});
    // Main stopped on #11, the second rejection; the model's next attempt (#12)
    // was never executed. Its arguments are rejected again, so the run now
    // stops there: one call later, on the unchanged consecutive fuse.
    assert.equal(replayed.terminalAt, 12);
    assert.deepEqual(replayed.correctable, [8, 11, 12]);
    assert.equal(STRIXY.calls.find(call => call.n === 12).stopped, true);
    const after = await replay(STRIXY, {persistFirst, calls: STRIXY.calls.slice(0, 11)});
    assert.equal(after.terminalAt, undefined, 'the second rejection no longer ends the run before its correction');
  });

  test(`mac-mini round 106: four unmeasured locators still stop the run at call 12 (${order})`, async () => {
    const replayed = await replay(MAC, {persistFirst});
    assert.equal(replayed.terminalAt, 12);
    assert.deepEqual(replayed.correctable, [9, 10, 11, 12]);
  });

  test(`windows-laptop round 107: the total failure cap still stops the run at call 40, entry 86 (${order})`, async () => {
    const replayed = await replay(LAPTOP, {persistFirst});
    assert.equal(replayed.terminalAt, 40);
    assert.equal(LAPTOP.calls.find(call => call.n === 40).entry, 86);
    const failures = LAPTOP.calls.filter(call => call.n <= 40 && (call.result?.isError || call.persistedIsError));
    assert.equal(failures.length, RUN_PROGRESS_LIMITS.totalFailures, 'every failure, correctable or not, counts toward the total');
    // visibility_mismatch receipts (#26, #33) are never deferred.
    assert.deepEqual(replayed.correctable, [4, 5, 9, 10, 13, 14, 24, 40]);
  });

  // After #8 (the first rejection) and after #11 (the rejection main stopped on).
  for (const last of [8, 11]) test(`counterfactual strixy: a passing corrected call after #${last} resets the consecutive count (${order})`, async () => {
    const replayed = await replay(STRIXY, {persistFirst, calls: STRIXY.calls.filter(call => call.n <= last)});
    assert.equal(replayed.terminalAt, undefined);
    const {guard, context, exhausted} = replayed;
    const rejected = STRIXY.calls.find(call => call.n === 8);
    const args = {...rejected.args, steps: [{action: 'assert-hidden', locator: {selector: '#midnight-card'}},
      {action: 'click', locator: {role: 'button', name: 'Show sold out', exact: true}},
      {action: 'assert-visible', locator: {selector: '#midnight-card'}}]};
    const id = 'corrected-inspection';
    const ctx = {...context, toolName: 'tool_call', toolCallId: id};
    const params = {id: PREVIEW_INSPECTION_TOOL, args};
    assert.notEqual(guard.beforeToolCall({toolName: 'tool_call', runId: context.runId, toolCallId: id, params}, ctx)?.block, true);
    const inner = await passing.execute(id, args);
    assert.equal(inner.details.status, 'passed');
    const result = envelope(PREVIEW_INSPECTION_TOOL, inner);
    const message = {role: 'toolResult', toolName: 'tool_call', toolCallId: id, ...result, isError: false};
    const after = () => guard.afterToolCall({toolName: 'tool_call', runId: context.runId, toolCallId: id, params, result}, ctx);
    const persist = () => guard.toolResultPersist({toolName: 'tool_call', toolCallId: id, message}, ctx);
    if (persistFirst) { persist(); after(); } else { after(); persist(); }
    assert.equal(exhausted(), false);
    // Consecutive count 0: three more ordinary failures are allowed, the fourth stops.
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      const failId = `failed-read-${i}`;
      guard.toolResultPersist({toolName: 'read', toolCallId: failId, message: {role: 'toolResult', toolName: 'read',
        toolCallId: failId, content: [{type: 'text', text: 'ENOENT'}], isError: true}}, {...context, toolName: 'read', toolCallId: failId});
      assert.equal(exhausted(), i === RUN_PROGRESS_LIMITS.consecutiveFailures - 1, `failure ${i + 1}`);
    }
  });
}

test('only an outer inspection call bound before execution gets the allowance', async () => {
  const rejected = await offline.execute('rejected', {siteId: 'site-1', sha256: '1', viewport: {width: 375, height: 667}, steps: []});
  assert.equal(correctableInspectionFailure(rejected), true);
  for (const [variant, allowed] of [['bound direct call', true], ['nested Tool Search child', false], ['unbound call', false],
    ['other tool with an inspection receipt', false]]) {
    const replayed = await replay(STRIXY, {calls: STRIXY.calls.slice(0, 7)});
    const {guard, context, exhausted} = replayed;
    const persistFailure = (id, toolName, result) => guard.toolResultPersist({toolName, toolCallId: id,
      message: {role: 'toolResult', toolName, toolCallId: id, ...result, isError: true}}, {...context, toolName, toolCallId: id});
    for (let i = 0; i < 3; i++) persistFailure(`failed-read-${i}`, 'read', {content: [{type: 'text', text: 'ENOENT'}]});
    assert.equal(exhausted(), false);
    const id = variant === 'nested Tool Search child' ? `tool_search_code:outer:${PREVIEW_INSPECTION_TOOL}:1` : `${variant}-call`;
    const toolName = variant === 'other tool with an inspection receipt' ? 'exec' : PREVIEW_INSPECTION_TOOL;
    const params = variant === 'other tool with an inspection receipt' ? {command: 'true'} : {siteId: 'site-1'};
    if (variant !== 'unbound call') guard.beforeToolCall({toolName, runId: context.runId, toolCallId: id, params}, {...context, toolName, toolCallId: id});
    guard.afterToolCall({toolName, runId: context.runId, toolCallId: id, params, result: rejected}, {...context, toolName, toolCallId: id});
    persistFailure(id, toolName, rejected);
    assert.equal(exhausted(), !allowed, variant);
  }
});
