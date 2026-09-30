import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import {subagentCwd,withPixelSubagentWorkspace} from '../plugin/subagent-workspace.mjs';
import {withPixelCronDeliveryDefault} from '../plugin/cron-delivery-default.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const source = fs.readFileSync(process.env.PIXEL_PLUGIN_ENTRY ?? new URL('../plugin/index.js',import.meta.url),'utf8');
const start=source.indexOf('    api.on("before_tool_call",');
const end=source.indexOf('    api.on("after_tool_call",',start);
assert.ok(start>=0 && end>start);
function fixture(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-subagent-cwd-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const workspace=path.join(root,'workspace'), project=path.join(workspace,'Playground','aurora');
  fs.mkdirSync(project,{recursive:true});fs.writeFileSync(path.join(project,'app.js'),'export const exact = 17;\n');
  const config={agents:{list:[{id:'pixel',workspace,sandbox:{mode:'off'}}]}};
  const ctx={agentId:'pixel',sessionKey:'agent:pixel:subagent:fixture',sessionId:'child-session',runId:'child-run'};
  const session={sessionId:ctx.sessionId,spawnedCwd:project};
  const guard=createToolLoopGuard();
  guard.observeRun(ctx,'pixel',{prompt:'Review existing code only.'},{workspaceRoot:workspace,executionHost:'gateway'});
  const callbacks={};let admitted=0;
  const runtime={isProbe:()=>false,beforeTool:()=>{admitted++;}};
  vm.runInNewContext(source.slice(start,end),{
    api:{config,on:(name,callback)=>{callbacks[name]=callback;}},
    AGENT_ID:'pixel',toolLoopGuard:guard,accessRuntime:runtime,
    goalProgress:{before(){}},bundleAdmission:{before(){}},taskActivity:{before(){}},
    withPixelCronDeliveryDefault,withPixelSubagentWorkspace,
    getSessionEntry:scope=>{assert.equal(scope.sessionKey,ctx.sessionKey);return session;},
    resolveStorePath:()=>'/fixture/session-store',
    resolveUserPath:value=>value==='~'?root:value.startsWith('~/')?path.join(root,value.slice(2)):path.resolve(value),
    resolveAgentWorkspaceDir:cfg=>{
      const explicit=cfg.agents.list.find(agent=>agent.id==='pixel').workspace;
      let value=explicit ?? cfg.agents.defaults?.workspace;
      value=value.startsWith('~/')?path.join(root,value.slice(2)):path.resolve(value);
      return !explicit && cfg.agents.list[0].id!=='pixel'?path.join(value,'pixel'):value;
    },
  });
  async function call(name,params,extra={}) {
    return callbacks.before_tool_call({toolName:name,toolCallId:'call-'+Math.random(),params},
      {...ctx,toolName:name,...extra});
  }
  return {root,workspace,project,config,ctx,session,call,runtime,admitted:()=>admitted};
}

test('actual registered hook anchors direct and deferred native spawn cwd before admission',async t=>{
  const f=fixture(t);
  for(const wrapped of [false,true]) {
    const args={runtime:'subagent',cwd:'Playground/aurora',task:'Review app.js',mode:'run'};
    const result=await f.call(wrapped?'tool_call':'sessions_spawn',wrapped?{id:'openclaw:core:sessions_spawn',args}:args,
      {sessionKey:'agent:pixel:main'});
    assert.equal(result?.block,undefined,result?.blockReason);
    const emitted=wrapped?result.params.args:result.params;
    assert.equal(emitted.cwd,f.project);
    assert.equal(emitted.task,args.task);
    assert.equal(args.cwd,'Playground/aurora');
  }
  assert.equal(f.admitted(),2);
});

test('invalid native spawn cwd refuses before tool admission, without fallback',async t=>{
  const f=fixture(t);
  for(const cwd of ['../outside','Playground/missing',path.join(f.project,'app.js'),'','bad\0path']) {
    const result=await f.call('sessions_spawn',{cwd,task:'Review'});
    assert.equal(result.block,true,cwd);assert.match(result.blockReason,/Nothing was started/);
  }
  assert.equal(f.admitted(),0);
});

test('nested spawn uses its exact parent session cwd and preserves native home resolution',async t=>{
  const f=fixture(t);fs.mkdirSync(path.join(f.project,'nested'));
  for(const [cwd,expected] of [['.',f.project],['nested',path.join(f.project,'nested')],['~',f.root],['~/workspace',f.workspace]]) {
    const result=await f.call('sessions_spawn',{cwd,task:'Review'});
    assert.equal(result?.block,undefined,result?.blockReason);
    assert.equal(result.params.cwd,expected);
  }
  f.session.sessionId='replaced-session';
  assert.equal((await f.call('sessions_spawn',{cwd:'.',task:'Review'})).block,true);
});

test('native home-relative configured workspace supports spawn and child reads',async t=>{
  const f=fixture(t);
  f.config.agents.list[0].workspace='~/workspace';
  const spawned=await f.call('sessions_spawn',{cwd:'Playground/aurora',task:'Review'}, {sessionKey:'agent:pixel:main'});
  assert.equal(spawned?.block,undefined,spawned?.blockReason);
  assert.equal(spawned.params.cwd,f.project);
  const read=await f.call('read',{path:'Playground/aurora/app.js'});
  assert.equal(read.params.path,path.join(f.project,'app.js'));
});

test('nondefault agent fallback workspace comes from the native resolver',async t=>{
  const f=fixture(t);
  delete f.config.agents.list[0].workspace;
  f.config.agents.list.unshift({id:'main',default:true});
  f.config.agents.defaults={workspace:'~/workspace'};
  const directory=path.join(f.workspace,'pixel','project');fs.mkdirSync(directory,{recursive:true});
  const spawned=await f.call('sessions_spawn',{cwd:'project',task:'Review'}, {sessionKey:'agent:pixel:main'});
  assert.equal(spawned?.block,undefined,spawned?.blockReason);
  assert.equal(spawned.params.cwd,directory);
});

test('absolute native cwd preserves core authorization, missing absolute paths fail',t=>{
  const f=fixture(t);
  assert.equal(subagentCwd(f.root,f.workspace).cwd,f.root,'do not newly prohibit an explicitly selected host directory');
  assert.equal(subagentCwd(path.join(f.root,'missing'),f.workspace).block,true);
  assert.equal(subagentCwd('.',f.workspace).cwd,f.workspace);
});

test('relative workspace symlink escape is rejected without changing explicit native path semantics',t=>{
  const f=fixture(t);
  fs.symlinkSync(f.root,path.join(f.workspace,'escape'),process.platform==='win32'?'junction':'dir');
  assert.equal(subagentCwd('escape',f.workspace).block,true);
  assert.equal(subagentCwd(path.join(f.workspace,'escape'),f.workspace).cwd,path.join(f.workspace,'escape'));
});

test('Windows drive spelling normalizes without creating drive-relative paths',()=>{
  const root='C:\\Users\\Owner\\Workspace';
  const options={paths:path.win32,stat:()=>({isDirectory:()=>true}),realpath:value=>value};
  assert.equal(subagentCwd('Playground\\aurora',root,options).cwd,root+'\\Playground\\aurora');
  assert.equal(subagentCwd('D:\\Projects',root,options).cwd,'D:\\Projects');
  assert.equal(subagentCwd('C:relative',root,options).block,true);
  assert.equal(subagentCwd('..\\escape',root,options).block,true);
});

test('actual registered hook preserves absolute reads and resolves child-local and exact shared prefix once',async t=>{
  const f=fixture(t);
  for(const name of ['read','write','edit']) for(const wrapped of [false,true]) {
    for(const file of ['app.js',path.join('Playground','aurora','app.js'),path.join(f.project,'app.js')]) {
      const args={path:file,...(name==='write'?{content:'new'}:name==='edit'?{oldText:'17',newText:'18'}:{})};
      const result=await f.call(wrapped?'tool_call':name,wrapped?{id:'openclaw:core:'+name,args}:args);
      assert.equal(result?.block,undefined,result?.blockReason);
      assert.equal((wrapped?result.params.args:result.params).path,path.join(f.project,'app.js'));
    }
  }
});

test('sandbox, ACP, other plugin IDs, unbound session, and guard/admission denials keep precedence',async t=>{
  const f=fixture(t), args={cwd:'Playground/aurora',task:'Review'};
  f.config.agents.list[0].sandbox.mode='all';
  assert.equal(withPixelSubagentWorkspace(undefined,{toolName:'sessions_spawn',params:args},f.ctx,'pixel',f.config),undefined);
  f.config.agents.list[0].sandbox.mode='off';
  assert.equal(withPixelSubagentWorkspace(undefined,{toolName:'sessions_spawn',params:{...args,runtime:'acp'}},f.ctx,'pixel',f.config),undefined);
  assert.equal(withPixelSubagentWorkspace(undefined,{toolName:'tool_call',params:{id:'other:plugin:read',args:{path:'app.js'}}},f.ctx,'pixel',f.config),undefined);
  const denied={block:true,blockReason:'policy refused'};
  assert.equal(withPixelSubagentWorkspace(denied,{toolName:'sessions_spawn',params:args},f.ctx,'pixel',f.config),denied);
  const event={toolName:'read',params:{path:'app.js'}};
  assert.equal(withPixelSubagentWorkspace(undefined,event,f.ctx,'pixel',f.config,()=>({...f.session,sessionId:'foreign'})),undefined);
  assert.equal(withPixelSubagentWorkspace(undefined,event,{...f.ctx,sessionKey:'agent:pixel:main'},'pixel',f.config,()=>{throw Error('must not read');}),undefined);
  f.runtime.beforeTool=()=>denied;
  assert.equal(await f.call('sessions_spawn',args,{sessionKey:'agent:pixel:main'}),denied);
});

test('emitted child file parameters execute with the real pinned native read tool',{
  skip:!process.env.OPENCLAW_PACKAGE,
},async t=>{
  const f=fixture(t), pkg=process.env.OPENCLAW_PACKAGE;
  assert.equal(JSON.parse(fs.readFileSync(path.join(pkg,'package.json'))).version,'2026.6.33');
  const {resolveUserPath}=await import(pathToFileURL(path.join(pkg,'dist/plugin-sdk/agent-harness-runtime.js')));
  const {resolveAgentWorkspaceDir}=await import(pathToFileURL(path.join(pkg,'dist/plugin-sdk/agent-runtime.js')));
  assert.equal(resolveAgentWorkspaceDir({agents:{list:[{id:'main',default:true},{id:'pixel'}],defaults:{workspace:f.workspace}}},'pixel'),path.join(f.workspace,'pixel'));
  assert.equal(resolveAgentWorkspaceDir({agents:{list:[{id:'pixel',workspace:'~/ods-fixture'}]}},'pixel'),resolveUserPath('~/ods-fixture'));
  assert.equal(subagentCwd('~',f.workspace,{resolveUserPath}).cwd,resolveUserPath('~'));
  const {V:createReadTool}=await import(pathToFileURL(path.join(pkg,'dist/sessions-CZbwb3_c.js')));
  const read=createReadTool(f.project);
  for(const file of ['app.js',path.join('Playground','aurora','app.js'),path.join(f.project,'app.js')]) {
    const result=await f.call('read',{path:file});
    assert.equal(result?.block,undefined,result?.blockReason);
    const receipt=await read.execute('real-native-read',result?.params??{path:file});
    assert.equal(receipt.isError,undefined);
    assert.ok(receipt.content.some(item=>item.type==='text'&&item.text.includes('export const exact = 17;')));
  }
});
