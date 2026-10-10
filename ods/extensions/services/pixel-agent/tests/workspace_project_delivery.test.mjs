import test from 'node:test';
import assert from 'node:assert/strict';
import {ARTIFACT_TOOL, ARTIFACT_BOUNDARY, createWorkspaceArtifactAdmission, createWorkspaceArtifactTool} from '../plugin/workspace-artifact.mjs';
import {createAskUserTool} from '../plugin/ask-user.mjs';
import {mkdirSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
const {createToolLoopGuard, userMessageRequestsWorkspaceDocumentDelivery} = await import(
  process.env.ODS_PROJECT_DELIVERY_GUARD_MODULE || '../plugin/tool-loop-guard.mjs');

// Exact first installed-candidate request: the model created a ZIP successfully,
// but never called the artifact tool and ended with an empty Download heading.
const prompt = 'Create a small Python project named reading_time_v2 with a CLI that reads a CSV containing title and minutes columns, rejects negative or nonnumeric minutes with a clear error and nonzero exit, and prints total minutes and hours. Include a sample CSV with 12, 25, and 8 minutes plus unittest coverage for valid, negative, and nonnumeric input. Run the tests and give me the downloadable project with its original source filenames.';
const context = {trigger:'user',agentId:'pixel',runId:'project-run',sessionId:'project-session',
  sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};

function fixture(t,{deferred=false, request=prompt, scope=context}={}) {
  const root=mkdtempSync(path.join(tmpdir(),'ods-project-delivery-'));
  t.after(()=>{
    assert.equal(path.dirname(root),path.resolve(tmpdir()));
    assert.ok(path.basename(root).startsWith('ods-project-delivery-'));
    rmSync(root,{recursive:true,force:true});
  });
  const guard=createToolLoopGuard();
  guard.observeRun(scope,'pixel',{prompt:request},{executionHost:'sandbox',workspaceRoot:root});
  let count=0;
  function call(name,args,{exitCode=0,text='',persistScope={},persistId,details={},beforePersist}={}) {
    const toolName=deferred?'tool_call':name,toolCallId=`call-${++count}`;
    const ctx={...scope,toolName,toolCallId};
    let params=deferred?{id:`openclaw:core:${name}`,args}:args;
    const decision=guard.beforeToolCall({toolName,toolCallId,params},ctx);
    assert.notEqual(decision?.block,true,decision?.blockReason);
    params=decision?.params ?? params;
    if(name==='write') {
      const value=deferred?params.args:params, target=path.resolve(root,value.path);
      assert.ok(target.startsWith(root+path.sep));
      mkdirSync(path.dirname(target),{recursive:true});writeFileSync(target,value.content);
    }
    const result={...(exitCode?{isError:true}:{}),content:[{type:'text',text}],
      details:{status:'completed',exitCode,aggregated:text,...details}};
    const tool={id:`openclaw:core:${name}`,name,source:'openclaw',sourceName:'core'};
    const observed=deferred?{content:[{type:'text',text:JSON.stringify({tool,result})}],details:{tool,result}}:result;
    guard.afterToolCall({toolName,toolCallId,params,result:observed},ctx);
    beforePersist?.(guard);
    const persisted=guard.toolResultPersist({toolName,toolCallId:persistId??toolCallId,
      message:{role:'toolResult',toolName,toolCallId,...observed}}, {...ctx,...persistScope});
    return JSON.stringify(persisted??{});
  }
  return {guard,call};
}

test('exact live request and project synonyms require artifact delivery, without quoted/negative/remote claims',()=>{
  for(const text of [prompt,'Give me the downloadable Python project.',
    'Attach the project with original source filenames.','Deliver the source code as a download.'])
    assert.equal(userMessageRequestsWorkspaceDocumentDelivery([],text),true,text);
  for(const text of ['Create a Python project and run the tests.',
    'How can I download a project?', 'Do not deliver the project; review it.',
    'Summarize this example: "give me a downloadable project".',
    'Create a website project with a download button.',
    'Download the project from https://example.com/project.zip.',
    'Do not use tools. Give me the downloadable project.'])
    assert.equal(userMessageRequestsWorkspaceDocumentDelivery([],text),false,text);
});

for(const deferred of [false,true]) test(`passing tests and ZIP creation require verified publication (${deferred?'deferred':'native'})`,async t=>{
  const {guard,call}=fixture(t,{deferred});
  call('write',{path:'Playground/reading_time_v2/test_reading_time.py',content:'import unittest\nclass ReadingTime(unittest.TestCase):\n def test_valid(self): self.assertEqual(12+25+8,45)\n'});
  const testResult=call('exec',{command:'python3 -m unittest -v',workdir:'/workspace/Playground/reading_time_v2'},
    {text:'test_valid ... ok\n\nRan 1 test in 0.001s\n\nOK'});
  assert.equal(guard.verificationForRun(context.runId).status,'passed');
  assert.match(testResult,/pixel_ods_workspace_artifact/);
  assert.doesNotMatch(testResult,/Give the owner the concise final result now/);
  const failed=call('exec',{command:'zip -r reading_time_v2.zip reading_time_v2',workdir:'/workspace/Playground'},
    {exitCode:127,text:'sh: 1: zip: not found'});
  assert.doesNotMatch(failed,/Creating a file is not delivery/,'a failed command keeps its repair instruction');
  call('exec',{command:'python3 -m zipfile -c reading_time_v2.zip reading_time_v2',workdir:'/workspace/Playground'});
  const verification=guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status,'failed');
  assert.match(verification.text,/requested download was not attached/);
  const reply=guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:'Done. Tests passed. Download:'}});
  assert.match(reply.payload.text,/requested download was not attached/);
  assert.doesNotMatch(reply.payload.text,/Download:$/);

  let publications=0;
  const args={relativePath:'Playground/reading_time_v2.zip'};
  const receipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',...args,
    siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'reading_time_v2.zip',bytes:8809,sha256:'b'.repeat(64)}};
  const admission=createWorkspaceArtifactAdmission();
  const scope={...context,toolCallId:'publish'},event={toolName:ARTIFACT_TOOL,params:args};
  admission.before(event,scope,guard.beforeToolCall(event,scope));
  const tool=createWorkspaceArtifactTool(scope,{admission,reserve:s=>guard.reserveWorkspaceArtifact(s),
    unavailableReason:s=>guard.workspaceArtifactUnavailableReason(s),accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),
    request:async()=>{publications++;return {...receipt,status:'succeeded',httpStatus:200,readbackVerified:true,
      executable:false,overwritten:false,boundary:ARTIFACT_BOUNDARY}}});
  assert.equal((await tool.execute('publish',args)).isError,undefined);
  assert.equal(publications,1);
  const delivered=guard.deliveryVerificationForRun(context.runId);
  assert.deepEqual(delivered.artifacts,[receipt]);
  assert.doesNotMatch(delivered.text??'',/requested download was not attached/);
  const after=call('exec',{command:'ls reading_time_v2.zip',workdir:'/workspace/Playground'});
  assert.doesNotMatch(after,/Creating a file is not delivery/);
});

test('publication coaching requires a current, exactly bound successful terminal exec',t=>{
  for(const variant of ['good','wrong-run','wrong-session','wrong-call','background','failed','ended','superseded','exhausted-attempts']) {
    const {guard,call}=fixture(t);
    call('write',{path:'Playground/reading_time_v2/main.py',content:'print(45)\n'});
    if(variant==='exhausted-attempts') for(let i=0;i<4;i++) assert.equal(guard.reserveWorkspaceArtifact(context),true);
    const value=call('exec',{command:'python3 -m zipfile -t reading_time_v2.zip',workdir:'/workspace/Playground'}, {
      persistScope:variant==='wrong-run'?{runId:'foreign'}:variant==='wrong-session'?{sessionId:'foreign'}:{},
      persistId:variant==='wrong-call'?'foreign':undefined,
      exitCode:variant==='failed'?1:0,
      details:variant==='background'?{status:'running',sessionId:'background'}:{},
      beforePersist:variant==='ended'?g=>g.observeAgentEnd({},context):variant==='superseded'
        ?g=>g.observeRun({...context,runId:'replacement'},'pixel',{prompt:'Thanks.'}):undefined,
    });
    if(variant==='good') assert.match(value,/Creating a file is not delivery/);
    else assert.doesNotMatch(value,/Creating a file is not delivery/,variant);
  }
});

test('passing verification cannot substitute for a missing download receipt',t=>{
  // Existing file language was already recognized before the project fix.
  // This isolates delivery truth from both the classifier and tool coaching.
  const {guard,call}=fixture(t,{request:'Run the tests and attach the existing project.zip file.'});
  call('exec',{command:'python3 -m unittest -v',workdir:'/workspace'},
    {text:'test_valid ... ok\n\nRan 1 test in 0.001s\n\nOK'});
  assert.equal(guard.verificationForRun(context.runId).status,'passed');
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
  assert.match(guard.deliveryVerificationForRun(context.runId).text,/requested download was not attached/);
});

test('owner cancellation retains its own terminal cause instead of a missing-download verdict',async t=>{
  const {guard}=fixture(t);
  await guard.abortUserRun('ods-'+'a'.repeat(64));
  assert.doesNotMatch(guard.deliveryVerificationForRun(context.runId).text??'',/requested download was not attached/);
});

test('pending owner question remains pending before requested project delivery',async t=>{
  const {guard}=fixture(t,{request:'Ask me which Python version before creating the downloadable project.'});
  const questions=[{id:'version',question:'Which Python version?',options:['3.11','3.12']}];
  const result=await createAskUserTool().execute('ask',{questions});
  guard.afterToolCall({toolName:'pixel_ods_ask_user',result},{...context,toolCallId:'ask'});
  const verification=guard.deliveryVerificationForRun(context.runId);
  assert.equal(verification.status,'pending');
  assert.deepEqual(verification.questions,questions);
  assert.doesNotMatch(verification.text,/requested download was not attached/);
});

test('missing attachment does not manufacture delivery obligations for source-only/background turns',t=>{
  for(const options of [{request:'Create a Python project and run the tests.'},
    {scope:{...context,trigger:'cron'}}, {scope:{...context,sessionKey:'agent:pixel:subagent:worker'}}]) {
    const {guard}=fixture(t,options);
    assert.doesNotMatch(guard.deliveryVerificationForRun(context.runId).text??'',/requested download was not attached/);
  }
});
