import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const MANIFEST_URL = new URL('../host/openclaw-compaction-resume.json', import.meta.url);
const MODULE_ENV = process.env.OPENCLAW_FILE_MODULE;

if (!MODULE_ENV) {
  throw new Error('OPENCLAW_FILE_MODULE env var is required (exact reviewed patched installed module URL)');
}

const manifestRaw = await readFile(MANIFEST_URL, 'utf8');
const manifest = JSON.parse(manifestRaw);

assert.match(manifest.patchedSha256, /^[0-9a-f]{64}$/);
const moduleUrl = MODULE_ENV.startsWith('file:')
  ? new URL(MODULE_ENV) : pathToFileURL(path.resolve(MODULE_ENV));
assert.equal(moduleUrl.protocol, 'file:', 'Only a local reviewed runtime is supported');
const bytes = await readFile(moduleUrl);
assert.equal(createHash('sha256').update(bytes).digest('hex'), manifest.patchedSha256,
  'Runtime bytes must match the reviewed patched module exactly');

const mod = await import(moduleUrl);
const { B: createWriteToolDefinition, X: createEditToolDefinition, H: createReadToolDefinition } = mod;
assert.equal(typeof createWriteToolDefinition, 'function', 'createWriteToolDefinition export');
assert.equal(typeof createEditToolDefinition, 'function', 'createEditToolDefinition export');
assert.equal(typeof createReadToolDefinition, 'function', 'createReadToolDefinition export');

let workDir;
test.before(async () => {
  workDir = await mkdtemp(path.join(tmpdir(), 'ods-repair-'));
});
test.after(async () => {
  if (workDir) await rm(workDir, { recursive: true, force: true });
});

function makeWrite() { return createWriteToolDefinition(workDir); }
function makeEdit() { return createEditToolDefinition(workDir); }
function makeRead() { return createReadToolDefinition(workDir); }

async function callTool(tool, input, signal) {
  return tool.execute('tc-' + Math.random().toString(36).slice(2), input, signal, () => {}, {});
}

function textOf(result) {
  return (result?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
}

test('no-op write returns nonterminal, preserves mtime/bytes, subsequent real read executes', async () => {
  const file = path.join(workDir, 'noop-write.txt');
  const content = 'hello world\n';
  await writeFile(file, content);
  const before = await stat(file);
  await new Promise(r => setTimeout(r, 20));
  const write = makeWrite();
  const result = await callTool(write, { path: file, content });
  assert.equal(result.terminate, false, 'no-op write must not terminate');
  assert.match(textOf(result), /No changes made/);
  const after = await stat(file);
  assert.equal(after.mtimeMs, before.mtimeMs, 'mtime preserved');
  assert.equal(after.size, before.size, 'size preserved');
  assert.equal(await readFile(file, 'utf8'), content, 'exact bytes preserved');
  const read = makeRead();
  const readResult = await callTool(read, { path: file });
  assert.match(textOf(readResult), /hello world/);
});

test('same-content edit returns nonterminal', async () => {
  const file = path.join(workDir, 'noop-edit.txt');
  const content = 'alpha\nbeta\ngamma\n';
  await writeFile(file, content);
  const edit = makeEdit();
  const result = await callTool(edit, {
    path: file,
    edits: [{ oldText: 'beta', newText: 'beta' }]
  });
  assert.equal(result.terminate, false, 'no-op edit must not terminate');
  assert.match(textOf(result), /No changes made/);
  const after = await readFile(file, 'utf8');
  assert.equal(after, content);

  // Separate edits can cancel out without each replacement being a no-op.
  // This reaches the EditNoChangeError return rather than realEdits.length=0.
  const netZero = path.join(workDir, 'net-zero-edit.txt');
  await writeFile(netZero, 'ab');
  const netZeroResult = await callTool(edit, {path: netZero, edits: [
    {oldText: 'a', newText: 'ab'}, {oldText: 'b', newText: ''},
  ]});
  assert.equal(netZeroResult.terminate, false);
  assert.match(textOf(netZeroResult), /replacement produced identical content/);
  assert.equal(await readFile(netZero, 'utf8'), 'ab');
});

test('real write still works and returns success', async () => {
  const file = path.join(workDir, 'real-write.txt');
  const write = makeWrite();
  const result = await callTool(write, { path: file, content: 'fresh content' });
  assert.notEqual(result.terminate, true);
  assert.match(textOf(result), /Successfully wrote/);
  assert.equal(await readFile(file, 'utf8'), 'fresh content');
});

test('real edit still works and returns success', async () => {
  const file = path.join(workDir, 'real-edit.txt');
  await writeFile(file, 'one two three\n');
  const edit = makeEdit();
  const result = await callTool(edit, {
    path: file,
    edits: [{ oldText: 'two', newText: 'TWO' }]
  });
  assert.notEqual(result.terminate, true);
  assert.match(textOf(result), /Successfully replaced/);
  assert.equal(await readFile(file, 'utf8'), 'one TWO three\n');
});

test('invalid edit (missing oldText match) still rejects', async () => {
  const file = path.join(workDir, 'invalid-edit.txt');
  await writeFile(file, 'abc\n');
  const edit = makeEdit();
  await assert.rejects(
    () => callTool(edit, { path: file, edits: [{ oldText: 'zzz', newText: 'yyy' }] }),
    (err) => err instanceof Error
  );
  assert.equal(await readFile(file, 'utf8'), 'abc\n');
});

test('missing file edit still rejects', async () => {
  const file = path.join(workDir, 'does-not-exist.txt');
  const edit = makeEdit();
  await assert.rejects(
    () => callTool(edit, { path: file, edits: [{ oldText: 'a', newText: 'b' }] }),
    (err) => err instanceof Error
  );
});

test('ambiguous edit (non-unique oldText) still rejects', async () => {
  const file = path.join(workDir, 'ambiguous.txt');
  await writeFile(file, 'dup\ndup\n');
  const edit = makeEdit();
  await assert.rejects(
    () => callTool(edit, { path: file, edits: [{ oldText: 'dup', newText: 'x' }] }),
    (err) => err instanceof Error
  );
  assert.equal(await readFile(file, 'utf8'), 'dup\ndup\n');
});

test('pre-aborted write and edit still reject', async () => {
  const file = path.join(workDir, 'aborted.txt');
  await writeFile(file, 'orig\n');
  const ac = new AbortController();
  ac.abort();
  const write = makeWrite();
  await assert.rejects(
    () => callTool(write, { path: file, content: 'new' }, ac.signal),
    (err) => err instanceof Error
  );
  await assert.rejects(
    () => callTool(write, { path: file, content: 'orig\n' }, ac.signal),
    /Operation aborted/
  );
  const edit = makeEdit();
  await assert.rejects(
    () => callTool(edit, { path: file, edits: [{ oldText: 'orig', newText: 'changed' }] }, ac.signal),
    (err) => err instanceof Error
  );
  assert.equal(await readFile(file, 'utf8'), 'orig\n');
});
