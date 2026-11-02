import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createIntegrationOutcomes, MAX_INTEGRATION_NOTICE} from '../plugin/integration-outcomes.mjs';
import {createIntegrationTools, integrationRequest} from '../plugin/integrations.mjs';
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

test('native end invalidates unfinished ticket control and same-run context reentry keeps the floor', () => {
  const outcomes = setup();
  outcomes.before(event('first'),{...context,toolCallId:'first'},undefined,{recoveryReviewOnly:true});
  const token = outcomes.take('first',integrationRequest('pixel_ods_mcp',params,context),factory());
  outcomes.end({success:true},{...context});
  assert.equal(outcomes.control(token),undefined);
  outcomes.begin({}, {...context});
  const next = reserve(outcomes,'next',params,{...context});
  assert.equal(outcomes.control(next).recoveryReviewOnly,true);
  outcomes.complete(token,'pending');
  assert.equal(outcomes.projection(runId),undefined);
});

test('a trusted floor latched at a concrete native child also tightens its untaken parent ticket', () => {
  const outcomes = setup();
  outcomes.before({toolName:'tool_call',toolCallId:'parent',params:{id:'pixel_ods_mcp',args:params}},
    {...context,toolCallId:'parent'});
  const child = 'tool_search_code:parent:pixel_ods_mcp:1';
  outcomes.before(event(child),{...context,toolCallId:child},undefined,{recoveryReviewOnly:true});
  const token = outcomes.take(child,integrationRequest('pixel_ods_mcp',params,context),factory());
  assert.equal(outcomes.control(token).recoveryReviewOnly,true);
  outcomes.complete(token,'pending');
  outcomes.begin({},context);
  const later = reserve(outcomes,'later');
  assert.equal(outcomes.control(later).recoveryReviewOnly,true);
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
  // The fourth argument is owned by the native current-run admission path.
  outcomes.before(event('review',params),{...context,toolCallId:'review'},undefined,{recoveryReviewOnly:true});
  await tool.execute('review',params);
  assert.equal(submitted[1].recoveryReviewOnly,true);
  assert.equal(submitted[1].arguments.recoveryReviewOnly,undefined);
  assert.equal(outcomes.control({}),undefined);
  outcomes.complete(ordinary,'settled');
  assert.match(outcomes.projection(runId),/request 3/);
  // A settled ordinary request cannot clear a review-only proposal, even when
  // every model-controlled argument is otherwise identical.
  // A later false control cannot lower the native run's floor.
  outcomes.before(event('latched'),{...context,toolCallId:'latched'},undefined,{recoveryReviewOnly:false});
  const latched = outcomes.take('latched',integrationRequest('pixel_ods_mcp',params,context),factory());
  assert.equal(outcomes.control(latched).recoveryReviewOnly,true);
  outcomes.complete(latched,'pending');
  assert.match(outcomes.projection(runId),/request 4/);
  const cancelled = reserve(outcomes,'cancelled',params);
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
    outcomes.before(event('review',value,name),{...context,toolCallId:'review'},undefined,{recoveryReviewOnly:true});
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
  assert.match(source,/integrationOutcomes\.before\(event, context, decision\)/);
  assert.match(source,/createIntegrationTools\(\{context, outcomes:integrationOutcomes\}\)/);
  assert.match(source,/integrationOutcomes\.verification\(/);
  assert.match(source,/integrationOutcomes\.channel\(event, toolLoopGuard\.replyPayloadSending\(event\)\)/);
  assert.match(source,/integrationOutcomes\.cancelUser\(parsed\.user\)/);
  assert.match(source,/integrationOutcomes\.end\(event, context\)/);
});
