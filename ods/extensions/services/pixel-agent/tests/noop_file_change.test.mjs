// OpenClaw 2026.6.33's write, edit and apply_patch return terminate: true for
// a change that leaves the file as it was, and the agent loop ends the turn
// when every result of a batch does. Tower2 round 092 (coding-v1) ended on an
// identical write with no answer. The ODS repair changes only the loop's
// batch test; runtime_noop_file_change.integration.mjs runs the real runtime.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-noop-file-change.json', import.meta.url)));
const [[pinned, repaired]] = manifest.replacements;
const load = source => new Function(`${source}\nreturn shouldTerminateToolBatch;`)();
const upstream = load(pinned), ods = load(repaired);
const result = (name, terminate) => ({toolCall: {id: name + '-1', name, arguments: {}},
  result: {content: [{type: 'text', text: 'result'}], ...(terminate ? {terminate: true} : {})}, isError: false});

test('the reviewed recipe binds one exact replacement of the pinned loop module', () => {
  assert.equal(manifest.version, '2026.6.33');
  assert.match(manifest.sourceSha256, /^[0-9a-f]{64}$/);
  assert.match(manifest.patchedSha256, /^[0-9a-f]{64}$/);
  assert.deepEqual(manifest.previousReplacements, {});
  assert.equal(manifest.replacements.length, 1);
  assert.match(pinned, /^function shouldTerminateToolBatch\(finalizedCalls\) \{\n/);
  assert.match(repaired, /^function shouldTerminateToolBatch\(finalizedCalls\) \{\n/);
});

for (const name of ['write', 'edit', 'apply_patch']) test(`an unchanged-file ${name} result no longer ends the turn`, () => {
  assert.equal(upstream([result(name, true)]), true, 'the pinned loop ends the turn without an answer');
  assert.equal(ods([result(name, true)]), false);
});

test('a batch of unchanged-file results continues', () => {
  const batch = ['write', 'edit', 'apply_patch'].map(name => result(name, true));
  assert.equal(upstream(batch), true);
  assert.equal(ods(batch), false);
});

test('other terminating results end the batch as before', () => {
  for (const batch of [[result('client_lookup', true)], [result('client_lookup', true), result('client_notify', true)]]) {
    assert.equal(upstream(batch), true);
    assert.equal(ods(batch), true);
  }
});

test('an unchanged-file result counts like a changed one in a mixed batch', () => {
  // Upstream already continues when a client-delegated call shares its batch
  // with a write that changed the file; an unchanged file is treated the same.
  assert.equal(upstream([result('client_lookup', true), result('write', false)]), false);
  assert.equal(ods([result('client_lookup', true), result('write', true)]), false);
});

test('ordinary batches keep their meaning', () => {
  for (const batch of [[], [result('write', false)], [result('exec', false), result('read', false)]]) {
    assert.equal(ods(batch), upstream(batch));
  }
});
