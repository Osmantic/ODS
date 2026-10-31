import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, STOPPED_OUTPUT_EXCERPT_CHARS, STOPPED_RESULT_TEXT_LIMIT,
  stoppedOutputExcerpt} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PREVIEW_CHECK_FINALIZATION_INSTRUCTION, PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';
import {EXAMPLE_NOTES} from '../plugin/example-guard.mjs';
import {sandboxHostWorkspaceFailure} from '../plugin/workspace-path-contract.mjs';

// When the tool limit stops a response, a call that ran keeps an excerpt of
// its own output ahead of the answer instruction, so the answer reports what
// really happened. On the laptop (2026-10-31) the hidden output was a node
// --check SyntaxError from a patch that doubled chars.js, and the answer told
// the owner the file passed node --check. A refused call never ran, whether
// it was refused after the stop, before it (its refusal tripping the stop),
// or by another plugin layer: its result is only the instruction, as before.
// The excerpt is sized so the whole result fits the smallest per-result cap
// ODS installs, so the instruction always reaches the model whole.
const prompt = 'Fix the enemy drawing in Playground/game.';
const SYNTAX_ERROR = "/workspace/Playground/game/chars.js:3255\n    ax: ax - x0, ay: ay - y0,\n      ^\n\nSyntaxError: Unexpected token ':'";
const EXITED = '\n\n(Command exited with code 1)';
const ROOT = '/home/owner/ods-workspace';

// OpenClaw 2026.6.33 as Pixel's hooks see a result:
// - after_tool_call gets each top-level text block cut to 8,000 characters
//   (embedded-agent-subscribe.tools truncateToolText); details stay whole.
const live = result => ({...result, content: (result.content ?? []).map(block => block?.type === 'text' && block.text.length > 8000
  ? {...block, text: `${block.text.slice(0, 8000)}\n…(truncated)…`} : block)});
// - tool_result_persist gets the copy already cut to toolResultMaxChars (16k
//   here; session-tool-result-guard keeps the head of a block whose tail it
//   does not consider important).
const CAP = 16000;
const capped = result => ({...result, content: (result.content ?? []).map(block => block?.type === 'text' && block.text.length > CAP
  ? {...block, text: `${block.text.slice(0, CAP - 90)}[... ${block.text.length - CAP + 90} more characters truncated; rerun with narrower args if needed]`}
  : block)});
// - a call a before_tool_call hook refuses gets this receipt, which reaches
//   after_tool_call and tool_result_persist (buildBlockedToolResult).
const veto = reason => ({content: [{type: 'text', text: reason}],
  details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason}});
// - Tool Search returns the selected tool's result in an envelope with its
//   catalog entry, description included (jsonResult: JSON.stringify(_, null, 2)).
const DESCRIPTION = 'Run a shell command in the workspace and return its combined output. '.repeat(90);
const envelope = (name, result) => ({tool: {id: `openclaw:core:${name}`, source: 'openclaw', sourceName: 'core', name,
  label: name, description: DESCRIPTION}, result});

// `receipt: 'error'` models a runtime whose refusal receipt is a plain error,
// so only the guard's own record of its refusal keeps that output out.
function harness({wrapped = false, workspaceRoot, executionHost, exampleGuard, limits, ownerPrompt = prompt, receipt = 'veto'} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true, exampleGuard, limits});
  const context = {agentId: 'pixel', runId: 'run', sessionId: 'owner-session', sessionKey: 'owner-key'};
  guard.observeRun(context, 'pixel', {prompt: ownerPrompt}, {workspaceRoot, executionHost});
  let sequence = 0;
  // before_tool_call, in a new model round unless `round` is false.
  function begin(name, args, {round = true} = {}) {
    if (round) guard.observeModelCall({}, context);
    const id = `${name}-${++sequence}`;
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId: id};
    return {name, id, toolName, params, ctx, decision: guard.beforeToolCall({toolName, params, toolCallId: id}, ctx)};
  }
  // The runtime's result for the call, then after_tool_call and
  // tool_result_persist. A call this guard refused gets the `receipt`;
  // `vetoedBy` is another plugin layer refusing a call this guard admitted (in
  // Tool Search, the selected tool's own call), which gets the veto receipt.
  function finish({name, id, toolName, params, ctx, decision}, inner, {vetoedBy} = {}) {
    let result;
    if (decision?.block) result = receipt === 'veto' ? veto(decision.blockReason)
      : {isError: true, content: [{type: 'text', text: decision.blockReason}]};
    else if (vetoedBy !== undefined && !wrapped) result = veto(vetoedBy);
    else {
      const selected = vetoedBy !== undefined ? veto(vetoedBy) : inner;
      const wrappedResult = envelope(name, selected);
      result = wrapped ? {content: [{type: 'text', text: JSON.stringify(wrappedResult, null, 2)}], details: wrappedResult,
        ...(selected.isError ? {isError: true} : {})} : inner;
    }
    const failed = decision?.block === true || vetoedBy !== undefined || result.isError === true;
    guard.afterToolCall({toolName, params: decision?.block || wrapped ? params : {...params, ...decision?.params},
      toolCallId: id, result: live(result), ...(failed ? {error: result.content[0].text.slice(0, 400)} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError: failed, ...structuredClone(capped(result))};
    return {decision, result, persisted: guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message};
  }
  const step = (name, args, inner, options) => finish(begin(name, args), inner, options);
  const failReads = (count = RUN_PROGRESS_LIMITS.consecutiveFailures - 1) => {
    for (let i = 0; i < count; i++) {
      step('read', {path: `Playground/game/missing-${i}.js`}, {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}});
    }
  };
  return {guard, begin, finish, step, failReads};
}
const texts = message => message.content.filter(block => block.type === 'text').map(block => block.text);
const total = message => texts(message).reduce((sum, text) => sum + text.length, 0);
const execResult = output => ({isError: true, content: [{type: 'text', text: output}],
  details: {status: 'completed', exitCode: 1, aggregated: output}});
const PATCH_AND_CHECK = {command: 'cd /workspace/Playground/game && python3 patch_chars.py && node --check chars.js'};

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: the call that trips the limit keeps its whole short output ahead of the answer instruction`, () => {
    const {step, failReads} = harness({wrapped});
    failReads();
    const output = `${SYNTAX_ERROR}${EXITED}`;
    const tripping = step('exec', PATCH_AND_CHECK, execResult(output));
    // Its own output (in Tool Search, the selected tool's result, never the
    // envelope with its long catalog description), then the instruction.
    assert.deepEqual(texts(tripping.persisted), [output, PROGRESS_FINALIZATION_INSTRUCTION]);
    // A call made after the stop is refused and never runs: only the instruction.
    const refused = step('exec', {command: 'cd /workspace/Playground/game && node --check chars.js'},
      {content: [{type: 'text', text: 'unreachable'}], details: {status: 'completed', exitCode: 0}});
    assert.equal(refused.decision?.block, true);
    assert.deepEqual(texts(refused.persisted), [PROGRESS_FINALIZATION_INSTRUCTION]);
  });

  test(`${transport}: an over-cap output keeps its head and its tail error within the stopped-result limit`, () => {
    const {step, failReads} = harness({wrapped});
    failReads();
    const head = 'patch_chars.py: rewriting drawEnemy in chars.js (pass 1 of 2)\n';
    const body = Array.from({length: 260}, (_, i) => `  drawEnemy frame ${String(i).padStart(3, '0')}: ` + 'x'.repeat(60)).join('\n');
    const output = `${head}${body}\n${SYNTAX_ERROR}${EXITED}`;
    assert.ok(output.length > 20000);
    const tripping = step('exec', PATCH_AND_CHECK, execResult(output));
    // Both copies a cap leaves to the hooks lost the error at the end: the
    // excerpt's tail comes from after_tool_call's whole output.
    assert.ok(!capped(tripping.result).content[0].text.includes('SyntaxError'));
    assert.ok(!live(tripping.result).content[0].text.includes('SyntaxError'));
    const kept = texts(tripping.persisted);
    assert.equal(kept.length, 2);
    assert.equal(kept.at(-1), PROGRESS_FINALIZATION_INSTRUCTION, 'the instruction is whole and last');
    assert.ok(total(tripping.persisted) <= STOPPED_RESULT_TEXT_LIMIT, `${total(tripping.persisted)} chars`);
    assert.ok(kept[0].startsWith(head), kept[0].slice(0, 120));
    assert.ok(kept[0].endsWith(`SyntaxError: Unexpected token ':'${EXITED}`), kept[0].slice(-200));
    assert.match(kept[0], /\n\[\.\.\. \d+ characters of this output omitted \.\.\.\]\n/);
    assert.doesNotMatch(kept.join('\n'), /rerun|narrower args|Run a shell command|"sourceName"/);
  });

  for (const receipt of ['veto', 'error']) test(`${transport}: a refusal that itself trips the limit keeps only the instruction (${receipt} receipt)`, () => {
    const {step, failReads} = harness({wrapped, receipt});
    failReads();
    // edit cannot create a file: refused after the call was recorded, and the
    // refusal is the fourth failure in a row.
    const refused = step('edit', {path: 'Playground/game/enemy.js', edits: [{oldText: '', newText: 'export const enemy = 1;\n'}]});
    assert.equal(refused.decision?.block, true);
    assert.match(refused.decision.blockReason, /^edit cannot create a new file/);
    assert.deepEqual(texts(refused.persisted), [PROGRESS_FINALIZATION_INSTRUCTION]);
  });

  test(`${transport}: a call another plugin layer refuses keeps only the instruction`, () => {
    const {step, failReads} = harness({wrapped});
    failReads();
    const vetoed = step('exec', PATCH_AND_CHECK, execResult('unreachable'),
      {vetoedBy: 'Cron command payloads cannot run shell commands.'});
    assert.notEqual(vetoed.decision?.block, true, 'this guard admitted it');
    assert.deepEqual(texts(vetoed.persisted), [PROGRESS_FINALIZATION_INSTRUCTION]);
  });
}

for (const receipt of ['veto', 'error']) test(`the research web-loop stop, a refusal of a recorded call, keeps only the instruction (${receipt} receipt)`, () => {
  const {step} = harness({limits: {search: 1, fetch: 4, total: 5}, receipt,
    ownerPrompt: 'Research current RX 9070 prices and cite sources.'});
  const search = query => step('web_search', {query},
    {content: [{type: 'text', text: JSON.stringify({query, results: []})}], details: {status: 'ok'}});
  assert.equal(search('rx 9070 price a').decision?.block, undefined);
  assert.equal(search('rx 9070 price b').decision?.block, true);
  assert.equal(search('rx 9070 price c').decision?.block, true);
  const stopped = search('rx 9070 price d');
  assert.equal(stopped.decision?.blockReason, PROGRESS_FINALIZATION_INSTRUCTION);
  assert.deepEqual(texts(stopped.persisted), [PROGRESS_FINALIZATION_INSTRUCTION], 'the instruction once, as on main');
});

test('parallel siblings that ran before the stop keep their own output too', () => {
  const {begin, finish, step, failReads} = harness();
  failReads();
  // One model round with three calls, all admitted before any result.
  const tripping = begin('exec', PATCH_AND_CHECK);
  const sibling = begin('read', {path: 'Playground/game/enemy.js'}, {round: false});
  const large = begin('read', {path: 'Playground/game/chars.js'}, {round: false});
  const trippingOutput = `${SYNTAX_ERROR}${EXITED}`;
  assert.deepEqual(texts(finish(tripping, execResult(trippingOutput)).persisted), [trippingOutput, PROGRESS_FINALIZATION_INSTRUCTION]);
  assert.deepEqual(texts(finish(sibling, {content: [{type: 'text', text: 'const enemies = [];'}]}).persisted),
    ['const enemies = [];', PROGRESS_FINALIZATION_INSTRUCTION]);
  // A read longer than after_tool_call's 8,000-character copy, without a whole
  // copy in its details: the excerpt says the rest is missing, never "rerun".
  const file = Array.from({length: 400}, (_, i) => `const sprite${i} = bake(${i});`).join('\n');
  assert.ok(file.length > 9000);
  const kept = finish(large, {content: [{type: 'text', text: file}]}).persisted;
  assert.equal(texts(kept).at(-1), PROGRESS_FINALIZATION_INSTRUCTION);
  assert.ok(texts(kept)[0].startsWith('const sprite0 = bake(0);'));
  assert.match(texts(kept)[0], /\[\.\.\. the rest of this text omitted \.\.\.\]$/);
  assert.ok(total(kept) <= STOPPED_RESULT_TEXT_LIMIT);
  assert.doesNotMatch(texts(kept).join('\n'), /rerun|…\(truncated\)…/);
  assert.deepEqual(texts(step('read', {path: 'Playground/game/ui.js'}, {content: [{type: 'text', text: 'unreachable'}]}).persisted),
    [PROGRESS_FINALIZATION_INSTRUCTION]);
});

test('the notes bound to the kept output stay between it and the instruction, as an unstopped result carries them', () => {
  // ODS put the shipped example back after the call, and the call used the
  // host workspace path inside the sandbox.
  let restoreNext = false;
  const exampleGuard = {begin: () => undefined, guarding: () => false, end: () => undefined,
    check: () => restoreNext ? (restoreNext = false, {outcome: 'restored'}) : undefined};
  const {step, failReads} = harness({workspaceRoot: ROOT, executionHost: 'sandbox', exampleGuard});
  const output = `sh: 1: cd: can't cd to ${ROOT}/Playground/game${EXITED}`;
  const command = script => ({command: `cd ${ROOT}/Playground/game && python3 ${script}`});
  const correction = sandboxHostWorkspaceFailure(command('patch_a.py'), execResult(output), ROOT, 'sandbox');
  assert.match(correction, /^\[ODS Pixel path correction\]/);
  restoreNext = true;
  const unstopped = texts(step('exec', command('patch_a.py'), execResult(output)).persisted);
  assert.ok(unstopped.includes(correction) && unstopped.includes(EXAMPLE_NOTES.restored.model), JSON.stringify(unstopped));
  failReads(RUN_PROGRESS_LIMITS.consecutiveFailures - 2);
  restoreNext = true;
  const stopped = step('exec', command('patch_b.py'), execResult(output)).persisted;
  assert.deepEqual(texts(stopped), [output, correction, EXAMPLE_NOTES.restored.model, PROGRESS_FINALIZATION_INSTRUCTION]);
});

test('the excerpt, the longest notes and the longest stop text fit the smallest per-result cap', () => {
  assert.equal(STOPPED_RESULT_TEXT_LIMIT, 4000 - 100);
  const output = `sh: 1: cd: can't cd to ${ROOT}/x${EXITED}`;
  const correction = sandboxHostWorkspaceFailure({command: `cd ${ROOT}/x`}, execResult(output), ROOT, 'sandbox');
  const note = Math.max(EXAMPLE_NOTES.restored.model.length, EXAMPLE_NOTES.failed.model.length);
  const stop = Math.max(PROGRESS_FINALIZATION_INSTRUCTION.length, PREVIEW_CHECK_FINALIZATION_INSTRUCTION.length,
    RUN_PROGRESS_STOP_REASON.length);
  assert.ok(STOPPED_OUTPUT_EXCERPT_CHARS + correction.length + note + stop <= STOPPED_RESULT_TEXT_LIMIT,
    `${STOPPED_OUTPUT_EXCERPT_CHARS} + ${correction.length} + ${note} + ${stop}`);
  // Never over its limit, short text unchanged, no surrogate pair split.
  const lone = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
  // Odd and even prefixes put each cut on either half of a surrogate pair.
  const pairs = ['', 'a', 'ab', 'abc'].map(prefix => `${prefix}${'\u{1F600}'.repeat(3000)}`);
  for (const text of ['', 'ok', 'x'.repeat(STOPPED_OUTPUT_EXCERPT_CHARS), 'y\n'.repeat(1201), 'z'.repeat(250000),
    ...pairs, ('line \u{1F47E}\n').repeat(900)]) {
    const excerpt = stoppedOutputExcerpt(text);
    assert.ok(excerpt.length <= STOPPED_OUTPUT_EXCERPT_CHARS, `${text.length} -> ${excerpt.length}`);
    if (text.length <= STOPPED_OUTPUT_EXCERPT_CHARS) assert.equal(excerpt, text);
    else assert.ok(text.startsWith(excerpt.slice(0, 40)) && text.endsWith(excerpt.slice(-40)));
    assert.doesNotMatch(excerpt, lone);
  }
});
