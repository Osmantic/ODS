import test from 'node:test';
import assert from 'node:assert/strict';
import {ARTIFACT_TOOL, ARTIFACT_BOUNDARY, normalizeWorkspaceArtifact, validDeliveredArtifacts, artifactReceipt, createWorkspaceArtifactAdmission, createWorkspaceArtifactTool} from '../plugin/workspace-artifact.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';
const context={trigger:'user',agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'agent:pixel:openai-user:ods-'+ 'a'.repeat(64),toolCallId:'artifact'};
const args={relativePath:'project/report.md'}, payload=normalizeWorkspaceArtifact(args);
const receipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',...args,siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'report.md',bytes:10,sha256:'b'.repeat(64)}};
const host={...receipt,status:'succeeded',httpStatus:200,readbackVerified:true,executable:false,overwritten:false,boundary:ARTIFACT_BOUNDARY};
function fixture(request=async()=>host) {
 const guard=createToolLoopGuard();guard.observeRun(context,'pixel',{prompt:'Deliver the report as a document.'});
 const admission=createWorkspaceArtifactAdmission();
 const event={toolName:ARTIFACT_TOOL,params:args};
 const tool=createWorkspaceArtifactTool(context,{admission,reserve:s=>guard.reserveWorkspaceArtifact(s),unavailableReason:s=>guard.workspaceArtifactUnavailableReason(s),accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request});
 return {guard,admission,event,tool};
}
test('actual registration includes exact bounded schema and no model-supplied receipt',async()=>{
 const tool=(await registeredPixelTools()).find(t=>t.name===ARTIFACT_TOOL);assert.ok(tool);
 assert.deepEqual(tool.parameters,{type:'object',additionalProperties:false,required:['relativePath'],properties:{relativePath:{type:'string',minLength:1,maxLength:512}}});
 assert.equal((await tool.execute('unbound',args)).isError,true);
});
test('paths and exact receipts reject expansion, traversal and model URL/content injection',()=>{
 for(const name of ['report.md','report.PDF','a.docx','a.xlsx','a.pptx','a.zip','a.rar']) assert.equal(normalizeWorkspaceArtifact({relativePath:'project/'+name}).relativePath,'project/'+name);
 for(const value of [{...args,url:'https://elsewhere'}, {...args,bytes:1}, {relativePath:'/etc/a.pdf'}, {relativePath:'../a.pdf'}, {relativePath:'a/.hidden.md'}, {relativePath:'a/a.docm'}, {relativePath:'a/b.html'}, {relativePath:'a/'.repeat(12)+'b.pdf'}, {relativePath:'a/'.repeat(11)+'b'.repeat(129)+'.md'}]) assert.throws(()=>normalizeWorkspaceArtifact(value));
 assert.deepEqual(artifactReceipt(host,payload),receipt);
 for(const bad of [{...host,readbackVerified:false},{...host,relativePath:'other/report.md'},{...host,file:{...host.file,bytes:4194305}},{...host,file:{...host.file,path:'other.md'}},{...host,url:'http://evil'},{...host,siteId:'site-'+ 'c'.repeat(24)}])assert.throws(()=>artifactReceipt(bad,payload));
 assert.equal(validDeliveredArtifacts([receipt,receipt]),false);assert.equal(validDeliveredArtifacts([]),true);
});
test('exact direct and deferred calls consume policy admission once with session binding',()=>{
 for(const deferred of [false,true]) {
  const admission=createWorkspaceArtifactAdmission();
  const event=deferred?{toolName:'tool_call',params:{id:'openclaw:pixel-ods:'+ARTIFACT_TOOL,args}}:{toolName:ARTIFACT_TOOL,params:args};
  const id=deferred?`tool_search_code:artifact:${ARTIFACT_TOOL}:1`:context.toolCallId;
  admission.before(event,context,{block:true});assert.throws(()=>admission.take(id,payload,context));
  admission.before(event,context);assert.throws(()=>admission.take(id,{...payload,relativePath:'other.md'},context));
  assert.throws(()=>admission.take(id,payload,{...context,sessionId:'foreign'}));
  assert.deepEqual(admission.take(id,payload,context),context);assert.throws(()=>admission.take(id,payload,context));
  admission.after(event,context);assert.throws(()=>admission.take(id,payload,context));
 }
});
test('only broker-verified admitted calls attach immutable cloned metadata to current run',async()=>{
 const f=fixture();assert.equal((await f.tool.execute(context.toolCallId,args)).isError,true);
 f.admission.before(f.event,context);assert.equal((await f.tool.execute(context.toolCallId,args)).isError,undefined);
 const delivered=f.guard.deliveryVerificationForRun('run');assert.deepEqual(delivered.artifacts,[receipt]);
 delivered.artifacts[0].file.bytes=99;assert.equal(f.guard.deliveryVerificationForRun('run').artifacts[0].file.bytes,10);
 assert.equal(f.guard.deliveryVerificationForRun('foreign').artifacts,undefined);
});
test('cancelled, superseded and foreign runs cannot reserve or accept document delivery',async()=>{
 for(const mode of ['signal','superseded','foreign','ended']) {
  let release;const f=fixture(()=>new Promise(resolve=>release=resolve));const signal=new AbortController();
  f.admission.before(f.event,context);const running=f.tool.execute(context.toolCallId,args,signal.signal);
  if(mode==='signal')signal.abort();
  else if(mode==='ended')f.guard.observeAgentEnd({},context);
  else if(mode==='superseded')f.guard.observeRun({...context,runId:'next'},'pixel',{prompt:'new task'});
  else f.guard.observeRun({...context,runId:'next',sessionKey:'foreign'},'pixel',{prompt:'new task'});
  release(host);assert.equal((await running).isError,true);assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
 }
 const f=fixture();assert.equal(f.guard.reserveWorkspaceArtifact({...context,sessionKey:'foreign'}),false);
 assert.equal(f.guard.acceptWorkspaceArtifact(context,receipt),false);
});
test('four failed attempts consume the bound and forged receipts never attach',async()=>{
 let calls=0;const f=fixture(async()=>{calls++;return {...host,readbackVerified:false};});
 for(let i=0;i<6;i++){f.admission.before(f.event,context);assert.equal((await f.tool.execute(context.toolCallId,args)).isError,true);}
 assert.equal(calls,4);assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
});

test('owner Stop invalidates a pending broker receipt even when transport completes',async()=>{
 let release;const f=fixture(()=>new Promise(resolve=>release=resolve));
 f.admission.before(f.event,context);const running=f.tool.execute(context.toolCallId,args);
 await f.guard.abortUserRun('ods-'+ 'a'.repeat(64));release(host);
 assert.equal((await running).isError,true);assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
 assert.equal(f.guard.reserveWorkspaceArtifact(context),false);
});

test('unsupported team roles and noninteractive surfaces are rejected before broker publication',async()=>{
 for(const role of ['Builder','Coordinator','Explorer','Reviewer',null]) {
  let calls=0;const f=fixture(async()=>{calls++;return host});
  const scope=role?context:{...context,trigger:'cron'};
  f.guard.observeRun(scope,'pixel',{prompt:role?`You are the ${role} in the owner's Portal team.\nDeliver report.md`:'Background goal round'});
  f.admission.before(f.event,context);const result=await f.tool.execute(context.toolCallId,args);
  assert.equal(result.isError,true);assert.equal(calls,0);assert.match(result.content[0].text,/No document publication was attempted/);
 }
 const f=fixture();f.guard.observeRun({...context,sessionKey:'agent:pixel:subagent:other'},'pixel',{prompt:'Report'});
 assert.equal(f.guard.reserveWorkspaceArtifact({...context,sessionKey:'agent:pixel:subagent:other'}),false);
});

test('wrong path field gives schema correction without consuming admission or a publication attempt',async()=>{
 let calls=0;const f=fixture(async()=>{calls++;return host});
 assert.match(f.tool.description,/relativePath.*Playground\/report\.pdf/);
 f.admission.before(f.event,context);
 const rejected=await f.tool.execute(context.toolCallId,{path:args.relativePath,secret:'never-echo-this-value'});
 assert.equal(rejected.details.code,'invalid-arguments');assert.match(rejected.content[0].text,/relativePath, not path/);
 assert.match(rejected.content[0].text,/No document publication was attempted/);assert.doesNotMatch(rejected.content[0].text,/never-echo-this-value/);
 assert.equal(calls,0);
 // The invalid input was rejected before take() or reserve(): this admitted
 // call can still consume its exact correct request once.
 const corrected=await f.tool.execute(context.toolCallId,args);assert.equal(corrected.isError,undefined);assert.equal(calls,1);
 assert.deepEqual(f.guard.deliveryVerificationForRun(context.runId).artifacts,[receipt]);
});
test('admission, transport and invalid broker receipts have distinct bounded diagnostics',async()=>{
 const unbound=fixture();assert.equal((await unbound.tool.execute(context.toolCallId,args)).details.code,'admission-unavailable');
 for(const [request,code] of [[async()=>{throw new Error('secret transport detail')},'publication-unavailable'],[async()=>({...host,readbackVerified:false}),'receipt-unverified']]) {
  const f=fixture(request);f.admission.before(f.event,context);const result=await f.tool.execute(context.toolCallId,args);
  assert.equal(result.details.code,code);assert.doesNotMatch(result.content[0].text,/secret transport detail/);
  assert.ok(result.content[0].text.length<1024);assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
 }
});

test('missing runtime trigger and exhausted attempts are distinct reasons, never guessed from unavailable',async()=>{
 const f=fixture();f.guard.observeRun({...context,runId:'missing-trigger',trigger:undefined},'pixel',{prompt:'Deliver report'});
 const missingScope={...context,runId:'missing-trigger',trigger:undefined};
 f.admission.before(f.event,missingScope);const missing=await f.tool.execute(context.toolCallId,args);
 assert.equal(missing.details.code,'trigger-unavailable');assert.match(missing.content[0].text,/not an exhausted publication limit/);
 assert.equal(f.guard.workspaceArtifactUnavailableReason(missingScope),'trigger-unavailable');
 const budget=fixture();for(let i=0;i<4;i++)assert.equal(budget.guard.reserveWorkspaceArtifact(context),true);
 assert.equal(budget.guard.workspaceArtifactUnavailableReason(context),'publication-attempt-limit');
 assert.equal(budget.guard.acceptWorkspaceArtifact(context,receipt),true,'fourth reserved receipt remains accepted');
});

test('real before-tool refresh keeps prompt trigger and enriches its missing session key',async()=>{
 for(const omittedKey of [false,true]) {
  const guard=createToolLoopGuard(),admission=createWorkspaceArtifactAdmission();let calls=0;
  const promptContext={...context,...(omittedKey?{sessionKey:undefined}: {})};
  guard.observeRun(promptContext,'pixel',{prompt:'Deliver report.md'});
  const toolContext={...context,trigger:undefined,toolName:'tool_call'};
  const event={toolName:'tool_call',toolCallId:context.toolCallId,params:{id:ARTIFACT_TOOL,args}};
  const decision=guard.beforeToolCall(event,toolContext);
  admission.before(event,toolContext,decision);
  assert.equal(guard.workspaceArtifactUnavailableReason(toolContext),undefined);
  const tool=createWorkspaceArtifactTool(context,{admission,reserve:s=>guard.reserveWorkspaceArtifact(s),unavailableReason:s=>guard.workspaceArtifactUnavailableReason(s),accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request:async()=>{calls++;return host}});
  const result=await tool.execute(`tool_search_code:artifact:${ARTIFACT_TOOL}:1`,args);
  assert.equal(result.isError,undefined);assert.equal(calls,1);assert.deepEqual(guard.deliveryVerificationForRun('run').artifacts,[receipt]);
 }
});
test('explicit later background trigger or foreign session revokes merged identity permanently for that run',()=>{
 for(const later of [{trigger:'cron'},{trigger:'heartbeat'},{trigger:'user',sessionKey:'agent:pixel:openai-user:ods-'+ 'b'.repeat(64)},{sessionId:'different-session'}]) {
  const f=fixture();const changed={...context,trigger:undefined,...later};
  f.guard.beforeToolCall({toolName:'read',params:{path:'report.md'}},changed);
  assert.equal(f.guard.reserveWorkspaceArtifact(context),false);
  assert.match(f.guard.workspaceArtifactUnavailableReason(context),/noninteractive-turn|run-identity-conflict/);
  f.guard.observeRun(context,'pixel',{prompt:'Owner message cannot silently undo conflicting runtime metadata'});
  assert.equal(f.guard.reserveWorkspaceArtifact(context),false);
  const fresh={...context,runId:'fresh-owner-run'};
  f.guard.observeRun(fresh,'pixel',{prompt:'A separate owner turn'});
  assert.equal(f.guard.reserveWorkspaceArtifact(fresh),true,'revocation never leaks across run IDs');
 }
});

for(const wrapped of [false,true]) test(`document download recovers from a rejected ${wrapped?'deferred':'direct'} website tool`,async()=>{
 const guard=createToolLoopGuard();
 guard.observeRun(context,'pixel',{prompt:'Create report.md containing exactly "Hello." and give me the file as a download.'});
 const preview='pixel_ods_workspace_preview';
 const event=wrapped
  ?{toolName:'tool_call',params:{id:'openclaw:pixel-ods:'+preview,args:{relativeDirectory:'report.md'}}}
  :{toolName:preview,params:{relativeDirectory:'report.md'}};
 const scope={...context,toolCallId:'wrong-preview',toolName:event.toolName};
 const decision=guard.beforeToolCall(event,scope);
 assert.equal(decision.block,true);
 const rejected={isError:true,content:[{type:'text',text:decision.blockReason}]};
 guard.afterToolCall({...event,result:wrapped?{details:{tool:{id:'openclaw:pixel-ods:'+preview,name:preview},result:rejected}}:rejected},scope);
 const admission=createWorkspaceArtifactAdmission();
 const artifactEvent={toolName:ARTIFACT_TOOL,params:args};
 admission.before(artifactEvent,context);
 const tool=createWorkspaceArtifactTool(context,{admission,reserve:s=>guard.reserveWorkspaceArtifact(s),unavailableReason:s=>guard.workspaceArtifactUnavailableReason(s),accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request:async()=>host});
 assert.equal((await tool.execute(context.toolCallId,args)).isError,undefined);
 const delivery=guard.deliveryVerificationForRun('run');
 assert.ok(['none','passed'].includes(delivery.status),JSON.stringify(delivery));
 assert.doesNotMatch(delivery.text??'',/website|browser preview/i);
 assert.deepEqual(delivery.artifacts,[receipt]);
 assert.match(decision.blockReason,/pixel_ods_workspace_artifact/);
 assert.match(decision.blockReason,/relativePath/);
});

test('document receipt cannot bypass an owner-requested website preview',()=>{
 const guard=createToolLoopGuard();
 guard.observeRun(context,'pixel',{prompt:'Build a website preview with a working button, and give me report.md as a download.'});
 const event={toolName:'pixel_ods_workspace_preview',params:{relativeDirectory:'site'}};
 const decision=guard.beforeToolCall(event,{...context,toolName:event.toolName});
 assert.equal(decision.block,true);
 assert.doesNotMatch(decision.blockReason,/Do not call pixel_ods_workspace_preview for documents/);
 guard.afterToolCall({...event,result:{isError:true,content:[{type:'text',text:decision.blockReason}]}},context);
 assert.equal(guard.reserveWorkspaceArtifact(context),true);
 assert.equal(guard.acceptWorkspaceArtifact(context,receipt),true);
 const delivery=guard.deliveryVerificationForRun('run');
 assert.equal(delivery.status,'failed');
 assert.match(delivery.text,/website|browser preview/i);
 assert.deepEqual(delivery.artifacts,[receipt]);
});

test('a rejected document preview cannot validate an invalid artifact or survive owner cancellation',async()=>{
 for(const cancelled of [false,true]) {
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'Give me existing report.md as a download.'});
  const event={toolName:'pixel_ods_workspace_preview',params:{relativeDirectory:'report.md'}};
  const decision=guard.beforeToolCall(event,{...context,toolName:event.toolName});
  guard.afterToolCall({...event,result:{isError:true,content:[{type:'text',text:decision.blockReason}]}},context);
  assert.equal(guard.reserveWorkspaceArtifact(context),true);
  if(cancelled) await guard.abortUserRun('ods-'+ 'a'.repeat(64));
  assert.equal(guard.acceptWorkspaceArtifact(context,cancelled?receipt:{...receipt,siteId:'site-invalid'}),false);
  assert.equal(guard.deliveryVerificationForRun('run').artifacts,undefined);
 }
});

test('a later owner website request does not inherit document-only handling',()=>{
 const guard=createToolLoopGuard();
 guard.observeRun(context,'pixel',{prompt:'Give me existing report.md as a download.'});
 const event={toolName:'pixel_ods_workspace_preview',params:{relativeDirectory:'site'}};
 assert.match(guard.beforeToolCall(event,{...context,toolName:event.toolName}).blockReason,/pixel_ods_workspace_artifact/);
 const next={...context,runId:'next'};
 guard.observeRun(next,'pixel',{prompt:'Create a website preview with a working button.'});
 const decision=guard.beforeToolCall(event,{...next,toolName:event.toolName});
 assert.equal(decision.block,true);
 assert.doesNotMatch(decision.blockReason,/Do not call pixel_ods_workspace_preview for documents/);
});
