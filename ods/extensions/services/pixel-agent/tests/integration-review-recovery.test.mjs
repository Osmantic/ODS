// Mechanical tests for native AUTO. They do not simulate semantic success.
// Previous semantic source/tests and all eight failed real packets are archived.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createIntegrationReviewRecovery} from '../plugin/integration-review-recovery.mjs';
import {defaultAgentId} from '../plugin/stop-synthesis.mjs';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createGoalProgress} from '../plugin/goal-progress.mjs';
import {createIntegrationTools,integrationRequest,NATIVE_REVIEW_ROLE,nativeReviewOperation} from '../plugin/integrations.mjs';
import {integrationCapabilitiesContext} from '../plugin/integration-capabilities.mjs';

const SESSION='agent:pixel:openai-user:ods-'+'a'.repeat(64);
const CONTEXT={agentId:'pixel',runId:'current-run',sessionId:'owned-session',sessionKey:SESSION,
  trigger:'user',modelProviderId:'ods-gateway',modelId:'ods/current'};
const OWNER='Prepare approval to append Current owned line to Example.md; do not write it.';
const ANSWER='A provisional answer with no native attempt.';
const event=(owner=OWNER)=>({runId:CONTEXT.runId,sessionId:CONTEXT.sessionId,sessionKey:SESSION,
  nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
  messages:[{role:'user',content:owner},{role:'assistant',content:ANSWER}],lastAssistantMessage:ANSWER});
const tool=(name,params={})=>({toolName:name,params});
const appendParams=()=>({action:'write',mode:'append',path:'Example.md',text:'Current owned line'});
const TOOL_CONTEXT={...CONTEXT,toolCallId:'native-current'};
const EMPTY_REFERENCES=Object.freeze({lookups:Object.freeze([]),truncated:false});
const EMPTY_PROGRESS=Object.freeze({reads:Object.freeze([]),truncated:false});
const reply=()=>{throw Error('No parallel model invocation is available');};
function world(options={}){
  const {owner=OWNER,...rest}=options;const calls=[],notices=[];
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,nativeStartClear:()=>true,
    complete:()=>{calls.push('forbidden');throw Error('Secondary completion reached');},record:value=>notices.push(value),...rest});
  recovery.observe({prompt:owner},CONTEXT,CONTEXT.runId);return {recovery,calls,notices};
}
const nativeScope=(context=CONTEXT,changes={})=>Object.freeze({version:1,ownerRunId:context.runId,active:true,
  toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_mcp']),...changes});
const admission=(changes={})=>({...event(),nativeRetryToolsAllowState:nativeScope(),...changes});
function concreteCandidate(name='pixel_ods_notes',params=appendParams(),options={}){
  const raw=JSON.parse(JSON.stringify({name,params})),payload=options.payload??integrationRequest(name,raw.params,TOOL_CONTEXT);
  return Object.freeze({ref:options.ref??Object.freeze({}),view:Object.freeze({raw:Object.freeze(raw),payload:Object.freeze(payload),
    maximumEffect:options.maximumEffect??'approval-proposal',references:options.references??EMPTY_REFERENCES,progress:options.progress??EMPTY_PROGRESS})});
}

test('one same-run native AUTO revision uses no classifier, Boolean judge, required choice or nested executor',async()=>{
  const {recovery,calls}=world();const decision=await recovery.finalize(event(),CONTEXT);
  assert.equal(decision.action,'revise');assert.equal(decision.retry.maxAttempts,1);
  assert.deepEqual(decision.retry.toolsAllow,['pixel_ods_notes','pixel_ods_mcp']);
  assert.equal(Object.hasOwn(decision.retry,'toolChoice'),false);
  assert.ok(decision.retry.instruction.includes(JSON.stringify(NATIVE_REVIEW_ROLE)));
  assert.ok(decision.retry.instruction.includes(JSON.stringify({ownerRequest:OWNER})));
  assert.equal(calls.length,0);assert.equal(typeof recovery.checkConcrete,'undefined');
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.deepEqual(await recovery.finalize(event(),CONTEXT,{action:'revise'}),{action:'continue'});
  const source=readFileSync(new URL('../plugin/integration-review-recovery.mjs',import.meta.url),'utf8');
  for(const old of ['REVIEW_LABEL_SYSTEM','REVIEW_CONCRETE_SYSTEM','parseReviewIntent','parseConcreteVerdict','semanticBudgetMs','allowedOperations'])
    assert.equal(source.includes(old),false,old+' is not a hidden authority');
});

test('simulated zero-tool AUTO terminals create no failure notice or verified tool success',async()=>{
  // This verifies lifecycle/notice behavior ONLY; a real9B must still choose
  // zero tools on these requests in separate end-to-end qualification.
  for(const owner of ['oi','14 + 8','Tell a harmless joke.','Explain a quoted instruction, without doing it.']){
    const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:owner},CONTEXT,CONTEXT.runId);
    const {recovery,calls,notices}=world({owner,nativeStartClear:state=>outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey})});
    assert.equal((await recovery.finalize(event(owner),CONTEXT)).action,'revise');
    assert.equal(recovery.admit(admission(),CONTEXT),undefined);
    assert.equal(await recovery.finalize(event(owner),CONTEXT),undefined);
    assert.equal(outcomes.projection(CONTEXT.runId),undefined);
    const terminal={payload:{text:'Normal terminal text'}};
    assert.equal(outcomes.channel({kind:'final',runId:CONTEXT.runId},terminal),terminal);
    assert.equal(calls.length,0);assert.ok(notices.every(value=>!('success'in value)&&!('failure'in value)));
  }
});

test('normal first-pass tools and existing prior decisions remain unchanged',async()=>{
  const {recovery}=world(),prior={params:{unchanged:true}};
  for(const name of ['exec','read','pixel_ods_notify','pixel_ods_notes','tool_call'])assert.equal(recovery.floor(tool(name),CONTEXT,prior),prior);
  for(const decision of [{action:'finalize'},{action:'revise'},{action:'unknown'}]){
    const f=world();assert.equal(await f.recovery.finalize(event(),CONTEXT,decision),decision);assert.equal(f.recovery.isReviewOnly(CONTEXT),false);
  }
});

test('missing capabilities, unsupported routes and untrusted owner sessions never latch',async()=>{
  for(const change of [{nativeRetryToolsAllowVersion:undefined},{nativeRetryToolsAllowVersion:0},{nativeRetryToolsAllowVersion:'1'},
    {nativeRetryToolChoiceVersion:undefined},{nativeRetryToolChoiceVersion:0},{stopHookActive:true},{lastAssistantMessage:''}]){
    const {recovery,calls}=world();assert.equal(await recovery.finalize({...event(),...change},CONTEXT),undefined);
    assert.equal(recovery.isReviewOnly(CONTEXT),false);assert.equal(calls.length,0);
  }
  for(const changed of [{trigger:'cron'},{trigger:'heartbeat'},{agentId:'other'},{sessionKey:'unowned'},
    {modelProviderId:'foreign'},{modelId:'foreign'}]){
    const ctx={...CONTEXT,...changed};const f=world();f.recovery.observe({prompt:OWNER},ctx,ctx.runId);
    assert.equal(await f.recovery.finalize({...event(),runId:ctx.runId,sessionKey:ctx.sessionKey},ctx),undefined);
  }
  const f=world();assert.equal(await f.recovery.finalize({...event(),sessionId:'foreign'},CONTEXT),undefined);
  assert.equal(await f.recovery.finalize(event('Different current owner'),CONTEXT),undefined);
});

test('native-start proof is exact, synchronous and checked only before latch',async()=>{
  for(const value of [undefined,()=>false,()=>1,()=> 'true',()=>Promise.resolve(true),()=>{throw Error('missing');}]){
    const {recovery,calls}=world({nativeStartClear:value});assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(calls.length,0);
  }
  let queries=0;const f=world({nativeStartClear:()=>++queries===1});assert.equal(await f.recovery.finalize(event(),CONTEXT),undefined);
  let clear=true,count=0;const g=world({nativeStartClear:()=>{count++;return clear;}});
  await g.recovery.finalize(event(),CONTEXT);const atLatch=count;clear=false;g.recovery.beforeTool(tool('pixel_ods_notes'),CONTEXT);
  assert.equal(g.recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT),undefined);assert.equal(g.recovery.admit(admission(),CONTEXT),undefined);
  assert.equal(await g.recovery.finalize(event(),CONTEXT),undefined);assert.equal(count,atLatch);
});

test('every native attempt and tool transcript prevents the initial automatic pass',async()=>{
  for(const name of ['pixel_ods_notes','pixel_ods_mcp','exec','unknown']){
    const {recovery}=world();recovery.beforeTool(tool(name),CONTEXT);assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  }
  for(const extra of [{role:'tool',content:'result'},{role:'assistant',content:[{type:'toolCall',name:'x'}]},
    {role:'assistant',content:'text',tool_calls:[{id:'call'}]},{role:'assistant',content:'text',function_call:{name:'x'}}]){
    const {recovery}=world();const ev=event();ev.messages.push(extra);assert.equal(await recovery.finalize(ev,CONTEXT),undefined);
  }
});

test('native AUTO admission requires an exact frozen active cap and refuses required-first state',async()=>{
  const changes=[{}, {nativeRetryToolsAllowState:undefined},{nativeRetryToolsAllowState:{...nativeScope()}},
    {nativeRetryToolsAllowState:nativeScope(CONTEXT,{active:false})},{nativeRetryToolsAllowState:nativeScope(CONTEXT,{ownerRunId:'other'})},
    {nativeRetryToolsAllowState:nativeScope(CONTEXT,{toolsAllow:['pixel_ods_notes','pixel_ods_mcp']})},
    {nativeRetryToolsAllowState:nativeScope(CONTEXT,{toolsAllow:Object.freeze(['pixel_ods_notes','exec'])})},
    {nativeRetryToolChoiceState:Object.freeze({version:1,ownerRunId:CONTEXT.runId,mode:'required-first',phase:'armed'})},
    {nativeRetryToolChoiceState:null}];
  for(const [at,change]of changes.entries()){
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);const result=recovery.admit(admission(change),CONTEXT);
    if(at===0)assert.equal(result,undefined);else{assert.equal(result.outcome,'block');assert.equal(recovery.admit(admission(),CONTEXT).outcome,'block');}
  }
});

test('native AUTO gate binds every actual inference route and refuses missing or fallback model identity',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  assert.equal(recovery.admit(admission(),CONTEXT),undefined,'the actual captured ods route remains admitted');
  for(const changes of [{modelProviderId:undefined},{modelId:undefined},{modelProviderId:null},{modelId:null},
    {modelProviderId:'fallback-provider'},{modelId:'fallback-model'},{modelProviderId:'ODS-gateway'}]){
    const f=world();await f.recovery.finalize(event(),CONTEXT);
    assert.equal(f.recovery.admit(admission(),{...CONTEXT,...changes})?.outcome,'block');
    assert.equal(f.recovery.admit(admission(),CONTEXT).outcome,'block','a later original route cannot reopen this refused owner run');
    assert.equal(f.recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
  }
  const normal=world();
  assert.equal(normal.recovery.admit(event(),{...CONTEXT,modelProviderId:undefined,modelId:undefined}),undefined,
    'ordinary unlatched inference keeps its native admission behavior');
});

test('revoked readiness, conflicting IDs, provider changes and supersession cannot restore a cap',async()=>{
  for(const change of ['ready','eligible','provider','new-owner','conflict']){
    let current=true;const f=world({ready:()=>current,eligible:()=>current});await f.recovery.finalize(event(),CONTEXT);
    if(change==='ready'||change==='eligible')current=false;
    if(change==='provider')f.recovery.observe({prompt:OWNER},{...CONTEXT,modelProviderId:'foreign'},CONTEXT.runId);
    if(change==='new-owner'){const next={...CONTEXT,runId:'new'};f.recovery.observe({prompt:'new'},next,next.runId);}
    if(change==='conflict')f.recovery.observe({prompt:OWNER},{...CONTEXT,sessionId:'foreign'},CONTEXT.runId);
    assert.equal(f.recovery.admit(admission(),CONTEXT).outcome,'block',change);
    assert.equal(f.recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
  }
});

test('role contract, strict candidate and private permit preserve exact raw/canonical/effect facts',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const candidate=concreteCandidate(),permit=recovery.admitNativeCandidate(candidate,TOOL_CONTEXT);
  assert.deepEqual(permit,{candidate:candidate.ref,admission:'native-review-policy-v1'});assert.ok(Object.isFrozen(permit));
  assert.equal(Object.hasOwn(permit,'entailed'),false);
  assert.ok(Object.isFrozen(NATIVE_REVIEW_ROLE)&&Object.isFrozen(NATIVE_REVIEW_ROLE.operations));
  assert.equal(NATIVE_REVIEW_ROLE.scope,'native-review-policy-not-owner-intent');
  const proposals=NATIVE_REVIEW_ROLE.operations.filter(value=>value.maximumEffect==='approval-proposal');
  assert.equal(proposals.length,4);assert.ok(proposals.every(value=>value.requiresCurrentAsk===true));
  const metadata=NATIVE_REVIEW_ROLE.operations.filter(value=>value.maximumEffect==='catalog-metadata');assert.equal(metadata.length,1);
  assert.equal(nativeReviewOperation(integrationRequest('pixel_ods_notes',appendParams(),CONTEXT)).maximumEffect,'approval-proposal');
});

test('native floor admits only the bounded builtin role, independent of hidden owner classifiers',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  for(const ev of [tool('pixel_ods_notes',{action:'search',query:'invented'}),tool('pixel_ods_notes',{action:'read',path:'Example.md'}),
    tool('pixel_ods_notes',appendParams()),tool('pixel_ods_mcp',{action:'list'}),
    tool('pixel_ods_mcp',{action:'call',server:'app:news',tool:'unread',arguments:{}})])assert.equal(recovery.floor(ev,CONTEXT),undefined);
  for(const ev of [tool('exec'),tool('tool_search_code'),tool('tool_search'),tool('pixel_ods_notify',{message:'x'}),
    tool('pixel_ods_mcp',{action:'call',server:'custom',tool:'read',arguments:{}}),tool('pixel_ods_notes',{...appendParams(),mode:'delete'})])
    assert.equal(recovery.floor(ev,CONTEXT).block,true);
  const deferred=tool('tool_call',{id:'openclaw:pixel-ods:pixel_ods_notes',args:appendParams()});
  assert.equal(recovery.floor(deferred,CONTEXT),undefined);
  assert.equal(recovery.floor(tool('tool_call',{id:'openclaw:other:pixel_ods_notes',args:appendParams()}),CONTEXT).block,true);
});

test('unknown constraints, changed payloads, wrong effects and untrusted body/binding facts refuse deterministically',async()=>{
  const candidates=[
    concreteCandidate('pixel_ods_notes',{...appendParams(),hidden:'constraint'}),
    concreteCandidate('pixel_ods_notes',appendParams(),{payload:{...integrationRequest('pixel_ods_notes',appendParams(),CONTEXT),path:'Changed.md'}}),
    concreteCandidate('pixel_ods_notes',appendParams(),{maximumEffect:'private-read'}),
    concreteCandidate('pixel_ods_mcp',{action:'call',server:'app:documents',tool:'search',arguments:{query:'x',date:'unsupported'}},{maximumEffect:'private-read'}),
    concreteCandidate('pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:9007199254740992}},{maximumEffect:'private-read'}),
    concreteCandidate('pixel_ods_notes',appendParams(),{references:{lookups:[{source:'obsidian',query:null,entries:[{path:'Example.md',binding:'not-public'}],truncated:false}],truncated:false}}),
    concreteCandidate('pixel_ods_notes',appendParams(),{progress:{reads:[{sequence:1,status:'read-complete',raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md'}},body:'not-a-fact'}],truncated:false}}),
  ];
  for(const candidate of candidates){
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    assert.equal(recovery.admitNativeCandidate(candidate,TOOL_CONTEXT).admission,'refused');
    assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
    assert.equal(recovery.admit(admission(),CONTEXT),undefined,'IO-terminal still permits native summary');
  }
});

test('typed read-progress stays factual, exact and bounded without claiming that a content condition is true',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const progress={reads:[{sequence:1,status:'read-complete',raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md'}}}],truncated:true};
  const candidate=concreteCandidate('pixel_ods_notes',appendParams(),{progress});
  assert.equal(recovery.admitNativeCandidate(candidate,TOOL_CONTEXT).admission,'native-review-policy-v1');
  assert.deepEqual(candidate.view.progress,progress,'gate does not manufacture or edit progress');
  assert.match(JSON.stringify(NATIVE_REVIEW_ROLE),/read/i);
});

test('all retained progress shape, sequence, count and byte fences remain deterministic',async()=>{
  const fact={sequence:1,raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md'}},status:'read-complete'};
  const invalid=[undefined,{reads:[],truncated:'false'},{reads:[],truncated:false,body:'extra'},
    ...['settled','unknown'].map(status=>({reads:[{...fact,status}],truncated:false})),
    ...[0,257].map(sequence=>({reads:[{...fact,sequence}],truncated:false})),
    {reads:[fact,fact],truncated:false},{reads:Array.from({length:17},(_,i)=>({...fact,sequence:i+1})),truncated:false},
    ...['body','binding','assistantProse','conditions','source'].map(key=>({reads:[{...fact,[key]:'not-a-fact'}],truncated:false})),
    {reads:[{...fact,raw:{name:'pixel_ods_notes',params:{action:'search',query:'x'}}}],truncated:false},
    {reads:[{...fact,raw:{name:'pixel_ods_notes',params:appendParams()}}],truncated:false},
    {reads:[{...fact,raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md',text:'body'}}}],truncated:false},
    {reads:Array.from({length:16},(_,i)=>({...fact,sequence:i+1,raw:{name:'pixel_ods_notes',params:{action:'read',path:'x'.repeat(500)}}})),truncated:true},
  ];
  for(const progress of invalid){
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    const candidate=concreteCandidate(),altered=Object.freeze({...candidate,view:Object.freeze({...candidate.view,progress})});
    assert.equal(recovery.admitNativeCandidate(altered,TOOL_CONTEXT).admission,'refused');
  }
});

test('reference shape/count/byte/Unicode/identity fences remain data validation, not semantic approval',async()=>{
  const invalid=[undefined,{lookups:[],truncated:0},{lookups:[],truncated:false,binding:'private'},
    {lookups:[{source:'foreign',query:null,entries:[],truncated:false}],truncated:false},
    {lookups:[{source:'obsidian',query:null,entries:[{path:'x',binding:'secret'}],truncated:false}],truncated:false},
    {lookups:[{source:'paperless',query:null,entries:[{id:'9223372036854775808',title:'x'}],truncated:false}],truncated:false},
    {lookups:[{source:'obsidian',query:null,entries:Array.from({length:65},(_,i)=>({path:'N'+i+'.md'})),truncated:false}],truncated:false},
    {lookups:[{source:'obsidian',query:null,entries:Array.from({length:64},()=>({path:'😀'.repeat(500)})),truncated:false}],truncated:false},
  ];
  for(const references of invalid){
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    const candidate=concreteCandidate(),altered=Object.freeze({...candidate,view:Object.freeze({...candidate.view,references})});
    assert.equal(recovery.admitNativeCandidate(altered,TOOL_CONTEXT).admission,'refused');
  }
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const candidate=concreteCandidate('pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:'9223372036854775807'}},
    {maximumEffect:'private-read',references:{lookups:[{source:'paperless',query:null,entries:[{id:'9223372036854775807',title:'😀'.repeat(200)}],truncated:true}],truncated:true},
      progress:{reads:[{sequence:256,status:'read-complete',raw:{name:'pixel_ods_mcp',params:{action:'call',server:'app:documents',tool:'read',arguments:{id:'9223372036854775807'}}}}],truncated:true}});
  assert.equal(recovery.admitNativeCandidate(candidate,TOOL_CONTEXT).admission,'native-review-policy-v1');
});

test('callbacks cannot revive revoked native custody and unobserved owner IDs never inherit an old latch',async()=>{
  let instance;
  const f=world({ready:()=>{instance?.cancelSession(SESSION);return true;}});instance=f.recovery;
  assert.equal(await f.recovery.finalize(event(),CONTEXT),undefined);assert.equal(f.recovery.isReviewOnly(CONTEXT),false);
  const g=world();await g.recovery.finalize(event(),CONTEXT);
  const next={...CONTEXT,runId:'unobserved-native-owner'};
  assert.equal(g.recovery.admit({runId:next.runId},next),undefined,'native fresh owner is normal before prompt observation');
  assert.equal(g.recovery.floor(tool('exec'),next).block,true,'an unobserved tool callback cannot borrow ordinary authority');
  g.recovery.observe({prompt:'new'},next,next.runId);assert.equal(g.recovery.floor(tool('exec'),next),undefined);
});

test('same current native candidate receives a real Outcomes permit before one-use factory dispatch',async()=>{
  const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
  const {recovery}=world({nativeStartClear:state=>outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey})});
  await recovery.finalize(event(),CONTEXT);
  const ev={...tool('pixel_ods_notes',appendParams()),toolCallId:TOOL_CONTEXT.toolCallId};recovery.beforeTool(ev,TOOL_CONTEXT);
  const candidate=await outcomes.prepareReviewCandidate(ev,TOOL_CONTEXT);
  const permit=outcomes.checkNativeCandidate(candidate,value=>recovery.admitNativeCandidate(value,TOOL_CONTEXT));
  assert.equal(outcomes.commitNativeCandidate(candidate,permit),undefined);
  const sent=[],adapter=createIntegrationTools({context:CONTEXT,outcomes,requireBound:ctx=>recovery.isReviewOnly(ctx),submit:async payload=>{
    sent.push(payload);return {ok:true,status:'approval-required',approvalId:'qa-pending',summary:'Ask-only QA request',recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false};
  }}).find(value=>value.name==='pixel_ods_notes');
  assert.notEqual((await adapter.execute(TOOL_CONTEXT.toolCallId,appendParams())).isError,true);assert.equal(sent.length,1);
  assert.equal((await adapter.execute(TOOL_CONTEXT.toolCallId,appendParams())).isError,true);assert.equal(sent.length,1);
});

test('cancel/end/stop and event-only native identity retain the floor; new trusted owner remains ordinary',async()=>{
  for(const action of ['cancel','end','stop']){
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    if(action==='cancel')recovery.cancelSession(SESSION);else if(action==='end')recovery.end({runId:CONTEXT.runId},{});else await recovery.stop();
    assert.equal(recovery.floor({...tool('pixel_ods_notes',appendParams()),runId:CONTEXT.runId},{}).block,true);
    assert.equal(recovery.admitNativeCandidate(concreteCandidate(),TOOL_CONTEXT).admission,'refused');
  }
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const next={...CONTEXT,runId:'new-owner'};recovery.observe({prompt:'new'},next,next.runId);
  assert.equal(recovery.floor(tool('exec'),next),undefined);assert.equal(recovery.admit({runId:next.runId},next),undefined);
  assert.equal(recovery.floor(tool('exec'),CONTEXT).block,true);
});

test('capacity never evicts a latch into unrestricted execution',async()=>{
  const {recovery}=world({maxRuns:1});await recovery.finalize(event(),CONTEXT);recovery.end({},CONTEXT);
  const next={...CONTEXT,runId:'next'};recovery.observe({prompt:'new'},next,next.runId);
  assert.equal(recovery.floor(tool('exec'),next),undefined);assert.equal(recovery.floor(tool('exec'),CONTEXT).block,true);
  assert.equal(recovery.isReviewOnly(next),false);assert.equal(recovery.isReviewOnly(CONTEXT),true);
});

for(const scenario of [
  {name:'ordinary greeting',owner:'Hello.',answer:'Hello.'},
  {name:'integration request',owner:OWNER,answer:'Approval is needed.'},
  {name:'existing completion assurance',owner:'notícias de hoje',
    answer:'Vou pesquisar agora.',retry:'ods-completion-assurance'},
]) test(`actual index finalize preserves ${scenario.name} without invoking integration recovery`,async()=>{
  const outcomes=createIntegrationOutcomes();
  outcomes.begin({prompt:scenario.owner},CONTEXT,CONTEXT.runId);
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,
    nativeStartClear:state=>outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,
      sessionId:state.sessionId,sessionKey:state.sessionKey})});
  recovery.observe({prompt:scenario.owner},CONTEXT,CONTEXT.runId);
  const guard=createToolLoopGuard(),goals=createGoalProgress();
  guard.observeRun(CONTEXT,'pixel',{prompt:scenario.owner});
  goals.begin({prompt:scenario.owner},CONTEXT);
  const source=readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start=source.indexOf('    api.on("before_agent_finalize",');
  const end=source.indexOf('    // Delivery rewriting',start);
  assert.ok(start>=0&&end>start);
  let finalize,ordinaryDecision,deliveredDecision,integrationCalls=0;
  vm.runInNewContext(source.slice(start,end),{
    api:{on:(_name,callback)=>{finalize=callback;}},AGENT_ID:'pixel',
    integrationReviewRecovery:{...recovery,finalize(){integrationCalls++;throw Error('Integration recovery invoked');}},
    toolLoopGuard:{...guard,beforeAgentFinalize(...args){ordinaryDecision=guard.beforeAgentFinalize(...args);return ordinaryDecision;}},
    goalProgress:goals,delegationDelivery:{finalize(_event,_context,decision){deliveredDecision=decision;}},
  });
  const nativeEvent={...event(scenario.owner),lastAssistantMessage:scenario.answer,
    messages:[{role:'user',content:scenario.owner},{role:'assistant',content:scenario.answer}]};
  const decision=await finalize(nativeEvent,CONTEXT);
  assert.equal(integrationCalls,0);
  assert.equal(decision,ordinaryDecision,'the existing native decision reaches delivery unchanged');
  assert.equal(deliveredDecision,ordinaryDecision);
  assert.equal(decision?.retry?.idempotencyKey,scenario.retry);
  assert.equal(recovery.isReviewOnly(CONTEXT),false,'finalization cannot create an integration review latch');
  assert.equal(recovery.admit({runId:CONTEXT.runId},CONTEXT),undefined);
  assert.equal(recovery.floor(tool('exec'),CONTEXT),undefined);
});

test('actual index lifecycle hooks suppress workful helpers even with event-only native run identity',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const source=readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const slice=(start,end)=>{
    const from=source.indexOf(start),to=source.indexOf(end,from);
    assert.ok(from>=0 && to>from);return source.slice(from,to);
  };
  const hooks=[slice('    api.on("before_prompt_build", async (event, context) => {','    api.on("model_call_started"'),
    slice('    api.on("agent_end", (event, context) => {','    api.on("before_tool_call"'),
    slice('    api.on("before_agent_finalize", async (event, context) => {','    // Delivery rewriting')].join('\n');
  const callbacks={},effects=[];
  const forbidden=name=>(()=>{effects.push(name);throw Error('Workful helper reached: '+name);});
  const noop=()=>undefined;
  const quiet={begin:noop,finish:noop,active:()=>false};
  const guard={observeRun:noop,ownerIntentEventForRun:(_id,value)=>value,verificationStatus:noop,
    observeExampleRun:forbidden('example-restore'),observeRepositorySource:noop,
    promptContextForRun:noop,exampleContextForRun:noop,clarificationContextForRun:noop,
    longTaskEligible:()=>false,endPreviewRevalidation:noop,observeAgentEnd:noop,
    revalidateWorkspacePreview:forbidden('preview-revalidate'),recoverWorkspacePreview:forbidden('preview-publish'),
    verifyCitedPages:forbidden('citation-fetch'),beforeAgentFinalize:forbidden('ordinary-finalize')};
  vm.runInNewContext(hooks,{
    api:{on:(name,callback)=>{callbacks[name]=callback;},config:{agents:{list:[{id:'pixel',workspace:'/invented'}]}}},
    AGENT_ID:'pixel',integrationReviewRecovery:recovery,integrationOutcomes:{begin:noop,end:noop},
    accessRuntime:{isProbe:()=>false,finish:noop},delegationDelivery:{admission:noop,observe:noop,
      ownerRunFor:noop,ownerRunOf:noop,promptContext:noop,finalize:noop,end:noop},
    toolLoopGuard:guard,goalProgress:quiet,taskActivity:quiet,projectSnapshots:{end:forbidden('workspace-snapshot-end')},
    privateBrowserAccessForAgent:()=>false,executionHostForAgent:()=> 'gateway',integrationCapabilitiesContext,
    promptContractForAgent:()=>({prependContext:'fixture',appendSystemContext:'fixture'}),
    extensionRepositoryContext:forbidden('repository-fetch'),responseLanguageTurnContext:noop,
    configuredResponseLanguage:undefined,configuredContextWindow:65536,configuredLeanPrompt:false,
    inResponseLanguage:value=>value,GOAL_CONTRACT:'',managedTeamRole:noop,PORTAL_INTEGRATION_API_CONTRACT:'static API fixture',PORTAL_INTEGRATION_TURN_CONTEXT:'fixture',
    longTaskContract:noop,ACTIVITY_CONTRACT:'',executionLocationContext:noop,executionContext:noop,
    conversationImageLifecycle:{observe:noop},managedRuntime:true,
  });
  const nativeEvent={...event(),prompt:OWNER};
  await callbacks.before_prompt_build(nativeEvent,{});
  const decision=await callbacks.before_agent_finalize(nativeEvent,{});
  assert.equal(decision.action,'finalize');
  callbacks.agent_end(nativeEvent,{});
  assert.deepEqual(effects,[]);
  assert.equal(recovery.floor({...tool('pixel_ods_notes'),runId:CONTEXT.runId},{}).block,true);
});

test('actual index readiness and native-start callback use the real exact integration record',async()=>{
  const source=readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start=source.indexOf('    integrationReviewRecovery ??= createIntegrationReviewRecovery({');
  const end=source.indexOf('\n    const bundleExecution',start);
  assert.ok(start>=0 && end>start);
  const config={agents:{defaults:{model:'ods-gateway/ods/current'},list:[{id:'pixel',default:true}]}};
  const integrationOutcomes=createIntegrationOutcomes();
  integrationOutcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
  let access={available:true,phase:'busy'};
  let options,clearQueries=0,modelCalls=0;
  const recovery=vm.runInNewContext('let integrationReviewRecovery;\n'+source.slice(start,end)+'\nintegrationReviewRecovery;',{
    api:{config,runtime:{llm:{complete:async()=>{modelCalls++;return reply();}}}},AGENT_ID:'pixel',defaultAgentId,
    accessRuntime:{status:()=>access},integrationOutcomes,toolLoopGuard:{continuationAllowed:()=>true},
    createIntegrationReviewRecovery:value=>{
      options=value;assert.equal(typeof value.nativeStartClear,'function');
      return createIntegrationReviewRecovery({...value,nativeStartClear:state=>{clearQueries++;return value.nativeStartClear(state);}});
    },
  });
  const ready=options.ready;
  assert.equal(ready(CONTEXT),true);
  assert.equal(options.nativeStartClear(CONTEXT),true);
  assert.equal(options.nativeStartClear({...CONTEXT,runId:'missing'}),false);
  assert.equal(options.nativeStartClear({...CONTEXT,sessionId:'foreign'}),false);
  for(const phase of ['held','interrupted','unavailable']) {
    access={available:true,phase};assert.equal(ready(CONTEXT),false,phase);
  }
  access={available:false,phase:'busy'};assert.equal(ready(CONTEXT),false);
  access={available:true,phase:'busy'};assert.equal(ready({...CONTEXT,modelId:'other-model'}),false);
  recovery.observe({prompt:OWNER},CONTEXT,CONTEXT.runId);
  assert.equal((await recovery.finalize(event(),CONTEXT)).action,'revise');
  const queriesAtLatch=clearQueries;assert.equal(modelCalls,0);assert.equal(queriesAtLatch,2,'native clear is checked before and immediately before latch');
  const called={...CONTEXT,toolCallId:'native-review-note'};
  const params={action:'write',path:'Example.md',text:'Current owned line',mode:'append'};
  integrationOutcomes.before({...tool('pixel_ods_notes',params),toolCallId:called.toolCallId},called);
  assert.equal(options.nativeStartClear(CONTEXT),false,'actual recorded native attempt closes start admission');
  recovery.beforeTool(tool('pixel_ods_notes'),called);
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT),undefined);
  assert.equal(recovery.admit(admission(),CONTEXT),undefined);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(clearQueries,queriesAtLatch,'post-latch summary must not recheck a now-nonempty start record');
});

test('a hook-refused attempt with zero admitted integration calls still blocks recovery independently',async()=>{
  const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
  const clear=state=>outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey});
  const {recovery,calls}=world({nativeStartClear:clear});
  const refused={...CONTEXT,toolCallId:'refused-before-admission'};
  outcomes.before({...tool('pixel_ods_notes',{action:'write',path:'Example.md',text:'x'}),toolCallId:refused.toolCallId},refused,{block:true});
  assert.equal(clear(CONTEXT),true,'no integration call was admitted by this blocked hook');
  recovery.beforeTool(tool('pixel_ods_notes'),refused);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(calls.length,0);
});
