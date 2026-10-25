import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {routePlaygroundTool} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {nativeExecWorkdir} from '../plugin/workspace-path-contract.mjs';

const directory='fleet-json-totals-20261021-0824';
const prompt=`Create a small standard-library Python project in a new folder ${directory}. Provide totals.py, test_totals.py and README.md.`;
function fixture(t,intent=prompt) {
  const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'ods-owner-project-')));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const state={};
  return {root,state,call:(tool,params)=>routePlaygroundTool({state,tool,params,root,session:'owner-project',intent})};
}

test('named-folder retry preserves nested filename, content and owner location',t=>{
  for(const wrapped of [false,true]) for(const prefix of ['/workspace/.openclaw/tmp/','.openclaw/tmp/','Playground/']) {
    const {root,state,call}=fixture(t);
    const write=args=>call(wrapped?'tool_call':'write',wrapped?{id:'openclaw:core:write',args}:args);
    const args={path:`${prefix}${directory}/src/totals.py`,content:'print(42)'};
    const denied=write(args);
    assert.equal(denied.block,true);
    assert.ok(denied.blockReason.includes(JSON.stringify(`${directory}/src/totals.py`)));
    assert.equal(denied.params,undefined);
    assert.equal(args.path,`${prefix}${directory}/src/totals.py`);
    const corrected={path:`${directory}/src/totals.py`,content:args.content};
    assert.equal(write(corrected),undefined);
    assert.equal(state.binding,null);
    fs.mkdirSync(path.dirname(path.join(root,corrected.path)),{recursive:true});
    fs.writeFileSync(path.join(root,corrected.path),corrected.content);
    assert.equal(fs.readFileSync(path.join(root,directory,'src/totals.py'),'utf8'),'print(42)');
    assert.equal(fs.existsSync(path.join(root,'Playground')),false);
    assert.equal(fs.existsSync(path.join(root,'.openclaw')),false);
  }
});

test('simple misplaced mkdir and workdir get a truthful correction, never shell rewriting',t=>{
  const {call}=fixture(t);
  for(const command of [`mkdir -p /workspace/.openclaw/tmp/${directory}`,`mkdir '/workspace/.openclaw/tmp/${directory}'`,`mkdir -p /workspace/.openclaw/tmp/${directory}/`]) {
    const args={command};const denied=call('exec',args);
    assert.equal(denied.block,true);assert.match(denied.blockReason,/Nothing was executed/);
    assert.ok(denied.blockReason.includes(`${directory}/`));assert.equal(denied.params,undefined);
    assert.equal(args.command,command);assert.doesNotMatch(denied.blockReason,/Files already written|saved in/);
  }
  assert.equal(call('exec',{command:'python3 totals.py',workdir:`/workspace/.openclaw/tmp/${directory}`}).block,true);
  assert.equal(call('exec',{command:'python3 totals.py',workdir:`/workspace/${directory}`}),undefined);
  assert.equal(call('exec',{command:`printf '%s' '/workspace/.openclaw/tmp/${directory}'`}),undefined);
});

test('new-folder parser preserves declared operands and does not acquire intent from examples or prohibitions',t=>{
  for(const intent of [`Create a Python project inside a separate folder "${directory}".`,`Write a Python project under an empty directory '${directory}'.`]) {
    const {call}=fixture(t,intent);assert.equal(call('write',{path:`${directory}/totals.py`,content:'x'}),undefined);
  }
  for(const intent of [
    `Build a Python project. Example: "Create a Python project in a new folder ${directory}."`,
    `Create a Python project, but do not put it in a new folder ${directory}.`,
    `Create a Python project.\n> Create a Python project in a new folder ${directory}.`,
    `Create a Python project.\n\`\`\`text\nCreate a Python project in a new folder ${directory}.\n\`\`\``,
  ]) {
    const {call}=fixture(t,intent);const decision=call('write',{path:'weather-tool/main.py',content:'x'});
    assert.equal(decision.params.path,'Playground/weather-tool/main.py',intent);
  }
  const multiple=fixture(t,'Create a Python project in a new folder first-choice and in a new folder second-choice.');
  for(const name of ['first-choice','second-choice']) assert.equal(multiple.call('write',{path:`${name}/main.py`,content:'x'}),undefined,'multiple owner operands are preserved without choosing one');
  const prohibition=fixture(t,`Create a Python project, and in a new folder ${directory}, do not write anything.`);
  assert.equal(prohibition.call('write',{path:`.openclaw/tmp/${directory}/main.py`,content:'x'}),undefined,'a following prohibition is not a request to write into that folder; core policy remains responsible');
});

test('correction never fabricates traversal destinations or rewrites other explicit and existing files',t=>{
  const {root,call}=fixture(t);
  fs.mkdirSync(path.join(root,'existing'));fs.writeFileSync(path.join(root,'existing/readme.txt'),'keep');
  for(const tool of ['read','edit']) assert.equal(call(tool,{path:'existing/readme.txt'}),undefined);
  assert.equal(fs.readFileSync(path.join(root,'existing/readme.txt'),'utf8'),'keep');
  for(const target of [`/tmp/${directory}/totals.py`,`.openclaw/tmp/${directory}/../escape.py`,`.openclaw/tmp/${directory}/CON`,`.openclaw/tmp/${directory}/${directory}/totals.py`]) {
    assert.equal(call('write',{path:target,content:'x'})?.params,undefined,'core permissions still decide, no invented safe rewrite');
  }
  const literal=`/workspace/.openclaw/tmp/${directory}/totals.py`;
  const explicit=fixture(t,`${prompt} Also use exactly "${literal}".`);
  assert.equal(explicit.call('write',{path:literal,content:'x'}),undefined,'an exact explicit operand remains subject to core policy');
  for(const operand of [literal,literal.slice('/workspace/'.length),literal.replaceAll('/','\\')]) {
    const selected=fixture(t,`${prompt} Also use exactly ${operand}.`);
    assert.equal(selected.call('write',{path:literal,content:'x'}),undefined,'equivalent workspace spellings preserve explicit location');
  }
  const substring=fixture(t,`${prompt} The label is not-${literal}.`);
  assert.equal(substring.call('write',{path:literal,content:'x'}).block,true,'a longer token is not the exact path operand');
  assert.equal(call('write',{path:`.openclaw/tmp/${directory}`,content:'x'})?.params,undefined,'never suggest a file replacing the project directory');
});

test('full hooks recover the live misplaced mkdir/write sequence and execute in the named folder', {skip:process.platform==='win32'}, t=>{
  for(const wrapped of [false,true]) for(const native of [false,true]) {
    const {root}=fixture(t);
    const guard=createToolLoopGuard({execControl:{resolveWorkdir:native?nativeExecWorkdir:()=>undefined,prepare:(_run,command)=>command}});
    const context={agentId:'pixel',runId:`named-${wrapped}-${native}`,sessionId:'named-project'};
    guard.observeRun(context,'pixel',{prompt},{workspaceRoot:root,executionHost:native?'gateway':'sandbox'});
    const invoke=(tool,args)=>guard.beforeToolCall({toolName:wrapped?'tool_call':tool,params:wrapped?{id:`openclaw:core:${tool}`,args}:args},context);
    assert.equal(invoke('exec',{command:`mkdir -p /workspace/.openclaw/tmp/${directory}`}).block,true);
    assert.equal(invoke('write',{path:`/workspace/.openclaw/tmp/${directory}/totals.py`,content:'print(42)'}).block,true);
    const args={path:`${directory}/totals.py`,content:'print(42)\n'};
    const accepted=invoke('write',args);assert.notEqual(accepted?.block,true,accepted?.blockReason);
    const actual=(wrapped?accepted?.params?.args:accepted?.params)??args;
    const filename=path.resolve(root,actual.path);assert.equal(filename,path.join(root,directory,'totals.py'));
    fs.mkdirSync(path.dirname(filename),{recursive:true});fs.writeFileSync(filename,actual.content);
    const command={command:'python3 totals.py',workdir:`/workspace/${directory}`};
    const decision=invoke('exec',command);assert.notEqual(decision?.block,true,decision?.blockReason);
    const executed=(wrapped?decision?.params?.args:decision?.params)??command;
    const cwd=native?executed.workdir:path.join(root,executed.workdir.slice('/workspace/'.length));
    assert.equal(cwd,path.join(root,directory));assert.equal(executed.command,'python3 totals.py');
    assert.equal(execFileSync('python3',['totals.py'],{cwd,encoding:'utf8'}),'42\n');
    assert.equal(fs.existsSync(path.join(root,'.openclaw')),false);
    assert.equal(fs.existsSync(path.join(root,'Playground')),false);
  }
});

test('charged mkdir and write refusals permit the actual corrected write without resetting the run',t=>{
  const {root}=fixture(t),guard=createToolLoopGuard();
  const context={agentId:'pixel',runId:'charged-owner-path',sessionId:'charged-session'};
  guard.observeRun(context,'pixel',{prompt},{workspaceRoot:root,executionHost:'sandbox'});
  let sequence=0,executions=0;
  const call=(tool,args)=>{
    const toolCallId=`owner-${++sequence}`,ctx={...context,toolName:tool,toolCallId};
    guard.observeModelCall({},context);
    const original=structuredClone(args);
    const decision=guard.beforeToolCall({toolName:tool,params:args,toolCallId},ctx);
    assert.deepEqual(args,original);
    const executed={...args,...decision?.params};
    let result;
    if(decision?.block) result={content:[{type:'text',text:decision.blockReason}],details:{status:'blocked',deniedReason:'plugin-before-tool-call',reason:decision.blockReason}};
    else {
      assert.equal(tool,'write');executions++;
      const file=path.resolve(root,executed.path);assert.equal(file,path.join(root,directory,'totals.py'));
      fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,executed.content);
      result={content:[{type:'text',text:`Successfully wrote ${Buffer.byteLength(executed.content)} bytes to ${executed.path}`}],details:{}};
    }
    const isError=decision?.block===true;
    guard.afterToolCall({toolName:tool,params:executed,toolCallId,result,...(isError?{error:result.content[0].text}:{})},ctx);
    const message={role:'toolResult',toolName:tool,toolCallId,isError,...result};
    const persisted=guard.toolResultPersist({toolName:tool,toolCallId,message},ctx)?.message??message;
    return {decision,persisted};
  };
  const mkdir=call('exec',{command:`mkdir -p /workspace/.openclaw/tmp/${directory}`});
  assert.equal(mkdir.decision.block,true);assert.equal(mkdir.persisted.isError,true);assert.equal(executions,0);
  const failed=call('write',{path:`/workspace/.openclaw/tmp/${directory}/totals.py`,content:'print(42)'});
  assert.equal(failed.decision.block,true);assert.equal(failed.persisted.isError,true);assert.equal(executions,0);
  const corrected=call('write',{path:`${directory}/totals.py`,content:'print(42)'});
  assert.notEqual(corrected.decision?.block,true,corrected.decision?.blockReason);assert.equal(corrected.persisted.isError,false);
  assert.equal(executions,1);assert.equal(fs.readFileSync(path.join(root,directory,'totals.py'),'utf8'),'print(42)');
  assert.equal(fs.existsSync(path.join(root,'.openclaw')),false);
});
