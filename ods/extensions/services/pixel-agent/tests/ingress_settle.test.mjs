// A restart that cuts a turn off leaves its history outcome unknown. Settle
// must prove nothing still runs before recording that turn as interrupted, and
// must never stop work itself: Stop stays the only way to end running work.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';

const u=content=>({role:'user',content});
const rawUser='settle-canary', user=computeSessionUser({user:rawUser});
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const nativeState=()=>({schemaVersion:1,status:'ready',sessionExists:true,sessionRevision:'a'.repeat(64),context:{used:100,window:32000,measuredAt:1},model:{id:'test',provider:'local',contextWindow:32000},compaction:{status:'idle',requestId:null,tokensBefore:null,tokensAfter:null,reason:null,count:0}});
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));

async function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ods-ingress-settle-'));fs.chmodSync(dir,0o700);
  // The turn that a restart cut off: prepared (pending) by the previous ingress process.
  const before=createChatHistoryLedger(dir);
  before.prepare(user,'cut-off',{schemaVersion:1,messages:[u('Can you do things on cron timers?')]},nativeState());
  // The restarted ingress has no record of that turn in flight.
  const ledger=createChatHistoryLedger(dir),calls=[],native=nativeState();
  const gateway=http.createServer(async(req,res)=>{
    if(req.url==='/health') {res.setHeader('content-type','application/json');return res.end('{"ok":true}');}
    let raw='';for await(const part of req) raw+=part;
    const body=JSON.parse(raw||'{}');calls.push({path:req.url,body});res.setHeader('content-type','application/json');
    if(req.url==='/pixel-ods/context') return res.end(JSON.stringify(native));
    if(req.url==='/pixel-ods/abort') return res.end(JSON.stringify({aborted:true}));
    if(req.url==='/pixel-ods/verification') return res.end(JSON.stringify({status:'none'}));
    if(req.url==='/pixel-ods/subagent-delivery') return res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:body.runId,status:'not-delegated'}));
    if(req.url==='/v1/chat/completions') return res.end(JSON.stringify({id:runId,choices:[{finish_reason:'stop',message:{role:'assistant',content:'Yes, I can schedule work.'}}]}));
    res.statusCode=404;res.end('{}');
  });
  const port=await listen(gateway),ingress=createIngressServer({token:'test-token',gatewayPort:port,historyLedger:ledger});
  const ingressPort=await listen(ingress);
  t.after(async()=>{await Promise.all([new Promise(r=>ingress.close(r)),new Promise(r=>gateway.close(r))]);fs.rmSync(dir,{recursive:true,force:true})});
  async function request(route,body,{method='POST',contentType='application/json'}={}) {
    const response=await fetch(`http://127.0.0.1:${ingressPort}${route}`,{method,headers:{'content-type':contentType},...(method==='POST'?{body:typeof body==='string'?body:JSON.stringify(body)}:{})});
    return {status:response.status,value:await response.json()};
  }
  const settle=(body={user:rawUser})=>request('/v1/chat/settle',body);
  const chat=(request_id,messages)=>request('/v1/chat/completions',{user:rawUser,request_id,history_snapshot:{schemaVersion:1,messages},messages:[{role:'system',content:'Trusted identity'},u(messages.at(-1).content)],stream:false});
  const stops=()=>calls.filter(call=>call.path==='/pixel-ods/abort' || call.path==='/v1/chat/completions');
  return {dir,ledger,calls,native,request,settle,chat,stops};
}

test('a turn cut off by a restart settles without stopping anything, and the next turn runs',async t=>{
  const f=await fixture(t);
  assert.equal(f.ledger.projection(user).status,'unknown');
  const refused=await f.chat('checking-in',[u('Can you do things on cron timers?'),u('Checking in')]);
  assert.equal(refused.status,409,'an unresolved turn still refuses a silent resubmission');
  assert.deepEqual(await f.settle(),{status:200,value:{state:'settled'}});
  assert.deepEqual(f.stops(),[],'settle never aborts a run or starts one');
  const settled=f.ledger.read(user);
  assert.equal(settled.status,'ready');
  assert.equal(settled.reason,'history-interrupted');
  assert.equal(settled.requests.at(-1).status,'interrupted');
  // Settling twice is harmless: there is nothing left to record.
  assert.deepEqual(await f.settle(),{status:200,value:{state:'settled'}});
  const next=await f.chat('checking-in-again',[u('Can you do things on cron timers?'),u('Checking in')]);
  assert.equal(next.status,200);
  assert.deepEqual(f.calls.filter(call=>call.path==='/v1/chat/completions').at(-1).body.messages,[{role:'system',content:'Trusted identity'},u('Checking in')]);
});

test('a busy native session answers active and leaves the unresolved turn alone',async t=>{
  const f=await fixture(t);
  f.native.status='busy';
  assert.deepEqual(await f.settle(),{status:200,value:{state:'active'}});
  assert.equal(f.ledger.projection(user).status,'unknown');
  assert.deepEqual(f.stops(),[]);
});

test('a turn holding the history lock answers active without reading or changing history',async t=>{
  const f=await fixture(t);
  const release=f.ledger.lock(user);
  try {assert.deepEqual(await f.settle(),{status:200,value:{state:'active'}});}
  finally {release();}
  assert.equal(f.calls.filter(call=>call.path==='/pixel-ods/context').length,0);
  assert.equal(f.ledger.projection(user).status,'unknown');
});

test('an unusable history lock answers unconfirmed, never settled and never active forever',async t=>{
  const f=await fixture(t);
  // Something that is not a lock file sits where the lock belongs, as after a
  // crash. Settle cannot take the lock, so it must not claim anything.
  const lockPath=path.join(f.dir,`${user}.lock`);
  fs.mkdirSync(lockPath);
  const answer=await f.settle();
  assert.equal(answer.status,503);
  assert.equal(answer.value.error.message,'settle-unconfirmed');
  assert.equal(f.ledger.projection(user).status,'unknown');
  assert.deepEqual(f.stops(),[]);
  fs.rmdirSync(lockPath);
  assert.deepEqual(await f.settle(),{status:200,value:{state:'settled'}});
});

test('settle fails closed when idle cannot be proven or the request is not exact',async t=>{
  const f=await fixture(t);
  f.native.status='unavailable';
  assert.equal((await f.settle()).status,503);
  assert.equal(f.ledger.projection(user).status,'unknown');
  for(const body of [{},{user:rawUser,request_id:'cut-off'},{user:'../escape'},{user:7},'not json'])
    assert.equal((await f.settle(body)).status,400,JSON.stringify(body));
  assert.equal((await f.request('/v1/chat/settle?user=other',{user:rawUser})).status,400);
  assert.equal((await f.request('/v1/chat/settle',{user:rawUser},{contentType:'text/plain'})).status,415);
  assert.equal((await f.request('/v1/chat/settle',undefined,{method:'GET'})).status,405);
  assert.equal(f.ledger.projection(user).status,'unknown');
  assert.deepEqual(f.stops(),[]);
});
