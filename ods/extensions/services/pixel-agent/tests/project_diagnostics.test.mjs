import test from 'node:test';
import assert from 'node:assert/strict';
import {createProjectBuildTool,normalizeProjectBuild} from '../plugin/project-build.mjs';
import {createProjectRunControl} from '../plugin/project-run-control.mjs';
import {displayForActivity} from '../plugin/activity-display.mjs';
import {validateDiagnostic} from '../plugin/project-diagnostics.mjs';

const scope={agentId:'pixel',runId:'probe-run',sessionId:'probe-session',sessionKey:'owner-probe'};
const params={action:'diagnose',runtime:'python'},name='pixel_ods_project_build';
const jobId=`ods-project-${'a'.repeat(24)}`;
const receipt=status=>({schemaVersion:1,kind:'ods-project-job',jobId,status,purpose:'diagnostic',scope:'managed-executor',
  runtime:'python',project:null,cancelRequested:false,steps:[],output:null});
const output={schemaVersion:1,kind:'ods-project-diagnostic',scope:'managed-executor',runtime:'python',code:'ready',
  checks:Object.fromEntries(['node','npm','python','pip','venv','scratch'].map(key=>[key,{code:'ready'}])),
  cleanup:'confirmed',network:'none',scratchLimitBytes:64*1024*1024,chatSandboxVerified:false,
  nextAction:{code:'prepare-locked-project',tool:name,action:'submit'}};

test('diagnostic schema has no command/path/package escape',()=>{
  assert.deepEqual(normalizeProjectBuild(params),{schemaVersion:1,...params});
  for(const extra of [{command:'rm -rf /'}, {path:'/tmp'}, {package:'anything'}])
    assert.throws(()=>normalizeProjectBuild({...params,...extra}));
  assert.throws(()=>normalizeProjectBuild({...params,runtime:'ruby'}));
});

test('diagnostic activity identifies the executor without displaying raw output',()=>{
  const display=displayForActivity({params,result:{text:'private output'}},{toolName:name});
  assert.equal(display.label,'Checking managed runtime tools');
  assert.equal(display.detail,null);
  assert.doesNotMatch(JSON.stringify(display),/private output/);
});

test('diagnostic completion exposes actual checks without claiming sandbox or artifact',async()=>{
  const completed={...receipt('succeeded'),steps:[{stage:'diagnose',status:'succeeded',exitCode:0}],output};
  const tool=createProjectBuildTool({request:async()=>completed});
  const value=await tool.execute('probe',params);
  assert.equal(value.isError,false);
  assert.equal(value.details.output.chatSandboxVerified,false);
  assert.equal(value.details.project,null);
  for(const bad of [{...completed,steps:[]},{...completed,output:{...output,chatSandboxVerified:true}},
    {...completed,output:{...output,network:'bridge'}},{...completed,runtime:'npm'}]) {
    const result=await createProjectBuildTool({request:async()=>bad}).execute('probe',params);
    assert.equal(result.details.status,'unconfirmed');
  }
});

test('missing tool and failed cleanup preserve other probe evidence',async()=>{
  for(const failed of ['missing','cleanup']) {
    const evidence={...output,checks:{...output.checks,pip:{code:failed==='missing'?'missing':'ready'}},
      code:failed==='missing'?'missing':'ready',cleanup:failed==='cleanup'?'unconfirmed':'confirmed'};
    const result=await createProjectBuildTool({request:async()=>({...receipt('succeeded'),
      steps:[{stage:'diagnose',status:'succeeded',exitCode:0}],output:evidence})}).execute('probe',params);
    assert.equal(result.isError,true);
    assert.equal(result.details.output.checks.scratch.code,'ready');
  }
});

for(const deferred of [false,true]) test(`diagnostic Stop owns only exact accepted job, deferred=${deferred}`,async()=>{
  const control=createProjectRunControl(),actions=[];
  control.before({toolName:deferred?'tool_call':name,params:deferred?{id:name,args:params}:params},{...scope,toolCallId:'probe'});
  const request=control.bind(deferred?`tool_search_code:probe:${name}:1`:'probe',params,scope,async value=>{
    actions.push(value.action);return receipt(value.action==='diagnose'?'running':'cancelled');
  });
  await request(normalizeProjectBuild(params),{});
  assert.equal(await control.cancel({...scope,runId:'foreign'}),true);
  assert.deepEqual(actions,['diagnose']);
  assert.equal(await control.cancel(scope),true);
  assert.deepEqual(actions,['diagnose','cancel']);
});

test('lost or mismatched diagnostic submission cannot acknowledge Stop',async()=>{
  for(const transport of [async()=>{throw Error('lost');},async()=>({...receipt('running'),runtime:'npm'})]) {
    const control=createProjectRunControl();
    control.before({toolName:name,params},{...scope,toolCallId:'probe'});
    const request=control.bind('probe',params,scope,transport);
    try {await request(normalizeProjectBuild(params),{});}catch{}
    assert.equal(await control.cancel(scope),false);
  }
});

test('bounded preflight cause survives tool projection and rejects arbitrary details',async()=>{
  const evidence={...output,code:'unavailable',checks:{},cleanup:'not-started',
    failure:{phase:'storage-reservation',code:'operation-timeout'}};
  const result=await createProjectBuildTool({request:async()=>({...receipt('failed'),output:evidence})}).execute('probe',params);
  assert.deepEqual(result.details.output.failure,evidence.failure);
  assert.equal(result.details.status,'failed');
  for(const failure of [null,{phase:'secret command',code:'operation-timeout'},
    {phase:'storage-reservation',code:'raw stderr'}, {...evidence.failure,stderr:'private'}, 'private']) {
    assert.throws(()=>validateDiagnostic({...evidence,failure}));
  }
  assert.throws(()=>validateDiagnostic({...output,failure:evidence.failure}));
});
