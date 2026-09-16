import test from 'node:test';
import assert from 'node:assert/strict';
import {canonicalWorkspaceParams,extensionlessHtmlWrite,workspaceFileParent} from '../plugin/workspace-path-contract.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
const root='/home/owner/.openclaw/workspace-pixel';
const context={agentId:'pixel',runId:'path-contract',sessionId:'session-path'};

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

test('preview path alias is exact and cannot silently replace conflicting fields',()=>{
  assert.deepEqual(canonicalWorkspaceParams('tool_call',{id:'pixel_ods_workspace_preview',args:{path:root+'/demo'}},root),{id:'pixel_ods_workspace_preview',args:{relativeDirectory:'demo'}});
  assert.deepEqual(canonicalWorkspaceParams('pixel_ods_workspace_preview',{path:'one',relativeDirectory:'two'},root),{path:'one',relativeDirectory:'two'});
});

test('HTML cannot accidentally occupy the project directory before publication',()=>{
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{prompt:'crie um site e abra pra eu ver, tpio u msite de marketing digital'},{workspaceRoot:root});
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
