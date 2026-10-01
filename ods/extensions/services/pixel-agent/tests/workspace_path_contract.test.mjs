import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,mkdirSync,writeFileSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {canonicalWorkspaceParams,extensionlessHtmlWrite,workspaceFileParent,nativeExecWorkdir,malformedRelativeWorkspacePath} from '../plugin/workspace-path-contract.mjs';
import {createToolLoopGuard,createExecCancellationControl} from '../plugin/tool-loop-guard.mjs';
const root='/home/owner/.openclaw/workspace-pixel';
const context={agentId:'pixel',runId:'path-contract',sessionId:'session-path'};

test('missing-one-leading-slash workspace paths are rejected only for selected core file tools',()=>{
  const missing=()=>{throw Object.assign(new Error('missing'),{code:'ENOENT'});};
  const malformed=root.slice(1)+'/calc/compute.py';
  for(const tool of ['read','write','edit']) for(const wrapped of [false,true]) {
    const args={path:malformed};
    const reason=malformedRelativeWorkspacePath(wrapped?'tool_call':tool,wrapped?{id:'openclaw:core:'+tool,args}:args,root,'Compute a sum',missing);
    assert.match(reason,/without its leading slash/);
    assert.match(reason,/workspace-relative path "calc\/compute.py"/);
    assert.equal(args.path,malformed);
  }
  for(const tool of ['exec','apply_patch','pixel_ods_workspace_preview']) assert.equal(malformedRelativeWorkspacePath(tool,{path:malformed},root,'',missing),undefined);
  assert.equal(malformedRelativeWorkspacePath('tool_call',{id:'other:plugin:write',args:{path:malformed}},root,'',missing),undefined);
  for(const value of [root+'/calc/compute.py','/workspace/calc/compute.py','calc/compute.py','home/other/.openclaw/workspace-pixel/calc/compute.py',root.slice(1)+'-other/calc/compute.py','./'+malformed,malformed.replace('/calc/','/../calc/'),malformed+'\0']) {
    assert.equal(malformedRelativeWorkspacePath('write',{path:value},root,'',missing),undefined,value);
  }
});

test('owner-selected literal paths and existing namespaces retain ordinary core behavior',()=>{
  const missing=()=>{throw Object.assign(new Error('missing'),{code:'ENOENT'});};
  const malformed=root.slice(1)+'/calc/compute.py';
  for(const selected of [malformed,root.slice(1)+'/calc',root.slice(1)]) {
    assert.equal(malformedRelativeWorkspacePath('write',{path:malformed},root,`Write exactly in \`${selected}\`.`,missing),undefined);
  }
  // Naming the correct absolute path does not authorize its malformed relative lookalike.
  assert.match(malformedRelativeWorkspacePath('write',{path:malformed},root,`Write ${root}/calc/compute.py.`,missing),/without its leading slash/);
  assert.match(malformedRelativeWorkspacePath('write',{path:malformed},root,`Write ${malformed}-other.`,missing),/without its leading slash/);
  const directory=()=>({isSymbolicLink:()=>false,isDirectory:()=>true});
  for(const tool of ['read','edit']) assert.equal(malformedRelativeWorkspacePath(tool,{path:malformed},root,'',directory),undefined);
  let calls=0;
  assert.equal(malformedRelativeWorkspacePath('edit',{path:malformed},root,'',()=>{calls++;return{isSymbolicLink:()=>true};}),undefined);
  assert.equal(calls,1,'never traverse an existing symbolic link');
  assert.equal(malformedRelativeWorkspacePath('read',{path:malformed},root,'',()=>{throw Object.assign(new Error('denied'),{code:'EACCES'});}),undefined);
  const long=root.slice(1)+'/'+ 'x'.repeat(300);
  const reason=malformedRelativeWorkspacePath('write',{path:long},root,'private unrelated prompt',missing);
  assert.ok(reason.length<300);
  assert.doesNotMatch(reason,/xxx|private unrelated prompt/);
});

test('an earlier misplaced tree never makes new writes under the repeated root look owned',()=>{
  // Laptop fleet run: a 2026-09-23 run created workspace/home/<owner>/.openclaw/workspace-pixel,
  // after which every later misrouted write succeeded into that nested tree.
  const malformed=root.slice(1)+'/Playground/site/index.html';
  let calls=0;
  const existing=()=>{calls++;return{isSymbolicLink:()=>false,isDirectory:()=>true};};
  for(const wrapped of [false,true]) {
    const args={path:malformed};
    const reason=malformedRelativeWorkspacePath(wrapped?'tool_call':'write',wrapped?{id:'openclaw:core:write',args}:args,root,'Build a site',existing);
    assert.match(reason,/Nothing was written or edited/);
    assert.match(reason,/workspace-relative path "Playground\/site\/index.html"/);
    assert.equal(args.path,malformed);
  }
  assert.equal(calls,0,'a write never consults the misplaced tree');
  // Recovery of already misplaced files stays possible.
  assert.equal(malformedRelativeWorkspacePath('read',{path:malformed},root,'',existing),undefined);
  assert.equal(malformedRelativeWorkspacePath('edit',{path:malformed},root,'',existing),undefined);
  // An owner who names the literal path keeps ordinary behavior.
  assert.equal(malformedRelativeWorkspacePath('write',{path:malformed},root,`Write exactly ${malformed}`,existing),undefined);
});

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

test('native cwd translation is limited to POSIX gateway execution',()=>{
  for (const [executionHost,platform] of [['sandbox','darwin'],['sandbox','linux'],['gateway','win32']]) {
    assert.equal(createExecCancellationControl({executionHost,platform}).resolveWorkdir('/workspace',undefined),undefined);
  }
  for (const platform of ['darwin','linux'])
    assert.equal(createExecCancellationControl({executionHost:'gateway',platform}).resolveWorkdir('/workspace',undefined).block,true);
});

test('Linux Full Access resolves normalized project cwd and rejects missing cwd before execution',t=>{
  const actual=mkdtempSync(path.join(tmpdir(),'pixel linux gateway cwd '));
  t.after(()=>rmSync(actual,{recursive:true,force:true}));
  const directory='Playground/sales-report';
  mkdirSync(path.join(actual,directory),{recursive:true});
  writeFileSync(path.join(actual,directory,'vendas.csv'),'item,total\nbook,12.50\n');
  const control=createExecCancellationControl({executionHost:'gateway',platform:'linux'});
  for (const tool of ['exec','tool_call']) {
    let prepared;
    const guard=createToolLoopGuard({execControl:{
      resolveWorkdir:control.resolveWorkdir,
      prepare:(_run,command)=>{prepared=command;return command;},
    }});
    guard.observeRun(context,'pixel',{prompt:'Check the existing project files.'},
      {workspaceRoot:actual,executionHost:'gateway'});
    // The model used a correct relative workdir in the real failure. Earlier
    // normalization adds /workspace; the final gate must translate that alias
    // for the selected execution host before it reaches core exec.
    const args={command:'sha256sum vendas.csv',workdir:directory};
    const decision=guard.beforeToolCall({toolName:tool,
      params:tool==='exec'?args:{id:'openclaw:core:exec',args}},context);
    assert.notEqual(decision?.block,true);
    const executed=tool==='exec'?decision.params:decision.params.args;
    assert.equal(executed.workdir,path.join(actual,directory));
    assert.equal(executed.command,args.command);
    assert.equal(prepared,args.command);
    assert.equal(args.workdir,directory,'do not mutate the model request');
    const bytes=execFileSync(process.execPath,['-e','process.stdout.write(require("node:fs").readFileSync("vendas.csv"))'],
      {cwd:executed.workdir,encoding:'utf8'});
    assert.equal(bytes,'item,total\nbook,12.50\n');
    prepared=undefined;
    const missing={command:'printf must-not-execute',workdir:directory+'/missing'};
    const blocked=guard.beforeToolCall({toolName:tool,
      params:tool==='exec'?missing:{id:'openclaw:core:exec',args:missing}},context);
    assert.equal(blocked?.block,true);
    assert.match(blocked.blockReason,/Nothing was executed/);
    assert.equal(prepared,undefined,'missing cwd is refused before command preparation');
  }
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
  assert.match(entry,/const executionHost = executionHostForAgent\(api.config, AGENT_ID\)/);
  assert.match(entry,/observeRun\(context, AGENT_ID, event, \{ privateBrowserAccess, workspaceRoot, executionHost \}\)/);
  assert.match(entry,/const ownerEvent = toolLoopGuard\.ownerIntentEventForRun\(context\?\.runId \?\? event\?\.runId, event\)/);
  assert.match(entry,/promptContractForAgent\(context, AGENT_ID, ownerEvent, \{[^}]*executionHost,/);
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

test('framework entry reads qualify relative previews only inside the configured workspace',()=>{
  const macRoot='/Users/test/ods/data/pixel-native/workspace';
  for (const [file, allowed] of [[macRoot+'/demo/index.html',true],
    [macRoot+'-other/demo/index.html',false], ['/tmp/demo/index.html',false]]) {
    const guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',{prompt:'Create and publish a new React website in demo.'},{workspaceRoot:macRoot});
    guard.afterToolCall({toolName:'read',params:{path:file},
      result:{content:[{type:'text',text:'<!doctype html><html><body>Test</body></html>'}]}},context);
    const result=guard.beforeToolCall({toolName:'pixel_ods_workspace_preview',
      params:{relativeDirectory:'demo'}},context);
    assert.equal(result?.block===true,!allowed,file);
  }
});

test('a read cannot stand in for authoring a requested new static entry',()=>{
  const guard=createToolLoopGuard();
  // Write-first authorship applies only when the owner requests a static
  // implementation with an unambiguous output path; a bare "in <name>" may
  // name a framework, so that ambiguity retains normal inspection/builds.
  guard.observeRun(context,'pixel',{prompt:'Create and publish a new static HTML website at demo/index.html.'},{workspaceRoot:root});
  guard.afterToolCall({toolName:'read',params:{path:root+'/demo/index.html'},
    result:{content:[{type:'text',text:'<!doctype html><html><body>Old</body></html>'}]}},context);
  assert.equal(guard.beforeToolCall({toolName:'pixel_ods_workspace_preview',
    params:{relativeDirectory:'demo'}},context).block,true);
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
