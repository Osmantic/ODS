// Composition of #6748 and #6749. #6748 replaces an inspection result's next
// step with "do not call any tool; answer the owner now" on the failure that
// ends the response's tool use (run-progress-budget failureEnds). #6749 lets a
// correctable inspection failure whose charge would trip the fuse wait for one
// corrected attempt. Without reconciling them, that correctable failure told
// the model to stop although the run still allowed the corrected call, and the
// ready call #6748 built for it was withheld. Here failureEnds follows
// observeResult's failure path exactly (correctable deferral included), the
// guard's guidance says when a correctable failure would wait
// (correctableWaits), and the tool keeps its next step only for a result that
// the budget classifies as correctable.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunProgressBudget, RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {correctableInspectionFailure} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, createWorkspacePreviewInspectTool, inspectionPlanHash,
  normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const FINAL = ' This failed call was the last one this response allows, so no further tool call can run: do not call any tool. ' +
  'Answer the owner now from the results already returned, and report the requested behavior as unverified.';
const INSPECT = 'pixel_ods_workspace_preview_inspect';
const LANES = [undefined, 'workspace', 'extension'];

// Deterministic PRNG (mulberry32) so a failure names a reproducible trace.
function random(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const apply = (budget, op) => op.op === 'round' ? budget.beginModelRound() : budget.observeResult(op.event);
const replayed = ops => { const budget = createRunProgressBudget(); for (const op of ops) apply(budget, op); return budget; };
// What one more failure actually does: the run stops, the failure's lane
// stops, or the next model round stops the run.
function actuallyEnds(ops, lane, correctable) {
  const budget = replayed(ops);
  budget.observeResult({callId: 'probe', tool: INSPECT, failed: true, lane, correctable});
  return budget.exhausted || (lane !== undefined && budget.laneExhausted(lane)) || budget.beginModelRound();
}

test('failureEnds predicts exactly what one more failure does, correctable or not, in every lane', () => {
  let checks = 0, deferrals = 0;
  for (let seed = 1; seed <= 400; seed++) {
    const next = random(seed), ops = [];
    const pick = list => list[Math.floor(next() * list.length)];
    for (let i = 0; i < 40; i++) {
      const budget = replayed(ops);
      for (const lane of LANES) {
        for (const correctable of [false, true]) {
          const predicted = budget.failureEnds(lane, {correctable});
          assert.equal(predicted, actuallyEnds(ops, lane, correctable),
            `seed ${seed} step ${i} lane ${lane} correctable ${correctable}: ${JSON.stringify(ops)}`);
          if (!predicted && !budget.failureEnds(lane)) continue;
          if (!predicted) deferrals += 1;
          checks += 1;
        }
      }
      // Read-only: asking changed nothing.
      assert.equal(budget.exhausted, replayed(ops).exhausted);
      const roll = next();
      const lane = pick(LANES);
      ops.push(roll < 0.12 ? {op: 'round'}
        : roll < 0.62 ? {op: 'fail', event: {callId: `f${i}`, tool: INSPECT, failed: true, lane, correctable: next() < 0.6}}
          : roll < 0.72 ? {op: 'ok', event: {callId: `c${i}`, tool: INSPECT, params: {plan: i}, failed: false, lane, corrected: true}}
            : roll < 0.8 ? {op: 'ok', event: {callId: `d${i}`, tool: 'tool_search', failed: false, discovery: true}}
              : roll < 0.85 ? {op: 'ok', event: {callId: `p${i}`, tool: 'process', params: {sessionId: 's'}, failed: false, pending: true, lane}}
                : {op: 'ok', event: {callId: `r${i}`, tool: 'read', params: {path: pick(['a', 'b', `n${i}`])}, failed: false, lane}});
    }
  }
  assert.ok(checks > 1000, `${checks} ending checks`);
  assert.ok(deferrals > 100, `${deferrals} correctable failures that would wait instead of ending`);
});

test('at the fuse only a correctable failure with an allowance left keeps going', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) budget.observeResult({callId: `f${i}`, tool: 'read', failed: true});
  assert.equal(budget.failureEnds(), true);
  assert.equal(budget.failureEnds(undefined, {correctable: true}), false, 'its charge would wait for one corrected attempt');
  budget.observeResult({callId: 'waiting', tool: INSPECT, failed: true, correctable: true});
  assert.equal(budget.exhausted, false);
  // A charge is waiting: any further failure lands it, correctable or not.
  assert.equal(budget.failureEnds(), true);
  assert.equal(budget.failureEnds(undefined, {correctable: true}), true);
  // The total cap is never held back.
  const total = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.totalFailures - 1; i++) {
    total.observeResult({callId: `t${i}`, tool: 'read', failed: true});
    total.observeResult({callId: `ok${i}`, tool: 'read', params: {path: `p${i}`}, failed: false});
  }
  assert.equal(total.failureEnds(undefined, {correctable: true}), true);
});

// The tool's wording, given the guard's guidance for the pending call.
const SITE = {siteId: `site-${'a'.repeat(24)}`, sha256: 'a'.repeat(64), viewport: {width: 375, height: 667}};
const CARD = {selector: '#card'}, BUTTON = {role: 'button', name: 'Show sold out', exact: true};
const PLAN = {...SITE, steps: [{action: 'assert-hidden', locator: CARD}, {action: 'click', locator: BUTTON},
  {action: 'assert-visible', locator: CARD}]};
const state = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible', opacity: '1',
  hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
const receipt = (request, steps) => ({schemaVersion: 1, kind: INSPECTION_KIND,
  status: steps.length === request.steps.length && steps.every(step => step.status === 'passed') ? 'passed' : 'failed',
  siteId: request.siteId, sha256: request.sha256, planSha256: inspectionPlanHash(request), viewport: request.viewport, steps,
  diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE});
const noMatch = request => receipt(request, [{index: 0, ...request.steps[0], before: {count: 0}, stable: true, status: 'failed', errorCode: 'no_match'}]);
const mismatch = request => receipt(request, [{index: 0, ...request.steps[0], before: state(true), stable: true, status: 'failed',
  errorCode: 'visibility_mismatch'}]);
const passedWithoutTransition = request => receipt(request, [{index: 0, ...request.steps[0], before: state(true), stable: true, status: 'passed'}]);
const REQUIREMENT = {target: 'Midnight', control: {role: 'button', name: 'Show sold out'}, initiallyHidden: true};
const CASES = [
  ['rejected arguments', {...SITE, steps: [{action: 'assert-hidden', selector: '#card'}]}, undefined, true],
  ['a locator that matched nothing', PLAN, noMatch, true],
  ['an untested requested transition (INCOMPLETE)', {...SITE, steps: [{action: 'assert-visible', locator: {selector: 'h1'}}]},
    passedWithoutTransition, true],
  ['a measured visibility mismatch', PLAN, mismatch, false],
];
async function inspectWith(hints, params, capsule) {
  const tool = createWorkspacePreviewInspectTool({
    request: async request => capsule ? capsule(request) : assert.fail('the inspector must not be contacted'),
    transitionRequirement: () => REQUIREMENT, guidance: () => hints});
  return tool.execute('pending', params);
}

test('a correctable failure whose charge waits keeps its next step; every other ending failure answers the owner', async () => {
  for (const [name, params, capsule, correctable] of CASES) {
    if (capsule) normalizeWorkspacePreviewInspectionParams(params);
    const waits = await inspectWith({finalFailure: true, correctableWaits: true}, params, capsule);
    assert.equal(waits.isError, true, name);
    assert.equal(correctableInspectionFailure(waits), correctable, `${name}: the budget's classification`);
    const summary = waits.content[0].text;
    if (correctable) {
      assert.ok(!summary.includes(FINAL), `${name}: ${summary}`);
      assert.match(summary, /Next step/, name);
    } else assert.ok(summary.includes(FINAL), `${name}: ${summary}`);
    // Without an allowance (or a charge already waiting) every failure is the last.
    const ends = (await inspectWith({finalFailure: true}, params, capsule)).content[0].text;
    assert.ok(ends.includes(FINAL), `${name}: ${ends}`);
    assert.doesNotMatch(ends.slice(0, ends.indexOf(FINAL)), /Next step/, name);
    // Not at the fuse: never the final wording.
    const open = (await inspectWith({}, params, capsule)).content[0].text;
    assert.ok(!open.includes(FINAL), `${name}: ${open}`);
  }
});
