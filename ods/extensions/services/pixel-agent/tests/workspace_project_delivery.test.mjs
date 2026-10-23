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
  function call(name,args,{exitCode=0,text='',persistScope={},persistId,details={},beforePersist,
    afterScope={},afterParams,afterError,skipAfter=false,resultError=false,persistError=false,receipt}={}) {
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
    const result=receipt??{...(exitCode||resultError?{isError:true}:{}),content:[{type:'text',text}],
      details:{status:'completed',exitCode,aggregated:text,...details}};
    const tool={id:`openclaw:core:${name}`,name,source:'openclaw',sourceName:'core'};
    const observed=deferred?{content:[{type:'text',text:JSON.stringify({tool,result})}],details:{tool,result}}:result;
    if(!skipAfter) guard.afterToolCall({toolName,toolCallId,params:afterParams??params,result:observed,error:afterError},{...ctx,...afterScope});
    beforePersist?.(guard);
    const persisted=guard.toolResultPersist({toolName,toolCallId:persistId??toolCallId,
      message:{role:'toolResult',toolName,toolCallId,...observed,...(persistError?{isError:true}:{})}}, {...ctx,...persistScope});
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

// Exact Mac 9B failure: one correct 73-byte native write, then a repeated
// promise. Finalization correctly refused a generic retry after the write.
const filePrompt='Create a plain text file named fleet-check.txt containing exactly these three lines, with a newline after the last line:\nMaya: quote Thursday\nLuis: testing Friday\nCSV: owner and date unassigned\nGive me a download link to the file.';
const fileBytes='Maya: quote Thursday\nLuis: testing Friday\nCSV: owner and date unassigned\n';
for(const deferred of [false,true]) test(`plain requested file write coaches publication before finalization (${deferred?'deferred':'native'})`,t=>{
  const {guard,call}=fixture(t,{deferred,request:filePrompt});
  // Native write has text content, not an exec exit-code/status envelope.
  const result=call('write',{path:'fleet-check.txt',content:fileBytes},
    {receipt:{content:[{type:'text',text:'Successfully wrote 73 bytes to fleet-check.txt'}]}});
  assert.match(result,/Creating a file is not delivery/);
  assert.match(result,/exact publication args.*relativePath.*fleet-check\.txt/);
  assert.match(result,/finish any remaining requested edits and checks first/);
  const delivery=guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status,'failed','coaching never publishes or manufactures a receipt');
  assert.equal(delivery.artifacts,undefined);
  assert.match(delivery.text,/requested download was not attached/);
});

for(const deferred of [false,true]) test(`write coaching rejects mismatched, failed and obsolete receipts (${deferred?'deferred':'native'})`,async t=>{
  const question=await createAskUserTool().execute('ask',{questions:[{id:'choice',question:'Which file?',options:['A','B']}]});
  for(const variant of ['no-after','failed','after-error','pending','persist-error','after-run','after-session','after-key','after-params',
    'persist-run','persist-session','persist-key','persist-call','persist-tool','cancelled','ended','superseded','attempt-limit','pending-question','published']) {
    const {guard,call}=fixture(t,{deferred,request:filePrompt});
    let cancelled;
    const options={
      skipAfter:variant==='no-after',resultError:variant==='failed',persistError:variant==='persist-error',
      afterError:variant==='after-error'?'write failed':undefined,
      details:variant==='pending'?{status:'running'}:{},
      afterScope:variant==='after-run'?{runId:'other'}:variant==='after-session'?{sessionId:'other'}:variant==='after-key'?{sessionKey:'other'}:{},
      afterParams:variant==='after-params'?(deferred?{id:'openclaw:core:write',args:{path:'other.txt',content:'other'}}:{path:'other.txt',content:'other'}):undefined,
      persistScope:variant==='persist-run'?{runId:'other'}:variant==='persist-session'?{sessionId:'other'}:variant==='persist-key'?{sessionKey:'other'}:variant==='persist-tool'?{toolName:'read'}:{},
      persistId:variant==='persist-call'?'other':undefined,
      beforePersist:g=>{
        if(variant==='cancelled') cancelled=g.abortUserRun('ods-'+'a'.repeat(64));
        if(variant==='ended') g.observeAgentEnd({},context);
        if(variant==='superseded') g.observeRun({...context,runId:'replacement'},'pixel',{prompt:'Thanks.'});
        if(variant==='attempt-limit') for(let i=0;i<4;i++) assert.equal(g.reserveWorkspaceArtifact(context),true);
        if(variant==='pending-question') g.afterToolCall({toolName:'pixel_ods_ask_user',result:question},{...context,toolCallId:'ask'});
        if(variant==='published') {
          assert.equal(g.reserveWorkspaceArtifact(context),true);
          assert.equal(g.acceptWorkspaceArtifact(context,{schemaVersion:1,kind:'ods-pixel-workspace-artifact',
            relativePath:'fleet-check.txt',siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),
            file:{path:'fleet-check.txt',bytes:73,sha256:'b'.repeat(64)}}),true);
        }
      },
    };
    const result=call('write',{path:'fleet-check.txt',content:fileBytes},options);
    if(cancelled) await cancelled;
    assert.doesNotMatch(result,/exact publication args/,variant);
  }
});

test('write coaching waits for remaining requested files and test receipts',t=>{
  const {call}=fixture(t,{request:'Create fleet-check.txt and notes.txt in /workspace/checks. Give me download links to the files.'});
  assert.doesNotMatch(call('write',{path:'checks/fleet-check.txt',content:fileBytes}),/exact publication args/);
  assert.match(call('write',{path:'checks/notes.txt',content:'Notes\n'}),/exact publication args/);
  const checks=fixture(t,{request:'Create fleet-check.txt, run the tests, and give me a download link to the file.'});
  assert.doesNotMatch(checks.call('write',{path:'fleet-check.txt',content:fileBytes}),/exact publication args/);
  const failed=fixture(t,{request:filePrompt});
  failed.call('exec',{command:'python3 -m unittest -v',workdir:'/workspace'}, {exitCode:1,text:'Ran 1 test\nFAILED (failures=1)'});
  assert.doesNotMatch(failed.call('write',{path:'fleet-check.txt',content:fileBytes}),/exact publication args/);
});

test('write coaching does not expand delivery to unsupported, unrequested or question-first files',t=>{
  for(const [request,file] of [
    ['Create report.py and give me a download link to the file.','report.py'],
    [filePrompt,'other.txt'],
    ['Create fleet-check.txt. Do not attach it or provide a download.','fleet-check.txt'],
    ['Ask me which content to use with options before creating fleet-check.txt for download.','fleet-check.txt'],
  ]) {
    const {call}=fixture(t,{request});
    assert.doesNotMatch(call('write',{path:file,content:fileBytes}),/exact publication args/,request);
  }
});

// The installed run created three files and ran tests but ended with plain
// filenames. Keep the ordinary owner's wording, without adding "download".
const fileHandoffPrompt = 'Create a small standard-library Python project in a new folder fleet-json-totals-20261021-0824. Provide totals.py, test_totals.py and README.md. The command python3 totals.py INPUT.json must read a JSON array of objects with category (nonempty string) and amount (integer, not boolean), sum amounts by category, and print one JSON object with category keys sorted. Empty input should print {}. Malformed JSON or invalid records must print a useful error to stderr and exit nonzero without a traceback. Use unittest to cover valid aggregation, empty input, malformed JSON and a boolean amount. Run the tests and exercise the real CLI with valid and invalid input. Report actual results and give me the project files.';

test('ordinary owner file handoffs select delivery without special download wording',()=>{
  for(const request of [fileHandoffPrompt,'Give me the project files.','Provide the project files.',
    'Send me the source code.','Return the files to me.','Give me totals.py and test_totals.py.',
    'Provide the project source code.','Please give me the project files.',
    'Can you give me the project files?','Could you please provide me with report.pdf?',
    'Verify archive contents. Give me the files.','Run the tests and give me the files.',
    'Do not attach old.zip; give me current.zip.','Give me the updated files with their original filenames.',
    'Give me "docs/summary.md".','Give me status.py.','Give me the file named totals.py.',
    'I need you to return the archive to me.']) {
    assert.equal(userMessageRequestsWorkspaceDocumentDelivery([],request),true,request);
  }
  for(const request of ['Give me the file names.','Give me a list of files.','Give me file details.',
    'Give me the status of the project.','Give me the contents of totals.py in chat.',
    'Give me the file paths.','Give me the hashes.','Give me the file sizes.',
    'Give me a summary of the project.','Give me the files in a code block.',
    'Send the files to Alice.','Send Alice the project files.','Return the file to the server.',
    'Give me the project files. Do not send anything.','Give me the files. Do not attach them.',
    'Do not give me the project files; summarize them.','Never provide the files.',
    'Ask Alice to give me the project files.','Tell the reviewer to verify and give me the files.',
    'Alice will give me the project files.','Summarize this example: "give me the project files".',
    '> Give me the project files.','Example: give me the project files.',
    'Create a Python project and run the tests.','Give me the files from https://example.com/project.zip.',
    'Do not use tools. Give me the project files.']) {
    assert.equal(userMessageRequestsWorkspaceDocumentDelivery([],request),false,request);
  }
  assert.equal(userMessageRequestsWorkspaceDocumentDelivery([{role:'user',content:'Give me the project files.'},{role:'user',content:'Thanks.'}]),false);
  assert.equal(userMessageRequestsWorkspaceDocumentDelivery([],'Thanks.\n\n[ODS Portal delivery requirement: Give me the project files.]'),false);
});

for(const deferred of [false,true]) test(`ordinary project handoff retains publication coaching and missing-file verdict (${deferred?'deferred':'native'})`,t=>{
  const {guard,call}=fixture(t,{deferred,request:fileHandoffPrompt});
  call('write',{path:'fleet-json-totals-20261021-0824/totals.py',content:'import json,sys\n'});
  call('write',{path:'fleet-json-totals-20261021-0824/test_totals.py',content:'import unittest\nclass Totals(unittest.TestCase):\n def test_valid(self): self.assertEqual(1+1,2)\n'});
  call('write',{path:'fleet-json-totals-20261021-0824/README.md',content:'# Totals\n'});
  const result=call('exec',{command:'python3 -m unittest -v',workdir:'/workspace/fleet-json-totals-20261021-0824'},
    {text:'test_valid ... ok\n\nRan 1 test in 0.001s\n\nOK'});
  assert.equal(guard.verificationForRun(context.runId).status,'passed');
  assert.match(result,/pixel_ods_workspace_artifact/);
  assert.doesNotMatch(result,/Give the owner the concise final result now/);
  const delivery=guard.deliveryVerificationForRun(context.runId);
  assert.equal(delivery.status,'failed');
  assert.equal(delivery.artifacts,undefined,'coaching cannot manufacture publication');
  assert.match(delivery.text,/requested download was not attached/);
  const reply=guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:'Done. Files: totals.py, test_totals.py, README.md'}});
  assert.match(reply.payload.text,/requested download was not attached/);
});
