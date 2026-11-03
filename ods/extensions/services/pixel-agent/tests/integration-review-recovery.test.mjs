import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {createIntegrationReviewRecovery,REVIEW_LABEL_SYSTEM,REVIEW_CONCRETE_SYSTEM,parseReviewIntent,parseConcreteVerdict,reviewIntentEligible,reviewOperationScope} from '../plugin/integration-review-recovery.mjs';
import {defaultAgentId} from '../plugin/stop-synthesis.mjs';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {createIntegrationTools,integrationRequest} from '../plugin/integrations.mjs';

const SESSION='agent:pixel:openai-user:ods-'+ 'a'.repeat(64);
const CONTEXT={agentId:'pixel',runId:'current-run',sessionId:'owned-session',sessionKey:SESSION,
  trigger:'user',modelProviderId:'fixture-local',modelId:'fixture/9b'};
const OWNER='Create an approval request to append Current owned line to Example.md. Do not write it.';
const ANSWER='You need to approve the new request in Settings.';
const event=(owner=OWNER)=>({runId:CONTEXT.runId,sessionId:CONTEXT.sessionId,sessionKey:SESSION,
  nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
  messages:[{role:'user',content:owner},{role:'assistant',content:ANSWER}],lastAssistantMessage:ANSWER});
const op=(id,approval='none')=>({id,approval});
const reply=(operations=[op('notes.append')])=>({agentId:'pixel',provider:'fixture-local',model:'fixture/9b',
  text:JSON.stringify({operations})});
const concreteReply=(entailed=true)=>({...reply(),text:JSON.stringify({entailed})});
function world(options={}) {
  const calls=[];
  const {owner=OWNER,complete:propose=async()=>reply(),verify=async()=>concreteReply(),...rest}=options;
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,nativeStartClear:()=>true,
    ...rest,complete:async params=>{calls.push(params);return (params.systemPrompt===REVIEW_CONCRETE_SYSTEM?verify:propose)(params);}});
  recovery.observe({prompt:owner},CONTEXT,CONTEXT.runId);
  return {recovery,calls};
}
const tool=(name,params={})=>({toolName:name,params});
const appendParams=()=>({action:'write',mode:'append',path:'Example.md',text:'Current owned line'});
const nativeScope=(context=CONTEXT,changes={})=>Object.freeze({version:1,active:true,
  ownerRunId:context.runId,toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_mcp']),...changes});
const nativeChoice=(context=CONTEXT,changes={})=>Object.freeze({version:1,ownerRunId:context.runId,
  mode:'required-first',phase:'armed',...changes});
const admission=(changes={})=>({...event(),nativeRetryToolsAllowState:nativeScope(),nativeRetryToolChoiceState:nativeChoice(),...changes});
const TOOL_CONTEXT=Object.freeze({agentId:CONTEXT.agentId,runId:CONTEXT.runId,sessionId:CONTEXT.sessionId,sessionKey:CONTEXT.sessionKey,toolCallId:'native-current'});
const EMPTY_REFERENCES=Object.freeze({lookups:Object.freeze([]),truncated:false});
const EMPTY_PROGRESS=Object.freeze({reads:Object.freeze([]),truncated:false});
const catalogDefinition=id=>REVIEW_LABEL_SYSTEM.split('\n').find(line=>line.startsWith(id+': ')).slice(id.length+2);
const semantic=(canonicalOperation,typeId,args,maximumEffect='private-read')=>({canonicalOperation,typeId,
  definition:catalogDefinition(typeId),arguments:args,maximumEffect});
function concreteCandidate(name='pixel_ods_notes',params=appendParams(),options={}) {
  const raw=JSON.parse(JSON.stringify({name,params}));
  const payload=options.payload??integrationRequest(name,raw.params,TOOL_CONTEXT);
  return Object.freeze({ref:options.ref??Object.freeze({}),view:Object.freeze({raw:Object.freeze(raw),payload:Object.freeze(payload),
    maximumEffect:options.maximumEffect??'approval-proposal',references:options.references??EMPTY_REFERENCES,
    progress:options.progress??EMPTY_PROGRESS})});
}
async function admitConcrete(outcomes,recovery,ev,context) {
  recovery.beforeTool(ev,context);
  const candidate=await outcomes.prepareReviewCandidate(ev,context);
  assert.ok(candidate?.ref,'the real native record must prepare an opaque candidate');
  const verdict=await outcomes.checkReviewCandidate(candidate,value=>recovery.checkConcrete(value,context));
  assert.equal(verdict?.entailed,true);assert.equal(verdict?.candidate,candidate.ref);
  assert.equal(outcomes.commitReviewCandidate(candidate,verdict),undefined);
}

test('catalog stays advisory: no concrete observation until a native candidate; exact normalized params are checked separately',async()=>{
  const {recovery,calls}=world();
  assert.equal(createHash('sha256').update(REVIEW_LABEL_SYSTEM).digest('hex'),'7dfbc5328bd9b1717ba1bc14b48ec9495a931966b63b38b5093e060949dfeb92');
  assert.equal((await recovery.finalize(event(),CONTEXT)).action,'revise');assert.equal(calls.length,1);
  recovery.beforeTool(tool('pixel_ods_notes',appendParams()),TOOL_CONTEXT);
  const candidate=concreteCandidate(),decision=await recovery.checkConcrete(candidate,TOOL_CONTEXT);
  assert.deepEqual(decision,{candidate:candidate.ref,entailed:true});assert.ok(Object.isFrozen(decision));assert.equal(calls.length,2);
  const verify=calls[1],input=JSON.parse(verify.messages[0].content);
  assert.equal(verify.systemPrompt,REVIEW_CONCRETE_SYSTEM);assert.equal(verify.maxTokens,192);assert.equal(verify.temperature,0.2);
  assert.deepEqual(Object.keys(verify).sort(),['maxTokens','messages','purpose','signal','systemPrompt','temperature']);
  assert.deepEqual(Object.keys(input).sort(),['candidateOperation','clock','declaredOperations','ownerRequest','priorCompletedReads','priorResourceReferences']);
  assert.equal(input.ownerRequest,OWNER);assert.deepEqual(input.declaredOperations,[{...op('notes.append'),definition:catalogDefinition('notes.append')}]);
  assert.deepEqual(input.candidateOperation,semantic('obsidian:append','notes.append',
    {mode:'append',path:'Example.md',text:'Current owned line'},'approval-proposal'));
  assert.deepEqual(input.priorResourceReferences,EMPTY_REFERENCES);
  assert.deepEqual(input.priorCompletedReads,EMPTY_PROGRESS);
  assert.ok(!verify.messages[0].content.includes('pixel_ods_notes'),'semantic input needs no opaque adapter name');
});

test('all twelve concrete operations use the same eleven catalog definitions through real prepare/check/commit',async()=>{
  const fixtures=[
    ['notes.search','obsidian:search','pixel_ods_notes',{action:'search',query:'an invented subject'}, {query:'an invented subject'}],
    ['notes.read','obsidian:read','pixel_ods_notes',{action:'read',path:'Folder/Orbit.md'}, {path:'Folder/Orbit.md'}],
    ['notes.create','obsidian:create','pixel_ods_notes',{action:'write',path:'Folder/New.md',text:'Exact content',mode:'create'},
      {path:'Folder/New.md',text:'Exact content',mode:'create'}],
    ['notes.append','obsidian:append','pixel_ods_notes',{action:'write',path:'Folder/Orbit.md',text:'Exact addition'},
      {path:'Folder/Orbit.md',text:'Exact addition',mode:'append'}],
    ['documents.search','paperless:search','pixel_ods_mcp',{action:'call',server:'app:documents',tool:'search',arguments:{query:'a receipt'}},{query:'a receipt'}],
    ['documents.read','paperless:read','pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:'9007199254740993'}},{id:'9007199254740993'}],
    ['photos.search','immich:search','pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{query:'boats',place:'Zürich',after:'2021-01-01',before:'2021-02-28'}},
      {query:'boats',place:'Zürich',after:'2021-01-01',before:'2021-02-28'}],
    ['monitors.list','watch:list','pixel_ods_mcp',{action:'call',server:'app:watch',tool:'list'},{}],
    ['monitors.add','watch:add','pixel_ods_mcp',{action:'call',server:'app:watch',tool:'add',arguments:{url:'https://example.invalid/item',title:'原文 🪁',below:0}},
      {url:'https://example.invalid/item',title:'原文 🪁',below:0}],
    ['monitors.remove','watch:remove','pixel_ods_mcp',{action:'call',server:'app:watch',tool:'remove',arguments:{id:'watch-exact'}},{id:'watch-exact'}],
    ['news.read','freshrss:unread','pixel_ods_mcp',{action:'call',server:'app:news',tool:'unread',arguments:{}},{}],
    ['news.read','freshrss:digest_read','pixel_ods_mcp',{action:'call',server:'app:news',tool:'digest',arguments:{}},{}],
  ];
  const seenTypes=new Set(),seenCanonical=new Set(),news=[];
  for(const [id,canonical,name,params,args]of fixtures){
    const effect=['notes.create','notes.append','monitors.add','monitors.remove'].includes(id)?'approval-proposal':'private-read';
    const approval=effect==='approval-proposal'?'before':'none',outcomes=createIntegrationOutcomes();
    outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
    const {recovery,calls}=world({complete:async()=>reply([op(id,approval)]),nativeStartClear:state=>outcomes.reviewStartClear({agentId:'pixel',
      runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey})});
    assert.equal((await recovery.finalize(event(),CONTEXT)).action,'revise',canonical);
    const ctx={...TOOL_CONTEXT,toolCallId:'mapped-'+canonical},ev={...tool(name,params),toolCallId:ctx.toolCallId};
    recovery.beforeTool(ev,ctx);assert.equal(recovery.floor(ev,ctx),undefined);
    const candidate=await outcomes.prepareReviewCandidate(ev,ctx);
    const verdict=await outcomes.checkReviewCandidate(candidate,value=>recovery.checkConcrete(value,ctx));
    assert.equal(verdict.entailed,true,canonical);assert.equal(verdict.candidate,candidate.ref);
    assert.equal(outcomes.commitReviewCandidate(candidate,verdict),undefined);
    const input=JSON.parse(calls[1].messages[0].content),projected=input.candidateOperation;
    assert.deepEqual(input.declaredOperations,[{id,definition:catalogDefinition(id),approval}]);
    const expected=semantic(canonical,id,args,effect);
    if(id==='news.read'){
      assert.equal(typeof projected.variantDefinition,'string');assert.match(projected.variantDefinition,/do not mark them read or send a notification/);
      news.push(projected.variantDefinition);expected.variantDefinition=projected.variantDefinition;
      assert.match(projected.variantDefinition,canonical==='freshrss:unread'?/currently unread/:/not previously delivered/);
    }
    assert.deepEqual(projected,expected);
    assert.deepEqual(input.priorResourceReferences,EMPTY_REFERENCES);assert.deepEqual(input.priorCompletedReads,EMPTY_PROGRESS);
    assert.equal(calls.length,2);assert.ok(!calls[1].messages[0].content.includes(SESSION));
    assert.deepEqual(candidate.view.raw,{name,params},'projection never rewrites the raw reservation');
    assert.deepEqual(candidate.view.payload,integrationRequest(name,params,ctx),'full payload stays bound outside model input');
    seenTypes.add(id);seenCanonical.add(canonical);await recovery.stop();outcomes.clear();
  }
  assert.equal(seenTypes.size,11);assert.equal(seenCanonical.size,12);assert.notEqual(news[0],news[1]);
  assert.equal(createHash('sha256').update(REVIEW_LABEL_SYSTEM).digest('hex'),'7dfbc5328bd9b1717ba1bc14b48ec9495a931966b63b38b5093e060949dfeb92');
});

test('semantic arguments preserve optional presence, defaults, zero, whitespace and non-BMP content',async()=>{
  const fixtures=[
    ['notes.append','pixel_ods_notes',{action:'write',path:' Folder/🪁 ',text:'  verbatim\n原文 🪁\n'},
      {mode:'append',path:' Folder/🪁 ',text:'  verbatim\n原文 🪁\n'},'approval-proposal'],
    ['photos.search','pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{after:'2020-01-01'}},
      {after:'2020-01-01'},'private-read'],
    ['photos.search','pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{query:'',place:'港 🪁'}},
      {query:'',place:'港 🪁'},'private-read'],
    ['monitors.add','pixel_ods_mcp',{action:'call',server:'app:watch',tool:'add',arguments:{url:'https://example.invalid/',below:0}},
      {url:'https://example.invalid/',below:0},'approval-proposal'],
    ['documents.read','pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:1}},{id:1},'private-read'],
  ];
  for(const [id,name,params,expected,effect]of fixtures){
    const {recovery,calls}=world({complete:async()=>reply([op(id)])});await recovery.finalize(event(),CONTEXT);
    assert.equal((await recovery.checkConcrete(concreteCandidate(name,params,{maximumEffect:effect}),TOOL_CONTEXT)).entailed,true);
    assert.deepEqual(JSON.parse(calls[1].messages[0].content).candidateOperation.arguments,expected);
  }
});

test('unknown raw constraints, unknown app arguments and unsafe document IDs never reach semantic projection',async()=>{
  const fixtures=[
    ['notes.append','pixel_ods_notes',{...appendParams(),after:'a user constraint'},'approval-proposal'],
    ['notes.read','pixel_ods_notes',{action:'read',path:'Example.md',limit:1},'private-read'],
    ['documents.search','pixel_ods_mcp',{action:'call',server:'app:documents',tool:'search',arguments:{query:'invoice',date:'2020-01-01'}},'private-read'],
    ['photos.search','pixel_ods_mcp',{action:'call',server:'app:photos',tool:'search',arguments:{query:'boats',limit:1}},'private-read'],
    ['news.read','pixel_ods_mcp',{action:'call',server:'app:news',tool:'digest',arguments:{feed:'selected'}},'private-read'],
    ['documents.read','pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:9007199254740992}},'private-read'],
    ['documents.read','pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:'9223372036854775808'}},'private-read'],
  ];
  for(const [id,name,params,effect]of fixtures){
    const {recovery,calls}=world({complete:async()=>reply([op(id)])});await recovery.finalize(event(),CONTEXT);
    assert.equal((await recovery.checkConcrete(concreteCandidate(name,params,{maximumEffect:effect}),TOOL_CONTEXT)).entailed,false);
    assert.equal(calls.length,1,'a lost or unsupported constraint is refused in code, not delegated to a model');
  }
});

test('concrete current-step input includes only typed bounded-read completion order, never inferred contents',async()=>{
  const owner='Read Example.md, then prepare approval to append Current owned line to it. Do not write it.';
  const progress={reads:[{sequence:2,raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md'}},status:'read-complete'}],truncated:false};
  const {recovery,calls}=world({owner,complete:async()=>reply([op('notes.read'),op('notes.append','before')])});
  await recovery.finalize(event(owner),CONTEXT);
  assert.equal((await recovery.checkConcrete(concreteCandidate('pixel_ods_notes',appendParams(),{progress}),TOOL_CONTEXT)).entailed,true);
  const input=JSON.parse(calls[1].messages[0].content);
  assert.deepEqual(input.priorCompletedReads,{reads:[{sequence:2,status:'read-complete',
    operation:semantic('obsidian:read','notes.read',{path:'Example.md'})}],truncated:false});assert.equal(input.ownerRequest,owner);
  assert.deepEqual(Object.keys(input.priorCompletedReads.reads[0]).sort(),['operation','sequence','status']);
  assert.match(REVIEW_CONCRETE_SYSTEM,/not that all contents were supplied, understood or satisfy a content condition/);
  assert.match(REVIEW_CONCRETE_SYSTEM,/missing or clipped facts remain unknown/);
  assert.equal(calls.length,2,'progress changes the existing concrete input, not the number of judges');
});

test('missing, malformed, foreign, non-read or excessive progress refuses before the concrete model',async()=>{
  const fact={sequence:1,raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md'}},status:'read-complete'};
  const progressCases=[undefined,{reads:[],truncated:'false'},
    {reads:[{...fact,status:'settled'}],truncated:false},{reads:[{...fact,status:'unknown'}],truncated:false},
    {reads:[{...fact,sequence:0}],truncated:false},{reads:[{...fact,sequence:257}],truncated:false},
    {reads:[fact,fact],truncated:false},{reads:Array.from({length:17},(_,index)=>({...fact,sequence:index+1})),truncated:false},
    ...['body','binding','assistantProse','conditions','source'].map(key=>({reads:[{...fact,[key]:'not-an-order-fact'}],truncated:false})),
    {reads:[{...fact,raw:{name:'pixel_ods_notes',params:{action:'search',query:'Example'}}}],truncated:false},
    {reads:[{...fact,raw:{name:'pixel_ods_notes',params:appendParams()}}],truncated:false},
    {reads:[{...fact,raw:{name:'pixel_ods_mcp',params:{action:'call',server:'app:documents',tool:'read',arguments:{id:'1'}}}}],truncated:false},
    {reads:[{...fact,raw:{name:'pixel_ods_notes',params:{action:'read',path:'Example.md',text:'untrusted body'}}}],truncated:false},
    {reads:Array.from({length:16},(_,index)=>({...fact,sequence:index+1,
      raw:{name:'pixel_ods_notes',params:{action:'read',path:'x'.repeat(500)}}})),truncated:true},
  ];
  for(const progress of progressCases){
    const {recovery,calls}=world({complete:async()=>reply([op('notes.read'),op('notes.append')])});
    await recovery.finalize(event(),CONTEXT);
    const candidate=concreteCandidate();
    const altered=Object.freeze({...candidate,view:Object.freeze({...candidate.view,progress})});
    assert.equal((await recovery.checkConcrete(altered,TOOL_CONTEXT)).entailed,false);
    assert.equal(calls.length,1,'invalid progress must not spend a semantic observation');
  }
});

test('progress retains exact decimal-string identities and explicit clipping without private bindings',async()=>{
  const progress={reads:[{sequence:9,raw:{name:'pixel_ods_mcp',params:{action:'call',server:'app:documents',tool:'read',
    arguments:{id:'9223372036854775807'}}},status:'read-complete'}],truncated:true};
  const {recovery,calls}=world({complete:async()=>reply([op('documents.read')])});await recovery.finalize(event(),CONTEXT);
  const candidate=concreteCandidate('pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:'9223372036854775807'}},
    {maximumEffect:'private-read',progress});
  assert.equal((await recovery.checkConcrete(candidate,TOOL_CONTEXT)).entailed,true);
  assert.deepEqual(JSON.parse(calls[1].messages[0].content).priorCompletedReads,{reads:[{sequence:9,status:'read-complete',
    operation:semantic('paperless:read','documents.read',{id:'9223372036854775807'})}],truncated:true});
  assert.equal(typeof JSON.parse(calls[1].messages[0].content).priorCompletedReads.reads[0].operation.arguments.id,'string');
  assert.equal(createHash('sha256').update(REVIEW_LABEL_SYSTEM).digest('hex'),'7dfbc5328bd9b1717ba1bc14b48ec9495a931966b63b38b5093e060949dfeb92');
});

test('every private candidate is checked, catalog metadata alone is exempt, and start-clear is never reused after latch',async()=>{
  let clear=true,queries=0;
  const {recovery,calls}=world({nativeStartClear:()=>{queries++;return clear;}});
  await recovery.finalize(event(),CONTEXT);const atLatch=queries;clear=false;
  recovery.beforeTool(tool('pixel_ods_notes',appendParams()),TOOL_CONTEXT);
  const catalog=concreteCandidate('pixel_ods_mcp',{action:'list'},{maximumEffect:'catalog-metadata'});
  assert.equal((await recovery.checkConcrete(catalog,TOOL_CONTEXT)).entailed,true);assert.equal(calls.length,1);
  for(const path of ['First.md','Second.md']){
    const next=concreteCandidate('pixel_ods_notes',{...appendParams(),path});
    assert.equal((await recovery.checkConcrete(next,TOOL_CONTEXT)).entailed,true);
  }
  assert.equal(calls.length,3);assert.equal(queries,atLatch);
});

test('strict concrete verdict is only boolean; false or invalid closes further review I/O while native summary remains live',async()=>{
  for(const text of ['{"entailed":false}','{"entailed":"true"}','{"entailed":true,"reason":"okay"}','```json\n{"entailed":true}\n```']){
    const {recovery,calls}=world({verify:async()=>({...concreteReply(),text})});await recovery.finalize(event(),CONTEXT);
    recovery.beforeTool(tool('pixel_ods_notes',appendParams()),TOOL_CONTEXT);
    const candidate=concreteCandidate(),decision=await recovery.checkConcrete(candidate,TOOL_CONTEXT);
    assert.equal(decision.entailed,false);assert.equal(decision.candidate,candidate.ref);assert.ok(Object.isFrozen(decision));
    assert.equal(recovery.isReviewOnly(CONTEXT),true);assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
    assert.equal(recovery.floor(tool('pixel_ods_mcp',{action:'list'}),CONTEXT).block,true);
    assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
    assert.equal((await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,false);assert.equal(calls.length,2,'no alternative or third judge');
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(calls.length,2);
  }
  assert.deepEqual(parseConcreteVerdict('{"entailed":true}'),{entailed:true});
  for(const text of ['true','false','null','[]','{}','{"entailed":1}',' '.repeat(4097)])assert.equal(parseConcreteVerdict(text),undefined);
});

test('raw invalid modes, canonical drift, wrong effects and opaque bindings never reach the concrete model',async()=>{
  const cases=[
    concreteCandidate('pixel_ods_notes',{...appendParams(),mode:'overwrite'}),
    concreteCandidate('pixel_ods_notes',appendParams(),{payload:{...integrationRequest('pixel_ods_notes',appendParams(),TOOL_CONTEXT),path:'Other.md'}}),
    concreteCandidate('pixel_ods_notes',appendParams(),{maximumEffect:'private-read'}),
    concreteCandidate('pixel_ods_notes',appendParams(),{references:{lookups:[{source:'obsidian',query:'x',truncated:false,
      entries:[{path:'Example.md',binding:'private-binding-never-for-model'}]}],truncated:false}}),
  ];
  for(const candidate of cases){
    const {recovery,calls}=world();await recovery.finalize(event(),CONTEXT);
    assert.equal((await recovery.checkConcrete(candidate,TOOL_CONTEXT)).entailed,false);assert.equal(calls.length,1);
    assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
  }
});

test('declared reads alone permit a same-source lookup potential, never an implicit private preread from a write',async()=>{
  const read=world({complete:async()=>reply([op('notes.read')])});await read.recovery.finalize(event(),CONTEXT);
  const lookup=concreteCandidate('pixel_ods_notes',{action:'search',query:'calendar'},{maximumEffect:'private-read'});
  assert.equal(read.recovery.floor(tool('pixel_ods_notes',{action:'search',query:'calendar'}),CONTEXT),undefined);
  assert.equal((await read.recovery.checkConcrete(lookup,TOOL_CONTEXT)).entailed,true);
  const write=world();await write.recovery.finalize(event(),CONTEXT);
  assert.equal((await write.recovery.checkConcrete(lookup,TOOL_CONTEXT)).entailed,false);assert.equal(write.calls.length,1);
  const mixed=world({complete:async()=>reply([op('notes.read'),op('notes.append','before')])});
  assert.equal((await mixed.recovery.finalize(event(),CONTEXT)).action,'revise');
  assert.equal((await mixed.recovery.checkConcrete(lookup,TOOL_CONTEXT)).entailed,true,'explicit separate read may precede its Ask proposal');
  assert.equal((await mixed.recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,true);
  assert.equal(reviewOperationScope(parseReviewIntent(reply([op('notes.read','before'),op('notes.append','before')]).text)),undefined);
});

test('trusted owner clock/timezone freezes once and cannot be supplied by owner/model text',async t=>{
  let now=Date.parse('2026-11-02T12:00:00Z'),zone='America/Sao_Paulo',queries=0;
  t.mock.method(Date,'now',()=>now);
  const {recovery,calls}=world({ownerTimezone:()=>{queries++;return zone;}});await recovery.finalize(event(),CONTEXT);
  zone='Europe/Berlin';now+=3600000;
  await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT);
  const clock=JSON.parse(calls[1].messages[0].content).clock;
  assert.deepEqual(clock,{ownerObservedAtUTC:'2026-11-02T12:00:00.000Z',ownerTimezone:'America/Sao_Paulo',checkedAtUTC:'2026-11-02T13:00:00.000Z'});
  assert.equal(queries,1);assert.deepEqual(Object.keys(JSON.parse(calls[0].messages[0].content)),['ownerRequest']);
  const unknown=world({ownerTimezone:()=> 'not-a-timezone'});await unknown.recovery.finalize(event(),CONTEXT);
  await unknown.recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT);
  assert.equal(JSON.parse(unknown.calls[1].messages[0].content).clock.ownerTimezone,null);
});

test('typed lookup facts are projected without opaque source/session bindings; news dispatch identity stays private',async()=>{
  const references={lookups:[{source:'paperless',query:'train',entries:[{id:'9223372036854775807',title:'Train ticket'}],truncated:true}],truncated:true};
  const read=world({complete:async()=>reply([op('documents.read')])});await read.recovery.finalize(event(),CONTEXT);
  const candidate=concreteCandidate('pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:'9223372036854775807'}},
    {maximumEffect:'private-read',references,ref:Object.freeze({opaque:'DO_NOT_SERIALIZE'})});
  assert.equal((await read.recovery.checkConcrete(candidate,TOOL_CONTEXT)).entailed,true);
  assert.deepEqual(JSON.parse(read.calls[1].messages[0].content).priorResourceReferences,references);assert.ok(!read.calls[1].messages[0].content.includes('DO_NOT_SERIALIZE'));
  const news=world({complete:async()=>reply([op('news.read')])});await news.recovery.finalize(event(),CONTEXT);
  await news.recovery.checkConcrete(concreteCandidate('pixel_ods_mcp',{action:'call',server:'app:news',tool:'digest',arguments:{}},{maximumEffect:'private-read'}),TOOL_CONTEXT);
  const input=JSON.parse(news.calls[1].messages[0].content);assert.equal(Object.hasOwn(input.candidateOperation.arguments,'runContext'),false);
  assert.ok(!news.calls[1].messages[0].content.includes(SESSION));
});

test('each concrete await rechecks native lifetime and route; cancellation/end/stop cannot grant a late verdict',async()=>{
  for(const change of ['cancel','end','stop','supersede','ready','provider-result','model-result','explicit-tool-route']){
    let release,started,ready=true;
    const waiting=new Promise(resolve=>{release=resolve;}),entered=new Promise(resolve=>{started=resolve;});
    const {recovery,calls}=world({ready:()=>ready,verify:()=>{started();return waiting;}});await recovery.finalize(event(),CONTEXT);
    const ctx={...TOOL_CONTEXT},pending=recovery.checkConcrete(concreteCandidate(),ctx);await entered;
    if(change==='cancel')recovery.cancelSession(SESSION);
    if(change==='end')recovery.end({},ctx);
    if(change==='stop')await recovery.stop();
    if(change==='supersede')recovery.observe({prompt:'New owner'}, {...CONTEXT,runId:'new-owner'},'new-owner');
    if(change==='ready')ready=false;
    if(change==='explicit-tool-route')ctx.modelProviderId='foreign';
    release({...concreteReply(),...(change==='provider-result'?{provider:'foreign'}:{}),...(change==='model-result'?{model:'foreign'}:{})});
    assert.equal((await pending).entailed,false,change);assert.equal(calls.length,2);
  }
});

test('cumulative semantic budget includes proposal and every veto, excludes intervening inference/queue time, never rearms',async t=>{
  let time=0;t.mock.method(performance,'now',()=>time);
  const {recovery,calls}=world({timeoutMs:1000,complete:async()=>{time+=100;return reply();},verify:async()=>{time+=300;return concreteReply();}});
  await recovery.finalize(event(),CONTEXT);time+=50000;
  assert.equal((await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,true);time+=50000;
  assert.equal((await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,true);time+=50000;
  assert.equal((await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,false,'final300 reaches total1000');
  assert.equal(calls.length,4);assert.equal((await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,false);assert.equal(calls.length,4);
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
});

test('a concrete deadline aborts the native observation and cannot admit its late fulfilled verdict',async()=>{
  let release,signal;
  const waiting=new Promise(resolve=>{release=resolve;});
  const {recovery,calls}=world({timeoutMs:200,verify:params=>{signal=params.signal;return waiting;}});
  assert.equal((await recovery.finalize(event(),CONTEXT)).action,'revise');
  const decision=await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT);
  assert.equal(decision.entailed,false);assert.equal(signal.aborted,true);
  release(concreteReply());await Promise.resolve();
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
  assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
  assert.equal((await recovery.checkConcrete(concreteCandidate(),TOOL_CONTEXT)).entailed,false);assert.equal(calls.length,2);
});

test('exact latched run needs actual immutable active native scope before inference',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  assert.equal(recovery.admit(admission(),CONTEXT),undefined);
  assert.equal(recovery.admit(admission({nativeRetryToolsAllowState:nativeScope(CONTEXT,{toolsAllow:Object.freeze(['pixel_ods_mcp','pixel_ods_notes'])})}),CONTEXT),undefined);
});

test('missing/reset/foreign/widened native scope blocks the same owner run and cannot be revived',async()=>{
  for(const value of [undefined,null,Object.freeze({version:1,active:false,ownerRunId:CONTEXT.runId,toolsAllow:null}),
    nativeScope(CONTEXT,{version:2}),nativeScope(CONTEXT,{ownerRunId:'foreign-owner'}),
    nativeScope(CONTEXT,{toolsAllow:Object.freeze(['pixel_ods_notes','exec'])}),
    nativeScope(CONTEXT,{toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_notes'])}),
    nativeScope(CONTEXT,{toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_mcp','tool_call'])}),
    nativeScope(CONTEXT,{toolsAllow:['pixel_ods_notes','pixel_ods_mcp']}),{...nativeScope()}]) {
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    assert.equal(recovery.admit({...event(),nativeRetryToolsAllowState:value},CONTEXT).outcome,'block');
    assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true);
    assert.equal(recovery.admit({...event(),nativeRetryToolsAllowState:nativeScope()},CONTEXT).outcome,'block');
  }
});

test('fresh owner run ID in the same chat is ordinary even before prompt observation',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  recovery.admit(event(),CONTEXT); // The earlier run lost its native scope.
  const fresh={...CONTEXT,runId:'brand-new-owner'};
  assert.equal(recovery.admit({runId:fresh.runId},fresh),undefined);
  recovery.observe({prompt:'A fresh owner request'},fresh,fresh.runId);
  assert.equal(recovery.admit({runId:fresh.runId},fresh),undefined);
  assert.equal(recovery.floor(tool('exec'),fresh),undefined);
});

test('conflicting exact native IDs fail closed without using session-only admission',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  assert.equal(recovery.admit({...event(),nativeRetryToolsAllowState:nativeScope()},{...CONTEXT,runId:'foreign'}).outcome,'block');
  assert.equal(recovery.admit({runId:'unrelated'},{agentId:'pixel',runId:'unrelated',sessionId:CONTEXT.sessionId,sessionKey:SESSION}),undefined);
});

test('native proof cannot override conflict, supersession or revoked current readiness/admission',async()=>{
  for(const change of ['provider','superseded','eligible','ready']) {
    let eligible=true,ready=true;
    const {recovery}=world({eligible:()=>eligible,ready:()=>ready});
    await recovery.finalize(event(),CONTEXT);
    if(change==='provider')recovery.observe({prompt:OWNER},{...CONTEXT,modelProviderId:'changed-provider'},CONTEXT.runId);
    if(change==='superseded')recovery.observe({prompt:'A fresh owner request'}, {...CONTEXT,runId:'replacement-owner'},'replacement-owner');
    if(change==='eligible')eligible=false;
    if(change==='ready')ready=false;
    assert.equal(recovery.admit({...event(),nativeRetryToolsAllowState:nativeScope()},CONTEXT).outcome,'block',change);
    assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true,change);
  }
});

test('review latches synchronously before ONE stock native revise; only six supported native params',async()=>{
  const {recovery,calls}=world();
  const result=await recovery.finalize(event(),CONTEXT);
  assert.equal(result.action,'revise');assert.equal(result.retry.maxAttempts,1);
  assert.deepEqual(result.retry.toolsAllow,['pixel_ods_notes','pixel_ods_mcp']);
  assert.equal(result.retry.toolChoice,'required');
  assert.equal(recovery.isReviewOnly(CONTEXT),true);
  assert.deepEqual(Object.keys(calls[0]).sort(),['maxTokens','messages','purpose','signal','systemPrompt','temperature']);
  assert.equal(calls[0].systemPrompt,REVIEW_LABEL_SYSTEM);
  assert.equal(calls[0].maxTokens,192);assert.equal(calls[0].temperature,0.2);
  assert.equal(JSON.parse(calls[0].messages[0].content).ownerRequest,OWNER);
  assert.deepEqual(Object.keys(JSON.parse(calls[0].messages[0].content)),['ownerRequest']);
  assert.match(result.retry.instruction,/Current owner request/);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(calls.length,1);
  assert.deepEqual(await recovery.finalize(event(),CONTEXT,{action:'revise'}),{action:'continue'});
});

test('old SDK or unrecognized native capability spends no semantic check and never requests an unfiltered retry',async()=>{
  for(const version of [undefined,null,0,2,'1',true]) {
    const {recovery,calls}=world();
    const ev={...event(),nativeRetryToolsAllowVersion:version};
    // A model/context claim cannot replace the native event acknowledgment.
    assert.equal(await recovery.finalize(ev,{...CONTEXT,nativeRetryToolsAllowVersion:1}),undefined);
    assert.equal(calls.length,0,String(version));
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
});

test('losing native tool-cap acknowledgment while the semantic check waits cannot latch a retry',async()=>{
  let release;
  const held=new Promise(resolve=>{release=resolve;});
  const {recovery}=world({complete:()=>held});
  const ev=event(),result=recovery.finalize(ev,CONTEXT);
  delete ev.nativeRetryToolsAllowVersion;
  release(reply());
  assert.equal(await result,undefined);
  assert.equal(recovery.isReviewOnly(CONTEXT),false);
});

test('cap-only SDK or unrecognized native required-first capability never spends the semantic check',async()=>{
  for(const version of [undefined,null,0,2,'1',true]) {
    const {recovery,calls}=world(),ev={...event(),nativeRetryToolChoiceVersion:version};
    assert.equal(await recovery.finalize(ev,{...CONTEXT,nativeRetryToolChoiceVersion:1}),undefined);
    assert.equal(calls.length,0,String(version));
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
});

test('losing required-first native acknowledgment while the semantic check waits cannot latch',async()=>{
  let release;
  const held=new Promise(resolve=>{release=resolve;});
  const {recovery}=world({complete:()=>held}),ev=event();
  const pending=recovery.finalize(ev,CONTEXT);
  delete ev.nativeRetryToolChoiceVersion;
  release(reply());
  assert.equal(await pending,undefined);
  assert.equal(recovery.isReviewOnly(CONTEXT),false);
});

test('latched native admission requires immutable exact required-first armed/tool-started proof',async()=>{
  for(const invalid of [undefined,null,{}, {...nativeChoice()},nativeChoice(CONTEXT,{version:2}),
    nativeChoice(CONTEXT,{ownerRunId:'foreign'}),nativeChoice(CONTEXT,{mode:'auto'}),
    nativeChoice(CONTEXT,{phase:'issued'}),nativeChoice(CONTEXT,{phase:'closed'}),
    nativeChoice(CONTEXT,{phase:'attempted'}),nativeChoice(CONTEXT,{phase:'unknown'})]) {
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:invalid}),CONTEXT).outcome,'block');
    assert.equal(recovery.admit(admission(),CONTEXT).outcome,'block');
  }
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  assert.equal(recovery.admit(admission(),CONTEXT),undefined);
  assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
});

test('a current tool-start or verified native tool-started proof can never rearm a required request',async()=>{
  for(const startedBy of ['native-proof','native-before-tool']) {
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    assert.equal(recovery.admit(admission(),CONTEXT),undefined);
    if(startedBy==='native-proof')assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
    else {
      recovery.beforeTool(tool('pixel_ods_notes'),CONTEXT);
      assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
    }
    assert.equal(recovery.admit(admission(),CONTEXT).outcome,'block',startedBy);
    assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true);
  }
});

test('native floor permits only the requested operation and harmless metadata discovery',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  for(const event of [tool('pixel_ods_notes',appendParams()),tool('pixel_ods_mcp',{action:'list'}),
    tool('tool_search'),tool('tool_describe'),
    tool('tool_call',{id:'openclaw:pixel-ods:pixel_ods_notes',args:appendParams()}),
    tool('tool_call',{id:'openclaw:pixel-ods:pixel_ods_mcp',args:{action:'list'}})])
    assert.equal(recovery.floor(event,CONTEXT),undefined,JSON.stringify(event));
  for(const name of ['exec','read','write','apply_patch','browser','web_fetch','pixel_ods_ops','pixel_ods_notify',
    'cron','sessions_spawn','tool_search_code','pixel_ods_extensions'])
    assert.equal(recovery.floor(tool(name),CONTEXT).block,true,name);
  for(const id of ['pixel_ods_notes','openclaw:other:pixel_ods_notes','openclaw:pixel-ods:pixel_ods_notify',
    'openclaw:core:exec','openclaw:pixel-ods:pixel_ods_ops'])
    assert.equal(recovery.floor(tool('tool_call',{id,args:{}}),CONTEXT).block,true,id);
  const existing={block:true,blockReason:'Unknown submitted outcome'};
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT,existing),existing);
});

test('latched operation scope blocks cross-source, cross-verb, implicit private reads and notes mode changes',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const denied=[tool('pixel_ods_notes',{action:'read',path:'Example.md'}),tool('pixel_ods_notes',{action:'search',query:'x'}),
    tool('pixel_ods_notes',{...appendParams(),mode:'create'}),tool('pixel_ods_notes',{...appendParams(),mode:'overwrite'}),
    tool('pixel_ods_mcp',{action:'call',server:'app:documents',tool:'read',arguments:{id:1}}),
    tool('pixel_ods_mcp',{action:'call',server:'app:watch',tool:'add',arguments:{url:'https://example.invalid'}}),
    tool('tool_call',{id:'openclaw:pixel-ods:pixel_ods_notes',args:{action:'read',path:'Example.md'}}),
    tool('tool_call',{id:'openclaw:pixel-ods:pixel_ods_notes',args:null}),tool('pixel_ods_notes',null),
    tool('pixel_ods_notes',[]),tool('pixel_ods_notes',{action:'remove',path:'Example.md'})];
  for(const value of denied)assert.equal(recovery.floor(value,CONTEXT).block,true,JSON.stringify(value));
  assert.equal(recovery.floor(tool('pixel_ods_notes',{action:'write',path:'Example.md',text:'x'}),CONTEXT),undefined,'native omitted mode means append');
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT,{params:{...appendParams(),mode:'create'}}).block,true);
  const malformed={action:'write',get mode(){throw Error('Malformed native parameters');}};
  assert.equal(recovery.floor(tool('pixel_ods_notes',malformed),CONTEXT).block,true);
  recovery.observe({prompt:'An internal fallback with different text'},CONTEXT,CONTEXT.runId);
  assert.equal(recovery.floor(tool('pixel_ods_notes',{action:'read',path:'Example.md'}),CONTEXT).block,true,'same-owner fallback retains scope');
});

test('builtin reads use exact requested app and operation; catalog metadata grants no private prerequisite read',async()=>{
  const {recovery}=world({complete:async()=>reply([op('documents.read')])});
  await recovery.finalize(event(),CONTEXT);
  const call=(server,name,args={})=>tool('pixel_ods_mcp',{action:'call',server,tool:name,arguments:args});
  assert.equal(recovery.floor(call('app:documents','read',{id:1}),CONTEXT),undefined);
  assert.equal(recovery.floor(tool('tool_call',{id:'openclaw:pixel-ods:pixel_ods_mcp',args:call('app:documents','read',{id:1}).params}),CONTEXT),undefined);
  assert.equal(recovery.floor(call('app:documents','search',{query:'invoice'}),CONTEXT),undefined,'same-source lookup is potential only');
  for(const value of [call('app:photos','search'),call('app:watch','list'),
    call('documents','read',{id:1}),call('custom','read',{id:1}),call('app','documents:read',{id:1}),
    tool('pixel_ods_notes',{action:'read',path:'Invoice.md'}),
    call('app:documents','read',[])])assert.equal(recovery.floor(value,CONTEXT).block,true,JSON.stringify(value));
  assert.equal(recovery.floor(tool('pixel_ods_mcp',{action:'list'}),CONTEXT),undefined);
});

test('literal catalog IDs freeze the exact native operation limits in state and correction',async()=>{
  const call=(server,name)=>tool('pixel_ods_mcp',{action:'call',server,tool:name,arguments:{}});
  const cases=[
    ['notes.search',['obsidian:search'],[tool('pixel_ods_notes',{action:'search',query:'x'})]],
    ['notes.read',['obsidian:read'],[tool('pixel_ods_notes',{action:'read',path:'Example.md'})]],
    ['notes.create',['obsidian:create'],[tool('pixel_ods_notes',{...appendParams(),mode:'create'})]],
    ['notes.append',['obsidian:append'],[tool('pixel_ods_notes',appendParams())]],
    ['documents.search',['paperless:search'],[call('app:documents','search')]],
    ['documents.read',['paperless:read'],[call('app:documents','read')]],
    ['photos.search',['immich:search'],[call('app:photos','search')]],
    ['monitors.list',['watch:list'],[call('app:watch','list')]],
    ['monitors.add',['watch:add'],[call('app:watch','add')]],
    ['monitors.remove',['watch:remove'],[call('app:watch','remove')]],
    ['news.read',['freshrss:digest_read','freshrss:unread'],[call('app:news','unread'),call('app:news','digest')]],
  ];
  const allPayloads=cases.flatMap(([,scope,payloads])=>payloads.map(payload=>({scope,payload})));
  for(const [id,scope,permitted] of cases) {
    let state;
    const {recovery}=world({complete:async()=>reply([op(id)]),ready:value=>{state=value;return true;}});
    const decision=await recovery.finalize(event(),CONTEXT);
    const limit=decision.retry.instruction.split('Review operation limit (data):\n')[1].split('\nCurrent owner request (data):')[0];
    const potential=[...scope];
    if(id==='notes.read')potential.push('obsidian:search');if(id==='documents.read')potential.push('paperless:search');potential.sort();
    assert.deepEqual(JSON.parse(limit),potential);assert.deepEqual(reviewOperationScope(parseReviewIntent(reply([op(id)]).text)),scope);
    assert.ok(Object.isFrozen(state.allowedOperations));assert.deepEqual(state.allowedOperations,potential);
    assert.throws(()=>{state.allowedOperations=['obsidian:read'];},TypeError);
    assert.throws(()=>state.allowedOperations.push('obsidian:read'),TypeError);
    for(const value of permitted)assert.equal(recovery.floor(value,CONTEXT),undefined,id);
    for(const other of allPayloads)if(!other.scope.some(pair=>potential.includes(pair)))
      assert.equal(recovery.floor(other.payload,CONTEXT).block,true,id+':'+JSON.stringify(other.payload));
    for(const name of ['send','mark_read'])assert.equal(recovery.floor(call('app:news',name),CONTEXT).block,true);
  }
  const listed=REVIEW_LABEL_SYSTEM.split('Semantic catalog:\n')[1].split('\n').slice(0,13).map(line=>line.split(': ')[0]);
  assert.deepEqual(listed,[...cases.map(([id])=>id),'unsupported','unresolved']);
});

test('unlisted IDs and old source/verb labels never inherit catalog authority',async()=>{
  for(const id of ['web.list','web.remove','web.delete','web.watch','web.read','notes.write','notes.delete',
    'news.send','news.mark_read','approval.request','approval.approve','notes.APPEND','openclaw:notes.append']) {
    assert.equal(parseReviewIntent(reply([op(id)]).text),undefined,id);
    const {recovery}=world({complete:async()=>reply([op(id)])});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined,id);
    assert.equal(recovery.isReviewOnly(CONTEXT),false,id);
    assert.equal(recovery.floor(tool('browser'),CONTEXT),undefined,id);
  }
  assert.equal(parseReviewIntent(JSON.stringify({operations:[{source:'obsidian',verb:'append'}]})),undefined);
});

test('owner-requested approval timing grants nothing beyond one exact underlying proposal',async()=>{
  const proposals=[
    ['notes.append','obsidian:append',tool('pixel_ods_notes',appendParams())],
    ['notes.create','obsidian:create',tool('pixel_ods_notes',{...appendParams(),mode:'create'})],
    ['monitors.add','watch:add',tool('pixel_ods_mcp',{action:'call',server:'app:watch',tool:'add',arguments:{}})],
    ['monitors.remove','watch:remove',tool('pixel_ods_mcp',{action:'call',server:'app:watch',tool:'remove',arguments:{}})],
  ];
  for(const [id,canonical,allowed] of proposals)for(const approval of ['none','before']) {
    const operations=[op(id,approval)];
    assert.deepEqual(reviewOperationScope(parseReviewIntent(reply(operations).text)),[canonical]);
    const {recovery}=world({complete:async()=>reply(operations)});
    const decision=await recovery.finalize(event(),CONTEXT);
    assert.equal(decision.action,'revise');assert.ok(decision.retry.instruction.includes(JSON.stringify([canonical])));
    assert.equal(recovery.floor(allowed,CONTEXT),undefined);
    assert.equal(recovery.floor(tool('pixel_ods_mcp',{action:'list'}),CONTEXT),undefined,'catalog metadata remains available');
    for(const denied of [tool('pixel_ods_notes',{action:'read',path:'Example.md'}),
      tool('pixel_ods_notes',{action:'search',query:'x'}),tool('pixel_ods_notify',{message:'x'}),
      tool('pixel_ods_mcp',{action:'call',server:'app:watch',tool:'list',arguments:{}})])
      assert.equal(recovery.floor(denied,CONTEXT).block,true,id);
  }
});

test('approval-before-read and mixed approval timing refuse atomically',async()=>{
  const reads=['notes.search','notes.read','documents.search','documents.read','photos.search','monitors.list','news.read'];
  const operationsCases=[...reads.map(id=>[op(id,'before')]),
    [op('notes.append'),op('notes.read','before')],[op('notes.append','before'),op('notes.read','before')],
    [op('notes.append','before'),op('documents.read')],[op('unsupported','before')],
    [op('notes.append','before'),op('unsupported')],[op('unresolved','before')]];
  for(const operations of operationsCases) {
    assert.equal(reviewOperationScope(parseReviewIntent(reply(operations).text)),undefined,JSON.stringify(operations));
    const {recovery}=world({complete:async()=>reply(operations)});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined,JSON.stringify(operations));
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
    assert.equal(recovery.floor(tool('exec'),CONTEXT),undefined,'ordinary first pass is unchanged');
  }
});

test('distinct proposal types never choose a winner or trim the owner workflow',async()=>{
  const proposals=['notes.append','notes.create','monitors.add','monitors.remove'];
  for(const first of proposals)for(const second of proposals) {
    if(first===second)continue;
    const operations=[op(first),op(second)];
    assert.equal(reviewOperationScope(parseReviewIntent(reply(operations).text)),undefined,JSON.stringify(operations));
    const {recovery}=world({complete:async()=>reply(operations)});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
  assert.equal(parseReviewIntent(reply([op('monitors.remove'),op('monitors.remove','before')]).text),undefined,'IDs are unique even when timing differs');
  assert.deepEqual(reviewOperationScope(parseReviewIntent(reply([op('notes.search'),op('notes.append')]).text)),
    ['obsidian:append','obsidian:search'],'without before timing explicit reads remain explicit operations');
});

test('native floor also rejects an invalid retained scope containing multiple proposal types',async()=>{
  for(const scope of [['obsidian:append','obsidian:create'],['watch:add','watch:remove']]) {
    let state,ready=false;
    const {recovery}=world({ready:value=>{state=value;return ready;}});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
    // Trusted-state fault injection exercises the validator independently of
    // scope construction. It makes no process-isolation claim.
    state.allowedOperations=Object.freeze(scope);state.reviewOnly=true;ready=true;
    for(const value of [tool('pixel_ods_notes',appendParams()),tool('pixel_ods_notes',{...appendParams(),mode:'create'}),
      tool('pixel_ods_mcp',{action:'call',server:'app:watch',tool:'add',arguments:{}}),
      tool('pixel_ods_mcp',{action:'list'}),tool('tool_search')])
      assert.equal(recovery.floor(value,CONTEXT).block,true,JSON.stringify(scope));
  }
});

test('requested create mode permits no append, and multiple explicit reads admit only their shared source',async()=>{
  const created=world({complete:async()=>reply([op('notes.create')])});
  await created.recovery.finalize(event(),CONTEXT);
  assert.equal(created.recovery.floor(tool('pixel_ods_notes',{...appendParams(),mode:'create'}),CONTEXT),undefined);
  assert.equal(created.recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
  const read=world({complete:async()=>reply([op('notes.search'),op('notes.read')])});
  await read.recovery.finalize(event(),CONTEXT);
  assert.equal(read.recovery.floor(tool('pixel_ods_notes',{action:'search',query:'Gmail'}),CONTEXT),undefined);
  assert.equal(read.recovery.floor(tool('pixel_ods_notes',{action:'read',path:'Gmail.md'}),CONTEXT),undefined);
  assert.equal(read.recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT).block,true);
});

test('same host-payload converter binds a permitted review call and changed source/verb/mode cannot borrow its ticket',async()=>{
  for(const change of ['none','mode','readonly','readonly_ended','readonly_cancelled','readonly_cleared','readonly_hookless']) {
    const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
    const {recovery}=world();await recovery.finalize(event(),CONTEXT);
    const params=appendParams(),context={...CONTEXT,toolCallId:'native-'+change},ev={...tool('pixel_ods_notes',params),toolCallId:context.toolCallId};
    assert.equal(recovery.floor(ev,CONTEXT),undefined);
    if(change!=='readonly_hookless')await admitConcrete(outcomes,recovery,ev,context);
    if(change==='readonly_ended'){outcomes.end({success:true},CONTEXT);recovery.end({},CONTEXT);}
    if(change==='readonly_cancelled'){outcomes.cancelUser(SESSION.slice('agent:pixel:openai-user:'.length));recovery.cancelSession(SESSION);}
    if(change==='readonly_cleared')outcomes.clear();
    const submitted=[];
    const notes=createIntegrationTools({context:CONTEXT,outcomes,requireBound:ctx=>recovery.isReviewOnly(ctx),submit:async payload=>{submitted.push(payload);
      return {ok:true,status:'approval-required',approvalId:'aabbcc112233',summary:'Add to note Example.md',
        recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false};}}).find(value=>value.name==='pixel_ods_notes');
    const actual=change==='mode'?{...params,mode:'create'}:change.startsWith('readonly')?{action:'read',path:'Private.md'}:params;
    const result=await notes.execute(context.toolCallId,actual);
    if(change==='none') {
      assert.equal(submitted.length,1);assert.deepEqual(submitted[0],{...integrationRequest('pixel_ods_notes',params,CONTEXT),recoveryReviewOnly:true});
      assert.notEqual(result.isError,true);
    } else {assert.equal(submitted.length,0,change);assert.equal(result.isError,true,change);}
  }
});

test('requested notes reads and harmless catalog calls keep exact private tickets; normal first-pass optional reads stay ordinary',async()=>{
  const fixtures=[
    [[op('notes.read')],'pixel_ods_notes',{action:'read',path:'Example.md'}],
    [[op('notes.search')],'pixel_ods_notes',{action:'search',query:'Gmail'}],
    [[op('notes.append')],'pixel_ods_mcp',{action:'list'}],
  ];
  for(const [operations,name,params] of fixtures) {
    const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
    const {recovery}=world({complete:async()=>reply(operations)});await recovery.finalize(event(),CONTEXT);
    const context={...CONTEXT,toolCallId:'requested-read'},ev={...tool(name,params),toolCallId:context.toolCallId};
    assert.equal(recovery.floor(ev,CONTEXT),undefined);await admitConcrete(outcomes,recovery,ev,context);
    const submitted=[];
    const adapter=createIntegrationTools({context:CONTEXT,outcomes,requireBound:ctx=>recovery.isReviewOnly(ctx),submit:async payload=>{submitted.push(payload);
      return payload.action==='notes-read'?{ok:true,path:'Example.md',text:'Invented note',recoveryReviewOnly:true,
          reviewReferences:{version:1,source:'obsidian',binding:'a'.repeat(64),
            entries:[{path:'Example.md',binding:'b'.repeat(64)}],truncated:false}}
        :payload.action==='notes-search'?{ok:true,results:[],recoveryReviewOnly:true,
          reviewReferences:{version:1,source:'obsidian',binding:'a'.repeat(64),entries:[],truncated:false}}
          :{ok:true,servers:[],recoveryReviewOnly:true};}}).find(value=>value.name===name);
    const result=await adapter.execute(context.toolCallId,params);
    assert.equal(submitted.length,1);assert.deepEqual(submitted[0],{...integrationRequest(name,params,CONTEXT),recoveryReviewOnly:true});
    assert.notEqual(result.isError,true);
  }
  const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
  const {recovery}=world(),submitted=[];
  const notes=createIntegrationTools({context:CONTEXT,outcomes,requireBound:ctx=>recovery.isReviewOnly(ctx),submit:async payload=>{submitted.push(payload);
    return {ok:true,path:'Example.md',text:'Invented note'};}}).find(value=>value.name==='pixel_ods_notes');
  const result=await notes.execute('ordinary-optional-read',{action:'read',path:'Example.md'});
  assert.notEqual(result.isError,true);assert.equal(submitted.length,1);assert.equal(Object.hasOwn(submitted[0],'recoveryReviewOnly'),false);
});

test('builtin source and verb changes after operation-floor admission cannot borrow an MCP ticket',async()=>{
  for(const change of ['none','source','verb']) {
    const outcomes=createIntegrationOutcomes();outcomes.begin({prompt:OWNER},CONTEXT,CONTEXT.runId);
    const {recovery}=world({complete:async()=>reply([op('documents.read')])});
    await recovery.finalize(event(),CONTEXT);
    const params={action:'call',server:'app:documents',tool:'read',arguments:{id:1}},context={...CONTEXT,toolCallId:'mcp-'+change};
    const ev={...tool('pixel_ods_mcp',params),toolCallId:context.toolCallId};
    assert.equal(recovery.floor(ev,CONTEXT),undefined);await admitConcrete(outcomes,recovery,ev,context);
    const submitted=[];
    const mcp=createIntegrationTools({context:CONTEXT,outcomes,requireBound:ctx=>recovery.isReviewOnly(ctx),submit:async payload=>{submitted.push(payload);
      return {ok:true,server:'app:documents',tool:'read',text:'Invented document',recoveryReviewOnly:true,
        reviewReferences:{version:1,source:'paperless',binding:'a'.repeat(64),
          entries:[{id:'1',title:'Invented document'}],truncated:false}};}}).find(value=>value.name==='pixel_ods_mcp');
    const actual=change==='source'?{...params,server:'app:photos',tool:'search'}:change==='verb'?{...params,tool:'search'}:params;
    const result=await mcp.execute(context.toolCallId,actual);
    if(change==='none') {
      assert.equal(submitted.length,1);assert.deepEqual(submitted[0],{...integrationRequest('pixel_ods_mcp',params,CONTEXT),recoveryReviewOnly:true});
      assert.notEqual(result.isError,true);
    } else {assert.equal(submitted.length,0,change);assert.equal(result.isError,true,change);}
  }
});

test('normal first-pass tools stay unchanged but EVERY attempt prevents automatic recovery',async()=>{
  for(const name of ['exec','pixel_ods_notes','pixel_ods_notify','tool_search','tool_call']) {
    const {recovery,calls}=world();
    assert.equal(recovery.floor(tool(name),CONTEXT),undefined);
    recovery.beforeTool(tool(name),CONTEXT);
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
    assert.equal(calls.length,0,name);
  }
});

test('native pending/unknown/failed/block decisions and transcript attempts never revise',async()=>{
  for(const prior of [{action:'abort'},{action:'block'},{action:'revise'},{action:'retry'}]) {
    const {recovery,calls}=world();
    assert.equal(await recovery.finalize(event(),CONTEXT,prior),prior);
    assert.equal(calls.length,0);
  }
  for(const message of [{role:'toolResult',content:'Pending'},
    {role:'assistant',content:[{type:'toolCall',name:'pixel_ods_notes'}]}]) {
    const {recovery,calls}=world(),ev=event();ev.messages.splice(1,0,message);
    assert.equal(await recovery.finalize(ev,CONTEXT),undefined);assert.equal(calls.length,0);
  }
});

test('only verified interactive native Portal owner runs qualify; cron/background/unknown do not',async()=>{
  for(const change of [{trigger:'cron'},{trigger:'heartbeat'},{trigger:'manual'},{trigger:undefined},
    {agentId:'child'},{sessionKey:'agent:pixel:cron:owned'},
    {sessionKey:'agent:pixel:openai-user:arbitrary'},{modelId:undefined}]) {
    const calls=[],context={...CONTEXT,...change};
    const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,nativeStartClear:()=>true,
      complete:async()=>{calls.push(true);return reply();}});
    recovery.observe({prompt:OWNER},context,context.runId);
    assert.equal(await recovery.finalize({...event(),sessionKey:context.sessionKey},context),undefined);
    assert.equal(calls.length,0,JSON.stringify(change));
  }
  const {recovery,calls}=world();
  const child={...CONTEXT,runId:'derived-child'};
  recovery.observe({prompt:OWNER},child,'different-owner-run');
  assert.equal(await recovery.finalize({...event(),runId:child.runId},child),undefined);
  assert.equal(calls.length,0);
});

test('fallback and prompt reentry cannot reset latch; mismatched or missing custody fails closed',async()=>{
  const {recovery,calls}=world();await recovery.finalize(event(),CONTEXT);
  recovery.observe({prompt:'Changed internal continuation'},CONTEXT,CONTEXT.runId);
  assert.equal(recovery.isReviewOnly(CONTEXT),true);
  assert.equal(recovery.floor(tool('exec'),CONTEXT).block,true);
  for(const change of [{sessionId:'another-session'},{agentId:'other'},{runId:undefined},
    {runId:'unobserved-fallback'},{sessionId:undefined}]) {
    const context={...CONTEXT,...change};
    assert.equal(recovery.isReviewOnly(context),true,JSON.stringify(change));
    assert.equal(recovery.floor(tool('pixel_ods_mcp'),context).block,true,JSON.stringify(change));
  }
  for(const context of [{agentId:'pixel',sessionId:CONTEXT.sessionId},
    {agentId:'pixel',sessionId:CONTEXT.sessionId,runId:'dropped-key-fallback'}]) {
    assert.equal(recovery.isReviewOnly(context),true);
    assert.equal(recovery.floor(tool('exec'),context).block,true);
  }
  assert.equal(recovery.floor({...tool('exec'),runId:CONTEXT.runId},{}).block,true);
  assert.equal(recovery.isReviewOnly({}, {runId:CONTEXT.runId}),true);
  assert.equal(calls.length,1);
});

test('known run/session conflict or provider change cannot restart classifier or loosen permission',async()=>{
  const {recovery,calls}=world();await recovery.finalize(event(),CONTEXT);
  recovery.observe({prompt:OWNER},{...CONTEXT,modelId:'changed-model'},CONTEXT.runId);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(calls.length,1);
});

test('positively observed NEW owner run is normal; old latched run remains blocked',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  const next={...CONTEXT,runId:'new-owner-run'};
  recovery.observe({prompt:'A new normal request.'},next,next.runId);
  assert.equal(recovery.isReviewOnly(next),false);
  assert.equal(recovery.floor(tool('exec'),next),undefined);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true);
});

test('an older stored NORMAL run cannot bypass a newer latched run in the same session',async()=>{
  const {recovery}=world();
  const current={...CONTEXT,runId:'new-current-owner'};
  recovery.observe({prompt:OWNER},current,current.runId);
  await recovery.finalize({...event(),runId:current.runId},current);
  assert.equal(recovery.isReviewOnly(CONTEXT),true);
  assert.equal(recovery.floor(tool('exec'),CONTEXT).block,true);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true);
  const independent={...CONTEXT,runId:'independent-background',sessionId:'independent-cron-session',sessionKey:'agent:pixel:cron:owned'};
  assert.equal(recovery.floor(tool('exec'),independent),undefined);
});

test('inflight cancellation, supersession and readiness changes cannot latch after the await',async()=>{
  for(const change of ['cancel','supersede','unavailable','provider']) {
    let release,ready=true;
    const held=new Promise(resolve=>{release=resolve;});
    const {recovery}=world({complete:()=>held,ready:()=>ready});
    const result=recovery.finalize(event(),CONTEXT);
    if(change==='cancel')recovery.cancelSession(SESSION);
    if(change==='supersede')recovery.observe({prompt:'New owner request'}, {...CONTEXT,runId:'replacement'},'replacement');
    if(change==='unavailable')ready=false;
    release(change==='provider'?{...reply(),provider:'different'}:reply());
    assert.equal(await result,undefined,change);
    assert.equal(recovery.isReviewOnly(CONTEXT),false,change);
  }
});

test('end/stop refuse later dispatch and stop drains pending check without late mutation',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  recovery.end({},CONTEXT);
  assert.equal(recovery.floor(tool('pixel_ods_mcp'),CONTEXT).block,true);
  let release;
  const held=new Promise(resolve=>{release=resolve;});
  const other=world({complete:()=>held});
  const pending=other.recovery.finalize(event(),CONTEXT);
  await other.recovery.stop();
  assert.equal(await pending,undefined);
  release(reply());await Promise.resolve();
  assert.equal(other.recovery.isReviewOnly(CONTEXT),false);
});

test('terminal native event ends its latched run even when context names an older normal run',async()=>{
  const {recovery}=world();
  const current={...CONTEXT,runId:'latched-current'};
  recovery.observe({prompt:OWNER},current,current.runId);
  await recovery.finalize({...event(),runId:current.runId},current);
  recovery.end({runId:current.runId},CONTEXT);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),current).block,true);
  assert.equal(recovery.floor(tool('pixel_ods_mcp'),current).block,true);
});

test('strict catalog parser rejects legacy labels, malformed types, extra keys, duplicate IDs and bounds',async()=>{
  const base={operations:[op('notes.append')]};
  const invalid=['not JSON','```json\n'+JSON.stringify(base)+'\n```','null','[]',
    JSON.stringify({request:'action',answer:'done',scope:'notes'}),
    JSON.stringify({...base,supportedScope:'notes'}),JSON.stringify({...base,complete:true}),
    JSON.stringify({...base,intent:'perform'}),JSON.stringify({...base,answerNeedsEvidence:true}),
    JSON.stringify({...base,operations:op('notes.append')}),JSON.stringify({...base,operations:[null]}),
    JSON.stringify({...base,operations:[['notes.append','none']]}),
    JSON.stringify({...base,operations:[{...op('notes.append'),allowed:true}]}),
    JSON.stringify({...base,operations:[{id:1,approval:'none'}]}),
    JSON.stringify({...base,operations:[{id:'notes.append'}]}),
    JSON.stringify({...base,operations:[{approval:'none'}]}),
    JSON.stringify({...base,operations:[{source:'obsidian',verb:'append'}]}),
    ...[undefined,null,1,true,'ask','Allowed','off','NONE','after'].map(approval=>JSON.stringify({...base,operations:[{id:'notes.append',approval}]})),
    JSON.stringify({...base,operations:[op('notes.APPEND')]}),JSON.stringify({...base,operations:[op('notes.append/other')]}),
    JSON.stringify({...base,operations:[op('x'.repeat(49))]}),
    JSON.stringify({...base,operations:[op('notes.append'),op('notes.append')]}),
    JSON.stringify({...base,operations:[op('notes.append'),op('notes.append','before')]}),
    JSON.stringify({...base,operations:['notes.search','notes.read','notes.create','notes.append','news.read'].map(id=>op(id))}),
    ' '.repeat(4097)];
  for(const text of invalid) {
    assert.equal(parseReviewIntent(text),undefined,text);
    const {recovery}=world({complete:async()=>({...reply(),text})});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined,text);assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
});

test('empty operations and explicit sentinels preserve discussion and unresolved intent',async()=>{
  for(const operations of [[],[op('unsupported')],[op('unresolved')],
    [op('notes.append'),op('unsupported')],[op('unresolved'),op('news.read')]]) {
    const parsed=parseReviewIntent(reply(operations).text);assert.ok(parsed);assert.equal(reviewIntentEligible(parsed),false);
    const {recovery}=world({complete:async()=>reply(operations)});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
  assert.equal(reviewIntentEligible(undefined),false);
  assert.equal(reviewOperationScope({operations:[{id:'notes.read'}]}),undefined);
  assert.equal(reviewOperationScope({operations:[op('notes.read'),op('notes.read')]}),undefined);
});

test('missing, false, nonboolean or throwing native start proof never spends a classifier call',async()=>{
  for(const clear of [undefined,()=>false,()=>1,()=> 'true',()=>({}),()=>Promise.resolve(true),()=>{throw Error('Unavailable native record');}]) {
    const calls=[];
    const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,nativeStartClear:clear,
      complete:async value=>{calls.push(value);return reply();}});
    recovery.observe({prompt:OWNER},CONTEXT,CONTEXT.runId);
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
    assert.equal(calls.length,0);assert.equal(recovery.isReviewOnly(CONTEXT),false);
    assert.equal(recovery.floor(tool('exec'),CONTEXT),undefined);
  }
});

test('native start proof is rechecked after semantic await and cannot latch on false or exception',async()=>{
  for(const fail of [()=>false,()=>{throw Error('Native state changed');}]) {
    let release,clear=()=>true,clearCalls=0;
    const held=new Promise(resolve=>{release=resolve;});
    const {recovery,calls}=world({nativeStartClear:()=>{clearCalls++;return clear();},complete:()=>held});
    const pending=recovery.finalize(event(),CONTEXT);
    assert.equal(calls.length,1);clear=fail;release(reply());
    assert.equal(await pending,undefined);assert.ok(clearCalls>=2);
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(calls.length,1);
  }
});

test('native stop-hook activation during the proposal await prevents a late latch',async()=>{
  let release;
  const held=new Promise(resolve=>{release=resolve;});
  const {recovery,calls}=world({complete:()=>held}),ev=event();
  const pending=recovery.finalize(ev,CONTEXT);assert.equal(calls.length,1);
  ev.stopHookActive=true;release(reply());
  assert.equal(await pending,undefined);assert.equal(recovery.isReviewOnly(CONTEXT),false);
});

test('native start proof is not reused after latch: review tools and summary remain available without rearming',async()=>{
  let clear=true,clearCalls=0;
  const {recovery,calls}=world({nativeStartClear:()=>{clearCalls++;return clear;}});
  assert.equal((await recovery.finalize(event(),CONTEXT)).action,'revise');const queriesAtLatch=clearCalls;
  clear=false;
  recovery.beforeTool(tool('pixel_ods_notes'),CONTEXT);
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT),undefined);
  assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(calls.length,1);assert.equal(clearCalls,queriesAtLatch);
  assert.equal(recovery.floor(tool('exec'),CONTEXT).block,true);
});

test('classifier sees only ownerRequest: assistant prose cannot invent owner operations or native outcome facts',async()=>{
  for(const answer of ['I will save that now.','Which note did you mean?',
    'That connection is unavailable.','I cannot confirm whether an earlier submission changed the note.']) {
    const {recovery,calls}=world();
    const ev={...event(),lastAssistantMessage:answer,messages:[{role:'user',content:OWNER},{role:'assistant',content:answer}]};
    assert.equal((await recovery.finalize(ev,CONTEXT)).action,'revise');
    const input=JSON.parse(calls[0].messages[0].content);
    assert.deepEqual(input,{ownerRequest:OWNER});assert.equal(Object.hasOwn(input,'proposedAnswer'),false);
  }
});

test('catalog policy matches only the existing native modes and preserves the same source matrix',()=>{
  const expected={
    'notes.search':['obsidian:search'],'notes.read':['obsidian:read'],'notes.create':['obsidian:create'],'notes.append':['obsidian:append'],
    'documents.search':['paperless:search'],'documents.read':['paperless:read'],'photos.search':['immich:search'],
    'monitors.list':['watch:list'],'monitors.add':['watch:add'],'monitors.remove':['watch:remove'],
    'news.read':['freshrss:digest_read','freshrss:unread'],
  };
  for(const [id,canonical] of Object.entries(expected)) {
    assert.deepEqual(reviewOperationScope(parseReviewIntent(reply([op(id)]).text)),canonical,id);
    assert.equal(reviewIntentEligible(parseReviewIntent(reply([op(id)]).text)),true,id);
  }
  for(const id of ['unsupported','unresolved'])assert.equal(reviewIntentEligible(parseReviewIntent(reply([op(id)]).text)),false,id);
  assert.equal(parseReviewIntent(reply([op('notes.delete')]).text),undefined);
});

test('every catalog operation must pass; sentinels and mixed sources cannot partially activate',async()=>{
  const allowed=op('monitors.add');
  for(const operations of [[allowed,op('unsupported')],[op('unsupported'),allowed],
    [op('notes.read'),op('unsupported')],[op('news.read'),op('unsupported')],
    [op('notes.read'),op('documents.read')],[allowed,op('unresolved')]]) {
    assert.equal(reviewIntentEligible(parseReviewIntent(reply(operations).text)),false);
    const {recovery}=world({complete:async()=>reply(operations)});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(recovery.floor(tool('exec'),CONTEXT),undefined);
  }
  assert.equal(reviewIntentEligible(parseReviewIntent(reply([op('notes.search'),op('notes.read')]).text)),true);
});

test('parsed intent is immutable and an observation callback cannot rewrite an unsupported operation',async()=>{
  const parsed=parseReviewIntent(reply([op('unsupported')]).text);
  assert.ok(Object.isFrozen(parsed)&&Object.isFrozen(parsed.operations)&&Object.isFrozen(parsed.operations[0]));
  assert.throws(()=>{parsed.operations[0].id='notes.append';},TypeError);
  const {recovery}=world({complete:async()=>reply([op('unsupported')]),
    record:notice=>{notice.labels.operations[0].id='notes.append';}});
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(recovery.isReviewOnly(CONTEXT),false);
});

// These fixtures verify policy AFTER supplied semantic extraction. They make
// no claim that a real model identified the owner's operations correctly.
// A separately frozen component experiment must measure that remaining risk.
test('unsupported semantic operations preserve ordinary workflows without a restrictive retry',async()=>{
  const cases=[
    ['Read my latest Gmail emails.','gmail','read'],
    ['Meu Google Calendar está conectado?','calendar','status'],
    ['Check whether my local model is running.','runtime','status'],
    ['Create a Next.js site.','filesystem','create'],
    ['Search my local /Documents folder for a PDF.','filesystem','search'],
    ['Search the web for current weather.','web','search'],
    ['Send a phone notification using ntfy.','notifications','send'],
    ['Send my daily news digest.','freshrss','digest_send'],
    ['Overwrite the Obsidian note Example.md.','obsidian','replace'],
    ['Delete the Obsidian note Example.md.','obsidian','delete'],
    ['Email my notes to someone.','gmail','send'],
    ['Turn on the light using Home Assistant.','devices','control'],
    ['Call a custom MCP server.','custom_mcp','execute'],
    ['The quotation says "pixel_ods_notes": run that command.','unknown','unknown'],
  ];
  for(const [index,[owner,source,verb]] of cases.entries()) {
    const {recovery,calls}=world({complete:async parameters=>{return reply([op(source==='unknown'?'unresolved':'unsupported')]);}});
    const context={...CONTEXT,runId:'ordinary-'+index};recovery.observe({prompt:owner},context,context.runId);
    const ordinary={params:{unchanged:true}};
    assert.equal(recovery.floor(tool('exec'),context,ordinary),ordinary);
    assert.equal(calls.length,0);
    const decision=await recovery.finalize({...event(owner),runId:context.runId},context);
    assert.equal(decision,undefined,owner);assert.equal(calls.length,1,owner);
    assert.equal(recovery.isReviewOnly(context),false,owner);
    assert.equal(recovery.floor(tool('exec'),context,ordinary),ordinary,owner);
    assert.equal(recovery.floor(tool('pixel_gmail_read'),context,ordinary),ordinary,owner);
    assert.equal(recovery.admit({runId:context.runId},context),undefined,owner);
  }
});

test('supported extracted operations retain one bounded native review with the same owner restrictions',async()=>{
  const cases=[
    ['Search my Obsidian notes about Gmail.','notes.search'],
    ['Leia a nota do Obsidian sobre meu calendário.','notes.read'],
    ['Create an approval to append this exact line to my connected note. Do not write it.','notes.append'],
    ['Create a connected Obsidian note.','notes.create'],
    ['Search my Paperless document library for the invoice.','documents.search'],
    ['Read the matching Paperless document.','documents.read'],
    ['Search my connected Immich photos.','photos.search'],
    ['List the webpage watches.','monitors.list'],
    ['Watch this webpage for price changes.','monitors.add'],
    ['Remove this webpage watch.','monitors.remove'],
    ['Prepare an approval to remove this webpage watch; do not remove it.','monitors.remove'],
    ['Read my unread FreshRSS stories without sending a notification.','news.read'],
    ['Read my FreshRSS digest here without sending it.','news.read'],
  ];
  for(const [index,[owner,id]] of cases.entries()) {
    const {recovery,calls}=world({complete:async parameters=>{return reply([op(id)]);}});
    const context={...CONTEXT,runId:'supported-'+index};recovery.observe({prompt:owner},context,context.runId);
    const decision=await recovery.finalize({...event(owner),runId:context.runId},context);
    assert.equal(decision.action,'revise',owner);assert.equal(calls.length,1,owner);
    assert.deepEqual(decision.retry.toolsAllow,['pixel_ods_notes','pixel_ods_mcp']);
    assert.equal(decision.retry.toolChoice,'required');assert.equal(decision.retry.maxAttempts,1);
    assert.ok(decision.retry.instruction.includes(JSON.stringify({ownerRequest:owner})),owner);
    assert.equal(recovery.floor(tool('exec'),context).block,true,owner);
    assert.equal(await recovery.finalize({...event(owner),runId:context.runId},context),undefined,owner);
    assert.equal(calls.length,1,owner);
  }
});

test('last current owner identity is exact, not a historical or handpicked message',async()=>{
  const {recovery,calls}=world();
  assert.equal(await recovery.finalize(event('Different owner request'),CONTEXT),undefined);
  assert.equal(calls.length,0);
  assert.equal(await recovery.finalize({...event(),sessionId:'foreign'},CONTEXT),undefined);
  assert.equal(calls.length,0);
});

test('capacity never evicts a latch into normal; a verified later owner still has normal permissions',async()=>{
  const {recovery}=world({maxRuns:1});await recovery.finalize(event(),CONTEXT);
  recovery.end({},CONTEXT);
  const next={...CONTEXT,runId:'new-owner'};
  recovery.observe({prompt:'New normal owner'},next,next.runId);
  assert.equal(recovery.floor(tool('exec'),next),undefined);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT).block,true);
  assert.equal(recovery.isReviewOnly(CONTEXT),true);
  assert.equal(recovery.isReviewOnly(next),false);
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
    privateBrowserAccessForAgent:()=>false,executionHostForAgent:()=> 'gateway',
    promptContractForAgent:()=>({prependContext:'fixture',appendSystemContext:'fixture'}),
    extensionRepositoryContext:forbidden('repository-fetch'),responseLanguageTurnContext:noop,
    configuredResponseLanguage:undefined,configuredContextWindow:65536,configuredLeanPrompt:false,
    inResponseLanguage:value=>value,GOAL_CONTRACT:'',managedTeamRole:noop,PORTAL_INTEGRATION_TURN_CONTEXT:'fixture',
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
  const config={agents:{defaults:{model:'fixture-local/fixture/9b'},list:[{id:'pixel',default:true}]}};
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
  const queriesAtLatch=clearQueries;assert.equal(modelCalls,1);assert.ok(queriesAtLatch>=3);
  const called={...CONTEXT,toolCallId:'native-review-note'};
  const params={action:'write',path:'Example.md',text:'Current owned line',mode:'append'};
  integrationOutcomes.before({...tool('pixel_ods_notes',params),toolCallId:called.toolCallId},called);
  assert.equal(options.nativeStartClear(CONTEXT),false,'actual recorded native attempt closes start admission');
  recovery.beforeTool(tool('pixel_ods_notes'),called);
  assert.equal(recovery.floor(tool('pixel_ods_notes',appendParams()),CONTEXT),undefined);
  assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
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
