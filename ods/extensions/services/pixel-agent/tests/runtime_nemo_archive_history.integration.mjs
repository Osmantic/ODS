// Investigation only: actual pinned gateway + actual ODS archive hydrator,
// disposable state, controlled offline provider; no installed owner state read.
// This qualifies the pre-router producer shape. Model-router regressions exercise
// the request projection against the captured user blocks and route guard.
// The in-memory maintenance admission double has no concurrent host operations.
// Transcript import, native compact RPC and provider serialization are real.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,cpSync,symlinkSync,rmSync,readdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join,isAbsolute} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {createIngressServer,computeSessionUser} from '../host/pixel_ingress.mjs';
import {createChatHistoryLedger} from '../host/chat_history_ledger.mjs';
let pkg=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(pkg && isAbsolute(pkg),'Set OPENCLAW_PACKAGE_DIR to the pinned package.');
const source=fileURLToPath(new URL('../',import.meta.url));
const evidence=process.env.ODS_NEMO_EVIDENCE_DIR;
assert.equal(JSON.parse(readFileSync(join(pkg,'package.json'))).version,'2026.6.33');
const root=mkdtempSync(join(tmpdir(),'ods-nemo-history-capture-'));
// Qualify the installed compaction path even when CI's pinned public package
// is still pristine. Only this fixture's dist is writable; dependencies and
// other public package resources remain read-only symlinks.
const installed=pkg;pkg=join(root,'package');mkdirSync(pkg);
for(const name of readdirSync(installed)) {
  if(name==='dist')cpSync(join(installed,name),join(pkg,name),{recursive:true});
  else symlinkSync(join(installed,name),join(pkg,name));
}
const sha=value=>createHash('sha256').update(value).digest('hex');
for(const [name,module] of [['compaction-empty','proxy-Bsfwfsp-.js'],['compaction-no-work','compact-DuWIsaq_.js'],['compaction-resume','sessions-CZbwb3_c.js']]) {
  const recipe=JSON.parse(readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
  const target=join(pkg,'dist',module);let text=readFileSync(target,'utf8');
  if(sha(text)!==recipe.patchedSha256) {
    const previous=recipe.previousReplacements?.[sha(text)];
    if(previous)for(const [before,after]of [...previous].reverse()){assert.equal(text.split(after).length,2);text=text.replace(after,before);}
    assert.equal(sha(text),recipe.sourceSha256,'only reviewed native bytes may be patched');
    for(const [before,after]of recipe.replacements){assert.equal(text.split(before).length,2);text=text.replace(before,after);}
    assert.equal(sha(text),recipe.patchedSha256);writeFileSync(target,text);
  }
}
let child,ingress,log='';const requests=[];
const upstream=createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const body=JSON.parse(Buffer.concat(chunks));requests.push(body);
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'CONTROLLED_OFFLINE_REPLY'},finish_reason:null}]})+'\n\n');
  res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:5,total_tokens:105}})+'\n\ndata: [DONE]\n\n');
});
await new Promise(resolve=>upstream.listen(0,'127.0.0.1',resolve));
const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
mkdirSync(join(root,'node_modules'));symlinkSync(pkg,join(root,'node_modules','openclaw'));
const plugin=join(root,'plugin');mkdirSync(plugin);cpSync(join(source,'plugin'),join(plugin,'ods'),{recursive:true});
writeFileSync(join(plugin,'package.json'),JSON.stringify({name:'nemo-history-fixture',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
writeFileSync(join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'nemo-history-fixture',contracts:{tools:['pixel_ods_history']},activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
writeFileSync(join(plugin,'index.mjs'),`
import {registerHistoryIntegration} from './ods/history-context.mjs';
import {createContextCompaction,readContextRequest} from './ods/context-compaction.mjs';
import {getSessionEntry,patchSessionEntry,resolveStorePath} from 'openclaw/plugin-sdk/session-store-runtime';
import {withSessionTranscriptWriteLock,appendAssistantMirrorMessageByIdentity} from 'openclaw/plugin-sdk/session-transcript-runtime';
import {callGatewayTool,resolveActiveEmbeddedRunSessionId} from 'openclaw/plugin-sdk/agent-harness-runtime';
let compactor,lease=null;
export default {id:'nemo-history-fixture',register(api){
compactor??=createContextCompaction({readConfig:()=>api.config,readSession:scope=>getSessionEntry({...scope,storePath:resolveStorePath(api.config.session?.store,{agentId:'pixel'})}),callGateway:callGatewayTool,
activeSession:key=>Boolean(resolveActiveEmbeddedRunSessionId(key)),admission:{status:()=>({available:true,phase:lease?'held':'idle',revision:0}),acquire:async token=>{if(lease)throw Error('fixture admission busy');lease=token},release:token=>{if(lease!==token)throw Error('wrong fixture admission');lease=null},owns:token=>lease===token}});
registerHistoryIntegration(api,{compactor,getSessionEntry,patchSessionEntry,resolveStorePath,withSessionTranscriptWriteLock,appendAssistantMirrorMessageByIdentity});
for(const operation of ['context','compact'])api.registerHttpRoute({path:'/pixel-ods/'+operation,auth:'gateway',match:'exact',handler:async(req,res)=>{const parsed=await readContextRequest(req,operation==='compact');if(parsed.status!==200){res.writeHead(parsed.status);res.end('{}');return true;}const result=operation==='compact'?await compactor.compact(parsed.user,parsed.requestId):compactor.context(parsed.user);res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));return true;}});
// No subagents or tool operations run in this fixture. Acknowledge only that
// absence so the ingress can finish its offline reply; no delivery gate claim.
for(const operation of ['subagent-delivery','verification'])api.registerHttpRoute({path:'/pixel-ods/'+operation,auth:'gateway',match:'exact',handler:async(req,res)=>{let body='';for await(const chunk of req)body+=chunk;const value=JSON.parse(body);res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(operation==='verification'?{status:'none'}:{schemaVersion:1,kind:'ods-subagent-delivery',runId:value.runId,status:'not-delegated'}));return true;}});
}};
`);
const config={logging:{file:join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token:'fixture-only'},http:{endpoints:{chatCompletions:{enabled:true}}}},
agents:{defaults:{workspace:join(root,'workspace'),skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true}]},
models:{mode:'replace',providers:{fixture:{baseUrl:`http://127.0.0.1:${upstream.address().port}/v1`,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:4096,reasoning:false,input:['text']}]}}},
plugins:{allow:['nemo-history-fixture'],load:{paths:[plugin]},entries:{'nemo-history-fixture':{enabled:true,hooks:{allowConversationAccess:true}}}}};
writeFileSync(join(root,'openclaw.json'),JSON.stringify(config));
try{
 child=spawn(process.execPath,[join(pkg,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:join(root,'state'),OPENCLAW_CONFIG_PATH:join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1'},stdio:['ignore','pipe','pipe']});
 child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
 let ready=false;for(let i=0;i<400;i++){try{ready=(await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok}catch{}if(ready)break;assert.equal(child.exitCode,null,log);await delay(100)}assert.ok(ready,log);
 const post=async(path,body)=>{const response=await fetch(`http://127.0.0.1:${port}${path}`,{method:'POST',headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(60000)});return {status:response.status,body:await response.text()}};
 const ledger=createChatHistoryLedger(join(root,'history-ledger'));
 ingress=createIngressServer({token:'fixture-only',gatewayPort:port,historyLedger:ledger});
 await new Promise(resolve=>ingress.listen(0,'127.0.0.1',resolve));
 const chat=async(user,content,archive)=>{const response=await fetch(`http://127.0.0.1:${ingress.address().port}/v1/chat/completions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:'openclaw:pixel',stream:true,user,messages:[{role:'user',content}],...(archive?{request_id:'archive-request-1',history_snapshot:{schemaVersion:1,messages:[...archive,{role:'user',content}]}}:{})}),signal:AbortSignal.timeout(60000)});return{status:response.status,body:await response.text()}};
 const freshReply=await chat('fresh-fixture','FRESH_CURRENT_REQUEST');
 const archive=[{role:'user',content:'HISTORIC_USER_CANARY'},{role:'assistant',content:'HISTORIC_ASSISTANT_CANARY'}];
 const hydratedReply=await chat('hydrated-fixture','HYDRATED_CURRENT_REQUEST',archive);
 assert.ok(freshReply.body.includes('CONTROLLED_OFFLINE_REPLY'),freshReply.body);
 assert.ok(hydratedReply.body.includes('CONTROLLED_OFFLINE_REPLY'),hydratedReply.body);
 const hydrated=computeSessionUser({user:'hydrated-fixture'});
 const compact=await post('/pixel-ods/context',{user:hydrated});
 assert.equal(JSON.parse(compact.body).compaction?.status,'skipped',compact.body);
 assert.equal(requests.length,2,JSON.stringify({freshReply,hydratedReply,requests:requests.length}));
 const result={runtime:'2026.6.33',syntheticProvider:true,fixtureRoot:root,actualIngress:true,archive,compact,freshReply,hydratedReply,requests};
 assert.deepEqual(requests.map(body=>body.messages.map(message=>message.role)),[['system','user'],['system','user','user']]);
 assert.ok(JSON.stringify(requests[1]).includes('HISTORIC_USER_CANARY'));
 assert.ok(JSON.stringify(requests[1]).includes('HISTORIC_ASSISTANT_CANARY'));
 assert.ok(JSON.stringify(requests[1]).includes('HYDRATED_CURRENT_REQUEST'));
 if(evidence)writeFileSync(join(evidence,'wire.json'),JSON.stringify(result,null,2));
 console.log('Pinned SDK archive ingress: fresh system/user; imported archive + native skipped compact: system/user/user; both offline replies delivered.');
}finally{
 if(ingress){ingress.closeAllConnections();await new Promise(resolve=>ingress.close(resolve));}
 if(child&&child.exitCode===null){const done=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([done,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await done}}
 upstream.closeAllConnections();await new Promise(resolve=>upstream.close(resolve));if(evidence)writeFileSync(join(evidence,'gateway.log'),log);
 rmSync(root,{recursive:true,force:true});
}
