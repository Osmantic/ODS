// Real pinned harness + real ingress, deterministic model, disposable state.
// A silent NO_REPLY to an owner chat message gets one revision pass; a second
// silent reply keeps the ingress's honest fallback.
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
import {OWNER_VISIBLE_REPLY_INSTRUCTION} from '../plugin/owner-visible-reply.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
const ANSWER='We decided to keep the session on reload and restore the last answer.';
const QUESTION="What did we decide about the dashboard reload?\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. " +
  'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';

for (const replies of [['NO_REPLY',ANSWER],['NO_REPLY','NO_REPLY']]) test(`real harness silent owner reply: ${replies.join(' -> ')}`,
  {skip:!pkg,timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-owner-visible-reply-'));
  let rounds=0,log='',child,ingress;
  const requests=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()).messages.filter(message=>message.role==='user')
      .map(message=>typeof message.content==='string'?message.content:JSON.stringify(message.content)));
    const delta={role:'assistant',content:replies[Math.min(rounds++,replies.length-1)]};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'visible-reply-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'visible-reply-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:()=>false});
    export default {id:'visible-reply-fixture',register(api){
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(JSON.parse(body).runId)));return true;
      }});
      api.on('before_prompt_build',(e,c)=>guard.observeRun(c,'pixel',e));
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);
        record({text:e.lastAssistantMessage,trigger:c.trigger,decision:d?.action??null});return d;});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    plugins:{allow:['visible-reply-fixture'],load:{paths:[plugin]},entries:{'visible-reply-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'visible-reply-fixture',messages:[{role:'user',content:QUESTION}]}),
      signal:AbortSignal.timeout(45000)});
    const body=await response.text();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({rounds,requests,events,delivered})+'\n'+log;
    assert.equal(rounds,2,'exactly one revision pass\n'+trace);
    assert.deepEqual(events.map(x=>[x.text,x.trigger,x.decision]),[['NO_REPLY','user','revise'],[replies[1],'user',null]],trace);
    assert.ok(requests[1].at(-1).includes(OWNER_VISIBLE_REPLY_INSTRUCTION),'the revision pass carries the fixed instruction\n'+trace);
    assert.ok(requests[1].some(text=>text.includes('What did we decide about the dashboard reload?')),'the owner message stays in context\n'+trace);
    if(replies[1]===ANSWER){
      assert.equal(delivered,ANSWER,trace);
      assert.notEqual(frames.at(-1).pixel_outcome.status,'failed',trace);
    } else {
      assert.match(delivered,/^Pixel ended without a visible answer/,trace);
      assert.equal(frames.at(-1).pixel_outcome.status,'failed',trace);
    }
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
