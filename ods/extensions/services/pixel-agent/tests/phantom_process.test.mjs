import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND, PHANTOM_PROCESS_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

const prompt = 'Run the existing Python unit tests and report the result.';
const passingTests = {content:[{type:'text',text:'Ran 22 tests. OK'}],details:{status:'completed',exitCode:0}};
const missingFile = {isError:true,content:[{type:'text',text:'ENOENT'}],details:{status:'error'}};
const running = sessionId => ({content:[{type:'text',text:`Command still running (session ${sessionId}, pid 95242).`}],
  details:{status:'running',sessionId,pid:95242}});

// Replays OpenClaw's hook sequence for one model round and one tool call:
// before_tool_call, the executed or blocked result, after_tool_call and
// tool_result_persist. A blocked call gets the SDK's standard veto result.
function harness({wrapped = false, guard = createToolLoopGuard(), runId = 'run',
  sessionId = 'owner-session', sessionKey = 'owner-key'} = {}) {
  const context = {agentId:'pixel', runId, sessionId, sessionKey};
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  function before(name, args, id) {
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id:`openclaw:core:${name}`, args} : args;
    return {toolName, params, ctx:{...context, toolName, toolCallId:id},
      decision:guard.beforeToolCall({toolName, params, toolCallId:id}, {...context, toolName, toolCallId:id})};
  }
  function finish({toolName, params, ctx, decision}, name, inner) {
    const id = ctx.toolCallId;
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
    return persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n');
  }
  function step(name, args, inner, id = `${name}-${++sequence}`) {
    guard.observeModelCall({}, context);
    const pending = before(name, args, id);
    const text = finish(pending, name, inner);
    return {decision:pending.decision, text};
  }
  // Exhaustion refuses every further tool with the canned stop message.
  const exhausted = () => guard.beforeToolCall({toolName:'read', params:{path:'README.md'}, toolCallId:'probe'},
    {...context, toolName:'read', toolCallId:'probe'})?.blockReason === RUN_PROGRESS_STOP_REASON;
  return {guard, context, before, finish, step, exhausted};
}

// Every process call from the fleet evidence (round 054) after exec passed.
const evidence = [
  {action:'poll', timeout:10000},
  {action:'log', offset:0, limit:100},
  {action:'list'},
  {action:'poll', sessionId:'934e3253-5b1c-4f5e-9d8a-2f4c1b7e6a90'},
];

for (const wrapped of [false, true]) {
  for (const args of evidence) {
    test(`phantom process ${JSON.stringify(args)} is answered directly (wrapped=${wrapped})`, () => {
      const {step} = harness({wrapped});
      assert.notEqual(step('exec', {command:'python3 -m unittest', workdir:'/workspace/project'}, passingTests).decision?.block, true);
      const {decision, text} = step('process', args, {details:{status:'failed'}});
      assert.deepEqual(decision, {block:true, blockReason:PHANTOM_PROCESS_REASON});
      // The model sees exactly the fixed answer: no appended coaching and no echoed arguments.
      assert.equal(text, PHANTOM_PROCESS_REASON);
    });
  }

  test(`phantom answers are byte-stable across a run (wrapped=${wrapped})`, () => {
    const {step} = harness({wrapped});
    step('exec', {command:'python3 -m unittest', workdir:'/workspace/project'}, passingTests);
    const texts = evidence.map(args => step('process', args, {details:{status:'failed'}}).text);
    assert.deepEqual(texts, evidence.map(() => PHANTOM_PROCESS_REASON));
    assert.doesNotMatch(PHANTOM_PROCESS_REASON, /934e3253|\d/);
  });

  test(`a real pending session still polls, with alias canonicalization (wrapped=${wrapped})`, () => {
    const {before, step} = harness({wrapped});
    const server = {command:'python3 -m http.server 8000', workdir:'/workspace/project', background:true};
    step('exec', server, running('fast-breeze'));
    assert.notEqual(step('process', {action:'poll', sessionId:'fast-breeze'}, running('fast-breeze')).decision?.block, true);
    const alias = before('process', {action:'poll', sessionId:'session-fast-breeze-95242'}, 'alias').decision;
    if (wrapped) assert.notEqual(alias?.block, true);
    else assert.deepEqual(alias, {params:{action:'poll', sessionId:'fast-breeze'}});
    // While a real session exists, even malformed or invented calls keep core's own answer.
    for (const args of evidence) assert.notEqual(before('process', args, 'other').decision?.block, true, JSON.stringify(args));
    step('process', {action:'poll', sessionId:'fast-breeze'},
      {content:[{type:'text',text:'Serving stopped.'}], details:{status:'completed', sessionId:'fast-breeze', exitCode:0}});
    // A completed real session remains inspectable.
    for (const args of [{action:'log', sessionId:'fast-breeze'}, {action:'list'}]) {
      assert.notEqual(before('process', args, 'after').decision?.block, true, JSON.stringify(args));
    }
  });

  test(`free phantom answers do not charge or reset the failure budget (wrapped=${wrapped})`, () => {
    const {step, exhausted} = harness({wrapped});
    step('exec', {command:'python3 -m unittest', workdir:'/workspace/project'}, passingTests);
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
      step('read', {path:`project/missing-${i}.py`}, missingFile);
    }
    for (let i = 0; i < FREE_CORRECTIONS_PER_KIND; i++) {
      assert.equal(step('process', evidence[i], {details:{status:'failed'}}).decision?.blockReason, PHANTOM_PROCESS_REASON);
      assert.equal(exhausted(), false, `free phantom answer ${i + 1} was charged`);
    }
    // A free answer is not progress either: the next real failure still trips the fuse.
    step('read', {path:'project/missing-last.py'}, missingFile);
    assert.equal(exhausted(), true);
  });

  test(`phantom answers beyond the bound are charged, so spam still ends (wrapped=${wrapped})`, () => {
    const {step, exhausted} = harness({wrapped});
    step('exec', {command:'python3 -m unittest', workdir:'/workspace/project'}, passingTests);
    const limit = FREE_CORRECTIONS_PER_KIND + RUN_PROGRESS_LIMITS.consecutiveFailures;
    for (let i = 1; i <= limit; i++) {
      const {decision} = step('process', {action:'poll'}, {details:{status:'failed'}});
      assert.equal(decision?.blockReason, PHANTOM_PROCESS_REASON, `call ${i}`);
      assert.equal(exhausted(), i === limit, `call ${i}`);
    }
    assert.equal(step('process', {action:'poll'}, {details:{status:'failed'}}).decision?.blockReason, RUN_PROGRESS_STOP_REASON);
  });
}

test('the round-054 evidence sequence no longer exhausts a passing run', () => {
  const {step, exhausted} = harness({wrapped:true});
  const tests = {command:'python3 -m unittest', workdir:'/workspace/project'};
  // Normal red-test iterations: failing run, repair, passing run.
  step('exec', tests, {content:[{type:'text',text:'FAILED (failures=1)'}], details:{status:'completed',exitCode:1}, isError:true});
  step('exec', tests, passingTests);
  for (const args of evidence) {
    assert.equal(step('process', args, {details:{status:'failed'}}).decision?.blockReason, PHANTOM_PROCESS_REASON);
  }
  assert.equal(exhausted(), false);
});

test('an exec in flight keeps process calls on the core path until its receipt is bound', () => {
  const {before, finish} = harness();
  const exec = before('exec', {command:'python3 -m unittest', workdir:'/workspace/project'}, 'exec-1');
  assert.notEqual(exec.decision?.block, true);
  // A sibling call in the same model round cannot know the exec's outcome yet.
  assert.notEqual(before('process', {action:'poll'}, 'sibling').decision?.block, true);
  finish(exec, 'exec', passingTests);
  assert.equal(before('process', {action:'poll'}, 'later').decision?.blockReason, PHANTOM_PROCESS_REASON);
});

test('a background session from an earlier run of the conversation stays real', () => {
  const guard = createToolLoopGuard();
  const first = harness({guard, runId:'first'});
  first.step('exec', {command:'npm run dev', workdir:'/workspace/site', background:true}, running('dev-server'));
  const later = harness({guard, runId:'later'});
  for (const args of [{action:'list'}, {action:'log', sessionId:'dev-server'}]) {
    assert.notEqual(later.before('process', args, 'inspect').decision?.block, true, JSON.stringify(args));
  }
  // Another conversation cannot see that process and gets the direct answer.
  const other = harness({guard, runId:'other', sessionId:'other-session', sessionKey:'other-key'});
  assert.equal(other.before('process', {action:'list'}, 'list').decision?.blockReason, PHANTOM_PROCESS_REASON);
});

test('ODS-internal and nested calls keep their own accounting', () => {
  const {guard, context, step, exhausted} = harness();
  step('exec', {command:'python3 -m unittest', workdir:'/workspace/project'}, passingTests);
  const direct = id => guard.beforeToolCall({toolName:'process', params:{action:'poll', sessionId:'bundle-session'}, toolCallId:id},
    {...context, toolName:'process', toolCallId:id});
  // Workspace bundle settlement polls its own SDK session; never answer it as a phantom.
  assert.notEqual(direct('ods-bundle-poll-bundle-0')?.block, true);
  // A nested Tool Search execution is answered, but charged through its outer
  // tool_call receipt, so it does not consume the free allowance.
  assert.equal(direct('tool_search_code:outer:process:1')?.blockReason, PHANTOM_PROCESS_REASON);
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
    step('read', {path:`project/missing-${i}.py`}, missingFile);
  }
  for (let i = 0; i < FREE_CORRECTIONS_PER_KIND; i++) step('process', {action:'poll'}, {details:{status:'failed'}});
  assert.equal(exhausted(), false);
});
