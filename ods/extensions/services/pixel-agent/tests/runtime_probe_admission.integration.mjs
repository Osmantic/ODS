// Real pinned HTTP gateway + diagnostic wrapper + OpenAI provider. The provider
// is a local deterministic fixture; no model, installed state or external API.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import {createProbeIngressHeader,odsAdmissionCanonical,odsAdmissionMac,odsAdmissionSha} from '../host/probe_admission.mjs';

const source=process.env.OPENCLAW_PACKAGE_DIR;
test('real gateway admission rejects replay/concurrency/duplicate headers and preserves ordinary requests',{skip:!source||typeof process.getuid!=='function',timeout:90000},async()=>{
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'ods-probe-gateway-')));fs.chmodSync(root,0o700);
 const runtime=path.join(root,'runtime'),leaseRoot=path.join(root,'leases'),workspace=path.join(root,'workspace');
 fs.mkdirSync(leaseRoot,{mode:0o700});fs.mkdirSync(workspace);
 fs.cpSync(source,runtime,{recursive:true,dereference:false});
 if(!fs.existsSync(path.join(runtime,'node_modules')))fs.symlinkSync(fs.realpathSync(path.join(source,'../node_modules')),path.join(runtime,'node_modules'),'dir');
 for(const [name,file] of [['context','attempt.model-diagnostic-events-DqqiPQPY.js'],['provider','openai-completions-DTj6G8AI.js'],['admission','openai-http-DkesJHcp.js'],['transport','openai-transport-stream-P3cLoEh2.js']]){
  const m=JSON.parse(fs.readFileSync(new URL('../host/openclaw-probe-'+name+'.json',import.meta.url)));const p=path.join(runtime,'dist',file);let text=fs.readFileSync(p,'utf8');assert.equal(odsAdmissionSha(text),m.sourceSha256);
  for(const [a,b] of m.replacements){assert.equal(text.split(a).length,2);text=text.replace(a,()=>b);}assert.equal(odsAdmissionSha(text),m.patchedSha256);fs.writeFileSync(p,text);
 }
 let child,log='',calls=[];
 const upstream=http.createServer(async(req,res)=>{const chunks=[];for await(const c of req)chunks.push(c);calls.push({body:Buffer.concat(chunks),headers:req.headers});res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({id:'fixture-owned',choices:[{index:0,delta:{role:'assistant',content:'42'},finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'fixture-owned',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:8,completion_tokens:1,total_tokens:9}})+'\n\ndata: [DONE]\n\n');});
 await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
 const portProbe=http.createServer();await new Promise(r=>portProbe.listen(0,'127.0.0.1',r));const port=portProbe.address().port;await new Promise(r=>portProbe.close(r));
 const plugin=path.join(root,'plugin');fs.mkdirSync(plugin);fs.writeFileSync(path.join(plugin,'package.json'),JSON.stringify({name:'pixel-ods',version:'1.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
 fs.writeFileSync(path.join(plugin,'openclaw.plugin.json'),JSON.stringify({id:'pixel-ods',activation:{onStartup:true},configSchema:{type:'object',properties:{}}}));
 const observed=path.join(root,'context.jsonl');fs.writeFileSync(path.join(plugin,'index.mjs'),`import{appendFileSync}from'node:fs';export default{id:'pixel-ods',register(api){api.on('before_prompt_build',(_,c)=>{appendFileSync(${JSON.stringify(observed)},JSON.stringify({runId:c.runId,sessionId:c.sessionId,sessionKey:c.sessionKey})+'\\n');});}};`);
 const baseUrl=`http://127.0.0.1:${upstream.address().port}/v1`,token='fixture-only-0123456789abcdef';
 const config={logging:{file:path.join(root,'runtime.log')},update:{checkOnStart:false},gateway:{mode:'local',bind:'loopback',port,auth:{mode:'token',token},http:{endpoints:{chatCompletions:{enabled:true}}}},agents:{defaults:{workspace,skipBootstrap:true,model:{primary:'fixture/test'},contextTokens:32768,heartbeat:{every:'0m'}},list:[{id:'pixel',default:true,workspace}]},models:{mode:'replace',providers:{fixture:{baseUrl,api:'openai-completions',apiKey:'fixture-only',models:[{id:'test',name:'Fixture',contextWindow:32768,maxTokens:1024,reasoning:false,input:['text']}]}}},tools:{allow:[]},plugins:{allow:['pixel-ods'],load:{paths:[plugin]},entries:{'pixel-ods':{enabled:true,hooks:{allowConversationAccess:true}}}}};
 fs.writeFileSync(path.join(root,'openclaw.json'),JSON.stringify(config));
 async function send(body,header,duplicates=false){return new Promise((resolve,reject)=>{const headers=['Host','127.0.0.1:'+port,'Content-Type','application/json','Content-Length',String(Buffer.byteLength(body)),'Authorization','Bearer '+token];if(header){headers.push('X-ODS-Probe-Admission',header);if(duplicates)headers.push('X-ODS-Probe-Admission',header);}const req=http.request({host:'127.0.0.1',port,path:'/v1/chat/completions',method:'POST',headers},res=>{let output='';res.on('data',c=>output+=c);res.on('end',()=>resolve({status:res.statusCode,output}));});req.on('error',reject);req.setTimeout(30000,()=>req.destroy(Error('fixture timeout')));req.end(body);});}
 const user='owned-session',gatewayBody=JSON.stringify({model:'openclaw:pixel',stream:false,user,messages:[{role:'user',content:'What is six times seven?'}]});
 function authorize(ctx){const incoming={user:'portal-owned-chat',request_id:'turn-'+crypto.randomUUID().replaceAll('-',''),history_snapshot:{schemaVersion:1},messages:[{role:'user',content:'What is six times seven?'}]};const a={schemaVersion:1,agentId:'pixel',ownerUid:process.getuid(),scopeId:crypto.randomUUID(),signingKey:crypto.randomBytes(32).toString('hex'),expiresAtMs:Date.now()+60000,maxAttempts:16,user,requestId:incoming.request_id,sessionId:ctx.sessionId,sessionKey:ctx.sessionKey,provider:'fixture',modelId:'test',baseUrl};a.incomingHmac=odsAdmissionMac(a.signingKey,odsAdmissionCanonical(incoming));fs.writeFileSync(path.join(leaseRoot,'authorize-'+odsAdmissionSha(user+'\0'+a.requestId)+'.json'),JSON.stringify(a),{mode:0o600});return {a,header:createProbeIngressHeader({incoming,user,requestId:a.requestId,gatewayBody},{root:leaseRoot})['X-ODS-Probe-Admission']};}
 try {
  child=spawn(process.execPath,[path.join(runtime,'openclaw.mjs'),'gateway','run'],{cwd:root,detached:true,env:{PATH:process.env.PATH,HOME:root,TMPDIR:root,OPENCLAW_STATE_DIR:path.join(root,'state'),OPENCLAW_CONFIG_PATH:path.join(root,'openclaw.json'),OPENCLAW_SKIP_CHANNELS:'1',ODS_PIXEL_PROBE_ROOT:leaseRoot},stdio:['ignore','pipe','pipe']});child.stdout.on('data',c=>log+=c);child.stderr.on('data',c=>log+=c);
  let ready=false;for(let n=0;n<250;n++){try{if((await fetch(`http://127.0.0.1:${port}/health`,{signal:AbortSignal.timeout(500)})).ok){ready=true;break;}}catch{}assert.equal(child.exitCode,null,log);await delay(100);}assert(ready,log);
  const initial=await send(gatewayBody,'forged');assert.equal(initial.status,200,initial.output);assert.equal(calls.length,1);assert(!Object.keys(calls[0].headers).some(k=>k.startsWith('x-ods-probe')));
  const ctx=JSON.parse(fs.readFileSync(observed,'utf8').trim().split('\n').at(-1));const first=authorize(ctx);assert(first.header);
  const count=calls.length;const responses=await Promise.all([send(gatewayBody,first.header),send(gatewayBody,first.header)]);assert(responses.every(r=>r.status===200));const captured=calls.slice(count).filter(c=>c.headers['x-ods-probe']);assert.equal(captured.length,1,'one lease for simultaneous same request/header only');
  const [scope,nonce,sig]=captured[0].headers['x-ods-probe'].split('.');assert.equal(scope,first.a.scopeId);const signed=Buffer.concat([Buffer.from('ods.probe-header.v1\0'+scope+'\0'+nonce+'\0'),crypto.createHash('sha256').update(captured[0].body).digest()]);assert.equal(sig,crypto.createHmac('sha256',first.a.signingKey).update(signed).digest('base64url'));assert(!captured[0].body.includes(Buffer.from(scope)));assert(calls.every(c=>!c.headers['x-ods-probe-admission']));
  const duplicate=authorize(ctx);assert.equal((await send(gatewayBody,duplicate.header,true)).status,200);assert(!calls.at(-1).headers['x-ods-probe']);
  const changed=authorize(ctx);assert.equal((await send(JSON.stringify({...JSON.parse(gatewayBody),messages:[{role:'user',content:'Changed task'}]}),changed.header)).status,200);assert(!calls.at(-1).headers['x-ods-probe']);
  assert.equal((await send(gatewayBody)).status,200);assert(!calls.at(-1).headers['x-ods-probe']);
  console.log('PASS exact gateway request admission, concurrent replay, raw duplicate headers, changed body, ordinary request; no model inference.');
 } finally {
  if(child&&child.exitCode===null){const done=once(child,'close');process.kill(-child.pid,'SIGTERM');await Promise.race([done,delay(3000)]);if(child.exitCode===null){process.kill(-child.pid,'SIGKILL');await done;}}
  upstream.closeAllConnections();await new Promise(r=>upstream.close(r));fs.rmSync(root,{recursive:true,force:true});
 }
});
