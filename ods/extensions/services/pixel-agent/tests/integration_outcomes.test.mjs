import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createIntegrationOutcomes, MAX_INTEGRATION_NOTICE} from '../plugin/integration-outcomes.mjs';
import {createIntegrationTools, integrationRequest} from '../plugin/integrations.mjs';
import {createIntegrationReviewRecovery} from '../plugin/integration-review-recovery.mjs';
import {parseVerificationResponse, applyVerificationToCompletion} from '../host/pixel_ingress.mjs';

const runId = 'chatcmpl_11111111-1111-4111-8111-111111111111';
const context = {agentId:'pixel', runId, sessionId:'session-1', sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),trigger:'user'};
const factory = ({runId: _run, ...rest} = context) => rest;
const params = {action:'call', server:'app:watch', tool:'add', arguments:{url:'https://example.com/private?token=secret'}};
const pending = {ok:true, status:'approval-required', approvalId:'approved_only_by_owner', summary:'model-facing detail'};
const event = (id, value=params, name='pixel_ods_mcp') => ({toolName:name, toolCallId:id, params:value});
function setup(options) {
  const outcomes = createIntegrationOutcomes(options);
  outcomes.begin({}, context);
  return outcomes;
}
function reserve(outcomes, id, value=params, ctx=context, name='pixel_ods_mcp') {
  outcomes.before(event(id,value,name), {...ctx,toolCallId:id});
  return outcomes.take(id, integrationRequest(name,value,ctx), factory(ctx));
}
async function execute(outcomes, id, value=params, reply=pending, ctx=context, submit) {
  outcomes.before(event(id,value), {...ctx, toolCallId:id});
  const tools = createIntegrationTools({context:factory(ctx), outcomes, submit:submit ?? (async () => reply)});
  const result = await tools.find(tool => tool.name === 'pixel_ods_mcp').execute(id,value);
  outcomes.after(event(id,value), {...ctx,toolCallId:id});
  return result;
}
async function admitReview(outcomes,id,value=params,ctx=context,name='pixel_ods_mcp') {
  const candidate=await outcomes.prepareReviewCandidate(event(id,value,name),{...ctx,toolCallId:id});
  if(candidate?.block)return candidate;
  const verdict=await outcomes.checkReviewCandidate(candidate,async current=>Object.freeze({candidate:current.ref,entailed:true}));
  return outcomes.commitReviewCandidate(candidate,verdict);
}
async function reserveReview(outcomes,id,value=params,ctx=context,name='pixel_ods_mcp') {
  assert.equal(await admitReview(outcomes,id,value,ctx,name),undefined);
  return outcomes.take(id,integrationRequest(name,value,ctx),factory(ctx),{name,params:value});
}

async function latchedReview() {
  const ctx={...context,modelProviderId:'ods-gateway',modelId:'ods/current'};
  const owner='Read the Obsidian note Example.md.';
  const startOutcomeRecord=createIntegrationOutcomes();startOutcomeRecord.begin({},ctx);
  const recovery=createIntegrationReviewRecovery({eligible:()=>true,ready:()=>true,
    nativeStartClear:state=>startOutcomeRecord.reviewStartClear({agentId:'pixel',
      runId:state.runId,sessionId:state.sessionId,sessionKey:state.sessionKey}),
    complete:async()=>({agentId:'pixel',provider:'ods-gateway',model:'ods/current',text:JSON.stringify({
      operations:[{id:'notes.read',approval:'none'}]})})});
  recovery.observe({prompt:owner},ctx,ctx.runId);
  const decision=await recovery.finalize({...ctx,nativeRetryToolsAllowVersion:1,nativeRetryToolChoiceVersion:1,
    lastAssistantMessage:'I read it.',messages:[{role:'user',content:owner},{role:'assistant',content:'I read it.'}]},ctx);
  assert.equal(decision.action,'revise');
  return {ctx,recovery};
}

test('a dynamic native review latch closes hookless notes reads and catalogs, including missing outcome records', async () => {
  const {ctx,recovery}=await latchedReview();let submissions=0;
  const tools=createIntegrationTools({context:factory(ctx),outcomes:createIntegrationOutcomes(),
    requireBound:scope=>recovery.isReviewOnly(scope),submit:async()=>{submissions++;return {ok:true,text:'never'};}});
  for(const [name,value] of [['pixel_ods_notes',{action:'read',path:'Example.md'}],
    ['pixel_ods_notes',{action:'search',query:'owned'}],['pixel_ods_mcp',{action:'list'}]]) {
    assert.equal((await tools.find(t=>t.name===name).execute('hookless',value)).details.code,'integration-call-unbound');
  }
  assert.equal(submissions,0);
});

test('changed append/read/search/mode/source payloads cannot downgrade to optional normal reads', async () => {
  const cases=[
    ['pixel_ods_notes',{action:'write',path:'a.md',text:'owned',mode:'append'},'pixel_ods_notes',{action:'read',path:'a.md'}],
    ['pixel_ods_notes',{action:'read',path:'a.md'},'pixel_ods_notes',{action:'read',path:'b.md'}],
    ['pixel_ods_notes',{action:'search',query:'owned'},'pixel_ods_notes',{action:'search',query:'other'}],
    ['pixel_ods_notes',{action:'write',path:'a.md',text:'owned',mode:'append'},'pixel_ods_notes',{action:'write',path:'a.md',text:'owned',mode:'create'}],
    ['pixel_ods_notes',{action:'read',path:'a.md'},'pixel_ods_mcp',{action:'list'}],
  ];
  for(const [beforeName,beforeValue,afterName,afterValue] of cases) {
    const outcomes=setup();let submissions=0;
    await admitReview(outcomes,'one',beforeValue,context,beforeName);
    const tools=createIntegrationTools({context:factory(),outcomes,requireBound:()=>true,
      submit:async()=>{submissions++;return {ok:true,text:'never'};}});
    const result=await tools.find(t=>t.name===afterName).execute('one',afterValue);
    assert.equal(result.details.code,'integration-call-unbound');assert.equal(submissions,0);
  }
});

test('only exact bound review note reads/search/catalogs settle, carry the private host flag, and cannot replay', async () => {
  for(const [name,value,reply] of [
    ['pixel_ods_notes',{action:'read',path:'a.md'},{path:'a.md',text:'owned'}],
    ['pixel_ods_notes',{action:'search',query:'owned'},{results:[]}],
    ['pixel_ods_mcp',{action:'list'},{servers:[]}],
  ]) {
    const outcomes=setup(),submissions=[];
    await admitReview(outcomes,'one',value,context,name);
    const tools=createIntegrationTools({context:factory(),outcomes,requireBound:()=>true,
      submit:async payload=>{submissions.push(payload);return {ok:true,...reply,recoveryReviewOnly:true};}});
    const tool=tools.find(t=>t.name===name),result=await tool.execute('one',value);
    assert.equal(result.details.status,'done');assert.equal(result.isError,undefined);
    assert.deepEqual(submissions,[{...integrationRequest(name,value,context),recoveryReviewOnly:true}]);
    assert.equal(outcomes.projection(runId),undefined,'settled reads do not invent pending or approval notices');
    assert.equal((await tool.execute('one',value)).details.code,'integration-call-unbound');
    assert.equal(submissions.length,1);
  }
});

test('native deferred review reads share one exact parent/child ticket and cannot change their arguments', async () => {
  for(const changed of [false,true]) {
    const outcomes=setup(),value={action:'read',path:'a.md'},child='tool_search_code:parent:pixel_ods_notes:1';
    await admitReview(outcomes,'parent',{id:'openclaw:pixel-ods:pixel_ods_notes',args:value},context,'tool_call');
    const concrete=changed?{...value,path:'b.md'}:value;
    const decision=await admitReview(outcomes,child,concrete,context,'pixel_ods_notes');
    let submissions=0;
    const tools=createIntegrationTools({context:factory(),outcomes,requireBound:()=>true,
      submit:async()=>{submissions++;return {ok:true,path:'a.md',text:'owned',recoveryReviewOnly:true};}});
    const result=await tools.find(t=>t.name==='pixel_ods_notes').execute(child,concrete);
    assert.equal(submissions,changed?0:1);assert.equal(result.details.status,changed?'blocked':'done');
    if(changed)assert.equal(decision.block,true);
  }
});

test('known review lifetime remains restrictive for session-only factories after end/cancel/eviction and same-run fallback', async () => {
  for(const terminal of ['end','cancel','evict','fallback']) {
    const {ctx,recovery}=await latchedReview(),outcomes=createIntegrationOutcomes();outcomes.begin({},ctx);
    const read={action:'read',path:'a.md'};let submissions=0;
    const tools=createIntegrationTools({context:factory(ctx),outcomes,requireBound:scope=>recovery.isReviewOnly(scope),
      submit:async()=>{submissions++;return {ok:true,path:'a.md',text:'owned',recoveryReviewOnly:true};}});
    if(terminal==='end'){outcomes.end({success:true},ctx);recovery.end({runId:ctx.runId},ctx);}
    if(terminal==='cancel'){outcomes.cancelUser('ods-'+'a'.repeat(64));recovery.cancelSession(ctx.sessionKey);}
    if(terminal==='evict')outcomes.clear();
    if(terminal==='fallback'){outcomes.begin({},ctx);recovery.observe({prompt:'wrapper'},ctx,ctx.runId);}
    assert.equal(recovery.isReviewOnly(factory(ctx)),true,terminal);
    assert.equal((await tools.find(t=>t.name==='pixel_ods_notes').execute('absent',read)).details.code,'integration-call-unbound');
    assert.equal(submissions,0,terminal);
  }
});

test('normal first-pass note reads/catalogs remain optional and a fresh owner cannot give an exact old run its new ticket', async () => {
  const {ctx,recovery}=await latchedReview(),outcomes=createIntegrationOutcomes();outcomes.begin({},ctx);
  const fresh={...ctx,runId:'chatcmpl_22222222-2222-4222-8222-222222222222'};
  recovery.observe({prompt:'Read a note.'},fresh,fresh.runId);outcomes.begin({},fresh);
  let submissions=0;const read={action:'read',path:'a.md'};
  const old=createIntegrationTools({context:ctx,outcomes,requireBound:scope=>recovery.isReviewOnly(scope),
    submit:async()=>{submissions++;return {ok:true,path:'a.md',text:'owned'};}});
  outcomes.before(event('reused',read,'pixel_ods_notes'),{...fresh,toolCallId:'reused'},undefined,{recoveryReviewOnly:true});
  assert.equal((await old.find(t=>t.name==='pixel_ods_notes').execute('reused',read)).details.code,'integration-call-unbound');
  const ordinary=createIntegrationOutcomes();ordinary.begin({},fresh);
  const tools=createIntegrationTools({context:factory(fresh),outcomes:ordinary,requireBound:scope=>recovery.isReviewOnly(scope),
    submit:async payload=>{submissions++;return payload.action==='mcp-tools'?{ok:true,servers:[]}:{ok:true,path:'a.md',text:'owned'};}});
  assert.equal((await tools.find(t=>t.name==='pixel_ods_notes').execute('read',read)).details.status,'done');
  assert.equal((await tools.find(t=>t.name==='pixel_ods_mcp').execute('catalog',{action:'list'})).details.status,'done');
  assert.equal(submissions,2);assert.equal(ordinary.reviewStartClear(fresh),true,'normal read tracking is unchanged');
  // A captured session-only native definition cannot prove its old run after
  // a new owner. The pinned SDK's definition lifecycle is the boundary for
  // that unsupported direct invocation; no runId is fabricated here.
});

test('review start is true only for an exact current known owner with zero admitted calls', () => {
  const outcomes=setup();
  assert.equal(outcomes.reviewStartClear({...context}),true);
  for(const change of [{agentId:'other'},{runId:'missing'},{sessionId:'other'},
    {sessionKey:'agent:pixel:other'},{runId:undefined},{sessionId:undefined},{sessionKey:undefined},
    {runId:'bad\nrun'},{sessionKey:''}]) {
    assert.equal(outcomes.reviewStartClear({...context,...change}),false);
    assert.equal(outcomes.reviewStartClear(context),true,'a rejected query must not invalidate or rebind the current owner');
  }
});

test('review-start queries never bind sparse records, reopen ended records or consume pending admission', () => {
  const outcomes=createIntegrationOutcomes();
  const {sessionKey:_key,...sparse}=context;
  outcomes.begin({},sparse);
  assert.equal(outcomes.reviewStartClear(context),false);
  assert.equal(outcomes.reviewStartClear(context),false,'the first query must not fill the missing saved key');
  outcomes.begin({},context); // Normal trusted observation may bind it.
  assert.equal(outcomes.reviewStartClear(context),true);
  outcomes.end({success:true},context);
  assert.equal(outcomes.reviewStartClear(context),false);
  assert.equal(outcomes.reviewStartClear(context),false,'a read cannot reopen an ended run');
  outcomes.begin({},context); // Ordinary same-run prompt reentry remains supported.
  assert.equal(outcomes.reviewStartClear(context),true);
  outcomes.before(event('reserved'),{...context,toolCallId:'reserved'});
  assert.equal(outcomes.reviewStartClear(context),false);
  const token=outcomes.take('reserved',integrationRequest('pixel_ods_mcp',params,context),factory());
  assert.ok(token,'the predicate must not consume or mutate the reservation');
  outcomes.complete(token,'pending');
  const notice=outcomes.projection(runId);
  assert.equal(outcomes.reviewStartClear(context),false);
  assert.equal(outcomes.projection(runId),notice);
});

test('actual pending, refusal, failure and unknown receipts cannot start an automatic review', async () => {
  for(const [name,reply,submit] of [
    ['pending',pending],
    ['refused',{ok:false,error:'mcp-auth',message:'Definitely refused'}],
    ['failed',{ok:true,text:'Failed',isError:true}],
    ['unknown',{ok:false,error:'mcp-outcome-unknown'}],
    ['transport',undefined,async()=>{throw Object.assign(new Error('private transport'),{code:'integration-outcome-unknown'});}]
  ]) {
    const outcomes=setup();
    await execute(outcomes,name,params,reply,context,submit);
    const before=outcomes.projection(runId);
    assert.equal(outcomes.reviewStartClear(context),false,name);
    assert.equal(outcomes.projection(runId),before,'no receipt is cleared by the predicate');
  }
});

test('missing admission, cancellation, failure, supersession and overflow stay closed without query mutation', async () => {
  const absent=createIntegrationOutcomes();let submissions=0;
  const tools=createIntegrationTools({context:factory(),outcomes:absent,submit:async()=>{submissions++;return pending;}});
  assert.equal((await tools.find(tool=>tool.name==='pixel_ods_mcp').execute('unbound',params)).details.code,'integration-call-unbound');
  assert.equal(absent.reviewStartClear(context),false);assert.equal(submissions,0);
  for(const reason of ['cancel','failed','superseded','invalid','clear']) {
    const outcomes=setup();
    if(reason==='cancel')outcomes.cancelUser('ods-'+'a'.repeat(64));
    if(reason==='failed')outcomes.end({success:false},context);
    if(reason==='superseded')outcomes.begin({}, {...context,runId:'next-owner'});
    if(reason==='invalid')outcomes.begin({}, {...context,sessionKey:'agent:pixel:conflicting-key'});
    if(reason==='clear')outcomes.clear();
    assert.equal(outcomes.reviewStartClear(context),false,reason);
    assert.equal(outcomes.reviewStartClear(context),false,reason);
  }
  const overflow=setup({maximumCalls:1});reserve(overflow,'first');overflow.before(event('second'),{...context,toolCallId:'second'});
  assert.equal(overflow.reviewStartClear(context),false);
});

test('unknown inheritance blocks internal fallback but a positively observed fresh owner is not perpetually fenced', () => {
  const outcomes=setup();outcomes.complete(reserve(outcomes,'unknown'),'unknown');
  const inherited={...context,runId:'internal-fallback',trigger:'internal'};
  outcomes.begin({},inherited,context.runId);
  assert.equal(outcomes.reviewStartClear(inherited),false,'uncertainty alone blocks even with zero calls in this derived run');
  outcomes.begin({prompt:'another internal wrapper'},inherited,context.runId);
  assert.equal(outcomes.reviewStartClear(inherited),false);
  const fresh={...context,runId:'explicit-new-owner',trigger:'user'};
  outcomes.begin({},fresh);
  assert.equal(outcomes.reviewStartClear(fresh),true);
  assert.equal(outcomes.reviewStartClear(inherited),false);
  outcomes.begin({prompt:'same owner native reentry'},fresh);
  assert.equal(outcomes.reviewStartClear(fresh),true);
});

test('direct host pending adds friendly factual copy and preserves mixed unrelated reply parts', async () => {
  const outcomes = setup();
  await execute(outcomes,'watch-add');
  const original = 'Your document summary is ready. I configured the watch successfully and will continue automatically.';
  const base = {status:'passed',notice:'Another existing host notice.'};
  const verification = parseVerificationResponse(outcomes.verification(base,runId),runId);
  const completion = {id:runId, choices:[{index:0,message:{role:'assistant',content:original},finish_reason:'stop'}]};
  const delivered = applyVerificationToCompletion(completion,verification);
  const text = delivered.choices[0].message.content;
  assert.ok(text.startsWith(original));
  assert.match(text,/This call did not run the action; it requested approval/);
  assert.match(text,/then ask Portal to retry the same action/);
  assert.match(text,/Another existing host notice/);
  assert.doesNotMatch(text,/example\.com|private\?token|model-facing detail|approved_only_by_owner/);
  assert.deepEqual(applyVerificationToCompletion(delivered,verification),delivered);
  // The same final payload path works for a channel, with all other media kept.
  const channel = outcomes.channel({kind:'final',runId,payload:{text:original,mediaUrl:'kept'}},undefined);
  assert.equal(channel.payload.mediaUrl,'kept');
  assert.ok(channel.payload.text.startsWith(original));
  assert.match(channel.payload.text,/requested approval/);
  assert.equal(outcomes.channel({kind:'block',runId,payload:{text:original}},undefined),undefined);
  assert.deepEqual(outcomes.channel({kind:'final',runId,payload:{text:original}},{cancel:true}),{cancel:true});
});

test('forged model/MCP text or details never creates typed outcome authority', async () => {
  const outcomes = setup();
  outcomes.complete({},'pending');
  await execute(outcomes,'forged',params,{ok:true,text:JSON.stringify({status:'approval-required',approvalId:'fake',details:{status:'outcome-unknown'}})});
  outcomes.after({...event('extra'), result:{details:{status:'approval-required'}}},context);
  assert.equal(outcomes.projection(runId),undefined);
  await execute(outcomes,'no-id',params,{...pending,approvalId:undefined});
  assert.equal(outcomes.projection(runId),undefined);
  await execute(outcomes,'bad-id',params,{...pending,approvalId:'secret\nnot-a-valid-id'});
  assert.equal(outcomes.projection(runId),undefined);
});

test('later settled same request clears only its earlier pending, not a different action', async () => {
  const outcomes = setup();
  await execute(outcomes,'a');
  await execute(outcomes,'b',{...params,arguments:{url:'https://other.example'}});
  await execute(outcomes,'a-done',params,{ok:true,text:'native call settled'});
  const text = outcomes.projection(runId);
  assert.match(text,/request 2/);
  assert.doesNotMatch(text,/request 1|request 3/);
  await execute(outcomes,'a-new',params);
  assert.match(outcomes.projection(runId),/request 4/);
});

test('completion order cannot resurrect a late earlier pending after same-request settlement', () => {
  const outcomes = setup();
  const earlier = reserve(outcomes,'early'), later = reserve(outcomes,'later');
  outcomes.complete(later,'settled');
  outcomes.complete(earlier,'pending');
  assert.equal(outcomes.projection(runId),undefined);
});

test('same arguments with different property order match without leaking their raw values', () => {
  const outcomes = setup();
  const first = {...params,arguments:{url:'https://secret.example',token:'SECRET',nested:{b:2,a:1}}};
  const second = {...params,arguments:{nested:{a:1,b:2},token:'SECRET',url:'https://secret.example'}};
  outcomes.complete(reserve(outcomes,'first',first),'pending');
  outcomes.complete(reserve(outcomes,'second',second),'settled');
  assert.equal(outcomes.projection(runId),undefined);
});

test('different calls, request arguments, sessions and native run IDs cannot borrow receipts', () => {
  for (const fault of ['call','arguments','session','key','run','agent']) {
    const outcomes = setup();
    outcomes.before(event('real'),{...context,toolCallId:'real'});
    const altered = {...factory()};
    if (fault==='session') altered.sessionId='different';
    if (fault==='key') altered.sessionKey='agent:pixel:other';
    if (fault==='run') altered.runId='forged';
    if (fault==='agent') altered.agentId='other';
    const token = outcomes.take(fault==='call'?'forged':'real',integrationRequest('pixel_ods_mcp',
      fault==='arguments'?{...params,arguments:{url:'another'}}:params,context),altered);
    assert.equal(token,undefined,fault);
    outcomes.complete(token,'pending');
    assert.equal(outcomes.projection(runId),undefined,fault);
  }
});

test('native deferred-tool wrapping retains exact parent and arguments without text heuristics', () => {
  const outcomes = setup();
  const wrapped = {toolName:'tool_call',toolCallId:'native-parent',params:{id:'openclaw:pixel-ods:pixel_ods_mcp',args:params}};
  outcomes.before(wrapped,{...context,toolCallId:'native-parent'});
  const payload = integrationRequest('pixel_ods_mcp',params,context);
  assert.equal(outcomes.take('tool_search_code:wrong:pixel_ods_mcp:1',payload,factory()),undefined);
  const token = outcomes.take('tool_search_code:native-parent:pixel_ods_mcp:1',payload,factory());
  outcomes.complete(token,'pending');
  assert.match(outcomes.projection(runId),/requested approval/);
  assert.equal(outcomes.take('tool_search_code:native-parent:pixel_ods_mcp:2',payload,factory()),undefined);
});

test('native Tool Search wrapper and concrete hooks admit one child execution, not two tickets', () => {
  const outcomes = setup();
  outcomes.before({toolName:'tool_call',toolCallId:'parent',params:{id:'openclaw:pixel-ods:pixel_ods_mcp',args:params}},
    {...context,toolCallId:'parent'});
  const child = 'tool_search_code:parent:pixel_ods_mcp:1';
  outcomes.before(event(child),{...context,toolCallId:child});
  const payload = integrationRequest('pixel_ods_mcp',params,context);
  assert.equal(outcomes.take('parent',payload,factory()),undefined);
  assert.equal(outcomes.take('tool_search_code:parent:pixel_ods_mcp:2',payload,factory()),undefined);
  const token = outcomes.take(child,payload,factory());
  assert.ok(token);
  outcomes.complete(token,'unknown');
  assert.match(outcomes.projection(runId),/request 1/);
  assert.doesNotMatch(outcomes.projection(runId),/request 2/);
  outcomes.after(event(child),{...context,toolCallId:child});
  assert.equal(outcomes.take(child,payload,factory()),undefined);
});

test('changed concrete child arguments close its parent instead of borrowing another admission', () => {
  const outcomes = setup();
  outcomes.before({toolName:'tool_call',toolCallId:'parent',params:{id:'pixel_ods_mcp',args:params}},
    {...context,toolCallId:'parent'});
  const child = 'tool_search_code:parent:pixel_ods_mcp:1';
  const changed = {...params,arguments:{url:'changed'}};
  assert.equal(outcomes.before(event(child,changed),{...context,toolCallId:child}).block,true);
  for (const value of [params,changed]) assert.equal(outcomes.take(child,
    integrationRequest('pixel_ods_mcp',value,context),factory()),undefined);
  assert.equal(outcomes.projection(runId),undefined);
});

test('dispatched bytes use the admitted private snapshot even if the caller later mutates arguments', async () => {
  const outcomes = setup();
  const value = {...params,arguments:{url:'admitted',nested:{array:['admitted']}}};
  outcomes.before(event('snapshot',value),{...context,toolCallId:'snapshot'});
  let submitted;
  const tools = createIntegrationTools({context:factory(),outcomes,submit:async payload=>{
    await new Promise(resolve=>setImmediate(resolve));
    submitted = JSON.parse(JSON.stringify(payload));
    return pending;
  }});
  const pendingCall = tools.find(tool=>tool.name==='pixel_ods_mcp').execute('snapshot',value);
  value.arguments.url = 'changed';
  value.arguments.nested.array[0] = 'changed';
  await pendingCall;
  assert.equal(submitted.arguments.url,'admitted');
  assert.deepEqual(submitted.arguments.nested.array,{array:['admitted']}.array);
  assert.match(outcomes.projection(runId),/requested approval/);
});

test('native end invalidates unfinished ticket control and same-run context reentry keeps the checked floor', async () => {
  const outcomes = setup();
  const token = await reserveReview(outcomes,'first');
  outcomes.end({success:true},{...context});
  assert.equal(outcomes.control(token),undefined);
  outcomes.finish(token); // This unit never started a host request to settle it.
  outcomes.begin({}, {...context});
  const next = await reserveReview(outcomes,'next',params,{...context});
  assert.equal(outcomes.control(next).recoveryReviewOnly,true);
  outcomes.complete(token,'pending');
  assert.equal(outcomes.projection(runId),undefined);
});

test('a newly latched concrete child needs its own check and cannot promote an unchecked ordinary parent', async () => {
  const outcomes = setup();
  outcomes.before({toolName:'tool_call',toolCallId:'parent',params:{id:'pixel_ods_mcp',args:params}},
    {...context,toolCallId:'parent'});
  const child = 'tool_search_code:parent:pixel_ods_mcp:1';
  assert.equal(await admitReview(outcomes,child),undefined);
  assert.equal(outcomes.take('parent',integrationRequest('pixel_ods_mcp',params,context),factory()),undefined);
  const token = outcomes.take(child,integrationRequest('pixel_ods_mcp',params,context),factory(),{name:'pixel_ods_mcp',params});
  assert.equal(outcomes.control(token).recoveryReviewOnly,true);
  outcomes.complete(token,'pending');
  const checked=setup(),later=await reserveReview(checked,'later');
  assert.equal(checked.control(later).recoveryReviewOnly,true);
});

test('only native trusted ticket control adds review-only and cannot borrow normal receipt identity', async () => {
  const outcomes = setup();
  const submitted = [];
  const tools = createIntegrationTools({context:{...factory(),recoveryReviewOnly:true},outcomes,
    submit:async payload => {submitted.push(payload); return {...pending,...(payload.recoveryReviewOnly ?
      {recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false} : {})};}});
  const tool = tools.find(value => value.name === 'pixel_ods_mcp');
  // Model arguments and factory data do not set private host control.
  const forged = {...params,recoveryReviewOnly:true,arguments:{...params.arguments,recoveryReviewOnly:true}};
  outcomes.before(event('normal',forged),{...context,toolCallId:'normal'});
  await tool.execute('normal',forged);
  assert.equal(submitted[0].recoveryReviewOnly,undefined);
  const ordinary = reserve(outcomes,'ordinary',params);
  // Only the native provisional/check/commit path supplies private control.
  await admitReview(outcomes,'review');
  await tool.execute('review',params);
  assert.equal(submitted[1].recoveryReviewOnly,true);
  assert.equal(submitted[1].arguments.recoveryReviewOnly,undefined);
  assert.equal(outcomes.control({}),undefined);
  outcomes.complete(ordinary,'settled');
  assert.match(outcomes.projection(runId),/request 3/);
  // A settled ordinary request cannot clear a review-only proposal, even when
  // every model-controlled argument is otherwise identical.
  // A later false control cannot lower the native run's floor.
  assert.equal(outcomes.before(event('latched'),{...context,toolCallId:'latched'},undefined,{recoveryReviewOnly:false}).block,true);
  const latched = await reserveReview(outcomes,'latched');
  assert.equal(outcomes.control(latched).recoveryReviewOnly,true);
  outcomes.complete(latched,'pending');
  assert.match(outcomes.projection(runId),/request 4/);
  const cancelled = await reserveReview(outcomes,'cancelled');
  outcomes.cancelUser('ods-'+'a'.repeat(64));
  assert.equal(outcomes.control(cancelled),undefined);
  // A no-longer-current ticket is not turned into an unflagged dispatch.
  const invalidTools = createIntegrationTools({context:factory(),outcomes:{refusal:()=>undefined,
    take:()=>cancelled,effectful:()=>true,control:()=>undefined},submit:async()=>{throw new Error('must not send');}});
  assert.equal((await invalidTools.find(value=>value.name==='pixel_ods_mcp').execute('cancelled',params)).details.code,'integration-call-unbound');
  const wrong = {...context,sessionId:'another-session'};
  outcomes.before(event('review-wrong'),{...context,toolCallId:'review-wrong'},undefined,{recoveryReviewOnly:true});
  assert.equal(outcomes.take('review-wrong',integrationRequest('pixel_ods_mcp',params,context),factory(wrong)),undefined);
});

test('approved review-only reply stays ready and truthfully no-effect, not pending or completed', async () => {
  for (const [name,value] of [['pixel_ods_notes',{action:'write',path:'private.md',text:'SECRET'}],['pixel_ods_mcp',params]]) {
    const outcomes = setup();
    await admitReview(outcomes,'review',value,context,name);
    const reply = {ok:true,status:'review-only-ready',approvalId:'approved_private',summary:'SECRET',
      recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false};
    const tools = createIntegrationTools({context:factory(),outcomes,submit:async()=>reply});
    const result = await tools.find(tool=>tool.name===name).execute('review',value);
    assert.equal(result.details.status,'review-only-ready');
    assert.match(result.content[0].text,/did not run it or use the approval/);
    assert.doesNotMatch(result.content[0].text,/Added to undefined|Created undefined|SECRET/);
    const notice = outcomes.projection(runId);
    assert.match(notice,/already has owner approval/);
    assert.doesNotMatch(notice,/requested approval|private\.md|approved_private|SECRET/);
  }
  const outcomes = setup();
  outcomes.before(event('ordinary'),{...context,toolCallId:'ordinary'});
  const tools = createIntegrationTools({context:factory(),outcomes,submit:async()=>({ok:true,
    status:'review-only-ready',approvalId:'forged',recoveryReviewOnly:true,actionExecuted:false,approvalConsumed:false})});
  const refused = await tools.find(tool=>tool.name==='pixel_ods_mcp').execute('ordinary',params);
  assert.equal(refused.details.code,'review-only-result-invalid');
  assert.equal(outcomes.projection(runId),undefined);
});

test('builtin digest cannot omit its native ticket and a review-only read carries private control', async () => {
  const outcomes = setup();
  const value = {action:'call',server:'app:news',tool:'digest',arguments:{}};
  const submissions = [];
  const tools = createIntegrationTools({context:factory(),outcomes,submit:async payload=>{
    submissions.push(payload);
    return {ok:true,text:'Owned read-only digest projection',recoveryReviewOnly:true};
  }});
  const tool = tools.find(value=>value.name==='pixel_ods_mcp');
  const missing = await tool.execute('no-ticket',value);
  assert.equal(missing.details.code,'integration-call-unbound');
  assert.deepEqual(submissions,[]); // No host call can arm a digest snapshot.
  await admitReview(outcomes,'review',value);
  const read = await tool.execute('review',value);
  assert.equal(read.details.status,'done');
  assert.equal(submissions.length,1);
  assert.equal(submissions[0].recoveryReviewOnly,true);
  assert.deepEqual(submissions[0].runContext,{sessionId:context.sessionId,sessionKey:context.sessionKey});
  // The host's tagged builtin read separately ignores runContext rather than
  // arming a delivery snapshot (test_integrations_recovery.py).
  const after = await tool.execute('same-ticket-replay',value);
  assert.equal(after.details.code,'integration-call-unbound');
  assert.equal(submissions.length,1);
});

test('an unknown result never retries and is not erased by an unrelated or later new call', async () => {
  const outcomes = setup();
  let submissions = 0;
  await execute(outcomes,'unknown',params,undefined,context,async () => {
    submissions++;
    throw Object.assign(new Error('SECRET_URL'),{code:'integration-outcome-unknown'});
  });
  assert.equal(submissions,1);
  assert.match(outcomes.projection(runId),/Its result could not be verified; check the destination before retrying/);
  assert.doesNotMatch(outcomes.projection(runId),/SECRET_URL/);
  const blocked = await execute(outcomes,'later-settled',params,undefined,context,async () => {
    submissions++;
    return {ok:true,text:'must never run'};
  });
  assert.equal(blocked.details.code,'integration-outcome-unresolved');
  assert.equal(submissions,1);
  assert.match(outcomes.projection(runId),/request 1/);
});

test('unknown refusals fence changed effectful arguments at native hooks and factory, while reads remain usable', async () => {
  const outcomes = setup();
  outcomes.complete(reserve(outcomes,'first'),'unknown');
  let submissions=0;
  const tools = createIntegrationTools({context:factory(),outcomes,submit:async () => {
    submissions++; return {ok:true,text:'read result',sent:true};
  }});
  for (const [name,value] of [
    ['pixel_ods_mcp',{...params,arguments:{url:'https://changed.example'}}],
    ['pixel_ods_mcp',{action:'call',server:'opaque',tool:'readOnlyByUntrustedHint',arguments:{different:true}}],
    ['pixel_ods_notes',{action:'write',path:'a.md',text:'changed'}],
    ['pixel_ods_notify',{title:'a new title',message:'changed'}],
  ]) {
    const native = outcomes.before(event(name,value,name),{...context,toolCallId:name});
    assert.equal(native.block,true);
    const result=await tools.find(tool=>tool.name===name).execute(name,value);
    assert.equal(result.details.code,'integration-outcome-unresolved');
  }
  assert.equal(submissions,0);
  const read={action:'call',server:'app:documents',tool:'search',arguments:{query:'safe'}};
  assert.equal(outcomes.before(event('read',read),{...context,toolCallId:'read'}),undefined);
  assert.equal((await tools.find(tool=>tool.name==='pixel_ods_mcp').execute('read',read)).isError,undefined);
  assert.equal(submissions,1);
  // A model/provider fallback for this same owner run cannot renew the fence.
  outcomes.end({success:false},context);
  outcomes.begin({prompt:'different internal wrapper'}, {...context,modelId:'fallback'});
  assert.equal(outcomes.before(event('fallback'),{...context,toolCallId:'fallback'}).block,true);
  const fresh={...context,runId:'fresh-owner-native-run'};
  outcomes.begin({},fresh);
  await execute(outcomes,'fresh',params,{ok:true,text:'fresh owner call'},fresh,async () => {
    submissions++; return {ok:true,text:'fresh owner call'};
  });
  assert.equal(submissions,2);
  assert.equal(outcomes.projection(fresh.runId),undefined);
});

test('closed host uncertainty codes become unknown while provider strings and definite refusals do not', async () => {
  for (const code of ['notification-outcome-unknown','mcp-outcome-unknown','app-outcome-unknown']) {
    const outcomes=setup();
    const result=await execute(outcomes,'native',params,{ok:false,error:code,message:'SECRET native details'});
    assert.equal(result.details.status,'outcome-unknown');
    assert.match(outcomes.projection(runId),/could not be verified/);
    assert.doesNotMatch(outcomes.projection(runId),/SECRET/);
  }
  for (const code of ['notification-refused','notification-not-connected','mcp-auth','mcp-error']) {
    const outcomes=setup();
    const result=await execute(outcomes,'native',params,{ok:false,error:code,message:'Provider says outcome-unknown'});
    assert.equal(result.details.status,'refused');
    assert.equal(outcomes.projection(runId),undefined);
  }
});

test('next owner run, cancellation, failures and shutdown suppress old projections and late callbacks', () => {
  for (const reason of ['new','cancel','failure','clear']) {
    const outcomes = setup();
    const early = reserve(outcomes,'early'), late = reserve(outcomes,'late');
    outcomes.complete(early,'pending');
    if (reason==='new') outcomes.begin({}, {...context,runId:'new-native-run'});
    if (reason==='cancel') outcomes.cancelUser('ods-'+'a'.repeat(64));
    if (reason==='failure') outcomes.end({success:false},context);
    if (reason==='clear') outcomes.clear();
    outcomes.complete(late,'pending');
    assert.equal(outcomes.projection(runId),undefined,reason);
    assert.equal(outcomes.projection('new-native-run'),undefined,reason);
  }
});

test('a successful native end retains its final exact-run notice, but closes unfinished calls', () => {
  const outcomes = setup(), complete = reserve(outcomes,'complete'), late = reserve(outcomes,'late');
  outcomes.complete(complete,'pending');
  outcomes.end({success:true},context);
  assert.match(outcomes.projection(runId),/request 1/);
  outcomes.begin({},context);
  outcomes.complete(late,'pending');
  assert.doesNotMatch(outcomes.projection(runId),/request 2/);
});

test('non-user same-chat continuations and trusted child runs inherit the uncertainty fence', () => {
  for (const trigger of ['cron','subagent',undefined]) {
    const outcomes=setup();
    outcomes.complete(reserve(outcomes,'uncertain'),'unknown');
    const continued={...context,runId:'continued',trigger};
    outcomes.begin({},continued);
    assert.equal(outcomes.before(event('blocked'),{...continued,toolCallId:'blocked'}).block,true);
    assert.equal(outcomes.projection(continued.runId),undefined); // Do not relabel a prior call as this run's receipt.
  }
  const outcomes=setup();
  outcomes.complete(reserve(outcomes,'uncertain'),'unknown');
  const child={...context,runId:'native-child',sessionId:'child-session',sessionKey:'agent:pixel:subagent:child'};
  outcomes.begin({},child,runId);
  assert.equal(outcomes.before(event('child-action'),{...child,toolCallId:'child-action'}).block,true);
});

test('a late old session cannot claim a newer owner key that was initially unavailable', () => {
  const outcomes = createIntegrationOutcomes();
  const old = {...context,runId:'old',sessionId:'old-session'};
  delete old.sessionKey;
  outcomes.begin({},old);
  outcomes.begin({},context);
  outcomes.before(event('old-call'),{...old,sessionKey:context.sessionKey,toolCallId:'old-call'});
  assert.equal(outcomes.take('old-call',integrationRequest('pixel_ods_mcp',params,context),factory({...old,sessionKey:context.sessionKey})),undefined);
  outcomes.complete(reserve(outcomes,'current'),'pending');
  assert.match(outcomes.projection(runId),/requested approval/);
});

test('free-form secrets never enter labels or owner output, and output/storage are bounded', async () => {
  const outcomes = setup({maximumCalls:8});
  for (let n=0;n<10;n++) await execute(outcomes,'call-'+n,
    {...params,server:'SECRET_SERVER_'+n,tool:'SECRET_TOOL',arguments:{token:'SECRET_TOKEN_'+n}});
  const text = outcomes.projection(runId);
  assert.ok(text.length<=MAX_INTEGRATION_NOTICE);
  assert.doesNotMatch(text,/SECRET_|example\.com|approved_only|model-facing/);
  assert.match(text,/Additional integration call statuses/);
  assert.equal((text.match(/requested approval/g)??[]).length,6);
  const huge = {...params,arguments:{secret:'x'.repeat(65537)}};
  assert.equal(reserve(outcomes,'huge',huge),undefined);
});

test('ingress rejects malformed, excessive or control-bearing overlay fields', () => {
  for (const bad of ['',7,'x'.repeat(MAX_INTEGRATION_NOTICE+1),'bad\rline','bad\0line']) {
    assert.throws(()=>parseVerificationResponse({status:'passed',integrationNotice:bad},runId));
  }
  const text='Notification (request 1): Its result could not be verified; check the destination before retrying.';
  assert.equal(parseVerificationResponse({status:'none',integrationNotice:text},runId).integrationNotice,text);
});

test('index wires only native admissions/direct outcomes and both existing delivery paths', () => {
  const source = readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  assert.match(source,/integrationOutcomes\.begin\(event, context, delegationDelivery\.ownerRunFor\(context\)\)/);
  assert.match(source,/integrationOutcomes\.before\(event, context, decision,\s*\{recoveryReviewOnly:/);
  assert.ok(source.indexOf('integrationReviewRecovery.floor(event, context)')
    < source.indexOf('await toolLoopGuard.beforeToolCall(event, context'));
  assert.match(source,/createIntegrationTools\(\{context, outcomes:integrationOutcomes,\s*requireBound:factoryContext=>integrationReviewRecovery\.isReviewOnly\(factoryContext\),/);
  assert.match(source,/prepareReviewCandidate\(event,context,decision\)/);
  assert.match(source,/checkReviewCandidate\(candidate,[\s\S]*?integrationReviewRecovery\.checkConcrete\(value,context\)/);
  assert.match(source,/commitReviewCandidate\(candidate,verdict\)/);
  assert.match(source,/integrationOutcomes\.verification\(/);
  assert.match(source,/integrationOutcomes\.channel\(event, toolLoopGuard\.replyPayloadSending\(event\)\)/);
  assert.match(source,/integrationOutcomes\.cancelUser\(parsed\.user\)/);
  assert.match(source,/integrationOutcomes\.end\(event, context\)/);
  assert.match(source,/nativeStartClear:\s*state\s*=>\s*integrationOutcomes\.reviewStartClear\(\{agentId:AGENT_ID,\s*runId:state\.runId,sessionId:state\.sessionId,sessionKey:state\.sessionKey\}\)/);
});
