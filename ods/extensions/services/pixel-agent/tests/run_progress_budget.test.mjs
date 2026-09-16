import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunProgressBudget, failedToolOutcome, isLiteralEcho} from '../plugin/run-progress-budget.mjs';

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

test('alternating discovery and failures cannot reset total failure allowance', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 12; i++) {
    budget.observeResult({callId:`search-${i}`, tool:'tool_search', params:{query:String(i)}, failed:false});
    budget.observeResult({callId:`bad-${i}`, tool:'tool_call', failed:true});
  }
  assert.equal(budget.exhausted, true);
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
