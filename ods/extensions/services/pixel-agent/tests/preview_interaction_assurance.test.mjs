import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, WORKSPACE_PREVIEW_COMPLETE_REASON} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL, PAGE_ERROR_REPAIR_INSTRUCTION, requestsVisibilityInteraction, requestsBehaviorPreservation, boundVisibilityInspection,
  preservesVisibilityInspection, visibilityInspectionInstruction} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams, createWorkspacePreviewInspectTool} from '../plugin/workspace-preview-inspect.mjs';

const owner='Create and publish a website in a new workspace directory site. Add a button that toggles hidden details.';
const context={agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'opaque-key'};
test('bundle output mutation invalidates publication and interaction until fresh verification',()=>{
  const {guard,preview}=setup();
  const observed=inspection(guard,plan(preview));
  guard.afterToolCall({...observed.event,result:observed.result},observed.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.equal(guard.invalidateWorkspaceBundle({...context,sessionId:'unrelated'}),false);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.equal(guard.invalidateWorkspaceBundle(context),true);
  assert.notEqual(guard.verificationForRun('run').status,'passed');
  guard.afterToolCall({...observed.event,result:observed.result},observed.ctx);
  assert.notEqual(guard.verificationForRun('run').status,'passed','late old inspection cannot restore the old publication');
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'new-publish',{details:preview});
  assert.notEqual(guard.verificationForRun('run').status,'passed','new publication still needs current interaction proof');
  const fresh=inspection(guard,plan(preview),{id:'new-inspection'});
  guard.afterToolCall({...fresh.event,result:fresh.result},fresh.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const next=revisePublishedSite(guard,preview);
  const staticPlan={...plan(next.preview),steps:[{action:'assert-visible',locator:{selector:'button'}}]};
  const staticCheck=inspection(guard,staticPlan,{id:'followup-static',runContext:next.ctx});
  guard.afterToolCall({...staticCheck.event,result:staticCheck.result},staticCheck.ctx);
  assert.equal(guard.verificationForRun(next.ctx.runId).status,'failed');
  assert.match(guard.verificationForRun(next.ctx.runId).text,/show\/hide interaction/);
  const nextTransition=inspection(guard,plan(next.preview),{id:'followup-transition',runContext:next.ctx});
  guard.afterToolCall({...nextTransition.event,result:nextTransition.result},nextTransition.ctx);
  assert.equal(guard.verificationForRun(next.ctx.runId).status,'passed');
});
function call(guard,name,params,id,result,runContext=context) {
  const ctx={...runContext,toolName:name,toolCallId:id};
  const event={toolName:name,runId:runContext.runId,toolCallId:id,params};
  const prepared=guard.beforeToolCall(event,ctx);
  assert.notEqual(prepared?.block,true,prepared?.blockReason);
  event.params=prepared?.params??params;
  if(result)guard.afterToolCall({...event,result},ctx);
  return {event,ctx};
}
function setup({enabled=true,prompt=owner,automatic=false,verifyWorkspacePreview}={}) {
  const guard=createToolLoopGuard({workspacePreviewInspectionAvailable:enabled,
    verifyWorkspacePreview,
    ...(automatic ? {publishWorkspacePreview:async()=>({details:preview})} : {})});
  guard.observeRun(context,'pixel',{prompt});
  const content='<!doctype html><button>Show details</button><p hidden>Details</p>';
  const write=call(guard,'write',{path:'site/index.html',content},'write',{content:[{type:'text',text:'Successfully wrote file.'}]}).event.params;
  guard.toolResultPersist({toolName:'write',toolCallId:'write',message:{role:'toolResult',toolName:'write',toolCallId:'write',content:[{type:'text',text:'Successfully wrote file.'}]}},{...context,toolName:'write',toolCallId:'write'});
  const dir=write.path.replace(/\/index.html$/,'');
  const name=Buffer.from('index.html'),data=Buffer.from(content),a=Buffer.alloc(4),b=Buffer.alloc(8);
  a.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(data.length));
  const sha256=createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId='site-'+sha256.slice(0,24);
  const preview={schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:dir,
    siteId,sha256,entryFile:'index.html',entrySha256:createHash('sha256').update(data).digest('hex'),files:1,bytes:data.length,
    port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,httpStatus:200,readbackVerified:true,executable:false,overwritten:false};
  if(!automatic) call(guard,'pixel_ods_workspace_preview',{relativeDirectory:dir},'publish',{details:preview});
  return {guard,preview};
}
function plan(preview) { return {siteId:preview.siteId,sha256:preview.sha256,viewport:{width:800,height:600},steps:[
  {action:'assert-hidden',locator:{selector:'#details'}},
  {action:'click',locator:{role:'button',name:'Show details',exact:true}},
  {action:'assert-visible',locator:{selector:'#details'}},
]}; }
function receipt(params) {
  const request=normalizeWorkspacePreviewInspectionParams(params);
  const state=visible=>({count:1,visible,display:visible?'block':'none',visibility:'visible',opacity:'1',hidden:!visible,hiddenUntilFound:false,rectCount:visible?1:0});
  return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:params.siteId,sha256:params.sha256,
    planSha256:inspectionPlanHash(request),viewport:params.viewport,steps:params.steps.map((s,index)=>({index,...s,
      before:state(s.action!=='assert-hidden'),...(s.action==='click'?{after:state(true)}:{}),stable:true,status:'passed'})),
    diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE};
}
function inspection(guard,params,{wrapped=false,id='inspect',runContext=context}={}) {
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:params}:params;
  const started=call(guard,name,args,id,undefined,runContext);
  const inner={details:receipt(params)};
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  return {...started,result};
}

test('successful static inspection reports its missing click before finalization',async()=>{
  const {preview}=setup();
  const params={...plan(preview),steps:[{action:'assert-visible',locator:{selector:'h1'}}]};
  const result=await createWorkspacePreviewInspectTool({request:async()=>receipt(params)}).execute('static',params);
  assert.equal(result.isError,undefined);
  assert.equal(result.details.status,'passed');
  assert.match(result.content[0].text,/no show\/hide transition was tested/);
  assert.match(result.content[0].text,/plan contains no click/);
  assert.equal(boundVisibilityInspection(params,result,preview),undefined);
});

test('successful click with unrelated initial assertion explains the missing same-target transition',async()=>{
  const {preview}=setup();
  const params=plan(preview);
  params.steps[0]={action:'assert-visible',locator:{selector:'h1'}};
  const result=await createWorkspacePreviewInspectTool({request:async()=>receipt(params)}).execute('mismatch',params);
  assert.equal(result.details.status,'passed');
  assert.match(result.content[0].text,/before and after a click do not check opposite visibility of the same affected element/);
  assert.match(result.content[0].text,/same target locator in both assertions/);
  assert.equal(boundVisibilityInspection(params,result,preview),undefined);
});

test('valid same-target transition retains its bound evidence without claiming all requested behavior',async()=>{
  const {preview}=setup();
  const params=plan(preview);
  const result=await createWorkspacePreviewInspectTool({request:async()=>receipt(params)}).execute('transition',params);
  assert.match(result.content[0].text,/tested opposite visibility states of the same element around a click/);
  assert.match(result.content[0].text,/does not establish every requested behavior/);
  assert.doesNotMatch(result.content[0].text,/no show\/hide transition was tested/);
  assert.ok(boundVisibilityInspection(params,result,preview));
});

test('a failed transition plan never receives successful coverage feedback',async()=>{
  const {preview}=setup();
  const params=plan(preview);
  const evidence=receipt(params);
  evidence.status='failed';
  evidence.steps=evidence.steps.slice(0,2);
  Object.assign(evidence.steps[1],{status:'failed',errorCode:'click_failed'});
  const result=await createWorkspacePreviewInspectTool({request:async()=>evidence}).execute('failed',params);
  assert.equal(result.isError,true);
  assert.equal(result.details.status,'failed');
  assert.match(result.content[0].text,/failed inspection does not establish a visibility transition/);
  assert.doesNotMatch(result.content[0].text,/These steps tested opposite visibility/);
  assert.equal(boundVisibilityInspection(params,result,preview),undefined);
});

for(const wrapped of [false,true]) for(const fault of ['none','host-bytes','receipt-sha','receipt-failed','outer-error','params','session','pending'])
test(`published inspections then grep -o require bound receipts and host bytes: wrapped=${wrapped}, fault=${fault}`,async()=>{
  let probes=0;
  const {guard,preview}=setup({verifyWorkspacePreview:async()=>{probes++;return fault!=='host-bytes';}});
  const persist=(event,result,ctx)=>guard.toolResultPersist({toolName:event.toolName,toolCallId:event.toolCallId,
    message:{role:'toolResult',toolName:event.toolName,toolCallId:event.toolCallId,...result}},ctx);
  persist({toolName:'pixel_ods_workspace_preview',toolCallId:'publish'},{details:preview},{...context,toolName:'pixel_ods_workspace_preview',toolCallId:'publish'});
  // The fleet first checked static visibility, then the actual visibility
  // transition. Static inspection is valid without satisfying interaction duty.
  const staticPlan={...plan(preview),steps:[{action:'assert-visible',locator:{selector:'button'}}]};
  const first=inspection(guard,staticPlan,{wrapped,id:'static-inspection'});
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  persist(first.event,first.result,first.ctx);
  const second=inspection(guard,plan(preview),{wrapped,id:'interaction-inspection'});
  const result=structuredClone(second.result),event={...second.event},ctx={...second.ctx};
  const inner=wrapped?result.details.result:result;
  if(fault==='receipt-sha')inner.details.sha256='b'.repeat(64);
  if(fault==='receipt-failed')inner.isError=true;
  if(fault==='outer-error')result.isError=true;
  if(fault==='params')event.params=wrapped?{...event.params,args:{...event.params.args,viewport:{width:801,height:600}}}:{...event.params,viewport:{width:801,height:600}};
  if(fault==='session')ctx.sessionId='foreign-session';
  if(fault!=='pending'){guard.afterToolCall({...event,result},ctx);persist(event,result,ctx);}
  if(fault==='pending'){
    assert.equal(await guard.revalidateWorkspacePreview({},context),false);
    assert.equal(probes,0);return;
  }
  const grepResult={content:[{type:'text',text:'<h1>Site</h1>'}],details:{status:'completed',exitCode:0}};
  const grep=call(guard,'exec',{command:"grep -o '<h1>[^<]*</h1>' site/index.html"},'final-grep',grepResult);
  persist(grep.event,grepResult,grep.ctx);
  assert.notEqual(guard.verificationForRun(context.runId).status,'passed');
  // Inspection is read-only for currency: host bytes alone restore it, while
  // an unbound interaction receipt still fails delivery independently.
  assert.equal(await guard.revalidateWorkspacePreview({},context),fault!=='host-bytes');
  assert.equal(probes,1);
  assert.equal(guard.verificationForRun(context.runId).status,fault==='none'?'passed':'failed');
});

test('visibility gate only requests checks supported by the installed capability',()=>{
  for(const text of ['A button shows details.','Click to hide the section.','Implement a toggle.']) assert.equal(requestsVisibilityInteraction(text),true,text);
  for(const text of ['Create a contact form.','Make a beautiful static website.','Explain a toggle.','Do not add a show button.']) assert.equal(requestsVisibilityInteraction(text),false,text);
  for(const config of [{enabled:false},{prompt:'Create and publish a static website in a new workspace directory site.'},{prompt:'Create and publish a website in a new workspace directory site.\n> A button shows details.'}]) {
    const {guard}=setup(config);assert.equal(guard.verificationForRun('run').status,'passed');
  }
});
test('published links remain available without falsely passing missing interaction evidence',()=>{
  const {guard,preview}=setup();const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,'failed');assert.equal(outcome.preview.sha256,preview.sha256);assert.match(outcome.text,/behavior remains unverified/);
  const retry=guard.beforeAgentFinalize({},context)?.retry;assert.equal(retry?.idempotencyKey,'pixel-ods-workspace-preview-interaction');assert.equal(retry?.maxAttempts,1);
  const guidance=guard.toolResultPersist({message:{role:'toolResult',toolName:'pixel_ods_workspace_preview',toolCallId:'publish',content:[{type:'text',text:'published'}]}},{...context,toolCallId:'publish'});
  assert.match(JSON.stringify(guidance),/pixel_ods_workspace_preview_inspect/);
  // The inspection tool is directly visible to Pixel. Naming the Tool Search
  // route steered strixy round 107 into tool_call {id:"tool_describe"}, which
  // OpenClaw cannot resolve, and into unshaped tool_call arguments.
  assert.doesNotMatch(retry.instruction,/tool_describe/);
  assert.doesNotMatch(retry.instruction,/tool_call/);
  assert.equal(retry.instruction,visibilityInspectionInstruction(preview));
  assert.match(retry.instruction,/call pixel_ods_workspace_preview_inspect directly/);
  assert.ok(retry.instruction.includes(preview.sha256));
  assert.ok(retry.instruction.includes(preview.siteId));
});

for(const wrapped of [false,true]) test(`bad inspection arguments preserve one bounded correction (${wrapped?'deferred':'direct'})`,async()=>{
  const {guard,preview}=setup();
  const params={...plan(preview),sha256:'3341'};
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:params}:params;
  const started=call(guard,name,args,'invalid');
  const inner=await createWorkspacePreviewInspectTool({request:async()=>{throw Error('must not execute');}}).execute('invalid',params);
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  guard.afterToolCall({...started.event,result},started.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  const retry=guard.beforeAgentFinalize({},context)?.retry;
  assert.equal(retry?.idempotencyKey,'pixel-ods-workspace-preview-interaction');
  assert.equal(retry?.maxAttempts,1);
  const corrected=inspection(guard,plan(preview),{wrapped,id:'corrected'});
  guard.afterToolCall({...corrected.event,result:corrected.result},corrected.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
});
for(const wrapped of [false,true]) test(`only current-run exact call receipt passes (${wrapped?'deferred':'direct'})`,()=>{
  const {guard,preview}=setup();const p=plan(preview);const {event,ctx,result}=inspection(guard,p,{wrapped});
  guard.afterToolCall({...event,result},ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.equal(guard.beforeAgentFinalize({},context),undefined);
});
for(const variant of ['wrong-run','wrong-call','wrong-session','wrong-session-key','changed-params','wrong-hash','wrong-plan','outer-error','wrong-tool','wrong-source','unbound']) test(`inspection rejects ${variant}`,()=>{
  const {guard,preview}=setup();let {event,ctx,result}=inspection(guard,plan(preview),{wrapped:true});
  if(variant==='wrong-run')event.runId='other';
  if(variant==='wrong-call')event.toolCallId='other';
  if(variant==='wrong-session')ctx.sessionId='other';
  if(variant==='wrong-session-key')ctx.sessionKey='other';
  if(variant==='changed-params')event.params=structuredClone(event.params),event.params.args.steps[0].locator.selector='#unrelated';
  if(variant==='wrong-hash')result.details.result.details.sha256='f'.repeat(64);
  if(variant==='wrong-plan')result.details.result.details.planSha256='e'.repeat(64);
  if(variant==='outer-error')event.error='transport failed';
  if(variant==='wrong-tool')result.details.tool.name='browser';
  if(variant==='wrong-source')result.details.tool.sourceName='foreign';
  if(variant==='unbound')ctx.toolCallId='unbound',event.toolCallId='unbound';
  guard.afterToolCall({...event,result},ctx);assert.equal(guard.verificationForRun('run').status,'failed');
});
test('clicks, unrelated before/after elements and unchanged visibility cannot satisfy transition evidence',()=>{
  const {preview}=setup();
  for(const mutate of [p=>p.steps.splice(0,1),p=>p.steps.splice(2,1),p=>p.steps[2].locator.selector='#different',p=>p.steps[2].action='assert-hidden']) {
    const p=plan(preview);mutate(p);assert.equal(boundVisibilityInspection(p,{details:receipt(p)},preview),undefined);
  }
});
test('later unavailable inspection revokes success, preserves preview, and does not ask to retry unavailable tool',()=>{
  const {guard,preview}=setup();let a=inspection(guard,plan(preview));guard.afterToolCall({...a.event,result:a.result},a.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  a=inspection(guard,plan(preview),{id:'second'});a.result={isError:true,details:{errorCode:'unavailable'}};
  guard.afterToolCall({...a.event,result:a.result},a.ctx);assert.equal(guard.verificationForRun('run').status,'failed');
  assert.equal(guard.beforeAgentFinalize({},context),undefined);
});
for(const wrapped of [false,true]) test(`selector syntax failure revokes proof until an actual corrected transition (${wrapped?'deferred':'direct'})`,async()=>{
  const {guard,preview}=setup();
  const first=inspection(guard,plan(preview));guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const params=plan(preview);params.steps[0].locator={selector:'article:contains("details")'};
  const failed=receipt(params);failed.status='failed';
  failed.steps=[{index:0,...params.steps[0],stable:false,status:'failed',errorCode:'invalid_selector'}];
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:params}:params;
  const started=call(guard,name,args,'syntax');
  const inner=await createWorkspacePreviewInspectTool({request:async()=>failed}).execute('syntax',params);
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  guard.afterToolCall({...started.event,result},started.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.equal(guard.beforeAgentFinalize({},context)?.retry?.maxAttempts,1);
  const corrected=inspection(guard,plan(preview),{wrapped,id:'corrected'});
  guard.afterToolCall({...corrected.event,result:corrected.result},corrected.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
});

test('different run cannot inherit previous interaction proof',()=>{
  const {guard,preview}=setup();const a=inspection(guard,plan(preview));guard.afterToolCall({...a.event,result:a.result},a.ctx);
  guard.observeRun({...context,runId:'next'},'pixel',{prompt:owner});assert.notEqual(guard.verificationForRun('next').status,'passed');
});
test('session identity changes within a run invalidate interaction proof',()=>{
  for(const patch of [{sessionId:'other'},{sessionKey:'other-key'}]) {
    const {guard,preview}=setup();const a=inspection(guard,plan(preview));guard.afterToolCall({...a.event,result:a.result},a.ctx);
    assert.equal(guard.verificationForRun('run').status,'passed');
    guard.observeRun({...context,...patch},'pixel',{prompt:owner});
    assert.equal(guard.verificationForRun('run').status,'failed');
  }
});


test('automatic preview delivery preserves the interaction gate until exact evidence arrives',async()=>{
  const {guard,preview}=setup({automatic:true});
  assert.equal(await guard.recoverWorkspacePreview({},context),true);
  const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,'failed');assert.equal(outcome.preview.sha256,preview.sha256);
  assert.equal(guard.beforeAgentFinalize({},context)?.retry?.idempotencyKey,'pixel-ods-workspace-preview-interaction');
  const a=inspection(guard,plan(preview));guard.afterToolCall({...a.event,result:a.result},a.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
});

function revisePublishedSite(guard,preview,{prompt='Update that same website: change its accent. Preserve the existing behavior and publish the updated preview.',
    sessionKey=context.sessionKey,runId='followup'}={}) {
  const ctx={...context,runId,sessionKey};
  guard.observeRun(ctx,'pixel',{prompt});
  const path=preview.relativeDirectory+'/index.html';
  call(guard,'read',{path},'followup-read',{content:[{type:'text',text:'<!doctype html><button>Show details</button><p hidden>Details</p>'}]},ctx);
  const content='<!doctype html><title>New accent</title><button>Show details</button><p id="details" hidden>Details</p>';
  call(guard,'write',{path,content},'followup-write',{content:[{type:'text',text:'Successfully wrote file.'}]},ctx);
  const name=Buffer.from('index.html'),data=Buffer.from(content),a=Buffer.alloc(4),b=Buffer.alloc(8);
  a.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(data.length));
  const sha256=createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId='site-'+sha256.slice(0,24);
  const next={...preview,sha256,siteId,entrySha256:createHash('sha256').update(data).digest('hex'),bytes:data.length,
    url:`http://${siteId}.localhost:9437/${siteId}/`};
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'followup-publish',{details:next},ctx);
  return {ctx,preview:next};
}

test('explicit preservation inherits an owner-bound interaction duty but never its previous passing proof',()=>{
  const {guard,preview}=setup();
  const first=inspection(guard,plan(preview));guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun(context.runId).status,'passed');
  const next=revisePublishedSite(guard,preview);
  assert.equal(guard.verificationForRun(next.ctx.runId).status,'failed');
  assert.match(guard.verificationForRun(next.ctx.runId).text,/show\/hide interaction/);
  assert.equal(guard.beforeAgentFinalize({},next.ctx)?.retry.idempotencyKey,'pixel-ods-workspace-preview-interaction');
  const check=inspection(guard,plan(next.preview),{runContext:next.ctx,id:'fresh-inspection'});
  guard.afterToolCall({...check.event,result:check.result},check.ctx);
  assert.equal(guard.verificationForRun(next.ctx.runId).status,'passed');
});

test('an unverified original interaction remains an obligation during explicit preservation',()=>{
  const {guard,preview}=setup();
  const next=revisePublishedSite(guard,preview);
  assert.equal(guard.verificationForRun(next.ctx.runId).status,'failed');
  assert.match(guard.verificationForRun(next.ctx.runId).text,/show\/hide interaction/);
});

test('preservation does not invent an interaction for a previously static publication',()=>{
  const {guard,preview}=setup({prompt:'Create and publish a static website in a new workspace directory site.'});
  const next=revisePublishedSite(guard,preview);
  assert.equal(guard.verificationForRun(next.ctx.runId).status,'passed');
});

test('preservation cannot inherit across owner session keys or quoted and negated requests',()=>{
  for(const change of [
    {sessionKey:'different-owner'},
    {prompt:'Update that same website: change its accent. Do not preserve the previous behavior. Publish the updated preview.'},
    {prompt:'Update that same website: change its accent.\n> Preserve the previous behavior.\nPublish the updated preview.'},
    {prompt:'Update that same website: change its accent. Display the words "Preserve the previous behavior". Publish the updated preview.'},
    {prompt:'Update that same website: change its accent and publish the updated preview.'},
  ]) {
    const {guard,preview}=setup();
    const next=revisePublishedSite(guard,preview,change);
    assert.doesNotMatch(guard.verificationForRun(next.ctx.runId).text,/show\/hide interaction/,JSON.stringify(change));
  }
});

test('behavior preservation recognition is generic and does not name a particular interaction',()=>{
  for(const text of ['Preserve the existing behavior.','Keep all interactions working.','Maintain its functionality.','Retain the previous behaviour.']) {
    assert.equal(requestsBehaviorPreservation(text),true,text);
  }
  for(const text of ['Explain how to preserve behavior.','Do not preserve its behavior.','Keep the same colors.','Its behavior works.']) {
    assert.equal(requestsBehaviorPreservation(text),false,text);
  }
});

test('a different new project cannot inherit the previous preview obligation',()=>{
  const {guard,preview}=setup();
  const ctx={...context,runId:'different-project'};
  guard.observeRun(ctx,'pixel',{prompt:'Create and publish a new static website in a new workspace directory other. Preserve its behavior.'});
  const written=call(guard,'write',{path:'other/index.html',content:'<!doctype html><h1>Static</h1>'},'other-write',
    {content:[{type:'text',text:'Successfully wrote file.'}]},ctx);
  // No receipt has been produced for this project, so there is no inherited
  // obligation or passing proof just because another site existed previously.
  assert.doesNotMatch(guard.beforeAgentFinalize({},ctx)?.retry?.instruction??'',/show\/hide interaction/);
  assert.notEqual(written.event.params.path,preview.relativeDirectory+'/index.html');
});

test('bounded session preview eviction also evicts the associated obligation',()=>{
  const {guard,preview}=setup();
  for(let i=0;i<256;i++) {
    const ctx={...context,runId:'eviction-run-'+i,sessionId:'eviction-session-'+i,sessionKey:'eviction-key-'+i};
    guard.observeRun(ctx,'pixel',{prompt:owner});
    const written=call(guard,'write',{path:'site/index.html',content:'<!doctype html><button>Show details</button><p hidden>Details</p>'},
      'write-'+i,{content:[{type:'text',text:'Successfully wrote file.'}]},ctx);
    call(guard,'pixel_ods_workspace_preview',{relativeDirectory:written.event.params.path.replace(/\/index.html$/,'')},
      'publish-'+i,{details:preview},ctx);
  }
  const next=revisePublishedSite(guard,preview);
  assert.doesNotMatch(guard.verificationForRun(next.ctx.runId).text,/show\/hide interaction/);
});

// A plan without a click, or one click whose later assertions all name a
// proved target exactly in its proved state (tower2 round 108) or none, cannot
// show the proved change failing. Any other assertion after a click can: it may
// be the proved target under another locator, or another element the change
// was meant to reach, even in the proved state.
const KEEPS_PROOF=new Set(['none','click-without-transition','target-visible-at-load']);
for (const wrapped of [false,true]) for (const fault of [
  'none','no-prior-proof','failed','unavailable','inner-error','outer-error','receipt-sha','receipt-plan',
  'params','session','session-key','source','changed-bytes','click-without-transition','failed-click',
  'target-unchanged-by-click','target-visible-at-load','other-control-unchanged','other-control-proved-state',
  'aliased-target-unchanged','aliased-control-unchanged',
]) test(`a later passing inspection preserves only existing bound interaction: wrapped=${wrapped}, fault=${fault}`,()=>{
  const {guard,preview}=setup();
  if (fault!=='no-prior-proof') {
    const first=inspection(guard,plan(preview),{wrapped,id:'mobile-transition'});
    guard.afterToolCall({...first.event,result:first.result},first.ctx);
    assert.equal(guard.verificationForRun('run').status,'passed');
  }
  if (fault==='changed-bytes') call(guard,'write',{path:'site/index.html',content:'<!doctype html><h1>Changed</h1>'},'changed-write',{content:[{type:'text',text:'Successfully wrote file.'}]});
  const params={...plan(preview),viewport:{width:1024,height:768},steps:[
    {action:'assert-visible',locator:{selector:'button'}},
    {action:'assert-hidden',locator:{selector:'#details'}},
  ]};
  const control={action:'click',locator:{role:'button',name:'Show details',exact:true}};
  if (['click-without-transition','failed-click'].includes(fault)) params.steps.push(control);
  if (fault==='target-unchanged-by-click') params.steps.push(control,{action:'assert-hidden',locator:{selector:'#details'}});
  if (fault==='target-visible-at-load') params.steps[1].action='assert-visible';
  if (fault==='other-control-unchanged') params.steps.push({action:'click',locator:{selector:'#other'}},{action:'assert-hidden',locator:{selector:'#details'}});
  if (fault==='other-control-proved-state') params.steps.push({action:'click',locator:{selector:'#other'}},{action:'assert-visible',locator:{selector:'button'}});
  if (fault==='aliased-target-unchanged') params.steps.push(control,{action:'assert-hidden',locator:{selector:'p#details'}});
  if (fault==='aliased-control-unchanged') params.steps.push({action:'click',locator:{selector:'#show'}},{action:'assert-hidden',locator:{selector:'#details'}});
  const next=inspection(guard,params,{wrapped,id:'desktop-static'});
  const result=structuredClone(next.result),event={...next.event},ctx={...next.ctx};
  const inner=wrapped?result.details.result:result;
  if (fault==='failed') {inner.details.status='failed';inner.details.steps[0].status='failed';}
  if (fault==='failed-click') {inner.details.status='failed';inner.details.steps[2].status='failed';inner.details.steps[2].errorCode='click_failed';}
  if (fault==='unavailable') {inner.isError=true;inner.details={errorCode:'unavailable'};}
  if (fault==='inner-error') inner.isError=true;
  if (fault==='outer-error') result.isError=true;
  if (fault==='receipt-sha') inner.details.sha256='a'.repeat(64);
  if (fault==='receipt-plan') inner.details.planSha256='b'.repeat(64);
  if (fault==='params') event.params=wrapped?{...event.params,args:{...params,viewport:{width:1025,height:768}}}:{...params,viewport:{width:1025,height:768}};
  if (fault==='session') ctx.sessionId='other';
  if (fault==='session-key') ctx.sessionKey='other';
  if (fault==='source') { if (wrapped) result.details.tool.sourceName='foreign'; else event.toolName='foreign'; }
  guard.afterToolCall({...event,result},ctx);
  assert.equal(guard.verificationForRun('run').status,KEEPS_PROOF.has(fault)?'passed':'failed');
  if (KEEPS_PROOF.has(fault)) assert.equal(guard.beforeAgentFinalize({},context),undefined);
});

// Review of #6754: which proofs a later passing plan with a click keeps. A
// change the proof saw only after a preparatory or second click names no
// clicked control, so any later click can test it. A later plan with two
// clicks keeps nothing. With one click, each assertion must be one proved
// target, exactly: before the click in its load state, after it in its proved
// state. Another element in the proved state is not the proved target: it can
// be one the requested change failed to reach (a card a self-hiding button
// never revealed, a summary a reveal never hid, a panel a tab never replaced).
{
  const SHOW={role:'button',name:'Show details',exact:true},D={selector:'#details'},S={selector:'#summary'};
  const step=(action,locator)=>({action,locator});
  const PROOFS={
    reveal:[step('assert-hidden',D),step('click',SHOW),step('assert-visible',D)],
    conceal:[step('assert-visible',D),step('click',SHOW),step('assert-hidden',D)],
    both:[step('assert-hidden',D),step('assert-visible',S),step('click',SHOW),step('assert-visible',D),step('assert-hidden',S)],
    'second-click':[step('assert-hidden',D),step('click',{selector:'#menu'}),step('click',SHOW),step('assert-visible',D)],
    'double-click':[step('assert-hidden',D),step('click',SHOW),step('click',SHOW),step('assert-visible',D)],
    'preparatory-click':[step('click',{selector:'#accept'}),step('assert-hidden',D),step('click',SHOW),step('assert-visible',D)],
  };
  for (const [proof,later,keeps] of [
    ['reveal',[step('assert-hidden',{selector:'p#details'}),step('click',SHOW),step('assert-hidden',{selector:'p#details'})],false],
    ['reveal',[step('click',{selector:'button'}),step('assert-hidden',D)],false],
    ['reveal',[step('assert-visible',{selector:'h1'}),step('click',{selector:'#show'}),step('assert-visible',{selector:'p#details'})],false],
    ['reveal',[step('assert-visible',{selector:'h1'}),step('click',{selector:'#show'}),step('assert-visible',D)],true],
    ['reveal',[step('assert-hidden',D),step('click',SHOW),step('assert-visible',D)],true],
    ['reveal',[step('assert-visible',D),step('click',SHOW),step('assert-visible',D)],false],
    ['reveal',[step('click',SHOW),step('assert-visible',S)],false],
    ['reveal',[step('assert-visible',D),step('click',SHOW)],false],
    ['reveal',[step('click',SHOW),step('click',SHOW),step('assert-visible',D)],false],
    ['reveal',[step('click',SHOW),step('assert-visible',D),step('click',SHOW),step('assert-hidden',{selector:'.toast'})],false],
    ['conceal',[step('click',SHOW),step('assert-hidden',{selector:'p#details'})],false],
    ['conceal',[step('click',SHOW),step('assert-hidden',D)],true],
    ['conceal',[step('assert-hidden',{selector:'h1'}),step('click',SHOW),step('assert-hidden',D)],true],
    ['conceal',[step('click',SHOW),step('assert-visible',{selector:'p#details'})],false],
    ['both',[step('click',SHOW),step('assert-visible',D),step('assert-hidden',S)],true],
    ['both',[step('click',SHOW),step('assert-hidden',{selector:'p#details'})],false],
    ['both',[step('click',SHOW),step('assert-visible',{selector:'p#summary'})],false],
    ['second-click',[step('assert-hidden',D),step('click',SHOW),step('assert-hidden',D)],false],
    ['second-click',[step('assert-visible',{selector:'h1'}),step('click',SHOW),step('assert-visible',D)],false],
    ['second-click',[step('assert-visible',{selector:'h1'}),step('assert-hidden',D)],true],
    ['double-click',[step('assert-hidden',D),step('click',SHOW),step('assert-hidden',D)],false],
    ['preparatory-click',[step('click',SHOW)],false],
    ['preparatory-click',[step('assert-visible',{selector:'button'})],true],
  ]) for (const wrapped of [false,true]) for (const width of [800,1280]) {
    test(`a later passing plan ${keeps?'keeps':'revokes'} a ${proof} proof: wrapped=${wrapped}, width=${width}, ${JSON.stringify(later)}`,()=>{
      const {guard,preview}=setup();
      const first=inspection(guard,{...plan(preview),steps:PROOFS[proof]},{wrapped,id:'proof'});
      guard.afterToolCall({...first.event,result:first.result},first.ctx);
      assert.equal(guard.verificationForRun('run').status,'passed');
      const next=inspection(guard,{...plan(preview),viewport:{width,height:720},steps:later},{wrapped,id:'later'});
      guard.afterToolCall({...next.event,result:next.result},next.ctx);
      assert.equal(guard.verificationForRun('run').status,keeps?'passed':'failed');
    });
  }
}

for (const wrapped of [false,true]) test(`unfinished or stale static receipt cannot restore proof after a newer failure: wrapped=${wrapped}`,()=>{
  const {guard,preview}=setup();
  const first=inspection(guard,plan(preview),{wrapped,id:'transition'});
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const params={...plan(preview),steps:[{action:'assert-visible',locator:{selector:'button'}}]};
  const older=inspection(guard,params,{wrapped,id:'pending-static'});
  assert.equal(guard.verificationForRun('run').status,'failed');
  const newer=inspection(guard,params,{wrapped,id:'newer-static'});
  const inner=wrapped?newer.result.details.result:newer.result;
  inner.isError=true;
  guard.afterToolCall({...newer.event,result:newer.result},newer.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  guard.afterToolCall({...older.event,result:older.result},older.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
});

// Fleet round 054: every functional browser check passed while the page threw
// uncaught storage errors. Passing steps on a throwing page are not verified.
const STORAGE_ERROR="SecurityError: Failed to read the 'sessionStorage' property from 'Window': The document is sandboxed and lacks the 'allow-same-origin' flag.";
function withPageErrors(params) { return {...receipt(params), pageErrors:{count:3, messages:[STORAGE_ERROR]}}; }
function erroredInspection(guard,params,options={}) {
  const observed=inspection(guard,params,options);
  (options.wrapped ? observed.result.details.result : observed.result).details=withPageErrors(params);
  return observed;
}
function persistResult(guard,{event,result,ctx}) {
  return JSON.stringify(guard.toolResultPersist({toolName:event.toolName,toolCallId:event.toolCallId,
    message:{role:'toolResult',toolName:event.toolName,toolCallId:event.toolCallId,content:[{type:'text',text:'inspection'}],...result}},ctx) ?? null);
}
function republish(guard,preview,content,id) {
  const path=preview.relativeDirectory+'/index.html';
  call(guard,'write',{path,content},id+'-write',{content:[{type:'text',text:'Successfully wrote file.'}]});
  const name=Buffer.from('index.html'),data=Buffer.from(content),a=Buffer.alloc(4),b=Buffer.alloc(8);
  a.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(data.length));
  const sha256=createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId='site-'+sha256.slice(0,24);
  const next={...preview,sha256,siteId,entrySha256:createHash('sha256').update(data).digest('hex'),bytes:data.length,
    url:`http://${siteId}.localhost:9437/${siteId}/`};
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},id+'-publish',{details:next});
  return next;
}

for (const wrapped of [false,true]) test(`page errors withhold interaction proof and select one stable repair step (${wrapped?'deferred':'direct'})`,()=>{
  const {guard,preview}=setup();
  const errored=erroredInspection(guard,plan(preview),{wrapped,id:'errored'});
  guard.afterToolCall({...errored.event,result:errored.result},errored.ctx);
  assert.equal(boundVisibilityInspection(plan(preview),{details:withPageErrors(plan(preview))},preview),undefined);
  const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,'failed');
  assert.equal(outcome.preview.sha256,preview.sha256,'the publication stays deliverable');
  assert.match(outcome.text,/show\/hide interaction has not passed browser inspection/);
  const retry=guard.beforeAgentFinalize({},context)?.retry;
  assert.equal(retry?.idempotencyKey,'pixel-ods-workspace-preview-interaction');
  assert.equal(retry.instruction,PAGE_ERROR_REPAIR_INSTRUCTION);
  assert.doesNotMatch(PAGE_ERROR_REPAIR_INSTRUCTION,/site-|[a-f0-9]{24}|\d+ uncaught/,'stable text for per-slot coaching dedupe');
  const persisted=persistResult(guard,errored);
  assert.ok(persisted.includes('[ODS Pixel next step] '+PAGE_ERROR_REPAIR_INSTRUCTION),persisted);
  assert.ok(!persisted.includes(WORKSPACE_PREVIEW_COMPLETE_REASON));
  // Republishing the unchanged bytes keeps the same snapshot and the same repair step.
  const fixed=republish(guard,preview,'<!doctype html><button>Show details</button><p id="details" hidden>Details</p>'+
    '<script>let seen;try{seen=sessionStorage.getItem("seen")}catch{seen=null}</script>','fixed');
  assert.notEqual(fixed.sha256,preview.sha256);
  const generic=guard.beforeAgentFinalize({},context)?.retry?.instruction;
  assert.notEqual(generic,PAGE_ERROR_REPAIR_INSTRUCTION,'errors of an older snapshot never describe the new one');
  assert.ok(generic.includes(fixed.sha256));
  const clean=inspection(guard,plan(fixed),{wrapped,id:'clean'});
  guard.afterToolCall({...clean.event,result:clean.result},clean.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
});

test('a static inspection that records page errors cannot preserve earlier interaction proof',()=>{
  const {guard,preview}=setup();
  const first=inspection(guard,plan(preview),{id:'transition'});
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const staticPlan={...plan(preview),viewport:{width:1024,height:768},steps:[{action:'assert-visible',locator:{selector:'button'}}]};
  const proof=boundVisibilityInspection(plan(preview),{details:receipt(plan(preview))},preview);
  assert.equal(preservesVisibilityInspection(proof,staticPlan,{details:receipt(staticPlan)},preview),true);
  assert.equal(preservesVisibilityInspection(proof,staticPlan,{details:withPageErrors(staticPlan)},preview),false);
  const errored=erroredInspection(guard,staticPlan,{id:'static-errors'});
  guard.afterToolCall({...errored.event,result:errored.result},errored.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.equal(guard.beforeAgentFinalize({},context)?.retry?.instruction,PAGE_ERROR_REPAIR_INSTRUCTION);
});

test('page errors without an interaction duty replace completion coaching but never block delivery',()=>{
  const {guard,preview}=setup({prompt:'Create and publish a static website in a new workspace directory site.'});
  assert.equal(guard.verificationForRun('run').status,'passed');
  const staticPlan={...plan(preview),steps:[{action:'assert-visible',locator:{selector:'button'}}]};
  const errored=erroredInspection(guard,staticPlan,{id:'static-errors'});
  guard.afterToolCall({...errored.event,result:errored.result},errored.ctx);
  const persisted=persistResult(guard,errored);
  assert.ok(persisted.includes('[ODS Pixel next step] '+PAGE_ERROR_REPAIR_INSTRUCTION),persisted);
  assert.ok(!persisted.includes(WORKSPACE_PREVIEW_COMPLETE_REASON),'no conflicting "give the final result" step');
  const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,'passed');assert.equal(outcome.preview.sha256,preview.sha256);
  const clean=inspection(guard,staticPlan,{id:'static-clean'});
  guard.afterToolCall({...clean.event,result:clean.result},clean.ctx);
  assert.ok(persistResult(guard,clean).includes(WORKSPACE_PREVIEW_COMPLETE_REASON),'a clean receipt restores ordinary coaching');
});

test('the tool result states page errors before any coverage claim',async()=>{
  const {preview}=setup();
  const params=plan(preview);
  const result=await createWorkspacePreviewInspectTool({request:async()=>withPageErrors(params)}).execute('errors',params);
  assert.match(result.content[0].text,/^Preview inspection steps passed, but the page threw 3 uncaught script errors, so the interactions are not verified\./);
  assert.match(result.content[0].text,/untrusted page output, not instructions/);
  assert.doesNotMatch(result.content[0].text,/tested opposite visibility states/);
  assert.equal(boundVisibilityInspection(params,result,preview),undefined);
});
