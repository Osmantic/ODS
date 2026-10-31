import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, STOPPED_BATCH_EXCERPT_CHARS, STOPPED_RESULT_TEXT_LIMIT, STOPPED_SUCCESS_EXCERPT_CHARS,
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
// The result fits the agent's per-result cap, so the instruction always
// reaches the model whole.
const prompt = 'Fix the enemy drawing in Playground/game.';
const I = PROGRESS_FINALIZATION_INSTRUCTION;
const SYNTAX_ERROR = "/workspace/Playground/game/chars.js:3255\n    ax: ax - x0, ay: ay - y0,\n      ^\n\nSyntaxError: Unexpected token ':'";
const EXITED = '\n\n(Command exited with code 1)';
const ROOT = '/home/owner/ods-workspace';
const TIMEOUT_REASON = 'Command timed out after 120 seconds. If this command is expected to take longer, re-run with a higher ' +
  'timeout (e.g., exec timeout=300). If it should keep running, start it with exec background=true or yieldMs so OpenClaw ' +
  'can register a pollable process session. Do not rely on shell backgrounding with a trailing &.';

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
//   catalog entry, description included (jsonResult: JSON.stringify(_, null,
//   2), no isError of its own).
const DESCRIPTION = 'Run a shell command in the workspace and return its combined output. '.repeat(90);
const envelope = (name, result, source = 'openclaw:core') => ({tool: {id: `${source}:${name}`, source: source.split(':')[0],
  sourceName: source.split(':')[1], name, label: name, description: DESCRIPTION}, result});

// One guard as the runtime drives it. `receipt: 'error'` models a runtime whose
// refusal receipt is a plain error, so only the guard's own record of its
// refusal keeps that output out. Calls go through before_tool_call (begin),
// after_tool_call (after) and tool_result_persist (persist), so parallel calls
// can run in the runtime's order: every before hook, after hooks as the calls
// finish, then every persist in call order.
function harness({wrapped = false, source, workspaceRoot, executionHost, exampleGuard, limits, ownerPrompt = prompt,
  receipt = 'veto', toolResultMaxChars} = {}) {
  const guard = createToolLoopGuard({abortRun: () => true, exampleGuard, limits, toolResultMaxChars});
  const context = {agentId: 'pixel', runId: 'run', sessionId: 'owner-session', sessionKey: 'owner-key'};
  guard.observeRun(context, 'pixel', {prompt: ownerPrompt}, {workspaceRoot, executionHost});
  let sequence = 0;
  function begin(name, args, {round = true, id = `${name}-${++sequence}`, direct = !wrapped} = {}) {
    if (round) guard.observeModelCall({}, context);
    const toolName = direct ? name : 'tool_call';
    const params = direct ? args : {id: `${source ?? 'openclaw:core'}:${name}`, args};
    const ctx = {...context, toolName, toolCallId: id};
    return {name, id, toolName, params, ctx, direct, decision: guard.beforeToolCall({toolName, params, toolCallId: id}, ctx)};
  }
  // The runtime's result: a call this guard refused gets the `receipt`;
  // `refusedBy` is another layer's result for a call this guard admitted (in
  // Tool Search, the selected tool's own call).
  function after(call, inner, {refusedBy} = {}) {
    const {name, id, toolName, params, ctx, decision, direct} = call;
    if (decision?.block) call.result = receipt === 'veto' ? veto(decision.blockReason)
      : {isError: true, content: [{type: 'text', text: decision.blockReason}]};
    else if (refusedBy && direct) call.result = refusedBy;
    else {
      const selected = refusedBy ?? inner;
      const wrappedResult = envelope(name, selected, source);
      call.result = direct ? inner : {content: [{type: 'text', text: JSON.stringify(wrappedResult, null, 2)}], details: wrappedResult};
    }
    call.failed = decision?.block === true || call.result.isError === true || ['blocked', 'error'].includes(call.result.details?.status);
    guard.afterToolCall({toolName, params: decision?.block || !direct ? params : {...params, ...decision?.params},
      toolCallId: id, result: live(call.result), ...(call.failed ? {error: call.result.content[0].text.slice(0, 400)} : {})}, ctx);
    return call;
  }
  function persist(call) {
    const message = {role: 'toolResult', toolName: call.toolName, toolCallId: call.id, isError: call.failed,
      ...structuredClone(capped(call.result))};
    call.persisted = guard.toolResultPersist({toolName: call.toolName, toolCallId: call.id, message}, call.ctx)?.message ?? message;
    return call;
  }
  const step = (name, args, inner, options) => persist(after(begin(name, args), inner, options));
  const failReads = (count = RUN_PROGRESS_LIMITS.consecutiveFailures - 1) => {
    for (let i = 0; i < count; i++) {
      step('read', {path: `Playground/game/missing-${i}.js`}, {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}});
    }
  };
  return {guard, begin, after, persist, step, failReads};
}
const texts = message => message.content.filter(block => block.type === 'text').map(block => block.text);
const total = message => texts(message).reduce((sum, text) => sum + text.length, 0);
const execResult = (output, details = {}) => ({isError: true, content: [{type: 'text', text: output}],
  details: {status: 'completed', exitCode: 1, aggregated: output, ...details}});
const timedOut = output => ({isError: true, content: [{type: 'text', text: `${output}\n\n${TIMEOUT_REASON}`}],
  details: {status: 'failed', exitCode: null, durationMs: 120000, aggregated: output, timedOut: true, cwd: '/workspace'}});
const PATCH_AND_CHECK = {command: 'cd /workspace/Playground/game && python3 patch_chars.py && node --check chars.js'};
const progress = count => Array.from({length: count}, (_, i) => `  PASS tests/enemy_${String(i).padStart(3, '0')}.test.js (${i % 7 + 3} ms)`).join('\n');
const OMITTED = /\n\[\.\.\. \d+ characters of this output omitted \.\.\.\]\n/;

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: the call that trips the limit keeps its whole short output ahead of the answer instruction`, () => {
    const {step, failReads} = harness({wrapped});
    failReads();
    const output = `${SYNTAX_ERROR}${EXITED}`;
    // Its own output (in Tool Search, the selected tool's result, never the
    // envelope with its long catalog description), then the instruction.
    assert.deepEqual(texts(step('exec', PATCH_AND_CHECK, execResult(output)).persisted), [output, I]);
    // A call made after the stop is refused and never runs: only the instruction.
    const refused = step('exec', {command: 'cd /workspace/Playground/game && node --check chars.js'},
      {content: [{type: 'text', text: 'unreachable'}], details: {status: 'completed', exitCode: 0}});
    assert.equal(refused.decision?.block, true);
    assert.deepEqual(texts(refused.persisted), [I]);
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
    // excerpt's tail comes from the whole output after_tool_call sees.
    assert.ok(!capped(tripping.result).content[0].text.includes('SyntaxError'));
    assert.ok(!live(tripping.result).content[0].text.includes('SyntaxError'));
    const kept = texts(tripping.persisted);
    assert.equal(kept.length, 2);
    assert.equal(kept.at(-1), I, 'the instruction is whole and last');
    assert.ok(total(tripping.persisted) <= STOPPED_RESULT_TEXT_LIMIT, `${total(tripping.persisted)} chars`);
    assert.ok(kept[0].startsWith(head), kept[0].slice(0, 120));
    assert.ok(kept[0].endsWith(`SyntaxError: Unexpected token ':'${EXITED}`), kept[0].slice(-200));
    assert.match(kept[0], OMITTED);
    assert.doesNotMatch(kept.join('\n'), /rerun|narrower args|Run a shell command|"sourceName"/);
  });

  for (const size of [3000, 12000, 20000]) test(`${transport}: a ${size}-char exec that timed out keeps its failure`, () => {
    const {step, failReads} = harness({wrapped});
    failReads();
    const output = `RUN npm test\n${progress(Math.ceil(size / 40))}`.slice(0, size);
    const kept = texts(step('exec', {command: 'cd /workspace/Playground/game && npm test'}, timedOut(output)).persisted);
    assert.equal(kept.at(-1), I);
    assert.ok(kept[0].startsWith('RUN npm test'));
    // The reason OpenClaw gives, or where both copies lost it, the failure its
    // details state: never a list of passes that reads as success.
    assert.ok(kept[0].endsWith(TIMEOUT_REASON) || (!wrapped && size > CAP && kept[0].endsWith('\n\n(Command timed out.)')),
      kept[0].slice(-160));
  });

  for (const lines of [100, 600]) test(`${transport}: a process poll with ${lines} new lines keeps its own output and the session's state`, () => {
    const {begin, after, persist, step, failReads} = harness({wrapped});
    persist(after(begin('exec', {command: 'cd /workspace/Playground/game && npm test', background: true}),
      {content: [{type: 'text', text: 'Command still running (session sess-1, pid 42). Use process (list/poll/log/write/kill/clear/remove) for follow-up.'}],
        details: {status: 'running', sessionId: 'sess-1', pid: 42, startedAt: 1, cwd: '/workspace/Playground/game', tail: ''}}));
    failReads();
    // The poll's text is its new output and the session's state; details carry
    // the whole session, the earlier output the model already read included.
    const earlier = `EARLIER POLL OUTPUT the model already read\n${progress(200)}\n`;
    const fresh = `${progress(lines)}\nFAIL tests/enemy_draw.test.js\n  TypeError: ctx.drawImage is not a function`;
    const poll = step('process', {action: 'poll', sessionId: 'sess-1'}, {content: [{type: 'text', text: `${fresh}\n\nProcess exited with code 1.`}],
      details: {status: 'failed', sessionId: 'sess-1', exitCode: 1, aggregated: `${earlier}${fresh}`, name: 'npm test'}});
    assert.notEqual(poll.decision?.block, true, poll.decision?.blockReason);
    const kept = texts(poll.persisted);
    assert.equal(kept.at(-1), I);
    assert.ok(kept[0].startsWith('  PASS tests/enemy_000'), 'its own new output, not the session from its start');
    assert.ok(kept[0].endsWith('TypeError: ctx.drawImage is not a function\n\nProcess exited with code 1.'), kept[0].slice(-120));
  });

  for (const receipt of ['veto', 'error']) test(`${transport}: a refusal that itself trips the limit keeps only the instruction (${receipt} receipt)`, () => {
    const {step, failReads} = harness({wrapped, receipt});
    failReads();
    // edit cannot create a file: refused after the call was recorded, and the
    // refusal is the fourth failure in a row.
    const refused = step('edit', {path: 'Playground/game/enemy.js', edits: [{oldText: '', newText: 'export const enemy = 1;\n'}]});
    assert.equal(refused.decision?.block, true);
    assert.match(refused.decision.blockReason, /^edit cannot create a new file/);
    assert.deepEqual(texts(refused.persisted), [I]);
  });

  for (const [label, refusal] of [
    ['a veto', veto('Cron command payloads cannot run shell commands.')],
    ['a failed hook', {content: [{type: 'text', text: JSON.stringify({status: 'error', tool: 'exec',
      error: 'Tool call blocked because before_tool_call hook failed'}, null, 2)}],
    details: {status: 'error', tool: 'exec', error: 'Tool call blocked because before_tool_call hook failed'}}],
  ]) test(`${transport}: a call another plugin layer refuses with ${label} keeps only the instruction`, () => {
    const {step, failReads} = harness({wrapped});
    failReads();
    const refused = step('exec', PATCH_AND_CHECK, execResult('unreachable'), {refusedBy: refusal});
    assert.notEqual(refused.decision?.block, true, 'this guard admitted it');
    assert.deepEqual(texts(refused.persisted), [I]);
  });

  test(`${transport}: parallel calls in the runtime's order keep their own output within one batch budget`, () => {
    const {begin, after, persist, failReads} = harness({wrapped});
    failReads();
    // One model round: every before hook, then after_tool_call as each call
    // finishes (the failing exec first, so the limit trips), then every
    // persist in call order.
    const sibling = begin('read', {path: 'Playground/game/enemy.js'});
    const tripping = begin('exec', PATCH_AND_CHECK, {round: false});
    const large = begin('read', {path: 'Playground/game/chars.js'}, {round: false});
    const output = `${'patch line\n'.repeat(400)}${SYNTAX_ERROR}${EXITED}`;
    const file = Array.from({length: 400}, (_, i) => `const sprite${i} = bake(${i});`).join('\n');
    after(tripping, execResult(output));
    after(sibling, {content: [{type: 'text', text: 'const enemies = [];'}]});
    after(large, {content: [{type: 'text', text: file}]});
    const [a, b, c] = [sibling, tripping, large].map(call => texts(persist(call).persisted));
    assert.deepEqual(a, ['const enemies = [];', I]);
    assert.equal(b.at(-1), I);
    assert.ok(b[0].endsWith(`SyntaxError: Unexpected token ':'${EXITED}`));
    assert.equal(c.at(-1), I);
    assert.ok(c[0].startsWith('const sprite0 = bake(0);'));
    assert.ok(a[0].length + c[0].length <= STOPPED_SUCCESS_EXCERPT_CHARS, 'the calls that did not fail share their budget');
    assert.ok(b[0].length <= STOPPED_BATCH_EXCERPT_CHARS);
    assert.doesNotMatch([...a, ...b, ...c].join('\n'), /rerun|…\(truncated\)…/);
  });

  test(`${transport}: a sibling that finishes before the call that trips the limit keeps its output too`, () => {
    const {begin, after, persist, failReads} = harness({wrapped});
    failReads(RUN_PROGRESS_LIMITS.consecutiveFailures - 2);
    const sibling = begin('read', {path: 'Playground/game/missing-sibling.js'});
    const tripping = begin('exec', PATCH_AND_CHECK, {round: false});
    // The sibling's failure is the third in a row, the exec's the fourth.
    after(sibling, {isError: true, content: [{type: 'text', text: 'ENOENT: no such file or directory, open missing-sibling.js'}],
      details: {status: 'error'}});
    after(tripping, execResult(`${SYNTAX_ERROR}${EXITED}`));
    assert.deepEqual(texts(persist(sibling).persisted), ['ENOENT: no such file or directory, open missing-sibling.js', I]);
    assert.deepEqual(texts(persist(tripping).persisted), [`${SYNTAX_ERROR}${EXITED}`, I]);
  });

  test(`${transport}: the notes bound to the kept output stay between it and the instruction, as an unstopped result carries them`, () => {
    // ODS put the shipped example back after the call, and the call used the
    // host workspace path inside the sandbox.
    let restoreNext = false;
    const exampleGuard = {begin: () => undefined, guarding: () => false, end: () => undefined,
      check: () => restoreNext ? (restoreNext = false, {outcome: 'restored'}) : undefined};
    const {step, failReads} = harness({wrapped, workspaceRoot: ROOT, executionHost: 'sandbox', exampleGuard});
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
    assert.deepEqual(texts(stopped), [output, correction, EXAMPLE_NOTES.restored.model, I]);
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
  assert.equal(stopped.decision?.blockReason, I);
  assert.deepEqual(texts(stopped.persisted), [I], 'the instruction once, as on main');
});

test('a Tool Search entry from another catalog source keeps its own result, not the envelope', () => {
  const {step, failReads} = harness({wrapped: true, source: 'mcp:files'});
  failReads();
  const kept = texts(step('exec', PATCH_AND_CHECK, execResult(`${SYNTAX_ERROR}${EXITED}`)).persisted);
  assert.deepEqual(kept, [`${SYNTAX_ERROR}${EXITED}`, I]);
});

test('a Tool Search child keeps its output once, with its outer call', () => {
  const {begin, after, persist, failReads} = harness({wrapped: true});
  failReads();
  const outer = begin('exec', PATCH_AND_CHECK);
  // The selected tool's own hooks, under the child call ID Tool Search gives it.
  const child = begin('exec', PATCH_AND_CHECK, {round: false, direct: true, id: `tool_search_code:${outer.id}:exec:1`});
  const output = `${SYNTAX_ERROR}${EXITED}`;
  after(child, execResult(output));
  after(outer, execResult(output));
  assert.deepEqual(texts(persist(child).persisted), [I], 'a child persisted on its own repeats nothing');
  assert.deepEqual(texts(persist(outer).persisted), [output, I]);
});

test('a stopped result fits a configured cap below the floor, and below the instruction keeps it alone', () => {
  // At 800 there would be room for a 113-character excerpt: too little to keep.
  for (const [cap, expectExcerpt] of [[1000, true], [800, false], [400, false]]) {
    const {step, failReads} = harness({toolResultMaxChars: () => cap});
    failReads();
    const output = `${'patch line\n'.repeat(400)}${SYNTAX_ERROR}${EXITED}`;
    const kept = texts(step('exec', PATCH_AND_CHECK, execResult(output)).persisted);
    assert.equal(kept.at(-1), I);
    if (expectExcerpt) {
      assert.ok(total({content: kept.map(text => ({type: 'text', text}))}) <= cap - 100, `${cap}: ${kept.join('').length}`);
      assert.ok(kept[0].endsWith(`SyntaxError: Unexpected token ':'${EXITED}`));
    } else assert.deepEqual(kept, [I], `cap ${cap}: as on main`);
  }
});

test('the excerpt keeps a third head and two thirds tail, within its limit, without splitting a surrogate pair', () => {
  // Without line breaks the split is exact.
  const excerpt = stoppedOutputExcerpt('h'.repeat(20000) + 't'.repeat(20000));
  const [head, tail] = excerpt.split(OMITTED);
  assert.ok(excerpt.length <= STOPPED_BATCH_EXCERPT_CHARS);
  assert.ok(Math.abs(tail.length - 2 * head.length) <= 2, `${head.length} / ${tail.length}`);
  assert.match(head, /^h+$/);
  assert.match(tail, /^t+$/);
  const lone = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
  // Odd and even prefixes put each cut on either half of a surrogate pair.
  const pairs = ['', 'a', 'ab', 'abc'].map(prefix => `${prefix}${'\u{1F600}'.repeat(3000)}`);
  for (const text of ['', 'ok', 'x'.repeat(STOPPED_BATCH_EXCERPT_CHARS), 'y\n'.repeat(1201), 'z'.repeat(250000),
    ...pairs, ('line \u{1F47E}\n').repeat(900)]) {
    for (const limit of [STOPPED_BATCH_EXCERPT_CHARS, 1000, 300]) {
      const cut = stoppedOutputExcerpt(text, limit);
      assert.ok(cut.length <= limit, `${text.length} -> ${cut.length} > ${limit}`);
      if (text.length <= limit) assert.equal(cut, text);
      else assert.ok(text.startsWith(cut.slice(0, 20)) && text.endsWith(cut.slice(-20)));
      assert.doesNotMatch(cut, lone);
    }
  }
});

test('the batch budget, the notes and the longest stop text fit the smallest per-result cap', () => {
  assert.equal(STOPPED_RESULT_TEXT_LIMIT, 4000 - 100);
  const output = `sh: 1: cd: can't cd to ${ROOT}/x${EXITED}`;
  const correction = sandboxHostWorkspaceFailure({command: `cd ${ROOT}/x`}, execResult(output), ROOT, 'sandbox');
  const note = Math.max(EXAMPLE_NOTES.restored.model.length, EXAMPLE_NOTES.failed.model.length);
  const stop = Math.max(I.length, PREVIEW_CHECK_FINALIZATION_INSTRUCTION.length, RUN_PROGRESS_STOP_REASON.length);
  assert.ok(STOPPED_BATCH_EXCERPT_CHARS + correction.length + note + stop <= STOPPED_RESULT_TEXT_LIMIT,
    `${STOPPED_BATCH_EXCERPT_CHARS} + ${correction.length} + ${note} + ${stop}`);
});
