// Exact installed SDK registry + Agent continuation, with no inference or live sessions.
// OPENCLAW_PACKAGE_DIR identifies the reviewed package; all handles are test-owned.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash, randomUUID} from 'node:crypto';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const root = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root, 'provide the reviewed OpenClaw package explicitly');
const hash = value => createHash('sha256').update(value).digest('hex');
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json', import.meta.url)));
let original = readFileSync(join(root, 'dist/selection-BEwSQKM-.js'), 'utf8');
const installed = hash(original) === manifest.patchedSha256
  ? manifest.replacements : manifest.previousReplacements[hash(original)];
if (installed) for (const [before, after] of [...installed].reverse()) {
  assert.equal(original.split(after).length, 2);
  original = original.replace(after, before);
}
assert.equal(hash(original), manifest.sourceSha256);
let candidate = original;
for (const [before, after] of manifest.replacements) {
  assert.equal(candidate.split(before).length, 2);
  candidate = candidate.replace(before, after);
}
assert.equal(hash(candidate), manifest.patchedSha256);
assert.equal(candidate.includes('onBeforeLifecycleTerminal: () => {'), false);
// The final cleanup remains the native identity-checked operation, not a new
// registry deletion or broad abort. Extract it from the enclosing finally.
const cleanupStart = candidate.indexOf('if (!isProbeSession && (aborted || timedOut)');
const cleanupEnd = candidate.indexOf('clearActiveEmbeddedRun(params.sessionId, queueHandle, params.sessionKey, params.sessionFile);', cleanupStart);
assert.ok(cleanupStart > 0 && cleanupEnd > cleanupStart);
const cleanupStatement = candidate.slice(cleanupEnd, candidate.indexOf(';', cleanupEnd) + 1);
const {setActiveEmbeddedRun, clearActiveEmbeddedRun, resolveActiveEmbeddedRunSessionId, abortAgentHarnessRun} =
  await import(pathToFileURL(join(root, 'dist/plugin-sdk/agent-harness.js')));
const {d: Agent} = await import(pathToFileURL(join(root, 'dist/session-manager-3lTZxT-y.js')));
let sessions = readFileSync(join(root, 'dist/sessions-CZbwb3_c.js'), 'utf8');
const resume = JSON.parse(readFileSync(new URL('../host/openclaw-compaction-resume.json', import.meta.url)));
// CI installs the pristine pinned package. Derive the reviewed recovery in
// memory only; reject unknown bytes and leave the installed package unchanged.
if (hash(sessions) === resume.sourceSha256) {
  for (const [before, after] of resume.replacements) {
    assert.equal(sessions.split(before).length, 2);
    sessions = sessions.replace(before, after);
  }
}
assert.equal(hash(sessions), resume.patchedSha256, 'use reviewed compaction recovery');
const start = sessions.indexOf('\tasync runAutoCompaction(reason, willRetry) {');
const end = sessions.indexOf('\n\t/**', start);
assert.ok(start > 0 && end > start);
const compact = new Function(`return ({${sessions.slice(start, end)}}).runAutoCompaction`)();
const earlyStart = original.indexOf('onBeforeLifecycleTerminal: () => {');
const earlyEnd = original.indexOf('\n\t\t\t\t},', earlyStart);
assert.ok(earlyStart > 0 && earlyEnd > earlyStart);
const early = original.slice(earlyStart, earlyEnd) + '\n}';

function fixture() {
  const id = 'ods-ownership-test-' + randomUUID(), key = 'agent:pixel:' + id;
  const params = {sessionId: id, sessionKey: key, sessionFile: '/tmp/' + id + '.jsonl'};
  let aborts = 0, continuations = 0;
  const handle = {kind: 'embedded', abort: () => {aborts++;}, isStreaming: () => true};
  const cleanup = new Function('params', 'queueHandle', 'clearActiveEmbeddedRun', cleanupStatement)
    .bind(null, params, handle, clearActiveEmbeddedRun);
  const originalEnd = new Function('params', 'queueHandle', 'clearActiveEmbeddedRun',
    'requiresCompletionRequiredAsyncTaskWait', 'toolMetasForTerminal', `return ({${early}}).onBeforeLifecycleTerminal`)
    (params, handle, clearActiveEmbeddedRun, () => false, []);
  const agent = new Agent();
  agent.runContinuation = async () => {continuations++;};
  const prior = [{role: 'user', content: 'Continue the original task', timestamp: 1},
    {role: 'assistant', content: [{type: 'toolCall', id: 't', name: 'read', arguments: {}}], stopReason: 'toolUse', timestamp: 2},
    {role: 'toolResult', toolCallId: 't', toolName: 'read', content: [], isError: false, timestamp: 3}];
  const session = {agent, settingsManager: {getCompactionSettings: () => ({})}, emit: () => {},
    runCompactionWork: async () => {
      agent.state.messages = [...prior, {role: 'assistant', content: [], stopReason: 'error', timestamp: 4}];
      return {status: 'compacted', result: {tokensBefore: 1000}};
    }};
  setActiveEmbeddedRun(id, handle, key, params.sessionFile);
  return {id, key, params, handle, cleanup, originalEnd, aborts: () => aborts,
    continuations: () => continuations,
    async retry() {assert.equal(await compact.call(session, 'overflow', true), true); await agent.continue();}};
}

test('original early terminal callback loses abort binding before real compaction continuation', async () => {
  const f = fixture();
  try {
    f.originalEnd(); await f.retry();
    assert.equal(f.continuations(), 1);
    assert.equal(resolveActiveEmbeddedRunSessionId(f.key), undefined);
    assert.equal(abortAgentHarnessRun(f.id), false);
    assert.equal(f.aborts(), 0);
  } finally {f.cleanup();}
});

test('candidate retains owned abort through repeated compaction and cleans terminal attempt', async () => {
  const f = fixture();
  try {
    for (let i = 0; i < 3; i++) {
      await f.retry();
      assert.equal(resolveActiveEmbeddedRunSessionId(f.key), f.id);
    }
    assert.equal(f.continuations(), 3);
    assert.equal(abortAgentHarnessRun(f.id), true);
    assert.equal(f.aborts(), 1);
  } finally {f.cleanup();}
  assert.equal(resolveActiveEmbeddedRunSessionId(f.key), undefined);
  assert.equal(abortAgentHarnessRun(f.id), false);
});

for (const throws of [false, true]) test(`outer cleanup releases ${throws ? 'throwing prompt' : 'ordinary successful prompt'}`, async () => {
  const f = fixture();
  const attempt = async () => {
    try {
      assert.equal(resolveActiveEmbeddedRunSessionId(f.key), f.id);
      if (throws) throw new Error('owned prompt failed');
    } finally {f.cleanup();}
  };
  if (throws) await assert.rejects(attempt(), /owned prompt failed/);
  else await attempt();
  assert.equal(resolveActiveEmbeddedRunSessionId(f.key), undefined);
  assert.equal(abortAgentHarnessRun(f.id), false);
});

test('stale cleanup cannot clear a newer handle in the same session', () => {
  const f = fixture();
  let newerAborts = 0;
  const newer = {kind: 'embedded', abort: () => {newerAborts++;}};
  try {
    setActiveEmbeddedRun(f.id, newer, f.key, f.params.sessionFile);
    f.cleanup();
    assert.equal(resolveActiveEmbeddedRunSessionId(f.key), f.id);
    assert.equal(newerAborts, 0);
  } finally {clearActiveEmbeddedRun(f.id, newer, f.key, f.params.sessionFile);}
});

test('stale session abort cannot target a different replacement session', () => {
  const old = fixture(), current = fixture();
  try {
    old.cleanup();
    assert.equal(abortAgentHarnessRun(old.id), false);
    assert.equal(current.aborts(), 0);
    assert.equal(resolveActiveEmbeddedRunSessionId(current.key), current.id);
  } finally {old.cleanup(); current.cleanup();}
});
