// Restart recovery (the ingress's /v1/chat/settle) and a new owner turn treat
// an idle owner session as proof that no run of the chat is left. A turn that
// delegated can leave the owner session idle while its children still work or
// will still report back into the chat. These tests pin when the delegation
// registry keeps such a chat busy, and that the plugin's context report says so.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {Readable} from 'node:stream';
import {createSubagentDelivery} from '../plugin/subagent-delivery.mjs';
import {readContextRequest} from '../plugin/context-compaction.mjs';

const user='ods-'+'a'.repeat(64), other='ods-'+'b'.repeat(64);
const id='chatcmpl_11111111-2222-4333-8444-555555555555';
const child='agent:pixel:subagent:22222222-2222-4333-8444-555555555555';
const childRun='33333333-2222-4333-8444-555555555555';
const owner={agentId:'pixel',runId:id,sessionId:'owner-session',sessionKey:'agent:pixel:openai-user:'+user,trigger:'user'};
const continuation={...owner,runId:`announce:v1:${child}:${childRun}`,
  inputProvenance:{kind:'inter_session',sourceTool:'subagent_announce',sourceSessionKey:child}};

function fixture() {
  let clock=1, access='current';
  // Native sessions with an active embedded run, as resolveActiveEmbeddedRunSessionId reports them.
  const running=new Set();
  const registry=createSubagentDelivery({now:()=>clock,accessIdentity:()=>access,
    finalText:message=>message.content.filter(block=>block.type==='text').map(block=>block.text).join('\n'),
    resolveOwnerSession:key=>key===owner.sessionKey?{sessionId:owner.sessionId}:null,
    abortSession:async key=>!running.has(key)});
  registry.observe({},owner);
  function spawn({key=child,runId=childRun,accepted=true}={}) {
    const context={...owner,toolName:'sessions_spawn',toolCallId:'spawn-'+key};
    registry.before({params:{runtime:'subagent',mode:'run'}},context);
    registry.nativeSpawn({runId,childSessionKey:key},{runId,childSessionKey:key,requesterSessionKey:owner.sessionKey});
    running.add(key);
    if (accepted) registry.after({result:{details:{status:'accepted',runId,childSessionKey:key}}},context);
  }
  function yieldTurn() {
    const context={...owner,toolName:'sessions_yield',toolCallId:'yield'};
    registry.before({params:{}},context);
    registry.after({result:{details:{status:'yielded'}}},context);
    registry.end({success:true},owner);
  }
  function final(text='Both reviews are done.') {
    registry.finalize({lastAssistantMessage:text},continuation);
    registry.end({success:true,messages:[{role:'assistant',stopReason:'stop',content:[{type:'text',text}]}]},continuation);
  }
  return {registry,running,spawn,yieldTurn,final,
    pending:(who=user,isActive=key=>running.has(key))=>registry.pending(who,isActive),
    tick:()=>{clock+=33*60*1000;},revoke:()=>{access='downgraded';}};
}

test('a delegated turn stays pending while its child runs and until the child reports back', () => {
  const f=fixture();
  f.spawn();f.yieldTurn();
  assert.equal(f.pending(),true,'the child still runs while the owner session waits');
  f.running.delete(child);
  assert.equal(f.pending(),true,'the child ended but its result has not reached the chat yet');
  f.registry.observe({prompt:'Review result: nothing to fix.'},continuation);
  f.final();
  assert.equal(f.pending(),false,'the consolidated answer is ready and nothing runs');
  assert.equal(f.registry.read(user,id).status,'ready');
  assert.equal(f.pending(),false,'a delivered answer is settled');
});

test('a native child that started before its spawn reply arrived keeps the turn pending', () => {
  const f=fixture();
  f.spawn({accepted:false});
  assert.equal(f.pending(),true,'the spawn call is still open and its child already runs');
  f.running.delete(child);
  assert.equal(f.pending(),true,'the spawn call has not answered yet');
});

test('Stop ends the wait; only a child that still runs keeps the chat busy', async () => {
  const f=fixture();
  f.spawn();f.yieldTurn();
  assert.deepEqual(await f.registry.cancel(user),{tracked:true,aborted:false},'the child could not be stopped yet');
  assert.equal(f.pending(),true,'never treat a child that still runs as finished');
  f.running.delete(child);
  assert.equal(f.pending(),false,'a stopped delegation can no longer report back');
});

test('a delegation that can no longer report back is over once its child ends', () => {
  for (const end of ['access change','expiry']) {
    const f=fixture();
    f.spawn();f.yieldTurn();
    if (end==='access change') f.revoke(); else f.tick();
    assert.equal(f.pending(),true,`${end}: the child still runs`);
    f.running.delete(child);
    assert.equal(f.pending(),false,end);
    assert.equal(f.registry.admission(continuation)?.outcome,'block',`${end}: its late result cannot start a turn either`);
  }
});

test('ordinary turns, other chats and malformed users are never pending', () => {
  const f=fixture();
  assert.equal(f.pending(),false,'an owner turn that never delegated');
  f.spawn();f.yieldTurn();
  assert.equal(f.pending(other),false,'another chat');
  for (const malformed of [undefined,'',owner.sessionKey,'ods-'+'A'.repeat(64)]) assert.equal(f.registry.pending(malformed,()=>true),false,String(malformed));
});

test('only an explicit idle answer proves a child has ended', () => {
  const f=fixture();
  f.spawn();f.yieldTurn();f.revoke();
  assert.equal(f.pending(user,()=>{throw new Error('registry unavailable');}),true);
  assert.equal(f.pending(user,()=>undefined),true);
  assert.equal(f.pending(user,()=>false),false);
});

// Values built inside the vm context have that context's prototypes.
const plain=value=>JSON.parse(JSON.stringify(value));

function contextRoute(registry,{active=new Set(),report}) {
  const source=fs.readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start=source.indexOf("    for (const operation of ['context','compact']) {");
  const end=source.indexOf('    // The OpenAI-compatible gateway route does not dispatch',start);
  assert.ok(start>=0 && end>start);
  const routes={}, compacted=[];
  vm.runInNewContext(source.slice(start,end),{api:{registerHttpRoute:value=>{routes[value.path]=value;}},
    readContextRequest,delegationDelivery:registry,resolveActiveEmbeddedRunSessionId:key=>active.has(key)?'native-session':undefined,
    contextCompaction:{context:()=>({...report()}),compact:async (who,requestId)=>{compacted.push([who,requestId]);return {...report()};}},
    sendJson:(res,status,value)=>{res.status=status;res.value=value;}});
  const call=async (path,body)=>{
    const req=Readable.from([Buffer.from(JSON.stringify(body))]);
    req.method='POST';req.headers={'content-type':'application/json'};const res={};
    await routes[path].handler(req,res);return res;
  };
  return {context:who=>call('/pixel-ods/context',{user:who}),compact:(who,requestId)=>call('/pixel-ods/compact',{user:who,request_id:requestId}),compacted};
}

test('the plugin reports an idle owner session busy while its delegation is pending', async () => {
  const f=fixture();
  let status='ready';
  const report=()=>({schemaVersion:1,status,sessionExists:status!=='missing',sessionRevision:status==='missing'?null:'r1',context:null,model:null,
    compaction:{status:'idle',count:0}});
  const route=contextRoute(f.registry,{active:f.running,report});
  assert.deepEqual(plain((await route.context(user)).value),report(),'nothing delegated: the report is unchanged');
  f.spawn();f.yieldTurn();
  const busy=await route.context(user);
  assert.equal(busy.status,200);
  assert.deepEqual(plain(busy.value),{...report(),status:'busy'},'only the status changes');
  status='missing';
  assert.equal((await route.context(user)).value.status,'busy');
  status='unavailable';
  assert.equal((await route.context(user)).value.status,'unavailable','an unavailable report stays unavailable');
  status='ready';
  assert.equal((await route.context(other)).value.status,'ready','another chat is not affected');
  assert.equal((await route.compact(user,'manual-1')).value.status,'ready','a compaction receipt is passed through as it is');
  assert.deepEqual(route.compacted,[[user,'manual-1']]);
  f.running.delete(child);
  assert.equal((await route.context(user)).value.status,'busy','the child result has not reached the chat yet');
  f.registry.observe({prompt:'Review result: nothing to fix.'},continuation);
  f.final();
  assert.equal((await route.context(user)).value.status,'ready');
});
