import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createToolLoopGuard, UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP, UNPUBLISHABLE_RESTRICTED_PREVIEW_DIRECTORY_STEP} from '../plugin/tool-loop-guard.mjs';

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
  const guidance=/Read expense-coding\/public\/index\.html/;
  invoke('write',{path:'expense-coding/build_public.py',content:'from pathlib import Path\nPath("public").mkdir(exist_ok=True)\nPath("public/index.html").write_text("<!doctype html><h1>Report</h1>")'},success('written'));
  invoke('exec',{command:'python3 build_public.py',workdir:'expense-coding'},success('Published public/'));
  assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:'expense-coding/public'},success('unused')).prepared?.block,true);
  const first=invoke('exec',{command:'test -f expense-coding/public/index.html && echo EXISTS || echo MISSING'},success('EXISTS'));
  assert.notEqual(first.prepared?.block,true);
  assert.match(first.text,guidance);
  assert.doesNotMatch(first.text,/Call (?:tool_call with id )?pixel_ods_workspace_preview/);
  assert.match(first.text,/project's real build within the owner's requested scope/);
  assert.match(first.text,/do not delete the directory or handwrite generated build outputs/);
  // Identical coaching is delivered once, not appended to every later result.
  const repeat=invoke('exec',{command:'pwd'},success('/workspace'));
  assert.notEqual(repeat.prepared?.block,true);
  assert.doesNotMatch(repeat.text,guidance);
  const failedRead=invoke('read',{path:'expense-coding/public/index.html'},{isError:true,content:[{type:'text',text:'read unavailable'}]});
  assert.doesNotMatch(failedRead.text??'',/Call (?:tool_call with id )?pixel_ods_workspace_preview/);
  assert.doesNotMatch(invoke('read',{path:'expense-coding/other/index.html'},success('<!doctype html><h1>Other</h1>')).text,guidance);
  // The prerequisite itself still holds, and the guidance returns after a bounded number of results.
  assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:'expense-coding/public'},success('unused')).prepared?.block,true);
  let redelivered=false;
  for(let i=0;i<8&&!redelivered;i++) redelivered=guidance.test(invoke('exec',{command:`echo step-${i}`},success(`step-${i}`)).text);
  assert.ok(redelivered);
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
  assert.match(result.text,/Call pixel_ods_workspace_preview with args \{"relativeDirectory":"expense-coding\/public"\}/);
  assert.doesNotMatch(result.text,/tool_call with id pixel_ods_workspace_preview/);
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

// mac-mini round 106 (main b060c6ae): the model wrote the site under a hidden
// directory, and the next step named that directory, which the publication
// check always refuses (the first failure). The model then moved it with mv
// on its own; publishing before a read of the moved entry was refused too
// (the second failure). The step now says to copy, read, then publish.
const MAC=JSON.parse(readFileSync(new URL('./hidden-directory-mac-mini-round106.json',import.meta.url),'utf8'));
test('mac-mini round 106: a hidden site directory is never prescribed for publication',()=>{
  const [turn]=MAC.turns;
  const [write,hiddenPublish,move,unreadPublish,read,publish]=turn.calls;
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:turn.runId,sessionId:MAC.sessionId,sessionKey:MAC.sessionKey};
  guard.observeRun(context,'pixel',{prompt:turn.prompt});
  // Every recorded call runs the full hook sequence; a refused call gets the
  // SDK's standard veto result, as OpenClaw reports it.
  const invoke=(call,executed)=>{
    const ctx={...context,toolName:call.tool,toolCallId:call.id};
    const prepared=guard.beforeToolCall({toolName:call.tool,params:call.arguments,toolCallId:call.id},ctx);
    const result=prepared?.block?{isError:true,content:[{type:'text',text:prepared.blockReason}],
      details:{status:'blocked',deniedReason:'plugin-before-tool-call',reason:prepared.blockReason}}:executed;
    guard.afterToolCall({toolName:call.tool,params:prepared?.params??call.arguments,toolCallId:call.id,result,
      ...(prepared?.block?{error:prepared.blockReason}:{})},ctx);
    const persisted=guard.toolResultPersist({toolName:call.tool,toolCallId:call.id,message:{role:'toolResult',toolName:call.tool,toolCallId:call.id,...result}},ctx);
    return {prepared,text:(persisted?.message?.content??[]).map(part=>part.text??'').join('\n')};
  };
  assert.equal(write.arguments.path,'.fleet-qualification-934ec2f819d5/index.html');
  assert.match(write.seen,/Call tool_call with id pixel_ods_workspace_preview and args \{"relativeDirectory":"\.fleet-qualification-934ec2f819d5"\}/);
  const written=invoke(write,success(`Successfully wrote ${Buffer.byteLength(write.arguments.content)} bytes to ${write.arguments.path}`));
  assert.ok(written.text.includes(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP),written.text);
  assert.ok(!written.text.includes('"relativeDirectory":".fleet-qualification-934ec2f819d5"'),written.text);
  assert.doesNotMatch(written.text,/tool_call|\bmv\b|\bMove\b/);
  assert.match(written.text,/Copy the files \(for example with one cp -r command\)[^.]*leave the original files unchanged\. Then read the copy's index\.html/);
  // The next step no longer invites it; the publication check is unchanged.
  assert.deepEqual(invoke(hiddenPublish).prepared,{block:true,blockReason:hiddenPublish.seen});
  invoke(move,{content:[{type:'text',text:'(no output)'}],details:move.details});
  // The step said to read the moved entry first; publishing without that read is still refused.
  assert.deepEqual(invoke(unreadPublish).prepared,{block:true,blockReason:unreadPublish.seen});
  const readback=invoke(read,success(write.arguments.content));
  assert.ok(readback.text.includes('Call pixel_ods_workspace_preview with args {"relativeDirectory":"fleet-qualification-934ec2f819d5"}. '),readback.text);
  assert.notEqual(invoke(publish,{content:[{type:'text',text:'published'}],details:publish.details}).prepared?.block,true);
});

test('no next step prescribes a directory that the publication check refuses',()=>{
  const deep=Array.from({length:13},(_,index)=>`d${index}`).join('/');
  for(const [file,shown] of [['.site/index.html',false],['site/.draft/index.html',false],['my site/index.html',false],
    [`${deep}/index.html`,false],[`${'a'.repeat(129)}/index.html`,false],['site-2/index.html',true]]) {
    const {invoke}=fixture();
    const result=invoke('write',{path:file,content:'<!doctype html><h1>Site</h1>'},success('written'));
    const directory=file.slice(0,-'/index.html'.length);
    assert.equal(result.text.includes(`Call pixel_ods_workspace_preview with args ${JSON.stringify({relativeDirectory:directory})}`),shown,result.text);
    assert.equal(result.text.includes(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP),!shown,result.text);
    assert.equal(invoke('pixel_ods_workspace_preview',{relativeDirectory:directory},success('unused')).prepared?.block===true,!shown,file);
  }
});

// The whole publication rule is stated, so a too-deep or too-long path is
// not blamed on hidden names alone.
test('the unpublishable-directory step states every part of the publication rule',()=>{
  for(const part of ['at most 12 components','512 characters total','start with a letter or digit',
    'letters, digits, dots, underscores or hyphens','128 characters maximum','hidden directories such as .site cannot be published'])
    assert.ok(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP.includes(part),part);
});

// A hidden directory can be the owner's own (a build output under a hidden
// path). The step copies it and leaves it unchanged; when the owner excluded
// changing files, it reports instead of copying.
function ownerRun(prompt) {
  const guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'owner-run',sessionId:'owner-session',sessionKey:'agent:pixel:owner'};
  guard.observeRun(context,'pixel',{prompt});
  let sequence=0;
  const invoke=(toolName,params,result)=>{
    const toolCallId=`owner-${++sequence}`,ctx={...context,toolName,toolCallId};
    const prepared=guard.beforeToolCall({toolName,params,toolCallId},ctx);
    if(prepared?.block)return {prepared};
    guard.afterToolCall({toolName,params:prepared?.params??params,toolCallId,result},ctx);
    const persisted=guard.toolResultPersist({toolName,toolCallId,message:{role:'toolResult',toolName,toolCallId,...result}},ctx);
    return {prepared,text:(persisted?.message?.content??[]).map(part=>part.text??'').join('\n')};
  };
  return {guard,context,invoke};
}
const PAGE='<!doctype html><title>Mine</title><h1>Mine</h1>';

test('an owner\'s existing hidden directory is copied, never moved, before publication',()=>{
  const {invoke}=ownerRun('Publish my existing site in the .mysite folder as a Workbench preview. Do not rebuild it.');
  const read=invoke('read',{path:'.mysite/index.html'},success(PAGE));
  assert.ok(read.text.includes(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP),read.text);
  assert.doesNotMatch(read.text,/\bmv\b|\bMove\b|"relativeDirectory":"\.mysite"/);
  assert.notEqual(invoke('exec',{command:'cp -r .mysite mysite'},success('')).prepared?.block,true);
  const copied=invoke('read',{path:'mysite/index.html'},success(PAGE));
  assert.ok(copied.text.includes('Call pixel_ods_workspace_preview with args {"relativeDirectory":"mysite"}. '),copied.text);
  assert.notEqual(invoke('pixel_ods_workspace_preview',{relativeDirectory:'mysite'},success('published')).prepared?.block,true);
});

test('when the owner excluded changing files, an unpublishable directory is reported, not copied',()=>{
  const {guard,context,invoke}=ownerRun('Publish my existing site in the .mysite folder as a Workbench preview. Do not create or modify any files.');
  const read=invoke('read',{path:'.mysite/index.html'},success(PAGE));
  assert.ok(read.text.endsWith(`[ODS Pixel next step] ${UNPUBLISHABLE_RESTRICTED_PREVIEW_DIRECTORY_STEP.trimEnd()}`),read.text);
  assert.doesNotMatch(read.text,/cp -r|Copy the files|publish BEFORE/);
  assert.equal(guard.beforeAgentFinalize({},context)?.retry?.instruction,UNPUBLISHABLE_RESTRICTED_PREVIEW_DIRECTORY_STEP.trimEnd());
});

// The one finalize revision follows the same rule as the next step: before
// this change it said 'Call tool_call now with id pixel_ods_workspace_preview
// and args {"relativeDirectory":".fleet-site"}', which is always refused.
test('the finalize revision never names an unpublishable directory',()=>{
  for(const [directory,publishable] of [['.fleet-site',false],['fleet-site',true]]) {
    const {guard,context,invoke}=ownerRun('Create a small static website with an index.html. Publish it as a Workbench preview.');
    invoke('write',{path:`${directory}/index.html`,content:PAGE},success('written'));
    const instruction=guard.beforeAgentFinalize({},context)?.retry?.instruction;
    assert.equal(instruction,publishable
      ? `Do not reply yet. Call tool_call now with id pixel_ods_workspace_preview and args ${JSON.stringify({relativeDirectory:directory})}. Do not start a sandbox server or claim another localhost URL.`
      : `Do not reply yet. ${UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP}Do not start a sandbox server or claim another localhost URL.`,directory);
  }
});
