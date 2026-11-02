import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createIntegrationReviewRecovery,REVIEW_LABEL_SYSTEM,parseReviewIntent,reviewIntentEligible} from '../plugin/integration-review-recovery.mjs';
import {defaultAgentId} from '../plugin/stop-synthesis.mjs';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';

const SESSION='agent:pixel:openai-user:ods-'+ 'a'.repeat(64);
const CONTEXT={agentId:'pixel',runId:'current-run',sessionId:'owned-session',sessionKey:SESSION,
  trigger:'user',modelProviderId:'fixture-local',modelId:'fixture/9b'};
const OWNER='Create an approval request to append Current owned line to Example.md. Do not write it.';
const ANSWER='You need to approve the new request in Settings.';
const event=(owner=OWNER)=>({runId:CONTEXT.runId,sessionId:CONTEXT.sessionId,sessionKey:SESSION,
  nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
  messages:[{role:'user',content:owner},{role:'assistant',content:ANSWER}],lastAssistantMessage:ANSWER});
const reply=(operations=[{source:'obsidian',verb:'append'}])=>({agentId:'pixel',provider:'fixture-local',model:'fixture/9b',
  text:JSON.stringify({operations})});
function world(options={}) {
  const calls=[];
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,nativeStartClear:()=>true,
    complete:async params=>{calls.push(params);return reply();},...options});
  recovery.observe({prompt:OWNER},CONTEXT,CONTEXT.runId);
  return {recovery,calls};
}
const tool=(name,params={})=>({toolName:name,params});
const nativeScope=(context=CONTEXT,changes={})=>Object.freeze({version:1,active:true,
  ownerRunId:context.runId,toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_mcp']),...changes});
const nativeChoice=(context=CONTEXT,changes={})=>Object.freeze({version:1,ownerRunId:context.runId,
  mode:'required-first',phase:'armed',...changes});
const admission=(changes={})=>({...event(),nativeRetryToolsAllowState:nativeScope(),nativeRetryToolChoiceState:nativeChoice(),...changes});

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

test('native floor permits only two integration adapters and harmless metadata discovery',async()=>{
  const {recovery}=world();await recovery.finalize(event(),CONTEXT);
  for(const name of ['pixel_ods_notes','pixel_ods_mcp','tool_search','tool_describe'])
    assert.equal(recovery.floor(tool(name),CONTEXT),undefined,name);
  for(const name of ['pixel_ods_notes','pixel_ods_mcp'])
    assert.equal(recovery.floor(tool('tool_call',{id:`openclaw:pixel-ods:${name}`,args:{action:'write'}}),CONTEXT),undefined,name);
  for(const name of ['exec','read','write','apply_patch','browser','web_fetch','pixel_ods_ops','pixel_ods_notify',
    'cron','sessions_spawn','tool_search_code','pixel_ods_extensions'])
    assert.equal(recovery.floor(tool(name),CONTEXT).block,true,name);
  for(const id of ['pixel_ods_notes','openclaw:other:pixel_ods_notes','openclaw:pixel-ods:pixel_ods_notify',
    'openclaw:core:exec','openclaw:pixel-ods:pixel_ods_ops'])
    assert.equal(recovery.floor(tool('tool_call',{id,args:{}}),CONTEXT).block,true,id);
  const existing={block:true,blockReason:'Unknown submitted outcome'};
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT,existing),existing);
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

test('strict operations-only parser rejects legacy labels, malformed types, extra keys, duplicates and bounds',async()=>{
  const base={operations:[{source:'obsidian',verb:'append'}]};
  const invalid=['not JSON','```json\n'+JSON.stringify(base)+'\n```','null','[]',
    JSON.stringify({request:'action',answer:'done',scope:'notes'}),
    JSON.stringify({...base,supportedScope:'notes'}),JSON.stringify({...base,complete:true}),
    JSON.stringify({...base,intent:'perform'}),JSON.stringify({...base,answerNeedsEvidence:true}),
    JSON.stringify({...base,operations:{source:'obsidian',verb:'append'}}),
    JSON.stringify({...base,operations:[null]}),JSON.stringify({...base,operations:[['obsidian','append']]}),
    JSON.stringify({...base,operations:[{source:'obsidian',verb:'append',allowed:true}]}),
    JSON.stringify({...base,operations:[{source:1,verb:'append'}]}),
    JSON.stringify({...base,operations:[{source:'obsidian',verb:'APPEND'}]}),
    JSON.stringify({...base,operations:[{source:'obsidian/other',verb:'append'}]}),
    JSON.stringify({...base,operations:[{source:'x'.repeat(49),verb:'append'}]}),
    JSON.stringify({...base,operations:[...base.operations,...base.operations]}),
    JSON.stringify({...base,operations:Array.from({length:5},(_,index)=>({source:'obsidian',verb:'unknown'+index}))}),
    ' '.repeat(4097)];
  for(const text of invalid) {
    assert.equal(parseReviewIntent(text),undefined,text);
    const {recovery}=world({complete:async()=>({...reply(),text})});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined,text);
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
});

test('empty and unknown operations preserve discussion and unresolved intent without policy labels',async()=>{
  for(const operations of [[],[{source:'unknown',verb:'unknown'}],[{source:'unknown',verb:'read'}],
    [{source:'obsidian',verb:'unknown'}]]) {
    const parsed=parseReviewIntent(reply(operations).text);
    assert.ok(parsed);assert.equal(reviewIntentEligible(parsed),false);
    const {recovery}=world({complete:async()=>reply(operations)});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
  }
  assert.equal(reviewIntentEligible(undefined),false);
  assert.equal(reviewIntentEligible({}),false);
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
    let release,clearCalls=0;
    const held=new Promise(resolve=>{release=resolve;});
    const {recovery,calls}=world({nativeStartClear:()=>++clearCalls===1?true:fail(),
      complete:params=>{calls.push(params);return held;}});
    const pending=recovery.finalize(event(),CONTEXT);
    assert.equal(calls.length,1);release(reply());
    assert.equal(await pending,undefined);assert.equal(clearCalls,2);
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);assert.equal(calls.length,1);
  }
});

test('native start proof is not reused after latch: review tools and summary remain available without rearming',async()=>{
  let clear=true,clearCalls=0;
  const {recovery,calls}=world({nativeStartClear:()=>{clearCalls++;return clear;}});
  assert.equal((await recovery.finalize(event(),CONTEXT)).action,'revise');assert.equal(clearCalls,2);
  clear=false;
  recovery.beforeTool(tool('pixel_ods_notes'),CONTEXT);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT),undefined);
  assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(calls.length,1);assert.equal(clearCalls,2);
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

test('operation policy matches only the existing notes modes, builtin reads and watch Ask changes',()=>{
  const allowed={obsidian:['search','read','create','append'],paperless:['search','read'],immich:['search'],
    watch:['list','add','remove','delete'],freshrss:['unread','digest_read','read'],web:['monitor_changes']};
  for(const [source,verbs] of Object.entries(allowed)) {
    for(const verb of verbs)assert.equal(reviewIntentEligible(parseReviewIntent(reply([{source,verb}]).text)),true,source+':'+verb);
    for(const verb of ['write','update','overwrite','replace','send','digest_send','schedule','configure','execute','status','unknown'])
      assert.equal(reviewIntentEligible(parseReviewIntent(reply([{source,verb}]).text)),false,source+':'+verb);
  }
  for(const source of ['gmail','calendar','notifications','filesystem','runtime','account','schedule','devices','custom_mcp','future_application','unknown'])
    for(const verb of ['read','search','create','append','send','status','unknown']) {
      const parsed=parseReviewIntent(reply([{source,verb}]).text);
      assert.ok(parsed,'unknown operations remain semantically representable');
      assert.equal(reviewIntentEligible(parsed),false,source+':'+verb);
    }
  for(const [source,verb] of [['web','watch'],['web','read'],['web','execute'],['obsidian','delete'],['notes','write'],['notes','update']])
    assert.equal(reviewIntentEligible(parseReviewIntent(reply([{source,verb}]).text)),false,source+':'+verb);
});

test('every operation must pass; unsupported companions and mixed sources cannot partially activate',async()=>{
  const allowed={source:'watch',verb:'add'};
  for(const operations of [
    [allowed,{source:'notifications',verb:'send'}],[{source:'notifications',verb:'send'},allowed],
    [{source:'obsidian',verb:'read'},{source:'obsidian',verb:'delete'}],
    [{source:'obsidian',verb:'read'},{source:'obsidian',verb:'replace'}],
    [{source:'freshrss',verb:'digest_read'},{source:'freshrss',verb:'digest_send'}],
    [{source:'obsidian',verb:'read'},{source:'paperless',verb:'read'}],
    [allowed,{source:'watch',verb:'unknown'}],
  ]) {
    assert.equal(reviewIntentEligible(parseReviewIntent(reply(operations).text)),false);
    const {recovery}=world({complete:async()=>reply(operations)});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
    assert.equal(recovery.floor(tool('exec'),CONTEXT),undefined);
  }
  assert.equal(reviewIntentEligible(parseReviewIntent(reply([{source:'obsidian',verb:'search'},{source:'obsidian',verb:'read'}]).text)),true);
});

test('parsed intent is immutable and an observation callback cannot rewrite an unsupported operation',async()=>{
  const parsed=parseReviewIntent(reply([{source:'obsidian',verb:'delete'}]).text);
  assert.ok(Object.isFrozen(parsed)&&Object.isFrozen(parsed.operations)&&Object.isFrozen(parsed.operations[0]));
  assert.throws(()=>{parsed.operations[0].verb='append';},TypeError);
  const {recovery}=world({complete:async()=>reply([{source:'obsidian',verb:'delete'}]),
    record:notice=>{notice.labels.operations[0].verb='append';}});
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
    const {recovery,calls}=world({complete:async parameters=>{calls.push(parameters);return reply([{source,verb}]);}});
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
    ['Search my Obsidian notes about Gmail.','obsidian','search'],
    ['Leia a nota do Obsidian sobre meu calendário.','obsidian','read'],
    ['Create an approval to append this exact line to my connected note. Do not write it.','obsidian','append'],
    ['Create a connected Obsidian note.','obsidian','create'],
    ['Search my Paperless document library for the invoice.','paperless','search'],
    ['Read the matching Paperless document.','paperless','read'],
    ['Search my connected Immich photos.','immich','search'],
    ['List the webpage watches.','watch','list'],
    ['Watch this webpage for price changes.','watch','add'],
    ['Remove this webpage watch.','watch','remove'],
    ['Prepare an approval to remove this webpage watch; do not remove it.','watch','remove'],
    ['Read my unread FreshRSS stories without sending a notification.','freshrss','unread'],
    ['Read my FreshRSS digest here without sending it.','freshrss','digest_read'],
  ];
  for(const [index,[owner,source,verb]] of cases.entries()) {
    const {recovery,calls}=world({complete:async parameters=>{calls.push(parameters);return reply([{source,verb}]);}});
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
  assert.equal(clearQueries,2);assert.equal(modelCalls,1);
  const called={...CONTEXT,toolCallId:'native-review-note'};
  const params={action:'write',path:'Example.md',text:'Current owned line',mode:'append'};
  integrationOutcomes.before({...tool('pixel_ods_notes',params),toolCallId:called.toolCallId},called);
  assert.equal(options.nativeStartClear(CONTEXT),false,'actual recorded native attempt closes start admission');
  recovery.beforeTool(tool('pixel_ods_notes'),called);
  assert.equal(recovery.floor(tool('pixel_ods_notes'),CONTEXT),undefined);
  assert.equal(recovery.admit(admission({nativeRetryToolChoiceState:nativeChoice(CONTEXT,{phase:'tool-started'})}),CONTEXT),undefined);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(clearQueries,2,'post-latch summary must not recheck a now-nonempty start record');
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
