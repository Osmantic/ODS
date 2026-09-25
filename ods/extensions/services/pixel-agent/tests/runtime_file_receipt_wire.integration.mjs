// Composite-only recording-provider fixture. No model inference or live runtime edits.
// Requires root-integrated prompt_context_runtime_fixture.mjs and all four receipt recipes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
const installed=process.env.OPENCLAW_PACKAGE;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const recipes=[['openclaw-compaction-resume.json','sessions-CZbwb3_c.js'],['openclaw-read-range.json','openclaw-tools-iHHy99PD.js'],['openclaw-file-operations.json','agent-tools-D1DOpg6D.js'],['openclaw-file-identity.json','sandbox-Y3MbG9Od.js']];
for(const mode of ['direct','deferred']) test(`composite actual provider-visible file receipts: ${mode}`,{skip:!installed,timeout:120000},async()=>{
 const {preparePromptContextRuntime}=await import('./prompt_context_runtime_fixture.mjs');
 const root=mkdtempSync(join(tmpdir(),'ods-file-wire-')),workspace=join(root,'workspace');mkdirSync(workspace);
 const pkg=preparePromptContextRuntime(installed,root),custody=[];
 for(const [name,module] of recipes){
  const manifest=JSON.parse(readFileSync(new URL('../host/'+name,import.meta.url))),file=join(pkg,'dist',module);
  let text=readFileSync(file,'utf8');const before=sha(text);
  if(before!==manifest.sourceSha256){const prior=before===manifest.patchedSha256?manifest.replacements:manifest.previousReplacements?.[before];assert(prior,'known exact receipt predecessor');for(const [a,b]of [...prior].reverse()){assert.equal(text.split(b).length,2);text=text.replace(b,()=>a);}}
  assert.equal(sha(text),manifest.sourceSha256);
  for(const [a,b]of manifest.replacements){assert.equal(text.split(a).length,2);text=text.replace(a,()=>b);}
  assert.equal(sha(text),manifest.patchedSha256);writeFileSync(file,text);custody.push({module,before,after:sha(text)});
 }
 assert.match(readFileSync(join(pkg,'dist/selection-BEwSQKM-.js'),'utf8'),/odsFileReceiptContext\?\.updateVisible/,'requires actual composed selection callback');
 const sourceFile=join(workspace,'a.txt');writeFileSync(sourceFile,'alpha\nbeta\n');
 let child,log='',plan=[],cursor=0,requests=[],lastRequests=[];
 const upstream=createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const body=JSON.parse(Buffer.concat(chunks));requests.push(body);lastRequests.push(body);
  const step=plan[cursor++];const call=step?{index:0,id:step.id,type:'function',function:{name:mode==='deferred'?'tool_call':step.name,arguments:JSON.stringify(mode==='deferred'?{id:'openclaw:core:'+step.name,args:step.args}:step.args)}}:null;
  const delta=call?{role:'assistant',tool_calls:[call]}:{role:'assistant',content:'Fixture complete.'};
  res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({id:'file-wire',choices:[{index:0,delta,finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'file-wire',choices:[{index:0,delta:{},finish_reason:call?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
 });
 await new Promise(r=>upstream.listen(0,'127.0.0.1',r));const probe=createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
 const plugin=join(root,'plugin');mkdirSync(plugin);
 writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'pixel-ods',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
 writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
 writeFileSync(join(plugin,'index.mjs'),`import {createToolLoopGuard} from ${JSON.stringify(new URL('../plugin/tool-loop-guard.mjs',import.meta.url).href)};
 const guard=createToolLoopGuard({fileVersionAdmissionAvailable:true});
 export default{id:'pixel-ods',register(api){
 api.on('before_prompt_build',(event,ctx)=>guard.observeRun(ctx,'pixel',event,{workspaceRoot:${JSON.stringify(workspace)}}));
 api.on('before_tool_call',(event,ctx)=>guard.beforeToolCall(event,ctx));
 api.on('after_tool_call',(event,ctx)=>guard.afterToolCall(event,ctx));
 api.on('tool_result_persist',(event,ctx)=>guard.toolResultPersist(event,ctx));
 }};`);
 const token='fixture-only-0123456789abcdef';
 const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token},http:{endpoints:{chatCompletions:{enabled:true}}}},agents:{defaults:{workspace,skipBootstrap:true,sandbox:{mode:'off'},model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:1024,reasoning:false,input:['text']}]}}},tools:{allow:['read','write','edit','tool_call','tool_search','tool_describe'],fs:{workspaceOnly:true},toolSearch:{enabled:mode==='deferred',mode:'tools'}},plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}}};
 writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
 async function start(){child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});child.stdout.on('data',c=>log+=c);child.stderr.on('data',c=>log+=c);for(let i=0;i<250;i++){try{if((await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok)return;}catch{}assert.equal(child.exitCode,null,log);await delay(100);}throw Error(log);}
 async function stop(){if(child&&child.exitCode===null){const done=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([done,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await done;}}}
 async function turn(steps,user='file-wire-session') {plan=steps;cursor=0;lastRequests=[];const response=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({model:'openclaw:pixel',stream:true,user,messages:[{role:'user',content:'Execute the requested bounded file diagnostic; report actual results.'}]}),signal:AbortSignal.timeout(45000)});const text=await response.text();assert.equal(response.status,200,text+'\n'+log);assert.equal(cursor,steps.length+1,log);return lastRequests;}
 const edit=(id,oldText,newText)=>({id,name:'edit',args:{path:'a.txt',edits:[{oldText,newText}]}});
 function observed(request,id){const message=request.messages.find(m=>m.role==='tool'&&m.tool_call_id===id);assert(message,'immediate exact call '+id);return typeof message.content==='string'?message.content:JSON.stringify(message.content);}
 try {
  await start();let rows=await turn([{id:'read-current',name:'read',args:{path:'a.txt'}},edit('edit-current','alpha','ALPHA')]);
  assert.match(observed(rows[1],'read-current'),/1\|alpha/);assert.match(observed(rows[2],'edit-current'),/1\|ALPHA/);assert.equal(readFileSync(sourceFile,'utf8'),'ALPHA\nbeta\n');
  await stop();await start();rows=await turn([edit('edit-next-turn','ALPHA','REUSED')]);assert.match(observed(rows[1],'edit-next-turn'),/REUSED/);assert.equal(readFileSync(sourceFile,'utf8'),'REUSED\nbeta\n');
  writeFileSync(sourceFile,'external\nbeta\n');rows=await turn([edit('edit-stale','REUSED','WRONG')]);assert.match(observed(rows[1],'edit-stale'),/changed|no longer visible/i);assert.equal(readFileSync(sourceFile,'utf8'),'external\nbeta\n');
  rows=await turn([edit('edit-foreign','external','WRONG')],'fresh-session');assert.match(observed(rows[1],'edit-foreign'),/changed|no longer visible/i);assert.equal(readFileSync(sourceFile,'utf8'),'external\nbeta\n');
  await stop();const sessionDir=join(root,'state/agents/pixel/sessions');const files=readdirSync(sessionDir).filter(n=>n.endsWith('.jsonl')&&!n.includes('.trajectory.'));const session=files.find(n=>readFileSync(join(sessionDir,n),'utf8').includes('read-current'));assert(session,'owned session file');
  const module=readdirSync(join(pkg,'dist')).find(n=>/^session-manager-.*\.js$/.test(n));const {t:SessionManager}=await import(pathToFileURL(join(pkg,'dist',module)));const manager=SessionManager.open(join(sessionDir,session));manager.appendCompaction('The old file contents are intentionally omitted from this summary.',manager.getLeafId(),1000);
  await start();rows=await turn([edit('edit-compacted','external','WRONG'),{id:'read-refresh',name:'read',args:{path:'a.txt'}},edit('edit-refreshed','external','REFRESHED')]);assert.match(observed(rows[1],'edit-compacted'),/changed|no longer visible/i);assert.match(observed(rows[2],'read-refresh'),/1\|external/);assert.match(observed(rows[3],'edit-refreshed'),/1\|REFRESHED/);assert.equal(readFileSync(sourceFile,'utf8'),'REFRESHED\nbeta\n');
  console.log(JSON.stringify({mode,passed:true,providerRequests:requests.length,custody,limitations:['Deterministic recording provider, no inference. Real SessionManager compaction with supplied summary. Final-provider body truncation, page union, sandbox and unrelated-agent matrix remain separate acceptance cases.']}));
 } finally {await stop();upstream.closeAllConnections();await new Promise(r=>upstream.close(r));rmSync(root,{recursive:true,force:true});}
});
