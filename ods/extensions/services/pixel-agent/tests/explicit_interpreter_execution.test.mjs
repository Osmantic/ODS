import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const retryKey='pixel-ods-explicit-code-execution';
const user='ods-'+ 'd'.repeat(64);
const livePrompt='Execute python3 -c "print(sum(i*i for i in range(1, 14)))" in the terminal and report the observed stdout.';
function turn(prompt=livePrompt, overrides={}) {
  const guard=createToolLoopGuard({abortRunAndDrain:async()=>({aborted:true,drained:true,forceCleared:false})});
  const context={agentId:'pixel',runId:'interp',sessionId:'session',sessionKey:'agent:pixel:openai-user:'+user,trigger:'user',...overrides};
  guard.observeRun(context,'pixel',{prompt});
  const finalize=(ctx=context,event={})=>guard.beforeAgentFinalize({runId:context.runId,
    lastAssistantMessage:'The observed stdout is 91.',
    messages:[{role:'assistant',content:[{type:'text',text:'The observed stdout is 91.'}]}],...event},ctx);
  return {guard,context,finalize};
}
function execute(f, exitCode=0, status='completed', command='python3 -c "print(sum(i*i for i in range(1, 14)))"') {
  const context={...f.context,toolCallId:'interp-exec',toolName:'exec'};
  const params={command,workdir:'/workspace'};
  const prepared=f.guard.beforeToolCall({toolName:'exec',params},context);
  assert.notEqual(prepared?.block,true,prepared?.blockReason);
  f.guard.afterToolCall({toolName:'exec',params:prepared?.params??params,
    result:{details:{status,exitCode,aggregated:exitCode===0?'819':'Interpreter failed'}}},context);
}

test('the exact live command cannot deliver a fabricated stdout',()=>{
  const f=turn(), result=f.guard.deliveryVerificationForRun('interp');
  assert.equal(result.status,'failed');
  assert.match(result.text,/did not receive a successful terminal execution receipt/);
  assert.doesNotMatch(result.text,/91/);
  const decision=f.finalize();
  assert.equal(decision?.retry?.idempotencyKey,retryKey);
  assert.equal(decision.retry.maxAttempts,1);
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
  assert.equal(f.guard.deliveryVerificationForRun('interp').status,'failed');
});

test('a successful execution satisfies the request, including after a native continuation',()=>{
  const f=turn();assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
  f.guard.observeRun(f.context,'pixel',{prompt:'Continue from the previous attempt.'});
  assert.equal(f.guard.deliveryVerificationForRun('interp').status,'failed');
  execute(f);
  assert.notEqual(f.guard.deliveryVerificationForRun('interp').status,'failed');
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
});

test('versioned and inline interpreter commands require execution',()=>{
  for(const prompt of [livePrompt,
    'Now use the terminal: execute python3 -c "print(sum(i*i for i in range(1, 11)))" and report the observed stdout.',
    'Run `python3 -c "print(1)"` and report its output.',
    'Please run `node -e "console.log(1)"`.',
    'Can you execute python3.12 -c "print(1)"?',
    'Run python3 -m pytest and report the results.',
    'Run py -3 -c "print(1)".',
    'Run nodejs -e "console.log(1)".',
    'Execute deno eval "console.log(1)".',
    'Run bash -c "printf 1".',
    'Run pwsh -c "Write-Output 1".',
    'Run the shell script to deploy.',
  ]) {
    const f=turn(prompt);
    assert.equal(f.guard.deliveryVerificationForRun('interp').status,'failed',prompt);
    const retry=f.finalize()?.retry;
    assert.equal(retry?.idempotencyKey,retryKey,prompt);
    assert.doesNotMatch(retry.instruction,/run Python/);
  }
});

test('explanations, code generation, quoted examples and no-tools stay outside execution',()=>{
  for(const prompt of [
    'Explain how to execute python3 -c "print(1)".',
    'Explain `run python3 -c "print(1)"`.',
    'Please explain this example: `execute python3 -c "print(1)"`.',
    'The documentation says "execute python3 -c print(1)". Explain it.',
    '> Run python3 -c "print(1)".\nSummarize that quote.',
    '```\nRun python3 -c "print(1)"\n```\nExplain the sample.',
    'Write Python code to execute python3 -c "print(1)".',
    'Give me a script to run python3 -c "print(1)" locally.',
    'Do not execute python3 -c "print(1)".',
    'Don’t run `python3 -c "print(1)"`.',
    'Run python3 -c "print(1)"; do not actually execute anything.',
    'Do not use tools. Execute python3 -c "print(1)".',
    'Calculate 2+2.',
    'Run a company selling shell furniture.',
    'Run the terminal emulator.',
  ]) {
    const f=turn(prompt);
    assert.notEqual(f.guard.deliveryVerificationForRun('interp').status,'failed',prompt);
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey,prompt);
  }
});

test('failed and running attempts are not repeated by the correction',()=>{
  for(const [exitCode,status] of [[1,'completed'],[0,'running']]) {
    const f=turn();execute(f,exitCode,status);
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
    assert.equal(f.guard.deliveryVerificationForRun('interp').status,'failed');
  }
});

test('command contents cannot cancel the execution obligation',()=>{
  const f=turn('Run `python3 -c "print(\'do not run tools\')"` and report stdout.');
  assert.equal(f.guard.deliveryVerificationForRun('interp').status,'failed');
  assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
});

test('cancelled, foreign and ended runs cannot request execution',async()=>{
  const f=turn();
  for(const ctx of [{...f.context,runId:'other'},{...f.context,sessionId:'other'},{...f.context,sessionKey:'other'}])
    assert.notEqual(f.finalize(ctx)?.retry?.idempotencyKey,retryKey);
  for(const event of [{runId:'other'},{sessionId:'other'},{sessionKey:'other'}])
    assert.notEqual(f.finalize(f.context,event)?.retry?.idempotencyKey,retryKey);
  assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
  const cancelled=turn();assert.equal(await cancelled.guard.abortUserRun(user),true);
  assert.notEqual(cancelled.finalize()?.retry?.idempotencyKey,retryKey);
  const ended=turn();ended.guard.observeAgentEnd({success:true},ended.context);
  assert.notEqual(ended.finalize()?.retry?.idempotencyKey,retryKey);
});
