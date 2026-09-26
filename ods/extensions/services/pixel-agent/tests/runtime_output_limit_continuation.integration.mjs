// Real pinned harness + the real pixel-ods plugin + the real ingress on the
// Portal history path; deterministic model, a stand-in ODS preview host,
// disposable state.
// strixy 2026-09-25: the owner's website request produced one reply that
// wrote the whole page in one tool call and stopped at the output limit
// (finish_reason "length"). OpenClaw 2026.6.33 does not run the cut call and
// skips before_agent_finalize, so the ingress asks Pixel for one continuation
// turn with a fixed message. Each cut case is compared with the same model
// behaviour without the cut: the continuation keeps the owner request's
// contract, Playground project and delivery checks, so the owner receives
// what the uncut turn would have delivered. A continuation cut again is
// reported once, in words that fit a website request or a written answer.
// After a tool error OpenClaw delivers a cut reply's own text instead of its
// incomplete-turn text: a website is still continued, and a written answer
// keeps its delivered part, followed by the report. A result the host
// already verified is kept when only the closing reply is cut, and a turn
// whose test run failed is reported as failed, never continued.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import net from 'node:net';
import {once} from 'node:events';
import {spawn, execFile} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,readdirSync,rmSync,statSync,copyFileSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer, gatewayFetch} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
import {OUTPUT_LIMIT_ANSWER_TEXT,OUTPUT_LIMIT_CONTINUATION_PROMPT,OUTPUT_LIMIT_CONTINUED_TEXT,
  OUTPUT_LIMIT_WORKSPACE_TEXT} from '../plugin/output-limit-recovery.mjs';
import {VERIFICATION_FAILED_DELIVERY_PREFIX} from '../plugin/tool-loop-guard.mjs';

const pkg=process.env.OPENCLAW_PACKAGE;
const DELIVERY="\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. " +
  'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]';
const SITE='as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest type effects.  Best you can do.'+DELIVERY;
const TEXT='Write me the longest, most detailed guide you can about how old-growth forests store carbon.'+DELIVERY;
const OPENING='Let me build something impressive — a full forest-themed interactive experience.';
const PARTIAL='Old-growth forests store carbon in three large pools: living trees, dead wood and deep soils. ' +
  'In living trees, most of the carbon sits in the massive trunks of the oldest individuals, which keep';
const READY='Your forest page is ready in Playground/forest as index.html, styles.css and script.js.';
const GUIDE='Old-growth forests store carbon in living wood, dead wood and deep soils; here is the concise guide.';
const STYLES={path:'Playground/forest/styles.css',content:'body{background:#0b3d20;color:#e8f5e9}\n'};
const PAGE={path:'Playground/forest/index.html',content:'<!doctype html><html><head><title>Forest</title><link rel="stylesheet" href="styles.css"></head><body><h1>Forest</h1></body></html>\n'};
const SCRIPT={path:'Playground/forest/script.js',content:'document.body.dataset.fireflies="on";\n'};
const SPLIT_PAGE={path:'Playground/forest/index.html',content:'<!doctype html><html><head><title>Forest</title><link rel="stylesheet" href="styles.css">'+
  '<script src="script.js" defer></script></head><body><h1>Forest</h1></body></html>\n'};
const cut={cut:true}, cutText={cut:true,text:true}, write=file=>({write:file}), say=text=>({say:text});
const read=path=>({read:path}), preview=relativeDirectory=>({preview:relativeDirectory});
// The cut reply's own text as OpenClaw delivers it after a tool error.
const cutAnswer={cut:true,text:true,opening:PARTIAL};
const cutStyles={cut:true,path:STYLES.path};
// A failed tool call: OpenClaw then keeps a cut reply's own text.
const missing=read('Playground/forest/notes.md');

// Each cut case and the uncut control with the same model behaviour.
const CASES={
  // Slow model calls so the Portal's live activity polls see both runs.
  'site':{prompt:SITE,steps:[cut,write(PAGE),say(READY)],control:[write(PAGE),say(READY)],continuationRound:1,delayMs:1800},
  'tools then cut':{prompt:SITE,steps:[write(STYLES),cut,write(PAGE),say(READY)],control:[write(STYLES),write(PAGE),say(READY)],continuationRound:2},
  'unfounded ready claim':{prompt:SITE,steps:[cut,say(READY)],control:[say(READY)],continuationRound:1},
  'text':{prompt:TEXT,steps:[cutText,say(GUIDE)],control:[say(GUIDE)],continuationRound:1,followUp:true},
  // Following the prevention line: index.html saved, the styles.css write cut.
  // The continuation writes the rest and publishes without rewriting it.
  'index.html saved, then cut':{prompt:SITE,
    steps:[write(SPLIT_PAGE),cutStyles,write(STYLES),write(SCRIPT),preview('Playground/forest'),say(READY)],
    control:[write(SPLIT_PAGE),write(STYLES),write(SCRIPT),preview('Playground/forest'),say(READY)],continuationRound:2},
  // After a tool error OpenClaw returns the reply's opening sentence.
  'site after a tool error':{prompt:SITE,steps:[write(STYLES),missing,cut,write(PAGE),say(READY)],
    control:[write(STYLES),missing,write(PAGE),say(READY)],continuationRound:3,incompleteTurn:false},
  // Cut twice: the report leads, in words that fit the request.
  'cut again':{prompt:SITE,steps:[cut,cut],report:OUTPUT_LIMIT_WORKSPACE_TEXT},
  'text cut again':{prompt:TEXT,steps:[cutText,cutText],report:OUTPUT_LIMIT_ANSWER_TEXT},
};

async function run(prompt,steps,{followUp=false,delayMs=0}={}) {
  const root=mkdtempSync(join(tmpdir(),'ods-output-limit-'));
  const workspace=join(root,'workspace');
  let log='',child,ingress,previewHost;
  const requests=[],grants=[],published=[];
  const chunk=(delta,finish=null,extra={})=>'data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',
    choices:[{index:0,delta,finish_reason:finish}],...extra})+'\n\n';
  const text=content=>typeof content==='string'?content:Array.isArray(content)?content.map(part=>part?.text??'').join('\n'):'';
  const call=(name,args)=>chunk({tool_calls:[{index:0,id:`call-${requests.length}`,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
  const upstream=createServer(async(req,res)=>{
    const parts=[];for await(const part of req) parts.push(part);
    const body=JSON.parse(Buffer.concat(parts).toString());
    // Each run has its own disposable root; compare prompts without it.
    requests.push({system:text(body.messages.find(message=>message.role==='system')?.content).replaceAll(root,'<root>'),
      users:body.messages.filter(message=>message.role==='user').map(message=>text(message.content)),
      toolResults:body.messages.filter(message=>message.role==='tool').map(message=>text(message.content).replaceAll(root,'<root>')),
      tools:body.messages.filter(message=>message.role==='tool').length,maxTokens:body.max_tokens??body.max_completion_tokens});
    // After the script, the model repeats its last answer (a revision pass).
    const step=steps[requests.length-1]??steps.at(-1);
    if(delayMs)await delay(delayMs);
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    if(step.say){res.write(chunk({role:'assistant',content:step.say}));res.end(chunk({},'stop')+'data: [DONE]\n\n');return;}
    if(step.write||step.read||step.preview||step.exec){
      res.write(chunk({role:'assistant',content:''}));
      res.write(step.write?call('write',step.write):step.read?call('read',{path:step.read})
        :step.exec?call('exec',{command:step.exec,workdir:join(workspace,step.workdir)})
        :call('pixel_ods_workspace_preview',{relativeDirectory:step.preview}));
      res.end(chunk({},'tool_calls')+'data: [DONE]\n\n');return;
    }
    // The recorded shape: an opening sentence, then (unless text-only) one
    // write whose arguments end mid-document at the output limit.
    res.write(chunk({role:'assistant',content:step.opening??OPENING}));
    if(!step.text)res.write(chunk({tool_calls:[{index:0,id:`call-${requests.length}`,type:'function',function:{name:'write',
      arguments:`{"path":"${step.path??'Playground/forest/index.html'}","content":"<!doctype html><html><head><style>:root{--moss:#2f5d3a`}}]}));
    res.end(chunk({},'length',{usage:{prompt_tokens:900,completion_tokens:8192,total_tokens:9092}})+'data: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  // A stand-in for the ODS preview host on a private socket: it publishes the
  // directory's files as they are on disk and answers with the host receipt.
  const socketPath=join(root,'preview.sock');
  previewHost=net.createServer(connection=>{
    let raw='';connection.on('data',data=>raw+=data);connection.on('end',()=>{
      const request=JSON.parse(raw.trim());
      const directory=join(workspace,request.relativeDirectory);
      let files=[];try{files=readdirSync(directory,{recursive:true}).map(String).filter(file=>statSync(join(directory,file)).isFile()).sort();}catch{}
      published.push(files);
      const boundary='Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; '+
        'no arbitrary host path, network destination, server process, overwrite, or execution authority.';
      if(!files.includes('index.html')){connection.end(JSON.stringify({schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'failed',
        error:'ODS workspace preview publication failed',errorCode:'missing_entry',boundary})+'\n');return;}
      const digest=createHash('sha256');let bytes=0;
      for(const file of files){const body=readFileSync(join(directory,file));bytes+=body.length;digest.update(file).update(body);}
      const sha256=digest.digest('hex'),siteId=`site-${sha256.slice(0,24)}`;
      connection.end(JSON.stringify({schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:request.relativeDirectory,
        port:9437,siteId,url:`http://${siteId}.localhost:9437/${siteId}/`,sha256,
        entrySha256:createHash('sha256').update(readFileSync(join(directory,'index.html'))).digest('hex'),entryFile:'index.html',
        files:files.length,bytes,httpStatus:200,readbackVerified:true,executable:false,overwritten:false,boundary,
        publishedPaths:files,publishedPathsOmitted:0})+'\n');
    });
  });
  await new Promise(resolve=>previewHost.listen(socketPath,resolve));
  // Only the gateway process: the plugin's fixed host socket leads to it.
  const preload=join(root,'preview-host.mjs');
  writeFileSync(preload,`import net from 'node:net';const connect=net.createConnection;
net.createConnection=function(options,...rest){if(options&&typeof options==='object'&&options.path==='/run/ods-pixel-preview/control.sock')options={...options,path:${JSON.stringify(socketPath)}};return connect.call(this,options,...rest);};\n`);
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  // Pixel's access runtime keeps its state under ~/.openclaw.
  mkdirSync(join(root,'.openclaw'),{mode:0o700});
  // Pixel runs exec through its cancellable wrapper under ~/.openclaw.
  const controls=join(root,'.openclaw','.ods-exec-control');mkdirSync(controls,{mode:0o700});
  copyFileSync(new URL('../host/cancellable-exec.sh',import.meta.url),join(controls,'cancellable-exec.sh'));
  chmodSync(join(controls,'cancellable-exec.sh'),0o500);
  mkdirSync(workspace);mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const model='Qwen3.6-35B-A3B';
  // The strixy managed shape: an 8192-token output limit for the local model.
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace,skipBootstrap:true,sandbox:{mode:'off'},model:{primary:`ods-local/${model}`},contextTokens:131072,heartbeat:{every:'0m'}},
      list:[{id:'pixel',default:true,workspace,model:`ods-local/${model}`,contextTokens:131072,params:{maxTokens:8192}}]},
    models:{mode:'replace',providers:{'ods-local':{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
      models:[{id:model,name:`ODS Local ${model}`,contextWindow:131072,maxTokens:8192,reasoning:false,input:['text']}]}}},
    tools:{exec:{host:'gateway',security:'full',ask:'off'}},
    plugins:{allow:['pixel-ods'],load:{paths:[fileURLToPath(new URL('../plugin/',import.meta.url))]},
      entries:{'pixel-ods':{enabled:true,config:{modelContextWindow:131072},hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),
        OPENCLAW_SKIP_CHANNELS:'1',NODE_OPTIONS:`--import=${pathToFileURL(preload).href}`},
      stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let n=0;n<400;n++){
      try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
      if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);
    }
    assert.ok(ready,log);
    // The production ingress transport, observed: every grant request and answer.
    const observed=async(url,init)=>{
      const response=await gatewayFetch(url,init);
      if(!String(url).endsWith('/pixel-ods/output-limit-continuation'))return response;
      const [kept,copy]=response.body.tee();
      const asked=JSON.parse(init.body);
      grants.push(new Response(copy).json().then(answer=>({...answer,incompleteTurn:asked.incompleteTurn})));
      return {...response,body:kept};
    };
    ingress=createIngressServer({token:'fixture-only',gatewayPort:port,historyLedger:createChatHistoryLedger(join(root,'chat-state')),
      deps:{execFile,fetch:observed,setTimeout,clearTimeout}});
    await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
    const turns=[];
    const send=async(snapshot,requestId)=>{
      const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
        headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(90000),
        body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'output-limit-fixture',
          messages:[{role:'user',content:snapshot.at(-1).content}],history_snapshot:{schemaVersion:1,messages:snapshot},request_id:requestId})});
      const body=await response.text();
      assert.equal(response.status,200,body+'\n'+log);
      const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
      assert.deepEqual(frames.filter(frame=>frame.error),[],body);
      const answer=frames.filter(frame=>frame.object!=='ods.task.activity');
      turns.push({delivered:answer.map(frame=>frame.choices?.[0]?.delta?.content??'').join(''),outcome:answer.at(-1).pixel_outcome?.status,
        preview:answer.at(-1).pixel?.preview?.url,runId:answer.at(-1).id,
        activity:[...new Set(frames.filter(frame=>frame.object==='ods.task.activity').map(frame=>frame.id))]});
    };
    await send([{role:'user',content:prompt}],'turn-1');
    const firstTurnRequests=requests.length;
    if(followUp)await send([{role:'user',content:prompt},{role:'assistant',content:turns[0].delivered},
      {role:'user',content:'Thanks. Summarize that in one sentence.'+DELIVERY}],'turn-2');
    const ledger=readdirSync(join(root,'chat-state')).filter(name=>name.endsWith('.json'))
      .map(name=>JSON.parse(readFileSync(join(root,'chat-state',name),'utf8')).status);
    const files=readdirSync(workspace,{recursive:true}).map(String).filter(file=>!file.startsWith('.')).sort();
    const contents=Object.fromEntries(files.filter(file=>/\.(?:html|css|js|py)$/.test(file))
      .map(file=>[file,readFileSync(join(workspace,file),'utf8')]));
    return {turns,requests,firstTurnRequests,grants:await Promise.all(grants),ledger,files,contents,published,log};
  } finally {
    if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');
      await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
    await new Promise(resolve=>previewHost.close(resolve));
    rmSync(root,{recursive:true,force:true});
  }
}

const skip=!pkg||process.platform==='win32';
const traceOf=result=>JSON.stringify({...result,log:undefined,requests:result.requests.map(r=>({...r,system:r.system.length}))})+'\n'+result.log;

for (const [name,spec] of Object.entries(CASES)) test(`real Pixel output-limit continuation: ${name}`,
  {skip,timeout:300000}, async () => {
  const result=await run(spec.prompt,spec.steps,{followUp:spec.followUp,delayMs:spec.delayMs});
  const trace=traceOf(result);
  const [turn]=result.turns;
  // Every completed owner turn asks; only the cut one is granted, once.
  assert.equal(result.grants.length,result.turns.length,trace);
  assert.equal(result.grants[0].eligible,true,trace);
  assert.equal(result.grants[0].incompleteTurn,spec.incompleteTurn??true,trace);
  assert.match(result.grants[0].user,/^ods-[0-9a-f]{64}$/,trace);
  assert.ok(result.grants.slice(1).every(grant=>grant.eligible===false),trace);
  assert.ok(result.requests.every(request=>request.maxTokens===8192),trace);
  // The cut call never ran: no file holds its unfinished document.
  assert.ok(!Object.values(result.contents).some(content=>content.includes('--moss')),trace);
  assert.deepEqual(result.ledger,['ready'],trace);
  if(spec.report){
    assert.equal(result.requests.length,2,'one continuation, never a loop\n'+trace);
    assert.ok(turn.delivered.startsWith(`${OUTPUT_LIMIT_CONTINUED_TEXT} ${spec.report}`),trace);
    // A written answer is never told that a file write was cut.
    if(spec.prompt===TEXT)assert.equal(turn.delivered,`${OUTPUT_LIMIT_CONTINUED_TEXT} ${spec.report}`,trace);
    assert.equal(turn.outcome,'failed',trace);
    return;
  }
  const continuation=result.requests[spec.continuationRound];
  // OpenClaw prefixes each user message with its own timestamp envelope.
  assert.match(continuation.users.at(-1),/^\[[^\]\n]+\] ODS internal continuation: /,trace);
  assert.ok(continuation.users.at(-1).endsWith(` ${OUTPUT_LIMIT_CONTINUATION_PROMPT}`),'only the fixed message is new\n'+trace);
  assert.ok(continuation.users.slice(0,-1).some(user=>user.includes('forest')),'the owner request stays in context\n'+trace);
  assert.equal(continuation.system,result.requests[0].system,'the continuation keeps the owner turn system prompt\n'+trace);
  assert.match(continuation.system,/can hold at most about 8192 output tokens/,trace);
  // The same model behaviour without the cut, for comparison.
  const control=await run(spec.prompt,spec.control);
  assert.ok(control.grants.every(grant=>grant.eligible===false),'an uncut turn is never continued\n'+JSON.stringify(control.grants));
  assert.equal(control.requests[0].system,result.requests[0].system,'the same owner contract');
  const {activity:_activity,runId:_runId,...delivered}=turn;
  const {activity:_controlActivity,runId:_controlRunId,...expected}=control.turns[0];
  assert.deepEqual(delivered,expected,'the owner receives what the uncut turn delivers\n'+trace);
  assert.deepEqual(result.files,control.files,'the same files in the same Playground project\n'+trace);
  assert.deepEqual(result.contents,control.contents,trace);
  assert.deepEqual(result.published,control.published,'the same published files\n'+trace);
  if(name==='unfounded ready claim')assert.equal(turn.outcome,'failed',trace);
  if(name==='index.html saved, then cut'){
    assert.equal(turn.outcome,'passed',trace);
    assert.match(turn.preview,/^http:\/\/site-[0-9a-f]{24}\.localhost:9437\//,trace);
    assert.deepEqual(result.published,[['index.html','script.js','styles.css']],trace);
    assert.ok(result.requests.every(request=>request.toolResults.every(result=>!/not created or inspected/.test(result))),
      'the continuation is never refused the preview\n'+trace);
  }
  if(name==='site'){
    // The Portal's live activity follows the continuation run that did the work.
    assert.ok(turn.activity.includes(turn.runId),trace);
    assert.ok(turn.activity.length>=2,trace);
  }
  if(name==='text'){
    assert.equal(turn.delivered,GUIDE,trace);
    assert.notEqual(turn.outcome,'failed',trace);
    // The next owner message is its own turn, classified by itself.
    const next=result.turns[1];
    assert.equal(next.delivered,GUIDE,trace);
    assert.equal(result.requests.length,result.firstTurnRequests+1,trace);
    assert.match(result.requests.at(-1).users.at(-1),/Summarize that in one sentence\./,trace);
  }
});

// #6743 review: after a tool error OpenClaw delivered the cut answer's own
// text, and Pixel replaced it with the report; the owner lost the answer.
test('real Pixel output-limit report: a cut answer OpenClaw delivered keeps its text',{skip,timeout:300000},async()=>{
  const result=await run(TEXT,[missing,cutAnswer]);
  const trace=traceOf(result);
  const [turn]=result.turns;
  assert.equal(result.requests.length,2,'no continuation\n'+trace);
  assert.deepEqual(result.grants.map(({eligible,incompleteTurn})=>({eligible,incompleteTurn})),[{eligible:false,incompleteTurn:false}],trace);
  assert.ok(turn.delivered.startsWith(PARTIAL),trace);
  assert.ok(turn.delivered.endsWith(`\n\n${OUTPUT_LIMIT_ANSWER_TEXT}`),trace);
  assert.equal(turn.outcome,'failed',trace);
});

// #6743 review: a result the host already verified became 'failed' when only
// the closing reply was cut.
test('real Pixel output-limit report: a verified page is kept when only the closing reply is cut',{skip,timeout:300000},async()=>{
  const work=[write(SPLIT_PAGE),write(STYLES),write(SCRIPT),preview('Playground/forest')];
  const result=await run(SITE,[...work,cutText]);
  const control=await run(SITE,[...work,say(READY)]);
  const trace=traceOf(result);
  assert.equal(result.requests.length,work.length+1,'no continuation\n'+trace);
  assert.ok(result.grants.every(grant=>grant.eligible===false),trace);
  assert.equal(control.turns[0].outcome,'passed',traceOf(control));
  assert.equal(result.turns[0].outcome,'passed',trace);
  assert.equal(result.turns[0].preview,control.turns[0].preview,trace);
  assert.doesNotMatch(result.turns[0].delivered,/output limit/i,trace);
});

// #6743 re-verification: the owner's tests ran and failed, and the reply
// carrying the whole-file fix was cut. The failed exec is a tool error, so
// OpenClaw delivered the cut reply's own text and the ingress asked with
// incompleteTurn false; the continuation rewrote calc.py and said "all tests
// pass now" without running them, and the owner got outcome none. A turn
// whose latest test run failed is not continued: the owner gets the failed
// report, and the same model behaviour uncut is delivered as failed too.
// (This gateway fixture has no /workspace, so the unittest run Pixel binds to
// /workspace/Playground/calc runs in the fallback directory and exits 5 with
// no tests run: a failed test run to Pixel, as a failing assertion is.)
const CALC='Write a Python script calc.py with a function add(a, b) and unit tests in test_calc.py, '+
  'then run the tests with python3 -m unittest.'+DELIVERY;
const BUGGY={path:'Playground/calc/calc.py',content:'def add(a, b):\n    return a - b\n'};
const FIXED={path:'Playground/calc/calc.py',content:'def add(a, b):\n    return a + b\n'};
const TESTS={path:'Playground/calc/test_calc.py',content:'import unittest\nfrom calc import add\n\n'+
  'class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n'};
const CLAIM='Fixed add() in calc.py; all tests pass now.';
test('real Pixel output-limit report: a failed test run is not continued into an unchecked claim',{skip,timeout:300000},async()=>{
  const failingTests=[write(BUGGY),write(TESTS),{exec:'python3 -m unittest -v',workdir:'Playground/calc'}];
  const result=await run(CALC,[...failingTests,{cut:true,path:BUGGY.path},write(FIXED),say(CLAIM)]);
  const control=await run(CALC,[...failingTests,write(FIXED),say(CLAIM)]);
  const trace=traceOf(result);
  const [turn]=result.turns;
  assert.match(result.requests[failingTests.length].toolResults.at(-1),/Command exited with code [1-9]/,'the test run failed\n'+trace);
  assert.deepEqual(result.grants.map(({eligible,incompleteTurn})=>({eligible,incompleteTurn})),
    [{eligible:false,incompleteTurn:false}],trace);
  assert.equal(result.requests.length,failingTests.length+1,'no continuation\n'+trace);
  assert.equal(turn.outcome,'failed',trace);
  assert.equal(turn.delivered,`${OUTPUT_LIMIT_WORKSPACE_TEXT}\n\n${VERIFICATION_FAILED_DELIVERY_PREFIX}`,trace);
  assert.equal(result.contents[BUGGY.path],BUGGY.content,'the cut fix never ran\n'+trace);
  assert.equal(control.turns[0].outcome,'failed',traceOf(control));
  assert.equal(control.turns[0].delivered,VERIFICATION_FAILED_DELIVERY_PREFIX,traceOf(control));
});
