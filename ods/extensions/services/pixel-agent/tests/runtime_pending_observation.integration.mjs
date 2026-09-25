// Disposable gateway + adversarial model. Never uses the installed gateway.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;

for (const wrapped of [false,true]) test(`native pending handoff: wrapped=${wrapped}`,
  {skip:!pkg,timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-pending-fixture-'));
  let rounds=0,log='',child,ingress;
  const name='pixel_ods_extension_request_status';
  const upstream=createServer(async(req,res)=>{
    for await(const _ of req) { /* consume */ }
    const call=++rounds;
    const delta={role:'assistant',tool_calls:[{index:0,id:`read-${call}`,type:'function',
      function:{name:wrapped?'tool_call':name,arguments:'{}'}}]};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
      choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
      choices:[{index:0,delta:{},finish_reason:'tool_calls'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'pending-fixture',version:'1.0.0',
    type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pending-fixture',
    contracts:{tools:[wrapped?'tool_call':name]},
    activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {createExtensionRequestStatusTool} from './ods/extension-proposal.mjs';
    import {abortAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
    import {appendFileSync} from 'node:fs';
    import {createHash} from 'node:crypto';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:(id,key)=>{
      const ok=abortAgentHarnessRun((key&&resolveActiveEmbeddedRunSessionId(key))||id);
      record({abort:ok});return ok;},execControl:{signal:()=>{throw Error('must not signal host');}}});
    const name=${JSON.stringify(name)},wrapped=${wrapped};
    const identity={chatId:'chat',requestId:'turn'};
    const status={schemaVersion:1,kind:'ods-extension-request-status',...identity,
      authorizationMode:'install',requestState:'pending',proposalAccepted:true,
      integrationBound:false,prepared:true,extensionId:'example',runtimeStatus:'installing'};
    const observation=createExtensionRequestStatusTool({agentId:'pixel',
      sessionKey:'agent:pixel:openai-user:ods-'+createHash('sha256').update('chat').digest('hex')},{
      waitMs:25,pollMs:5,submit:async payload=>{record({read:payload.action});return status;}});
    let ctx;
    const eventFor=result=>wrapped?{toolName:'tool_call',params:{id:'openclaw:pixel-ods:'+name,args:{}},
      result:{details:{tool:{id:'openclaw:pixel-ods:'+name,name,source:'openclaw',sourceName:'pixel-ods'},result}}}
      :{toolName:name,params:{},result};
    export default {id:'pending-fixture',register(api){
      for (const [path,method] of [
        ['/pixel-ods/verification','deliveryVerificationForRun'],
        ['/pixel-ods/read-only-extension-continuation','readOnlyExtensionRecoveryForRun'],
        ['/pixel-ods/unfinished-extension-decision','unfinishedExtensionDecisionForRun'],
      ]) api.registerHttpRoute({path,auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        const value=guard[method](JSON.parse(body).runId);
        res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return true;
      }});
      api.on('before_prompt_build',(e,c)=>{ctx=c;guard.observeRun(c,'pixel',{prompt:'Use the corrected recipe and try again.'});});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>{guard.observeModelEnd(e,c);record({end:true,verification:guard.deliveryVerificationForRun(c.runId)});});
      api.on('before_tool_call',(e,c)=>guard.beforeToolCall({...e,...eventFor(undefined)},c));
      api.on('after_tool_call',(e,c)=>{guard.afterToolCall({...e,...eventFor(wrapped?e.result.details.result:e.result)},c);
        record({after:true,verification:guard.deliveryVerificationForRun(c.runId)});});
      api.on('before_agent_finalize',(e,c)=>guard.beforeAgentFinalize(e,c));
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
      api.registerTool({name:wrapped?'tool_call':name,description:'Observe the accepted managed build.',
        parameters:{type:'object',properties:{}},async execute(id,args,signal){
          record({execute:true});const result=await observation.execute(id,identity,signal);
          return wrapped?{content:result.content,details:eventFor(result).result.details}:result;
        }});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},
      http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},
      contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,
      api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',
        contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:[wrapped?'tool_call':name],loopDetection:{enabled:true,historySize:12,
      warningThreshold:2,criticalThreshold:4,globalCircuitBreakerThreshold:6,
      detectors:{genericRepeat:true,knownPollNoProgress:true,pingPong:true}}},
    plugins:{allow:['pending-fixture'],load:{paths:[plugin]},
      entries:{'pending-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),
        OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
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
      body:JSON.stringify({model:'openclaw:pixel',stream:wrapped,user:'pending-fixture',
        messages:[{role:'user',content:'/extensions install https://github.com/o/r'}]}),
      signal:AbortSignal.timeout(40000)});
    const body=await response.text();
    const events=readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(response.status,200,body+'\n'+log);
    const packets=wrapped ? body.split('\n').filter(line=>line.startsWith('data: {'))
      .map(line=>JSON.parse(line.slice(6))) : [];
    const delivered=wrapped ? packets.map(x=>x.choices?.[0]?.delta?.content ?? '').join('')
      : JSON.parse(body).choices[0].message.content;
    if(wrapped) assert.ok(packets.some(x=>x.pixel_outcome?.status==='pending'),body);
    assert.match(delivered,/managed installation.*pending/);
    assert.match(delivered,/not verified readiness/);
    assert.doesNotMatch(delivered,/cancelled|canceled|stopped|tool failures|loop|aborted/i);
    assert.equal(events.filter(x=>x.execute).length,1,JSON.stringify(events));
    assert.ok(rounds<=2,`model repeated ${rounds} rounds`);
    assert.ok(events.some(x=>x.verification?.status==='pending'),JSON.stringify(events));
    assert.ok(events.some(x=>x.abort===true),JSON.stringify(events));
    assert.ok(events.filter(x=>x.read).every(x=>x.read==='github-request-status'));
    assert.doesNotMatch(log,/CRITICAL.*identical|progress-limit abort/);
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
  }
});
