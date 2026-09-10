import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHandoffOwnerAdapter } from '../plugin/handoff-owner-worker.mjs';
import { createProviderRoutingBridge } from '../plugin/provider-routing.mjs';

const digest = 'a'.repeat(64);
const posix = {skip: process.platform === 'win32', timeout: 10000};
function command(mode, marker) {
  return [process.execPath,'-e',`
    const {writeFileSync}=require('node:fs');
    writeFileSync(${JSON.stringify(marker)},String(process.pid),{mode:0o600});
    let bytes=Buffer.alloc(0);
    process.stdin.on('data',chunk=>{
      bytes=Buffer.concat([bytes,chunk]);
      if(bytes.length<4 || bytes.length<4+bytes.readUInt32BE(0)) return;
      const body=JSON.parse(bytes.subarray(4).toString());
      if(${JSON.stringify(mode)}==='wait') return;
      if(${JSON.stringify(mode)}==='oversize') {process.stdout.end(Buffer.alloc(9000));return;}
      if(${JSON.stringify(mode)}==='bad-size') {process.stdout.end(Buffer.from([255,255,255,255]));return;}
      const receipt={approved:true,checkpointDigest:${JSON.stringify(mode)}==='wrong-digest'?'b'.repeat(64):body.checkpointDigest};
      if(Object.keys(process.env).some(k=>!['PATH','LANG','PYTHONDONTWRITEBYTECODE'].includes(k))) receipt.checkpointDigest='bad-env';
      const raw=Buffer.from(JSON.stringify(receipt));const prefix=Buffer.alloc(4);prefix.writeUInt32BE(raw.length);
      process.stdout.end(Buffer.concat([prefix,raw]),()=>process.exit(0));
    });
    process.stdin.on('end',()=>process.exit(0));
  `,'--'];
}

for (const mode of ['valid','oversize','bad-size','wrong-digest']) {
  test('owner pipe validates framed receipt and environment: '+mode,posix,async()=>{
    const root=mkdtempSync(join(tmpdir(),'ods-owner-pipe-'));
    const marker=join(root,'pid');
    const adapter=createHandoffOwnerAdapter({command:command(mode,marker),directory:root,timeoutSeconds:1});
    const receipt=await adapter({checkpoint:{prompt:'Synthetic'},checkpointDigest:digest,signal:new AbortController().signal});
    assert.deepEqual(receipt,mode==='valid'?{approved:true,checkpointDigest:digest}:null);
    const pid=Number(readFileSync(marker,'utf8'));
    assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
  });
}

test('owner pipe abort waits for its own process to exit',posix,async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-owner-abort-')); const marker=join(root,'pid');
  const controller=new AbortController();
  const adapter=createHandoffOwnerAdapter({command:command('wait',marker),directory:root,timeoutSeconds:1});
  const timer=setTimeout(()=>controller.abort(),200);
  try {
    assert.equal(await adapter({checkpoint:{prompt:'Synthetic'},checkpointDigest:digest,signal:controller.signal}),null);
    assert.throws(()=>process.kill(Number(readFileSync(marker,'utf8')),0),{code:'ESRCH'});
  } finally {clearTimeout(timer);}
});

test('owner pipe rejects absent executable without hanging',posix,async()=>{
  const adapter=createHandoffOwnerAdapter({command:['/absent-ods-owner-fixture'],directory:'/tmp',timeoutSeconds:1});
  assert.equal(await adapter({checkpoint:{},checkpointDigest:digest,signal:new AbortController().signal}),null);
});

test('pre-aborted and oversized checkpoints never spawn',posix,async()=>{
  const adapter=createHandoffOwnerAdapter({command:['/absent-ods-owner-fixture'],directory:'/tmp'});
  assert.equal(await adapter({checkpoint:{},checkpointDigest:digest,signal:AbortSignal.abort()}),null);
  assert.equal(await adapter({checkpoint:{text:'x'.repeat(2*1024*1024)},checkpointDigest:digest,signal:new AbortController().signal}),null);
});

test('bounded approval hook registration exceeds the runtime default without extending leases',()=>{
  const bridge=createProviderRoutingBridge({enabled:true,approvalTimeoutMs:60000,
    acquireLease:async()=>{throw new Error('not called');},releaseLease:async()=>{}});
  assert.deepEqual(bridge.beforeAgentRunOptions,{timeoutMs:65000});
  assert.ok(Object.isFrozen(bridge.beforeAgentRunOptions));
  assert.throws(()=>createProviderRoutingBridge({approvalTimeoutMs:120001}));
});
