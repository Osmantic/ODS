// Explicit qualification with the pinned installed SDK. Own temporary state;
// no owner settings, service restart, remote provider, or model turn.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createLayaBatchTool,createLayaBatchAdmission,LAYA_BATCH_TOOL} from '../plugin/laya-batch.mjs';
import {createLayaBatchExecution} from '../plugin/laya-batch-execution.mjs';
import {createExecCancellationControl} from '../plugin/tool-loop-guard.mjs';
import {createAccessRuntime} from '../plugin/access-runtime.mjs';

const installed=process.argv[2], sandboxImage=process.argv[3];
assert.ok(installed && path.isAbsolute(installed));
assert.equal(JSON.parse(fs.readFileSync(path.join(installed,'package.json'))).version,'2026.6.33');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-laya-sdk-'));
process.env.OPENCLAW_STATE_DIR=path.join(temporary,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'openclaw.json');
const require=createRequire(path.join(installed,'package.json'));
const keepAlive=setInterval(()=>{},1000);
let container, passed=false,resetHooks=()=>{};
try {
  const {createOpenClawCodingTools,resolveSandboxContext}=await import(pathToFileURL(require.resolve('openclaw/plugin-sdk/agent-harness')));
  const hooks=await import(pathToFileURL(require.resolve('openclaw/plugin-sdk/hook-runtime')));
  resetHooks=hooks.resetGlobalHookRunner;
  const access=createAccessRuntime({directory:path.join(temporary,'admission'),runtimeVersion:'2026.6.33',hooksAllowed:true});
  const admittedHelpers=[];
  hooks.initializeGlobalHookRunner({plugins:[{id:'laya-fixture',status:'loaded'}],hooks:[],typedHooks:[
    {pluginId:'laya-fixture',hookName:'before_tool_call',handler:(event,scope)=>{
      admittedHelpers.push(event.toolCallId);return access.beforeTool(event,scope);
    }},
  ]});
  const workspace=path.join(temporary,'workspace');fs.mkdirSync(workspace);
  fs.mkdirSync(path.join(workspace,'.openclaw','sandbox-skills'),{recursive:true});
  const controlRoot=path.join(workspace,'.cancellation');fs.mkdirSync(controlRoot,{mode:0o700});
  fs.copyFileSync(new URL('../host/cancellable-exec.sh',import.meta.url),path.join(controlRoot,'cancellable-exec.sh'));
  fs.chmodSync(path.join(controlRoot,'cancellable-exec.sh'),0o500);
  const sandbox=sandboxImage ? {mode:'all',scope:'session',workspaceAccess:'rw',docker:{image:sandboxImage,
    containerPrefix:'ods-laya-sdk-',network:'none',readOnlyRoot:true,user:`${process.getuid()}:${process.getgid()}`,
    capDrop:['ALL'],pidsLimit:64,memory:'256m',cpus:1,binds:[`${controlRoot}:/run/pixel-ods-control:ro`]}} : {mode:'off'};
  const config={agents:{defaults:{workspace,sandbox},list:[{id:'pixel',workspace}]},plugins:{enabled:false},
    tools:{profile:'coding',exec:{host:sandboxImage?'sandbox':'gateway',security:'full',ask:'off'}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const control=createExecCancellationControl({root:controlRoot,executionHost:sandboxImage?'sandbox':'gateway'});
  const completedHelpers=[];
  const execution=createLayaBatchExecution({readConfig:()=>config,createTools:createOpenClawCodingTools,
    resolveSandbox:async options=>{const value=await resolveSandboxContext(options);container=value?.containerName;return value;},execControl:()=>control,
    onToolResult:(event,scope)=>{assert.equal(event.toolCallId,scope.toolCallId);completedHelpers.push(event);access.afterTool(event,scope);}});
  const nonce=path.basename(temporary), context={agentId:'pixel',runId:nonce,sessionId:nonce,sessionKey:'agent:pixel:'+nonce,toolCallId:'classify'};
  const factory={...context,workspaceDir:workspace,oneShotCliRun:true};
  const admission=createLayaBatchAdmission();
  const request={source:{path:'tickets.csv',idColumn:'id',textColumn:'text'},outputDirectory:'reports',
    questions:[{id:'category',type:'choice',instructions:'Choose topic',choices:[{id:'billing',description:'payments'},{id:'other',description:'anything else'}]}]};
  fs.writeFileSync(path.join(workspace,'tickets.csv'),'id,text\nA,"Pagamento duplicado, reembolso"\nB,"Refund please"\n');
  const client={decide:async({items})=>({items:items.map(item=>({id:item.id,checkpoint:'english',answers:{category:{
    type:'choice',choice:'billing',probabilities:{billing:0.8,other:0.2},confidence:0.7,answerConfidence:0.8}}}))})};
  const tool=createLayaBatchTool(factory,{admission,execution,resolveClient:()=>client});
  assert.equal(access.admit({},context).outcome,'pass');
  admission.before({toolName:LAYA_BATCH_TOOL,params:request},context);
  const result=await tool.execute('classify',request);
  assert.notEqual(result.isError,true,JSON.stringify(result));
  const summary=JSON.parse(result.content[0].text);
  const csv=fs.readFileSync(path.join(workspace,summary.outputs[0].path),'utf8');
  assert.equal(csv,'id,category,category_confidence,category_answer_confidence\nA,billing,0.7,0.8\nB,billing,0.7,0.8\n');
  assert.equal(summary.rows,2);assert.equal(summary.readbackVerified,true);assert.equal(summary.accuracyVerified,false);
  assert.equal(JSON.parse(fs.readFileSync(path.join(workspace,summary.outputs[1].path))).items.length,2);
  assert.deepEqual(completedHelpers.map(event=>[event.toolName,event.result?.details?.status]),[['exec','completed'],['exec','completed']]);
  assert.notEqual(completedHelpers[0].toolCallId,completedHelpers[1].toolCallId);
  assert.deepEqual(admittedHelpers,completedHelpers.map(event=>event.toolCallId));
  access.finish({},context);
  assert.deepEqual(access.status().activity,{runs:0,tools:0,detached:0});
  const held=access.acquire('a'.repeat(64),access.status().revision);
  assert.equal(held.phase,'held');access.release('a'.repeat(64));
  passed=true;
  console.log(JSON.stringify({kind:'installed-sdk-laya-batch',version:'2026.6.33',host:sandboxImage?'sandbox':'gateway',status:'passed',outputs:summary.outputs}));
} finally {
  resetHooks();
  clearInterval(keepAlive);
  if (container) {assert.match(container,/^ods-laya-sdk-/);const r=spawnSync('docker',['rm','-f',container],{encoding:'utf8'});assert.equal(r.status,0,r.stderr);}
  if(passed)fs.rmSync(temporary,{recursive:true,force:true});
  else console.error('Failed qualification retained at '+temporary);
}
