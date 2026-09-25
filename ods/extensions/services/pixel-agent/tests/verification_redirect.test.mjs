import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND, PHANTOM_PROCESS_REASON,
  VERIFICATION_COMMAND_NOT_AUDITABLE_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

// Fleet evidence (strixy, round 055): the owner asked for the actual unittest
// output as public/test-results.txt and the model redirected the runner.
const prompt = 'Run the unit tests in /workspace/project and publish the actual unittest output as public/test-results.txt.';
const redirected = [
  'cd /workspace/project && python3 -m unittest -v > test-results.txt',
  'python3 -m unittest -v > public/test-results.txt 2>&1',
  'python3 -m unittest -v 2>&1 > public/test-results.txt',
  'python3 -m unittest -v &> public/test-results.txt',
  'python3 -m unittest -v | tee public/test-results.txt',
];
const output = 'test_total (test_report.ReportTest.test_total) ... ok\n\nRan 1 test in 0.001s\n\nOK';
const passingTests = {content:[{type:'text',text:output}],details:{status:'completed',exitCode:0,aggregated:output}};
const missingFile = {isError:true,content:[{type:'text',text:'ENOENT'}],details:{status:'error'}};

// Replays OpenClaw's hook sequence for one model round and one tool call.
// A blocked call gets the SDK's standard veto result.
function harness({wrapped = false} = {}) {
  const prepared = [];
  const guard = createToolLoopGuard({execControl:{prepare:(_run, command) => { prepared.push(command); return command; },
    signal:() => true}});
  const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key'};
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  function step(name, args, inner, id = `${name}-${++sequence}`) {
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id:`openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId:id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId:id}, ctx);
    let result, executed = params;
    if (decision?.block) {
      result = {content:[{type:'text',text:decision.blockReason}],
        details:{status:'blocked',deniedReason:'plugin-before-tool-call',reason:decision.blockReason}};
    } else {
      executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
      const envelope = {tool:{id:`openclaw:core:${name}`,name,source:'openclaw',sourceName:'core'},result:inner};
      result = wrapped ? {content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope} : inner;
    }
    const isError = decision?.block === true || inner?.isError === true;
    guard.afterToolCall({toolName, params:executed, toolCallId:id, result,
      ...(isError ? {error:result.content[0].text} : {})}, ctx);
    const message = {role:'toolResult', toolName, toolCallId:id, isError, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId:id, message}, ctx)?.message ?? message;
    return {decision, text:persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  }
  const exhausted = () => guard.beforeToolCall({toolName:'read', params:{path:'README.md'}, toolCallId:'probe'},
    {...context, toolName:'read', toolCallId:'probe'})?.blockReason === RUN_PROGRESS_STOP_REASON;
  return {guard, prepared, step, exhausted};
}

test('the refusal names the supported path in fixed text', () => {
  assert.match(VERIFICATION_COMMAND_NOT_AUDITABLE_REASON, /Run it directly/);
  assert.match(VERIFICATION_COMMAND_NOT_AUDITABLE_REASON, /save the returned output with the write tool/);
  assert.ok(VERIFICATION_COMMAND_NOT_AUDITABLE_REASON.split(/(?<=\.)\s/).length <= 2, 'one or two sentences');
});

for (const wrapped of [false, true]) {
  test(`redirected test runs are refused before execution with the same text (wrapped=${wrapped})`, () => {
    const {prepared, step} = harness({wrapped});
    for (const command of redirected) {
      const {decision, text} = step('exec', {command, workdir:'/workspace/project'}, passingTests);
      assert.deepEqual(decision, {block:true, blockReason:VERIFICATION_COMMAND_NOT_AUDITABLE_REASON}, command);
      assert.equal(text, VERIFICATION_COMMAND_NOT_AUDITABLE_REASON, command);
    }
    assert.deepEqual(prepared, [], 'no refused command reaches execution');
  });

  test(`the bare run then a write of its output is the supported path (wrapped=${wrapped})`, () => {
    const {guard, step, exhausted} = harness({wrapped});
    for (const command of redirected.slice(0, 3)) step('exec', {command, workdir:'/workspace/project'}, passingTests);
    assert.notEqual(step('exec', {command:'python3 -m unittest -v', workdir:'/workspace/project'}, passingTests).decision?.block, true);
    assert.notEqual(step('write', {path:'project/public/test-results.txt', content:output},
      {content:[{type:'text',text:'Wrote file.'}],details:{status:'completed'}}).decision?.block, true);
    assert.equal(guard.verificationForRun('run').status, 'passed');
    assert.equal(exhausted(), false, 'three refusals did not end the run');
  });

  test(`free refusals neither charge nor reset the failure budget (wrapped=${wrapped})`, () => {
    const {step, exhausted} = harness({wrapped});
    step('exec', {command:'python3 -m unittest -v', workdir:'/workspace/project'}, passingTests);
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) step('read', {path:`project/missing-${i}.py`}, missingFile);
    for (let i = 0; i < FREE_CORRECTIONS_PER_KIND; i++) {
      step('exec', {command:redirected[1], workdir:'/workspace/project'}, passingTests);
      assert.equal(exhausted(), false, `free refusal ${i + 1} was charged`);
    }
    step('read', {path:'project/missing-last.py'}, missingFile);
    assert.equal(exhausted(), true);
  });

  test(`identical refusals beyond the bound are charged, so a loop still ends (wrapped=${wrapped})`, () => {
    const {step, exhausted} = harness({wrapped});
    step('exec', {command:'python3 -m unittest -v', workdir:'/workspace/project'}, passingTests);
    const limit = FREE_CORRECTIONS_PER_KIND + RUN_PROGRESS_LIMITS.consecutiveFailures;
    for (let i = 1; i <= limit; i++) {
      assert.equal(step('exec', {command:redirected[1], workdir:'/workspace/project'}, passingTests).decision?.blockReason,
        VERIFICATION_COMMAND_NOT_AUDITABLE_REASON, `call ${i}`);
      assert.equal(exhausted(), i === limit, `call ${i}`);
    }
  });
}

test('each corrective kind has its own bounded allowance', () => {
  const {step, exhausted} = harness();
  step('exec', {command:'python3 -m unittest -v', workdir:'/workspace/project'}, passingTests);
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) step('read', {path:`project/missing-${i}.py`}, missingFile);
  for (let i = 0; i < FREE_CORRECTIONS_PER_KIND; i++) {
    assert.equal(step('process', {action:'poll'}, {details:{status:'failed'}}).decision?.blockReason, PHANTOM_PROCESS_REASON);
    assert.equal(step('exec', {command:redirected[0]}, passingTests).decision?.blockReason, VERIFICATION_COMMAND_NOT_AUDITABLE_REASON);
  }
  assert.equal(exhausted(), false);
  step('exec', {command:redirected[0]}, passingTests);
  assert.equal(exhausted(), true, 'the next refusal of a used-up kind is charged');
});
