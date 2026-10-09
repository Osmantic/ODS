// Pinned native runner, actual registered ODS hooks/tools, deterministic local
// provider. This tests admission and payload custody, not small-model quality.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {once} from 'node:events';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {registerHooks} from 'node:module';
import {registeredPixelTools} from './tool-grammar-registration.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the pinned reviewed OpenClaw package');
assert.equal(JSON.parse(fs.readFileSync(path.join(pkg,'package.json'))).version,'2026.6.33');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-8k-budget-'));
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const manifest=JSON.parse(fs.readFileSync(new URL('../host/openclaw-image-envelope.json',import.meta.url)));
const moduleFile=path.join(pkg,'dist/tool-search-BInRpkE3.js');
const sha=value=>createHash('sha256').update(value).digest('hex');
let source=fs.readFileSync(moduleFile,'utf8');
const applied=sha(source)===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[sha(source)];
if(applied)for(const [before,after] of [...applied].reverse()){
  assert.equal(source.split(after).length,2);source=source.replace(after,before);
}
assert.equal(sha(source),manifest.sourceSha256,'reject unreviewed native source');
for(const [before,after] of manifest.replacements){assert.equal(source.split(before).length,2);source=source.replace(before,after);}
assert.equal(sha(source),manifest.patchedSha256);
const loader=registerHooks({load(url,context,next){return url===pathToFileURL(moduleFile).href
  ? {format:'module',source,shortCircuit:true}:next(url,context);}});
const {a:pressure}=await import(pathToFileURL(path.join(pkg,'dist/attempt.tool-run-context-yigSIkBW.js')));
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});

test('8K fresh owner chat reaches provider once with full 2048 output after fixed prompt reduction', {timeout:60000}, async t=>{
  const prompt='Reply in one short sentence: what is 12 plus 7?';
  const capture=async contextWindow=>{
    const hooks=[];
    const tools=await registeredPixelTools({contextWindow,onHook:(name,hook)=>{if(name==='before_prompt_build')hooks.push(hook);}});
    const context={agentId:'pixel',sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),runId:`budget-${contextWindow}`};
    const addenda=[];
    for(const hook of hooks){const result=await hook({prompt,messages:[]},context);if(result?.appendSystemContext)addenda.push(result.appendSystemContext.trim());}
    return {tools,prompt:addenda.join('\n\n')};
  };
  const baseline=await capture(16384),small=await capture(8192);
  // Match the observed native fixed prompt pressure (5784 vs 4915 available)
  // without reading a real owner's session or turning off any source guards.
  let fixed='';
  while(pressure({messages:[],systemPrompt:fixed+'\n\n'+baseline.prompt,prompt})<5784)fixed+='Unchanged native runtime context. ';
  const before=pressure({messages:[],systemPrompt:fixed+'\n\n'+baseline.prompt,prompt});
  const after=pressure({messages:[],systemPrompt:fixed+'\n\n'+small.prompt,prompt});
  assert.ok(before>8192-3277);
  assert.ok(after<=8192-3277-250,`retain owner input headroom: ${after}`);
  const requests=[];
  const server=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks)));
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'12 plus 7 is 19.'},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:4000,completion_tokens:8,total_tokens:4008}})+'\n\ndata: [DONE]\n\n');
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const workspace=path.join(root,'workspace'),agentDir=path.join(root,'agent'),plugin=path.join(root,'plugin');
  for(const dir of [workspace,agentDir,plugin])fs.mkdirSync(dir);
  const tools=small.tools.map(({name,label,description,parameters})=>({name,label,description,parameters}));
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'ods-8k-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'ods-8k-fixture',activation:{onStartup:true},contracts:{tools:tools.map(t=>t.name)},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`export default {id:'ods-8k-fixture',register(api){for(const tool of ${JSON.stringify(tools)})api.registerTool({...tool,execute(){throw new Error('no tool execution in arithmetic fixture');}});api.on('before_prompt_build',()=>(${JSON.stringify({systemPrompt:fixed,appendSystemContext:small.prompt})}));}};`);
  const config={skills:{allowBundled:[]},agents:{defaults:{workspace,skipBootstrap:true,model:'fixture/model',contextTokens:8192,
    compaction:{reserveTokens:3277,reserveTokensFloor:0,keepRecentTokens:512},heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace,agentDir,skills:[],contextInjection:'never'}]},
    tools:{profile:'coding',alsoAllow:['web_search','web_fetch',...tools.map(t=>t.name)],toolSearch:{enabled:true,mode:'tools',searchDefaultLimit:5,maxSearchLimit:10}},
    plugins:{allow:['ods-8k-fixture'],load:{paths:[plugin]},entries:{'ods-8k-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${server.address().port}/v1`,apiKey:'fixture-only',models:[{id:'model',name:'Fixture',contextWindow:8192,maxTokens:2048,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId:'1aaaaaaa-2222-4333-8444-555555555555',sessionFile:path.join(root,'session.jsonl'),
    sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64),workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',prompt,runId:'8k-arithmetic',trigger:'user',timeoutMs:30000});
  const trace=JSON.stringify({before,after,result});
  if(process.env.ODS_BUDGET_RECEIPT)fs.writeFileSync(process.env.ODS_BUDGET_RECEIPT,JSON.stringify({before,after,context:8192,reserve:3277,output:2048,prompt:small.prompt,request:requests[0]},null,2),{mode:0o600});
  assert.equal(result.meta?.error,undefined,trace);
  assert.equal(requests.length,1,trace);
  assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,2048,'preserve the full response budget');
  assert.equal(result.payloads?.map(p=>p.text??'').join(''),'12 plus 7 is 19.',trace);
  const sent=JSON.stringify(requests[0].messages);
  assert.equal(sent.split(prompt).length-1,1,'owner prompt appears once');
  assert.ok(requests[0].messages.some(m=>m.role==='system'&&m.content.includes(small.prompt)),'actual registered prompt reaches provider');
  assert.deepEqual(requests[0].tools.map(tool=>tool.function.name).sort(),['tool_call','tool_describe','tool_search'],
    'the 8K prompt keeps discovery; policy-filtered tools remain in its catalog');
});
