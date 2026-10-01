import test from 'node:test';
import assert from 'node:assert/strict';
import {createProjectBuildTool,normalizeProjectBuild} from '../plugin/project-build.mjs';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';

const params={action:'submit',project:'demo',outputDirectory:'out'};
const rejection={schemaVersion:1,kind:'ods-project-job',status:'invalid-request',executionStarted:false,
  issue:{code:'python-lock-sha256',file:'requirements.lock'}};
const scope={agentId:'pixel',sessionId:'session',sessionKey:'owner',runId:'run'};

test('manifest guidance reaches model and permits a corrected admitted submit',async()=>{
  const control=createProjectRunControl(); let count=0;
  const request=async()=> ++count===1 ? rejection : {schemaVersion:1,kind:'ods-project-job',
    jobId:'ods-project-'+'a'.repeat(24),status:'queued',project:'demo',cancelRequested:false,steps:[],output:null};
  async function submit(id) {
    control.before({toolName:'pixel_ods_project_build',params},{...scope,toolCallId:id});
    const bound=control.bind(id,params,scope,request);
    return createProjectBuildTool({request:bound}).execute(id,params);
  }
  const result=await submit('bad-lock');
  assert.equal(result.details.status,'invalid-request');
  assert.equal(result.details.issue.file,'requirements.lock');
  assert.match(result.content[0].text,/exactly 64 hexadecimal digits/);
  assert.match(result.content[0].text,/do not trim, pad or invent/);
  assert.match(result.content[0].text,/every transitive dependency/);
  assert.equal(result.details.nextAction.automaticRetry,false);
  assert.equal(count,1);
  assert.equal((await submit('corrected-lock')).details.status,'queued');
  assert.equal(count,2);
});

test('pin guidance requires the transitive dependency closure too',async()=>{
  const tool=createProjectBuildTool({request:async()=>({...rejection,
    issue:{code:'python-lock-pin',file:'requirements.lock'}})});
  const result=await tool.execute('pin-error',params);
  assert.match(result.content[0].text,/every direct and transitive dependency/);
  assert.match(result.content[0].text,/SHA-256 wheel hashes/);
});

test('unexpected manifest diagnostics are never reflected or treated as no execution',async()=>{
  for(const mutate of [r=>{r.issue.file='/private/secret';},r=>{r.issue.code='arbitrary text';},
    r=>{r.issue.detail='PRIVATE';},r=>{r.issue.code=['python-lock-sha256'];},r=>{r.executionStarted=true;}]) {
    const raw=structuredClone(rejection); mutate(raw);
    const control=createProjectRunControl();
    control.before({toolName:'pixel_ods_project_build',params},{...scope,toolCallId:'bad'});
    const request=control.bind('bad',params,scope,async()=>raw);
    const result=await createProjectBuildTool({request}).execute('bad',params);
    assert.equal(result.details.status,'unconfirmed');
    assert.doesNotMatch(JSON.stringify(result),/PRIVATE|private\/secret|arbitrary text/);
    assert.equal(await control.cancel(scope),false);
  }
});

test('unbound validation feedback cannot manufacture admission for corrected input',()=>{
  const control=createProjectRunControl();
  assert.throws(()=>control.bind('unknown',params,scope,async()=>rejection),/not bound/);
  assert.equal(normalizeProjectBuild(params).action,'submit');
});
