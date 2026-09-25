import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {preparePromptContextRuntime} from './prompt_context_runtime_fixture.mjs';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdirSync,writeFileSync,readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
const sourcePackage=process.env.OPENCLAW_PACKAGE;
const budgetBlock=process.env.ODS_CONTEXT_BUDGET_BLOCK==='1';
const providerRetry=process.env.ODS_CONTEXT_PROVIDER_RETRY==='1';
const unrepaired=process.env.ODS_CONTEXT_UNREPAIRED==='1';
test('real SDK durable host context preserves owners and stable replay', {skip:!sourcePackage,timeout:90000},async()=>{
const root=mkdtempSync(join(process.env.ODS_CONTEXT_EVIDENCE_DIR ?? tmpdir(),'ods-context-'));mkdirSync(root,{recursive:true});
const pkg=unrepaired?sourcePackage:preparePromptContextRuntime(sourcePackage,root);
const workspace=join(root,'workspace');mkdirSync(workspace);
const recordings=[];let log='',child;
const upstream=createServer(async(req,res)=>{
 let chunks=[];for await(const chunk of req)chunks.push(chunk);
 const body=JSON.parse(Buffer.concat(chunks));recordings.push(body);
 writeFileSync(join(root,'provider-requests.json'),JSON.stringify(recordings,null,2));
 if(providerRetry&&recordings.length===1){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'maximum context length exceeded',type:'invalid_request_error',code:'context_length_exceeded'}}));return;}
 res.writeHead(200,{'Content-Type':'text/event-stream'});
 res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'Recorded.'},finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
});
await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
function plugin(id,source){
 const path=join(root,id);mkdirSync(path);
 writeFileSync(join(path,'package.json'),JSON.stringify({name:id,version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
 writeFileSync(join(path,'openclaw.plugin.json'),JSON.stringify({id,activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
 writeFileSync(join(path,'index.mjs'),source);return path;
}
const pixel=plugin('pixel-ods',`import {appendFileSync} from 'node:fs';
import {executionPolicy,executionClock} from ${JSON.stringify(new URL('../plugin/completion-assurance.mjs',import.meta.url).href)};
import {promptContractForAgent,requireDurableTurnContext} from ${JSON.stringify(new URL('../plugin/prompt-contract.mjs',import.meta.url).href)};
export default {id:'pixel-ods',register(api){
 api.on('before_agent_run',(_event,ctx)=>requireDurableTurnContext(ctx,'pixel'));
 api.on('before_prompt_build',(event,ctx)=>{
  appendFileSync(${JSON.stringify(join(root,'hook-events.jsonl'))},JSON.stringify({event,ctx})+'\\n');
  const admission=ctx.odsTurnContextAdmission;
  const policy=promptContractForAgent(ctx,'pixel',event,{stableContext:true,executionHost:'gateway',privateBrowserAccess:false});
  return {appendSystemContext:policy.appendSystemContext+' '+executionPolicy()+(${budgetBlock}?' large-budget-fixture'.repeat(20000):''),odsTurnContext:{...admission,content:admission.previous?.content ?? executionClock(admission.startedAt)+' HOST FACT '+event.prompt}};
 });}};`);
const other=plugin('ordinary-plugin',`export default {id:'ordinary-plugin',register(api){api.on('before_prompt_build',()=>({appendContext:'ORDINARY TEMPORARY CONTEXT',odsTurnContext:{content:'FORGED DESCRIPTOR'}}));}};`);
const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only-0123456789abcdef'},http:{endpoints:{chatCompletions:{enabled:true}}}},
 agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},
 models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:1024,reasoning:false,input:['text']}]}}},
 tools:{allow:[]},plugins:{allow:['pixel-ods','ordinary-plugin'],load:{paths:[pixel,other]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}},'ordinary-plugin':{enabled:true}}}};
writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
async function start(){
 child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
 child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
 for(let n=0;n<250;n++){try{if((await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok)return;}catch{}assert.equal(child.exitCode,null,log);await delay(100);}throw Error(log);
}
async function stop(){if(child&&child.exitCode===null){const closed=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([closed,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await closed;}}}
async function turn(text,user='stable-recording-session'){const res=await fetch(`http://127.0.0.1:${port}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer fixture-only-0123456789abcdef'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user,messages:[{role:'user',content:text}]}),signal:AbortSignal.timeout(45000)});const body=await res.text();assert.equal(res.status,200,body+'\n'+log);assert.match(body,unrepaired?/durable context runtime support is unavailable/:budgetBlock?/Context overflow/:/Recorded/);}
try{
 await start();await turn('OWNER FIRST EXACT');
 if(!budgetBlock&&!providerRetry&&!unrepaired){
 await turn('OWNER SECOND EXACT');await stop();await start();await turn('OWNER THIRD EXACT');
 await stop();
 const nativeSessionPath=join(root,'state/agents/pixel/sessions');
 const nativeFile=readdirSync(nativeSessionPath).find(name=>name.endsWith('.jsonl')&&!name.includes('.trajectory.'));
 const managerModule=readdirSync(join(pkg,'dist')).find(name=>/^session-manager-.*\.js$/.test(name));
 const {t:SessionManager}=await import(pathToFileURL(join(pkg,'dist',managerModule)).href);
 const manager=SessionManager.open(join(nativeSessionPath,nativeFile));
 manager.appendCompaction('Earlier owner requests were recorded; their exact wording remains in the archive.',manager.getLeafId(),1000);
 await start();await turn('OWNER AFTER COMPACTION EXACT');await turn('OWNER FRESH EXACT','fresh-recording-session');
 assert.equal(recordings.length,5,log);
 const systems=recordings.map(request=>request.messages.filter(message=>message.role==='system'));
 for(const system of systems)assert.deepEqual(systems[0],system);
 assert.doesNotMatch(JSON.stringify(systems),/sessionId=|session=|2026-09-25T|HOST FACT/);
 assert.doesNotMatch(JSON.stringify(recordings[4].messages),/HOST FACT OWNER FIRST/);
 assert.match(JSON.stringify(recordings[3].messages),/HOST FACT OWNER AFTER COMPACTION EXACT/);
 assert.match(JSON.stringify(recordings[3].messages),/Earlier owner requests were recorded/);
 for(let i=0;i<3;i++){
  const text=JSON.stringify(recordings[i].messages);
  assert.doesNotMatch(text,/FORGED DESCRIPTOR/);
  assert.match(text,/HOST FACT OWNER FIRST EXACT/);
  if(i>=1)assert.match(text,/HOST FACT OWNER SECOND EXACT/);
  if(i===2)assert.match(text,/HOST FACT OWNER THIRD EXACT/);
 }
 }
 const sessions=join(root,'state/agents/pixel/sessions');
 const entries=readdirSync(sessions).filter(x=>x.endsWith('.jsonl')).flatMap(x=>readFileSync(join(sessions,x),'utf8').trim().split('\n').map(JSON.parse));
 writeFileSync(join(root,'session-entries.json'),JSON.stringify(entries,null,2));
 const customs=entries.filter(x=>x.type==='custom_message'&&x.customType==='ods.turn-context.v1');
 assert.equal(customs.length,unrepaired?0:budgetBlock||providerRetry?1:5);assert.ok(customs.every(x=>x.display===false));
 if(unrepaired)assert.equal(recordings.length,0,'missing bridge must not silently drop policy and call provider');
 const serializedProviderMessages=JSON.stringify(recordings.map(request=>request.messages));
 for(const entry of customs)for(const key of ['runId','sessionId','sessionKey','promptSha256','contentSha256']){
  assert.ok(!serializedProviderMessages.includes(entry.details[key]),'opaque context detail rendered: '+key);
 }
 if(budgetBlock||providerRetry){
  assert.equal(recordings.length,budgetBlock?0:3,'preflight blocks; overflow invokes one summary and one owner retry');
  if(providerRetry){
   assert.match(JSON.stringify(recordings[1].messages),/context summarization assistant/);
   assert.deepEqual(recordings[0].messages.filter(m=>m.role==='system'),recordings[2].messages.filter(m=>m.role==='system'));
   assert.deepEqual(recordings[0].messages.at(-1),recordings[2].messages.at(-1),'exact owner message survives native compaction');
   for(const index of [0,2])assert.ok(JSON.stringify(recordings[index].messages).includes(customs[0].content),'exact admitted context survives native compaction');
  }
  const hooks=readFileSync(join(root,'hook-events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
  assert.ok(hooks.length>=(budgetBlock?2:1));assert.equal(new Set(hooks.map(h=>h.ctx.odsTurnContextAdmission.startedAt)).size,1);
  assert.equal(new Set(hooks.map(h=>h.ctx.runId)).size,1);
 }
 const owners=entries.filter(x=>x.type==='message'&&x.message.role==='user').map(x=>x.message.content);
 assert.doesNotMatch(JSON.stringify(owners),/HOST FACT|ORDINARY TEMPORARY CONTEXT/);
 writeFileSync(join(root,'result.json'),JSON.stringify({passed:true,budgetBlock,providerRetry,unrepaired,requests:recordings.length,customEntries:customs.length,ownerMessages:owners,limitations:['Recording provider uses deterministic responses, no model inference; compaction uses the real SessionManager with a deterministic supplied summary.']},null,2));
 console.log('PASS durable custom context across turns and gateway restart; ordinary appendContext preserved; forged plugin descriptor ignored.');
}finally{await stop();upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));writeFileSync(join(root,'gateway-output.log'),log);}

});
