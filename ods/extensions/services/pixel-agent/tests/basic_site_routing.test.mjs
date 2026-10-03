import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard, workspacePreviewMode, userMessageRequestsWorkspacePreview,
  WORKSPACE_PREVIEW_FRESH_ENTRY_REASON} from '../plugin/tool-loop-guard.mjs';
import {promptContractForAgent, ODS_WORKSPACE_NEW_STATIC_CONTRACT,
  ODS_WORKSPACE_PREVIEW_CONTRACT} from '../plugin/prompt-contract.mjs';

const context = {agentId:'pixel',runId:'basic-site-routing',sessionId:'basic-site-routing'};
const basic = [
  'Make me a basic website.',
  'Build a basic responsive website and publish it.',
  'Please create a simple landing page and show its preview.',
  'Could you please make us a new polished, responsive one-page website?',
  'Design a simple accessible web page with a blue heading and a contact section.',
  'Create a simple one-page website for a bakery with a hero, menu and footer.',
  'Make a simple website in Playground/cafe/index.html.',
  'Make a basic website with a dark background and a contact section.',
  'Make a basic website with plain HTML and CSS.',
  'Create a simple website. Then publish its preview.',
  'Create and publish a new static HTML website at demo/index.html.',
  'Crie um site simples e mostre a prévia.',
  'Por favor, faça para mim um site básico.',
  'Construa uma página web simples.',
];
const flexible = [
  ['Make me a basic website using Qwik.','exec',{command:'npm --version'}],
  ['Make me a basic website in Blazor.','exec',{command:'dotnet --version'}],
  ['Make me a basic website with SolidStart framework.','read',{path:'package.json'}],
  ['Make me a basic website powered by Phoenix.','exec',{command:'mix --version'}],
  ['Make me a basic website with authentication and a database.','read',{path:'README.md'}],
  ['Make me a basic website with an API backend and publish its preview.','exec',{command:'ls'}],
  ['Make me a basic website from metrics.json.','read',{path:'metrics.json'}],
  ['Make me a basic website matching mockup.png.','read',{path:'mockup.png'}],
  ['Make me a basic website. Inspect the attached brand guide first.','read',{path:'brand.md'}],
  ['Make me a basic website based on https://example.invalid/design.','web_fetch',{url:'https://example.invalid/design'}],
  ['Make me a basic website inside the existing project.','read',{path:'package.json'}],
  ['Make me a basic website and then write a Python CLI program.','write',{path:'cli.py',content:'print(1)'}],
  ['Crie um site simples usando Django.','exec',{command:'python --version'}],
  ['Faça um site básico em Rails.','exec',{command:'ruby --version'}],
  ['Build a basic static HTML website using Django.','exec',{command:'python --version'}],
  ['Create a static HTML website with user login and authentication.','read',{path:'README.md'}],
  ['Build a plain HTML website using a REST API.','read',{path:'README.md'}],
  ['Create a static HTML website with a live database.','exec',{command:'ls'}],
  ['Make me a basic website from the brief in NOTES.','read',{path:'NOTES'}],
  ['Build a static HTML website using the project in Playground/app.','read',{path:'Playground/app/package.json'}],
  ['Make me a basic website after you ask me three design questions.','read',{path:'README.md'}],
  ['Make me a basic website. Generate assets before editing files.','read',{path:'README.md'}],
  ['Make me a basic website; download the logo first.','web_fetch',{url:'https://example.invalid/logo.svg'}],
  ['Make me a basic website; ask me for the text before you start.','read',{path:'README.md'}],
  ['Crie um site simples com autenticação e banco de dados.','exec',{command:'ls'}],
  ['Build a basic site from the recording.','read',{path:'recording.wav'}],
  ['Build a basic site matching this video.','read',{path:'video.mp4'}],
  ['Crie um site simples com base na imagem.','read',{path:'reference.png'}],
  ['Crie um site simples a partir dos dados da planilha.','read',{path:'metrics.csv'}],
  ['Make me a basic website in django.','exec',{command:'python -m django --version'}],
  ['Create a static HTML website in django.','exec',{command:'python -m django --version'}],
  ['Create and publish a new static HTML website in demo.','read',{path:'README.md'}],
  ['Make me a basic website in blazor.','exec',{command:'dotnet --version'}],
  ['Make me a basic website with quuxstack.','read',{path:'README.md'}],
  ['Make me a basic website with the quuxstack.','read',{path:'README.md'}],
  ['Make me a basic website on anunknownstack.','read',{path:'README.md'}],
  ['Make me a basic website on Django.','exec',{command:'python -m django --version'}],
  ['Create a simple website leveraging anunknownstack.','read',{path:'README.md'}],
  ['Faça um site básico em umaframeworknova.','read',{path:'README.md'}],
  ['Make me a basic website without static HTML.','exec',{command:'ls'}],
  ['Make me a basic website, not a static site.','read',{path:'README.md'}],
  ['Create a simple website mockup as an SVG.','write',{path:'mockup.svg',content:'<svg xmlns="http://www.w3.org/2000/svg"/>'}],
  ['Make me a basic website. Check what is already in the workspace first.','read',{path:'README.md'}],
  ['Make me a basic website. Examine the project before changing anything.','read',{path:'README.md'}],
  ['Make me a basic website and check the workspace first.','read',{path:'README.md'}],
  ['Create a static HTML website. Check the project first.','read',{path:'README.md'}],
  ['Make me a basic website. Consult the brief before starting.','read',{path:'NOTES'}],
  ['Make me a basic website, plus a Python CLI.','write',{path:'cli.py',content:'print(1)'}],
  ['Create a basic website. Start by making a test suite.','write',{path:'test_site.py',content:'import unittest'}],
  ['Create a static HTML website. Start by making a test suite.','write',{path:'test_site.py',content:'import unittest'}],
  ['Build a simple website performance analyzer.','write',{path:'analyzer.py',content:'print(1)'}],
  ['Build a simple website crawler. Then publish its preview.','write',{path:'crawler.py',content:'print(1)'}],
];
const softwareObjects = [
  'Build a simple website crawler.',
  'Build a simple website security scanner.',
  'Create a simple website uptime monitor.',
  'Make a basic website generator.',
  'Create a simple website sitemap generator.',
  'Create a simple landing page template generator.',
  'Build a basic website translation utility.',
];
const ordinary = [
  'Write a report about how to create a basic website.',
  'Create a checker for a simple website.',
  'Write a Markdown accessibility report about a basic website.',
  'Create unit tests for the simple website payment form.',
  'Explain this quoted request: "Make me a basic website."',
  'Do not make me a basic website. Write a Python CLI instead.',
  'Crie um relatório sobre como fazer um site simples.',
];

for (const wrapped of [false,true]) for (const history of [false,true]) {
  const tool = (name,params) => wrapped ? {toolName:'tool_call',params:{id:`openclaw:core:${name}`,args:params}} : {toolName:name,params};
  const event = prompt => history ? {messages:[
    {role:'user',content:'Install an extension for an old project.'},
    {role:'assistant',content:'That previous request is finished.'},
    {role:'user',content:prompt},
  ]} : {prompt};
  for (const prompt of basic) test(`direct basic site keeps first-write default (wrapped=${wrapped},history=${history}): ${prompt}`,()=>{
    const owner=event(prompt), guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',owner);
    assert.equal(workspacePreviewMode(owner.messages,owner.prompt),'new-static');
    assert.equal(guard.beforeToolCall(tool('exec',{command:'mkdir -p unrelated'}),context)?.blockReason,WORKSPACE_PREVIEW_FRESH_ENTRY_REASON);
    assert.notEqual(guard.beforeToolCall(tool('write',{path:'Playground/basic/index.html',content:'<!doctype html><h1>Basic</h1>'}),context)?.block,true);
    assert.ok(promptContractForAgent(context,'pixel',owner,{configuredLeanPrompt:true}).appendSystemContext.includes(ODS_WORKSPACE_NEW_STATIC_CONTRACT));
  });
  for (const [prompt,name,args] of flexible) test(`basic site prerequisites keep normal tools (wrapped=${wrapped},history=${history}): ${prompt}`,()=>{
    const owner=event(prompt), guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',owner);
    assert.equal(workspacePreviewMode(owner.messages,owner.prompt),'existing-project');
    assert.notEqual(guard.beforeToolCall(tool(name,args),context)?.block,true);
    assert.equal(promptContractForAgent(context,'pixel',owner,{configuredLeanPrompt:true}).appendSystemContext.includes(ODS_WORKSPACE_NEW_STATIC_CONTRACT),false);
  });
  for (const prompt of softwareObjects) test(`website modifier is not a page object (wrapped=${wrapped},history=${history}): ${prompt}`,()=>{
    const owner=event(prompt), guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',owner);
    assert.equal(userMessageRequestsWorkspacePreview(owner.messages,owner.prompt),false);
    assert.equal(workspacePreviewMode(owner.messages,owner.prompt),undefined);
    assert.notEqual(guard.beforeToolCall(tool('write',{path:'utility.py',content:'print(1)'}),context)?.block,true);
    const contract=promptContractForAgent(context,'pixel',owner,{configuredLeanPrompt:true}).appendSystemContext;
    assert.equal(contract.includes(ODS_WORKSPACE_NEW_STATIC_CONTRACT),false);
    assert.equal(contract.includes(ODS_WORKSPACE_PREVIEW_CONTRACT),false);
  });
  for (const prompt of ordinary) test(`basic site phrase is not an object request (wrapped=${wrapped},history=${history}): ${prompt}`,()=>{
    const owner=event(prompt), guard=createToolLoopGuard();
    guard.observeRun(context,'pixel',owner);
    assert.notEqual(workspacePreviewMode(owner.messages,owner.prompt),'new-static');
    assert.notEqual(guard.beforeToolCall(tool('write',{path:'report.md',content:'# Report'}),context)?.block,true);
  });
}

for (const type of ['image_url','input_image','file','input_file','audio','input_audio','video','future_attachment']) test(`owner ${type} attachment defeats basic-site first-write default`,()=>{
  const messages=[{role:'user',content:[{type:'text',text:'Make me a basic website.'},{type}]}];
  const guard=createToolLoopGuard();
  guard.observeRun(context,'pixel',{messages});
  assert.equal(workspacePreviewMode(messages),'existing-project');
  assert.notEqual(guard.beforeToolCall({toolName:'read',params:{path:'reference.png'}},context)?.block,true);
});
