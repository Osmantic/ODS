import test from 'node:test';
import assert from 'node:assert/strict';
import {createIntegrationTools,integrationRequest,reviewIntegrationCandidate,nativeReviewOperation,NATIVE_REVIEW_ROLE} from '../plugin/integrations.mjs';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {createIntegrationReviewRecovery} from '../plugin/integration-review-recovery.mjs';

const context={agentId:'pixel',runId:'prepare-owner',sessionId:'prepare-session',
  sessionKey:'agent:pixel:openai-user:ods-'+'d'.repeat(64),trigger:'user',modelProviderId:'ods-gateway',modelId:'ods/current'};
const note={action:'prepare',path:'Owned.md',text:'Exact current text',mode:'append'};
const watch={action:'prepare',server:'app:watch',tool:'add',arguments:{url:'https://invented.example/page'}};
const receipt={ok:true,status:'approval-required',approvalId:'invented-request',summary:'Exact preparation',
  preparationOnly:true,actionExecuted:false,approvalConsumed:false};
const tool=(tools,name)=>tools.find(value=>value.name===name);

test('prepare has distinct RPC names, preserves arguments, and does not add tools or change normal routes',async()=>{
  const sent=[];
  const tools=createIntegrationTools({submit:async payload=>{sent.push(payload);return receipt;}});
  assert.deepEqual(tools.map(value=>value.name),['pixel_ods_notes','pixel_ods_notify','pixel_ods_mcp']);
  assert.equal((await tool(tools,'pixel_ods_notes').execute('note',note)).details.preparationOnly,true);
  assert.equal((await tool(tools,'pixel_ods_mcp').execute('watch',watch)).details.status,'approval-required');
  assert.deepEqual(sent,[{action:'notes-prepare',path:note.path,text:note.text,mode:'append'},
    {action:'mcp-prepare',server:watch.server,tool:watch.tool,arguments:watch.arguments}]);
  assert.equal(integrationRequest('pixel_ods_notes',{...note,action:'write'}).action,'notes-write');
  assert.equal(integrationRequest('pixel_ods_mcp',{...watch,action:'call'}).action,'mcp-call');
  const ready=createIntegrationTools({submit:async()=>({...receipt,status:'approval-ready'})});
  const result=await tool(ready,'pixel_ods_notes').execute('ready',note);
  assert.equal(result.details.status,'approval-ready');
  assert.match(result.content[0].text,/did not run it or use the approval/);
  const legacyCalls=[];
  const legacy=createIntegrationTools({submit:async payload=>{legacyCalls.push(payload);return {ok:false,error:'invalid-request',message:'Unknown integration action.'};}});
  assert.equal((await tool(legacy,'pixel_ods_notes').execute('legacy',note)).details.status,'refused');
  assert.deepEqual(legacyCalls.map(payload=>payload.action),['notes-prepare']);
});

test('preparation refuses dropped raw constraints, invalid modes and malformed receipt effects',async()=>{
  let submitted=0;
  const tools=createIntegrationTools({submit:async()=>{submitted++;return receipt;}});
  for(const value of [{...note,mode:'overwrite'},{...note,date:'ignored?'},{...note,path:3}])
    assert.equal((await tool(tools,'pixel_ods_notes').execute('bad',value)).details.status,'invalid');
  for(const value of [{...watch,arguments:[]},{...watch,arguments:null},{...watch,date:'ignored?'}])
    assert.equal((await tool(tools,'pixel_ods_mcp').execute('bad',value)).details.status,'invalid');
  assert.equal(submitted,0);
  for(const reply of [{ok:true,written:'Owned.md',mode:'append'},
    {...receipt,actionExecuted:true},{...receipt,approvalConsumed:true},{...receipt,preparationOnly:undefined},
    {...receipt,status:'done'},{...receipt,approvalId:undefined}]) {
    const invalid=createIntegrationTools({submit:async()=>reply});
    assert.equal((await tool(invalid,'pixel_ods_notes').execute('invalid-receipt',note)).details.code,'preparation-result-invalid');
  }
});

test('normal preparation needs the same current one-use raw/payload ticket, with no hookless fallback',async()=>{
  const outcomes=createIntegrationOutcomes();outcomes.begin({},context,context.runId);
  let submissions=0;
  const tools=createIntegrationTools({context,outcomes,submit:async()=>{submissions++;return receipt;}});
  assert.equal((await tool(tools,'pixel_ods_notes').execute('unbound',note)).details.code,'integration-call-unbound');
  outcomes.before({toolName:'pixel_ods_notes',toolCallId:'bound',params:note},{...context,toolCallId:'bound'});
  assert.equal((await tool(tools,'pixel_ods_notes').execute('bound',note)).details.status,'approval-required');
  assert.equal((await tool(tools,'pixel_ods_notes').execute('bound',note)).details.code,'integration-call-unbound');
  assert.equal(submissions,1);
  const readyOutcomes=createIntegrationOutcomes();readyOutcomes.begin({},context,context.runId);
  readyOutcomes.before({toolName:'pixel_ods_notes',toolCallId:'ready',params:note},{...context,toolCallId:'ready'});
  const readyTools=createIntegrationTools({context,outcomes:readyOutcomes,submit:async()=>({...receipt,status:'approval-ready'})});
  assert.equal((await tool(readyTools,'pixel_ods_notes').execute('ready',note)).details.status,'approval-ready');
  assert.match(readyOutcomes.projection(context.runId),/This preparation did not run it or use the approval/);
  assert.doesNotMatch(readyOutcomes.projection(context.runId),/review-only recovery/);
});

test('native preparation retains exact role, opaque permit, one proposal family and current Ask-only host control',async()=>{
  const outcomes=createIntegrationOutcomes();outcomes.begin({},context,context.runId);
  const owner='Prepare the explicitly requested integration action without executing it.';
  const source=createIntegrationReviewRecovery({ready:()=>true,eligible:()=>true,
    nativeStartClear:state=>outcomes.reviewStartClear({agentId:'pixel',runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey})});
  source.observe({prompt:owner},context,context.runId);
  assert.equal((await source.finalize({...context,nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
    lastAssistantMessage:'A provisional reply.',messages:[{role:'user',content:owner},{role:'assistant',content:'A provisional reply.'}]},context)).action,'revise');
  assert.equal(source.admit({...context,nativeRetryToolsAllowState:Object.freeze({version:1,ownerRunId:context.runId,active:true,
    toolsAllow:Object.freeze(['pixel_ods_notes','pixel_ods_mcp'])})},context),undefined);
  const scoped={...context,toolCallId:'prepare-family'};
  const candidate=await outcomes.prepareReviewCandidate({toolName:'pixel_ods_notes',toolCallId:scoped.toolCallId,params:note},scoped);
  const permit=outcomes.checkNativeCandidate(candidate,value=>source.admitNativeCandidate(value,scoped));
  assert.equal(permit.admission,'native-review-policy-v1');
  assert.equal(outcomes.commitNativeCandidate(candidate,permit),undefined);
  const sent=[];
  const tools=createIntegrationTools({context,outcomes,requireBound:ctx=>source.isReviewOnly(ctx),submit:async payload=>{
    sent.push(payload);return {...receipt,recoveryReviewOnly:true};}});
  assert.equal((await tool(tools,'pixel_ods_notes').execute(scoped.toolCallId,note)).details.status,'approval-required');
  assert.equal(sent[0].action,'notes-prepare');assert.equal(sent[0].recoveryReviewOnly,true);
  const next={...context,toolCallId:'second-proposal'};
  const changed=await outcomes.prepareReviewCandidate({toolName:'pixel_ods_mcp',toolCallId:next.toolCallId,params:watch},next);
  const second=outcomes.checkNativeCandidate(changed,value=>source.admitNativeCandidate(value,next));
  assert.equal(outcomes.commitNativeCandidate(changed,second).block,true);
  assert.equal(sent.length,1);
  assert.equal(reviewIntegrationCandidate('pixel_ods_notes',note,context).maximumEffect,'approval-proposal');
  assert.equal(reviewIntegrationCandidate('pixel_ods_mcp',watch,context).maximumEffect,'approval-proposal');
  for(const value of [{...watch,server:'app:news',tool:'digest',arguments:{}},
    {...watch,server:'opaque',tool:'change',arguments:{}},
    {...watch,arguments:{...watch.arguments,date:'ignored?'}}])
    assert.throws(()=>reviewIntegrationCandidate('pixel_ods_mcp',value,context));
  assert.equal(nativeReviewOperation({action:'mcp-prepare',server:'app:news',tool:'digest'}),undefined);
  assert.equal(NATIVE_REVIEW_ROLE.operations.length,13);
});
