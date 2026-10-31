// Real pinned harness + real ingress, deterministic model, disposable state.
// A response's second attempt gets a fresh runtime timer, so it could run past
// Portal's ingress, which cuts the whole response off with no reply at all
// (laptop, 2026-10-30: a goal ended on such a round). The guard must stop
// that attempt at the response's deadline itself, and Portal must receive
// the deadline stop, with what stopped a goal round, before the attempt's
// own timer would have fired.
//
// The first attempt answers NO_REPLY after a delay, which gets one revision
// pass (a new attempt); the provider never answers the revision. The next
// message in the same chat, like a goal's next round, must then be answered:
// the stop leaves no session lock behind.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const pkg=process.env.OPENCLAW_PACKAGE;
// Short enough for a test; the first attempt's reply comes FIRST_REPLY_MS in.
const TIMEOUT_SECONDS=8;
const FIRST_REPLY_MS=3000;
const REQUIREMENT="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. " +
  'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const QUESTION='Fix the wolf hitboxes in my game.'+REQUIREMENT;
const NEXT='Reply with exactly: Next round started.'+REQUIREMENT;
const NEXT_ANSWER='Next round started.';
const STOP_COPY=`**Portal stopped this response after ${TIMEOUT_SECONDS} seconds, the most one response can run.** ` +
  'This request needed more time. Send "continue" to pick up where it left off.' +
  ' If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';

test('real harness: a revised attempt still running at the response deadline is stopped there and delivered as a goal stop',
  {skip:!pkg || process.platform==='win32',timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-retried-deadline-'));
  let rounds=0,log='',child,ingress;
  const held=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const round=rounds++;
    // The revision pass: this model never answers it.
    if(round===1){held.push(res);return;}
    if(round===0)await delay(FIRST_REPLY_MS);
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',
      content:round===0?'NO_REPLY':NEXT_ANSWER},finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'retried-deadline-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'retried-deadline-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard,createRunAbortAdapter} from './ods/tool-loop-guard.mjs';
    import {abortAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify({...x,at:Date.now()})+'\\n');
    const adapter=createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun});
    // As a /goal round (goal-progress.mjs active), whose outcome says which limit stopped it.
    const guard=createToolLoopGuard({goalRun:()=>true,abortRun:(...args)=>{const ok=adapter(...args);record({abort:ok});return ok;}});
    export default {id:'retried-deadline-fixture',register(api){
      // This isolated fixture has no spawning tools; explicitly declare the
      // nondelegated gateway contract used by the paired ingress.
      api.registerHttpRoute({path:'/pixel-ods/subagent-delivery',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:JSON.parse(body).runId,status:'not-delegated'}));return true;
      }});
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(JSON.parse(body).runId)));return true;
      }});
      api.on('before_prompt_build',(e,c)=>{record({attempt:c.runId});
        // The run deadline the runtime just armed for this attempt (index.js).
        const runTimeoutSeconds=(api.runtime?.config?.current?.() ?? api.config)?.agents?.defaults?.timeoutSeconds;
        return guard.observeRun(c,'pixel',e,{runTimeoutSeconds});});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);record({text:e.lastAssistantMessage,decision:d?.action??null});return d;});
      api.on('agent_end',(e,c)=>{record({end:e.success});return guard.observeAgentEnd(e,c);});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'},
      timeoutSeconds:TIMEOUT_SECONDS},list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    plugins:{allow:['retried-deadline-fixture'],load:{paths:[plugin]},entries:{'retried-deadline-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
    const send=content=>fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'retried-deadline-fixture',messages:[{role:'user',content}]}),
      signal:AbortSignal.timeout(45000)});
    const response=await send(QUESTION);
    const body=await response.text();
    const delivered=Date.now();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const text=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const runtimeLog=existsSync(join(root,'runtime.log'))?readFileSync(join(root,'runtime.log'),'utf8'):'';
    const trace=JSON.stringify({rounds,events,text})+'\n'+log+'\n'+runtimeLog.slice(-6000);
    const attempts=events.filter(e=>e.attempt);
    assert.equal(attempts.length,2,'the revision pass is a second attempt\n'+trace);
    assert.deepEqual(events.filter(e=>'decision' in e).map(e=>[e.text,e.decision]),[['NO_REPLY','revise']],trace);
    // The guard stopped the second attempt at the response's deadline, before
    // that attempt's own timer: the runtime's timeout never fired.
    assert.deepEqual(events.filter(e=>'abort' in e).map(e=>e.abort),[true],trace);
    const stop=events.find(e=>'abort' in e).at;
    assert.ok(stop-attempts[0].at>=TIMEOUT_SECONDS*1000-100,'not before the response deadline\n'+trace);
    assert.ok(stop<attempts[1].at+TIMEOUT_SECONDS*1000,'before the attempt\'s own timer\n'+trace);
    assert.ok(delivered<attempts[1].at+TIMEOUT_SECONDS*1000,'delivered before the attempt\'s own timer\n'+trace);
    assert.doesNotMatch(log+runtimeLog,/embedded run timeout/,trace);
    assert.equal(text,STOP_COPY,trace);
    assert.deepEqual(frames.at(-1).pixel_outcome,{schemaVersion:1,status:'failed',stop:'run-deadline'},trace);
    // The goal's next round, in the same chat.
    const next=await send(NEXT);
    const nextBody=await next.text();
    assert.equal(next.status,200,nextBody+'\n'+trace);
    const nextFrames=nextBody.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    assert.equal(nextFrames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join(''),NEXT_ANSWER,nextBody+'\n'+trace);
    assert.notEqual(nextFrames.at(-1).pixel_outcome.status,'failed',nextBody+'\n'+trace);
    assert.equal(rounds,3,trace);
  } finally {
    for(const res of held)res.destroy();
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
