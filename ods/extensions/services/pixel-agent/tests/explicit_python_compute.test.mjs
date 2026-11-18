import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const prompt='Use Python to calculate sum(i*i for i in range(1, 21)). Show the exact command and its result.';
const retryKey='pixel-ods-explicit-code-execution';
const user='ods-'+ 'c'.repeat(64);
function turn(options={}) {
  const guard=createToolLoopGuard({abortRunAndDrain:async()=>({aborted:true,drained:true,forceCleared:false})});
  const context={agentId:'pixel',runId:'compute',sessionId:'session',sessionKey:'agent:pixel:openai-user:'+user,trigger:'user',...options.context};
  guard.observeRun(context,'pixel',{prompt:options.prompt??prompt});
  const finalize=(ctx=context,event={})=>guard.beforeAgentFinalize({runId:context.runId,
    lastAssistantMessage:'The sum is 2870.',messages:[{role:'assistant',content:[{type:'text',text:'The sum is 2870.'}]}],...event},ctx);
  return {guard,context,finalize};
}
function execute(f,exitCode=0,status='completed') {
  const context={...f.context,toolCallId:'compute-exec',toolName:'exec'};
  const params={command:"python3 -c 'print(sum(i*i for i in range(1,21)))'",workdir:'/workspace'};
  const prepared=f.guard.beforeToolCall({toolName:'exec',params},context);
  assert.notEqual(prepared?.block,true,prepared?.blockReason);
  f.guard.afterToolCall({toolName:'exec',params:prepared?.params??params,
    result:{details:{status,exitCode,aggregated:exitCode===0?'2870':'Python failed'}}},context);
}

test('the exact live Python request requires an execution receipt before finalization',()=>{
  const f=turn(),result=f.guard.deliveryVerificationForRun('compute');
  assert.equal(result.status,'failed');
  assert.match(result.text,/did not receive a successful terminal execution receipt/);
  assert.doesNotMatch(result.text,/2870/);
});

test('one revision asks for real execution and never manufactures its receipt',()=>{
  const f=turn(),decision=f.finalize();
  assert.equal(decision?.action,'revise');
  assert.equal(decision.retry.idempotencyKey,retryKey);
  assert.equal(decision.retry.maxAttempts,1);
  assert.match(decision.retry.instruction,/exec/);
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
  assert.equal(f.guard.deliveryVerificationForRun('compute').status,'failed');
});

test('execution recovery honors a no-file-change request and keeps ordinary exec denied',()=>{
  const f=turn({prompt:prompt+' Do not create or modify files.'});
  const decision=f.finalize();
  assert.equal(decision?.retry?.idempotencyKey,retryKey);
  assert.match(decision.retry.instruction,/Discover and use pixel_ods_readonly_exec/);
  assert.match(decision.retry.instruction,/relative workspace input paths/);
  assert.match(decision.retry.instruction,/owner-requested format/);
  assert.doesNotMatch(decision.retry.instruction,/Use the exec tool/);
  const context={...f.context,toolName:'exec',toolCallId:'still-forbidden'};
  assert.equal(f.guard.beforeToolCall({toolName:'exec',params:{command:'printf 4'}},context)?.block,true);
  assert.equal(f.guard.deliveryVerificationForRun('compute').status,'failed');
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
});

test('revision followed by a bound successful execution satisfies the request',()=>{
  const f=turn();assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
  // A native continuation cannot erase the original owner's obligation.
  f.guard.observeRun(f.context,'pixel',{prompt:'Continue from the previous attempt.'});
  assert.equal(f.guard.deliveryVerificationForRun('compute').status,'failed');
  execute(f);
  assert.notEqual(f.guard.deliveryVerificationForRun('compute').status,'failed');
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
});

test('failed and pending execution attempts are not automatically repeated',()=>{
  for(const [exitCode,status] of [[1,'completed'],[undefined,'running']]) {
    const f=turn();execute(f,exitCode,status);
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
    assert.equal(f.guard.deliveryVerificationForRun('compute').status,'failed');
  }
});

test('imperative Python calculations are recognized without classifying examples as commands',()=>{
  for(const text of [prompt,'Please use the Python tool to compute 2+2.',
    'Calculate the mean using Python.','Evaluate this expression with Python.',
    'Read data/orders.csv and use Python to calculate paid orders only. Do not create or modify files.',
    'Open orders.csv and then please use Python to compute its total.',
    'Inspect orders.csv then calculate the total using Python.',
    'Can you use Python to verify this sum?']) {
    const f=turn({prompt:text});
    assert.equal(f.guard.deliveryVerificationForRun('compute').status,'failed',text);
    assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey,text);
  }
  for(const text of ['What is 2+2?','Explain how to use Python to calculate a sum.',
    'Write Python code to calculate a sum.','Give me Python code to run locally.',
    'The documentation says "Use Python to calculate the sum." Explain it.',
    'Explain how to read orders.csv and use Python to calculate its total.',
    'Read the instruction "Use Python to calculate the sum" and explain it.',
    'Read orders.csv and explain how to calculate its total using Python.',
    'Read orders.csv and calculate the total without using tools.',
    'Read orders.csv and use Python to calculate its total; do not execute anything.',
    '> Use Python to calculate 2+2.\nSummarize that quote.',
    '```\nUse Python to calculate 2+2.\n```\nExplain the sample.',
    'Use Python to calculate 2+2; do not actually execute anything.',
    'Don’t use Python to calculate it.','Do not calculate this using Python.',
    'Without using tools, use Python to calculate 2+2.',
    'Evaluate whether Python is a good teaching language.']) {
    const f=turn({prompt:text});
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey,text);
    assert.notEqual(f.guard.deliveryVerificationForRun('compute').status,'failed',text);
  }
});

test('foreign event or context identities cannot consume the owner revision',()=>{
  const f=turn();
  for(const context of [{...f.context,runId:'other'},{...f.context,sessionId:'other'},
    {...f.context,sessionKey:'other'}]) assert.notEqual(f.finalize(context)?.retry?.idempotencyKey,retryKey);
  for(const event of [{runId:'other'},{sessionId:'other'},{sessionKey:'other'}])
    assert.notEqual(f.finalize(f.context,event)?.retry?.idempotencyKey,retryKey);
  assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
});

test('superseded and ended runs cannot request more execution',()=>{
  const f=turn();f.guard.observeRun({...f.context,runId:'next'},'pixel',{prompt:'Hello'});
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
  const ended=turn();ended.guard.observeAgentEnd({success:true},ended.context);
  assert.notEqual(ended.finalize()?.retry?.idempotencyKey,retryKey);
});

test('cancelled, noninteractive, managed-team and no-tools turns cannot retry',async()=>{
  const cancelled=turn();assert.equal(await cancelled.guard.abortUserRun(user),true);
  assert.notEqual(cancelled.finalize()?.retry?.idempotencyKey,retryKey);
  for(const options of [{context:{trigger:'cron'}},{context:{sessionKey:'agent:pixel:main'}},
    {prompt:"You are the Builder in the owner's Portal team. Use Python to calculate 2+2."},
    {prompt:'Do not use tools. Use Python to calculate 2+2.'}]) {
    assert.notEqual(turn(options).finalize()?.retry?.idempotencyKey,retryKey,JSON.stringify(options));
  }
});

test('exhausted progress does not obtain an additional execution revision',()=>{
  const f=turn();
  for(let i=0;i<RUN_PROGRESS_LIMITS.consecutiveFailures;i++) f.guard.toolResultPersist({toolCallId:'failed-'+i,
    message:{role:'toolResult',toolName:'read',isError:true,content:[]}}, {...f.context,toolName:'read',toolCallId:'failed-'+i});
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
});
