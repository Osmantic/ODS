import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

const prompt = 'Run a read-only hostname check on this Mac. Then use Python in the workspace to create fleet-squares.csv with columns number,square and rows for 1 through 12. Read the file back to verify the last row is 12,144, and give me a downloadable link to the CSV.';
const name = 'fleet-squares.csv';
const csv = 'number,square\r\n' + Array.from({length:12},(_,i)=>`${i+1},${(i+1)**2}\r\n`).join('');
const user = `ods-${'e'.repeat(64)}`;
const ctx = {trigger:'user',agentId:'pixel',runId:'exec-artifact',sessionId:'exec-artifact-session',
  sessionKey:`agent:pixel:openai-user:${user}`};
const textResult = text => ({content:[{type:'text',text}]});
const matchedFooter = /read back .* and verified that the published download contains the same bytes/;
const noWorkspacePythonPrompt = 'Use Python to create fleet-even.csv with columns n,double and rows 1 through 6. Read it back and check the final row is 6,12. Publish the file for download. In the final answer, format the download as bold Download: followed by a link labelled only fleet-even.csv.';

for (const wrapped of [false, true]) for (const tool of ['write', 'edit'])
test(`Python creation corrects a hidden alternate filename: ${tool}, wrapped=${wrapped}`, () => {
  for (const requested of ['fleet-receipt.csv', 'reports/fleet-receipt.csv']) {
    const hidden = requested.replace(/([^/]+)$/, '.$1');
    const ownerPath = requested.includes('/') ? `/workspace/${requested}` : requested;
    const f = fixture(`Use Python to create ${ownerPath}. Read the saved CSV back and give me the CSV itself as a direct download.`);
    const args = {path: `/workspace/${hidden}`, content: csv, oldText: 'x', newText: 'y'};
    const toolName = wrapped ? 'tool_call' : tool;
    const event = {toolName, params: wrapped ? {id: tool, args} : args, runId: ctx.runId, toolCallId: 'hidden-output'};
    const action = f.guard.beforeToolCall(event, {...ctx, toolName, toolCallId: event.toolCallId}, 'pixel');
    assert.equal(action?.block, true, requested);
    assert.match(action.blockReason, /executing Python/);
    assert.ok(action.blockReason.includes(`"${requested}"`), 'name the requested output');
    assert.ok(action.blockReason.includes(`"${hidden}"`), 'explain the alternate filename');
    assert.equal(action.params, undefined, 'never silently rewrite the file path');
  }
});

test('Python hidden-name correction leaves scripts, other directories and owner intent boundaries intact', () => {
  const owners = [
    'Use Python to create fleet-receipt.csv.',
    'Do not use Python to create fleet-receipt.csv.',
    'Explain how to use Python to create fleet-receipt.csv.',
    'Write a note containing "Use Python to create fleet-receipt.csv".',
    'Use Python to create fleet-receipt.csv and other.csv.',
    'Create fleet-receipt.csv.'
  ];
  for (const wrapped of [false, true]) for (const tool of ['write', 'edit']) {
    for (const owner of owners) for (const target of [
      'make_receipt.py', '.make_receipt.py', 'notes.csv', 'other/fleet-receipt.csv',
      'other/.fleet-receipt.csv', '..fleet-receipt.csv', 'fleet-receipt.csv.bak',
      ...(owner === owners[0] ? [] : ['.fleet-receipt.csv'])
    ]) {
      const f = fixture(owner);
      const args = {path: target, content: csv, oldText: 'x', newText: 'y'};
      const toolName = wrapped ? 'tool_call' : tool;
      const event = {toolName, params: wrapped ? {id: tool, args} : args, runId: ctx.runId, toolCallId: 'other-output'};
      const action = f.guard.beforeToolCall(event, {...ctx, toolName, toolCallId: event.toolCallId}, 'pixel');
      assert.doesNotMatch(action?.blockReason ?? '', /executing Python/, `${owner}: ${target}`);
    }
  }
});

function fixture(ownerPrompt=prompt) {
  const guard=api.createToolLoopGuard({abortRunAndDrain:async()=>({aborted:true,drained:true,forceCleared:false})});
  guard.observeRun(ctx,'pixel',{prompt:ownerPrompt});
  let calls=0;
  function call(name,args,result,{wrapped=false,outerError=false,sourceName='core',eventRunId,sessionId}={}) {
    const toolName=wrapped?'tool_call':name;
    const params=wrapped?{id:name,args}:args;
    const toolCallId=`exec-artifact-call-${++calls}`;
    const event={toolName,params,runId:ctx.runId,toolCallId};
    const action=guard.beforeToolCall(event,{...ctx,toolName,toolCallId},'pixel');
    assert.notEqual(action?.block,true,action?.blockReason);
    const actualParams=action?.params??params;
    const envelope={tool:{id:`openclaw:${sourceName}:${name}`,name,source:'openclaw',sourceName},result};
    guard.afterToolCall({...event,params:actualParams,...(eventRunId?{runId:eventRunId}:{}),
      result:wrapped?{isError:outerError,details:envelope,content:[{type:'text',text:JSON.stringify(envelope)}]}:result},
      {...ctx,toolName,toolCallId,...(sessionId?{sessionId}:{})},'pixel');
    return actualParams;
  }
  const host=(failed=false)=>call('pixel_ods_host_observe',{actions:['host.identity']},
    {details:{jobId:'ops-1234567890123-abcdef123456',status:failed?'failed':'succeeded',waitTimedOut:false,
      steps:[{stepId:'identity',target:'ods-host',action:'host.identity',exitCode:failed?1:0,
        stdout:'michaels-Mini\n',stderr:'',outputTruncated:{stdout:false,stderr:false},riskSignals:[]}]}});
  const exec=(wrapped=false)=>call('exec',{command:'python3 make_csv.py'},
    {...textResult('Created fleet-squares.csv'),details:{status:'completed',exitCode:0}}, {wrapped});
  const read=(text=csv,options={})=>call('read',{path:`/workspace/${name}`,...options.params},
    {...textResult(text),...options.result},options);
  const receipt=(text=csv,options={})=>({schemaVersion:1,kind:'ods-pixel-workspace-artifact',
    relativePath:name,siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),
    file:{path:name,bytes:Buffer.byteLength(text),sha256:createHash('sha256').update(text).digest('hex')},...options});
  function publish(text=csv,options={}) {
    assert.equal(guard.reserveWorkspaceArtifact(ctx),true);
    assert.equal(guard.acceptWorkspaceArtifact(ctx,receipt(text,options)),true);
  }
  const final=()=>guard.beforeAgentFinalize({runId:ctx.runId,lastAssistantMessage:'The CSV is ready.'},ctx,'pixel');
  const delivery=()=>guard.deliveryVerificationForRun(ctx.runId);
  return {guard,call,host,exec,read,receipt,publish,final,delivery};
}

test('binds the unchanged live Python CSV prompt and direct owner file commands',()=>{
  for(const p of [prompt,'Create fleet-squares.csv in the workspace.',
    'In the workspace, create fleet-squares.csv.',
    'Please generate "fleet-squares.csv" in the workspace.',
    'Use Python in the workspace to create fleet-squares.csv.',
    'Could you write the file fleet-squares.csv in the workspace?'])
    assert.equal(api.userMessageWorkspaceContinuationPath([],p),name,p);
  for(const p of ['Inspect the workspace. Do not create fleet-squares.csv.',
    'Inspect the workspace. Explain why we create fleet-squares.csv.',
    'Inspect the workspace. Use Python without permission to create fleet-squares.csv.',
    'Inspect the workspace. If needed, create fleet-squares.csv.',
    'Create a workspace note containing "create fleet-squares.csv".',
    'Inspect the workspace.\n> Create fleet-squares.csv.',
    'Inspect the workspace.\n```text\nCreate fleet-squares.csv.\n```',
    'Create fleet-squares.csv and other.csv in the workspace.',
    'Create .fleet-squares.csv in the workspace.',
    'Create ../fleet-squares.csv in the workspace.'])
    assert.equal(api.userMessageWorkspaceContinuationPath([],p),undefined,p);
});

test('repeated conjunctions cannot cause filename-intent backtracking',()=>{
  const hostile='Inspect the workspace. and'+ '\tthen\tand'.repeat(5000)+' create fleet-squares.csv.';
  const start=performance.now();
  assert.equal(api.userMessageWorkspaceContinuationPath([],hostile),undefined);
  assert.ok(performance.now()-start<1000,'bounded owner-intent classification');
  assert.equal(api.userMessageWorkspaceContinuationPath([],'Inspect the workspace. And then create fleet-squares.csv.'),name);
});

test('explicit Python file creation does not require the word workspace',()=>{
  for(const p of [noWorkspacePythonPrompt, 'Use Python to create fleet-even.csv.',
    'Please create fleet-even.csv using Python.', 'Use Python3 to generate fleet-even.csv.'])
    assert.equal(api.userMessagePythonCreationPath([],p),'fleet-even.csv',p);
  for(const p of ['Do not use Python to create fleet-even.csv.',
    'Explain how to use Python to create fleet-even.csv.',
    'Write a note containing "Use Python to create fleet-even.csv".',
    '> Use Python to create fleet-even.csv.',
    '```text\nUse Python to create fleet-even.csv.\n```',
    'Use Python to create fleet-even.csv and another.csv.',
    'Use Python to create ../fleet-even.csv.',
    'Create fleet-even.csv. Python is an example, not a requirement.'])
    assert.equal(api.userMessagePythonCreationPath([],p),undefined,p);
});

test('the unchanged live Python prompt blocks directly writing the requested output',()=>{
  for(const wrapped of [false,true]) {
    const f=fixture(noWorkspacePythonPrompt);
    const params={path:'/workspace/fleet-even.csv',content:'n,double\n6,12\n'};
    const action=f.guard.beforeToolCall({toolName:wrapped?'tool_call':'write',
      params:wrapped?{id:'write',args:params}:params,runId:ctx.runId,toolCallId:'direct-output'},ctx,'pixel');
    assert.equal(action?.block,true);
    assert.match(action.blockReason,/executing Python/);
    f.call('write',{path:'/workspace/create_csv.py',content:'print("generator")'},textResult('Written'));
    f.call('exec',{command:'python3 create_csv.py'},
      {...textResult('created'),details:{status:'completed',exitCode:0}});
  }
});

for(const wrapped of [false,true]) test(`Python-created CSV composes with readback and accepted download, wrapped=${wrapped}`,()=>{
  const f=fixture(); f.host(); f.exec(wrapped);
  assert.equal(f.final().retry.idempotencyKey,'pixel-ods-workspace-read');
  f.read(csv,{wrapped});
  assert.equal(f.final().retry.idempotencyKey,'pixel-ods-workspace-publish');
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  f.publish();
  assert.match(f.delivery().text,matchedFooter);
  assert.doesNotMatch(f.delivery().text,/Pixel wrote and read back/,'exec must not fabricate write provenance');
  assert.equal(f.delivery().artifacts[0].file.bytes,90);
  assert.notEqual(f.final()?.action,'revise','no redundant write/read/publication');
  const payload=f.guard.replyPayloadSending({runId:ctx.runId,kind:'final',payload:{text:'The CSV is ready.'}});
  assert.match(payload.payload.text,matchedFooter);
  assert.doesNotMatch(payload.payload.text,/not verified|not both written/);
});

test('independent workspace reads retain their actual target and range',()=>{
  const f=fixture();f.host();
  assert.deepEqual(f.call('read',{path:'source.txt',offset:3,limit:2},textResult('source')),
    {path:'source.txt',offset:3,limit:2});
  assert.doesNotMatch(f.delivery().text,matchedFooter);
});

test('exec success and an unaccepted tool-result receipt cannot establish publication',()=>{
  const f=fixture();f.host();f.exec();f.read();
  f.call('pixel_ods_workspace_artifact',{relativePath:name},{details:f.receipt()},
    {wrapped:true,sourceName:'pixel-ods'});
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  assert.equal(f.delivery().artifacts,undefined);
  assert.equal(f.guard.acceptWorkspaceArtifact(ctx,f.receipt()),false,'reservation is mandatory');
});

for(const details of [{status:'completed',exitCode:1},{status:'running',sessionId:'not-completed'}])
test(`non-successful exec does not bypass creation coaching: ${details.status}`,()=>{
  const f=fixture();f.host();
  f.call('exec',{command:'python3 make_csv.py'},{...textResult('not completed'),details});
  assert.equal(f.final().retry.idempotencyKey,'pixel-ods-workspace-write');
  assert.doesNotMatch(f.delivery().text,matchedFooter);
});

test('unbound read results cannot establish current-run snapshot evidence',()=>{
  const f=fixture();f.host();f.exec();f.publish();
  f.guard.afterToolCall({toolName:'read',params:{path:`/workspace/${name}`},result:textResult(csv),
    toolCallId:'never-started',runId:ctx.runId},{...ctx,toolName:'read',toolCallId:'never-started'},'pixel');
  assert.doesNotMatch(f.delivery().text,matchedFooter);
});

test('an unbound read cannot verify readback after an ordinary direct write',()=>{
  const f=fixture('Run a read-only hostname check on this Mac. Create fleet-squares.csv in the workspace, read it back, and give me a downloadable link.');
  f.host();
  f.call('write',{path:`/workspace/${name}`,content:csv},textResult('Written'));
  f.publish();
  f.guard.afterToolCall({toolName:'read',params:{path:`/workspace/${name}`},result:textResult(csv),
    toolCallId:'never-started',runId:ctx.runId},{...ctx,toolName:'read',toolCallId:'never-started'},'pixel');
  assert.doesNotMatch(f.delivery().text,/Pixel wrote and read back/);
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  assert.equal(f.final()?.retry?.idempotencyKey,'pixel-ods-workspace-read');
});

test('readback hashes exact UTF-8 bytes, including empty and non-ASCII content',()=>{
  for(const text of ['', 'café,雪\r\n']){
    const f=fixture();f.host();f.exec();f.read(text);f.publish(text);
    assert.match(f.delivery().text,matchedFooter);
  }
});

const invalidReads = [
  ['failed',{result:{isError:true}}], ['wrong path',{params:{path:'different.csv'}}],
  ['offset',{params:{offset:1}}], ['limit',{params:{limit:100}}],
  ['truncation',{result:{details:{truncation:{truncated:true}}}}],
  ['output truncated',{result:{details:{outputTruncated:{stdout:true}}}}],
  ['multiple blocks',{result:{content:[{type:'text',text:csv},{type:'text',text:'guidance'}]}}],
  ['empty blocks',{result:{content:[]}}], ['outer failure',{wrapped:true,outerError:true}],
  ['wrong wrapper source',{wrapped:true,sourceName:'unrelated'}],
  ['conflicting run',{eventRunId:'different-run'}], ['conflicting session',{sessionId:'different-session'}],
];
for(const [reason,options] of invalidReads) test(`accepted snapshot does not qualify an invalid read: ${reason}`,()=>{
  const f=fixture();f.host();f.exec();f.read(csv,options);f.publish();
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  assert.notEqual(f.final()?.retry?.idempotencyKey,'pixel-ods-workspace-write','retain the file; request readback');
});

test('exact content, bytes, line endings and requested path must match the accepted receipt',()=>{
  for(const variation of ['content','bytes','line-endings','path']){
    const f=fixture();f.host();f.exec();f.read();
    const receipt=f.receipt(variation==='content'?'different':variation==='line-endings'?csv.replaceAll('\r\n','\n'):csv);
    if(variation==='bytes')receipt.file.bytes++;
    if(variation==='path'){receipt.relativePath='other.csv';receipt.file.path='other.csv';}
    assert.equal(f.guard.reserveWorkspaceArtifact(ctx),true);
    assert.equal(f.guard.acceptWorkspaceArtifact(ctx,receipt),true);
    assert.doesNotMatch(f.delivery().text,matchedFooter,variation);
  }
});

test('later failed or partial read invalidates earlier proof; a complete read can recover it',()=>{
  for(const options of [{result:{isError:true}},{params:{limit:2}}]){
    const f=fixture();f.host();f.exec();f.read();f.publish();
    assert.match(f.delivery().text,matchedFooter);
    f.read(csv,options);
    assert.doesNotMatch(f.delivery().text,matchedFooter);
    assert.equal(f.final().retry.idempotencyKey,'pixel-ods-workspace-read');
    f.read(); assert.match(f.delivery().text,matchedFooter);
  }
});

test('readback may precede or follow publication when its actual bytes match',()=>{
  const f=fixture();f.host();f.exec();f.publish();
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  f.read();assert.match(f.delivery().text,matchedFooter);
});

test('the newest accepted snapshot for the requested path controls verification',()=>{
  const f=fixture();f.host();f.exec();f.read();f.publish();
  f.publish('changed',{siteId:`site-${'b'.repeat(24)}`,sha256:'b'.repeat(64)});
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  assert.equal(f.final().retry.idempotencyKey,'pixel-ods-workspace-publish');
  f.read('changed');assert.match(f.delivery().text,matchedFooter);
});

test('failed host observation remains failed despite matching workspace readback and publication',()=>{
  const f=fixture();f.host(true);f.exec();f.read();f.publish();
  assert.equal(f.delivery().status,'failed');
  assert.match(f.delivery().text,/required host Operations job reached terminal status failed/);
  assert.doesNotMatch(f.delivery().text,/Portal verified these ODS host facts/);
  assert.match(f.delivery().text,matchedFooter,'independent file evidence does not satisfy host success');
});

test('cancelled or superseded runs cannot gain a matching snapshot completion claim',async()=>{
  for(const stop of ['cancel','supersede']){
    const f=fixture();f.host();f.exec();f.read();f.publish();
    if(stop==='cancel')assert.equal(await f.guard.abortUserRun(user),true);
    else f.guard.observeRun({...ctx,runId:'later-run'},'pixel',{prompt:'Hello'});
    assert.doesNotMatch(f.delivery().text??'',matchedFooter);
    assert.equal(f.guard.acceptWorkspaceArtifact(ctx,f.receipt()),false);
  }
});

test('the fourth reserved receipt may match; a fifth publication cannot be coached',()=>{
  const f=fixture();f.host();f.exec();f.read();
  for(let i=0;i<4;i++)assert.equal(f.guard.reserveWorkspaceArtifact(ctx),true);
  assert.equal(f.guard.acceptWorkspaceArtifact(ctx,f.receipt()),true);
  assert.match(f.delivery().text,matchedFooter);
  f.read('mismatch');
  assert.doesNotMatch(f.delivery().text,matchedFooter);
  assert.notEqual(f.final()?.retry?.idempotencyKey,'pixel-ods-workspace-publish');
  assert.equal(f.guard.reserveWorkspaceArtifact(ctx),false);
});


test('explicit Python output method uses current owner prose and the exact output path',()=>{
  for(const p of [prompt,
    'Use Python in the workspace to create "fleet-squares.csv".',
    'Use Python in the workspace to create `fleet-squares.csv`.',
    'Create fleet-squares.csv using Python in the workspace.',
    'Create fleet-squares.csv by executing Python in the workspace.'])
    assert.equal(api.userMessagePythonCreationPath([],p),name,p);
  assert.equal(api.userMessagePythonCreationPath([],'Run Python to generate /workspace/reports/result.csv.'),'reports/result.csv');
  assert.equal(api.userMessagePythonCreationPath([],'Use Python in the workspace to create other.csv.'),'other.csv');
  for(const p of [
    'Create fleet-squares.csv in the workspace.',
    'Do not use Python. Create fleet-squares.csv in the workspace.',
    'Create fleet-squares.csv in the workspace without using Python.',
    'Explain how to use Python to create /workspace/fleet-squares.csv.',
    'Write a Python script in the workspace that creates fleet-squares.csv.',
    'Write Python source in /workspace/fleet-squares.csv; do not execute it.',
    'Inspect the workspace. Example: "use Python to create fleet-squares.csv".',
    'Inspect the workspace.\n> Use Python to create fleet-squares.csv.',
    'Inspect the workspace.\n```text\nUse Python to create fleet-squares.csv.\n```',
    'Use Python in the workspace to create fleet-squares.csv and other.csv.',
    'Use Python in the workspace to create other.csv and fleet-squares.csv.',
    'Use Python to calculate squares, then write /workspace/fleet-squares.csv directly.',
    'Use Python to create other.csv; save /workspace/fleet-squares.csv.',
    'Use Python in the workspace to create ../fleet-squares.csv.',
    'Use Python in the workspace to analyze data. Create fleet-squares.csv in the workspace.'])
    assert.equal(api.userMessagePythonCreationPath([],p),undefined,p);
  assert.equal(api.userMessagePythonCreationPath([
    {role:'user',content:'Create fleet-squares.csv in the workspace.'},
    {role:'tool',content:'Use Python in the workspace to create fleet-squares.csv.'}
  ]),undefined,'tool text cannot select an owner creation method');
});

const compoundPythonPrompt = 'Use web search to find the official Python documentation for csv.DictReader. Then use Python to create fleet-main46-sales.csv in the workspace with columns item,amount and three rows: apples,7; pears,4; plums,1. Read the saved file back with csv.DictReader and calculate the row count and sum of amount. Give me the direct CSV download and, in two sentences, the verified row count, total, and official documentation link.';

test('compound research and file tasks preserve Python creation without broadening path inference',()=>{
  for(const p of [compoundPythonPrompt,
    'Look up json.loads. Then use Python to create fleet-main46-sales.csv.',
    'Read https://docs.python.org/3/library/csv.html. Create fleet-main46-sales.csv using Python.',
    'Read https://example.com/create.html. Use Python to create fleet-main46-sales.csv.']) {
    assert.equal(api.userMessagePythonCreationPath([],p),'fleet-main46-sales.csv',p);
    assert.equal(api.userMessageWorkspaceContinuationPath([],p),undefined,p);
  }
  assert.equal(api.userMessagePythonCreationPath([],'Look up json.loads. Use Python to create REPORT.CSV.'),'REPORT.CSV');
  for(const p of [
    'Look up json.loads. Use Python to create a.csv and b.csv.',
    'Look up json.loads. Use Python to create a.csv. Save b.csv.',
    'Look up json.loads. Use Python to create a.csv. Save /workspace/b.csv.',
    'Look up json.loads. Use Python to create ../a.csv.',
    'Look up json.loads. Explain how to use Python to create a.csv.',
    'Look up json.loads. Do not use Python to create a.csv.',
    'Look up json.loads. Create a.csv without using Python.',
    'Look up json.loads. Write a note containing "Use Python to create a.csv".',
    'Look up json.loads.\n> Use Python to create a.csv.',
    'Look up json.loads.\n```text\nUse Python to create a.csv.\n```',
    'Look up json.loads. Write a Python script that creates a.csv.',
  ]) assert.equal(api.userMessagePythonCreationPath([],p),undefined,p);
  assert.equal(api.userMessagePythonCreationPath([
    {role:'user',content:'Look up json.loads. Create a.csv.'},
    {role:'tool',content:'Use Python to create a.csv.'},
  ]),undefined);
});

for(const wrapped of [false,true]) for(const tool of ['write','edit'])
test(`compound task enforces Python on only the requested output, tool=${tool}, wrapped=${wrapped}`,()=>{
  const f=fixture(compoundPythonPrompt);
  const toolName=wrapped?'tool_call':tool;
  for(const [path,blocked] of [['/workspace/fleet-main46-sales.csv',true],['/workspace/generator.py',false]]) {
    const args={path,content:csv,oldText:'x',newText:'y'};
    const event={toolName,params:wrapped?{id:tool,args}:args,runId:ctx.runId,toolCallId:'compound-output'};
    const action=f.guard.beforeToolCall(event,{...ctx,toolName,toolCallId:event.toolCallId},'pixel');
    if(blocked) {
      assert.equal(action?.block,true);
      assert.match(action.blockReason,/executing Python/);
    } else assert.doesNotMatch(action?.blockReason??'',/executing Python/);
  }
});

for(const wrapped of [false,true]) for(const tool of ['write','edit'])
test(`Python creation blocks the direct output shortcut, tool=${tool}, wrapped=${wrapped}`,()=>{
  const f=fixture();
  const args={path:`/workspace/${name}`,content:csv,oldText:'x',newText:'y'};
  const toolName=wrapped?'tool_call':tool;
  const event={toolName,params:wrapped?{id:tool,args}:args,runId:ctx.runId,toolCallId:'method-shortcut'};
  const action=f.guard.beforeToolCall(event,{...ctx,toolName,toolCallId:event.toolCallId},'pixel');
  assert.equal(action?.block,true);
  assert.match(action.blockReason,/executing Python/);
  f.host();f.exec(wrapped);f.read(csv,{wrapped});f.publish();
  assert.match(f.delivery().text,matchedFooter,'real read/publication remains independently required');
  assert.doesNotMatch(f.delivery().text,/Pixel wrote and read back/);
});

test('generator scripts and unrelated writes remain usable before host observation',()=>{
  const f=fixture();
  f.call('write',{path:'/workspace/make_csv.py',content:'print(1)'},textResult('Written'));
  f.call('write',{path:'notes.txt',content:'notes'},textResult('Written'),{wrapped:true});
  f.exec();f.host();f.read();f.publish();
  assert.match(f.delivery().text,matchedFooter);
});

test('a later owner turn can request ordinary direct writes',()=>{
  const f=fixture();
  const next={...ctx,runId:'ordinary-next-turn'};
  f.guard.observeRun(next,'pixel',{prompt:'Create fleet-squares.csv in the workspace.'});
  const event={toolName:'write',params:{path:name,content:csv},runId:next.runId,toolCallId:'ordinary-write'};
  assert.notEqual(f.guard.beforeToolCall(event,{...next,toolName:'write',toolCallId:event.toolCallId},'pixel')?.block,true);
});

test('tool-result continuation retains the current owner creation method',()=>{
  const f=fixture();f.exec();
  f.guard.observeRun(ctx,'pixel',{messages:[{role:'tool',content:'Execution completed.'}]});
  const event={toolName:'write',params:{path:name,content:csv},runId:ctx.runId,toolCallId:'post-exec-write'};
  const action=f.guard.beforeToolCall(event,{...ctx,toolName:'write',toolCallId:event.toolCallId},'pixel');
  assert.equal(action?.block,true,'an exec exit code must not pretend the requested creation method was proved');
  assert.match(action.blockReason,/executing Python/);
});

test('Python host reports keep the requested method instead of the fixed report writer',()=>{
  const f=fixture('Run a read-only hostname check on this Mac. Use Python in the workspace to create host-report.csv with the observed hostname and give me a downloadable link.');
  f.host();
  const pending=f.final();
  assert.equal(pending.retry.idempotencyKey,'pixel-ods-workspace-write');
  assert.match(pending.retry.instruction??pending.retry.prompt??JSON.stringify(pending),/requested method/);
  assert.doesNotMatch(JSON.stringify(pending),/narrow adapter writes/);
});

test('explicit Python creation accepts named output descriptions without losing the method',()=>{
  const target='fleet-main-f151-squares.csv';
  for(const p of [
    `Use Python to create a downloadable CSV named ${target} with columns n,square for the integers 1 through 4. Give me the finished file.`,
    `Use Python to create a downloadable CSV called ${target}.`,
    `Use Python to create a CSV file named ${target}.`,
    `Use Python to create a text file called ${target}.`,
    `Use Python to create a downloadable file named ${target}.`,
    `Use Python to create a plain text file named "${target}".`,
    `Create a CSV file named ${target} using Python.`,
    `Create a downloadable CSV named ${target} by executing Python.`])
    assert.equal(api.userMessagePythonCreationPath([],p),target,p);
  for(const p of [
    `Do not use Python to create a CSV file named ${target}.`,
    `Explain how to use Python to create a CSV file named ${target}.`,
    `Use Python to create a CSV file named ${target} and other.csv.`,
    `Use Python to create a CSV file named ../${target}.`,
    `Use Python to create a CSV file named /workspace/../${target}.`,
    `Write a note containing "Use Python to create a downloadable CSV named ${target}".`,
    `Inspect the workspace.\n> Use Python to create a downloadable CSV named ${target}.`,
    'Inspect the workspace.\n```text\nUse Python to create a downloadable CSV named '+target+'.\n```',
    `Write a Python script named generator.py that creates ${target}.`])
    assert.equal(api.userMessagePythonCreationPath([],p),undefined,p);
});

for(const wrapped of [false,true])for(const tool of ['write','edit'])
test(`named CSV creation rejects a direct-byte shortcut: ${tool}, wrapped=${wrapped}`,()=>{
  const target='fleet-main-f151-squares.csv';
  const f=fixture(`Use Python to create a downloadable CSV named ${target} with columns n,square for the integers 1 through 4. Give me the finished file.`);
  const args={path:`/workspace/${target}`,content:csv,oldText:'x',newText:'y'};
  const toolName=wrapped?'tool_call':tool;
  const event={toolName,params:wrapped?{id:tool,args}:args,runId:ctx.runId,toolCallId:'named-csv-shortcut'};
  const action=f.guard.beforeToolCall(event,{...ctx,toolName,toolCallId:event.toolCallId},'pixel');
  assert.equal(action?.block,true);
  assert.match(action.blockReason,/executing Python/);
});
