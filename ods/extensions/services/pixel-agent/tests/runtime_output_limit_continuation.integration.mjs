// Real pinned harness + the real pixel-ods plugin + the real ingress on the
// Portal history path; deterministic model, disposable state.
// strixy 2026-09-25: the owner's website request produced one reply that
// wrote the whole page in one tool call and stopped at the output limit
// (finish_reason "length"). OpenClaw 2026.6.33 does not run the cut call and
// skips before_agent_finalize, so the ingress asks Pixel for one continuation
// turn with a fixed message. Each cut case is compared with the same model
// behaviour without the cut: the continuation keeps the owner request's
// contract, Playground project and delivery checks, so the owner receives
// what the uncut turn would have delivered. A continuation cut again is
// reported once, in words that fit a website request or a written answer.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn, execFile} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,readdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer, gatewayFetch} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {OUTPUT_LIMIT_ANSWER_TEXT,OUTPUT_LIMIT_CONTINUATION_PROMPT,OUTPUT_LIMIT_CONTINUED_TEXT,
  OUTPUT_LIMIT_WORKSPACE_TEXT} from '../plugin/output-limit-recovery.mjs';

const pkg=process.env.OPENCLAW_PACKAGE;
const DELIVERY="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. " +
  'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const SITE='as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest type effects.  Best you can do.'+DELIVERY;
const TEXT='Write me the longest, most detailed guide you can about how old-growth forests store carbon.'+DELIVERY;
const OPENING='Let me build something impressive — a full forest-themed interactive experience.';
const READY='Your forest page is ready in Playground/forest as index.html, styles.css and script.js.';
const GUIDE='Old-growth forests store carbon in living wood, dead wood and deep soils; here is the concise guide.';
const STYLES={path:'Playground/forest/styles.css',content:'body{background:#0b3d20;color:#e8f5e9}\n'};
const PAGE={path:'Playground/forest/index.html',content:'<!doctype html><html><head><title>Forest</title><link rel="stylesheet" href="styles.css"></head><body><h1>Forest</h1></body></html>\n'};
const cut={cut:true}, cutText={cut:true,text:true}, write=file=>({write:file}), say=text=>({say:text});

// Each cut case and the uncut control with the same model behaviour.
const CASES={
  'site':{prompt:SITE,steps:[cut,write(PAGE),say(READY)],control:[write(PAGE),say(READY)],continuationRound:1},
  'tools then cut':{prompt:SITE,steps:[write(STYLES),cut,write(PAGE),say(READY)],control:[write(STYLES),write(PAGE),say(READY)],continuationRound:2},
  'unfounded ready claim':{prompt:SITE,steps:[cut,say(READY)],control:[say(READY)],continuationRound:1},
  'text':{prompt:TEXT,steps:[cutText,say(GUIDE)],control:[say(GUIDE)],continuationRound:1,followUp:true},
  // Cut twice: the report leads, in words that fit the request.
  'cut again':{prompt:SITE,steps:[cut,cut],report:OUTPUT_LIMIT_WORKSPACE_TEXT},
  'text cut again':{prompt:TEXT,steps:[cutText,cutText],report:OUTPUT_LIMIT_ANSWER_TEXT},
};

async function run(prompt,steps,{followUp=false}={}) {
  const root=mkdtempSync(join(tmpdir(),'ods-output-limit-'));
  const workspace=join(root,'workspace');
  let log='',child,ingress;
  const requests=[],grants=[],executed=[];
  const chunk=(delta,finish=null,extra={})=>'data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
    choices:[{index:0,delta,finish_reason:finish}],...extra})+'\n\n';
  const text=content=>typeof content==='string'?content:Array.isArray(content)?content.map(part=>part?.text??'').join('\n'):'';
  const upstream=createServer(async(req,res)=>{
    const parts=[];for await(const part of req) parts.push(part);
    const body=JSON.parse(Buffer.concat(parts).toString());
    // Each run has its own disposable root; compare prompts without it.
    requests.push({system:text(body.messages.find(message=>message.role==='system')?.content).replaceAll(root,'<root>'),
      users:body.messages.filter(message=>message.role==='user').map(message=>text(message.content)),
      tools:body.messages.filter(message=>message.role==='tool').length,maxTokens:body.max_tokens??body.max_completion_tokens});
    // After the script, the model repeats its last answer (a revision pass).
    const step=steps[requests.length-1]??steps.at(-1);
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    if(step.say){res.write(chunk({role:'assistant',content:step.say}));res.end(chunk({},'stop')+'data: [DONE]\n\n');return;}
    if(step.write){
      res.write(chunk({role:'assistant',content:''}));
      res.write(chunk({tool_calls:[{index:0,id:`call-${requests.length}`,type:'function',function:{name:'write',arguments:JSON.stringify(step.write)}}]}));
      res.end(chunk({},'tool_calls')+'data: [DONE]\n\n');return;
    }
    // The recorded shape: an opening sentence, then (unless text-only) one
    // whole-page write whose arguments end mid-document at the output limit.
    res.write(chunk({role:'assistant',content:OPENING}));
    if(!step.text)res.write(chunk({tool_calls:[{index:0,id:`call-${requests.length}`,type:'function',function:{name:'write',
      arguments:'{"path":"Playground/forest/index.html","content":"<!doctype html><html><head><style>:root{--moss:#2f5d3a'}}]}));
    res.end(chunk({},'length',{usage:{prompt_tokens:900,completion_tokens:8192,total_tokens:9092}})+'data: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  // Pixel's access runtime keeps its state under ~/.openclaw.
  mkdirSync(join(root,'.openclaw'),{mode:0o700});
  mkdirSync(workspace);mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const model='Qwen3.6-35B-A3B';
  // The strixy managed shape: an 8192-token output limit for the local model.
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace,skipBootstrap:true,sandbox:{mode:'off'},model:{primary:`ods-local/${model}`},contextTokens:131072,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true,workspace,model:`ods-local/${model}`,contextTokens:131072,params:{maxTokens:8192}}]},
    models:{mode:'replace',providers:{'ods-local':{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:model,name:`ODS Local ${model}`,contextWindow:131072,maxTokens:8192,reasoning:false,input:['text']}]}}},
    tools:{exec:{host:'gateway'}},
    plugins:{allow:['pixel-ods'],load:{paths:[fileURLToPath(new URL('../plugin/',import.meta.url))]},
      entries:{'pixel-ods':{enabled:true,config:{modelContextWindow:131072},hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},
      stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let n=0;n<400;n++){
      try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
      if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);
    }
    assert.ok(ready,log);
    // The production ingress transport, observed: every grant it received.
    const observed=async(url,init)=>{
      const response=await gatewayFetch(url,init);
      if(!String(url).endsWith('/pixel-ods/output-limit-continuation'))return response;
      const [kept,copy]=response.body.tee();
      grants.push(new Response(copy).json());
      return {...response,body:kept};
    };
    ingress=createIngressServer({token:'fixture-only',gatewayPort:port,historyLedger:createChatHistoryLedger(join(root,'chat-state')),
      deps:{execFile,fetch:observed,setTimeout,clearTimeout}});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const turns=[];
    const send=async(snapshot,requestId)=>{
      const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
        headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(90000),
        body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'output-limit-fixture',
          messages:[{role:'user',content:snapshot.at(-1).content}],history_snapshot:{schemaVersion:1,messages:snapshot},request_id:requestId})});
      const body=await response.text();
      assert.equal(response.status,200,body+'\n'+log);
      const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
      assert.deepEqual(frames.filter(frame=>frame.error),[],body);
      turns.push({delivered:frames.map(frame=>frame.choices?.[0]?.delta?.content??'').join(''),outcome:frames.at(-1).pixel_outcome?.status});
    };
    await send([{role:'user',content:prompt}],'turn-1');
    const firstTurnRequests=requests.length;
    if(followUp)await send([{role:'user',content:prompt},{role:'assistant',content:turns[0].delivered},
      {role:'user',content:'Thanks. Summarize that in one sentence.'+DELIVERY}],'turn-2');
    const ledger=readdirSync(join(root,'chat-state')).filter(name=>name.endsWith('.json'))
      .map(name=>JSON.parse(readFileSync(join(root,'chat-state',name),'utf8')).status);
    const files=readdirSync(workspace,{recursive:true}).map(String).sort();
    const contents=Object.fromEntries(files.filter(file=>/\.(?:html|css|js)$/.test(file))
      .map(file=>[file,readFileSync(join(workspace,file),'utf8')]));
    return {turns,requests,firstTurnRequests,grants:await Promise.all(grants),ledger,files,contents,log};
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
}

for (const [name,spec] of Object.entries(CASES)) test(`real Pixel output-limit continuation: ${name}`,
  {skip:!pkg||process.platform==='win32',timeout:300000}, async () => {
  const result=await run(spec.prompt,spec.steps,{followUp:spec.followUp});
  const trace=JSON.stringify({...result,log:undefined,requests:result.requests.map(r=>({...r,system:r.system.length}))})+'\n'+result.log;
  const [turn]=result.turns;
  assert.equal(result.grants.length,1,'the ingress asks once, only for the cut owner turn\n'+trace);
  assert.equal(result.grants[0].eligible,true,trace);
  assert.match(result.grants[0].user,/^ods-[0-9a-f]{64}$/,trace);
  assert.ok(result.requests.every(request=>request.maxTokens===8192),trace);
  // The cut call never ran: no file holds its unfinished document.
  assert.ok(!Object.values(result.contents).some(content=>content.includes('--moss')),trace);
  assert.deepEqual(result.ledger,['ready'],trace);
  if(spec.report){
    assert.equal(result.requests.length,2,'one continuation, never a loop\n'+trace);
    assert.ok(turn.delivered.startsWith(`${OUTPUT_LIMIT_CONTINUED_TEXT} ${spec.report}`),trace);
    // A written answer is never told that a file write was cut.
    if(spec.prompt===TEXT)assert.equal(turn.delivered,`${OUTPUT_LIMIT_CONTINUED_TEXT} ${spec.report}`,trace);
    assert.equal(turn.outcome,'failed',trace);
    return;
  }
  const continuation=result.requests[spec.continuationRound];
  // OpenClaw prefixes each user message with its own timestamp envelope.
  assert.match(continuation.users.at(-1),/^\[[^\]\n]+\] ODS internal continuation: /,trace);
  assert.ok(continuation.users.at(-1).endsWith(` ${OUTPUT_LIMIT_CONTINUATION_PROMPT}`),'only the fixed message is new\n'+trace);
  assert.ok(continuation.users.slice(0,-1).some(user=>user.includes('forest')),'the owner request stays in context\n'+trace);
  assert.equal(continuation.system,result.requests[0].system,'the continuation keeps the owner turn system prompt\n'+trace);
  assert.match(continuation.system,/can hold at most about 8192 output tokens/,trace);
  // The same model behaviour without the cut, for comparison.
  const control=await run(spec.prompt,spec.control);
  assert.deepEqual(control.grants,[],'an uncut turn never asks\n'+JSON.stringify(control.grants));
  assert.equal(control.requests[0].system,result.requests[0].system,'the same owner contract');
  assert.deepEqual(turn,control.turns[0],'the owner receives what the uncut turn delivers\n'+trace);
  assert.deepEqual(result.files,control.files,'the same files in the same Playground project\n'+trace);
  assert.deepEqual(result.contents,control.contents,trace);
  if(name==='unfounded ready claim')assert.equal(turn.outcome,'failed',trace);
  if(name==='text'){
    assert.equal(turn.delivered,GUIDE,trace);
    assert.notEqual(turn.outcome,'failed',trace);
    // The next owner message is its own turn, classified by itself.
    const next=result.turns[1];
    assert.equal(next.delivered,GUIDE,trace);
    assert.equal(result.requests.length,result.firstTurnRequests+1,trace);
    assert.match(result.requests.at(-1).users.at(-1),/Summarize that in one sentence\./,trace);
  }
});
