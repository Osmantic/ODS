import test from 'node:test';
import assert from 'node:assert/strict';
import { createRunProgressBudget } from '../plugin/run-progress-budget.mjs';
import { createToolLoopGuard } from '../plugin/tool-loop-guard.mjs';

const variants = [
  {query:'read', options:{limit:5, scope:'core', format:'json'}},
  {options:{format:'json', scope:'core', limit:5}, query:'read'},
  {query:'read', options:{scope:'core', format:'json', limit:5}},
  {options:{limit:5, format:'json', scope:'core'}, query:'read'},
];

test('key reordering cannot keep identical successful tool descriptions alive', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel', runId:'argument-order', sessionId:'session-order'};
  guard.observeRun(context, 'pixel', {prompt:'Describe the read tool.'});
  for (let i=0; i<12; i++) {
    guard.observeModelCall({runId:context.runId}, context);
    guard.afterToolCall({toolName:'tool_describe', toolCallId:`call-${i}`,
      params:variants[i % variants.length], result:{content:[{type:'text', text:'Read a file.'}]}}, context);
  }
  assert.equal(guard.deliveryVerificationForRun(context.runId).status, 'failed');
  assert.match(guard.deliveryVerificationForRun(context.runId).text, /without progress/);
});

test('ordered arguments and changed values remain distinct progress', () => {
  const budget = createRunProgressBudget();
  for (let i=0; i<20; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`read-${i}`, tool:'read', params:{paths:[String(i), 'common']}, failed:false});
  }
  assert.equal(budget.exhausted, false);
});
