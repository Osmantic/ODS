import assert from 'node:assert/strict';
import test from 'node:test';
import {createToolLoopGuard, OWNER_NO_TOOLS_REASON} from '../plugin/tool-loop-guard.mjs';
import {OWNER_FILE_MUTATION_FORBIDDEN_REASON, ownerReadonlyToolAllowed} from '../plugin/owner-readonly-tools.mjs';
import {createReadonlyExecutionAuthority} from '../plugin/readonly-execution-authority.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const user = 'ods-' + 'e'.repeat(64);
const prompt = 'Read orders.csv and calculate the paid total. Do not create or modify files.';
function fixture(text = prompt, options = {}) {
  const authority = createReadonlyExecutionAuthority();
  const guard = createToolLoopGuard({readonlyExecutionAuthority: authority,
    abortRunAndDrain: async () => ({aborted:true, drained:true}), ...options});
  const scope = {agentId:'pixel', runId:'readonly', sessionId:'session-ro',
    sessionKey:'agent:pixel:openai-user:' + user, trigger:'user'};
  guard.observeRun(scope, 'pixel', {prompt:text});
  let calls = 0;
  function call(name, params, internal = false) {
    const id = `ro-${++calls}`;
    const context = {...scope, toolName:name, toolCallId:id};
    const event = {toolName:name, toolCallId:id, params};
    const invoke = () => guard.beforeToolCall(event, context);
    return internal ? authority.run(scope, name, id, params, invoke) : invoke();
  }
  return {authority, guard, scope, call};
}

test('file restriction supplies calculation guidance before tools, without overriding no-tools',()=>{
  const readonly=fixture();
  assert.equal(readonly.guard.promptContextForRun('readonly'),OWNER_FILE_MUTATION_FORBIDDEN_REASON);
  assert.equal(fixture(prompt+' Do not use tools.').guard.promptContextForRun('readonly'),undefined);
  assert.equal(fixture('Write a script.').guard.promptContextForRun('readonly'),undefined);
});

test('ordinary file ban denies direct, deferred, delegated and unknown mutation paths', () => {
  const cases = [
    ['write',{path:'orders.csv',content:'wrong'}], ['edit',{path:'orders.csv',oldText:'paid',newText:'free'}],
    ['apply_patch',{input:'*** Begin Patch\n*** End Patch'}],
    ['exec',{command:'python3 -c "open(\'orders.csv\',\'w\').write(\'wrong\')"'}],
    ['process',{action:'write',sessionId:'existing',data:'overwrite'}],
    ['sessions_spawn',{task:'Write orders.csv'}], ['task',{prompt:'Write orders.csv'}],
    ['sessions_send',{sessionKey:'other',message:'Write orders.csv'}], ['subagents',{action:'steer'}],
    ['pixel_ods_workspace_bundle',{}], ['pixel_ods_project_build',{}],
    ['pixel_ods_workspace_preview',{relativeDirectory:'unrequested'}],
    ['pixel_ods_workspace_artifact',{}], ['pixel_ods_download_promote',{}],
    ['pixel_ods_extension_request_prepare',{}], ['pixel_ods_host_command_propose',{}],
    ['future_mutation_tool',{}], ['tool_call',{id:'openclaw:untrusted:read',args:{}}],
    ['tool_call',{id:'tool_call',args:{id:'write',args:{path:'orders.csv'}}}],
  ];
  for (const [name, params] of cases) {
    assert.equal(fixture().call(name, params)?.blockReason, OWNER_FILE_MUTATION_FORBIDDEN_REASON, name);
  }
  for (const id of ['write','openclaw:core:write','exec','openclaw:core:exec',
    'pixel_ods_workspace_bundle','openclaw:pixel-ods:pixel_ods_workspace_bundle','sessions_spawn']) {
    assert.equal(fixture().call('tool_call',{id,args:{command:'touch x'}})?.blockReason,
      OWNER_FILE_MUTATION_FORBIDDEN_REASON, id);
  }
});

test('read tools and isolated computation stay available, directly and deferred', () => {
  for (const [name,params] of [
    ['read',{path:'orders.csv'}], ['tool_search',{query:'read-only calculation'}],
    ['pixel_ods_readonly_exec',{command:'python3 -c "print(2+2)"'}],
    ['tool_call',{id:'openclaw:core:read',args:{path:'orders.csv'}}],
    ['tool_call',{id:'openclaw:pixel-ods:pixel_ods_readonly_exec',args:{command:'printf 4'}}],
  ]) {
    const result = fixture().call(name,params);
    assert.notEqual(result?.block,true,result?.blockReason);
  }
});

test('owner file ban preserves authorized browser observation but denies mutation actions', () => {
  const allowed = [
    ['browser',{action:'open',url:'http://127.0.0.1:3001/pixel-preview/site-test/'}],
    ['browser',{action:'navigate',targetUrl:'http://127.0.0.1:3001/'}],
    ['browser',{action:'snapshot',targetId:'tab-1'}],
    ['browser',{action:'screenshot',targetId:'tab-1'}],
    ['browser',{action:'console',targetId:'tab-1'}],
    ['browser',{action:'tabs'}],
    ['browser',{action:'status'}],
    ['browser',{action:'focus',targetId:'tab-1'}],
    ['browser',{action:'close',targetId:'tab-1'}],
    ['tool_call',{id:'browser',args:{action:'open',url:'http://127.0.0.1:3001/'}}],
    ['tool_call',{id:'openclaw:browser:browser',args:{action:'snapshot',targetId:'tab-1'}}],
    ['tool_call',{id:'openclaw:core:browser',args:{action:'navigate',targetUrl:'http://127.0.0.1:3001/'}}],
  ];
  for (const [name,params] of allowed) {
    assert.equal(ownerReadonlyToolAllowed(name,params),true,`${name} ${JSON.stringify(params)}`);
  }
  const denied = [
    ['browser',{action:'act',targetId:'tab-1'}],
    ['browser',{action:'evaluate',expression:'1+1'}],
    ['browser',{action:'upload',targetId:'tab-1'}],
    ['browser',{action:'pdf',targetId:'tab-1'}],
    ['browser',{action:'download',targetId:'tab-1'}],
    ['browser',{action:'unknown'}],
    ['browser',{}],
    ['browser',{action:'open',url:'http://127.0.0.1:3001/',path:'/tmp/x'}],
    ['browser',{action:'screenshot',targetId:'tab-1',filePath:'/tmp/x.png'}],
    ['browser',{action:'screenshot',targetId:'tab-1',paths:['/tmp/x.png']}],
    ['browser',{action:'screenshot',targetId:'tab-1',filename:'x.png'}],
    ['browser',{action:'screenshot',targetId:'tab-1',outputPath:'/tmp/x.png'}],
    ['browser',{action:'screenshot',targetId:'tab-1',outputFile:'/tmp/x.png'}],
    ['browser',{action:'download',targetId:'tab-1',downloadPath:'/tmp/x'}],
    ['tool_call',{id:'browser',args:{action:'evaluate',expression:'1+1'}}],
    ['tool_call',{id:'openclaw:browser:browser',args:{action:'download',targetId:'tab-1'}}],
    ['tool_call',{id:'openclaw:core:browser',args:{action:'screenshot',targetId:'tab-1',outputPath:'/tmp/x.png'}}],
    ['tool_call',{id:'browser'}],
    ['tool_call',{id:'browser',args:'not-an-object'}],
    ['tool_call',{id:'browser',args:[]}],
    ['tool_call',{id:'openclaw:untrusted:browser',args:{action:'open',url:'http://127.0.0.1:3001/'}}],
    ['tool_call',{id:'openclaw:core:browser',args:{action:'open',url:'http://127.0.0.1:3001/',path:'/tmp/x'}}],
  ];
  for (const [name,params] of denied) {
    assert.equal(ownerReadonlyToolAllowed(name,params),false,`${name} ${JSON.stringify(params)}`);
  }
});

test('only exact privately bound internal execution gets the file-policy exception', async () => {
  const f = fixture();
  assert.notEqual((await f.call('exec',{command:'printf 4'},true))?.block,true);
  assert.equal(f.call('exec',{command:'printf 4',readonly:true})?.blockReason,OWNER_FILE_MUTATION_FORBIDDEN_REASON);
  assert.equal(f.call('tool_call',{id:'openclaw:core:exec',args:{command:'printf 4'}})?.block,true);
});

test('private readonly exec preserves SDK params and skips the native execution wrapper', async () => {
  const calls = [];
  const execControl = {
    resolveWorkdir: directory => { calls.push(['directory',directory]); return {workdir:'/resolved'}; },
    prepare: (run,command) => { calls.push(['prepare',run,command]); return 'host-wrapper ' + command; },
  };
  const readonly = fixture(prompt,{execControl});
  const params = {command:'printf 4',workdir:'/private/bootstrap',timeout:10,yieldMs:120000,background:false};
  const before = structuredClone(params);
  const decision = await readonly.call('exec',params,true);
  assert.notEqual(decision?.block,true,decision?.blockReason);
  assert.deepEqual(decision?.params ?? params,before);
  assert.deepEqual(params,before);
  assert.deepEqual(calls,[]);
  const coding = fixture('Write a small script that prints 4.',{execControl});
  const ordinary = coding.call('exec',{command:'printf 4',workdir:'/workspace'});
  assert.notEqual(ordinary?.block,true,ordinary?.blockReason);
  assert.equal(ordinary.params.command,'host-wrapper printf 4');
  assert.equal(ordinary.params.workdir,'/resolved');
  assert.equal(calls.filter(call => call[0] === 'prepare').length,1);
});

test('stronger no-tools, cancellation and progress stops still deny internal calculation', async () => {
  const noTools = fixture(prompt + ' Do not use tools.');
  assert.equal((await noTools.call('exec',{command:'printf 4'},true))?.blockReason,OWNER_NO_TOOLS_REASON);
  assert.equal(noTools.call('pixel_ods_readonly_exec',{command:'printf 4'})?.blockReason,OWNER_NO_TOOLS_REASON);
  const cancelled = fixture();
  assert.equal(await cancelled.guard.abortUserRun(user),true);
  const decision = await cancelled.call('exec',{command:'printf 4'},true);
  assert.equal(decision?.block,true);
  assert.notEqual(decision.blockReason,OWNER_FILE_MUTATION_FORBIDDEN_REASON);
  const exhausted = fixture();
  for(let i=0;i<RUN_PROGRESS_LIMITS.consecutiveFailures;i++) {
    const id='failed-'+i;
    exhausted.guard.toolResultPersist({toolCallId:id,message:{role:'toolResult',toolName:'read',isError:true,content:[]}},
      {...exhausted.scope,toolName:'read',toolCallId:id});
  }
  const stopped = await exhausted.call('exec',{command:'printf 4'},true);
  assert.equal(stopped?.block,true);
  assert.notEqual(stopped.blockReason,OWNER_FILE_MUTATION_FORBIDDEN_REASON);
});

test('quotes and later owner turns do not inherit the blanket file ban', () => {
  assert.notEqual(fixture('Explain "Do not create or modify files". Create sample.txt.')
    .call('write',{path:'sample.txt',content:'ok'})?.block,true);
  const f = fixture();
  const context = {...f.scope,runId:'next',toolName:'write',toolCallId:'next-write'};
  f.guard.observeRun(context,'pixel',{messages:[{role:'user',content:prompt},
    {role:'assistant',content:'Done.'},{role:'user',content:'Create sample.txt with ok.'}]});
  assert.notEqual(f.guard.beforeToolCall({toolName:'write',params:{path:'sample.txt',content:'ok'}},context)?.block,true);
});

test('a retry cannot erase the original file restriction or manufacture internal authority', () => {
  const f = fixture();
  f.guard.observeRun(f.scope,'pixel',{prompt:'Continue the previous attempt.'});
  assert.equal(f.call('exec',{command:'touch escaped.txt'})?.blockReason,OWNER_FILE_MUTATION_FORBIDDEN_REASON);
  const other = fixture(prompt,{readonlyExecutionAuthority:createReadonlyExecutionAuthority()});
  const event={toolName:'exec',toolCallId:'replayed',params:{command:'printf 4'}};
  const context={...other.scope,toolName:'exec',toolCallId:'replayed'};
  return f.authority.run(other.scope,'exec','replayed',event.params,()=> {
    assert.equal(other.guard.beforeToolCall(event,context)?.blockReason,OWNER_FILE_MUTATION_FORBIDDEN_REASON);
  });
});
