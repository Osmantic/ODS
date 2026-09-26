import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunProgressBudget, failedToolOutcome, isLiteralEcho, RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {RUN_PROGRESS_CORRECTED_ATTEMPTS} from '../plugin/run-progress-budget.mjs';
import {correctableInspectionFailure} from '../plugin/preview-interaction-assurance.mjs';
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
const fail = (budget, callId, extra = {}) => budget.observeResult({callId, tool: 'tool_call', failed: true, ...extra});
const inspectFailure = (budget, callId, result, extra = {}) => fail(budget, callId,
  {tool: 'pixel_ods_workspace_preview_inspect', correctable: correctableInspectionFailure(result), ...extra});
const succeed = (budget, callId, extra = {}) => budget.observeResult({callId, tool: 'read', params: {path: callId}, failed: false, ...extra});
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
  const passed = inspection({status: 'passed', ...SITE, blockedRequests: [], steps: [{index: 0, action: 'assert-visible', status: 'passed'}]}, false);
  for (const result of [MISMATCH, unmeasured('click_failed'), unmeasured('unstable'),
    unmeasured('no_match', {pageErrors: {count: 1, messages: ['boom']}}), unmeasured('no_match', {blockedRequests: ['navigation']}),
    ...['unavailable', 'timeout', 'cancelled', 'output_limit'].map(errorCode => inspection({status: 'failed', errorCode, ...SITE})),
    inspection({status: 'failed', ...SITE, blockedRequests: ['navigation'], steps: [{index: 0, action: 'assert-visible', status: 'passed'}]}),
    inspection({status: 'passed', errorCode: 'invalid_request'}), inspection({status: 'incomplete', errorCode: 'unavailable'}),
    passed, {...REJECTED, isError: undefined}, {...unmeasured('no_match'), isError: false},
    {isError: true, details: {...REJECTED.details, kind: 'ods-pixel-workspace-preview'}},
    {isError: true, details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: 'stopped'}},
    {isError: true, details: {persistedDetailsTruncated: true, status: 'failed'}}, undefined, null]) {
    assert.equal(correctableInspectionFailure(result), false, JSON.stringify(result?.details));
  }
});

test('at consecutive 3 an INCOMPLETE result waits for its corrected attempt', () => {
  for (const corrected of ['passes', 'fails']) {
    const budget = createRunProgressBudget();
    for (let i = 0; i < 3; i++) fail(budget, `bad-${i}`);
    inspectFailure(budget, 'incomplete', INCOMPLETE);
    assert.equal(budget.exhausted, false, 'the result carrying the corrected steps does not end the run');
    if (corrected === 'fails') {
      inspectFailure(budget, 'corrected', unmeasured('no_match'));
      assert.equal(budget.exhausted, true, 'a failed corrected attempt is charged with the deferred one');
    } else {
      budget.observeResult({callId: 'corrected', tool: 'pixel_ods_workspace_preview_inspect', params: {steps: 3}, failed: false});
      assert.equal(budget.exhausted, false);
      assertConsecutiveReset(budget, 'later');
    }
  }
});

test('a correctable failure right after a deferred one is charged together with it', () => {
  const budget = createRunProgressBudget();
  fail(budget, 'bad-0');
  fail(budget, 'bad-1');
  inspectFailure(budget, 'rejected', REJECTED);
  assert.equal(budget.exhausted, false);
  inspectFailure(budget, 'rejected-again', REJECTED);
  assert.equal(budget.exhausted, true, 'consecutive 2 + 2');
});

test('a third correctable failure in a run is charged at once', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_CORRECTED_ATTEMPTS; i++) {
    inspectFailure(budget, `rejected-${i}`, REJECTED);
    succeed(budget, `corrected-${i}`);
  }
  for (let i = 0; i < 3; i++) fail(budget, `bad-${i}`);
  inspectFailure(budget, 'rejected-third', REJECTED);
  assert.equal(budget.exhausted, true);
});

test('correctable failures still count toward the total cap of 12', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.totalFailures; i++) {
    inspectFailure(budget, `unmeasured-${i}`, unmeasured('no_match'));
    assert.equal(budget.exhausted, i === RUN_PROGRESS_LIMITS.totalFailures - 1, `failure ${i + 1}`);
    succeed(budget, `progress-${i}`);
  }
});

test('discovery and free corrections neither charge nor forgive a deferred failure', () => {
  const budget = createRunProgressBudget();
  fail(budget, 'bad-0');
  fail(budget, 'bad-1');
  inspectFailure(budget, 'rejected', REJECTED);
  budget.observeResult({callId: 'search', tool: 'tool_search', params: {query: 'inspect'}, failed: false});
  budget.observeResult({callId: 'describe', tool: 'tool_describe', params: {id: 'inspect'}, failed: false});
  budget.observeResult({callId: 'free-correction', tool: 'process', failed: false, discovery: true});
  assert.equal(budget.exhausted, false);
  fail(budget, 'bad-2');
  assert.equal(budget.exhausted, true, 'consecutive 2 + the deferred failure + this one');
});

test('a lane deferral is forgiven only by a success in the same lane', () => {
  const inspect = {tool: 'pixel_ods_workspace_preview_inspect', lane: 'workspace'};
  const other = createRunProgressBudget();
  for (let i = 0; i < 2; i++) fail(other, `workspace-${i}`, {lane: 'workspace'});
  inspectFailure(other, 'rejected', REJECTED, inspect);
  succeed(other, 'extension-progress', {lane: 'extension'});
  succeed(other, 'unlaned-progress');
  fail(other, 'workspace-2', {lane: 'workspace'});
  assert.equal(other.laneExhausted('workspace'), true, '2 + deferred + 1');
  assert.equal(other.exhausted, false);

  const next = createRunProgressBudget();
  for (let i = 0; i < 2; i++) fail(next, `workspace-${i}`, {lane: 'workspace'});
  inspectFailure(next, 'rejected', REJECTED, inspect);
  fail(next, 'extension-0', {lane: 'extension'});
  assert.equal(next.laneExhausted('workspace'), false, 'the next failure of any tool charges it to its own lane');
  fail(next, 'workspace-2', {lane: 'workspace'});
  assert.equal(next.laneExhausted('workspace'), true, '2 + deferred + 1');
  assert.equal(next.laneExhausted('extension'), false);

  const same = createRunProgressBudget();
  for (let i = 0; i < 2; i++) fail(same, `workspace-${i}`, {lane: 'workspace'});
  inspectFailure(same, 'rejected', REJECTED, inspect);
  succeed(same, 'workspace-progress', {lane: 'workspace'});
  for (let i = 0; i < 3; i++) fail(same, `workspace-later-${i}`, {lane: 'workspace'});
  assert.equal(same.laneExhausted('workspace'), false);
  fail(same, 'workspace-later-3', {lane: 'workspace'});
  assert.equal(same.laneExhausted('workspace'), true);
});

test('visibility mismatches and other measured failures are never deferred', () => {
  for (const result of [MISMATCH, unmeasured('click_failed'), inspection({status: 'failed', errorCode: 'unavailable'})]) {
    const budget = createRunProgressBudget();
    for (let i = 0; i < 3; i++) fail(budget, `bad-${i}`);
    inspectFailure(budget, 'measured', result);
    assert.equal(budget.exhausted, true, JSON.stringify(result.details));
  }
});

test('a duplicate receipt of a deferred failure is ignored and a stop stays sticky', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 3; i++) fail(budget, `bad-${i}`);
  inspectFailure(budget, 'rejected', REJECTED);
  inspectFailure(budget, 'rejected', REJECTED);
  fail(budget, 'rejected');
  assert.equal(budget.exhausted, false, 'one call ID is one result');
  fail(budget, 'bad-3');
  assert.equal(budget.exhausted, true);
  succeed(budget, 'late-progress');
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

test('without correctable the budget is unchanged; with it, a model that keeps failing stops one failure later at most', () => {
  let seed = 20260925;
  const random = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const pick = items => items[Math.floor(random() * items.length)];
  for (let trial = 0; trial < 400; trial++) {
    const omitted = createRunProgressBudget(), flagged = createRunProgressBudget(), before = previousBudget();
    for (let step = 0; step < 80 && !before.exhausted; step++) {
      const kind = random();
      const callId = random() < 0.05 && step > 0 ? `call-${Math.floor(random() * step)}` : `call-${step}`;
      const lane = pick([undefined, undefined, 'workspace', 'extension', 'fake-lane']);
      let event;
      if (kind < 0.15) {
        for (const budget of [omitted, flagged, before]) budget.beginModelRound();
      } else if (kind < 0.55) event = {callId, tool: pick(['tool_call', 'exec', 'pixel_ods_workspace_preview_inspect']), failed: true,
        lane, discovery: random() < 0.1};
      else event = {callId, tool: pick(['read', 'exec', 'tool_search', 'tool_describe', 'pixel_ods_skill', 'tool_call']),
        params: {path: pick(['a', 'b', 'c'])}, failed: false, lane, pending: random() < 0.1, discovery: random() < 0.1};
      if (event) {
        before.observeResult(event);
        omitted.observeResult(event);
        flagged.observeResult({...event, correctable: random() < 0.5});
      }
      assert.equal(omitted.exhausted, before.exhausted, `trial ${trial} step ${step}`);
      assert.deepEqual(omitted.exhaustedLanes, before.exhaustedLanes, `trial ${trial} step ${step}`);
      assert.ok(!flagged.exhausted || before.exhausted, `never stops earlier: trial ${trial} step ${step}`);
    }
    if (before.exhausted && !flagged.exhausted) {
      flagged.observeResult({callId: 'next-failure', tool: 'tool_call', failed: true});
      assert.equal(flagged.exhausted, true, `trial ${trial}: the next failure charges the deferred one`);
    }
  }
});
