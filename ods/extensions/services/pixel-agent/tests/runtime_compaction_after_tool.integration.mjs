// Real pinned gateway and ingress: direct/deferred tools, actual compaction,
// one observable append and exactly one complete terminal answer.
import test, {after} from 'node:test';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn,execFile} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,rmSync,existsSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer,gatewayFetch} from '../host/pixel_ingress.mjs';
const installed=process.env.OPENCLAW_PACKAGE;
let pkg=installed, runtimeCopy;
// Exercise the reviewed installer recipe on a private runtime copy. The red
// control retains the prior recipe; neither path modifies the installed SDK.
if (installed && process.env.ODS_COMPACTION_DELIVERY_RED !== '1') {
  const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json',import.meta.url)));
  const hash=text=>createHash('sha256').update(text).digest('hex');
  let source=readFileSync(join(installed,'dist/selection-BEwSQKM-.js'),'utf8');
  const previous=hash(source)===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements[hash(source)];
  if(previous)for(const [before,value] of [...previous].reverse()){
    assert.equal(source.split(value).length,2);source=source.replace(value,()=>before);
  }
  assert.equal(hash(source),manifest.sourceSha256,'refuse unknown SDK source');
  for(const [before,value] of manifest.replacements){
    assert.equal(source.split(before).length,2);source=source.replace(before,()=>value);
  }
  assert.equal(hash(source),manifest.patchedSha256);
  runtimeCopy=mkdtempSync(join(tmpdir(),'ods-compaction-delivery-runtime-'));pkg=join(runtimeCopy,'package');
  cpSync(installed,pkg,{recursive:true});writeFileSync(join(pkg,'dist/selection-BEwSQKM-.js'),source);
}
after(()=>{if(runtimeCopy)rmSync(runtimeCopy,{recursive:true,force:true});});

// The ODS install applies host/openclaw-completion-recovery.json to the
// pinned OpenClaw dist. Detect it the same way runtime_progress_finalization
// does, so a missing repair is reported as a skip, not a failure.
function completionRecoveryRepaired(dir) {
  try {
    const [[,repaired]]=JSON.parse(readFileSync(new URL('../host/openclaw-completion-recovery.json',import.meta.url),'utf8')).replacements;
    return readdirSync(join(dir,'dist')).some(name=>name.startsWith('agent-command-') &&
      readFileSync(join(dir,'dist',name),'utf8').includes(repaired));
  } catch { return false; }
}
const repairedRuntime=Boolean(pkg) && completionRecoveryRepaired(pkg);

// Bounded fixture request; the append is confined to its disposable root.
const OWNER_REQUEST='Append one line to the fixture log, then report the result.';
const WARMUP_REQUEST='Remember the fixture label: earlier-turn.';
// The model's complete, correct, nonempty final answer. It must be delivered
// verbatim; the ingress's empty-completion fallback must not replace it.
const ENTRIES=Array.from({length:46},(_,i)=>({name:`fixture-entry-${String(i+1).padStart(2,'0')}${i%2?'':'.txt'}`,folder:Boolean(i%2)}));
const LISTING=ENTRIES.map(x=>`${x.folder?'drwxr-xr-x':'-rw-------'} 1 sandbox sandbox 4096 Oct 19 05:20 ${x.name}`).join('\n');
const ANSWER='The fixture log contains one new line. Fixture entries:\n'+ENTRIES.map(x=>`- ${x.name} (${x.folder?'folder':'file'})`).join('\n');

// The fixture tool's real, observable side effect: one append to a file
// inside the disposable root. It is the "one real side effect" the test
// requires to run exactly once.
const SIDE_EFFECT_LINE='compaction-after-tool-append';

for (const variant of ['plain','compaction','8k-direct','8k-deferred','8k-deferred-plain','8k-deferred-history']) test(`real harness compaction after tool: ${variant}`,
  {skip:!pkg ? true : !variant.endsWith('plain') && !repairedRuntime
    ? 'needs the ODS completion-recovery repair (host/openclaw_tool_recovery.py --completion-recovery)' : false,
  timeout:90000}, async () => {
  const small=variant.startsWith('8k');
  const deferred=variant.includes('deferred');
  const warmups=variant.endsWith('history')?3:0;
  const root=mkdtempSync(join(tmpdir(),`ods-compaction-after-tool-${variant}-`));
  const appendPath=join(root,'side-effect.log');
  let rounds=0,summaries=0,log='',child,ingress;
  const requests=[];
  const upstream=createServer(async(req,res)=>{
    // Runtime discovery/health probes are not model requests. A bodyless GET
    // must receive an HTTP response instead of rejecting this async handler.
    if(req.method!=='POST'||req.url!=='/v1/chat/completions'){
      res.writeHead(404,{'Content-Type':'application/json'});res.end('{}');return;
    }
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const request=JSON.parse(Buffer.concat(chunks).toString());
    // A compaction summarization request carries no tools. Answer it
    // separately so it never consumes an agent round.
    if (!request.tools?.length) {
      summaries++;
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.write('data: '+JSON.stringify({id:'summary',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',
        content:'Summary: the owner asked to append one line; the tool appended it once and returned fixture entries; the answer reported that result.'},finish_reason:null}]})+'\n\n');
      res.end('data: '+JSON.stringify({id:'summary',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
      return;
    }
    // Give the history case six real messages before its actual attempt. A
    // compaction may leave fewer messages than prePromptMessageCount; the
    // delivered result must still be the new answer, never this older reply.
    if(JSON.stringify(request.messages.filter(m=>m.role==='user').at(-1)?.content).includes(WARMUP_REQUEST)){
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.write('data: '+JSON.stringify({id:'warmup',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'Earlier-turn label remembered.'},finish_reason:null}]})+'\n\n');
      res.end('data: '+JSON.stringify({id:'warmup',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
      return;
    }
    requests.push(request.messages.filter(message=>message.role==='user')
      .map(message=>typeof message.content==='string'?message.content:JSON.stringify(message.content)));
    const round=rounds++;
    // Round 0: one tool call. Round 1: the complete nonempty answer.
    const delta=round===0
      ? {role:'assistant',tool_calls:[{index:0,id:'append-0',type:'function',
          function:{name:deferred?'tool_call':'fixture_append',arguments:JSON.stringify(deferred?{id:'fixture_append',args:{line:SIDE_EFFECT_LINE}}:{line:SIDE_EFFECT_LINE})}}]}
      : {role:'assistant',content:ANSWER};
    // On the answer round of the 'compaction' variant, report near-window
    // prompt usage so OpenClaw runs a real threshold auto-compaction after
    // the answer. The 'plain' variant reports ordinary usage.
    const usage=!variant.endsWith('plain') && round===1
      ? {usage:small?{prompt_tokens:5622,completion_tokens:575,total_tokens:6197}:{prompt_tokens:31000,completion_tokens:120,total_tokens:31120}}
      : {};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
      choices:[{index:0,delta:{},finish_reason:delta.tool_calls?'tool_calls':'stop'}],...usage})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'compaction-after-tool-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'compaction-after-tool-fixture',contracts:{tools:['fixture_append']},
    activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {createTaskActivity} from './ods/task-activity.mjs';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:()=>false});
    const activity=createTaskActivity();
    export default {id:'compaction-after-tool-fixture',register(api){
      // This isolated fixture has no spawning tools; explicitly declare the
      // nondelegated gateway contract used by the paired ingress.
      api.registerHttpRoute({path:'/pixel-ods/subagent-delivery',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:JSON.parse(body).runId,status:'not-delegated'}));return true;
      }});
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        const runId=JSON.parse(body).runId;await guard.settleDelivery(runId);
        record({verification:activity.projection(runId)});
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify({...guard.deliveryVerificationForRun(runId),task:activity.projection(runId)}));return true;
      }});
      api.on('before_prompt_build',(e,c)=>{record({beginAgent:c.agentId,beginRun:c.runId,eventRun:e.runId});activity.begin(e,c);return guard.observeRun(c,'pixel',e);});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_compaction',(e,c)=>{record({compaction:'start',sessionKey:Boolean(c?.sessionKey)});guard.observeCompaction(c,'start');});
      api.on('after_compaction',(e,c)=>{record({compaction:'end'});guard.observeCompaction(c,'end');});
      api.on('before_tool_call',(e,c)=>{const d=guard.beforeToolCall(e,c);activity.before(e,c,d?.block===true);record({before:e.toolName});return d;});
      api.on('after_tool_call',(e,c)=>{activity.after(e,c);return guard.afterToolCall(e,c);});
      api.on('agent_end',(e,c)=>{record({endToolResults:(e.messages??[]).filter(m=>m.role==='toolResult').map(m=>m.toolName)});activity.finish(e,c);return guard.observeAgentEnd(e,c);});
      api.on('tool_result_persist',(e,c)=>guard.toolResultPersist(e,c));
      api.on('before_message_write',(e,c)=>{if(e.message?.role==='assistant')record({write:(e.message.content??[]).filter(b=>b?.type==='toolCall').length,
        text:(e.message.content??[]).some(b=>b?.type==='text'&&b.text),sessionKey:Boolean(c?.sessionKey)});return guard.observeAssistantMessage(e,c);});
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);
        record({finalize:e.lastAssistantMessage?.slice(0,40)??null,decision:d?.action??null});return d;});
      api.on('reply_payload_sending',e=>{const d=guard.replyPayloadSending(e);record({deliveryKind:e.kind,text:e.payload?.text,cancel:d?.cancel===true});return d;});
      api.registerTool({name:'fixture_append',description:'Append one line to the fixture side-effect log.',
        parameters:{type:'object',properties:{line:{type:'string'}},required:['line']},
        async execute(id,args){record({execute:args.line});appendFileSync(${JSON.stringify(appendPath)},args.line+'\\n');return {content:[{type:'text',text:${JSON.stringify(LISTING)}}],details:{ok:true}};}});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:small?8192:32768,heartbeat:{every:'0m'},
      // The 'compaction' variant needs a real threshold auto-compaction after
      // the answer. keepRecentTokens:1 and reserveTokensFloor:0 make the
      // near-window usage on the answer round cross the threshold.
      ...(small?{compaction:{keepRecentTokens:512,reserveTokens:3277,reserveTokensFloor:0}}:!variant.endsWith('plain')?{compaction:{mode:'default',keepRecentTokens:1,reserveTokensFloor:0}}:{})},
      list:[{id:'pixel',default:true,skills:[],contextTokens:small?8192:32768}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:small?8192:32768,maxTokens:small?2048:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['fixture_append'],toolSearch:{enabled:deferred,mode:'tools'}},
    plugins:{allow:['compaction-after-tool-fixture'],load:{paths:[plugin]},
      entries:{'compaction-after-tool-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},
      stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let n=0;n<250;n++){
      try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
      if(ready)break;assert.equal(child.exitCode,null,log);assert.equal(child.signalCode,null,log);await delay(100);
    }
    assert.ok(ready,log);
    // Wrap deps.fetch so we can observe only the /v1/chat/completions response
    // and capture the real gateway completion (including any SDK warning)
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
    for(let i=0;i<warmups;i++){
      const warmup=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
        headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},
        body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'compaction-after-tool-fixture',messages:[{role:'user',content:WARMUP_REQUEST}]}),
        signal:AbortSignal.timeout(45000)});
      assert.equal(warmup.status,200);assert.match(await warmup.text(),/Earlier-turn label remembered/);
    }
    const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
      headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'compaction-after-tool-fixture',messages:[{role:'user',content:OWNER_REQUEST}]}),
      signal:AbortSignal.timeout(45000)});
    const body=await response.text();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({variant,rounds,summaries,requests,events,delivered,completions:completions.length})+'\n'+log;

    // The real gateway completion was observed via the wrapped fetch.
    assert.equal(completions.length,warmups+1,'every real gateway completion was captured\n'+trace);
    const upstreamBody=(await completions.at(-1)).choices[0].message.content;
    assert.equal(upstreamBody,ANSWER,'the gateway itself delivered the complete final answer\n'+trace);

    // The nonidempotent side effect ran exactly once.
    const sideEffects=existsSync(appendPath)?readFileSync(appendPath,'utf8').trim().split('\n').filter(Boolean):[];
    assert.deepEqual(sideEffects,[SIDE_EFFECT_LINE],'the fixture tool appended exactly once\n'+trace);
    assert.equal(events.filter(x=>x.execute).length,1,'the tool executed exactly once\n'+trace);

    // No replay: the owner request appears exactly once in every model call.
    assert.equal(requests.length,2,'exactly one tool round and one answer round\n'+trace);
    assert.ok(requests.every(messages=>messages.filter(text=>text.includes(OWNER_REQUEST)).length===1),
      'the owner request was never replayed\n'+trace);

    // One terminal delivery: the answer is delivered verbatim, and the
    // ingress's empty-completion fallback is not used.
    assert.equal(delivered,ANSWER,'the complete nonempty answer was delivered\n'+trace);
    assert.doesNotMatch(delivered,/Portal ended without a visible answer|couldn't generate|some tool actions may have already been executed/i,
      'the empty-completion fallback was not used\n'+trace);
    assert.notEqual(frames.at(-1)?.pixel_outcome?.status,'failed','the outcome is not failed\n'+trace);

    // The real task activity is preserved.
    assert.ok(frames.at(-1).pixel_task,trace);
    assert.deepEqual(frames.at(-1).pixel_task,events.filter(e=>e.verification).at(-1).verification,trace);
    assert.equal(frames.at(-1).pixel_task.calls,1,trace);
    if(deferred)assert.ok(events.some(x=>x.endToolResults?.includes('fixture_append')),
      'nested tool evidence remains available to agent_end after compaction\n'+trace);

    if(!variant.endsWith('plain')){
      // OpenClaw ran a real compaction summarization call after the answer.
      assert.ok(summaries>=1,'OpenClaw ran a real compaction summarization call after the answer\n'+trace);
      assert.ok(events.some(x=>x.compaction==='start'&&x.sessionKey),'before_compaction fired for the run\n'+trace);
      assert.ok(events.some(x=>x.compaction==='end'),'after_compaction fired\n'+trace);
      // The answer survived the compaction: the guard did not forfeit it.
      assert.ok(events.some(x=>x.finalize&&x.finalize.startsWith(ANSWER.slice(0,40))),
        'the answer was the final reply at before_agent_finalize\n'+trace);
    } else {
      // The control: no compaction, the answer is delivered.
      assert.equal(summaries,0,'no compaction summarization call in the plain variant\n'+trace);
      assert.ok(!events.some(x=>x.compaction),'no compaction in the plain variant\n'+trace);
    }
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    try {
      if(child&&child.exitCode===null&&child.signalCode===null){
        const closed=once(child,'close');
        const signal=kind=>{try{process.kill(-child.pid,kind);}catch(error){if(error.code!=='ESRCH')throw error;}};
        signal('SIGTERM');await Promise.race([closed,delay(3000)]);
        if(child.exitCode===null&&child.signalCode===null){signal('SIGKILL');await Promise.race([closed,delay(3000)]);}
      }
    } finally {
      upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
    }
  }
});
