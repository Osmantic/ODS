import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import path from 'node:path';
import {executionHostForAgent} from './access-runtime.mjs';
import {LayaBatchError} from './laya-batch.mjs';

const fail = code => { throw new LayaBatchError(code); };
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const packed = value => deflateSync(Buffer.from(value)).toString('base64');

// Same scoped SDK boundary as ordinary exec. Never read/write dataset bytes
// using the gateway's filesystem and never weaken the configured tool policy.
export function createLayaBatchExecution({readConfig,createTools,resolveSandbox,execControl,onToolResult,
  helperSource = () => readFileSync(new URL('./laya-files.py',import.meta.url),'utf8')} = {}) {
  const prepared = new WeakMap();
  function scopeForContext(active,factory) {
    const config = readConfig(), pixel = config?.agents?.list?.find(agent=>agent.id==='pixel');
    const workspaceRoot = pixel?.workspace ?? config?.agents?.defaults?.workspace;
    if (active?.agentId !== 'pixel' || !active.runId || !active.sessionId || !active.sessionKey ||
        !path.isAbsolute(workspaceRoot ?? '') || factory?.workspaceDir !== workspaceRoot ||
        ['agentId','sessionId','sessionKey'].some(key=>active[key]!==factory?.[key])) fail('dataset-execution-scope-unavailable');
    return Object.freeze({...active,workspaceRoot,configDigest:digest(config),factory});
  }
  async function toolsFor(scope) {
    if (digest(readConfig()) !== scope.configDigest) fail('dataset-runtime-changed');
    if (!prepared.has(scope)) prepared.set(scope,(async()=>{
      const config=readConfig(), sandbox=await resolveSandbox({config,sessionKey:scope.sessionKey,workspaceDir:scope.workspaceRoot});
      if ((executionHostForAgent(config,'pixel') === 'sandbox') !== Boolean(sandbox?.enabled)) fail('dataset-sandbox-mismatch');
      const model=scope.factory.activeModel;
      const tools=createTools({config,agentId:'pixel',runId:scope.runId,sessionId:scope.sessionId,
        sessionKey:scope.sessionKey,runSessionKey:scope.sessionKey,workspaceDir:scope.workspaceRoot,cwd:scope.workspaceRoot,sandbox,
        modelProvider:model?.provider,modelId:model?.modelId,oneShotCliRun:scope.factory.oneShotCliRun===true,
        emitBeforeToolCallDiagnostics:false});
      const exec=tools.find(tool=>tool.name==='exec');
      if (!exec) fail('dataset-core-exec-unavailable');
      return {exec,process:tools.find(tool=>tool.name==='process')};
    })());
    return prepared.get(scope);
  }
  async function runHelper(scope,payload,signal) {
    const tools=await toolsFor(scope);
    const source=helperSource(), data=JSON.stringify(payload);
    if (typeof source!=='string' || Buffer.byteLength(source)>32768 || Buffer.byteLength(data)>256*1024) fail('dataset-request-too-large');
    // Compression keeps structured data beneath POSIX argv limits. Nothing in
    // the dataset becomes shell source, a Python import or an executable path.
    const command=`python3 -I -S -c 'import base64,zlib; exec(compile(zlib.decompress(base64.b64decode("${packed(source)}")), "<ods-laya-dataset>", "exec"))' '${packed(data)}'`;
    if (command.length>60000) fail('dataset-request-too-large');
    if (signal?.aborted || digest(readConfig())!==scope.configDigest) fail('dataset-cancelled-or-changed');
    const controlled=execControl().prepare(scope.runId,command);
    let cancelSignalFailed=false;
    const cancel=()=>{
      try {execControl().signal(scope.runId);}
      catch {cancelSignalFailed=true;} // Still kill/observe the exact SDK process below.
    };
    signal?.addEventListener('abort',cancel,{once:true});
    try {
      // The pinned SDK emits before_tool_call, but its agent harness owns the
      // matching after hook. These nested calls do not pass through that
      // harness, so deliver their real outcome to the same Portal observer.
      // Otherwise completed helpers leak active tool entries and block updates.
      const execute=async(name,id,params)=>{
        const context={agentId:scope.agentId,runId:scope.runId,sessionId:scope.sessionId,
          sessionKey:scope.sessionKey,toolName:name,toolCallId:id};
        let result;
        try {result=await tools[name].execute(id,params);}
        catch(error) {
          await onToolResult?.({toolName:name,toolCallId:id,params,error:'Scoped dataset tool failed'},context);
          throw error;
        }
        await onToolResult?.({toolName:name,toolCallId:id,params,result},context);
        return result;
      };
      let result=await execute('exec',`ods-laya-${scope.toolCallId}-${payload.operation}`,{
        command:controlled,workdir:scope.workspaceRoot,timeout:10,yieldMs:10000,background:false});
      if (result?.details?.status==='running' && typeof result.details.sessionId==='string') {
        const sessionId=result.details.sessionId;
        if (!tools.process) fail('dataset-execution-unsettled');
        if (signal?.aborted) await execute('process',`ods-laya-kill-${scope.toolCallId}`,{action:'kill',sessionId});
        result=await execute('process',`ods-laya-poll-${scope.toolCallId}`,{action:'poll',sessionId,timeout:10000});
        if (result?.details?.sessionId!==sessionId || !['completed','failed'].includes(result.details.status)) fail('dataset-execution-unsettled');
      }
      if (cancelSignalFailed) fail('dataset-cancellation-signal-failed');
      if (signal?.aborted || digest(readConfig())!==scope.configDigest) fail('dataset-cancelled-or-changed');
      const details=result?.details;
      const text=typeof details?.aggregated==='string' ? details.aggregated
        : result?.content?.filter(item=>item.type==='text').map(item=>item.text).join('\n');
      if (typeof text!=='string' || text.length>256*1024) fail('dataset-receipt-unavailable');
      let value;
      try {value=JSON.parse(text);} catch {fail('dataset-receipt-unavailable');}
      if (result.isError || details?.status!=='completed' || details.exitCode!==0 || value.status!=='succeeded')
        fail(typeof value.error==='string' && /^[a-z-]{1,80}$/.test(value.error) ? value.error : 'dataset-helper-failed');
      return value;
    } finally {signal?.removeEventListener('abort',cancel);}
  }
  return {scopeForContext,runHelper};
}
