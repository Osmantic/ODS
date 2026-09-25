// Composite-only recording-provider fixture. No model inference or live runtime edits.
// Requires root-integrated prompt_context_runtime_fixture.mjs and all four receipt recipes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync,readdirSync,renameSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
const installed=process.env.OPENCLAW_PACKAGE;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const recipes=[['openclaw-compaction-resume.json','sessions-CZbwb3_c.js'],['openclaw-read-range.json','openclaw-tools-iHHy99PD.js'],['openclaw-file-operations.json','agent-tools-D1DOpg6D.js'],['openclaw-file-identity.json','sandbox-Y3MbG9Od.js']];
const image=process.env.OPENCLAW_TEST_SANDBOX_IMAGE;
for(const mode of ['direct','deferred','sandbox','sandbox-deferred','unrelated','truncated','truncated-deferred']) test(`composite actual provider-visible file receipts: ${mode}`,{skip:!installed||(mode.startsWith('sandbox')&&!image),timeout:120000},async()=>{
 const deferred=mode.includes('deferred'),sandbox=mode.startsWith('sandbox'),agentId=mode==='unrelated'?'other':'pixel',truncated=mode.startsWith('truncated');
 const {preparePromptContextRuntime}=await import('./prompt_context_runtime_fixture.mjs');
 const root=mkdtempSync(join(tmpdir(),'ods-file-wire-')),workspace=join(root,'workspace');mkdirSync(workspace);
 const containerPrefix='ods-file-wire-'+root.split('-').at(-1).toLowerCase()+'-';
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
  const step=plan[cursor++];const call=step?{index:0,id:step.id,type:'function',function:{name:deferred?'tool_call':step.name,arguments:JSON.stringify(deferred?{id:'openclaw:core:'+step.name,args:step.args}:step.args)}}:null;
  const delta=call?{role:'assistant',tool_calls:[call]}:{role:'assistant',content:'Fixture complete.'};
  res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({id:'file-wire',choices:[{index:0,delta,finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'file-wire',choices:[{index:0,delta:{},finish_reason:call?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
 });
 await new Promise(r=>upstream.listen(0,'127.0.0.1',r));const probe=createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
 const plugin=join(root,'plugin');mkdirSync(plugin);
 writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'pixel-ods',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
 writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
 writeFileSync(join(plugin,'index.mjs'),`import {createToolLoopGuard} from ${JSON.stringify(new URL('../plugin/tool-loop-guard.mjs',import.meta.url).href)};
 import {appendFileSync} from 'node:fs';
 import {executionPolicy,executionClock} from ${JSON.stringify(new URL('../plugin/completion-assurance.mjs',import.meta.url).href)};
 import {promptContractForAgent,requireDurableTurnContext} from ${JSON.stringify(new URL('../plugin/prompt-contract.mjs',import.meta.url).href)};
 const guard=createToolLoopGuard({fileVersionAdmissionAvailable:true});
 export default{id:'pixel-ods',register(api){
 api.on('before_agent_run',(_event,ctx)=>${agentId==='pixel'}?requireDurableTurnContext(ctx,'pixel'):undefined);
 api.on('before_prompt_build',(event,ctx)=>{
   appendFileSync(${JSON.stringify(join(root,'prompt-receipts.jsonl'))},JSON.stringify({ctx:{sessionKey:ctx.sessionKey,runId:ctx.runId},tools:event.messages?.filter(m=>m.role==='toolResult')})+'\\n');
   guard.observeRun(ctx,'pixel',event,{workspaceRoot:${JSON.stringify(workspace)}});
   if(${agentId!=='pixel'})return {};
   const admission=ctx.odsTurnContextAdmission;
   const policy=promptContractForAgent(ctx,'pixel',event,{stableContext:true,executionHost:'gateway',privateBrowserAccess:false});
   return {appendSystemContext:policy.appendSystemContext+' '+executionPolicy(),odsTurnContext:{...admission,content:admission.previous?.content ?? executionClock(admission.startedAt)+' File receipt fixture.'}};
 });
 api.on('before_tool_call',(event,ctx)=>guard.beforeToolCall(event,ctx));
 api.on('after_tool_call',(event,ctx)=>guard.afterToolCall(event,ctx));
 api.on('tool_result_persist',(event,ctx)=>guard.toolResultPersist(event,ctx));
 }};`);
 const token='fixture-only-0123456789abcdef';
 const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token},http:{endpoints:{chatCompletions:{enabled:true}}}},agents:{defaults:{workspace,skipBootstrap:true,sandbox:sandbox?{mode:'all',scope:'session',workspaceAccess:'rw',docker:{image,containerPrefix,network:'none',readOnlyRoot:true,user:String(process.getuid())+':'+process.getgid(),capDrop:['ALL'],memory:'256m',cpus:1}}:{mode:'off'},model:{primary:'fixture/test'},contextTokens:65536,...(truncated?{contextLimits:{toolResultMaxChars:2000}}:{}),heartbeat:{every:'0m'}},list:[{id:agentId,default:true,workspace}]},models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:65536,maxTokens:1024,reasoning:false,input:['text']}]}}},tools:{allow:['read','write','edit','tool_call','tool_search','tool_describe'],fs:{workspaceOnly:true},toolSearch:{enabled:deferred,mode:'tools'}},plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}}};
 writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
 async function start(){child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});child.stdout.on('data',c=>log+=c);child.stderr.on('data',c=>log+=c);for(let i=0;i<250;i++){try{if((await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok)return;}catch{}assert.equal(child.exitCode,null,log);await delay(100);}throw Error(log);}
 async function stop(){if(child&&child.exitCode===null){const done=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([done,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await done;}}}
 async function turn(steps,user='file-wire-session') {plan=steps;cursor=0;lastRequests=[];const response=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({model:'openclaw:'+agentId,stream:true,user,messages:[{role:'user',content:'Execute the requested bounded file diagnostic; report actual results.'}]}),signal:AbortSignal.timeout(45000)});const text=await response.text();assert.equal(response.status,200,text+'\n'+log);assert.equal(cursor,steps.length+1,log);return lastRequests;}
 const edit=(id,oldText,newText)=>({id,name:'edit',args:{path:'a.txt',edits:[{oldText,newText}]}});
 function observed(request,id){const message=request.messages.find(m=>m.role==='tool'&&m.tool_call_id===id);assert(message,'immediate exact call '+id+' '+JSON.stringify(request.messages.filter(m=>m.role==='tool')));return typeof message.content==='string'?message.content:JSON.stringify(message.content);}
 try {
  await start();
  if(truncated){
   const text=Array.from({length:150},(_,i)=>`row-${i}-`+'x'.repeat(40)).join('\n');writeFileSync(sourceFile,text);
   let rows=await turn([{id:'readtruncated',name:'read',args:{path:'a.txt'}},{id:'writetruncated',name:'write',args:{path:'a.txt',content:'WRONG'}}]);
   const body=observed(rows[1],'readtruncated');assert.match(body,/truncated/i);assert.doesNotMatch(body,/150\|row-149/);
   assert.match(observed(rows[2],'writetruncated'),/changed|no longer visible/i);assert.equal(readFileSync(sourceFile,'utf8'),text);
   rows=await turn([{id:'readnarrow',name:'read',args:{path:'a.txt',offset:1,limit:1}},edit('editnarrow','row-0-','changed-0-')]);
   assert.match(observed(rows[1],'readnarrow'),/1\|row-0-/);assert.equal(readFileSync(sourceFile,'utf8'),text.replace('row-0-','changed-0-'));
   console.log(JSON.stringify({mode,passed:true,actualProviderBodyTruncated:true,hiddenMetadataDidNotAuthorize:true,narrowRefreshSucceeded:true,custody}));return;
  }
  if(agentId!=='pixel'){
   const rows=await turn([{id:'otherread',name:'read',args:{path:'a.txt'}},edit('otheredit','alpha','OTHER')]);
   assert.match(observed(rows[1],'otherread'),/alpha/);assert.doesNotMatch(observed(rows[1],'otherread'),/File read:|sha256=/);
   assert.equal(readFileSync(sourceFile,'utf8'),'OTHER\nbeta\n');
   console.log(JSON.stringify({mode,passed:true,receiptCapabilityBypassed:true}));return;
  }
  let rows=await turn([{id:'readcurrent',name:'read',args:{path:'a.txt'}},edit('editcurrent','alpha','ALPHA')]);
  assert.match(observed(rows[1],'readcurrent'),/1\|alpha/);assert.match(observed(rows[2],'editcurrent'),/1\|ALPHA/);assert.equal(readFileSync(sourceFile,'utf8'),'ALPHA\nbeta\n');
  await stop();await start();rows=await turn([edit('editnextturn','ALPHA','REUSED')]);assert.match(observed(rows[1],'editnextturn'),/REUSED/);assert.equal(readFileSync(sourceFile,'utf8'),'REUSED\nbeta\n');
  writeFileSync(sourceFile,'external\nbeta\n');rows=await turn([edit('editstale','REUSED','WRONG')]);assert.match(observed(rows[1],'editstale'),/changed|no longer visible/i);assert.equal(readFileSync(sourceFile,'utf8'),'external\nbeta\n');
  rows=await turn([edit('editforeign','external','WRONG')],'fresh-session');assert.match(observed(rows[1],'editforeign'),/changed|no longer visible/i);assert.equal(readFileSync(sourceFile,'utf8'),'external\nbeta\n');
  // A currently visible post-write body must not authorize a same-byte inode replacement.
  rows=await turn([{id:'readidentity',name:'read',args:{path:'a.txt'}}]);
  writeFileSync(join(workspace,'replacement.txt'),readFileSync(sourceFile));renameSync(join(workspace,'replacement.txt'),sourceFile);
  rows=await turn([edit('editidentity','external','WRONG')]);assert.match(observed(rows[1],'editidentity'),/changed|no longer visible/i);assert.equal(readFileSync(sourceFile,'utf8'),'external\nbeta\n');
  const large=Array.from({length:900},(_,i)=>`line${i}-abcdefghijklmnopqrst`).join('\n');writeFileSync(join(workspace,'pages.txt'),large);
  rows=await turn([0,1,2].map(i=>({id:'page'+i,name:'read',args:{path:'pages.txt',offset:1+i*300,limit:300}})));
  for(let i=0;i<3;i++){const body=observed(rows[i+1],'page'+i);assert.match(body,new RegExp((1+i*300)+'\\|line'+i*300));assert.equal(body.split('[File read: pages.txt;').length,2,'one actual body');assert.doesNotMatch(body,/renderedSha256|bodyStorage/);assert(body.length<13024);}
  // Large range receipts must survive the real 8 KiB details cap and restart.
  await stop();await start();
  rows=await turn([{id:'replacepages',name:'write',args:{path:'pages.txt',content:'PAGES REPLACED'}}]);
  assert.equal(readFileSync(join(workspace,'pages.txt'),'utf8'),'PAGES REPLACED');
  await turn([{id:'readbeforecompaction',name:'read',args:{path:'a.txt'}}]);
  await stop();const sessionDir=join(root,'state/agents/pixel/sessions');const files=readdirSync(sessionDir).filter(n=>n.endsWith('.jsonl')&&!n.includes('.trajectory.'));const session=files.find(n=>readFileSync(join(sessionDir,n),'utf8').includes('readcurrent'));assert(session,'owned session file');
  const module=readdirSync(join(pkg,'dist')).find(n=>/^session-manager-.*\.js$/.test(n));const {t:SessionManager}=await import(pathToFileURL(join(pkg,'dist',module)));const manager=SessionManager.open(join(sessionDir,session));manager.appendCompaction('The old file contents are intentionally omitted from this summary.',manager.getLeafId(),1000);
  await start();rows=await turn([edit('editcompacted','external','WRONG'),{id:'readrefresh',name:'read',args:{path:'a.txt'}},edit('editrefreshed','external','REFRESHED')]);assert.match(observed(rows[1],'editcompacted'),/changed|no longer visible/i);assert.match(observed(rows[2],'readrefresh'),/1\|external/);assert.match(observed(rows[3],'editrefreshed'),/1\|REFRESHED/);assert.equal(readFileSync(sourceFile,'utf8'),'REFRESHED\nbeta\n');
  console.log(JSON.stringify({mode,passed:true,providerRequests:requests.length,custody,limitations:['Deterministic recording provider, no inference. Real SessionManager compaction with supplied summary. Actual configured provider-result truncation is exercised in the truncated modes.']}));
 } finally {writeFileSync(join(root,'provider-requests.json'),JSON.stringify(requests,null,2));writeFileSync(join(root,'gateway.log'),log);await stop();upstream.closeAllConnections();await new Promise(r=>upstream.close(r));if(sandbox){const owned=spawnSync('docker',['ps','-aq','--filter','name=^'+containerPrefix],{encoding:'utf8'});assert.equal(owned.status,0);for(const id of owned.stdout.trim().split(/\s+/).filter(Boolean))assert.equal(spawnSync('docker',['rm','-f',id]).status,0);}if(process.env.ODS_RECEIPT_WIRE_KEEP!=='1')rmSync(root,{recursive:true,force:true});else console.log('Fixture evidence '+root);}
});
