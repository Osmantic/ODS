// Pinned native precheck, schema conversion and loopback compaction. No model,
// installed config or business tool executes; summaries below are canned.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {once} from 'node:events';
import {createHash,randomUUID} from 'node:crypto';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
import {reviewedNativeRevisionDependencies} from './fixtures/native-revision-dependencies.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;assert.ok(pkg,'provide the pinned reviewed native package');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-schema-pressure-'));
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const sha=x=>createHash('sha256').update(x).digest('hex');
const sources=new Map([...reviewedNativeRevisionDependencies(pkg)].map(([file,source])=>[pathToFileURL(file).href,source]));
for(const [name,module] of [['compaction-budget','selection-BEwSQKM-.js'],['compaction-empty','proxy-Bsfwfsp-.js'],
 ['compaction-no-work','compact-DuWIsaq_.js'],['compaction-resume','sessions-CZbwb3_c.js']]){
 const recipe=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
 const file=path.join(pkg,'dist',module);let source=fs.readFileSync(file,'utf8');
 const prior=sha(source)===recipe.patchedSha256?recipe.replacements:recipe.previousReplacements?.[sha(source)];
 if(prior)for(const [before,after] of [...prior].reverse()){
  assert.equal(source.split(after).length,2);source=source.replace(after,()=>before);
 }
 assert.equal(sha(source),recipe.sourceSha256,'reject unknown native bytes');
 for(const [before,after] of recipe.replacements){assert.equal(source.split(before).length,2);source=source.replace(before,()=>after);}
 assert.equal(sha(source),recipe.patchedSha256);sources.set(pathToFileURL(file).href,source);
}
const loader=registerHooks({load(url,context,next){return sources.has(url)?{format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
const {a:pressure}=await import(pathToFileURL(path.join(pkg,'dist/attempt.tool-run-context-yigSIkBW.js')));
const {t:convert}=await import(pathToFileURL(path.join(pkg,'dist/openai-transport-stream-P3cLoEh2.js')));
const {j:prepare,S:settings}=await import(pathToFileURL(path.join(pkg,'dist/proxy-Bsfwfsp-.js')));
test.after(async()=>{
 const {n:close}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));close();loader.deregister();
 await fs.promises.rm(root,{recursive:true,force:true});
});

for(const fixed of [false,true])test(`64K schemas ${fixed?'refuse irreducible fixed overhead':'compact prior turns and retain full owner output'}`,{timeout:60000},async t=>{
 const directory=path.join(root,String(fixed));fs.mkdirSync(directory);
 const workspace=path.join(directory,'workspace'),agentDir=path.join(directory,'agent'),plugin=path.join(directory,'plugin');
 for(const folder of [workspace,agentDir,plugin])fs.mkdirSync(folder);
 const id=randomUUID(),sessionFile=path.join(directory,'session.jsonl'),now=Date.now();
 const definitions=['fixture_schema_read_a','fixture_schema_read_b'].map(name=>({name,
  description:'Synthetic read-only schema field explanation. '.repeat(400),parameters:{type:'object',properties:{}}}));
 const systemPrompt=fixed?'Synthetic irreducible context. '.repeat(6500):'Synthetic native context.';
 const prompt='Answer only 22. Do not call any tools.';
 const rows=[{type:'session',version:3,id,timestamp:new Date(now).toISOString(),cwd:workspace}];
 for(let i=0;i<12;i++)for(const role of ['user','assistant']){
  const previous=rows.at(-1),timestamp=now-60000+i*2000+(role==='assistant'?1000:0);
  rows.push({type:'message',id:`prior-${i}-${role}`,parentId:previous.type==='session'?null:previous.id,
   message:role==='user'?{role,content:'Synthetic retained evidence. '.repeat(380),timestamp}:
    {role,content:[{type:'text',text:'Prior synthetic answer.'}],stopReason:'stop',timestamp,provider:'fixture',model:'model',api:'openai-completions'}});
 }
 const initial=rows.map(JSON.stringify).join('\n')+'\n';fs.writeFileSync(sessionFile,initial);
 assert.ok(prepare(rows,settings).value?.messagesToSummarize.length>0,'native history is genuinely compactable');
 const events=[],requests=[];let compacting=false;
 const key=Symbol.for('ods.test.schema-pressure');globalThis[key]={observe(kind,event){
  if(kind==='input')events.push(event);else compacting=kind==='before';
 }};
 t.after(()=>{delete globalThis[key];});
 fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'schema-pressure-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
 fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'schema-pressure-fixture',activation:{onStartup:true},contracts:{tools:definitions.map(t=>t.name)},configSchema:{type:'object',properties:{}}}));
 fs.writeFileSync(path.join(plugin,'data.json'),JSON.stringify({definitions,systemPrompt}));
 fs.writeFileSync(path.join(plugin,'index.mjs'),`import fs from 'node:fs';const data=JSON.parse(fs.readFileSync(new URL('./data.json',import.meta.url)));
const state=globalThis[Symbol.for('ods.test.schema-pressure')];export default{id:'schema-pressure-fixture',register(api){
 api.on('before_prompt_build',()=>({systemPrompt:data.systemPrompt}));api.on('llm_input',event=>state.observe('input',event));
 api.on('before_compaction',event=>state.observe('before',event));api.on('after_compaction',event=>state.observe('after',event));
 for(const tool of data.definitions)api.registerTool({...tool,execute(){throw Error('No business tool may execute');}});}};`);
 const upstream=http.createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks));
  requests.push({compacting,body});
  if(compacting)assert.equal(body.tools?.length??0,0,'native compaction requests no tools');
  else{assert.equal(fixed,false,'irreducible overhead must never reach the owner provider');
   assert.equal(body.max_tokens??body.max_completion_tokens,8192,'no silent owner output reduction');
   assert.deepEqual(body.tools.map(t=>t.function.name).sort(),definitions.map(t=>t.name));}
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  const text=compacting?'Synthetic checkpoint: prior evidence retained.':'22';
  res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:text},finish_reason:null}]})+'\n\n');
  res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:1000,completion_tokens:2,total_tokens:1002}})+'\n\ndata: [DONE]\n\n');
 });
 upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
 t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
 const model={id:'model',name:'Fixture64K',contextWindow:65536,maxTokens:8192,reasoning:false,input:['text']};
 const config={agents:{defaults:{workspace,skipBootstrap:true,model:'fixture/model',contextTokens:65536,
  compaction:{reserveTokens:19661,reserveTokensFloor:0,keepRecentTokens:20000},heartbeat:{every:'0m'}},
  list:[{id:'pixel',default:true,workspace,agentDir,skills:[]}]},
  tools:{allow:definitions.map(t=>t.name),toolSearch:{enabled:false}},
  plugins:{allow:['schema-pressure-fixture'],load:{paths:[plugin]},entries:{'schema-pressure-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}},
  models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',models:[model]}}}};
 fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
 const result=await runEmbeddedAgent({agentId:'pixel',sessionId:id,sessionFile,sessionKey:'agent:pixel:schema-'+id,workspaceDir:workspace,agentDir,
  config,provider:'fixture',model:'model',prompt,runId:id,trigger:'user',timeoutMs:30000});
 const offered=convert({...model,api:'openai-completions',provider:'fixture',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`},{messages:[],tools:definitions}).tools;
 const before=pressure({messages:events[0].historyMessages,systemPrompt:events[0].systemPrompt,prompt:events[0].prompt});
 const withSchemas=before+pressure({messages:[],prompt:JSON.stringify(offered)});
 assert.ok(withSchemas>45875,'schema-aware initial precheck must trigger before provider submission');
 if(fixed){assert.equal(requests.filter(r=>!r.compacting).length,0);assert.equal(result.meta.error.kind,'context_overflow');}
 else{assert.ok(before<45875,'old text-only precheck would admit this exact request');
  assert.equal(result.meta?.error,undefined,JSON.stringify(result.meta?.error));
  assert.equal(requests.filter(r=>r.compacting).length,1);assert.equal(requests.filter(r=>!r.compacting).length,1);
  assert.ok(fs.readFileSync(sessionFile,'utf8').startsWith(initial),'existing durable history remains intact');
 }
});
