import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';

// When the tool limit stops a response, the call that tripped it ran: its own
// output stays ahead of the answer instruction, so the answer reports what
// really happened. On the laptop (2026-10-31) the hidden output was a node
// --check SyntaxError from a patch that doubled chars.js, and the answer told
// the owner the file passed node --check. A call refused after the stop never
// ran, so its result is only the instruction.
const prompt = 'Fix the enemy drawing in Playground/game.';
const SYNTAX_ERROR = "/workspace/Playground/game/chars.js:3255\n    ax: ax - x0, ay: ay - y0,\n      ^\n\nSyntaxError: Unexpected token ':'";

function harness({wrapped = false} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true});
  const context = {agentId: 'pixel', runId: 'run', sessionId: 'owner-session', sessionKey: 'owner-key'};
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  function step(name, args, inner) {
    const id = `${name}-${++sequence}`;
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    if (decision?.block) {
      // The runtime persists a refused call as an error carrying the reason.
      const message = {role: 'toolResult', toolName, toolCallId: id, isError: true, content: [{type: 'text', text: decision.blockReason}]};
      return {decision, persisted: guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message};
    }
    const envelope = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result: inner};
    const result = wrapped ? {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope, isError: inner.isError} : inner;
    guard.afterToolCall({toolName, params: wrapped ? decision?.params ?? params : {...params, ...decision?.params},
      toolCallId: id, result, ...(inner.isError ? {error: inner.content[0].text} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError: inner.isError === true, ...structuredClone(result)};
    return {decision, persisted: guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message};
  }
  return {guard, step};
}
const texts = message => message.content.filter(block => block.type === 'text').map(block => block.text);

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: the call that trips the limit keeps its output ahead of the answer instruction`, () => {
    const {step} = harness({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
      step('read', {path: `Playground/game/missing-${i}.js`}, {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}});
    }
    const tripping = step('exec', {command: 'cd /workspace/Playground/game && python3 patch_chars.py && node --check chars.js'},
      {isError: true, content: [{type: 'text', text: `${SYNTAX_ERROR}\n\n(Command exited with code 1)`}],
        details: {status: 'completed', exitCode: 1, aggregated: SYNTAX_ERROR}});
    const kept = texts(tripping.persisted);
    assert.ok(kept.some(text => text.includes("SyntaxError: Unexpected token ':'")), JSON.stringify(kept).slice(0, 300));
    assert.equal(kept.at(-1), PROGRESS_FINALIZATION_INSTRUCTION);
    // A call made after the stop is refused and never runs: only the instruction.
    const refused = step('exec', {command: 'cd /workspace/Playground/game && node --check chars.js'},
      {content: [{type: 'text', text: 'unreachable'}], details: {status: 'completed', exitCode: 0}});
    assert.equal(refused.decision?.block, true);
    assert.deepEqual(texts(refused.persisted), [PROGRESS_FINALIZATION_INSTRUCTION]);
  });
}
