import test from 'node:test';
import assert from 'node:assert/strict';
import {createIntegrationReviewRecovery,REVIEW_LABEL_SYSTEM} from '../plugin/integration-review-recovery.mjs';

const SESSION='agent:pixel:openai-user:ods-'+ 'a'.repeat(64);
const CONTEXT={agentId:'pixel',runId:'current-run',sessionId:'owned-session',sessionKey:SESSION,
  trigger:'user',modelProviderId:'fixture-local',modelId:'fixture/9b'};
const OWNER='Create an approval request to append Current owned line to Example.md. Do not write it.';
const ANSWER='You need to approve the new request in Settings.';
const event=(owner=OWNER)=>({runId:CONTEXT.runId,sessionId:CONTEXT.sessionId,sessionKey:SESSION,
  messages:[{role:'user',content:owner},{role:'assistant',content:ANSWER}],lastAssistantMessage:ANSWER});
const reply=(request='action',answer='approval_needed')=>({agentId:'pixel',provider:'fixture-local',model:'fixture/9b',
  text:JSON.stringify({request,answer})});
function world(options={}) {
  const calls=[];
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,
    complete:async params=>{calls.push(params);return reply();},...options});
  recovery.observe({prompt:OWNER},CONTEXT,CONTEXT.runId);
  return {recovery,calls};
}
const tool=(name,params={})=>({toolName:name,params});

test('review latches synchronously before ONE stock native revise; only six supported native params',async()=>{
  const {recovery,calls}=world();
  const result=await recovery.finalize(event(),CONTEXT);
  assert.equal(result.action,'revise');assert.equal(result.retry.maxAttempts,1);
  assert.equal(recovery.isReviewOnly(CONTEXT),true);
  assert.deepEqual(Object.keys(calls[0]).sort(),['maxTokens','messages','purpose','signal','systemPrompt','temperature']);
  assert.equal(calls[0].systemPrompt,REVIEW_LABEL_SYSTEM);
  assert.equal(JSON.parse(calls[0].messages[0].content).ownerRequest,OWNER);
  assert.match(result.retry.instruction,/Current owner request/);
  assert.equal(await recovery.finalize(event(),CONTEXT),undefined);
  assert.equal(calls.length,1);
  assert.deepEqual(await recovery.finalize(event(),CONTEXT,{action:'revise'}),{action:'continue'});
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
    const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,
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

test('closed labels and honest/nonaction controls cannot request review',async()=>{
  for(const text of ['not JSON','{"request":"action","answer":"done","extra":true}',
    '{"request":"action","answer":"unknown"}',JSON.stringify({request:'draft',answer:'content'}),
    JSON.stringify({request:'explanation',answer:'content'}),JSON.stringify({request:'conversation',answer:'content'}),
    JSON.stringify({request:'action',answer:'unavailable'}),JSON.stringify({request:'action',answer:'question'})]) {
    const {recovery}=world({complete:async()=>({...reply(),text})});
    assert.equal(await recovery.finalize(event(),CONTEXT),undefined,text);
    assert.equal(recovery.isReviewOnly(CONTEXT),false);
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
