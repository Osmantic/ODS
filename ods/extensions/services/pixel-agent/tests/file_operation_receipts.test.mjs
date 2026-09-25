import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createToolLoopGuard } from '../plugin/tool-loop-guard.mjs';
import { createFileReceiptContext, createFileReceiptAdapter } from '../host/file-operation-receipts.mjs';
import { retainedFileReceipt } from '../plugin/file-receipt-feedback.mjs';

function fixture(initial = '\ufeffone\r\ntwo\r\n') {
  const root = path.resolve('owned-workspace');
  const file = path.join(root, 'a.txt');
  let bytes = Buffer.from(initial), writes = 0, identity = '1'.repeat(64);
  const operations = {
    async readFile() { if (bytes === undefined) throw Object.assign(new Error('missing'), { code: 'ENOENT' }); Object.defineProperty(bytes, 'odsFileIdentity', { value: identity, configurable: true }); return bytes; },
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
  return { root, file, operations, context, read, mutation, show, get bytes() { return bytes; }, get writes() { return writes; }, setIdentity(value) { identity = value; }, set(value) { bytes = value === undefined ? undefined : Buffer.from(value); } };
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

test('cross-turn trusted receipt survives only same scope and actual visible body', async () => {
  const f = fixture(); const result = await f.read.execute('read-1', { path: 'a.txt' });
  const messages = [{ role: 'toolResult', toolName: 'read', toolCallId: 'read-1', ...result }];
  const next = createFileReceiptContext({ agentId: 'pixel', sessionKey: 'session-a', workspace: f.root, enabled: true });
  next.hydrateTrustedHistory(messages); next.updateVisible(messages);
  assert.equal(next.visible('a.txt').version, result.details.fileReceipt.version);
  next.updateVisible([{ ...messages[0], content: [{ type: 'text', text: '[truncated]' }] }]);
  assert.equal(next.visible('a.txt'), undefined);
  const foreign = createFileReceiptContext({ agentId: 'pixel', sessionKey: 'session-b', workspace: f.root, enabled: true });
  foreign.hydrateTrustedHistory(messages); foreign.updateVisible(messages);
  assert.equal(foreign.visible('a.txt'), undefined);
});

test('deferred envelope requires exact core child binding and visible outer body', async () => {
  const f = fixture(); const result = await f.read.execute('tool_search_code:outer:read:1', { path: 'a.txt' });
  const message = { role: 'toolResult', toolName: 'tool_call', toolCallId: 'outer', content: result.content,
    details: { tool: { source: 'openclaw', sourceName: 'core', name: 'read', id: 'openclaw:core:read' }, result } };
  f.context.updateVisible([message]); assert.ok(f.context.visible('a.txt'));
  f.context.updateVisible([{ ...message, content: [{ type: 'text', text: '[truncated]' }] }]);
  assert.equal(f.context.visible('a.txt'), undefined, 'nested content is not provider visibility');
  const foreign = structuredClone(message); foreign.details.tool.sourceName = 'other';
  f.context.updateVisible([foreign]); assert.equal(f.context.visible('a.txt'), undefined);
  const otherCall = structuredClone(message); otherCall.toolCallId = 'other';
  f.context.updateVisible([otherCall]); assert.equal(f.context.visible('a.txt'), undefined);
});

test('compaction preservation does not promote failures or hidden metadata', async () => {
  const f = fixture(); const result = await f.read.execute('read-1', { path: 'a.txt' });
  assert.deepEqual(retainedFileReceipt(result, 'read'), result.details.fileReceipt);
  assert.equal(retainedFileReceipt({ ...result, isError: true }, 'read'), undefined);
  assert.equal(retainedFileReceipt({ ...result, content: [] }, 'read'), undefined);
  assert.equal(retainedFileReceipt(result, 'exec'), undefined);
});

test('partial write error and cancelled recovery retain failure', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', { path: 'a.txt' }));
  f.operations.writeFile = async () => { f.set('partial'); throw Object.assign(new Error('disk full'), { code: 'ENOSPC' }); };
  await assert.rejects(f.mutation('write', true).execute('w', { path: 'a.txt', content: 'replacement' }), { code: 'ENOSPC' });
  assert.equal(f.bytes.toString(), 'partial');
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.read.execute('r-abort', { path: 'a.txt' }, controller.signal), /Operation aborted/);
});

test('changed readback cannot be recovered into a successful native receipt', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', { path: 'a.txt' }));
  f.operations.writeFile = async () => { f.set('competing bytes'); };
  await assert.rejects(f.mutation('write', true).execute('w', { path: 'a.txt', content: 'replacement' }), { code: 'ODS_FILE_READBACK_MISMATCH' });
  assert.equal(f.bytes.toString(), 'competing bytes');
});

test('deleted file between SDK read and mutation is not silently recreated', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', { path: 'a.txt' }));
  const originalRead = f.operations.readFile; let reads = 0;
  f.operations.readFile = async (...args) => { if (++reads === 2) f.set(undefined); return originalRead(...args); };
  await assert.rejects(f.mutation('edit').execute('e', { path: 'a.txt', oldText: 'one', newText: 'x' }), { code: 'ODS_FILE_VERSION_REFRESH_REQUIRED' });
  assert.equal(f.writes, 0);
});

test('an explicit repeated read still returns the actual requested body', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', { path: 'a.txt' }));
  const again = await f.read.execute('read-2', { path: 'a.txt', offset: 2, limit: 1 });
  assert.match(again.content[0].text, /2\|two/);
  assert.equal(again.details.fileReceipt.start, 2);
  assert.equal(again.details.fileReceipt.end, 2);
});

test('two disjoint visible ranges retain earlier coverage at the same version', async () => {
  const f = fixture('one\ntwo\nthree\nfour');
  const first = await f.read.execute('r1', { path: 'a.txt', offset: 1, limit: 1 });
  const last = await f.read.execute('r2', { path: 'a.txt', offset: 4, limit: 1 });
  f.context.updateVisible([{ role:'toolResult',toolName:'read',toolCallId:'r1',...first },
    { role:'toolResult',toolName:'read',toolCallId:'r2',...last }]);
  assert.equal(f.context.visible('a.txt').coverage.length, 2);
  await f.mutation('edit').execute('e', {path:'a.txt',oldText:'one',newText:'ONE'});
  assert.equal(f.bytes.toString(), 'ONE\ntwo\nthree\nfour');
});

test('newer hidden version invalidates earlier visible coverage', async () => {
  const f = fixture('one'); const first = await f.read.execute('r1', {path:'a.txt'});
  f.set('new'); const newer = await f.read.execute('r2', {path:'a.txt'});
  f.context.updateVisible([{role:'toolResult',toolName:'read',toolCallId:'r1',...first},
    {role:'toolResult',toolName:'read',toolCallId:'r2',...newer,content:[{type:'text',text:'[truncated]'}]}]);
  assert.equal(f.context.visible('a.txt'), undefined);
});

test('same bytes at a changed opened identity require refresh', async () => {
  const f = fixture(); f.show(await f.read.execute('read-1', {path:'a.txt'}));
  f.setIdentity('2'.repeat(64));
  await assert.rejects(f.mutation('edit').execute('e',{path:'a.txt',oldText:'one',newText:'x'}), {code:'ODS_FILE_VERSION_REFRESH_REQUIRED'});
  assert.equal(f.writes,0);
});

test('an unmapped mutating path fails closed before writing', async () => {
  const f=fixture();f.operations.receiptPath=()=>'/unmapped/a.txt';
  for(const op of ['write','edit'])await assert.rejects(f.mutation(op).execute(op,{path:'a.txt',content:'x',oldText:'one',newText:'x'}),{code:'ODS_FILE_RECEIPT_SCOPE'});
  assert.equal(f.writes,0);
});

for (const available of [false, true]) test(`current-run read gate yields only to positive SDK capability: ${available}`, () => {
  const guard=createToolLoopGuard({fileVersionAdmissionAvailable:available});
  const first={agentId:'pixel',runId:'initial',sessionId:'session',sessionKey:'key'};
  guard.observeRun(first,'pixel',{prompt:'Create and publish a website in a new workspace directory site.'});
  const content='<!doctype html><h1>Original</h1>';
  const invoke=(ctx,name,params,id,result)=>{
    const c={...ctx,toolCallId:id,toolName:name},event={toolName:name,toolCallId:id,params};
    const prepared=guard.beforeToolCall(event,c);assert.notEqual(prepared?.block,true,prepared?.blockReason);
    const executed=prepared?.params??params;guard.afterToolCall({...event,params:executed,result},c);return executed;
  };
  const written=invoke(first,'write',{path:'site/index.html',content},'seed-write',{content:[{type:'text',text:'Successfully wrote file.'}]});
  const data=Buffer.from(content),name=Buffer.from('index.html'),a=Buffer.alloc(4),b=Buffer.alloc(8);a.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(data.length));
  const sha256=createHash('sha256').update(a).update(name).update(b).update(data).digest('hex'),siteId='site-'+sha256.slice(0,24),relativeDirectory=written.path.replace(/\/index.html$/,'');
  const preview={schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory,siteId,sha256,entryFile:'index.html',entrySha256:createHash('sha256').update(data).digest('hex'),files:1,bytes:data.length,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,httpStatus:200,readbackVerified:true,executable:false,overwritten:false};
  invoke(first,'pixel_ods_workspace_preview',{relativeDirectory},'seed-preview',{details:preview});
  const followup={...first,runId:'followup'};
  guard.observeRun(followup,'pixel',{prompt:'Change the previous website heading to Revised and republish it.'});
  const prepared=guard.beforeToolCall({toolName:'edit',params:{path:written.path,edits:[{oldText:'Original',newText:'Revised'}]},toolCallId:'edit-followup'}, {...followup,toolName:'edit',toolCallId:'edit-followup'});
  assert.equal(prepared?.block===true,!available);
  if(!available)assert.match(prepared.blockReason,/read/i);
});

test('pinned runtime recipe embeds the reviewed helper source exactly', () => {
  const helper=readFileSync(new URL('../host/file-operation-receipts.mjs',import.meta.url),'utf8')
    .replace(/\r\n/g,'\n').split('\n').filter(line=>!line.startsWith('import ')).join('\n').replaceAll('export function ','function ').trimEnd();
  const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-compaction-resume.json',import.meta.url)));
  assert.ok(manifest.replacements.some(([before,after])=>before==='function createEditTool(cwd, options) {' && after.startsWith(helper)));
});
