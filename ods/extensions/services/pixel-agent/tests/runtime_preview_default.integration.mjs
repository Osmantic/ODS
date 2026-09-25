// Actual pinned SDK preview dispatch; recording inspector and deterministic provider, no browser/model inference.
// The fixture seeds a validated publication receipt; it does not qualify browser rendering.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {preparePromptContextRuntime} from './prompt_context_runtime_fixture.mjs';
const installed=process.env.OPENCLAW_PACKAGE;
let pkg=installed, runtimeCopy;
if(installed) {
  runtimeCopy=mkdtempSync(join(tmpdir(),'ods-preview-reviewed-runtime-'));
  pkg=preparePromptContextRuntime(installed,runtimeCopy);
}
after(()=>{if(runtimeCopy)rmSync(runtimeCopy,{recursive:true,force:true});});
for(const mode of ['direct','deferred']) test(`real SDK preview default binding: ${mode}`,{skip:!pkg,timeout:90000},async()=>{
  assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'))).version,'2026.6.33');
  const agentId='pixel';
  const root=mkdtempSync(join(tmpdir(),'ods-preview-completion-'));
  const workspace=join(root,'workspace');mkdirSync(workspace);
  let rounds=0,log='',child,toolResults=[],providerRequests=[];
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    toolResults=JSON.parse(Buffer.concat(chunks).toString()).messages.filter(x=>x.role==='tool');
    providerRequests.push(structuredClone(toolResults));
    const round=rounds++;
    const args={viewport:{width:800,height:600},steps:[{action:'assert-hidden',locator:{selector:'#details'}},{action:'click',locator:{role:'button',name:'Show details',exact:true}},{action:'assert-visible',locator:{selector:'#details'}}],...(round===1?{siteId:'site-'+'a'.repeat(24)}:{})};
    const transport=mode==='deferred'?'tool_call':'pixel_ods_workspace_preview_inspect';
    const parameters=mode==='deferred'?{id:'openclaw:pixel-ods:pixel_ods_workspace_preview_inspect',args}:args;
    const delta=round<3?{role:'assistant',tool_calls:[{index:0,id:'preview-'+round,type:'function',function:{name:transport,arguments:JSON.stringify(parameters)}}]}:{role:'assistant',content:'Both diagnostics finished.'};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:round<3?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'preview-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',contracts:{tools:['pixel_ods_workspace_preview_inspect']},activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import assert from 'node:assert/strict';
    import {createHash} from 'node:crypto';
    import {createToolLoopGuard} from ${JSON.stringify(new URL('../plugin/tool-loop-guard.mjs',import.meta.url).href)};
    import {PREVIEW_INSPECTION_TOOL} from ${JSON.stringify(new URL('../plugin/preview-interaction-assurance.mjs',import.meta.url).href)};
    import {createWorkspacePreviewInspectTool,INSPECTION_KIND,INSPECTION_SCOPE,inspectionPlanHash,normalizeWorkspacePreviewInspectionParams} from ${JSON.stringify(new URL('../plugin/workspace-preview-inspect.mjs',import.meta.url).href)};
    import {appendFileSync} from 'node:fs';
    import {requireDurableTurnContext} from ${JSON.stringify(new URL('../plugin/prompt-contract.mjs',import.meta.url).href)};
    const owner='Create and publish a website in a new workspace directory site. Add a button that toggles hidden details.';
    let context,guard,preview,retired=false;
    const save=row=>appendFileSync(${JSON.stringify(join(root,'hooks.jsonl'))},JSON.stringify(row)+'\\n');
    function call(guard,name,params,id,result,runContext=context) {
  const ctx={...runContext,toolName:name,toolCallId:id};
  const event={toolName:name,runId:runContext.runId,toolCallId:id,params};
  const prepared=guard.beforeToolCall(event,ctx);
  assert.notEqual(prepared?.block,true,prepared?.blockReason);
  event.params=prepared?.params??params;
  if(result)guard.afterToolCall({...event,result},ctx);
  return {event,ctx};
}
function setup({enabled=true,prompt=owner,automatic=false,verifyWorkspacePreview}={}) {
  const guard=createToolLoopGuard({workspacePreviewInspectionAvailable:enabled,
    verifyWorkspacePreview,
    ...(automatic ? {publishWorkspacePreview:async()=>({details:preview})} : {})});
  guard.observeRun(context,'pixel',{prompt});
  const content='<!doctype html><button>Show details</button><p hidden>Details</p>';
  const write=call(guard,'write',{path:'site/index.html',content},'write',{content:[{type:'text',text:'Successfully wrote file.'}]}).event.params;
  guard.toolResultPersist({toolName:'write',toolCallId:'write',message:{role:'toolResult',toolName:'write',toolCallId:'write',content:[{type:'text',text:'Successfully wrote file.'}]}},{...context,toolName:'write',toolCallId:'write'});
  const dir=write.path.replace(/\\/index.html$/,'');
  const name=Buffer.from('index.html'),data=Buffer.from(content),a=Buffer.alloc(4),b=Buffer.alloc(8);
  a.writeUInt32BE(name.length);b.writeBigUInt64BE(BigInt(data.length));
  const sha256=createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId='site-'+sha256.slice(0,24);
  const preview={schemaVersion:1,kind:'ods-pixel-workspace-preview',status:'succeeded',relativeDirectory:dir,
    siteId,sha256,entryFile:'index.html',entrySha256:createHash('sha256').update(data).digest('hex'),files:1,bytes:data.length,
    port:9437,url:\`http://\${siteId}.localhost:9437/\${siteId}/\`,httpStatus:200,readbackVerified:true,executable:false,overwritten:false};
  if(!automatic) call(guard,'pixel_ods_workspace_preview',{relativeDirectory:dir},'publish',{details:preview});
  return {guard,preview};
}
function plan(preview) { return {siteId:preview.siteId,sha256:preview.sha256,viewport:{width:800,height:600},steps:[
  {action:'assert-hidden',locator:{selector:'#details'}},
  {action:'click',locator:{role:'button',name:'Show details',exact:true}},
  {action:'assert-visible',locator:{selector:'#details'}},
]}; }
function receipt(params) {
  const request=normalizeWorkspacePreviewInspectionParams(params);
  const state=visible=>({count:1,visible,display:visible?'block':'none',visibility:'visible',opacity:'1',hidden:!visible,hiddenUntilFound:false,rectCount:visible?1:0});
  return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:params.siteId,sha256:params.sha256,
    planSha256:inspectionPlanHash(request),viewport:params.viewport,steps:params.steps.map((s,index)=>({index,...s,
      before:state(s.action!=='assert-hidden'),...(s.action==='click'?{after:state(true)}:{}),stable:true,status:'passed'})),
    diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE};
}

    export default {id:'pixel-ods',register(api){
      api.registerTool(createWorkspacePreviewInspectTool({request:async params=>{save({hook:'runner',params});const {schemaVersion,action,...plan}=params;return receipt(plan);}}));
      api.on('before_agent_run',(_event,ctx)=>requireDurableTurnContext(ctx,'pixel'));
      api.on('before_prompt_build',(event,ctx)=>{
        if(!guard){context=ctx;({guard,preview}=setup());save({hook:'seed',siteId:preview.siteId,sha256:preview.sha256});}else guard.observeRun(ctx,'pixel',event);
        const admission=ctx.odsTurnContextAdmission;
        return {odsTurnContext:{...admission,content:admission.previous?.content ?? 'Preview default binding fixture.'}};
      });
      api.on('before_tool_call',(event,ctx)=>{if(!retired&&ctx.toolCallId==='preview-2'){retired=true;guard.observeRun({...ctx,runId:'replacement-owner-run'},'pixel',{prompt:'Inspect this website.'});}const result=guard.beforeToolCall(event,ctx);save({hook:'before',id:ctx.toolCallId,params:result?.params??event.params,block:result?.block});return result;});
      api.on('after_tool_call',(event,ctx)=>{const result=guard.afterToolCall(event,ctx);save({hook:'after',id:ctx.toolCallId,params:event.params,status:event.result?.details?.status,verification:guard.verificationForRun(ctx.runId)?.status});return result;});
      api.on('tool_result_persist',(event,ctx)=>guard.toolResultPersist(event,ctx));
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
    const body=await response.text();assert.equal(response.status,200,body+'\n'+log);assert.equal(rounds,4,log);
    const hooks=readFileSync(join(root,'hooks.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
    const runners=hooks.filter(x=>x.hook==='runner'),seed=hooks.find(x=>x.hook==='seed');
    assert.equal(runners.length,1,JSON.stringify(hooks));assert.equal(runners[0].params.siteId,seed.siteId);assert.equal(runners[0].params.sha256,seed.sha256);
    assert.ok(hooks.some(x=>x.hook==='after'&&x.id==='preview-0'&&x.verification==='passed'),JSON.stringify(hooks));
    assert.equal(toolResults.length,3,JSON.stringify(toolResults));
    assert.match(JSON.stringify(toolResults[0]),/inspection passed/);
    assert.match(JSON.stringify(toolResults[1]),/invalid arguments/);
    assert.match(JSON.stringify(toolResults[2]),/invalid arguments/);
    console.log(JSON.stringify({mode,actualRunnerCalls:runners.length,normalizedBinding:true,partialRejected:true,staleRejected:true,receiptAccepted:true}));
  }finally{
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
    rmSync(root,{recursive:true,force:true});
  }
});
