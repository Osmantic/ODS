import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

function fixture() {
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'entry-run',sessionId:'entry-session',sessionKey:'agent:pixel:entry'};
  guard.observeRun(context,'pixel',{prompt:'Implement a Python CSV report CLI in a new workspace project expense-coding, run its tests, then create public/index.html and publish ONLY public as a verified Pixel workspace preview.'});
  let sequence=0;
  const invoke=(toolName,params,result)=>{
    const toolCallId=`entry-${++sequence}`,ctx={...context,toolName,toolCallId};
    const prepared=guard.beforeToolCall({toolName,params,toolCallId},ctx);
    if(prepared?.block)return {prepared};
    guard.afterToolCall({toolName,params:prepared?.params??params,toolCallId,result},ctx);
    const persisted=guard.toolResultPersist({toolName,toolCallId,message:{role:'toolResult',toolName,toolCallId,...result}},ctx);
    return {prepared,text:(persisted?.message?.content??[]).map(part=>part.text??'').join('\n')};
  };
  return {guard,context,invoke};
}
const success=text=>({content:[{type:'text',text}],details:{status:'completed',exitCode:0}});

test('a shell-generated entry receives read guidance until an exact read succeeds',()=>{
  const {invoke}=fixture();
  invoke('write',{path:'expense-coding/build_public.py',content:'from pathlib import Path\nPath("public").mkdir(exist_ok=True)\nPath("public/index.html").write_text("<!doctype html><h1>Report</h1>")'},success('written'));
  invoke('exec',{command:'python3 build_public.py',workdir:'expense-coding'},success('Published public/'));
  assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:'expense-coding/public'},success('unused')).prepared?.block,true);
  for(const command of ['test -f expense-coding/public/index.html && echo EXISTS || echo MISSING','pwd']) {
    const result=invoke('exec',{command},success(command==='pwd'?'/workspace':'EXISTS'));
    assert.notEqual(result.prepared?.block,true);
    assert.match(result.text,/Read expense-coding\/public\/index\.html/);
    assert.doesNotMatch(result.text,/Call tool_call with id pixel_ods_workspace_preview/);
    assert.match(result.text,/project's real build within the owner's requested scope/);
    assert.match(result.text,/do not delete the directory or handwrite generated build outputs/);
  }
  const failedRead=invoke('read',{path:'expense-coding/public/index.html'},{isError:true,content:[{type:'text',text:'read unavailable'}]});
  assert.doesNotMatch(failedRead.text??'',/Call tool_call with id pixel_ods_workspace_preview/);
  const otherRead=invoke('read',{path:'expense-coding/other/index.html'},success('<!doctype html><h1>Other</h1>'));
  assert.match(otherRead.text,/Read expense-coding\/public\/index\.html/);
  assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:'expense-coding/public'},success('unused')).prepared?.block,true);
  const read=invoke('read',{path:'expense-coding/public/index.html'},success('<!doctype html><h1>Report</h1>'));
  assert.doesNotMatch(read.text,/with the workspace read tool before requesting/);
  assert.match(read.text,/call (?:tool_call with id )?pixel_ods_workspace_preview/i);
  assert.notEqual(invoke('pixel_ods_workspace_preview',{relativeDirectory:'expense-coding/public'},success('not a publication receipt')).prepared?.block,true);
});

test('a newly bound authored entry takes priority over a rejected directory hint',()=>{
  const {invoke}=fixture();
  assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:'old/public'},success('unused')).prepared?.block,true);
  const result=invoke('write',{path:'expense-coding/public/index.html',content:'<!doctype html><h1>Report</h1>'},success('written'));
  assert.doesNotMatch(result.text,/Read old\/public\/index\.html/);
  assert.match(result.text,/Call tool_call with id pixel_ods_workspace_preview/);
});

test('malformed preview targets never enter prerequisite coaching',()=>{
  for(const relativeDirectory of ['../escape','bad\npath','x'.repeat(513)]) {
    const {invoke}=fixture();
    assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory},success('unused')).prepared?.block,true);
    const result=invoke('exec',{command:'pwd'},success('/workspace'));
    assert.doesNotMatch(result.text,/with the workspace read tool before requesting/);
    assert.ok(!result.text.includes(relativeDirectory));
  }
});

test('a new owner turn does not inherit the previous missing-entry prerequisite',()=>{
  const {guard,context,invoke}=fixture();
  assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:'old/public'},success('unused')).prepared?.block,true);
  const next={...context,runId:'next-entry-run',toolName:'exec',toolCallId:'next-pwd'};
  guard.observeRun(next,'pixel',{prompt:'Implement a different Python CLI in fresh-coding, then publish its public directory as a verified Pixel workspace preview.'});
  const params={command:'pwd'};
  const prepared=guard.beforeToolCall({toolName:'exec',params,toolCallId:next.toolCallId},next);
  assert.notEqual(prepared?.block,true);
  const result=success('/workspace');
  guard.afterToolCall({toolName:'exec',params:prepared?.params??params,toolCallId:next.toolCallId,result},next);
  const persisted=guard.toolResultPersist({toolName:'exec',toolCallId:next.toolCallId,message:{role:'toolResult',toolName:'exec',toolCallId:next.toolCallId,...result}},next);
  const text=(persisted?.message?.content??[]).map(part=>part.text??'').join('\n');
  assert.ok(!text.includes('old/public'));
  assert.doesNotMatch(text,/with the workspace read tool before requesting/);
});
