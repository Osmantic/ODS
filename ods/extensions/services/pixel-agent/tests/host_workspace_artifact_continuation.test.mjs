import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

const prompts = [
  ['host-receipt-7833.txt', 'Read the actual hostname of the ODS host. Then create a workspace text file named host-receipt-7833.txt containing exactly that hostname followed by one newline, and give me a working download link to the file.'],
  ['host-readback-7833.txt', 'Read the actual hostname of the ODS host. Save exactly that hostname and one newline to a workspace text file named host-readback-7833.txt. Read the saved file back, verify its contents, and give me a working download link.'],
];
const user = `ods-${'a'.repeat(64)}`;
const ctx = {trigger:'user', agentId:'pixel', runId:'artifact-composition', sessionId:'artifact-session',
  sessionKey:`agent:pixel:openai-user:${user}`};
function fixture(prompt) {
  const guard = api.createToolLoopGuard({abortRunAndDrain:async()=>({aborted:true,drained:true,forceCleared:false})});
  guard.observeRun(ctx,'pixel',{prompt});
  let calls=0;
  const call = (toolName,params,result) => {
    const toolCallId=`artifact-continuation-${++calls}`;
    const event={toolName,params,runId:ctx.runId,toolCallId};
    const context={...ctx,toolName,toolCallId};
    const action=guard.beforeToolCall(event,context,'pixel');
    assert.notEqual(action?.block,true);
    guard.afterToolCall({...event,params:action?.params??params,result},context,'pixel');
  };
  const host = (status='succeeded') => call('pixel_ods_host_observe',{actions:['host.identity']},
    {details:{jobId:'ops-1234567890123-abcdef123456',status,waitTimedOut:false,
      steps:[{stepId:'identity',target:'ods-host',action:'host.identity',exitCode:status==='succeeded'?0:1,
        stdout:'test-host\n',stderr:'',outputTruncated:{stdout:false,stderr:false},riskSignals:[]}]}});
  const write = (path,failed=false) => call('write',{path,content:'test-host\n'},
    {isError:failed,content:[{type:'text',text:failed?'write failed':'Successfully wrote 10 bytes'}]});
  const read = (path,failed=false) => call('read',{path},
    {isError:failed,content:[{type:'text',text:failed?'read failed':'test-host\n'}]});
  const finalize = () => guard.beforeAgentFinalize({runId:ctx.runId,lastAssistantMessage:'Done.'},ctx,'pixel');
  return {guard,call,host,write,read,finalize};
}

test('binds both live owner-named filenames without widening workspace intent',()=>{
  for (const [name,prompt] of prompts) assert.equal(api.userMessageWorkspaceContinuationPath([],prompt),name);
  assert.equal(api.userMessageWorkspaceContinuationPath([],'Create /workspace/reports/host.txt and read it back.'),'reports/host.txt');
  assert.equal(api.userMessageWorkspaceContinuationPath([],'Create a workspace text file called "host.txt".'),'host.txt');
  for (const prompt of [
    'Create a file named host.txt.',
    'Create workspace files named one.txt and two.txt.',
    'Create a workspace file named ../escape.txt.',
    'Create a workspace file named folder/host.txt.',
    'Create a workspace file named C:\\temp\\host.txt.',
    'Create a workspace file named https://example.com/host.txt.',
    'Create a workspace file named .hidden.txt.',
    'Do not create a workspace file named host.txt.',
    'Explain this quote: "create a workspace file named host.txt".',
    'Create a workspace note.\n> create a file named host.txt',
    'Create a workspace note containing "file named host.txt".',
    'Create a workspace note.\n```text\nfile named host.txt\n```',
    'Inspect the ODS host.\n\n[ODS Pixel delivery requirement: create a workspace file named host.txt.]',
  ]) assert.equal(api.userMessageWorkspaceContinuationPath([],prompt),undefined,prompt);
});

test('real hooks coach publication only after exact write and read, then require a real artifact receipt',()=>{
  for(const [name,prompt] of prompts){
    const f=fixture(prompt);f.host();
    assert.equal(f.guard.verificationForRun(ctx.runId).status,'passed');
    assert.equal(f.finalize().retry.idempotencyKey,'pixel-ods-workspace-write');
    f.write(name);
    assert.equal(f.finalize().retry.idempotencyKey,'pixel-ods-workspace-read');
    f.read(name);
    const next=f.finalize();
    assert.equal(next.action,'revise');
    assert.equal(next.retry.idempotencyKey,'pixel-ods-workspace-publish');
    assert.match(next.retry.instruction,/pixel_ods_workspace_artifact/);
    assert.ok(next.retry.instruction.includes(JSON.stringify({relativePath:name})));
    // The pinned runtime cannot revise finalization after side effects. The
    // persisted read result must carry this guidance into the next model call.
    const persisted=f.guard.toolResultPersist({runId:ctx.runId,message:{role:'toolResult',toolName:'read',
      content:[{type:'text',text:'test-host\n'}]}},ctx,'pixel');
    assert.match(JSON.stringify(persisted.message.content),/pixel_ods_workspace_artifact/);
    const before=f.guard.deliveryVerificationForRun(ctx.runId);
    assert.equal(before.artifacts,undefined);
    assert.equal(before.status,'failed','write/read alone cannot complete document delivery');
    assert.doesNotMatch(before.text,/not both written and read/);
    assert.equal(f.guard.reserveWorkspaceArtifact(ctx),true);
    assert.equal(f.guard.acceptWorkspaceArtifact(ctx,{schemaVersion:1,kind:'ods-pixel-workspace-artifact',
      relativePath:name,siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),
      file:{path:name,bytes:10,sha256:createHash('sha256').update('test-host\n').digest('hex')}}),true);
    assert.equal(f.guard.deliveryVerificationForRun(ctx.runId).artifacts.length,1);
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,'pixel-ods-workspace-publish');
  }
});

test('host failure and a request without download cannot trigger publication',()=>{
  const [name,prompt]=prompts[1];
  const failed=fixture(prompt);failed.host('failed');failed.write(name);failed.read(name);
  assert.notEqual(failed.finalize()?.retry?.idempotencyKey,'pixel-ods-workspace-publish');
  const plain=fixture('Read the actual hostname of the ODS host. Create a workspace text file named host.txt and read it back.');
  plain.host();plain.write('host.txt');plain.read('host.txt');
  assert.notEqual(plain.finalize()?.retry?.idempotencyKey,'pixel-ods-workspace-publish');
});

test('wrong-path and failed write/read results cannot advance publication',()=>{
  const [name,prompt]=prompts[1];
  for(const failure of ['wrong-write','failed-write','wrong-read','failed-read']){
    const f=fixture(prompt);f.host();
    f.write(failure==='wrong-write'?'different.txt':name,failure==='failed-write');
    f.read(failure==='wrong-read'?'different.txt':name,failure==='failed-read');
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,'pixel-ods-workspace-publish',failure);
    assert.equal(f.guard.deliveryVerificationForRun(ctx.runId).artifacts,undefined);
  }
});

test('publication coaching respects cancellation, supersession and attempt limits',async()=>{
  const [name,prompt]=prompts[1];
  for(const stop of ['cancel','supersede','attempt-limit']){
    const f=fixture(prompt);f.host();f.write(name);f.read(name);
    assert.equal(f.finalize().retry.idempotencyKey,'pixel-ods-workspace-publish');
    if(stop==='cancel')assert.equal(await f.guard.abortUserRun(user),true);
    if(stop==='supersede')f.guard.observeRun({...ctx,runId:'new-owner-run'},'pixel',{prompt:'Hello'});
    if(stop==='attempt-limit')for(let i=0;i<4;i++)assert.equal(f.guard.reserveWorkspaceArtifact(ctx),true);
    assert.notEqual(f.finalize()?.retry?.idempotencyKey,'pixel-ods-workspace-publish',stop);
    assert.equal(f.guard.reserveWorkspaceArtifact(ctx),false,stop);
  }
});
