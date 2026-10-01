import test from 'node:test';
import assert from 'node:assert/strict';
import {createProjectBuildTool, normalizeProjectBuild} from '../plugin/project-build.mjs';
import {withPiToolErrorContract} from '../plugin/pi-tool-result.mjs';

const job = `ods-project-${'a'.repeat(24)}`;
const submit = {action: 'submit', project: 'project', outputDirectory: 'out'};
const success = () => ({schemaVersion: 1, kind: 'ods-project-job', jobId: job,
  status: 'succeeded', project: 'project', cancelRequested: false,
  steps: ['acquire', 'test', 'build'].map(stage => ({stage, status: 'succeeded', exitCode: 0})),
  output: {sha256: 'b'.repeat(64), files: 1, relativeDirectory: `project/ods-builds/${'a'.repeat(24)}/site`}});

test('requires explicit controller transport and refuses host command parameters', () => {
  assert.throws(() => createProjectBuildTool(), /transport/);
  for (const params of [{...submit, command: 'sudo something'}, {...submit, project: '../escape'},
    {...submit, request_key: 'a'.repeat(64)}, {...submit, outputDirectory: '/tmp/out'},
    {action: 'observe', jobId: 'arbitrary'}]) assert.throws(() => normalizeProjectBuild(params));
});

test('forwards call identity out of model parameters and preserves actual success', async () => {
  let observed;
  const tool = createProjectBuildTool({request: async (params, context) => {
    observed = {params, context}; return success();
  }});
  const result = await tool.execute('trusted-call', submit);
  assert.equal(result.details.status, 'succeeded');
  assert.deepEqual(observed.params, {schemaVersion: 1, ...submit});
  assert.equal(observed.context.toolCallId, 'trusted-call');
  assert.equal(result.details.output.url, undefined);
});

test('missing execution evidence or a different job is never success', async () => {
  for (const mutate of [r => {r.steps.pop();}, r => {r.steps[1].exitCode = 1;},
    r => {r.output.relativeDirectory = 'another/project';}, r => {r.output.files = 0;},
    r => {r.jobId = `ods-project-${'c'.repeat(24)}`;}]) {
    const receipt = success(); mutate(receipt);
    const tool = createProjectBuildTool({request: async () => receipt});
    assert.equal((await tool.execute('observe', {action: 'observe', jobId: job})).details.status, 'unconfirmed');
  }
});

test('cancel request is not a confirmed cancellation; unavailable transport is not retried', async () => {
  const receipt = {...success(), status: 'running', cancelRequested: true, steps: [], output: null};
  const tool = createProjectBuildTool({request: async () => receipt});
  assert.equal((await tool.execute('cancel', {action: 'cancel', jobId: job})).details.status, 'running');
  let calls = 0;
  const offline = createProjectBuildTool({request: async () => {calls++; throw Error('offline');}});
  const result = await offline.execute('submit', submit);
  assert.equal(result.details.status, 'unconfirmed');
  assert.equal(calls, 1);
});

test('confirmed policy denial remains distinct from a lost response', async () => {
  const denied = {schemaVersion: 1, kind: 'ods-project-job', status: 'denied'};
  const tool = createProjectBuildTool({request: async () => denied});
  assert.equal((await tool.execute('call', submit)).details.status, 'denied');
});

test('recovery refusal is actionable without a new execution or automatic retry', async () => {
  for (const jobId of [undefined,job]) {
    const receipt={schemaVersion:1,kind:'ods-project-job',status:'recovery-required',executionStarted:false,
      ...(jobId ? {jobId} : {})};
    const tool=createProjectBuildTool({request:async()=>receipt});
    const result=await tool.execute('retry',submit);
    assert.equal(result.isError,true);
    assert.equal(result.details.status,'recovery-required');
    assert.equal(result.details.executionStarted,false);
    assert.equal(result.details.nextAction.automaticRetry,false);
    assert.equal(result.details.nextAction.action,jobId ? 'observe' : undefined);
  }
});

test('malformed recovery refusal cannot be interpreted as no execution', async () => {
  for (const extra of [{executionStarted:true},{jobId:'foreign'},{secret:'unexpected'}]) {
    const tool=createProjectBuildTool({request:async()=>({schemaVersion:1,kind:'ods-project-job',
      status:'recovery-required',executionStarted:false,...extra})});
    assert.equal((await tool.execute('retry',submit)).details.status,'unconfirmed');
  }
});

test('observation waits for real completion without another submission', async () => {
  const actions = [], waits = [];
  const tool = createProjectBuildTool({
    wait: async ms => waits.push(ms),
    request: async params => {
      actions.push(params.action);
      return actions.length < 3 ? {...success(),status:'running',steps:[],output:null} : success();
    },
  });
  const result = await tool.execute('observe', {action:'observe',jobId:job});
  assert.equal(result.details.status,'succeeded');
  assert.deepEqual(actions,['observe','observe','observe']);
  assert.deepEqual(waits,[5000,5000]);
});

test('observation remains bounded and abort does not claim cancellation', async () => {
  let calls = 0;
  const tool = createProjectBuildTool({wait:async()=>{},request:async()=>{
    calls++; return {...success(),status:'running',steps:[],output:null};
  }});
  assert.equal((await tool.execute('observe',{action:'observe',jobId:job})).details.status,'running');
  assert.equal(calls,49);
  const abort = new AbortController();
  const interrupted = createProjectBuildTool({wait:async()=>abort.abort(),request:async()=>{
    return {...success(),status:'running',steps:[],output:null};
  }});
  assert.equal((await interrupted.execute('observe',{action:'observe',jobId:job},abort.signal)).details.status,'unconfirmed');
});

test('long observation completes within one call and slow reads respect the wall-clock budget', async () => {
  let elapsed = 0, calls = 0;
  const tool = createProjectBuildTool({now:()=>elapsed, wait:async ms=>{elapsed+=ms;}, request:async()=>{
    calls++; return elapsed >= 150000 ? success() : {...success(),status:'running',steps:[],output:null};
  }});
  assert.equal((await tool.execute('observe',{action:'observe',jobId:job})).details.status,'succeeded');
  assert.equal(elapsed,150000);
  assert.equal(calls,31);
  elapsed=0; calls=0;
  const slow = createProjectBuildTool({now:()=>elapsed,wait:async ms=>{elapsed+=ms;},request:async()=>{
    calls++; elapsed+=10000; return {...success(),status:'running',steps:[],output:null};
  }});
  assert.equal((await slow.execute('observe',{action:'observe',jobId:job})).details.status,'running');
  assert.equal(elapsed,240000); // includes the initial read; no new request at the deadline
  assert.equal(calls,16);
});

test('observation stops immediately on denial or a lost response after waiting', async () => {
  for (const outcome of ['denied', 'offline']) {
    let calls = 0, waits = 0;
    const tool = createProjectBuildTool({wait:async()=>{waits++;},request:async()=>{
      calls++;
      if (calls === 1) return {...success(),status:'running',steps:[],output:null};
      if (outcome === 'offline') throw Error('offline');
      return {schemaVersion:1,kind:'ods-project-job',status:'denied'};
    }});
    const result = await tool.execute('observe',{action:'observe',jobId:job});
    assert.equal(result.details.status,outcome === 'offline' ? 'unconfirmed' : 'denied');
    assert.equal(calls,2);
    assert.equal(waits,1);
  }
});

test('Portal transcript receives native failure markers without changing job evidence', async () => {
  for (const status of ['failed', 'unconfirmed']) {
    const receipt = {...success(), status, output: null};
    const tool = withPiToolErrorContract(createProjectBuildTool({request: async () => receipt}));
    const result = await tool.execute('call', {action: 'observe', jobId: job});
    assert.equal(result.isError, true);
    assert.equal(result.details.ok, false);
    assert.equal(result.details.status, status);
    assert.equal(result.details.jobId, job);
  }
});
