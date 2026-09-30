// Actual pinned gateway, native child/announce and offline model. Only temporary state.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {cpSync,mkdtempSync,mkdirSync,writeFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {setTimeout as delay} from 'node:timers/promises';
const installed=process.env.OPENCLAW_PACKAGE_DIR;
const sha=value=>createHash('sha256').update(value).digest('hex');

for(const interim of ['final','silent','waiting'])
test(`real gateway deferred delegation waits for two children and a verified revised terminal answer: interim=${interim}`,
  {skip:!installed || process.platform==='win32',timeout:120000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-delegation-hooks-'));
  const pkg=join(root,'package'), workspace=join(root,'workspace'), plugin=join(root,'plugin');
  let child, ingress, log='', revision=false, requests=0;
  cpSync(installed,pkg,{recursive:true});
  assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'))).version,'2026.6.33');
  for(const [name,module] of [['hook-provenance','hook-agent-context-ugCMMoT5.js'],
    ['context-usage','attempt-execution-DnVHak5f.js'],['compaction-empty','proxy-Bsfwfsp-.js']]) {
    const recipe=JSON.parse(readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
    const target=join(pkg,'dist',module);let text=readFileSync(target,'utf8');
    if(sha(text)!==recipe.patchedSha256) {
      assert.equal(sha(text),recipe.sourceSha256);
      for(const [before,after] of recipe.replacements){assert.equal(text.split(before).length,2);text=text.replace(before,after);}
      assert.equal(sha(text),recipe.patchedSha256);writeFileSync(target,text);
    }
  }
  mkdirSync(workspace);mkdirSync(plugin);
  const eventsFile=join(root,'hooks.jsonl');
  for(const name of ['subagent-delivery.mjs','owner-visible-reply.mjs']) cpSync(new URL('../plugin/'+name,import.meta.url),join(plugin,name));
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'delivery-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'delivery-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {appendFileSync} from 'node:fs';
    import {subagentDeliveryFor} from './subagent-delivery.mjs';
    import {getSessionEntry,resolveStorePath} from ${JSON.stringify(pathToFileURL(join(pkg,'dist/plugin-sdk/session-store-runtime.js')).href)};
    import {extractAssistantVisibleText} from ${JSON.stringify(pathToFileURL(join(pkg,'dist/plugin-sdk/agent-runtime.js')).href)};
    let revised=false;const sharedGuard={};
    const record=(hook,event,ctx,extra={})=>appendFileSync(${JSON.stringify(eventsFile)},JSON.stringify({
      hook,agentId:ctx.agentId,runId:ctx.runId,sessionId:ctx.sessionId,sessionKey:ctx.sessionKey,trigger:ctx.trigger,
      provenance:ctx.inputProvenance,success:event.success,text:event.lastAssistantMessage,
      messageRoles:event.messages?.map(m=>m.role),
      lastStopReason:[...(event.messages??[])].reverse().find(m=>m.role==='assistant')?.stopReason,
      lastText:[...(event.messages??[])].reverse().find(m=>m.role==='assistant')?.content?.filter(c=>c.type==='text').map(c=>c.text).join(''),...extra})+'\\n');
    export default {id:'delivery-fixture',register(api){
      const registry=subagentDeliveryFor(sharedGuard,{finalText:extractAssistantVisibleText,
        resolveOwnerSession:sessionKey=>getSessionEntry({sessionKey,storePath:resolveStorePath(api.config?.session?.store,{agentId:'pixel'})})});
      api.on('before_prompt_build',(event,ctx)=>{registry.observe(event,ctx);record('prompt',event,ctx);});
      api.on('before_tool_call',(event,ctx)=>{registry.before(event,ctx);record('before-tool',event,ctx,{toolName:event.toolName,toolCallId:ctx.toolCallId,params:event.params});});
      api.on('subagent_spawned',(event,ctx)=>{registry.nativeSpawn(event,ctx);record('spawned',event,ctx,{event,ctx});});
      api.registerHttpRoute({path:'/pixel-ods/subagent-delivery',auth:'gateway',match:'exact',handler:async(req,res)=>{
        const parts=[];for await(const p of req)parts.push(p);const {user,runId}=JSON.parse(Buffer.concat(parts));
        const result=registry.read(user,runId);record('delivery',{}, {runId},{result,entryId:getSessionEntry({sessionKey:'agent:pixel:openai-user:'+user,storePath:resolveStorePath(api.config?.session?.store,{agentId:'pixel'})})?.sessionId});
        res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(result));return true;
      }});
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        for await(const p of req)void p;res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({status:'none'}));return true;
      }});
      api.on('before_agent_finalize',(event,ctx)=>{
        const revise=event.lastAssistantMessage==='CONSOLIDATED_REVIEW'&&!revised;
        if(revise)revised=true;
        record('finalize',event,ctx,{revise});
        const decision=revise?{action:'revise',reason:'Verify the existing child report before finalizing.'}:undefined;
        registry.finalize(event,ctx,decision);return decision;
      });
      api.on('after_tool_call',(event,ctx)=>{registry.after(event,ctx);record('tool',event,ctx,{toolName:event.toolName,toolCallId:ctx.toolCallId,result:event.result,error:event.error});});
      api.on('agent_end',(event,ctx)=>{registry.end(event,ctx);record('end',event,ctx);});
    }};
  `);
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks));requests++;
    const all=JSON.stringify(body.messages);
    const userMessages=body.messages.filter(m=>m.role==='user').map(m=>typeof m.content==='string'?m.content:JSON.stringify(m.content)).join('\n');
    let delta,finish='stop';
    const deferred=(id,name,args)=>({index:0,id,type:'function',function:{name:'tool_call',arguments:JSON.stringify({id:name,args})}});
    if(userMessages.includes('FAIL_FIXTURE')) {
      res.writeHead(400,{'Content-Type':'application/json'});
      res.end(JSON.stringify({error:{message:'offline fixture provider refusal',type:'invalid_request_error'}}));return;
    } else if(userMessages.includes('HELLO_FIXTURE')) {
      delta={role:'assistant',content:'HELLO_VERIFIED'};
    } else if(userMessages.includes('CHILD_FIXTURE_TASK')&&!userMessages.includes('Internal task completion event')) {
      if(interim!=='final'&&userMessages.includes('CHILD_FIXTURE_TASK 1')) {
        // Release child two only after the first announced parent turn has
        // actually ended. This reproduces the live partial-completion ordering.
        let firstEnded=false;
        for(let i=0;i<250;i++) {
          const events=existsSync(eventsFile)?readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse):[];
          firstEnded=events.some(e=>e.hook==='end'&&e.runId?.startsWith('announce:'));
          if(firstEnded)break;await delay(50);
        }
        assert.ok(firstEnded,'first parent announcement must finish while the second child is pending');
      } else await delay(300);
      delta={role:'assistant',content:'CHILD_VERIFIED'};
    } else if(userMessages.includes('Internal task completion event') || userMessages.includes('CONSOLIDATED_REVIEW') || revision) {
      const finished=readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse)
        .filter(e=>e.hook==='end'&&e.sessionKey?.includes(':subagent:')&&e.lastText==='CHILD_VERIFIED').length;
      if(interim!=='final'&&finished<2)delta={role:'assistant',content:interim==='silent'?'NO_REPLY':'One review arrived; waiting for the second.'};
      else {delta={role:'assistant',content:revision?'CONSOLIDATED_VERIFIED':'CONSOLIDATED_REVIEW'};revision=true;}
    } else if(all.includes('childSessionKey')) {
      delta={role:'assistant',tool_calls:[deferred('yield-fixture','sessions_yield',{})]};finish='tool_calls';
    } else {
      delta={role:'assistant',content:'Waiting for the review.',tool_calls:[0,1].map((i)=>({...deferred('spawn-fixture-'+i,'sessions_spawn',{task:'CHILD_FIXTURE_TASK '+i+': return CHILD_VERIFIED, no tools.',runtime:'subagent',mode:'run',label:'fixture-review-'+i}),index:i}))};finish='tool_calls';
    }
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:finish}],usage:{prompt_tokens:500,completion_tokens:30,total_tokens:530}})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only-0123456789abcdef'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace,skipBootstrap:true,sandbox:{mode:'off'},model:{primary:'fixture/test'},contextTokens:131072,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:131072,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['tool_search','tool_describe','tool_call','sessions_spawn','sessions_yield'],toolSearch:{enabled:true,mode:'tools'},loopDetection:{enabled:false}},plugins:{allow:['delivery-fixture'],load:{paths:[plugin]},entries:{'delivery-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let i=0;i<250;i++){try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);}
    assert.ok(ready,log);
    ingress=createIngressServer({token:'fixture-only-0123456789abcdef',gatewayPort:port});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const ingressPort=ingress.address().port;
    const response=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'delegation-fixture',messages:[{role:'user',content:'Delegate two read-only reviews, yield, and consolidate the result.'}]}),signal:AbortSignal.timeout(45000)});
    const body=await response.text();assert.equal(response.status,200,body+'\n'+log);
    assert.ok(body.includes('CONSOLIDATED_VERIFIED'),body);assert.ok(!body.includes('Waiting for the review.'),body);assert.ok(!body.includes('CONSOLIDATED_REVIEW'),body);
    let events=[];
    for(let i=0;i<250;i++) {
      events=existsSync(eventsFile)?readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse):[];
      if(events.some(e=>e.hook==='end'&&e.lastText==='CONSOLIDATED_VERIFIED'))break;
      await delay(100);
    }
    assert.ok(events.some(e=>e.hook==='end'&&e.lastText==='CONSOLIDATED_VERIFIED'),JSON.stringify({body,events,requests,log}));
    const original=events.find(e=>e.hook==='prompt'&&!e.sessionKey?.includes(':subagent:'));
    const verifiedRun=events.find(e=>e.hook==='finalize'&&e.text==='CONSOLIDATED_REVIEW')?.runId;
    const announcement=events.find(e=>e.hook==='prompt'&&e.runId===verifiedRun&&e.provenance?.sourceTool==='subagent_announce');
    assert.ok(announcement,JSON.stringify(events));
    assert.equal(announcement.sessionId,original.sessionId);
    const scoped=events.filter(e=>e.runId===announcement.runId);
    const finals=scoped.filter(e=>e.hook==='finalize');
    assert.deepEqual(finals.map(e=>[e.text,e.revise]),[['CONSOLIDATED_REVIEW',true],['CONSOLIDATED_VERIFIED',false]]);
    assert.equal(scoped.filter(e=>e.hook==='end').length,1);
    assert.equal(scoped.at(-1).hook,'end');assert.equal(scoped.at(-1).success,true);
    assert.equal(scoped.at(-1).lastText,'CONSOLIDATED_VERIFIED');
    assert.ok(!events.some(e=>e.runId===original.runId&&e.hook==='finalize'),'yield must not produce final-answer proof');
    const originalEvents=events.filter(e=>e.runId===original.runId);
    const yieldIndex=originalEvents.findIndex(e=>e.hook==='tool'&&e.toolName==='sessions_yield');
    assert.ok(yieldIndex>=0,JSON.stringify(originalEvents));
    assert.equal(originalEvents[yieldIndex].result.details.status,'yielded');
    assert.ok(yieldIndex<originalEvents.findIndex(e=>e.hook==='end'));
    assert.ok(finals.every(e=>e.provenance===undefined),'lifecycle helper sparsifies provenance; use earlier bound prompt identity');
    assert.equal(events.filter(e=>e.hook==='spawned').length,2);
    if(interim!=='final') {
      const firstEnd=events.findIndex(e=>e.hook==='end'&&e.runId?.startsWith('announce:'));
      const childEnds=events.map((e,i)=>[e,i]).filter(([e])=>e.hook==='end'&&e.sessionKey?.includes(':subagent:'));
      assert.equal(childEnds.length,2);assert.ok(firstEnd<childEnds[1][1],'interim parent completion precedes second child completion');
      assert.equal(events[firstEnd].lastText,interim==='silent'?'NO_REPLY':'One review arrived; waiting for the second.');
      assert.ok(!body.includes('One review arrived; waiting for the second.'));
    }
    assert.equal(events.filter(e=>e.hook==='end'&&e.sessionKey?.includes(':subagent:')&&e.lastText==='CHILD_VERIFIED').length,2);
    const innerSpawns=originalEvents.filter(e=>e.hook==='tool'&&e.toolName==='sessions_spawn');
    assert.equal(innerSpawns.length,2);
    for(const spawned of innerSpawns) {
      assert.match(spawned.toolCallId,/^tool_search_code:spawn-fixture-[01]:sessions_spawn:/);
      assert.ok(originalEvents.some(e=>e.hook==='before-tool'&&e.toolCallId===spawned.toolCallId));
      assert.ok(events.some(e=>e.hook==='spawned'&&e.event.runId===spawned.result.details.runId&&e.event.childSessionKey===spawned.result.details.childSessionKey));
      assert.ok(events.some(e=>e.hook==='prompt'&&e.provenance?.sourceSessionKey===spawned.result.details.childSessionKey));
    }
    assert.match(originalEvents[yieldIndex].toolCallId,/^tool_search_code:yield-fixture:sessions_yield:/);
    const chunks=body.split('\n').filter(line=>line.startsWith('data: ')&&line!=='data: [DONE]').map(line=>JSON.parse(line.slice(6)));
    assert.equal(chunks.flatMap(chunk=>chunk.choices??[]).map(choice=>choice.delta?.content??'').join(''),'CONSOLIDATED_VERIFIED');
    assert.ok(chunks.filter(chunk=>chunk.choices).every(chunk=>chunk.id===original.runId),'public completion retains the original owner run ID');
    assert.equal((body.match(/data: \[DONE\]/g)??[]).length,1);
    assert.ok(events.some(e=>e.hook==='delivery'&&e.result.status==='waiting'));
    assert.ok(events.some(e=>e.hook==='delivery'&&e.result.status==='ready'));
    for(const [user,prompt,expected] of [['greeting-fixture','HELLO_FIXTURE: say hello.','HELLO_VERIFIED'],['error-fixture','FAIL_FIXTURE: controlled provider failure.',null]]) {
      const reply=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user,messages:[{role:'user',content:prompt}]}),signal:AbortSignal.timeout(30000)});
      const visible=await reply.text();
      for(let i=0;i<100;i++) {
        events=readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
        if(events.some(e=>e.hook==='end'&&e.sessionKey===`agent:pixel:openai-user:ods-${sha(user)}`))break;
        await delay(50);
      }
      const relevant=events.filter(e=>e.sessionKey===`agent:pixel:openai-user:ods-${sha(user)}`);
      const promptEvent=relevant.find(e=>e.hook==='prompt');
      assert.ok(promptEvent?.runId && promptEvent.sessionId && promptEvent.sessionKey,JSON.stringify(relevant));
      const terminal=relevant.filter(e=>e.hook==='end').at(-1);
      assert.ok(terminal,JSON.stringify(relevant));
      assert.equal(terminal.success,true,'native success means no thrown prompt error, not successful provider output');
      if(expected) {
        assert.equal(relevant.find(e=>e.hook==='finalize')?.text,expected);
        assert.equal(terminal.lastText,expected);
        assert.ok(visible.includes(expected),visible);
        assert.ok(events.some(e=>e.hook==='delivery'&&e.runId===promptEvent.runId&&e.result.status==='not-delegated'));
      } else {
        assert.equal(terminal.lastStopReason,'error');
        assert.ok(!relevant.some(e=>e.hook==='finalize'),'provider failure must not create a final-answer candidate');
      }
    }
    if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.'+interim,JSON.stringify({requests,body,events},null,2));
  } finally {
    if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.'+interim+'.debug',JSON.stringify({log,events:existsSync(eventsFile)?readFileSync(eventsFile,'utf8'):''},null,2));
    if(child&&child.exitCode===null){const done=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([done,delay(3000)]);if(child.exitCode===null)process.kill(-child.pid,'SIGKILL');}
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
    rmSync(root,{recursive:true,force:true});
  }
});
