import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {routePlaygroundTool, requestsNewPlaygroundProject} from '../plugin/playground-projects.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

function fixture(t) {
  const root=fs.mkdtempSync(path.join(tmpdir(),'ods-playground-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const state={};
  const call=(tool,params,overrides={})=>routePlaygroundTool({state,tool,params,root,session:'owner-session',intent:'Crie um jogo de cobra.',...overrides});
  return {root,state,call};
}

test('new PT/EN projects include native tools and retain ordinary constraints',()=>{
  for(const prompt of ['Crie um site para uma cafeteria sem dependências externas.','Build a weather tool without external packages.','Create a Python script using existing assets.','Crie um aplicativo e use index.html.','Crie um jogo em uma pasta com um nome descritivo.','Build a timer.','Write a Python utility.']) assert.equal(requestsNewPlaygroundProject(prompt),true,prompt);
  for(const prompt of ['Não crie um site.','Do not create a game.','Explain how to create a project.','Create a button for this app.','Crie um botão para esse site.','Edite o projeto existente.','Make the game harder.','Faça o jogo ficar mais difícil.']) assert.equal(requestsNewPlaygroundProject(prompt),false,prompt);
});

test('creates a real descriptive project, routes files of every type and stores no prompt or identity',t=>{
  const {root,state,call}=fixture(t);
  assert.equal(call('write',{path:'weather-tool/main.py',content:'print(1)'}).params.path,'Playground/weather-tool/main.py');
  assert.ok(fs.statSync(path.join(root,'Playground/weather-tool')).isDirectory());
  assert.equal(call('write',{path:'README.md',content:'Docs'}).params.path,'Playground/weather-tool/README.md');
  assert.equal(call('edit',{path:'src/main.py',oldText:'1',newText:'2'}).params.path,'Playground/weather-tool/src/main.py');
  assert.equal(call('read',{path:'/workspace/weather-tool/main.py'}).params.path,'Playground/weather-tool/main.py');
  assert.equal(call('exec',{command:'python main.py',workdir:'weather-tool'}).params.workdir,'Playground/weather-tool');
  assert.equal(call('exec',{command:'python main.py'}).params.workdir,'Playground/weather-tool');
  assert.equal(call('exec',{command:'python weather-tool/main.py'}).params.workdir,'Playground');
  const record=fs.readdirSync(path.join(root,'.ods-projects'));
  assert.equal(record.length,1);
  assert.match(record[0],/^[a-f0-9]{64}\.json$/);
  const content=fs.readFileSync(path.join(root,'.ods-projects',record[0]),'utf8');
  assert.doesNotMatch(content,/owner-session|Crie|print/);
  assert.equal(state.binding.directory,'Playground/weather-tool');
});

test('category words inside file operands do not turn ordinary writes into new projects',t=>{
  const {root,call}=fixture(t);
  for (const intent of [
    'Crie a pasta macos-final-tool-check no workspace e escreva no arquivo probe.txt somente ODS_MAC_OK.',
    'Create game.txt containing hello.',
    'Write a note in project-notes.txt.',
    'Crie o arquivo app.config.json.',
    'Create a folder named website-check.',
  ]) {
    assert.equal(requestsNewPlaygroundProject(intent),false,intent);
    assert.equal(call('tool_call',{id:'exec',args:{command:'mkdir -p macos-final-tool-check',workdir:root}},
      {state:{},intent}),undefined,intent);
  }
  assert.equal(fs.existsSync(path.join(root,'Playground')),false);
  assert.equal(requestsNewPlaygroundProject('Create a game in snake-game/index.html.'),true);
});

test('fresh reservations avoid files, existing names and links without moving legacy content',t=>{
  const {root,call}=fixture(t);
  fs.mkdirSync(path.join(root,'Playground'));
  fs.mkdirSync(path.join(root,'Playground/snake-game'));
  fs.writeFileSync(path.join(root,'Playground/snake-game/keep.txt'),'keep');
  const routed=call('write',{path:'Playground/snake-game/index.html',content:'<html/>'});
  assert.equal(routed.params.path,'Playground/snake-game-2/index.html');
  assert.equal(call('pixel_ods_workspace_preview',{relativeDirectory:'Playground/snake-game'}).params.relativeDirectory,'Playground/snake-game-2');
  assert.equal(call('read',{path:'snake-game/index.html'}).params.path,'Playground/snake-game-2/index.html');
  assert.equal(fs.readFileSync(path.join(root,'Playground/snake-game/keep.txt'),'utf8'),'keep');
  assert.equal(call('write',{path:'Playground/snake-game-2/app.js'}),undefined);
  assert.equal(fs.existsSync(path.join(root,'Playground/Playground')),false);
});

test('bare files and generic folders produce one actionable correction without reserving a fake project',t=>{
  const {root,call}=fixture(t);
  for(const target of ['index.html','src/main.py','public/index.html','Playground/project/index.html','Playground/CON/main.py','../outside/main.py']) assert.equal(call('write',{path:target,content:'x'}).block,true,target);
  assert.equal(fs.existsSync(path.join(root,'Playground')),false);
  assert.equal(call('write',{path:'cafeteria-site/index.html'}).params.path,'Playground/cafeteria-site/index.html');
});

test('same run never reserves twice; fresh run gets a distinct folder and independent session records',t=>{
  const {root,call}=fixture(t);
  call('write',{path:'snake-game/index.html'});
  call('write',{path:'snake-game/app.js'});
  assert.deepEqual(fs.readdirSync(path.join(root,'Playground')),['snake-game']);
  assert.equal(call('write',{path:'Playground/snake-game/index.html'},{state:{}}).params.path,'Playground/snake-game-2/index.html');
  call('write',{path:'notes-tool/main.py'},{state:{},session:'second-owner-session'});
  assert.equal(fs.readdirSync(path.join(root,'.ods-projects')).length,2);
  assert.equal(call('read',{path:'main.py'},{state:{},session:'second-owner-session',intent:'Leia o arquivo criado.'}).params.path,'Playground/notes-tool/main.py');
});

test('restart restores project basenames even with conflicting root files, while unrelated reads stay exact',t=>{
  const {root,call}=fixture(t);
  call('write',{path:'snake-game/index.html'});
  fs.writeFileSync(path.join(root,'index.html'),'legacy root');
  fs.mkdirSync(path.join(root,'docs'));
  fs.writeFileSync(path.join(root,'docs/manual.md'),'unrelated');
  const restored={};
  const options={state:restored,intent:'Mude a cor para azul.'};
  assert.equal(call('read',{path:'index.html'},options).params.path,'Playground/snake-game/index.html');
  assert.equal(call('read',{path:'docs/manual.md'},options),undefined);
  assert.equal(call('read',{path:'other-project/index.html'},options),undefined);
  assert.equal(call('exec',{command:'cat Playground/snake-game/index.html'},options),undefined);
});

test('ordinary PT/EN follow-ups retain the same persisted project and exact existing paths',t=>{
  const {root,call}=fixture(t);
  call('write',{path:'snake-game/index.html'});
  const canonical='Playground/snake-game/index.html';
  fs.writeFileSync(path.join(root,canonical),'game');
  for(const intent of ['Make the game harder.','Faça o jogo ficar mais difícil.']) {
    const options={state:{},intent,existingPaths:[canonical]};
    assert.equal(call('read',{path:'index.html'},options).params.path,canonical);
    assert.equal(call('write',{path:canonical,content:'harder'},options),undefined);
    assert.equal(call('read',{path:'index.html'},{state:{},intent:'Continue.'}).params.path,canonical);
  }
  assert.deepEqual(fs.readdirSync(path.join(root,'Playground')),['snake-game']);
});

test('first exec/patch creation must establish project with write; later patches route filenames only',t=>{
  const {root,call}=fixture(t);
  assert.equal(call('exec',{command:'ls -la'}),undefined);
  assert.equal(call('exec',{command:'mkdir snake-game && echo hi > snake-game/index.html'}).block,true);
  const input='*** Begin Patch\n*** Add File: snake-game/index.html\n+<html>snake-game/raw-text</html>\n*** End Patch';
  assert.equal(call('apply_patch',{input}).block,true);
  assert.equal(fs.existsSync(path.join(root,'Playground')),false);
  call('write',{path:'snake-game/README.md',content:'Snake'});
  assert.equal(call('apply_patch',{input}).params.input,input.replace('Add File: snake-game/','Add File: Playground/snake-game/'));
  assert.equal(call('tool_call',{id:'openclaw:core:apply_patch',args:{input:'*** Begin Patch\n*** Update File: src/game.js\n@@\n-old\n+new\n*** End Patch'}}).params.args.input.includes('Update File: Playground/snake-game/src/game.js'),true);
  assert.equal(call('apply_patch',{input:'*** Begin Patch\n*** Add File: ../outside.js\n+bad\n*** End Patch'}).block,true);
  const collision=call('write',{path:'Playground/snake-game/index.html'},{state:{}});
  assert.equal(collision.params.path,'Playground/snake-game-2/index.html');
  const restored={state:{},intent:'Check the game.'};
  assert.equal(call('exec',{command:'node snake-game/game.js'},restored).block,true);
  const command='node game.js';
  const scoped=call('exec',{command,workdir:'snake-game'},restored);
  assert.deepEqual(scoped.params,{command,workdir:'Playground/snake-game-2'});
});

test('explicit legacy paths and trusted legacy continuation invalidate stale session default',t=>{
  const {root,call}=fixture(t);
  call('write',{path:'snake-game/index.html'});
  fs.mkdirSync(path.join(root,'legacy-site'));
  const opts={state:{},intent:'Edite legacy-site/index.html.'};
  assert.equal(call('edit',{path:'legacy-site/index.html',oldText:'a',newText:'b'},opts),undefined);
  assert.equal(call('write',{path:'note.txt'},{state:{},intent:'Continue.'}),undefined);
  call('write',{path:'snake-game/index.html'},{state:{}});
  assert.equal(call('read',{path:'legacy-site/index.html'},{state:{},preserveExisting:true,intent:'Improve this site.'}),undefined);
  assert.equal(call('write',{path:'note.txt'},{state:{},intent:'Continue.'}),undefined);
});

test('named folders and existing model paths stay in place; filename requirements do not disable a fresh project',t=>{
  const {root,call}=fixture(t);
  for(const intent of ['Create a site in the directory legacy-site.','Crie um site na pasta chamada meu-site.','Build the site at /workspace/exact-site/index.html.']) assert.equal(call('write',{path:'legacy-site/index.html'},{state:{},intent}),undefined);
  for(const intent of ['Crie um site e use index.html.','Crie um site numa pasta com um nome descritivo.']) assert.match(call('write',{path:'cafe-site/index.html'},{state:{},intent}).params.path,/^Playground\/cafe-site/);
  fs.mkdirSync(path.join(root,'legacy-project'));
  assert.equal(call('write',{path:'legacy-project/main.py'},{state:{},intent:'Create a Python tool.'}),undefined);
});

test('unsafe child links or registry state fail closed on repeated calls; trusted configured root aliases work',t=>{
  const {root,call}=fixture(t);
  const outside=fs.mkdtempSync(path.join(tmpdir(),'ods-outside-'));
  t.after(()=>fs.rmSync(outside,{recursive:true,force:true}));
  fs.symlinkSync(outside,path.join(root,'Playground'),'junction');
  assert.equal(call('write',{path:'snake-game/index.html'}).block,true);
  assert.equal(call('write',{path:'other-game/index.html'}).block,true);
  assert.deepEqual(fs.readdirSync(outside),[]);
  const other=fixture(t);
  fs.mkdirSync(path.join(other.root,'.ods-projects'));
  const identity=createHash('sha256').update('owner-session').digest('hex');
  fs.writeFileSync(path.join(other.root,'.ods-projects',`${identity}.json`),'broken');
  assert.equal(other.call('read',{path:'index.html'},{intent:'Continue.'}).block,true);
  assert.equal(other.call('read',{path:'index.html'},{intent:'Continue.'}).block,true);
  const alias=path.join(outside,'workspace-alias');
  fs.symlinkSync(other.root,alias,'junction');
  assert.equal(other.call('write',{path:'safe-tool/main.py'},{root:alias,state:{},session:'new-session'}).params.path,'Playground/safe-tool/main.py');
});

test('guard routes actual write params and preserves canonical evidence for preview and after restart',t=>{
  const {root}=fixture(t);
  const context={agentId:'pixel',runId:'project-one',sessionId:'session-one',sessionKey:'agent:pixel:owner-project'};
  const guard=createToolLoopGuard();
  const event={prompt:'Crie um site para uma cafeteria sem dependências externas. Escolha uma pasta descritiva para esse projeto.'};
  guard.observeRun(context,'pixel',event,{workspaceRoot:root});
  const params={id:'write',args:{path:'cafeteria-site/index.html',content:'<!doctype html><html><body>Café</body></html>'}};
  const decision=guard.beforeToolCall({toolName:'tool_call',toolCallId:'write-one',params},context);
  assert.notEqual(decision?.block,true,decision?.blockReason);
  assert.equal(decision.params.args.path,'Playground/cafeteria-site/index.html');
  fs.writeFileSync(path.join(root,decision.params.args.path),decision.params.args.content);
  guard.afterToolCall({toolName:'tool_call',toolCallId:'write-one',params:decision.params,result:{details:{tool:{id:'openclaw:core:write',source:'openclaw',sourceName:'core',name:'write'},result:{content:[{type:'text',text:'File written'}]}}}},context);
  guard.observeRun(context,'pixel',event,{workspaceRoot:root});
  const preview=guard.beforeToolCall({toolName:'tool_call',toolCallId:'publish-one',params:{id:'pixel_ods_workspace_preview',args:{relativeDirectory:'cafeteria-site'}}},context);
  assert.notEqual(preview?.block,true,preview?.blockReason);
  assert.equal(preview.params.args.relativeDirectory,'Playground/cafeteria-site');
  assert.deepEqual(fs.readdirSync(path.join(root,'Playground')),['cafeteria-site']);
  const restarted=createToolLoopGuard();
  const later={...context,runId:'project-two',sessionId:'new-runtime-session'};
  restarted.observeRun(later,'pixel',{prompt:'Mude a cor do site para azul.'},{workspaceRoot:root});
  const read=restarted.beforeToolCall({toolName:'read',params:{path:'index.html'}},later);
  assert.notEqual(read?.block,true,read?.blockReason);
  assert.equal(read.params.path,'Playground/cafeteria-site/index.html');
});
