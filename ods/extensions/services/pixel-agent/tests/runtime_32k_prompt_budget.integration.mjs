// Real pinned runner + deterministic loopback provider; no installed state/model.
// The synthetic fixed overhead is sized to the observed post-compaction budget
// failure. This is a runtime regression, not a Qwen quality or tokenizer test.
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
import {promptContractForAgent, ODS_CONVERSATION_CONTRACT} from '../plugin/prompt-contract.mjs';

const pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg,'provide the pinned reviewed OpenClaw package');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-32k-budget-'));
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
const {a:pressure}=await import(pathToFileURL(path.join(pkg,'dist/attempt.tool-run-context-yigSIkBW.js')));
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});

test('32K owner follow-up continues once after compaction without replay or reduced output budget', {timeout:60000}, async t=>{
  const requests=[];
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks)));
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'22'},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:14000,completion_tokens:1,total_tokens:14001}})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const workspace=path.join(root,'workspace'),agentDir=path.join(root,'agent');
  fs.mkdirSync(workspace);fs.mkdirSync(agentDir);
  const sessionId='1aaaaaaa-2222-4333-8444-555555555555',sessionFile=path.join(root,sessionId+'.jsonl');
  const now=Date.now();
  const rows=[
    {type:'session',version:3,id:sessionId,timestamp:new Date(now).toISOString(),cwd:workspace},
    {type:'message',id:'owner-old',parentId:null,message:{role:'user',content:'Inspect the fixture documentation once.',timestamp:now-4000}},
    {type:'message',id:'read-call',parentId:'owner-old',message:{role:'assistant',content:[{type:'text',text:'The existing documentation was inspected.'},{type:'toolCall',id:'read-once',name:'read',arguments:{path:'README.md'}}],stopReason:'toolUse',model:'model',provider:'fixture',api:'openai-completions',timestamp:now-3000}},
    {type:'message',id:'read-result',parentId:'read-call',message:{role:'toolResult',toolName:'read',toolCallId:'read-once',content:[{type:'text',text:'Retained verified fixture documentation. '.repeat(32)}],timestamp:now-2000}},
    {type:'compaction',id:'checkpoint',parentId:'read-result',summary:'The documentation was already read. Preserve its evidence; no action needs to be repeated.',firstKeptEntryId:'read-call',tokensBefore:23221,timestamp:new Date(now-1000).toISOString()},
  ];
  const before=rows.map(JSON.stringify).join('\n')+'\n';fs.writeFileSync(sessionFile,before);
  const history=native.F(rows).messages;
  assert.equal(native.j(rows,{...native.S,keepRecentTokens:2048}).value,undefined,'completed checkpoint has no new compactable work');
  const prompt='Do not continue the research or start subagents. Answer this new question only: what is 14 plus 8?';
  // Match the observed pressure region with content-free synthetic overhead.
  // Never reduce the production reserve or set an artificially small model.
  let fixed='';
  while(pressure({messages:history,systemPrompt:fixed+'\n\n'+ODS_CONVERSATION_CONTRACT,prompt})<19916)fixed+='Retained private workspace context. ';
  const contract=promptContractForAgent({agentId:'pixel',contextTokenBudget:32768},'pixel',{prompt,messages:history},{configuredContextWindow:32768});
  const plugin=path.join(root,'plugin');fs.mkdirSync(plugin);
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'ods-budget-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'ods-budget-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`export default {id:'ods-budget-fixture',register(api){api.on('before_prompt_build',()=>(${JSON.stringify({systemPrompt:fixed,...contract})}));}};`);
  const config={agents:{defaults:{workspace,skipBootstrap:true,model:'fixture/model',contextTokens:32768,
    compaction:{reserveTokens:13108,reserveTokensFloor:0,keepRecentTokens:2048},heartbeat:{every:'0m'}},
    list:[{id:'pixel',default:true,workspace,agentDir}]},tools:{deny:['*']},
    plugins:{allow:['ods-budget-fixture'],load:{paths:[plugin]},entries:{'ods-budget-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture',contextWindow:32768,maxTokens:8192,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:budget-fixture',workspaceDir:workspace,agentDir,
    config,provider:'fixture',model:'model',prompt,runId:'budget-followup',trigger:'user',timeoutMs:30000});
  const trace=JSON.stringify({requests:requests.length,result});
  assert.equal(result.meta?.error,undefined,trace);
  assert.equal(requests.length,1,'one actual provider continuation, no compaction/replay\n'+trace);
  assert.equal(requests[0].tools?.length??0,0,'this continuation fixture exposes no execution capabilities');
  assert.equal(requests[0].max_tokens??requests[0].max_completion_tokens,8192,'preserve full configured output budget');
  assert.equal(result.payloads?.map(p=>p.text??'').join(''),'22',trace);
  const sent=JSON.stringify(requests[0].messages);
  assert.ok(requests[0].messages.some(m=>m.role==='system'&&typeof m.content==='string'&&m.content.includes(contract.appendSystemContext)),
    'the actual native provider payload must include the selected production contract');
  assert.equal(sent.split(prompt).length-1,1,'submit new owner request exactly once');
  assert.ok(sent.includes('Retained verified fixture documentation.'),'keep completed tool evidence');
  assert.ok(!sent.includes('Inspect the fixture documentation once.'),'do not replay the archived owner request');
  const after=fs.readFileSync(sessionFile,'utf8');
  assert.ok(after.startsWith(before),'preserve existing checkpoint and durable audit');
  assert.equal(after.split('\n').filter(Boolean).map(JSON.parse).filter(r=>r.type==='message'&&r.message?.role==='toolResult').length,1,'no repeated tool execution');
});

test('post-turn compaction cannot replace a real final reply with an orphaned Tool Search projection', {timeout:60000}, async t=>{
  const directory=path.join(root,'terminal-projection');fs.mkdirSync(directory);
  const workspace=path.join(directory,'workspace'),agentDir=path.join(directory,'agent');
  fs.mkdirSync(workspace);fs.mkdirSync(agentDir);
  const effects=path.join(directory,'reads.jsonl'),requests=[];
  let mainCalls=0,summaryCalls=0;
  const answer='The completed read returned the Cedar fixture fact.';
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const request=JSON.parse(Buffer.concat(chunks));requests.push(request);
    let delta,finish,usage;
    if(!request.tools?.length) {
      summaryCalls++;
      delta={role:'assistant',content:'The owner asked to read the Cedar fact. The read completed. Preserve the final answer and do not replay completed actions.'};finish='stop';
      usage={prompt_tokens:300,completion_tokens:35,total_tokens:335};
    } else {
      mainCalls++;
      assert.ok(request.tools.some(tool=>tool.function.name==='tool_call'));
      if(mainCalls<=2) {
        delta={role:'assistant',content:'Checking the connected note.',tool_calls:[{index:0,id:'projection-'+mainCalls,type:'function',function:{
          name:'tool_call',arguments:JSON.stringify({id:'openclaw:ods-projection-fixture:fixture_read',...(mainCalls===2?{args:{key:'cedar'}}:{})})}}]};
        finish='tool_calls';
      } else {delta={role:'assistant',content:answer};finish='stop';}
      // Controlled telemetry triggers native threshold compaction after the
      // terminal answer. This is a lifecycle regression, not a tokenizer test.
      usage={prompt_tokens:mainCalls>=3?21000:1000,completion_tokens:50,total_tokens:mainCalls>=3?21050:1050};
    }
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'projection-fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'projection-fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:finish}],usage})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const plugin=path.join(directory,'plugin');fs.mkdirSync(plugin);
  fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'ods-projection-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'ods-projection-fixture',activation:{onStartup:true},contracts:{tools:['fixture_read']},configSchema:{type:'object',properties:{}}}));
  fs.writeFileSync(path.join(plugin,'index.mjs'),`import {appendFileSync} from 'node:fs';
export default {id:'ods-projection-fixture',register(api){api.registerTool({name:'fixture_read',description:'Read the controlled Cedar note.',parameters:{type:'object',properties:{key:{type:'string'}}},
execute:async(_id,args)=>{appendFileSync(${JSON.stringify(effects)},JSON.stringify(args)+'\\n');return args.key==='cedar'?{content:[{type:'text',text:'Cedar fixture fact'}]}:{isError:true,content:[{type:'text',text:'Missing key. Describe the tool before retrying.'}]};}});}};`);
  const config={agents:{defaults:{workspace,skipBootstrap:true,model:'fixture/model',contextTokens:32768,
    compaction:{reserveTokens:13108,reserveTokensFloor:0,keepRecentTokens:64},heartbeat:{every:'0m'}},
    list:[{id:'pixel',default:true,workspace,agentDir}]},
    tools:{allow:['fixture_read','tool_call','tool_search','tool_describe'],toolSearch:{enabled:true,mode:'tools'}},
    plugins:{allow:['ods-projection-fixture'],load:{paths:[plugin]},entries:{'ods-projection-fixture':{enabled:true}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture',contextWindow:32768,maxTokens:2048,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const sessionId='2aaaaaaa-2222-4333-8444-555555555555',sessionFile=path.join(directory,sessionId+'.jsonl');
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:terminal-projection',workspaceDir:workspace,agentDir,
    config,provider:'fixture',model:'model',prompt:'Read the Cedar fixture note and report its fact.',runId:'terminal-projection',trigger:'user',timeoutMs:30000});
  const rows=fs.readFileSync(sessionFile,'utf8').trim().split('\n').map(JSON.parse);
  const checkpoint=rows.findLast(row=>row.type==='compaction');
  assert.ok(checkpoint,'exercise actual post-turn compaction');
  const kept=rows.slice(rows.findIndex(row=>row.id===checkpoint.firstKeptEntryId),rows.indexOf(checkpoint));
  assert.ok(!kept.some(row=>row.message?.content?.some(part=>part.type==='toolCall' && part.id==='projection-1')),
    'the earlier target projection must outlive its compacted parent');
  assert.ok(kept.some(row=>row.message?.stopReason==='stop' && row.message.content.some(part=>part.text===answer)),
    'the actual terminal answer remains in native history');
  assert.ok(summaryCalls>0,'the native compactor must reach the controlled summary provider');
  assert.equal(result.meta?.error,undefined,JSON.stringify(result));
  assert.ok(result.payloads.some(payload=>payload.text===answer),'deliver the actual final model answer');
  assert.equal(mainCalls,3,'do not replay the request or add a compensating model call');
  assert.deepEqual(fs.readFileSync(effects,'utf8').trim().split('\n').map(JSON.parse),[{}, {key:'cedar'}]);
});
