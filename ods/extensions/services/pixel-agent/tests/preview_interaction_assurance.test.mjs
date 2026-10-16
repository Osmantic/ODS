import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createToolLoopGuard, userMessageRequestsWorkspaceVerificationContinuation, WORKSPACE_PREVIEW_COMPLETE_REASON} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL, PAGE_ERROR_REPAIR_INSTRUCTION, requestsVisibilityInteraction, requestsBehaviorPreservation, boundVisibilityInspection,
  boundStaticPreviewInspection} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, BEHAVIOR_UNTESTED, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams,
  createWorkspacePreviewInspectTool, previewBehaviorPlanIssue, validateIncompleteInspectionReceipt} from '../plugin/workspace-preview-inspect.mjs';

import {PREVIEW_STORAGE_DISCLOSURE} from '../plugin/workspace-preview.mjs';

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
  for(const text of ['A button shows details.','Click to hide the section.','Add a button that toggles visibility of the details panel.']) assert.equal(requestsVisibilityInteraction(text),true,text);
  for(const text of ['Create a contact form.','Make a beautiful static website.','Implement a toggle.','Explain a toggle.','Do not add a show button.']) assert.equal(requestsVisibilityInteraction(text),false,text);
  for(const config of [{enabled:false},{prompt:'Create and publish a static website in a new workspace directory site.'},{prompt:'Create and publish a website in a new workspace directory site.\n> A button shows details.'}]) {
    const {guard}=setup(config);assert.equal(guard.verificationForRun('run').status,config.enabled===false?'passed':'failed');
  }
});

test('static page contents beside form controls do not request show/hide inspection',()=>{
  const prompt='Create a tiny reading-list webpage in a new Playground/fleet-reading-list folder. It should show the title Fleet Reading List, a text box for a book title, and an Add book button that adds the title to the visible list. Save it as index.html, publish a workspace preview, and give me the preview link. Keep everything local; no external services.';
  for (const text of [prompt,
    'Show a heading and a Submit button.',
    'Add a visible list and a button that appends a book.',
    'Show the title, a contact form, and a Submit button.',
    'Add a Submit button and show the page title.',
  ]) assert.equal(requestsVisibilityInteraction(text), false, text);
  const {guard}=setup({prompt});
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.match(guard.verificationForRun('run').text,/browser check remains unverified/);
  assert.doesNotMatch(guard.verificationForRun('run').text,/show\/hide interaction/);
  for (const text of [
    'Show a title and a contact form with a button that reveals hidden help.',
    'Add a book button and a button that shows the details.',
    'A click makes the details visible.',
    'Show the details when the button is clicked.',
  ]) assert.equal(requestsVisibilityInteraction(text), true, text);
});

test('initial control-state corrections do not imply a new visibility transition',()=>{
  for (const text of [
    'First, wireframe is off initially, but its Show wireframe button starts with aria-pressed=true; initialise it to match the actual state.',
    'The "Show details" button starts with aria-pressed=true.',
    'The button named "Hide details" should start unpressed.',
    'Its Expand menu toggle defaults to aria-expanded=false.',
  ]) {
    assert.equal(requestsVisibilityInteraction(text), false, text);
  }
  for (const text of [
    'First, wireframe is off initially, but its Show wireframe button starts with aria-pressed=true; initialise it to match the actual state.',
    'The "Show details" button starts with aria-pressed=true.',
  ]) {
    const {guard} = setup({prompt:`Create and publish a website in a new workspace directory site. ${text}`});
    assert.equal(guard.verificationForRun('run').status, 'failed', text);
    assert.match(guard.verificationForRun('run').text, /browser check remains unverified/, text);
    assert.doesNotMatch(guard.verificationForRun('run').text, /show\/hide interaction/, text);
  }
});

test('control labels and aria state never remove real affected-content duties',()=>{
  for (const text of [
    'Add a "Show details" button.',
    'Add a "Show details" button that starts with aria-pressed=false.',
    'Create the Show details button starting with aria-pressed=false.',
  ]) assert.equal(requestsVisibilityInteraction(text), true, text);
  for (const text of [
    'Add a "Show details" button that reveals the hidden panel.',
    'The "Hide sidebar" button should hide the sidebar.',
    'Click the button to show "Results card".',
    'Show this card with a button.',
    'Click "Expand" to reveal the section.',
    'The Show details button starts with aria-pressed=false and reveals the panel on click.',
    'Its Show wireframe button starts with aria-pressed=true, and also add a button that shows the hidden details panel.',
  ]) {
    assert.equal(requestsVisibilityInteraction(text), true, text);
    const {guard} = setup({prompt:`Create and publish a website in a new workspace directory site. ${text}`});
    assert.equal(guard.verificationForRun('run').status, 'failed', text);
    assert.match(guard.verificationForRun('run').text, /show\/hide interaction/, text);
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

test('page errors without an interaction duty withhold verification while preserving the preview',()=>{
  const {guard,preview}=setup({prompt:'Create and publish a static website in a new workspace directory site.'});
  assert.equal(guard.verificationForRun('run').status,'failed');
  const staticPlan={...plan(preview),steps:[{action:'assert-visible',locator:{selector:'button'}}]};
  const errored=erroredInspection(guard,staticPlan,{id:'static-errors'});
  guard.afterToolCall({...errored.event,result:errored.result},errored.ctx);
  const persisted=persistResult(guard,errored);
  assert.ok(persisted.includes('[ODS Pixel next step] '+PAGE_ERROR_REPAIR_INSTRUCTION),persisted);
  assert.ok(!persisted.includes(WORKSPACE_PREVIEW_COMPLETE_REASON),'no conflicting "give the final result" step');
  const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,'failed');assert.equal(outcome.preview.sha256,preview.sha256);
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

// Real forest edit failed because a motion toggle was forced through show/hide.
test("visibility duty matches requested behavior: Please add a Pause motion / Resume motion toggle to this page so I can quiet the animated effects. Keep the design and publish the updated preview.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Please add a Pause motion / Resume motion toggle to this page so I can quiet the animated effects. Keep the design and publish the updated preview.";
  assert.equal(requestsVisibilityInteraction("Please add a Pause motion / Resume motion toggle to this page so I can quiet the animated effects. Keep the design and publish the updated preview."),false);
  const {guard}=setup({prompt});
  const delivery=guard.verificationForRun('run');
  assert.equal(delivery.status,'failed');
  assert.match(delivery.text,/browser check remains unverified/);
  assert.doesNotMatch(delivery.text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Add a dark mode toggle to the header.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Add a dark mode toggle to the header.";
  assert.equal(requestsVisibilityInteraction("Add a dark mode toggle to the header."),false);
  const {guard}=setup({prompt});
  const delivery=guard.verificationForRun('run');
  assert.equal(delivery.status,'failed');
  assert.match(delivery.text,/browser check remains unverified/);
  assert.doesNotMatch(delivery.text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Add a button that toggles the accent color between blue and green.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Add a button that toggles the accent color between blue and green.";
  assert.equal(requestsVisibilityInteraction("Add a button that toggles the accent color between blue and green."),false);
  const {guard}=setup({prompt});
  const delivery=guard.verificationForRun('run');
  assert.equal(delivery.status,'failed');
  assert.match(delivery.text,/browser check remains unverified/);
  assert.doesNotMatch(delivery.text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Add a mute toggle for the background audio.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Add a mute toggle for the background audio.";
  assert.equal(requestsVisibilityInteraction("Add a mute toggle for the background audio."),false);
  const {guard}=setup({prompt});
  const delivery=guard.verificationForRun('run');
  assert.equal(delivery.status,'failed');
  assert.match(delivery.text,/browser check remains unverified/);
  assert.doesNotMatch(delivery.text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Add a button that toggles the visibility of the details panel.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Add a button that toggles the visibility of the details panel.";
  assert.equal(requestsVisibilityInteraction("Add a button that toggles the visibility of the details panel."),true);
  const {guard}=setup({prompt});
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.match(guard.verificationForRun('run').text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Add a button that shows the details section and a button that hides it.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Add a button that shows the details section and a button that hides it.";
  assert.equal(requestsVisibilityInteraction("Add a button that shows the details section and a button that hides it."),true);
  const {guard}=setup({prompt});
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.match(guard.verificationForRun('run').text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Do not add a toggle that shows or hides anything.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Do not add a toggle that shows or hides anything.";
  assert.equal(requestsVisibilityInteraction("Do not add a toggle that shows or hides anything."),false);
  const {guard}=setup({prompt});
  const delivery=guard.verificationForRun('run');
  assert.equal(delivery.status,'failed');
  assert.match(delivery.text,/browser check remains unverified/);
  assert.doesNotMatch(delivery.text,/show\/hide interaction/);
});
test("visibility duty matches requested behavior: Add a Pause motion toggle to quiet the animated effects, and also add a button that shows the hidden details panel.",()=>{
  const prompt="Create and publish a website in a new workspace directory site. Add a Pause motion toggle to quiet the animated effects, and also add a button that shows the hidden details panel.";
  assert.equal(requestsVisibilityInteraction("Add a Pause motion toggle to quiet the animated effects, and also add a button that shows the hidden details panel."),true);
  const {guard}=setup({prompt});
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.match(guard.verificationForRun('run').text,/show\/hide interaction/);
});

// Quoted control names can be set off by commas without requesting a transition.
test('comma-separated initial control state case 1',()=>{
  assert.equal(requestsVisibilityInteraction("The button, \"Hide details\", starts unpressed."),false);
});
test('comma-separated initial control state case 2',()=>{
  assert.equal(requestsVisibilityInteraction("The button, \"Hide details\", starts unpressed and reveals the panel on click."),true);
});
test('comma-separated initial control state case 3',()=>{
  assert.equal(requestsVisibilityInteraction("The \"Hide details\", button starts unpressed."),false);
});
test('comma-separated initial control state case 4',()=>{
  assert.equal(requestsVisibilityInteraction("The \"Show details\" button starts with aria-pressed=false and reveals the panel on click."),true);
});
test('comma-separated initial control state case 5',()=>{
  assert.equal(requestsVisibilityInteraction("Add a button, \"Hide details\", that starts unpressed."),true);
});
test('comma-separated initial control state case 6',()=>{
  assert.equal(requestsVisibilityInteraction("The button, \"Hide details\", starts unpressed, and the toggle, \"Show more\", starts expanded."),false);
});
test('comma-separated initial control state case 7',()=>{
  assert.equal(requestsVisibilityInteraction("Do not click the button, \"Hide details\", which starts unpressed."),false);
});
test('comma-separated initial control state case 8',()=>{
  assert.equal(requestsVisibilityInteraction("The button, \"Read\", starts unpressed and clicking it shows \"Results card\"."),true);
});

// --- Strixy current-main 29c25961: attempted behavior plan obligation -------------------
// A first bounded action plan (fill/click/select) is remembered per run and
// snapshot. A plan with an interaction and postconditions must pass before delivery; a static
// heading-only inspection cannot substitute for them.

function behaviorReceipt(params,{pageErrors}={}) {
  const request=normalizeWorkspacePreviewInspectionParams(params);
  const state=visible=>({count:1,visible,display:visible?'block':'none',visibility:'visible',opacity:'1',hidden:!visible,hiddenUntilFound:false,rectCount:visible?1:0});
  const inputState=value=>({...state(true),input:{eligible:true,disabled:false,readOnly:false,matches:true}});
  const steps=params.steps.map((s,index)=>{
    const base={index,...s,before:state(s.action!=='assert-hidden'),stable:true,status:'passed'};
    if(s.action==='click') base.after=state(true);
    if(s.action==='fill') { base.before=inputState(); base.after=inputState(); }
    if(s.action==='assert-text') base.before={...state(true),text:{actual:s.expectedText,truncated:false}};
    return base;
  });
  const receipt={schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:params.siteId,sha256:params.sha256,
    planSha256:inspectionPlanHash(request),viewport:params.viewport,steps,
    diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE+(params.steps.some(s=>s.action==='fill')?FILL_INSPECTION_SCOPE:'')};
  if(pageErrors) receipt.pageErrors=pageErrors;
  return receipt;
}

function behaviorInspection(guard,params,{wrapped=false,id='behavior',runContext=context,pageErrors}={}) {
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:params}:params;
  const started=call(guard,name,args,id,undefined,runContext);
  const inner={details:behaviorReceipt(params,{pageErrors})};
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  return {...started,result};
}

// The fleet's packing-checklist prompt: add a checklist with an Add button and
// a packed checkbox. The model's first plan used fill+click but omitted the
// exact/expectedText fields, so the guard must remember the interaction duty
// and refuse to accept a later heading-only static inspection as proof.
const packingPrompt='Create and publish a website in a new workspace directory site. Add a packing checklist with a text field, an Add button, and a packed checkbox.';

function packingPlan(preview) { return {siteId:preview.siteId,sha256:preview.sha256,viewport:{width:800,height:600},steps:[
  {action:'fill',locator:{selector:'#item'},value:'Socks'},
  {action:'click',locator:{role:'button',name:'Add',exact:true}},
  {action:'assert-text',locator:{selector:'#list'},expectedText:'Socks'},
  {action:'click',locator:{role:'checkbox',name:'Packed',exact:true}},
  {action:'assert-visible',locator:{selector:'#packed'}},
]}; }

for (const wrapped of [false,true]) test(`attempted behavior plan survives a static heading pass and requires a corrected interaction and postconditions (${wrapped?'deferred':'direct'})`,()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  // First attempt: fill+click but the assert-text step is missing expectedText,
  // so normalization rejects it. The guard must still remember the attempt.
  const invalid={...packingPlan(preview)};
  delete invalid.steps[2].expectedText;
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:invalid}:invalid;
  const started=call(guard,name,args,'invalid-attempt');
  const inner={isError:true,details:{schemaVersion:1,kind:INSPECTION_KIND,status:'failed',errorCode:'invalid_request',scope:INSPECTION_SCOPE}};
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  guard.afterToolCall({...started.event,result},started.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  // A static heading-only inspection passes the capsule but cannot satisfy the
  // remembered behavior obligation.
  const staticPlan={...packingPlan(preview),steps:[{action:'assert-visible',locator:{selector:'h1'}}]};
  const staticCheck=behaviorInspection(guard,staticPlan,{wrapped,id:'static-heading'});
  guard.afterToolCall({...staticCheck.event,result:staticCheck.result},staticCheck.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.match(guard.verificationForRun('run').text,/attempted preview interaction checks/);
  const retry=guard.beforeAgentFinalize({},context)?.retry;
  assert.equal(retry?.idempotencyKey,'pixel-ods-workspace-preview-behavior');
  assert.match(retry.instruction,/Correct mistaken locators or action types/);
  assert.match(retry.instruction,/do not replace the behavior checks with a heading-only assertion/);
  // The corrected plan keeps the same actions and adds the missing postcondition.
  const corrected=behaviorInspection(guard,packingPlan(preview),{wrapped,id:'corrected'});
  guard.afterToolCall({...corrected.event,result:corrected.result},corrected.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.equal(guard.beforeAgentFinalize({},context),undefined);
});

for (const wrapped of [false,true]) test(`a later static inspection retains the behavior proof for the same snapshot (${wrapped?'deferred':'direct'})`,()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const first=behaviorInspection(guard,packingPlan(preview),{wrapped,id:'behavior'});
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const staticPlan={...packingPlan(preview),viewport:{width:1024,height:768},steps:[{action:'assert-visible',locator:{selector:'h1'}}]};
  const staticCheck=behaviorInspection(guard,staticPlan,{wrapped,id:'static'});
  guard.afterToolCall({...staticCheck.event,result:staticCheck.result},staticCheck.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
});

for (const wrapped of [false,true]) for (const fault of ['failed','newsha','pageerrors','inflight','unbound']) test(`behavior proof is revoked by ${fault} (${wrapped?'deferred':'direct'})`,()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const first=behaviorInspection(guard,packingPlan(preview),{wrapped,id:'behavior'});
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  if(fault==='newsha') {
    const next=republish(guard,preview,'<!doctype html><h1>Changed</h1>','newsha');
    assert.notEqual(next.sha256,preview.sha256);
    assert.equal(guard.verificationForRun('run').status,'failed');
    return;
  }
  const params=packingPlan(preview);
  const next=behaviorInspection(guard,params,{wrapped,id:'next',pageErrors:fault==='pageerrors'?{count:1,messages:['boom']}:undefined});
  const result=structuredClone(next.result),event={...next.event},ctx={...next.ctx};
  const inner=wrapped?result.details.result:result;
  if(fault==='failed') { inner.details.status='failed'; inner.details.steps[0].status='failed'; inner.details.steps[0].errorCode='no_match'; inner.details.steps[0].before={count:0}; }
  if(fault==='unbound') { ctx.toolCallId='unbound'; event.toolCallId='unbound'; }
  if(fault!=='inflight') guard.afterToolCall({...event,result},ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
});

for (const wrapped of [false,true]) test(`a smaller corrected interaction proves only its submitted checks (${wrapped?'deferred':'direct'})`,()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const invalid={...packingPlan(preview)};
  delete invalid.steps[2].expectedText;
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:invalid}:invalid;
  const started=call(guard,name,args,'invalid-attempt');
  const inner={isError:true,details:{schemaVersion:1,kind:INSPECTION_KIND,status:'failed',errorCode:'invalid_request',scope:INSPECTION_SCOPE}};
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  guard.afterToolCall({...started.event,result},started.ctx);
  // Only one click, no fill, no assert-text: fewer than the attempted plan.
  const fewer={...packingPlan(preview),steps:[{action:'click',locator:{role:'button',name:'Add',exact:true}},{action:'assert-visible',locator:{selector:'#list'}}]};
  const check=behaviorInspection(guard,fewer,{wrapped,id:'fewer'});
  guard.afterToolCall({...check.event,result:check.result},check.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.match(guard.verificationForRun('run').text,/submitted interaction checks only; this does not verify all requested behavior/);
});

test('a different run and session cannot inherit the behavior obligation',()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const invalid={...packingPlan(preview)};
  delete invalid.steps[2].expectedText;
  const started=call(guard,PREVIEW_INSPECTION_TOOL,invalid,'invalid-attempt');
  guard.afterToolCall({...started.event,result:{isError:true,details:{schemaVersion:1,kind:INSPECTION_KIND,status:'failed',errorCode:'invalid_request',scope:INSPECTION_SCOPE}}},started.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  const nextContext={...context,runId:'next',sessionId:'next-session',sessionKey:'next-key'};
  guard.observeRun(nextContext,'pixel',{prompt:packingPrompt});
  call(guard,'write',{path:preview.relativeDirectory+'/index.html',content:'<!doctype html><button>Show details</button><p hidden>Details</p>'},'next-write',{content:[{type:'text',text:'Successfully wrote file.'}]},nextContext);
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'next-publish',{details:preview},nextContext);
  assert.equal(guard.verificationForRun('next').status,'failed');
  assert.match(guard.verificationForRun('next').text,/browser check remains unverified/);
  assert.doesNotMatch(guard.verificationForRun('next').text,/attempted preview interaction checks/);
});

// --- Pure helper tests for attemptedPreviewBehavior/boundPreviewBehavior ---

// Mac ab277929: the capsule passed every submitted step, but an empty Add
// click followed immediately by fill did not establish a postcondition. The
// tool said "passed" while completion correctly withheld interaction proof.
// Keep the recorded browser receipt distinct from synthetic guard fixtures.
const macReading = JSON.parse(readFileSync(new URL('./fixtures/mac-reading-interaction-2026-10-08.json', import.meta.url), 'utf8'));

test('recorded Mac browser pass explains its missing postcondition without changing the receipt', async () => {
  const original = structuredClone(macReading);
  const tool = createWorkspacePreviewInspectTool({request: async () => structuredClone(macReading.receipt),
    behaviorRequirement: () => true});
  const result = await tool.execute('recorded-mac', macReading.params);
  assert.equal(result.isError, true);
  assert.equal(result.details.status, 'incomplete');
  assert.equal(result.details.errorCode, BEHAVIOR_UNTESTED);
  assert.deepEqual(result.details.receipt, original.receipt);
  assert.deepEqual(macReading, original, 'no edits to the submitted plan or original browser evidence');
  assert.deepEqual(validateIncompleteInspectionReceipt(result.details, normalizeWorkspacePreviewInspectionParams(macReading.params)), original.receipt);
  assert.match(result.content[0].text, /Step 5 \(click\) has no result assertion before step 6 \(fill\)/);
  assert.match(result.content[0].text, /same published snapshot/);
  assert.match(result.content[0].text, /Do not change the site just to satisfy a mistaken test plan/);
  assert.match(result.content[0].text, /Evidence: .*"status":"incomplete"/);
  assert.doesNotMatch(result.content[0].text, /^Preview inspection passed\./);
  assert.equal(boundPreviewBehavior(macReading.params, result, macReading.params, {requiresInteraction:true}), undefined);
  // A forged outer success cannot turn the incomplete envelope into proof.
  assert.equal(boundPreviewBehavior(macReading.params, {...result, isError:false}, macReading.params, {requiresInteraction:true}), undefined);
  assert.throws(() => validateIncompleteInspectionReceipt({...result.details, errorCode:'unknown'}, normalizeWorkspacePreviewInspectionParams(macReading.params)));
});

async function executeBehaviorInspection(guard, params, {transport='direct', id, evidence=behaviorReceipt(params)} = {}) {
  const wrapped = transport !== 'direct';
  const outer = behaviorInspection(guard, params, {wrapped, id});
  const childId = wrapped ? `tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1` : id;
  const child = transport === 'nested' ? call(guard, PREVIEW_INSPECTION_TOOL, params, childId) : undefined;
  const result = await createWorkspacePreviewInspectTool({request: async () => structuredClone(evidence),
    transitionRequirement: (id, args) => guard.previewInspectionTransition(id, args),
    behaviorRequirement: (id, args) => guard.previewInspectionBehavior(id, args)}).execute(childId, params);
  if (child) guard.afterToolCall({...child.event, result}, child.ctx);
  const envelope = wrapped ? {...outer.result, details:{...outer.result.details, result}} : result;
  guard.afterToolCall({...outer.event, result:envelope}, outer.ctx);
  return result;
}

for (const transport of ['direct', 'deferred', 'nested']) test(`tool-time interaction feedback and corrected same-snapshot evidence (${transport})`, async () => {
  const {guard, preview} = setup({prompt:packingPrompt});
  // Synthetic receipt at this fixture's published snapshot, with the exact
  // recorded action order. This is protocol/guard proof, not a browser rerun.
  const params = {...macReading.params, siteId:preview.siteId, sha256:preview.sha256};
  const first = await executeBehaviorInspection(guard, params, {transport, id:'bad-order'});
  assert.equal(first.details.errorCode, BEHAVIOR_UNTESTED);
  assert.match(first.content[0].text, /Step 5 \(click\).*step 6 \(fill\)/);
  assert.equal(guard.verificationForRun('run').status, 'failed');
  const staticPlan = {...params, steps:[{action:'assert-visible', locator:{selector:'h1'}}]};
  const staticResult = await executeBehaviorInspection(guard, staticPlan, {transport, id:'static-too-early'});
  assert.equal(staticResult.details.errorCode, BEHAVIOR_UNTESTED);
  assert.match(staticResult.content[0].text, /contains no interaction/);
  assert.equal(guard.verificationForRun('run').status, 'failed');
  // Keep all submitted interactions and expected results. Add a check that
  // the empty Add click leaves the count alone, before filling the input.
  const corrected = {...params, steps:[...params.steps.slice(0,5),
    {action:'assert-text', locator:{selector:'#total-count'}, expectedText:'1'}, ...params.steps.slice(5)]};
  const passed = await executeBehaviorInspection(guard, corrected, {transport, id:'corrected-order'});
  assert.equal(passed.isError, undefined);
  assert.equal(passed.details.status, 'passed');
  assert.equal(guard.verificationForRun('run').status, 'passed');
  const laterStatic = await executeBehaviorInspection(guard, staticPlan, {transport, id:'static-after-proof'});
  assert.equal(laterStatic.details.status, 'passed', 'static checks retain existing current-snapshot interaction proof');
  assert.equal(guard.verificationForRun('run').status, 'passed');
});

test('behavior feedback requires an exact pending call in the current run and session', () => {
  const {guard, preview} = setup({prompt:packingPrompt});
  const params = packingPlan(preview);
  assert.equal(guard.previewInspectionBehavior('unbound', params), undefined);
  const started = behaviorInspection(guard, params, {id:'pending'});
  assert.equal(guard.previewInspectionBehavior('pending', params), true);
  assert.equal(guard.previewInspectionBehavior('other', params), undefined);
  assert.equal(guard.previewInspectionBehavior('pending', {...params, viewport:{width:900,height:600}}), undefined);
  assert.equal(guard.previewInspectionBehavior('tool_search_code:other:pixel_ods_workspace_preview_inspect:1', params), undefined);
  guard.afterToolCall({...started.event, result:started.result}, started.ctx);
  guard.toolResultPersist({toolName:PREVIEW_INSPECTION_TOOL, toolCallId:'pending',
    message:{role:'toolResult', toolName:PREVIEW_INSPECTION_TOOL, toolCallId:'pending', ...started.result}}, started.ctx);
  assert.equal(guard.previewInspectionBehavior('pending', params), undefined, 'consumed call cannot demand another check');
  behaviorInspection(guard, params, {id:'old-session'});
  guard.observeRun({...context, runId:'new-run'}, 'pixel', {prompt:packingPrompt});
  assert.equal(guard.previewInspectionBehavior('old-session', params), undefined, 'new active run revokes old call context');
});

test('static-only work and explicit show/hide duties do not gain generic behavior requirements', async () => {
  const {guard, preview} = setup({prompt:packingPrompt});
  const staticResult = await executeBehaviorInspection(guard,
    {...packingPlan(preview), steps:[{action:'assert-visible', locator:{selector:'h1'}}]}, {id:'only-static'});
  assert.equal(staticResult.details.status, 'passed');
  const visibility = setup();
  const extraActions = {...plan(visibility.preview), steps:[...plan(visibility.preview).steps,
    {action:'click', locator:{selector:'#extra'}}, {action:'fill', locator:{selector:'#input'}, value:'x'}]};
  assert.ok(previewBehaviorPlanIssue(extraActions));
  const passed = await executeBehaviorInspection(visibility.guard, extraActions, {id:'visibility-with-exploration'});
  assert.equal(passed.details.status, 'passed');
  assert.equal(visibility.guard.verificationForRun('run').status, 'passed', 'exploration does not expand the explicit owner-bound transition duty');
});

for (const fault of ['browser-failure', 'page-errors', 'receipt-mismatch']) test(`behavior feedback preserves ${fault}`, async () => {
  const {guard, preview} = setup({prompt:packingPrompt});
  const params = {...packingPlan(preview), steps:packingPlan(preview).steps.slice(0,2)};
  const evidence = behaviorReceipt(params);
  if (fault === 'browser-failure') {
    evidence.status = 'failed';
    evidence.steps = [{...evidence.steps[0], status:'failed', errorCode:'no_match', before:{count:0}}];
    delete evidence.steps[0].after;
  } else if (fault === 'page-errors') evidence.pageErrors = {count:1, messages:['script threw']};
  else evidence.planSha256 = 'b'.repeat(64);
  const result = await executeBehaviorInspection(guard, params, {id:fault, evidence});
  assert.notEqual(result.details.errorCode, BEHAVIOR_UNTESTED);
  assert.doesNotMatch(result.content[0].text, /listed browser steps passed/);
  assert.equal(guard.verificationForRun('run').status, 'failed');
  if (fault === 'browser-failure') assert.equal(result.details.steps[0].errorCode, 'no_match');
  if (fault === 'page-errors') assert.match(result.content[0].text, /uncaught script error/);
});

test('shared plan predicate preserves the existing action/postcondition evidence floor', () => {
  const action = name => ({action:name});
  for (const [names, reason, index, nextIndex] of [
    [['assert-visible'], 'no_interaction'],
    [['download'], 'download'],
    [['click','assert-text'], undefined],
    [['fill','fill','click','assert-visible'], undefined],
    [['select-option','assert-visible'], undefined],
    [['click','fill','assert-text'], 'missing_postcondition', 0, 1],
    [['click','click','assert-text'], 'missing_postcondition', 0, 1],
    [['fill'], 'missing_postcondition', 0],
    [['assert-visible','click'], 'missing_postcondition', 1],
  ]) {
    const issue = previewBehaviorPlanIssue({steps:names.map(action)});
    assert.equal(issue?.reason, reason, names.join(','));
    assert.equal(issue?.index, index);
    assert.equal(issue?.nextIndex, nextIndex);
  }
});

import {FILL_INSPECTION_SCOPE} from '../plugin/workspace-preview-inspect.mjs';
import {attemptedPreviewBehavior, boundPreviewBehavior} from '../plugin/preview-interaction-assurance.mjs';

function purePreview(sha='a'.repeat(64)) { return {siteId:'site-'+sha.slice(0,24),sha256:sha}; }

function pureParams(preview,steps) { return {siteId:preview.siteId,sha256:preview.sha256,viewport:{width:800,height:600},steps}; }

function pureReceipt(params) {
  const request=normalizeWorkspacePreviewInspectionParams(params);
  const state=visible=>({count:1,visible,display:visible?'block':'none',visibility:'visible',opacity:'1',hidden:!visible,hiddenUntilFound:false,rectCount:visible?1:0});
  const inputState=()=>({...state(true),input:{eligible:true,disabled:false,readOnly:false,matches:true}});
  return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:params.siteId,sha256:params.sha256,
    planSha256:inspectionPlanHash(request),viewport:params.viewport,steps:params.steps.map((s,index)=>{
      const base={index,...s,before:state(s.action!=='assert-hidden'),stable:true,status:'passed'};
      if(s.action==='click') base.after=state(true);
      if(s.action==='fill') { base.before=inputState(); base.after=inputState(); }
      if(s.action==='assert-text') base.before={...state(true),text:{actual:s.expectedText,truncated:false}};
      return base;
    }),diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE+(params.steps.some(s=>s.action==='fill')?FILL_INSPECTION_SCOPE:'')};
}

test('attemptedPreviewBehavior ignores plans with no click/fill/select and unsupported downloads',()=>{
  const preview=purePreview();
  assert.equal(attemptedPreviewBehavior(pureParams(preview,[{action:'assert-visible',locator:{selector:'h1'}}]),preview),undefined);
  assert.equal(attemptedPreviewBehavior(pureParams(preview,[{action:'download',locator:{selector:'#dl'},path:'a.pdf',expectedBytes:1,expectedSha256:'a'.repeat(64)}]),preview),undefined);
  assert.equal(attemptedPreviewBehavior(pureParams(preview,[{action:'click',locator:{selector:'#b'}},{action:'download',locator:{selector:'#dl'},path:'a.pdf',expectedBytes:1,expectedSha256:'a'.repeat(64)}]),preview),undefined);
});

test('attemptedPreviewBehavior records an interaction duty bound to the snapshot',()=>{
  const preview=purePreview();
  const duty=attemptedPreviewBehavior(pureParams(preview,[
    {action:'fill',locator:{selector:'#i'},value:'x'},
    {action:'click',locator:{selector:'#b'}},
    {action:'click',locator:{selector:'#c'}},
  ]),preview);
  assert.deepEqual(duty,{requiresInteraction:true});
  assert.equal(attemptedPreviewBehavior(pureParams({...preview,sha256:'b'.repeat(64)},[{action:'click',locator:{selector:'#b'}}]),preview),undefined);
});

test('boundPreviewBehavior rejects wrong sha, unknown actions and malformed steps',()=>{
  const preview=purePreview();
  const attempted={fill:0,click:1,'select-option':0};
  const params=pureParams(preview,[{action:'click',locator:{selector:'#b'}},{action:'assert-visible',locator:{selector:'#b'}}]);
  const receipt=pureReceipt(params);
  assert.ok(boundPreviewBehavior(params,{details:receipt},preview,attempted));
  const wrongSha=structuredClone(receipt); wrongSha.sha256='b'.repeat(64);
  assert.equal(boundPreviewBehavior(params,{details:wrongSha},preview,attempted),undefined);
  const unknown=structuredClone(receipt); unknown.steps[0].action='teleport';
  assert.equal(boundPreviewBehavior(params,{details:unknown},preview,attempted),undefined);
  const malformed=structuredClone(receipt); malformed.steps[0].before={count:2};
  assert.equal(boundPreviewBehavior(params,{details:malformed},preview,attempted),undefined);
});

test('boundPreviewBehavior requires a postcondition after each action',()=>{
  const preview=purePreview();
  const attempted={fill:1,click:1,'select-option':0};
  const params=pureParams(preview,[
    {action:'fill',locator:{selector:'#i'},value:'x'},
    {action:'click',locator:{selector:'#b'}},
    {action:'assert-text',locator:{selector:'#o'},expectedText:'ok'},
  ]);
  assert.ok(boundPreviewBehavior(params,{details:pureReceipt(params)},preview,attempted));
  const trailing=pureParams(preview,[
    {action:'fill',locator:{selector:'#i'},value:'x'},
    {action:'click',locator:{selector:'#b'}},
    {action:'assert-text',locator:{selector:'#o'},expectedText:'ok'},
    {action:'click',locator:{selector:'#b'}},
  ]);
  assert.equal(boundPreviewBehavior(trailing,{details:pureReceipt(trailing)},preview,attempted),undefined);
});

test('Tool Search child hooks bind behavior proof and preserve it through a nested static check',()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const nested=(params,id)=>{
    const outer=behaviorInspection(guard,params,{wrapped:true,id});
    const childId=`tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1`;
    const child=call(guard,PREVIEW_INSPECTION_TOOL,params,childId);
    guard.afterToolCall({...child.event,result:outer.result.details.result},child.ctx);
    guard.afterToolCall({...outer.event,result:outer.result},outer.ctx);
    return guard.toolResultPersist({toolName:'tool_call',toolCallId:id,
      message:{role:'toolResult',toolName:'tool_call',toolCallId:id,...outer.result}},outer.ctx);
  };
  nested(packingPlan(preview),'nested-behavior');
  assert.equal(guard.verificationForRun('run').status,'passed');
  nested({...packingPlan(preview),steps:[{action:'assert-visible',locator:{selector:'h1'}}]},'nested-static');
  assert.equal(guard.verificationForRun('run').status,'passed');
});

test('real invalid inspection followed by static pass coaches preserved behavior before finalization',async()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const invalid=packingPlan(preview);
  delete invalid.steps[1].locator.exact;
  delete invalid.steps[2].expectedText;
  const started=call(guard,PREVIEW_INSPECTION_TOOL,invalid,'invalid-real');
  const result=await createWorkspacePreviewInspectTool({request:async()=>assert.fail('invalid request must not reach capsule')}).execute('invalid-real',invalid);
  assert.equal(result.details.errorCode,'invalid_request');
  assert.match(result.content[0].text,/Retain an interaction and its visible postcondition/);
  guard.afterToolCall({...started.event,result},started.ctx);
  guard.toolResultPersist({toolName:PREVIEW_INSPECTION_TOOL,toolCallId:'invalid-real',
    message:{role:'toolResult',toolName:PREVIEW_INSPECTION_TOOL,toolCallId:'invalid-real',...result}},started.ctx);
  const staticCheck=behaviorInspection(guard,{...packingPlan(preview),steps:[{action:'assert-visible',locator:{selector:'h1'}}]},{id:'static-real'});
  guard.afterToolCall({...staticCheck.event,result:staticCheck.result},staticCheck.ctx);
  const coached=guard.toolResultPersist({toolName:PREVIEW_INSPECTION_TOOL,toolCallId:'static-real',
    message:{role:'toolResult',toolName:PREVIEW_INSPECTION_TOOL,toolCallId:'static-real',...staticCheck.result}},staticCheck.ctx);
  assert.match(JSON.stringify(coached),/Correct mistaken locators or action types/);
  assert.match(JSON.stringify(coached),/heading-only assertion/);
  assert.equal(guard.verificationForRun('run').status,'failed');
});

test('bundle invalidation revokes behavior proof even when republishing identical bytes',()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const first=behaviorInspection(guard,packingPlan(preview));
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.equal(guard.invalidateWorkspaceBundle(context),true);
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'republished',{details:preview});
  assert.equal(guard.verificationForRun('run').status,'failed');
});

test('several fields may prepare one click, but two clicks cannot share a final assertion',()=>{
  const preview=purePreview();
  const steps=[{action:'fill',locator:{selector:'#first'},value:'one'},
    {action:'fill',locator:{selector:'#second'},value:'two'},
    {action:'click',locator:{selector:'#submit'}},
    {action:'assert-text',locator:{selector:'#result'},expectedText:'one two'}];
  const params=pureParams(preview,steps),attempted=attemptedPreviewBehavior(params,preview);
  assert.ok(boundPreviewBehavior(params,{details:pureReceipt(params)},preview,attempted));
  const noPostcondition=pureParams(preview,[...steps.slice(0,3),{action:'click',locator:{selector:'#toggle'}},steps[3]]);
  assert.equal(boundPreviewBehavior(noPostcondition,{details:pureReceipt(noPostcondition)},preview,attempted),undefined);
});

// A live laptop page was published with unguarded localStorage and called
// functional despite a sandbox SecurityError. HTTP readback alone must not
// certify model-authored pages when browser inspection is installed.
const loadPrompt='Create and publish a static website in a new workspace directory site.';
function loadPlan(preview) { return {...plan(preview),steps:[{action:'assert-visible',locator:{selector:'button'}}]}; }

test('browser-load gate requests current identifiers before model-authored page completion',()=>{
  const {guard,preview}=setup({prompt:loadPrompt});
  const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,'failed');
  assert.equal(outcome.preview.sha256,preview.sha256);
  assert.match(outcome.text,/browser check remains unverified/);
  const retry=guard.beforeAgentFinalize({},context)?.retry;
  assert.equal(retry?.idempotencyKey,'pixel-ods-workspace-preview-rendered');
  assert.equal(retry?.maxAttempts,1);
  assert.ok(retry.instruction.includes(preview.sha256));
  assert.ok(retry.instruction.includes(preview.siteId));
  const persisted=guard.toolResultPersist({message:{role:'toolResult',toolName:'pixel_ods_workspace_preview',toolCallId:'publish',content:[{type:'text',text:'published'}]}},{...context,toolCallId:'publish'});
  assert.match(JSON.stringify(persisted),/page has not passed browser inspection/);
});

for(const wrapped of [false,true]) for(const fault of ['none','page-errors','session','receipt-sha','outer-error','pending'])
test(`browser-load evidence requires the bound current receipt: wrapped=${wrapped}, fault=${fault}`,()=>{
  const {guard,preview}=setup({prompt:loadPrompt});
  const observed=inspection(guard,loadPlan(preview),{wrapped});
  const inner=wrapped?observed.result.details.result:observed.result;
  if(fault==='page-errors')inner.details.pageErrors={count:1,messages:['SecurityError: storage unavailable']};
  if(fault==='receipt-sha')inner.details.sha256='b'.repeat(64);
  if(fault==='outer-error')observed.result.isError=true;
  if(fault!=='pending')guard.afterToolCall({...observed.event,result:observed.result},fault==='session'?{...observed.ctx,sessionId:'foreign'}:observed.ctx);
  const outcome=guard.verificationForRun('run');
  assert.equal(outcome.status,fault==='none'?'passed':'failed');
  if(fault==='none')assert.doesNotMatch(outcome.text,/interaction checks.*passed|show\/hide checks.*passed/);
  if(fault==='page-errors')assert.equal(guard.beforeAgentFinalize({},context)?.retry?.instruction,PAGE_ERROR_REPAIR_INSTRUCTION);
});

test('browser-load proof is revoked by a later failed inspection and by same-byte republication',()=>{
  const {guard,preview}=setup({prompt:loadPrompt});
  const first=inspection(guard,loadPlan(preview));
  guard.afterToolCall({...first.event,result:first.result},first.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const failed=erroredInspection(guard,loadPlan(preview),{id:'later-errors'});
  guard.afterToolCall({...failed.event,result:failed.result},failed.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  const fresh=inspection(guard,loadPlan(preview),{id:'fresh'});
  guard.afterToolCall({...fresh.event,result:fresh.result},fresh.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'republish',{details:preview});
  assert.equal(guard.verificationForRun('run').status,'failed');
  guard.afterToolCall({...fresh.event,result:fresh.result},fresh.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed','late consumed receipt cannot restore proof');
});

test('browser-load unavailable result preserves an unverified preview without another retry',()=>{
  const {guard,preview}=setup({prompt:loadPrompt});
  const observed=inspection(guard,loadPlan(preview));
  guard.afterToolCall({...observed.event,result:{isError:true,details:{errorCode:'unavailable'}}},observed.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed');
  assert.equal(guard.verificationForRun('run').preview.sha256,preview.sha256);
  assert.notEqual(guard.beforeAgentFinalize({},context)?.retry?.idempotencyKey,'pixel-ods-workspace-preview-rendered');
});

test('browser-load gate does not require unavailable capability or pre-existing owner files',()=>{
  assert.equal(setup({prompt:loadPrompt,enabled:false}).guard.verificationForRun('run').status,'passed');
  const {preview}=setup({prompt:loadPrompt});
  const guard=createToolLoopGuard({workspacePreviewInspectionAvailable:true});
  guard.observeRun(context,'pixel',{prompt:'Publish the existing website from site without changing any files.'});
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'publish-existing',{details:preview});
  assert.equal(guard.verificationForRun('run').status,'passed');
});

for(const wrapped of [false,true]) for(const first of ['checkbox-select','too-many-actions'])
test(`repairable interaction obligation: ${first}, wrapped=${wrapped}`,()=>{
  const {guard,preview}=setup({prompt:packingPrompt});
  const steps=first==='checkbox-select'
    ? [{action:'select-option',locator:{selector:'input[type="checkbox"]'},value:'true'}]
    : [...Array.from({length:3},()=>({action:'fill',locator:{selector:'#item'},value:'Socks'})),
       ...Array.from({length:5},()=>({action:'click',locator:{selector:'#add'}})),
       ...Array.from({length:4},()=>({action:'assert-visible',locator:{selector:'#list'}}))];
  const attempted={...plan(preview),steps};
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:attempted}:attempted;
  const started=call(guard,name,args,'wrong-first-plan');
  const inner={isError:true,details:{errorCode:'invalid_request'}};
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  guard.afterToolCall({...started.event,result},started.ctx);
  const heading=inspection(guard,loadPlan(preview),{wrapped,id:'heading'});
  guard.afterToolCall({...heading.event,result:heading.result},heading.ctx);
  assert.equal(guard.verificationForRun('run').status,'failed','a static check cannot replace interaction testing');
  const corrected=behaviorInspection(guard,packingPlan(preview),{wrapped,id:'corrected-controls'});
  guard.afterToolCall({...corrected.event,result:corrected.result},corrected.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  assert.match(guard.verificationForRun('run').text,/submitted interaction checks only; this does not verify all requested behavior/);
});


const verifyFollowup = 'Please finish verifying the preview: add an item, mark it packed, and fix anything that fails.';
for (const prompt of [verifyFollowup, 'Verify the current preview.', 'Please test the existing app: fill the form and click Submit.'])
test(`verification follow-up classifier accepts explicit check: ${prompt}`, () => {
  assert.equal(userMessageRequestsWorkspaceVerificationContinuation([], prompt), true);
});
for (const prompt of [
  'Add a dark mode toggle to the preview and republish it.', 'Make it blue.',
  'Verify the preview and change the background.', 'Verify the preview: change the background.',
  'Verify the preview. Add a new feature.', 'Verify the preview: add a dark mode toggle.',
  'Verify the preview: fix the broken button.', 'Do not verify the preview.',
  'The document says "verify the preview".', '"Verify the preview"',
  'Verify the preview. Create a new website.', 'Verify the preview, but do not publish anything.',
]) test(`verification follow-up classifier preserves other intent: ${prompt}`, () => {
  assert.equal(userMessageRequestsWorkspaceVerificationContinuation([], prompt), false);
});

test('verification follow-up reads and freshly republishes unchanged files before inspecting', () => {
  const {guard, preview}=setup({prompt:'Build a packing checklist website in site and show me a working preview.'});
  const next={...context,runId:'verify-followup'};
  guard.observeRun(next,'pixel',{prompt:verifyFollowup});
  assert.equal(guard.verificationForRun(next.runId).status,'failed');
  assert.equal(guard.beforeAgentFinalize({},next)?.retry?.idempotencyKey,'pixel-ods-workspace-visual-continuation-read');
  const attempted=guard.beforeToolCall({toolName:'pixel_ods_workspace_preview',toolCallId:'too-early',params:{relativeDirectory:preview.relativeDirectory}}, {...next,toolName:'pixel_ods_workspace_preview',toolCallId:'too-early'});
  assert.equal(attempted?.block,true,'publication still requires current entry readback');
  const historical=inspection(guard,plan(preview),{id:'historical-check',runContext:next});
  guard.afterToolCall({...historical.event,result:historical.result},historical.ctx);
  assert.equal(guard.verificationForRun(next.runId).status,'failed','immutable old inspection never certifies current files');
  call(guard,'read',{path:preview.relativeDirectory+'/index.html'},'read-current',{content:[{type:'text',text:'<!doctype html><button>Show details</button><p hidden>Details</p>'}]},next);
  const decision=guard.beforeAgentFinalize({},next);
  assert.equal(decision?.retry?.idempotencyKey,'pixel-ods-workspace-preview');
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'fresh-publish',{details:preview},next);
  assert.equal(guard.verificationForRun(next.runId).status,'failed','new publication must receive its own browser check');
  const fresh=inspection(guard,plan(preview),{id:'fresh-check',runContext:next});
  guard.afterToolCall({...fresh.event,result:fresh.result},fresh.ctx);
  assert.equal(guard.verificationForRun(next.runId).status,'passed');
  assert.equal(guard.beforeAgentFinalize({},next),undefined);
});

for (const prompt of ['Add a dark mode toggle to the preview and republish it.','Make it blue.','Verify the preview and change the background.'])
test(`verification follow-up does not waive requested source edits: ${prompt}`,()=>{
  const {guard,preview}=setup();const next={...context,runId:'edit-followup'};
  guard.observeRun(next,'pixel',{prompt});
  call(guard,'read',{path:preview.relativeDirectory+'/index.html'},'read-current',{content:[{type:'text',text:'<!doctype html><button>Show details</button><p hidden>Details</p>'}]},next);
  const event={toolName:'pixel_ods_workspace_preview',toolCallId:'unchanged',params:{relativeDirectory:preview.relativeDirectory}};
  const ctx={...next,toolName:event.toolName,toolCallId:event.toolCallId};
  assert.equal(guard.beforeToolCall(event,ctx)?.block,true);
  guard.afterToolCall({...event,result:{details:preview}},ctx);
  assert.equal(guard.verificationForRun(next.runId).status,'failed');
});

test('verification follow-up cannot discover another session publication',()=>{
  const {guard,preview}=setup();const next={...context,runId:'foreign',sessionId:'foreign-session',sessionKey:'foreign-key'};
  guard.observeRun(next,'pixel',{prompt:verifyFollowup});
  const decision=guard.beforeAgentFinalize({},next);
  assert.notEqual(decision?.retry?.idempotencyKey,'pixel-ods-workspace-visual-continuation-read');
  assert.ok(!decision?.retry?.instruction.includes(preview.relativeDirectory+'/index.html'));
  assert.notEqual(guard.verificationForRun(next.runId).status,'passed');
});

for (const wrapped of [false,true]) test(`verification follow-up conditional repair cannot pass a failed check: wrapped=${wrapped}`,()=>{
  const {guard,preview}=setup({prompt:'Build a packing checklist website in site and show me a working preview.'});
  const next={...context,runId:'conditional-repair'};
  guard.observeRun(next,'pixel',{prompt:'Verify the preview: fix it if the check fails.'});
  call(guard,'read',{path:preview.relativeDirectory+'/index.html'},'read-current',{content:[{type:'text',text:'<!doctype html><button>Show details</button><p hidden>Details</p>'}]},next);
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'fresh-publish',{details:preview},next);
  const checked=inspection(guard,plan(preview),{id:'failing-check',wrapped,runContext:next});
  (wrapped?checked.result.details.result:checked.result).details.pageErrors={count:1,messages:['SecurityError: storage unavailable']};
  guard.afterToolCall({...checked.event,result:checked.result},checked.ctx);
  assert.equal(guard.verificationForRun(next.runId).status,'failed');
  assert.equal(guard.beforeAgentFinalize({},next)?.retry?.instruction,PAGE_ERROR_REPAIR_INSTRUCTION);
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'unchanged-republish',{details:preview},next);
  assert.equal(guard.verificationForRun(next.runId).status,'failed','republication cannot substitute for a passing browser check');
});


test('verification after gateway restart requires fresh publication and browser evidence',()=>{
  const {preview}=setup({prompt:'Build a packing checklist website in site and show me a working preview.'});
  const guard=createToolLoopGuard({workspacePreviewInspectionAvailable:true});
  const next={...context,runId:'after-restart'};
  guard.observeRun(next,'pixel',{prompt:verifyFollowup,messages:[
    {role:'assistant',content:'The previous preview passed all six checks.'}
  ]});
  assert.equal(guard.verificationForRun(next.runId).status,'failed','prior prose cannot satisfy a new verification request');
  const retry=guard.beforeAgentFinalize({},next)?.retry;
  assert.ok(retry,'no-tool answer must request fresh work');
  guard.observeRun(next,'pixel',{prompt:retry.instruction});
  call(guard,'read',{path:preview.relativeDirectory+'/index.html'},'read-current',{content:[{type:'text',text:'<!doctype html><button>Show details</button><p hidden>Details</p>'}]},next);
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:preview.relativeDirectory},'fresh-publish',{details:preview},next);
  assert.equal(guard.verificationForRun(next.runId).status,'failed','HTTP readback alone is insufficient after restart');
  const fresh=inspection(guard,plan(preview),{id:'fresh-browser-check',runContext:next});
  guard.afterToolCall({...fresh.event,result:fresh.result},fresh.ctx);
  assert.equal(guard.verificationForRun(next.runId).status,'passed');
  assert.equal(guard.beforeAgentFinalize({},next),undefined);
});

test('verified preview delivery retains storage scope beside contradictory model prose',()=>{
  const {guard,preview}=setup();
  const observed=inspection(guard,plan(preview));
  guard.afterToolCall({...observed.event,result:observed.result},observed.ctx);
  assert.equal(guard.verificationForRun('run').status,'passed');
  const modelText='For actual persistence, open it in your regular browser.';
  const event={runId:'run',kind:'final',payload:{text:modelText}};
  const delivered=guard.replyPayloadSending(event).payload;
  assert.ok(delivered.text.startsWith(modelText));
  assert.ok(delivered.text.includes(PREVIEW_STORAGE_DISCLOSURE));
  assert.ok(delivered.text.includes(preview.url));
  assert.equal(guard.replyPayloadSending({...event,payload:delivered}).payload.text,delivered.text);
});

test('published but unverified interactions still disclose temporary preview input',()=>{
  const {guard}=setup();
  assert.equal(guard.verificationForRun('run').status,'failed');
  const delivered=guard.replyPayloadSending({runId:'run',kind:'final',payload:{text:'Everything persists.'}}).payload;
  assert.ok(delivered.text.includes(PREVIEW_STORAGE_DISCLOSURE));
  assert.doesNotMatch(delivered.text,/Everything persists/);
});
