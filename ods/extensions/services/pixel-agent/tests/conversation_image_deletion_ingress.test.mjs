import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {createChatImageStore} from '../host/chat_image_store.mjs';

async function fixture(t) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'ods-delete-ingress-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const ledger=createChatHistoryLedger(path.join(directory,'ledger')),store=createChatImageStore(path.join(directory,'images'));
  let busy=false, failure=false;const calls=[];
  const native={schemaVersion:1,status:'ready',sessionRevision:null,compaction:{status:'idle',count:0}};
  const deps={setTimeout,clearTimeout,fetch:async url=>{
    calls.push(url);
    if(url.endsWith('/context'))return Response.json({...native,status:busy?'busy':'ready'});
    if(url.endsWith('/images-delete'))return failure?Response.json({error:'offline'},{status:503}):Response.json({schemaVersion:1,deleted:true});
    throw new Error('Unexpected provider access');
  }};
  const server=createIngressServer({token:'private-fixture-token',gatewayPort:1,deps,historyLedger:ledger,imageStore:store});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
  const request=(route,body)=>fetch(`http://127.0.0.1:${server.address().port}${route}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  const user=computeSessionUser({user:'chat'}),data=Buffer.from([137,80,78,71,13,10,26,10,1]);
  const image={id:`img-${'a'.repeat(32)}`,sha256:createHash('sha256').update(data).digest('hex'),data,mimeType:'image/png'};
  const ref={id:image.id,sha256:image.sha256};store.put(user,[image],[ref]);
  return {ledger,store,request,user,ref,calls,native,set busy(value){busy=value;},set failure(value){failure=value;}};
}
test('real ingress delete refuses an active lock/native run before any tombstone and preserves private bytes',async t=>{
  const f=await fixture(t);let release=f.ledger.lock(f.user);
  assert.equal((await f.request('/v1/chat/images-delete',{user:'chat'})).status,409);release();
  f.busy=true;assert.equal((await f.request('/v1/chat/images-delete',{user:'chat'})).status,409);
  assert.equal(f.ledger.read(f.user),null);assert.ok(f.store.read(f.user,f.ref,[f.ref]));
});
test('native failure leaves durable fence; retry purges cache and stale old history cannot reach provider',async t=>{
  const f=await fixture(t);f.failure=true;
  assert.equal((await f.request('/v1/chat/images-delete',{user:'chat'})).status,503);
  assert.equal(f.ledger.read(f.user).status,'deleted');
  const before=f.calls.length;
  const old={user:'chat',model:'portal/default',stream:true,request_id:'old',messages:[{role:'user',content:'retry'}],history_snapshot:{schemaVersion:1,messages:[{role:'user',content:'retry'}]}};
  assert.equal((await f.request('/v1/chat/completions',old)).status,410);assert.equal(f.calls.length,before);
  f.failure=false;
  const result=await f.request('/v1/chat/images-delete',{user:'chat'});assert.equal(result.status,200);assert.deepEqual(await result.json(),{schemaVersion:1,deleted:true});
  assert.throws(()=>f.store.read(f.user,f.ref,[f.ref]),/conversation-deleted/);
  assert.equal((await f.request('/v1/chat/images-delete',{user:'chat'})).status,200);
});
test('delete body admits only a bounded opaque chat identity, never paths or session keys',async t=>{
  const f=await fixture(t);
  for(const body of [{user:'../other'},{user:'agent:pixel:other'},{user:'chat',path:'/home/user'},{user:'a'.repeat(129)},{user:'chat\n'}]) {
    assert.equal((await f.request('/v1/chat/images-delete',body)).status,400);
  }
  assert.equal(f.calls.length,0);assert.ok(f.store.read(f.user,f.ref,[f.ref]));
});
