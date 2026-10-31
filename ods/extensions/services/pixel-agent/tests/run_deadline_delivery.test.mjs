// Replays the RTX Spark run of 2026-10-16: an owner's game fix worked for the
// full 30-minute run deadline and the owner received OpenClaw's advice to
// raise agents.defaults.timeoutSeconds, which ODS resets. The guard must warn
// the model before the deadline and deliver what happened when it hits.
// The ingress case runs over real loopback TCP (also on Windows).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_EARLIER_NOTE} from '../plugin/preview-explanation.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {RUN_DEADLINE_GRACE_MS} from '../plugin/run-deadline.mjs';

const RUN_ID = 'chatcmpl_3d1f6c0a-6b1e-4f7a-9a52-2e6f0c7d1a30';
const USER = 'ods-' + 'a'.repeat(64);
const OWNER = {agentId: 'pixel', runId: RUN_ID, sessionId: 'spark-session', sessionKey: `agent:pixel:openai-user:${USER}`, trigger: 'user'};
const PROMPT = 'Fix the wolf and boss hitboxes in my game.';
const CAPABILITIES = {runTimeoutSeconds: 1800, executionHost: 'sandbox', workspaceRoot: '/workspace'};
const OPENCLAW_TIMEOUT_TEXT = 'Request timed out before a response was generated. Please try again, or increase `agents.defaults.timeoutSeconds` in your config.';
const SPARK_CONTENT = `LLM request failed.\n\n${OPENCLAW_TIMEOUT_TEXT}`;
const STOP_COPY = '**Portal stopped this response after 30 minutes, the most one response can run.** This request needed more time. ' +
  'Files saved before the stop are kept, but the last change may be unfinished, so the project may not work yet. ' +
  'Send "continue" to pick up where it left off.' +
  ' If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';
const UNCHANGED_STOP_COPY = '**Portal stopped this response after 30 minutes, the most one response can run.** ' +
  'This request needed more time. Send "continue" to pick up where it left off.' +
  ' If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';
const NOTICE = /\[ODS Pixel time limit\]/;
const minutes = value => value * 60000;
// The deadline fired while the model was still editing (288 model calls, 6 edits).
const CUT_OFF = {success: false, durationMs: minutes(30), messages: [
  {role: 'user', content: [{type: 'text', text: PROMPT}]},
  {role: 'assistant', stopReason: 'toolUse', content: [{type: 'toolCall', id: 'edit-6', name: 'edit', arguments: {path: 'game/ui.js'}}]},
  {role: 'toolResult', toolCallId: 'edit-6', toolName: 'edit', content: [{type: 'text', text: 'Successfully replaced text in game/ui.js.'}]},
  {role: 'assistant', stopReason: 'aborted', content: []},
]};

function fixture({context = OWNER, capabilities = CAPABILITIES, prompt = PROMPT, goal = false, abortAcknowledged = () => true,
  execControl} = {}) {
  let clock = 0;
  const aborts = [];
  // The guard's own timers, which run as the clock passes them (at).
  const timers = new Set();
  const guard = createToolLoopGuard({now: () => clock, goalRun: () => goal, execControl,
    setTimer: (run, ms) => { const timer = {run, due: clock + ms}; timers.add(timer); return timer; },
    clearTimer: timer => { timers.delete(timer); },
    abortRun: sessionId => { aborts.push(sessionId); return abortAcknowledged(); },
    abortRunAndDrain: async () => ({aborted: true, drained: true})});
  const begin = () => guard.observeRun(context, 'pixel', {prompt}, capabilities);
  begin();
  let calls = 0;
  // An agent turn's model call starts (OpenClaw's run for the attempt is
  // live) and, with end, finishes.
  let modelCall;
  const model = () => { modelCall = `model-${++calls}`; guard.observeModelCall({callId: modelCall}, context); };
  const modelEnd = () => guard.observeModelEnd({callId: modelCall, outcome: 'completed'}, context);
  // A tool call the guard admits, still running until its after_tool_call.
  const start = (toolCallId, toolName = 'read', params = {path: 'game/ui.js'}) => {
    const before = guard.beforeToolCall({toolName, params, toolCallId}, {...context, toolName, toolCallId});
    assert.notEqual(before?.block, true, before?.blockReason);
  };
  // The attempt writes a reply (before_message_write).
  const reply = message => guard.observeReplyEnd({message: {role: 'assistant', ...message}}, context);
  const invoke = (toolName, params, result, toolCallId) => {
    const ctx = {...context, toolName, toolCallId};
    const before = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    assert.notEqual(before?.block, true, before?.blockReason);
    guard.afterToolCall({toolName, params: before?.params ?? params, result, toolCallId}, ctx);
    guard.toolResultPersist({toolName, toolCallId, message: {role: 'toolResult', toolName, toolCallId, ...result}}, ctx);
  };
  // A saved edit, like the Spark run's six.
  const edit = (path = 'game/ui.js') => invoke('edit', {path, oldText: 'wolfHitShape(', newText: 'wolfHitShapes('},
    {content: [{type: 'text', text: `Successfully replaced text in ${path}.`}], details: {status: 'completed'}}, `edit-${++calls}`);
  // A plain read result: no other projection applies to it.
  const read = (text = 'export function wolfHitShapes() { return []; }') => {
    const toolCallId = `read-${++calls}`;
    const message = {role: 'toolResult', toolName: 'read', toolCallId, content: [{type: 'text', text}]};
    const original = structuredClone(message);
    const persisted = guard.toolResultPersist({toolName: 'read', toolCallId, message}, {...context, toolName: 'read', toolCallId});
    return {original, persisted: persisted?.message ?? message};
  };
  const exec = (command, exitCode = 0, text = 'ok') => {
    const toolCallId = `exec-${++calls}`;
    const ctx = {...context, toolName: 'exec', toolCallId};
    const params = {command, workdir: '/workspace/game'};
    const before = guard.beforeToolCall({toolName: 'exec', params, toolCallId}, ctx);
    assert.notEqual(before?.block, true, before?.blockReason);
    const result = {content: [{type: 'text', text}], details: {status: 'completed', exitCode}};
    guard.afterToolCall({toolName: 'exec', params: {...params, ...before?.params}, toolCallId, result}, ctx);
    const message = {role: 'toolResult', toolName: 'exec', toolCallId, ...structuredClone(result)};
    const original = structuredClone(message);
    const persisted = guard.toolResultPersist({toolName: 'exec', toolCallId, message}, ctx);
    return {original, persisted: persisted?.message ?? message};
  };
  // A verified publication of game/ (the host snapshot digest the guard checks).
  const publish = () => {
    const content = '<!doctype html><title>Wolf Run</title><script src="ui.js"></script>';
    invoke('write', {path: 'game/index.html', content}, {content: [{type: 'text', text: 'written'}], details: {status: 'completed'}}, 'write-index');
    const length = (bytes, value) => { const buffer = Buffer.alloc(bytes); bytes === 4 ? buffer.writeUInt32BE(value) : buffer.writeBigUInt64BE(BigInt(value)); return buffer; };
    const sha256 = createHash('sha256').update(length(4, 10)).update('index.html').update(length(8, content.length)).update(content).digest('hex');
    const siteId = `site-${sha256.slice(0, 24)}`;
    const details = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: 'game', port: 9437,
      url: `http://${siteId}.localhost:9437/${siteId}/`, siteId, files: 1, bytes: content.length, sha256, entryFile: 'index.html',
      entrySha256: createHash('sha256').update(content).digest('hex'), httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
    invoke('pixel_ods_workspace_preview', {relativeDirectory: 'game'}, {content: [{type: 'text', text: 'published'}], details}, 'preview');
    return details.url;
  };
  const at = value => {
    clock = minutes(value);
    for (const timer of [...timers].sort((a, b) => a.due - b.due)) if (timer.due <= clock && timers.delete(timer)) timer.run();
  };
  return {guard, aborts, timers, begin, model, modelEnd, start, reply, read, exec, edit, publish, at};
}

// When Portal stops a response: 15 s before its 30 minutes are up.
const STOP_AT = 30 - RUN_DEADLINE_GRACE_MS / 60000;
const ANSWER = {stopReason: 'stop', content: [{type: 'text', text: 'Both hitboxes are fixed; all six levels load.'}]};

const notices = message => message.content.filter(block => block?.type === 'text' && NOTICE.test(block.text));

test('the model is warned at minute 25 and minute 28, once each, after the result it belongs to', () => {
  const f = fixture();
  f.at(24.99);
  assert.equal(notices(f.read().persisted).length, 0);
  f.at(25);
  const first = f.read();
  assert.deepEqual(first.persisted.content.slice(0, -1), first.original.content, 'the result itself is unchanged');
  assert.match(first.persisted.content.at(-1).text, /^\[ODS Pixel time limit\] About 5 minutes remain .* 30-minute limit/);
  f.at(26);
  assert.equal(notices(f.read().persisted).length, 0, 'delivered once');
  assert.equal(notices(f.exec('node --check game/ui.js').persisted).length, 0);
  f.at(28);
  const second = f.exec('node --check game/chars.js');
  assert.deepEqual(second.persisted.content[0], second.original.content[0]);
  assert.equal(notices(second.persisted).length, 1);
  assert.match(second.persisted.content.at(-1).text, /^\[ODS Pixel time limit\] About 2 minutes remain/,
    'after the exec receipt facts, as the last block');
  f.at(29);
  assert.equal(notices(f.read().persisted).length, 0);
});

for (const [name, context, capabilities, prompt] of [
  ['a cron turn', {...OWNER, trigger: 'cron', sessionKey: 'agent:pixel:cron:nightly'}, CAPABILITIES],
  ['a subagent', {...OWNER, sessionKey: 'agent:pixel:subagent:7f2c'}, CAPABILITIES],
  ['a turn without a trigger', {...OWNER, trigger: undefined}, CAPABILITIES],
  ['an owner turn without a configured timeout', OWNER, {...CAPABILITIES, runTimeoutSeconds: undefined}],
  ['a team worker in the owner session', OWNER, CAPABILITIES, `You are the Builder in the owner's Portal team.\n\n${PROMPT}`],
]) test(`no notice, deadline copy or stop for ${name}`, () => {
  const f = fixture({context, capabilities, prompt});
  // A later attempt too, whose own timer would come after the deadline.
  f.at(10);
  f.begin();
  f.model();
  for (const value of [25, 28]) { f.at(value); assert.equal(notices(f.read().persisted).length, 0); }
  f.at(31);
  assert.deepEqual(f.aborts, [], 'Portal never stops it at the deadline');
  f.guard.observeAgentEnd(CUT_OFF, context);
  assert.doesNotMatch(f.guard.deliveryVerificationForRun(RUN_ID).text ?? '', /Portal stopped this response/);
});

// The ingress stops the whole response at 32 minutes, whatever attempt it is
// on, with no reply at all. A retry's fresh runtime timer must not move the
// notices past it, and Portal stops a later attempt before the response's
// deadline itself (laptop, 2026-10-30: a goal ended on such a cut round).
test('a later attempt keeps the response\'s notice schedule and is stopped just before the response\'s deadline', () => {
  const f = fixture();
  f.at(10);
  f.begin();
  f.model();
  f.at(24.99);
  assert.equal(notices(f.read().persisted).length, 0);
  f.at(25);
  assert.match(notices(f.read().persisted)[0].text, /^\[ODS Pixel time limit\] About 5 minutes remain/);
  f.at(26);
  f.begin();
  f.model();
  assert.equal(notices(f.read().persisted).length, 0, 'a delivered stage stays delivered');
  f.at(28);
  assert.match(notices(f.read().persisted)[0].text, /^\[ODS Pixel time limit\] About 2 minutes remain/);
  // This attempt ran 4 of its own 30 minutes; the response ran all 30.
  f.at(STOP_AT - 0.001);
  assert.deepEqual(f.aborts, []);
  f.at(STOP_AT);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: UNCHANGED_STOP_COPY});
  const reply = f.guard.replyPayloadSending({runId: RUN_ID, kind: 'final', payload: {text: 'No response from OpenClaw.', isError: true}});
  assert.equal(reply.payload.text, UNCHANGED_STOP_COPY);
});

test('a goal round\'s retried attempt stopped at the deadline says which limit stopped it; a chat\'s does not', () => {
  for (const goal of [true, false]) {
    const f = fixture({goal});
    f.at(4);
    f.edit();
    // A compaction retry or a revision: a new attempt with its own timer.
    f.at(10);
    f.begin();
    f.model();
    f.at(STOP_AT);
    assert.deepEqual(f.aborts, [OWNER.sessionId]);
    f.guard.observeAgentEnd(CUT_OFF, OWNER);
    assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID),
      {status: 'failed', text: STOP_COPY, ...(goal ? {stop: 'run-deadline'} : {})});
  }
});

// Its own timer's recovery can outlast the ingress: a compaction and a retry
// when the prompt fills most of the window, or a grace of up to
// compaction.timeoutSeconds (30 more minutes on ODS) for a compaction in flight.
test('the first attempt is stopped just before its own timer', () => {
  const f = fixture({goal: true});
  f.model();
  f.at(12);
  f.edit();
  f.at(STOP_AT - 0.001);
  assert.deepEqual(f.aborts, []);
  f.at(STOP_AT);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: STOP_COPY, stop: 'run-deadline'});
});

// An abort before an attempt's first model call cannot reach the prompt
// OpenClaw is about to start, and one while a call is still setting up its
// stream leaves the attempt hung until its own timer (real harness).
const STREAM_STARTED = 3 / 60;
test('a stop due with no call under way waits for one, and gives a new call time to set up', () => {
  const f = fixture({goal: true});
  f.at(10);
  f.begin();
  f.at(STOP_AT);
  assert.deepEqual(f.aborts, []);
  f.at(29.9);
  f.model();
  f.at(29.9 + STREAM_STARTED - 0.001);
  assert.deepEqual(f.aborts, []);
  f.at(29.9 + STREAM_STARTED);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: UNCHANGED_STOP_COPY, stop: 'run-deadline'});
  // A call that began just before the deadline gets the same time.
  const recent = fixture({goal: true});
  recent.at(STOP_AT - 0.01);
  recent.model();
  recent.at(STOP_AT);
  assert.deepEqual(recent.aborts, []);
  recent.at(STOP_AT - 0.01 + STREAM_STARTED);
  assert.deepEqual(recent.aborts, [OWNER.sessionId]);
  // One that begins past the deadline, such as the revision an answer asked for.
  const late = fixture({goal: true});
  late.model();
  late.at(STOP_AT - 1);
  late.reply(ANSWER);
  late.at(30.5);
  late.begin();
  late.at(30.5);
  late.model();
  late.at(30.5 + STREAM_STARTED - 0.001);
  assert.deepEqual(late.aborts, []);
  late.at(30.5 + STREAM_STARTED);
  assert.deepEqual(late.aborts, [OWNER.sessionId]);
});

test('past the deadline a tool call is refused and the stop lands there', () => {
  const f = fixture({goal: true});
  f.at(10);
  f.begin();
  f.at(STOP_AT);
  const ctx = {...OWNER, toolName: 'read', toolCallId: 'late-read'};
  const decision = f.guard.beforeToolCall({toolName: 'read', params: {path: 'game/ui.js'}, toolCallId: 'late-read'}, ctx);
  assert.equal(decision?.block, true);
  assert.match(decision.blockReason, /^Portal stopped this response at its time limit before this call ran\.$/);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  // No notice on the stopped run, not even after a compaction resets them.
  f.guard.observeCompaction(OWNER, 'start');
  f.guard.observeCompaction(OWNER, 'end');
  assert.equal(notices(f.read().persisted).length, 0);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: UNCHANGED_STOP_COPY, stop: 'run-deadline'});
});

// A hung test or dev server is a typical way to reach the limit.
test('a tool still running at the deadline is stopped there', () => {
  const f = fixture({goal: true});
  f.model();
  f.at(20);
  f.modelEnd();
  f.start('slow-test', 'exec', {command: 'npm test', workdir: '/workspace/game'});
  f.at(STOP_AT);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  // One that finished is not running, and a new model call starts a new turn.
  for (const finished of [true, false]) {
    const done = fixture({goal: true});
    done.model();
    done.at(20);
    done.modelEnd();
    done.start('read-1');
    if (finished) done.guard.afterToolCall({toolName: 'read', toolCallId: 'read-1', params: {path: 'game/ui.js'},
      result: {content: [{type: 'text', text: 'ok'}]}}, {...OWNER, toolName: 'read', toolCallId: 'read-1'});
    else { done.at(STOP_AT - 0.01); done.model(); }
    done.at(STOP_AT);
    assert.deepEqual(done.aborts, [], `finished=${finished}`);
  }
});

test('a stop the runtime did not take is made again at the attempt\'s next model call', () => {
  let acknowledged = false;
  const f = fixture({goal: true, abortAcknowledged: () => acknowledged});
  f.at(10);
  f.begin();
  f.model();
  f.at(STOP_AT);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  acknowledged = true;
  f.at(29.9);
  f.model();
  f.at(29.9 + STREAM_STARTED);
  assert.deepEqual(f.aborts, [OWNER.sessionId, OWNER.sessionId]);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: UNCHANGED_STOP_COPY, stop: 'run-deadline'});
});

// OpenClaw drops the answer of an attempt anything but its own timer aborted,
// and Pixel's finalization (preview checks, cited pages) runs after the answer.
test('an attempt that wrote its answer is never stopped, and is delivered as usual', () => {
  const f = fixture({goal: true});
  f.at(10);
  f.begin();
  f.model();
  f.at(STOP_AT - 0.5);
  f.reply({stopReason: 'toolUse', content: [{type: 'toolCall', id: 'edit-9', name: 'edit', arguments: {}}]});
  f.model();
  f.reply(ANSWER);
  f.at(31);
  assert.deepEqual(f.aborts, []);
  f.guard.observeAgentEnd({success: true, messages: [{role: 'user', content: PROMPT}, {role: 'assistant', ...ANSWER}]}, OWNER);
  assert.notEqual(f.guard.deliveryVerificationForRun(RUN_ID).status, 'failed');
  // A reply cut at the output limit is delivered too; one with tool calls is not an answer.
  const cut = fixture();
  cut.model();
  cut.reply({stopReason: 'length', content: [{type: 'text', text: 'The wolves now use two circles each and'}]});
  cut.at(31);
  assert.deepEqual(cut.aborts, []);
  // A further agent turn means that reply was not the last.
  const more = fixture();
  more.model();
  more.reply(ANSWER);
  more.model();
  more.at(STOP_AT);
  assert.deepEqual(more.aborts, [OWNER.sessionId]);
});

test('a tool-limit stop\'s answer turn and a refused delete keep their own stop', () => {
  const f = fixture();
  f.model();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    const toolCallId = `failed-${i}`;
    f.guard.toolResultPersist({toolCallId, message: {role: 'toolResult', toolName: 'web_fetch', toolCallId, isError: true,
      content: [{type: 'text', text: 'Web fetch failed (403)'}]}}, {...OWNER, toolName: 'web_fetch', toolCallId});
  }
  f.at(31);
  assert.deepEqual(f.aborts, []);
  const refused = fixture();
  refused.model();
  const ctx = {...OWNER, toolName: 'exec', toolCallId: 'rm-1'};
  assert.equal(refused.guard.beforeToolCall({toolName: 'exec', toolCallId: 'rm-1',
    params: {command: 'rm -rf /workspace/game/build', workdir: '/workspace/game'}}, ctx)?.block, true);
  refused.at(31);
  assert.deepEqual(refused.aborts, []);
});

test('Portal\'s stop ends a sandboxed command too, like every ODS stop', () => {
  const signalled = [];
  const f = fixture({execControl: {signal: runId => { signalled.push(runId); return true; }}});
  f.model();
  f.at(STOP_AT);
  assert.deepEqual(signalled, [RUN_ID]);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
});

test('Portal never stops an attempt that ended, a cancelled run, or a newer run in the chat', async () => {
  const ended = fixture();
  ended.model();
  ended.at(29);
  ended.guard.observeAgentEnd({success: true, messages: []}, OWNER);
  assert.equal(ended.timers.size, 0, 'agent_end cancels the stop');
  ended.at(60);
  assert.deepEqual(ended.aborts, []);
  // The owner's Stop aborts and drains the run itself.
  const cancelled = fixture();
  cancelled.at(10);
  cancelled.begin();
  cancelled.model();
  cancelled.at(20);
  cancelled.exec('node --check game/ui.js');
  assert.equal(await cancelled.guard.abortUserRun(USER), true);
  cancelled.at(31);
  assert.deepEqual(cancelled.aborts, []);
  // The owner's next message started a newer run in the same session.
  const replaced = fixture();
  replaced.at(10);
  replaced.begin();
  replaced.model();
  replaced.at(20);
  replaced.guard.observeRun({...OWNER, runId: 'chatcmpl_9e2a4b1c-3d5f-4a6b-8c7d-0e1f2a3b4c5d'}, 'pixel',
    {prompt: 'Now make the boss slower.'}, CAPABILITIES);
  replaced.at(31);
  assert.deepEqual(replaced.aborts, []);
});

test('a new attempt clears an earlier deadline classification', () => {
  const f = fixture();
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.equal(f.guard.deliveryVerificationForRun(RUN_ID).status, 'failed');
  f.begin();
  assert.notEqual(f.guard.deliveryVerificationForRun(RUN_ID).status, 'failed');
});

test('a compaction sends the stage now due again, with the same bytes', () => {
  const f = fixture();
  const compact = () => { f.guard.observeCompaction(OWNER, 'start'); f.guard.observeCompaction(OWNER, 'end'); };
  f.at(10);
  compact();
  assert.equal(notices(f.read().persisted).length, 0, 'nothing due, nothing sent');
  f.at(25);
  const [first] = notices(f.read().persisted);
  compact();
  f.at(26);
  assert.deepEqual(notices(f.read().persisted), [first]);
  assert.equal(notices(f.read().persisted).length, 0, 'once per compaction');
  f.at(28);
  const [second] = notices(f.read().persisted);
  assert.match(second.text, /About 2 minutes remain/);
  compact();
  f.at(29);
  assert.deepEqual(notices(f.read().persisted), [second]);
});

test('an ODS abort keeps its own outcome and ends the notices', () => {
  // A second call after the private-URL refusal is an ODS stop.
  const f = fixture({prompt: 'Open http://192.168.1.20:8080/ and fix the wolf hitboxes there.'});
  // Due a notice, just before Portal's own deadline stop.
  f.at(29.7);
  const call = toolCallId => f.guard.beforeToolCall({toolName: 'web_fetch', params: {url: 'http://192.168.1.20:8080/'}, toolCallId},
    {...OWNER, toolName: 'web_fetch', toolCallId});
  assert.equal(call('fetch-1')?.block, true);
  assert.equal(call('fetch-2')?.block, true);
  assert.deepEqual(f.aborts, [OWNER.sessionId]);
  const after = f.read();
  assert.deepEqual(after.persisted, after.original);
  const before = f.guard.deliveryVerificationForRun(RUN_ID);
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), before);
});

test('a budget stop keeps its own text, and its tool-free turn gets no notice', () => {
  const f = fixture();
  f.at(10);
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    const toolCallId = `failed-${i}`;
    f.guard.toolResultPersist({toolCallId, message: {role: 'toolResult', toolName: 'web_fetch', toolCallId, isError: true,
      content: [{type: 'text', text: 'Web fetch failed (403)'}]}}, {...OWNER, toolName: 'web_fetch', toolCallId});
  }
  f.at(26);
  // The answer turn is still ahead, so the persisted result carries its
  // instruction (progress-finalization.mjs), never a deadline notice.
  assert.deepEqual(f.read().persisted.content, [{type: 'text', text: PROGRESS_FINALIZATION_INSTRUCTION}]);
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.match(verification.text, new RegExp('^' + RUN_PROGRESS_STOP_REASON.slice(0, 40)));
  assert.doesNotMatch(verification.text, /Portal stopped this response/);
});

test('an owner cancel ends the notices and keeps the cancel outcome', async () => {
  const f = fixture();
  f.at(20);
  f.exec('node --check game/ui.js');
  assert.equal(await f.guard.abortUserRun(USER), true);
  f.at(26);
  const after = f.read();
  assert.deepEqual(after.persisted, after.original);
  const cancelled = f.guard.deliveryVerificationForRun(RUN_ID);
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), cancelled);
});

test('Spark replay: the deadline stop replaces the runtime advice in every delivery path', () => {
  const f = fixture();
  f.at(12); f.edit();
  f.at(25); f.read();
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.deepEqual(verification, {status: 'failed', text: STOP_COPY});
  const reply = f.guard.replyPayloadSending({runId: RUN_ID, kind: 'final', payload: {text: OPENCLAW_TIMEOUT_TEXT, isError: true}});
  assert.equal(reply.payload.text, STOP_COPY);
});

test('a response that changed no files is not told its project may be broken', () => {
  const f = fixture({prompt: 'Compare the newest local text-to-speech engines for me.'});
  f.at(20); f.read('Kokoro, Piper and XTTS release notes');
  f.at(30);
  f.guard.observeAgentEnd({...CUT_OFF, messages: [CUT_OFF.messages[0],
    {role: 'assistant', stopReason: 'aborted', content: []}]}, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), {status: 'failed', text: UNCHANGED_STOP_COPY});
});

for (const [name, end] of [
  ['a successful attempt', {...CUT_OFF, success: true}],
  ['a failure before the deadline', CUT_OFF],
  ['a completed final answer', {...CUT_OFF, messages: [...CUT_OFF.messages.slice(0, 3),
    {role: 'assistant', stopReason: 'stop', content: [{type: 'text', text: 'Both hitboxes are fixed; all six levels load.'}]}]}],
]) test(`no deadline copy for ${name}`, () => {
  const f = fixture();
  f.at(end === CUT_OFF ? 20 : 30);
  const before = f.guard.deliveryVerificationForRun(RUN_ID);
  f.guard.observeAgentEnd(end, OWNER);
  assert.deepEqual(f.guard.deliveryVerificationForRun(RUN_ID), before);
});

test('the last verified preview follows the deadline copy, with its receipt', () => {
  const f = fixture({prompt: `${PROMPT} Publish the game preview when it works.`});
  f.at(12);
  const url = f.publish();
  assert.equal(f.guard.verificationForRun(RUN_ID).preview?.url, url);
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.equal(verification.status, 'failed');
  assert.equal(verification.text, `${STOP_COPY}\n\n[Open last published preview](${url})\n\n` +
    'This is the last verified publication, not proof that all requested work completed.');
  assert.equal(verification.preview.url, url);
  assert.equal(verification.preview.kind, 'ods-pixel-workspace-preview');
});

test('a preview edited since it was published is linked once, with what it does not prove', () => {
  const f = fixture({prompt: `${PROMPT} Publish the game preview when it works.`});
  f.at(12);
  const url = f.publish();
  f.at(20);
  f.edit();
  const stale = f.guard.verificationForRun(RUN_ID);
  assert.equal(stale.status, 'failed');
  assert.equal(stale.preview?.url, url);
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  // The owner reads Portal's plain line about the stale preview, not its facts.
  assert.equal(verification.text, `${STOP_COPY}\n\n${PREVIEW_EARLIER_NOTE}\n\n[Open last published preview](${url})`);
  assert.match(stale.text, /may not include subsequent changes and does not verify completion/);
  assert.equal(verification.text.split(url).length, 2, 'one link');
  assert.deepEqual(verification.preview, stale.preview);
});

test('failed test facts follow the deadline copy', () => {
  const f = fixture();
  f.at(4);
  f.edit('game/test_hitboxes.py');
  f.at(5);
  f.exec('python3 -m unittest', 1, 'FAILED (failures=1)');
  const failed = f.guard.verificationForRun(RUN_ID);
  assert.equal(failed.status, 'failed');
  f.at(30);
  f.guard.observeAgentEnd(CUT_OFF, OWNER);
  const verification = f.guard.deliveryVerificationForRun(RUN_ID);
  assert.equal(verification.status, 'failed');
  assert.equal(verification.text, `${STOP_COPY}\n\n${failed.text}`);
});

test('the prompt hook passes the live configured run timeout', () => {
  const entry = readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const declaration = entry.match(/const runTimeoutSeconds = ([^;]*);/);
  assert.ok(declaration);
  const resolve = new Function('api', `return (${declaration[1]});`);
  const config = timeoutSeconds => ({agents: {defaults: {timeoutSeconds}}});
  assert.equal(resolve({runtime: {config: {current: () => config(1800)}}, config: config(60)}), 1800);
  assert.equal(resolve({config: config(1800)}), 1800);
  assert.equal(resolve({config: {}}), undefined);
});

async function listen(server) {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}

// retried: the run's second attempt is still running at the response's
// deadline, so Portal stops it there; OpenClaw then answers with no payload.
for (const stream of [false, true]) for (const published of [false, true]) for (const retried of [false, true])
test(`Portal receives the ODS stop over the real ingress, stream=${stream}, published=${published}, retried=${retried}`, async t => {
  // As a /goal round: its outcome also says which limit stopped it.
  const f = fixture({prompt: published ? `${PROMPT} Publish the game preview when it works.` : PROMPT, goal: true});
  f.edit();
  const url = published ? f.publish() : undefined;
  const expected = published ? `${STOP_COPY}\n\n[Open last published preview](${url})\n\n` +
    'This is the last verified publication, not proof that all requested work completed.' : STOP_COPY;
  const task = {schemaVersion: 1, runId: RUN_ID, startedAt: '2026-10-16T13:32:54.000Z', finishedAt: '2026-10-16T14:02:54.000Z',
    state: 'completed', calls: 47, failures: 1, blocked: 0, truncated: false,
    activities: [{kind: 'read', calls: 40, failures: 0, blocked: 0}, {kind: 'edit', calls: 7, failures: 1, blocked: 0}]};
  const gateway = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : null;
    let result;
    switch (req.url) {
      case '/health': result = {ok: true}; break;
      case '/v1/chat/completions':
        // The run ends inside the gateway request, before the verification read.
        if (retried) { f.at(10); f.begin(); f.model(); }
        f.at(30);
        assert.deepEqual(f.aborts, retried ? [OWNER.sessionId] : []);
        f.guard.observeAgentEnd(CUT_OFF, OWNER);
        result = {id: RUN_ID, choices: [{index: 0, message: {role: 'assistant',
          content: retried ? 'No response from OpenClaw.' : SPARK_CONTENT}, finish_reason: 'stop'}]};
        break;
      case '/pixel-ods/verification': result = {...f.guard.deliveryVerificationForRun(body.runId), task}; break;
      case '/pixel-ods/subagent-delivery':
        result = {schemaVersion: 1, kind: 'ods-subagent-delivery', runId: body.runId, status: 'not-delegated'}; break;
      case '/pixel-ods/read-only-extension-continuation':
        result = {schemaVersion: 1, kind: 'ods-extension-read-only-continuation', eligible: false}; break;
      case '/pixel-ods/unfinished-extension-decision':
        result = {schemaVersion: 1, kind: 'ods-extension-unfinished-decision', eligible: false}; break;
      case '/pixel-ods/activity': result = {task: null}; break;
      default: res.writeHead(404); res.end(); return;
    }
    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify(result));
  });
  const ingress = createIngressServer({token: 'test-gateway-token-0123456789abcdef', gatewayPort: await listen(gateway)});
  const port = await listen(ingress);
  t.after(async () => { await close(ingress); await close(gateway); });
  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
    headers: {'content-type': 'application/json', connection: 'close'}, signal: AbortSignal.timeout(5000),
    body: JSON.stringify({user: 'ods-spark-game-20261016', stream, messages: [{role: 'user', content: PROMPT}]})});
  const body = await response.text();
  assert.equal(response.status, 200, body);
  const frames = stream ? body.split(/\r?\n/).filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6))) : [];
  const text = stream ? frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('')
    : JSON.parse(body).choices[0].message.content;
  assert.equal(text, expected);
  assert.doesNotMatch(body, /timeoutSeconds|LLM request failed|No response from OpenClaw/);
  // Goal mode's controller continues a goal after this stop (dashboard-api portal_goal.py).
  if (stream) assert.deepEqual(frames.at(-1).pixel_outcome, {schemaVersion: 1, status: 'failed', stop: 'run-deadline'});
});
