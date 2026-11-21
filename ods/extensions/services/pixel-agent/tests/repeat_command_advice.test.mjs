import test from 'node:test';
import assert from 'node:assert/strict';
import {CODING_REPEAT_NO_PROGRESS_REASON, createToolLoopGuard,
  FREE_REPEAT_COMMANDS_PER_RUN} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';

// A read-only command that already succeeded twice is refused a third time.
// On the laptop (2026-10-30, a 227-call elf-of-doom run) the model asked again
// because its earlier reads of ui.js had left the prompt, and four of the
// thirteen failures that stopped the run were these refusals. Nothing ran, so
// the refusal is advice, within FREE_REPEAT_COMMANDS_PER_RUN. Each case replays
// OpenClaw's hook sequence for one model round and one call, in both the direct
// and the Tool Search transport.
const STOPPED = [PROGRESS_FINALIZATION_INSTRUCTION, RUN_PROGRESS_STOP_REASON];
const prompt = 'In elf-of-doom, big fights lag. Make normal enemies draw from a sprite cache.';
const completed = (exitCode, output = '') => ({
  ...(exitCode ? {isError:true} : {}),
  content:[{type:'text', text:`${output}${exitCode ? `\n\n(Command exited with code ${exitCode})` : ''}`}],
  details:{status:'completed', exitCode, durationMs:4},
});
const PRINTED = completed(0, '1:// Elf of Doom — AI, updates, rendering, UI wiring');
const MISSING = completed(2, "sed: can't read game.js: No such file or directory");

function harness({wrapped = false} = {}) {
  const guard = createToolLoopGuard({execControl:{prepare:(_run, command) => command, signal:() => true}});
  const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key'};
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  function exec(command, inner) {
    const id = `exec-${++sequence}`;
    guard.observeModelCall({}, context);
    const args = {command, workdir:'/workspace/Playground/elf-of-doom'};
    const toolName = wrapped ? 'tool_call' : 'exec';
    const params = wrapped ? {id:'openclaw:core:exec', args} : args;
    const ctx = {...context, toolName, toolCallId:id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId:id}, ctx);
    let result, executed = params;
    if (decision?.block) {
      result = {content:[{type:'text', text:decision.blockReason}],
        details:{status:'blocked', deniedReason:'plugin-before-tool-call', reason:decision.blockReason}};
    } else {
      executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
      if (wrapped) {
        // Tool Search runs the core exec as a nested child with its own hooks.
        const childId = `tool_search_code:${id}:exec:1`, childCtx = {...context, toolName:'exec', toolCallId:childId};
        const child = guard.beforeToolCall({toolName:'exec', params:executed.args, toolCallId:childId}, childCtx);
        assert.notEqual(child?.block, true, child?.blockReason);
        guard.afterToolCall({toolName:'exec', params:child?.params ?? executed.args, toolCallId:childId, result:inner}, childCtx);
      }
      const envelope = {tool:{id:'openclaw:core:exec', name:'exec', source:'openclaw', sourceName:'core'}, result:inner};
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
  // Asks for the same `sed -n 'a,bp' ui.js` until it is refused: the third ask
  // in the direct transport, the second in Tool Search, which also counts the
  // nested child's receipt as a run (as on main).
  const repeat = n => {
    const command = `sed -n '${n * 100 + 1},${n * 100 + 100}p' ui.js`;
    for (let i = 0; i < 3; i++) {
      const decision = exec(command, PRINTED);
      if (decision?.block) return decision;
    }
    assert.fail(`never refused: ${command}`);
  };
  return {exec, exhausted, repeat};
}

test('the refusal explains why the output may be gone and how to go on', () => {
  assert.match(CODING_REPEAT_NO_PROGRESS_REASON, /without a write, edit, or apply_patch since, so Portal did not run it again/);
  assert.match(CODING_REPEAT_NO_PROGRESS_REASON, /in a long response Portal replaces older tool output with a short notice/);
  assert.match(CODING_REPEAT_NO_PROGRESS_REASON, /keep the facts you still need .* in the explanation of update_plan/);
  assert.match(CODING_REPEAT_NO_PROGRESS_REASON, /write, edit, or apply_patch, or give the owner a visible blocker/);
});

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: a refused repeat read is advice, not a failure`, () => {
    const run = harness({wrapped});
    assert.deepEqual(run.repeat(0), {block:true, blockReason:CODING_REPEAT_NO_PROGRESS_REASON});
    // Charged, the refusal and three failures would make four in a row.
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) run.exec(`sed -n 1,20p game-${i}.js`, MISSING);
    assert.equal(run.exhausted(), false);
    run.exec('sed -n 1,20p game-last.js', MISSING);
    assert.equal(run.exhausted(), true);
  });

  test(`${transport}: the allowance is bounded per run`, () => {
    const run = harness({wrapped});
    for (let n = 0; n < FREE_REPEAT_COMMANDS_PER_RUN; n++) {
      assert.equal(run.repeat(n).blockReason, CODING_REPEAT_NO_PROGRESS_REASON);
      assert.equal(run.exhausted(), false);
    }
    // Past the allowance a refusal is charged again: with three failures it stops the run.
    assert.equal(run.repeat(FREE_REPEAT_COMMANDS_PER_RUN).blockReason, CODING_REPEAT_NO_PROGRESS_REASON);
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) run.exec(`sed -n 1,20p game-${i}.js`, MISSING);
    assert.equal(run.exhausted(), true);
  });
}
