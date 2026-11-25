// The sandbox has node (no npm), and the verification guide sends the agent to
// node --check, node --test and small harness scripts. A red result is the
// answer that loop exists to get, so it is recorded like an empty search
// (#7701), not as a failed tool call. Before this, every red node run spent the
// run's failure budget: an edit/test loop stopped at the 12-failure total and
// one red node --check per file at the 4-failure streak. These cases replay
// OpenClaw's hook sequence for one model round and one call, in both the
// direct and the Tool Search transport, and pin the guards that still apply:
// genuine failures stay charged, the allowance and the round fuse are bounded,
// an unchanged red run is refused, a measurement repeated a third time without
// an edit is refused, and inline node or Python code that deletes a tree needs
// the owner, as rm -rf does.
import test from 'node:test';
import assert from 'node:assert/strict';
import {CODING_RETRY_EXHAUSTED_REASON, createToolLoopGuard, FREE_NODE_RESULTS_PER_RUN,
  RECURSIVE_DELETE_REQUIRES_OWNER_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';

const STOPPED = [PROGRESS_FINALIZATION_INSTRUCTION, RUN_PROGRESS_STOP_REASON];
const PROMPT = 'Fix the bugs in the game in Playground/elf-of-doom.';

// OpenClaw's receipt for a completed command: merged output plus its exit note.
const completed = (exitCode, output = '', status = 'completed') => ({
  ...(exitCode ? {isError: true} : {}),
  content: [{type: 'text', text: `${output}${exitCode ? `\n\n(Command exited with code ${exitCode})` : ''}`}],
  details: {status, exitCode, durationMs: 4},
});
const RED_TEST = completed(1, 'not ok 1 - advances one frame\n# tests 3\n# pass 2\n# fail 1');
const SYNTAX_ERROR = file => completed(1, `/workspace/Playground/elf-of-doom/${file}:3\n  ctx.save(\n          ^\n\nSyntaxError: missing ) after argument list\n\nNode.js v24.21.0`);
const LEAK = completed(1, '{"frames":60,"saveDepth":60,"maxFrameMs":0.4}');
const EDITED = {content: [{type: 'text', text: 'Successfully replaced 1 block(s) in Playground/elf-of-doom/ui.js.'}], details: {}};

function run({wrapped = false, prompt = PROMPT} = {}) {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run', sessionId: 'owner-session', sessionKey: 'owner-key'};
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  const blocks = [];
  function step(name, args, inner, {executedParams} = {}) {
    const toolCallId = `${name}-${++sequence}`;
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId};
    const decision = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    let result, executed = params;
    if (decision?.block) {
      blocks.push(decision.blockReason);
      result = {content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: decision.blockReason}};
    } else {
      executed = executedParams ?? (wrapped ? decision?.params ?? params : {...params, ...decision?.params});
      const envelope = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result: inner};
      result = wrapped ? {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope} : inner;
    }
    const isError = decision?.block === true || inner?.isError === true;
    guard.afterToolCall({toolName, params: executed, toolCallId, result,
      ...(isError ? {error: result.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolName, toolCallId,
      message: {role: 'toolResult', toolName, toolCallId, isError, ...structuredClone(result)}}, ctx);
    return decision;
  }
  return {
    blocks,
    exec: (command, inner, options) => step('exec', {command, workdir: '/workspace/Playground/elf-of-doom'}, inner, options),
    edit: n => step('edit', {path: 'Playground/elf-of-doom/ui.js', oldText: `a${n}`, newText: `b${n}`}, EDITED),
    stopped: () => STOPPED.includes(guard.beforeToolCall({toolName: 'read', params: {path: 'README.md'}, toolCallId: 'probe'},
      {...context, toolName: 'read', toolCallId: 'probe'})?.blockReason),
  };
}

test('the run failure limits and the node result allowance these pins assume', () => {
  assert.equal(RUN_PROGRESS_LIMITS.consecutiveFailures, 4);
  assert.equal(RUN_PROGRESS_LIMITS.recentFailures, 12);
  assert.ok(FREE_NODE_RESULTS_PER_RUN >= RUN_PROGRESS_LIMITS.recentFailures);
});

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: a red node check, test or harness after each edit no longer stops a working run`, () => {
    const agent = run({wrapped});
    const shapes = [
      ['node --test logic.test.mjs', RED_TEST],
      ['cd /workspace/Playground/elf-of-doom && node --check ui.js && node --check chars.js', SYNTAX_ERROR('chars.js')],
      ['node --check ui.js && timeout 60 node harness.cjs ui.js', LEAK],
      ['timeout 60 node --test tests/frame.test.mjs 2>&1', RED_TEST],
    ];
    for (let i = 0; i < FREE_NODE_RESULTS_PER_RUN; i++) {
      agent.edit(i);
      const [command, result] = shapes[i % shapes.length];
      agent.exec(command, result);
      assert.equal(agent.stopped(), false, `round ${i}: ${command}`);
    }
    assert.deepEqual(agent.blocks, []);
  });

  test(`${transport}: one red node --check per file in a row does not stop the run`, () => {
    const agent = run({wrapped});
    for (const file of ['ui.js', 'chars.js', 'audio.js', 'main.js', 'legacy.js']) {
      agent.exec(`node --check ${file}`, SYNTAX_ERROR(file));
      assert.equal(agent.stopped(), false, file);
    }
  });

  test(`${transport}: node that did not run, crashed silently, timed out or got a wrong path is still charged`, () => {
    const agent = run({wrapped});
    const failures = [
      ['node --check ui.js', completed(127, 'sh: 1: node: not found', 'failed')],
      ['node harness.cjs ui.js', completed(1)],
      ['timeout 60 node bench.cjs ui.js', completed(124)],
      ['node --check missing.js', completed(1, "node:internal/modules/cjs/loader:1459\n  throw err;\n  ^\n\nError: Cannot find module '/workspace/Playground/elf-of-doom/missing.js'\n  code: 'MODULE_NOT_FOUND',")],
    ];
    for (const [command, result] of failures) {
      assert.equal(agent.stopped(), false, command);
      agent.exec(command, result);
    }
    assert.equal(agent.stopped(), true);
  });

  test(`${transport}: inline node code, pipes and other programs are still charged`, () => {
    const agent = run({wrapped});
    for (const command of [
      "node -e \"require('./logic.js').step()\"",
      'node --test logic.test.mjs || true',
      'cat ui.js | node --check',
      'for f in *.js; do node --check "$f" || exit 1; done',
    ]) {
      assert.equal(agent.stopped(), false, command);
      agent.exec(command, SYNTAX_ERROR('ui.js'));
    }
    assert.equal(agent.stopped(), true);
  });

  test(`${transport}: the node result allowance is bounded per run`, () => {
    const agent = run({wrapped});
    for (let i = 0; i < FREE_NODE_RESULTS_PER_RUN; i++) { agent.edit(i); agent.exec(`node --test t${i}.test.mjs`, RED_TEST); }
    assert.equal(agent.stopped(), false);
    // Past the allowance a red run is an ordinary failure again.
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(agent.stopped(), false);
      agent.exec(`node --test beyond${i}.test.mjs`, RED_TEST);
    }
    assert.equal(agent.stopped(), true);
  });

  test(`${transport}: a red node run never erases earlier failures`, () => {
    const agent = run({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures - 1; i++) {
      agent.exec(`python3 build${i}.py`, completed(2, 'Traceback (most recent call last): boom'));
    }
    agent.exec('node --test logic.test.mjs', RED_TEST);
    assert.equal(agent.stopped(), false);
    agent.exec('python3 build.py', completed(2, 'Traceback (most recent call last): boom'));
    assert.equal(agent.stopped(), true);
  });

  test(`${transport}: a run that only collects red results still stops on the rounds fuse`, () => {
    const agent = run({wrapped});
    for (let i = 0; i < RUN_PROGRESS_LIMITS.roundsWithoutProgress; i++) {
      agent.exec(`node --check part${i}.js`, SYNTAX_ERROR(`part${i}.js`));
      assert.equal(agent.stopped(), false);
    }
    agent.exec('node --check one-more.js', SYNTAX_ERROR('one-more.js'));
    assert.equal(agent.stopped(), true);
  });

  test(`${transport}: the same red run without an edit in between stays bounded`, () => {
    const agent = run({wrapped});
    if (!wrapped) {
      for (let i = 0; i < 3; i++) assert.equal(agent.exec('node --test logic.test.mjs', RED_TEST)?.block, undefined);
      assert.deepEqual(agent.exec('node --test logic.test.mjs', RED_TEST), {block: true, blockReason: CODING_RETRY_EXHAUSTED_REASON});
    } else {
      // The failed-exec retry counter reads direct exec receipts only (as for
      // any failing command before this change); the round fuse still ends it.
      for (let i = 0; i < RUN_PROGRESS_LIMITS.roundsWithoutProgress; i++) agent.exec('node --test logic.test.mjs', RED_TEST);
      assert.equal(agent.stopped(), false);
      agent.exec('node --test logic.test.mjs', RED_TEST);
      assert.equal(agent.stopped(), true);
    }
    // An edit is what the loop is for: the same test may run again after one.
    const fixing = run({wrapped});
    for (let i = 0; i < 6; i++) {
      fixing.edit(i);
      assert.equal(fixing.exec('node --test logic.test.mjs', RED_TEST)?.block, undefined, `round ${i}`);
    }
  });

  test(`${transport}: a receipt that does not match the recorded call is charged`, () => {
    const agent = run({wrapped});
    const forged = wrapped ? {id: 'openclaw:core:exec', args: {command: 'node --check other.js'}} : {command: 'node --check other.js'};
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(agent.stopped(), false);
      agent.exec(`node --check part${i}.js`, SYNTAX_ERROR(`part${i}.js`), {executedParams: forged});
    }
    assert.equal(agent.stopped(), true);
  });
}

test('a measurement repeated a third time without an edit is refused', () => {
  const agent = run();
  const bench = 'timeout 60 node bench.cjs ui.js';
  assert.equal(agent.exec(bench, completed(0, '{"p95":6.1}')), undefined);
  assert.equal(agent.exec(bench, completed(0, '{"p95":6.0}')), undefined);
  assert.equal(agent.exec(bench, completed(0, '{"p95":6.2}'))?.block, true);
  // Before and after an edit is the pattern the guide asks for.
  const measured = run();
  measured.exec(bench, completed(0, '{"p95":6.1}'));
  measured.edit(1);
  assert.equal(measured.exec(bench, completed(0, '{"p95":2.4}')), undefined);
});

test('inline node or Python code that deletes a tree needs the owner, like rm -rf', () => {
  for (const command of [
    'rm -rf /workspace/Playground/elf-of-doom',
    "node -e \"require('fs').rmSync('/workspace/Playground/elf-of-doom',{recursive:true,force:true})\"",
    "node -p \"require('fs').rmSync('/workspace', {recursive: true})\"",
    "node --eval \"fs.promises.rm(path.join(root, 'build'), {force: true, recursive: true})\"",
    "node -e \"require('fs').rmdirSync('dist', {recursive:true})\"",
    "node -e \"require('child_process').execSync('rm -rf /workspace/Playground')\"",
    "python3 -c \"import shutil; shutil.rmtree('/workspace/Playground/elf-of-doom')\"",
    "python3 -c \"from shutil import rmtree; rmtree('dist')\"",
    "sh -c 'rm -rf /workspace/Playground/elf-of-doom'",
    "bash -lc 'rm -rf /workspace/Playground/elf-of-doom'",
    "perl -e 'use File::Path qw(rmtree); rmtree(\"dist\")'",
  ]) {
    const decision = run().exec(command, completed(0));
    assert.deepEqual(decision, {block: true, blockReason: RECURSIVE_DELETE_REQUIRES_OWNER_REASON}, command);
  }
  for (const command of [
    'node --check ui.js',
    'timeout 60 node harness.cjs ui.js',
    "node -e \"require('fs').rmSync('out/report.json')\"",
    "node -e \"require('fs').rmSync('out/report.json', {force: true})\"",
    "node -e \"require('fs').mkdirSync('out/frames', {recursive: true})\"",
    "node -e \"require('fs').cpSync('assets', '/tmp/assets', {recursive: true})\"",
    "python3 -c \"import os; os.makedirs('out', exist_ok=True)\"",
    'rm -f out/report.json',
    // Searching for the text of a delete is not a delete.
    'grep -rn "rm -rf dist" scripts Makefile',
    'grep -n "rmSync(dir, {recursive: true})" src/*.js',
    'echo "cleanup: rm -rf dist"',
  ]) {
    assert.notEqual(run().exec(command, completed(0))?.block, true, command);
  }
  // The owner's explicit instruction still authorizes it.
  const authorized = run({prompt: 'Delete the directory /workspace/Playground/elf-of-doom recursively.'});
  assert.notEqual(authorized.exec("node -e \"require('fs').rmSync('/workspace/Playground/elf-of-doom',{recursive:true})\"",
    completed(0))?.block, true);
});
