import test from 'node:test';
import assert from 'node:assert/strict';
import {appendFileSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {readCompleteJsonl} from './fixtures/complete-jsonl.mjs';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'ods-jsonl-evidence-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  return join(root, 'hooks.jsonl');
}

test('a split append defers only its unfinished record and reads it after completion', t => {
  const path = fixture(t);
  const first = {hook: 'prompt', runId: 'announce:1'};
  const second = {hook: 'end', runId: 'announce:1', note: 'café'};
  const record = Buffer.from(JSON.stringify(second) + '\n');
  const split = record.indexOf(Buffer.from('é')) + 1;
  writeFileSync(path, JSON.stringify(first) + '\n');
  appendFileSync(path, record.subarray(0, split));
  assert.deepEqual(readCompleteJsonl(path), [first]);
  appendFileSync(path, record.subarray(split));
  assert.deepEqual(readCompleteJsonl(path), [first, second]);
});

test('a valid tail still waits for its newline commit marker', t => {
  const path = fixture(t);
  writeFileSync(path, '{"hook":"end"}');
  assert.deepEqual(readCompleteJsonl(path), []);
  appendFileSync(path, '\n');
  assert.deepEqual(readCompleteJsonl(path), [{hook: 'end'}]);
});

test('malformed complete evidence fails instead of being hidden', t => {
  const path = fixture(t);
  writeFileSync(path, '{"hook":"start"}\n{"broken":}\n{"tail":');
  assert.throws(() => readCompleteJsonl(path), SyntaxError);
});

test('missing and empty evidence can be polled before the producer starts', t => {
  const path = fixture(t);
  assert.deepEqual(readCompleteJsonl(path), []);
  writeFileSync(path, '');
  assert.deepEqual(readCompleteJsonl(path), []);
});

test('filesystem errors other than a missing file remain visible', t => {
  const path = fixture(t);
  assert.throws(() => readCompleteJsonl(dirname(path)), {code: 'EISDIR'});
});
