// Exact pinned native modules; all state/files are disposable, provider is offline.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {registerHooks} from 'node:module';
import http from 'node:http';
import {once} from 'node:events';
import {createContextCompaction} from '../plugin/context-compaction.mjs';

const root=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root,'provide exact reviewed OpenClaw package');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'ods-no-work-'));
process.env.OPENCLAW_STATE_DIR=path.join(temporary,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(temporary,'config.json');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
function recipe(name,module) {
  const manifest=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
  let original=fs.readFileSync(path.join(root,'dist',module),'utf8');
  const digest=sha(original);
  const replacements=digest===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[digest];
  if(replacements)for(const [before,after]of [...replacements].reverse()){assert.equal(original.split(after).length,2);original=original.replace(after,before);}
  assert.equal(sha(original),manifest.sourceSha256,'reject unreviewed runtime source');
  let patched=original;
  for(const [before,after]of manifest.replacements){assert.equal(patched.split(before).length,2);patched=patched.replace(before,after);}
  assert.equal(sha(patched),manifest.patchedSha256);
  return {manifest,original,patched};
}
const proxy=recipe('compaction-empty','proxy-Bsfwfsp-.js');
const wrapper=recipe('compaction-no-work','compact-DuWIsaq_.js');
const sessions=recipe('compaction-resume','sessions-CZbwb3_c.js');
// Explicit red-baseline mode loads the exact previously reviewed repairs,
// never unknown runtime bytes, and expects the same acceptance assertions.
function predecessor(value,digest){let text=value.original;for(const [before,after]of value.manifest.previousReplacements[digest])text=text.replace(before,after);assert.equal(sha(text),digest);return text;}
const baseline=process.env.ODS_NO_WORK_BASELINE==='1';
const sources=new Map([
  [pathToFileURL(path.join(root,'dist/proxy-Bsfwfsp-.js')).href,baseline?predecessor(proxy,'724abca2876c4ca0f2881330f8fc72cfcd276937c6584302205e845a696e4d25'):proxy.patched],
  [pathToFileURL(path.join(root,'dist/compact-DuWIsaq_.js')).href,baseline?wrapper.original:wrapper.patched],
  [pathToFileURL(path.join(root,'dist/sessions-CZbwb3_c.js')).href,baseline?predecessor(sessions,'bed5ac34cb82fa3d126f244538faf0b01d8a630e3506bd3ecf0db10be970c266'):sessions.patched],
]);
const loader=registerHooks({load(url,context,next){return sources.has(url)?{format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const native=await import(pathToFileURL(path.join(root,'dist/proxy-Bsfwfsp-.js')));
const {compactEmbeddedAgentSessionDirect}=await import(pathToFileURL(path.join(root,'dist/compact-DuWIsaq_.js')));
const {i:openStateDatabase,n:closeStateDatabases}=await import(pathToFileURL(path.join(root,'dist/openclaw-state-db-BtpXMqJX.js')));
test.after(async()=>{
  // The native compaction wrapper caches the shared SQLite connection. Windows
  // correctly refuses to remove that open file; close its actual owner first.
  const {db}=openStateDatabase();
  closeStateDatabases();
  assert.equal(db.isOpen,false,'native shared state handle must be closed before cleanup');
  loader.deregister();
  await fs.promises.rm(temporary,{recursive:true,force:true});
});

function harness(entries) {
  const storage=new native.l({entries}),session=new native.P(storage);
  let providerCalls=0;
  const instance=new native.p({session,model:{id:'fixture',api:'openai-completions',provider:'fixture',contextWindow:131072,maxTokens:1024},
    getApiKeyAndHeaders:async()=>({apiKey:'fixture-only'}),runtime:{completeSimple:async()=>{providerCalls++;throw Error('offline provider should not run');}}});
  return {instance,storage,session,get providerCalls(){return providerCalls;}};
}
const history=()=>[
  {type:'message',id:'a',parentId:null,message:{role:'user',content:'Review the project.'}},
  {type:'message',id:'b',parentId:'a',message:{role:'assistant',content:[{type:'text',text:'Waiting for review.'}],stopReason:'stop',usage:{input:2000,output:20,totalTokens:2020}}},
];

test('actual harness emits typed no-work before provider and leaves history and idle state intact',async()=>{
  const rows=history(),f=harness(rows),before=JSON.stringify(await f.session.getEntries());
  await assert.rejects(f.instance.compact(),error=>error instanceof native.q&&error.code==='compaction_not_needed');
  assert.equal(f.instance.phase,'idle');assert.equal(f.providerCalls,0);
  assert.equal(JSON.stringify(await f.session.getEntries()),before);
  await f.instance.appendMessage({role:'user',content:'Continue after Stop.'});
  assert.equal((await f.session.getEntries()).length,rows.length+1,'next turn can append without a stale compaction phase');
});

async function caller(t,rows,{mode='success',abortSignal,timeoutSeconds,trigger='manual',pressure=false}={}) {
  let requests=0;const requestBodies=[];
  const server=http.createServer(async(req,res)=>{
    let body='';for await(const chunk of req)body+=chunk;requestBodies.push(JSON.parse(body));requests++;
    if(mode==='error'){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'Nothing to compact',type:'invalid_request_error',code:'compaction_not_needed'}}));return;}
    if(mode==='hang')return;
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{content:'Verified retained project summary.'},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:60000,completion_tokens:10,total_tokens:60010}})+'\n\ndata: [DONE]\n\n');
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const area=fs.mkdtempSync(path.join(temporary,'caller-'));
  const workspace=path.join(area,'workspace'),agentDir=path.join(area,'agent');
  fs.mkdirSync(workspace,{recursive:true});fs.mkdirSync(agentDir,{recursive:true});
  const sessionId='11111111-2222-4333-8444-555555555555',sessionFile=path.join(area,sessionId+'.jsonl');
  fs.writeFileSync(sessionFile,[{type:'session',version:3,id:sessionId,timestamp:new Date().toISOString(),cwd:workspace},...rows].map(JSON.stringify).join('\n')+'\n');
  const config={agents:{defaults:{model:'fixture/model',workspace,...pressure?{compaction:{reserveTokens:13108,reserveTokensFloor:0,keepRecentTokens:2048,...timeoutSeconds?{timeoutSeconds}:{}}}:timeoutSeconds?{compaction:{timeoutSeconds}}:{}},list:[{id:'pixel',workspace,agentDir}]},
    models:{providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${server.address().port}/v1`,apiKey:'fixture-only',models:[{id:'model',name:'fixture',contextWindow:pressure?32768:131072,maxTokens:pressure?8192:1024}]}}},
    plugins:{enabled:false}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const invoke=()=>compactEmbeddedAgentSessionDirect({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:fixture',workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',trigger,abortSignal});
  return {invoke,sessionFile,requestBodies,get requests(){return requests;}};
}
const readRows=file=>fs.readFileSync(file,'utf8').trim().split('\n').map(JSON.parse);
test('actual native compact wrapper reports confirmed skip for a short conversation',async t=>{
  const rows=history(),f=await caller(t,rows);const before=fs.readFileSync(f.sessionFile,'utf8');
  const result=await f.invoke();
  assert.deepEqual(result,{ok:true,compacted:false,reason:'no compactable conversation messages'});
  assert.equal(f.requests,0);
  assert.deepEqual(readRows(f.sessionFile).filter(row=>row.type==='message'),rows,'conversation messages unchanged');
  assert.equal(readRows(f.sessionFile).some(row=>row.type==='compaction'),false);
  assert.ok(fs.readFileSync(f.sessionFile,'utf8').startsWith(before),'only native model/thinking metadata may be appended');
});

function longHistory(){
  const rows=[];
  for(let i=0;i<60;i++)rows.push({type:'message',id:`m${i}`,parentId:i?`m${i-1}`:null,message:i%2?{role:'assistant',content:[{type:'text',text:'Reviewed project evidence. '.repeat(220)}],stopReason:'stop',usage:{input:90000,output:50,totalTokens:90050}}:{role:'user',content:'Please retain this project detail. '.repeat(220)}});
  return rows;
}
test('provider failure containing no-work text stays failed and preserves history',async t=>{
  const rows=longHistory(),f=await caller(t,rows,{mode:'error'});
  const result=await f.invoke();
  assert.equal(result.ok,false);assert.equal(result.compacted,false);assert.ok(f.requests>0);
  assert.deepEqual(readRows(f.sessionFile).filter(row=>row.type==='message'),rows);
});
test('necessary compaction still calls the real offline provider and saves a checkpoint',async t=>{
  const f=await caller(t,longHistory());const result=await f.invoke();
  assert.equal(result.ok,true);assert.equal(result.compacted,true);assert.ok(f.requests>0);
  assert.equal(typeof result.result.summary,'string');assert.ok(readRows(f.sessionFile).some(row=>row.type==='compaction'));
});
test('cancellation of needed compaction is never converted into a skip',async t=>{
  const controller=new AbortController(),f=await caller(t,longHistory(),{mode:'hang',abortSignal:controller.signal});
  const timer=setTimeout(()=>controller.abort(),1500);t.after(()=>clearTimeout(timer));
  const result=await f.invoke();assert.equal(result.ok,false);assert.equal(result.compacted,false);
});
test('native compaction deadline is not classified as a no-work skip',async t=>{
  const f=await caller(t,longHistory(),{mode:'hang',timeoutSeconds:1});
  const result=await f.invoke();assert.equal(result.ok,false);assert.equal(result.compacted,false);assert.ok(f.requests>0);
});
test('already-compacted harness has a typed no-work outcome without another provider call',async()=>{
  const f=harness([{type:'compaction',id:'checkpoint',parentId:null,summary:'Previous verified summary.',firstKeptEntryId:'checkpoint',tokensBefore:2000}]);
  const before=JSON.stringify(await f.session.getEntries());
  await assert.rejects(f.instance.compact(),error=>error instanceof native.q&&error.code==='compaction_not_needed');
  assert.equal(f.providerCalls,0);assert.equal(f.instance.phase,'idle');assert.equal(JSON.stringify(await f.session.getEntries()),before);
});
test('authenticated exact-session no-work result releases maintenance and permits the next turn',async t=>{
  const f=await caller(t,history()),user='ods-'+'a'.repeat(64),sessionKey='agent:pixel:openai-user:'+user;
  let token=null;const admission={status:()=>({available:true,phase:token?'held':'idle',revision:'fixture'}),
    acquire:value=>{assert.equal(token,null);token=value;},owns:value=>token===value,release:value=>{assert.equal(token,value);token=null;}};
  let settled;const completion=new Promise(resolve=>{settled=resolve;});
  const runtime=createContextCompaction({directory:path.join(temporary,'context-ledger'),admission,
    readSession:()=>({sessionId:'fixture-session'}),callGateway:async(method,options,payload)=>{
      assert.equal(method,'sessions.compact');assert.equal(payload.key,sessionKey);
      try{return {key:sessionKey,...await f.invoke()};}finally{setImmediate(settled);}
    }});
  assert.equal((await runtime.compact(user,'after-stop')).compaction.status,'running');await completion;
  assert.equal(runtime.context(user).compaction.status,'skipped');assert.equal(token,null);
  let next=false;await runtime.withMaintenance(user,()=>{next=true;});assert.equal(next,true);assert.equal(token,null);
  assert.equal(f.requests,0);
});

// Public synthetic messages preserve the measured failing shape: a single user
// turn, 14 messages, 2158 heuristic tokens, with 1988 after its first message.
// No production prompt, tool result, credential or private reasoning is copied.
function pressureHistory() {
  const messages=[];
  const text=(value,tokens)=>value.padEnd(tokens*4,'.');
  const user='Delegate two short native read-only reviews, wait for both, and combine findings. Do not edit or publish. OUTPUT_CONTRACT_SENTINEL';
  messages.push({role:'user',content:text(user,170),timestamp:1});
  const call=(tokens,calls)=>{
    const blocks=calls.map(([id,name])=>({type:'toolCall',id,name,arguments:{}}));
    const chars=blocks.reduce((n,b)=>n+b.name.length+JSON.stringify(b.arguments).length,0);
    blocks.push({type:'text',text:'.'.repeat(tokens*4-chars)});
    messages.push({role:'assistant',content:blocks,stopReason:'toolUse',api:'openai-completions',provider:'fixture',model:'model',usage:{input:19500,output:100,totalTokens:19600},timestamp:1});
  };
  const result=(tokens,id,name)=>messages.push({role:'toolResult',toolCallId:id,toolName:name,content:[{type:'text',text:text('Public fixture result',tokens)}],isError:false,timestamp:1});
  call(33,[['search','tool_search']]);result(192,'search','tool_search');
  call(172,[['bad-one','tool_call'],['bad-two','tool_call']]);result(19,'bad-one','tool_call');result(19,'bad-two','tool_call');
  call(37,[['describe','tool_describe']]);result(737,'describe','tool_describe');
  call(174,[['child-one','sessions_spawn'],['child-two','sessions_spawn']]);result(231,'child-one','sessions_spawn');result(231,'child-two','sessions_spawn');
  call(46,[['poll','process']]);result(48,'poll','process');
  messages.push({role:'assistant',content:[{type:'text',text:text('Waiting for both child results.',49)}],stopReason:'stop',api:'openai-completions',provider:'fixture',model:'model',usage:{input:20099,output:49,totalTokens:20148},timestamp:1});
  return messages.map((message,i)=>({type:'message',id:`pressure-${i}`,parentId:i?`pressure-${i-1}`:null,message}));
}
function assertPairedTail(rows,firstKept) {
  const start=rows.findIndex(r=>r.id===firstKept);assert.ok(start>0);
  const calls=new Set();
  for(const {message}of rows.slice(start)) {
    if(message.role==='assistant')for(const part of message.content)if(part.type==='toolCall')calls.add(part.id);
    if(message.role==='toolResult')assert.ok(calls.has(message.toolCallId),'retained result must retain its originating call');
  }
}
test('pressure fixture reproduces exact native no-work boundary with safe split available',()=>{
  const rows=pressureHistory();assert.equal(rows.length,14);
  assert.equal(rows.reduce((n,r)=>n+native.E(r.message),0),2158);
  assert.equal(native.D(rows,0,rows.length,2048).firstKeptEntryIndex,0);
  const cut=native.D(rows,0,rows.length,1024);assert.equal(cut.firstKeptEntryIndex,8);assert.equal(cut.isSplitTurn,true);
  assertPairedTail(rows,rows[cut.firstKeptEntryIndex].id);
});
test('overflow recovery compacts a retained short turn and preserves exact owner request and tool pairing',async t=>{
  const rows=pressureHistory(),f=await caller(t,rows,{pressure:true,trigger:'overflow'});
  const outcome=await f.invoke();assert.equal(outcome.ok,true);assert.equal(outcome.compacted,true);
  assert.equal(f.requests,1,'one bounded preparation retry, one summary call');
  const checkpoint=readRows(f.sessionFile).find(r=>r.type==='compaction');assert.ok(checkpoint);
  assert.ok(checkpoint.summary.includes(JSON.stringify(rows[0].message.content)),'exact unfinished request survives the cut');
  assert.match(checkpoint.summary,/do not replay completed actions/);
  assertPairedTail(rows,checkpoint.firstKeptEntryId);
  assert.deepEqual(readRows(f.sessionFile).filter(r=>r.type==='message'),rows,'compaction must never dispatch or rewrite original tools/messages');
  assert.ok(f.requestBodies.every(b=>b.max_tokens<=8192||b.max_completion_tokens<=8192),'model output limit remains bounded');
});
test('manual compaction of identical short turn remains no-work without provider',async t=>{
  const f=await caller(t,pressureHistory(),{pressure:true});const outcome=await f.invoke();
  assert.equal(outcome.ok,true);assert.equal(outcome.compacted,false);assert.equal(f.requests,0);
});
test('pressure fallback with no safe smaller cut remains no-work',async t=>{
  const f=await caller(t,history(),{pressure:true,trigger:'overflow'});const outcome=await f.invoke();
  assert.equal(outcome.ok,true);assert.equal(outcome.compacted,false);assert.equal(f.requests,0);
});
test('pressure summary provider failure remains failed without checkpoint',async t=>{
  const rows=pressureHistory(),f=await caller(t,rows,{pressure:true,trigger:'overflow',mode:'error'});
  const outcome=await f.invoke();assert.equal(outcome.ok,false);assert.equal(outcome.compacted,false);assert.equal(f.requests,1);
  assert.equal(readRows(f.sessionFile).some(r=>r.type==='compaction'),false);
  assert.deepEqual(readRows(f.sessionFile).filter(r=>r.type==='message'),rows);
});
test('pressure recovery respects cancellation while summary is pending',async t=>{
  const controller=new AbortController(),f=await caller(t,pressureHistory(),{pressure:true,trigger:'overflow',mode:'hang',abortSignal:controller.signal});
  const timer=setTimeout(()=>controller.abort(),500);t.after(()=>clearTimeout(timer));
  const outcome=await f.invoke();assert.equal(outcome.ok,false);assert.equal(outcome.compacted,false);
  assert.equal(readRows(f.sessionFile).some(r=>r.type==='compaction'),false);
});
test('pressure recovery respects configured summary deadline',async t=>{
  const f=await caller(t,pressureHistory(),{pressure:true,trigger:'overflow',mode:'hang',timeoutSeconds:1});
  const outcome=await f.invoke();assert.equal(outcome.ok,false);assert.equal(outcome.compacted,false);assert.equal(f.requests,1);
  assert.equal(readRows(f.sessionFile).some(r=>r.type==='compaction'),false);
});

test('pressure recovery preserves text-block owner content verbatim',async t=>{
  const rows=pressureHistory();rows[0].message.content=[{type:'text',text:rows[0].message.content.slice(0,100)},{type:'text',text:rows[0].message.content.slice(100)}];
  const f=await caller(t,rows,{pressure:true,trigger:'overflow'}),outcome=await f.invoke();
  assert.equal(outcome.ok,true);assert.equal(outcome.compacted,true);
  assert.ok(readRows(f.sessionFile).find(r=>r.type==='compaction').summary.includes(JSON.stringify(rows[0].message.content)));
});
test('pressure compaction refuses to lose a non-text owner attachment',async t=>{
  const rows=pressureHistory();rows[0].message.content=[{type:'text',text:rows[0].message.content},{type:'image',data:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jVJkAAAAASUVORK5CYII=',mimeType:'image/png'}];
  const f=await caller(t,rows,{pressure:true,trigger:'overflow'}),outcome=await f.invoke();
  assert.equal(outcome.ok,false);assert.equal(outcome.compacted,false);assert.match(outcome.reason,/non-text attachment/);
  assert.equal(readRows(f.sessionFile).some(r=>r.type==='compaction'),false);
  assert.deepEqual(readRows(f.sessionFile).filter(r=>r.type==='message'),rows);
});
test('pressure compaction refuses an owner request beyond verbatim preservation budget',async t=>{
  const rows=pressureHistory();rows[0].message.content+='x'.repeat(32769);
  const f=await caller(t,rows,{pressure:true,trigger:'overflow'}),outcome=await f.invoke();
  assert.equal(outcome.ok,false);assert.equal(outcome.compacted,false);assert.match(outcome.reason,/preservation budget/);
  assert.equal(readRows(f.sessionFile).some(r=>r.type==='compaction'),false);
  assert.deepEqual(readRows(f.sessionFile).filter(r=>r.type==='message'),rows);
});
