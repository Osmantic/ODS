// Real compiled SDK provider and diagnostic wrapper; no network or inference.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

const source=process.env.OPENCLAW_PACKAGE_DIR;
assert(source && path.isAbsolute(source));
assert.equal(JSON.parse(fs.readFileSync(path.join(source,'package.json'))).version,'2026.6.33');
const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'ods-probe-sdk-')));
process.once('exit',()=>fs.rmSync(root,{recursive:true,force:true}));
const runtimeRequire=createRequire(path.join(source,'package.json'));
const sha=x=>crypto.createHash('sha256').update(x).digest('hex');
const files=[['openclaw-probe-context.json','attempt.model-diagnostic-events-DqqiPQPY.js'],['openclaw-probe-provider.json','openai-completions-DTj6G8AI.js']];
// Dependency imports keep referring to immutable reviewed runtime. Only these
// two exact source modules are copied and transformed, in owned scratch files.
for(const [name,file] of files){
  const manifest=JSON.parse(fs.readFileSync(new URL('../host/'+name,import.meta.url)));
  let text=fs.readFileSync(path.join(source,'dist',file),'utf8');assert.equal(sha(text),manifest.sourceSha256);
  for(const [before,after] of manifest.replacements){assert.equal(text.split(before).length,2);text=text.replace(before,()=>after);}
  assert.equal(sha(text),manifest.patchedSha256);
  text=text.replace(/(from\s+|import\s+|import\()(["'])\.\/([^"']+)\2/g,(_,prefix,quote,relative)=>prefix+quote+pathToFileURL(path.join(source,'dist',relative)).href+quote);
  text=text.replace('from "openai"','from '+JSON.stringify(pathToFileURL(runtimeRequire.resolve('openai')).href));
  fs.writeFileSync(path.join(root,file),text);
}
const originalFetch=globalThis.fetch,oldLease=process.env.ODS_PIXEL_PROBE_ROOT;
const context={agentId:'pixel',runId:'owned-run',sessionId:'owned-session',sessionKey:'agent:pixel:owned',provider:'ods-local',modelId:'ods/current',baseUrl:'http://127.0.0.1:4102/v1'};
const model={id:context.modelId,name:'Owned test',provider:context.provider,api:'openai-completions',baseUrl:context.baseUrl,reasoning:false,input:['text'],contextWindow:65536,maxTokens:8192,cost:{input:0,output:0,cacheRead:0,cacheWrite:0},headers:{'X-ODS-Probe':'forged-model-header'},compat:{supportsStore:false,supportsUsageInStreaming:true,maxTokensField:'max_tokens'}};
let calls=[],mode='normal';
globalThis.fetch=async (url,init)=>{
  calls.push({url:String(url),body:init.body,headers:new Headers(init.headers),signal:init.signal});
  if(mode==='retry' && calls.length===1)return new Response('{}',{status:429,headers:{'content-type':'application/json','retry-after-ms':'1'}});
  if(mode==='cancel')throw new DOMException('Aborted','AbortError');
  const data=[{id:'response-owned',choices:[{index:0,delta:{role:'assistant',content:'42'},finish_reason:null}]},{id:'response-owned',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:1,total_tokens:11,prompt_tokens_details:{cached_tokens:4}}}];
  return new Response(data.map(x=>'data: '+JSON.stringify(x)+'\n\n').join('')+'data: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
};
const {r:provider}=await import(pathToFileURL(path.join(root,files[1][1])));
const {t:wrap}=await import(pathToFileURL(path.join(root,files[0][1])));
const {r:originalProvider}=await import(pathToFileURL(path.join(source,'dist',files[1][1])));
const {t:originalWrap}=await import(pathToFileURL(path.join(source,'dist',files[0][1])));
function lease(){
  fs.chmodSync(root,0o700);const value={schemaVersion:1,...context,ownerUid:process.getuid(),scopeId:crypto.randomUUID(),signingKey:crypto.randomBytes(32).toString('hex'),expiresAtMs:Date.now()+60000,maxAttempts:16};
  const file=path.join(root,'run-'+sha(context.runId)+'.json');fs.writeFileSync(file,JSON.stringify(value),{mode:0o600});process.env.ODS_PIXEL_PROBE_ROOT=root;return value;
}
async function run(change={},options={},implementation=provider,wrapper=wrap){
  let seq=0;const fn=wrapper(implementation,{...context,model:context.modelId,api:model.api,trace:{traceId:'a'.repeat(32),spanId:'b'.repeat(16),traceFlags:'01'},nextCallId:()=>`owned-run:model:${++seq}`,...change});
  const stream=fn(model,{systemPrompt:'Keep exact arithmetic.',messages:[{role:'user',content:'What is 6 times 7?',timestamp:1}]},{apiKey:'fixture-only',maxTokens:64,maxRetries:1,headers:{'x-ods-probe-unsafe':'must-strip'},...options});
  const events=[];for await(const event of stream)events.push(event);return {events,result:await stream.result()};
}
test('real SDK final fetch signs exact serialized bytes for each retry and preserves stream/usage',async()=>{
  const l=lease();calls=[];mode='retry';const result=await run();assert.equal(calls.length,2);assert.equal(result.result.content[0].text,'42');assert(result.events.some(e=>e.type==='text_delta'));
  assert.equal(result.result.usage.cacheRead,4);
  const ids=[];for(const c of calls){const [scope,id,sig]=c.headers.get('x-ods-probe').split('.');ids.push(id);assert.equal(scope,l.scopeId);assert(!c.body.includes(scope));assert(!c.headers.has('x-ods-probe-unsafe'));const signed=Buffer.concat([Buffer.from('ods.probe-header.v1\0'+scope+'\0'+id+'\0'),crypto.createHash('sha256').update(c.body).digest()]);assert.equal(sig,crypto.createHmac('sha256',l.signingKey).update(signed).digest('base64url'));}
  assert.notEqual(ids[0],ids[1]);assert.equal(calls[0].body,calls[1].body);
});
test('real SDK unowned context gets no diagnostic header and unchanged model input',async()=>{
  lease();mode='normal';calls=[];await run({runId:'other-request'});assert.equal(calls.length,1);assert(![...calls[0].headers.keys()].some(k=>k.startsWith('x-ods-probe')));const observed=calls[0].body;calls=[];await run({runId:'other-request'}, {},originalProvider,originalWrap);assert.equal(calls.length,1);assert.equal(observed,calls[0].body,'full final serialized model request equals unrepaired pinned SDK');
});
test('real SDK cancellation is not retried by diagnostic wrapper',async()=>{
  lease();mode='cancel';calls=[];const result=await run({}, {maxRetries:0});assert.equal(calls.length,1);assert.equal(result.result.stopReason,'error');
});
test.after(()=>{globalThis.fetch=originalFetch;if(oldLease===undefined)delete process.env.ODS_PIXEL_PROBE_ROOT;else process.env.ODS_PIXEL_PROBE_ROOT=oldLease;fs.rmSync(root,{recursive:true,force:true});});
