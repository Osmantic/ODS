// Actual pinned admission/runner and complete registered plugin prompt hook,
// with a loopback provider. No synthetic padding or replacement system prompt.
// This checks prompt assembly and unchanged reserves, not model quality.
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
import {realPromptBuild} from './prompt-build-harness.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the pinned reviewed OpenClaw package');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-8k-budget-'));
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const sha=value=>createHash('sha256').update(value).digest('hex');
const sources=new Map();
for(const [name,module] of [
  ['compaction-empty','proxy-Bsfwfsp-.js'],
  ['compaction-no-work','compact-DuWIsaq_.js'],
  ['compaction-resume','sessions-CZbwb3_c.js'],
  ['compaction-budget','selection-BEwSQKM-.js'],
]) {
  const manifest=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
  const filename=path.join(pkg,'dist',module);
  let source=fs.readFileSync(filename,'utf8');
  const digest=sha(source);
  const applied=digest===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[digest];
  if(applied)for(const [before,after] of [...applied].reverse()){
    assert.equal(source.split(after).length,2);source=source.replace(after,before);
  }
  assert.equal(sha(source),manifest.sourceSha256,'reject unreviewed native bytes');
  for(const [before,after] of manifest.replacements){assert.equal(source.split(before).length,2);source=source.replace(before,after);}
  assert.equal(sha(source),manifest.patchedSha256);
  sources.set(pathToFileURL(filename).href,source);
}
const loader=registerHooks({load(url,context,next){return sources.has(url)
  ? {format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const native=await import(pathToFileURL(path.join(pkg,'dist/proxy-Bsfwfsp-.js')));
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});


for (const scenario of [
  {id:'chat', prompt:'Without tools, answer in one short sentence: what color is a ripe banana?'},
  {id:'listing', prompt:'Use a read-only workspace tool to list the files and folders at the top of the current workspace. Report only what the tool returns. Do not create or change files.'},
  {id:'listing-correction', prompt:'The command used head -20, so that was incomplete. Use a read-only tool to run ls -la /workspace without head, tail, or any truncation. List every returned entry except . and .. with its exact name and file/folder type. Do not change files.'},
]) test('8K '+scenario.id+' full plugin hook reaches the provider with unchanged reserves', {timeout:60000}, async t=>{
  const requests=[];
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks)));
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'A ripe banana is yellow.'},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:4000,completion_tokens:7,total_tokens:4007}})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const workspace=path.join(root,'workspace-'+scenario.id),agentDir=path.join(root,'agent-'+scenario.id);
  fs.mkdirSync(workspace);fs.mkdirSync(agentDir);
  const prompt=scenario.prompt;
  let contract;
  await t.test('assemble all real before_prompt_build instructions', async child => {
    const build = await realPromptBuild(child, {
      pluginConfig: {modelContextWindow: 65536, leanPrompt: false, projectBuildSocket: '/not-contacted/project.sock'},
      currentConfig: () => ({plugins: {entries: {'pixel-ods': {config: {modelContextWindow: 8192, leanPrompt: true}}}}}),
    });
    contract = await build(prompt);
  });
  const reserveTokens=3277;
  const plugin=path.join(root,'plugin-'+scenario.id);fs.mkdirSync(plugin);
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'ods-8k-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'ods-8k-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`export default {id:'ods-8k-fixture',register(api){api.on('before_prompt_build',()=>(${JSON.stringify(contract)}));}};`);
  const config={agents:{defaults:{workspace,skipBootstrap:true,model:'fixture/model',contextTokens:8192,
    compaction:{reserveTokens,reserveTokensFloor:0,keepRecentTokens:512},heartbeat:{every:'0m'}},
    list:[{id:'pixel',default:true,workspace,agentDir,skills:[],contextTokens:8192}]},tools:{deny:['*']},
    plugins:{allow:['ods-8k-fixture'],load:{paths:[plugin]},entries:{'ods-8k-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture',contextWindow:8192,maxTokens:2048,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const sessionId=({chat:'2aaaaaaa',listing:'3aaaaaaa','listing-correction':'4aaaaaaa'})[scenario.id]+'-2222-4333-8444-555555555555',sessionFile=path.join(root,sessionId+'.jsonl');
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:8k-fixture-'+scenario.id,workspaceDir:workspace,agentDir,
    config,provider:'fixture',model:'model',prompt,runId:'8k-'+scenario.id,trigger:'user',timeoutMs:30000});
  const trace=JSON.stringify({requests:requests.length,result});
  assert.equal(result.meta?.error,undefined,trace);
  assert.equal(requests.length,1,'one real provider request, no repeated compaction\n'+trace);
  assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,2048);
  assert.equal(result.payloads?.map(p=>p.text??'').join(''),'A ripe banana is yellow.',trace);
  const normalize = text => text.replace(/\s+/g, ' ').trim();
  assert.ok(requests[0].messages.some(m=>m.role==='system'&&typeof m.content==='string'&&normalize(m.content).includes(normalize(contract.appendSystemContext))),
    'the provider receives the complete registered production hook output');
  assert.equal(JSON.stringify(requests[0].messages).split(prompt).length-1,1);
});
