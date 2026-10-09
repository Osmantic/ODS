import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createSubagentDelivery} from '../plugin/subagent-delivery.mjs';
import {createToolLoopGuard,PHANTOM_PROCESS_REASON,FREE_CORRECTIONS_PER_KIND} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS,RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';
const user='ods-'+'a'.repeat(64),id='chatcmpl_11111111-2222-4333-8444-555555555555';
const owner={agentId:'pixel',runId:id,sessionId:'owner-session',sessionKey:'agent:pixel:openai-user:'+user,trigger:'user'};
const child='agent:pixel:subagent:22222222-2222-4333-8444-555555555555',childRun='33333333-2222-4333-8444-555555555555';
const continuation={...owner,runId:`announce:v1:${child}:${childRun}`,inputProvenance:{kind:'inter_session',sourceTool:'subagent_announce',sourceSessionKey:child}};
const request='Delegate two native read-only reviews, wait for both, and combine their findings.';
function fixture(){
 let now=1,access='live',ownerSession=owner.sessionId;const guard=createToolLoopGuard();guard.observeRun(owner,'pixel',{prompt:request});
 const registry=createSubagentDelivery({now:()=>now,accessIdentity:()=>access,resolveOwnerSession:()=>({sessionId:ownerSession})});registry.observe({prompt:request},owner);
 const source=fs.readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
 const start=source.indexOf('    api.on("before_tool_call", async (event, context) => {'),end=source.indexOf('    api.on("after_tool_call"',start);assert.ok(start>=0&&end>start);
 let before;
 vm.runInNewContext(source.slice(start,end),{api:{on:(_name,callback)=>{before=callback;},config:{}},AGENT_ID:'pixel',toolLoopGuard:guard,delegationDelivery:registry,
  accessRuntime:{isProbe:()=>false,beforeTool:()=>undefined},withPixelCronDeliveryDefault:decision=>decision,withPixelSubagentWorkspace:decision=>decision,withCronCommandPayloadBlock:decision=>decision,
  resolveUserPath:()=>{},resolveAgentWorkspaceDir:()=>{},
  goalProgress:{before:()=>undefined},bundleAdmission:{before:()=>{}},artifactAdmission:{before:()=>{}},projectRunControl:{before:()=>{}},taskActivity:{before:()=>{}}});
 function spawn({key=child,runId=childRun,native=true,accepted=true}={}){
  const context={...owner,toolName:'sessions_spawn',toolCallId:'spawn-'+key};registry.before({params:{runtime:'subagent',mode:'run'}},context);
  if(native)registry.nativeSpawn({runId,childSessionKey:key},{runId,childSessionKey:key,requesterSessionKey:owner.sessionKey});
  registry.after(accepted?{result:{details:{status:'accepted',runId,childSessionKey:key}}}:{error:'rejected'},context);
 }
 async function call({wrapped=false,context=owner,tool='process',args={action:'list'},callId='process-call'}={}){
  const toolName=wrapped?'tool_call':tool,params=wrapped?{id:`openclaw:core:${tool}`,args}:args;const ctx={...context,toolName,toolCallId:callId};
  return {decision:await before({toolName,params,toolCallId:callId},ctx),context:ctx,toolName,params,callId};
 }
 function persist(call){
  const result={content:[{type:'text',text:call.decision.blockReason}],details:{status:'blocked',reason:call.decision.blockReason}};
  guard.afterToolCall({toolName:call.toolName,params:call.params,toolCallId:call.callId,result,error:call.decision.blockReason},call.context);
  guard.toolResultPersist({toolName:call.toolName,toolCallId:call.callId,message:{role:'toolResult',toolName:call.toolName,toolCallId:call.callId,isError:true,...result}},call.context);
 }
 return {guard,registry,spawn,call,persist,expire:()=>{now=33*60*1000;},revoke:()=>{access='changed';},remap:()=>{ownerSession='replacement';}};
}
for(const wrapped of [false,true])test(`actual index hook guides a phantom process call to native yield (wrapped=${wrapped})`,async()=>{
 const f=fixture();f.spawn();const result=await f.call({wrapped});assert.equal(result.decision.block,true);assert.match(result.decision.blockReason,/sessions_yield/);assert.match(result.decision.blockReason,/completion events/);
 const repeat=await f.call({wrapped,callId:'second'});assert.equal(repeat.decision.blockReason,result.decision.blockReason,'fixed refusal preserves identical-outcome loop detection');
 assert.match(result.decision.blockReason,/tool_describe with \{"id":"openclaw:core:sessions_yield"\}/);
 assert.match(result.decision.blockReason,/tool_call with \{"id":"openclaw:core:sessions_yield","args":\{\}\}/);
 assert.match(result.decision.blockReason,/not process\(action="yield"\)/);
 assert.equal(f.registry.hasPendingChildren(owner),true,'the refusal did not execute native yield or deliver child events');
});
test('only accepted pending native child custody qualifies, with no prompt-string inference',async()=>{
 for(const kind of ['none','native-only','untrusted-accepted','rejected']){
  const f=fixture();if(kind==='native-only'){const ctx={...owner,toolName:'sessions_spawn',toolCallId:'spawn'};f.registry.before({params:{}},ctx);f.registry.nativeSpawn({runId:childRun,childSessionKey:child},{runId:childRun,childSessionKey:child,requesterSessionKey:owner.sessionKey});}
  if(kind==='untrusted-accepted')f.spawn({native:false});if(kind==='rejected')f.spawn({accepted:false});
  const result=await f.call();assert.doesNotMatch(result.decision?.blockReason??'',/sessions_yield/,kind);
 }
});
test('real exec sessions keep their native process path even while native children are pending',async()=>{
 const f=fixture();f.spawn();const ctx={...owner,toolName:'exec',toolCallId:'exec'};const params={command:'sleep 1',background:true};
 f.guard.beforeToolCall({toolName:'exec',params},ctx);f.guard.afterToolCall({toolName:'exec',params,toolCallId:'exec',result:{content:[{type:'text',text:'Still running.'}],details:{status:'running',sessionId:'owned-process',pid:123}}},ctx);
 const result=await f.call({args:{action:'poll',sessionId:'owned-process'}});assert.notEqual(result.decision?.block,true);
});
test('all received children remove yield advice; one still pending retains it for current continuation',async()=>{
 const f=fixture();f.spawn();f.spawn({key:child.replace('22222222','77777777'),runId:childRun.replace('33333333','88888888')});
 f.registry.observe({prompt:'First native result.'},continuation);f.guard.observeRun(continuation,'pixel',{prompt:'First native result.'});
 assert.match((await f.call({context:continuation})).decision.blockReason,/sessions_yield/);
 const next={...continuation,runId:`announce:v1:${child.replace('22222222','77777777')}:${childRun.replace('33333333','88888888')}`,inputProvenance:{...continuation.inputProvenance,sourceSessionKey:child.replace('22222222','77777777')}};
 f.registry.observe({prompt:'Second native result.'},next);f.guard.observeRun(next,'pixel',{prompt:'Second native result.'});assert.equal((await f.call({context:next})).decision.blockReason,PHANTOM_PROCESS_REASON);
});
test('Stop, access drift, expiry, sparse/foreign identity and superseded runs never acquire yield advice',async()=>{
 for(const kind of ['stop','revoke','expire','foreign','superseded','sparse-id','sparse-key','remapped-owner']){
  const f=fixture();f.spawn();let context=owner;
  if(kind==='stop')await f.registry.cancel(user);if(kind==='revoke')f.revoke();if(kind==='expire')f.expire();
  if(kind==='foreign')context={...owner,sessionId:'foreign'};
  if(kind==='sparse-id')context={...owner,sessionId:undefined};if(kind==='sparse-key')context={...owner,sessionKey:undefined};
  if(kind==='remapped-owner')f.remap();
  if(kind==='superseded'){const newer={...owner,runId:id.replace('11111111','aaaaaaaa')};f.registry.observe({prompt:'New owner turn'},newer);}
  assert.doesNotMatch((await f.call({context})).decision?.blockReason??'',/sessions_yield/,kind);
 }
});
test('the same refusal allowance and failure fuse still stop repeated native-wait process calls',async()=>{
 const f=fixture();f.spawn();const limit=FREE_CORRECTIONS_PER_KIND+RUN_PROGRESS_LIMITS.consecutiveFailures;
 for(let i=0;i<limit;i++){f.guard.observeModelCall({},owner);const call=await f.call({callId:'poll-'+i});assert.match(call.decision.blockReason,/sessions_yield/);f.persist(call);}
 const exhausted=await f.call({tool:'read',args:{path:'README.md'},callId:'probe'});assert.equal(exhausted.decision.blockReason,PROGRESS_FINALIZATION_INSTRUCTION);
 f.guard.observeModelCall({},owner);
 assert.equal((await f.call({callId:'after-stop'})).decision.blockReason,RUN_PROGRESS_STOP_REASON);
});
