// Real pinned harness + real ingress, deterministic model, disposable state.
// An explicit no-tools turn must reach the provider with no callable tools;
// the next normal turn in the same session must regain them; an explicit
// tool_choice:none must suppress tools even when a client function is offered.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
const LUNCH='Without using tools, suggest three simple vegetarian lunches using chickpeas, rice, and tomatoes. Keep it under 80 words.';
const NORMAL='Now list the tools you have available, one per line.';
const TOOL_OFFER={type:'function',function:{name:'fixture_echo',description:'Echo text.',parameters:{type:'object',properties:{text:{type:'string'}},required:['text']}}};

function sse(res,delta,finish){res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:finish??null}]})+'\n\n');}

for (const mode of ['lunch-then-normal','explicit-none']) test(`real harness no-tools ingress: ${mode}`,
  {skip:!pkg,timeout:90000}, async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-no-tools-'));
  let log='',child,ingress;
  const requests=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const parsed=JSON.parse(Buffer.concat(chunks).toString());
    requests.push({tools:parsed.tools??null,tool_choice:parsed.tool_choice??null});
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    sse(res,{role:'assistant',content:'Chickpea rice bowl; tomato chickpea stew; rice salad with chickpeas and tomatoes.'});
    sse(res,{},'stop');
    res.end('data: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'no-tools-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'no-tools-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    const guard=createToolLoopGuard({abortRun:()=>false});
    export default {id:'no-tools-fixture',register(api){
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
      api.on('before_agent_finalize',(e,c)=>guard.beforeAgentFinalize(e,c));
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    plugins:{allow:['no-tools-fixture'],load:{paths:[plugin]},entries:{'no-tools-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
    const send=async body=>{
      const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(45000)});
      const text=await response.text();
      assert.equal(response.status,200,text+'\n'+log);
      assert.ok(text.includes('Chickpea rice bowl'),text);
      assert.ok(!text.includes('\"status\":\"failed\"'),text);
      return text;
    };
    if(mode==='lunch-then-normal'){
      await send({model:'openclaw:pixel',stream:true,user:'no-tools-fixture',messages:[{role:'user',content:LUNCH}]});
      await send({model:'openclaw:pixel',stream:true,user:'no-tools-fixture',messages:[{role:'user',content:NORMAL}]});
      assert.equal(requests.length,2,'two provider turns\n'+log);
      assert.equal(requests[0].tools?.length??0,0,'no-tools turn must reach provider with no callable tools\n'+JSON.stringify(requests));
      assert.ok(Array.isArray(requests[1].tools)&&requests[1].tools.length>0,'normal turn must regain tools\n'+JSON.stringify(requests));
    } else {
      await send({model:'openclaw:pixel',stream:true,user:'no-tools-fixture',tools:[TOOL_OFFER],tool_choice:'none',messages:[{role:'user',content:'Suggest three vegetarian lunches.'}]});
      assert.equal(requests.length,1,'one provider turn\n'+log);
      assert.equal(requests[0].tools?.length??0,0,'explicit tool_choice:none must suppress offered tools\n'+JSON.stringify(requests));
    }
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
