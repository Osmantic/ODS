// Exercise the reviewed installed-runtime method and its real Agent continuation
// boundary without inference, installations, or changes to the running runtime.
// ODS_OPENCLAW_ROOT must identify the exact supported npm runtime.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import test from 'node:test';

const root = process.env.ODS_OPENCLAW_ROOT;
assert.ok(root, 'Set ODS_OPENCLAW_ROOT to the installed OpenClaw runtime');
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-resume.json', import.meta.url)));
const hash = value => createHash('sha256').update(value).digest('hex');
let source = readFileSync(join(root, 'dist/sessions-CZbwb3_c.js'), 'utf8');
if (hash(source) !== manifest.sourceSha256) {
  const replacements = hash(source) === manifest.patchedSha256
    ? manifest.replacements : manifest.previousReplacements[hash(source)];
  assert.ok(replacements, 'Runtime bytes must match a reviewed version');
  for (const [before, after] of [...replacements].reverse()) source = source.replace(after, before);
}
assert.equal(hash(source), manifest.sourceSha256);
for (const [before, after] of manifest.replacements) source = source.replace(before, after);
assert.equal(hash(source), manifest.patchedSha256);
const start = source.indexOf('\tasync runAutoCompaction(reason, willRetry) {');
const end = source.indexOf('\n\t/**', start);
assert.ok(start > 0 && end > start);
const runAutoCompaction = new Function(`return ({${source.slice(start, end)}}).runAutoCompaction`)();
const eventStart = source.indexOf('\tasync handleAgentEventUnlocked(event) {');
const eventEnd = source.indexOf('\n\twillRetryAfterAgentEnd(', eventStart);
assert.ok(eventStart > 0 && eventEnd > eventStart);
const handleAgentEvent = new Function(`return ({${source.slice(eventStart, eventEnd)}}).handleAgentEventUnlocked`)();
const {d: Agent} = await import(pathToFileURL(join(root, 'dist/session-manager-3lTZxT-y.js')));

function fixture(stopReason, outcome = 'compacted') {
  const agent = new Agent();
  const prior = [
    {role: 'user', content: 'Research and prepare only', timestamp: 1},
    {role: 'assistant', content: [{type: 'toolCall', id: 'read-1', name: 'read', arguments: {}}], stopReason: 'toolUse', timestamp: 2},
    {role: 'toolResult', toolCallId: 'read-1', toolName: 'read', content: [], isError: false, timestamp: 3},
  ];
  const tail = {role: 'assistant', content: [], stopReason, timestamp: 4};
  agent.state.messages = [...prior]; // pre-compaction removal is insufficient
  let continued = 0;
  agent.runContinuation = async () => {continued++;};
  const events = [];
  const session = {
    agent, settingsManager: {getCompactionSettings: () => ({})},
    emit: event => events.push(event),
    runCompactionWork: async () => {
      // The actual compactor rebuilds retained messages from the saved transcript.
      agent.state.messages = [...prior, tail];
      return {status: outcome, result: {tokensBefore: 30000}};
    },
  };
  return {agent, prior, tail, session, events, continued: () => continued};
}

for (const stopReason of ['length', 'error']) {
  test(`retry after ${stopReason} preserves tool evidence and reaches continuation`, async () => {
    const f = fixture(stopReason);
    assert.equal(await runAutoCompaction.call(f.session, 'threshold', true), true);
    assert.deepEqual(f.agent.state.messages, f.prior);
    await f.agent.continue();
    assert.equal(f.continued(), 1);
    assert.equal(f.events.at(-1).willRetry, true);
    assert.equal(f.session.autoCompactionAbortController, undefined);
  });
}

test('ordinary compaction does not delete a completed answer or start another turn', async () => {
  const f = fixture('stop');
  assert.equal(await runAutoCompaction.call(f.session, 'threshold', false), false);
  assert.equal(f.agent.state.messages.at(-1), f.tail);
  assert.equal(f.continued(), 0);
});

test('a completed assistant answer is never silently dropped even with retry requested', async () => {
  const f = fixture('stop');
  await runAutoCompaction.call(f.session, 'threshold', true);
  await assert.rejects(f.agent.continue(), /Cannot continue from message role: assistant/);
  assert.equal(f.agent.state.messages.at(-1), f.tail);
});

for (const outcome of ['aborted', 'skipped']) {
  test(`${outcome} compaction does not authorize a continuation`, async () => {
    const f = fixture('length', outcome);
    assert.equal(await runAutoCompaction.call(f.session, 'threshold', true), false);
    assert.equal(f.events.at(-1).willRetry, false);
    assert.equal(f.continued(), 0);
  });
}

for (const stopReason of ['length', 'error', 'stop', 'toolUse']) {
  test(`${stopReason} completion updates recovery budget from actual progress`, async () => {
    const session = {
      overflowRecoveryAttempted: true, retryCount: 0,
      emitExtensionEvent: async () => false, emit: () => {},
      sessionManager: {appendMessage: () => {}},
    };
    await handleAgentEvent.call(session, {type: 'message_end', message: {role: 'assistant', stopReason}});
    assert.equal(session.overflowRecoveryAttempted, ['length', 'error'].includes(stopReason));
  });
}
