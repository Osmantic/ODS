import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, FREE_EMPTY_LOOKUPS_PER_RUN} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';

// A search that finds nothing is an answer. On the RTX Spark (Elf of Doom,
// 2026-10-16) greps with no match, charged as failures, filled the 12-failure
// total and stopped a run with 21 good edits behind it. Each case replays
// OpenClaw's hook sequence for one model round and one call, in both the
// direct and the Tool Search transport.
const STOPPED = [PROGRESS_FINALIZATION_INSTRUCTION, RUN_PROGRESS_STOP_REASON];
const prompt = 'Fix the bugs in the game in Playground/elf-of-doom.';

// OpenClaw's receipt for a completed command: merged output plus its exit note.
const completed = (exitCode, output = '') => ({
  ...(exitCode ? {isError:true} : {}),
  content:[{type:'text', text:`${output}${exitCode ? `\n\n(Command exited with code ${exitCode})` : ''}`}],
  details:{status:'completed', exitCode, durationMs:4},
});
const NO_MATCH = completed(1);
const edited = {content:[{type:'text', text:'Successfully replaced 1 block(s) in Playground/elf-of-doom/ui.js.'}], details:{}};

function harness({wrapped = false} = {}) {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key'};
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  function step(name, args, inner, {executedParams} = {}) {
    const id = `${name}-${++sequence}`;
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id:`openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId:id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId:id}, ctx);
    let result, executed = params;
    if (decision?.block) {
      result = {content:[{type:'text', text:decision.blockReason}],
        details:{status:'blocked', deniedReason:'plugin-before-tool-call', reason:decision.blockReason}};
    } else {
      executed = executedParams ?? (wrapped ? decision?.params ?? params : {...params, ...decision?.params});
      const envelope = {tool:{id:`openclaw:core:${name}`, name, source:'openclaw', sourceName:'core'}, result:inner};
      result = wrapped ? {content:[{type:'text', text:JSON.stringify(envelope)}], details:envelope} : inner;
    }
    const isError = decision?.block === true || inner?.isError === true;
    guard.afterToolCall({toolName, params:executed, toolCallId:id, result,
      ...(isError ? {error:result.content[0].text} : {})}, ctx);
    const message = {role:'toolResult', toolName, toolCallId:id, isError, ...structuredClone(result)};
    guard.toolResultPersist({toolName, toolCallId:id, message}, ctx);
    return decision;
  }
  const exhausted = () => STOPPED.includes(guard.beforeToolCall({toolName:'read', params:{path:'README.md'}, toolCallId:'probe'},
    {...context, toolName:'read', toolCallId:'probe'})?.blockReason);
  const grep = (command, inner = NO_MATCH, options) => step('exec', {command}, inner, options);
  const edit = n => step('edit', {path:'Playground/elf-of-doom/ui.js', oldText:`a${n}`, newText:`b${n}`}, edited);
  return {grep, edit, step, exhausted};
}

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: empty searches between edits no longer stop a working run`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < FREE_EMPTY_LOOKUPS_PER_RUN; i++) {
      run.edit(i);
      // The shapes Portal's agent used on the Spark.
      run.grep(i % 2 ? `cd Playground/elf-of-doom && grep -n "function drawEnemyEnt${i}" -A 12 chars.js`
        : `rg -n "hitSet${i}" Playground/elf-of-doom`);
    }
    assert.ok(FREE_EMPTY_LOOKUPS_PER_RUN >= RUN_PROGRESS_LIMITS.totalFailures);
    assert.equal(run.exhausted(), false);
  });

  test(`${transport}: chained searches whose last search came up empty are answers too`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.totalFailures; i++) {
      run.edit(i);
      run.grep(`cd Playground/elf-of-doom && sed -n '860,900p' ui.js; echo ===; grep -n "function showScreen${i}" -A 15 ui.js`,
        completed(1, '  updateHud();\n==='));
    }
    assert.equal(run.exhausted(), false);
  });

  test(`${transport}: a chain that printed an error is charged`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(run.exhausted(), false);
      run.grep(`grep -n "x${i}" missing.js; grep -n "y" ui.js`, completed(1, 'grep: missing.js: No such file or directory'));
    }
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: an earlier grep with suppressed errors is charged`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(run.exhausted(), false);
      run.grep(`grep -ns x missing${i}.js; grep -n z ui.js`, NO_MATCH);
    }
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: the empty-search allowance is bounded per run`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < FREE_EMPTY_LOOKUPS_PER_RUN; i++) { run.edit(i); run.grep(`grep -rn "name${i}" Playground/elf-of-doom`); }
    assert.equal(run.exhausted(), false);
    // Past the allowance an empty search is an ordinary failure again.
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(run.exhausted(), false);
      run.grep(`grep -rn "beyond${i}" Playground/elf-of-doom`);
    }
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: real errors and compound commands are still charged and still trip the fuse`, () => {
    const run = harness({wrapped});
    run.grep('grep -n "x" missing.js', completed(2, 'grep: missing.js: No such file or directory'));
    run.grep('cd missing && grep -n "x" chars.js', completed(1, 'sh: 1: cd: can\'t cd to missing'));
    run.grep('grep -n "x" chars.js | head -3');
    assert.equal(run.exhausted(), false);
    run.grep('grep -n "x" chars.js; echo done', completed(1, 'done'));
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: an empty search never erases earlier failures`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
      run.step('exec', {command:`python3 build${i}.py`}, completed(2, 'Traceback (most recent call last): boom'));
    }
    run.grep('grep -n "renderWorld" ui.js');
    assert.equal(run.exhausted(), false);
    run.step('exec', {command:'python3 build.py'}, completed(2, 'Traceback (most recent call last): boom'));
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: a run that only searches still stops on the rounds fuse`, () => {
    const run = harness({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.roundsWithoutProgress; i++) {
      run.grep(`grep -rn "missing${i}" Playground`);
      assert.equal(run.exhausted(), false);
    }
    run.grep('grep -rn "one more" Playground');
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: a receipt that does not match the recorded call is charged`, () => {
    const run = harness({wrapped});
    const forged = wrapped ? {id:'openclaw:core:exec', args:{command:'grep -n "other" ui.js'}} : {command:'grep -n "other" ui.js'};
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(run.exhausted(), false);
      run.grep(`grep -n "name${i}" ui.js`, NO_MATCH, {executedParams:forged});
    }
    assert.equal(run.exhausted(), true);
  });
}
