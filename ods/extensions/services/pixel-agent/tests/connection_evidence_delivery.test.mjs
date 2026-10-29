import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createConnectionsStatusTool} from '../plugin/connections-status.mjs';
import {withPiToolErrorContract} from '../plugin/pi-tool-result.mjs';
import {applyVerificationToCompletion,parseVerificationResponse,createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';

const RUN='chatcmpl_11111111-2222-4333-8444-555555555555';
const NEXT='chatcmpl_66666666-7777-4888-8999-aaaaaaaaaaaa';
const USER='connection-evidence-delivery';
const context={agentId:'pixel',trigger:'user',runId:RUN,sessionId:'evidence-session',
  sessionKey:`agent:pixel:openai-user:${computeSessionUser({user:USER})}`};
const FALSE_REPLY='No Google account is configured. Set up a new connection.';
const artifact={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'project/report.md',
  siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'report.md',bytes:10,sha256:'b'.repeat(64)}};
const preview={schemaVersion:1,kind:'ods-pixel-workspace-preview',relativeDirectory:'demo',
  siteId:'site-'+'c'.repeat(24),port:9437,files:1,bytes:40,sha256:'c'.repeat(64),entrySha256:'d'.repeat(64)};
preview.url=`http://${preview.siteId}.localhost:${preview.port}/${preview.siteId}/`;

function startGuard() {
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'Check the saved connection. Continue the independent report work.'});
  return guard;
}

let sequence=0;
function receipt(guard,scope,name,result,{wrapped=false,params={}}={}) {
  const toolCallId=`connection-evidence-${++sequence}`;
  const toolName=wrapped?'tool_call':name;
  const args=wrapped?{id:`openclaw:pixel-ods:${name}`,args:params}:params;
  const bound={...scope,toolName,toolCallId};
  const event={toolName,toolCallId,params:args};
  const decision=guard.beforeToolCall(event,bound);
  assert.notEqual(decision?.block,true,decision?.blockReason);
  const observed=wrapped?{content:[{type:'text',text:JSON.stringify({tool:{name,source:'openclaw',sourceName:'pixel-ods'},result})}],
    details:{tool:{name,source:'openclaw',sourceName:'pixel-ods'},result},...(result.isError?{isError:true}:{})}:result;
  guard.afterToolCall({...event,result:observed},bound);
  guard.toolResultPersist({...event,message:{role:'toolResult',toolName,toolCallId,...observed}},bound);
  return {event,bound,observed};
}

async function unknownResult() {
  const tool=withPiToolErrorContract(createConnectionsStatusTool({read:async()=>{throw Error('private diagnostic');}}));
  return tool.execute('unreadable',{});
}

function knownAbsent() {
  return {schemaVersion:1,kind:'ods-connection-agent-status',scope:'reviewed-google',
    observedAt:'2026-10-29T20:00:00Z',available:true,reason:'not-connected',recordConfigured:false,localSavedSignIn:false,
    connectionState:'not-connected',ownerEnabled:{gmailRead:false,calendarRead:false},
    providerPermissionObserved:{gmailRead:null,calendarRead:null},sourceCheckedAt:null,
    reads:{email:{lastObservedState:'disabled',recent:null,lastSuccessAt:null,complete:null,error:null},
      calendar:{lastObservedState:'disabled',recent:null,lastSuccessAt:null,complete:null,error:null}},providerCheckedNow:false};
}

function finalize(guard,scope=context,text=FALSE_REPLY) {
  guard.beforeAgentFinalize({lastAssistantMessage:text,messages:[{role:'assistant',content:[{type:'text',text}],stopReason:'stop'}]},scope);
  return guard.deliveryVerificationForRun(scope.runId);
}

function completion(text=FALSE_REPLY,id=RUN) {
  return {id,choices:[{index:0,message:{role:'assistant',content:text},finish_reason:'stop'}]};
}

for(const wrapped of [false,true]) test(`unknown connection evidence replaces native reply and ingress prose (${wrapped?'Tool Search':'direct'})`,async()=>{
  const guard=startGuard();
  receipt(guard,context,'pixel_ods_connections_status',await unknownResult(),{wrapped});
  // A failed read does not cancel independently allowed work.
  const unrelated=guard.beforeToolCall({toolName:'tool_search',params:{query:'workspace read'},toolCallId:'other-discovery'},
    {...context,toolName:'tool_search',toolCallId:'other-discovery'});
  assert.notEqual(unrelated?.block,true);
  const verification=finalize(guard);
  assert.equal(verification.status,'failed');
  assert.match(verification.text,/Could not verify the requested Google lookup/);
  assert.equal(Object.hasOwn(verification,'deliveryMode'),false);
  const parsed=parseVerificationResponse(JSON.parse(JSON.stringify(verification)),RUN);
  const delivered=applyVerificationToCompletion(completion(),parsed);
  const native=guard.replyPayloadSending({runId:RUN,kind:'final',payload:{text:FALSE_REPLY}});
  assert.equal(native.payload.text,verification.text);
  assert.equal(delivered.choices[0].message.content,verification.text);
  assert.doesNotMatch(delivered.choices[0].message.content,/No Google account is configured/);
  assert.equal(guard.replyPayloadSending({runId:NEXT,kind:'final',payload:{text:'Other task'}}),undefined);
});

test('a known Root not-connected result resolves the failed observation without affecting the next turn',async()=>{
  const guard=startGuard();
  receipt(guard,context,'pixel_ods_connections_status',await unknownResult());
  const result=await createConnectionsStatusTool({read:async()=>knownAbsent()}).execute('known',{});
  assert.notEqual(result.isError,true);
  receipt(guard,context,'pixel_ods_connections_status',result);
  assert.notEqual(finalize(guard).status,'failed');
  const next={...context,runId:NEXT};
  guard.observeRun(next,'pixel',{prompt:'Explain a sorting algorithm without using tools.'});
  const clean=finalize(guard,next,'A stable sorting algorithm preserves the relative order of equal keys.');
  assert.notEqual(clean.status,'failed');
  assert.equal(guard.replyPayloadSending({runId:NEXT,kind:'final',payload:{text:'Independent answer'}}),undefined);
});

test('unresolved connection evidence preserves an independently verified artifact',async()=>{
  const guard=startGuard();
  assert.equal(guard.reserveWorkspaceArtifact(context),true);
  assert.equal(guard.acceptWorkspaceArtifact(context,artifact),true);
  receipt(guard,context,'pixel_ods_connections_status',await unknownResult());
  const verification=finalize(guard);
  assert.equal(verification.status,'failed');
  assert.deepEqual(verification.artifacts,[artifact]);
  const delivered=applyVerificationToCompletion(completion(),parseVerificationResponse(verification,RUN));
  assert.deepEqual(delivered.pixel_artifacts,{schemaVersion:1,artifacts:[artifact]});
  assert.equal(delivered.choices[0].message.content,verification.text);
});

test('a late failed observation from the previous run cannot replace an unrelated current reply',async()=>{
  const guard=startGuard(),toolName='pixel_ods_connections_status',toolCallId='delayed-status';
  const pending={toolName,toolCallId,params:{}};
  const oldScope={...context,toolName,toolCallId};
  assert.notEqual(guard.beforeToolCall(pending,oldScope)?.block,true);
  const next={...context,runId:NEXT};
  guard.observeRun(next,'pixel',{prompt:'Explain a stable sorting algorithm.'});
  const result=await unknownResult();
  guard.afterToolCall({...pending,result},oldScope);
  assert.equal(finalize(guard).status,'failed','the original run retains its own unresolved result');
  const text='A stable sort keeps equal keys in their original order.';
  assert.notEqual(finalize(guard,next,text).status,'failed');
  assert.equal(guard.replyPayloadSending({runId:NEXT,kind:'final',payload:{text}}),undefined);
});

const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
async function transport(t,verification) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-connection-evidence-delivery-'));
  fs.chmodSync(root,0o700);
  const ledger=createChatHistoryLedger(root),calls=[];
  let answer=FALSE_REPLY;
  const native={schemaVersion:1,status:'ready',sessionExists:true,sessionRevision:'e'.repeat(64),
    context:{used:100,window:65536,measuredAt:1},model:{id:'fixture',provider:'local',contextWindow:65536},
    compaction:{status:'idle',requestId:null,tokensBefore:null,tokensAfter:null,reason:null,count:0}};
  const gateway=http.createServer(async(req,res)=>{
    if(req.url==='/health'){res.setHeader('Content-Type','application/json');return res.end('{"ok":true}');}
    let raw='';for await(const part of req)raw+=part;
    const body=JSON.parse(raw||'{}');calls.push({path:req.url,body});
    res.setHeader('Content-Type','application/json');
    if(req.url==='/pixel-ods/context')return res.end(JSON.stringify(native));
    if(req.url==='/pixel-ods/history')return res.end(JSON.stringify({schemaVersion:1,hydrated:true}));
    if(req.url==='/pixel-ods/verification')return res.end(JSON.stringify(verification));
    if(req.url==='/pixel-ods/subagent-delivery')return res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:body.runId,status:'not-delegated'}));
    if(req.url==='/v1/chat/completions')return res.end(JSON.stringify(completion(answer)));
    res.statusCode=404;res.end('{}');
  });
  const ingress=createIngressServer({token:'fixture-only',gatewayPort:await listen(gateway),historyLedger:ledger});
  const port=await listen(ingress);
  t.after(async()=>{
    await Promise.all([new Promise(resolve=>ingress.close(resolve)),new Promise(resolve=>gateway.close(resolve))]);
    fs.rmSync(root,{recursive:true,force:true});
  });
  async function chat(requestId,messages,stream=false) {
    const response=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({user:USER,request_id:requestId,
        history_snapshot:{schemaVersion:1,messages},messages,stream})});
    const text=await response.text();
    return {status:response.status,...(stream?{text}:{value:JSON.parse(text)})};
  }
  return {ledger,calls,chat,setVerification:value=>{verification=value;},setAnswer:value=>{answer=value;}};
}

test('corrected unknown reply and verified receipts survive HTTP JSON, SSE history replay and the next-turn delivery record',async t=>{
  const guard=startGuard();
  receipt(guard,context,'pixel_ods_connections_status',await unknownResult());
  const verification={...finalize(guard),preview,artifacts:[artifact]};
  const fixture=await transport(t,verification);
  const messages=[{role:'user',content:'Check the connection and retain the independent report.'}];
  const first=await fixture.chat('unknown-first',messages);
  assert.equal(first.status,200,JSON.stringify(first));
  assert.equal(first.value.choices[0].message.content,verification.text);
  assert.deepEqual(first.value.pixel_artifacts,{schemaVersion:1,artifacts:[artifact]});
  const retained=fixture.ledger.read(computeSessionUser({user:USER})).lastResult;
  // Private native draft is preserved for audit, but every public replay uses
  // its paired host verification, rather than trusting that draft as truth.
  assert.equal(retained.completion.choices[0].message.content,FALSE_REPLY);
  assert.deepEqual(retained.verification,verification);
  const replay=await fixture.chat('unknown-first',messages,true);
  assert.equal(replay.status,200);
  const frames=replay.text.split('\n').filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
  assert.equal(frames.map(frame=>frame.choices[0].delta.content??'').join(''),verification.text);
  const terminal=frames.filter(frame=>frame.choices[0].finish_reason==='stop');
  assert.equal(terminal.length,1);
  assert.deepEqual(terminal[0].pixel.preview,preview);
  assert.deepEqual(terminal[0].pixel_artifacts,{schemaVersion:1,artifacts:[artifact]});
  assert.deepEqual(terminal[0].pixel_outcome,{schemaVersion:1,status:'failed'});
  assert.equal(fixture.calls.filter(call=>call.path==='/v1/chat/completions').length,1);
  fixture.setVerification({status:'none'});fixture.setAnswer('Independent next-turn answer.');
  const next=await fixture.chat('independent-next',[...messages,{role:'assistant',content:verification.text},{role:'user',content:'Now explain a stable sort.'}]);
  assert.equal(next.status,200);
  assert.equal(next.value.choices[0].message.content,'Independent next-turn answer.');
  const correction=fixture.calls.filter(call=>call.path==='/pixel-ods/history').at(-1)?.body.messages;
  assert.ok(correction?.some(message=>message.role==='assistant'&&message.content.includes(JSON.stringify({reply:verification.text}))));
  assert.ok(!correction.some(message=>message.content?.includes(FALSE_REPLY)));
});
