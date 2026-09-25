// Real pinned harness + real ingress, deterministic model, disposable state.
// strixy 2026-09-25: the owner's website request produced one reply that
// wrote the whole page in one tool call and stopped at the output limit
// (finish_reason "length"). The model here replays that reply: OpenClaw
// 2026.6.33 does not run the cut call and skips before_agent_finalize, so the
// ingress asks Pixel for one continuation turn with the fixed message. The
// continuation either answers ('answer') or is cut again ('cut'), which keeps
// the honest output-limit report with no further turn. A long text answer cut
// the same way ('text') reaches the owner as OpenClaw's generic "couldn't
// generate a response" without the continuation, so it is continued as well.
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
import {OUTPUT_LIMIT_CONTINUATION_PROMPT,OUTPUT_LIMIT_UNRECOVERED_TEXT} from '../plugin/output-limit-recovery.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
const PROMPT='as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest type effects.  Best you can do.' +
  "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. " +
  'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const OPENING='Let me build something impressive — a full forest-themed interactive experience.';
const ANSWER='Your forest page is ready in Playground/forest as index.html, styles.css and script.js.';

for (const continuation of ['answer','cut','text']) test(`real harness output-limit continuation: ${continuation}`,
  {skip:!pkg||process.platform==='win32',timeout:120000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-output-limit-'));
  let rounds=0,log='',child,ingress;
  const requests=[];
  const chunk=(delta,finish=null,extra={})=>'data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
    choices:[{index:0,delta,finish_reason:finish}],...extra})+'\n\n';
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const part of req) chunks.push(part);
    const body=JSON.parse(Buffer.concat(chunks).toString());
    requests.push(body.messages.filter(message=>message.role==='user')
      .map(message=>typeof message.content==='string'?message.content:JSON.stringify(message.content)));
    const round=rounds++;
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    if(round===1&&continuation!=='cut'){
      res.write(chunk({role:'assistant',content:ANSWER}));
      res.end(chunk({},'stop')+'data: [DONE]\n\n');return;
    }
    // The recorded shape: an opening sentence, then one whole-page write whose
    // arguments end mid-document when the output limit is reached.
    res.write(chunk({role:'assistant',content:OPENING}));
    if(continuation!=='text')res.write(chunk({tool_calls:[{index:0,id:`call-${round}`,type:'function',function:{name:'fixture_write',
      arguments:'{"path":"forest/index.html","content":"<!doctype html><html><head><style>:root{--moss:#2f5d3a'}}]}));
    res.end(chunk({},'length',{usage:{prompt_tokens:900,completion_tokens:4096,total_tokens:4996}})+'data: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'output-limit-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'output-limit-fixture',contracts:{tools:['fixture_write']},activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:()=>false});
    const route=(api,path,answer)=>api.registerHttpRoute({path,auth:'gateway',match:'exact',handler:async(req,res)=>{
      let body='';for await(const part of req)body+=part;
      const value=answer(JSON.parse(body).runId);record({route:path,value});
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return true;
    }});
    export default {id:'output-limit-fixture',register(api){
      route(api,'/pixel-ods/verification',runId=>guard.deliveryVerificationForRun(runId));
      route(api,'/pixel-ods/output-limit-continuation',runId=>guard.outputLimitContinuationForRun(runId));
      api.on('before_prompt_build',(e,c)=>guard.observeRun(c,'pixel',e));
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_message_write',(e,c)=>{if(e.message?.role==='assistant')record({write:e.message.stopReason});
        return guard.observeAssistantMessage(e,c);});
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);
        record({finalize:e.lastAssistantMessage??null,decision:d?.action??null});return d;});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
      api.registerTool({name:'fixture_write',description:'Write one workspace file.',
        parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content']},
        async execute(id,args){record({execute:args.path});return {content:[{type:'text',text:'written'}]};}});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['fixture_write']},plugins:{allow:['output-limit-fixture'],load:{paths:[plugin]},
      entries:{'output-limit-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'output-limit-fixture',messages:[{role:'user',content:PROMPT}]}),
      signal:AbortSignal.timeout(60000)});
    const body=await response.text();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({rounds,requests,events,delivered})+'\n'+log;
    assert.equal(rounds,2,'exactly one continuation turn, never a loop\n'+trace);
    assert.deepEqual(events.filter(x=>x.execute),[],'the cut write call never ran\n'+trace);
    const grants=events.filter(x=>x.route==='/pixel-ods/output-limit-continuation');
    assert.equal(grants.length,1,'the ingress asks once, for the owner turn only\n'+trace);
    assert.equal(grants[0].value.eligible,true,trace);
    assert.match(grants[0].value.user,/^ods-[0-9a-f]{64}$/,trace);
    // OpenClaw prefixes each user message with its own timestamp envelope.
    assert.match(requests[1].at(-1),/^\[[^\]\n]+\] ODS internal continuation: /,trace);
    assert.ok(requests[1].at(-1).endsWith(` ${OUTPUT_LIMIT_CONTINUATION_PROMPT}`),'the continuation carries only the fixed message\n'+trace);
    assert.ok(requests[1].slice(0,-1).some(text=>text.includes('forest theme')),'the owner request stays in context\n'+trace);
    if(continuation!=='cut'){
      assert.deepEqual(events.filter(x=>'write' in x).map(x=>x.write),['length','stop'],trace);
      // OpenClaw skipped the hook for the cut turn; only the answer reached it.
      assert.deepEqual(events.filter(x=>'finalize' in x).map(x=>[x.finalize,x.decision]),[[ANSWER,null]],trace);
      assert.equal(delivered,ANSWER,trace);
      assert.notEqual(frames.at(-1).pixel_outcome.status,'failed',trace);
    } else {
      assert.deepEqual(events.filter(x=>'write' in x).map(x=>x.write),['length','length'],trace);
      assert.deepEqual(events.filter(x=>'finalize' in x),[],'OpenClaw skips the hook for both cut turns\n'+trace);
      assert.ok(delivered.startsWith(OUTPUT_LIMIT_UNRECOVERED_TEXT),trace);
      assert.equal(frames.at(-1).pixel_outcome.status,'failed',trace);
    }
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
