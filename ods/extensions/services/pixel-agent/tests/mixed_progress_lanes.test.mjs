import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard,userMessageRequestsWorkspacePreview} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

const prompt='/extensions install https://github.com/example/project; create /workspace/report.md with the findings.';
const context={agentId:'pixel',runId:'mixed-run',sessionId:'mixed-session',
  sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};
const status={details:{schemaVersion:1,kind:'ods-extension-request-status',chatId:'chat',requestId:'request',
  authorizationMode:'install',requestState:'pending',proposalAccepted:false,integrationBound:false,
  prepared:false,extensionId:null,runtimeStatus:'not_observed',existingExtensionIds:[]}};
function event(name,params,result,wrapped,callId) {
  const sourceName=['read','write','edit','exec','process'].includes(name)?'core'
    :name.startsWith('pixel_ops_')?'pixel-operations-broker':'pixel-ods';
  const id=`openclaw:${sourceName}:${name}`;
  return {toolCallId:callId,...(wrapped?{toolName:'tool_call',params:{id,args:params},
    result:result?{details:{tool:{id,name,source:'openclaw',sourceName},result}}:undefined}
    :{toolName:name,params,result})};
}
function fixture(wrapped=false,options={}) {
  const guard=createToolLoopGuard(options);
  guard.observeRun(context,'pixel',{prompt},{executionHost:'sandbox'});
  const call=(name,params={},id=`call-${name}`)=>guard.beforeToolCall(event(name,params,undefined,wrapped,id),context);
  const after=(name,params,result,id)=>guard.afterToolCall(event(name,params,result,wrapped,id),context);
  return {guard,call,after};
}

function previewReceipt(relativeDirectory,content) {
  // Unit receipt fixture for the publisher digest contract, not a live proof.
  const entry=Buffer.from('index.html'),bytes=Buffer.from(content);
  const pathLength=Buffer.alloc(4),contentLength=Buffer.alloc(8);
  pathLength.writeUInt32BE(entry.length);contentLength.writeBigUInt64BE(BigInt(bytes.length));
  const sha256=createHash('sha256').update(pathLength).update(entry).update(contentLength).update(bytes).digest('hex');
  const siteId=`site-${sha256.slice(0,24)}`;
  return {details:{schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory,
    files:1,bytes:bytes.length,sha256,siteId,entryFile:'index.html',entrySha256:createHash('sha256').update(bytes).digest('hex'),
    port:9437,url:`http://${siteId}.localhost:9437/${siteId}/`,httpStatus:200,readbackVerified:true,executable:false,overwritten:false}};
}

const inventory={details:{schemaVersion:2,generatedAt:'2026-09-23T00:00:00Z',policySha256:'a'.repeat(64),
  authority:{defaultLevel:'propose',standingGrantIds:[],paused:false,activeLeaseIds:[]},
  targets:[{id:'ods-host',backend:'local',capabilities:['inspect','manage-extensions']}],
  actions:[{id:'ods.extensions.install',tier:'managed',effect:'manage',defaultAuthority:'propose',
    targets:['ods-host'],parameters:['serviceId']}]}};

for(const wrapped of [false,true]) test(`extension failures leave authorized workspace work available (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  for(let i=0;i<4;i++) {
    guard.observeModelCall({},context);
    assert.notEqual(call('pixel_ods_extension_request_prepare',{},`prepare-${i}`)?.block,true);
    after('pixel_ods_extension_request_prepare',{},
      {isError:true,content:[{type:'text',text:'Repository evidence unavailable'}]},`prepare-${i}`);
    const persisted=guard.toolResultPersist({toolCallId:`prepare-${i}`,message:{toolName:wrapped?'tool_call':'pixel_ods_extension_request_prepare',
      isError:true,content:[{type:'text',text:'Repository evidence unavailable'}]}},context);
    if(i===3) assert.match(JSON.stringify(persisted?.message?.content),/extension portion.*stopped/);
    after('pixel_ods_extension_request_status',{},status,`status-${i}`);
  }
  assert.match(call('pixel_ods_extension_request_prepare')?.blockReason??'',/extension portion.*stopped/);
  assert.match(call('pixel_ods_source_proposal')?.blockReason??'',/extension portion.*stopped/);
  const write={path:'report.md',content:'Partial findings; extension installation is incomplete.'};
  assert.notEqual(call('write',write)?.block,true);
  after('write',write,{details:{status:'completed'}},'written-report');
  assert.notEqual(call('read',{path:'report.md'})?.block,true);
  assert.doesNotMatch(guard.beforeAgentFinalize({lastAssistantMessage:'Report saved. Extension incomplete.'},context)?.retry?.instruction??'',
    /extension_request_prepare|extension_request_advance|library_proposal|source_proposal/);
  const verification=guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status,'failed','sibling success cannot hide the stopped extension requirement');
  assert.match(verification.text,/extension portion.*stopped/);
});

for(const wrapped of [false,true]) test(`workspace failures leave managed extension work available (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  for(let i=0;i<4;i++) after('read',{path:`missing-${i}.md`},{isError:true},`read-${i}`);
  assert.match(call('write',{path:'report.md',content:'Unverified'})?.blockReason??'',/workspace portion.*stopped/);
  assert.notEqual(call('pixel_ods_extension_request_status')?.block,true);
  assert.notEqual(call('pixel_ods_extension_request_prepare')?.block,true);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
});

test('unattributable native wrapper failures retain the strict whole-run fuse',()=>{
  const {guard,call}=fixture(true);
  for(let i=0;i<4;i++) guard.toolResultPersist({toolCallId:`unknown-${i}`,message:{toolName:'tool_call',isError:true,
    content:[{type:'text',text:'Malformed dispatch'}]}},context);
  assert.equal(call('write',{path:'report.md',content:'findings'})?.blockReason,RUN_PROGRESS_STOP_REASON);
});

for(const persistOnly of [false,true]) test(`wrong-source wrapped failures cannot acquire a larger lane budget (persistOnly=${persistOnly})`,()=>{
  const {guard,call}=fixture(true);
  for(let i=0;i<4;i++) {
    const malformed={toolName:'tool_call',toolCallId:`bad-${i}`,
      params:{id:'unknown:pixel_ods_extension_request_prepare',args:{}}};
    guard.beforeToolCall(malformed,context);
    if(!persistOnly) guard.afterToolCall({...malformed,result:{isError:true}},context);
    guard.toolResultPersist({toolCallId:`bad-${i}`,message:{toolName:'tool_call',isError:true}},context);
  }
  assert.equal(call('write',{path:'report.md',content:'findings'})?.blockReason,RUN_PROGRESS_STOP_REASON);
});

test('pure extension work retains the original whole-run failure behavior',()=>{
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'/extensions install https://github.com/example/project'},{executionHost:'sandbox'});
  for(let i=0;i<4;i++) guard.afterToolCall(event('pixel_ods_extension_request_prepare',{}, {isError:true},false,`error-${i}`),context);
  assert.equal(guard.beforeToolCall(event('read',{path:'report.md'},undefined,false,'read'),context)?.blockReason,RUN_PROGRESS_STOP_REASON);
});

test('lane containment preserves owner cancellation and does not abort at the local threshold',async()=>{
  const aborted=[],signalled=[];
  const {guard,after}=fixture(false,{abortRun:(...args)=>{aborted.push(args);return true;},
    execControl:{signal:id=>{signalled.push(id);return true;}}});
  for(let i=0;i<4;i++) after('pixel_ods_extension_request_prepare',{}, {isError:true},`prepare-${i}`);
  guard.observeModelEnd({},context);
  assert.deepEqual(aborted,[]);
  assert.deepEqual(signalled,[]);
  assert.equal(await guard.abortUserRun('ods-'+'a'.repeat(64)),true);
  assert.equal(aborted.length,1);
  assert.deepEqual(signalled,[context.runId]);
});

for(const wrapped of [false,true]) test(`local extension failure retains a genuine workspace publication without passing the full request (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  guard.observeRun(context,'pixel',{prompt:'/extensions install https://github.com/example/project; create /workspace/report/index.html and show its preview.'},
    {executionHost:'sandbox'});
  for(let i=0;i<4;i++) after('pixel_ods_extension_request_prepare',{}, {isError:true},`prepare-${i}`);
  const file={path:'report/index.html',content:'<!doctype html><title>Findings</title><p>Installation incomplete</p>'};
  assert.notEqual(call('write',file)?.block,true);
  after('write',file,{details:{status:'completed'}},'write');
  const params={relativeDirectory:'report'},receipt=previewReceipt('report',file.content);
  assert.notEqual(call('pixel_ods_workspace_preview',params)?.block,true);
  after('pixel_ods_workspace_preview',params,receipt,'preview');
  const verification=guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status,'failed');
  assert.equal(verification.preview?.url,receipt.details.url);
  assert.match(verification.text,/extension portion.*stopped/);
});

for(const wrapped of [false,true]) test(`stopped workspace may only poll its already accepted process (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  const exec={command:'python3 -m unittest -v',workdir:'/workspace/report',background:true};
  assert.notEqual(call('exec',exec,'exec')?.block,true);
  after('exec',exec,{details:{status:'running',sessionId:'accepted-process'}},'exec');
  for(let i=0;i<4;i++) after('read',{path:`missing-${i}.md`},{isError:true},`read-${i}`);
  assert.notEqual(call('process',{action:'poll',sessionId:'accepted-process'})?.block,true);
  assert.match(call('process',{action:'poll',sessionId:'foreign-process'})?.blockReason??'',/workspace portion.*stopped/);
  assert.match(call('process',{action:'write',sessionId:'accepted-process',data:'run more'})?.blockReason??'',/workspace portion.*stopped/);
  after('process',{action:'poll',sessionId:'accepted-process'},
    {details:{status:'running',sessionId:'accepted-process'}},'pending-process');
  assert.match(call('write',{path:'report.md',content:'Premature'})?.blockReason??'',/workspace portion.*stopped/,
    'a verified pending callback cannot reopen its stopped lane');
  after('process',{action:'poll',sessionId:'accepted-process'},
    {details:{status:'completed',sessionId:'accepted-process',exitCode:0}},'finished-process');
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
  assert.match(call('write',{path:'report.md',content:'Done'})?.blockReason??'',/workspace portion.*stopped/);
});

for(const wrapped of [false,true]) test(`recognized preview-only continuation cannot pass on Operations or extension metadata (wrapped=${wrapped})`,()=>{
  const guard=createToolLoopGuard();
  const initial={...context,runId:'initial'},followup={...context,runId:'followup'};
  const file={path:'Playground/mac-preview-1790153380/index.html',content:'<!doctype html><title>Hi</title>'};
  guard.observeRun(initial,'pixel',{prompt:'Build a website in /workspace/Playground/mac-preview-1790153380/index.html and show its preview.'});
  guard.afterToolCall(event('write',file,{details:{status:'completed'}},wrapped,'initial-write'),initial);
  const continuation='The existing file Playground/mac-preview-1790153380/index.html is already written. Now call pixel_ods_workspace_preview on Playground/mac-preview-1790153380 and report its verified URL. Do not edit files, run shell commands, install extensions, or contact external sites.';
  assert.equal(userMessageRequestsWorkspacePreview([],continuation),true,'this test isolates delivery, not unknown intent');
  guard.observeRun(followup,'pixel',{prompt:continuation});
  for(const [name,result] of [['pixel_ops_inventory',inventory],['pixel_ods_extensions',{details:{status:'succeeded'}}],
    ['pixel_ods_extension_request_status',{details:{...status.details,proposalAccepted:true,prepared:true,
      extensionId:'example',runtimeStatus:'cli_installed'}}]]) {
    guard.afterToolCall(event(name,{},result,wrapped,`metadata-${name}`),followup);
    const verification=guard.deliveryVerificationForRun(followup.runId);
    assert.equal(verification.status,'failed',`${name} cannot satisfy requested preview`);
    assert.equal(verification.preview,undefined);
    assert.doesNotMatch(verification.text,/managed installation readiness|exact Pixel Operations capability inventory/);
  }
});

for(const wrapped of [false,true]) test(`mixed metadata successes cannot extend the global no-progress allowance (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  for(let i=0;i<9;i++) {
    guard.observeModelCall({},context);
    after('pixel_ops_inventory',{},inventory,`inventory-${i}`);
    after('pixel_ods_extension_request_status',{},status,`status-${i}`);
  }
  assert.equal(call('write',{path:'report.md',content:'findings'})?.blockReason,RUN_PROGRESS_STOP_REASON);
});

test('finalization and persisted metadata do not coach a suspended workspace lane',()=>{
  const {guard,after}=fixture();
  guard.observeRun(context,'pixel',{prompt:'/extensions install https://github.com/example/project; build /workspace/report/index.html and show its preview.'},
    {executionHost:'sandbox'});
  for(let i=0;i<4;i++) after('read',{path:`report/missing-${i}.html`},{isError:true},`read-${i}`);
  after('pixel_ods_extension_request_status',{}, {details:{...status.details,proposalAccepted:true,prepared:true,
    extensionId:'example',runtimeStatus:'cli_installed'}},'extension-ready');
  const final=guard.beforeAgentFinalize({lastAssistantMessage:'Installation ready; report incomplete.'},context);
  assert.doesNotMatch(final?.retry?.instruction??'',/workspace_preview|browser-ready|id write|id read/);
  const persisted=guard.toolResultPersist({toolCallId:'extension-ready',message:{toolName:'pixel_ods_extension_request_status',
    content:[{type:'text',text:'Extension ready'}]}},context);
  assert.doesNotMatch(JSON.stringify(persisted??{}),/workspace_preview|Finish all requested files|Publish last/);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
});

for(const wrapped of [false,true]) test(`a promise cannot reopen suspended installation after workspace completion (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  for(let i=0;i<4;i++) after('pixel_ods_extension_request_prepare',{}, {isError:true},`prepare-${i}`);
  const file={path:'report.md',content:'Findings saved. Extension installation failed.'};
  assert.notEqual(call('write',file)?.block,true);
  after('write',file,{details:{status:'completed'}},'write-report');
  after('read',{path:file.path},{content:[{type:'text',text:file.content}]},'read-report');
  const final=guard.beforeAgentFinalize({lastAssistantMessage:'The report is saved. I will install it now.'},context);
  assert.equal(final?.retry,undefined,'generic completion recovery must not solicit the stopped installation');
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
  assert.match(call('pixel_ods_extension_request_prepare')?.blockReason??'',/extension portion.*stopped/);
});

for(const wrapped of [false,true]) test(`stopped installation still permits the exact remaining workspace publication (wrapped=${wrapped})`,()=>{
  const {guard,call,after}=fixture(wrapped);
  guard.observeRun(context,'pixel',{prompt:'/extensions install https://github.com/example/project; create /workspace/report/index.html and show its preview.'},
    {executionHost:'sandbox'});
  for(let i=0;i<4;i++) after('pixel_ods_extension_request_prepare',{}, {isError:true},`prepare-${i}`);
  const file={path:'report/index.html',content:'<!doctype html><title>Report</title>'};
  assert.notEqual(call('write',file)?.block,true);
  after('write',file,{details:{status:'completed'}},'write-report');
  const before=guard.beforeAgentFinalize({lastAssistantMessage:'I will install it now.'},context);
  assert.match(before?.retry?.instruction??'',/id pixel_ods_workspace_preview/);
  assert.match(before?.retry?.instruction??'',/"relativeDirectory":"report"/);
  assert.doesNotMatch(before?.retry?.instruction??'',/Continue the actual owner-requested task|extension_request/);
  after('pixel_ods_workspace_preview',{relativeDirectory:'report'},previewReceipt('report',file.content),'publish-report');
  const afterPublication=guard.beforeAgentFinalize({lastAssistantMessage:'I will install it now.'},context);
  assert.equal(afterPublication?.retry,undefined);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
});
