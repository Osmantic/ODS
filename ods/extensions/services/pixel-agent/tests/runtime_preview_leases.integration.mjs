// Real pinned gateway hooks with a recording broker/provider; no model inference.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
const pkg=process.env.OPENCLAW_PACKAGE;
for(const mode of ['direct','deferred'])test(`real SDK document lease admission: ${mode}`,{skip:!pkg,timeout:90000},async()=>{
  assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'))).version,'2026.6.33');
  const agentId='pixel',root=mkdtempSync(join(tmpdir(),'ods-document-sdk-')),workspace=join(root,'workspace');mkdirSync(workspace);
  let rounds=0,log='',child,toolResults=[];
  const binding={siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:800,height:600}};
  const leaseId='b'.repeat(32),generation='c'.repeat(32),ref='e'.repeat(32);
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    toolResults=JSON.parse(Buffer.concat(chunks).toString()).messages.filter(x=>x.role==='tool');
    const round=rounds++;
    const args=round===0?{mode:'snapshot',...binding}:round===1?{mode:'continue',leaseId,...binding,
      steps:[{action:'assert-visible',locator:{ref,documentGeneration:generation}}]}:
      {mode:'close',leaseId:round===2?'f'.repeat(32):leaseId,...binding};
    const transport=mode==='deferred'?'tool_call':'pixel_ods_workspace_preview_inspect';
    const parameters=mode==='deferred'?{id:'openclaw:pixel-ods:pixel_ods_workspace_preview_inspect',args}:args;
    const delta=round<4?{role:'assistant',tool_calls:[{index:0,id:'lease-'+round,type:'function',function:{name:transport,arguments:JSON.stringify(parameters)}}]}:{role:'assistant',content:'Checks finished.'};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:round<4?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'document-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',contracts:{tools:['pixel_ods_workspace_preview_inspect']},activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {appendFileSync} from 'node:fs';
    import {createPreviewDocumentLeases} from ${JSON.stringify(new URL('../plugin/preview-document-leases.mjs',import.meta.url).href)};
    import {createWorkspacePreviewInspectTool,INSPECTION_KIND,INSPECTION_SCOPE,inspectionPlanHash} from ${JSON.stringify(new URL('../plugin/workspace-preview-inspect.mjs',import.meta.url).href)};
    const leases=createPreviewDocumentLeases();
    const save=row=>appendFileSync(${JSON.stringify(join(root,'hooks.jsonl'))},JSON.stringify(row)+'\\n');
    const request=async value=>{
      save({hook:'broker',value});
      if(value.operation==='open')return {schemaVersion:2,kind:'ods-pixel-preview-snapshot',status:'snapshot',scope:value.scope,leaseId:'${leaseId}',containerId:'${'d'.repeat(64)}',siteId:value.siteId,sha256:value.sha256,viewport:value.viewport,documentGeneration:'${generation}',elements:[{ref:'${ref}',tag:'p',role:'',name:'Target'}],limit:128,boundedSnapshot:true,truncatedByBytes:false,descriptionsAreUntrusted:true,maximumLifetimeSeconds:120,idleSeconds:45};
      if(value.operation==='close')return {schemaVersion:2,kind:'ods-pixel-preview-lease',status:'closed',scope:value.scope,leaseId:value.leaseId};
      const p=value.request,state={count:1,visible:true,display:'block',visibility:'visible',opacity:'1',hidden:false,hiddenUntilFound:false,rectCount:1};
      return {schemaVersion:2,kind:'ods-pixel-preview-lease',status:'inspected',scope:value.scope,leaseId:value.leaseId,documentGeneration:'${generation}',result:{schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:p.siteId,sha256:p.sha256,planSha256:inspectionPlanHash(p),viewport:p.viewport,steps:p.steps.map((s,index)=>({index,...s,before:state,stable:true,status:'passed'})),diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE}};
    };
    export default {id:'pixel-ods',register(api){
      api.registerTool(context=>createWorkspacePreviewInspectTool({context,leases,request}));
      api.on('before_tool_call',(event,context)=>{leases.before(event,context);});
      api.on('after_tool_call',(event,context)=>leases.after(event,context));
      api.on('agent_end',(event,context)=>leases.finish(event,context));
    }};
  `);
  const sandbox={mode:'off'};
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only-0123456789abcdef'},http:{endpoints:{chatCompletions:{enabled:true}}}},agents:{defaults:{workspace,skipBootstrap:true,sandbox,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:agentId,default:true,workspace}]},models:{mode:'replace',providers:{fixture:{baseUrl:'http://127.0.0.1:'+upstream.address().port+'/v1',api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},tools:{allow:['pixel_ods_workspace_preview_inspect','tool_call','tool_search','tool_describe'],toolSearch:{enabled:mode==='deferred',mode:'tools'},exec:{host:'gateway',security:'full',ask:'off'}},plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try{
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    let ready=false;for(let n=0;n<250;n++){try{ready=(await fetch('http://127.0.0.1:'+port+'/health',{signal:AbortSignal.timeout(500)})).ok;}catch{}if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);}assert.ok(ready,log);
    const response=await fetch('http://127.0.0.1:'+port+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer fixture-only-0123456789abcdef'},body:JSON.stringify({model:'openclaw:'+agentId,stream:true,user:'preview-fixture',messages:[{role:'user',content:'Create and publish a website in a new workspace directory site. Add a button that toggles hidden details.'}]}),signal:AbortSignal.timeout(45000)});
    const body=await response.text();assert.equal(response.status,200,body+'\n'+log);assert.equal(rounds,5,log);

    const hooks=readFileSync(join(root,'hooks.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    const operations=hooks.filter(x=>x.hook==='broker').map(x=>x.value.operation);
    assert.deepEqual(operations,['open','inspect','close'],JSON.stringify(hooks));
    assert.equal(toolResults.length,4,JSON.stringify(toolResults));
    assert.match(JSON.stringify(toolResults[0]),/snapshot created/);
    assert.match(JSON.stringify(toolResults[1]),/inspection passed/);
    assert.match(JSON.stringify(toolResults[2]),/unavailable/);
    assert.match(JSON.stringify(toolResults[3]),/lease closed/);
    assert.doesNotMatch(JSON.stringify(toolResults),new RegExp('d'.repeat(64)));
    for(const row of hooks)assert.doesNotMatch(JSON.stringify(toolResults),new RegExp(row.value.scope));
    console.log(JSON.stringify({mode,operations,privateIdsHidden:true,wrongLeaseRejected:true}));
  }finally{
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
    rmSync(root,{recursive:true,force:true});
  }
});
