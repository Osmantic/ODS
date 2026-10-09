// Actual pinned gateway, native child/announce and offline model. Only temporary state.
// cancel qualifies an existing chat: real warmup, history ledger/context, two
// active children, Stop, immediate owner followup, then both late announcements.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {cpSync,mkdtempSync,mkdirSync,writeFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {setTimeout as delay} from 'node:timers/promises';
const installed=process.env.OPENCLAW_PACKAGE_DIR;
const sha=value=>createHash('sha256').update(value).digest('hex');

for(const interim of ['final','silent','waiting','extra-yield','cancel','daily-reset','reset'])
test(`real gateway deferred delegation waits for two children and a verified revised terminal answer: interim=${interim}`,
  {skip:!installed || process.platform==='win32',timeout:120000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-delegation-hooks-'));
  const pkg=join(root,'package'), workspace=join(root,'workspace'), plugin=join(root,'plugin');
  let child, ingress, log='', revision=false, requests=0, askedFinalYield=false;
  const heldChildren=[];
  let firstChildResponse;
  const prior=[{role:'user',content:'WARMUP_FIXTURE'},{role:'assistant',content:'READY'}];
  const childTexts=['CHILD_VERIFIED_0: ORIGINAL_CART_EVIDENCE_914','CHILD_VERIFIED_1: ORIGINAL_ACCESSIBILITY_EVIDENCE_731'];
  const clockFile=join(root,'fixture-clock.json'),clockLoader=join(root,'fixture-clock.mjs');
  if(interim==='daily-reset'){
    // Cross the real default 04:00 UTC reset boundary only after the parent
    // has yielded. Timers remain real; only this disposable gateway's wall
    // clock changes, by two seconds, when the first child finishes.
    writeFileSync(clockFile,JSON.stringify({now:Date.parse('2026-10-09T03:59:59Z')}));
    writeFileSync(clockLoader,`import {readFileSync} from 'node:fs';
      const NativeDate=Date;
      globalThis.Date=class extends NativeDate {
        constructor(...args){super(...args.length?args:[Date.now()]);}
        static now(){const c=JSON.parse(readFileSync(${JSON.stringify(clockFile)},'utf8'));
          return c.now+(c.started===undefined?0:NativeDate.now()-c.started);}
      };`);
  }
  const consolidatedRequests=[],providerTrace=[];
  cpSync(installed,pkg,{recursive:true});
  assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'))).version,'2026.6.33');
  for(const [name,module] of [['subagent-admission','subagent-announce-origin-XoBlouka.js'],['subagent-session','agent-Dme3RrGI.js'],['hook-provenance','hook-agent-context-ugCMMoT5.js'],['run-id-redaction','redact-cvFSPoXf.js'],
    ['context-usage','attempt-execution-DnVHak5f.js'],['compaction-budget','selection-BEwSQKM-.js'],['yield-usage','embedded-agent-CJx-nG3W.js'],['compaction-empty','proxy-Bsfwfsp-.js']]) {
    const recipe=JSON.parse(readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
    const target=join(pkg,'dist',module);let text=readFileSync(target,'utf8');
    if(sha(text)!==recipe.patchedSha256) {
      const predecessor=recipe.previousReplacements?.[sha(text)];
      if(predecessor)for(const [before,after] of [...predecessor].reverse()){assert.equal(text.split(after).length,2);text=text.replace(after,before);}
      assert.equal(sha(text),recipe.sourceSha256);
      for(const [before,after] of recipe.replacements){assert.equal(text.split(before).length,2);text=text.replace(before,after);}
      assert.equal(sha(text),recipe.patchedSha256);writeFileSync(target,text);
    }
  }
  // Reproduce the CI UUID/credential collision deterministically in the private
  // package only; native spawn, tool sanitization and admission remain real.
  const collisionRun='9c98b56b-cd0c-43b5-91fc-4591a1943618';
  const spawnModule=join(pkg,'dist','openclaw-tools-iHHy99PD.js');
  let spawnSource=readFileSync(spawnModule,'utf8');
  const randomChild='const childIdem = crypto.randomUUID();';
  assert.equal(spawnSource.split(randomChild).length,2);
  spawnSource=spawnSource.replace(randomChild,`const childIdem = label === "fixture-review-0" ? "${collisionRun}" : crypto.randomUUID();`);
  writeFileSync(spawnModule,spawnSource);
  if(interim==='final') {
    const redactor=await import(pathToFileURL(join(pkg,'dist','redact-cvFSPoXf.js')).href);
    const redact=value=>redactor.d(value,{}), field=(key,value)=>redactor.o(key,value,{});
    assert.equal(redact(collisionRun),collisionRun);
    assert.equal(field('runId',collisionRun),collisionRun);
    assert.equal(field('childSessionKey','agent:pixel:subagent:'+collisionRun),'agent:pixel:subagent:'+collisionRun);
    assert.equal(redact(JSON.stringify({runId:collisionRun})),JSON.stringify({runId:collisionRun}));
    for(const secret of ['fc-4591a1943618','fc-abcdef0123456789abcdef','Bearer '+collisionRun,
      'x'+collisionRun,collisionRun+'x',collisionRun+'-extra',collisionRun.replace('9c98b56b','zz98b56b')])
      assert.notEqual(redact(secret),secret,`credential or malformed UUID must remain masked: ${secret}`);
    const tail=' '.repeat(24000);
    for(const value of [' '.repeat(16383)+'x'+collisionRun+tail,' '.repeat(16348)+collisionRun+'_'+tail]) {
      assert.notEqual(redact(value),value,'chunk edges cannot hide identifier boundaries');
      assert.notEqual(redactor.c(value,{mode:'tools',patterns:[/(fc-[A-Za-z0-9]{10,})/g]}),value,'custom RegExp sees the same full boundary');
    }
    for(const offset of [16349,16363,16364,16383]) {
      const value=' '.repeat(offset)+collisionRun+tail;
      assert.equal(redact(value),value,'canonical UUID crossing a chunk boundary stays intact');
    }
    assert.notEqual(field('apiKey',collisionRun),collisionRun,'sensitive field intent still masks UUID-shaped values');
    assert.notEqual(redactor.c(collisionRun,{mode:'tools',patterns:[collisionRun]}),collisionRun,'custom owner redaction still applies');
  }
  // Force late native announcements to arrive while the next owner response
  // is streaming. Without this barrier fast Linux runs can miss the Mac race.
  const followupActiveFile=join(root,'followup-active');
  const announcementEnteredFile=join(root,'announcement-entered');
  if(['cancel','reset'].includes(interim)) {
    const announcePath=join(pkg,'dist','subagent-announce-origin-XoBlouka.js');
    const signature='async function deliverSubagentAnnouncement(params) {';
    const source=readFileSync(announcePath,'utf8');
    assert.equal(source.split(signature).length,2);
    const gate=`${signature}
      const fixtureFs=await import('node:fs');
      ${interim==='reset'?`fixtureFs.writeFileSync(${JSON.stringify(announcementEnteredFile)},'entered');`:''}
      for(let attempt=0;attempt<${interim==='reset'?2000:400}&&!fixtureFs.existsSync(${JSON.stringify(followupActiveFile)});attempt++)
        await new Promise(resolve=>setTimeout(resolve,10));
      if(!fixtureFs.existsSync(${JSON.stringify(followupActiveFile)}))throw new Error('owner followup did not start');
    `;
    writeFileSync(announcePath,source.replace(signature,gate));
  }
  mkdirSync(workspace);mkdirSync(plugin);
  const eventsFile=join(root,'hooks.jsonl');
  cpSync(new URL('../plugin/',import.meta.url),plugin,{recursive:true});
  if(process.env.ODS_DELIVERY_FIXTURE_MODULE)cpSync(process.env.ODS_DELIVERY_FIXTURE_MODULE,join(plugin,'subagent-delivery.mjs'));
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'delivery-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'delivery-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {appendFileSync} from 'node:fs';
    import {subagentDeliveryFor} from './subagent-delivery.mjs';
    import {createToolLoopGuard,createRunAbortAdapter} from './tool-loop-guard.mjs';
    import {createContextCompaction} from './context-compaction.mjs';
    import {createHistoryHydrator} from './history-context.mjs';
    import {withSessionTranscriptWriteLock,appendAssistantMirrorMessageByIdentity} from ${JSON.stringify(pathToFileURL(join(pkg,'dist/plugin-sdk/session-transcript-runtime.js')).href)};
    import {abortAgentHarnessRun,abortAndDrainAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from ${JSON.stringify(pathToFileURL(join(pkg,'dist/plugin-sdk/agent-harness-runtime.js')).href)};
    import {getSessionEntry,patchSessionEntry,resolveStorePath} from ${JSON.stringify(pathToFileURL(join(pkg,'dist/plugin-sdk/session-store-runtime.js')).href)};
    import {extractAssistantVisibleText} from ${JSON.stringify(pathToFileURL(join(pkg,'dist/plugin-sdk/agent-runtime.js')).href)};
    let revised=false,contextCompaction;const sharedGuard=createToolLoopGuard({
      abortRun:createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun}),
      abortRunAndDrain:(sessionId,sessionKey)=>abortAndDrainAgentHarnessRun({
        sessionId:resolveActiveEmbeddedRunSessionId(sessionKey)||sessionId,sessionKey,settleMs:4000,forceClear:false,reason:'ods_client_disconnect'}),
      execControl:{signal:()=>true}});
    const record=(hook,event,ctx,extra={})=>appendFileSync(${JSON.stringify(eventsFile)},JSON.stringify({
      hook,agentId:ctx.agentId,runId:ctx.runId,sessionId:ctx.sessionId,sessionKey:ctx.sessionKey,trigger:ctx.trigger,
      provenance:ctx.inputProvenance,success:event.success,text:event.lastAssistantMessage,
      messageRoles:event.messages?.map(m=>m.role),
      lastStopReason:[...(event.messages??[])].reverse().find(m=>m.role==='assistant')?.stopReason,
      lastText:[...(event.messages??[])].reverse().find(m=>m.role==='assistant')?.content?.filter(c=>c.type==='text').map(c=>c.text).join(''),...extra})+'\\n');
    export default {id:'delivery-fixture',register(api){
      contextCompaction??=createContextCompaction({readConfig:()=>api.config,
        readSession:scope=>getSessionEntry({...scope,storePath:resolveStorePath(api.config?.session?.store,{agentId:'pixel'})}),
        callGateway:async()=>{throw new Error('compaction not requested by this fixture');},
        admission:{status:()=>({available:true,phase:'idle'}),owns:()=>false},
        activeSession:key=>Boolean(resolveActiveEmbeddedRunSessionId(key))});
      api.registerHttpRoute({path:'/pixel-ods/context',auth:'gateway',match:'exact',handler:async(req,res)=>{
        const parts=[];for await(const p of req)parts.push(p);const {user}=JSON.parse(Buffer.concat(parts));
        const result=contextCompaction.context(user);record('context',{}, {},{user,result});
        res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(result));return true;
      }});
      const hydrate=createHistoryHydrator({getSessionEntry,patchSessionEntry,resolveStorePath,withSessionTranscriptWriteLock,appendAssistantMirrorMessageByIdentity,readConfig:()=>api.config});
      api.registerHttpRoute({path:'/pixel-ods/history',auth:'gateway',match:'exact',handler:async(req,res)=>{
        const parts=[];for await(const p of req)parts.push(p);const body=JSON.parse(Buffer.concat(parts));
        const result=await hydrate(body);record('history',{}, {},{result});
        res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(result));return true;
      }});
      const registry=subagentDeliveryFor(sharedGuard,{finalText:extractAssistantVisibleText,
        abortSession:async sessionKey=>{
          const sessionId=resolveActiveEmbeddedRunSessionId(sessionKey);if(!sessionId)return true;
          await abortAndDrainAgentHarnessRun({sessionId,sessionKey,settleMs:4000,forceClear:false,reason:'ods_client_disconnect'});
          return !resolveActiveEmbeddedRunSessionId(sessionKey);
        },
        resolveOwnerSession:sessionKey=>getSessionEntry({sessionKey,storePath:resolveStorePath(api.config?.session?.store,{agentId:'pixel'})})});
      api.on('before_prompt_build',(event,ctx)=>{
        if(registry.admission?.(ctx)){record('prompt-denied',event,ctx);return;}
        registry.observe(event,ctx);if(${JSON.stringify(interim)}==='cancel')sharedGuard.observeRun(ctx,'pixel');
        record('prompt',event,ctx);return {prependContext:registry.promptContext(ctx)};
      });
      api.on('before_agent_run',(event,ctx)=>{const decision=registry.admission?.(ctx);record('admission',{},ctx,{decision});return decision;});
      api.registerHttpRoute({path:'/pixel-ods/abort',auth:'gateway',match:'exact',handler:async(req,res)=>{
        const parts=[];for await(const p of req)parts.push(p);const {user}=JSON.parse(Buffer.concat(parts));
        const delegated=registry.cancel(user),parent=sharedGuard.abortUserRun(user);
        const [d,p]=await Promise.all([delegated,parent]);
        record('cancel',{}, {},{result:d,parent:p});
        res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({aborted:d.tracked?d.aborted:p}));return true;
      }});
      api.on('before_tool_call',(event,ctx)=>{const decision=registry.blocked(ctx,event);registry.before(event,ctx,decision);record('before-tool',event,ctx,{toolName:event.toolName,toolCallId:ctx.toolCallId,params:event.params,decision});return decision;});
      api.on('subagent_spawned',(event,ctx)=>{const result=registry.nativeSpawn(event,ctx);record('spawned',event,ctx,{event,ctx});return result;});
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
    const currentUser=JSON.stringify(body.messages.filter(m=>m.role==='user').at(-1)?.content);
    const userMessages=body.messages.filter(m=>m.role==='user').map(m=>typeof m.content==='string'?m.content:JSON.stringify(m.content)).join('\n');
    const trace={request:requests,lastRole:body.messages.at(-1)?.role,childTask:userMessages.includes('CHILD_FIXTURE_TASK'),announcement:userMessages.includes('Internal task completion event'),revision,markers:childTexts.map(value=>all.includes(value))};
    providerTrace.push(trace);if(providerTrace.length>40)providerTrace.shift();
    let delta,finish='stop';
    const deferred=(id,name,args)=>({index:0,id,type:'function',function:{name:'tool_call',arguments:JSON.stringify({id:name,args})}});
    if(userMessages.includes('FAIL_FIXTURE')) {
      trace.branch='provider-error';res.writeHead(400,{'Content-Type':'application/json'});
      res.end(JSON.stringify({error:{message:'offline fixture provider refusal',type:'invalid_request_error'}}));return;
    } else if(currentUser.includes('WARMUP_FIXTURE')&&!currentUser.includes('Delegate two')&&!userMessages.includes('RECOVER_AFTER_STOP')&&!userMessages.includes('CHILD_FIXTURE_TASK')) {
      trace.branch='warmup';delta={role:'assistant',content:'READY'};
    } else if(userMessages.includes('RECOVER_AFTER_STOP')||userMessages.includes('RESET_FOLLOWUP_FIXTURE')) {
      trace.branch='recovery';delta={role:'assistant',content:'19'};
      if(['cancel','reset'].includes(interim))writeFileSync(followupActiveFile,'active');
    } else if(userMessages.includes('HELLO_FIXTURE')) {
      trace.branch='greeting';delta={role:'assistant',content:'HELLO_VERIFIED'};
    } else if(userMessages.includes('CHILD_FIXTURE_TASK')&&!userMessages.includes('Internal task completion event')) {
      trace.branch='child';
      if(['cancel','reset'].includes(interim)){
        if(userMessages.includes('CHILD_FIXTURE_TASK 0'))firstChildResponse=res;
        heldChildren.push(res);res.writeHead(200,{'Content-Type':'text/event-stream'});
        res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'CHILD_REVIEW_IN_PROGRESS'},finish_reason:null}]})+'\n\n');return;
      }
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
      } else {
        await delay(300);
        if(interim==='daily-reset'){
          let yielded=false;
          for(let i=0;i<250;i++){
            const events=readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
            yielded=events.some(e=>e.hook==='tool'&&e.toolName==='sessions_yield')
              && events.some(e=>e.hook==='end'&&e.runId?.startsWith('chatcmpl_'));
            if(yielded)break;await delay(20);
          }
          assert.ok(yielded,'cross the reset boundary only after the owner has yielded');
          writeFileSync(clockFile,JSON.stringify({now:Date.parse('2026-10-09T04:00:01Z'),started:Date.now()}));
        }
      }
      delta={role:'assistant',content:childTexts[userMessages.includes('CHILD_FIXTURE_TASK 1')?1:0]};
    } else if(userMessages.includes('Internal task completion event') || userMessages.includes('CONSOLIDATED_REVIEW') || revision) {
      trace.branch='parent-announcement';
      const observed=readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
      const finished=observed.filter(e=>e.hook==='end'&&e.sessionKey?.includes(':subagent:')&&childTexts.includes(e.lastText)).length;
      const announced=new Set(observed.filter(e=>e.hook==='prompt'&&e.provenance?.sourceTool==='subagent_announce').map(e=>e.provenance.sourceSessionKey)).size;
      if(finished<2||announced<2)delta={role:'assistant',content:interim==='silent'?'NO_REPLY':'One review arrived; waiting for the second.'};
      else if(interim==='extra-yield'&&!askedFinalYield) {
        askedFinalYield=true;delta={role:'assistant',tool_calls:[deferred('unneeded-yield','sessions_yield',{})]};finish='tool_calls';
      } else {
        const evidence=childTexts.map(text=>all.includes(text));consolidatedRequests.push(evidence);
        delta={role:'assistant',content:evidence.every(Boolean)?(revision||interim==='extra-yield'?'CONSOLIDATED_VERIFIED':'CONSOLIDATED_REVIEW'):'MISSING_SIBLING_EVIDENCE'};revision=true;
      }
    } else if(all.includes('childSessionKey')) {
      delta={role:'assistant',tool_calls:[deferred('yield-fixture','sessions_yield',{})]};finish='tool_calls';
    } else {
      delta={role:'assistant',content:'Waiting for the review.',tool_calls:[0,1].map((i)=>({...deferred('spawn-fixture-'+i,'sessions_spawn',{task:'CHILD_FIXTURE_TASK '+i+': return CHILD_VERIFIED, no tools.',runtime:'subagent',mode:'run',label:'fixture-review-'+i}),index:i}))};finish='tool_calls';
    }
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    if(interim==='cancel'&&trace.branch==='recovery'&&!trace.announcement)await delay(1200);
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
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1',
        ...interim==='daily-reset'?{TZ:'UTC',NODE_OPTIONS:'--import='+pathToFileURL(clockLoader).href}:{}},stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let i=0;i<250;i++){try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);}
    assert.ok(ready,log);
    ingress=createIngressServer({token:'fixture-only-0123456789abcdef',gatewayPort:port,...interim==='cancel'?{historyLedger:createChatHistoryLedger(join(root,'history-ledger'))}:{}});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const ingressPort=ingress.address().port;
    if(interim==='cancel') {
      const warmup=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'delegation-fixture',messages:[prior[0]],request_id:'cancel-warmup',history_snapshot:{schemaVersion:1,messages:[prior[0]]}}),signal:AbortSignal.timeout(30000)});
      assert.ok((await warmup.text()).includes('READY'));
    }
    const responsePromise=fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'delegation-fixture',messages:[{role:'user',content:'Delegate two read-only reviews, yield, and consolidate the result.'}],...interim==='cancel'?{request_id:'cancel-original',history_snapshot:{schemaVersion:1,messages:[...prior,{role:'user',content:'Delegate two read-only reviews, yield, and consolidate the result.'}]}}:{}}),signal:AbortSignal.timeout(45000)});
    if(interim==='reset'){
      const initialBody=responsePromise.then(response=>response.text()).catch(error=>{assert.ok(['terminated','AbortError'].includes(error.message)||error.name==='AbortError');return '';});
      const readEvents=()=>existsSync(eventsFile)?readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse):[];
      let events=[];
      for(let i=0;i<250;i++){
        events=readEvents();
        if(heldChildren.length===2&&events.some(e=>e.hook==='tool'&&e.toolName==='sessions_yield'))break;
        await delay(50);
      }
      assert.equal(heldChildren.length,2);
      assert.ok(events.some(e=>e.hook==='tool'&&e.toolName==='sessions_yield'));
      const original=events.find(e=>e.hook==='prompt'&&e.runId?.startsWith('chatcmpl_'));
      const completedChild=events.find(e=>e.hook==='spawned'&&e.event.label==='fixture-review-0');
      assert.ok(firstChildResponse&&completedChild);
      firstChildResponse.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{content:childTexts[0]},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
      for(let i=0;i<250&&!existsSync(announcementEnteredFile);i++)await delay(40);
      assert.ok(existsSync(announcementEnteredFile),'one real completed child announcement is queued before the owner reset');
      // The real explicit owner reset, using only this fixture's URL/token/state.
      const result=await promisify(execFile)(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','call','sessions.reset',
        '--url',`ws://127.0.0.1:${port}`,'--token','fixture-only-0123456789abcdef','--json',
        '--params',JSON.stringify({key:original.sessionKey,reason:'reset'})],
        {cwd:root,timeout:20000,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,
          OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'}});
      const reset=JSON.parse(result.stdout);assert.equal(reset.ok,true);
      assert.notEqual(reset.entry.sessionId,original.sessionId);
      const followup=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',
        headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},
        body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'delegation-fixture',messages:[{role:'user',content:'RESET_FOLLOWUP_FIXTURE: answer only 17 + 2.'}]}),signal:AbortSignal.timeout(30000)});
      const visible=await followup.text();assert.equal(followup.status,200,visible);
      const chunks=visible.split('\n').filter(x=>x.startsWith('data: ')&&x!=='data: [DONE]').map(x=>JSON.parse(x.slice(6)));
      assert.equal(chunks.flatMap(c=>c.choices??[]).map(c=>c.delta?.content??'').join(''),'19',visible);
      for(let i=0;i<250;i++){
        events=readEvents();
        if(events.some(e=>e.hook==='admission'&&e.provenance?.sourceSessionKey===completedChild.event.childSessionKey&&e.decision?.outcome==='block'))break;
        await delay(40);
      }
      assert.ok(events.some(e=>e.hook==='admission'&&e.provenance?.sourceSessionKey===completedChild.event.childSessionKey&&e.decision?.outcome==='block'),
        'the real late completed child is denied after an explicit owner reset');
      assert.ok(events.some(e=>e.hook==='end'&&e.sessionKey?.includes(':subagent:')&&e.lastStopReason==='aborted'),
        'the still-running sibling is aborted by the native owner reset');
      assert.equal(providerTrace.filter(e=>e.announcement).length,0,'old children never reach the provider after an owner reset');
      assert.equal(events.filter(e=>e.hook==='prompt'&&e.runId?.startsWith('chatcmpl_')).at(-1).sessionId,reset.entry.sessionId);
      assert.ok(!(await initialBody).includes('CONSOLIDATED'));
      if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.reset',JSON.stringify({events,providerTrace,reset},null,2));
      return;
    }
    if(interim==='cancel') {
      const initialBody=responsePromise.then(response=>response.text()).catch(error=>{assert.ok(['terminated','AbortError'].includes(error.message)||error.name==='AbortError');return '';});
      const readEvents=()=>existsSync(eventsFile)?readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse):[];
      let events=[];
      for(let i=0;i<250;i++){
        events=readEvents();
        if(heldChildren.length===2&&events.some(e=>e.hook==='tool'&&e.toolName==='sessions_yield'))break;
        await delay(50);
      }
      assert.equal(heldChildren.length,2,'both actual child providers must be active before Stop');
      assert.ok(events.some(e=>e.hook==='tool'&&e.toolName==='sessions_yield'));
      assert.ok(events.some(e=>e.hook==='tool'&&e.toolName==='sessions_spawn'&&e.result.details.runId===collisionRun),'known UUID survives native tool sanitization before Stop');

      const original=events.filter(e=>e.hook==='prompt'&&!e.sessionKey?.includes(':subagent:')).at(-1);
      const stop=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/cancel`,{method:'POST',headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},body:JSON.stringify({user:'delegation-fixture'}),signal:AbortSignal.timeout(15000)});
      const stopped=await stop.json().catch(error=>{throw new Error("stop response: "+error.message)});assert.equal(stopped.aborted,true,JSON.stringify(stopped));
      const followup=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'delegation-fixture',messages:[{role:'user',content:'RECOVER_AFTER_STOP: do not continue the review or use tools. Answer only 17 + 2.'}],request_id:'cancel-followup',history_snapshot:{schemaVersion:1,messages:[...prior,{role:'user',content:'Delegate two read-only reviews, yield, and consolidate the result.'},{role:'user',content:'RECOVER_AFTER_STOP: do not continue the review or use tools. Answer only 17 + 2.'}]}}),signal:AbortSignal.timeout(30000)});
      const visible=await followup.text().catch(error=>{throw new Error("followup response: "+error.message)});
      assert.equal(followup.status,200,visible);
      const chunks=visible.split('\n').filter(x=>x.startsWith('data: ')&&x!=='data: [DONE]').map(x=>JSON.parse(x.slice(6)));
      assert.equal(chunks.flatMap(c=>c.choices??[]).map(c=>c.delta?.content??'').join(''),'19',visible);
      assert.equal(providerTrace.filter(e=>e.branch==='recovery').length,1,'same-chat owner prompt reaches the actual provider once');
      for(let i=0;i<200;i++){
        events=readEvents();
        if(new Set(events.filter(e=>e.hook==='admission'&&e.provenance?.sourceTool==='subagent_announce'&&e.decision?.outcome==='block'&&events.some(end=>end.hook==='end'&&end.runId===e.runId)).map(e=>e.provenance.sourceSessionKey)).size===2)break;
        await delay(50);
      }
      const blockedChildren=new Set(events.filter(e=>e.hook==='admission'&&e.provenance?.sourceTool==='subagent_announce'&&e.decision?.outcome==='block'&&events.some(end=>end.hook==='end'&&end.runId===e.runId)).map(e=>e.provenance.sourceSessionKey));
      assert.equal(blockedChildren.size,2,JSON.stringify({events,log}));
      for(const key of blockedChildren)assert.ok(events.some(e=>e.hook==='end'&&e.sessionKey===key&&e.lastStopReason==='aborted'&&e.success===false),'both native child runs confirm abortion');
      assert.equal(providerTrace.filter(e=>e.announcement).length,0,'after both late announcements, no announcement reached the model');
      assert.equal(providerTrace.filter(e=>e.branch==='recovery').length,1,'after draining both late announcements, only the actual owner followup generated');
      assert.equal(requests,6,'warmup + owner spawn/yield + two children + owner followup only');
      assert.equal(providerTrace.filter(e=>e.branch==='parent-announcement').length,0,'cancelled native announcements never reach the provider');
      assert.ok(!(await initialBody).includes('CONSOLIDATED'));
      events=readEvents();
      const transcript=readFileSync(join(root,'state','agents','pixel','sessions',original.sessionId+'.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
      const redacted=transcript.filter(e=>e.message?.idempotencyKey?.startsWith('hook-block:before_agent_run:'));
      if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.cancel-transcript',JSON.stringify(transcript,null,2));
      assert.ok(transcript.filter(e=>e.message?.role==='user').every(e=>!JSON.stringify(e).includes('CHILD_REVIEW_IN_PROGRESS')),'cancelled child output is never promoted to an owner message');
      assert.ok(redacted.every(e=>!JSON.stringify(e).includes('CHILD_VERIFIED')));
      assert.ok(!events.some(e=>e.hook==='tool'&&e.runId?.startsWith('announce:')),'cancelled announcements execute no tools');
      if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.cancel',JSON.stringify({events,providerTrace,visible,redacted},null,2));
      return;
    }
    const response=await responsePromise;
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
    const verifiedRun=events.find(e=>e.hook==='finalize'&&e.text===(interim==='extra-yield'?'CONSOLIDATED_VERIFIED':'CONSOLIDATED_REVIEW'))?.runId;
    const announcement=events.find(e=>e.hook==='prompt'&&e.runId===verifiedRun&&e.provenance?.sourceTool==='subagent_announce');
    assert.ok(announcement,JSON.stringify(events));
    assert.equal(announcement.sessionId,original.sessionId);
    const scoped=events.filter(e=>e.runId===announcement.runId);
    const finals=scoped.filter(e=>e.hook==='finalize');
    assert.deepEqual(finals.map(e=>[e.text,e.revise]),interim==='extra-yield'?[['CONSOLIDATED_VERIFIED',false]]:[['CONSOLIDATED_REVIEW',true],['CONSOLIDATED_VERIFIED',false]]);
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
    assert.ok(consolidatedRequests.length>=(interim==='extra-yield'?1:2));assert.ok(consolidatedRequests.every(pair=>pair.every(Boolean)),'provider must really receive both distinct child report bodies');
    if(interim!=='final') {
      const firstEnd=events.findIndex(e=>e.hook==='end'&&e.runId?.startsWith('announce:'));
      const childEnds=events.map((e,i)=>[e,i]).filter(([e])=>e.hook==='end'&&e.sessionKey?.includes(':subagent:'));
      assert.equal(childEnds.length,2);assert.ok(firstEnd<childEnds[1][1],'interim parent completion precedes second child completion');
      assert.equal(events[firstEnd].lastText,interim==='silent'?'NO_REPLY':'One review arrived; waiting for the second.');
      assert.ok(!body.includes('One review arrived; waiting for the second.'));
    }
    assert.equal(events.filter(e=>e.hook==='end'&&e.sessionKey?.includes(':subagent:')&&childTexts.includes(e.lastText)).length,2);
    if(interim==='extra-yield')assert.ok(events.some(e=>e.hook==='before-tool'&&e.toolName==='sessions_yield'&&e.decision?.block),'native deferred yield is blocked after both results and the model can still finalize');
    const innerSpawns=originalEvents.filter(e=>e.hook==='tool'&&e.toolName==='sessions_spawn');
    assert.equal(innerSpawns.length,2);
    assert.ok(innerSpawns.some(e=>e.result.details.runId===collisionRun),'known UUID survives native spawn/tool/admission custody');
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
    const transcript=readFileSync(join(root,'state','agents','pixel','sessions',original.sessionId+'.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    const mirrors=transcript.filter(e=>e.message?.api==='cli').map(e=>e.message);
    assert.ok(mirrors.length>0);
    assert.ok(mirrors.every(message=>message.usage?.totalTokens===530),JSON.stringify(mirrors.map(message=>message.usage)));
    if(interim==='daily-reset'){
      const next=await fetch(`http://127.0.0.1:${ingressPort}/v1/chat/completions`,{method:'POST',
        headers:{Authorization:'Bearer fixture-only-0123456789abcdef','Content-Type':'application/json'},
        body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'delegation-fixture',messages:[{role:'user',content:'HELLO_FIXTURE: a new ordinary owner request after the reset boundary.'}]}),signal:AbortSignal.timeout(30000)});
      const visible=await next.text();assert.ok(visible.includes('HELLO_VERIFIED'),visible);
      events=readFileSync(eventsFile,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
      const newOwner=events.filter(e=>e.hook==='prompt'&&e.runId?.startsWith('chatcmpl_')&&e.sessionKey===original.sessionKey).at(-1);
      assert.notEqual(newOwner.sessionId,original.sessionId,'an ordinary new owner request retains the native daily-reset policy');
    }
    if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.'+interim,JSON.stringify({requests,body,events,providerTrace,consolidatedRequests},null,2));
  } finally {
    if(process.env.ODS_HOOK_EVIDENCE_PATH)writeFileSync(process.env.ODS_HOOK_EVIDENCE_PATH+'.'+interim+'.debug',JSON.stringify({log:log.slice(-131072),events:existsSync(eventsFile)?readFileSync(eventsFile,'utf8').slice(-524288):'',providerTrace,consolidatedRequests},null,2));
    if(child&&child.exitCode===null){const done=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([done,delay(3000)]);if(child.exitCode===null)process.kill(-child.pid,'SIGKILL');}
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
    rmSync(root,{recursive:true,force:true});
  }
});
