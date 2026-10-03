import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createWorkspaceBundleAdmission, createWorkspaceBundleService, createWorkspaceBundleTool,
  normalizeWorkspaceBundle, WORKSPACE_BUNDLE_TOOL} from '../plugin/workspace-bundle.mjs';
import {createWorkspaceBundleExecution} from '../plugin/workspace-bundle-execution.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';

const request = {outputRoot:'project/public',mappingPath:'sources.json',files:[{source:'project/a.py',key:'a.py',copyTo:'a.py.txt'}]};
const generation='bundle-'+'a'.repeat(32);
const payload={request,generation};
const context = {agentId:'pixel',runId:'run',sessionId:'session',sessionKey:'agent:pixel:session',toolCallId:'bundle'};
const posix = {skip:process.platform === 'win32'};
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(),'ods-bundle-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const workspaceRoot=path.join(root,'workspace'),stateDir=path.join(root,'private');
  fs.mkdirSync(path.join(workspaceRoot,'project'),{recursive:true});
  fs.writeFileSync(path.join(workspaceRoot,request.files[0].source),'print("exact")\r\n');
  const scope={...context,workspaceRoot};
  const runHelper = async (_scope,payload,_signal,lifecycle) => {
    const result=spawnSync('python3',['-I','-S',fileURLToPath(new URL('../plugin/workspace-bundle.py',import.meta.url)),Buffer.from(JSON.stringify(payload)).toString('base64')],{cwd:workspaceRoot,encoding:'utf8'});
    lifecycle?.onSettled?.();
    assert.equal(result.error,undefined);
    const receipt=JSON.parse(result.stdout);
    if(receipt.status!=='succeeded')throw Object.assign(new Error(receipt.error),{code:receipt.error,written:receipt.written});
    return receipt;
  };
  return {root,scope,stateDir,runHelper};
}

test('real registration exposes a bounded content-free bundle schema',async()=>{
  const tool=(await registeredPixelTools()).find(tool=>tool.name===WORKSPACE_BUNDLE_TOOL);
  assert.ok(tool);
  assert.deepEqual(tool.parameters.required,['files','mappingPath','outputRoot']);
  assert.equal(tool.parameters.additionalProperties,false);
  assert.equal(tool.parameters.properties.files.maxItems,32);
  assert.deepEqual(tool.parameters.properties.files.items.required,['source','key','copyTo']);
});

test('model arguments cannot smuggle content, shell paths, duplicate destinations or nested files',()=>{
  assert.deepEqual(normalizeWorkspaceBundle(request),request);
  for(const input of [{...request,content:'invented'}, {...request,mappingPath:'../outside'},
    {...request,mappingPath:request.files[0].copyTo}, {...request,mappingPath:'a.py.txt/child'},
    {...request,files:[...request.files,{source:'project/b.py',key:'other',copyTo:'A.PY.TXT'}]}])
    assert.throws(()=>normalizeWorkspaceBundle(input));
});

test('only an admitted exact direct or deferred call can use the factory scope once',()=>{
  for(const deferred of [false,true]) {
    const admission=createWorkspaceBundleAdmission();
    const event=deferred?{toolName:'tool_call',params:{id:'openclaw:pixel-ods:'+WORKSPACE_BUNDLE_TOOL,args:request}}:{toolName:WORKSPACE_BUNDLE_TOOL,params:request};
    const id=deferred?`tool_search_code:bundle:${WORKSPACE_BUNDLE_TOOL}:1`:'bundle';
    admission.before(event,context,{block:true});
    assert.throws(()=>admission.take(id,request,context),/unbound/);
    admission.before(event,context);
    assert.throws(()=>admission.take(id,{...request,mappingPath:'other.json'},context),/unbound/);
    assert.throws(()=>admission.take(id,request,{...context,sessionId:'other'}),/unbound/);
    assert.deepEqual(admission.take(id,request,context),context);
    assert.throws(()=>admission.take(id,request,context),/unbound/);
    admission.after(event,context);
  }
});

test('fresh service instances create new generations and preserve originals and prior output edits',posix,async t=>{
  const f=fixture(t);let invalidated=0;
  const make=()=>createWorkspaceBundleService({...f,invalidatePreview:()=>{invalidated++;return true;}});
  const one=await make()(f.scope,request);
  const previous=path.join(f.scope.workspaceRoot,one.generationPath,'a.py.txt');
  fs.writeFileSync(previous,'owner edit');
  fs.writeFileSync(path.join(f.scope.workspaceRoot,request.files[0].source),'new\n');
  const two=await make()({...f.scope,runId:'next-turn'},request);
  assert.equal(invalidated,2);assert.notEqual(one.generationPath,two.generationPath);
  assert.equal(fs.readFileSync(previous,'utf8'),'owner edit');
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.scope.workspaceRoot,two.mappingPath),'utf8'))['a.py'],'new\n');
  assert.equal(fs.readdirSync(f.stateDir).length,0);
});

test('unsettled helper keeps a durable workspace lane across later service instances',posix,async t=>{
  const f=fixture(t);let attempts=0;
  const uncertain=async(_scope,_payload,_signal,lifecycle)=>{
    attempts++;lifecycle.onProcess({sessionId:'exact-owned-process',runId:'run'});throw new Error('unknown settlement');
  };
  await assert.rejects(createWorkspaceBundleService({...f,runHelper:uncertain,invalidatePreview:()=>true})(f.scope,request),/unknown settlement/);
  const record=JSON.parse(fs.readFileSync(path.join(f.stateDir,fs.readdirSync(f.stateDir)[0])));
  assert.equal(record.execution.sessionId,'exact-owned-process');
  await assert.rejects(createWorkspaceBundleService({...f,runHelper:uncertain,invalidatePreview:()=>true})(f.scope,request),/workspace-execution-unsettled/);
  assert.equal(attempts,1);
});

test('settled helper failure releases the workspace lane without claiming a verified generation',posix,async t=>{
  const f=fixture(t);
  await assert.rejects(createWorkspaceBundleService({...f,invalidatePreview:()=>true,
    runHelper:async(_scope,_payload,_signal,lifecycle)=>{lifecycle.onSettled();throw new Error('failed output');}})(f.scope,request),/failed output/);
  assert.equal(fs.readdirSync(f.stateDir).length,0);
  assert.equal((await createWorkspaceBundleService({...f,invalidatePreview:()=>true})(f.scope,request)).readbackVerified,true);
});

test('unavailable run writes no generation and releases its undispatched lane',posix,async t=>{
  const f=fixture(t);
  await assert.rejects(createWorkspaceBundleService({...f,invalidatePreview:()=>false})(f.scope,request),/run-unavailable/);
  assert.equal(fs.existsSync(path.join(f.scope.workspaceRoot,'project/public')),false);
  assert.equal(fs.readdirSync(f.stateDir).length,0);
});

test('adapter keeps installed core execution policy and rejects scope/config/sandbox drift',async()=>{
  let config={agents:{list:[{id:'pixel',workspace:path.resolve('workspace'),tools:{exec:{host:'gateway'}}}]}}, calls=0, options;
  const factory={...context,workspaceDir:config.agents.list[0].workspace,activeModel:{provider:'local',modelId:'default'}};
  const execution=createWorkspaceBundleExecution({readConfig:()=>config,resolveSandbox:async()=>null,
    execControl:()=>({prepare:(run,command)=>{assert.equal(run,'run');return command;}}),helperSource:()=> 'print("fixed")',
    createTools:value=>{options=value;return [{name:'exec',execute:async(_id,args)=>{
      calls++;assert.equal(args.workdir,factory.workspaceDir);assert.equal(args.background,false);
      assert.match(args.command,/^python3 -I -S -c /);
      return {details:{status:'completed',exitCode:0,aggregated:JSON.stringify({status:'succeeded'})}};
    }}];}});
  const scope=execution.scopeForContext(context,factory);
  await execution.runHelper(scope,payload);
  assert.equal(calls,1);assert.equal(options.sessionId,context.sessionId);assert.equal(options.sandbox,null);
  assert.equal(options.modelId,'default');
  assert.throws(()=>execution.scopeForContext(context,{...factory,sessionKey:'other'}),/scope-unavailable/);
  config={...config,changed:true};
  await assert.rejects(execution.runHelper(scope,payload),/runtime-changed/);
  assert.equal(calls,1);
});

test('adapter never treats running or nonzero execution as completed bundle and preserves bounded partial writes',async()=>{
  const workspaceRoot=path.resolve('workspace'),config={agents:{list:[{id:'pixel',workspace:workspaceRoot,tools:{exec:{host:'gateway'}}}]}};
  let result={details:{status:'running',sessionId:'background'}};
  const adapter=createWorkspaceBundleExecution({readConfig:()=>config,resolveSandbox:async()=>null,
    helperSource:()=>'',execControl:()=>({prepare:(_id,x)=>x}),createTools:()=>[{name:'exec',execute:async()=>result}]});
  const scope=adapter.scopeForContext(context,{...context,workspaceDir:workspaceRoot});
  await assert.rejects(adapter.runHelper(scope,payload),/unsettled/);
  result={isError:true,details:{status:'failed',exitCode:1,aggregated:JSON.stringify({status:'failed',error:'source-changed-during-commit',written:[`${request.outputRoot}/${generation}/${request.files[0].copyTo}`]})}};
  await assert.rejects(adapter.runHelper(scope,payload),error=>{
    assert.deepEqual(error.written,[`${request.outputRoot}/${generation}/${request.files[0].copyTo}`]);return error.code==='source-changed-during-commit';
  });
});

test('tool failed output reports partial writes without claiming proof or leaking arbitrary errors',async()=>{
  const admission=createWorkspaceBundleAdmission();admission.before({toolName:WORKSPACE_BUNDLE_TOOL,params:request},context);
  const tool=createWorkspaceBundleTool(context,{admission,scopeForContext:()=>context,
    execute:async()=>{throw Object.assign(new Error('private internals'),{code:'workspace-io-failed',written:[request.mappingPath]});}});
  const result=await tool.execute('bundle',request);
  assert.equal(result.isError,true);assert.equal(result.details.readbackVerified,false);
  assert.deepEqual(result.details.written,[request.mappingPath]);assert.doesNotMatch(JSON.stringify(result),/private internals/);
});

test('bundle preview invalidation requires the exact current owned live run',()=>{
  const guard=createToolLoopGuard();guard.observeRun(context,'pixel',{prompt:'Create files in a workspace project.'});
  assert.equal(guard.invalidateWorkspaceBundle({...context,sessionKey:'other'}),false);
  assert.equal(guard.invalidateWorkspaceBundle(context),true);
  guard.observeRun({...context,runId:'new'},'pixel',{prompt:'Next task.'});
  assert.equal(guard.invalidateWorkspaceBundle(context),false);
});


test('owned background process is polled to terminal before releasing lifecycle',async()=>{
  const workspaceRoot=path.resolve('workspace'),config={agents:{list:[{id:'pixel',workspace:workspaceRoot,tools:{exec:{host:'gateway'}}}]}};
  const calls=[];let observed=false,settled=false;
  const adapter=createWorkspaceBundleExecution({readConfig:()=>config,resolveSandbox:async()=>null,
    helperSource:()=>'',execControl:()=>({prepare:(_id,x)=>x}),createTools:()=>[
      {name:'exec',execute:async()=>({details:{status:'running',sessionId:'owned'}})},
      {name:'process',execute:async(_id,args)=>{calls.push(args);assert.equal(settled,false);
        return {details:{status:'completed',sessionId:'owned',exitCode:0,aggregated:JSON.stringify({status:'succeeded'})}};}},
    ]});
  const scope=adapter.scopeForContext(context,{...context,workspaceDir:workspaceRoot});
  await adapter.runHelper(scope,payload,undefined,{onProcess:value=>{observed=true;assert.equal(value.sessionId,'owned');},onSettled:()=>{settled=true;}});
  assert.equal(observed,true);assert.equal(settled,true);
  assert.deepEqual(calls,[{action:'poll',sessionId:'owned',timeout:10000}]);
});

test('cancelled background execution is killed and drained, never treated as successful output',async()=>{
  const workspaceRoot=path.resolve('workspace'),config={agents:{list:[{id:'pixel',workspace:workspaceRoot,tools:{exec:{host:'gateway'}}}]}};
  const abort=new AbortController(),calls=[];let settled=false,signalled=false;
  const adapter=createWorkspaceBundleExecution({readConfig:()=>config,resolveSandbox:async()=>null,
    helperSource:()=>'',execControl:()=>({prepare:(_id,x)=>x,signal:()=>{signalled=true;}}),createTools:()=>[
      {name:'exec',execute:async()=>{abort.abort();return {details:{status:'running',sessionId:'owned'}};}},
      {name:'process',execute:async(_id,args)=>{calls.push(args.action);assert.equal(settled,false);
        return args.action==='kill'?{details:{status:'failed'}}:{details:{status:'failed',sessionId:'owned',exitCode:143,aggregated:''}};}},
    ]});
  const scope=adapter.scopeForContext(context,{...context,workspaceDir:workspaceRoot});
  await assert.rejects(adapter.runHelper(scope,payload,abort.signal,{onSettled:()=>{settled=true;}}),/bundle-cancelled/);
  assert.equal(signalled,true);assert.equal(settled,true);assert.deepEqual(calls,['kill','poll']);
});
