// Real pinned harness + real ingress, deterministic model, disposable state.
// tower1 round 067 cancel_and_recovery: the owner cancels a research request,
// then asks for an exact literal in the same chat. OpenClaw keeps the
// cancelled request in the transcript without an answer, so the next model
// call sees it right before the new message.
//   'early'    the cancel lands before any model output (the fleet timing);
//   'revising' the research run answered with a bare promise, completion
//              assurance armed a revision, and the cancel lands while
//              OpenClaw runs that revision pass.
// The model follows the withdrawal notice when it is there ('follows'); the
// fleet model instead ran the withdrawn research inside the next request
// ('ignores'). Either way the literal must be delivered as is: no revision
// and no armed replacement. (These fixture tools are not on OpenClaw's
// replay-safe list, unlike web_search and web_fetch, so here OpenClaw itself
// refuses a revision after them and only the armed replacement could leak.)
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
import {CLIENT_CANCELLED_REASON,OWNER_CANCELLED_REQUEST_CONTEXT} from '../plugin/tool-loop-guard.mjs';
const pkg=process.env.OPENCLAW_PACKAGE;
const PORTAL="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, " +
  'copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const RESEARCH='Research ten current Philadelphia event calendars thoroughly and compare their upcoming events. Do not write files.';
const LITERAL='RECOVERED-FLEET-fcccdbf3cd';
const RECOVERY=`Reply with exactly ${LITERAL}`;
const REVISION_PREFIX='Before accepting the previous final answer';
const WRONG='I need to revise the Philadelphia events research with proper source attribution.';
const PROMISE='I will search the Philadelphia event calendars now.';

const text=content=>typeof content==='string'?content:Array.isArray(content)?content.map(part=>part?.text ?? '').join('\n'):'';
const sse=(res,deltas,finish)=>{
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for (const delta of deltas) res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
  res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n');
};
const research=round=>[{role:'assistant',tool_calls:[
  {index:0,id:`search-${round}`,type:'function',function:{name:'fixture_search',arguments:JSON.stringify({query:'Philadelphia event calendars September 2026'})}},
  {index:1,id:`fetch-${round}`,type:'function',function:{name:'fixture_fetch',arguments:JSON.stringify({url:'https://www.visitphilly.com/events'})}}]}];

for (const phase of ['early','revising']) for (const model of ['follows','ignores'])
test(`real harness owner cancel then literal reply: cancel ${phase}, model ${model} the notice`,{skip:!pkg,timeout:120000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-cancel-recovery-'));
  let log='',child,ingress,held;
  const requests=[];
  const holding=new Promise(resolve=>{held=resolve;});
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req) chunks.push(chunk);
    const request=JSON.parse(Buffer.concat(chunks).toString());
    if (!request.tools?.length) { sse(res,[{role:'assistant',content:'Summary.'}],'stop'); return; }
    const users=request.messages.filter(message=>message.role==='user').map(message=>text(message.content));
    const current=users.at(-1) ?? '', afterTool=request.messages.at(-1)?.role==='tool';
    const turn=current.includes(RECOVERY)?'B':current.includes(RESEARCH)?'A':current.includes(REVISION_PREFIX)?'revision':'other';
    // A revision pass is B's when B's request is already in context.
    const owner=turn==='revision'?(users.some(user=>user.includes(RECOVERY))?'B':'A'):turn;
    requests.push({turn,owner,afterTool,current,users});
    const hold=()=>{res.writeHead(200,{'Content-Type':'text/event-stream'});held();};
    if (owner==='A') return phase==='early' || turn==='revision' ? hold() : sse(res,[{role:'assistant',content:PROMISE}],'stop');
    if (owner==='B') {
      if (turn==='revision') return sse(res,[{role:'assistant',content:WRONG}],'stop');
      if (afterTool || (model==='follows' && current.includes(OWNER_CANCELLED_REQUEST_CONTEXT))) return sse(res,[{role:'assistant',content:LITERAL}],'stop');
      return sse(res,research('b'),'tool_calls');
    }
    sse(res,[{role:'assistant',content:'Unexpected request.'}],'stop');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'cancel-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'cancel-fixture',contracts:{tools:['fixture_search','fixture_fetch']},
    activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  // The production wiring of index.js for these hooks; the fixture tools stand
  // in for web_search and web_fetch with the same result shapes.
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard,createRunAbortAdapter} from './ods/tool-loop-guard.mjs';
    import {abortAgentHarnessRun,abortAndDrainAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
    import {appendFileSync} from 'node:fs';
    const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
    const guard=createToolLoopGuard({
      abortRun:createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun}),
      abortRunAndDrain:(sessionId,sessionKey)=>abortAndDrainAgentHarnessRun({
        sessionId:(sessionKey&&resolveActiveEmbeddedRunSessionId(sessionKey))||sessionId,sessionKey,settleMs:4000,forceClear:false,reason:'ods_client_disconnect'}),
      execControl:{signal:()=>true}});
    const WEB={fixture_search:'web_search',fixture_fetch:'web_fetch'};
    const web=(e,c)=>[{...e,toolName:WEB[e.toolName]??e.toolName},{...c,toolName:WEB[c?.toolName]??c?.toolName}];
    export default {id:'cancel-fixture',register(api){
      api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        const runId=JSON.parse(body).runId;await guard.settleDelivery(runId);
        res.writeHead(200,{'Content-Type':'application/json'});
        res.end(JSON.stringify(guard.deliveryVerificationForRun(runId)));return true;
      }});
      api.registerHttpRoute({path:'/pixel-ods/abort',auth:'gateway',match:'exact',handler:async(req,res)=>{
        let body='';for await(const part of req)body+=part;
        const aborted=await guard.abortUserRun(JSON.parse(body).user);record({cancel:aborted});
        res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({aborted}));return true;
      }});
      api.on('before_prompt_build',(e,c)=>{guard.observeRun(c,'pixel',e);const context=guard.promptContextForRun(c.runId);
        record({prompt:c.runId,trigger:c.trigger,context:context??null});return context?{prependContext:context}:undefined;});
      api.on('model_call_started',(e,c)=>guard.observeModelCall(e,c));
      api.on('model_call_ended',(e,c)=>guard.observeModelEnd(e,c));
      api.on('before_tool_call',(e,c)=>{const d=guard.beforeToolCall(e,c);record({before:e.toolName,run:c.runId,block:d?.blockReason??null});return d;});
      api.on('after_tool_call',(e,c)=>{record({after:e.toolName,run:c.runId,details:Boolean(e.result?.details)});return guard.afterToolCall(...web(e,c));});
      api.on('before_agent_finalize',async(e,c)=>{await guard.verifyCitedPages(e,c);const d=guard.beforeAgentFinalize(e,c);
        record({finalize:c.runId,text:e.lastAssistantMessage,decision:d?.action??null,reason:d?.reason??null});return d;});
      api.on('agent_end',(e,c)=>{guard.endPreviewRevalidation(e,c);guard.observeAgentEnd(e,c);});
      api.on('reply_payload_sending',e=>guard.replyPayloadSending(e));
      api.registerTool({name:'fixture_search',description:'Search the web.',parameters:{type:'object',properties:{query:{type:'string'}},required:['query']},
        async execute(id,args){const results=[{url:'https://www.metrophiladelphia.com/calendar',title:'Calendar - Metro Philadelphia'},
          {url:'https://www.visitphilly.com/events',title:'Events in Philadelphia'}];
          return {content:[{type:'text',text:JSON.stringify({query:args.query,results})}],details:{results}};}});
      api.registerTool({name:'fixture_fetch',description:'Read one web page.',parameters:{type:'object',properties:{url:{type:'string'}},required:['url']},
        async execute(id,args){return {content:[{type:'text',text:'Porchfest, September 27. Boyz II Men, September 26.'}],
          details:{status:200,url:args.url,finalUrl:args.url+'/',text:'Porchfest, September 27. Boyz II Men, September 26.'}};}});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['fixture_search','fixture_fetch']},plugins:{allow:['cancel-fixture'],load:{paths:[plugin]},
      entries:{'cancel-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
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
    const base=`http://127.0.0.1:${ingress.address().port}`;
    const chat=content=>fetch(`${base}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'cancel-fixture',messages:[{role:'user',content:content+PORTAL}]}),
      signal:AbortSignal.timeout(60000)}).then(async response=>({status:response.status,body:await response.text()}));
    const first=chat(RESEARCH);
    await Promise.race([holding,delay(45000).then(()=>{throw new Error('the research request never reached the held model call\n'+log);})]);
    const cancel=await fetch(`${base}/v1/chat/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user:'cancel-fixture'}),signal:AbortSignal.timeout(30000)});
    assert.deepEqual(await cancel.json(),{aborted:true},log);
    // The ingress ends the cancelled stream; its body may be cut off.
    await first.catch(()=>undefined);
    const second=await chat(RECOVERY);
    const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
    const frames=second.body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
    const delivered=frames.map(frame=>frame.choices?.[0]?.delta?.content ?? '').join('');
    const trace=JSON.stringify({requests:requests.map(({turn,owner,afterTool,current})=>({turn,owner,afterTool,current:current.slice(0,160)})),events,delivered})+'\n'+log;
    assert.equal(second.status,200,trace);
    assert.equal(delivered,LITERAL,trace);
    assert.notEqual(frames.at(-1)?.pixel_outcome?.status,'failed',trace);

    const bRequests=requests.filter(request=>request.owner==='B');
    assert.ok(bRequests[0].users.some(user=>user.includes(RESEARCH)),'the cancelled request is still in the next transcript\n'+trace);
    // OpenClaw stamps the time in front of the model-only context and message.
    const notice=bRequests[0].current.indexOf(OWNER_CANCELLED_REQUEST_CONTEXT);
    assert.ok(notice>=0&&notice<bRequests[0].current.indexOf(RECOVERY),'the next owner message is told it is withdrawn\n'+trace);
    assert.ok(bRequests.every(request=>request.turn!=='revision'),'no revision pass reaches the next request\n'+trace);
    assert.ok(!requests.some(request=>request.owner==='B'&&request.users.some(user=>user.includes('Use the web evidence already returned'))),
      'the research run\'s revision instruction never reaches the next request\n'+trace);
    const bRun=events.find(event=>event.prompt&&event.context===OWNER_CANCELLED_REQUEST_CONTEXT)?.prompt;
    assert.ok(bRun,trace);
    assert.deepEqual(events.filter(event=>event.finalize===bRun).map(event=>event.decision),[null],trace);
    const bTools=events.filter(event=>event.before&&event.run===bRun);
    if (model==='follows') assert.deepEqual(bTools,[],'a conversational reply uses no tools\n'+trace);
    else {
      assert.equal(bTools.length,2,trace);
      assert.ok(bTools.every(event=>event.block===null),'the next request is not fenced as cancelled\n'+trace);
      assert.ok(events.filter(event=>event.after&&event.run===bRun).every(event=>event.details),trace);
    }
    if (phase==='revising') {
      const aRun=events.find(event=>event.decision==='revise')?.finalize;
      assert.ok(aRun&&aRun!==bRun,'the research run armed its revision before the cancel\n'+trace);
      assert.ok(requests.some(request=>request.owner==='A'&&request.turn==='revision'),'OpenClaw started that revision pass\n'+trace);
      assert.ok(events.filter(event=>event.prompt===aRun).every(event=>[null,CLIENT_CANCELLED_REASON].includes(event.context)),
        'the cancelled run never takes the next message\'s notice\n'+trace);
    }
    assert.equal(events.filter(event=>event.context===OWNER_CANCELLED_REQUEST_CONTEXT).length,
      events.filter(event=>event.prompt===bRun).length,'only the next owner run carries the notice\n'+trace);
    // Model-only context: the persisted owner message is the owner's text.
    const sessions=join(root,'state','agents','pixel','sessions');
    const transcript=readdirSync(sessions).filter(name=>name.endsWith('.jsonl')&&!name.includes('trajectory'))
      .map(name=>readFileSync(join(sessions,name),'utf8')).join('\n');
    assert.ok(transcript.includes(RECOVERY),trace);
    assert.ok(!transcript.includes(OWNER_CANCELLED_REQUEST_CONTEXT.slice(0,60)),'the notice is not persisted as owner text\n'+trace);
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
  }
});
