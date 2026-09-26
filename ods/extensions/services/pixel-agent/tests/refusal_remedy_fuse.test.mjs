import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {bindsNewProject, routePlaygroundTool} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createRunProgressBudget, RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {createProgressFinalization, PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';

// Fleet evidence, claude-open-prompts sweep on ODS build bfb62a2b, prompt 07
// "write a python script that renames all photos in a folder by date taken,
// and test it" (fixture header). The Playground router refused five probe
// commands before the first project file; each ran nothing and named its
// remedy, yet the fourth consecutive refusal (call 6) tripped the run-progress
// fuse, which then refused call 7: the write that refusal asked for.
const TRACE = JSON.parse(fs.readFileSync(new URL('./fixtures/photo-renamer-bfb62a2b.json', import.meta.url), 'utf8'));
const NOT_RUN = 'Not run: write the first project file before running commands; write creates its folder, so mkdir is not needed.';
const CORRECTION_START = 'For a new project, use a workspace-relative path such as Playground/snake-game/index.html';

const roots = [];
after(() => { for (const root of roots) fs.rmSync(root, {recursive: true, force: true}); });
function workspace() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(tmpdir(), 'pixel-refusal-remedy-')));
  roots.push(root);
  return root;
}
const synthesized = (chars, fill = 'x') => '#!/usr/bin/env python3\n' + fill.repeat(chars - 23);

// Replays OpenClaw's hook sequence, one model message per call as recorded:
// model_call_started and model_call_ended, then before_tool_call. An allowed
// write lands on disk; a blocked call gets the SDK's veto receipt, which
// reaches after_tool_call and tool_result_persist in the given order (the
// runtime dispatches after_tool_call without awaiting it).
function harness({order = 'after-first', runId = 'refusal-remedy', prompt = TRACE.prompt} = {}) {
  const root = workspace();
  const aborts = [];
  const signals = [];
  const guard = createToolLoopGuard({
    abortRun: (sessionId) => { aborts.push(sessionId); return true; },
    execControl: {resolveWorkdir: () => undefined, prepare: (_run, command) => command,
      signal: (run) => { signals.push(run); return true; }},
  });
  const context = {agentId: 'pixel', runId, sessionId: `session-${runId}`, sessionKey: `agent:pixel:openai-user:ods-${'c'.repeat(64)}`};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root, executionHost: 'sandbox'});
  let sequence = 0;
  function modelRound() {
    guard.observeModelCall({}, context);
    guard.observeModelEnd({outcome: 'completed'}, context);
  }
  function call(tool, args, perform, id = `${tool}-${++sequence}`) {
    modelRound();
    const ctx = {...context, toolName: tool, toolCallId: id};
    const decision = guard.beforeToolCall({toolName: tool, params: args, toolCallId: id}, ctx);
    let result, executed = args;
    if (decision?.block) {
      result = {content: [{type: 'text', text: decision.blockReason}],
        details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason: decision.blockReason}};
    } else {
      executed = {...args, ...decision?.params};
      result = perform(executed);
    }
    const isError = decision?.block === true || result?.isError === true;
    const message = {role: 'toolResult', toolName: tool, toolCallId: id, isError, ...structuredClone(result)};
    let persisted;
    const afterHook = () => guard.afterToolCall({toolName: tool, params: executed, toolCallId: id, result,
      ...(isError ? {error: result.content[0].text} : {})}, ctx);
    const persistHook = () => { persisted = guard.toolResultPersist({toolName: tool, toolCallId: id, message}, ctx)?.message ?? message; };
    if (order === 'after-first') { afterHook(); persistHook(); } else { persistHook(); afterHook(); }
    return {decision, executed, text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  }
  const write = (args, id, {fail = false} = {}) => call('write', args, (executed) => {
    if (fail) return {isError: true, content: [{type: 'text', text: 'EIO: i/o error, write'}], details: {status: 'error'}};
    const target = path.join(root, ...executed.path.split('/'));
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, executed.content);
    return {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(executed.content)} bytes to ${executed.path}`}], details: {}};
  }, id);
  const exec = (args, id, {ok = true} = {}) => call('exec', args, (executed) => ({...(ok ? {} : {isError: true}),
    content: [{type: 'text', text: ok ? 'ok' : 'Traceback: FileNotFoundError\n\n(Command exited with code 1)'}],
    details: {status: 'completed', exitCode: ok ? 0 : 1, aggregated: ok ? 'ok' : 'FileNotFoundError', cwd: executed.workdir}}), id);
  return {guard, context, root, aborts, signals, modelRound, call, write, exec};
}

const [probeAll, version, probeEach, probeEachRoot, probePillow, probePillowAgain, recordedWrite, recordedRewrite] = TRACE.calls;
const refusedProbe = (h, recorded) => {
  const refused = h.exec(recorded.args, recorded.id);
  assert.equal(refused.decision?.block, true, recorded.args.command);
  assert.ok(refused.decision.blockReason.startsWith(`${NOT_RUN} Create the first project file`), refused.decision.blockReason);
  assert.deepEqual(Object.keys(refused.decision).sort(), ['block', 'blockReason'], 'only the refusal reaches the runtime');
  return refused;
};
// Calls 1-6 as recorded: five refusals and the one allowed inspection.
function replayToFuse(h) {
  const first = refusedProbe(h, probeAll);
  assert.ok(first.text.startsWith(probeAll.recorded.resultHead));
  const allowed = h.exec(version.args, version.id);
  assert.notEqual(allowed.decision?.block, true, allowed.decision?.blockReason);
  for (const recorded of [probeEach, probeEachRoot, probePillow]) {
    assert.ok(refusedProbe(h, recorded).text.startsWith(recorded.recorded.resultHead));
  }
  return refusedProbe(h, probePillowAgain);
}
const projectRecord = (root) => {
  const records = fs.readdirSync(path.join(root, '.ods-projects'));
  assert.equal(records.length, 1);
  return JSON.parse(fs.readFileSync(path.join(root, '.ods-projects', records[0]), 'utf8')).directory;
};
const stoppedAtFuse = (h, next) => {
  assert.equal(next.decision?.block, true);
  assert.equal(next.decision.blockReason, PROGRESS_FINALIZATION_INSTRUCTION, 'the unchanged stop, as on main');
  assert.equal(next.text, RUN_PROGRESS_STOP_REASON);
};

test('bfb62a2b replay: call 6 still trips the fuse, and call 7, the write the refusal named, runs, binds the project and the run continues', () => {
  assert.deepEqual({...RUN_PROGRESS_LIMITS}, {consecutiveFailures: 4, totalFailures: 12, roundsWithoutProgress: 8, identicalSuccesses: 2});
  assert.equal(TRACE.sessionFileSha256.slice(0, 8), '25cb6213');
  assert.equal(recordedWrite.args.path, 'Playground/photo-renamer/rename_photos.py');
  for (const order of ['after-first', 'persist-first']) {
    const h = harness({order, runId: `replay-${order}`});
    const tripping = replayToFuse(h);
    // Recorded: the transcript copy of call 6 was replaced by the stop text.
    // While its correction is pending it keeps the refusal it really got.
    assert.ok(probePillowAgain.recorded.resultHead.startsWith('This response was stopped'));
    assert.ok(tripping.text.startsWith(probePillow.recorded.resultHead), tripping.text);
    assert.equal(fs.existsSync(path.join(h.root, 'Playground')), false, 'refusals reserve and write nothing');

    const content = synthesized(recordedWrite.args.contentChars);
    const remedy = h.write({path: recordedWrite.args.path, content}, recordedWrite.id);
    assert.notEqual(remedy.decision?.block, true, remedy.decision?.blockReason);
    assert.equal(remedy.executed.path, recordedWrite.args.path, 'accepted exactly as written');
    assert.equal(fs.readFileSync(path.join(h.root, 'Playground', 'photo-renamer', 'rename_photos.py'), 'utf8'), content);
    assert.equal(projectRecord(h.root), 'Playground/photo-renamer');
    assert.match(remedy.text, /^Successfully wrote 5853 bytes/);
    // The model-end boundary between the trip and call 7 signaled exec
    // control once, as on main; nothing was aborted.
    assert.deepEqual(h.signals, [h.context.runId]);

    // Recorded call 8 revises the file; later commands get the project cwd.
    const rewrite = h.write({path: recordedRewrite.args.path, content: synthesized(recordedRewrite.args.contentChars, 'y')}, recordedRewrite.id);
    assert.notEqual(rewrite.decision?.block, true, rewrite.decision?.blockReason);
    const run = h.exec({command: 'python3 rename_photos.py --help'});
    assert.notEqual(run.decision?.block, true, run.decision?.blockReason);
    assert.equal(run.executed.workdir, '/workspace/Playground/photo-renamer');
    assert.deepEqual(h.aborts, []);
    assert.notEqual(h.guard.deliveryVerificationForRun(h.context.runId).status, 'failed');

    // The successful binding write reset the consecutive count: the ordinary
    // fuse applies from zero again, three failures pass and the fourth stops.
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      const failed = h.exec({command: `python3 rename_photos.py sample-${i}`}, undefined, {ok: false});
      assert.notEqual(failed.decision?.block, true, failed.decision?.blockReason);
    }
    stoppedAtFuse(h, h.write({path: 'Playground/photo-renamer/rename_photos.py', content: synthesized(400, 'z')}));
    assert.equal(fs.readFileSync(path.join(h.root, 'Playground', 'photo-renamer', 'rename_photos.py'), 'utf8'),
      synthesized(recordedRewrite.args.contentChars, 'y'));
  }
});

test('a model that ignores the refusal is stopped exactly as on main: the first call after the fuse spends the correction', () => {
  for (const order of ['after-first', 'persist-first']) {
    const h = harness({order, runId: `ignore-${order}`});
    replayToFuse(h);
    // The recorded probe once more: refused with the finalization instruction.
    stoppedAtFuse(h, h.exec(probePillowAgain.args));
    // The remedy now comes too late. The answer turn's tool call ends the run.
    const late = h.write({path: recordedWrite.args.path, content: synthesized(recordedWrite.args.contentChars)});
    assert.equal(late.decision?.block, true);
    assert.equal(late.decision.blockReason, RUN_PROGRESS_STOP_REASON);
    assert.equal(h.aborts.length, 1);
    assert.equal(fs.existsSync(path.join(h.root, 'Playground')), false);
    assert.equal(h.write({path: recordedWrite.args.path, content: 'x'}).decision.blockReason, RUN_PROGRESS_STOP_REASON);
  }
});

test('a forfeited answer turn admits no remedy: the next tool call ends the run as on main', () => {
  // Two model calls without a tool boundary: the second forfeits the answer
  // turn, and the next tool call ends the run exactly as on main.
  const forfeited = harness({runId: 'forfeited'});
  replayToFuse(forfeited);
  forfeited.modelRound();
  const late = forfeited.write({path: recordedWrite.args.path, content: 'x'});
  assert.equal(late.decision?.block, true);
  assert.equal(late.decision.blockReason, RUN_PROGRESS_STOP_REASON);
  assert.equal(forfeited.aborts.length, 1);
  assert.equal(fs.existsSync(path.join(forfeited.root, 'Playground')), false);
});

test('only a write the router binds as the project is the remedy; any other first call keeps the stop', () => {
  const others = {
    'bare filename': ['write', {path: 'rename_photos.py', content: 'x'}],
    'generic folder': ['write', {path: 'Playground/project/rename_photos.py', content: 'x'}],
    'no project folder': ['write', {path: 'Playground/rename_photos.py', content: 'x'}],
    // The router would bind this misspelling, but it is not the spelling the
    // refusal asked for; the conservative remedy check does not admit it.
    'lowercase spelling': ['write', {path: 'playground/photo-renamer/rename_photos.py', content: 'x'}],
    'outside the workspace': ['write', {path: '/tmp/photo-renamer/rename_photos.py', content: 'x'}],
    'edit': ['edit', {path: 'Playground/photo-renamer/rename_photos.py', oldText: 'a', newText: 'b'}],
    'patch': ['apply_patch', {input: '*** Begin Patch\n*** Add File: Playground/photo-renamer/rename_photos.py\n+x\n*** End Patch'}],
    'mkdir': ['exec', {command: 'mkdir -p Playground/photo-renamer'}],
    'read': ['read', {path: 'Playground/photo-renamer/rename_photos.py'}],
  };
  for (const [name, [tool, args]] of Object.entries(others)) {
    const h = harness({runId: `other-${name.replaceAll(' ', '-')}`});
    replayToFuse(h);
    stoppedAtFuse(h, h.call(tool, args, () => { throw new Error(`${name} must not run`); }));
    const late = h.write({path: recordedWrite.args.path, content: 'x'});
    assert.equal(late.decision.blockReason, RUN_PROGRESS_STOP_REASON, name);
    assert.equal(fs.existsSync(path.join(h.root, 'Playground')), false, name);
  }
  // A nested Tool Search child never receives the allowance.
  const nested = harness({runId: 'nested-child'});
  replayToFuse(nested);
  stoppedAtFuse(nested, nested.write({path: recordedWrite.args.path, content: 'x'}, 'tool_search_code:call_1:1'));
  assert.equal(fs.existsSync(path.join(nested.root, 'Playground')), false);
});

test('a refusal followed by a different failing tool still counts', () => {
  // Below the fuse: three refusals and a failed inspection are four
  // consecutive failures. A measured failure is not correctable.
  const measured = harness({runId: 'measured-fourth'});
  for (const recorded of [probeAll, probeEach, probeEachRoot]) refusedProbe(measured, recorded);
  const listing = measured.exec({command: 'ls test_photos'}, undefined, {ok: false});
  assert.notEqual(listing.decision?.block, true, 'inspection before the first write stays allowed');
  assert.equal(listing.text, RUN_PROGRESS_STOP_REASON, 'charged as always: the fuse trips at the fourth failure');
  stoppedAtFuse(measured, measured.write({path: recordedWrite.args.path, content: 'x'}));
  assert.equal(fs.existsSync(path.join(measured.root, 'Playground')), false);

  // At the fuse: the admitted remedy itself fails. Its failure is charged on
  // top of the tripped count, so the next call is stopped, and the spent
  // correction is not granted again.
  for (const order of ['after-first', 'persist-first']) {
    const h = harness({order, runId: `remedy-fails-${order}`});
    replayToFuse(h);
    const failed = h.write({path: recordedWrite.args.path, content: 'x'}, undefined, {fail: true});
    assert.notEqual(failed.decision?.block, true, 'admitted');
    assert.equal(failed.text, RUN_PROGRESS_STOP_REASON);
    stoppedAtFuse(h, h.write({path: recordedWrite.args.path, content: 'x'}));
    assert.equal(h.write({path: 'Playground/photo-renamer/other.py', content: 'x'}).decision.blockReason, RUN_PROGRESS_STOP_REASON);
    assert.equal(fs.existsSync(path.join(h.root, 'Playground', 'photo-renamer', 'rename_photos.py')), false);
  }
});

test('the total and round caps are unchanged: a refusal that reaches either grants nothing', () => {
  // Total cap: the twelfth failure is also the fourth consecutive refusal.
  const total = harness({runId: 'total-cap'});
  const inspections = ['pwd', 'ls', 'ls -la'];
  let probes = 0;
  const probe = () => refusedProbe(total, {args: {command: `python3 -c "import piexif; print(${++probes})"`}});
  for (let streak = 0; streak < 3; streak++) {
    for (let i = 0; i < (streak < 2 ? 3 : 2); i++) probe();
    assert.notEqual(total.exec({command: inspections[streak]}).decision?.block, true);
  }
  for (let i = 0; i < 3; i++) probe();
  const twelfth = probe();
  assert.equal(probes, RUN_PROGRESS_LIMITS.totalFailures);
  assert.equal(twelfth.text, RUN_PROGRESS_STOP_REASON, 'no correction pends at the total cap');
  stoppedAtFuse(total, total.write({path: recordedWrite.args.path, content: 'x'}));
  assert.equal(fs.existsSync(path.join(total.root, 'Playground')), false);

  // Round cap: with the fuse tripped in round 8, the model call that would
  // send the remedy is round 9, which exceeds roundsWithoutProgress.
  for (const [idle, admitted] of [[3, true], [4, false]]) {
    const h = harness({runId: `round-cap-${idle}`});
    for (let i = 0; i < idle; i++) h.modelRound();
    for (const recorded of [probeAll, probeEach, probeEachRoot, probePillow]) refusedProbe(h, recorded);
    const remedy = h.write({path: recordedWrite.args.path, content: 'x'});
    if (admitted) assert.notEqual(remedy.decision?.block, true, remedy.decision?.blockReason);
    else stoppedAtFuse(h, remedy);
    assert.equal(fs.existsSync(path.join(h.root, 'Playground', 'photo-renamer', 'rename_photos.py')), admitted);
  }
});

test('the other correctable refusal: a bare-filename write that trips the fuse admits the suggested write once', () => {
  const h = harness({runId: 'project-path'});
  for (const recorded of [probeAll, probeEach, probeEachRoot]) refusedProbe(h, recorded);
  const refused = h.write({path: 'rename_photos.py', content: 'x'});
  assert.equal(refused.decision?.block, true);
  assert.deepEqual(Object.keys(refused.decision).sort(), ['block', 'blockReason']);
  const suggested = /with path (\S+) and the same content/.exec(refused.decision.blockReason)?.[1];
  assert.equal(suggested, 'Playground/rename-photos/rename_photos.py');
  assert.ok(refused.text.startsWith('Not written:'), 'the pending correction keeps the refusal text');
  const accepted = h.write({path: suggested, content: 'x'});
  assert.notEqual(accepted.decision?.block, true, accepted.decision?.blockReason);
  assert.equal(projectRecord(h.root), 'Playground/rename-photos');
});

test('run budget: a correctable refusal that trips the global fuse leaves exactly one corrected attempt', () => {
  const trip = (budget, kind = 'first-write', prefix = 'refusal') => {
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      assert.equal(budget.exhausted, false);
      assert.equal(budget.pendingCorrection, undefined, 'nothing pends below the fuse');
      budget.observeResult({callId: `${prefix}-${i}`, tool: 'exec', failed: true, correctableRefusal: kind});
    }
    assert.equal(budget.exhausted, true, 'charged as always: the fuse trips');
  };
  const declined = createRunProgressBudget();
  trip(declined);
  assert.equal(declined.pendingCorrection, 'first-write');
  assert.equal(declined.correctFuse(false), false);
  assert.equal(declined.pendingCorrection, undefined, 'the first call after the fuse spends it');
  assert.equal(declined.correctFuse(true), false);
  assert.equal(declined.exhausted, true);

  // Admitted: counters are kept, so a failure stops at once and a success resets.
  const failing = createRunProgressBudget();
  trip(failing);
  assert.equal(failing.correctFuse(true), true);
  assert.equal(failing.exhausted, false);
  failing.observeResult({callId: 'remedy', tool: 'write', failed: true});
  assert.equal(failing.exhausted, true);
  assert.equal(failing.pendingCorrection, undefined);

  const corrected = createRunProgressBudget();
  trip(corrected);
  assert.equal(corrected.correctFuse(true), true);
  corrected.observeResult({callId: 'remedy', tool: 'write', params: {path: 'Playground/a/b.py'}, failed: false});
  for (let i = 0; i < 3; i++) corrected.observeResult({callId: `later-${i}`, tool: 'exec', failed: true});
  assert.equal(corrected.exhausted, false, 'the consecutive count restarted at zero');
  // Once per kind per run; another kind is its own single grant.
  corrected.observeResult({callId: 'again', tool: 'exec', failed: true, correctableRefusal: 'first-write'});
  assert.equal(corrected.exhausted, true);
  assert.equal(corrected.pendingCorrection, undefined, 'no second grant for the same kind');

  const kinds = createRunProgressBudget();
  trip(kinds, 'first-write', 'a');
  assert.equal(kinds.correctFuse(true), true);
  kinds.observeResult({callId: 'b-0', tool: 'write', failed: true, correctableRefusal: 'project-path'});
  assert.equal(kinds.pendingCorrection, 'project-path');
  assert.equal(kinds.correctFuse(true), true);
  kinds.observeResult({callId: 'c-0', tool: 'write', failed: true, correctableRefusal: 'project-path'});
  kinds.observeResult({callId: 'c-1', tool: 'write', failed: true, correctableRefusal: 'first-write'});
  assert.equal(kinds.exhausted, true);
  assert.equal(kinds.pendingCorrection, undefined);
  assert.equal(kinds.correctFuse(true), false);
});

test('run budget: the total cap, round cap, lanes and external stops never leave a correction', () => {
  const total = createRunProgressBudget();
  let failures = 0;
  for (const streak of [3, 3, 2]) {
    for (let i = 0; i < streak; i++) total.observeResult({callId: `f-${++failures}`, tool: 'exec', failed: true});
    total.observeResult({callId: `s-${failures}`, tool: 'read', params: {path: String(failures)}, failed: false});
  }
  for (let i = 0; i < 4; i++) total.observeResult({callId: `r-${i}`, tool: 'exec', failed: true, correctableRefusal: 'first-write'});
  assert.equal(failures + 4, RUN_PROGRESS_LIMITS.totalFailures);
  assert.equal(total.exhausted, true);
  assert.equal(total.pendingCorrection, undefined, 'failure 12 is also the fourth consecutive one');

  const rounds = createRunProgressBudget();
  for (let i = 0; i < 4; i++) rounds.beginModelRound();
  for (let i = 0; i < 4; i++) {
    assert.equal(rounds.beginModelRound(), false);
    rounds.observeResult({callId: `r-${i}`, tool: 'exec', failed: true, correctableRefusal: 'first-write'});
  }
  assert.equal(rounds.pendingCorrection, 'first-write');
  assert.equal(rounds.beginModelRound(), true);
  assert.equal(rounds.pendingCorrection, undefined, 'round 9 exceeds the round cap');
  assert.equal(rounds.correctFuse(true), false);

  const lane = createRunProgressBudget();
  for (let i = 0; i < 4; i++) lane.observeResult({callId: `w-${i}`, tool: 'exec', lane: 'workspace', failed: true, correctableRefusal: 'first-write'});
  assert.equal(lane.laneExhausted('workspace'), true);
  assert.equal(lane.pendingCorrection, undefined, 'a lane fuse is not relaxed');

  const stopped = createRunProgressBudget();
  for (let i = 0; i < 4; i++) stopped.observeResult({callId: `r-${i}`, tool: 'exec', failed: true, correctableRefusal: 'first-write'});
  stopped.stop();
  assert.equal(stopped.pendingCorrection, undefined);
  assert.equal(stopped.correctFuse(true), false);
  assert.equal(stopped.exhausted, true);
});

// A seeded random walk over results and model rounds. Without admissions the
// budget with correctable kinds matches one without them at every step. With
// admissions, the hard bounds still hold against counts the test keeps
// itself: 12 failures observed while open, or more than 8 model rounds since
// the last success, always stop the response, and an open response has at
// most one consecutive failure beyond the fuse per admission in that streak,
// with at most one admission per kind (two kinds here) in the whole run.
test('run budget: correctable kinds change nothing unless admitted, and admissions keep every hard bound', (t) => {
  let seed = 0x5eed1234;
  const random = () => { seed = (seed * 1103515245 + 12345) >>> 0; return seed / 0x100000000; };
  let admissions = 0, rescues = 0;
  for (let trace = 0; trace < 400; trace++) {
    const admit = trace % 2 === 1;
    const plain = createRunProgressBudget();
    const marked = createRunProgressBudget();
    let admitted = false, rescued = false, openFailures = 0, sinceSuccess = 0, streak = 0, streakAdmissions = 0, runAdmissions = 0;
    for (let step = 0; step < 60; step++) {
      const roll = random();
      if (roll < 0.2) {
        plain.beginModelRound();
        const wasOpen = !marked.exhausted;
        marked.beginModelRound();
        if (wasOpen) sinceSuccess += 1;
      } else {
        const callId = `call-${step}`;
        const failed = roll < 0.75;
        const kind = failed && random() < 0.7 ? ['first-write', 'project-path'][Math.floor(random() * 2)] : undefined;
        const result = failed ? {callId, tool: 'exec', failed: true}
          : random() < 0.2 ? {callId, tool: 'tool_search', params: {query: String(step)}, failed: false}
            : {callId, tool: 'read', params: {path: `file-${step}`}, failed: false};
        const wasOpen = !marked.exhausted;
        plain.observeResult(result);
        marked.observeResult({...result, correctableRefusal: kind});
        if (wasOpen && failed) { openFailures += 1; streak += 1; }
        if (wasOpen && !failed && result.tool === 'read') {
          sinceSuccess = 0; streak = 0; streakAdmissions = 0;
          // Progress the unmarked budget had already stopped before.
          if (admitted && plain.exhausted) rescued = true;
        }
      }
      if (marked.pendingCorrection !== undefined) {
        const accepted = admit && random() < 0.8;
        if (marked.correctFuse(accepted)) {
          admitted = true;
          admissions += 1;
          streakAdmissions += 1;
          runAdmissions += 1;
        }
      }
      if (!admitted) assert.equal(marked.exhausted, plain.exhausted, `trace ${trace} step ${step}`);
      if (openFailures >= RUN_PROGRESS_LIMITS.totalFailures || sinceSuccess > RUN_PROGRESS_LIMITS.roundsWithoutProgress) {
        assert.equal(marked.exhausted, true, `trace ${trace} step ${step}: total or round cap exceeded while open`);
      }
      if (!marked.exhausted) assert.ok(streak < RUN_PROGRESS_LIMITS.consecutiveFailures + streakAdmissions, `trace ${trace} step ${step}`);
      assert.ok(runAdmissions <= 2, `trace ${trace}: one admission per kind`);
    }
    if (rescued) rescues += 1;
  }
  t.diagnostic(`admissions ${admissions}, traces with progress after the unmarked budget stopped ${rescues}`);
  assert.ok(admissions > 20 && rescues > 5, `admissions ${admissions}, rescues ${rescues}`);
});

test('progress finalization: disarm returns to idle only before the instruction was delivered', () => {
  const pending = createProgressFinalization();
  assert.equal(pending.disarm(), false, 'idle stays idle');
  pending.arm(true);
  pending.modelCallStarted();
  assert.equal(pending.disarm(), true);
  assert.equal(pending.phase, 'idle');
  // Re-armed later, the uninstructed-call allowance starts over.
  pending.arm(true);
  assert.equal(pending.modelCallStarted(), 'pending');
  for (const setup of [
    (f) => { f.arm(false); },
    (f) => { f.arm(true); f.toolBoundary(1, 'a'); },
    (f) => { f.arm(true); f.toolBoundary(1, 'a'); f.modelCallStarted(); },
    (f) => { f.arm(true); f.accept('An answer written from the evidence already returned by tools.'); },
  ]) {
    const finalization = createProgressFinalization();
    setup(finalization);
    const phase = finalization.phase;
    assert.equal(finalization.disarm(), false, phase);
    assert.equal(finalization.phase, phase);
  }
});

test('router: only the two refusals before the first project file are correctable, and bindsNewProject matches its binding', () => {
  const fresh = () => {
    const root = workspace();
    const state = {};
    const call = (tool, params, extra = {}) => routePlaygroundTool({state, tool, params, root, session: 'remedy-session', intent: TRACE.prompt, ...extra});
    return {root, state, call};
  };
  const gate = fresh();
  assert.equal(gate.call('exec', {command: probePillow.args.command}).correctable, 'playground-first-write');
  assert.equal(gate.call('apply_patch', {input: '*** Begin Patch\n*** Add File: x.py\n+x\n*** End Patch'}).correctable, 'playground-first-write');
  assert.equal(gate.call('write', {path: 'rename_photos.py', content: 'x'}).correctable, 'playground-project-path');
  assert.ok(gate.call('write', {path: 'src/main.py', content: 'x'}).blockReason.startsWith(CORRECTION_START));
  assert.equal(gate.call('write', {path: 'src/main.py', content: 'x'}).correctable, 'playground-project-path');
  assert.equal(gate.call('exec', {command: 'python3 --version'}), undefined);

  // [remedy, bound by the router]: every admitted remedy is bound. The check
  // is narrower than the router, which also binds some spellings the
  // refusals never ask for.
  const cases = {
    'Playground/photo-renamer/rename_photos.py': [true, true],
    '/workspace/Playground/photo-renamer/rename_photos.py': [true, true],
    'CONFIGURED/Playground/photo-renamer/rename_photos.py': [true, true],
    'Playground/photo-renamer/tests/test_rename.py': [true, true],
    'Playground/x1/main.py': [true, true],
    'playground/photo-renamer/rename_photos.py': [false, true],
    'photo-renamer/rename_photos.py': [false, true],
    'Playground/project/rename_photos.py': [false, false],
    'Playground/rename_photos.py': [false, false],
    'rename_photos.py': [false, false],
    '/tmp/photo-renamer/rename_photos.py': [false, false],
    '../Playground/photo-renamer/rename_photos.py': [false, false],
  };
  for (const [spelled, [remedy, binds]] of Object.entries(cases)) for (const wrapped of [false, true]) {
    const {root, state, call} = fresh();
    const target = spelled.replace(/^CONFIGURED/, root.replaceAll('\\', '/'));
    call('exec', {command: probePillow.args.command});
    const tool = wrapped ? 'tool_call' : 'write';
    const params = wrapped ? {id: 'openclaw:core:write', args: {path: target, content: 'x'}} : {path: target, content: 'x'};
    assert.equal(bindsNewProject({state, tool, params, root}), remedy, spelled);
    call(tool, params);
    assert.equal(Boolean(state.binding), binds, `${spelled}: bound by the router`);
  }
  // Read files are preserved by the router, not bound: never the remedy.
  const read = fresh();
  read.call('exec', {command: probePillow.args.command});
  const readPath = 'Playground/photo-renamer/rename_photos.py';
  assert.equal(bindsNewProject({state: read.state, tool: 'write', params: {path: readPath}, root: read.root, existingPaths: [readPath]}), false);
  // After the first project file, nothing is correctable and nothing binds anew.
  const bound = fresh();
  bound.call('write', {path: readPath, content: 'x'});
  assert.equal(bindsNewProject({state: bound.state, tool: 'write', params: {path: 'Playground/other-tool/main.py'}, root: bound.root}), false);
  const oldPrefix = bound.call('exec', {command: 'python3 photo-renamer/rename_photos.py'});
  assert.equal(oldPrefix.block, true);
  assert.equal(oldPrefix.correctable, undefined);
  // A state the router never initialized (owner-named paths, continuations)
  // has no gate to correct.
  assert.equal(bindsNewProject({state: {}, tool: 'write', params: {path: readPath}, root: read.root}), false);
  assert.equal(bindsNewProject({state: read.state, tool: 'exec', params: {command: 'ls'}, root: read.root}), false);
});
