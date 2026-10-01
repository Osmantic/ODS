// Real native exec/hook/ingress lifecycle, loopback provider and private state.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,cpSync,symlinkSync,readFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer} from '../host/pixel_ingress.mjs';

const pkg=process.env.OPENCLAW_PACKAGE;
const answer='O Node respondeu ao diagnóstico. O teste de venv não foi executado porque o comando inteiro incluía uma remoção recursiva não autorizada. Não confirmei instalação de dependências nem a capacidade do executor gerenciado.';
for(const finalMode of ['answer','tool']) test(`native blocked diagnostic preserves prior evidence through real ingress: ${finalMode}`,
  {skip:!pkg || process.platform==='win32',timeout:90000},async()=>{
    const root=mkdtempSync(join(tmpdir(),'ods-blocked-diagnostic-'));
    const blockedPath=join(root,'blocked-venv');mkdirSync(blockedPath);
    writeFileSync(join(blockedPath,'preserve.txt'),'fixture-owned original');
    const quoted="'"+blockedPath.replace(/'/g,"'\\''")+"'";
    let rounds=0,child,ingress,log='';
    const requests=[];
    const upstream=createServer(async(req,res)=>{
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const request=JSON.parse(Buffer.concat(chunks));requests.push(request);rounds++;
      const call=(command)=>({role:'assistant',tool_calls:[{index:0,id:'call-'+rounds,type:'function',
        function:{name:'exec',arguments:JSON.stringify({command,workdir:join(root,'workspace')})}}]});
      const delta=rounds===1?call('node --version'):rounds===2?call('python3 -m venv '+quoted+' && rm -rf '+quoted):
        rounds===3&&finalMode==='tool'?call('echo forbidden-after-refusal'):{role:'assistant',content:answer};
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
      res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:delta.tool_calls?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
    });
    await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
    const socket=createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
    const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));
    mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules/openclaw'));
    mkdirSync(join(root,'workspace'));
    const plugin=join(root,'plugin');mkdirSync(plugin);
    cpSync(new URL('../plugin/',import.meta.url),join(plugin,'ods'),{recursive:true});
    writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'blocked-diagnostic',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
    writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'blocked-diagnostic',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
    writeFileSync(join(plugin,'index.mjs'),`
      import {createToolLoopGuard,createRunAbortAdapter} from './ods/tool-loop-guard.mjs';
      import {abortAgentHarnessRun,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
      import {appendFileSync} from 'node:fs';
      const record=x=>appendFileSync(${JSON.stringify(join(root,'events.jsonl'))},JSON.stringify(x)+'\\n');
      let guard;
      const adapter=createRunAbortAdapter({resolveSessionId:resolveActiveEmbeddedRunSessionId,abort:abortAgentHarnessRun});
      export default {id:'blocked-diagnostic',register(api){
        guard??=createToolLoopGuard({abortRun:(...args)=>{const ok=adapter(...args);record({abort:ok});return ok;}});
        api.on('before_prompt_build',(e,c)=>guard.observeRun(c,'pixel',e));
        api.on('before_tool_call',(e,c)=>{const d=guard.beforeToolCall(e,c);record({tool:e.toolName,blocked:d?.block===true});return d;});
        api.on('after_tool_call',(e,c)=>guard.afterToolCall(e,c));
        api.on('before_agent_finalize',(e,c)=>{const d=guard.beforeAgentFinalize(e,c);record({final:d?.action});return d;});
        // This exec-only fixture never spawns children; satisfy the ingress's
        // authenticated delivery query before its existing verification query.
        api.registerHttpRoute({path:'/pixel-ods/subagent-delivery',auth:'gateway',match:'exact',handler:async(req,res)=>{
          let body='';for await(const part of req)body+=part;
          res.writeHead(200,{'Content-Type':'application/json'});
          res.end(JSON.stringify({schemaVersion:1,kind:'ods-subagent-delivery',runId:JSON.parse(body).runId,status:'not-delegated'}));return true;
        }});
        api.registerHttpRoute({path:'/pixel-ods/verification',auth:'gateway',match:'exact',handler:async(req,res)=>{
          let body='';for await(const part of req)body+=part;
          const value=guard.deliveryVerificationForRun(JSON.parse(body).runId);
          res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return true;
        }});
      }};
    `);
    const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
      gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
      agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,model:'fixture/test'}]},
      models:{mode:'replace',providers:{fixture:{baseUrl:'http://127.0.0.1:'+upstream.address().port+'/v1',api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']} ]}}},
      tools:{allow:['exec']},plugins:{allow:['blocked-diagnostic'],load:{paths:[plugin]},entries:{'blocked-diagnostic':{enabled:true,hooks:{allowConversationAccess:true}}}}};
    writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
    try{
      child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
        env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
      child.stdout.on('data',b=>log=(log+b).slice(-5000));child.stderr.on('data',b=>log=(log+b).slice(-5000));
      let ready=false;for(let i=0;i<250;i++){
        try{ready=(await fetch('http://127.0.0.1:'+port+'/health',{signal:AbortSignal.timeout(500)})).ok;}catch{}
        if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);
      }assert.ok(ready,log);
      ingress=createIngressServer({token:'fixture-only',gatewayPort:port});await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
      const response=await fetch('http://127.0.0.1:'+ingress.address().port+'/v1/chat/completions',{method:'POST',
        headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user:'diagnostic-fixture',messages:[{role:'user',content:'Analise as limitações do workspace e explique os recursos disponíveis.'}]}),signal:AbortSignal.timeout(45000)});
      const body=await response.text();
      const events=existsSync(join(root,'events.jsonl'))?readFileSync(join(root,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse):[];
      const trace=JSON.stringify({rounds,events})+'\n'+log;
      assert.equal(response.status,200,trace);
      assert.equal(rounds,3,trace);
      assert.equal(readFileSync(join(blockedPath,'preserve.txt'),'utf8'),'fixture-owned original');
      assert.match(JSON.stringify(requests[2].messages),/v\d+\.\d+\.\d+/);
      assert.match(JSON.stringify(requests[2].messages),/no tools/);
      assert.equal(events.filter(e=>e.final==='revise').length,0,trace);
      assert.deepEqual(events.filter(e=>e.tool==='exec').map(e=>e.blocked),finalMode==='tool'?[false,true,true]:[false,true],trace);
      const frames=body.split(/\r?\n/).filter(line=>line.startsWith('data: {')).map(line=>JSON.parse(line.slice(6)));
      const delivered=frames.map(f=>f.choices?.[0]?.delta?.content??'').join('');
      if(finalMode==='answer')assert.ok(delivered.includes(answer),delivered+'\n'+trace);
      else {
        assert.deepEqual(events.filter(e=>'abort' in e).map(e=>e.abort),[true],trace);
        assert.match(delivered,/v\d+\.\d+\.\d+/);
        assert.match(delivered,/código final não verifica cada subetapa/);
        assert.ok(!delivered.includes(answer),delivered);
      }
      assert.doesNotMatch(delivered,/Tudo foi concluído|preview|browser/);
      assert.equal(frames.at(-1).pixel_outcome.status,'failed',trace);
    }finally{
      if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
      if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
      upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));rmSync(root,{recursive:true,force:true});
    }
  });
