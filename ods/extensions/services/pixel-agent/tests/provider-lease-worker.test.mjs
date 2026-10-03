import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { createLeaseWorkerAdapter } from '../plugin/provider-lease-worker.mjs';

const context = () => ({runId: randomUUID(), sessionId: randomUUID()});
function fixture(extra = '') {
  const directory = mkdtempSync(join(tmpdir(), 'lease-pipe-test-'));
  const script = join(directory, 'child.mjs');
  writeFileSync(script, `
import assert from 'node:assert/strict';
assert.equal(process.env.ODS_FAKE_SECRET, undefined);
let data=Buffer.alloc(0), started=false;
process.stdin.on('end',()=>process.exit(0));
process.stdin.on('data',chunk=>{
 data=Buffer.concat([data,chunk]);
 if(started||data.length<4||data.length<4+data.readUInt32BE(0))return;
 started=true;const value=JSON.parse(data.subarray(4));
 const result={schemaVersion:1,runId:value.runId,sessionId:value.sessionId,lease:{
  baseUrl:'http://127.0.0.1:1234/v1',token:String(process.pid),contextTokens:32768,
  maxOutputTokens:4096,reasoning:false,supportsVision:false}};
 const send=value=>{const raw=Buffer.from(JSON.stringify(value)),head=Buffer.alloc(4);
  head.writeUInt32BE(raw.length);process.stdout.write(Buffer.concat([head,raw]));};
 ${extra || 'send(result);'}
});
`, {mode: 0o600});
  return createLeaseWorkerAdapter({directory,command:[process.execPath,script],
    request:()=>({expectedRevision:1,allowCloud:false,confirmed:true,timeoutSeconds:30})});
}

test('private pipe preserves identity, excludes ambient secrets, and release reaps child',
  {skip:process.platform==='win32'}, async () => {
    process.env.ODS_FAKE_SECRET='not-for-child';
    const adapter=fixture(), ctx=context();
    let lease;
    try {
      lease=await adapter.acquireLease(ctx);
      assert.equal(adapter.durableReplayGuard,true);
      assert.equal(lease.contextTokens,32768);
      await assert.rejects(adapter.releaseLease({...ctx,sessionId:randomUUID()}));
      process.kill(Number(lease.token),0);
    } finally {
      delete process.env.ODS_FAKE_SECRET;
      await adapter.releaseLease(ctx);
    }
    assert.throws(()=>process.kill(Number(lease.token),0), /ESRCH/);
    await adapter.releaseLease(ctx);
  });

for (const body of [
  "result.runId='foreign';send(result);",
  "process.stdout.write(Buffer.from([0,16,0,0]));",
  "process.stdout.write(Buffer.from([0,0,0,1,123]));",
  "const raw=Buffer.from(JSON.stringify(result)),head=Buffer.alloc(4);head.writeUInt32BE(raw.length);process.stdout.write(Buffer.concat([head,raw,Buffer.from('extra')]));",
]) test('invalid worker reply closes process: '+body.slice(0,35),
  {skip:process.platform==='win32',timeout:8000}, async () => {
    const adapter=fixture(body);
    await assert.rejects(adapter.acquireLease(context()), /ODS lease worker unavailable/);
  });

test('partial worker frames and parallel children remain separately owned',
  {skip:process.platform==='win32'}, async () => {
    const adapter=fixture("const raw=Buffer.from(JSON.stringify(result)),head=Buffer.alloc(4);head.writeUInt32BE(raw.length);process.stdout.write(head);setTimeout(()=>process.stdout.write(raw),25);");
    const a=context(),b=context();
    const [one,two]=await Promise.all([adapter.acquireLease(a),adapter.acquireLease(b)]);
    try {
      assert.notEqual(one.token,two.token);
      await adapter.releaseLease(a);
      process.kill(Number(two.token),0);
    } finally {await adapter.releaseLease(a);await adapter.releaseLease(b);}
  });
