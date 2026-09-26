import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {createHash} from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
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
  // The owner asked for the hidden name itself, so the step never points at
  // "the directory name the owner asked for"; it suggests that name without its dot.
  assert.doesNotMatch(read.text,/the directory name the owner asked for/);
  assert.ok(read.text.includes("into a directory that meets this rule within the owner's requested scope (for a hidden directory, the same name without its leading dot)"),read.text);
  assert.notEqual(invoke('exec',{command:'cp -r .mysite mysite'},success('')).prepared?.block,true);
  // A command made the copy, so no step names it; the model publishes it by
  // name, as the copy step says.
  const copied=invoke('read',{path:'mysite/index.html'},success(PAGE));
  assert.doesNotMatch(copied.text,/"relativeDirectory"/);
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

// Re-verification of #6747 at 65412f21: the model read an earlier round's
// site (fleet-site-old, never written by this run) and then wrote the new
// site under the hidden .fleet-site. The next step, the wrapped
// tool_describe answer and the finalize revision each named fleet-site-old,
// finalize stored it as the publication default, an argumentless publication
// was admitted as fleet-site-old, and the owner was told "Your preview is
// ready" for bytes this run never wrote. Guidance now names a directory for
// publication only when this run wrote its index.html with write or edit.
const WEBSITE='Create a small static website in fleet-site with an index.html. It has a "Show sold out" button that reveals a hidden card. Publish it as a Workbench preview and check the show/hide interaction.';
const OLD_SITE='<!doctype html><html><head><title>Harbor Lights</title></head><body><h1>Harbor Lights</h1><p>last round</p></body></html>';
const NEW_SITE='<!doctype html><html><head><title>Harbor Lights</title></head><body><h1>Harbor Lights</h1><p>this round</p></body></html>';
const PUBLISH_STEP=directory=>`Call pixel_ods_workspace_preview with args ${JSON.stringify({relativeDirectory:directory})}. `;
const FINALIZE_PUBLISH=directory=>`Do not reply yet. Call tool_call now with id pixel_ods_workspace_preview and args ${JSON.stringify({relativeDirectory:directory})}. Do not start a sandbox server or claim another localhost URL.`;
const FINALIZE_COPY=`Do not reply yet. ${UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP}Do not start a sandbox server or claim another localhost URL.`;
const DESCRIBE_PUBLISH='tool_describe is its own tool, not a tool_call id. pixel_ods_workspace_preview is directly available in your tool list; call pixel_ods_workspace_preview itself';

// A host receipt for exactly these files, as the publication tool reports it.
function receipt(directory,files) {
  const digest=createHash('sha256');let bytes=0;
  const entries=Object.entries(files).sort(([a],[b])=>a<b?-1:1);
  for(const [name,content] of entries) {
    const n=Buffer.from(name),d=Buffer.from(content),a=Buffer.alloc(4),b=Buffer.alloc(8);
    a.writeUInt32BE(n.length);b.writeBigUInt64BE(BigInt(d.length));digest.update(a).update(n).update(b).update(d);bytes+=d.length;
  }
  const sha256=digest.digest('hex'),siteId=`site-${sha256.slice(0,24)}`;
  return {schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:directory,siteId,sha256,
    entryFile:'index.html',entrySha256:createHash('sha256').update(files['index.html']).digest('hex'),
    files:entries.length,bytes,port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,httpStatus:200,
    readbackVerified:true,executable:false,overwritten:false,publishedPaths:entries.map(([n])=>n),publishedPathsOmitted:0};
}

// The full OpenClaw hook sequence per call against a real workspace; a
// refused call gets the SDK's standard veto result.
function siteRun(t,{prompt=WEBSITE,inspection=true}={}) {
  const root=mkdtempSync(path.join(os.tmpdir(),'pixel-guided-copy-'));
  t.after(()=>rmSync(root,{recursive:true,force:true}));
  const guard=createToolLoopGuard({workspacePreviewInspectionAvailable:inspection,abortRun:()=>true});
  const context={agentId:'pixel',runId:'guided-run',sessionId:'guided-session',sessionKey:'agent:pixel:main'};
  guard.observeRun(context,'pixel',{prompt},{workspaceRoot:root});
  let sequence=0;
  const hooks=(toolName,params,execute)=>{
    const toolCallId=`guided-${++sequence}`,ctx={...context,toolName,toolCallId};
    guard.observeModelCall({},context);
    const decision=guard.beforeToolCall({toolName,params,toolCallId},ctx);
    const admitted=decision?.block?params:toolName==='tool_call'?decision?.params??params:{...params,...decision?.params};
    const result=decision?.block?{content:[{type:'text',text:decision.blockReason}],
      details:{status:'blocked',deniedReason:'plugin-before-tool-call',reason:decision.blockReason}}:execute(admitted);
    const isError=decision?.block===true||result.isError===true;
    guard.afterToolCall({toolName,params:admitted,toolCallId,result,...(isError?{error:result.content[0].text}:{})},ctx);
    const message={role:'toolResult',toolName,toolCallId,isError,...structuredClone(result)};
    const persisted=guard.toolResultPersist({toolName,toolCallId,message},ctx)?.message??message;
    return {decision,admitted,text:persisted.content.map(part=>part.text??'').join('\n')};
  };
  const disk=(file,content)=>{mkdirSync(path.dirname(path.join(root,file)),{recursive:true});writeFileSync(path.join(root,file),content);};
  const read=file=>hooks('read',{path:file},()=>success(readFileSync(path.join(root,file),'utf8')));
  const write=(file,content)=>hooks('write',{path:file,content},()=>{disk(file,content);return success(`Successfully wrote ${content.length} bytes to ${file}`);});
  const copy=(from,to)=>hooks('exec',{command:`cp -r ${from} ${to}`},()=>{
    disk(`${to}/index.html`,readFileSync(path.join(root,from,'index.html'),'utf8'));return success('');});
  const describePublish=()=>hooks('tool_call',{id:'tool_describe',args:{id:'pixel_ods_workspace_preview'}},()=>assert.fail('must not run')).decision.blockReason;
  const finalize=()=>guard.beforeAgentFinalize({},context)?.retry?.instruction;
  const publish=args=>hooks('pixel_ods_workspace_preview',args,admitted=>({content:[{type:'text',text:'published'}],
    details:receipt(admitted.relativeDirectory,{'index.html':readFileSync(path.join(root,admitted.relativeDirectory,'index.html'),'utf8')})}));
  return {guard,disk,read,write,copy,describePublish,finalize,publish};
}

for(const inspection of [true,false]) test(`an earlier round's site that was only read never stands in for a new site written under a hidden directory (inspection ${inspection})`,t=>{
  const r=siteRun(t,{inspection});
  r.disk('fleet-site-old/index.html',OLD_SITE);
  r.read('fleet-site-old/index.html');
  const written=r.write('.fleet-site/index.html',NEW_SITE);
  const described=r.describePublish(),finalized=r.finalize();
  // Nothing became the default of an argumentless publication, so no
  // preview of the earlier site is published or delivered as ready.
  const argless=r.publish({});
  assert.equal(argless.decision?.block,true,JSON.stringify(argless.decision));
  assert.doesNotMatch(argless.decision.blockReason,/fleet-site-old/);
  assert.notEqual(r.guard.verificationForRun('guided-run')?.status,'passed');
  assert.doesNotMatch(String(r.guard.deliveryVerificationForRun('guided-run')?.text),/Your preview is ready/);
  // The wrapped answer and the finalize revision never name it; they, and
  // the next step, ask for a copy of the hidden site and name no directory.
  assert.equal(described,`${DESCRIBE_PUBLISH}.`);
  assert.equal(finalized,FINALIZE_COPY);
  assert.ok(written.text.includes(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP),written.text);
  assert.doesNotMatch(written.text,/fleet-site-old|"relativeDirectory"/);
  // Read again, the earlier site is still only read: never offered.
  r.read('fleet-site-old/index.html');
  assert.equal(r.finalize(),FINALIZE_COPY);
});

// The same with a finalize revision between the read and the hidden write.
// The earlier site is then the only entry seen, but this run only read it:
// it is neither named nor stored as the argumentless default, so the later
// hidden write does not leave it steering publication.
test('a finalize revision before the hidden write leaves no earlier site as the publication default',t=>{
  const r=siteRun(t);
  r.disk('fleet-site-old/index.html',OLD_SITE);
  r.read('fleet-site-old/index.html');
  assert.doesNotMatch(r.finalize(),/"relativeDirectory"|fleet-site-old/);
  const written=r.write('.fleet-site/index.html',NEW_SITE);
  const argless=r.publish({});
  assert.equal(argless.decision?.block,true,JSON.stringify(argless.decision));
  assert.notEqual(r.guard.verificationForRun('guided-run')?.status,'passed');
  assert.ok(written.text.includes(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP),written.text);
  assert.equal(r.describePublish(),`${DESCRIBE_PUBLISH}.`);
  // An entry this run wrote is still stored as the default, as before.
  const own=siteRun(t);
  own.write('fleet-site/index.html',NEW_SITE);
  assert.equal(own.finalize(),FINALIZE_PUBLISH('fleet-site'));
  own.disk('notes/index.html','<!doctype html><title>notes</title>');
  own.read('notes/index.html');
  assert.deepEqual(own.publish({}).admitted,{relativeDirectory:'fleet-site'});
});

// Re-verification of #6747 at 869a1dc9: a visible directory counted as this
// run's copy of the hidden site when its index.html matched the hidden one
// byte for byte. An earlier round's site with the same index.html (a split
// page's boilerplate) was named, published and delivered as ready with that
// round's styles.css. No directory is inferred to be a copy any more: a
// directory that a command copied, or that was only read, is never named or
// made the default; the model publishes it by name. One whose index.html this
// run wrote with write or edit is named, as before.
test('a visible directory is named for a hidden site only when this run writes its index.html',t=>{
  // cp -r, then a read of the copy: the copy step stays, and names nothing.
  const copied=siteRun(t);
  copied.disk('fleet-site-old/index.html',OLD_SITE);
  copied.read('fleet-site-old/index.html');
  copied.write('.fleet-site/index.html',NEW_SITE);
  assert.notEqual(copied.copy('.fleet-site','fleet-site').decision?.block,true);
  const readback=copied.read('fleet-site/index.html');
  assert.doesNotMatch(readback.text,/"relativeDirectory"/);
  assert.equal(copied.describePublish(),`${DESCRIBE_PUBLISH}.`);
  assert.equal(copied.finalize(),FINALIZE_COPY);
  assert.equal(copied.publish({}).decision?.block,true);
  // Published by name, the copy is admitted and verified.
  assert.notEqual(copied.publish({relativeDirectory:'fleet-site'}).decision?.block,true);
  assert.equal(copied.guard.verificationForRun('guided-run').preview?.relativeDirectory,'fleet-site');

  // The earlier site overwritten on disk with the new bytes, then read: not named.
  const overwritten=siteRun(t);
  overwritten.disk('fleet-site-old/index.html',OLD_SITE);
  overwritten.read('fleet-site-old/index.html');
  overwritten.write('.fleet-site/index.html',NEW_SITE);
  overwritten.disk('fleet-site-old/index.html',NEW_SITE);
  const reread=overwritten.read('fleet-site-old/index.html');
  assert.doesNotMatch(reread.text,/"relativeDirectory"/);
  assert.equal(overwritten.finalize(),FINALIZE_COPY);

  // Written by this run with write: named, and the default of an
  // argumentless publication.
  const rewritten=siteRun(t);
  rewritten.disk('fleet-site-old/index.html',OLD_SITE);
  rewritten.read('fleet-site-old/index.html');
  rewritten.write('.fleet-site/index.html',NEW_SITE);
  const own=rewritten.write('fleet-site/index.html',NEW_SITE.replace('this round','today'));
  assert.ok(own.text.includes(PUBLISH_STEP('fleet-site')),own.text);
  assert.equal(rewritten.describePublish(),`${DESCRIBE_PUBLISH} with args {"relativeDirectory":"fleet-site"}.`);
  assert.equal(rewritten.finalize(),FINALIZE_PUBLISH('fleet-site'));
  assert.deepEqual(rewritten.publish({}).admitted,{relativeDirectory:'fleet-site'});
});

// The reviewer's probe: the earlier round's site has the same index.html as
// this run's hidden site but its own styles.css. With the probe's own request
// the hidden writes are refused (a new project belongs under Playground), so
// the earlier site is the only entry seen; with WEBSITE they are admitted.
// Either way, read before or after the hidden writes and with the same or a
// different index.html, the earlier site is never named, never the default
// and never delivered as ready. Published by name beside the admitted hidden
// site, it is still admitted (publishing an existing site is supported).
const SPLIT_INDEX='<!doctype html><html><head><meta charset="utf-8"><title>Fleet site</title><link rel="stylesheet" href="styles.css"><script src="script.js" defer></script></head><body><main id="app"></main></body></html>\n';
const PROBE_PROMPTS={refused:'Create a small static website in fleet-site with index.html, styles.css and script.js, with a forest theme. Publish it as a Workbench preview.',
  admitted:WEBSITE};
for(const [writes,prompt] of Object.entries(PROBE_PROMPTS)) for(const sameIndex of [true,false]) for(const readLast of [false,true])
test(`an earlier site is never named for a hidden site (hidden writes ${writes}, same index ${sameIndex}, read ${readLast?'after':'before'} them)`,t=>{
  const r=siteRun(t,{prompt,inspection:false});
  r.disk('fleet-site-old/index.html',sameIndex?SPLIT_INDEX:SPLIT_INDEX.replace('Fleet site','Fleet site (ocean)'));
  r.disk('fleet-site-old/styles.css','body{background:#123;color:#fff} /* last round: ocean theme */\n');
  r.disk('fleet-site-old/script.js','/* old */\n');
  if(!readLast)r.read('fleet-site-old/index.html');
  const steps=[r.write('.fleet-site/index.html',SPLIT_INDEX),
    r.write('.fleet-site/styles.css','body{background:#0b3d20;color:#e8f5e9} /* this round: forest theme */\n'),
    r.write('.fleet-site/script.js','document.body.dataset.theme="forest";\n')];
  assert.equal(steps[0].decision?.block===true,writes==='refused',steps[0].text);
  if(readLast)steps.push(r.read('fleet-site-old/index.html'));
  for(const step of steps)assert.doesNotMatch(step.text,/"relativeDirectory"|fleet-site-old/,step.text);
  if(writes==='admitted')assert.ok(steps[0].text.includes(UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP),steps[0].text);
  assert.equal(r.describePublish(),`${DESCRIBE_PUBLISH}.`);
  const finalized=r.finalize();
  assert.doesNotMatch(finalized,/"relativeDirectory"|fleet-site-old/);
  if(writes==='admitted')assert.equal(finalized,FINALIZE_COPY);
  const argless=r.publish({});
  assert.equal(argless.decision?.block,true,JSON.stringify(argless.decision));
  assert.notEqual(r.guard.verificationForRun('guided-run')?.status,'passed');
  assert.doesNotMatch(String(r.guard.deliveryVerificationForRun('guided-run')?.text),/Your preview is ready/);
  if(writes==='admitted')assert.notEqual(r.publish({relativeDirectory:'fleet-site-old'}).decision?.block,true);
});

// Reviewer probe rr6747-extra: the owner's own hidden site, only read. Each
// restriction keeps its own step, and neither the step, the finalize
// revision nor the wrapped tool_describe answer names a directory.
test('an owner\'s hidden site that was only read is never named, whatever the owner excluded',t=>{
  for(const [prompt,step] of [
    ['Publish my existing site in the .mysite folder as a Workbench preview. Do not change any files.',UNPUBLISHABLE_RESTRICTED_PREVIEW_DIRECTORY_STEP],
    ['Publish the existing .mysite folder as a Workbench preview. Do not run any commands.',UNPUBLISHABLE_RESTRICTED_PREVIEW_DIRECTORY_STEP],
    ['Publish my existing site in the .mysite folder as a Workbench preview. Do not rebuild it.',UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP]]) {
    const r=siteRun(t,{prompt});
    r.disk('.mysite/index.html',PAGE);
    const read=r.read('.mysite/index.html');
    assert.ok(read.text.includes(step.trimEnd()),read.text);
    assert.doesNotMatch(read.text,/"relativeDirectory"/);
    const finalized=r.finalize();
    assert.equal(finalized,step===UNPUBLISHABLE_PREVIEW_DIRECTORY_STEP?FINALIZE_COPY:step.trimEnd(),prompt);
    assert.equal(r.describePublish(),`${DESCRIBE_PUBLISH}.`,prompt);
  }
});

// The same probe with a build: a template directory that was only read beside
// a hidden build output that was only read. This run wrote neither, so no
// directory is guessed and the template is never offered.
test('a read-only template beside a hidden build output is never offered for publication',t=>{
  const r=siteRun(t,{prompt:'Build my existing app and show me a Workbench preview of the built site.'});
  r.disk('public/index.html','<!doctype html><div id="root"></div><!-- %PUBLIC_URL% template -->');
  r.disk('.output/public/index.html','<!doctype html><html><head><title>t</title></head><body><h1>x</h1></body></html>');
  r.read('public/index.html');
  const built=r.read('.output/public/index.html');
  assert.doesNotMatch(built.text,/"relativeDirectory":"public"/);
  assert.doesNotMatch(r.finalize(),/"relativeDirectory"/);
  assert.equal(r.describePublish(),`${DESCRIBE_PUBLISH}.`);
  assert.equal(r.publish({}).decision?.block,true);
});
