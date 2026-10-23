import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ARTIFACT_TOOL, ARTIFACT_BOUNDARY, ARCHIVE_BOUNDARY,
  normalizeWorkspaceArtifact, artifactReceipt, validDeliveredArtifact,
  createWorkspaceArtifactAdmission, createWorkspaceArtifactTool,
} from '../plugin/workspace-artifact.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const context={trigger:'user',agentId:'pixel',runId:'run',sessionId:'session',
  sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),toolCallId:'artifact'};
const directArgs={relativePath:'project/report.md'};
const archiveArgs={relativePath:'project.zip',sourcePath:'project'};
const directPayload=normalizeWorkspaceArtifact(directArgs);
const archivePayload=normalizeWorkspaceArtifact(archiveArgs);
const baseReceipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',
  relativePath:'project/report.md',siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),
  file:{path:'report.md',bytes:10,sha256:'b'.repeat(64)}};
const directHost={...baseReceipt,status:'succeeded',httpStatus:200,readbackVerified:true,
  executable:false,overwritten:false,boundary:ARTIFACT_BOUNDARY};
const archiveReceipt={...baseReceipt,relativePath:'project.zip',
  file:{path:'project.zip',bytes:100,sha256:'c'.repeat(64)}};
const archiveHost={...archiveReceipt,status:'succeeded',httpStatus:200,readbackVerified:true,
  executable:false,overwritten:false,boundary:ARCHIVE_BOUNDARY,
  archive:{sourcePath:'project',fileCount:3,sourceBytes:90,omittedCacheEntries:2}};

function fixture(request=async()=>directHost,args=directArgs) {
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'Deliver the report as a document.'});
  const admission=createWorkspaceArtifactAdmission();
  const event={toolName:ARTIFACT_TOOL,params:args};
  const tool=createWorkspaceArtifactTool(context,{admission,
    reserve:s=>guard.reserveWorkspaceArtifact(s),
    unavailableReason:s=>guard.workspaceArtifactUnavailableReason(s),
    accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request});
  return {guard,admission,event,tool};
}

test('normalize accepts optional sourcePath only with .zip and rejects bad shapes',()=>{
  assert.deepEqual(normalizeWorkspaceArtifact(directArgs),
    {schemaVersion:1,action:'publish-artifact',relativePath:'project/report.md'});
  assert.deepEqual(normalizeWorkspaceArtifact(archiveArgs),
    {schemaVersion:1,action:'publish-artifact',relativePath:'project.zip',sourcePath:'project'});
  for(const bad of [
    {relativePath:'project/report.md',sourcePath:'project'},
    {relativePath:'project.zip',sourcePath:'../project'},
    {relativePath:'project.zip',sourcePath:'/abs'},
    {relativePath:'project.zip',sourcePath:''},
    {relativePath:'project.zip',sourcePath:'project',extra:1},
    {relativePath:'project/report.md',extra:1},
    {relativePath:'project.zip',sourcePath:'project',url:'http://x'},
  ]) assert.throws(()=>normalizeWorkspaceArtifact(bad),/invalid-artifact-path/);
});

test('archive receipt requires exact host shape and strips internal metadata',()=>{
  const stripped=artifactReceipt(archiveHost,archivePayload);
  assert.deepEqual(stripped,archiveReceipt);
  assert.equal(Object.hasOwn(stripped,'archive'),false);
  assert.equal(Object.hasOwn(stripped,'boundary'),false);
  assert.equal(validDeliveredArtifact(stripped),true);
  const bad=[
    {...archiveHost,archive:{...archiveHost.archive,sourcePath:'other'}},
    {...archiveHost,archive:{...archiveHost.archive,fileCount:0}},
    {...archiveHost,archive:{...archiveHost.archive,fileCount:129}},
    {...archiveHost,archive:{...archiveHost.archive,fileCount:1.5}},
    {...archiveHost,archive:{...archiveHost.archive,sourceBytes:NaN}},
    {...archiveHost,archive:{...archiveHost.archive,sourceBytes:-1}},
    {...archiveHost,archive:{...archiveHost.archive,sourceBytes:16*1024*1024+1}},
    {...archiveHost,archive:{...archiveHost.archive,omittedCacheEntries:-1}},
    {...archiveHost,archive:{...archiveHost.archive,omittedCacheEntries:4097}},
    {...archiveHost,archive:{...archiveHost.archive,extra:1}},
    {...archiveHost,archive:undefined},
    {...archiveHost,boundary:ARTIFACT_BOUNDARY},
    {...archiveHost,relativePath:'project/report.md'},
  ];
  for(const value of bad) assert.throws(()=>artifactReceipt(value,archivePayload),/unverified-artifact/);
  assert.throws(()=>artifactReceipt(directHost,archivePayload),/unverified-artifact/);
  assert.throws(()=>artifactReceipt(archiveHost,directPayload),/unverified-artifact/);
});

test('direct ZIP without sourcePath remains allowed and exact',()=>{
  const zipArgs={relativePath:'project.zip'};
  const zipPayload=normalizeWorkspaceArtifact(zipArgs);
  const zipReceipt={...baseReceipt,relativePath:'project.zip',
    file:{path:'project.zip',bytes:100,sha256:'c'.repeat(64)}};
  const zipHost={...zipReceipt,status:'succeeded',httpStatus:200,readbackVerified:true,
    executable:false,overwritten:false,boundary:ARTIFACT_BOUNDARY};
  assert.deepEqual(artifactReceipt(zipHost,zipPayload),zipReceipt);
  assert.throws(()=>artifactReceipt({...zipHost,archive:{sourcePath:'x',fileCount:1,
    sourceBytes:1,omittedCacheEntries:0}},zipPayload),/unverified-artifact/);
});

test('admission binds archive args and rejects sourcePath tampering',()=>{
  const admission=createWorkspaceArtifactAdmission();
  const event={toolName:ARTIFACT_TOOL,params:archiveArgs};
  admission.before(event,context);
  assert.throws(()=>admission.take(context.toolCallId,
    {...archivePayload,sourcePath:'other'},context),/artifact-call-unbound/);
  assert.throws(()=>admission.take(context.toolCallId,archivePayload,
    {...context,sessionKey:'foreign'}),/artifact-call-unbound/);
  assert.deepEqual(admission.take(context.toolCallId,archivePayload,context),context);
  assert.throws(()=>admission.take(context.toolCallId,archivePayload,context));
});

test('deferred archive admission rejects wrong session and sourcePath tampering',()=>{
  const admission=createWorkspaceArtifactAdmission();
  const event={toolName:'tool_call',params:{id:'openclaw:pixel-ods:'+ARTIFACT_TOOL,args:archiveArgs}};
  const id=`tool_search_code:artifact:${ARTIFACT_TOOL}:1`;
  admission.before(event,context);
  assert.throws(()=>admission.take(id,{...archivePayload,sourcePath:'other'},context));
  assert.throws(()=>admission.take(id,archivePayload,{...context,sessionId:'foreign'}));
  assert.deepEqual(admission.take(id,archivePayload,context),context);
});

test('actual tool accepts archive and reports counts, strips internal metadata',async()=>{
  const f=fixture(async()=>archiveHost,archiveArgs);
  f.admission.before(f.event,context);
  const result=await f.tool.execute(context.toolCallId,archiveArgs);
  assert.equal(result.isError,undefined);
  assert.deepEqual(result.details,archiveReceipt);
  assert.match(result.content[0].text,/ZIP contains 3 source files \(90 bytes\)/);
  assert.match(result.content[0].text,/2 generated cache or dependency entries were omitted/);
  assert.match(result.content[0].text,new RegExp(ARCHIVE_BOUNDARY.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  assert.deepEqual(f.guard.deliveryVerificationForRun('run').artifacts,[archiveReceipt]);
});

test('aborted, stale and Stop runs do not attach archive receipts',async()=>{
  for(const mode of ['signal','superseded','stop']) {
    let release;const f=fixture(()=>new Promise(r=>release=r),archiveArgs);
    const signal=new AbortController();
    f.admission.before(f.event,context);
    const running=f.tool.execute(context.toolCallId,archiveArgs,signal.signal);
    if(mode==='signal') signal.abort();
    else if(mode==='stop') await f.guard.abortUserRun('ods-'+'a'.repeat(64));
    else f.guard.observeRun({...context,runId:'next'},'pixel',{prompt:'new'});
    release(archiveHost);
    assert.equal((await running).isError,true);
    assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
  }
});

test('four failed archive receipts consume the publication budget',async()=>{
  let calls=0;
  const f=fixture(async()=>{calls++;return {...archiveHost,readbackVerified:false}},archiveArgs);
  for(let i=0;i<6;i++){
    f.admission.before(f.event,context);
    assert.equal((await f.tool.execute(context.toolCallId,archiveArgs)).isError,true);
  }
  assert.equal(calls,4);
  assert.equal(f.guard.deliveryVerificationForRun('run').artifacts,undefined);
});
