// Disposable HTTP ingress and real compaction state machine. No model/provider calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {createContextCompaction} from '../plugin/context-compaction.mjs';
const rawUser='native-stop-fixture',user=computeSessionUser({user:rawUser});
const key=`agent:pixel:openai-user:${user}`;
const u=content=>({role:'user',content});
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
const refusal=()=>Object.assign(new Error('Session is active; retry compaction after the current run finishes.'),
  {name:'GatewayClientRequestError',gatewayCode:'UNAVAILABLE'});

// Exercise the production abort route rather than recreating its aggregation.
function abortHandler(compactor,delegated={tracked:false,aborted:false}) {
  const source=fs.readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start=source.lastIndexOf('    api.registerHttpRoute({',source.indexOf('path: "/pixel-ods/abort"'));
  const end=source.indexOf('    api.registerHttpRoute({',start+1);
  assert.ok(start>0 && end>start);
  let route;
  new Function('api','readAbortUser','sendJson','delegationDelivery','contextCompaction','toolLoopGuard',source.slice(start,end))(
    {registerHttpRoute:value=>{route=value;}},async()=>({status:200,user}),
    (res,status,body)=>{res.statusCode=status;res.end(JSON.stringify(body));},
    {cancel:async()=>delegated},compactor,{abortUserRun:async()=>false});
  return route.handler;
}
async function fixture(t,mode) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'ods-stop-race-'));fs.chmodSync(directory,0o700);
  const historyDirectory=path.join(directory,'history');fs.mkdirSync(historyDirectory,{mode:0o700});
  const ledger=createChatHistoryLedger(historyDirectory);
  ledger.prepare(user,'stopped',{schemaVersion:1,messages:[u('Delegate the old review')]},
    {status:'missing',sessionRevision:null,compaction:{count:0}});
  ledger.interrupt(user,'stopped');
  let phase='idle',lease=null,revision=0,calls=0,actions=0,notify;
  const observed=new Promise(resolve=>{notify=resolve;});const submissions=[],routes=[],abortResults=[];
  let releaseFlight,abortNotify;const flight=new Promise(resolve=>{releaseFlight=resolve;});const abortObserved=new Promise(resolve=>{abortNotify=resolve;});
  const compactor=createContextCompaction({directory:path.join(directory,'compaction'),busyRetryDelayMs:mode==='cancel'?100:1,busyRetryLimit:3,
    readSession:()=>({sessionId:'created-by-stopped-turn'}),
    readConfig:()=>({agents:{defaults:{model:'ods-gateway/ods/current'}},models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:'ODS Current (fixture.gguf)',contextWindow:32768}]}}}}),
    admission:{status:()=>({available:true,phase,revision}),owns:token=>lease===token,
      acquire:async(token,expected)=>{assert.equal(phase,'idle');assert.equal(revision,expected);lease=token;phase='held';revision++;},
      release:token=>{assert.equal(token,lease);lease=null;phase='idle';revision++;}},
    callGateway:async(method,_options,params)=>{
      assert.equal(method,'sessions.compact');assert.equal(params.key,key);assert.equal(phase,'held');calls++;notify();
      if(mode==='cancel-flight'){await flight;throw refusal();}
      if(mode==='unknown')throw new Error('transport lost');
      if(mode==='cancel'||mode==='persistent'||calls===1)throw refusal();
      actions++;return {key,ok:true,compacted:true,result:{tokensAfter:100}};
    }});
  const abort=abortHandler(compactor);
  const gateway=http.createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw||'{}');routes.push(req.url);res.setHeader('content-type','application/json');
    if(req.url==='/health')return res.end('{"ok":true}');
    if(req.url==='/pixel-ods/context')return res.end(JSON.stringify(compactor.context(user)));
    if(req.url==='/pixel-ods/history')return res.end('{"schemaVersion":1,"hydrated":true}');
    if(req.url==='/pixel-ods/compact')return res.end(JSON.stringify(await compactor.compact(user,body.request_id)));
    if(req.url==='/pixel-ods/abort') {
      const end=res.end.bind(res);res.end=value=>{abortResults.push(JSON.parse(value));abortNotify();return end(value);};
      return abort(req,res);
    }
    if(req.url==='/pixel-ods/verification')return res.end('{"status":"none"}');
    if(req.url==='/pixel-ods/subagent-delivery')return res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:body.runId,status:'not-delegated'}));
    if(req.url==='/v1/chat/completions') {
      assert.equal(actions,1,'no owner input until compaction is proven');submissions.push(body);
      return res.end(JSON.stringify({id:'chatcmpl_11111111-2222-4333-8444-555555555555',choices:[{finish_reason:'stop',message:{role:'assistant',content:'25'}}]}));
    }
    res.statusCode=404;res.end('{}');
  });
  const gatewayPort=await listen(gateway),ingress=createIngressServer({token:'test-only-token',gatewayPort,historyLedger:ledger});
  const port=await listen(ingress);
  t.after(async()=>{await Promise.all([new Promise(r=>ingress.close(r)),new Promise(r=>gateway.close(r))]);fs.rmSync(directory,{recursive:true,force:true});});
  const post=async(route,body)=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});return {status:response.status,body:await response.json()};};
  return {compactor,ledger,observed,submissions,routes,post,abortResults,abortObserved,releaseFlight,get calls(){return calls;},get actions(){return actions;},get phase(){return phase;},
    chat:()=>post('/v1/chat/completions',{user:rawUser,request_id:'new-owner-turn',stream:false,
      history_snapshot:{schemaVersion:1,messages:[u('Delegate the old review'),u('Do not resume it. What is 18 + 7?')]},
      messages:[u('Do not resume it. What is 18 + 7?')]})};
}

test('first-turn native Stop followed by history hydration submits only the new owner input once',async t=>{
  const f=await fixture(t,'drain');const result=await f.chat();
  assert.equal(result.status,200,JSON.stringify(result.body));assert.equal(result.body.choices[0].message.content,'25');
  assert.equal(f.calls,2);assert.equal(f.actions,1);assert.equal(f.submissions.length,1);
  assert.deepEqual(f.submissions[0].messages,[u('Do not resume it. What is 18 + 7?')]);
  assert.equal(f.ledger.read(user).requests.find(x=>x.id==='stopped').status,'interrupted');
  assert.equal(f.ledger.read(user).requests.find(x=>x.id==='new-owner-turn').status,'completed');assert.equal(f.phase,'idle');
});

test('Stop during native busy wait closes ingress without submitting the owner input',async t=>{
  const f=await fixture(t,'cancel');const pending=f.chat();await f.observed;
  const stopped=await f.post('/v1/chat/cancel',{user:rawUser});assert.equal(stopped.status,200);assert.equal(stopped.body.aborted,true);
  const result=await pending;assert.equal(result.status,502);
  assert.equal(f.calls,1);assert.equal(f.actions,0);assert.equal(f.submissions.length,0);assert.equal(f.phase,'idle');
});

for(const mode of ['unknown','persistent'])test(`unconfirmed history remains undelivered: ${mode}`,async t=>{
  const f=await fixture(t,mode);const result=await f.chat();assert.equal(result.status,502);
  assert.equal(f.calls,mode==='unknown'?1:3);assert.equal(f.actions,0);assert.equal(f.submissions.length,0);
  assert.equal(f.phase,mode==='unknown'?'held':'idle');
  assert.equal(f.ledger.read(user).reason,'history-preparation-failed');
});

test('a cancelled compaction cannot override incomplete native child cancellation',async()=>{
  const handler=abortHandler({cancelPending:async()=>true},{tracked:true,aborted:false});
  let result;await handler({}, {end:body=>{result=JSON.parse(body);}});
  assert.deepEqual(result,{aborted:false});
});

test('Stop while native busy refusal is in flight cannot claim an abort or dispatch current input',async t=>{
  const f=await fixture(t,'cancel-flight');const pending=f.chat();await f.observed;
  const stopping=f.post('/v1/chat/cancel',{user:rawUser});await f.abortObserved;
  assert.deepEqual(f.abortResults[0],{aborted:false},'an unresolved native RPC is not proven cancelled');
  assert.equal(f.calls,1);assert.equal(f.submissions.length,0);assert.equal(f.phase,'held');
  f.releaseFlight();const result=await pending;assert.equal(result.status,502);
  await stopping;
  assert.equal(f.calls,1,'known refusal must not retry after Stop');assert.equal(f.actions,0);assert.equal(f.submissions.length,0);
  assert.equal(f.phase,'idle');
});
