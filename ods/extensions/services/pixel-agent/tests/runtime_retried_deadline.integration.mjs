// Real pinned harness + real ingress, deterministic model, disposable state.
// A response's later attempt gets a fresh runtime timer, so it could run past
// Portal's ingress, which cuts the whole response off with no reply at all
// (laptop, 2026-10-30: a goal ended on such a round). The guard stops the
// response just before its deadline itself, only where the stop reaches
// OpenClaw's run and costs no answer, and Portal receives the deadline stop
// with what stopped a goal round.
//
// Each first attempt answers NO_REPLY after a delay, which gets one revision
// pass (a later attempt). After a stop, the next message in the same chat,
// like a goal's next round, must be answered: no session lock or prompt is
// left behind.
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
// Short enough for a test: the guard stops a response 133 ms (1/60) early,
// and a model call 3 s after it began at the earliest.
const TIMEOUT_SECONDS=8;
const STOP_MS=TIMEOUT_SECONDS*1000-133;
const FIRST_REPLY_MS=3000;
const REQUIREMENT="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. " +
  'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const NEXT='Reply with exactly: Next round started.'+REQUIREMENT;
const NEXT_ANSWER='Next round started.';
const STOP_COPY=`**Portal stopped this response after ${TIMEOUT_SECONDS} seconds, the most one response can run.** ` +
  'This request needed more time. Send "continue" to pick up where it left off.' +
  ' If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).';

// replies: one per provider request, {delayMs, content} or 'hold' (never
// answered). finalize: by attempt number, {untilMs: wait until this long after
// the response began, revise: then ask for one more pass}, like Pixel's
// finalization hooks (preview checks, cited pages) that run after the answer.
async function withGateway({replies,finalize={}},run){
  const root=mkdtempSync(join(tmpdir(),'ods-retried-deadline-'));
  let rounds=0,log='',child,ingress;
  const held=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const reply=replies[Math.min(rounds++,replies.length-1)];
    if(reply==='hold'){held.push(once(res,'close'));return;}
    if(reply.delayMs)await delay(reply.delayMs);
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:reply.content},finish_reason:null}]})+'\n\n');
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
    import {setTimeout as sleep} from 'node:timers/promises';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify({...x,at:Date.now()})+'\\n');
    const finalize=${JSON.stringify(finalize)};
    const adapter=createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun});
    // As a /goal round (goal-progress.mjs active), whose outcome says which limit stopped it.
    const guard=createToolLoopGuard({goalRun:()=>true,abortRun:(...args)=>{const ok=adapter(...args);record({abort:ok});return ok;}});
    const attempts=new Map(),starts=new Map();
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
      api.on('before_prompt_build',(e,c)=>{
        attempts.set(c.runId,(attempts.get(c.runId)??0)+1);if(!starts.has(c.runId))starts.set(c.runId,Date.now());
        record({attempt:attempts.get(c.runId),run:c.runId});
        // The run deadline the runtime just armed for this attempt (index.js).
        const runTimeoutSeconds=(api.runtime?.config?.current?.() ?? api.config)?.agents?.defaults?.timeoutSeconds;
        return guard.observeRun(c,'pixel',e,{runTimeoutSeconds});});
      api.on('model_call_started',(e,c)=>{record({model:attempts.get(c.runId)});guard.observeModelCall(e,c);});
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_message_write',(e,c)=>guard.observeReplyEnd(e,c));
      api.on('before_agent_finalize',async(e,c)=>{
        const plan=finalize[attempts.get(c.runId)];
        if(plan?.untilMs)await sleep(Math.max(0,starts.get(c.runId)+plan.untilMs-Date.now()));
        const guardDecision=guard.beforeAgentFinalize(e,c);
        const d=plan?.revise?{action:'revise',reason:'Fixture check.',retry:{instruction:'Check the result once more.',idempotencyKey:'fixture-check',maxAttempts:1}}:guardDecision;
        record({text:e.lastAssistantMessage,decision:d?.action??null});return d;});
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
    const send=async content=>{
      const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'retried-deadline-fixture',messages:[{role:'user',content}]}),
        signal:AbortSignal.timeout(45000)});
      const body=await response.text();
      const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
      return {status:response.status,body,at:Date.now(),outcome:frames.at(-1)?.pixel_outcome,
        text:frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('')};
    };
    const events=()=>existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const runtimeLog=()=>log+(existsSync(join(root,'runtime.log'))?readFileSync(join(root,'runtime.log'),'utf8'):'');
    const trace=()=>JSON.stringify({rounds,events:events()})+'\n'+runtimeLog().slice(-6000);
    await run({send,events,held,rounds:()=>rounds,runtimeLog,trace});
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
}

// The stop ended the attempt at once (an abort while a call still sets up
// its stream left it hung until its own timer), reached the held provider
// request, and the chat's next message is answered.
async function assertStoppedCleanly(h,next){
  const events=h.events();
  const abort=events.find(e=>'abort' in e),end=events.find(e=>e.end===false);
  assert.ok(abort&&end&&end.at-abort.at<2000,'the attempt ended right after the stop\n'+h.trace());
  assert.equal(h.held.length,1,h.trace());
  await Promise.race([h.held[0],delay(5000).then(()=>assert.fail('the stopped provider request stayed open\n'+h.trace()))]);
  const answer=await h.send(NEXT);
  assert.equal(answer.status,200,answer.body+'\n'+h.trace());
  assert.equal(answer.text,NEXT_ANSWER,answer.body+'\n'+h.trace());
  assert.notEqual(answer.outcome.status,'failed',answer.body+'\n'+h.trace());
  assert.equal(h.rounds(),next,h.trace());
}

const skip=!pkg || process.platform==='win32';

test('real harness: a revised attempt still running at the deadline is stopped there and delivered as a goal stop',
  {skip,timeout:90000}, async () => {
  await withGateway({replies:[{delayMs:FIRST_REPLY_MS,content:'NO_REPLY'},'hold',{content:NEXT_ANSWER}]}, async h => {
    const stop=await h.send('Fix the wolf hitboxes in my game.'+REQUIREMENT);
    assert.equal(stop.status,200,stop.body+'\n'+h.trace());
    const events=h.events();
    const attempts=events.filter(e=>e.attempt);
    assert.equal(attempts.length,2,'the revision pass is a second attempt\n'+h.trace());
    assert.deepEqual(events.filter(e=>'decision' in e).map(e=>[e.text,e.decision]),[['NO_REPLY','revise']],h.trace());
    // Stopped at the response's deadline, before the attempt's own timer.
    assert.deepEqual(events.filter(e=>'abort' in e).map(e=>e.abort),[true],h.trace());
    const at=events.find(e=>'abort' in e).at;
    assert.ok(at-attempts[0].at>=STOP_MS-100,'not before the response deadline\n'+h.trace());
    assert.ok(stop.at<attempts[1].at+TIMEOUT_SECONDS*1000,'delivered before the attempt\'s own timer\n'+h.trace());
    assert.doesNotMatch(h.runtimeLog(),/embedded run timeout/,h.trace());
    assert.equal(stop.text,STOP_COPY,h.trace());
    assert.deepEqual(stop.outcome,{schemaVersion:1,status:'failed',stop:'run-deadline'},h.trace());
    await assertStoppedCleanly(h,3);
  });
});

// OpenClaw drops the answer of an attempt anything but its own timer aborted.
test('real harness: an answer whose finalization runs past the deadline is delivered, never stopped',
  {skip,timeout:90000}, async () => {
  const ANSWER='We decided to give each wolf two hit circles and keep the boss box as it is.';
  await withGateway({replies:[{delayMs:FIRST_REPLY_MS,content:'NO_REPLY'},{content:ANSWER}],
    finalize:{2:{untilMs:TIMEOUT_SECONDS*1000+1000}}}, async h => {
    const reply=await h.send('What did we decide about the wolf hitboxes?'+REQUIREMENT);
    assert.equal(reply.status,200,reply.body+'\n'+h.trace());
    const events=h.events();
    const attempts=events.filter(e=>e.attempt);
    assert.equal(attempts.length,2,h.trace());
    assert.ok(reply.at-attempts[0].at>STOP_MS,'finalized past the deadline\n'+h.trace());
    assert.deepEqual(events.filter(e=>'abort' in e),[],h.trace());
    assert.equal(reply.text,ANSWER,h.trace());
    assert.notEqual(reply.outcome.status,'failed',h.trace());
  });
});

// Before an attempt's first model call an abort cannot reach the prompt
// OpenClaw starts next, so the stop waits for that call to be under way.
test('real harness: a revision that begins past the deadline is stopped once its first model call is under way',
  {skip,timeout:90000}, async () => {
  await withGateway({replies:[{delayMs:FIRST_REPLY_MS,content:'NO_REPLY'},{content:'Fixed the boss hitboxes.'},'hold',{content:NEXT_ANSWER}],
    finalize:{2:{untilMs:TIMEOUT_SECONDS*1000+1000,revise:true}}}, async h => {
    const stop=await h.send('Fix the boss hitboxes in my game.'+REQUIREMENT);
    assert.equal(stop.status,200,stop.body+'\n'+h.trace());
    const events=h.events();
    const attempts=events.filter(e=>e.attempt);
    assert.equal(attempts.length,3,h.trace());
    assert.ok(attempts[2].at-attempts[0].at>STOP_MS,'the third attempt began past the deadline\n'+h.trace());
    assert.deepEqual(events.filter(e=>'abort' in e).map(e=>e.abort),[true],h.trace());
    const call=events.find(e=>e.model===3),abort=events.find(e=>'abort' in e);
    assert.ok(call&&abort.at-call.at>=2900,'once that attempt\'s first model call is under way\n'+h.trace());
    assert.equal(stop.text,STOP_COPY,h.trace());
    assert.deepEqual(stop.outcome,{schemaVersion:1,status:'failed',stop:'run-deadline'},h.trace());
    await assertStoppedCleanly(h,4);
  });
});
