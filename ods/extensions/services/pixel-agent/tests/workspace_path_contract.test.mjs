import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,mkdirSync,writeFileSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {canonicalWorkspaceParams,extensionlessHtmlWrite,workspaceFileParent,nativeExecWorkdir} from '../plugin/workspace-path-contract.mjs';
import {createToolLoopGuard,createExecCancellationControl} from '../plugin/tool-loop-guard.mjs';
const root='/home/owner/.openclaw/workspace-pixel';
const context={agentId:'pixel',runId:'path-contract',sessionId:'session-path'};

test('native exec selects the configured workspace without altering command text',t=>{
  const actual=mkdtempSync(path.join(tmpdir(),'pixel native cwd '));
  t.after(()=>rmSync(actual,{recursive:true,force:true}));
  mkdirSync(path.join(actual,'project'));
  writeFileSync(path.join(actual,'plain.txt'),'x');
  for (const alias of [undefined,'.','/workspace','workspace']) {
    assert.equal(nativeExecWorkdir(alias,actual).workdir,actual);
  }
  for (const alias of ['project','workspace/project','/workspace/project',path.join(actual,'project')]) {
    const selected=nativeExecWorkdir(alias,actual);
    assert.equal(selected.workdir,path.join(actual,'project'));
    const output=execFileSync(process.execPath,['-e','process.stdout.write(require("node:fs").realpathSync(process.cwd()))'],{cwd:selected.workdir,encoding:'utf8'});
    assert.equal(output,realpathSync(selected.workdir));
  }
  for (const alias of ['/workspace/missing','plain.txt','/workspace/../escape','../escape',null,'',42]) {
    assert.equal(nativeExecWorkdir(alias,actual).block,true,String(alias));
  }
  assert.equal(nativeExecWorkdir('.',undefined).block,true);
  assert.equal(nativeExecWorkdir('.', '/').block,true);
  assert.equal(nativeExecWorkdir(actual,actual).workdir,actual);

  for (const tool of ['exec','tool_call']) {
    let command;
    const guard=createToolLoopGuard({execControl:{
      resolveWorkdir:nativeExecWorkdir,
      prepare:(_run,text)=>{command=text;return 'wrapped-command';},
    }});
    guard.observeRun(context,'pixel',{prompt:'Run pwd.'},{workspaceRoot:actual});
    const args={command:'pwd',workdir:'/workspace/project'};
    const decision=guard.beforeToolCall({toolName:tool,params:tool==='exec'?args:{id:'openclaw:core:exec',args}},context);
    assert.notEqual(decision?.block,true);
    const result=tool==='exec'?decision.params:decision.params.args;
    assert.equal(result.workdir,path.join(actual,'project'));
    assert.equal(command,'pwd');
    assert.equal(args.workdir,'/workspace/project');
    command=undefined;
    const missing={command:'pwd',workdir:'/workspace/missing'};
    const blocked=guard.beforeToolCall({toolName:tool,params:tool==='exec'?missing:{id:'exec',args:missing}},context);
    assert.equal(blocked.block,true);
    assert.equal(command,undefined);
  }
});

test('native cwd translation is limited to macOS gateway execution',()=>{
  for (const [executionHost,platform] of [['sandbox','darwin'],['sandbox','linux'],['gateway','linux'],['gateway','win32']]) {
    assert.equal(createExecCancellationControl({executionHost,platform}).resolveWorkdir('/workspace',undefined),undefined);
  }
  assert.equal(createExecCancellationControl({executionHost:'gateway',platform:'darwin'}).resolveWorkdir('/workspace',undefined).block,true);
});

test('plugin passes inherited workspace to evidence tracking for absolute macOS paths',()=>{
  const entry=readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const declaration=entry.match(/const workspaceRoot = ([\s\S]*?);/);
  assert.ok(declaration);
  const resolve=new Function('api','AGENT_ID',`return (${declaration[1]});`);
  const macRoot='/Users/test/ods/data/pixel-native/workspace';
  const config={agents:{defaults:{workspace:macRoot},list:[{id:'pixel'}]}};
  assert.equal(resolve({config},'pixel'),macRoot);
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'Create and publish a website.'},
    {workspaceRoot:resolve({config},'pixel')});
  guard.afterToolCall({toolName:'write',params:{path:macRoot+'/demo/index.html',content:'<html></html>'},
    result:{content:[{type:'text',text:'Successfully wrote file'}]}},context);
  const decision=guard.beforeToolCall({toolName:'pixel_ods_workspace_preview',
    params:{relativeDirectory:'demo'}},context);
  assert.notEqual(decision?.block,true);
  config.agents.list[0].workspace='/custom/pixel';
  assert.equal(resolve({config},'pixel'),'/custom/pixel');
  assert.match(entry,/observeRun\(context, AGENT_ID, event, \{ privateBrowserAccess, workspaceRoot, executionHost: executionHostForAgent\(api.config, AGENT_ID\) \}\)/);
});

test('detects existing file parents without following links or escaping the workspace',()=>{
  const file=()=>({isSymbolicLink:()=>false,isFile:()=>true,isDirectory:()=>false});
  assert.equal(workspaceFileParent('write',{path:'marketing/index.html'},root,file),'marketing');
  const link=()=>({isSymbolicLink:()=>true});
  assert.equal(workspaceFileParent('write',{path:'linked/index.html'},root,link),undefined);
  for(const target of ['../outside/index.html','/etc/index.html','a/../b/index.html']) {
    assert.equal(workspaceFileParent('write',{path:target},root,()=>{throw new Error('must not inspect');}),undefined);
  }
  assert.equal(workspaceFileParent('write',{path:'fresh/index.html'},root,()=>{throw new Error('ENOENT');}),undefined);
});

test('only the trusted configured root maps to a workspace-relative path',()=>{
  assert.equal(canonicalWorkspaceParams('write',{path:root+'/demo/index.html'},root).path,'demo/index.html');
  for(const path of ['/etc/passwd',root+'-other/index.html','../outside/index.html']) {
    assert.equal(canonicalWorkspaceParams('write',{path},root).path,path);
  }
  assert.equal(canonicalWorkspaceParams('write',{path:root+'/../outside'},root).path,'../outside');
  assert.equal(canonicalWorkspaceParams('write',{path:root+'/demo'},undefined).path,root+'/demo');
  assert.equal(canonicalWorkspaceParams('other',{path:root+'/demo'},root).path,root+'/demo');
  const other={id:'third-party:write',args:{path:root+'/demo'}};
  assert.deepEqual(canonicalWorkspaceParams('tool_call',other,root),other);
});

test('absolute reads qualify relative previews only inside the configured workspace',()=>{
  const macRoot='/Users/test/ods/data/pixel-native/workspace';
  for (const [file, allowed] of [[macRoot+'/demo/index.html',true],
    [macRoot+'-other/demo/index.html',false], ['/tmp/demo/index.html',false]]) {
    const guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',{prompt:'Create and publish a new website in demo.'},{workspaceRoot:macRoot});
    guard.afterToolCall({toolName:'read',params:{path:file},
      result:{content:[{type:'text',text:'<!doctype html><html><body>Test</body></html>'}]}},context);
    const result=guard.beforeToolCall({toolName:'pixel_ods_workspace_preview',
      params:{relativeDirectory:'demo'}},context);
    assert.equal(result?.block===true,!allowed,file);
  }
});

test('preview path alias is exact and cannot silently replace conflicting fields',()=>{
  assert.deepEqual(canonicalWorkspaceParams('tool_call',{id:'pixel_ods_workspace_preview',args:{path:root+'/demo'}},root),{id:'pixel_ods_workspace_preview',args:{relativeDirectory:'demo'}});
  assert.deepEqual(canonicalWorkspaceParams('pixel_ods_workspace_preview',{path:'one',relativeDirectory:'two'},root),{path:'one',relativeDirectory:'two'});
});

test('HTML cannot accidentally occupy the project directory before publication',()=>{
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'crie um site em /workspace/marketing-digital e abra pra eu ver, tipo um site de marketing digital'},{workspaceRoot:root});
  const params={id:'write',args:{path:root+'/marketing-digital',content:'<!DOCTYPE html>\n<html><title>Marketing</title></html>'}};
  const blocked=guard.beforeToolCall({toolName:'tool_call',params},context);
  assert.equal(blocked.block,true);
  assert.match(blocked.blockReason,/path names a FILE/);
  const fixed={...params,args:{...params.args,path:root+'/marketing-digital/index.html'}};
  const decision=guard.beforeToolCall({toolName:'tool_call',params:fixed},context);
  assert.notEqual(decision?.block,true);
  assert.equal(decision.params.args.path,'marketing-digital/index.html');
  guard.afterToolCall({toolName:'write',params:fixed.args,result:{details:{status:'completed'}}},context);
  const preview=guard.beforeToolCall({toolName:'tool_call',params:{id:'pixel_ods_workspace_preview',args:{path:root+'/marketing-digital'}}},context);
  assert.notEqual(preview?.block,true);
  assert.equal(preview.params.args.relativeDirectory,'marketing-digital');
  assert.equal(guard.beforeToolCall({toolName:'tool_call',params:{id:'pixel_ods_workspace_preview',args:{path:'wrong',relativeDirectory:'marketing-digital'}}},context).block,true);
});

test('ordinary extensionless text files and explicitly named HTML files are unaffected',()=>{
  assert.equal(extensionlessHtmlWrite('write',{path:'LICENSE',content:'license text'}),false);
  assert.equal(extensionlessHtmlWrite('write',{path:'index.html',content:'<html></html>'}),false);
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'Write a text file called LICENSE.'},{workspaceRoot:root});
  assert.notEqual(guard.beforeToolCall({toolName:'write',params:{path:'LICENSE',content:'<html></html>'}},context)?.block,true);
});
