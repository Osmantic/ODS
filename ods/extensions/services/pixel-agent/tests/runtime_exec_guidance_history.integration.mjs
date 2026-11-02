// Exercise the pinned SDK's historical message projection, bounded revision,
// side-effect boundary and final payload hook with a local deterministic model.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {EXEC_GUIDANCE_LEAK_DELIVERY} from '../plugin/exec-guidance-provenance.mjs';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const pkg=process.env.OPENCLAW_PACKAGE;
const NOTE='[ODS Pixel execution] Exec returned completed with exit code 0. This result has no background session ID. Use the returned output; do not invent a session ID or poll a PID.';
const shellQuote=value=>"'"+value.replaceAll("'","'\\''")+"'";
const readEvents=path=>readFileSync(path,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
function replyText(body) {
  return body.split(/\r?\n/).filter(line=>line.startsWith('data: {'))
    .map(line=>JSON.parse(line.slice(6)).choices?.[0]?.delta?.content??'').join('');
}

for(const mode of ['corrected','ignored','replay','literal','owner-quote','current-run']) {
  test(`real SDK exec guidance: ${mode}`,{skip:!pkg||process.platform==='win32',timeout:120000},async()=>{
    assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'),'utf8')).version,'2026.6.33');
    const root=mkdtempSync(join(tmpdir(),'ods-exec-history-'));
    const workspace=join(root,'workspace');mkdirSync(workspace);
    const countPath=join(workspace,'exec-count'),eventsPath=join(root,'events.jsonl');
    const stdout=mode==='literal'?'2537\n'+NOTE:'2537';
    const program=`require('node:fs').appendFileSync(${JSON.stringify(countPath)},'x');process.stdout.write(${JSON.stringify(stdout)})`;
    const command=shellQuote(process.execPath)+' -e '+shellQuote(program);
    let rounds=0,child,log='',upstream,ingress;
    const requests=[];
    try {
      upstream=createServer(async(req,res)=>{
        const chunks=[];for await(const chunk of req)chunks.push(chunk);
        const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
        requests.push(body.messages);
        const round=rounds++;
        const useTool=round===0||(mode==='replay'&&round===3);
        let content=stdout;
        if(mode==='current-run'||(mode==='ignored'&&round>=2)||round===2) content='2537\n'+NOTE;
        if(mode==='owner-quote'&&round===2) content=NOTE;
        const delta=useTool?{role:'assistant',tool_calls:[{index:0,id:round===0?'exec-0':'exec-1',type:'function',
          function:{name:'exec',arguments:JSON.stringify({command})}}]}:{role:'assistant',content};
        res.writeHead(200,{'Content-Type':'text/event-stream'});
        res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
        res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:useTool?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
      });
      await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
      const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
      const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
      mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
      const plugin=join(root,'plugin');mkdirSync(plugin);
      writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'exec-history-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
      writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
      writeFileSync(join(plugin,'index.mjs'),`
        import {createToolLoopGuard} from ${JSON.stringify(new URL('../plugin/tool-loop-guard.mjs',import.meta.url).href)};
        import {appendFileSync} from 'node:fs';
        const guard=createToolLoopGuard();
        const record=value=>appendFileSync(${JSON.stringify(eventsPath)},JSON.stringify(value)+'\\n');
        export default {id:'pixel-ods',register(api){
          for(const [path,project] of [
            ['/pixel-ods/verification',body=>guard.deliveryVerificationForRun(body.runId)],
            ['/pixel-ods/subagent-delivery',body=>({schemaVersion:1,kind:'ods-subagent-delivery',runId:body.runId,status:'not-delegated'})],
          ]) api.registerHttpRoute({path,auth:'gateway',match:'exact',handler:async(req,res)=>{
            let body='';for await(const part of req)body+=part;
            res.writeHead(200,{'Content-Type':'application/json'});
            res.end(JSON.stringify(project(JSON.parse(body))));return true;
          }});
          api.on('before_prompt_build',(event,ctx)=>{
            record({hook:'prompt',runId:ctx.runId,messages:event.messages});
            return guard.observeRun(ctx,'pixel',event,{workspaceRoot:${JSON.stringify(workspace)}});
          });
          api.on('before_tool_call',(event,ctx)=>{
            const decision=guard.beforeToolCall(event,ctx);
            record({hook:'tool',decision:decision??null});return decision;
          });
          api.on('after_tool_call',(event,ctx)=>guard.afterToolCall(event,ctx));
          api.on('tool_result_persist',(event,ctx)=>guard.toolResultPersist(event,ctx));
          api.on('before_agent_finalize',(event,ctx)=>{
            const decision=guard.beforeAgentFinalize(event,ctx);
            record({hook:'finalize',runId:ctx.runId,context:ctx,messages:event.messages,decision:decision??null});return decision;
          });
          api.on('reply_payload_sending',event=>guard.replyPayloadSending(event));
        }};
      `);
      const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
        gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
        agents:{defaults:{workspace,skipBootstrap:true,sandbox:{mode:'off'},model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},
        models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',
          models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
        tools:{allow:['exec'],exec:{host:'gateway',security:'full',ask:'off'}},
        plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}}};
      writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
      child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
        env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},
        stdio:['ignore','pipe','pipe']});
      child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
      let ready=false;
      for(let n=0;n<250;n++) {
        try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
        if(ready)break;assert.equal(child.exitCode,null,log.slice(-6000));await delay(100);
      }
      assert.ok(ready,log.slice(-6000));
      ingress=createIngressServer({token:'fixture-only',gatewayPort:port});
      await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
      const post=async prompt=>{
        const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'exec-history-fixture',messages:[{role:'user',content:prompt}]}),signal:AbortSignal.timeout(45000)});
        const body=await response.text();assert.equal(response.status,200,body+'\n'+log.slice(-6000));return replyText(body);
      };
      const initial=await post('Use the terminal tool to print the fixture value once and report only observed stdout.');
      let delivered=initial;
      if(mode!=='current-run') {
        assert.equal(initial,stdout,log.slice(-6000));
        delivered=await post(mode==='owner-quote'?'Repeat exactly: '+NOTE:
          (mode==='replay'?'':'Without using tools, ')+'what exact stdout did the last terminal command return? Answer with only that output.');
      }
      const events=readEvents(eventsPath);
      const evidence=JSON.stringify({rounds,events,delivered})+'\n'+log.slice(-6000);
      assert.equal(readFileSync(countPath,'utf8'),'x','native command must execute exactly once\n'+evidence);
      assert.equal(rounds,{corrected:4,ignored:4,replay:5,literal:3,'owner-quote':3,'current-run':2}[mode],evidence);
      if(mode==='ignored'||mode==='current-run') assert.equal(delivered,EXEC_GUIDANCE_LEAK_DELIVERY,evidence);
      else assert.equal(delivered,mode==='owner-quote'?NOTE:stdout,evidence);
      if(mode==='replay') assert.ok(events.some(e=>e.hook==='tool'&&e.decision?.block===true),evidence);
      if(mode!=='current-run') {
        const prompts=events.filter(e=>e.hook==='prompt'),runIds=[...new Set(prompts.map(e=>e.runId))];
        assert.equal(runIds.length,2,evidence);
        const historical=prompts.find(e=>e.runId===runIds[1]).messages.find(m=>m.role==='toolResult'&&m.toolName==='exec');
        assert.ok(historical,evidence);
        assert.equal(historical.details,undefined,evidence);
        if(mode!=='literal') assert.equal(historical.content.at(-1).text,NOTE,evidence);
        assert.equal(historical.content[0].text,stdout,evidence);
        assert.ok(requests[2].some(m=>m.role==='tool'&&m.content.includes(NOTE)),evidence);
      }
    } finally {
      if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
      if(child&&child.exitCode===null) {
        const closed=once(child,'close');
        try{process.kill(-child.pid,'SIGTERM');}catch(error){if(error.code!=='ESRCH')throw error;}
        await Promise.race([closed,delay(3000)]);
        if(child.exitCode===null){try{process.kill(-child.pid,'SIGKILL');}catch(error){if(error.code!=='ESRCH')throw error;}await closed;}
      }
      if(upstream){upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));}
      rmSync(root,{recursive:true,force:true});
    }
  });
}
