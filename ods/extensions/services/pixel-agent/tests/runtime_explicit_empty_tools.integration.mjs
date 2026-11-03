// Real pinned native runner, disposable state and a deterministic loopback
// provider. No installed config, gateway, cron job, model or external tool runs.
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
import {reviewedNativeRevisionDependencies} from './fixtures/native-revision-dependencies.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the pinned reviewed native package');
assert.equal(JSON.parse(fs.readFileSync(path.join(pkg,'package.json'))).version,'2026.6.33');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-native-empty-cap-'));
const identity=fs.lstatSync(root,{bigint:true});
process.env.HOME=path.join(root,'home');fs.mkdirSync(process.env.HOME);
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const sha=value=>createHash('sha256').update(value).digest('hex');
const selection=path.join(pkg,'dist/selection-BEwSQKM-.js');
const installedBefore=sha(fs.readFileSync(selection));
const sources=new Map([...reviewedNativeRevisionDependencies(pkg)].map(([filename,source])=>[pathToFileURL(filename).href,source]));
const manifest=JSON.parse(fs.readFileSync(new URL('../host/openclaw-compaction-budget.json',import.meta.url)));
let source=fs.readFileSync(selection,'utf8');
const prior=installedBefore===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[installedBefore];
if(prior)for(const [before,after] of [...prior].reverse()) {
  assert.equal(source.split(after).length,2);source=source.replace(after,()=>before);
}
assert.equal(sha(source),manifest.sourceSha256,'reject unreviewed native bytes');
for(const [before,after] of manifest.replacements) {
  assert.equal(source.split(before).length,2);source=source.replace(before,()=>after);
}
assert.equal(sha(source),manifest.patchedSha256);
sources.set(pathToFileURL(selection).href,source);
const loader=registerHooks({load(url,context,next){return sources.has(url)
  ?{format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();
  assert.equal(sha(fs.readFileSync(selection)),installedBefore,'installed native module stays unchanged');
  const current=fs.lstatSync(root,{bigint:true});
  assert.equal(current.isDirectory()&&!current.isSymbolicLink(),true);
  assert.equal(current.dev,identity.dev);assert.equal(current.ino,identity.ino);
  assert.equal(path.dirname(root),os.tmpdir());
  await fs.promises.rm(root,{recursive:true});
});

const cases=[
  {name:'explicit NONE under global and agent allowlists',toolsAllow:[],allowed:['read','cron'],denied:[],provider:true,toolNames:[]},
  {name:'nonempty matched runtime cap keeps its native discovery surface',toolsAllow:['read'],allowed:['read','cron'],denied:[],provider:true,toolNames:['read','tool_call','tool_describe','tool_search']},
  {name:'nonempty unmatched runtime cap stays refused',toolsAllow:['fixture_missing_tool'],allowed:['read','cron'],denied:[],provider:false},
  {name:'nonempty denied runtime cap stays refused',toolsAllow:['read'],allowed:['read','cron'],denied:['read'],provider:false},
  {name:'runtime cap cannot expand the owner allowlist',toolsAllow:['write'],allowed:['read','cron'],denied:[],provider:false},
  {name:'disabled tools cannot waive a nonempty runtime cap',toolsAllow:['read'],disableTools:true,allowed:['read','cron'],denied:[],provider:false},
];
for(const [index,c] of cases.entries())test(`actual native runtime: ${c.name}`,{timeout:60000},async t=>{
  const requests=[];
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks)));
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'22'},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:1000,completion_tokens:1,total_tokens:1001}})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const directory=path.join(root,String(index));fs.mkdirSync(directory);
  const workspace=path.join(directory,'workspace'),agentDir=path.join(directory,'agent');
  fs.mkdirSync(workspace);fs.mkdirSync(agentDir);
  const config={agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/model'},contextTokens:32768,
    heartbeat:{every:'0m'},compaction:{reserveTokens:8192,reserveTokensFloor:0,keepRecentTokens:2048}},
    list:[{id:'pixel',default:true,workspace,agentDir,tools:{allow:c.allowed}}]},
    tools:{allow:c.allowed,deny:c.denied,toolSearch:{enabled:true,mode:'tools'}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture',contextWindow:32768,maxTokens:2048,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  let result,error;
  try {result=await runEmbeddedAgent({agentId:'pixel',sessionId:`empty-cap-${index}`,sessionFile:path.join(directory,'session.jsonl'),
    sessionKey:`agent:pixel:cron:fixture-empty-cap-${index}`,workspaceDir:workspace,agentDir,config,
    provider:'fixture',model:'model',prompt:'Calculate 14 + 8. Reply only with the number.',
    toolsAllow:c.toolsAllow,...(c.disableTools?{disableTools:true}:{}),runId:`empty-cap-${index}`,trigger:'cron',timeoutMs:30000});}
  catch(caught){error=caught;}
  if(!c.provider) {
    assert.equal(requests.length,0,'refused explicit caps never reach the provider');
    assert.match(error?.message??result?.meta?.error?.message??JSON.stringify(result),/No callable tools remain after resolving explicit tool allowlist/);
    return;
  }
  assert.equal(error,undefined,error?.stack);assert.equal(result.meta?.error,undefined,JSON.stringify(result));
  assert.equal(requests.length,1,'one provider call, no replay');
  assert.equal(result.payloads?.map(p=>p.text??'').join(''),'22');
  assert.deepEqual((requests[0].tools??[]).map(tool=>tool.function.name),c.toolNames);
  assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,2048);
});
