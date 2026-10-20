// Real pinned harness + real ingress, deterministic model, disposable state.
// RTX Spark, 2026-10-16: the owner asked "Increase enemies by 10%", the model
// asked two questions with pixel_ods_ask_user, and the card's answer ran as
// the first message of a new native session (02269765 -> 8b4c4b17). Here the
// session entry is replaced between the card and its answer the same way: a
// new session ID for the same key, so OpenClaw starts a fresh transcript.
// The answer's model request must carry the request as model-only context
// after the owner text, the persisted transcript must hold only the owner
// text, and the record must go once that run ends well.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,readdirSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {clarificationAnswerContext} from '../plugin/pending-clarification.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
const PORTAL="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, " +
  'copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const REQUEST='Increase enemies by 10%';
const QUESTIONS=[
  {id:'target',question:'Where should I apply the 10% enemy increase?',
    options:["A game project in my workspace (I'll locate the enemy/spawn config)",'A game you will describe']},
  {id:'meaning',question:'What does "increase enemies by 10%" mean for you?',options:['Enemy spawn count / density','Enemy health / damage']},
];
// dashboard answersMessage, as the owner sent it at 20:28Z.
const ANSWER=QUESTIONS.map((q,i)=>`${q.question}\n${i?'Enemy spawn count / density':q.options[0]}`).join('\n\n');
const NOTE=clarificationAnswerContext({request:REQUEST,earlierAnswers:[]});
const CHAT='clarify-fixture', KEY=`agent:pixel:openai-user:ods-${createHash('sha256').update(CHAT).digest('hex')}`;

const text=content=>typeof content==='string'?content:Array.isArray(content)?content.map(part=>part?.text ?? '').join('\n'):'';
const sse=(res,deltas,finish)=>{
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for (const delta of deltas) res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
  res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n');
};

test('real harness: a clarification answer in a replaced native session gets its request as model-only context',{skip:!pkg,timeout:120000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-clarification-answer-'));
  let log='',child,ingress;
  const requests=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const request=JSON.parse(Buffer.concat(chunks).toString());
    if (!request.tools?.length) { sse(res,[{role:'assistant',content:'Summary.'}],'stop'); return; }
    const users=request.messages.filter(message=>message.role==='user').map(message=>text(message.content));
    const current=users.at(-1) ?? '', afterTool=request.messages.at(-1)?.role==='tool';
    // The answer's note names the request too: classify the answer first.
    const turn=current.includes(QUESTIONS[0].question)?'answer':current.includes(REQUEST)?'ask':'other';
    const asked=request.messages.some(message=>message.role==='assistant' && message.tool_calls?.some(call=>call.function?.name==='pixel_ods_ask_user'));
    requests.push({turn,afterTool,current,users,asked});
    if (turn==='ask' && !afterTool) return sse(res,[{role:'assistant',tool_calls:[{index:0,id:'ask-1',type:'function',
      function:{name:'pixel_ods_ask_user',arguments:JSON.stringify({questions:QUESTIONS})}}]}],'tool_calls');
    if (turn==='ask') return sse(res,[{role:'assistant',content:'I will make the change once you answer.'}],'stop');
    sse(res,[{role:'assistant',content:turn==='answer'?'ROSTER_SCALE in world.js goes from 1.3 to 1.43.':'Unexpected request.'}],'stop');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'clarification-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'clarification-fixture',contracts:{tools:['pixel_ods_ask_user']},
    activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  // The production wiring of index.js for these hooks, the real ask tool and
  // store. /fixture/replace-session stands in for OpenClaw replacing the
  // session between the card and its answer.
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard,createRunAbortAdapter} from './ods/tool-loop-guard.mjs';
    import {createPendingClarifications} from './ods/pending-clarification.mjs';
    import {createAskUserTool} from './ods/ask-user.mjs';
    import {abortAgentHarnessRun,abortAndDrainAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
    import {getSessionEntry,resolveStorePath,upsertSessionEntry} from 'openclaw/plugin-sdk/session-store-runtime';
    import {randomUUID} from 'node:crypto';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({
      abortRun:createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun}),
      abortRunAndDrain:(sessionId,sessionKey)=>abortAndDrainAgentHarnessRun({
        sessionId:(sessionKey&&resolveActiveEmbeddedRunSessionId(sessionKey))||sessionId,sessionKey,settleMs:4000,forceClear:false,reason:'ods_client_disconnect'}),
      execControl:{signal:()=>true},pendingClarifications:createPendingClarifications()});
    const json=async req=>{let body='';for await(const part of req)body+=part;return JSON.parse(body);};
    const send=(res,value)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return true;};
    export default {id:'clarification-fixture',register(api){
      api.registerHttpRoute({path:'/pixel-ods/subagent-delivery',auth:'gateway',match:'exact',handler:async(req,res)=>
        send(res,{schemaVersion:1,kind:'ods-subagent-delivery',runId:(await json(req)).runId,status:'not-delegated'})});
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        const runId=(await json(req)).runId;await guard.settleDelivery(runId);return send(res,guard.deliveryVerificationForRun(runId));
      }});
      api.registerHttpRoute({path:'/fixture/replace-session',auth:'gateway',match:'exact',handler:async(req,res)=>{
        const scope={agentId:'pixel',sessionKey:(await json(req)).sessionKey,storePath:resolveStorePath(api.config?.session?.store,{agentId:'pixel'})};
        const before=(await getSessionEntry(scope))?.sessionId ?? null;
        await upsertSessionEntry({...scope,entry:{sessionId:randomUUID(),updatedAt:Date.now()}});
        return send(res,{before,after:(await getSessionEntry(scope))?.sessionId ?? null});
      }});
      api.on('before_prompt_build',(e,c)=>{guard.observeRun(c,'pixel',e);
        const prependContext=guard.promptContextForRun(c.runId), appendContext=guard.clarificationContextForRun(c.runId);
        record({prompt:c.runId,sessionId:c.sessionId,sessionKey:c.sessionKey,trigger:c.trigger,appendContext:appendContext??null});
        return prependContext||appendContext?{...(prependContext?{prependContext}:{}),...(appendContext?{appendContext}:{})}:undefined;});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_tool_call',(e,c)=>{const d=guard.beforeToolCall(e,c);record({before:e.toolName,run:c.runId,block:d?.blockReason??null});return d;});
      api.on('after_tool_call',(e,c)=>{record({after:e.toolName,run:c.runId,status:e.result?.details?.status??null});return guard.afterToolCall(e,c);});
      api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);record({finalize:c.runId,decision:d?.action??null});return d;});
      api.on('agent_end',(e,c)=>{guard.endPreviewRevalidation(e,c);guard.observeAgentEnd(e,c);record({end:c.runId,success:e.success});});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
      api.registerTool(createAskUserTool());
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['pixel_ods_ask_user']},plugins:{allow:['clarification-fixture'],load:{paths:[plugin]},
      entries:{'clarification-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  const store=join(root,'state','.ods-pending-clarifications');
  const records=()=>existsSync(store)?readdirSync(store).filter(name=>name.endsWith('.json')):[];
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
    const base=`http://127.0.0.1:${ingress.address().port}`;
    const chat=content=>fetch(`${base}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:CHAT,messages:[{role:'user',content:content+PORTAL}]}),
      signal:AbortSignal.timeout(60000)}).then(async response=>({status:response.status,body:await response.text()}));
    const frames=body=>body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const events=()=>existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const trace=()=>JSON.stringify({requests:requests.map(({turn,afterTool,asked,current})=>({turn,afterTool,asked,current:current.slice(0,400)})),events:events()})+'\n'+log;

    const asked=await chat(REQUEST);
    assert.equal(asked.status,200,trace());
    assert.deepEqual(frames(asked.body).at(-1)?.pixel_questions,{schemaVersion:1,questions:QUESTIONS},'the card reached the owner\n'+trace());
    assert.equal(records().length,1,'the card left its record\n'+trace());
    const askSession=events().find(event=>event.prompt)?.sessionId;
    assert.equal(events().find(event=>event.prompt)?.sessionKey,KEY,trace());

    const replaced=await fetch(`http://127.0.0.1:${port}/fixture/replace-session`,{method:'POST',
      headers:{authorization:'Bearer fixture-only','content-type':'application/json'},body:JSON.stringify({sessionKey:KEY})}).then(response=>response.json());
    assert.equal(replaced.before,askSession,trace());
    assert.ok(replaced.after && replaced.after!==askSession,trace());

    const answered=await chat(ANSWER);
    assert.equal(answered.status,200,trace());
    const answer=requests.find(request=>request.turn==='answer');
    assert.ok(answer,trace());
    // The replaced session: no earlier request and no ask call in context.
    assert.equal(answer.asked,false,'the model request lacks its own question\n'+trace());
    assert.ok(answer.users.slice(0,-1).every(user=>!user.includes(REQUEST)),'the earlier request is not in context\n'+trace());
    const owner=answer.current.indexOf(ANSWER), note=answer.current.indexOf(NOTE);
    assert.ok(owner>=0 && note>owner+ANSWER.length,'the request follows the owner text as model-only context\n'+trace());
    assert.equal(answer.current.split(NOTE).length,2,'once\n'+trace());
    const answerRun=events().find(event=>event.prompt && event.appendContext===NOTE);
    assert.ok(answerRun && answerRun.sessionId===replaced.after,'it ran in the replacement session\n'+trace());
    assert.ok(events().some(event=>event.end===answerRun.prompt && event.success===true),trace());
    assert.deepEqual(records(),[],'the run that ended well settled the card\n'+trace());
    // Model-only context: the persisted owner message is the owner's text.
    const sessions=join(root,'state','agents','pixel','sessions');
    const transcripts=readdirSync(sessions).filter(name=>name.endsWith('.jsonl')&&!name.includes('trajectory'))
      .map(name=>readFileSync(join(sessions,name),'utf8'));
    assert.ok(transcripts.some(transcript=>transcript.includes(JSON.stringify(ANSWER).slice(1,-1))),trace());
    assert.ok(transcripts.every(transcript=>!transcript.includes(NOTE.slice(0,60))),'the note is not persisted\n'+trace());

    const again=await chat(ANSWER);
    assert.equal(again.status,200,trace());
    assert.ok(requests.filter(request=>request.turn==='answer').slice(1).every(request=>!request.current.includes(NOTE)),
      'a second copy of the answer gets no note\n'+trace());
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
