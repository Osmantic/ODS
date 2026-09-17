import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createWorkspaceProjects,workspaceMutationFiles} from '../plugin/workspace-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createTaskActivity} from '../plugin/task-activity.mjs';
import {parseTaskActivity} from '../host/task_activity_schema.mjs';

const sessionKey=`agent:pixel:openai-user:ods-${'a'.repeat(64)}`;
const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
const observedAt='2026-09-16T10:00:00.000Z';
function fixture(t, options={}) {
  const base=fs.mkdtempSync(path.join(os.tmpdir(),'ods-project-receipt-'));
  t.after(()=>fs.rmSync(base,{recursive:true,force:true}));
  const workspaceRoot=path.join(base,'workspace'),stateDir=path.join(base,'private');
  fs.mkdirSync(workspaceRoot);
  const settings={stateDir,now:()=>observedAt,...options};
  const registry=createWorkspaceProjects(settings);
  const write=(file,body='{}')=>{const target=path.join(workspaceRoot,file);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,body);return target;};
  const record=(file,overrides={})=>registry.record({sessionKey,workspaceRoot,file,kind:'write',...overrides});
  return {base,workspaceRoot,stateDir,settings,registry,write,record};
}

test('requires a real file before recording, survives restart, and isolates sessions',t=>{
  const f=fixture(t),file='Playground/http-method-smoke/summary.json';
  assert.equal(f.record(file),false);
  fs.mkdirSync(path.join(f.workspaceRoot,'Playground/http-method-smoke'),{recursive:true});
  assert.equal(f.record(file),false);
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
  const target=f.write(file);
  assert.equal(f.record(file),true);
  const receipt={schemaVersion:1,kind:'ods-workspace-project',relativeDirectory:'Playground/http-method-smoke',observedAt};
  assert.deepEqual(createWorkspaceProjects(f.settings).forSession(sessionKey),[receipt]);
  assert.deepEqual(f.registry.forSession(sessionKey.replace('a'.repeat(64),'b'.repeat(64))),[]);
  assert.doesNotMatch(JSON.stringify(receipt),/workspaceRoot|summary.json|session|private/);
  const saved=fs.readdirSync(f.stateDir);assert.equal(saved.length,1);assert.match(saved[0],/^[a-f0-9]{64}\.json$/);
  assert.doesNotMatch(fs.readFileSync(path.join(f.stateDir,saved[0]),'utf8'),/openai-user|ods-aaaa/);
  fs.unlinkSync(target);
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
});

test('respects a portable configured OpenClaw state directory',t=>{
  const f=fixture(t),prior=process.env.OPENCLAW_STATE_DIR;
  process.env.OPENCLAW_STATE_DIR=path.join(f.base,'custom-state');
  t.after(()=>{if(prior===undefined)delete process.env.OPENCLAW_STATE_DIR;else process.env.OPENCLAW_STATE_DIR=prior;});
  f.write('Playground/custom/main.py');
  const registry=createWorkspaceProjects({now:()=>observedAt});
  assert.equal(registry.record({sessionKey,workspaceRoot:f.workspaceRoot,file:'Playground/custom/main.py',kind:'write'}),true);
  assert.equal(fs.readdirSync(path.join(process.env.OPENCLAW_STATE_DIR,'.ods-workspace-projects')).length,1);
  assert.equal(registry.forSession(sessionKey)[0].relativeDirectory,'Playground/custom');
});

test('rejects traversal, reserved device names and non-project paths',t=>{
  const f=fixture(t);f.write('Playground/real/main.py');
  for(const file of ['Playground/real/../real/main.py','Playground/../real/main.py','/etc/passwd','main.py',
    'Playground/CON/main.py','Playground/LPT1.txt/main.py','Playground/name./main.py','Playground//real/main.py',
    'Playground/real/main.py:secret',`${f.workspaceRoot}/Playground/real/../real/main.py`]) assert.equal(f.record(file),false,file);
  assert.equal(f.record('/workspace/Playground/real/main.py'),true);
});

test('rejects symlinks, hard links and a linked project registry',t=>{
  const f=fixture(t),outside=path.join(f.base,'elsewhere');fs.mkdirSync(outside);fs.writeFileSync(path.join(outside,'data.txt'),'data');
  fs.mkdirSync(path.join(f.workspaceRoot,'Playground'));
  fs.symlinkSync(outside,path.join(f.workspaceRoot,'Playground/linked'),'junction');
  assert.equal(f.record('Playground/linked/data.txt'),false);
  f.write('Playground/real/main.txt');
  fs.linkSync(path.join(outside,'data.txt'),path.join(f.workspaceRoot,'Playground/real/hard.txt'));
  assert.equal(f.record('Playground/real/hard.txt'),false);
  fs.symlinkSync(outside,f.stateDir,'junction');
  assert.equal(f.record('Playground/real/main.txt'),false);
  assert.deepEqual(fs.readdirSync(outside),['data.txt']);
});

test('will not overwrite an unsafe session record and bounds durable bindings',t=>{
  const f=fixture(t,{maximumSessions:1});
  for(let i=0;i<10;i++){const file=`Playground/project-${i}/main.txt`;f.write(file);assert.equal(f.record(file),true);}
  assert.equal(f.registry.forSession(sessionKey).length,8);
  assert.equal(f.registry.forSession(sessionKey)[0].relativeDirectory,'Playground/project-9');
  assert.equal(f.record('Playground/project-9/main.txt',{sessionKey:sessionKey.replace('a'.repeat(64),'b'.repeat(64))}),false);
  const record=path.join(f.stateDir,`${createHash('sha256').update(sessionKey).digest('hex')}.json`);
  fs.writeFileSync(record,'invalid');
  assert.equal(f.record('Playground/project-9/main.txt'),false);
  assert.equal(fs.readFileSync(record,'utf8'),'invalid');
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
});

test('guard excludes failed writes and emits explicit MDN file-only project after actual successful mutation',t=>{
  const f=fixture(t),context={agentId:'pixel',runId,sessionId:'native-session',sessionKey};
  const guard=createToolLoopGuard({onWorkspaceMutation:f.registry.record});
  const file='Playground/http-method-smoke-20260916/summary.json';
  guard.observeRun(context,'pixel',{prompt:`Write the JSON summary to ${file}.`},{workspaceRoot:f.workspaceRoot});
  f.write(file,'old');
  const failed={toolName:'write',toolCallId:'failed',params:{path:file,content:'new'}};
  assert.notEqual(guard.beforeToolCall(failed,context)?.block,true);
  guard.afterToolCall({...failed,result:{isError:true,content:[{type:'text',text:'Failed'}]}},context);
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
  const succeeded={toolName:'write',toolCallId:'succeeded',params:{path:file,content:'{"method":"POST"}'}};
  assert.notEqual(guard.beforeToolCall(succeeded,context)?.block,true);
  f.write(file,succeeded.params.content);
  guard.afterToolCall({...succeeded,result:{content:[{type:'text',text:'File written'}]}},context);
  assert.equal(f.registry.forSession(sessionKey)[0]?.relativeDirectory,'Playground/http-method-smoke-20260916');
  const activity=createTaskActivity({now:()=>observedAt,projectsForSession:f.registry.forSession});
  activity.begin({},context);activity.finish({success:true},context);
  const projected=activity.projection(runId);
  assert.equal(projected.schemaVersion,4);
  assert.equal(parseTaskActivity(projected,runId),projected);
  assert.equal(projected.projects[0].relativeDirectory,'Playground/http-method-smoke-20260916');
});

test('canonical rewritten Tool Search writes bind the actual project without exposing the prompt',t=>{
  const f=fixture(t),context={agentId:'pixel',runId,sessionId:'native-session',sessionKey};
  const guard=createToolLoopGuard({onWorkspaceMutation:f.registry.record});
  guard.observeRun(context,'pixel',{prompt:'Build a weather tool in a descriptive project folder.'},{workspaceRoot:f.workspaceRoot});
  const event={toolName:'tool_call',toolCallId:'write-one',params:{id:'write',args:{path:'weather-tool/main.py',content:'print(1)'}}};
  const decision=guard.beforeToolCall(event,context);
  assert.notEqual(decision?.block,true,decision?.blockReason);
  assert.equal(decision.params.args.path,'Playground/weather-tool/main.py');
  f.write(decision.params.args.path,decision.params.args.content);
  guard.afterToolCall({...event,params:decision.params,result:{details:{tool:{id:'openclaw:core:write',source:'openclaw',sourceName:'core',name:'write'},result:{content:[{type:'text',text:'File written'}]}}}},context);
  assert.equal(f.registry.forSession(sessionKey)[0]?.relativeDirectory,'Playground/weather-tool');
});

test('successful patch headers bind existing final paths; failed patches and shell prose never do',t=>{
  const f=fixture(t),context={agentId:'pixel',runId,sessionId:'native-session',sessionKey};
  const guard=createToolLoopGuard({onWorkspaceMutation:f.registry.record});
  guard.observeRun(context,'pixel',{prompt:'Apply the requested patch to Playground/patch-tool/main.py.'},{workspaceRoot:f.workspaceRoot});
  const input='*** Begin Patch\n*** Add File: Playground/patch-tool/main.py\n+print(1)\n+*** Add File: Playground/forged/main.py\n*** End Patch';
  f.write('Playground/patch-tool/main.py');f.write('Playground/forged/main.py');
  const event={toolName:'apply_patch',toolCallId:'patch-one',params:{input}};
  assert.notEqual(guard.beforeToolCall(event,context)?.block,true);
  guard.afterToolCall({...event,result:{isError:true,content:[{type:'text',text:'Failed'}]}},context);
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
  guard.afterToolCall({...event,result:{content:[{type:'text',text:'Success. Updated the following files.'}]}},context);
  assert.deepEqual(f.registry.forSession(sessionKey).map(item=>item.relativeDirectory),['Playground/patch-tool']);
  assert.deepEqual(workspaceMutationFiles('exec',{command:'touch Playground/forged/main.py'}),[]);
  assert.deepEqual(workspaceMutationFiles('apply_patch',{input:'*** Begin Patch\n*** Update File: Playground/old/main.py\n*** Move to: Playground/new/main.py\n@@\n-a\n+b\n*** Delete File: Playground/gone/main.py\n*** End Patch'}),['Playground/new/main.py']);
});

for(const [name,workdir,result,populated,expected] of [
  ['successful explicit directory','Playground/exec-tool',{details:{exitCode:0,status:'completed'}},true,true],
  ['failed execution','Playground/exec-tool',{isError:true,details:{exitCode:1}},true,false],
  ['missing terminal status','Playground/exec-tool',{content:[{type:'text',text:'done'}]},true,false],
  ['empty directory','Playground/exec-tool',{details:{exitCode:0}},false,false],
  ['workspace root','/workspace',{details:{exitCode:0}},true,false],
  ['default directory',undefined,{details:{exitCode:0}},true,false],
]) test(`exec project association: ${name}`,t=>{
  const f=fixture(t),context={agentId:'pixel',runId,sessionId:'native-session',sessionKey};
  fs.mkdirSync(path.join(f.workspaceRoot,'Playground/exec-tool'),{recursive:true});
  if(populated) f.write('Playground/exec-tool/src/main.py');
  const guard=createToolLoopGuard({onWorkspaceMutation:f.registry.record});
  guard.observeRun(context,'pixel',{prompt:'Inspect Playground/exec-tool.'},{workspaceRoot:f.workspaceRoot});
  const event={toolName:'exec',toolCallId:'inspect-one',params:{command:'pwd',...(workdir===undefined?{}:{workdir})}};
  const decision=guard.beforeToolCall(event,context);assert.notEqual(decision?.block,true,decision?.blockReason);
  guard.afterToolCall({...event,params:decision?.params??event.params,result},context);
  assert.equal(f.registry.forSession(sessionKey).length,expected?1:0);
});

test('background exec association waits for its own successful terminal receipt',t=>{
  const f=fixture(t),context={agentId:'pixel',runId,sessionId:'native-session',sessionKey};
  f.write('Playground/exec-tool/main.py');
  const guard=createToolLoopGuard({onWorkspaceMutation:f.registry.record});
  guard.observeRun(context,'pixel',{prompt:'Inspect Playground/exec-tool.'},{workspaceRoot:f.workspaceRoot});
  const event={toolName:'exec',toolCallId:'background-one',params:{command:'python main.py',workdir:'Playground/exec-tool'}};
  const decision=guard.beforeToolCall(event,context);assert.notEqual(decision?.block,true,decision?.blockReason);
  guard.afterToolCall({...event,params:decision?.params??event.params,result:{details:{status:'running',sessionId:'background'}}},context);
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
  const process={toolName:'process',toolCallId:'poll-one',params:{action:'poll',sessionId:'other'}};
  guard.afterToolCall({...process,result:{details:{status:'completed',sessionId:'background',exitCode:0}}},context);
  assert.deepEqual(f.registry.forSession(sessionKey),[]);
  guard.afterToolCall({...process,params:{action:'poll',sessionId:'background'},result:{details:{status:'completed',sessionId:'background',exitCode:0}}},context);
  assert.equal(f.registry.forSession(sessionKey)[0]?.relativeDirectory,'Playground/exec-tool');
});

test('directory associations reject links and keep an existing project after one file is removed',t=>{
  const f=fixture(t);const first=f.write('Playground/keep/main.py');f.write('Playground/keep/README.md');
  assert.equal(f.record('Playground/keep/main.py'),true);fs.unlinkSync(first);
  assert.equal(f.registry.forSession(sessionKey)[0]?.relativeDirectory,'Playground/keep');
  const outside=path.join(f.base,'outside');fs.mkdirSync(outside);fs.writeFileSync(path.join(outside,'readme'),'x');
  fs.symlinkSync(outside,path.join(f.workspaceRoot,'Playground/linked'),'junction');
  assert.equal(f.registry.record({sessionKey,workspaceRoot:f.workspaceRoot,directory:'Playground/linked',kind:'exec'}),false);
});

test('closed v4 host projection rejects forged, duplicated or malformed projects and preserves v3 compatibility',()=>{
  const receipt={schemaVersion:1,kind:'ods-workspace-project',relativeDirectory:'Playground/tool',observedAt};
  const activity=createTaskActivity({now:()=>observedAt,projectsForSession:()=>[receipt]});activity.begin({},{agentId:'pixel',runId});activity.finish({success:true},{agentId:'pixel',runId});
  const value=activity.projection(runId);
  for(const projects of [[{...receipt,relativeDirectory:'Playground/../escape'}],[{...receipt,relativeDirectory:'Playground/CON'}],
    [{...receipt,source:'model'}],[{...receipt,observedAt:'2026-09-17T00:00:00.000Z'}],[receipt,receipt],Array(9).fill(receipt)])
    assert.equal(parseTaskActivity({...value,projects},runId),null);
  const {projects,...old}=value;
  assert.ok(parseTaskActivity({...old,schemaVersion:3},runId));
});

test('late trusted session metadata restores receipts without permitting cross-chat rebinding',t=>{
  const f=fixture(t),file='Playground/late-key/main.py';f.write(file);f.record(file);
  const activity=createTaskActivity({now:()=>observedAt,projectsForSession:f.registry.forSession});
  const early={agentId:'pixel',runId};activity.begin({},early);
  assert.deepEqual(activity.projection(runId).projects,[]);
  const trusted={...early,sessionKey};
  const event={toolName:'read',toolCallId:'one',params:{path:file}};
  activity.before(event,trusted);activity.after({...event,result:{}},trusted);
  assert.equal(activity.activeForUser(sessionKey.split(':').at(-1)).projects[0].relativeDirectory,'Playground/late-key');
  activity.begin({},{...trusted,sessionKey:sessionKey.replace('a'.repeat(64),'b'.repeat(64))});
  activity.finish({success:true},trusted);
  assert.deepEqual(activity.projection(runId).projects,[]);
});
