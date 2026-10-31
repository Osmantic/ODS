// The real native caller must forward its resolved model window to ToolSearch.
// Fixed loopback replies inspect wire shape; no model or Root helper executes.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createConnectionsStatusTool} from '../plugin/connections-status.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the pinned reviewed native package');
const sha=value=>createHash('sha256').update(value).digest('hex');
const sources=new Map();
for(const [manifestName,module] of [
  ['image-envelope','tool-search-BInRpkE3.js'],
  ['compaction-budget','selection-BEwSQKM-.js'],
]) {
  const manifest=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${manifestName}.json`,import.meta.url)));
  const filename=path.join(pkg,'dist',module);
  let source=fs.readFileSync(filename,'utf8');
  const digest=sha(source);
  const applied=digest===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[digest];
  if(applied)for(const [before,after] of [...applied].reverse()) {
    assert.equal(source.split(after).length,2);source=source.replace(after,before);
  }
  assert.equal(sha(source),manifest.sourceSha256,'reject unreviewed native bytes');
  for(const [before,after] of manifest.replacements) {
    assert.equal(source.split(before).length,2);source=source.replace(before,after);
  }
  assert.equal(sha(source),manifest.patchedSha256);
  sources.set(pathToFileURL(filename).href,source);
}
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-native-context-wire-'));
process.env.HOME=path.join(root,'home');fs.mkdirSync(process.env.HOME);
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const loader=registerHooks({load(url,context,next){return sources.has(url)
  ? {format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});

for(const [window,explicit,direct] of [[65536,undefined,true],[8192,undefined,false],[65536,8192,false]]) {
  test(`native provider-only window${window} explicit${explicit??'absent'} status direct${direct}`,{timeout:60000},async t=>{
    const requests=[];
    const upstream=http.createServer(async(req,res)=>{
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      requests.push(JSON.parse(Buffer.concat(chunks)));
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'READY'},finish_reason:null}]})+'\n\n');
      res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:1000,completion_tokens:1,total_tokens:1001}})+'\n\ndata: [DONE]\n\n');
    });
    upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
    t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
    const directory=path.join(root,`${window}-${explicit??'none'}`);fs.mkdirSync(directory);
    const workspace=path.join(directory,'workspace'),agentDir=path.join(directory,'agent'),plugin=path.join(directory,'plugin');
    for(const p of [workspace,agentDir,plugin])fs.mkdirSync(p);
    const {name,description,parameters}=createConnectionsStatusTool({read:()=>{throw Error('No Root reads in wire fixture');}});
    fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'pixel-ods',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
    fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},contracts:{tools:[name]},configSchema:{type:'object',properties:{}}}));
    fs.writeFileSync(path.join(plugin,'tool.json'),JSON.stringify({name,description,parameters}));
    fs.writeFileSync(path.join(plugin,'index.mjs'),`import fs from 'node:fs';
const tool=JSON.parse(fs.readFileSync(new URL('./tool.json',import.meta.url),'utf8'));
export default {id:'pixel-ods',register(api){api.registerTool({...tool,execute:async()=>{throw Error('No tool execution in native wire fixture');}});}};`);
    const effective=Math.min(window,explicit??window);
    const config={agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/model'},
      compaction:{reserveTokens:Math.ceil(effective*0.4),reserveTokensFloor:0,keepRecentTokens:2048},heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true,workspace,agentDir,...(explicit===undefined?{}:{contextTokens:explicit})}]},
      tools:{allow:[name],toolSearch:{enabled:true,mode:'tools'}},
      plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true}}},
      models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
        models:[{id:'model',name:'Fixture',contextWindow:window,maxTokens:2048,reasoning:false,input:['text']}]}}}};
    assert.equal(Object.hasOwn(config.agents.defaults,'contextTokens'),false);
    if(explicit===undefined)assert.equal(Object.hasOwn(config.agents.list[0],'contextTokens'),false);
    fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
    const result=await runEmbeddedAgent({agentId:'pixel',sessionId:`context-${window}-${explicit??'none'}`,
      sessionFile:path.join(directory,'session.jsonl'),sessionKey:`agent:pixel:context-${window}-${explicit??'none'}`,
      workspaceDir:workspace,agentDir,config,provider:'fixture',model:'model',prompt:'Reply READY without using tools.',
      runId:`context-${window}-${explicit??'none'}`,trigger:'user',timeoutMs:30000});
    assert.equal(result.meta?.error,undefined,JSON.stringify(result));
    assert.equal(requests.length,1);
    const names=requests[0].tools.map(tool=>tool.function.name);
    assert.equal(names.includes(name),direct,JSON.stringify(names));
    assert.ok(names.includes('tool_search')&&names.includes('tool_describe')&&names.includes('tool_call'));
    if(direct)assert.deepEqual(requests[0].tools.find(tool=>tool.function.name===name).function.parameters,parameters);
    assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,2048);
  });
}
