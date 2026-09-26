import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunProgressBudget, failedToolOutcome, isLiteralEcho, RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {RUN_PROGRESS_CORRECTED_ATTEMPTS} from '../plugin/run-progress-budget.mjs';
import {correctableInspectionFailure, passedInspection} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, TRANSITION_UNTESTED} from '../plugin/workspace-preview-inspect.mjs';

test('outer and nested failure receipts do not masquerade as successful progress', () => {
  for (const event of [{error:'unavailable'}, {result:{isError:true}},
    {result:{details:{exitCode:1}}}, {result:{details:{status:'blocked'}}},
    {result:{details:{result:{isError:true}}}}]) assert.equal(failedToolOutcome(event), true);
  assert.equal(failedToolOutcome({result:{content:[{type:'text',text:'Example error: failed'}],details:{exitCode:0}}}), false);
});

test('consecutive malformed calls trip a sticky run-wide fuse', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 4; i++) {
    budget.beginModelRound();
    budget.observeResult({callId: `call-${i}`, tool:'tool_call', failed:true});
    assert.equal(budget.exhausted, i === 3);
  }
  budget.observeResult({callId:'recovery', tool:'read', params:{path:'file'}, failed:false});
  assert.equal(budget.exhausted, true);
});

test('alternating discovery and failures cannot reset consecutive failure allowance', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 4; i++) {
    budget.observeResult({callId:`search-${i}`, tool:'tool_search', params:{query:String(i)}, failed:false});
    budget.observeResult({callId:`bad-${i}`, tool:'tool_call', failed:true});
    assert.equal(budget.exhausted, i === 3);
  }
  assert.equal(budget.exhausted, true);
});

test('discovery-only rounds are bounded even with distinct successful searches', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 8; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`discover-${i}`, tool:i % 2 ? 'tool_describe' : 'tool_search',
      params:{query:String(i)}, failed:false});
  }
  assert.equal(budget.beginModelRound(), true);
});

test('missing tool hooks are bounded at model continuation level', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 8; i++) assert.equal(budget.beginModelRound(), false);
  assert.equal(budget.beginModelRound(), true);
});

test('genuine progress permits long tasks; repeated success is not endless progress', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 100; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`read-${i}`, tool:'read', params:{path:`file-${i}`}, failed:false});
  }
  for (let i = 0; i < 12; i++) {
    budget.beginModelRound();
    budget.observeResult({callId:`echo-${i}`, tool:'exec', params:{command:'echo done'}, failed:false});
  }
  assert.equal(budget.exhausted, true);
});

test('after-tool and persist delivery of one failure count only once', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 30; i++) budget.observeResult({callId:'same', failed:true});
  assert.equal(budget.exhausted, false);
  for (let i = 0; i < 3; i++) budget.observeResult({callId:`next-${i}`, failed:true});
  assert.equal(budget.exhausted, true);
});

test('caller-classified metadata cannot reset failures or count as task progress', () => {
  const budget=createRunProgressBudget();
  for(let i=0;i<4;i++) {
    budget.observeResult({callId:`failure-${i}`,tool:'exec',failed:true});
    budget.observeResult({callId:`metadata-${i}`,tool:'tool_call',failed:false,discovery:true});
    assert.equal(budget.exhausted,i===3);
  }
  const failedMetadata=createRunProgressBudget();
  for(let i=0;i<4;i++) failedMetadata.observeResult({callId:`metadata-${i}`,tool:'tool_call',failed:true,discovery:true});
  assert.equal(failedMetadata.exhausted,true,'metadata errors still count');
});

test('mixed failure streaks suspend only their lane and remain sticky after sibling progress', () => {
  const budget=createRunProgressBudget();
  for(let i=0;i<4;i++) {
    budget.observeResult({callId:`extension-${i}`,tool:'prepare',lane:'extension',failed:true});
    budget.observeResult({callId:`metadata-${i}`,tool:'status',lane:'extension',discovery:true,failed:false});
    budget.observeResult({callId:`workspace-${i}`,tool:'write',params:{path:`file-${i}`},lane:'workspace',failed:false});
  }
  assert.equal(budget.exhausted,false);
  assert.deepEqual(budget.exhaustedLanes,['extension']);
  budget.observeResult({callId:'late-success',tool:'prepare',lane:'extension',failed:false});
  assert.equal(budget.laneExhausted('extension'),true);
  assert.equal(budget.laneExhausted('workspace'),false);
});

test('mixed lanes retain global total and no-progress ceilings', () => {
  const total=createRunProgressBudget();
  for(let i=0;i<12;i++) {
    total.observeResult({callId:`failure-${i}`,lane:'extension',failed:true});
    assert.equal(total.exhausted,i===11);
  }
  const rounds=createRunProgressBudget();
  for(let i=0;i<8;i++) {
    assert.equal(rounds.beginModelRound(),false);
    rounds.observeResult({callId:`metadata-${i}`,lane:'extension',tool:'status',discovery:true,failed:false});
  }
  assert.equal(rounds.beginModelRound(),true);
});

test('unclassified failures and two exhausted lanes still stop the full run', () => {
  for(const lane of [undefined,'model-supplied-fake-lane']) {
    const budget=createRunProgressBudget();
    for(let i=0;i<4;i++) budget.observeResult({callId:`unknown-${i}`,lane,failed:true});
    assert.equal(budget.exhausted,true);
  }
  const both=createRunProgressBudget();
  for(const lane of ['extension','workspace']) for(let i=0;i<4;i++) {
    both.observeResult({callId:`${lane}-${i}`,lane,failed:true});
    both.observeResult({callId:`${lane}-${i}`,failed:true});
  }
  assert.deepEqual(both.exhaustedLanes,['extension','workspace']);
  assert.equal(both.exhausted,true);
});

test('an external stop is sticky and changes no limit', () => {
  const budget = createRunProgressBudget();
  budget.stop();
  assert.equal(budget.exhausted, true);
  budget.observeResult({callId: 'later', tool: 'read', params: {path: 'x'}, failed: false});
  assert.equal(budget.exhausted, true);
  assert.deepEqual(RUN_PROGRESS_LIMITS, {consecutiveFailures: 4, totalFailures: 12, roundsWithoutProgress: 8, identicalSuccesses: 2});
});

test('verified pending process receipts do not exhaust progress rounds', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 100; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`poll-${i}`, tool:'process', pending:true, failed:false});
  }
});

test('read-only echo classification excludes substitutions, redirections and compound commands', () => {
  for (const command of ['echo "Done!"', "echo 'Site ready'", "echo 'literal $HOME' "]) assert.equal(isLiteralEcho(command), true, command);
  for (const command of ['echo "$HOME"', 'echo "$(touch bad)"', 'echo "`touch bad`"', 'echo "ok" > index.html', 'echo "ok"; rm file', 'echo "ok" && run', 'echo "a\\"', undefined]) assert.equal(isLiteralEcho(command), false, command);
});


// Host-guided inspection failures: the result that already carries the
// correction must not be the one that ends the run before it is sent.
const SITE = {siteId: `site-${'a'.repeat(24)}`, sha256: 'a'.repeat(64), planSha256: 'b'.repeat(64)};
const inspection = (details, isError = true) => ({content: [{type: 'text', text: 'Preview inspection'}],
  details: {schemaVersion: 1, kind: INSPECTION_KIND, scope: INSPECTION_SCOPE, ...details}, ...(isError ? {isError} : {})});
const unmeasured = (errorCode, extra = {}) => inspection({status: 'failed', ...SITE, blockedRequests: [], ...extra,
  steps: [{index: 0, action: 'assert-visible', status: 'passed'}, {index: 1, action: 'assert-hidden', status: 'failed', errorCode}]});
const REJECTED = inspection({status: 'failed', errorCode: 'invalid_request'});
const INCOMPLETE = inspection({status: 'incomplete', errorCode: TRANSITION_UNTESTED, ...SITE});
const MISMATCH = unmeasured('visibility_mismatch');
const PASSED = inspection({status: 'passed', ...SITE, blockedRequests: [],
  steps: [{index: 0, action: 'assert-hidden', status: 'passed'}, {index: 1, action: 'click', status: 'passed'},
    {index: 2, action: 'assert-visible', status: 'passed'}]}, false);
const INSPECT = 'pixel_ods_workspace_preview_inspect';
const fail = (budget, callId, extra = {}) => budget.observeResult({callId, tool: 'tool_call', failed: true, ...extra});
const inspectFailure = (budget, callId, result, extra = {}) => fail(budget, callId,
  {tool: INSPECT, correctable: correctableInspectionFailure(result), ...extra});
const succeed = (budget, callId, extra = {}) => budget.observeResult({callId, tool: 'read', params: {path: callId}, failed: false, ...extra});
const inspectPass = (budget, callId, extra = {}) => budget.observeResult({callId, tool: INSPECT, params: {plan: callId},
  failed: false, corrected: passedInspection(PASSED), ...extra});
const failTimes = (budget, prefix, count, extra = {}) => { for (let i = 0; i < count; i++) fail(budget, `${prefix}-${i}`, extra); };
// Consecutive count 0: three more ordinary failures are allowed, the fourth stops.
function assertConsecutiveReset(budget, prefix) {
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    fail(budget, `${prefix}-${i}`);
    assert.equal(budget.exhausted, i === RUN_PROGRESS_LIMITS.consecutiveFailures - 1, `${prefix} failure ${i + 1}`);
  }
}

test('only failures that measured nothing and name their correction are correctable', () => {
  for (const result of [REJECTED, INCOMPLETE, unmeasured('no_match'), unmeasured('selector_not_unique'),
    unmeasured('invalid_selector')]) assert.equal(correctableInspectionFailure(result), true, JSON.stringify(result.details));
  for (const result of [MISMATCH, unmeasured('click_failed'), unmeasured('unstable'),
    unmeasured('no_match', {pageErrors: {count: 1, messages: ['boom']}}), unmeasured('no_match', {blockedRequests: ['navigation']}),
    ...['unavailable', 'timeout', 'cancelled', 'output_limit'].map(errorCode => inspection({status: 'failed', errorCode, ...SITE})),
    inspection({status: 'failed', ...SITE, blockedRequests: ['navigation'], steps: [{index: 0, action: 'assert-visible', status: 'passed'}]}),
    inspection({status: 'passed', errorCode: 'invalid_request'}), inspection({status: 'incomplete', errorCode: 'unavailable'}),
    PASSED, {...REJECTED, isError: undefined}, {...unmeasured('no_match'), isError: false},
    {isError: true, details: {...REJECTED.details, kind: 'ods-pixel-workspace-preview'}},
    {isError: true, details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: 'stopped'}},
    {isError: true, details: {persistedDetailsTruncated: true, status: 'failed'}}, undefined, null]) {
    assert.equal(correctableInspectionFailure(result), false, JSON.stringify(result?.details));
  }
});

test('only an inspection whose steps all passed without page errors is the corrected attempt', () => {
  assert.equal(passedInspection(PASSED), true);
  for (const result of [REJECTED, INCOMPLETE, MISMATCH, unmeasured('no_match'), {...PASSED, isError: true},
    inspection({...PASSED.details, pageErrors: {count: 1, messages: ['boom']}}, false),
    {...PASSED, details: {...PASSED.details, kind: 'ods-pixel-workspace-preview'}},
    {content: [], details: {status: 'passed'}}, {details: {persistedDetailsTruncated: true, status: 'passed'}}, undefined, null]) {
    assert.equal(passedInspection(result), false, JSON.stringify(result?.details));
  }
});

test('at consecutive 3 an INCOMPLETE result waits for exactly one corrected attempt', () => {
  for (const next of ['passes', 'fails', 'other success', 'pending process']) {
    const budget = createRunProgressBudget();
    failTimes(budget, 'bad', 3);
    inspectFailure(budget, 'incomplete', INCOMPLETE);
    assert.equal(budget.exhausted, false, 'the result carrying the corrected steps does not end the run');
    if (next === 'passes') {
      inspectPass(budget, 'corrected');
      assert.equal(budget.exhausted, false);
      assertConsecutiveReset(budget, 'later');
      continue;
    }
    if (next === 'fails') inspectFailure(budget, 'corrected', unmeasured('no_match'));
    // Not the corrected attempt: an unrelated read (a new path, so it would
    // otherwise be progress) or a running-process receipt.
    else if (next === 'other success') succeed(budget, 'unrelated-read');
    else budget.observeResult({callId: 'poll', tool: 'process', params: {sessionId: 's'}, failed: false, pending: true});
    assert.equal(budget.exhausted, true, `${next}: the waiting charge lands and the run stops one result later`);
  }
});

test('a correctable failure below the fuse is charged at once and spends no allowance', () => {
  // Laptop r107 / mac r106 shape: correctable failures early in the run, each
  // followed by progress, then three ordinary failures and an INCOMPLETE.
  const budget = createRunProgressBudget();
  for (let i = 0; i < 2 * RUN_PROGRESS_CORRECTED_ATTEMPTS; i++) {
    inspectFailure(budget, `early-${i}`, i % 2 ? unmeasured('no_match') : REJECTED);
    succeed(budget, `progress-${i}`);
  }
  failTimes(budget, 'bad', 3);
  inspectFailure(budget, 'incomplete', INCOMPLETE);
  assert.equal(budget.exhausted, false, 'the allowance is still there for the failure that would stop the run');
  inspectPass(budget, 'corrected');
  assertConsecutiveReset(budget, 'later');

  // Wherever a streak of correctable failures starts, it gets exactly one
  // more attempt than the unchanged fuse.
  for (let start = 0; start < RUN_PROGRESS_LIMITS.consecutiveFailures; start++) {
    const streak = createRunProgressBudget();
    failTimes(streak, 'bad', start);
    let attempts = 0;
    while (!streak.exhausted && attempts < 10) inspectFailure(streak, `rejected-${attempts++}`, REJECTED);
    assert.equal(attempts, RUN_PROGRESS_LIMITS.consecutiveFailures - start + 1, `streak from consecutive ${start}`);
  }
});

test('a correctable failure right after a deferred one is charged together with it', () => {
  const budget = createRunProgressBudget();
  failTimes(budget, 'bad', 3);
  inspectFailure(budget, 'rejected', REJECTED);
  assert.equal(budget.exhausted, false);
  inspectFailure(budget, 'rejected-again', REJECTED);
  assert.equal(budget.exhausted, true, 'consecutive 3 + the deferred failure + this one');
});

test('a third deferral in a run is not granted', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_CORRECTED_ATTEMPTS; i++) {
    failTimes(budget, `bad-${i}`, 3);
    inspectFailure(budget, `rejected-${i}`, REJECTED);
    assert.equal(budget.exhausted, false, `deferral ${i + 1}`);
    inspectPass(budget, `corrected-${i}`);
  }
  failTimes(budget, 'bad-last', 3);
  inspectFailure(budget, 'rejected-third', REJECTED);
  assert.equal(budget.exhausted, true);
});

test('correctable failures still count toward the total cap of 12, which is never deferred', () => {
  const spread = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.totalFailures; i++) {
    inspectFailure(spread, `unmeasured-${i}`, unmeasured('no_match'));
    assert.equal(spread.exhausted, i === RUN_PROGRESS_LIMITS.totalFailures - 1, `failure ${i + 1}`);
    succeed(spread, `progress-${i}`);
  }
  // Failure 12 arrives at consecutive 3 with both allowances unused.
  const atCap = createRunProgressBudget();
  for (let i = 0; i < 4; i++) { failTimes(atCap, `pair-${i}`, 2); succeed(atCap, `progress-${i}`); }
  failTimes(atCap, 'bad', 3);
  assert.equal(atCap.exhausted, false);
  inspectFailure(atCap, 'incomplete', INCOMPLETE);
  assert.equal(atCap.exhausted, true);
});

test('discovery and free corrections neither charge nor forgive a waiting charge', () => {
  for (const next of ['failure', 'other success', 'corrected pass']) {
    const budget = createRunProgressBudget();
    failTimes(budget, 'bad', 3);
    inspectFailure(budget, 'rejected', REJECTED);
    budget.observeResult({callId: 'search', tool: 'tool_search', params: {query: 'inspect'}, failed: false});
    budget.observeResult({callId: 'describe', tool: 'tool_describe', params: {id: INSPECT}, failed: false});
    budget.observeResult({callId: 'skill', tool: 'pixel_ods_skill', params: {name: 'web'}, failed: false});
    budget.observeResult({callId: 'free-correction', tool: 'process', failed: false, discovery: true, corrected: true});
    assert.equal(budget.exhausted, false);
    if (next === 'failure') fail(budget, 'bad-after');
    else if (next === 'other success') succeed(budget, 'read-after');
    else inspectPass(budget, 'corrected');
    assert.equal(budget.exhausted, next !== 'corrected pass', next);
  }
});

test('a lane deferral is forgiven only by a passing corrected check in the same lane', () => {
  const inspect = {tool: INSPECT, lane: 'workspace'};
  const waiting = () => {
    const budget = createRunProgressBudget();
    failTimes(budget, 'workspace', 3, {lane: 'workspace'});
    inspectFailure(budget, 'rejected', REJECTED, inspect);
    assert.equal(budget.laneExhausted('workspace'), false, 'the failure carrying the correction does not stop the lane');
    return budget;
  };

  const forgiven = waiting();
  inspectPass(forgiven, 'corrected', {lane: 'workspace'});
  failTimes(forgiven, 'workspace-later', 3, {lane: 'workspace'});
  assert.equal(forgiven.laneExhausted('workspace'), false);
  fail(forgiven, 'workspace-last', {lane: 'workspace'});
  assert.equal(forgiven.laneExhausted('workspace'), true);

  for (const [label, next] of [
    ['a success in the other lane', budget => succeed(budget, 'extension-progress', {lane: 'extension'})],
    ['an unrelated success in the same lane', budget => succeed(budget, 'workspace-read', {lane: 'workspace'})],
    ['a passing check in the other lane', budget => inspectPass(budget, 'extension-check', {lane: 'extension'})],
    ['a failure in the other lane', budget => fail(budget, 'extension-0', {lane: 'extension'})],
  ]) {
    const budget = waiting();
    next(budget);
    assert.equal(budget.laneExhausted('workspace'), true, label);
    assert.equal(budget.laneExhausted('extension'), false, label);
    assert.equal(budget.exhausted, false, `${label}: the other lane remains`);
  }
});

test('visibility mismatches and other measured failures are never deferred', () => {
  for (const result of [MISMATCH, unmeasured('click_failed'), inspection({status: 'failed', errorCode: 'unavailable'})]) {
    const budget = createRunProgressBudget();
    failTimes(budget, 'bad', 3);
    inspectFailure(budget, 'measured', result);
    assert.equal(budget.exhausted, true, JSON.stringify(result.details));
  }
});

test('a duplicate receipt of a deferred failure is ignored and a stop stays sticky', () => {
  const budget = createRunProgressBudget();
  failTimes(budget, 'bad', 3);
  inspectFailure(budget, 'rejected', REJECTED);
  inspectFailure(budget, 'rejected', REJECTED);
  fail(budget, 'rejected');
  assert.equal(budget.exhausted, false, 'one call ID is one result');
  fail(budget, 'bad-3');
  assert.equal(budget.exhausted, true);
  inspectPass(budget, 'late-progress');
  inspectFailure(budget, 'late-rejected', REJECTED);
  assert.equal(budget.exhausted, true);
});

// The observeResult/beginModelRound rules before correctable existed.
function previousBudget() {
  let rounds = 0, failures = 0, consecutive = 0, terminal = false;
  const seen = new Set(), successes = new Map(), laneFailures = new Map(), exhaustedLanes = new Set();
  return {
    get exhausted() { return terminal; },
    get exhaustedLanes() { return [...exhaustedLanes]; },
    beginModelRound() { if (++rounds > RUN_PROGRESS_LIMITS.roundsWithoutProgress) terminal = true; },
    observeResult({callId, tool, params, failed, pending = false, discovery = false, lane}) {
      if (terminal || typeof callId !== 'string' || !callId || seen.has(callId)) return;
      seen.add(callId);
      if (seen.size > 256) seen.delete(seen.values().next().value);
      const laned = ['workspace', 'extension'].includes(lane) ? lane : undefined;
      if (failed) {
        failures += 1;
        if (laned) {
          const count = (laneFailures.get(laned) ?? 0) + 1;
          laneFailures.set(laned, count);
          if (count >= RUN_PROGRESS_LIMITS.consecutiveFailures) exhaustedLanes.add(laned);
        } else consecutive += 1;
        terminal = exhaustedLanes.size === 2 || consecutive >= RUN_PROGRESS_LIMITS.consecutiveFailures ||
          failures >= RUN_PROGRESS_LIMITS.totalFailures;
        return;
      }
      if (discovery || ['tool_search', 'tool_describe', 'pixel_ods_skill'].includes(tool)) return;
      consecutive = 0;
      if (laned) laneFailures.set(laned, 0);
      if (pending) { rounds = 0; return; }
      const key = JSON.stringify([tool, params]);
      const count = (successes.get(key) ?? 0) + 1;
      successes.set(key, count);
      if (successes.size > 128) successes.delete(successes.keys().next().value);
      if (count <= RUN_PROGRESS_LIMITS.identicalSuccesses) rounds = 0;
    },
  };
}

test('property: unchanged without correctable; never earlier with it; without a passing correction, one result later at most', () => {
  let seed = 20260925;
  const random = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const pick = items => items[Math.floor(random() * items.length)];
  // A stopped run ignores every later result, so its lanes no longer matter.
  const state = budget => budget.exhausted ? 'stopped' : JSON.stringify([...budget.exhaustedLanes].sort());
  let deferrals = 0, rescued = 0;
  for (let trial = 0; trial < 600; trial++) {
    const before = previousBudget(), omitted = createRunProgressBudget(), flagged = createRunProgressBudget(),
      uncorrected = createRunProgressBudget();
    const seen = new Set();
    for (let step = 0; step < 80; step++) {
      const kind = random();
      const callId = random() < 0.05 && step > 0 ? `call-${Math.floor(random() * step)}` : `call-${step}`;
      const lane = pick([undefined, undefined, 'workspace', 'extension', 'fake-lane']);
      let event;
      if (kind < 0.15) {
        for (const budget of [before, omitted, flagged, uncorrected]) budget.beginModelRound();
      } else if (kind < 0.55) event = {callId, tool: pick(['tool_call', 'exec', INSPECT]), failed: true,
        lane, discovery: random() < 0.1};
      else event = {callId, tool: pick(['read', 'exec', 'tool_search', 'tool_describe', 'pixel_ods_skill', 'tool_call', INSPECT]),
        params: {path: pick(['a', 'b', 'c'])}, failed: false, lane, pending: random() < 0.1, discovery: random() < 0.1};
      const correctable = random() < 0.5, corrected = random() < 0.5;
      const lagging = state(uncorrected) !== state(before), flaggedWaits = state(flagged) !== state(before);
      if (event) {
        before.observeResult(event);
        omitted.observeResult({...event, corrected});
        flagged.observeResult({...event, correctable, corrected});
        uncorrected.observeResult({...event, correctable});
      }
      const at = `trial ${trial} step ${step}`;
      assert.equal(state(omitted), state(before), `without correctable: ${at}`);
      for (const budget of [flagged, uncorrected]) {
        assert.ok(!budget.exhausted || before.exhausted, `never stops earlier: ${at}`);
        assert.ok(before.exhausted || budget.exhaustedLanes.every(item => before.exhaustedLanes.includes(item)),
          `never stops a lane earlier: ${at}`);
      }
      // Discovery, a model round or a repeated call ID leaves a waiting charge
      // waiting; any other result resolves it, and without a passing
      // correction the budget then matches the unchanged one again.
      const neutral = !event || seen.has(event.callId) ||
        (!event.failed && (event.discovery || ['tool_search', 'tool_describe', 'pixel_ods_skill'].includes(event.tool)));
      if (event) seen.add(event.callId);
      const differs = state(uncorrected) !== state(before);
      if (differs && !lagging) {
        assert.ok(event?.failed && correctable && !neutral, `only a correctable failure opens a wait: ${at}`);
        deferrals += 1;
      }
      if (lagging && differs) assert.ok(neutral, `a waiting charge lasts one result: ${at}`);
      // A passing correction resolved flagged's wait and kept it going.
      if (flaggedWaits && !neutral && !event.failed && corrected && state(flagged) !== state(before)) rescued += 1;
    }
  }
  assert.ok(deferrals > 50, `the property exercised deferrals (${deferrals})`);
  assert.ok(rescued > 0, 'a passing correction can keep a run going');
});
