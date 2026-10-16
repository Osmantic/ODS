// Real pinned harness + real ingress, deterministic model, disposable state.
// Fleet event search (rounds 105-112): the first answer cited pages it never
// read, so completion assurance asked for one source revision. OpenClaw sends
// a revision as the next attempt's prompt, but that attempt's pre-prompt
// context check overflowed, and after its recovery OpenClaw retried with the
// owner's original message: the revision never reached the model. The model
// answered the same request again with the same unread citations, and the one
// source revision was spent.
//   'compaction'  earlier turns fill the chat; OpenClaw compacts them first;
//   'truncation'  OpenClaw truncates this run's tool results instead, without
//                 a model call.
// The model follows a revision request whenever it reaches it. Budgets are
// the ODS model contract for a 32768-token window with 4096 output tokens.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,readdirSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {RESUBMITTED_REVISION_CONTEXT} from '../plugin/tool-loop-guard.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
const PORTAL="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, " +
  'copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const OWNER='Today is 2026-09-26. Search the live web for at least three public events in Philadelphia happening within the next 45 days. ' +
  'Actually search and open sources. For each give event title, exact date, venue and a direct official source URL. ' +
  'Exclude undated listings and past events. Explain any unavailable result honestly. Do not create files.';
const REVISION_PREFIX='Before accepting the previous final answer';
const UNREAD_INSTRUCTION='These cited URLs were not read successfully in this response';
const UNREAD_URL='https://www.lincolnfinancialfield.com/events/ac-dc-power-up-tour-2026/';
const FIRST=size=>'Three public events in Philadelphia in the next 45 days:\n\n' +
  `1. AC/DC Power Up Tour 2026 - September 29, 2026 - Lincoln Financial Field - ${UNREAD_URL}\n` +
  '2. Journey - October 28, 2026 - Xfinity Mobile Arena - https://www.xfinitymobilearena.com/events/detail/journey-10-28-26\n' +
  '3. Philadelphia Eagles vs. Dallas Cowboys - October 26, 2026 - Lincoln Financial Field - https://www.lincolnfinancialfield.com/events/list/\n\n' +
  'Listings checked and excluded:\n' +
  Array.from({length:size},(_,n)=>`- Community calendar entry ${n+1}: no exact date on the listing, excluded as undated.`).join('\n');
// An earlier turn of the same chat, as the website and coding journeys were.
const EARLIER='Write the complete volunteer checklist for the neighborhood fair as plain text.';
const CHECKLIST=size=>'Volunteer checklist for the neighborhood fair:\n' +
  Array.from({length:size},(_,n)=>`- Task ${n+1}: confirm the table, the signs and the supplies for station ${n+1} before opening.`).join('\n');
const REVISED='I could not open any event page in this response, so none of these events is verified: ' +
  'AC/DC Power Up Tour 2026 (September 29, 2026, Lincoln Financial Field), Journey (October 28, 2026, Xfinity Mobile Arena) ' +
  'and Philadelphia Eagles vs. Dallas Cowboys (October 26, 2026, Lincoln Financial Field). Their source links are unverified.';

const text=content=>typeof content==='string'?content:Array.isArray(content)?content.map(part=>part?.text ?? '').join('\n'):'';
const sse=(res,deltas,finish)=>{
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for (const delta of deltas) res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
  res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n');
};

// Line counts that put the revision attempt, and only it, over the pre-prompt
// budget: the earlier checklist, this run's local reads, the first answer.
const SIZES={compaction:{earlier:430,reads:0,first:250},truncation:{earlier:0,reads:2,first:330}};

for (const route of ['compaction','truncation'])
test(`real harness keeps a finalize revision when the revision attempt overflows before the model call: ${route}`,{skip:!pkg,timeout:120000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-revision-retry-'));
  let log='',child,ingress;
  const requests=[],size=SIZES[route];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const request=JSON.parse(Buffer.concat(chunks).toString());
    // A compaction summary call carries no tools.
    if (!request.tools?.length) { requests.push({turn:'summary'}); sse(res,[{role:'assistant',content:'Summary of the earlier conversation.'}],'stop'); return; }
    const users=request.messages.filter(message=>message.role==='user').map(message=>text(message.content));
    const current=users.at(-1) ?? '', afterTool=request.messages.at(-1)?.role==='tool';
    const turn=current.includes(REVISION_PREFIX)?'revision':current.includes(OWNER)?'owner':current.includes(EARLIER)?'earlier':'other';
    const owners=requests.filter(request=>request.turn==='owner'&&!request.afterTool).length;
    requests.push({turn,afterTool,current});
    if (turn==='earlier') return sse(res,[{role:'assistant',content:CHECKLIST(size.earlier)}],'stop');
    if (turn==='other') return sse(res,[{role:'assistant',content:'Unexpected request.'}],'stop');
    // Whenever a revision request reaches the model, the model follows it.
    if (current.includes(UNREAD_INSTRUCTION)) return sse(res,[{role:'assistant',content:REVISED}],'stop');
    if (size.reads && owners===0 && !afterTool) return sse(res,[{role:'assistant',tool_calls:Array.from({length:size.reads},(_,n)=>({index:n,
      id:`read-${n}`,type:'function',function:{name:'read',arguments:JSON.stringify({path:`notes/calendar-export-${n}.txt`})}}))}],'tool_calls');
    // The same request again: the same answer, as the fleet model did.
    sse(res,[{role:'assistant',content:FIRST(size.first)}],'stop');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const workspace=join(root,'workspace');mkdirSync(join(workspace,'notes'),{recursive:true});
  // Large local exports: 'read' is a replay-safe core tool, so OpenClaw still
  // honors a revision after it, and together the reads pass the aggregate
  // tool-result budget, so the pre-prompt check can truncate them.
  for (let n=0;n<size.reads;n++) writeFileSync(join(workspace,'notes',`calendar-export-${n}.txt`),
    Array.from({length:400},(_,line)=>`listing ${line+1}: community calendar entry without an exact date`).join('\n'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'revision-retry-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'revision-retry-fixture',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  // The production wiring of index.js for these hooks.
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard} from './ods/tool-loop-guard.mjs';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({abortRun:()=>false});
    export default {id:'revision-retry-fixture',register(api){
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        const runId=JSON.parse(body).runId;await guard.settleDelivery(runId);
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(runId)));return true;
      }});
      api.on('before_prompt_build',(e,c)=>{guard.observeRun(c,'pixel',e);const context=guard.promptContextForRun(c.runId);
        record({prompt:c.runId,text:e.prompt,context:context??null});return context?{prependContext:context}:undefined;});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_agent_finalize',async(e,c)=>{await guard.verifyCitedPages(e,c);const d=guard.beforeAgentFinalize(e,c);
        record({finalize:c.runId,text:e.lastAssistantMessage,decision:d?.action??null,reason:d?.reason??null});return d;});
      api.on('agent_end',(e,c)=>{guard.endPreviewRevalidation(e,c);guard.observeAgentEnd(e,c);});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'},
      compaction:{reserveTokens:9831,reserveTokensFloor:0,keepRecentTokens:2048}},
      list:[{id:'pixel',default:true,workspace,contextLimits:{toolResultMaxChars:8192}}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['read']},plugins:{allow:['revision-retry-fixture'],load:{paths:[plugin]},
      entries:{'revision-retry-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
    const chat=content=>fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'revision-retry-fixture',messages:[{role:'user',content:content+PORTAL}]}),
      signal:AbortSignal.timeout(90000)}).then(async response=>({status:response.status,body:await response.text()}));
    if (size.earlier) assert.equal((await chat(EARLIER)).status,200,log);
    const {status,body}=await chat(OWNER);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({requests:requests.map(({turn,afterTool,current})=>({turn,afterTool,current:current?.slice(0,300)})),
      events:events.map(event=>({...event,text:event.text?.slice(0,160)})),delivered:delivered.slice(0,600),
      outcome:frames.at(-1)?.pixel_outcome})+'\n'+log.split('\n').filter(line=>/precheck|compaction|before_agent_finalize|revision/i.test(line)).join('\n');
    assert.equal(status,200,trace);

    // The fleet sequence: one answer with an unread citation, a source
    // revision, an attempt that stops at the pre-prompt context check, and a
    // retry with the owner's own message instead of the revision.
    const run=events.find(event=>event.prompt&&event.text?.startsWith(OWNER))?.prompt;
    const finals=events.filter(event=>event.finalize===run);
    assert.equal(finals[0]?.decision,'revise',trace);
    assert.match(finals[0].reason,/Cited pages lack current-turn read receipts/,trace);
    const attempts=events.filter(event=>event.prompt===run);
    const revisionAttempt=attempts.findIndex(event=>event.text?.startsWith(REVISION_PREFIX));
    assert.ok(revisionAttempt>0,'OpenClaw prepared the revision attempt\n'+trace);
    assert.ok(attempts[revisionAttempt+1]?.text?.startsWith(OWNER),'the next attempt resends the owner message\n'+trace);
    assert.ok(requests.every(request=>request.turn!=='revision'),'the revision prompt never reached the model\n'+trace);
    assert.match(log,/context-overflow-precheck/,trace);
    if (route==='compaction') assert.ok(requests.some(request=>request.turn==='summary'),'OpenClaw compacted before the retry\n'+trace);
    else assert.match(log,/early tool-result truncation succeeded/,trace);

    // The retried owner message carries the revision, ahead of the owner text.
    const retried=requests.filter(request=>request.turn==='owner'&&!request.afterTool).at(-1);
    assert.ok(retried.current.indexOf(RESUBMITTED_REVISION_CONTEXT)>=0,'the retry carries the revision\n'+trace);
    assert.ok(retried.current.indexOf(UNREAD_INSTRUCTION)<retried.current.indexOf(OWNER),trace);
    assert.ok(retried.current.indexOf(`"${UNREAD_URL}"`)>retried.current.indexOf(UNREAD_INSTRUCTION),'with the unread citations it named\n'+trace);
    assert.equal(attempts[revisionAttempt+1].context?.startsWith(RESUBMITTED_REVISION_CONTEXT),true,trace);
    // Only that retry: the first attempt and the revision attempt itself carry none.
    assert.deepEqual(attempts.slice(0,revisionAttempt+1).map(event=>event.context),attempts.slice(0,revisionAttempt+1).map(()=>null),trace);
    assert.equal(delivered,REVISED,trace);
    assert.deepEqual(finals.map(event=>event.decision),['revise',null],trace);
    assert.notEqual(frames.at(-1)?.pixel_outcome?.status,'failed',trace);

    // Model-only context: the persisted owner message is the owner's text.
    const sessions=join(root,'state','agents','pixel','sessions');
    const transcript=readdirSync(sessions).filter(name=>name.endsWith('.jsonl')&&!name.includes('trajectory'))
      .map(name=>readFileSync(join(sessions,name),'utf8')).join('\n');
    assert.ok(transcript.includes('Actually search and open sources.'),trace);
    assert.ok(!transcript.includes(RESUBMITTED_REVISION_CONTEXT.slice(0,60)),'the revision context is not persisted as owner text\n'+trace);
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
