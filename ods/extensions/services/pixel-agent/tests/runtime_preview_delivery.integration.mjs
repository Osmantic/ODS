// Real pinned harness, real workspace write, deterministic model and preview host.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
for (const refresh of [false,true]) for (const publishFails of [false,true]) test(`real harness recovers saved HTML without replaying work: refresh=${refresh}, publishFails=${publishFails}`, {skip:!pkg,timeout:90000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-preview-delivery-'));
  const workspace=join(root,'workspace');mkdirSync(workspace);
  const html='<!doctype html><title>Fixture game</title><p>Ready</p>';
  let rounds=0,log='',child,ingress,toolResults=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    toolResults=JSON.parse(Buffer.concat(chunks).toString()).messages.filter(message=>message.role==='tool');
    const round=rounds++;
    const delta=round===0 ? {role:'assistant',tool_calls:[{index:0,id:'write-game',type:'function',
      function:{name:'write',arguments:JSON.stringify({path:'signal-garden/index.html',content:html})}}]}
      : refresh && round===1 ? {role:'assistant',tool_calls:[{index:0,id:'publish-game',type:'function',
        function:{name:'pixel_ods_workspace_preview',arguments:JSON.stringify({relativeDirectory:'Playground/signal-garden'})}}]}
      : refresh && round===2 ? {role:'assistant',tool_calls:[{index:0,id:'check-game',type:'function',
        function:{name:'exec',arguments:JSON.stringify({command:"node -e \"require('fs').appendFileSync('check-count','1');console.log('checked once')\"",workdir:workspace})}}]}
      : {role:'assistant',content:'The file is ready. Would you like a preview?'};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:delta.tool_calls?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'preview-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'preview-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from ${JSON.stringify(new URL('../plugin/tool-loop-guard.mjs',import.meta.url).href)};
    import {createWorkspacePreviewTool} from ${JSON.stringify(new URL('../plugin/workspace-preview.mjs',import.meta.url).href)};
    import {readFileSync,appendFileSync} from 'node:fs';
    import {createHash} from 'node:crypto';
    const publisher=createWorkspacePreviewTool({request:async request=>{
      const bytes=readFileSync(${JSON.stringify(join(workspace,'Playground/signal-garden/index.html'))});
      const hash=createHash('sha256').update(bytes).digest('hex'),siteId='site-'+hash.slice(0,24);
      appendFileSync(${JSON.stringify(join(root,'published'))},request.relativeDirectory+'\\n');
      if (${publishFails} && (!${refresh} || readFileSync(${JSON.stringify(join(root,'published'))},'utf8').trim().split('\\n').length>1)) return {status:'failed'};
      return {schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:request.relativeDirectory,
        port:9437,siteId,url:'http://'+siteId+'.localhost:9437/'+siteId+'/',sha256:hash,entrySha256:hash,
        entryFile:'index.html',files:1,bytes:bytes.length,httpStatus:200,readbackVerified:true,executable:false,overwritten:false,
        boundary:'Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.'};
    }});
    const guard=createToolLoopGuard({publishWorkspacePreview:(params,{signal})=>publisher.execute('delivery',params,signal)});
    export default {id:'preview-fixture',register(api){
      api.registerTool(publisher);
      api.on('before_prompt_build',(event,ctx)=>guard.observeRun(ctx,'pixel',event,{workspaceRoot:${JSON.stringify(workspace)}}));
      api.on('before_tool_call',(event,ctx)=>guard.beforeToolCall(event,ctx));
      api.on('after_tool_call',(event,ctx)=>guard.afterToolCall(event,ctx));
      api.on('tool_result_persist',(event,ctx)=>guard.toolResultPersist(event,ctx));
      api.on('before_agent_finalize',async(event,ctx)=>{
        await guard.recoverWorkspacePreview(event,ctx);
        const decision=guard.beforeAgentFinalize(event,ctx);
        appendFileSync(${JSON.stringify(join(root,'verdicts.jsonl'))},JSON.stringify({decision,verification:guard.deliveryVerificationForRun(ctx.runId)})+'\\n');
        return decision;
      });
      api.on('reply_payload_sending',event=>guard.replyPayloadSending(event));
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let raw='';for await(const chunk of req)raw+=chunk;
        const {runId}=JSON.parse(raw);
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(runId)));return true;
      }});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only-0123456789abcdef'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['write',...(refresh?['exec','pixel_ods_workspace_preview']:[])]},plugins:{allow:['preview-fixture'],load:{paths:[plugin]},entries:{'preview-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let n=0;n<250;n++){
      try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
      if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);
    }
    assert.ok(ready,log);
    ingress=createIngressServer({token:'fixture-only-0123456789abcdef',gatewayPort:port});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'preview-fixture',messages:[{role:'user',content:'Build and publish a website in existing signal-garden.'}]}),signal:AbortSignal.timeout(45000)});
    const body=await response.text();assert.equal(response.status,200,body+'\n'+log);
    assert.equal(rounds,refresh?4:2,log);
    if(refresh)assert.equal(readFileSync(join(workspace,'check-count'),'utf8'),'1','verification command must execute exactly once');
    try { assert.equal(readFileSync(join(workspace,'Playground/signal-garden/index.html'),'utf8'),html); }
    catch(error) { throw new Error(error.message+'\n'+JSON.stringify(toolResults)+'\n'+body+'\n'+log); }
    let publications;
    try {publications=readFileSync(join(root,'published'),'utf8');}
    catch(error){throw new Error(error.message+'\n'+JSON.stringify(toolResults)+'\n'+body+'\n'+log);}
    assert.equal(publications,'Playground/signal-garden\n'.repeat(refresh?2:1),JSON.stringify(toolResults)+'\n'+body+'\n'+log);
    const verdicts=readFileSync(join(root,'verdicts.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(verdicts.at(-1).verification.status,publishFails?'failed':'passed',JSON.stringify(verdicts));
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    if(publishFails){
      assert.equal(frames.at(-1).pixel_outcome.status,'failed');
      if(!refresh)assert.equal(frames.at(-1).pixel,undefined);
      assert.doesNotMatch(body,/Open preview/);
      assert.match(log,/revision after potential side effects/);
      return;
    }
    // The OpenAI-compatible endpoint retains model text. ODS ingress consumes
    // this separate host-authoritative projection to append its preview card.
    assert.match(verdicts.at(-1).verification.text,/Open preview/);
    assert.equal(verdicts.at(-1).verification.deliveryMode,'append');
    assert.equal(verdicts.at(-1).verification.preview.kind,'ods-pixel-workspace-preview');
    assert.match(body,/Open preview/);
    assert.equal(frames.at(-1).pixel.preview.kind,'ods-pixel-workspace-preview');
    assert.doesNotMatch(log,/revision after potential side effects/);
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child && child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
