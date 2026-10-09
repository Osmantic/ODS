// Actual pinned runner, native precheck/compaction and production citation
// assurance. All provider/tool responses are deterministic offline fixtures.
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
assert.ok(pkg,'provide the pinned reviewed OpenClaw package');
assert.equal(JSON.parse(fs.readFileSync(path.join(pkg,'package.json'))).version,'2026.6.33');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-finalize-compaction-'));
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');
process.env.OPENCLAW_CONFIG_PATH=path.join(root,'openclaw.json');
const sha=value=>createHash('sha256').update(value).digest('hex');
const sources=new Map();
for(const [name,module] of [
  ['compaction-empty','proxy-Bsfwfsp-.js'],['compaction-no-work','compact-DuWIsaq_.js'],
  ['compaction-resume','sessions-CZbwb3_c.js'],['compaction-budget','selection-BEwSQKM-.js'],
  ['yield-usage','embedded-agent-CJx-nG3W.js'],
]) {
  const recipe=JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
  const filename=path.join(pkg,'dist',module);
  let source=fs.readFileSync(filename,'utf8');
  const applied=sha(source)===recipe.patchedSha256?recipe.replacements:recipe.previousReplacements?.[sha(source)];
  if(applied)for(const [before,after] of [...applied].reverse()){
    assert.equal(source.split(after).length,2);source=source.replace(after,before);
  }
  assert.equal(sha(source),recipe.sourceSha256,'reject unknown native bytes');
  const baseline=name==='yield-usage'?(process.env.ODS_FINALIZE_RETRY_BASELINE==='1'?'d00804f960d617b6b3e2ae6b1691f3aa9866029c8da8772f236a35a6033b37f8'
    :process.env.ODS_FINALIZE_RETRY_BASELINE==='initial-only'?'71eefa582f753745f85f6f853900bb661a96ff8a31db107ed8b890c07c2b4fdc':null):null;
  const replacements=baseline?recipe.previousReplacements[baseline]:recipe.replacements;
  for(const [before,after] of replacements){assert.equal(source.split(before).length,2);source=source.replace(before,after);}
  assert.equal(sha(source),baseline??recipe.patchedSha256);
  sources.set(pathToFileURL(filename).href,source);
}
const loader=registerHooks({load(url,context,next){return sources.has(url)
  ?{format:'module',source:sources.get(url),shortCircuit:true}:next(url,context);}});
const {t:runEmbeddedAgent}=await import(pathToFileURL(path.join(pkg,'dist/embedded-agent-CJx-nG3W.js')));
test.after(async()=>{
  const {n:closeStateDatabases}=await import(pathToFileURL(path.join(pkg,'dist/openclaw-state-db-BtpXMqJX.js')));
  closeStateDatabases();loader.deregister();await fs.promises.rm(root,{recursive:true,force:true});
});
const DOC_URL='https://docs.python.org/3/library/pathlib.html';
const OWNER="Search the web for Python's official pathlib documentation. Give the official page link and a short Python example that tests whether a path is a file. Use a real web search and cite the result; do not create or edit files.";
const ANSWER=`Path.is_file() tests whether a path is a file. [Official pathlib documentation](${DOC_URL}).\n\nfrom pathlib import Path\nprint(Path('example.txt').is_file())`;
const workspace=path.join(root,'workspace'),agentDir=path.join(root,'agent'),plugin=path.join(root,'plugin'),eventsFile=path.join(root,'events.jsonl');
for(const dir of [workspace,agentDir,plugin])fs.mkdirSync(dir);
for(const name of ['completion-assurance.mjs','page-excerpt.mjs'])
  fs.copyFileSync(new URL('../plugin/'+name,import.meta.url),path.join(plugin,name));
fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'finalize-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./fixture.mjs']}}));
fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'finalize-fixture',contracts:{tools:['web_search','web_fetch']},toolMetadata:{web_search:{replaySafe:true},web_fetch:{replaySafe:true}},activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
fs.writeFileSync(path.join(plugin,'fixture.mjs'),`
  import {appendFileSync} from 'node:fs';
  import {createCompletionAssurance} from './completion-assurance.mjs';
  const runs=new Map();
  const record=(type,ctx,data)=>appendFileSync(${JSON.stringify(eventsFile)},JSON.stringify({type,run:ctx.runId,...data})+'\\n');
  export default {id:'finalize-fixture',register(api){
    api.on('before_prompt_build',(event,ctx)=>{
      let state=runs.get(ctx.runId);
      if(!state){state={assurance:createCompletionAssurance(),revised:false,pressure:false};runs.set(ctx.runId,state);state.assurance.begin(event.prompt,event);}
      record('prompt',ctx,{prompt:event.prompt});
      if(state.revised&&!state.pressure){state.pressure=true;return {appendSystemContext:'PUBLIC_OFFLINE_PRESSURE_TOKEN '.repeat(1000)};}
      if(state.revised&&ctx.runId.endsWith('mid-turn')&&!state.read) return {appendSystemContext:'PUBLIC_OFFLINE_PRESSURE_TOKEN '.repeat(1000)};
    });
    api.on('after_tool_call',(event,ctx)=>{
      const name={web_search:'web_search',web_fetch:'web_fetch'}[event.toolName];
      if(name)runs.get(ctx.runId).assurance.observe(name,event);
      if(name==='web_fetch')runs.get(ctx.runId).read=true;
      record('tool',ctx,{name:event.toolName});
    });
    api.on('before_agent_finalize',(event,ctx)=>{
      const state=runs.get(ctx.runId),decision=state.assurance.finalize(event.lastAssistantMessage);
      if(decision?.action==='revise')state.revised=true;
      record('finalize',ctx,{decision,terminal:state.assurance.terminal});return decision;
    });
    api.on('agent_end',(event,ctx)=>record('end',ctx,{success:event.success,error:event.error}));
    api.registerTool({name:'web_search',description:'Search the offline documentation index.',parameters:{type:'object',properties:{query:{type:'string'}},required:['query']},
      async execute(){const results=[{url:${JSON.stringify(DOC_URL)},title:'Official pathlib documentation'}];
        return {content:[{type:'text',text:JSON.stringify({results})}],details:{results}};}});
    api.registerTool({name:'web_fetch',description:'Read the offline documentation page.',parameters:{type:'object',properties:{url:{type:'string'},fixtureLong:{type:'boolean'}},required:['url']},
      async execute(id,args){return {content:[{type:'text',text:args.fixtureLong?'Documented regular-file behavior. '.repeat(5000):'Path.is_file() returns whether the path is a regular file.'}],
        details:{status:200,url:args.url,text:'Path.is_file() returns whether the path is a regular file.'}};}});
  }};
`);

for(const mode of ['revised','message-id','ignored','compaction-error','abort','provider-overflow','tool-overflow','mid-turn'])
test(`citation revision survives native precheck and compaction: ${mode}`,{timeout:60000},async t=>{
  const runId='finalize-'+mode,sessionId=randomUUID(),sessionFile=path.join(root,sessionId+'.jsonl');
  const now=Date.now(),rows=[{type:'session',version:3,id:sessionId,timestamp:new Date(now).toISOString(),cwd:workspace}];
  // Real compactable prior history, with no prior citation-revision instruction.
  for(let i=0;i<12;i++)rows.push({type:'message',id:'old-'+i,parentId:i?'old-'+(i-1):null,
    message:i%2?{role:'assistant',content:[{type:'text',text:'Retained completed project detail. '.repeat(100)}],stopReason:'stop',
      model:'model',provider:'fixture',api:'openai-completions',timestamp:now-1000+i}:
      {role:'user',content:'Keep the existing project evidence. '.repeat(100),timestamp:now-1000+i}});
  fs.writeFileSync(sessionFile,rows.map(JSON.stringify).join('\n')+'\n');
  const controller=new AbortController(),requests=[];
  let normalCalls=0,compactionCalls=0,readIssued=false;
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks));requests.push(body);
    const compact=!body.tools?.length;
    if(compact){
      compactionCalls++;
      if(mode==='abort'){controller.abort(new Error('fixture owner Stop'));return;}
      if(mode==='compaction-error'){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'fixture compaction refused',type:'invalid_request_error'}}));return;}
    }else{
      normalCalls++;
      if((mode==='provider-overflow'&&normalCalls===3)||(mode==='tool-overflow'&&normalCalls===4)){res.writeHead(400,{'Content-Type':'application/json'});
        res.end(JSON.stringify({error:{message:'Context overflow: prompt too large for the model.',type:'invalid_request_error',code:'context_length_exceeded'}}));return;}
    }
    const last=body.messages.at(-1);
    const hasRevision=JSON.stringify(body.messages).includes('Cited pages lack current-turn read receipts.');
    let delta={content:compact?'Retain the owner request and completed search evidence. No page read has happened.':ANSWER},finish='stop';
    const call=(name,args)=>{delta={tool_calls:[{index:0,id:'fixture-'+normalCalls,type:'function',function:{name,arguments:JSON.stringify(args)}}]};finish='tool_calls';};
    if(!compact&&normalCalls===1)call('web_search',{query:'Python pathlib official documentation'});
    else if(!compact&&hasRevision&&mode!=='ignored'&&last?.role!=='tool'&&!readIssued){readIssued=true;call('web_fetch',{url:DOC_URL,...mode==='mid-turn'?{fixtureLong:true}:{}});}
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',...delta},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:finish}],
      usage:{prompt_tokens:1000,completion_tokens:30,total_tokens:1030}})+'\n\ndata: [DONE]\n\n');
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  t.after(async()=>{upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));});
  const config={agents:{defaults:{workspace,skipBootstrap:true,model:'fixture/model',contextTokens:32768,
    compaction:{reserveTokens:13108,reserveTokensFloor:0,keepRecentTokens:2048,midTurnPrecheck:{enabled:mode==='mid-turn'}},heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace,agentDir}]},
    // Disable network-backed built-ins; only the two offline read-only tools
    // above can run. Native replay and revision safety remain in force.
    tools:{web:{search:{enabled:false},fetch:{enabled:false}},allow:['web_search','web_fetch']},plugins:{allow:['finalize-fixture'],load:{paths:[plugin]},entries:{'finalize-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}},
    models:{mode:'replace',providers:{fixture:{api:'openai-completions',baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,apiKey:'fixture-only',
      models:[{id:'model',name:'Fixture',contextWindow:32768,maxTokens:8192,reasoning:false,input:['text']}]}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH,JSON.stringify(config));
  const result=await runEmbeddedAgent({agentId:'pixel',sessionId,sessionFile,sessionKey:'agent:pixel:'+runId,workspaceDir:workspace,agentDir,
    config,provider:'fixture',model:'model',prompt:OWNER,runId,trigger:'user',timeoutMs:30000,abortSignal:controller.signal,
    ...(mode==='message-id'?{currentMessageId:'owner-message'}:{})});
  const events=fs.readFileSync(eventsFile,'utf8').trim().split('\n').map(JSON.parse).filter(e=>e.run===runId);
  const trajectory=fs.readFileSync(sessionFile.replace('.jsonl','.trajectory.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  const compiled=trajectory.filter(e=>e.type==='context.compiled');
  const prechecks=trajectory.filter(e=>e.type==='model.completed'&&JSON.stringify(e).includes('(precheck)'));
  const submitted=trajectory.filter(e=>e.type==='prompt.submitted');
  const stored=fs.readFileSync(sessionFile,'utf8').trim().split('\n').map(JSON.parse);
  const summary={mode,result,normalCalls,compactionCalls,events,compiled:compiled.map(e=>({type:e.type,data:{prompt:e.data.prompt}})),prechecks:prechecks.length,
    submitted:submitted.map(e=>e.data.prompt),checkpoints:stored.filter(e=>e.type==='compaction').length};
  if(process.env.ODS_FINALIZE_RETRY_RECEIPTS){fs.mkdirSync(process.env.ODS_FINALIZE_RETRY_RECEIPTS,{recursive:true});
    fs.writeFileSync(path.join(process.env.ODS_FINALIZE_RETRY_RECEIPTS,mode+'.json'),JSON.stringify(summary,null,2));}
  const trace=JSON.stringify(summary);
  const secondOverflow=['provider-overflow','tool-overflow','mid-turn'].includes(mode);
  assert.ok(compactionCalls>=1&&compactionCalls<=(secondOverflow?4:2),'bounded native chunk/summary calls\n'+trace);
  assert.equal(prechecks.length,mode==='mid-turn'?2:1,'actual compiled attempt must fail before provider\n'+trace);
  const revision=events.find(e=>e.type==='finalize'&&e.decision?.action==='revise')?.decision;
  assert.ok(revision?.retry?.instruction.includes(DOC_URL),'production citation guard names the unread URL');
  assert.equal(events.filter(e=>e.type==='tool'&&e.name==='web_search').length,1,'no repeated search');
  if(mode==='abort'||mode==='compaction-error'){
    assert.equal(normalCalls,2,'no post-failure provider continuation\n'+trace);
    assert.equal(stored.filter(e=>e.type==='compaction').length,0,'failed/aborted compaction cannot invent checkpoint');
    assert.ok(result.meta?.error,trace);
    if(mode==='abort')assert.equal(controller.signal.aborted,true);
    return;
  }
  assert.equal(stored.filter(e=>e.type==='compaction').length,secondOverflow?2:1,'actual compacted checkpoint');
  assert.equal(compiled.length,secondOverflow?4:3,trace);
  const before=compiled[1].data.prompt,after=compiled[2].data.prompt;
  assert.equal(after,before,'the exact compiled revision must survive recovery without replaying the original prompt');
  assert.equal(after.split(revision.retry.instruction).length-1,1,'actionable revision occurs exactly once');
  if(mode==='provider-overflow')assert.equal(compiled[3].data.prompt,before,'provider rejection before work preserves exact revision once again');
  if(mode==='provider-overflow'||mode==='tool-overflow'||mode==='mid-turn'){
    const indices=events.map((event,index)=>event.type==='prompt'?index:-1).filter(index=>index>=0);
    const workBeforeRejection=events.slice(indices[2]+1,indices[3]).filter(event=>event.type==='tool').map(event=>event.name);
    assert.deepEqual(workBeforeRejection,mode==='provider-overflow'?[]:['web_fetch'],'actual failed provider attempt tool evidence');
    if(mode!=='mid-turn')assert.ok(trajectory.some(event=>event.type==='model.completed'&&JSON.stringify(event).includes('400 Context overflow')),'native classified an actual provider400');
  }
  if(mode==='tool-overflow'||mode==='mid-turn'){
    assert.match(compiled[3].data.prompt,/Continue from the current transcript after the latest tool result\./);
    assert.equal(compiled[3].data.prompt.includes(revision.retry.instruction),false,'completed read must not replay the revision');
    assert.equal(compiled[3].data.prompt.includes(OWNER),false,'completed work must not replay the owner request');
  }
  // Native trajectory normalizes both precheck error strings. Distinguish the
  // real paths by order: initial failure precedes submission; the second
  // follows submission and the completed page tool, without a provider400.
  if(mode==='mid-turn'){
    assert.ok(trajectory.indexOf(prechecks[0])<trajectory.indexOf(submitted[1]));
    assert.ok(trajectory.indexOf(prechecks[1])>trajectory.indexOf(submitted[1]));
    assert.equal(normalCalls,4,'mid-turn precheck prevents a provider request until recovery');
  }
  assert.equal(submitted.length,secondOverflow?3:2,'precheck never submits its blocked attempt');
  assert.ok(JSON.stringify(compiled[2].data.messages).includes(OWNER),'original owner remains bound through compaction');
  const ownerMessages=stored.filter(e=>e.type==='message'&&e.message?.role==='user').map(e=>typeof e.message.content==='string'
    ?e.message.content:e.message.content.filter(c=>c.type==='text').map(c=>c.text).join(''));
  assert.equal(ownerMessages.filter(text=>text.includes(OWNER)).length,1,'persist the original owner exactly once');
  assert.equal(ownerMessages.some(text=>text.includes(revision.retry.instruction)),false,'never persist revision as a new owner request');
  const final=events.filter(e=>e.type==='finalize').at(-1);
  if(mode==='ignored'){
    assert.equal(events.filter(e=>e.type==='tool'&&e.name==='web_fetch').length,0);
    assert.match(final.terminal,/source reads were not confirmed/,'search hits never become read receipts');
  }else{
    assert.equal(events.filter(e=>e.type==='tool'&&e.name==='web_fetch').length,1,'exactly one normal page read after revision');
    assert.equal(final.terminal,undefined,'successful current-run page receipt clears citation failure');
    assert.equal(result.payloads?.map(p=>p.text??'').join(''),ANSWER,trace);
  }
});
