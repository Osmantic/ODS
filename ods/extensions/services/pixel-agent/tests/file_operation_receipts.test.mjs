import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createFileReceiptContext, createFileReceiptAdapter } from '../host/file-operation-receipts.mjs';

function fixture(initial = '\ufeffone\r\ntwo\r\n') {
  const root = path.resolve('owned-workspace');
  const file = path.join(root, 'a.txt');
  let bytes = Buffer.from(initial), writes = 0;
  const operations = {
    async readFile() { if (bytes === undefined) throw Object.assign(new Error('missing'), { code: 'ENOENT' }); return bytes; },
    async writeFile(_, value) { writes++; bytes = Buffer.from(value); },
  };
  const context = createFileReceiptContext({ agentId: 'pixel', sessionKey: 'session-a', workspace: root, enabled: true });
  const readAdapter = createFileReceiptAdapter({ root, operation: 'read', operations, context });
  const read = readAdapter.wrap({ async execute(id, args) {
    await readAdapter.operations.readFile(file);
    return { content: [{ type: 'text', text: 'original' }] };
  } });
  function mutation(operation, recover = false) {
    const adapter = createFileReceiptAdapter({ root, operation, operations, context });
    return adapter.wrap({ async execute(id, args) {
      try {
        const before = await adapter.operations.readFile(file).catch(error => { if (operation === 'write' && error.code === 'ENOENT') return undefined; throw error; });
        await adapter.operations.writeFile(file, operation === 'edit' ? before.toString().replace(args.oldText, args.newText) : args.content);
      } catch (error) { if (!recover) throw error; }
      return { content: [{ type: 'text', text: 'success' }] };
    } });
  }
  const show = (result, call = 'read-1', name = 'read') => context.updateVisible([{ role: 'toolResult', toolCallId: call, toolName: name, ...result }]);
  return { root, file, context, read, mutation, show, get bytes() { return bytes; }, get writes() { return writes; }, set(value) { bytes = value === undefined ? undefined : Buffer.from(value); } };
}

test('receipt describes actual CRLF/BOM/Unicode bytes and numbered range', async () => {
  const f = fixture('\ufeffone\r\nλ🙂\r\n');
  const result = await f.read.execute('read-1', { path: 'a.txt', offset: 2, limit: 1 });
  assert.equal(result.details.fileReceipt.bytes, f.bytes.length);
  assert.equal(result.details.fileReceipt.fullFile, false);
  assert.match(result.content[0].text, /2\|λ🙂\r\n/);
  assert.match(result.content[0].text, /offset=3/);
  f.show(result);
  assert.ok(f.context.visible('a.txt'));
});

test('current full read authorizes write and successful readback supplies actual version', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', { path: 'a.txt' }));
  const result = await f.mutation('write').execute('write-1', { path: 'a.txt', content: 'new🙂' });
  assert.equal(result.details.fileReceipt.bytes, Buffer.byteLength('new🙂'));
  assert.equal(f.bytes.toString(), 'new🙂');
  f.show(result, 'write-1', 'write');
  assert.equal(f.context.visible('a.txt').version, result.details.fileReceipt.version);
});

test('external version change rejects mutation without writing', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', { path: 'a.txt' })); f.set('owner change');
  await assert.rejects(f.mutation('edit').execute('e', { path: 'a.txt', oldText: 'one', newText: 'x' }), { code: 'ODS_FILE_VERSION_REFRESH_REQUIRED' });
  assert.equal(f.writes, 0); assert.equal(f.bytes.toString(), 'owner change');
});

test('metadata-only or truncated body never counts as visible', async () => {
  const f = fixture(); const result = await f.read.execute('read-1', { path: 'a.txt' });
  f.show({ ...result, content: [{ type: 'text', text: result.content[0].text.split('\n')[0] }] });
  assert.equal(f.context.visible('a.txt'), undefined);
  await assert.rejects(f.mutation('write').execute('w', { path: 'a.txt', content: 'x' }), { code: 'ODS_FILE_VERSION_REFRESH_REQUIRED' });
});

test('partial read permits matching edit but not full replacement or unseen edit', async () => {
  const f = fixture('one\ntwo\nthree');
  f.show(await f.read.execute('read-1', { path: 'a.txt', offset: 2, limit: 1 }));
  await assert.rejects(f.mutation('write').execute('w', { path: 'a.txt', content: 'x' }), { code: 'ODS_FILE_VERSION_REFRESH_REQUIRED' });
  await assert.rejects(f.mutation('edit').execute('e', { path: 'a.txt', oldText: 'one', newText: 'x' }), { code: 'ODS_FILE_VERSION_REFRESH_REQUIRED' });
  await f.mutation('edit').execute('e2', { path: 'a.txt', oldText: 'two', newText: 'TWO' });
  assert.equal(f.bytes.toString(), 'one\nTWO\nthree');
});

test('context loss, unrelated tool/call and unrelated agent cannot grant reuse', async () => {
  const f = fixture(); const result = await f.read.execute('read-1', { path: 'a.txt' });
  f.show(result, 'another-call'); assert.equal(f.context.visible('a.txt'), undefined);
  f.show(result, 'read-1', 'exec'); assert.equal(f.context.visible('a.txt'), undefined);
  f.show(result); f.context.updateVisible([]); assert.equal(f.context.visible('a.txt'), undefined);
  assert.equal(createFileReceiptContext({ agentId: 'other', sessionKey: 's', workspace: f.root, enabled: true }), undefined);
});

test('large bodies retain complete visible range plus deterministic continuation', async () => {
  const f = fixture(Array.from({ length: 3000 }, (_, i) => `line${i}`).join('\n'));
  const result = await f.read.execute('read-1', { path: 'a.txt' });
  assert.ok(result.content[0].text.length < 12500);
  assert.equal(result.details.fileReceipt.fullFile, false);
  assert.equal(result.details.fileReceipt.bodyComplete, true);
  assert.match(result.content[0].text, /More content: read path=/);
});

test('SDK error recovery cannot launder failed version check', async () => {
  const f = fixture();
  await assert.rejects(f.mutation('write', true).execute('w', { path: 'a.txt', content: 'x' }), { code: 'ODS_FILE_VERSION_REFRESH_REQUIRED' });
  assert.equal(f.writes, 0);
});

test('empty file and new file are distinct and byte exact', async () => {
  const f = fixture(''); const result = await f.read.execute('read-1', { path: 'a.txt' });
  assert.equal(result.details.fileReceipt.bytes, 0); assert.equal(result.details.fileReceipt.fullFile, true);
  f.set(undefined);
  const created = await f.mutation('write').execute('w', { path: 'a.txt', content: '\ufeffnew\r\n' });
  assert.equal(created.details.fileReceipt.bytes, Buffer.byteLength('\ufeffnew\r\n'));
});
