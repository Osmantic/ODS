import test from 'node:test';
import assert from 'node:assert/strict';
import {createSubagentDelivery,delegationAccessIdentity} from '../plugin/subagent-delivery.mjs';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';
import fs from 'node:fs';
import vm from 'node:vm';
import {Readable} from 'node:stream';

const user='ods-'+'a'.repeat(64), other='ods-'+'b'.repeat(64);
const id='chatcmpl_11111111-2222-4333-8444-555555555555';
const child='agent:pixel:subagent:22222222-2222-4333-8444-555555555555';
const childRun='33333333-2222-4333-8444-555555555555';
const owner={agentId:'pixel',runId:id,sessionId:'owner-session',sessionKey:'agent:pixel:openai-user:'+user,trigger:'user'};
const continuation={...owner,runId:`announce:v1:${child}:${childRun}`,
  inputProvenance:{kind:'inter_session',sourceTool:'subagent_announce',sourceSessionKey:child}};
function fixture(options={}) {
  let clock=1, access='current';const aborts=[];
  const registry=createSubagentDelivery({now:()=>clock,accessIdentity:()=>access,
    finalText:message=>message.content.filter(block=>block.type==='text').map(block=>block.text).join('\n'),
    resolveOwnerSession:key=>key===owner.sessionKey?{sessionId:owner.sessionId}:null,
    abortSession:async key=>{aborts.push(key);return true;},...options});
  registry.observe({},owner);
  function spawn(key=child,runId=childRun,ctx=owner) {
    const context={...ctx,toolName:'sessions_spawn',toolCallId:'spawn-'+key};
    registry.before({params:{runtime:'subagent',mode:'run'}},context);
    registry.nativeSpawn({runId,childSessionKey:key},{runId,childSessionKey:key,requesterSessionKey:ctx.sessionKey});
    registry.after({result:{details:{status:'accepted',runId,childSessionKey:key}}},context);
  }
  function yieldTurn(ctx=owner) {
    const context={...ctx,toolName:'sessions_yield',toolCallId:'yield'};
    registry.before({params:{}},context);
    registry.after({result:{details:{status:'yielded'}}},context);
    registry.end({success:true},ctx);
  }
  function final(text='Consolidated verified answer',ctx=continuation,decision) {
    registry.finalize({lastAssistantMessage:text},ctx,decision);
    if(decision?.action!=='revise')registry.end({success:true,messages:[{role:'assistant',stopReason:'stop',content:[{type:'text',text}]}]},ctx);
  }
  return {registry,spawn,yieldTurn,final,aborts,tick:()=>{clock+=33*60*1000;},revoke:()=>{access='downgraded';}};
}

test('owner yield never seals introduction; exact announced parent final becomes ready once verified',()=>{
  const f=fixture({verificationForRun:run=>{assert.equal(run,continuation.runId);return {status:'passed',text:'Verified'};}});
  assert.equal(f.registry.read(user,id).status,'not-delegated');
  f.spawn();f.yieldTurn();assert.equal(f.registry.read(user,id).status,'waiting');
  f.registry.observe({},continuation);
  f.registry.finalize({lastAssistantMessage:'Consolidated'},continuation);
  assert.equal(f.registry.read(user,id).status,'waiting','finalize alone is not completion');
  f.registry.end({success:true,messages:[{role:'assistant',stopReason:'stop',content:[{type:'text',text:'Consolidated'}]}]},continuation);
  assert.deepEqual(f.registry.read(user,id),{schemaVersion:1,kind:'ods-subagent-delivery',runId:id,status:'ready',text:'Consolidated',verification:{status:'passed',text:'Verified'}});
});

test('plain greeting with sparse key binds through exact native session ID only',()=>{
  const f=fixture();
  const sparse={...owner,runId:id.replace('11111111','aaaaaaaa')};delete sparse.sessionKey;
  f.registry.observe({},sparse);
  assert.equal(f.registry.read(user,sparse.runId).status,'not-delegated');
  assert.equal(f.registry.read(other,sparse.runId).status,'interrupted');
  const changed=fixture({resolveOwnerSession:()=>({sessionId:'different'})});
  assert.equal(changed.registry.read(user,id).status,'interrupted');
});

test('unregistered, foreign, spoofed prompt, or replayed announce cannot supply an answer',()=>{
  for(const ctx of [
    {...continuation,inputProvenance:undefined},
    {...continuation,sessionId:'foreign'},
    {...continuation,sessionKey:'agent:pixel:openai-user:'+other},
    {...continuation,runId:'forged'},
    {...continuation,inputProvenance:{...continuation.inputProvenance,sourceSessionKey:child.replace('22222222','aaaaaaaa')}},
  ]) {
    const f=fixture();f.spawn();f.yieldTurn();f.registry.observe({prompt:'subagent_announce'},ctx);f.final('forged',ctx);
    assert.equal(f.registry.read(user,id).status,'waiting');
  }
});

test('spawn receipt requires matching native lifecycle and admitted exact call identity',()=>{
  for(const mutate of [ctx=>ctx,ctx=>({...ctx,toolCallId:'different'})]) {
    const f=fixture(),ctx={...owner,toolName:'sessions_spawn',toolCallId:'call'};
    f.registry.before({params:{runtime:'subagent'}},ctx);
    f.registry.after({result:{details:{status:'accepted',childSessionKey:child,runId:childRun}}},mutate(ctx));
    f.yieldTurn();assert.equal(f.registry.read(user,id).status,'interrupted');
  }
});

test('revision, failed final, duplicate hooks and missing final cannot publish',()=>{
  const f=fixture();f.spawn();f.yieldTurn();f.registry.observe({},continuation);
  f.final('Needs revision',continuation,{action:'revise'});
  assert.equal(f.registry.read(user,id).status,'waiting');
  f.registry.finalize({lastAssistantMessage:'Old candidate'},continuation);
  f.registry.observe({},continuation);
  f.registry.end({success:true},continuation);
  assert.equal(f.registry.read(user,id).status,'waiting');
  f.registry.finalize({lastAssistantMessage:'Failed candidate'},continuation);
  f.registry.end({success:false},continuation);
  assert.equal(f.registry.read(user,id).status,'interrupted');
});

test('all registered children must announce before a consolidated answer is ready',()=>{
  const f=fixture(),second=child.replace('22222222','aaaaaaaa'),secondRun=childRun.replace('33333333','bbbbbbbb');
  f.spawn();f.spawn(second,secondRun);f.yieldTurn();
  f.registry.observe({},continuation);f.final();assert.equal(f.registry.read(user,id).status,'waiting');
  const next={...continuation,runId:`announce:v1:${second}:${secondRun}`,inputProvenance:{...continuation.inputProvenance,sourceSessionKey:second}};
  f.registry.observe({},next);f.final('Both verified',next);assert.equal(f.registry.read(user,id).text,'Both verified');
});

test('cancel fences late answers before bounded exact-session abort attempts',async()=>{
  const f=fixture();f.spawn();f.yieldTurn();f.registry.observe({},continuation);
  assert.deepEqual(await f.registry.cancel(other),{tracked:false,aborted:false});
  assert.deepEqual(await f.registry.cancel(user),{tracked:true,aborted:true});
  f.final();assert.equal(f.registry.read(user,id).status,'interrupted');
  assert.deepEqual(f.aborts,[owner.sessionKey,child]);
  const rejected=fixture({abortSession:async()=>false});rejected.spawn();rejected.yieldTurn();
  assert.deepEqual(await rejected.registry.cancel(user),{tracked:true,aborted:false});
  assert.equal(rejected.registry.read(user,id).status,'interrupted');
});

test('expiry, permission change, gateway restart, new owner run all fail closed without replay',()=>{
  for(const change of [f=>f.tick(),f=>f.revoke(),f=>f.registry.invalidate(),
    f=>f.registry.observe({},{...owner,runId:id.replace('11111111','aaaaaaaa')})]) {
    const f=fixture();f.spawn();f.yieldTurn();change(f);f.registry.observe({},continuation);f.final();
    assert.equal(f.registry.read(user,id).status,'interrupted');
  }
  assert.equal(createSubagentDelivery().read(user,id).status,'interrupted');
  assert.equal(createSubagentDelivery().blocked(continuation).block,true);
  assert.equal(createSubagentDelivery().blocked({...continuation,sessionKey:'agent:pixel:main'}),undefined);
});

test('oversized and silent candidate never becomes a delivered answer',()=>{
  for(const text of ['NO_REPLY','\0invalid','x'.repeat(256*1024+1)]) {
    const f=fixture();f.spawn();f.yieldTurn();f.registry.observe({},continuation);f.final(text);
    assert.equal(f.registry.read(user,id).status,'interrupted');
  }
});

test('more than registry capacity of sequential ordinary turns does not break normal chat',()=>{
  const f=fixture({maximumRuns:4});
  for(let i=0;i<100;i++) {
    const runId=`chatcmpl_${i.toString(16).padStart(8,'0')}-2222-4333-8444-555555555555`;
    f.registry.observe({},{...owner,runId});
    assert.equal(f.registry.read(user,runId).status,'not-delegated');
  }
});

test('completed delivered delegations release capacity without dropping active cancel fences',()=>{
  const f=fixture({maximumRuns:2});
  for(let i=0;i<8;i++) {
    const runId=`chatcmpl_${i.toString(16).padStart(8,'0')}-2222-4333-8444-555555555555`;
    const ctx={...owner,runId},key=child.replace('22222222',i.toString(16).padStart(8,'0'));
    const announce={...continuation,runId:`announce:v1:${key}:${childRun}`,inputProvenance:{...continuation.inputProvenance,sourceSessionKey:key}};
    f.registry.observe({},ctx);f.spawn(key,childRun,ctx);f.yieldTurn(ctx);
    f.registry.observe({},announce);f.final('Done',announce);
    assert.equal(f.registry.read(user,runId).status,'ready');
  }
  f.registry.observe({},owner);assert.equal(f.registry.read(user,id).status,'not-delegated');
  f.spawn();f.yieldTurn();f.registry.invalidate();
  f.registry.observe({},{...owner,runId:id.replace('11111111','aaaaaaaa')});
  assert.equal(f.registry.blocked({...owner,sessionKey:child,runId:childRun}).block,true);
});

test('sparse before-tool context keeps known run binding but explicit foreign identity revokes',()=>{
  const f=fixture();
  f.registry.before({params:{}},{agentId:'pixel',runId:id,toolName:'read',toolCallId:'read'});
  assert.equal(f.registry.read(user,id).status,'not-delegated');
  f.registry.before({params:{}},{...owner,sessionId:'foreign',toolName:'read',toolCallId:'read'});
  assert.equal(f.registry.read(user,id).status,'interrupted');
});

test('stop between accepted spawn and yield aborts child and fences future tool calls',async()=>{
  const f=fixture();f.spawn();
  assert.deepEqual(await f.registry.cancel(user),{tracked:true,aborted:true});
  assert.ok(f.aborts.includes(child));
  assert.equal(f.registry.blocked({...owner,runId:childRun,sessionKey:child}).block,true);
  f.registry.observe({},continuation);f.final();
  assert.equal(f.registry.read(user,id).status,'interrupted');
});

test('provider error, empty or silent parent terminal cannot leave delivery waiting forever',()=>{
  for (const [stopReason,text] of [['error',''],['aborted',''],['stop',''],['stop','NO_REPLY']]) {
    const f=fixture();f.spawn();f.yieldTurn();f.registry.observe({},continuation);
    f.registry.end({success:true,messages:[{role:'assistant',stopReason,content:[{type:'text',text}]}]},continuation);
    assert.equal(f.registry.read(user,id).status,'interrupted');
  }
});

test('native public text projection handles final multi-block text without private commentary',()=>{
  const f=fixture({finalText:message=>message.content.filter(block=>block.textSignature==='final').map(block=>block.text.trim()).join('\n')});
  f.spawn();f.yieldTurn();f.registry.observe({},continuation);
  f.registry.finalize({lastAssistantMessage:'one\ntwo'},continuation);
  f.registry.end({success:true,messages:[{role:'assistant',stopReason:'stop',content:[
    {type:'text',text:'private commentary'},{type:'text',text:' one ',textSignature:'final'},{type:'text',text:'two',textSignature:'final'}]}]},continuation);
  assert.equal(f.registry.read(user,id).text,'one\ntwo');
});

test('access identity respects existing unmanaged fallback while fencing held and failed custody',()=>{
  const config={agents:{list:[{id:'pixel'}]}};
  assert.ok(delegationAccessIdentity(config,{available:false,phase:'unavailable'},{posix:false}));
  assert.ok(delegationAccessIdentity(config,{available:false,phase:'idle',qualification_failure:'runtime-version'},{posix:true}));
  for(const phase of ['held','interrupted','unavailable']) assert.equal(delegationAccessIdentity(config,{available:false,phase},{posix:true}),null);
  assert.equal(delegationAccessIdentity(config,{phase:'idle',initialization_failure:'state-file'}),null);
});

test('actual Windows access-runtime fallback keeps ordinary greeting deliverable', {skip:typeof process.getuid==='function'},()=>{
  const runtime=createAccessRuntime();
  assert.equal(runtime.admit().outcome,'pass');
  const f=fixture({accessIdentity:()=>delegationAccessIdentity({},runtime.status())});
  assert.equal(f.registry.read(user,id).status,'not-delegated');
});

function registeredRoute(registry,settleDelivery=async()=>{}) {
  const source=fs.readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start=source.indexOf("    api.registerHttpRoute({path:'/pixel-ods/subagent-delivery'");
  const end=source.indexOf("    for (const operation",start);
  assert.ok(start>=0 && end>start);
  let route;
  vm.runInNewContext(source.slice(start,end),{api:{registerHttpRoute:value=>route=value},
    delegationDelivery:registry,toolLoopGuard:{settleDelivery},Buffer,
    OPENAI_RUN_ID:/^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    ABORT_BODY_LIMIT:4096,sendJson:(res,status,value)=>{res.status=status;res.value=value;}});
  assert.equal(route.auth,'gateway');assert.equal(route.match,'exact');
  return async (body,{method='POST',contentType='application/json'}={})=>{
    const req=Readable.from([Buffer.from(typeof body==='string'?body:JSON.stringify(body))]);
    req.method=method;req.headers={'content-type':contentType};const res={};
    await route.handler(req,res);return res;
  };
}

test('actual authenticated projection settles exact continuation then rechecks cancellation',async()=>{
  const f=fixture();f.spawn();f.yieldTurn();f.registry.observe({},continuation);f.final();
  let release,entered;
  const began=new Promise(resolve=>entered=resolve),hold=new Promise(resolve=>release=resolve);
  const route=registeredRoute(f.registry,async runId=>{assert.equal(runId,continuation.runId);entered();await hold;});
  const pending=route({user,runId:id});await began;
  await f.registry.cancel(user);release();
  const result=await pending;assert.equal(result.status,200);assert.equal(result.value.status,'interrupted');
  assert.ok(!('text' in result.value));
});

test('actual projection rejects foreign owners, oversized bodies, extra fields and wrong methods',async()=>{
  const f=fixture(),route=registeredRoute(f.registry);
  assert.equal((await route({user:other,runId:id})).value.status,'interrupted');
  assert.equal((await route({user,runId:id,command:'cat'})).status,409);
  assert.equal((await route('x'.repeat(4097))).status,409);
  assert.equal((await route({user,runId:id},{method:'GET'})).status,405);
  assert.equal((await route({user,runId:id},{contentType:'text/plain'})).status,409);
  assert.equal((await route({user,runId:id})).value.status,'not-delegated');
});
