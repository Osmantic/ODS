import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {register} from 'node:module';
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
function receipt(guard,scope,name,result,{wrapped=false,params={},sourceName='pixel-ods'}={}) {
  const toolCallId=`connection-evidence-${++sequence}`;
  const toolName=wrapped?'tool_call':name;
  const id=`openclaw:${sourceName}:${name}`;
  const args=wrapped?{id,args:params}:params;
  const bound={...scope,toolName,toolCallId};
  const event={toolName,toolCallId,params:args};
  const decision=guard.beforeToolCall(event,bound);
  assert.notEqual(decision?.block,true,decision?.blockReason);
  const tool={id,name,source:'openclaw',sourceName};
  const observed=wrapped?{content:[{type:'text',text:JSON.stringify({tool,result})}],
    details:{tool,result},...(result.isError?{isError:true}:{})}:result;
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

for(const wrapped of [false,true]) test(`a known Root not-connected result resolves the failed observation without affecting the next turn (${wrapped?'Tool Search':'direct'})`,async()=>{
  const guard=startGuard();
  receipt(guard,context,'pixel_ods_connections_status',await unknownResult(),{wrapped});
  const result=await createConnectionsStatusTool({read:async()=>knownAbsent()}).execute('known',{});
  assert.notEqual(result.isError,true);
  receipt(guard,context,'pixel_ods_connections_status',result,{wrapped});
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
  let answer=FALSE_REPLY,runId=RUN;
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
    if(req.url==='/v1/chat/completions')return res.end(JSON.stringify(completion(answer,runId)));
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
  return {ledger,calls,chat,setVerification:value=>{verification=value;},setAnswer:value=>{answer=value;},setRunId:value=>{runId=value;}};
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

test('canonical wrapped real Source retries preserve normal JSON, SSE and retained history after a failed lookup',
  {skip:!process.env.PIXEL_TEST_TYPEBOX_DIR},async t=>{
  // Use the same locked validator and registered Source factory as production;
  // only the local, invented projection files substitute its input boundary.
  register('../../../../vendor/pixel/tests/fixtures/source-plugin/loader.mjs',import.meta.url);
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'ods-source-delivery-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const at=Date.now(),date=days=>new Date(at+days*86400000).toISOString();
  const boundary={rawContentStored:false,instructionsAuthorized:false,projectionOnly:true};
  const email={schemaVersion:1,source:'gmail',enabled:true,sanitizerRevision:3,generatedAt:date(0),boundary,
    records:[],coverage:{bounded:true,sync:{state:'in-progress',retainedRecords:0},folders:{}}};
  const calendar={schemaVersion:1,source:'google-calendar',enabled:true,generatedAt:date(0),boundary,records:[],
    coverage:{bounded:true,windowStart:date(-1),windowEnd:date(10),completeWithinWindow:true,truncated:false,
      calendarTimeZone:'UTC',calendarTimeZoneState:'consistent',sync:{state:'complete',retainedRecords:0,failure:null}}};
  const status={kind:'pixel-source-broker-status',sources:{email:{state:'ok'},calendar:{state:'ok'}}};
  for(const [name,value] of Object.entries({email,calendar,status}))
    fs.writeFileSync(path.join(directory,`${name}.json`),JSON.stringify(value));
  const env={PIXEL_SOURCE_PROJECTION_DIR:directory,PIXEL_AGENT_ID:'pixel',PIXEL_LIMB_EMAIL_ENABLED:'1',
    PIXEL_LIMB_CALENDAR_ENABLED:'1',PIXEL_LIMB_CALENDAR_WRITE_ENABLED:'0',PIXEL_LIMB_WEB_ENABLED:'0',
    PIXEL_LIMB_SOCIAL_ENABLED:'0',PIXEL_LIMB_EMAIL_DRAFTS_ENABLED:'0',PIXEL_SOURCE_STALE_AFTER_MS:'180000'};
  const saved=Object.fromEntries(Object.keys(env).map(key=>[key,process.env[key]])),tools=new Map();
  try {
    Object.assign(process.env,env);
    const {default:entry}=await import('../../../../vendor/pixel/plugin/index.js?connection-evidence-delivery');
    entry.register({config:{agents:{defaults:{userTimezone:'UTC'}}},registerTool(factory,{names}){
      const tool=factory({agentId:'pixel'});if(tool)tools.set(names[0],tool);
    }});
  } finally {
    for(const [key,value] of Object.entries(saved))if(value===undefined)delete process.env[key];else process.env[key]=value;
  }
  const frames=value=>value.text.split('\n').filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
  const text=value=>frames(value).map(frame=>frame.choices[0].delta.content??'').join('');
  for(const [name,args] of [['pixel_gmail_count',{}],['pixel_calendar_count',{window:{kind:'rolling',days:7}}]]) {
    const sourceName='pixel-source-broker',tool=tools.get(name);
    assert.ok(tool,name);
    const failed=await tool.execute('invalid-source-arguments',{max:1});
    assert.equal(failed.isError,true,'the actual strict factory refuses an undeclared count argument');
    const before=startGuard();
    receipt(before,context,name,failed,{wrapped:true,params:{max:1},sourceName});
    const failure=finalize(before);
    assert.equal(failure.status,'failed');
    const f=await transport(t,failure),messages=[{role:'user',content:`Run the ${name} lookup.`}];
    const first=await f.chat('failed-source',messages);
    assert.equal(first.status,200,JSON.stringify(first));
    assert.equal(first.value.choices[0].message.content,failure.text);
    assert.notEqual(first.value.choices[0].message.content,FALSE_REPLY);
    const failedReplay=await f.chat('failed-source',messages,true);
    assert.equal(failedReplay.status,200);
    assert.equal(text(failedReplay),failure.text);
    assert.deepEqual(frames(failedReplay).at(-1).pixel_outcome,{schemaVersion:1,status:'failed'});

    const retry=createToolLoopGuard(),scope={...context,runId:NEXT};
    retry.observeRun(scope,'pixel',{prompt:'Retry that same Source lookup with its declared schema.'});
    receipt(retry,scope,name,failed,{wrapped:true,params:{max:1},sourceName});
    assert.equal(finalize(retry,scope).status,'failed');
    const successful=await tool.execute('valid-source-arguments',args);
    assert.notEqual(successful.isError,true,JSON.stringify(successful));
    assert.equal(successful.details.readState,'ok');
    assert.equal(successful.details.recordsReturned,0);
    const repaired=receipt(retry,scope,name,successful,{wrapped:true,params:args,sourceName});
    assert.equal(repaired.observed.details.tool.id,`openclaw:${sourceName}:${name}`);
    const normal='The requested local lookup succeeded. Its count and coverage retain the Source receipt limits.';
    const verification=finalize(retry,scope,normal);
    assert.equal(verification.status,'none','a valid retry for the same source settles its own failure');
    assert.equal(retry.replyPayloadSending({runId:NEXT,kind:'final',payload:{text:normal}}),undefined);
    f.setRunId(NEXT);f.setVerification(verification);f.setAnswer(normal);
    const history=[...messages,{role:'assistant',content:failure.text},{role:'user',content:'Retry that lookup.'}];
    const delivered=await f.chat('repaired-source',history);
    assert.equal(delivered.status,200,JSON.stringify(delivered));
    assert.equal(delivered.value.choices[0].message.content,normal);
    const retained=f.ledger.read(computeSessionUser({user:USER})).lastResult;
    assert.equal(retained.completion.choices[0].message.content,normal);
    assert.equal(retained.verification.status,'none');
    const replay=await f.chat('repaired-source',history,true);
    assert.equal(replay.status,200);
    assert.equal(text(replay),normal);
    assert.deepEqual(frames(replay).at(-1).pixel_outcome,{schemaVersion:1,status:'none'});
    assert.equal(f.calls.filter(call=>call.path==='/v1/chat/completions').length,2,'history replays neither failed nor repaired tools');
  }
});
