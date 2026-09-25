import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {createOwnedProbeFetch} from '../host/probe_fetch.mjs';

const supported = typeof process.getuid === 'function';
function fixture() {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ods-probe-test-'));fs.chmodSync(dir,0o700);
  const leasePath=path.join(dir,'lease.json'),now=Date.now();
  const context={agentId:'pixel',runId:'owned-run',sessionId:'owned-session',sessionKey:'agent:pixel:owned',provider:'ods-local',modelId:'ods/current',baseUrl:'http://127.0.0.1:4102/v1',callId:'owned-run:model:1'};
  const lease={schemaVersion:1,...context,ownerUid:process.getuid(),scopeId:crypto.randomUUID(),signingKey:'a'.repeat(64),expiresAtMs:now+10000,maxAttempts:16};
  fs.writeFileSync(leasePath,JSON.stringify(lease),{mode:0o600});
  return {dir,leasePath,now,context,lease,close:()=>fs.rmSync(dir,{recursive:true,force:true})};
}
test('exact serialized bytes signed outside body, retry invocations get unique correlations', {skip:!supported},async()=>{
  const f=fixture();try {
    const seen=[],originalFetch=globalThis.fetch;
    const fetch=createOwnedProbeFetch(f.context,async(input,init)=>{seen.push({input,init});return new Response('ok');},{leasePath:f.leasePath,clock:()=>f.now});
    const body='{"messages":[{"role":"user","content":"private 😀"}],"model":"ods/current"}',controller=new AbortController();
    for(let i=0;i<2;i++)await fetch(f.context.baseUrl+'/chat/completions',{method:'POST',body,signal:controller.signal,headers:{'X-ODS-Probe':'forged','X-ODS-Probe-Secret':'private'}});
    const correlations=[];
    for(const row of seen){
      assert.equal(row.init.body,body);assert.equal(row.init.signal,controller.signal);assert.equal(row.init.headers.has('x-ods-probe-secret'),false);
      const [probe,call,sig]=row.init.headers.get('x-ods-probe').split('.');correlations.push(call);
      const signed=Buffer.concat([Buffer.from('ods.probe-header.v1\0'+probe+'\0'+call+'\0'),crypto.createHash('sha256').update(body).digest()]);
      assert.equal(sig,crypto.createHmac('sha256',f.lease.signingKey).update(signed).digest('base64url'));
      assert.equal(probe,f.lease.scopeId);
    }
    assert.notEqual(...correlations);assert.equal(globalThis.fetch,originalFetch);
    const records=fs.readFileSync(f.leasePath+'.events.jsonl','utf8');assert(!records.includes('private'));assert(!records.includes(f.lease.signingKey));assert.equal(records.trim().split('\n').length,2);
  }finally{f.close();}
});
test('wrong owner session request provider model and expired lease strip caller header', {skip:!supported},async()=>{
  const f=fixture();try{
    const variations=[{runId:'other'},{sessionId:'other'},{sessionKey:'agent:other:owned'},{provider:'other'},{modelId:'other'},{agentId:'other'}];
    for(const change of variations){
      let seen;await createOwnedProbeFetch({...f.context,...change},async(_,init)=>{seen=init;return new Response('ok');},{leasePath:f.leasePath,clock:()=>f.now})(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}',headers:{'x-ods-probe':'forged'}});assert.equal(seen.headers.has('x-ods-probe'),false);
    }
    let seen;await createOwnedProbeFetch(f.context,async(_,init)=>{seen=init;return new Response('ok');},{leasePath:f.leasePath,clock:()=>f.now+11000})(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}'});assert.equal(seen.headers.has('x-ods-probe'),false);
    assert.equal(fs.existsSync(f.leasePath+'.events.jsonl'),false);
  }finally{f.close();}
});
test('unrelated destinations, GET, non-string bodies, absent lease, and cap are fail closed', {skip:!supported},async()=>{
  const f=fixture();try{
    const calls=[];const fetch=createOwnedProbeFetch(f.context,async(url,init)=>{calls.push(init);return new Response('ok');},{leasePath:f.leasePath,clock:()=>f.now});
    for(const [url,init] of [['http://example.test/v1/chat/completions',{method:'POST',body:'{}'}],[f.context.baseUrl+'/models',{method:'GET'}],[f.context.baseUrl+'/chat/completions',{method:'POST',body:Buffer.from('{}')}]])await fetch(url,{...init,headers:{'x-ods-probe':'forged'}});
    assert(calls.every(c=>!c.headers.has('x-ods-probe')));
    f.lease.maxAttempts=1;fs.writeFileSync(f.leasePath,JSON.stringify(f.lease));
    await fetch(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}'});await fetch(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}'});
    assert(calls.at(-2).headers.has('x-ods-probe'));assert(!calls.at(-1).headers.has('x-ods-probe'));
    fs.unlinkSync(f.leasePath);await fetch(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}',headers:{'x-ods-probe':'forged'}});assert(!calls.at(-1).headers.has('x-ods-probe'));
  }finally{f.close();}
});
test('unsafe lease/symlink and cancellation do not alter or retry native fetch', {skip:!supported},async()=>{
  const f=fixture();try{
    fs.chmodSync(f.leasePath,0o644);let count=0;
    const error=new Error('aborted');const fetch=createOwnedProbeFetch(f.context,async(_,init)=>{count++;assert(!init.headers.has('x-ods-probe'));throw error;},{leasePath:f.leasePath});
    await assert.rejects(fetch(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}'}),e=>e===error);assert.equal(count,1);
    fs.chmodSync(f.leasePath,0o600);const link=f.leasePath+'.link';fs.symlinkSync(f.leasePath,link);let seen;
    await createOwnedProbeFetch(f.context,async(_,init)=>{seen=init;return new Response('ok');},{leasePath:link})(f.context.baseUrl+'/chat/completions',{method:'POST',body:'{}'});assert(!seen.headers.has('x-ods-probe'));
  }finally{f.close();}
});
