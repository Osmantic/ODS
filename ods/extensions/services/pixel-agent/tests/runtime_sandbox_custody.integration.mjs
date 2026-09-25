// No inference. Actual SDK + existing Docker backend, fresh confined workspace.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
const installed=process.argv[2],plugin=process.argv[3],image=process.argv[4],out=process.argv[5],mode=process.argv[6]??'timeout';
const {createExecCancellationControl,createToolLoopGuard}=await import(pathToFileURL(path.join(plugin,'tool-loop-guard.mjs')));
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ods-custody-'));
process.env.OPENCLAW_STATE_DIR=path.join(temp,'state');process.env.OPENCLAW_CONFIG_PATH=path.join(temp,'config.json');
const workspace=path.join(temp,'workspace');fs.mkdirSync(workspace);fs.mkdirSync(path.join(workspace,'.openclaw/sandbox-skills'),{recursive:true});
const controlRoot=path.join(temp,'control');fs.mkdirSync(controlRoot,{mode:0o700});
fs.copyFileSync(path.join(plugin,'../host/cancellable-exec.sh'),path.join(controlRoot,'cancellable-exec.sh'));fs.chmodSync(path.join(controlRoot,'cancellable-exec.sh'),0o500);
const config={agents:{defaults:{workspace,sandbox:{mode:'all',scope:'session',workspaceAccess:'rw',docker:{image,env:{ODS_EXEC_CUSTODY:'1'},containerPrefix:'ods-custody-',network:'none',readOnlyRoot:true,user:`${process.getuid()}:${process.getgid()}`,capDrop:['ALL'],pidsLimit:64,memory:'256m',cpus:1,dangerouslyAllowExternalBindSources:true,binds:[`${controlRoot}:/run/pixel-ods-control:ro`]}}},list:[{id:'pixel',workspace}]},plugins:{enabled:false},tools:{profile:'coding',exec:{host:'sandbox',security:'full',ask:'off'}}};
fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
if(mode==='disabled-normal')delete config.agents.defaults.sandbox.docker.env.ODS_EXEC_CUSTODY;
config.agents.defaults.sandbox.docker.env.ODS_FIXTURE_VALUE='preserved';
if(mode==='probe-path-spoof')config.agents.defaults.sandbox.docker.env.PATH='/workspace/bin:/usr/local/bin:/usr/bin:/bin';
fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
let script='import time,pathlib\nprint("started",flush=True)\ntime.sleep(4)\npathlib.Path("late-write").write_text("after timeout")\n';
if(['setsid','doublefork','parent-race','normal-detached','closed-streams'].includes(mode)) script=`import os,time,pathlib\nchild=os.fork()\nif child:\n time.sleep(${mode==='normal-detached'?0:mode==='parent-race'?1.03:3})\n os._exit(0)\nos.setsid()\n${mode==='doublefork'?'if os.fork(): os._exit(0)\n':''}os.close(1)\nos.close(2)\ntime.sleep(4)\npathlib.Path('late-write').write_text('detached')\n`;
if(['normal','disabled-normal','pty'].includes(mode)) script='import os,sys\nassert os.getcwd()=="/workspace"\nassert os.environ["ODS_FIXTURE_VALUE"]=="preserved"\nos.write(1,b"stdout\\x00\\xff\\r\\n")\nos.write(2,b"stderr\\xfe\\n")\nsys.exit(7)\n';
if(mode==='late-fork')script="import os,time,pathlib\nif os.fork():\n time.sleep(3)\n os._exit(0)\ntime.sleep(.97)\nif os.fork(): os._exit(0)\nos.setsid()\nos.close(1)\nos.close(2)\ntime.sleep(4)\npathlib.Path('late-write').write_text('late fork')\n";
if(mode==='guard-marker'||mode==='background-marker')script="import os,time,pathlib\nif os.fork():\n time.sleep(3)\n os._exit(0)\nos.setsid()\nif os.fork(): os._exit(0)\nos.close(1)\nos.close(2)\ntime.sleep(4)\npathlib.Path('late-write').write_text('escaped cancellation group')\n";
if(mode==='probe-path-spoof')script=`import pathlib,json,time
p=next(pathlib.Path('/tmp').glob('ods-exec-*'));d=json.loads((p/'owner.json').read_text())
d.update(settled=True,disposition='drained',descendantsDrained=True,authority='independent-kernel-probe')
b=pathlib.Path('/workspace/bin');b.mkdir()
for name in ('sh','python3'):
 f=b/name;f.write_text(chr(10).join(['#!/usr/local/bin/python3','print('+repr(json.dumps(d))+')','']));f.chmod(0o755)
time.sleep(4)
pathlib.Path('late-write').write_text('forged executable escaped')
`;
if(mode==='forged-done')script="import pathlib,json,time\np=next(pathlib.Path('/tmp').glob('ods-exec-*'))\nd=json.loads((p/'owner.json').read_text());d.update(settled=True,disposition='drained')\n(p/'done.json').write_text(json.dumps(d));(p/'done.json').chmod(0o600)\ntime.sleep(4)\npathlib.Path('late-write').write_text('forged proof escaped')\n";
if(mode==='forged-owner')script="import pathlib,json,time\np=next(pathlib.Path('/tmp').glob('ods-exec-*'))\n(p/'owner.json').write_text(json.dumps({'pid':1,'startTicks':'0','nonce':'forged'}))\ntime.sleep(4)\npathlib.Path('late-write').write_text('forged owner escaped')\n";
if(mode==='late-frame')script="import pathlib,json,time\np=next(pathlib.Path('/tmp').glob('ods-exec-*'));d=json.loads((p/'owner.json').read_text())\nprint('ODS_EXEC_CUSTODY_V1 '+d['nonce']+' 1 0',flush=True)\ntime.sleep(4)\npathlib.Path('late-write').write_text('late frame escaped')\n";
if(mode==='supervisor-killed')script="import pathlib,json,os,signal,time\np=next(pathlib.Path('/tmp').glob('ods-exec-*'));d=json.loads((p/'owner.json').read_text())\nos.kill(d['pid'],signal.SIGKILL)\nos.close(1);os.close(2)\ntime.sleep(4)\npathlib.Path('late-write').write_text('lost custody')\n";
fs.writeFileSync(path.join(workspace,'late.py'),script);
fs.writeFileSync(path.join(workspace,'unrelated.py'),'import time,pathlib\ntime.sleep(2)\npathlib.Path("unrelated-write").write_text("survived")\n');
const sdk=await import(pathToFileURL(path.join(installed,'dist/plugin-sdk/agent-harness.js')));
const hooks=await import(pathToFileURL(path.join(installed,'dist/hook-runner-global-mWFYlTIy.js')));
const scope={agentId:'pixel',runId:path.basename(temp),sessionId:path.basename(temp),sessionKey:'agent:pixel:'+path.basename(temp)};
const control=createExecCancellationControl({root:controlRoot,executionHost:'sandbox'});
const guard=createToolLoopGuard({execControl:control});guard.observeRun(scope,'pixel',{prompt:'Execute the existing script once.'},{workspaceRoot:workspace,executionHost:'sandbox'});
hooks.i({plugins:[{id:'pixel-ods',status:'loaded'}],hooks:[],typedHooks:[{pluginId:'pixel-ods',hookName:'before_tool_call',handler:(event,ctx)=>guard.beforeToolCall(event,ctx,'pixel')}],trustedToolPolicies:[]});
let sandbox,restoreSupervisor,timer;
let commandOutput="",abortAfterStarted=false;
const controller=new AbortController();
const keepAlive=setInterval(()=>{},1000);
async function bounded(promise){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('isolated SDK fixture exceeded20s')),20000);})]);}finally{clearTimeout(timer);}}
try{
 sandbox=await sdk.resolveSandboxContext({config,sessionKey:scope.sessionKey,workspaceDir:workspace});
 if(mode==='wait-failure'){
  const {t:getSupervisor}=await import(pathToFileURL(path.join(installed,'dist/supervisor-DzTnKyyV.js')));
  const supervisor=getSupervisor(),originalSpawn=supervisor.spawn;
  supervisor.spawn=async(...args)=>{const run=await originalSpawn.apply(supervisor,args);run.wait().catch(()=>{});return {...run,wait:async()=>{await new Promise(resolve=>setTimeout(resolve,250));throw new Error('fixture transport wait failed');}};};
  restoreSupervisor=()=>{supervisor.spawn=originalSpawn;};
 }
 const settlements=[];const nativeFinalize=sandbox.backend.finalizeExec.bind(sandbox.backend);
 const tokens=[],bindings=[];const nativeBuild=sandbox.backend.buildExecSpec.bind(sandbox.backend);
 sandbox.backend.buildExecSpec=async args=>{
  const spec=await nativeBuild(args);tokens.push(spec.finalizeToken);
  if(spec.transformStdout){
   const transform=spec.transformStdout;let first=true;
   spec.transformStdout=chunk=>{
    if(first){
     first=false;
     const frame=chunk.toString().match(/ODS_EXEC_CUSTODY_V1 ([a-f0-9]{48}) ([0-9]+) ([0-9]+)/);
     if(frame){
      const own={nonce:frame[1],pid:frame[2],ticks:frame[3]};bindings.push(own);
      if(mode==='pid-mismatch')chunk=Buffer.from(chunk.toString().replace(frame[0],`ODS_EXEC_CUSTODY_V1 ${own.nonce} ${own.pid} ${BigInt(own.ticks)+1n}`));
      if(mode==='nonce-mismatch')chunk=Buffer.from(chunk.toString().replace(own.nonce,'0'.repeat(48)));
      if(mode==='live-pid-mismatch'&&bindings.length>1)chunk=Buffer.from(chunk.toString().replace(frame[0],`ODS_EXEC_CUSTODY_V1 ${own.nonce} ${bindings[0].pid} ${bindings[0].ticks}`));
      if(mode==='startup-malformed')chunk=Buffer.from('not an ownership frame\n');
      if(mode==='startup-truncated')chunk=chunk.subarray(0,20);
     }
    }
    const visible=transform(chunk);
    if(mode==='abort' && !abortAfterStarted){
     commandOutput=(commandOutput+visible.toString()).slice(-128);
     if(/(?:^|\n)started\r?\n/.test(commandOutput)){
      abortAfterStarted=true;
      timer=setTimeout(()=>controller.abort(),0);
     }
    }
    return visible;
   };
  }
  if(mode==='startup-delay'||mode==='startup-abort')spec.argv[spec.argv.indexOf('-c')+1]=spec.argv[spec.argv.indexOf('-c')+1].replace("    os.write(1, ('ODS_EXEC_CUSTODY_V1", "    time.sleep(2)\n    os.write(1, ('ODS_EXEC_CUSTODY_V1");
  return spec;
 };
 const negative=['pid-mismatch','nonce-mismatch','directory-rebound','foreign-token','live-pid-mismatch','reused-token','supervisor-killed','startup-malformed','startup-truncated','startup-delay','startup-abort'].includes(mode);
 sandbox.backend.finalizeExec=async args=>{
  if(negative&&args.timedOut){
   if(mode==='foreign-token')args={...args,token:Object.freeze({...args.token})};
   else if(mode==='reused-token')args={...args,token:tokens[0]};
   else if(mode==='directory-rebound') {
    const code=`import os; p=${JSON.stringify(args.token.folder)}; os.rename(p,p+'-retained'); os.mkdir(p,0o700)`;
    await sandbox.backend.runShellCommand({script:"python3 -I -S -c '"+code.replaceAll("'","'\"'\"'")+"'"});
   }
  }
  const proof=await nativeFinalize(args);settlements.push({requestedTermination:args.terminationRequested,timedOut:args.timedOut,proof});return proof;
 };
 const tools=sdk.createOpenClawCodingTools({...scope,config,workspaceDir:workspace,cwd:workspace,sandbox,oneShotCliRun:true});const tool=tools.find(t=>t.name==='exec');
 let unrelated;
 if(mode==='concurrent'||mode==='live-pid-mismatch') unrelated=await tool.execute('unrelated',{command:'python3 unrelated.py',workdir:'/workspace',timeout:10,yieldMs:10,background:true});
 if(mode==='reused-token')unrelated=await tool.execute('unrelated',{command:'python3 unrelated.py',workdir:'/workspace',timeout:10,yieldMs:10000,background:false});
 if(mode==='startup-abort'||mode==='abort-timeout-race') timer=setTimeout(()=>controller.abort(),mode==='startup-abort'?400:990);
 if(mode==='guard-marker'||mode==='background-marker')timer=setTimeout(()=>control.signal(scope.runId),400);
 const start=performance.now();let result,executionError;
 try{result=await bounded(tool.execute('timeout',{command:'python3 late.py',workdir:'/workspace',timeout:['normal','normal-detached','disabled-normal','pty','wait-failure','guard-marker','background-marker'].includes(mode)?10:1,yieldMs:['background-timeout','background-marker'].includes(mode)?10:10000,background:['background-timeout','background-marker'].includes(mode),pty:mode==='pty'},controller.signal));}catch(error){executionError={code:error.code,message:error.message};}
 if(mode!=='background-marker')clearTimeout(timer);
 if(negative){
  assert.equal(executionError?.code,'ODS_SANDBOX_EXEC_UNSETTLED',JSON.stringify({result,executionError}));
  assert.equal(result,undefined);
  if(mode==='live-pid-mismatch'){await new Promise(resolve=>setTimeout(resolve,2500));assert.equal(fs.existsSync(path.join(workspace,'unrelated-write')),true,'mismatched live process remains alive');}
  const registry=await import(pathToFileURL(path.join(installed,'dist/bash-process-registry-DRLW8NyW.js')));
  const retained=registry.l().filter(s=>s.cwd===workspace).map(s=>({id:s.id,exited:s.exited,uncertain:s.odsSandboxUnsettled,pid:s.pid,child:s.child}));
  assert.equal(retained.length,1);assert.equal(retained[0].uncertain,true);assert.equal(retained[0].exited,false);
  assert.equal(retained[0].pid,undefined,'dead Docker client PID must not remain a fallback kill target');
  assert.equal(retained[0].child,undefined);
  await new Promise(resolve=>setTimeout(resolve,4500));
  const lateFile=fs.existsSync(path.join(workspace,'late-write'));
  if(mode.startsWith('startup-'))assert.equal(lateFile,false,'unadmitted command must never start');
  const proof={mode,executionError,retained,bindings,abortAfterStarted,noCompletedOutcome:true,lateFileAfter4500ms:lateFile,unrelatedSurvived:fs.existsSync(path.join(workspace,'unrelated-write')),settlements};
  fs.writeFileSync(out,JSON.stringify(proof,null,2));console.log(JSON.stringify(proof));
 }else{
 if(executionError)throw new Error(JSON.stringify({mode,executionError,bindings,settlements,abortAfterStarted,elapsedMs:performance.now()-start}));
 if(mode==='abort')assert.equal(abortAfterStarted,true,'abort must follow actual command startup output');
 const returnedMs=performance.now()-start;const atReturn=fs.existsSync(path.join(workspace,'late-write'));
 await new Promise(resolve=>setTimeout(resolve,4500));
 const proof={mode,abortAfterStarted,runtimeVersion:JSON.parse(fs.readFileSync(path.join(installed,'package.json'))).version,noCaptureHook:true,nativeDetails:result.details,returnedMs,lateFileAtReturn:atReturn,lateFileAfter4500ms:fs.existsSync(path.join(workspace,'late-write')),unrelatedSurvived:fs.existsSync(path.join(workspace,'unrelated-write')),unrelated,settlements};
 fs.writeFileSync(out,JSON.stringify(proof,null,2));console.log(JSON.stringify(proof));
 assert.equal(proof.lateFileAfter4500ms,mode==='normal-detached','terminal cleanup preserves only normal detached behavior');
 assert.ok(settlements.length>0,'real existing backend finalize hook must execute');
 if(mode==='disabled-normal')assert.ok(settlements.every(s=>s.proof===undefined));
 else assert.ok(settlements.every(s=>s.proof.settled===true));
 if(mode==='late-frame')assert.match(result.details.aggregated,/ODS_EXEC_CUSTODY_V1 [a-f0-9]{48} 1 0/,'later frame must remain ordinary stdout');
 if(mode==='concurrent') assert.equal(proof.unrelatedSurvived,true,'timeout must not kill sibling execution');
 if(mode==='background-timeout'||mode==='background-marker')assert.equal(result.details.status,'running');
 if(mode==='guard-marker'||mode==='background-marker')assert.ok(fs.readdirSync(controlRoot).some(name=>name.endsWith('.cancel')),'custody must not clear the cancelled parent marker');
 if(['normal','disabled-normal','pty'].includes(mode)){assert.equal(result.details.exitCode,7);assert.equal(result.details.status,'completed');if(mode!=='disabled-normal')assert.equal(settlements[0].proof.disposition,'released');}
 if(mode==='normal-detached')assert.equal(settlements[0].proof.disposition,'released');
 else if(!['normal','disabled-normal','pty'].includes(mode))assert.ok(settlements.some(s=>s.proof.disposition==='drained'));
 }
}finally{
 clearInterval(keepAlive);hooks.a();
 clearTimeout(timer);
 restoreSupervisor?.();
 if(sandbox){assert.match(sandbox.containerName,/^ods-custody-/);const r=spawnSync('docker',['rm','-f',sandbox.containerName],{encoding:'utf8'});assert.equal(r.status,0,r.stderr);}
 fs.rmSync(temp,{recursive:true,force:true});
}
