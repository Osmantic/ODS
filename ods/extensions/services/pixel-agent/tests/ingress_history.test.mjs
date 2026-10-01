import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {createHash} from 'node:crypto';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {createChatImageStore} from '../host/chat_image_store.mjs';
const u=content=>({role:'user',content}),a=content=>({role:'assistant',content});
const rawUser='history-canary', user=computeSessionUser({user:rawUser});
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const state=()=>({schemaVersion:1,status:'ready',sessionExists:true,sessionRevision:'a'.repeat(64),context:{used:100,window:32000,measuredAt:1},model:{id:'test',provider:'local',contextWindow:32000},compaction:{status:'idle',requestId:null,tokensBefore:null,tokensAfter:null,reason:null,count:0}});
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
async function fixture(t, verification={status:'none'}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ods-ingress-history-'));fs.chmodSync(dir,0o700);
  const ledger=createChatHistoryLedger(dir),calls=[],native=state();let chatFailure=false,answer='answer';
  const gateway=http.createServer(async(req,res)=>{
    if(req.url==='/health') {res.setHeader('content-type','application/json');return res.end('{"ok":true}');}
    let raw='';for await(const part of req) raw+=part;
    const body=JSON.parse(raw||'{}');calls.push({path:req.url,body});res.setHeader('content-type','application/json');
    if(req.url==='/pixel-ods/context') return res.end(JSON.stringify(native));
    if(req.url==='/pixel-ods/abort') return res.end(JSON.stringify({aborted:true}));
    if(req.url==='/pixel-ods/history') return res.end(JSON.stringify({schemaVersion:1,hydrated:true}));
    if(req.url==='/pixel-ods/images-bind') return res.end(JSON.stringify({schemaVersion:1,bound:true}));
    if(req.url==='/pixel-ods/compact') {native.status='ready';native.compaction={...native.compaction,status:'completed',requestId:body.request_id,count:native.compaction.count+1};return res.end(JSON.stringify(native));}
    if(req.url==='/pixel-ods/verification') return res.end(JSON.stringify(verification));
    if(req.url==='/v1/chat/completions') {if(chatFailure){res.statusCode=500;return res.end('{}')}return res.end(JSON.stringify({id:runId,choices:[{message:{role:'assistant',content:answer}}]}));}
    res.statusCode=404;res.end('{}');
  });
  const imageStore=createChatImageStore(path.join(dir,'images'));
  const port=await listen(gateway),ingress=createIngressServer({token:'test-token',gatewayPort:port,historyLedger:ledger,imageStore});
  const ingressPort=await listen(ingress);
  t.after(async()=>{await Promise.all([new Promise(r=>ingress.close(r)),new Promise(r=>gateway.close(r))]);fs.rmSync(dir,{recursive:true,force:true})});
  async function post(route,body) {const response=await fetch(`http://127.0.0.1:${ingressPort}${route}`,{method:'POST',headers:{'content-type':'application/json',...(body.messages?.at(-1)?.images?{'x-ods-image-turn':'1'}:{})},body:JSON.stringify(body)});return {status:response.status,value:await response.json()}}
  const chat=(request_id,messages)=>post('/v1/chat/completions',{user:rawUser,request_id,history_snapshot:{schemaVersion:1,messages},messages:[{role:'system',content:'Trusted identity'},...messages.slice(-3,-1),u(messages.at(-1).content+'\nDelivery contract')],stream:false});
  return {ledger,imageStore,calls,native,post,chat,setFailure:(value=true)=>{chatFailure=value},setAnswer:value=>{answer=value}};
}

test('image turn reaches gateway as bytes, caches privately and replays without another call',async t=>{
  const f=await fixture(t);
  f.native.model={...f.native.model,imageInput:'supported',routeFingerprint:'f'.repeat(64)};
  const data=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6ioAAAAASUVORK5CYII=','base64');
  const ref={id:'img-'+'a'.repeat(32),sha256:createHash('sha256').update(data).digest('hex')};
  const archived={role:'user',content:'',images:[ref]};
  const body={user:rawUser,request_id:'image-turn',stream:false,
    image_route:{routeFingerprint:'f'.repeat(64),unknownConsent:false},
    history_snapshot:{schemaVersion:2,messages:[archived]},
    messages:[{...archived,content:[{type:'image_url',image_url:{url:'data:image/png;base64,'+data.toString('base64')}}]}]};
  assert.equal((await f.post('/v1/chat/completions',body)).status,200);
  const binding=f.calls.findIndex(call=>call.path==='/pixel-ods/images-bind');
  assert.ok(binding>=0 && binding<f.calls.findIndex(call=>call.path==='/v1/chat/completions'));
  assert.deepEqual(f.calls[binding].body,{user});
  const delivered=f.calls.find(call=>call.path==='/v1/chat/completions').body.messages.at(-1);
  assert.deepEqual(delivered.content[0],body.messages[0].content[0]);
  assert.equal('images' in delivered,false);
  assert.deepEqual(f.imageStore.read(user,ref,[ref]).data,data);
  assert.deepEqual(f.ledger.read(user).messages,[archived]);
  const read=await f.post('/v1/chat/image',{user,...ref});
  assert.equal(read.status,200);
  assert.deepEqual(Buffer.from(read.value.image.data,'base64'),data);
  const policy=await f.post('/v1/chat/image-policy',{user});
  assert.deepEqual(policy.value,{schemaVersion:1,policy:{imageInput:'supported',routeFingerprint:'f'.repeat(64),unknownConsent:false}});
  assert.equal((await f.post('/v1/chat/image',{user:'ods-'+'b'.repeat(64),...ref})).status,409);
  assert.equal((await f.post('/v1/chat/image',{user,...ref,path:'/tmp/private'})).status,400);
  assert.equal((await f.post('/v1/chat/completions',body)).status,200);
  assert.equal(f.calls.filter(call=>call.path==='/v1/chat/completions').length,1);
  f.native.model.imageInput='unsupported';
  assert.equal((await f.post('/v1/chat/image',{user,...ref})).status,409);
  assert.equal((await f.post('/v1/chat/completions',{...body,request_id:'rejected'})).status,409);
  assert.equal(f.calls.filter(call=>call.path==='/v1/chat/completions').length,1);
});
test('ingress delivers a delta with the edge contract, persists full snapshot and replays without rerunning',async t=>{
  const f=await fixture(t);
  assert.equal((await f.chat('first',[u('first')])).status,200);
  const messages=[u('first'),a('answer'),u('second')];assert.equal((await f.chat('second',messages)).status,200);
  const chats=f.calls.filter(c=>c.path==='/v1/chat/completions');assert.equal(chats.length,2);
  assert.deepEqual(chats[1].body.messages,[{role:'system',content:'Trusted identity'},u('second\nDelivery contract')]);
  assert.equal(f.ledger.read(user).messages.length,3);assert.equal(f.ledger.projection(user).acknowledgedMessages,3);
  assert.equal((await f.chat('second',messages)).status,200);assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,2);
  const context=await f.post('/v1/chat/context',{user:rawUser});assert.equal(context.status,200);assert.equal(context.value.history.acknowledgedMessages,3);assert.equal('sessionExists' in context.value,false);
});
test('initial archived conversation is hydrated and compacted before current input runs',async t=>{
  const f=await fixture(t),history=[u('old task'),a('old response'),u('new request')];
  assert.equal((await f.chat('seeded',history)).status,200);
  const paths=f.calls.map(c=>c.path);assert.ok(paths.indexOf('/pixel-ods/history')<paths.indexOf('/pixel-ods/compact'));assert.ok(paths.indexOf('/pixel-ods/compact')<paths.indexOf('/v1/chat/completions'));
  assert.deepEqual(f.calls.find(c=>c.path==='/pixel-ods/history').body.messages,history.slice(0,-1));
  assert.deepEqual(f.calls.find(c=>c.path==='/v1/chat/completions').body.messages,[{role:'system',content:'Trusted identity'},u('new request\nDelivery contract')]);
});

test('missing answer persists its incomplete outcome and cached delivery never reruns the task',async t=>{
  const f=await fixture(t);f.setAnswer('NO_REPLY');
  const first=await f.chat('silent',[u('Check the existing report without modifying it')]);
  assert.equal(first.status,200);
  assert.match(first.value.choices[0].message.content,/request is incomplete/);
  assert.equal(f.ledger.read(user).lastResult.verification.status,'failed');
  const again=await f.chat('silent',[u('Check the existing report without modifying it')]);
  assert.deepEqual(again,first);
  assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,1);
});
test('unconfirmed execution is retained as unknown and refuses silent resubmission',async t=>{
  const f=await fixture(t);f.setFailure();assert.equal((await f.chat('failed',[u('execute')])).status,502);
  assert.equal(f.ledger.projection(user).status,'unknown');
  const retry=await f.chat('retry',[u('execute')]);assert.equal(retry.status,409);assert.match(retry.value.error.message,/history-outcome-unknown/);
  assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,1);
});
test('manual compaction refuses a concurrent turn; control and archive reject foreign addressing',async t=>{
  const f=await fixture(t),release=f.ledger.lock(user);
  try {assert.equal((await f.post('/v1/chat/compact',{user:rawUser,request_id:'manual'})).status,409)} finally {release()}
  assert.equal((await f.post('/v1/chat/compact',{user:rawUser,request_id:'manual'})).value.compaction.requestId,'manual');
  assert.equal((await f.post('/v1/chat/context',{user:rawUser,sessionKey:'another'})).status,400);
  assert.equal((await f.post('/v1/chat/history',{user:'../../another'})).status,400);
});
test('Stop confirms an unknown outcome as interrupted so the next turn sends only its new input',async t=>{
  const f=await fixture(t);f.setFailure();await f.chat('stopped',[u('old task')]);f.setFailure(false);
  assert.equal(f.ledger.projection(user).status,'unknown');
  const stop=await f.post('/v1/chat/cancel',{user:rawUser});assert.deepEqual(stop.value,{aborted:true});
  assert.equal(f.ledger.projection(user).status,'ready');
  assert.equal((await f.chat('next',[u('old task'),u('different task')])).status,200);
  assert.deepEqual(f.calls.filter(c=>c.path==='/v1/chat/completions').at(-1).body.messages,[{role:'system',content:'Trusted identity'},u('different task\nDelivery contract')]);
});


test('incomplete attribution is a delivered outcome, not an interrupted turn',async t=>{
  const text='Research summary. Source check: attribution remains incomplete.';
  const f=await fixture(t,{status:'failed',text});
  const result=await f.chat('uncited',[u('Research official sources')]);
  assert.equal(result.status,200);
  assert.equal(result.value.choices[0].message.content,text);
  assert.equal(f.ledger.read(user).lastResult.verification.status,'failed');
  assert.notEqual(f.ledger.projection(user).status,'unknown');
  const next=await f.chat('next',[u('Research official sources'),a(text),u('Explain the limitation')]);
  assert.equal(next.status,200,'the next owner message needs no interrupted-turn resolution');
  assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,2);
});
