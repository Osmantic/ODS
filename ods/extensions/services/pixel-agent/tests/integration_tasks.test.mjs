import test from 'node:test';
import assert from 'node:assert/strict';
import {createTaskHooks} from '../plugin/integration-tasks.mjs';
const NONCE = 'a'.repeat(32);
const BASE = {enabled:true,name:'Daily news digest',description:'Created by Portal integrations for the daily news digest. [ODS managed:'+NONCE+']',expr:'30 6 * * *',tz:'Europe/Lisbon',message:'Read the news digest.',timeoutSeconds:600,tools:['pixel_ods_mcp']};
const timers = () => ({setInterval:()=>({unref(){}}),clearInterval(){}});
function fixture(jobs=[]) {
  const state={plan:{...BASE},management:{nonce:NONCE,jobId:null},sent:[],warnings:[]};
  const cron={jobs,calls:[],async list(options){this.calls.push(['list',options]);return structuredClone(this.jobs);},
    async add(job){this.calls.push(['add',job]);const value={id:'created-'+this.calls.length,...structuredClone(job)};this.jobs.push(value);return value;},
    async update(id,patch){this.calls.push(['update',id,patch]);Object.assign(this.jobs.find(x=>x.id===id),structuredClone(patch));},
    async remove(id){this.calls.push(['remove',id]);this.jobs.splice(this.jobs.findIndex(x=>x.id===id),1);return{removed:true};}};
  const submit=async request=>{
    state.sent.push(request);
    if(request.action==='digest-plan')return{ok:true,plan:{...structuredClone(state.plan),management:structuredClone(state.management)}};
    if(request.action==='digest-job-record') {assert.equal(request.nonce,state.management.nonce);assert.ok(state.management.jobId===null||state.management.jobId===request.jobId);state.management.jobId=request.jobId;return{ok:true,recorded:true};}
    if(request.action==='digest-job-clear'){assert.equal(request.jobId,state.management.jobId);state.management=null;return{ok:true,cleared:true};}
    return{ok:true,notified:true};
  };
  const hooks=createTaskHooks({submit,timers:timers(),warn:message=>state.warnings.push(message)});
  return{state,cron,submit,hooks,start:()=>hooks.gatewayStart({}, {getCron:()=>cron})};
}

test('native finished events require explicit Portal ownership and retain exact run identity',async()=>{
 const {state,hooks}=fixture();
 await hooks.cronChanged({action:'started',agentId:'pixel',jobId:'ignored'});
 await hooks.cronChanged({action:'finished',agentId:'other',jobId:'ignored'});
 await hooks.cronChanged({action:'finished',jobId:'unattributed'});
 await hooks.cronChanged({action:'finished',agentId:'pixel',jobId:'j1',status:'error',runAtMs:42,sessionId:'session',sessionKey:'agent:pixel:cron:j1:run:session',runId:'run',summary:'x'.repeat(5000),error:'failed',job:{name:'Backup',description:'Nightly'}});
 assert.equal(state.sent.length,1);assert.equal(state.sent[0].summary.length,4000);
 assert.deepEqual(Object.fromEntries(['sessionId','sessionKey','runId'].map(k=>[k,state.sent[0][k]])),{sessionId:'session',sessionKey:'agent:pixel:cron:j1:run:session',runId:'run'});
});

test('job add records returned identity, keep is stable, full execution drift is repaired, and off removes only that ID',async()=>{
 const foreign={id:'foreign',agentId:'other',description:BASE.description};const f=fixture([foreign]);
 assert.equal(await f.hooks.syncDigest(),'no-cron');assert.equal(await f.start(),'added');
 const id=f.state.management.jobId;assert.ok(id);assert.equal(await f.hooks.syncDigest(),'kept');
 Object.assign(f.cron.jobs.find(j=>j.id===id),{sessionTarget:'main',wakeMode:'next-heartbeat',delivery:{mode:'webhook',to:'https://example.invalid/'},deleteAfterRun:true});
 assert.equal(await f.hooks.syncDigest(),'updated');
 const restored=f.cron.jobs.find(j=>j.id===id);assert.equal(restored.sessionTarget,'isolated');assert.deepEqual(restored.delivery,{mode:'none'});assert.equal(restored.deleteAfterRun,false);
 f.state.plan.enabled=false;assert.equal(await f.hooks.syncDigest(),'removed');assert.equal(f.state.management,null);assert.deepEqual(f.cron.jobs,[foreign]);await f.hooks.gatewayStop();
});

test('same prose does not adopt or delete legacy or foreign agent jobs',async()=>{
 const jobs=[{id:'legacy',agentId:'pixel',description:'Created by Portal integrations for the daily news digest.'},{id:'other-agent',agentId:'another',description:BASE.description}];
 const f=fixture(structuredClone(jobs));assert.equal(await f.start(),'added');
 assert.deepEqual(f.cron.jobs.slice(0,2),jobs);assert.equal(f.cron.calls.filter(x=>x[0]==='remove').length,0);await f.hooks.gatewayStop();
});

test('persisted ID with changed owner or journal marker is refused rather than overwritten',async()=>{
 const f=fixture();await f.start();f.cron.jobs[0].agentId='someone-else';await assert.rejects(f.hooks.syncDigest(),/recorded digest job was changed/);
 assert.equal(f.cron.calls.filter(x=>['update','remove'].includes(x[0])).length,0);await f.hooks.gatewayStop();
});

test('nonce collision or malformed recovery candidate fails closed without deleting duplicates',async()=>{
 const candidate={agentId:'pixel',description:BASE.description,sessionTarget:'isolated',payload:{kind:'agentTurn',lightContext:true}};
 const f=fixture([{id:'a',...candidate},{id:'b',...candidate}]);await f.start();
 assert.match(f.state.warnings[0],/More than one job/);assert.equal(f.cron.calls.filter(x=>x[0]!=='list').length,0);await f.hooks.gatewayStop();
 const g=fixture([{id:'bad',agentId:'pixel',description:BASE.description,sessionTarget:'main'}]);await g.start();assert.match(g.state.warnings[0],/unexpected execution settings/);assert.equal(g.state.management.jobId,null);await g.hooks.gatewayStop();
});

test('crash after native add recovers the durable nonce journal without adding another job',async()=>{
 const f=fixture();let fail=true;
 const hooks=createTaskHooks({timers:timers(),submit:async r=>{if(r.action==='digest-job-record'&&fail){fail=false;throw Error('lost response');}return f.submit(r);}});
 await hooks.gatewayStart({}, {getCron:()=>f.cron});assert.equal(f.state.management.jobId,null);assert.equal(f.cron.jobs.length,1);
 assert.equal(await hooks.syncDigest(),'kept');assert.equal(f.state.management.jobId,f.cron.jobs[0].id);assert.equal(f.cron.calls.filter(x=>x[0]==='add').length,1);await hooks.gatewayStop();
});

test('overlapping syncs add only once and gateway stop blocks queued mutations',async()=>{
 const f=fixture();let release;const gate=new Promise(resolve=>{release=resolve;});const list=f.cron.list.bind(f.cron);
 f.cron.list=async options=>{await gate;return list(options);};const start=f.start();
 await new Promise(resolve=>setImmediate(resolve));const second=f.hooks.syncDigest();const stop=f.hooks.gatewayStop();release();await Promise.all([start,stop]);await assert.rejects(second,/stopped/);
 assert.equal(f.cron.calls.filter(x=>x[0]==='add').length,0);assert.equal(await f.hooks.syncDigest(),'stopped');
});

test('stop waits for an already submitted add and its identity bookkeeping',async()=>{
 const f=fixture();let release,entered;const gate=new Promise(resolve=>{release=resolve;});const began=new Promise(resolve=>{entered=resolve;});const add=f.cron.add.bind(f.cron);
 f.cron.add=async job=>{entered();await gate;return add(job);};const start=f.start();await began;let stopped=false;const stop=f.hooks.gatewayStop().then(()=>{stopped=true;});await new Promise(resolve=>setImmediate(resolve));assert.equal(stopped,false);release();await Promise.all([start,stop]);
 assert.equal(f.state.management.jobId,f.cron.jobs[0].id);assert.equal(f.cron.calls.filter(x=>x[0]==='add').length,1);
 const count=f.cron.calls.length;await f.hooks.syncDigest();assert.equal(f.cron.calls.length,count);
});
