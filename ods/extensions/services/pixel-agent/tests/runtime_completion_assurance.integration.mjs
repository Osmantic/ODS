// Exercise the real pinned harness with a deliberately unreliable fake model.
// No production configuration, real search service or workspace is used.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,copyFileSync,symlinkSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
const pkg=process.env.OPENCLAW_PACKAGE;
test('real harness recovers a premature stop and retains safe delivery when further revision is refused', {skip:!pkg,timeout:90000},async()=>{
  const root=mkdtempSync(join(tmpdir(),'ods-completion-fixture-'));
  let rounds=0,log='',child;
  const upstream=createServer(async(req,res)=>{
    for await(const _ of req) { /* consume */ }
    const round=rounds++;
    const delta=round===1 ? {role:'assistant',tool_calls:[{index:0,id:'read-source',type:'function',function:{name:'fixture_source',arguments:'{}'}}]}
      : {role:'assistant',content:round===0?'Vou pesquisar agora.':round===2?'O resultado foi encontrado.':'Resultado obtido: [fonte](https://example.org/news).'};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:round===1?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  copyFileSync(new URL('../plugin/completion-assurance.mjs',import.meta.url),join(plugin,'completion-assurance.mjs'));
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'completion-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'completion-fixture',contracts:{tools:['fixture_source']},toolMetadata:{fixture_source:{replaySafe:true}},activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createCompletionAssurance} from './completion-assurance.mjs';
    import {appendFileSync} from 'node:fs';
    const guard=createCompletionAssurance();
    export default {id:'completion-fixture',register(api){
      api.on('before_prompt_build',()=>guard.begin('notícias de hoje'));
      api.on('before_agent_finalize',(event)=>{
        const result=guard.finalize(event.lastAssistantMessage ?? '');
        appendFileSync(${JSON.stringify(join(root,'hooks.jsonl'))},JSON.stringify({text:event.lastAssistantMessage,result,terminal:guard.terminal})+'\\n');
        return result;
      });
      api.registerTool({name:'fixture_source',description:'Read the fixture source.',parameters:{type:'object',properties:{}},
        async execute(){const result={content:[{type:'text',text:JSON.stringify({results:[{url:'https://example.org/news',title:'Fixture',description:'A test result.'}]})}]};guard.observe('web_search',{result});return result;}});
    }};
  `);
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},
    gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
    agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true}]},
    models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
    tools:{allow:['fixture_source']},plugins:{allow:['completion-fixture'],load:{paths:[plugin]},entries:{'completion-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try {
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,
      env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;
    for(let n=0;n<200;n++){
      try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok;}catch{}
      if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);
    }
    assert.ok(ready,log);
    const response=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},
      body:JSON.stringify({model:'openclaw:pixel',stream:false,user:'completion-fixture',messages:[{role:'user',content:'Pesquise notícias de hoje.'}]}),signal:AbortSignal.timeout(45000)});
    const body=await response.text();assert.equal(response.status,200,body+'\n'+log);
    const hooks=readFileSync(join(root,'hooks.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    // This pinned harness conservatively classifies extension execution as a
    // possible effect and refuses another pass. Never relax that boundary to
    // make a citation repair succeed; ingress must use the armed safe output.
    assert.equal(rounds,3);
    assert.deepEqual(hooks.map(x=>x.result?.action),['revise','revise']);
    assert.match(log,/revision after potential side effects/);
    assert.match(hooks.at(-1).terminal,/https:\/\/example.org\/news/);
    assert.match(hooks.at(-1).terminal,/O resultado foi encontrado/);
    assert.match(hooks.at(-1).terminal,/Fontes retornadas pela pesquisa/);
  } finally {
    if(child && child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null)process.kill(-child.pid,'SIGKILL');}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
  }
});
