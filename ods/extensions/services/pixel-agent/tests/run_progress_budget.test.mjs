import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunProgressBudget, failedToolOutcome, isLiteralEcho, RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

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

// A result offered to the model must not name a call the budget will refuse.
test('failureEnds says, read-only, whether one more failure stops the response or its lane', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 3; i++) {
    assert.equal(budget.failureEnds(), false);
    budget.beginModelRound();
    budget.observeResult({callId: `bad-${i}`, tool: 'tool_call', failed: true});
  }
  assert.equal(budget.failureEnds(), true, 'the fourth consecutive failure stops the response');
  assert.equal(budget.exhausted, false, 'asking changes nothing');
  budget.observeResult({callId: 'ok', tool: 'read', params: {path: 'file'}, failed: false});
  assert.equal(budget.failureEnds(), false);
  // Eleven failures in all: the twelfth stops the response whatever came between.
  for (let i = 3; i < 11; i++) {
    budget.observeResult({callId: `bad-${i}`, tool: 'tool_call', failed: true});
    if (i % 2) budget.observeResult({callId: `ok-${i}`, tool: 'read', params: {path: `f${i}`}, failed: false});
  }
  assert.equal(budget.failureEnds(), true);
  // A classified lane stops at its own fourth consecutive failure.
  const lanes = createRunProgressBudget();
  for (let i = 0; i < 3; i++) lanes.observeResult({callId: `w-${i}`, tool: 'write', failed: true, lane: 'workspace'});
  assert.equal(lanes.failureEnds('workspace'), true);
  assert.equal(lanes.failureEnds('extension'), false);
  assert.equal(lanes.failureEnds(), false);
  // Eight model rounds without progress: the next round stops the response.
  const rounds = createRunProgressBudget();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.roundsWithoutProgress; i++) rounds.beginModelRound();
  assert.equal(rounds.failureEnds(), true);
});
