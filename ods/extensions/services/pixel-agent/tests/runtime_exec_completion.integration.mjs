// Actual pinned SDK hook lifecycle; deterministic provider, no model inference.
import test,{after} from 'node:test';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,symlinkSync,readFileSync,rmSync,copyFileSync,chmodSync,cpSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createLoopbackGatewayFetch} from '../host/pixel_ingress.mjs';
const installed=process.env.OPENCLAW_PACKAGE;
const guardModule=process.env.ODS_PROJECT_DELIVERY_GUARD_MODULE ?? new URL('../plugin/tool-loop-guard.mjs',import.meta.url).href;
let pkg=installed, runtimeCopy;
if(installed && process.env.ODS_EXEC_RECEIPT_RED!=='1') {
  const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-compaction-budget.json',import.meta.url)));
  const hash=text=>createHash('sha256').update(text).digest('hex');
  let source=readFileSync(join(installed,'dist/selection-BEwSQKM-.js'),'utf8');
  const prior=hash(source)===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements[hash(source)];
  if(prior)for(const [before,value] of [...prior].reverse()){assert.equal(source.split(value).length,2);source=source.replace(value,before);}
  assert.equal(hash(source),manifest.sourceSha256,'refuse unknown SDK source');
  for(const [before,value] of manifest.replacements){assert.equal(source.split(before).length,2);source=source.replace(before,value);}
  assert.equal(hash(source),manifest.patchedSha256);
  runtimeCopy=mkdtempSync(join(tmpdir(),'ods-exec-reviewed-runtime-'));pkg=join(runtimeCopy,'package');
  cpSync(installed,pkg,{recursive:true});writeFileSync(join(pkg,'dist/selection-BEwSQKM-.js'),source);
}
after(()=>{if(runtimeCopy)rmSync(runtimeCopy,{recursive:true,force:true});});
const image=process.env.OPENCLAW_TEST_SANDBOX_IMAGE;
for(const mode of ['plain','wrapped','sandbox','deferred','unrelated','write','write-deferred','write-unrelated']) test(`real SDK completed exec guidance: ${mode}`,{skip:!pkg||(mode==='sandbox'&&!image),timeout:90000},async()=>{
  assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'))).version,'2026.6.33');
  const writeMode=mode.startsWith('write'),unrelated=mode==='unrelated'||mode==='write-unrelated';
  const deferred=mode==='deferred'||mode==='write-deferred';
  const agentId=unrelated?'other':'pixel';
  const fileContent='Maya: quote Thursday\nLuis: testing Friday\nCSV: owner and date unassigned\n';
  const filePrompt='Create a plain text file named fleet-check.txt containing exactly these three lines, with a newline after the last line:\n'+fileContent+'Give me a download link to the file.';
  const root=mkdtempSync(join(tmpdir(),'ods-exec-completion-'));
  const workspace=join(root,'workspace');mkdirSync(workspace);
  writeFileSync(join(workspace,'broken.py'),'print(1)\\nprint(2)\n');
  mkdirSync(join(workspace,'.openclaw','sandbox-skills'),{recursive:true});
  const controls=join(workspace,'.control');mkdirSync(controls,{mode:0o700});
  copyFileSync(new URL('../host/cancellable-exec.sh',import.meta.url),join(controls,'cancellable-exec.sh'));
  chmodSync(join(controls,'cancellable-exec.sh'),0o500);
  const prefix='ods-exec-fixture-'+root.split('-').at(-1).toLowerCase()+'-';
  let rounds=0,log='',child,childStartTicks,toolResults=[],providerRequests=[];
  const startTicks=pid=>process.platform==='linux'?readFileSync(`/proc/${pid}/stat`,'utf8').split(') ').at(-1).split(' ')[19]:undefined;
  const providerOwner={pid:process.pid,startTicks:startTicks(process.pid)};
  const upstream=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    toolResults=JSON.parse(Buffer.concat(chunks).toString()).messages.filter(x=>x.role==='tool');
    providerRequests.push(structuredClone(toolResults));
    const round=rounds++;
    const args=writeMode?{path:'fleet-check.txt',content:fileContent}:{command:round===0?'printf sdk-completed':round===1?'printf sdk-failed; exit 1':'python3 broken.py',workdir:mode==='sandbox'?'/workspace':workspace};
    const selected=writeMode?'write':'exec';
    const transport=deferred?'tool_call':selected;
    const parameters=deferred?{id:'openclaw:core:'+selected,args}:args;
    const call=round<(writeMode?1:3);
    const delta=call?{role:'assistant',tool_calls:[{index:0,id:selected+'-'+round,type:'function',function:{name:transport,arguments:JSON.stringify(parameters)}}]}:{role:'assistant',content:writeMode?'File saved.':'Both diagnostics finished.'};
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:null}]})+'\n\n');
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:call?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
  const plugin=join(root,'plugin');mkdirSync(plugin);
  writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'exec-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
  writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
  writeFileSync(join(plugin,'index.mjs'),`
    import {createToolLoopGuard,createExecCancellationControl} from ${JSON.stringify(guardModule)};
    import {appendFileSync} from 'node:fs';
    const expected=new Map();
    const guard=createToolLoopGuard(${mode==='plain'||writeMode?'{}':`{execControl:createExecCancellationControl({root:${JSON.stringify(controls)},executionHost:${JSON.stringify(mode==='sandbox'?'sandbox':'gateway')}})}`});
    const save=row=>appendFileSync(${JSON.stringify(join(root,'hooks.jsonl'))},JSON.stringify(row)+'\\n');
    export default {id:'pixel-ods',register(api){
      api.on('before_prompt_build',(event,ctx)=>guard.observeRun(ctx,'pixel',event,{workspaceRoot:${JSON.stringify(workspace)}}));
      api.on('before_tool_call',(event,ctx)=>{const result=guard.beforeToolCall(event,ctx);expected.set(ctx.toolCallId,{original:event.params,executed:result?.params??event.params});return result;});
      api.on('after_tool_call',(event,ctx)=>{const p=expected.get(ctx.toolCallId);save({hook:'after',tool:event.toolName,error:!!event.error,status:event.result?.details?.status,exitCode:event.result?.details?.exitCode,original:JSON.stringify(p?.original)===JSON.stringify(event.params),executed:JSON.stringify(p?.executed)===JSON.stringify(event.params),paramKeys:Object.keys(event.params??{}),changed:Object.keys(event.params??{}).filter(k=>JSON.stringify(event.params[k])!==JSON.stringify(p?.executed?.[k])).map(k=>({key:k,actual:event.params[k],expected:p?.executed?.[k]}))});return guard.afterToolCall(event,ctx);});
      api.on('tool_result_persist',(event,ctx)=>{const result=guard.toolResultPersist(event,ctx);
        const output=${unrelated}?{message:{...event.message,content:[...event.message.content,{type:'text',text:'[Fixture storage-only annotation]'}]}}:result;
        save({hook:'persist',guidance:String(JSON.stringify(output?.message?.content)).includes('[ODS Pixel execution]'),artifactGuidance:String(JSON.stringify(output?.message?.content)).includes('exact publication args'),storageOnly:${unrelated}});return output;});
    }};
  `);
  const sandbox=mode==='sandbox'?{mode:'all',scope:'session',workspaceAccess:'rw',docker:{image,containerPrefix:prefix,network:'none',readOnlyRoot:true,user:String(process.getuid())+':'+process.getgid(),capDrop:['ALL'],pidsLimit:64,memory:'256m',cpus:1,binds:[controls+':/run/pixel-ods-control:ro']}}:{mode:'off'};
  const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only-0123456789abcdef'},http:{endpoints:{chatCompletions:{enabled:true}}}},agents:{defaults:{workspace,skipBootstrap:true,sandbox,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:agentId,default:true,workspace}]},models:{mode:'replace',providers:{fixture:{baseUrl:'http://127.0.0.1:'+upstream.address().port+'/v1',api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},tools:{allow:[writeMode?'write':'exec','tool_call','tool_search','tool_describe'],toolSearch:{enabled:deferred,mode:'tools'},exec:{host:mode==='sandbox'?'sandbox':'gateway',security:'full',ask:'off'}},plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}}};
  writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
  try{
    child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
    childStartTicks=startTicks(child.pid);
    child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
    const gatewayTransport=createLoopbackGatewayFetch();
    const gatewayFetch=async(...args)=>{
      const response=await gatewayTransport(...args);
      return new Response(response.body,{status:response.status});
    };
    let ready=false;for(let n=0;n<250;n++){try{ready=(await gatewayFetch('http://127.0.0.1:'+port+'/health',{signal:AbortSignal.timeout(1500)})).ok;}catch{}if(ready)break;assert.equal(child.exitCode,null,log);await delay(100);}assert.ok(ready,log);
    const response=await gatewayFetch('http://127.0.0.1:'+port+'/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer fixture-only-0123456789abcdef'},body:JSON.stringify({model:'openclaw:'+agentId,stream:true,user:writeMode?'ods-'+'a'.repeat(64):'exec-fixture',messages:[{role:'user',content:writeMode?filePrompt:'Run the three shell diagnostics and report their actual completion status.'}]}),signal:AbortSignal.timeout(45000)});
    const body=await response.text();assert.equal(response.status,200,body+'\n'+log);assert.equal(rounds,writeMode?2:4,log);
    const hooks=readFileSync(join(root,'hooks.jsonl'),'utf8').trim().split('\n').map(JSON.parse);console.log(JSON.stringify({mode,hooks,wire:toolResults.map(x=>({toolCallId:x.tool_call_id,completion:JSON.stringify(x.content).includes('[ODS Pixel execution]'),artifactGuidance:JSON.stringify(x.content).includes('exact publication args')}))}));
    if(writeMode) {
      assert.equal(readFileSync(join(workspace,'fleet-check.txt'),'utf8'),fileContent);
      assert.equal(providerRequests.length,2,'one write and its immediate next provider request, no retry');
      assert.equal(providerRequests[1].length,1);
      const text=JSON.stringify(providerRequests[1][0].content);
      assert.equal(hooks.filter(x=>x.hook==='persist').length,1,'write result persisted once');
      if(unrelated) {
        assert.equal(hooks.filter(x=>x.storageOnly).length,1,'negative control transformed the stored result');
        assert.doesNotMatch(text,/exact publication args|Fixture storage-only annotation/);
      } else {
        assert.equal(hooks.filter(x=>x.artifactGuidance).length,1);
        assert.match(text,/pixel_ods_workspace_artifact/);
        assert.match(text,/relativePath.*fleet-check\.txt/);
        assert.equal((text.match(/exact publication args/g)??[]).length,1);
      }
      console.log(JSON.stringify({mode,providerRequests:2,writeBytes:Buffer.byteLength(fileContent),immediateArtifactGuidance:!unrelated,storageOnlyExcluded:unrelated}));
      return;
    }
    assert.equal(toolResults.length,3,JSON.stringify(toolResults));
    assert.equal(hooks.filter(x=>x.hook==='persist').length,3,'each tool result persisted exactly once');
    assert.equal(providerRequests.length,4);
    for(let i=0;i<3;i++) {
      const immediate=providerRequests[i+1];assert.equal(immediate.length,i+1);
      const text=JSON.stringify(immediate[i].content);
      if(mode==='unrelated') {
        assert.doesNotMatch(text,/\[ODS Pixel|Fixture storage-only annotation/,'other agents retain storage-only hook semantics');
      } else if(i<2) {
        assert.match(text,new RegExp('Exec returned completed with exit code '+i),JSON.stringify({mode,hooks,immediate}));
        assert.equal((text.match(/\[ODS Pixel execution\]/g)??[]).length,1,'feedback is not duplicated');
      } else assert.match(text,/\[ODS Pixel Python syntax\]/,'specific repair advice is immediate');
    }
    if(mode==='unrelated')assert.equal(hooks.filter(x=>x.storageOnly).length,3,'negative control actually transformed persisted results');
    assert.match(JSON.stringify(toolResults[1].content),/sdk-failed/,'nonzero process evidence remains visible');
    assert.match(JSON.stringify(toolResults[2].content),/SyntaxError/,'actual syntax failure remains visible');
  }finally{
    if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}
    upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));
    let gatewayGroupGone=false;
    try{process.kill(-child.pid,0);}catch(error){if(error.code!=='ESRCH')throw error;gatewayGroupGone=true;}
    console.log(JSON.stringify({mode,settlement:{providerOwner,providerClosed:!upstream.listening,
      gateway:{pid:child.pid,startTicks:childStartTicks,exitCode:child.exitCode,signalCode:child.signalCode,groupGone:gatewayGroupGone}}}));
    assert.ok(gatewayGroupGone,'only this fixture gateway process group must be gone');
    if(mode==='sandbox'){const names=spawnSync('docker',['ps','-aq','--filter','name=^'+prefix],{encoding:'utf8'});assert.equal(names.status,0);for(const id of names.stdout.trim().split(/\s+/).filter(Boolean))assert.equal(spawnSync('docker',['rm','-f',id]).status,0);}
    rmSync(root,{recursive:true,force:true});
  }
});
