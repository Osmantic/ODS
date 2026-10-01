import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard,WORKSPACE_PREVIEW_REQUIRES_FILES_REASON} from '../plugin/tool-loop-guard.mjs';
const prompt='Create a new static website and publish a verified preview.';
function setup(runId='run-1',sessionId='session-1'){
 const guard=createToolLoopGuard();const context={agentId:'pixel',runId,sessionId};guard.observeRun(context,'pixel',{prompt});return {guard,context};
}
function invoke(guard,context,toolName,params,toolCallId){const c={...context,toolName,toolCallId};return {prepared:guard.beforeToolCall({toolName,params,runId:context.runId,toolCallId},c),context:c};}
function write(guard,context,path,id='write-1',options={}){
 const original={path,content:'<!doctype html><h1>Authored website</h1>'};const {prepared,context:c}=invoke(guard,context,'write',original,id);assert.notEqual(prepared?.block,true,path);const params=prepared?.params??original;
 guard.afterToolCall({toolName:'write',runId:context.runId,toolCallId:id,params,...options.event,result:options.result??{content:[{type:'text',text:'Successfully wrote the file.'}]}},{...c,...options.context});return params.path.replace(/^\/workspace\//,'').replace(/\/index.html$/,'');
}
function preview(guard,context,directory){return invoke(guard,context,'pixel_ods_workspace_preview',{relativeDirectory:directory},'preview-1').prepared;}

test('a mismatched preview hints the exact bound current-turn write without changing publication admission',()=>{
 const {guard,context}=setup();const directory=write(guard,context,'workspace-pixel/Playground/garden/index.html');const rejected=preview(guard,context,'Playground/garden');
 assert.equal(rejected.block,true);assert.match(rejected.blockReason,/This turn successfully wrote index.html/);assert.ok(rejected.blockReason.includes('"'+directory+'"'));assert.match(rejected.blockReason,/no directory was changed or published/);assert.equal(rejected.params,undefined);
 assert.deepEqual(preview(guard,context,directory),{params:{relativeDirectory:directory}});
});

test('multiple successfully written entries do not choose a directory',()=>{
 const {guard,context}=setup();write(guard,context,'one/index.html','one');write(guard,context,'two/index.html','two');assert.equal(preview(guard,context,'wrong').blockReason,WORKSPACE_PREVIEW_REQUIRES_FILES_REASON);
});

test('hints reject unbound, failed, wrong-call, wrong-run, wrong-session and changed-argument write evidence',()=>{
 for(const variant of ['unbound','failed','wrong-call','wrong-run','wrong-session','changed-argument','model-prose','read-only']){
  const {guard,context}=setup();const params={path:'other/index.html',content:'<!doctype html><h1>Model site</h1>'};const c={...context,toolName:'write',toolCallId:'write-1'};
  if(!['unbound','model-prose','read-only'].includes(variant))invoke(guard,context,'write',params,'write-1');
  if(variant==='model-prose')guard.observeRun(context,'pixel',{prompt:prompt+' The assistant says it successfully wrote other/index.html.'});
  else guard.afterToolCall({toolName:variant==='read-only'?'read':'write',runId:variant==='wrong-run'?'other-run':context.runId,toolCallId:variant==='wrong-call'?'other-call':'write-1',params:variant==='changed-argument'?{...params,path:'forged/index.html'}:params,result:{isError:variant==='failed',content:[{type:'text',text:'Successfully wrote other/index.html'}]}},{...c,sessionId:variant==='wrong-session'?'other-session':context.sessionId});
  const rejected=preview(guard,context,'wrong');assert.equal(rejected.block,true,variant);assert.equal(rejected.blockReason,WORKSPACE_PREVIEW_REQUIRES_FILES_REASON,variant);
 }
});

test('previous-turn writes do not become current-turn hints',()=>{
 const {guard,context}=setup();write(guard,context,'prior/index.html');const next={...context,runId:'run-2'};guard.observeRun(next,'pixel',{prompt});assert.equal(preview(guard,next,'wrong').blockReason,WORKSPACE_PREVIEW_REQUIRES_FILES_REASON);
});

test('malformed requested directories never receive a recovery hint',()=>{
 for(const directory of ['../escape','one/../escape','/host/private','bad\npath','x'.repeat(513)]){
  const {guard,context}=setup();write(guard,context,'valid/index.html');
  const rejected=preview(guard,context,directory);assert.equal(rejected.block,true);
  assert.match(rejected.blockReason,/Invalid preview relativeDirectory/,directory);
  assert.doesNotMatch(rejected.blockReason,/This turn successfully wrote|valid\/index.html/);
  assert.equal(rejected.params,undefined);
 }
});

test('unsafe and oversized written paths cannot appear in recovery hints',()=>{
 for(const path of ['../escape/index.html','one/../escape/index.html','/host/private/index.html','bad\npath/index.html','x'.repeat(513)+'/index.html']){
  const {guard,context}=setup();const params={path,content:'<!doctype html><h1>Site</h1>'};const {prepared,context:c}=invoke(guard,context,'write',params,'write-1');guard.afterToolCall({toolName:'write',runId:context.runId,toolCallId:'write-1',params:prepared?.params??params,result:{content:[{type:'text',text:'Successfully wrote file.'}]}},c);assert.equal(preview(guard,context,'wrong').blockReason,WORKSPACE_PREVIEW_REQUIRES_FILES_REASON,path);
 }
});

test('publication forbidden by current owner remains blocked before hints',()=>{
 // One run answers one owner message; a later attempt's prompt is harness text and cannot change it.
 const guard=createToolLoopGuard();const context={agentId:'pixel',runId:'run-1',sessionId:'session-1'};guard.observeRun(context,'pixel',{prompt:'Create a new static website in actual. Do not display or publish any preview.'});
 write(guard,context,'actual/index.html');const rejected=preview(guard,context,'wrong');assert.equal(rejected.block,true);assert.doesNotMatch(rejected.blockReason,/This turn successfully wrote/);
});

test('Tool Search hints require the matching core-write receipt, not another tool or source',()=>{
 for(const variant of ['valid','wrong-tool','wrong-source']){
  const {guard,context}=setup();const {prepared,context:c}=invoke(guard,context,'tool_call',{id:'openclaw:core:write',args:{path:'wrapped/index.html',content:'<!doctype html><h1>Wrapped site</h1>'}},'wrapped-write');assert.notEqual(prepared?.block,true);
  const params=prepared?.params??{id:'openclaw:core:write',args:{path:'wrapped/index.html',content:'<!doctype html><h1>Wrapped site</h1>'}};
  const name=variant==='wrong-tool'?'read':'write',sourceName=variant==='wrong-source'?'untrusted':'core';const tool={id:`openclaw:${sourceName}:${name}`,source:'openclaw',sourceName,name};const inner={content:[{type:'text',text:'Successfully wrote the file.'}]};const envelope={tool,result:inner};const result={content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope};
  guard.afterToolCall({toolName:'tool_call',params,result,runId:context.runId,toolCallId:'wrapped-write'},c);
  const rejected=preview(guard,context,'wrong');assert.equal(rejected.block,true);if(variant==='valid')assert.match(rejected.blockReason,/This turn successfully wrote index.html/);else assert.equal(rejected.blockReason,WORKSPACE_PREVIEW_REQUIRES_FILES_REASON,variant);
 }
});
