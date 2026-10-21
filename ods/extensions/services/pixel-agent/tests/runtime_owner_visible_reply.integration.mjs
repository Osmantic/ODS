// Real pinned harness + real ingress, deterministic model, disposable state.
// A silent NO_REPLY to an owner chat message gets one revision pass; a second
// silent reply keeps the ingress's honest fallback.
//
// A nonidempotent fixture tool performs one real append
// (recorded on disk), then the model returns empty content. The pinned
// OpenClaw gateway must emit its real post-tool warning; the candidate ingress
// must report an incomplete failed outcome, preserve the real task activity,
// and never repeat the tool or the owner request.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn,execFile} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer,gatewayFetch} from '../host/pixel_ingress.mjs';
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
      assert.match(delivered,/^Portal ended without a visible answer/,trace);
      assert.equal(frames.at(-1).pixel_outcome.status,'failed',trace);
    }
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});

// A real nonidempotent tool side effect (one append) followed by an
// empty model reply. The pinned OpenClaw gateway must emit its real post-tool
// warning; the candidate ingress must report an incomplete failed outcome,
// preserve the real task activity, and never repeat the tool or the owner
// request. The upstream response is captured by wrapping deps.fetch and
// splitting only the /v1/chat/completions body stream, so the SDK warning is the
// real one emitted by the pinned gateway, not a fixture.
test('real harness PR7410: post-tool empty reply preserves side effect and never repeats',
  {skip:!pkg,timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-pr7410-'));
  const appendPath=join(root,'side-effect.log');
  let rounds=0,log='',child,ingress;
  const requests=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const parsed=JSON.parse(Buffer.concat(chunks).toString());
    requests.push(parsed.messages.filter(message=>message.role==='user')
      .map(message=>typeof message.content==='string'?message.content:JSON.stringify(message.content)));
    const round=rounds++;
    // Round 0: call the nonidempotent fixture tool. Round 1: empty content.
    const delta=round===0
      ? {role:'assistant',tool_calls:[{index:0,id:'append-0',type:'function',
          function:{name:'fixture_append',arguments:JSON.stringify({line:'pr7410-append'})}}]}
      : {role:'assistant',content:''};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
      choices:[{index:0,delta:{},finish_reason:delta.tool_calls?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'pr7410-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pr7410-fixture',contracts:{tools:['fixture_append']},
    activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {createTaskActivity} from './ods/task-activity.mjs';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:()=>false});
    const activity=createTaskActivity();
    export default {id:'pr7410-fixture',register(api){
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
        const runId=JSON.parse(body).runId;
        record({verification:activity.projection(runId)});
        res.end(JSON.stringify({...guard.deliveryVerificationForRun(runId),task:activity.projection(runId)}));return true;
      }});
      api.on('before_prompt_build',(e,c)=>{record({beginAgent:c.agentId,beginRun:c.runId,eventRun:e.runId});activity.begin(e,c);return guard.observeRun(c,'pixel',e);});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_tool_call',(e,c)=>{const d=guard.beforeToolCall(e,c);activity.before(e,c,d?.block===true);record({before:e.toolName});return d;});
      api.on('after_tool_call',(e,c)=>{activity.after(e,c);return guard.afterToolCall(e,c);});
      api.on('agent_end',(e,c)=>{activity.finish(e,c);return guard.observeAgentEnd(e,c);});
      api.on('tool_result_persist',(e,c)=>guard.toolResultPersist(e,c));
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);
        record({text:e.lastAssistantMessage,trigger:c.trigger,decision:d?.action??null});return d;});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
      api.registerTool({name:'fixture_append',description:'Append one line to the fixture side-effect log.',
        parameters:{type:'object',properties:{line:{type:'string'}},required:['line']},
        async execute(id,args){record({execute:args.line});appendFileSync(${JSON.stringify(appendPath)},args.line+'\\n');return {content:[{type:'text',text:'Appended one line.'}],details:{ok:true}};}});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['fixture_append']},
    plugins:{allow:['pr7410-fixture'],load:{paths:[plugin]},entries:{'pr7410-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
    // Wrap deps.fetch so we can observe only the /v1/chat/completions response
    // and observe the real gateway completion (including the SDK warning)
    // without mutating the response the ingress consumes.
    const realFetch=gatewayFetch;
    const completions=[];
    const deps={execFile,setTimeout,clearTimeout,fetch:async(input,init)=>{
      const url=new URL(input);
      const response=await realFetch(input,init);
      if(url.pathname==='/v1/chat/completions'){
        const [delivery,evidence]=response.body.tee();
        completions.push(new Response(evidence).json());
        return {...response,body:delivery};
      }
      return response;
    }};
    ingress=createIngressServer({token:'fixture-only',gatewayPort:port,deps});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
      headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'pr7410-fixture',messages:[{role:'user',
        content:'Append one line to the fixture log, then report the result.'}]}),
      signal:AbortSignal.timeout(45000)});
    const body=await response.text();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({rounds,requests,events,delivered,completions:completions.length})+'\n'+log;
    // The real gateway completion must have been observed via the wrapped fetch.
    assert.equal(completions.length,1,'the real gateway completion was captured\n'+trace);
    const upstreamBody=(await completions[0]).choices[0].message.content;
    // The pinned OpenClaw gateway emits its real post-tool warning when the
    // model returns empty content after a tool call. Assert on the actual
    // upstream response, not a fixture.
    assert.match(upstreamBody,/some tool actions may have already been executed/i,
      'the pinned gateway emitted its real post-tool warning\n'+trace);
    // The nonidempotent side effect ran exactly once.
    const sideEffects=existsSync(appendPath)?readFileSync(appendPath,'utf8').trim().split('\n').filter(Boolean):[];
    assert.deepEqual(sideEffects,['pr7410-append'],'the fixture tool appended exactly once\n'+trace);
    assert.equal(events.filter(x=>x.execute).length,1,'the tool executed exactly once\n'+trace);
    // The owner request was never replayed.
    assert.equal(requests.length,2,'exactly one tool round and one empty reply round\n'+trace);
    assert.ok(requests.every(messages=>messages.filter(text=>text.includes('Append one line to the fixture log, then report the result.')).length===1),trace);
    // The candidate ingress reports an incomplete failed outcome and preserves
    // the real task activity.
    assert.match(delivered,/request is incomplete/i,trace);
    assert.match(delivered,/check its receipts before repeating any action/i,trace);
    assert.doesNotMatch(delivered,/couldn't generate|some tool actions may have already been executed/i,trace);
    assert.deepEqual(frames.at(-1)?.pixel_outcome,{schemaVersion:1,status:'failed'},trace);
    assert.ok(frames.at(-1).pixel_task,trace);
    assert.deepEqual(frames.at(-1).pixel_task,events.filter(e=>e.verification).at(-1).verification,trace);
    assert.equal(frames.at(-1).pixel_task.calls,1,trace);
    assert.ok(frames.at(-1).pixel_task.activities.some(a=>a.kind==='unknown'&&a.calls===1),
      'the real custom-tool activity is preserved\n'+trace);
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
