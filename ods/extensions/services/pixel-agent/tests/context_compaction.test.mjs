import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {Readable} from 'node:stream';
import {createContextCompaction,readContextRequest} from '../plugin/context-compaction.mjs';

const user = `ods-${'a'.repeat(64)}`, other = `ods-${'b'.repeat(64)}`;
const sessionKey = `agent:pixel:openai-user:${user}`;
const tick = () => new Promise(resolve => setImmediate(resolve));
function observed(runtime,event,context) {
  const ctx={...context,runId:'test-run'};
  runtime.observeModelInput({},ctx);
  runtime.observeModelOutput({...event,lastAssistant:{...event.lastAssistant,timestamp:Date.now()+10000}},ctx);
}
const deferred = () => {let resolve,reject;const promise = new Promise((ok,no) => {resolve=ok;reject=no;});return {promise,resolve,reject};};
function fixture(t, overrides = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'ods-context-'));
  fs.chmodSync(directory,0o700);
  t.after(() => fs.rmSync(directory,{recursive:true,force:true}));
  let entry = {sessionId:'session-one',modelProvider:'ods-gateway',model:'ods/current',contextTokens:32768,
    totalTokens:17000,totalTokensFresh:true,updatedAt:Date.now(),compactionCount:0};
  let held = null, phase='idle', revision=0, active=false;
  const calls = [], releases = [];
  const result = deferred();
  const admission = {status:() => ({available:true,phase,revision:String(revision),active:phase==='busy'?1:0}),
    acquire(token,expected) {if(phase!=='idle'||String(revision)!==expected) throw Error('busy');held=token;phase='held';revision++;},
    release(token) {assert.equal(token,held);releases.push(token);held=null;phase='idle';revision++;},
    owns:token => held!==null && token===held};
  const args = {directory,requireModelObservation:false,readSession:scope => {assert.equal(scope.agentId,'pixel');assert.match(scope.sessionKey,/^agent:pixel:openai-user:ods-[a-f0-9]{64}$/);return entry;},
    activeSession:() => active,admission,
    readConfig:()=>({agents:{list:[{id:'pixel',model:'ods-gateway/ods/current'}]},models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:'ODS Current (local-4b.gguf)',contextWindow:32768}]}}}}),
    callGateway:(...values) => {calls.push(values);return result.promise;},...overrides};
  return {runtime:createContextCompaction(args),args,result,calls,releases,admission,directory,
    get entry(){return entry;},set entry(value){entry=value;},
    get phase(){return phase;},set phase(value){phase=value;},set active(value){active=value;}};
}

test('context exposes fresh occupancy and bounded metadata only, not transcript fields',t => {
  const f=fixture(t);f.entry={...f.entry,sessionFile:'/secret/file',apiKey:'secret',messages:[{content:'secret'}]};
  const value=f.runtime.context(user);
  assert.equal(value.status,'ready');assert.equal(value.context.used,17000);assert.equal(value.context.window,32768);
  assert.equal(value.sessionExists,true);assert.match(value.sessionRevision,/^[a-f0-9]{64}$/);
  assert.deepEqual(value.model,{provider:'ods-gateway',id:'local-4b.gguf',contextWindow:32768});
  assert.equal(JSON.stringify(value).includes('secret'),false);assert.equal(f.calls.length,0);
  f.entry.totalTokensFresh=false;assert.equal(f.runtime.context(user).context,null);
  f.entry.totalTokensFresh=true;f.entry.totalTokens=NaN;assert.equal(f.runtime.context(user).context,null);
});

test('absent session reports missing without creating one; rotational ID changes revision',t => {
  const f=fixture(t),before=f.runtime.context(user).sessionRevision;
  f.entry.sessionId='rotated';assert.notEqual(f.runtime.context(user).sessionRevision,before);
  f.entry=undefined;assert.equal(f.runtime.context(user).status,'missing');assert.equal(f.runtime.context(user).sessionExists,false);
});

test('native compact runs once asynchronously with summarization, never maxLines/truncation',async t => {
  const f=fixture(t);
  assert.equal((await f.runtime.compact(user,'first')).compaction.status,'running');
  await tick();assert.equal(f.phase,'held');assert.equal(f.calls.length,1);
  assert.deepEqual(f.calls[0],['sessions.compact',{timeoutMs:1920000},{key:sessionKey,agentId:'pixel'}]);
  assert.equal((await f.runtime.compact(user,'first')).compaction.status,'running');
  assert.equal((await f.runtime.compact(other,'parallel')).status,'busy');
  f.entry={...f.entry,totalTokens:1200,compactionCount:1,sessionId:'rotated'};
  f.result.resolve({key:sessionKey,ok:true,compacted:true,result:{tokensBefore:17000,tokensAfter:1200,summary:'PRIVATE'}});
  await tick();const done=f.runtime.context(user);
  assert.equal(done.status,'ready');assert.deepEqual(done.compaction,{status:'completed',requestId:'first',tokensBefore:17000,tokensAfter:1200,reason:null,count:1});
  assert.equal(done.context.used,1200);assert.equal(f.phase,'idle');
  assert.equal((await f.runtime.compact(user,'first')).compaction.status,'completed');assert.equal(f.calls.length,1);
  assert.equal(fs.readFileSync(path.join(f.directory,`${user}.json`),'utf8').includes('PRIVATE'),false);
});

test('active admission and native run checks refuse compact without aborting or RPC',async t => {
  const f=fixture(t);f.phase='busy';assert.equal((await f.runtime.compact(user,'busy')).status,'busy');
  f.phase='idle';f.active=true;assert.equal((await f.runtime.compact(user,'active')).status,'busy');
  assert.equal(f.calls.length,0);assert.equal(fs.readdirSync(f.directory).length,0);
});

test('another conversation does not make a context read busy or discard its usage',async t=>{
  const f=fixture(t);f.phase='busy';
  const snapshot=f.runtime.context(user);
  assert.equal(snapshot.status,'ready');assert.equal(snapshot.context.used,17000);
  assert.equal((await f.runtime.compact(user,'wait-for-other-run')).status,'busy');
  assert.equal(f.calls.length,0);
  f.phase='idle';
  assert.equal((await f.runtime.compact(user,'wait-for-other-run')).compaction.status,'running');
  await tick();f.result.resolve({key:sessionKey,ok:true,compacted:false});await tick();
  assert.equal(f.calls.length,1);
});

test('admission race fails closed; recheck session rotation releases before any RPC',async t => {
  const f=fixture(t),acquire=f.admission.acquire;
  f.admission.acquire=(...args) => {f.phase='busy';return acquire(...args);};
  assert.equal((await f.runtime.compact(user,'race')).status,'busy');assert.equal(f.calls.length,0);
  f.phase='idle';f.admission.acquire=(...args)=>{acquire(...args);f.entry.sessionId='changed';};
  assert.equal((await f.runtime.compact(user,'rotation')).status,'ready');assert.equal(f.calls.length,0);assert.equal(f.phase,'idle');
});

test('unknown transport result remains held and never replays, including persisted retry',async t => {
  const f=fixture(t);await f.runtime.compact(user,'timeout');await tick();
  f.result.reject(Error('upstream token SECRET timeout'));await tick();
  const value=f.runtime.context(user);assert.equal(value.compaction.status,'unknown');assert.equal(value.compaction.reason,'result-unconfirmed');assert.equal(value.status,'unavailable');
  assert.equal(f.phase,'held');assert.equal(JSON.stringify(value).includes('SECRET'),false);
  const sameProcess=createContextCompaction(f.args);
  assert.equal((await sameProcess.compact(user,'timeout')).compaction.status,'unknown');assert.equal(f.calls.length,1);
  const restarted=createContextCompaction({...f.args,instanceId:'next-gateway'});
  assert.equal(restarted.context(user).compaction.reason,'runtime-restarted');assert.equal(f.phase,'idle');
  assert.equal((await restarted.compact(user,'timeout')).compaction.status,'unknown');assert.equal(f.calls.length,1);
});

test('deadline expires conservatively without a second native request',async t => {
  const f=fixture(t,{timeoutMs:10});await f.runtime.compact(user,'deadline');
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(f.runtime.context(user).compaction.status,'unknown');assert.equal(f.phase,'held');
  assert.equal((await f.runtime.compact(user,'deadline')).compaction.status,'unknown');assert.equal(f.calls.length,1);
  f.result.resolve({key:sessionKey,ok:true,compacted:true});await tick();
  assert.equal(f.runtime.context(user).compaction.status,'completed');assert.equal(f.phase,'idle');
});

test('validated skipped and failed responses release the hold and sanitize runtime reasons',async t => {
  for(const ok of [true,false]){
    const f=fixture(t);await f.runtime.compact(user,`skip-${ok}`);await tick();
    f.result.resolve({key:sessionKey,ok,compacted:false,reason:'private filename and traceback'});await tick();
    assert.equal(f.runtime.context(user).compaction.status,ok?'skipped':'failed');assert.equal(f.phase,'idle');
    assert.equal(JSON.stringify(f.runtime.context(user)).includes('traceback'),false);
  }
});

test('mismatched RPC identity never claims success or releases uncertain mutation',async t => {
  const f=fixture(t);await f.runtime.compact(user,'wrong-key');await tick();
  f.result.resolve({key:`agent:pixel:openai-user:${other}`,ok:true,compacted:true});await tick();
  assert.equal(f.runtime.context(user).compaction.status,'unknown');assert.equal(f.phase,'held');
});

test('pre-dispatch provider failure releases admission and does not invoke RPC',async t => {
  const f=fixture(t,{prepareModel:async()=>{throw Error('denied');}});await f.runtime.compact(user,'denied');await tick();
  assert.equal(f.runtime.context(user).compaction.status,'failed');assert.equal(f.phase,'idle');assert.equal(f.calls.length,0);
});

test('completed idempotency survives recreation and bounded ledger refuses new work',async t => {
  const f=fixture(t,{maximumRequests:1});await f.runtime.compact(user,'once');await tick();
  f.result.resolve({key:sessionKey,ok:true,compacted:false});await tick();
  const restored=createContextCompaction({...f.args,instanceId:'new-instance'});
  assert.equal((await restored.compact(user,'once')).compaction.status,'skipped');
  const filename=path.join(f.directory,`${user}.json`), journal=fs.readFileSync(filename,'utf8');
  let admissionAttempts=0;
  f.admission.acquire=()=>{admissionAttempts++;throw Error('capacity refusal must not acquire');};
  for(const runtime of [restored,restored,createContextCompaction({...f.args,instanceId:'another-instance'})]) {
    const refused=await runtime.compact(user,'twice');
    assert.equal(refused.status,'unavailable');
    assert.deepEqual(refused.compaction,{status:'failed',requestId:'twice',tokensBefore:null,tokensAfter:null,reason:'request-limit',count:0});
    assert.equal(f.phase,'idle');assert.equal(f.calls.length,1);assert.equal(admissionAttempts,0);
    assert.equal(fs.readFileSync(filename,'utf8'),journal,'capacity must preserve prior receipts without journal writes');
    assert.equal((await runtime.compact(user,'once')).compaction.status,'skipped');
  }
});

test('maintenance helper shares exclusion and releases after exact callback settles',async t => {
  const f=fixture(t),done=deferred();f.entry=undefined;
  const operation=f.runtime.withMaintenance(user,async ctx=>{assert.equal(ctx.sessionKey,sessionKey);assert.equal(ctx.session,null);await done.promise;return 42;});
  await tick();assert.equal(f.phase,'held');assert.equal((await f.runtime.compact(other,'race')).status,'busy');
  done.resolve();assert.equal(await operation,42);assert.equal(f.phase,'idle');
});

test('symlinked or corrupt ledger cannot dispatch or expose arbitrary data',async t => {
  const f=fixture(t);fs.writeFileSync(path.join(f.directory,`${user}.json`),'not json',{mode:0o600});
  assert.equal(f.runtime.context(user).status,'unavailable');assert.equal((await f.runtime.compact(user,'no')).status,'unavailable');assert.equal(f.calls.length,0);
});

test('HTTP body reader requires exact opaque user and bounded request id',async () => {
  const request=(body,method='POST',type='application/json')=>Object.assign(Readable.from([Buffer.from(typeof body==='string'?body:JSON.stringify(body))]),{method,headers:{'content-type':type}});
  assert.deepEqual(await readContextRequest(request({user})),{status:200,user});
  assert.deepEqual(await readContextRequest(request({user,request_id:'compact-abc'}),true),{status:200,user,requestId:'compact-abc'});
  for(const body of [{user:'raw-chat-id'},{user,sessionKey},{user,request_id:'../unsafe'}])assert.equal((await readContextRequest(request(body),Boolean(body.request_id))).status,400);
  assert.equal((await readContextRequest(request('x'.repeat(513)))).status,413);
  assert.equal((await readContextRequest(request({user},'GET'))).status,405);
  assert.equal((await readContextRequest(request({user},'POST','text/plain'))).status,415);
});

test('model-bound measurement survives restart but invalidates same-window alias changes',t=>{
  let model='model-a.gguf',time=Date.now()+1000;
  const f=fixture(t,{requireModelObservation:true,now:()=>time,
    readConfig:()=>({agents:{list:[{id:'pixel',model:'ods-gateway/ods/current'}]},models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:`ODS Current (${model})`,contextWindow:32768}]}}}})});
  assert.equal(f.runtime.context(user).context,null,'legacy alias has no proof of the actual model used');
  const event={lastAssistant:{usage:{input:9000,output:100,cacheRead:200}},contextTokenBudget:32000};
  const ctx={agentId:'pixel',sessionKey,sessionId:f.entry.sessionId};
  observed(f.runtime,event,ctx);
  assert.equal(f.runtime.context(user).context.used,9300);assert.equal(f.runtime.context(user).context.window,32000);
  assert.equal(createContextCompaction(f.args).context(user).context.used,9300);
  model='model-b.gguf';assert.equal(f.runtime.context(user).context,null);assert.equal(f.runtime.context(user).model.id,model);
  time++;observed(f.runtime,event,ctx);assert.equal(f.runtime.context(user).context.used,9300);
  f.entry.updatedAt=time+100;f.entry.totalTokensFresh=false;
  assert.equal(f.runtime.context(user).context.used,9300,'session metadata writes preserve the last measured call');
  f.entry.totalTokensFresh=true;f.entry.totalTokens=111195;
  assert.equal(f.runtime.context(user).context.used,9300,'cumulative session tokens are not context occupancy');
  assert.equal(f.runtime.context(user).model.contextWindow,32000,'reported model budget matches the measured effective budget');
  f.entry.compactionCount++;
  assert.equal(f.runtime.context(user).context,null,'a later compaction invalidates the old occupancy');
});

test('overflow precheck cannot attribute a historical cumulative reply to the new model',t=>{
  let time=200000;
  const f=fixture(t,{requireModelObservation:true,now:()=>time});
  const ctx={agentId:'pixel',sessionKey,sessionId:f.entry.sessionId,runId:'new-turn'};
  const output=(timestamp,usage)=>({contextTokenBudget:16384,lastAssistant:{timestamp,stopReason:'stop',usage}});
  f.runtime.observeModelInput({},ctx);
  f.runtime.observeModelOutput(output(time-1000,{input:68203,output:649,totalTokens:18145}),ctx);
  assert.equal(f.runtime.context(user).context,null,'reproduces the stale 420% report');
  f.runtime.observeModelOutput(output(time,{input:68203,output:649,totalTokens:18145}),ctx);
  assert.equal(f.runtime.context(user).context,null,'synthetic aggregate usage is not a model-call measurement');
  f.runtime.observeModelOutput(output(time,{input:7000,output:200,totalTokens:7200}),{...ctx,runId:'other-turn'});
  assert.equal(f.runtime.context(user).context,null);
  f.runtime.observeModelOutput(output(time,{input:7000,output:200,totalTokens:7200}),ctx);
  assert.equal(f.runtime.context(user).context.used,7200);
  f.runtime.observeModelOutput({...output(time,{input:0,output:0,totalTokens:0}),lastAssistant:{timestamp:time,stopReason:'aborted',usage:{input:8000,output:0}}},ctx);
  assert.equal(f.runtime.context(user).context.used,7200,'aborted output preserves the last verified call');
});

test('a model change between input and output cannot relabel usage',t=>{
  let model='before';
  const time=Date.now();
  const f=fixture(t,{requireModelObservation:true,now:()=>time,
    readConfig:()=>({agents:{list:[{id:'pixel',model:'ods-gateway/ods/current'}]},models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:`ODS Current (${model})`,contextWindow:32768}]}}}})});
  const ctx={agentId:'pixel',sessionKey,sessionId:f.entry.sessionId,runId:'switch-race'};
  f.runtime.observeModelInput({},ctx); model='after';
  f.runtime.observeModelOutput({contextTokenBudget:32768,lastAssistant:{timestamp:time,usage:{input:9000,output:100}}},ctx);
  assert.equal(f.runtime.context(user).context,null);
});

test('native RPC explicit busy refusal releases admission without falsely reporting compaction',async t=>{
  const f=fixture(t);await f.runtime.compact(user,'rpc-race');await tick();
  f.result.reject(Object.assign(new Error('Session is active; retry compaction after the current run finishes.'),{name:'GatewayClientRequestError',gatewayCode:'UNAVAILABLE'}));await tick();
  assert.equal(f.runtime.context(user).compaction.status,'failed');assert.equal(f.runtime.context(user).compaction.reason,'runtime-busy');assert.equal(f.phase,'idle');
});

test('remote route identity invalidates same-model usage across providers and restart',t=>{
  let fingerprint='a'.repeat(64),time=Date.now()+1000;
  const f=fixture(t,{requireModelObservation:true,now:()=>time,
    readConfig:()=>({agents:{list:[{id:'pixel',model:'ods-gateway/ods/current'}]},
      models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:'ODS Current (same-model)',contextWindow:32768}]}}},
      plugins:{entries:{'pixel-ods':{config:fingerprint===undefined?{}:{modelRouteFingerprint:fingerprint}}}}})});
  const event={lastAssistant:{usage:{input:9000,output:100}},contextTokenBudget:32768};
  const ctx={agentId:'pixel',sessionKey,sessionId:f.entry.sessionId};
  observed(f.runtime,event,ctx);
  assert.equal(createContextCompaction(f.args).context(user).context.used,9100);
  assert.equal(f.runtime.context(user).model.routeFingerprint,fingerprint);
  fingerprint='b'.repeat(64);
  assert.equal(f.runtime.context(user).context,null);
  assert.equal(createContextCompaction(f.args).context(user).context,null);
  assert.equal(f.runtime.context(user).model.id,'same-model');
  time++;observed(f.runtime,event,ctx);
  assert.equal(f.runtime.context(user).context.used,9100);
  fingerprint=undefined;
  assert.equal(f.runtime.context(user).context,null,'returning to local clears remote measurement');
  assert.equal('routeFingerprint' in f.runtime.context(user).model,false);
  time++;observed(f.runtime,event,ctx);
  assert.equal(f.runtime.context(user).context.used,9100,'legacy/local route remains measurable');
  fingerprint='https://provider.invalid/secret';
  assert.equal(f.runtime.context(user).status,'unavailable');
  assert.equal(f.runtime.context(user).model,null,'invalid identity is neither trusted nor exposed');
  fingerprint='a'.repeat(64)+'\n';
  assert.equal(f.runtime.context(user).status,'unavailable');
});

test('legacy measurements without attempt provenance are invalidated after upgrade',t=>{
  const f=fixture(t,{requireModelObservation:true});
  const sha=value=>createHash('sha256').update(value).digest('hex');
  const measurement={
    modelRevision:sha(JSON.stringify(['ods-gateway','ods/current','local-4b.gguf',32768])),
    sessionRevision:sha(`${sessionKey}\0${f.entry.sessionId}`),
    used:7654,window:32768,measuredAt:Date.now(),compactionCount:0,
  };
  fs.writeFileSync(path.join(f.directory,`${user}.json`),JSON.stringify({version:1,operations:[],measurement}),{mode:0o600});
  f.entry.totalTokensFresh=false;
  assert.equal(createContextCompaction(f.args).context(user).context,null);
});

test('settings cap reduction invalidates persisted usage across restart and restoration of the previous cap',t=>{
  let cap=32768;
  const readConfig=()=>({agents:{defaults:{contextTokens:16384},list:[{id:'pixel',model:'ods-gateway/ods/current',contextTokens:cap}]},
    models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:'ODS Current (local-4b.gguf)',contextWindow:32768}]}}},
    plugins:{entries:{'pixel-ods':{config:{modelContextWindow:cap}}}}});
  const f=fixture(t,{requireModelObservation:true,readConfig});f.entry.totalTokensFresh=false;
  const ctx={agentId:'pixel',sessionKey,sessionId:f.entry.sessionId};
  const event=window=>({contextTokenBudget:window,lastAssistant:{usage:{input:800,output:100}}});
  observed(f.runtime,event(32768),ctx);
  assert.equal(f.runtime.context(user).context.window,32768,'explicit agent cap overrides the inherited default');
  cap=8192;
  const restarted=createContextCompaction({...f.args,instanceId:'smaller-context'});
  assert.equal(restarted.context(user).context,null);
  assert.equal(restarted.context(user).model.contextWindow,8192);
  observed(restarted,event(32768),ctx);
  assert.equal(restarted.context(user).context,null,'old larger-budget event cannot validate the reduced budget');
  cap=32768;
  assert.equal(createContextCompaction({...f.args,instanceId:'restored-context'}).context(user).context,null,'old usage is not resurrected');
  cap=8192;
  observed(restarted,event(8192),ctx);
  assert.equal(restarted.context(user).context.window,8192);
  assert.equal(restarted.context(user).context.used,900);
});

test('default cap bounds stale session capacity and native compaction measurements',async t=>{
  const f=fixture(t,{requireModelObservation:true,readConfig:()=>({agents:{defaults:{contextTokens:8192},list:[{id:'pixel',model:'ods-gateway/ods/current'}]},
    models:{providers:{'ods-gateway':{models:[{id:'ods/current',name:'ODS Current (local-4b.gguf)'}]}}},
    plugins:{entries:{'pixel-ods':{config:{modelContextWindow:65536}}}}})});
  assert.equal(f.runtime.context(user).model.contextWindow,8192,'session 32K and plugin 64K cannot override native 8K cap');
  assert.equal(f.runtime.context(user).context,null,'a configured cap alone is not measured usage');
  await f.runtime.compact(user,'smaller-context');await tick();
  f.entry={...f.entry,compactionCount:1,totalTokensFresh:false};
  f.result.resolve({key:sessionKey,ok:true,compacted:true,result:{tokensBefore:17000,tokensAfter:1200}});await tick();
  assert.equal(f.runtime.context(user).context.window,8192);
  assert.equal(f.runtime.context(user).context.used,1200);
});

test('unknown window produces model null instead of fabricated context capacity',t=>{
  const f=fixture(t,{readConfig:()=>({})});f.entry.contextTokens=undefined;f.entry.model='actual-model';
  assert.equal(f.runtime.context(user).model,null);assert.equal(f.runtime.context(user).context,null);
});

test('an expired managed turn is never reused as a compaction provider',async t=>{
  const f=fixture(t);f.entry.modelProvider='ods-policy';f.entry.model='turn-old';
  await f.runtime.compact(user,'managed');await tick();
  assert.equal(f.calls.length,0);assert.equal(f.phase,'idle');
  assert.equal(f.runtime.context(user).compaction.reason,'unsupported-model');
});

test('restart recovers an owned compaction hold before a different chat is opened',async t=>{
  const f=fixture(t);await f.runtime.compact(user,'interrupted');await tick();assert.equal(f.phase,'held');
  const recovered=createContextCompaction({...f.args,instanceId:'restarted-gateway'});
  assert.equal(recovered.context(other).status,'ready');assert.equal(f.phase,'idle');
  assert.equal(recovered.context(user).compaction.reason,'runtime-restarted');
  assert.equal((await recovered.compact(user,'interrupted')).compaction.status,'unknown');assert.equal(f.calls.length,1);
});

test('startup recovery releases only exact historical maintenance custody, never an external lease',t=>{
  const f=fixture(t),token='c'.repeat(64);f.admission.acquire(token,f.admission.status().revision);
  const external=createContextCompaction({...f.args,instanceId:'new-gateway'});
  assert.equal(f.phase,'held');assert.equal(external.context(other).status,'ready');assert.equal(f.releases.length,0);
  fs.writeFileSync(path.join(f.directory,`${user}.json`),JSON.stringify({version:1,operations:[],maintenance:{leaseToken:token,instance:'old-gateway'}}),{mode:0o600});
  const owned=createContextCompaction({...f.args,instanceId:'new-gateway'});
  assert.equal(f.phase,'idle');assert.equal(owned.context(other).status,'ready');assert.equal(f.releases.length,1);
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.directory,`${user}.json`))).maintenance,undefined);
});
