import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {createIngressServer,computeSessionUser,createLoopbackGatewayFetch} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';

const rawUser='connect-refusal-fixture',user=computeSessionUser({user:rawUser});
const u=content=>({role:'user',content}),a=content=>({role:'assistant',content});
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const completion={id:runId,choices:[{finish_reason:'stop',message:a('answer')}]};
const native={schemaVersion:1,status:'ready',sessionExists:true,sessionRevision:'a'.repeat(64),context:{used:100,window:32000,measuredAt:1},model:{id:'fixture',provider:'local',contextWindow:32000},compaction:{status:'idle',requestId:null,tokensBefore:null,tokensAfter:null,reason:null,count:0}};
const listen=(server,port=0,host='127.0.0.1')=>new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,host,()=>{server.off('error',reject);resolve(server.address().port)})});
const close=server=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections()});

async function fixture(t,host='127.0.0.1') {
  const tempParent=path.resolve(os.tmpdir());
  const directory=fs.mkdtempSync(path.join(tempParent,'ods-connect-refusal-'));fs.chmodSync(directory,0o700);
  const ledger=createChatHistoryLedger(directory),chats=[],errors=[];
  let fault=null,attempts=0,beforeRefusal;
  const gateway=http.createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw||'{}');res.setHeader('content-type','application/json');
    res.setHeader('connection','close'); // Each chat must establish a real fresh TCP connection.
    if(req.url==='/health')return res.end('{}');
    if(req.url==='/pixel-ods/context')return res.end(JSON.stringify(native));
    if(req.url==='/pixel-ods/verification')return res.end('{"status":"none"}');
    if(req.url==='/pixel-ods/subagent-delivery')return res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:body.runId,status:'not-delegated'}));
    if(req.url==='/pixel-ods/read-only-extension-continuation')return res.end(JSON.stringify({
      schemaVersion:1,kind:'ods-extension-read-only-continuation',eligible:true,chatId:rawUser,requestId:'uncertain'}));
    if(req.url==='/v1/chat/completions'){
      chats.push(body);
      if(fault==='truncated')return res.end('{"id":');
      if(fault==='continuation-refused')return res.end(JSON.stringify({...completion,choices:[{
        finish_reason:'stop',message:a("\u26a0\ufe0f Agent couldn't generate a response. Please try again.") }]}));
      return res.end(JSON.stringify(completion));
    }
    res.statusCode=404;res.end('{}');
  });
  const gatewayPort=await listen(gateway,0,host);
  const transport=createLoopbackGatewayFetch(); // Unmodified production HTTP transport and discovery.
  const deps={setTimeout,clearTimeout,fetch:async(url,options)=>{
    const chat=String(url).endsWith('/v1/chat/completions');
    if(chat){
      attempts++;
      if(fault==='refused' || fault==='continuation-refused' && attempts===2){
        beforeRefusal?.();await close(gateway);
      }else if(fault && typeof fault==='object'){
        throw new TypeError('fetch failed',{cause:{code:'ECONNREFUSED',syscall:'connect',address:'127.0.0.1',port:gatewayPort,...fault}});
      }
    }
    if(fault==='verification-refused' && String(url).endsWith('/pixel-ods/verification')){
      throw new TypeError('fetch failed',{cause:{code:'ECONNREFUSED',syscall:'connect',address:'127.0.0.1',port:gatewayPort}});
    }
    try{return await transport(url,options)}catch(error){if(chat)errors.push(error.cause?.code ?? error.code);throw error}
  }};
  const ingress=createIngressServer({token:'synthetic-token',gatewayPort,historyLedger:ledger,deps});
  const port=await listen(ingress);
  t.after(async()=>{
    await close(ingress);await close(gateway);
    assert.equal(path.dirname(directory),tempParent);assert.match(path.basename(directory),/^ods-connect-refusal-/);
    fs.rmSync(directory,{recursive:true,force:true});
  });
  return {ledger,chats,errors,get attempts(){return attempts},setFault(value,hook){fault=value;beforeRefusal=hook},
    async restore(){fault=null;await listen(gateway,gatewayPort,host)},
    async chat(request_id,messages){
      const response=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({user:rawUser,request_id,history_snapshot:{schemaVersion:1,messages},messages,stream:true})});
      try{return {status:response.status,text:await response.text()}}catch(error){return {status:response.status,streamError:error.name}}
    }};
}

for(const host of ['127.0.0.1','::1'])for(const prior of [false,true])test(`real ${host} refused connection abandons only the unsubmitted ${prior?'follow-up':'first'} input`,async t=>{
  const f=await fixture(t,host);
  if(prior)assert.match((await f.chat('previous',[u('previous')])).text,/answer/);
  const previous=f.ledger.read(user),messages=prior?[u('previous'),a('answer'),u('next')]:[u('next')];
  f.setFault('refused');
  const result=await f.chat('refused',messages);
  assert.equal(result.status,200);assert.equal(result.streamError,undefined);
  assert.equal(result.text,'data: {"error":{"type":"pixel_ingress_error","code":"gateway_connect_refused"}}\n\ndata: [DONE]\n\n');
  assert.deepEqual(f.errors,['ECONNREFUSED']);assert.equal(f.chats.length,prior?1:0);
  assert.equal(f.attempts,prior?2:1); // No automatic resend.
  const after=f.ledger.read(user);
  assert.equal(after.status,'ready');assert.equal(after.reason,'gateway-connect-refused');
  assert.equal(after.acknowledgedMessages,prior?1:0);
  assert.equal(after.requests.at(-1).status,'failed');
  if(prior){assert.deepEqual(after.requests[0],previous.requests[0]);assert.deepEqual(after.messages[0],previous.messages[0]);}
  await f.restore();
  assert.equal((await f.chat('refused',messages)).status,409); // Attempt IDs never silently replay.
  const manual=await f.chat('manual-new-attempt',messages);assert.match(manual.text,/answer/);
  assert.equal(f.chats.length,prior?2:1);assert.deepEqual(f.chats.at(-1).messages,[u('next')]);
});

for(const [label,fault] of [
  ['connect timeout',{code:'ETIMEDOUT'}],['reset',{code:'ECONNRESET'}],
  ['wrong address',{address:'127.0.0.2'}],['wrong port',{port:1}],
  ['wrong syscall',{syscall:'write'}],['truncated response','truncated'],
  ['verification failure after run','verification-refused'],
  ['real continuation connection refusal after run','continuation-refused'],
])test(`${label} never becomes a known-not-started receipt`,async t=>{
  const f=await fixture(t);f.setFault(fault);
  const result=await f.chat('uncertain',[u('task')]);
  assert.doesNotMatch(result.text||'',/gateway_connect_refused/);
  const attempted=fault==='continuation-refused'?2:1;
  assert.equal(f.ledger.projection(user).status,'unknown');assert.equal(f.attempts,attempted);
  if(fault==='continuation-refused'){assert.deepEqual(f.errors,['ECONNREFUSED']);assert.equal(f.chats.length,1);}
  if(fault==='continuation-refused')await f.restore();else f.setFault(null);
  assert.equal((await f.chat('successor',[u('task')])).status,409);
  assert.equal(f.attempts,attempted);
});

test('failure to durably abandon the exact request never promises it can be resent',async t=>{
  const f=await fixture(t);f.ledger.abandon=()=>{throw new Error('fixture write failure')};
  f.setFault('refused');
  const result=await f.chat('uncertain',[u('task')]);
  assert.doesNotMatch(result.text||'',/gateway_connect_refused/);
  assert.equal(f.ledger.projection(user).status,'unknown');assert.equal(f.chats.length,0);
});

test('a completed exact attempt cannot be abandoned by a late connection error',async t=>{
  const f=await fixture(t);
  f.setFault('refused',()=>{
    const state=f.ledger.read(user);f.ledger.complete(user,{state},completion,{status:'none'},native);
  });
  const result=await f.chat('completed',[u('task')]);
  assert.doesNotMatch(result.text||'',/gateway_connect_refused/);
  const kept=f.ledger.read(user);assert.equal(kept.status,'ready');assert.equal(kept.requests[0].status,'completed');
  assert.deepEqual(kept.lastResult.completion,completion);assert.equal(kept.acknowledgedMessages,1);
});

for(const [label,change] of [
  ['exact IPv4',{}],['connect timeout',{code:'ETIMEDOUT'}],['reset',{code:'ECONNRESET'}],
  ['wrong address',{address:'127.0.0.2'}],['wrong port',{port:1235}],['wrong syscall',{syscall:'write'}],
])test(`transport validates the selected destination before proving refusal: ${label}`,async()=>{
  const raw=Object.assign(new Error('synthetic transport failure'),
    {code:'ECONNREFUSED',syscall:'connect',address:'127.0.0.1',port:1234},change);
  const transport=createLoopbackGatewayFetch(async url=>{
    if(new URL(url).pathname==='/health')return new Response('{}',{status:url.includes('[::1]')?503:200});
    throw raw;
  });
  await assert.rejects(transport('http://127.0.0.1:1234/v1/chat/completions',{method:'POST'}),error=>{
    assert.equal(error.constructor.name==='GatewayConnectRefused',label==='exact IPv4');
    assert.equal(label==='exact IPv4'?error.cause:error,raw);
    return true;
  });
});
