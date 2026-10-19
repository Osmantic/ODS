// Real pinned compaction caller, actual session storage and a loopback provider.
// Synthetic transcripts only; this checks boundaries, not summary quality.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {once} from 'node:events';
import {createHash, randomUUID} from 'node:crypto';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the exact reviewed OpenClaw package');
const area=fs.mkdtempSync(path.join(os.tmpdir(),'ods-completed-turn-'));
process.env.OPENCLAW_STATE_DIR=path.join(area,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(area,'config.json');
const hash=text=>createHash('sha256').update(text).digest('hex');
const sources=new Map();
for (const [name,module] of [['compaction-empty','proxy-Bsfwfsp-.js'],['compaction-no-work','compact-DuWIsaq_.js'],['compaction-resume','sessions-CZbwb3_c.js']]) {
  const manifest=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
  const filename=path.join(pkg,'dist',module);
  let source=fs.readFileSync(filename,'utf8');
  const digest=hash(source),old=digest===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[digest];
  if(old) for(const [before,after] of [...old].reverse()) {assert.equal(source.split(after).length,2);source=source.replace(after,before);}
  assert.equal(hash(source),manifest.sourceSha256);
  const prior=process.env.ODS_COMPLETED_TURN_BASELINE==='1'&&({
    'compaction-empty':'3f906f4ba114ed5469d49da38388532bb441a59a05e952e91d7b33e60cc9ac63',
    'compaction-resume':'e7f5214e2c1289efea7beb3dae1e3ceed4ae68bb4a05007ffa8d63cd0e593eec',
  })[name];
  for(const [before,after] of prior?manifest.previousReplacements[prior]:manifest.replacements) {assert.equal(source.split(before).length,2);source=source.replace(before,after);}
  assert.equal(hash(source),prior||manifest.patchedSha256);
  sources.set(pathToFileURL(filename).href,source);
}
const loader=registerHooks({load(url,context,next){return sources.has(url)?{format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const {compactEmbeddedAgentSessionDirect:compact}=await import(pathToFileURL(path.join(pkg,'dist/compact-DuWIsaq_.js')));
const {t:SessionManager}=await import(pathToFileURL(path.join(pkg,'dist/session-manager-3lTZxT-y.js')));
test.after(async()=>{
  const {n:close}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  close();loader.deregister();await fs.promises.rm(area,{recursive:true,force:true});
});

const marker='ods.completed-turn-pressure-boundary';
const answer=Array.from({length:46},(_,i)=>`- project-${String(i).padStart(2,'0')}-configuration-and-notes.txt: file`).join('\n');
const final=(overrides={})=>({role:'assistant',content:[{type:'text',text:answer}],stopReason:'stop',usage:{input:4000,output:700,totalTokens:4700},...overrides});
const messages=()=>[{role:'user',content:'List the workspace read-only. Do not change any file.'},final()];
const rows=messages=>messages.map((message,i)=>({type:'message',id:`entry${i}`,parentId:i?`entry${i-1}`:null,message}));
const read=file=>fs.readFileSync(file,'utf8').trim().split('\n').map(JSON.parse);

async function fixture(t,history,{context=8192,trigger='overflow',mode='success',controller,summaryBudget,summaryText,summaryStop='stop'}={}) {
  let responseMode=mode;
  const requests=[];
  const server=http.createServer(async(req,res)=>{
    let text='';for await(const block of req)text+=block;
    requests.push(JSON.parse(text));
    if(responseMode==='abort') {setTimeout(()=>controller.abort(),10);return;}
    if(responseMode==='error') {res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'Synthetic summary failure'}}));return;}
    const summary=summaryText??'The owner requested a read-only workspace listing. The completed answer listed 46 files. No files were changed. Any new request still requires fresh evidence.';
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:summary},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:summaryStop}],usage:{prompt_tokens:1000,completion_tokens:40,total_tokens:1040}})+'\n\ndata: [DONE]\n\n');
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const workspace=fs.mkdtempSync(path.join(area,'workspace-')),agentDir=fs.mkdtempSync(path.join(area,'agent-'));
  const sessionId=randomUUID(),sessionFile=path.join(area,sessionId+'.jsonl'),input=rows(history);
  fs.writeFileSync(sessionFile,[{type:'session',version:3,id:sessionId,timestamp:new Date().toISOString(),cwd:workspace},...input].map(JSON.stringify).join('\n')+'\n');
  const config={agents:{defaults:{workspace,model:'fixture/model',...typeof context==='number'?{contextTokens:context}:{},compaction:{reserveTokens:3277,reserveTokensFloor:0,keepRecentTokens:512}},list:[{id:'pixel',workspace,agentDir}]},plugins:{enabled:false},
    models:{providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${server.address().port}/v1`,apiKey:'fixture-only',models:[{id:'model',name:'Fixture',...typeof context==='number'?{contextWindow:context}:{},maxTokens:2048,reasoning:false,input:['text','image']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const invoke=(abortSignal=controller?.signal,invokeTrigger=trigger)=>compact({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:fixture-'+sessionId,workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',trigger:invokeTrigger,abortSignal,odsSummaryCharBudget:summaryBudget});
  return {invoke,sessionFile,input,requests,setMode:value=>{responseMode=value;},entries:()=>read(sessionFile),context:()=>SessionManager.open(sessionFile).buildSessionContext().messages};
}

for(const kind of ['plain','thinking','resolved tools']) test(`8K overflow summarizes a completed ${kind} turn and retains its raw evidence`,async t=>{
  const history=messages();
  if(kind==='thinking')history[1]=final({content:[{type:'thinking',thinking:'Synthetic reasoning about a listing.'},{type:'text',text:answer}]});
  if(kind==='resolved tools')history.splice(1,0,
    {role:'assistant',content:[{type:'toolCall',id:'read-one',name:'exec',arguments:{command:'ls -la /workspace'}}],stopReason:'toolUse'},
    {role:'toolResult',toolCallId:'read-one',toolName:'exec',content:[{type:'text',text:answer}],isError:false});
  const f=await fixture(t,history),result=await f.invoke();
  assert.equal(result.ok,true);assert.equal(result.compacted,true);
  const entries=f.entries(),boundary=entries.filter(e=>e.customType===marker),checkpoint=entries.find(e=>e.type==='compaction');
  assert.equal(boundary.length,1);assert.equal(checkpoint.firstKeptEntryId,boundary[0].id);
  assert.equal(f.requests.length,1);assert.ok(JSON.stringify(f.requests[0]).includes('project-45-configuration-and-notes.txt'),'the complete answer reaches the summarizer');
  assert.equal(checkpoint.summary.includes('Current unfinished user request'),false,'do not relabel the old completed request');
  assert.deepEqual(entries.filter(e=>e.type==='message'),f.input,'raw tool calls and original messages are unchanged');
  assert.equal(f.context().some(m=>m.role==='assistant'&&JSON.stringify(m.content).includes('project-45-configuration-and-notes.txt')),false);
});

test('pressure summarizes the completed answer retained by an earlier native compaction',async t=>{
  const history=messages();
  history.splice(1,0,
    {role:'assistant',content:[{type:'toolCall',id:'read-one',name:'exec',arguments:{command:'ls -la /workspace'}}],stopReason:'toolUse'},
    {role:'toolResult',toolCallId:'read-one',toolName:'exec',content:[{type:'text',text:answer}],isError:false});
  const f=await fixture(t,history),first=await f.invoke(undefined,'manual');
  assert.equal(first.ok,true);assert.equal(first.compacted,true);
  const prior=f.entries().findLast(e=>e.type==='compaction');
  assert.equal(prior.firstKeptEntryId,f.input.at(-1).id,'reproduce the live retained-answer boundary');
  assert.equal(f.entries().some(e=>e.customType===marker),false,'manual compaction stays unchanged');
  const result=await f.invoke();assert.equal(result.ok,true);assert.equal(result.compacted,true);
  const boundary=f.entries().find(e=>e.customType===marker);
  assert.ok(boundary,'pressure must validate the completed raw turn before the retained boundary');
  assert.equal(f.entries().findLast(e=>e.type==='compaction').firstKeptEntryId,boundary.id);
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
  assert.equal(f.context().some(m=>m.role==='assistant'&&JSON.stringify(m.content).includes('project-45-configuration-and-notes.txt')),false);
});

for(const [name,change] of [
  ['unresolved earlier tool',h=>h.splice(1,0,{role:'assistant',content:[{type:'toolCall',id:'unresolved',name:'exec',arguments:{command:'ls'}}],stopReason:'toolUse'})],
  ['earlier attachment',h=>{h[0].content=[{type:'text',text:'Inspect this image.'},{type:'image',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDP8AAAAASUVORK5CYII=',mimeType:'image/png'}];}],
]) test(`prior native compaction does not hide ${name} from pressure validation`,async t=>{
  const history=messages();change(history);
  const f=await fixture(t,history),first=await f.invoke(undefined,'manual');
  assert.equal(first.ok,true);assert.equal(first.compacted,true);
  assert.equal(f.entries().findLast(e=>e.type==='compaction').firstKeptEntryId,f.input.at(-1).id);
  await f.invoke();
  assert.equal(f.entries().some(e=>e.customType===marker),false);
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
});

for(const [name,options,change] of [
  ['manual request',{trigger:'manual'},()=>{}],
  ['larger context',{context:32768},()=>{}],
  ['unspecified context',{context:'unspecified'},()=>{}],
  ['pending new user',{},h=>h.push({role:'user',content:'Now verify the listing with a fresh read-only tool.'})],
  ...['length','error','aborted'].map(stop=>[stop+' assistant',{},h=>{h[1]=final({stopReason:stop});}]),
  ['unresolved tool',{},h=>h.splice(1,0,{role:'assistant',content:[{type:'toolCall',id:'unresolved',name:'exec',arguments:{command:'ls'}}],stopReason:'toolUse'})],
  ['non-text attachment',{},h=>{h[0].content=[{type:'text',text:'Inspect this image.'},{type:'image',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDP8AAAAASUVORK5CYII=',mimeType:'image/png'}];}],
]) test(`completed-turn pressure boundary stays off for ${name}`,async t=>{
  const history=messages();change(history);
  const f=await fixture(t,history,options);await f.invoke();
  assert.equal(f.entries().some(e=>e.customType===marker),false,JSON.stringify(f.entries().filter(e=>e.type==='message').map(e=>({role:e.message.role,content:typeof e.message.content==='string'?e.message.content:e.message.content.map(b=>({type:b.type,text:b.text?.slice(0,150)}))}))));
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
});

for(const mode of ['error','abort']) test(`${mode} keeps raw history and an inert reusable boundary`,async t=>{
  const controller=new AbortController(),f=await fixture(t,messages(),{mode,controller});
  const failed=await f.invoke();assert.equal(failed.ok,false);assert.equal(failed.compacted,false);
  assert.equal(f.entries().filter(e=>e.customType===marker).length,1);
  assert.equal(f.entries().some(e=>e.type==='compaction'),false);
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
  assert.ok(f.context().some(m=>m.role==='assistant'&&JSON.stringify(m.content).includes('project-45-configuration-and-notes.txt')),'metadata alone never discards history');
  f.setMode('success');const recovered=await f.invoke(new AbortController().signal);
  assert.equal(recovered.ok,true);assert.equal(recovered.compacted,true);
  assert.equal(f.entries().filter(e=>e.customType===marker).length,1,'retry reuses the same boundary');
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
});

for(const scenario of [
  {name:'no available summary room',summaryBudget:0,calls:0},
  {name:'provider exceeds character budget',summaryBudget:100,calls:1},
  {name:'non-Latin text exceeds weighted budget',summaryBudget:100,summaryText:'用户要求只读列出工作区。不要修改文件。结果已经显示，但后续请求需要新的工具证据。',calls:1},
  {name:'provider stops at its output limit',summaryBudget:800,summaryStop:'length',calls:1},
  {name:'provider returns an empty checkpoint',summaryBudget:800,summaryText:'',calls:1},
]) test('completed-turn budget rejects '+scenario.name+' without saving a checkpoint',async t=>{
  const f=await fixture(t,messages(),scenario),result=await f.invoke();
  assert.equal(result.ok,false);
  assert.equal(f.requests.length,scenario.calls);
  assert.equal(f.entries().filter(e=>e.type==='compaction').length,0);
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
});

test('completed-turn budget bounds summary generation without changing chat reserves',async t=>{
  const f=await fixture(t,messages(),{summaryBudget:200}),result=await f.invoke();
  assert.equal(result.ok,true);assert.equal(result.compacted,true);
  assert.equal(f.requests[0].max_tokens??f.requests[0].max_completion_tokens,50);
  assert.ok(f.entries().find(e=>e.type==='compaction').summary.includes('No files were changed'));
});

test('larger-context manual compaction ignores the completed-turn-only budget',async t=>{
  const f=await fixture(t,messages(),{context:32768,trigger:'manual',summaryBudget:0}),result=await f.invoke();
  assert.equal(result.ok,true);assert.equal(result.compacted,true);
  assert.ok(f.requests.every(r=>(r.max_tokens??r.max_completion_tokens)>50));
});

test('unfinished split-turn compaction ignores a completed-turn-only zero budget',async t=>{
  const history=messages();history[1]=final({stopReason:'length'});
  const f=await fixture(t,history,{summaryBudget:0}),result=await f.invoke();
  assert.equal(result.ok,true);assert.equal(result.compacted,true);
  assert.equal(f.entries().some(e=>e.customType===marker),false);
  const checkpoint=f.entries().find(e=>e.type==='compaction');
  assert.ok(checkpoint.summary.includes('Turn Context (split turn)'));
  assert.ok(checkpoint.summary.includes('Current unfinished user request'));
  assert.deepEqual(f.entries().filter(e=>e.type==='message'),f.input);
});
