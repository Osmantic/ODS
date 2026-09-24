import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL, requestsVisibilityInteraction, boundVisibilityInspection} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const owner='Create and publish a website in a new workspace directory site. Add a button that toggles hidden details.';
const context={agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'opaque-key'};
function call(guard,name,params,id,result) {
  const ctx={...context,toolName:name,toolCallId:id};
  const event={toolName:name,runId:context.runId,toolCallId:id,params};
  const prepared=guard.beforeToolCall(event,ctx);
  assert.notEqual(prepared?.block,true,prepared?.blockReason);
  event.params=prepared?.params??params;
  if(result)guard.afterToolCall({...event,result},ctx);
  return {event,ctx};
}
function setup({enabled=true,prompt=owner}={}) {
  const guard=createToolLoopGuard({workspacePreviewInspectionAvailable:enabled});
  guard.observeRun(context,'pixel',{prompt});
  const content='<!doctype html><button>Show details</button><p hidden>Details</p>';
  const write=call(guard,'write',{path:'site/index.html',content},'write',{content:[{type:'text',text:'Successfully wrote file.'}]}).event.params;
  const dir=write.path.replace(/\/index.html$/,'');
  const name=Buffer.from('index.html'),data=Buffer.from(content),a=Buffer.alloc(4),b=Buffer.alloc(8);
  a.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(data.length));
  const sha256=createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId='site-'+sha256.slice(0,24);
  const preview={schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:dir,
    siteId,sha256,entryFile:'index.html',entrySha256:createHash('sha256').update(data).digest('hex'),files:1,bytes:data.length,
    port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,httpStatus:200,readbackVerified:true,executable:false,overwritten:false};
  call(guard,'pixel_ods_workspace_preview',{relativeDirectory:dir},'publish',{details:preview});
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
function inspection(guard,params,{wrapped=false,id='inspect'}={}) {
  const name=wrapped?'tool_call':PREVIEW_INSPECTION_TOOL;
  const args=wrapped?{id:'openclaw:pixel-ods:'+PREVIEW_INSPECTION_TOOL,args:params}:params;
  const started=call(guard,name,args,id);
  const inner={details:receipt(params)};
  const result=wrapped?{details:{tool:{id:args.id,name:PREVIEW_INSPECTION_TOOL,source:'openclaw',sourceName:'pixel-ods'},result:inner}}:inner;
  return {...started,result};
}

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
