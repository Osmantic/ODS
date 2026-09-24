import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pythonSyntaxGuidance } from '../plugin/python-syntax-guidance.mjs';
import { createToolLoopGuard } from '../plugin/tool-loop-guard.mjs';

const failure = '  File "/workspace/project/script.py", line 21\n    with open(path, "w") as f:\\n    f.write(content)\n                              ^\nSyntaxError: unexpected character after line continuation character\n';
function result(text = failure) {
  return {isError:true, content:[{type:'text',text}],details:{status:'completed',exitCode:1,aggregated:text,durationMs:31}};
}
function wrapped(value) {
  const envelope={tool:{id:'openclaw:core:exec',name:'exec',source:'openclaw',sourceName:'core'},result:value};
  return {content:[{type:'text',text:JSON.stringify(envelope)}],details:envelope};
}
function run({deferred=false,wrap=false,variant='',command='python3 script.py',text=failure}={}) {
  const guard=createToolLoopGuard(wrap ? {execControl:{prepare:(runId,command)=>`/control/wrapper ${runId} ${Buffer.from(command).toString('base64')}`}} : {});
  const context={agentId:'pixel',runId:'run-1',sessionId:'session-1',sessionKey:'owner-1',toolCallId:'syntax-1'};
  guard.observeRun(context,'pixel',{prompt:'Work in /workspace/project. Run the existing Python script and diagnose its failure.'});
  const toolName=deferred?'tool_call':'exec';
  const args={command,workdir:'/workspace/project'};
  const params=deferred?{id:'openclaw:core:exec',args}:args;
  const before=guard.beforeToolCall({toolName,params},{...context,toolName});
  assert.notEqual(before?.block,true);
  let actual=structuredClone(before?.params??params);
  const execution=result(text);
  if(variant==='success') {execution.isError=false;execution.details.exitCode=0;}
  if(variant==='running') execution.details.status='running';
  if(variant==='no-exit') delete execution.details.exitCode;
  const receipt=deferred?wrapped(execution):execution;
  if(variant==='bad-envelope') receipt.details.tool.sourceName='other';
  if(variant==='wrong-command') (deferred?actual.args:actual).command='python3 different.py';
  if(variant==='wrong-workdir') (deferred?actual.args:actual).workdir='/workspace/other';
  const afterContext={...context,toolName};
  const afterEvent={toolName,params:actual,result:receipt};
  if(variant==='after-run') afterContext.runId='run-2';
  if(variant==='after-call') afterContext.toolCallId='syntax-2';
  if(variant==='after-session') afterContext.sessionId='session-2';
  if(variant==='after-key') afterContext.sessionKey='owner-2';
  if(variant==='event-run') afterEvent.runId='run-2';
  if(variant==='event-call') afterEvent.toolCallId='syntax-2';
  if(variant==='event-tool') afterEvent.toolName='read';
  if(variant!=='no-after') guard.afterToolCall(afterEvent,afterContext);
  const persistContext={...context,toolName};
  const event={toolName,toolCallId:context.toolCallId,message:{role:'toolResult',toolName,toolCallId:context.toolCallId,...receipt}};
  if(variant==='persist-run') persistContext.runId='run-2';
  if(variant==='persist-session') persistContext.sessionId='session-2';
  if(variant==='persist-key') persistContext.sessionKey='owner-2';
  if(variant==='persist-tool') event.message.toolName='read';
  if(variant==='persist-call') event.message.toolCallId='syntax-2';
  if(variant==='persist-success') (deferred?event.message.details.result:event.message).details.exitCode=0;
  if(variant==='persist-envelope') event.message.details.tool.name='read';
  const persisted=guard.toolResultPersist(event,persistContext)?.message??event.message;
  return {guard,persisted,event,persistContext,execution};
}

for(const deferred of [false,true]) for(const wrap of [false,true]) {
  test(`short Python failure gains bounded advice without changing receipt (deferred=${deferred},wrap=${wrap})`,()=>{
    const {guard,persisted,event,persistContext,execution}=run({deferred,wrap});
    const text=persisted.content.map(b=>b.text).join('\n');
    assert.match(text,/ODS Pixel Python syntax/);
    assert.match(text,/actual bytes first/);
    assert.match(text,/never globally replace/);
    assert.match(text,/same failed command within the remaining repair budget/);
    const kept=deferred?persisted.details.result:persisted;
    assert.equal(kept.details.exitCode,1);
    assert.equal(kept.isError,true);
    assert.equal(kept.content[0].text,execution.content[0].text);
    assert.equal(kept.details.durationMs,31);
    if (!deferred) assert.equal(kept.details.aggregated,execution.details.aggregated);
    const second=guard.toolResultPersist(event,persistContext)?.message??event.message;
    assert.doesNotMatch(JSON.stringify(second),/ODS Pixel Python syntax/);
  });
}
for(const deferred of [false,true]) for(const wrap of [false,true]) {
  for(const variant of ['no-after','wrong-command','wrong-workdir','after-run','after-call','after-session','after-key',
    'event-run','event-call','event-tool','persist-run','persist-session','persist-key','persist-tool','persist-call','persist-success','success','running','no-exit',
    ...(deferred?['bad-envelope','persist-envelope']:[])]) {
    test(`syntax advice rejects ${variant} (deferred=${deferred},wrap=${wrap})`,()=>{
      assert.doesNotMatch(JSON.stringify(run({deferred,wrap,variant}).persisted),/ODS Pixel Python syntax/);
    });
  }
}
for(const [label,command,text] of [
  ['stdin',"python3 - <<'PY'\npass\nPY",failure.replace('/workspace/project/script.py','<stdin>')],
  ['string','python3 -c "import script"',failure.replace('/workspace/project/script.py','<string>')],
  ['hex quote','python3 script.py',failure.replace('with open(path, "w") as f:\\n    f.write(content)',"with open(path, \\x27w\\x27) as f:")],
  ['short traceback without visible escape','python3 -m unittest',failure.replace('with open(path, "w") as f:\\n    f.write(content)','n()')],
]) test(`diagnostic covers ${label}`,()=>assert.match(JSON.stringify(run({command,text}).persisted),/ODS Pixel Python syntax/));

test('unrelated errors and valid Python escaped strings do not trigger the diagnostic',()=>{
  for(const [command,value] of [
    ['python3 script.py',result(failure.replace('unexpected character after line continuation character','invalid syntax'))],
    ['python3 script.py',result('SyntaxError: unexpected character after line continuation character')],
    ['echo python3 script.py',result()],
    ['python3 script.py',{...result('value = "\\n"\n'),isError:false,details:{status:'completed',exitCode:0}}],
    ['python3 script.py',result('value = "\\n"\nAssertionError: unexpected value')],
  ]) assert.equal(pythonSyntaxGuidance({command},value),undefined);
});

test('real Python distinguishes escaped source bytes from valid string escapes',()=>{
  for(const [source,expected] of [
    ['x = 1\\ny = 2',true],
    ['value = \\x27text\\x27',true],
    ['value = "\\n"\nassert value == chr(10)',false],
    ['if True print("invalid")',false],
  ]) {
    const python=process.platform==='win32'?'python':'python3';
    const execution=spawnSync(python,['-'],{input:source,encoding:'utf8',timeout:5000});
    assert.ifError(execution.error);
    const receipt={content:[{type:'text',text:execution.stderr}],details:{status:'completed',exitCode:execution.status,aggregated:execution.stderr}};
    assert.equal(Boolean(pythonSyntaxGuidance({command:'python3 -'},receipt)),expected);
  }
});
