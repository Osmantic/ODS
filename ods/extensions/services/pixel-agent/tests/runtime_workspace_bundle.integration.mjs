// Explicit integration with the installed, pinned SDK. No model or live owner
// configuration/session is read. All files and runtime state are temporary.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {createWorkspaceBundleExecution} from '../plugin/workspace-bundle-execution.mjs';
import {createWorkspaceBundleService} from '../plugin/workspace-bundle.mjs';
import {createExecCancellationControl} from '../plugin/tool-loop-guard.mjs';

const installed=process.argv[2];
assert.ok(installed&&path.isAbsolute(installed),'Pass the installed OpenClaw package directory.');
assert.equal(JSON.parse(fs.readFileSync(path.join(installed,'package.json'))).version,'2026.6.33');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-native-bundle-'));
process.env.OPENCLAW_STATE_DIR=path.join(temporary,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'openclaw.json');
const require=createRequire(path.join(installed,'package.json'));
const sandboxImage=process.argv[3] === '-' ? undefined : process.argv[3];
const mode=process.argv[4] ?? 'normal';
assert.ok(['normal','background','cancel'].includes(mode));
let container, quiescent=false;
// A gateway has a live server handle. Keep this standalone fixture alive while
// the SDK uses unref-ed background process/poll timers.
const keepAlive=setInterval(()=>{},1000);
try {
  const {createOpenClawCodingTools,resolveSandboxContext}=await import(pathToFileURL(require.resolve('openclaw/plugin-sdk/agent-harness')));
  const workspace=path.join(temporary,'workspace');fs.mkdirSync(workspace);
  fs.mkdirSync(path.join(workspace,'.openclaw','sandbox-skills'),{recursive:true});
  const controlRoot=path.join(workspace,'.cancellation');fs.mkdirSync(controlRoot,{mode:0o700});
  const sandboxSettings=sandboxImage ? {mode:'all',scope:'session',workspaceAccess:'rw',docker:{
    image:sandboxImage,containerPrefix:'ods-bundle-integration-',network:'none',readOnlyRoot:true,
    user:`${process.getuid()}:${process.getgid()}`,capDrop:['ALL'],pidsLimit:64,memory:'256m',cpus:1,
    binds:[`${controlRoot}:/run/pixel-ods-control:ro`]}} : {mode:'off'};
  const config={agents:{defaults:{workspace,sandbox:sandboxSettings},list:[{id:'pixel',workspace}]},
    plugins:{enabled:false},tools:{profile:'coding',exec:{host:sandboxImage?'sandbox':'gateway',security:'full',ask:'off'}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  fs.copyFileSync(new URL('../host/cancellable-exec.sh',import.meta.url),path.join(controlRoot,'cancellable-exec.sh'));
  fs.chmodSync(path.join(controlRoot,'cancellable-exec.sh'),0o500);
  const control=createExecCancellationControl({root:controlRoot,executionHost:sandboxImage?'sandbox':'gateway'});
  const abort=new AbortController(), actions=[];
  let backgrounds=0, signals=0;
  const executionControl={prepare:(...args)=>control.prepare(...args),signal:(...args)=>{signals++;return control.signal(...args);}};
  const adapter=createWorkspaceBundleExecution({readConfig:()=>config,
    helperSource:()=> (mode==='normal'?'':'import time; time.sleep(2)\n') + fs.readFileSync(new URL('../plugin/workspace-bundle.py',import.meta.url),'utf8'),
    createTools:options=>createOpenClawCodingTools(options).map(tool=>!['exec','process'].includes(tool.name)?tool:{...tool,execute:async(id,args)=>{
      if(tool.name==='process')actions.push(args.action);
      const result=await tool.execute(id,tool.name==='exec' && mode!=='normal'?{...args,yieldMs:10}:args);
      if(tool.name==='exec' && result.details?.status==='running'){backgrounds++;if(mode==='cancel')abort.abort();}
      return result;
    }}),
    resolveSandbox:async options=>{const value=await resolveSandboxContext(options);container=value?.containerName;return value;},execControl:()=>executionControl});
  const service=createWorkspaceBundleService({runHelper:(scope,payload,signal,lifecycle)=>adapter.runHelper(scope,payload,signal,{...lifecycle,onSettled:()=>{quiescent=true;lifecycle.onSettled();}}),invalidatePreview:()=>true,stateDir:path.join(temporary,'receipts')});
  const nonce=path.basename(temporary);
  const base={agentId:'pixel',runId:nonce,sessionId:nonce,sessionKey:'agent:pixel:'+nonce,toolCallId:'bundle'};
  const factory={...base,workspaceDir:workspace,oneShotCliRun:true};
  const scope=adapter.scopeForContext(base,factory);
  const request={outputRoot:'public',mappingPath:'map.json',files:[{source:'source.py',key:'renamed.py',copyTo:'raw.txt'}]};
  const bytes=Buffer.from('\ufeffprint("\\n")\r\n# snowman ☃\r\n');fs.writeFileSync(path.join(workspace,'source.py'),bytes);
  const start=performance.now();
  let receipt;
  if(mode==='cancel') await assert.rejects(service(scope,request,abort.signal),/bundle-cancelled/);
  else {
    receipt=await service(scope,request);
    assert.equal(receipt.readbackVerified,true);
    assert.deepEqual(fs.readFileSync(path.join(workspace,receipt.generationPath,'raw.txt')),bytes);
    assert.deepEqual(Buffer.from(JSON.parse(fs.readFileSync(path.join(workspace,receipt.mappingPath),'utf8'))['renamed.py']),bytes);
  }
  assert.equal(quiescent,true);
  assert.equal(fs.readdirSync(path.join(temporary,'receipts')).length,0,'lane released only after SDK settlement');
  if(mode!=='normal'){assert.equal(backgrounds,1);assert.ok(actions.includes('poll'));}
  if(mode==='cancel'){await new Promise(resolve=>setTimeout(resolve,2100));assert.equal(signals,1);assert.ok(actions.includes('kill'));assert.equal(fs.existsSync(path.join(workspace,'public')),false);}
  console.log(JSON.stringify({kind:'isolated-installed-sdk-bundle',version:'2026.6.33',executionHost:sandboxImage?'sandbox':'gateway',mode,status:'passed',milliseconds:Math.round(performance.now()-start),backgrounds,actions,signals,quiescent,sources:receipt?.sources,outputs:receipt?.outputs}));

} finally {
  clearInterval(keepAlive);
  if(container){assert.match(container,/^ods-bundle-integration-/);const removed=spawnSync('docker',['rm','-f',container],{encoding:'utf8'});assert.equal(removed.status,0,removed.stderr);}
  if(quiescent)fs.rmSync(temporary,{recursive:true,force:true});
  else console.error('Uncertain isolated execution retained at '+temporary); }
