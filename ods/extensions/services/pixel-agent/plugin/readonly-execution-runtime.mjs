import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import {createReadonlySandboxAllocator} from './readonly-sandbox-allocator.mjs';
import {createReadonlyExecutionProvider} from './readonly-execution-provider.mjs';
import {createReadonlyExecutionRunner} from './readonly-execution-runner.mjs';

const unavailable=()=>Object.assign(new Error('readonly-host-unavailable'),{code:'readonly-host-unavailable'});
const absolute=p=>typeof p==='string'&&p!=='/'&&path.posix.isAbsolute(p)&&path.posix.normalize(p)===p&&
  !p.endsWith('/')&&!/[\\\x00-\x1f\x7f]/.test(p);

// No host state is touched during plugin discovery. Host argv comes only from
// the allocator; all model-supplied commands still use the public SDK exec tool.
export function createReadonlyExecutionRuntime({readConfig,resolveStateDir,resolveCliPath,createTools,
  resolveSandbox,acquireLease,afterToolCall,authority,onSettled,runFile=promisify(execFile)}={}){
  if([resolveStateDir,resolveCliPath,runFile].some(fn=>typeof fn!=='function'))
    throw new TypeError('readonly runtime dependencies required');
  let allocator;
  function allocatedRuntime(){
    if(allocator)return allocator;
    const stateRoot=resolveStateDir(),cliPath=resolveCliPath();
    if(!absolute(stateRoot)||!absolute(cliPath))throw unavailable();
    async function docker(args){
      try{
        const result=await runFile('docker',args,{encoding:'utf8',shell:false,timeout:15000,maxBuffer:1024*1024});
        if(typeof result?.stdout!=='string')throw unavailable();
        return result.stdout;
      }catch(error){
        if(args.length===4&&args[0]==='inspect'&&args[1]==='--type'&&args[2]==='container'&&error?.code===1){
          const match=/^(?:Error: No such object: |Error response from daemon: No such container: )([^\r\n]+)$/.exec(String(error.stderr??'').trim());
          if(match?.[1]===args[3])throw Object.assign(new Error('DOCKER_NOT_FOUND'),{code:'DOCKER_NOT_FOUND'});
        }
        throw unavailable();
      }
    }
    async function cli(args){
      try{
        const result=await runFile(process.execPath,[cliPath,...args],{encoding:'utf8',shell:false,
          timeout:20000,maxBuffer:1024*1024,env:{...process.env,OPENCLAW_STATE_DIR:stateRoot}});
        if(typeof result?.stdout!=='string')throw unavailable();
        return result.stdout;
      }catch{throw unavailable();}
    }
    allocator=createReadonlySandboxAllocator({stateRoot,runDocker:docker,runCli:cli,acquireLease});
    return allocator;
  }
  const provider=createReadonlyExecutionProvider({readConfig,createTools,resolveSandbox,allocator:{
    allocate:(...args)=>allocatedRuntime().allocate(...args),recover:()=>allocatedRuntime().recover(),
  }});
  const runner=createReadonlyExecutionRunner({prepare:provider.prepare,authority,afterToolCall,onSettled});
  return Object.freeze({scopeForContext:provider.scopeForContext,execute:runner.execute,
    cancel:runner.cancel,recover:provider.recover});
}
