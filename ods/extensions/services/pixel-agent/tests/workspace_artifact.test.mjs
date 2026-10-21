import test from 'node:test';
import assert from 'node:assert/strict';
import {ARTIFACT_TOOL, ARTIFACT_BOUNDARY, normalizeWorkspaceArtifact, validDeliveredArtifacts, artifactReceipt, createWorkspaceArtifactAdmission, createWorkspaceArtifactTool} from '../plugin/workspace-artifact.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';
const context={trigger:'user',agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'agent:pixel:openai-user:ods-'+ 'a'.repeat(64),toolCallId:'artifact'};
const args={relativePath:'project/report.md'}, payload=normalizeWorkspaceArtifact(args);
const receipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',...args,siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'report.md',bytes:10,sha256:'b'.repeat(64)}};
const host={...receipt,status:'succeeded',httpStatus:200,readbackVerified:true,executable:false,overwritten:false,boundary:ARTIFACT_BOUNDARY};
// Exact failure envelope produced by workspace_preview.py _error_result.
const hostFailure={schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'failed',
 error:'ODS workspace preview publication failed',errorCode:'unsafe_file',
 boundary:'Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.'};
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

test('registered artifact tool guides unsupported code to ZIP without renaming sources',async()=>{
 const tool=(await registeredPixelTools()).find(t=>t.name===ARTIFACT_TOOL);
 assert.match(tool.description,/unsupported source-code files.*code projects.*ZIP/i);
 assert.match(tool.description,/preserv(?:e|ing) (?:original )?filenames and bytes/i);
 assert.match(tool.description,/never rename source.*\.txt/i);
 assert.match(tool.description,/verify archive contents and integrity/i);
 assert.match(tool.description,/at most 4 MiB/);
});

test('unsupported code rejection explains ZIP recovery without spending the bounded publication budget',async()=>{
 let calls=0;
 const zipArgs={relativePath:'project/source.zip'};
 const zipReceipt={...receipt,...zipArgs,file:{path:'source.zip',bytes:4*1024*1024,sha256:'b'.repeat(64)}};
 const zipHost={...host,...zipReceipt};
 const f=fixture(async()=>{calls++;return zipHost});
 for(const path of ['project/main.py','project/main.js','project/source.py.txt.exe']) {
  const value={relativePath:path};
  f.admission.before({toolName:ARTIFACT_TOOL,params:value},context);
  const rejected=await f.tool.execute(context.toolCallId,value);
  assert.equal(rejected.details.code,'invalid-arguments');
  assert.match(rejected.content[0].text,/ZIP.*preserving filenames and bytes/);
  assert.match(rejected.content[0].text,/never rename source.*\.txt/i);
  assert.equal(calls,0);
 }
 for(let n=0;n<4;n++) {
  f.admission.before({toolName:ARTIFACT_TOOL,params:zipArgs},context);
  assert.equal((await f.tool.execute(context.toolCallId,zipArgs)).isError,undefined);
 }
 f.admission.before({toolName:ARTIFACT_TOOL,params:zipArgs},context);
 assert.equal((await f.tool.execute(context.toolCallId,zipArgs)).details.code,'publication-attempt-limit');
 assert.equal(calls,4);
 assert.throws(()=>artifactReceipt({...zipHost,file:{...zipReceipt.file,bytes:4*1024*1024+1}},normalizeWorkspaceArtifact(zipArgs)));
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

test('known document publisher failures retain the bounded category without attaching a download',async()=>{
 for(const code of ['unsafe_file','unsafe_directory','unsupported_file_type','unavailable']) {
  const f=fixture(async()=>({...hostFailure,errorCode:code}));
  f.admission.before(f.event,context);
  const result=await f.tool.execute(context.toolCallId,args);
  assert.equal(result.isError,true);
  assert.deepEqual(result.details,{status:'failed',kind:'ods-pixel-workspace-artifact',code:'publication-rejected',hostErrorCode:code});
  assert.match(result.content[0].text,/No verified download is attached/);
  assert.match(result.content[0].text,/Preserve the workspace file/);
  assert.doesNotMatch(result.content[0].text,/receipt.*not.*valid|file corruption/);
  assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
 }
});

test('unavailable publication preserves the requested path until an explicit corrected nested-path call',async()=>{
 const basenameArgs={relativePath:'report.md'};
 const nestedArgs={relativePath:'workspace-pixel/report.md'};
 const nestedReceipt={...receipt,...nestedArgs};
 const requests=[];
 const f=fixture(async request=>{
  requests.push(structuredClone(request));
  return request.relativePath===nestedArgs.relativePath
   ? {...host,...nestedReceipt}
   : {...hostFailure,errorCode:'unavailable'};
 });
 f.admission.before({toolName:ARTIFACT_TOOL,params:basenameArgs},context);
 const failed=await f.tool.execute(context.toolCallId,basenameArgs);
 assert.equal(failed.details.hostErrorCode,'unavailable');
 assert.match(failed.content[0].text,/exact workspace-relative path.*file you wrote or verified.*file exists there/);
 assert.match(failed.content[0].text,/preserve every directory component/);
 assert.match(failed.content[0].text,/does not establish a missing file or a file-permission problem/);
 assert.deepEqual(requests,[normalizeWorkspaceArtifact(basenameArgs)]);
 assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
 f.admission.before({toolName:ARTIFACT_TOOL,params:nestedArgs},context);
 assert.equal((await f.tool.execute(context.toolCallId,nestedArgs)).isError,undefined);
 assert.deepEqual(requests,[normalizeWorkspaceArtifact(basenameArgs),normalizeWorkspaceArtifact(nestedArgs)]);
 assert.deepEqual(f.guard.deliveryVerificationForRun('run').artifacts,[nestedReceipt]);
});

test('malformed, unrelated and unknown host failures retain strict receipt rejection and never reflect host text',async()=>{
 const secret='PRIVATE_HOST_RESPONSE_CANARY';
 const {boundary,...missingBoundary}=hostFailure;
 for(const response of [null,[],missingBoundary,{...hostFailure,schemaVersion:true},
  {...hostFailure,kind:'foreign'},{...hostFailure,status:'succeeded'},
  {...hostFailure,error:secret},{...hostFailure,errorCode:secret},
  {...hostFailure,errorCode:'__proto__'},{...hostFailure,errorCode:'missing_entry'},
  {...hostFailure,boundary:secret},{...hostFailure,extra:secret},
  {...hostFailure,artifactError:{path:secret,line:1,column:1}}]) {
  const f=fixture(async()=>response);f.admission.before(f.event,context);
  const result=await f.tool.execute(context.toolCallId,args);
  assert.equal(result.details.code,'receipt-unverified');
  assert.equal(result.details.hostErrorCode,undefined);
  assert.doesNotMatch(JSON.stringify(result),new RegExp(secret));
  assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
 }
});

test('publisher rejection still consumes the publication budget and does not bypass admission',async()=>{
 let calls=0;const f=fixture(async()=>{calls++;return hostFailure});
 assert.equal((await f.tool.execute(context.toolCallId,args)).details.code,'admission-unavailable');
 assert.equal(calls,0);
 for(let i=0;i<4;i++) {
  f.admission.before(f.event,context);
  assert.equal((await f.tool.execute(context.toolCallId,args)).details.hostErrorCode,'unsafe_file');
 }
 f.admission.before(f.event,context);
 assert.equal((await f.tool.execute(context.toolCallId,args)).details.code,'publication-attempt-limit');
 assert.equal(calls,4);
 assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
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

for(const wrapped of [false,true]) for(const failure of ['result','error']) test(`document download recovers from a rejected ${wrapped?'deferred':'direct'} website tool (${failure})`,async()=>{
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
 guard.afterToolCall({...event,...(failure==='error' ? {error:decision.blockReason} : {result:wrapped?{details:{tool:{id:'openclaw:pixel-ods:'+preview,name:preview},result:rejected}}:rejected})},scope);
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

for(const failure of ['result','error']) test(`document receipt cannot bypass an owner-requested website preview (${failure})`,()=>{
 const guard=createToolLoopGuard();
 guard.observeRun(context,'pixel',{prompt:'Build a website preview with a working button, and give me report.md as a download.'});
 const event={toolName:'pixel_ods_workspace_preview',params:{relativeDirectory:'site'}};
 const decision=guard.beforeToolCall(event,{...context,toolName:event.toolName});
 assert.equal(decision.block,true);
 assert.doesNotMatch(decision.blockReason,/Do not call pixel_ods_workspace_preview for documents/);
 guard.afterToolCall({...event,...(failure==='error' ? {error:decision.blockReason} : {result:{isError:true,content:[{type:'text',text:decision.blockReason}]}})},context);
 assert.equal(guard.reserveWorkspaceArtifact(context),true);
 assert.equal(guard.acceptWorkspaceArtifact(context,receipt),true);
 const delivery=guard.deliveryVerificationForRun('run');
 assert.equal(delivery.status,'failed');
 assert.match(delivery.text,/website|browser preview/i);
 assert.deepEqual(delivery.artifacts,[receipt]);
});

for(const failure of ['result','error']) test(`plain text attachment survives four blocked previews and two artifact deliveries (${failure})`,async()=>{
 // The owner prompt and seven-call order match the Mac fleet-check journey.
 // Broker metadata is a fixture. The live transcript retains isError, not the
 // after-tool hook envelope; exercise both supported failure representations.
 const prompt='Create a plain text file named fleet-check.txt containing exactly these three lines, then attach it so I can download it:\nMaya: quote Thursday\nLuis: testing Friday\nCSV: owner and date unassigned';
 const content='Maya: quote Thursday\nLuis: testing Friday\nCSV: owner and date unassigned\n';
 const relativePath='fleet-check.txt',sha256='2f04ae05f1d8d14be14f26dec03ca48e3b804ea67d8353d654e391b0235ce3e0';
 const documentReceipt={...receipt,relativePath,file:{path:relativePath,bytes:73,sha256}};
 const guard=createToolLoopGuard(),admission=createWorkspaceArtifactAdmission();
 guard.observeRun(context,'pixel',{prompt});
 const write={toolName:'write',toolCallId:'write',params:{path:relativePath,content}};
 const writeScope={...context,toolName:'write',toolCallId:'write'};
 assert.notEqual(guard.beforeToolCall(write,writeScope)?.block,true);
 guard.afterToolCall({...write,result:{content:[{type:'text',text:'Successfully wrote 73 bytes to fleet-check.txt'}]}},writeScope);
 let publications=0;
 const artifact=createWorkspaceArtifactTool(context,{admission,
  reserve:s=>guard.reserveWorkspaceArtifact(s),unavailableReason:s=>guard.workspaceArtifactUnavailableReason(s),
  accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request:async()=>{
   publications++;return {...host,...documentReceipt};
  }});
 for(const [index,name] of ['preview','preview','preview','artifact','preview','artifact'].entries()) {
  const toolCallId='call-'+index;
  if(name==='preview') {
   const event={toolName:'pixel_ods_workspace_preview',toolCallId,params:{relativeDirectory:relativePath}};
   const scope={...context,toolName:event.toolName,toolCallId};
   const blocked=guard.beforeToolCall(event,scope);
   assert.equal(blocked.block,true);
   assert.match(blocked.blockReason,/Do not call pixel_ods_workspace_preview for documents/);
   const result={content:[{type:'text',text:blocked.blockReason}],details:{status:'blocked'}};
   guard.afterToolCall({...event,...(failure==='error' ? {result,error:blocked.blockReason} : {result:{...result,isError:true}})},scope);
   guard.toolResultPersist({toolName:event.toolName,toolCallId,message:{role:'toolResult',toolName:event.toolName,toolCallId,...result,isError:true}},scope);
  } else {
   const event={toolName:'tool_call',toolCallId,params:{id:ARTIFACT_TOOL,args:{relativePath}}};
   const scope={...context,toolName:event.toolName,toolCallId};
   const decision=guard.beforeToolCall(event,scope);
   assert.notEqual(decision?.block,true);
   admission.before(event,scope,decision);
   const result=await artifact.execute(`tool_search_code:${toolCallId}:${ARTIFACT_TOOL}:1`,{relativePath});
   assert.equal(result.isError,undefined);
   const wrapped={content:result.content,details:{tool:{id:ARTIFACT_TOOL,name:ARTIFACT_TOOL},result}};
   guard.afterToolCall({...event,result:wrapped},scope);
   guard.toolResultPersist({toolName:event.toolName,toolCallId,message:{role:'toolResult',toolName:event.toolName,toolCallId,...wrapped}},scope);
  }
 }
 assert.equal(publications,2);
 const delivery=guard.deliveryVerificationForRun(context.runId);
 assert.ok(['none','passed'].includes(delivery.status),JSON.stringify(delivery));
 assert.deepEqual(delivery.artifacts,[documentReceipt]);
 const text='Attached fleet-check.txt with the three requested lines.';
 const sent=guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text}})?.payload?.text??text;
 assert.ok(sent.includes(text),sent);
 assert.doesNotMatch(sent,/requested website files|retry the build|browser preview/i);
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
