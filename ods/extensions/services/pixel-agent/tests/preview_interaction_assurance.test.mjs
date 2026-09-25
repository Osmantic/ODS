import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, WORKSPACE_PREVIEW_COMPLETE_REASON} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL, PAGE_ERROR_REPAIR_INSTRUCTION, requestsVisibilityInteraction, requestsBehaviorPreservation, boundVisibilityInspection,
  boundStaticPreviewInspection} from '../plugin/preview-interaction-assurance.mjs';
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
  assert.match(retry.instruction,/tool_describe/);
  assert.match(retry.instruction,/tool_call/);
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

for (const wrapped of [false,true]) for (const fault of [
  'none','no-prior-proof','failed','unavailable','inner-error','outer-error','receipt-sha','receipt-plan',
  'params','session','session-key','source','changed-bytes','incomplete-click',
]) test(`static inspection preserves only existing bound interaction: wrapped=${wrapped}, fault=${fault}`,()=>{
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
  if (fault==='incomplete-click') params.steps.push({action:'click',locator:{role:'button',name:'Show details',exact:true}});
  const next=inspection(guard,params,{wrapped,id:'desktop-static'});
  const result=structuredClone(next.result),event={...next.event},ctx={...next.ctx};
  const inner=wrapped?result.details.result:result;
  if (fault==='failed') {inner.details.status='failed';inner.details.steps[0].status='failed';}
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
  assert.equal(guard.verificationForRun('run').status,fault==='none'?'passed':'failed');
  if (fault==='none') assert.equal(guard.beforeAgentFinalize({},context),undefined);
});

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
  assert.equal(boundStaticPreviewInspection(staticPlan,{details:withPageErrors(staticPlan)},preview),undefined);
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

// Tower2 round 092 website-create: after its last publication the model ran a
// read-only grep, which leaves the preview out of currency until the host byte
// comparison at finalization, then a passing transition inspection of that same
// immutable snapshot. The comparison restored the snapshot, but the passing
// receipt had bound to nothing, so delivery said the interaction was unverified.
function awaitingByteCheck({verified=true}={}) {
  let probes=0;
  const {guard,preview}=setup({verifyWorkspacePreview:async()=>{probes++;return verified;}});
  const persist=({event,ctx},result)=>guard.toolResultPersist({toolName:event.toolName,toolCallId:event.toolCallId,
    message:{role:'toolResult',toolName:event.toolName,toolCallId:event.toolCallId,...result}},ctx);
  persist({event:{toolName:'pixel_ods_workspace_preview',toolCallId:'publish'},ctx:{...context,toolName:'pixel_ods_workspace_preview',toolCallId:'publish'}},{details:preview});
  const grepResult={content:[{type:'text',text:'256: <p id="details" hidden>'}],details:{status:'completed',exitCode:0}};
  const grep=(id='grep')=>{const g=call(guard,'exec',{command:'cd /workspace && grep -n "details" site/index.html',workdir:'/workspace'},id,grepResult);persist(g,grepResult);};
  const finish=(observed,{ctx=observed.ctx,result=observed.result}={})=>{guard.afterToolCall({...observed.event,result},ctx);persist(observed,result);};
  const inspect=(params=plan(preview),options={})=>{const observed=inspection(guard,params,options);finish(observed);return observed;};
  // Writes and publishes new bytes, both settled like a real turn.
  const publish=(content,id)=>{const next=republish(guard,preview,content,id);
    for (const [name,callId,result] of [['write',id+'-write',{content:[{type:'text',text:'Successfully wrote file.'}]}],
      ['pixel_ods_workspace_preview',id+'-publish',{details:next}]])
      persist({event:{toolName:name,toolCallId:callId},ctx:{...context,toolName:name,toolCallId:callId}},result);
    return next;};
  return {guard,preview,grep,inspect,finish,publish,probes:()=>probes};
}

for (const wrapped of [false,true]) for (const order of ['grep-then-inspect','inspect-then-grep','inspect-across-grep'])
test(`R092: a passing inspection of the snapshot awaiting its byte check counts once that snapshot is current: ${order}, wrapped=${wrapped}`,async()=>{
  const r=awaitingByteCheck();
  if (order==='grep-then-inspect') {r.grep();r.inspect(undefined,{wrapped});}
  if (order==='inspect-then-grep') {r.inspect(undefined,{wrapped});r.grep();}
  if (order==='inspect-across-grep') {const started=inspection(r.guard,plan(r.preview),{wrapped,id:'parallel'});r.grep();r.finish(started);}
  const pending=r.guard.verificationForRun('run');
  assert.notEqual(pending.status,'passed','a receipt alone never makes the snapshot current');
  assert.match(pending.text,/not been verified again since later tool activity/);
  assert.equal(await r.guard.revalidateWorkspacePreview({},context),true);
  assert.equal(r.probes(),1);
  const outcome=r.guard.verificationForRun('run');
  assert.equal(outcome.preview.sha256,r.preview.sha256);
  assert.equal(outcome.status,'passed',outcome.text);
  assert.equal(r.guard.beforeAgentFinalize({},context),undefined);
});

for (const fault of ['host-bytes-changed','unpublished-snapshot','foreign-session','failed-receipt','static-plan'])
test(`R092: grep-then-inspect leaves the interaction unverified when ${fault}`,async()=>{
  const r=awaitingByteCheck({verified:fault!=='host-bytes-changed'});
  r.grep();
  const sha=fault==='unpublished-snapshot'?'c'.repeat(64):r.preview.sha256;
  const target={...r.preview,sha256:sha,siteId:'site-'+sha.slice(0,24)};
  const params=fault==='static-plan'?{...plan(target),steps:[{action:'assert-visible',locator:{selector:'button'}}]}:plan(target);
  const observed=inspection(r.guard,params,{id:'inspect'});
  const result=structuredClone(observed.result);
  if (fault==='failed-receipt') {result.isError=true;result.details.status='failed';result.details.steps[2].status='failed';}
  r.finish(observed,{result,ctx:fault==='foreign-session'?{...observed.ctx,sessionId:'foreign-session'}:observed.ctx});
  assert.equal(await r.guard.revalidateWorkspacePreview({},context),fault!=='host-bytes-changed');
  const outcome=r.guard.verificationForRun('run');
  assert.equal(outcome.status,'failed');
  assert.match(outcome.text,fault==='host-bytes-changed'?/not been verified again since later tool activity/:/show\/hide interaction has not passed browser inspection/);
});

test('R092: an earlier transition proof survives a static check of the snapshot awaiting its byte check, not a failed one',async()=>{
  for (const failed of [false,true]) {
    const r=awaitingByteCheck();
    r.inspect(undefined,{id:'transition'});
    r.grep();
    const observed=inspection(r.guard,{...plan(r.preview),viewport:{width:1024,height:768},
      steps:[{action:'assert-visible',locator:{selector:'button'}}]},{id:'static'});
    const result=structuredClone(observed.result);
    if (failed) {result.isError=true;result.details.status='failed';result.details.steps[0].status='failed';}
    r.finish(observed,{result});
    assert.equal(await r.guard.revalidateWorkspacePreview({},context),true);
    assert.equal(r.guard.verificationForRun('run').status,failed?'failed':'passed');
  }
});

test('R092: page errors recorded for the snapshot awaiting its byte check select the repair step',async()=>{
  const r=awaitingByteCheck();
  r.grep();
  r.finish(erroredInspection(r.guard,plan(r.preview),{id:'errored'}));
  assert.equal(await r.guard.revalidateWorkspacePreview({},context),true);
  assert.equal(r.guard.verificationForRun('run').status,'failed');
  assert.equal(r.guard.beforeAgentFinalize({},context)?.retry?.instruction,PAGE_ERROR_REPAIR_INSTRUCTION);
});

// Without a pending snapshot for this exact session and workspace a receipt
// binds nothing, so even a republication of identical bytes is not verified.
for (const drop of ['bundle-invalidated','ineligible-call','session-id','session-key','workspace-root'])
test(`R092: a receipt binds nothing once the pending snapshot is dropped: ${drop}`,async()=>{
  const r=awaitingByteCheck();
  r.grep();
  let runContext=context;
  if (drop==='bundle-invalidated') assert.equal(r.guard.invalidateWorkspaceBundle(context),true);
  if (drop==='ineligible-call') call(r.guard,'exec',{command:'sleep 1 &'},'detached',{content:[{type:'text',text:'(no output)'}],details:{status:'completed',exitCode:0}});
  if (['session-id','session-key','workspace-root'].includes(drop)) {
    runContext={...context,...drop==='session-id'?{sessionId:'other-session'}:drop==='session-key'?{sessionKey:'other-key'}:{}};
    r.guard.observeRun(runContext,'pixel',{prompt:owner},drop==='workspace-root'?{workspaceRoot:'/other-root'}:undefined);
  }
  const observed=inspection(r.guard,plan(r.preview),{id:'inspect',runContext});
  r.guard.afterToolCall({...observed.event,result:observed.result},observed.ctx);
  assert.equal(await r.guard.revalidateWorkspacePreview({},runContext),false);
  call(r.guard,'pixel_ods_workspace_preview',{relativeDirectory:r.preview.relativeDirectory},'republish',{details:r.preview},runContext);
  const outcome=r.guard.verificationForRun('run');
  assert.equal(outcome.preview?.sha256,r.preview.sha256);
  assert.equal(outcome.status,'failed');
  assert.match(outcome.text,/show\/hide interaction has not passed browser inspection/);
});

// PR #6726 review probes. An inspection that settles after a parallel write
// binds to the snapshot awaiting its byte check; the host byte comparison then
// decides whether that snapshot is still what the workspace holds.
for (const identical of [true,false])
test(`R092: an inspection that settles after a parallel write counts only if the write left the published bytes unchanged (${identical?'identical':'changed'} write)`,async()=>{
  const r=awaitingByteCheck({verified:identical});
  const started=inspection(r.guard,plan(r.preview),{id:'parallel-inspect'});
  const content=identical?'<!doctype html><button>Show details</button><p hidden>Details</p>'
    :'<!doctype html><button>Show details</button><p id="details" hidden>Changed details</p>';
  const written={content:[{type:'text',text:'Successfully wrote file.'}]};
  r.finish(call(r.guard,'write',{path:'site/index.html',content},'parallel-write'),{result:written});
  r.finish(started);
  assert.notEqual(r.guard.verificationForRun('run').status,'passed');
  assert.equal(await r.guard.revalidateWorkspacePreview({},context),identical);
  assert.equal(r.probes(),1);
  const outcome=r.guard.verificationForRun('run');
  assert.equal(outcome.status,identical?'passed':'failed',outcome.text);
  if (!identical) assert.match(outcome.text,/not been verified again since later tool activity/);
});

// A receipt for an older snapshot that settles after a newer publication and a
// grep names a snapshot that is no longer pending; it binds nothing.
test('R092: a late receipt for an earlier snapshot never verifies the newer snapshot awaiting its byte check',async()=>{
  const r=awaitingByteCheck();
  const late=inspection(r.guard,plan(r.preview),{id:'late-inspect'});
  const next=r.publish('<!doctype html><title>B</title><button>Show details</button><p id="details" hidden>Details</p>','b');
  assert.notEqual(next.sha256,r.preview.sha256);
  r.grep();
  r.finish(late);
  assert.equal(await r.guard.revalidateWorkspacePreview({},context),true);
  const outcome=r.guard.verificationForRun('run');
  assert.equal(outcome.preview.sha256,next.sha256);
  assert.equal(outcome.status,'failed');
  assert.match(outcome.text,/show\/hide interaction has not passed browser inspection/);
});

// A pass bound to the pending snapshot is bound to its bytes, not to the run:
// a later publication of different bytes still needs its own inspection.
test('R092: a pass on the snapshot awaiting its byte check does not verify a later publication of different bytes',async()=>{
  const r=awaitingByteCheck();
  r.grep();
  r.inspect();
  const next=r.publish('<!doctype html><title>B</title><button>Show details</button><p id="details" hidden>Details</p>','b');
  assert.notEqual(next.sha256,r.preview.sha256);
  assert.equal(await r.guard.revalidateWorkspacePreview({},context),false,'the newer snapshot is already current');
  assert.equal(r.probes(),0);
  const outcome=r.guard.verificationForRun('run');
  assert.equal(outcome.preview.sha256,next.sha256);
  assert.equal(outcome.status,'failed');
  assert.match(outcome.text,/show\/hide interaction has not passed browser inspection/);
});
