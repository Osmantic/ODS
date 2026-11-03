// Actual pinned native catalog + actual Source factory schemas, with all
// execution stopped at a fixture counter. No Source records/provider/model runs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createHash,randomUUID} from 'node:crypto';
import {registerHooks} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import http from 'node:http';
import {once} from 'node:events';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {reviewedNativeRevisionDependencies} from './fixtures/native-revision-dependencies.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR,typebox=process.env.PIXEL_TEST_TYPEBOX_DIR;
assert.ok(pkg&&typebox,'provide the pinned native package and locked Source TypeBox');
assert.equal(JSON.parse(fs.readFileSync(path.join(pkg,'package.json'))).version,'2026.6.33');
const privateRoot=fs.mkdtempSync(path.join(os.tmpdir(),'ods-cold-schema-'));
const rootIdentity=fs.lstatSync(privateRoot,{bigint:true});
process.env.HOME=path.join(privateRoot,'home');fs.mkdirSync(process.env.HOME);
process.env.OPENCLAW_STATE_DIR=path.join(privateRoot,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(privateRoot,'config.json');
const sourceRoot=fileURLToPath(new URL('../../../../vendor/pixel/plugin/',import.meta.url));
const sourceUrl=pathToFileURL(sourceRoot+path.sep).href;
assert.equal(JSON.parse(fs.readFileSync(path.join(typebox,'package.json'))).version,
  JSON.parse(fs.readFileSync(path.join(sourceRoot,'package.json'))).dependencies.typebox);
const modulePath=path.join(pkg,'dist/tool-search-BInRpkE3.js'),moduleUrl=pathToFileURL(modulePath).href;
const nativeSources=new Map([...reviewedNativeRevisionDependencies(pkg)].map(([name,value])=>[pathToFileURL(name).href,value]));
const manifest=JSON.parse(fs.readFileSync(new URL('../host/openclaw-image-envelope.json',import.meta.url)));
const sha=value=>createHash('sha256').update(value).digest('hex');
const installedBefore=sha(fs.readFileSync(modulePath));
let source=fs.readFileSync(modulePath,'utf8');
const prior=installedBefore===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[installedBefore];
if(prior)for(const [before,after] of [...prior].reverse()){assert.equal(source.split(after).length,2);source=source.replace(after,()=>before);}
assert.equal(sha(source),manifest.sourceSha256);
for(const [before,after] of manifest.replacements){assert.equal(source.split(before).length,2);source=source.replace(before,()=>after);}
assert.equal(sha(source),manifest.patchedSha256);
for(const [name,digest] of Object.entries(manifest.reviewedDependencies))
  assert.equal(sha(fs.readFileSync(path.join(pkg,'dist',name))),digest);
const alias={
  typebox:pathToFileURL(path.join(typebox,'build/index.mjs')).href,
  'typebox/value':pathToFileURL(path.join(typebox,'build/value/index.mjs')).href,
  'openclaw/plugin-sdk/plugin-entry':pathToFileURL(path.join(pkg,'dist/plugin-sdk/plugin-entry.js')).href,
};
const loader=registerHooks({
  resolve(specifier,context,next){return context.parentURL?.startsWith(sourceUrl)&&alias[specifier]
    ?{url:alias[specifier],shortCircuit:true}:next(specifier,context);},
  load(url,context,next){return url===moduleUrl?{format:'module',source,shortCircuit:true}:nativeSources.has(url)
    ?{format:'module',source:nativeSources.get(url),shortCircuit:true}:next(url,context);},
});
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();assert.equal(sha(fs.readFileSync(modulePath)),installedBefore,'input native bytes stay untouched');
  const current=fs.lstatSync(privateRoot,{bigint:true});
  assert.equal(current.dev,rootIdentity.dev);assert.equal(current.ino,rootIdentity.ino);
  assert.equal(current.isDirectory()&&!current.isSymbolicLink(),true);
  assert.equal(path.dirname(privateRoot),os.tmpdir());fs.rmSync(privateRoot,{recursive:true});
});
const {u:apply,m:createRef,h:createControls}=await import(moduleUrl);
const {t:filterByPolicy}=await import(pathToFileURL(path.join(pkg,'dist/agent-tools.policy-Dyn4VuLn.js')));
const flags={PIXEL_AGENT_ID:'pixel',PIXEL_LIMB_EMAIL_ENABLED:'1',PIXEL_LIMB_CALENDAR_ENABLED:'1',
  PIXEL_LIMB_CALENDAR_WRITE_ENABLED:'0',PIXEL_LIMB_EMAIL_DRAFTS_ENABLED:'0',PIXEL_LIMB_WEB_ENABLED:'0',PIXEL_LIMB_SOCIAL_ENABLED:'0'};
const saved=Object.fromEntries(Object.keys(flags).map(key=>[key,process.env[key]]));
const actualTools=new Map();
try {
  Object.assign(process.env,flags);
  const {default:entry}=await import(pathToFileURL(path.join(sourceRoot,'index.js')));
  entry.register({config:{agents:{defaults:{userTimezone:'America/Sao_Paulo'}}},registerTool(factory,{names}){
    const tool=factory({agentId:'pixel'});if(tool)actualTools.set(names[0],tool);
  }});
}finally{for(const [key,value] of Object.entries(saved))if(value===undefined)delete process.env[key];else process.env[key]=value;}
const tool=name=>({name,description:name,parameters:{type:'object',properties:{}},execute:async()=>{throw Error('no business execution');}});
const controls=['tool_search','tool_describe','tool_call'].map(tool);
function catalog(tools,{window=65536,agentId='pixel',mode='tools'}={}) {
  const catalogRef=createRef(),config={agents:{list:[{id:'pixel',contextTokens:window}]},tools:{toolSearch:{enabled:true,mode}}};
  const result=apply({tools:[...controls,...tools],agentId,config,catalogRef,runId:randomUUID(),modelContextWindowTokens:window});
  const dispatched=[];
  const ctx={agentId,config,catalogRef,executeTool:async request=>{dispatched.push(request);return {content:[]};}};
  return {result,dispatched,api:Object.fromEntries(createControls(ctx).map(value=>[value.name,value]))};
}

for(const window of [8192,32768,65536])test(`actual Source count/read schemas hydrate cold at ${window} with no execution`,async t=>{
  const count=actualTools.get('pixel_calendar_count'),read=actualTools.get('pixel_gmail_read');
  assert.ok(count&&read);
  if(window===65536)t.diagnostic(JSON.stringify({actualFactorySchemaBytes:{calendarCount:Buffer.byteLength(JSON.stringify(count.parameters),'utf8'),gmailRead:Buffer.byteLength(JSON.stringify(read.parameters),'utf8')},inlineLimit:4096}));
  const selected=[count,read],value=catalog(selected,{window});
  assert.ok(!value.result.tools.includes(count),'count direct/deferred surface remains unchanged');
  for(const [original,args,bad] of [[count,{window:{kind:'civil-days',date:'2026-11-04',days:1}},
    {timeMin:'2026-11-04T00:00:00Z',timeMax:'2026-11-04T23:59:59Z'}],
    [read,{messageId:'invented-qa-id'},{id:'invented-qa-id'}]]) {
    const found=await value.api.tool_search.execute('cold-search',{query:original.name,limit:2});
    assert.equal(found.details[0].name,original.name);
    assert.deepEqual(found.details[0].parameters,original.parameters,'the top hit carries the complete ACTUAL Source factory schema');
    assert.ok(Buffer.byteLength(JSON.stringify(found.details[0].parameters),'utf8')<=4096);
    assert.equal(value.dispatched.length,original===count?0:1,'discovery never executes a business tool');
    const before=value.dispatched.length;
    await assert.rejects(value.api.tool_call.execute('invalid',{id:found.details[0].id,args:bad}));
    assert.equal(value.dispatched.length,before,'invalid guessed args are refused before dispatch');
    await value.api.tool_call.execute('typed',{id:found.details[0].id,args});
    assert.equal(value.dispatched.length,before+1);
    assert.deepEqual(value.dispatched.at(-1).input,args);
  }
  const all=await value.api.tool_search.execute('all',{query:'',limit:2});
  assert.equal(all.details.length,2);
  assert.ok(all.details[0].parameters);
  assert.equal(all.details[1].parameters,undefined);
  assert.equal(all.details[1].parametersDeferred,true);
  assert.deepEqual(all.details[1].describeWith,{tool:'tool_describe',id:all.details[1].id});
  const described=await value.api.tool_describe.execute('describe',{id:all.details[1].describeWith.id});
  assert.deepEqual(described.details.parameters,selected.find(item=>item.name===all.details[1].name).parameters);
});

function exactBytes(size) {
  const schema={type:'object',description:'',properties:{}};
  const room=size-Buffer.byteLength(JSON.stringify(schema),'utf8');
  schema.description='é'.repeat(Math.floor(room/2))+(room%2?'a':'');
  assert.equal(Buffer.byteLength(JSON.stringify(schema),'utf8'),size);
  return schema;
}
test('native schema hydration is atomic at4096 UTF8 bytes, with an exact describe pointer above the bound',async()=>{
  for(const bytes of [4096,4097]) {
    const large={...tool('fixture_large'),parameters:exactBytes(bytes)},value=catalog([large]);
    const found=await value.api.tool_search.execute('bounded',{query:large.name});
    assert.equal(found.details.length,1);
    if(bytes===4096)assert.deepEqual(found.details[0].parameters,large.parameters);
    else {
      assert.equal(found.details[0].parameters,undefined,'never emit a schema fragment');
      assert.equal(found.details[0].parametersDeferred,true);
      const described=await value.api.tool_describe.execute('full',{id:found.details[0].describeWith.id});
      assert.deepEqual(described.details.parameters,large.parameters);
    }
    assert.equal(value.dispatched.length,0);
  }
});
test('denied or empty catalogs cannot disclose source schemas and other agents keep their prior compact metadata',async()=>{
  const count=actualTools.get('pixel_calendar_count');
  for(const permitted of [[],filterByPolicy([count],{deny:[count.name]})]) {
    const value=catalog(permitted),found=await value.api.tool_search.execute('denied',{query:count.name});
    assert.deepEqual(found.details,[]);
    await assert.rejects(value.api.tool_describe.execute('denied',{id:count.name}));
    await assert.rejects(value.api.tool_call.execute('denied',{id:count.name,args:{window:{kind:'civil-days',date:'2026-11-04',days:1}}}));
    assert.equal(value.dispatched.length,0);
  }
  const value=catalog([count],{agentId:'another-agent'});
  const found=await value.api.tool_search.execute('other',{query:count.name});
  assert.equal(found.details[0].parameters,undefined);
  assert.equal(found.details[0].parametersDeferred,undefined);
  assert.equal(value.dispatched.length,0);
});

// Physical normal-profile wire coverage. The actual writer-derived 8K profile
// remains covered by runtime_8k_prompt_budget.integration.mjs, not a shortened fixture.
for(const window of [65536])for(const name of ['pixel_calendar_count','pixel_gmail_read'])
test(`actual native second provider request retains full cold ${name} schema at ${window}`,{timeout:60000},async t=>{
  const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
  const original=actualTools.get(name),requests=[];
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push(JSON.parse(Buffer.concat(chunks)));
    const first=requests.length===1;
    const delta=first?{role:'assistant',tool_calls:[{index:0,id:'schema-only-search',type:'function',
      function:{name:'tool_search',arguments:JSON.stringify({query:name,limit:2})}}]}:{role:'assistant',content:'READY'};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'schema-fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'schema-fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},
      finish_reason:first?'tool_calls':'stop'}],usage:{prompt_tokens:1000,completion_tokens:10,total_tokens:1010}})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const directory=path.join(privateRoot,`${window}-${name}`);fs.mkdirSync(directory);
  const workspace=path.join(directory,'workspace'),agentDir=path.join(directory,'agent'),plugin=path.join(directory,'plugin');
  for(const folder of [workspace,agentDir,plugin])fs.mkdirSync(folder);
  const prompt='Discover the requested fixture capability, then reply READY. Do not run a business tool.';
  const contract=promptContractForAgent({agentId:'pixel',contextTokenBudget:window},'pixel',{prompt,messages:[]},
    {configuredContextWindow:window});
  const entries=[...actualTools.values()].filter(value=>['pixel_calendar_count','pixel_gmail_read'].includes(value.name))
    .map(({name,description,parameters})=>({name,description,parameters}));
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'pixel-source-broker',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-source-broker',activation:{onStartup:true},
    contracts:{tools:entries.map(value=>value.name)},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'tools.json'),JSON.stringify(entries));
  fs.writeFileSync(path.join(plugin,'contract.json'),JSON.stringify(contract));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`import fs from 'node:fs';
const tools=JSON.parse(fs.readFileSync(new URL('./tools.json',import.meta.url)));
export default{id:'pixel-source-broker',register(api){
 api.on('before_prompt_build',()=>JSON.parse(fs.readFileSync(new URL('./contract.json',import.meta.url))));
 for(const value of tools)api.registerTool({...value,execute:async()=>{throw Error('business execution forbidden in cold-schema fixture');}});
}};`);
  const output=8192;
  const config={agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/model'},contextTokens:window,
    heartbeat:{every:'0m'},compaction:{reserveTokens:Math.ceil(window*0.4),reserveTokensFloor:0,keepRecentTokens:2048}},
    list:[{id:'pixel',default:true,workspace,agentDir}]},
    tools:{allow:[...entries.map(value=>value.name),'tool_search','tool_describe','tool_call'],toolSearch:{enabled:true,mode:'tools'}},
    plugins:{allow:['pixel-source-broker'],load:{paths:[plugin]},entries:{'pixel-source-broker':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture',contextWindow:window,maxTokens:output,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const sessionFile=path.join(directory,'session.jsonl');
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId:`schema-${window}-${name}`,sessionFile,
    sessionKey:`agent:pixel:schema-${window}-${name}`,workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',prompt,
    runId:`schema-${window}-${name}`,trigger:'user',timeoutMs:30000});
  assert.equal(result.meta?.error,undefined,JSON.stringify(result));
  assert.equal(requests.length,2,'one real native search round trip, no business tool or compensating request');
  assert.deepEqual(requests[0].tools,requests[1].tools,'discovery does not expand native function schemas');
  assert.ok(!requests[0].tools.some(value=>value.function?.name===name),'source target remains deferred on initial wire');
  for(const request of requests){
    assert.equal(request.max_tokens??request.max_completion_tokens,output,'configured output budget unchanged');
    assert.ok(request.messages.some(m=>m.role==='system'&&typeof m.content==='string'&&m.content.includes(contract.appendSystemContext)),
      'production core/output contract remains on the physical provider wire');
  }
  const wireResults=requests[1].messages.filter(m=>m.role==='tool');assert.equal(wireResults.length,1);
  const rendered=typeof wireResults[0].content==='string'?wireResults[0].content:wireResults[0].content.map(x=>x.text??'').join('\n');
  const decoded=JSON.parse(rendered),hit=decoded.find(value=>value.name===name);assert.ok(hit);
  assert.deepEqual(hit.parameters,original.parameters,'full ACTUAL Source schema survives text formatter/projection onto next request');
  const persisted=fs.readFileSync(sessionFile,'utf8').trim().split('\n').map(JSON.parse).filter(row=>row.message?.role==='toolResult');
  assert.equal(persisted.length,1);
  const persistedText=persisted[0].message.content.filter(x=>x.type==='text').map(x=>x.text).join('\n');
  assert.deepEqual(JSON.parse(persistedText).find(value=>value.name===name).parameters,original.parameters);
  t.diagnostic(JSON.stringify({window,name,actualSchemaUtf8Bytes:Buffer.byteLength(JSON.stringify(original.parameters)),
    nextRequestToolResultUtf8Bytes:Buffer.byteLength(rendered),configuredOutputTokens:output,businessExecutions:0}));
});
