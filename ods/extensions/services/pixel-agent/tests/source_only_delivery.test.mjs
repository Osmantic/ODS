import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';
import vm from 'node:vm';
const {createToolLoopGuard, userMessageRequestsWorkspacePreview} = await import(
  process.env.ODS_SOURCE_ONLY_GUARD_MODULE || '../plugin/tool-loop-guard.mjs');

test('optional publication does not become required for requested source files', () => {
  for (const prompt of [
    'Create a Next.js project in the workspace. Create package.json and app/page.jsx. No need to build, publish or launch a server in this test.',
    'Create a website. No need to publish it.',
    'Create the website source. You do not need to build and publish it.',
    'Crie um site. Não precisa compilar, publicar ou abrir uma prévia agora.',
  ]) assert.equal(userMessageRequestsWorkspacePreview([], prompt), false, prompt);
});

test('an unrelated optional action does not cancel requested publication', () => {
  for (const prompt of [
    'Create a website. No need to explain the code; publish the website.',
    'Create a website. No need to install dependencies, but publish it.',
    'Create a website. No need to build it again. Publish the existing website.',
    'Create a website. No need to publish the notes, but publish the website.',
    'Crie um site. Não precisa publicar as notas; publique o site.',
  ]) assert.equal(userMessageRequestsWorkspacePreview([], prompt), true, prompt);
});

function sourceFixture(prompt, deferred, t) {
  const root = mkdtempSync(path.join(tmpdir(),'ods-source-only-'));
  t.after(()=>{
    assert.equal(path.dirname(root),path.resolve(tmpdir()));
    assert.ok(path.basename(root).startsWith('ods-source-only-'));
    rmSync(root,{recursive:true,force:true});
  });
  let publications = 0;
  const guard = createToolLoopGuard({publishWorkspacePreview: async () => {
    publications++;
    throw new Error('Source-only request must never publish');
  }});
  const context = {agentId:'pixel',runId:'source-only-run',sessionId:'source-only-session',
    sessionKey:'agent:pixel:source-only'};
  guard.observeRun(context, 'pixel', {prompt}, {executionHost:'gateway',workspaceRoot:root});
  const files = [
    {path:'Playground/source-only/package.json',content:'{"scripts":{"build":"next build"}}\n'},
    {path:'Playground/source-only/app/page.jsx',content:'export default function Page(){ return <main>Verified source</main>; }\n'},
  ];
  let id = 0;
  for (const file of files) for (const name of ['write','read']) {
    const toolCallId = `source-action-${++id}`;
    const args = name === 'write' ? file : {path:file.path};
    const toolName = deferred ? 'tool_call' : name;
    const params = deferred ? {id:`openclaw:core:${name}`,args} : args;
    const ctx = {...context,toolName,toolCallId};
    const prepared = guard.beforeToolCall({toolName,toolCallId,params},ctx);
    assert.notEqual(prepared?.block,true,prepared?.blockReason);
    const effective = prepared?.params ?? params;
    const requested = deferred ? effective.args : effective;
    const actualPath = path.resolve(root,requested.path);
    assert.ok(actualPath.startsWith(root+path.sep));
    if (name === 'write') {
      mkdirSync(path.dirname(actualPath),{recursive:true});
      writeFileSync(actualPath,requested.content);
    }
    const result = {content:[{type:'text',text:name === 'read' ? readFileSync(actualPath,'utf8') : 'Successfully wrote file'}],
      details:{status:'completed'}};
    const tool = {id:`openclaw:core:${name}`,source:'openclaw',sourceName:'core',name};
    const observed = deferred ? {content:[{type:'text',text:JSON.stringify({tool,result})}],details:{tool,result}} : result;
    guard.afterToolCall({toolName,toolCallId,params:prepared?.params ?? params,result:observed},ctx);
    guard.toolResultPersist({toolName,toolCallId,message:{role:'toolResult',toolName,toolCallId,...observed}},ctx);
  }
  return {guard,context,publications:()=>publications};
}

for (const deferred of [false,true]) test(`real finalization retains verified source reply without preview (${deferred ? 'ToolSearch' : 'native'})`, async t => {
  const prompt = 'Create a Next.js project in the workspace. Create package.json and app/page.jsx, then read both files back. No need to build, publish or launch a server in this test.';
  const {guard,context,publications} = sourceFixture(prompt,deferred,t);
  const original = 'Created and reread package.json and app/page.jsx. No build, publication or server was started.';
  const event = {lastAssistantMessage:original};
  const source = readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start = source.indexOf('    api.on("before_agent_finalize",');
  const end = source.indexOf('    // Delivery rewriting',start);
  let finalize;
  vm.runInNewContext(source.slice(start,end),{
    api:{on:(_name,callback)=>{finalize=callback;}}, toolLoopGuard:guard, AGENT_ID:'pixel',
    goalProgress:{finalize:(_event,_context,decision)=>decision.guardDecision},
  });
  assert.equal(await finalize(event,context),undefined);
  assert.equal(publications(),0);
  const verification = guard.deliveryVerificationForRun(context.runId);
  assert.notEqual(verification.status,'failed');
  assert.equal(verification.preview,undefined);
  const outgoing = {runId:context.runId,kind:'final',payload:{text:original}};
  const result = guard.replyPayloadSending(outgoing);
  assert.equal(result?.payload?.text ?? outgoing.payload.text,original);
});

test('quoted optional-publication text does not waive actual website delivery after writes', t => {
  for (const quoted of [
    '"No need to publish it"', "'No need to publish it'", '`No need to publish it`',
    '\n> No need to publish it\n', '\n```text\nNo need to publish it\n```\n',
  ]) {
    const prompt = `Create and publish a website. The heading text is ${quoted}.`;
    assert.equal(userMessageRequestsWorkspacePreview([],prompt),true,prompt);
    const {guard,context} = sourceFixture(prompt,false,t);
    assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:'Saved the source files.'},context)?.action,'revise',prompt);
    assert.equal(guard.verificationForRun(context.runId).status,'failed',prompt);
  }
});

test('later explicit website delivery survives an independent optional clause after file receipts', t => {
  const prompt = 'Create a website. No need to publish the notes, but publish the website.';
  const {guard,context} = sourceFixture(prompt,true,t);
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage:'Created the source.'},context)?.action,'revise');
  assert.equal(guard.verificationForRun(context.runId).status,'failed');
});
