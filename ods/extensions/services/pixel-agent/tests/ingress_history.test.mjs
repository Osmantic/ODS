import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {odsAdmissionCanonical,odsAdmissionMac,odsAdmissionSha} from '../host/probe_admission.mjs';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
const u=content=>({role:'user',content}),a=content=>({role:'assistant',content});
const rawUser='history-canary', user=computeSessionUser({user:rawUser});
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const state=()=>({schemaVersion:1,status:'ready',sessionExists:true,sessionRevision:'a'.repeat(64),context:{used:100,window:32000,measuredAt:1},model:{id:'test',provider:'local',contextWindow:32000},compaction:{status:'idle',requestId:null,tokensBefore:null,tokensAfter:null,reason:null,count:0}});
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
async function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ods-ingress-history-'));fs.chmodSync(dir,0o700);
  const ledger=createChatHistoryLedger(dir),calls=[],native=state();let chatFailure=false,answer='answer';
  const gateway=http.createServer(async(req,res)=>{
    if(req.url==='/health') {res.setHeader('content-type','application/json');return res.end('{"ok":true}');}
    let raw='';for await(const part of req) raw+=part;
    const body=JSON.parse(raw||'{}');calls.push({path:req.url,body,raw,headers:req.headers});res.setHeader('content-type','application/json');
    if(req.url==='/pixel-ods/context') return res.end(JSON.stringify(native));
    if(req.url==='/pixel-ods/abort') return res.end(JSON.stringify({aborted:true}));
    if(req.url==='/pixel-ods/history') return res.end(JSON.stringify({schemaVersion:1,hydrated:true}));
    if(req.url==='/pixel-ods/compact') {native.status='ready';native.compaction={...native.compaction,status:'completed',requestId:body.request_id,count:native.compaction.count+1};return res.end(JSON.stringify(native));}
    if(req.url==='/pixel-ods/verification') return res.end(JSON.stringify({status:'none'}));
    if(req.url==='/v1/chat/completions') {if(chatFailure){res.statusCode=500;return res.end('{}')}return res.end(JSON.stringify({id:runId,choices:[{message:{role:'assistant',content:answer}}]}));}
    res.statusCode=404;res.end('{}');
  });
  const port=await listen(gateway),ingress=createIngressServer({token:'test-token',gatewayPort:port,historyLedger:ledger});
  const ingressPort=await listen(ingress);
  t.after(async()=>{await Promise.all([new Promise(r=>ingress.close(r)),new Promise(r=>gateway.close(r))]);fs.rmSync(dir,{recursive:true,force:true})});
  async function post(route,body,headers={}) {const response=await fetch(`http://127.0.0.1:${ingressPort}${route}`,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});return {status:response.status,value:await response.json()}}
  const chat=(request_id,messages)=>post('/v1/chat/completions',{user:rawUser,request_id,history_snapshot:{schemaVersion:1,messages},messages:[{role:'system',content:'Trusted identity'},...messages.slice(-3,-1),u(messages.at(-1).content+'\nDelivery contract')],stream:false});
  return {dir,ledger,calls,native,post,chat,setFailure:(value=true)=>{chatFailure=value},setAnswer:value=>{answer=value}};
}
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


test('ordinary ingress socket binds owned history request to exact gateway bytes without trusting public probe headers',async t=>{
  const f=await fixture(t),old=process.env.ODS_PIXEL_PROBE_ROOT;
  const root=path.join(f.dir,'probe');fs.mkdirSync(root,{mode:0o700});process.env.ODS_PIXEL_PROBE_ROOT=root;
  t.after(()=>{if(old===undefined)delete process.env.ODS_PIXEL_PROBE_ROOT;else process.env.ODS_PIXEL_PROBE_ROOT=old});
  const incoming={user:rawUser,request_id:'owned-probe',history_snapshot:{schemaVersion:1,messages:[u('six times seven')]},messages:[{role:'system',content:'Trusted identity'},u('six times seven\nDelivery contract')],stream:false};
  const auth={schemaVersion:1,agentId:'pixel',ownerUid:process.getuid(),scopeId:crypto.randomUUID(),signingKey:crypto.randomBytes(32).toString('hex'),expiresAtMs:Date.now()+60000,maxAttempts:16,user,requestId:incoming.request_id,sessionId:'existing-session',sessionKey:'agent:pixel:owned',provider:'local',modelId:'test',baseUrl:'http://127.0.0.1:1234/v1'};
  auth.incomingHmac=odsAdmissionMac(auth.signingKey,odsAdmissionCanonical(incoming));
  const authFile=path.join(root,'authorize-'+odsAdmissionSha(user+'\0'+incoming.request_id)+'.json');fs.writeFileSync(authFile,JSON.stringify(auth),{mode:0o600});
  assert.equal((await f.post('/v1/chat/completions',incoming,{'X-ODS-Probe-Admission':'forged-public','X-ODS-Probe':'forged-public'})).status,200);
  const chat=f.calls.find(c=>c.path==='/v1/chat/completions'),header=chat.headers['x-ods-probe-admission'];assert.match(header,/^[a-f0-9]{32}\.[a-f0-9]{64}$/);assert(!chat.headers['x-ods-probe']);
  const transfer=JSON.parse(fs.readFileSync(path.join(root,'transfer-'+header.split('.')[0]+'.json')));
  assert.equal(transfer.gatewayBodySha256,odsAdmissionSha(chat.raw));assert(!chat.raw.includes(auth.scopeId));assert(!fs.existsSync(authFile));
  assert.equal((await f.post('/v1/chat/completions',incoming)).status,200);assert.equal(f.calls.filter(c=>c.path==='/v1/chat/completions').length,1,'ordinary history replay never resubmits');
});
