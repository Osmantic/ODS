// An exit-0 unittest run is judged by the runner's own summary, and a coding
// stop takes precedence over test-command coaching. Fleet evidence: ODS main
// d4a61f33, open-prompt 07 photo-renamer ("write a python script that renames
// all photos in a folder by date taken, and test it"), replayed call by call
// from the recorded sessions (tests/fixtures/photo-renamer-*-d4a61f33.json).
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  CODING_LOOP_ABORT_REASON,
  CODING_REPEAT_NO_PROGRESS_REASON,
  CODING_RETRY_EXHAUSTED_REASON,
  DEFAULT_WEB_TOOL_LIMITS,
  RECURSIVE_DELETE_REQUIRES_OWNER_REASON,
  VERIFICATION_COMMAND_NOT_AUDITABLE_REASON,
  VERIFICATION_FAILED_DELIVERY_PREFIX,
  createToolLoopGuard,
} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

const fixture = name => JSON.parse(fs.readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));
const TOWER3 = fixture('photo-renamer-tower3-d4a61f33.json');
const STRIXY = fixture('photo-renamer-strixy-tail-d4a61f33.json');
const TOWER2 = fixture('photo-renamer-tower2-d4a61f33.json');

// Production wraps every exec for cancellation; the guard maps it back.
const EXEC_CONTROL = {prepare: (runId, command) => `/control/wrapper ${runId} ${Buffer.from(command).toString('base64')}`};
const textOf = result => result.content.filter(block => block.type === 'text').map(block => block.text).join('\n');

// Each replay gets its own owner workspace, where Playground project routing
// keeps its binding and allowed writes and edits land, as on the host.
const roots = [];
after(() => { for (const root of roots) fs.rmSync(root, {recursive: true, force: true}); });
function applyToWorkspace(root, name, args) {
  if (typeof args?.path !== 'string') return;
  const target = path.join(root, ...args.path.split('/'));
  if (name === 'write') {
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, args.content);
  } else if (name === 'edit' && fs.existsSync(target)) {
    let text = fs.readFileSync(target, 'utf8');
    for (const edit of args.edits ?? []) text = text.replace(edit.oldText, () => edit.newText);
    fs.writeFileSync(target, text);
  }
}

// Replays OpenClaw's hook order for one model round and one tool call:
// before_tool_call, the recorded result (or the SDK's veto result for a
// refused call), after_tool_call, tool_result_persist.
function session(recording, {limits, wrapped = false} = {}) {
  const aborted = [];
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-unittest-verdict-')));
  roots.push(root);
  const guard = createToolLoopGuard({execControl: EXEC_CONTROL, ...(limits ? {limits} : {}),
    abortRun: sessionId => { aborted.push(sessionId); return true; }});
  const context = {agentId: 'pixel', runId: 'run', sessionId: recording.session.id, sessionKey: 'agent:pixel:open-prompt'};
  guard.observeRun(context, 'pixel', {prompt: recording.prompt}, {workspaceRoot: root, executionHost: 'sandbox'});
  function step(name, args, recorded, id) {
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id: `openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    let result, executed = params;
    if (decision?.block) {
      result = {content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: decision.blockReason}};
    } else {
      assert.ok(recorded, `${id} ran in the replay but was refused when recorded`);
      executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
      applyToWorkspace(root, name, wrapped ? executed.args : executed);
      const inner = structuredClone(recorded);
      if (name === 'exec' && inner.details?.status === 'completed' && inner.details.aggregated === undefined) {
        inner.details.aggregated = inner.content[0].text;
      }
      const envelope = {tool: {id: `openclaw:core:${name}`, name, source: 'openclaw', sourceName: 'core'}, result: inner};
      result = wrapped ? {content: [{type: 'text', text: JSON.stringify(envelope)}], details: envelope} : inner;
    }
    const isError = decision?.block === true || result.isError === true;
    guard.afterToolCall({toolName, params: executed, toolCallId: id, result,
      ...(isError ? {error: textOf(result)} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError, ...structuredClone(result)};
    guard.toolResultPersist({toolName, toolCallId: id, message}, ctx);
    return {decision, status: guard.verificationStatus('run')};
  }
  return {
    guard, aborted,
    step,
    replay: call => step(call.name, call.arguments, call.result, call.id),
    finalize: text => {
      guard.observeModelCall({}, context);
      return guard.beforeAgentFinalize({lastAssistantMessage: text}, context);
    },
    deliver: text => guard.replyPayloadSending({runId: 'run', kind: 'final', payload: {text}}),
  };
}

// Every refusal the recorded run received is reproduced by the replay, except
// the calls this change intends to answer differently.
function assertRecordedRefusals(recording, outcomes, changed = {}) {
  for (const [index, call] of recording.calls.entries()) {
    const reason = outcomes[index].decision?.blockReason;
    if (call.n in changed) assert.equal(reason, changed[call.n], `call ${call.n}`);
    else assert.equal(reason, call.blocked, `call ${call.n}`);
  }
}

test('tower3 replay: the exit-0 run with a clean summary passes and the model answer is delivered', () => {
  const [, , firstRun, , secondRun, script, dryRun, realRun] = TOWER3.calls;
  assert.equal(TOWER3.session.sha256, 'e861b9f0263626593ff7e3966f81d95a901976fdc74e3cc3dfa2f07a70d228f3');
  assert.equal(secondRun.arguments.command, 'cd /workspace/Playground/photo-renamer && python3 -m unittest test_photo_renamer -v');
  assert.equal(firstRun.arguments.command, secondRun.arguments.command);
  assert.equal(firstRun.result.details.exitCode, 1);
  assert.equal(secondRun.result.details.exitCode, 0);
  const output = secondRun.result.content[0].text;
  assert.match(output, /\nRan 11 tests in 0\.010s\n\nOK\n/);
  // The program under test prints this in a negative-path test that passed.
  assert.match(output, /\nError: Folder '\/nonexistent\/folder\/path' does not exist\.$/);
  // Recorded on d4a61f33: the verdict stayed failed and the answer was replaced.
  assert.equal(TOWER3.delivered, VERIFICATION_FAILED_DELIVERY_PREFIX);

  const s = session(TOWER3);
  const outcomes = TOWER3.calls.map(call => s.replay(call));
  assertRecordedRefusals(TOWER3, outcomes);
  assert.deepEqual(outcomes.map(outcome => outcome.status),
    [undefined, undefined, 'failed', 'failed', 'passed', 'passed', 'passed', 'passed']);
  assert.match(script.arguments.command, /python3 -c /);
  assert.match(dryRun.arguments.command, /--dry-run$/);
  assert.match(realRun.arguments.command, /photo_renamer\.py \. && echo/);
  assert.deepEqual(s.guard.verificationForRun('run'), {status: 'passed'});
  assert.equal(TOWER3.finalStopReason, 'stop');
  assert.equal(TOWER3.finalAnswer.length, 973);
  assert.equal(s.finalize(TOWER3.finalAnswer), undefined, 'no revision is requested');
  assert.deepEqual(s.guard.deliveryVerificationForRun('run'), {status: 'passed'});
  assert.equal(s.deliver(TOWER3.finalAnswer), undefined, 'the recorded 973-character answer is delivered unchanged');
});

test('tower3 replay: the recorded failing run counts one attempt and the passing run resets the count', () => {
  const [, , firstRun] = TOWER3.calls;
  const probe = (s, id) => s.step('exec', firstRun.arguments, firstRun.result, id).decision?.blockReason;
  const limits = {...DEFAULT_WEB_TOOL_LIMITS, failedVerificationAttempts: 2};

  // After the recorded exit-1 run one of two attempts remains.
  const failed = session(TOWER3, {limits});
  for (const call of TOWER3.calls.slice(0, 3)) failed.replay(call);
  assert.equal(failed.guard.verificationStatus('run'), 'failed');
  assert.equal(probe(failed, 'attempt-2'), undefined);
  assert.equal(probe(failed, 'attempt-3'), CODING_RETRY_EXHAUSTED_REASON);

  // After the recorded exit-0 run both attempts are available again.
  const passed = session(TOWER3, {limits});
  for (const call of TOWER3.calls.slice(0, 5)) passed.replay(call);
  assert.equal(passed.guard.verificationStatus('run'), 'passed');
  assert.equal(probe(passed, 'attempt-1'), undefined);
  assert.equal(probe(passed, 'attempt-2'), undefined);
  assert.equal(probe(passed, 'attempt-3'), CODING_RETRY_EXHAUSTED_REASON);
});

test('tower3 replay: the passing command is counted as a successful command, not a failed one', () => {
  const secondRun = TOWER3.calls[4];
  const s = session(TOWER3);
  for (const call of TOWER3.calls.slice(0, 5)) s.replay(call);
  // A successful identical command may run once more; a third run is no progress.
  const repeat = s.step('exec', secondRun.arguments, secondRun.result, 'repeat-1');
  assert.equal(repeat.decision?.block, undefined);
  assert.equal(repeat.status, 'passed');
  assert.equal(s.step('exec', secondRun.arguments, secondRun.result, 'repeat-2').decision?.blockReason,
    CODING_REPEAT_NO_PROGRESS_REASON);
});

test('tower3 output through the process-poll completion path passes', () => {
  const secondRun = TOWER3.calls[4];
  const output = secondRun.result.content[0].text;
  const s = session(TOWER3);
  for (const call of TOWER3.calls.slice(0, 4)) s.replay(call);
  s.step('exec', secondRun.arguments, {content: [{type: 'text', text: 'Command still running (session calm-otter).'}],
    details: {status: 'running', sessionId: 'calm-otter'}}, 'background');
  assert.equal(s.guard.verificationStatus('run'), 'pending');
  s.step('process', {action: 'poll', sessionId: 'calm-otter'}, {content: [{type: 'text', text: output}],
    details: {status: 'completed', sessionId: 'calm-otter', exitCode: 0, aggregated: output}}, 'poll');
  assert.equal(s.guard.verificationStatus('run'), 'passed');
  assert.deepEqual(s.guard.verificationForRun('run'), {status: 'passed'});
});

test('strixy tail replay: after the coding stop, the non-auditable test command ends the run', () => {
  const calls = new Map(STRIXY.calls.map(call => [call.n, call]));
  const testRuns = [2, 8, 14, 20, 28, 31];
  assert.equal(STRIXY.session.sha256, 'e5134e083040be21ebe95ebfad9c4b966f058d464aec4f4381ef5ab3d3bc66cf');
  for (const n of testRuns) {
    assert.equal(calls.get(n).arguments.command, 'cd /workspace/Playground/renamer && python3 -m unittest test_renamer -v 2>&1');
    assert.equal(calls.get(n).result.details.exitCode, 1);
  }
  assert.equal(calls.get(40).arguments.command,
    'cd /workspace/Playground/renamer && python3 -m unittest test_renamer.TestEdgeCases.test_jpeg_with_exif_sub_ifd 2>&1; echo "EXIT:$?"');
  // Recorded on d4a61f33: the coding stop, its terminal block, then coaching
  // to run the test "directly", which the model obeyed.
  assert.equal(calls.get(38).blocked, CODING_RETRY_EXHAUSTED_REASON);
  assert.equal(calls.get(39).blocked, CODING_RETRY_EXHAUSTED_REASON);
  assert.equal(calls.get(40).blocked, VERIFICATION_COMMAND_NOT_AUDITABLE_REASON);
  assert.equal(calls.get(41).blocked, CODING_LOOP_ABORT_REASON);
  assert.match(VERIFICATION_COMMAND_NOT_AUDITABLE_REASON, /Run it directly/);
  assert.match(CODING_RETRY_EXHAUSTED_REASON, /Do not call another tool/);
  assert.equal(STRIXY.delivered, RUN_PROGRESS_STOP_REASON);

  const s = session(STRIXY);
  const outcomes = [];
  for (const call of STRIXY.calls) {
    outcomes.push(s.replay(call));
    // No test run passed, and nothing was aborted before the non-auditable call.
    assert.notEqual(outcomes.at(-1).status, 'passed', `call ${call.n}`);
    if (call.n < 40) assert.deepEqual(s.aborted, [], `call ${call.n}`);
    if (call.n === 40) assert.deepEqual(s.aborted, [STRIXY.session.id]);
  }
  for (const n of testRuns) assert.equal(outcomes[STRIXY.calls.indexOf(calls.get(n))].status, 'failed');
  // The seventh run is still refused at six of six failed attempts.
  assert.equal(DEFAULT_WEB_TOOL_LIMITS.failedVerificationAttempts, testRuns.length);
  assertRecordedRefusals(STRIXY, outcomes, {40: CODING_LOOP_ABORT_REASON});
  assert.equal(s.guard.verificationStatus('run'), 'failed');
  assert.equal(s.guard.verificationForRun('run').status, 'failed');
  assert.equal(s.guard.deliveryVerificationForRun('run').status, 'failed');
  assert.notEqual(s.deliver('All tests pass.'), undefined, 'a claimed success is not delivered');
});

test('tower2 replay: a custom runner without a unittest summary keeps its passing verdict', () => {
  const custom = TOWER2.calls[8];
  assert.equal(TOWER2.session.sha256, '8d21ebc1d247f332de279b80e3fd205d5a477a15211dd5bb1f47f158e4ca6f89');
  assert.equal(custom.arguments.command, 'cd /workspace/Playground/photo-renamer && python3 test_rename.py');
  assert.equal(custom.result.details.exitCode, 0);
  assert.doesNotMatch(custom.result.content[0].text, /^Ran \d+ tests? in /m);
  assert.equal(TOWER2.delivered, RECURSIVE_DELETE_REQUIRES_OWNER_REASON);

  const s = session(TOWER2);
  const outcomes = TOWER2.calls.map(call => s.replay(call));
  assertRecordedRefusals(TOWER2, outcomes);
  assert.equal(outcomes[6].status, 'failed');
  assert.equal(outcomes[8].status, 'passed');
  assert.equal(s.guard.verificationStatus('run'), 'passed');
  assert.deepEqual(s.guard.verificationForRun('run'), {status: 'failed', text: RECURSIVE_DELETE_REQUIRES_OWNER_REASON});
});

// One exit-0 test command in a fresh run; returns the recorded verdict.
const UNIT = {session: {id: 'unit-session'},
  prompt: 'Write a photo renamer in /workspace/project and test it.'};
function verdict(output, {command = 'python3 -m unittest -v', details = {}, wrapped = false, exitCode = 0} = {}) {
  const s = session(UNIT, {wrapped});
  s.step('exec', {command, workdir: '/workspace/project'},
    {content: [{type: 'text', text: output}], details: {status: 'completed', exitCode, aggregated: output, ...details}}, 'unit');
  return s.guard.verificationStatus('run');
}
const RULE = '-'.repeat(70);
const summary = (count, outcome = 'OK') => `\n${RULE}\nRan ${count} test${count === 1 ? '' : 's'} in 0.002s\n\n${outcome}\n`;

const PASSED = {
  'default logging from the code under test': 'ERROR:root:cannot read folder /photos/missing\n.' + summary(1),
  // strixy renamer.py: print(f"ERROR: cannot read folder {folder}: {exc}", file=sys.stderr)
  'the strixy renamer message on stderr':
    "ERROR: cannot read folder /photos/missing: [Errno 2] No such file or directory: '/photos/missing'\n." + summary(1),
  'a verbose docstring that starts with Error':
    'test_missing (test_renamer.RenamerTest.test_missing)\nError when the folder is missing returns an empty list. ... ok' + summary(1),
  'AssertionError text printed by a passing test':
    'test_message (test_renamer.RenamerTest.test_message) ... AssertionError: shown to the user\nok' + summary(1),
  'skipped tests': 'test_raw (m.T.test_raw) ... skipped \'needs RAW fixtures\'\ntest_jpeg (m.T.test_jpeg) ... ok' + summary(3, 'OK (skipped=2)'),
  'CRLF output': ('ERROR: cannot read folder /photos/missing\n.' + summary(1)).replace(/\n/g, '\r\n'),
  'program stdout after the summary': summary(2) + 'Error: Folder \'/missing\' does not exist.\nFAIL: rename skipped\n',
  // Python 3.14 on a terminal (pty exec) colors the runner's markers.
  'Python 3.14 colored output':
    'test_missing (test_renamer.RenamerTest.test_missing) ... ERROR: cannot read folder /photos/missing\r\n\x1b[32mok\x1b[0m\r\n' +
    `\r\n${RULE}\r\nRan 2 tests in 0.001s\r\n\r\n\x1b[32mOK\x1b[0m (\x1b[33mskipped=1\x1b[0m)\r\n`,
};
const FAILED = {
  'a strict failure header with OK':
    `FAIL: test_total (test_report.ReportTest.test_total)\n${RULE}\nTraceback (most recent call last):\nAssertionError: 2 != 3` + summary(1),
  'an ERROR header for a module that did not import':
    'ERROR: test_renamer (unittest.loader._FailedTest.test_renamer)\nImportError: Failed to import test module: test_renamer' + summary(1),
  'unittest.main(exit=False) after a failure':
    `test_total (__main__.T.test_total) ... FAIL\n\n${'='.repeat(70)}\nFAIL: test_total (__main__.T.test_total)\n${RULE}\n` +
    'Traceback (most recent call last):\nAssertionError: 2 != 3' + summary(1, 'FAILED (failures=1)'),
  'FAILED without a failure header': summary(4, 'FAILED (errors=1)'),
  // df3f4bf3a: a test file with no discoverable tests.
  'a zero-test summary': 'Ran 0 tests in 0.000s\n\nOK',
  'NO TESTS RAN': summary(0, 'NO TESTS RAN'),
  'expected failures': 'test_gap (m.T.test_gap) ... expected failure' + summary(2, 'OK (expected failures=1)'),
  'a verbose unexpected success': 'test_gap (m.T.test_gap) ... unexpected success' + summary(1),
  'a summary cut before its outcome': `test_total (m.T.test_total) ... ok\n${RULE}\nRan 1 test in 0.002s\n`,
  'a clean summary followed by a failed one': summary(2) + summary(1, 'FAILED (failures=1)'),
  'Python 3.14 colored failure':
    `\x1b[31mFAIL\x1b[0m\x1b[1;31m: test_total (__main__.T.test_total)\x1b[0m\r\n${RULE}\r\n` +
    '\x1b[1;35mAssertionError\x1b[0m: \x1b[35m2 != 3\x1b[0m\r\n\r\n' +
    `${RULE}\r\nRan 1 test in 0.001s\r\n\r\n\x1b[1;31mFAILED\x1b[0m (\x1b[1;31mfailures=1\x1b[0m)\r\n`,
  'Python 3.14 colored unexpected success': 'test_gap (m.T.test_gap) ... \x1b[31munexpected success\x1b[0m\r\n' + summary(1),
  // df3f4bf3a: a custom runner (no summary) keeps the text heuristics.
  'a custom runner failure without a summary': 'FAIL: negative numbers\nAssertionError',
};

for (const wrapped of [false, true]) {
  for (const [name, output] of Object.entries(PASSED)) {
    test(`exit-0 unittest with a clean summary passes: ${name} (wrapped=${wrapped})`, () => {
      assert.equal(verdict(output, {wrapped}), 'passed');
    });
  }
  for (const [name, output] of Object.entries(FAILED)) {
    test(`exit-0 unittest stays failed: ${name} (wrapped=${wrapped})`, () => {
      assert.equal(verdict(output, {wrapped}), 'failed');
    });
  }
}

test('separate stdout and stderr streams are judged together by the summary', () => {
  const stderr = "ERROR: cannot read folder /photos/missing: [Errno 2] No such file or directory: '/photos/missing'\n." + summary(1);
  assert.equal(verdict(stderr, {details: {stdout: 'Renamed 0 files\n', stderr}}), 'passed');
  assert.equal(verdict(stderr, {details: {stdout: 'FAIL: test_total (m.T.test_total)\n', stderr}}), 'failed');
  assert.equal(verdict(stderr, {details: {stdout: 'Ran 0 tests in 0.000s\n\nOK\n', stderr}}), 'failed');
});

test('direct test scripts use the same summary rule; custom runners keep the text heuristics', () => {
  assert.equal(verdict('Error: Folder \'/missing\' does not exist.\n.' + summary(1), {command: 'python3 test_photo_renamer.py'}), 'passed');
  assert.equal(verdict('FAIL: negative numbers\nAssertionError', {command: 'python3 test_photo_renamer.py'}), 'failed');
  // Unchanged: without a summary, program text is still judged heuristically.
  assert.equal(verdict('Error: Folder \'/missing\' does not exist.\nAll tests passed!', {command: 'python3 test_photo_renamer.py'}), 'failed');
  assert.equal(verdict('Verification:\n  ok 20240110_091530.jpg\nAll tests passed!', {command: 'python3 test_rename.py'}), 'passed');
});

test('other runners are still judged by exit status only', () => {
  for (const output of ['FAILED (failures=1)', 'ERROR: cannot read folder\n1 passed in 0.01s', 'Ran 0 tests in 0.000s\n\nOK']) {
    assert.equal(verdict(output, {command: 'python3 -m pytest -q'}), 'passed', output);
  }
  assert.equal(verdict('Ran 1 test in 0.002s\n\nOK', {command: 'python3 -m pytest -q', exitCode: 1}), 'failed');
  assert.equal(verdict('Ran 1 test in 0.002s\n\nOK', {exitCode: 1}), 'failed', 'a nonzero exit decides first');
});

test('after a coding stop, a non-auditable test command gets the stop and still invalidates a stale pass', () => {
  const s = session(UNIT, {limits: {...DEFAULT_WEB_TOOL_LIMITS, failedExecRetries: 1}});
  const ok = {content: [{type: 'text', text: summary(2)}], details: {status: 'completed', exitCode: 0}};
  const written = {content: [{type: 'text', text: 'Successfully wrote 20 bytes to project/renamer.py'}]};
  const crashed = {isError: true, content: [{type: 'text', text: 'Traceback\n\n(Command exited with code 1)'}],
    details: {status: 'completed', exitCode: 1}};
  s.step('write', {path: 'project/renamer.py', content: 'print("renamer")\n'}, written, 'write');
  s.step('exec', {command: 'python3 -m unittest -v', workdir: '/workspace/project'}, ok, 'test');
  assert.equal(s.guard.verificationStatus('run'), 'passed');
  s.step('write', {path: 'project/renamer.py', content: 'print("renamed")\n'}, written, 'rewrite');
  s.step('exec', {command: 'python3 renamer.py', workdir: '/workspace/project'}, crashed, 'demo');
  assert.equal(s.step('exec', {command: 'python3 renamer.py', workdir: '/workspace/project'}, crashed, 'demo-again')
    .decision?.blockReason, CODING_RETRY_EXHAUSTED_REASON);
  const piped = {command: 'python3 -m unittest -v 2>&1 | tail -5', workdir: '/workspace/project'};
  assert.equal(s.step('exec', piped, ok, 'piped').decision?.blockReason, CODING_RETRY_EXHAUSTED_REASON);
  assert.equal(s.guard.verificationStatus('run'), 'failed', 'the pass predates the rewrite');
  assert.deepEqual(s.aborted, []);
  assert.equal(s.step('exec', piped, ok, 'piped-again').decision?.blockReason, CODING_LOOP_ABORT_REASON);
  assert.deepEqual(s.aborted, ['unit-session']);
});

test('before a coding stop, the non-auditable refusal and its text are unchanged', () => {
  const s = session(UNIT);
  const decision = s.step('exec', {command: 'python3 -m unittest -v | tail -5', workdir: '/workspace/project'},
    {content: [{type: 'text', text: summary(1)}], details: {status: 'completed', exitCode: 0}}, 'piped').decision;
  assert.deepEqual(decision, {block: true, blockReason: VERIFICATION_COMMAND_NOT_AUDITABLE_REASON});
  assert.deepEqual(s.aborted, []);
});
