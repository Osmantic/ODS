import test from 'node:test';
import assert from 'node:assert/strict';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';
import {normalizeProjectBuild} from '../plugin/project-build.mjs';
const name = 'pixel_ods_project_build';
const scope = {agentId:'pixel', runId:'run-a', sessionId:'session-a', sessionKey:'owner-a'};
const params = {action:'submit', project:'demo', outputDirectory:'dist'};
const jobId = `ods-project-${'a'.repeat(24)}`;
const receipt = status => ({schemaVersion:1, kind:'ods-project-job', jobId, project:'demo', status});
function bind(control, request, owner = scope, deferred = false) {
  control.before({toolName:deferred ? 'tool_call' : name,
    params:deferred ? {id:name,args:params} : params}, {...owner,toolCallId:'call-a'});
  return control.bind(deferred ? `tool_search_code:call-a:${name}:1` : 'call-a', params, owner, request);
}
for (const deferred of [false,true]) test(`Stop cancels only the bound run (${deferred ? 'discovered' : 'direct'})`, async () => {
  const control = createProjectRunControl({wait:async()=>{}}), actions=[];
  const request = async p => {actions.push(p.action); return receipt(p.action === 'submit' ? 'running' : p.action === 'cancel' ? 'running' : 'cancelled');};
  await bind(control,request,scope,deferred)(normalizeProjectBuild(params),{});
  assert.equal(await control.cancel({...scope,runId:'unrelated'}),true);
  assert.deepEqual(actions,['submit']);
  assert.equal(await control.cancel(scope),true);
  assert.deepEqual(actions,['submit','cancel','observe']);
});
test('lost submission is not retried and cannot acknowledge Stop', async () => {
  const control=createProjectRunControl(); let calls=0;
  await assert.rejects(bind(control,async()=>{calls++;throw Error('lost reply');})(normalizeProjectBuild(params),{}));
  assert.equal(await control.cancel(scope),false);
  assert.equal(calls,1);
});
test('mismatched controller job cannot confirm cancellation', async () => {
  const control=createProjectRunControl();
  await bind(control,async p=>p.action==='submit'?receipt('running'):{...receipt('cancelled'),jobId:`ods-project-${'b'.repeat(24)}`})(normalizeProjectBuild(params),{});
  assert.equal(await control.cancel(scope),false);
});
test('unadmitted or different-session calls never reach transport', () => {
  const control=createProjectRunControl();
  assert.throws(()=>control.bind('unknown',params,scope,()=>{}),/not bound/);
  control.before({toolName:name,params},{...scope,toolCallId:'call-a'});
  assert.throws(()=>control.bind('call-a',params,{...scope,sessionId:'other'},()=>{}),/not bound/);
});
test('pending submission blocks acknowledgement and subsequent submission remains stopped', async () => {
  const control=createProjectRunControl(); let finish;
  const request=bind(control,()=>new Promise(resolve=>{finish=resolve;}));
  const pending=request(normalizeProjectBuild(params),{});
  assert.equal(await control.cancel(scope),false);
  finish(receipt('queued')); await pending;
  await assert.rejects(request(normalizeProjectBuild(params),{}),/stopping/);
});
test('observing an old job does not adopt it for a new run Stop', async () => {
  const control=createProjectRunControl(), actions=[];
  const observation={action:'observe',jobId};
  control.before({toolName:name,params:observation},{...scope,toolCallId:'observe-a'});
  const request=control.bind('observe-a',observation,scope,async p=>{actions.push(p.action);return receipt('running');});
  await request(normalizeProjectBuild(observation),{});
  assert.equal(await control.cancel(scope),true);
  assert.deepEqual(actions,['observe']);
});

test('read-only capability queries cannot adopt jobs or leave unknown execution on Stop', async () => {
  const control = createProjectRunControl();
  const query = {action:'capabilities',runtime:'python'};
  let reject;
  control.before({toolName:name,params:query},{...scope,toolCallId:'caps'});
  const request = control.bind('caps',query,scope,()=>new Promise((_, failure)=>{reject=failure;}));
  const pending = request(normalizeProjectBuild(query),{});
  assert.equal(await control.cancel(scope),true);
  reject(Error('probe lost'));
  await assert.rejects(pending);
  assert.equal(await control.cancel(scope),true);
  assert.throws(()=>request(normalizeProjectBuild(params),{}),/cannot execute/);
  assert.throws(()=>control.bind('caps',query,scope,()=>{}),/not bound/);
});

test('uncertain same-project submissions are refused but explicit confirmed-failure retry is allowed', async () => {
  const control=createProjectRunControl(); let calls=0;
  const submit=bind(control,async()=>{calls++;return receipt('unconfirmed');});
  await submit(normalizeProjectBuild(params),{});
  assert.equal((await bind(control,async()=>{calls++;})(normalizeProjectBuild(params),{})).status,'recovery-required');
  assert.equal(calls,1);
  const retry=createProjectRunControl();
  await bind(retry,async()=>({...receipt('failed'),output:{executionStarted:false,retryEligible:true}}))(normalizeProjectBuild(params),{});
  assert.equal((await bind(retry,async()=>receipt('queued'))(normalizeProjectBuild(params),{})).status,'queued');
});

test('lost reply fences the same target across owner turns without leaking prior job IDs', async () => {
  const control=createProjectRunControl();
  await assert.rejects(bind(control,async()=>{throw Error('reply lost');})(normalizeProjectBuild(params),{}));
  const next={...scope,runId:'new-run',sessionId:'new-session'};
  const refused=await bind(control,()=>assert.fail('must not submit'),next)(normalizeProjectBuild(params),{});
  assert.equal(refused.status,'recovery-required');
  assert.equal(refused.jobId,undefined);
  const independent={...params,project:'independent'};
  control.before({toolName:name,params:independent},{...next,toolCallId:'independent'});
  const run=control.bind('independent',independent,next,async()=>({...receipt('queued'),project:'independent'}));
  assert.equal((await run(normalizeProjectBuild(independent),{})).status,'queued');
});

test('authenticated recovery refusal does not adopt another run job for Stop', async () => {
  const control=createProjectRunControl(); let calls=0;
  await bind(control,async()=>{calls++;return {schemaVersion:1,kind:'ods-project-job',status:'recovery-required',executionStarted:false,jobId};})(normalizeProjectBuild(params),{});
  assert.equal(await control.cancel(scope),true);
  assert.equal(calls,1);
});

test('malformed recovery refusal cannot prove no execution', async () => {
  const control=createProjectRunControl();
  await bind(control,async()=>({schemaVersion:1,kind:'ods-project-job',status:'recovery-required',executionStarted:true}))(normalizeProjectBuild(params),{});
  assert.equal(await control.cancel(scope),false);
});
