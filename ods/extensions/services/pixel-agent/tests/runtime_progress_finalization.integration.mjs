// Real pinned harness + real ingress, deterministic model, disposable state.
// A tool fails until the run-progress budget stops the response. The next
// model call still sees the original failure (tool_result_persist rewrites the
// transcript only); it either answers directly ('direct') or calls a tool,
// which is refused with the finalization instruction. The following call is
// the single tool-free turn: it answers ('answer') or tries a tool ('tool').
// 'compaction' reports near-window usage on the answer, so OpenClaw runs a real
// threshold auto-compaction whose summarization call uses the run's model
// stream (tower3, 2026-09-25): the answer must survive it. 'partial' streams a
// substantive answer and then a tool call in that turn (tower3 r8): the call is
// refused, the run ends at that boundary, and the text is delivered as a
// partial answer, observed through before_message_write.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,rmSync,existsSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION,PROGRESS_FINALIZATION_NOTE,PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE} from '../plugin/progress-finalization.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
// ODS installs OpenClaw with its completion-recovery repair
// (host/openclaw-completion-recovery.json); upstream discards a response that
// is followed by a post-turn compaction. The 'compaction' variant therefore
// needs the repaired runtime (CI applies it with openclaw_tool_recovery.py).
function completionRecoveryRepaired(dir) {
  try {
    const [[,repaired]]=JSON.parse(readFileSync(new URL('../host/openclaw-completion-recovery.json',import.meta.url),'utf8')).replacements;
    return readdirSync(join(dir,'dist')).some(name=>name.startsWith('agent-command-') &&
      readFileSync(join(dir,'dist',name),'utf8').includes(repaired));
  } catch { return false; }
}
const repairedRuntime=Boolean(pkg) && completionRecoveryRepaired(pkg);
const ANSWER='```json\n{"name":"RTX 5070","vramGB":12,"boardPowerW":250,"retail":null}\n```\n\n' +
  'The NVIDIA page returned 12 GB and 250 W. Retail price and benchmark results are unverified because later fetches failed.';
const PARTIAL='From the pages returned before the limit:\n\n- RTX 5070: 12 GB of VRAM and 250 W board power, according to the NVIDIA page.\n' +
  '- US retail price: not verified, because every retailer fetch failed.\n' +
  '- Benchmark comparison: not verified, because no benchmark page was read.\n\nLet me try one more source for the price:';

for (const finalTurn of ['answer','tool','direct','compaction','partial']) test(`real harness graceful finalization: final turn ${finalTurn}`,
  {skip:!pkg ? true : finalTurn==='compaction' && !repairedRuntime
    ? 'needs the ODS completion-recovery repair (host/openclaw_tool_recovery.py --completion-recovery)' : false,
  timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-progress-finalization-'));
  let rounds=0,summaries=0,log='',child,ingress;
  const seen=[];
  const answerRound=finalTurn==='direct'?4:5;
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const request=JSON.parse(Buffer.concat(chunks).toString());
    if (!request.tools?.length) {
      // A compaction summarization request: no tools, not an agent turn.
      summaries++;
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.write('data: '+JSON.stringify({id:'summary',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',
        content:'Summary: four source fetches failed; the answer used the NVIDIA page.'},finish_reason:null}]})+'\n\n');
      res.end('data: '+JSON.stringify({id:'summary',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
      return;
    }
    const tools=request.messages.filter(message=>message.role==='tool');
    seen.push(tools.at(-1)?.content ?? null);
    const round=rounds++;
    const call={tool_calls:[{index:0,id:`fetch-${round}`,type:'function',
      function:{name:'fixture_fetch',arguments:JSON.stringify({url:`https://example.org/source-${round}`})}}]};
    const deltas=round!==answerRound || finalTurn==='tool' ? [{role:'assistant',...call}]
      : finalTurn==='partial' ? [{role:'assistant',content:PARTIAL},call] : [{role:'assistant',content:ANSWER}];
    const delta=deltas.at(-1);
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    for (const part of deltas) res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:part,finish_reason:null}]})+'\n\n');
    const usage=finalTurn==='compaction' && !delta.tool_calls ? {usage:{prompt_tokens:31000,completion_tokens:60,total_tokens:31060}} : {};
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
      choices:[{index:0,delta:{},finish_reason:delta.tool_calls?'tool_calls':'stop'}],...usage})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'finalization-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'finalization-fixture',contracts:{tools:['fixture_fetch']},
    activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard,createRunAbortAdapter} from './ods/tool-loop-guard.mjs';
    import {abortAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const adapter=createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun});
    const guard=createToolLoopGuard({abortRun:(id,key,observe)=>{const ok=adapter(id,key,observe);record({abort:ok});return ok;},
      execControl:{signal:()=>true}});
    export default {id:'finalization-fixture',register(api){
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        const runId=JSON.parse(body).runId;await guard.settleDelivery(runId);
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(runId)));return true;
      }});
      api.on('before_prompt_build',(e,c)=>guard.observeRun(c,'pixel',e));
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_compaction',(e,c)=>{record({compaction:'start',sessionKey:Boolean(c?.sessionKey)});guard.observeCompaction(c,'start');});
      api.on('after_compaction',(e,c)=>{record({compaction:'end'});guard.observeCompaction(c,'end');});
      api.on('before_tool_call',(e,c)=>{const d=guard.beforeToolCall(e,c);record({before:e.toolName,block:d?.blockReason?.slice(0,40)??null});return d;});
      api.on('after_tool_call',(e,c)=>guard.afterToolCall(e,c));
      api.on('tool_result_persist',(e,c)=>guard.toolResultPersist(e,c));
      api.on('before_message_write',(e,c)=>{if(e.message?.role==='assistant')record({write:(e.message.content??[]).filter(b=>b?.type==='toolCall').length,
        text:(e.message.content??[]).some(b=>b?.type==='text'&&b.text),sessionKey:Boolean(c?.sessionKey)});return guard.observeAssistantMessage(e,c);});
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);
        record({finalize:e.lastAssistantMessage?.slice(0,20)??null,decision:d?.action??null});return d;});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
      api.registerTool({name:'fixture_fetch',description:'Fetch one fixture page.',
        parameters:{type:'object',properties:{url:{type:'string'}},required:['url']},
        async execute(id,args){record({execute:args.url});throw new Error('Web fetch failed (403)');}});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'},
      ...(finalTurn==='compaction'?{compaction:{mode:'default',keepRecentTokens:1,reserveTokensFloor:0}}:{})},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['fixture_fetch']},plugins:{allow:['finalization-fixture'],load:{paths:[plugin]},
      entries:{'finalization-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},
      stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let n=0;n<250;n++){
      try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
      if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);
    }
    assert.ok(ready,log);
    ingress=createIngressServer({token:'fixture-only',gatewayPort:port});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
      headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'finalization-fixture',messages:[{role:'user',
        content:'Compare the RTX 5070 VRAM and board power with a US retail price. Actually search the live web and open sources. Return one fenced JSON object then a concise explanation.'}]}),
      signal:AbortSignal.timeout(45000)});
    const body=await response.text();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({rounds,seen,events,delivered})+'\n'+log;
    assert.equal(events.filter(x=>x.execute).length,4,'the budget is unchanged: four failures stop the run\n'+trace);
    assert.equal(rounds,answerRound+1,'the stop grants no model call beyond the single answer turn\n'+trace);
    assert.doesNotMatch(JSON.stringify(seen[4]),/ODS Pixel tool limit/,'the persisted rewrite never reaches the live model\n'+trace);
    if(finalTurn!=='direct'){
      assert.ok(events.some(x=>x.before==='fixture_fetch'&&x.block===PROGRESS_FINALIZATION_INSTRUCTION.slice(0,40)),trace);
      assert.ok(JSON.stringify(seen[5]).includes(JSON.stringify(PROGRESS_FINALIZATION_INSTRUCTION).slice(1,-1)),
        'the answer turn sees the fixed instruction as the refused call result\n'+trace);
    }
    assert.equal(frames.at(-1).pixel_outcome.status,'failed',trace);
    if(finalTurn==='compaction'){
      assert.ok(summaries>=1,'OpenClaw ran a real compaction summarization call after the answer\n'+trace);
      assert.ok(events.some(x=>x.compaction==='start'&&x.sessionKey),trace);
    }
    if(finalTurn==='partial'){
      assert.ok(delivered.startsWith(PARTIAL),trace);
      assert.ok(delivered.includes(`${PROGRESS_FINALIZATION_NOTE}\n\n${PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE}`),trace);
      assert.ok(!delivered.includes(RUN_PROGRESS_STOP_REASON),trace);
      assert.ok(events.some(x=>x.write===1&&x.text&&x.sessionKey),'the answer turn message reached before_message_write\n'+trace);
      assert.ok(events.some(x=>x.before==='fixture_fetch'&&x.block===RUN_PROGRESS_STOP_REASON.slice(0,40)),'its call is refused\n'+trace);
      assert.equal(events.filter(x=>x.execute).length,4,'the refused call never ran\n'+trace);
      assert.deepEqual(events.filter(x=>'abort' in x).map(x=>x.abort),[true],'aborted once at the tool boundary\n'+trace);
    } else if(finalTurn!=='tool'){
      assert.ok(delivered.startsWith(ANSWER),trace);
      assert.ok(delivered.includes(PROGRESS_FINALIZATION_NOTE),trace);
      assert.ok(!delivered.includes(RUN_PROGRESS_STOP_REASON),trace);
      assert.ok(!events.some(x=>'abort' in x),'no abort after a tool-free answer\n'+trace);
      assert.deepEqual(events.filter(x=>'finalize' in x).map(x=>x.decision),[null],trace);
    } else {
      assert.equal(delivered,RUN_PROGRESS_STOP_REASON,trace);
      assert.ok(events.some(x=>x.before==='fixture_fetch'&&x.block===RUN_PROGRESS_STOP_REASON.slice(0,40)),trace);
      assert.deepEqual(events.filter(x=>'abort' in x).map(x=>x.abort),[true],'aborted once at the tool boundary\n'+trace);
    }
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
