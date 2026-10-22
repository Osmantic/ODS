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
const OWNER_PROMPT='Please deliver that existing CSV once more. Format your final answer as **Download:** in its own paragraph, followed by a standalone Markdown image labelled fleet-pr7911-laptop.csv.';
const RAW_ANSWER='**Download:**\n\n![fleet-pr7911-laptop.csv](file:///home/michael/.openclaw/workspace-pixel/fleet-pr7911-laptop.csv)';
const RETRY_KEY='pixel-ods-workspace-document-delivery';
const RETRY_INSTRUCTION_MARKER='pixel_ods_workspace_artifact';

// Real pinned harness + real ingress, deterministic model, disposable state.
// The model returns the same standalone Markdown CSV image twice, without
// tools. The real before_agent_finalize hook must request exactly one revision
// whose instruction mentions pixel_ods_workspace_artifact; the provider must
// see exactly two calls; the original owner prompt must remain in context; and
// the ingress must report a failed outcome for missing verified publication
// without ever passing the raw file URL or a fabricated receipt.
test('real harness document-delivery finalize retry: no verified publication, no raw URL, no fabricated receipt',
  {skip:!pkg,timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-doc-delivery-retry-'));
  let rounds=0,log='',child,ingress;
  const requests=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks).toString());
    requests.push(body.messages.filter(message=>message.role==='user')
      .map(message=>typeof message.content==='string'?message.content:JSON.stringify(message.content)));
    const delta={role:'assistant',content:RAW_ANSWER};
    rounds++;
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
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'doc-delivery-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'doc-delivery-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:()=>false});
    export default {id:'doc-delivery-fixture',register(api){
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
        record({text:e.lastAssistantMessage,trigger:c.trigger,decision:d?.action??null,
          retryKey:d?.retry?.idempotencyKey??null,retryInstruction:d?.retry?.instruction??null,
          retryMaxAttempts:d?.retry?.maxAttempts??null});return d;});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    plugins:{allow:['doc-delivery-fixture'],load:{paths:[plugin]},entries:{'doc-delivery-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'doc-delivery-fixture',messages:[{role:'user',content:OWNER_PROMPT}]}),
      signal:AbortSignal.timeout(45000)});
    const body=await response.text();
    assert.equal(response.status,200,body+'\n'+log);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({rounds,requests,events,delivered,frames:frames.map(f=>f.pixel_outcome??null)})+'\n'+log;

    // Exactly one revision pass: the provider is called exactly twice.
    assert.equal(rounds,2,'provider must receive exactly two calls\n'+trace);
    assert.equal(requests.length,2,'exactly two provider requests\n'+trace);

    // The real before_agent_finalize hook requested one revision with the
    // documented retry shape and instruction marker.
    assert.equal(events.length,2,'exactly two finalize observations\n'+trace);
    assert.equal(events[0].decision,'revise','first finalize must revise\n'+trace);
    assert.equal(events[0].retryKey,RETRY_KEY,'retry idempotency key\n'+trace);
    assert.equal(events[0].retryMaxAttempts,1,'retry maxAttempts must be 1\n'+trace);
    assert.ok(typeof events[0].retryInstruction==='string' && events[0].retryInstruction.includes(RETRY_INSTRUCTION_MARKER),
      'retry instruction must mention pixel_ods_workspace_artifact\n'+trace);
    assert.equal(events[1].decision,null,'second finalize must not revise again\n'+trace);

    // The original owner prompt remains in the second provider call, and the
    // last user message is the correction mentioning pixel_ods_workspace_artifact.
    const secondUsers=requests[1];
    assert.ok(secondUsers.some(text=>text.includes(OWNER_PROMPT)),'original owner prompt must remain in context\n'+trace);
    assert.ok(secondUsers.at(-1).includes(RETRY_INSTRUCTION_MARKER),
      'last user correction must mention pixel_ods_workspace_artifact\n'+trace);

    // The ingress reports a failed outcome for missing verified publication.
    const lastOutcome=frames.at(-1)?.pixel_outcome;
    assert.ok(lastOutcome,'ingress must emit a pixel_outcome\n'+trace);
    assert.equal(lastOutcome.status,'failed','ingress must report failed outcome\n'+trace);
    assert.match(delivered,/requested download was not attached.*verified artifact publication receipt/i,
      'failed outcome must reference missing verified publication\n'+trace);

    // The raw file URL must never be passed through as a delivered answer,
    // and no fabricated receipt may appear in the outcome.
    assert.ok(!delivered.includes('file:///home/michael/.openclaw/workspace-pixel/fleet-pr7911-laptop.csv'),
      'raw file URL must not be delivered\n'+trace);
    assert.ok(!JSON.stringify(lastOutcome).includes('file:///home/michael/.openclaw/workspace-pixel/fleet-pr7911-laptop.csv'),
      'raw file URL must not appear in outcome\n'+trace);
    assert.equal(lastOutcome.artifacts,undefined,'no fabricated receipt may be attached\n'+trace);
    assert.ok(!JSON.stringify(lastOutcome).includes('ods-pixel-workspace-artifact'),
      'no fabricated receipt kind may appear\n'+trace);
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
